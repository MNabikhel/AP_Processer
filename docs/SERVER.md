# AP Coder on a company Windows Server (for IT)

This guide sets AP Coder up on one Windows Server for the whole AP team. It runs as a Windows scheduled task (not a
Windows service), staff open it in their browser over HTTPS and log in with their own account, and nothing is installed on their PCs. Everything stays
offline: AP Coder and LM Studio run on the server and connect to nothing on the internet.

Laptop mode is not changed by any of this: a laptop still runs AP Coder for one person at 127.0.0.1, with no
logins. The server mode is switched on by `AP_SERVER_MODE=1` (step 2).

In this guide:

- `C:\APCoder\APProcessor` is the AP Coder folder (the code), `D:\APCoderData` the data folder (the database,
  invoices, backups, logs, settings). Use your own paths; keep the data folder outside the code folder.
- `apcoder.company.local` is the name staff type in their browser, and `APCoderService` the Windows account AP Coder
  runs as.
- Commands run in an **administrator** Command Prompt, in the current AP Coder folder:
  `cd /d C:\APCoder\APProcessor`, then `.venv\Scripts\python.exe -m ap_coder ...`. After an update the current folder
  is the new one (`service status` names it).

Contents: [1. What the server needs](#1-what-the-server-needs) · [2. Install](#2-install-from-the-offline-bundle) ·
[3. HTTPS](#3-the-https-certificate) · [4. The name](#4-the-name-staff-type) · [5. Firewall](#5-the-firewall) ·
[6. The service](#6-the-service) · [7. First admin](#7-the-first-admin) · [8. LM Studio](#8-lm-studio-on-the-same-server)
· [9. Backups](#9-backups) · [10. Users and roles](#10-users-and-roles) · [11. Updating](#11-updating-and-going-back)
· [12. Troubleshooting](#12-troubleshooting)

## 1. What the server needs

| | Minimum | Better |
|---|---|---|
| Windows | Windows Server 2019, 2022 or 2025, 64-bit (or Windows 11 Pro: below) | |
| Processor | 8 cores (the OCR of scans runs on the processor) | 16 cores |
| Memory | 32 GB | 64 GB |
| Graphics card (for LM Studio) | NVIDIA with 12 GB of VRAM (an estimate) | 16 GB of VRAM or more (an estimate) |
| Disk | 60 GB free on an SSD | 200 GB |
| Python | Python 3.12, 64-bit, installed for all users | |
| LM Studio | 0.4.8 or newer, with its models copied in | |

Why these numbers:

- **The models** (docs/LM_STUDIO.md): the chat model Gemma 4 12B is about 7.2 GB, or Qwen 3.5 9B about 6.5 GB;
  OvisOCR2, the page reader that reads every page of every invoice, about 1 GB; the embedding model under 1 GB; plus
  the room each needs for its context (16,384 and 20,480 tokens). Estimated from those sizes (none of the
  graphics-card figures in this section has been measured on a graphics card): on 8 GB of VRAM they spill into the
  server's memory and are slower; 12 GB holds them, 16 GB leaves room. Without a graphics card AP Coder still works,
  but reading each page takes minutes on the processor instead of seconds (estimated) on a graphics card.
- **Disk**: each AP Coder version is 2 to 3 GB (its packages and OCR models), and two are kept (the current one and
  the previous one, to go back to). The models are about 10 GB. The data grows with the invoices (about 0.5 MB each,
  kept with the database) and 14 daily copies of the database, plus one copy of each invoice file (each content
  kept once, D-143): about twice the invoices' size.
- **How many people and invoices** (D-111). *Measured*: a 12,000-invoice database, a load test of logged-in people
  each clicking every 10 seconds on average through the review queue, invoices, their pages, approvals, search and
  exports, on a 4-core test machine (shared with other work, so on the slow side), before the database speed-ups
  of the same release:

  | People at once | 1 web worker | 4 web workers |
  |---|---|---|
  | 25 | half the pages in 6.9 s, 95% in 39 s | half in 0.4 s, 95% in 1.5 s |
  | 50 | half in 45 s; 22 pages gave up after 2 minutes | half in 1.0 s, 95% in 5.9 s |
  | 100 | 82 pages gave up after 2 minutes | half in 6.9 s, 95% in 22 s (all 4 cores busy) |

  Memory: 2 GB for the 4 workers and the background process with 25 people, 5 GB with 100. *Estimated* (the
  processor and memory from that measurement; the graphics card's rows are estimates, from an assumed 15 s a page,
  not measured on a graphics card):

  | | 5,000 invoices a month, 25 people | 10,000 invoices a month, 50 people at once |
  |---|---|---|
  | Processor | 8 cores, 7 workers (the default) | 16 cores, `--workers 8` (8 for OCR and the rest) |
  | Memory | 32 GB | 64 GB |
  | Graphics card | 16 GB of VRAM, `--llm-parallel 2` | 24 GB of VRAM or more, `--llm-parallel 4` (step 8) |
  | The graphics card's day | 2 to 3 hours of reading (about 500 pages a working day, every invoice read) | 4 to 6 hours; about half with clean digital PDFs left out (step 6) |

  The reading runs in the background: nobody waits on it, but invoices come into the queue later on a busy day.
- **Windows 11 Pro** also runs the server mode (on the pilot's laptop or desktop, for example): the service is a Task Scheduler
  task (step 6), not a Windows service, and Task Scheduler is part of Windows 11 too. CI tests AP Coder on Windows
  Server only (GitHub's Windows machines; the task itself is not registered there, its commands are checked), so run
  `service install` and `service status` on that computer before staff use it. Windows 11 Pro allows at most 20
  devices connected to it at once; whether a laptop or desktop may serve the pilot is the company's IT's decision.
  A laptop serving others stays docked and plugged in, with sleep off and its battery set to stop charging at about
  80% (its battery then covers a power cut).
- **Python**: the offline bundle has packages for Python 3.11 to 3.14. Install Python 3.12 from python.org's offline
  installer (copy it in): *Customize installation* → *Install for all users*, and *Add Python to environment
  variables*.

## 2. Install from the offline bundle

1. **The account AP Coder runs as**: a local account used for nothing else. In an administrator PowerShell:

   ```powershell
   New-LocalUser -Name APCoderService -Password (Read-Host -AsSecureString "Password") `
     -PasswordNeverExpires -UserMayNotChangePassword -Description "Runs AP Coder"
   ```

   It needs no group (not Administrators). Keep its password in your password safe: Windows asks for it when the
   service is installed. To copy backups to a network share (step 9), use a domain account instead (for example
   `COMPANY\svc-apcoder`); a local account can't sign in to another server's share.

2. **Unzip the bundle** (the ZIP IT built with `scripts/build_offline_bundle.py`), and make the data folder:

   ```bat
   powershell -Command "Expand-Archive -Path D:\Install\APProcessor-offline-0.4.0-20261101.zip -DestinationPath C:\APCoder"
   mkdir D:\APCoderData
   ```

   This makes `C:\APCoder\APProcessor`. Unzip it on the server's own disk, not a network drive. The data folder too
   must be on one of the server's own disks: AP Coder's database can be damaged on a network share (a `\\server\share`
   path or a mapped drive letter), so the server refuses to start there and says why; `doctor` says it too (*data
   folder place*). For a copy on a share, set the backups' second folder (step 9).

3. **Tell AP Coder where its data is, and that this is the server**, for every account on the server (the service and
   IT's console):

   ```bat
   setx /M AP_PRIVATE_DIR D:\APCoderData
   setx /M AP_SERVER_MODE 1
   ```

   Close the Command Prompt and open a new administrator one (it reads them when it opens).

4. **Set it up** (the packages come from the bundle's `wheelhouse`, offline; a few minutes; it ends with a
   self-check that reads the sample invoices):

   ```bat
   cd /d C:\APCoder\APProcessor
   APProcessor.bat --no-start --yes
   ```

   On the server the JD Edwards E1 settings and export are shown from the start (*Settings* → *JD Edwards E1*, and
   *JD Edwards E1* on *Exports*); on a laptop they wait for *Settings* → *About* → *Show JD Edwards settings*, which
   on the server hides them again if you export to Excel or CSV only.

5. **Folder permissions.** The data folder: full control for `APCoderService` and the administrators, nobody else
   (the database holds every invoice). The code folder: full control for the administrators and SYSTEM, read and run
   for `APCoderService`, nobody else (whoever can change the code can read every invoice and log in as anyone).

   ```bat
   icacls D:\APCoderData /inheritance:r /grant:r "APCoderService:(OI)(CI)F" "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
   icacls C:\APCoder /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "APCoderService:(OI)(CI)RX"
   ```

   `/inheritance:r` drops what `C:\` gives everyone (Users may add files there). The folders `server-update` makes
   later (`C:\APCoder\APProcessor-<version>`, step 11) are made inside `C:\APCoder`, so they get the same three
   permissions and nothing else. Check with `icacls C:\APCoder\APProcessor`: only those three lines.

   The code folder must stay this way: `server-update` runs, as you, what is in it. It trusts only the folder it is
   run from (and, for `--back`, a folder beside it in `C:\APCoder`): `server.json` is in the data folder, which the
   service's account can change, so a `folder` there that is not the one you run `server-update` from is refused.
   The administrator's commands (`server-update`, `service install|start|stop|status|remove`) read nothing from the
   data folder's `.env`, the Pythons `server-update` starts get a clean environment (`-E -s`, no `PYTHON*` or
   `PIP_*` variable), and no `.env` ever sets `PATH`, `PYTHON*`, `PIP_*` and the like. Put AP Coder's settings for
   the service in `server.json` (`service install`) or in the machine's environment (`setx /M`), not in `.env`.

## 3. The HTTPS certificate

Staff sign in with a password, so AP Coder is served over HTTPS only. Ask your company's certificate authority (CA)
for a server certificate for `apcoder.company.local` (in its *Subject Alternative Name*). The staff PCs already trust
the company CA, so the browser shows no warning.

AP Coder reads two PEM files: the certificate (with the CA's intermediate certificates after it, if any) and its
private key, not protected by a password. Put them in `D:\APCoderData\tls` (the data folder's permissions keep the key
private).

The CA usually gives a `.pfx` file. Convert it with `openssl` (on any IT PC: Git for Windows has it in
`C:\Program Files\Git\usr\bin\openssl.exe`); it asks for the `.pfx` password:

```bat
openssl pkcs12 -in apcoder.pfx -clcerts -nokeys -out apcoder.crt.pem
openssl pkcs12 -in apcoder.pfx -cacerts -nokeys -out ca-chain.pem
openssl pkcs12 -in apcoder.pfx -nocerts -nodes -out apcoder.key.pem
copy /b apcoder.crt.pem + ca-chain.pem D:\APCoderData\tls\apcoder.pem
move apcoder.key.pem D:\APCoderData\tls\apcoder.key.pem
```

(With OpenSSL 3 and an old `.pfx`, add `-legacy` to each `pkcs12` command if it says "unsupported".) If the CA gave a
`.cer` file and the key separately, `certutil -encode apcoder.cer apcoder.crt.pem` turns the certificate into PEM;
`certutil` can't write the private key as PEM, so use `openssl` for that. Delete the copies left on the IT PC.

For a first test only, AP Coder can run without HTTPS (`--insecure-http` in step 6): passwords then cross the network
readable. Never leave it that way.

## 4. The name staff type

1. **DNS**: ask for an alias (a CNAME, or an A record) `apcoder.company.local` pointing to the server.
2. **Allowed names**: AP Coder answers only to the names it is told (against DNS rebinding). Give them to the
   service as `--allowed-hosts apcoder.company.local` (step 6; several: comma-separated). They are kept in
   `D:\APCoderData\server.json`; the environment variable `AP_ALLOWED_HOSTS` overrides them.

Staff then open `https://apcoder.company.local:8501`. To leave the port out (`https://apcoder.company.local`), install
the service with `--port 443` when nothing else on the server uses that port (IIS).

**Behind IIS** (optional, if your policy wants every web site in IIS): IIS with URL Rewrite and ARR forwards to
`https://127.0.0.1:8501`, and the service is installed with `--proxy <the IIS server's IP address>` (`127.0.0.1` when IIS
is on this server), so AP Coder believes the forwarded client address from that one address only. IIS must then
*replace* `X-Forwarded-For` with the client's address (not append to one the browser sent): the logins' pauses
(step 10) and the access log go by that address. Without `--proxy`, everyone behind IIS has IIS's address: they
share its pauses, and the server refuses such requests anyway. While a web worker is busy its answers say
`Connection: close`, so the next request goes to another worker (D-161): IIS opens a new connection for it, as a
browser does; that is expected, not an error.

**Compression**: AP Coder sends its pages and answers uncompressed, laptop and server (D-161). On a gigabit office
network even the largest page (a review screen of 3.6 MB on a 100,000-invoice database) takes about 30 milliseconds
on the wire, while compressing every answer would cost the server CPU at each request, for no time anyone sees. The styles, scripts and fonts are fetched once per browser after each update,
so they need none either. For people on a slow link (a VPN), IIS in front may compress **only** `text/html` and
`application/json`, with dynamic compression at its lowest level (1); never the downloads (CSV, Excel, PDF, PNG,
ZIP). Without IIS, nothing needs setting up.

## 5. The firewall

Let the office network reach the port (and only the office network, when you know its range):

```bat
netsh advfirewall firewall add rule name="AP Coder" dir=in action=allow protocol=TCP localport=8501 profile=domain remoteip=10.0.0.0/16
```

LM Studio's port (1234) stays closed: only AP Coder, on the server itself, talks to it.

## 6. The service

AP Coder runs as a Windows *scheduled task* that starts when the server starts, under `APCoderService`, whether
anyone is logged on or not. (Why a task and not a classic service, with nothing to download: docs/decisions.md D-100.)

```bat
.venv\Scripts\python.exe -m ap_coder service install --certfile D:\APCoderData\tls\apcoder.pem --keyfile D:\APCoderData\tls\apcoder.key.pem --allowed-hosts apcoder.company.local
```

Windows asks for `APCoderService`'s password once and keeps it for the task. `service install` saves the settings in
`D:\APCoderData\server.json`, writes `D:\APCoderData\service\run.cmd` and the task, and starts it. Add
`--dry-run` to see all of that first, changing nothing. Its options: `--account` (default `APCoderService`;
`SYSTEM`, `"LOCAL SERVICE"` and `"NETWORK SERVICE"` need no password), `--port` (default 8501), `--host` (default
`0.0.0.0`: every network card), `--certfile`, `--keyfile`, `--allowed-hosts`, `--proxy`, `--insecure-http`,
`--task-name` (default `AP Coder`), `--folder` (the AP Coder folder the service runs; default: the one the command
runs in; `server-update` changes it), `--allowed-folders` (the folders Admins may name, step 9), `--workers`
and `--llm-parallel` (below), `--no-start`.

`--account`, `--task-name` and `--folder` are never taken from `server.json`: the service's account can change that
file, and these three say what the task runs, as whom, and which task your `schtasks` commands change. Each
`service install|start|stop|status|remove` uses the ones given on its own command line, else the defaults (the
folder: the one you run it from); `server-update` takes `--task-name` the same way, and changes nothing when no task
of that name is installed while `server.json` names another or the dashboard answers. So when you chose another
account, task name or folder, give them again each time;
`SYSTEM` and the other built-in accounts are used only when `--account` names them. `service stop` ends only the
service's own `python.exe` (its `.venv`'s, or the Python that `.venv` was made with) when it will not stop by itself,
and the service host only while it runs (it holds `service\host.lock`).

**Workers** (`--workers N`, kept in `server.json` as `"workers"`): one Python process answers about 1.6 pages a
second however many cores the server has, so the service runs N *web workers*, all answering on the same port, and
one *background process* beside them for the work nobody waits on (OvisOCR2's reading, the AI reviewer, the Azure
check, JD Edwards, the review queue checked again after a change of settings, the daily backup, the finance hub's
schedule), and for the long downloads (search to Excel, the Activity CSV, the duplicate audit: the page says how far
the file got and downloads it once made; each person's own, kept an hour in `.cache\downloads\made`). The invoices
brought in (uploads, the invoices folder, a scheduled watch) wait in an import queue in the database, kept over a
restart, and are read by *capture processes* of their own, one per core but two (`server.json` `"capture_processes"`,
1 to 64, or `AP_CAPTURE_PROCESSES` in the .env to choose; D-168), never by a web worker: a big batch doesn't slow the pages, and colleagues' batches are read in turn.
A file a scanner or a mail rule saves in the invoices folder shows on *Import invoices* within a minute (the folder is
listed once for every worker, D-176). A
worker, the background process or a capture process that stops is started again on its own (a download it was making
is made again). `--workers 1` runs everything in one process, as before. Stopping (`service stop`) gives
the requests under way 25 seconds, then ends them: the service is stopped within 30 seconds.

- **The default** (D-153): one worker per processor core but an eighth of them, and no more than fit in the memory at
  1.5 GB each, a quarter of the memory (at least 4 GB) being kept for the system, the background process and the
  database's cache. 32 cores and 64 GB: 28 workers; 16 cores and 32 GB: 14; 4 cores and 16 GB: 4; 8 GB: 2.
- **Each worker works on 4 requests at once** (`server.json` `"worker_threads"`, 1 to 40; the others wait their
  turn): one process works on one at a time anyway, and each request under way holds what it read (about 110 MB on
  a big database). With anyio's default of 40, 16 people at once took a worker to about 2 GB; with 4, to 1.3 GB, the
  same work done. A worker stays near 1 to 1.5 GB.
- **Reports and exports take turns**: the reports (Activity, the controls report, Spend, Month-end and its
  accruals, Sales tax, Insights), the exports (the Exports page, a batch, its files, the duplicates CSV, search to
  CSV or Excel, the JD Edwards worksheet), Report a problem, a backup or restore, and the buttons that wait on LM
  Studio or JD Edwards run 2 at a time in each worker, beside its 4, so they never hold up the other pages. One that
  waits more than 30 seconds for its turn says AP Coder is busy, to try again in a minute.
- **A worker is started afresh** past its share of the memory (that 1.5 GB share, at least 2 GB; the variable
  `AP_WORKER_MAX_MB`, 0 for never) or after about 50,000 requests (a number of its own, so they don't all go
  together; `AP_WORKER_MAX_REQUESTS`). It takes no new requests, finishes those under way and what they started (an
  import, a backup), and a new one takes its place as it ends; one worker at a time, and never while an import, a
  backup or a report of its own runs. Nobody is signed out (logins are in the database). The service log says "A web
  worker (process N) is started afresh after ...".

**Requests to LM Studio at once** (`--llm-parallel N`, `server.json` `"llm_parallel"`, or `AP_LLM_PARALLEL`;
default 1): how many pages OvisOCR2 reads, and how many questions the chat model answers, at the same time, for all
of AP Coder's processes together. 1 is strictly one at a time, the safe choice with LM Studio's defaults. Raise it
only once the model server is set to answer that many together (step 8): the background process then reads that
many invoices at once.

**Less work for the graphics card** (both on by default on the server, off on a laptop):

- A digital PDF whose own text the rule reader read in full (every page with its text layer, the supplier, invoice
  number, date and total all verified, the totals adding up) and that goes to a person anyway (its supplier is not
  touchless) is not read by OvisOCR2: the invoice page says so, and *Add it to OvisOCR2's queue* still reads it.
  One in ten of them is read all the same, so OvisOCR2's accuracy stays measured; *Settings* → *Reading* counts the
  invoices and pages left out. Invoices a touchless supplier sent are always read by every reader. Turn it off with
  `AP_PAGE_READER_SKIP_CLEAN=0` in the machine's environment (`setx /M`); `AP_PAGE_READER_SAMPLE=0.2` reads one in
  five.
- The AI reviewer reviews only the invoices AP Coder could approve without a person (*Settings* → *Reading* → *AI
  reviewer* → *What it reviews*; an Admin may choose *Every invoice*). None is approved without a person until it
  agrees, as before; the others go to a person anyway.

| To | Run |
|---|---|
| See whether it runs, its version and folder | `.venv\Scripts\python.exe -m ap_coder service status` |
| Stop it (it stays stopped, also after a restart of the server) | `.venv\Scripts\python.exe -m ap_coder service stop` |
| Start it again | `.venv\Scripts\python.exe -m ap_coder service start` |
| Change a setting (port, certificate, names) | `service install` again with the new options (the others are kept, except `--account`, `--task-name` and `--folder`: give them again unless they are the defaults) |
| Remove it (the data, settings and logs are kept) | `.venv\Scripts\python.exe -m ap_coder service remove` |

What it does: the task runs `run.cmd`, which runs AP Coder's *service host* (`python -m ap_coder service run`). The
host starts the dashboard (`python -m ap_coder serve --server ...` with the settings of `server.json`) and starts it
again if it stops (after 5 seconds, then longer, up to 5 minutes). The task itself comes back within 5 minutes if the
host was ended. Settings can also come from environment variables, which win over `server.json`: `AP_SERVER_PORT`,
`AP_DASHBOARD_ADDRESS`, `AP_TLS_CERT`, `AP_TLS_KEY`, `AP_ALLOWED_HOSTS`, `AP_SERVER_PROXY`, `AP_SERVER_WORKERS`,
`AP_LLM_PARALLEL`, `AP_WORKER_THREADS`, `AP_CAPTURE_PROCESSES`. The host reads the data folder's `.env` into its
environment first, as every AP Coder process does, so one of these written there wins over `server.json` too: keep
them in `server.json` or the machine's environment (step 2). The backup model servers are the exception:
`server.json`'s `llm_backup_url` and `page_reader_backup_url` win over the `.env`'s `AP_LLM_BACKUP_URL` and
`AP_PAGE_READER_BACKUP_URL` (the machine's environment still wins over both; D-180).

**Logs**: `D:\APCoderData\logs\service.log` (what the host and the dashboard say; 5 MB, then up to 5 older files,
`service.log.1` to `.5`), and `D:\APCoderData\logs\access.log`, a line per request: the time, the address, the
user name (`-` before a login), the method, the path (without what follows `?`), the status, the bytes and the
milliseconds (10 MB, then up to 9 older files); with several workers, one file per worker, `access-1.log`,
`access-2.log`... And `D:\APCoderData\logs\background.log`: what the background process does, the work nobody waits on (it starts, the page reader's self-test, each invoice read, each AI review, JD Edwards; 5 MB, then up to 5 older files). Each download (a batch's files or ZIP, the directory's export, a
backup, a report) is also in *Activity* → *Logins*, with the person. The daily database backup runs as on a laptop
(step 9).

**HTTPS**: TLS 1.2 and 1.3 only; on TLS 1.2 only ECDHE with AES-GCM or ChaCha20 (no AES-CBC, no SHA-1 ciphers).
Browsers from the last ten years have them. Answers carry no `server:` header.

**Sizes**: a request before a login takes at most 64 KB (the login form); after it, 260 MB. *Import invoices* takes
up to 200 files and 250 MB per upload, each file up to 50 MB (a saved email 100 MB), and each person up to 2 GB or
120 uploads an hour; more is refused with a plain message. A larger batch: an Admin puts the files in the invoices
folder.

Don't double-click `APProcessor.bat` on the server once the service runs: it would start a second AP Coder on the same
data. Use `service stop` / `service start`.

## 7. The first admin

On the server's console (nobody can sign up from a browser):

```bat
.venv\Scripts\python.exe -m ap_coder users add --admin jsmith
```

It asks for a password, twice (at least 12 characters, not the user name). That password is a temporary one (it
works for 3 days): tell it to the person, who signs in at `https://apcoder.company.local:8501` and, before anything
else, chooses their own password, then sets up an authenticator app on their phone (a QR code to scan, then a code
from the app: *Sign-in codes*, step 10). The first Admin does this too: every account signs in with a code. They
then add everyone else (step 10).

## 8. LM Studio on the same server

LM Studio reads the pages and suggests GL accounts (docs/LM_STUDIO.md). On the server it runs headless, without its
window, started at boot like AP Coder.

1. **Install LM Studio** (0.4.8 or newer) as the account that will run it. LM Studio installs per user, and keeps its
   models in that user's `.lmstudio\models` folder. Use `APCoderService`: sign in as it once by Remote Desktop for the
   install (add it to *Remote Desktop Users* for that, and take it out after), or install it as yourself and run its
   task as yourself.
2. **Copy the models in** (nothing is downloaded on the server), into that account's `%USERPROFILE%\.lmstudio\models`:
   OvisOCR2 (`bartowski\ATH-MaaS_OvisOCR2-GGUF`: `ATH-MaaS_OvisOCR2-Q8_0.gguf` and
   `mmproj-ATH-MaaS_OvisOCR2-f16.gguf`) and one chat model (`lmstudio-community\gemma-4-12B-it-QAT-GGUF`), as in
   docs/PILOT.md and docs/LM_STUDIO.md. Open LM Studio once: *My Models* → the chat model → *Context Length* 16384, GPU
   offload at the maximum; OvisOCR2 → 20480, left unloaded (AP Coder loads it when it reads).
3. **The command it starts with**: `C:\APCoder\lm-studio-start.cmd`, holding (`%USERPROFILE%` is the profile of the
   account it runs as; the model's exact name is the one `lms ls` shows):

   ```bat
   "%USERPROFILE%\.lmstudio\bin\lms.exe" server start --port 1234
   "%USERPROFILE%\.lmstudio\bin\lms.exe" load gemma-4-12b-it-qat --context-length 16384 --gpu max
   ```

4. **Start it at boot**, as that account (Windows asks for its password):

   ```bat
   schtasks /create /tn "LM Studio server" /sc onstart /delay 0000:30 /ru APCoderService /rl LIMITED /tr C:\APCoder\lm-studio-start.cmd
   schtasks /run /tn "LM Studio server"
   ```

5. **Check it**: `lms server status` and `lms ps` (as that account) show the server and the loaded model; in AP Coder,
   *Settings* → *Reading* → *Check again*. AP Coder finds LM Studio at `http://127.0.0.1:1234/v1`, its usual place.
   On another computer of the company network instead (a Mac Studio), give its address in the .env
   (`AP_LLM_BASE_URL`, `AP_PAGE_READER_BASE_URL`). A name in the company's DNS (`macstudio.corp.example.com`) also
   needs that DNS domain named, `AP_COMPANY_DOMAINS=corp.example.com` (several: comma-separated): such a name is used
   while every address it has is private, AP Coder connects to the very addresses it checked (never to a second
   lookup's), and no other name is looked up. **A second model machine as the backup** (a Mac mini beside the Mac
   Studio, the same models in its LM Studio): its address in `AP_LLM_BACKUP_URL` (and `AP_PAGE_READER_BACKUP_URL` for
   a page reader on a server of its own), in *Settings* → *Reading* → *For IT*, or server.json's `llm_backup_url` and
   `page_reader_backup_url`. After two calls in a row the main one doesn't answer, the calls go to the backup (the
   calls under way are asked again there); the main one is asked every few seconds and taken back once it answered
   three times, after a minute at least (D-180). The Reading tab and `python -m ap_coder doctor` say which one answers,
   and how fast.
6. **Several requests at once** (for 5,000 invoices a month and more). One request at a time leaves most of a
   graphics card idle; a server that answers several together (continuous batching) reads 2 to 4 pages in about
   the time of one and a half (estimated, from llama.cpp's published batching figures; not measured here). Two
   ways, both offline and both on Windows Server:
   - **LM Studio** (0.4 or newer): load each model with *Max concurrent predictions* (`--parallel` with `lms load`)
     set to 4, and give each model 4 times its context (`--context-length 65536` for the chat model): the context is
     shared by the requests answered together. Then `service install --llm-parallel 4`.
   - **llama.cpp's `llama-server`** (a single program, its CUDA build copied in): one per model, for example
     `llama-server -m gemma-4-12B-it-QAT.gguf --port 1234 -ngl 99 --parallel 4 -c 65536 --cache-type-k q8_0
     --cache-type-v q8_0`, and OvisOCR2 with its `--mmproj` file on another port (`AP_PAGE_READER_BASE_URL`). Then
     `--llm-parallel 4`.

   vLLM batches best but runs on Linux only (WSL2 on Windows Server is possible, not covered here). The KV cache of 4
   requests needs memory: with 16 GB of VRAM keep 4 for the chat model and 2 for OvisOCR2 (`--llm-parallel 2`), with
   24 GB or more, 4 for both.

## 9. Backups

- **Every day, automatically**: a backup in `D:\APCoderData\backups`, when AP Coder starts and once a day while it
  runs; the newest 14 are kept.
  - **When:** set `AP_BACKUP_HOURS=1-5` in the `.env` so the daily copy starts between 1 and 5 in the morning, not
    during the working day. A year's database is 7-9 GB: the copy takes minutes of disk work. If the server was off
    during those hours, the backup is made as soon as the last one is 30 hours old, so no day is skipped (B-1671).
  - **Room:** a backup starts only when the copy and 512 MB more fit on the disk. A full disk would stop every write
    to the database. Otherwise *Settings* → *Data & backups* says it was not made and why (B-1670).
  - Plan the disk for 14 daily copies of the database, plus the invoice files once. It is made in the background: AP Coder serves pages meanwhile, and *Settings* →
  *Data & backups* says while one is being made (B-1553). An update adds its own (`...-before-update-<version>`),
  made before the update goes on, kept until you delete it. Each backup is AP Coder as it was at that moment,
  restorable whole (D-143):
  - `ap_coder-<date>-<time>.db`: the database;
  - `ap_coder-<date>-<time>.files.json`: its list of files, each with its size and SHA-256: the whole `invoices`
    folder (the PDFs and pictures, `originals`, `deleted`, `statements`, the saved emails and their attachments in
    `invoices\emails`), the managers' replies in `emails`, `reference`, `canada_tax_rates.csv` and
    `jde_orchestrations.json` if you keep your own, the JD Edwards postings ledger `jde_postings.jsonl`, and an
    invoice's file kept outside the data folder;
  - `files\<2 digits>\<sha256>`: the files themselves, each content kept once for all the backups. A daily backup copies
    only the files that are new or changed since the last one (2,000 invoices: about a second when nothing changed);
    a file is dropped when no backup kept lists it any more.

  Not in a backup: the `.env` (it holds passwords; set it again on a new server), `server.json` (made by
  `service install`), the caches, logs, exports and reports (made again), and the OCR models (they come with the
  bundle).
- **The folders an Admin may name** (the backups' second copy, the finance hub's folder): on the server only those IT
  lists, or a folder inside one. Give them when installing the service, separated by `;`:
  `service install --allowed-folders "\\fileserver\Backups\APCoder;\\fileserver\FinanceHub"` (kept in
  `server.json` as `allowed_folders`; the other settings are kept). Any other folder typed on a page is refused before
  AP Coder touches it, so a page can't make the server's account sign in to another computer's share (its password
  hash sent there) or copy the invoices out. With none listed, both settings are refused.
- **A copy elsewhere**: an Admin sets *Settings* → *Data & backups* → *Also copy each backup to* a network share, for
  example `\\fileserver\Backups\APCoder` (the `backup_copy_dir` setting; one of the allowed folders above). Each daily
  backup and each update's backup is then copied there too, with the files it needs that the share does not have yet
  (the same layout: the newest 14 daily ones kept), and the page says, for each backup, whether its copy was made or
  why it failed. That folder alone restores the server on a new computer. The service's account must be able to write
  there: a domain account (step 2), with *Modify* on that share folder.
- **What each backup holds**: *Settings* → *Data & backups* lists every backup with when it was made, its invoices and
  files and their size, whether its second copy was made, and whether every file is there (*Complete*, or how many are
  missing or damaged). `python -m ap_coder backup list` says the same on the console, and `backup check` reads every
  file of every backup and compares it with its SHA-256.
- **The server's own backup** (Windows Server Backup, Veeam...): include `D:\APCoderData`, but restore from the
  `backups` copies, not from a copy of the open `ap_coder.db` file.
- **Restoring**: *Settings* → *Data & backups* → *Restore this state* (an Admin; type RESTORE to confirm). The
  database and the files go back to that moment; a file that came in or changed since is moved to
  `backups\set-aside-<date>-<time>` (never deleted), and the state before the restore is backed up first, so it can be
  undone. A file missing or damaged in the backup is taken from the second copy when it has it, else the page says
  which. The user accounts (passwords, roles, who is turned off) and paused logins stay as they are now, not as in the
  backup, and everyone is logged out, the Admin too. `server-update --back` and an update that fails do the same.
  While the restore runs (a minute or two; longer for a year's database), everyone else's pages and requests, on
  every web worker, get *AP Coder is being restored from a backup; back in a minute or two* (a 503 answer, which
  tries again by itself), never a page drawn from a database being replaced; `/healthz` still answers, and the Admin's
  own page waits for the restore. A marker beside the database (`ap_coder.db.restoring`) says a restore runs; it goes
  when the restore ends, whether it worked or not. One left by a restore cut short (the server stopped during it) is
  removed when AP Coder next starts, with a line in `logs\service.log` saying so: then check *Settings* → *Data &
  backups*, and restore again if needed (the state before the restore was backed up first).
- **Restoring from the console** (the same rules): `service stop`, then, in the AP Coder folder:

  ```bat
  .venv\Scripts\python.exe -m ap_coder backup list
  .venv\Scripts\python.exe -m ap_coder backup restore ap_coder-20261201-090000.db
  ```

  then `service start`. It refuses while AP Coder runs on the data folder, and asks first (`--yes` doesn't); a service
  started meanwhile answers *being restored* until it ends. `backup now` makes a backup. Copying a `.db` over
  `D:\APCoderData\ap_coder.db` by hand brings back neither the files nor the accounts as they are now: use the command.
- **A database that is gone**: when `D:\APCoderData\ap_coder.db` is missing but the data folder shows there was one
  (its backups, an update's record in `server.json`, the sign-in codes' key, the access log, invoice files), the
  server does not start with a new, empty database in its place: `logs\service.log` says so. Restore the newest
  backup (`backup list`, then `backup restore <name>`, as above), or put the file back. To begin again with an empty
  database on purpose, run `.venv\Scripts\python.exe -m ap_coder serve --server --new-database` once (it makes the
  database; stop it with Ctrl+C), then `service start`.
- **A new server** (the old one lost): install AP Coder (step 2) and the service, then restore from the share alone,
  **before making the first Admin (step 7)**:
  `.venv\Scripts\python.exe -m ap_coder backup list --from \\fileserver\Backups\APCoder`, then
  `backup restore <name> --from \\fileserver\Backups\APCoder`. The database and every file come back into the new
  data folder, and the backup's accounts with it, because the new server has none yet to keep. Once an account
  exists, a restore keeps the accounts as they are (above): restored after step 7, only that new Admin could log
  in, and the backup's other people would have to be added again. Set again what a backup does not hold: the
  `.env`, the allowed folders, and the second backup folder (a restore clears it, so a backup never chooses where
  later ones go).
- **The sign-in codes' key** (`D:\APCoderData\authenticator.key`, made when the first person sets up their
  authenticator app): it seals each person's authenticator secret, which the database keeps only sealed, so a copy of
  the database alone gives no one's codes away. Every backup holds it. A restore puts it back only where there is none
  (a new server, whose accounts come from the backup), never over the one there, so after a restore here and on a
  new server everyone signs in with their codes as before. Keep the data folder's permissions as step 2 set them (the
  service's account and the administrators only): the key is as private as the data.
  A backup holds the database and the key together: the second backup folder's share must be protected by IT like
  the data folder (only the service's account and the administrators can read it).
- **One at a time**: a backup, a restore and an update's put-back never run at once, on the server across its web
  workers and its background process too (a lock beside the backups). A restore asked for while the day's backup is
  being made says so and changes nothing: try again once the page no longer says a backup is being made. A file
  uploaded while a restore runs is left in place, never moved aside.
- **The finance hub feed** (off until an Admin turns it on, *Settings* → *Finance hub*): its share needs the same
  *Modify* for the service's account. Its schedule runs while staff use the dashboard; for a night run, add a
  scheduled task running `python -m ap_coder hub export` as the service's account (docs/FINANCE_HUB.md).

## 10. Users and roles

An Admin adds people on the *Users* page (`https://apcoder.company.local:8501/admin/users`): a user name, their name
and a role. AP Coder makes up a temporary password and shows it once, on that page: give it to the person (it is not
kept anywhere to look up later). They must change it when they first sign in, then set up an authenticator app
(*Sign-in codes*, below). The same page resets a password (a new temporary one, shown once), resets an authenticator
app, changes a role, and turns an account off or on again (an Admin can't turn off the last Admin).

| Role | Can |
|---|---|
| Read-only | Look at invoices, the review queue, reports, the exports list and Help. Change nothing. |
| Clerk | Also import and process invoices, review and correct them, approve within the limit, park, reject, reopen, bulk approve, export batches, make the Spend page's Excel workbook, and put a vendor on hold or edit its notes (not take it off hold, nor change a held vendor's tax numbers, D-142). |
| Approver | Also give the second approval (always by another account than the first), take a vendor off hold, change a held vendor's tax numbers, approve a held vendor's invoice coded to another vendor, and post vouchers to JD Edwards through the API. |
| Admin | Everything else: Settings, users, the JD Edwards setup, GL accounts and vendors imports, backups and restore, deleting, the Azure check, updating. |
| Manager | Only confirms, or sends back, the coding of the invoices AP sends them (*Manager confirmations*): sees those invoices and nothing else of AP Coder. An Admin sets each Manager's cost centers on the users page; AP picks the Manager when sending (D-138). |

Pages show each person only what their role may use; the server refuses the rest anyway. The JD Edwards settings
are on *Settings* → *JD Edwards E1* from the start on the server (step 2).

The *Spend* page's Excel workbook (every invoice, its lines and its posting) takes minutes for a year of invoices: on
the server a Clerk or above clicks *Make the Excel workbook* and it is made in the background, one at a time for
everyone, the page saying how far it got; *All invoice data (Excel)* then downloads the one made last, with when it was
made (D-156). On a laptop it is made as it is downloaded, as before.

From the server's console, the same:

```bat
.venv\Scripts\python.exe -m ap_coder users list
.venv\Scripts\python.exe -m ap_coder users add mlee --name "Mary Lee" --role approver
.venv\Scripts\python.exe -m ap_coder users role mlee clerk
.venv\Scripts\python.exe -m ap_coder users reset mlee
.venv\Scripts\python.exe -m ap_coder users reset-2fa mlee
.venv\Scripts\python.exe -m ap_coder users deactivate mlee
.venv\Scripts\python.exe -m ap_coder users activate mlee
```

`users add` takes `--name` (the name on their approvals; default: the user name) and `--role` (`readonly`, `clerk`,
`approver` or `admin`; default `clerk`; `--admin` is `--role admin`). On the console the temporary password is the one
you type, twice (`add` and `reset`); the person changes it at their first sign-in. `activate` turns an account that
was turned off on again, with its password as it was. `reset-2fa` forgets a person's authenticator app and resets
their password, printing a temporary one once (see below): the way back in for the last Admin, whom no other Admin
can reset.

**Sign-in codes** (D-146). Every login has two steps: the password, then the 6-digit code an authenticator app on
the person's phone shows (it changes every 30 seconds). Any authenticator app works: Microsoft Authenticator, Google
Authenticator and others; the codes are worked out on the phone and on the server, nothing goes over the internet.

- **The first sign-in**: the temporary password, then their own password, then AP Coder shows a QR code (and the same
  key as text, to type in the app instead). They scan it, type the code the app shows, and are in. An account that
  had already signed in before this version does the same at its next login. Until the code is right there is no
  session: nothing of AP Coder opens.
- **A code** is taken 30 seconds early or late (a phone's clock a little off), and only once. Keep the server's clock
  right (Windows' time service: `w32tm /query /status`); the phones set theirs automatically. A wrong code counts as a
  wrong password does toward the pauses below.
- **A new or lost phone**: an Admin clicks *Users* → *Reset authenticator*. It resets the password too (a phone lost
  or stolen may go with it): the person is logged out everywhere, the page shows a temporary password once, as when
  a person is added; with it they choose their own password and set the app up again. An Admin's own is reset by
  another Admin; the last Admin's, on the server's console: `users reset-2fa <username>`, which prints the temporary
  password once. There are no printed recovery codes: an Admin or the console resets an app in a minute.

Logins that don't match pause, in three ways:

- **One username from one address**: five wrong passwords or codes within 15 minutes pause that username's logins from that
  address for 15 minutes (five wrong *current* passwords on *Your password* count the same, and also log the person
  out).
- **One username from anywhere**: from the 10th wrong password within an hour, from any addresses, that username's
  logins pause for 1 minute, then 2, 4, 8, and 15 minutes after each further one, from every address except those the
  person logged in from in the last 30 days. Someone guessing from many computers gets a few tries an hour; the
  person at their own desk is not kept out.
- **One address, whatever the usernames**: thirty pause that address for 15 minutes (an office's internet connection
  or a remote desktop server is shared: five people mistyping don't pause it). The people who logged in from that
  address in the last 30 days can still log in from it: the pause stops guesses at other names, not the office.

The server's own console (127.0.0.1, no `--proxy`) is never paused by address. Resetting a person's password lifts
what pauses them, their address too; the *Users* page lists paused addresses and usernames ("from anywhere" too) with
*Lift the pause*. Every pause is in *Activity* → *Logins*.

Passwords: at least 12 characters, not the username or the name, not a common password, not one character or a short
pattern repeated. A temporary password (a new account, a reset) works for 3 days: after that, reset it again. Every
sign-in, code that didn't match, lockout, authenticator set up or reset, change to a user and download is in
*Activity*, shown to Admins only.

**Sessions**: a login ends after 30 minutes unused, after 12 hours whatever, and when the browser is closed. IT can
change the first two in `server.json`: `"session_idle_minutes": 30` (5 to 480) and `"session_hours": 12` (1 to 168),
applied when the service starts again. *Your password* shows where else the person is logged in and logs them out
there.

**Separation of duties**: the second approval is always another account than the first, and never an account the
first approver added or reset the password of in the last 7 days (an Admin can't approve their own invoice twice
through an account they made). An Admin can also tick *Settings* → *Review* → *The person who posts a batch to JD
Edwards can't be one who approved its invoices* (off by default). *Activity* → *Controls report* lists the invoices
where one person did several steps (approved, gave the second approval, exported, posted). *Help* → *Report a
problem*'s setup report (versions, settings, the server's names) is an Admin's.

## 11. Updating and going back

A new version comes as a new offline bundle, with its SHA-256: the bundle builder prints it and writes it beside the
ZIP (`APProcessor-offline-<version>.zip.sha256`). Get that value from whoever built the bundle **by another way than
the ZIP** (e-mail, chat, read over the phone): it proves the ZIP is the one they built, so a ZIP changed on a share
or on its way is refused before anything of it runs. Copy the ZIP to the server, then, in the current AP Coder
folder (the one `service status` names: `server-update` refuses to run from another):

```bat
.venv\Scripts\python.exe -m ap_coder server-update D:\Install\APProcessor-offline-0.5.0-20261201.zip --sha256 <the 64 digits> --dry-run
.venv\Scripts\python.exe -m ap_coder server-update D:\Install\APProcessor-offline-0.5.0-20261201.zip --sha256 <the 64 digits>
```

Without `--sha256` it refuses. `--no-hash-check` installs without the check: it warns, and `server.json`'s
`history` records it (`"bundle_hash": "not checked (--no-hash-check)"`). Use it only for a bundle you built
yourself on this server. (`certutil -hashfile <zip> SHA256` shows a file's SHA-256 too.)

The first line shows the steps with the folders and files, and changes nothing (on a server where the service isn't
installed yet, it says there is no service to stop or start). The second does them, in order. First, while the
version running keeps serving staff:

1. checks the bundle: its SHA-256 is the one given, one AP Coder folder, every file it lists, nothing damaged, and
   newer than the one running (and that the disk has room for a copy of the database);
2. unzips the bundle next to the current folder: `C:\APCoder\APProcessor-0.5.0`;
3. sets that folder up from its own `wheelhouse` (offline; its launcher's window shows it);
4. updates a copy of the database with the new version (in `D:\APCoderData\service\update-check`, removed after),
   which proves the update works on your data and says how long it takes;
5. runs the self-check (the sample invoices read right).

Then the switch, the only time staff see AP Coder closed:

6. stops the service;
7. backs up the database and the files (section 9): `backups\ap_coder-<date>-<time>-before-update-0.5.0.db`;
8. updates the database (the new version opens it once);
9. starts the service from the new folder, and waits for it to answer. The backup is then copied to the second
   backup folder, when one is set.

It prints how long the service was down (from the stop to the new version answering) and each step's time, and
keeps them in `server.json`'s `history` (`times`, in seconds). A year's database takes a few minutes for step 7 and
the start; CI checks that with a database of 20,000 invoices it stays under 5 minutes on Windows (11 s on a Linux
test computer, D-188). If step 2, 3, 4 or 5 fails, nothing was stopped or changed: the old version kept serving, and it says
why. If step 6, 7, 8 or 9 fails, it puts the database backup back, switches the service back to the old folder and
starts it again, and says why. Either way the new folder is kept to look at; delete it when done.

**If the power goes off (or the window is closed) during an update**, where it was is in
`D:\APCoderData\service\update.json` at each step. The service's next start (the server booting, or the scheduled
task's 5-minute retry) and the next `server-update` act on it before anything else, and say what they did in
`logs\service.log` or on screen:

- cut short while it prepared, stopped the service or made the backup: the old version starts as it was (its
  database was not touched); a backup cut short is removed;
- cut short while the new version updated the database: the backup made a moment before (the service already
  stopped, so nothing was done since) is put back, and the old version starts;
- cut short at or after the switch: the update is finished and the new version starts (`server-update --back` goes
  back if it turns out wrong). `server-update` goes back instead when the new version does not answer; the service
  goes back when it still runs the old folder (run.cmd not rewritten yet);
- cut short while it went back: going back is finished.

`server-update` then stops there: run it again to update. While one `server-update` runs, another (and the service's
retry) leaves it alone (`service\update.lock`).

**Going back** after an update that went through, when the new version turns out wrong:

```bat
.venv\Scripts\python.exe -m ap_coder server-update --back
```

It stops the service, backs up the database and the files as they are now (`backups\...-before-going-back.db`), puts
back the backup made before the update with its files (keeping the user accounts and paused logins as they are now,
and logging everyone out; files that came in since are moved to `backups\set-aside-<date>-<time>`), and starts the
previous folder again. What was done in AP Coder since the update is
not in that backup (the older version can't open a database the newer one changed), so go back soon, or not at all.
It asks first (`--yes` doesn't); `--dry-run` shows what it would do. If the copy or the backup put back fails (a full
disk, Ctrl+C), it starts the version that was running again, on the database as it was, and says so; once the backup
is back, it starts the previous version.

Going back from a version of November 2026 or later (D-159) to an older one: the older version refuses the backups
the newer one made, the `before-going-back` copy included (*contains triggers, which AP Coder never makes*: the newer
version's own, not planted ones). `server-update --back` is the way back, since the backup it puts back was made
before the update. To restore one of the newer backups, update again and restore it from the newer version.

Two folders are kept: the current one and the previous one. After the next update, the message names the folder two
versions back, which you can delete. What was done, when, is in `D:\APCoderData\server.json` (`history`), and the
installs in the administrator's `%USERPROFILE%\.ap_coder\settings.json`, as on a laptop.

## 12. Troubleshooting

| What you see | What to do |
|---|---|
| The browser can't reach `https://apcoder.company.local:8501` | `service status` on the server. Not running: the end of `D:\APCoderData\logs\service.log` says why. Running: check the firewall rule (step 5) and the DNS alias (`nslookup apcoder.company.local` on a staff PC). |
| The browser warns about the certificate | The certificate doesn't name `apcoder.company.local`, has expired, or lacks the CA's intermediate certificates (step 3: add `ca-chain.pem` after it). |
| "not one of the server's names" (a 400 error) | The name typed is not one of the allowed names: `service install --allowed-hosts apcoder.company.local,apcoder` (each name staff use; it restarts AP Coder), or add it to `AP_ALLOWED_HOSTS` and restart. |
| AP Coder doesn't start: "Port 8501 is in use by another program" | On the server AP Coder keeps the port staff were given, and never moves to another. Find the other program (`netstat -ano \| findstr :8501`) and stop it, or install with another `--port` and tell staff the new address. |
| The task doesn't start; Task Scheduler's *History* says "logon failure" | The account's password changed: `service install` again (it asks for it). Or the account lacks *Log on as a batch job*: `secpol.msc` → *Local Policies* → *User Rights Assignment* → add `APCoderService`. |
| `service status` says "Service host: not running", and stopped on purpose | Someone ran `service stop`: `service start`. |
| The log repeats "The dashboard stopped (exit code 1)" | The lines before it say why: often the port in use (another program on 8501: `netstat -ano \| findstr :8501`), or a certificate file it can't read (the account's rights on `D:\APCoderData\tls`). |
| "LM Studio isn't running" in AP Coder | `schtasks /run /tn "LM Studio server"`, then `lms server status` as its account; *Settings* → *Reading* → *Check again*. |
| The second backup copy fails | The account can't write to the share: use a domain account with *Modify* there (step 9). |
| `server-update` asks for the SHA-256, or says it is not the bundle that was built | Get the SHA-256 from whoever built the bundle (the `.sha256` file beside the ZIP), by another way than the ZIP, and give it with `--sha256`. A different one: copy the ZIP again; still different, ask for the bundle again (it may have been changed). |
| `server-update` says "this command runs from" another folder | Run it in the folder `service status` names (`cd /d` there). If server.json names a folder nobody installed, someone changed it: check, then `service install --folder <the right folder>`. |
| "AP Coder writes only to the folders IT allows" | Add the folder: `service install --allowed-folders "..."` (step 9). |
| "Your temporary password has expired" | An Admin resets it again (*Users* → *Reset password*). |
| `server-update` says the bundle is not newer | It is the version running, or older. To go back to the previous version: `server-update --back`. |
| `server-update --back` says "Nothing to go back to" | No update was made with `server-update` since the last `--back`. To install an older version, ask for its bundle and the backup made before the update. |
| `server-update` says "The update from ... was cut short" | An update stopped part way (a power cut, the window closed): it dealt with it (section 11 says how) and stopped there. Run `server-update` again to update. |
| The log says "The data folder ... is on a network share" (or a network path, a network drive) | Move the data folder to one of the server's own disks (step 2), set `AP_PRIVATE_DIR` to it, and `service install` again; copy the backups to the share instead (step 9). |
| The log says "The database ... is missing, but this data folder had one" | Restore the newest backup (section 9, *A database that is gone*), or put the file back. A new, empty database only on purpose: `serve --server --new-database`. |
| Staff see "AP Coder is being restored from a backup" | An Admin restores a backup (section 9): it ends by itself. |
| Someone forgot their password, or is locked out | An Admin: *Users* → *Reset password*; or on the console `users reset <username>`. A lockout ends by itself after 15 minutes. |
| "That code doesn't match" every time | The phone's clock: set it to set the time automatically. The server's: `w32tm /query /status`, then `w32tm /resync`. Still wrong: *Users* → *Reset authenticator* (a new temporary password too), and they set the app up again. |
| A new phone, or a lost one | An Admin: *Users* → *Reset authenticator* (it resets the password too: give them the temporary password shown). The last Admin: `users reset-2fa <username>` on the console, which prints it. |
| "AP Coder can't check your authenticator codes on this server: its key file ... is missing, damaged or not the one your app was set up with" | `authenticator.key` was deleted or changed in the data folder. Put it back from a copy of the data folder if you have one; otherwise reset each person's authenticator (*Users* → *Reset authenticator*; the last Admin with `users reset-2fa`): they set their apps up again. |
| Pages are slow when many people work at once | `service status`, then Task Manager: when every core is busy, the server needs more cores (step 1); when they are not, `service install --workers N` with N up to the cores. The log names a worker started again after it stopped. |
| Invoices wait long for OvisOCR2 | Set the model server to answer several requests together and raise `--llm-parallel` (step 8, point 6). |
| Anything else | `.venv\Scripts\python.exe -m ap_coder doctor`, and the log. *Help* → *Report a problem* makes a report without invoice data. |

**What `doctor` checks on the server** (when `AP_SERVER_MODE=1`, or `server.json` is in the data folder), besides
what it checks on a laptop: *dashboard network* (the address and port the server listens on, and the address staff
open), *server HTTPS certificate* and *private key* (set, there, and readable by the account you run it as; run it
as `APCoderService` to check the service's own rights), *server names* (the allowed names, a warning when none is set)
and, on Windows, *server service* (the scheduled task: installed, its state, the account, and whether it was stopped
on purpose).
