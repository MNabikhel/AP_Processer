"""The company server's capture processes (D-168): ``serve --capture-work`` reads the import queue in a process of its
own, never a web worker; as many as the cores less two, each with its share of the OCR's threads; stopped with the
server, the file under way put back in line."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.folder import read_file
from ap_coder.web import server
from tests.conftest import SAMPLES

ROOT = Path(__file__).resolve().parents[2]


def test_as_many_capture_processes_as_the_cores_less_two_each_with_its_share_of_threads(monkeypatch):
    monkeypatch.delenv(server.CAPTURE_ENV, raising=False)
    assert [server.capture_processes(cores) for cores in (1, 2, 4, 32)] == [1, 1, 2, 30]
    monkeypatch.setenv(server.CAPTURE_ENV, "3")
    assert server.capture_processes(32) == 3
    for name in (server.OCR_THREADS_ENV, "OMP_NUM_THREADS"):
        monkeypatch.delenv(name, raising=False)
    env = server.capture_env(2, 2, cores=4)
    assert env[server.CAPTURE_NUMBER_ENV] == "2" and env[server.OCR_THREADS_ENV] == "1" and env["OMP_NUM_THREADS"] == "1"
    assert server.capture_env(1, 5, cores=32)[server.OCR_THREADS_ENV] == "6"
    monkeypatch.setenv(server.OCR_THREADS_ENV, "4")  # set by IT: kept
    assert server.capture_env(1, 30, cores=32)[server.OCR_THREADS_ENV] == "4"


def test_the_ocr_threads_are_given_to_onnx_runtime(monkeypatch):
    from ap_coder.ops import offline

    monkeypatch.delenv(server.OCR_THREADS_ENV, raising=False)
    assert not any(k.startswith("EngineConfig") for k in offline._engine_params({}, ()))
    monkeypatch.setenv(server.OCR_THREADS_ENV, "2")
    params = offline._engine_params({}, ())
    assert params["EngineConfig.onnxruntime.intra_op_num_threads"] == 2
    assert params["EngineConfig.onnxruntime.inter_op_num_threads"] == 1


def test_a_capture_process_reads_the_queue_and_stops_when_its_input_closes(tmp_path):
    """``serve --capture-work`` (what the server's ``CaptureWork`` starts): the files in line are read into the review
    queue by it, with its ``capture:`` lease renewed meanwhile; its input closed, it ends and lets go of its lease."""
    data = tmp_path / "private"
    store = Store(data / "ap_coder.db")
    load_sample_setup(store)
    folder = data / "invoices"
    folder.mkdir()
    read = []
    for path in sorted(SAMPLES.glob("*.pdf"))[:3]:
        shutil.copy(path, folder / path.name)
        read.append(read_file(folder / path.name))
    store.queue_imports(read, source="upload", person=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AP_")}
    env.update({"AP_PRIVATE_DIR": str(data), "PYTHONPATH": str(ROOT), "AP_LLM_BASE_URL": "http://127.0.0.1:9/v1",
                **{k: v for k, v in server.capture_env(1, 1).items() if k.startswith(("AP_CAPTURE", "AP_OCR"))}})
    command = [sys.executable, "-m", "ap_coder", "--db", str(store.path), "serve", "--capture-work"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, env=env, cwd=ROOT)
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and (store.imports_open() != {"waiting": 0, "reading": 0}
                                               or len(store.list_invoices()) < 3):  # fmt: skip
            assert process.poll() is None, "the capture process stopped"
            time.sleep(0.2)
        assert len(store.list_invoices()) == 3 and store.capture_processes() == 1
    finally:
        process.stdin.close()  # the server stopping
        code = process.wait(30)
    assert code == 0 and store.capture_processes() == 0
    assert store.import_run()["counts"] == {"done": 3}
    assert (data / "logs" / "capture-1.log").exists()
