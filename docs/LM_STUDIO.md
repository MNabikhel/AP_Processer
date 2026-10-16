# LM Studio: the models AP Coder uses, and sharing them with CloseDesk

AP Coder is offline: every model it uses runs in LM Studio on this computer. CloseDesk (the inbox app, same team)
uses the **same LM Studio** on the pilot tester's Mac, often at the same time. This page says what AP Coder uses each
model for, the limits it keeps (the same as CloseDesk's, so the two apps share one copy of each model), and what to
do when something is wrong.

## The setup on the pilot tester's Mac

| Model in LM Studio | AP Coder uses it for | CloseDesk uses it for |
| --- | --- | --- |
| **Gemma 4 12B** or **Qwen 3.5 9B** (chat, can see; one of them) | Suggests a GL account for an invoice line nothing was learned for, and reads each invoice's header from the page images (after OvisOCR2, in the background) as one more independent reader. Optional: without it those lines are left for AP, and the other readers read the header. | Files mail, answers questions, reads scans when OvisOCR2 can't. |
| **OvisOCR2** (0.85B document reader, `ATH-MaaS_OvisOCR2-GGUF` Q8_0, about 1 GB) | The page reader: reads every page of every invoice in the background (`ap_coder/page_reader.py`). | Reads scanned pages. |
| **Qwen embedding model** | **Not used by AP Coder.** It is never picked as the chat model or page reader, not listed in *Settings → Reading*, and never loaded or unloaded. | Search by meaning. |

AP Coder finds both models on its own (no names to type), however LM Studio spells them: `google/gemma-4-12b`,
`gemma-4-12b-it-qat`, `lmstudio-community/gemma-4-12B-it-QAT-GGUF`, `gemma4:12b`; `qwen/qwen3.5-9b`,
`qwen3.5-9b-mlx`, `lmstudio-community/Qwen3.5-9B-GGUF`, `qwen3.5-9b@q4_k_m`; `ath-maas_ovisocr2`, `ovis-ocr2`,
`OvisOCR2-GGUF` (`local_llm.team_chat_model`, `local_llm.is_chat_model`, `page_reader.document_reader`). Whichever
chat model is **loaded** is the one used, so switching between Gemma and Qwen is only a matter of which one you load.

## Limits AP Coder follows (aligned with CloseDesk)

| | CloseDesk (`controller_inbox`) | AP Coder |
| --- | --- | --- |
| Which chat model | `CONTROLLER_INBOX_LLM_MODEL` if set; else the chat model loaded now; else the one it last saw loaded. Never the first on LM Studio's list, never OvisOCR2 (`local_llm._pick_model`). | `AP_LLM_MODEL` if set; else the chat model loaded now (whichever app loaded it); else Gemma 4 12B if downloaded, else Qwen 3.5 9B; else none. Never the first on the list, never OvisOCR2 or an embedding model (`local_llm.pick_model`). |
| Chat context | Loads/reloads its chat model with **16,384** tokens (`min_context_tokens`), or the model's maximum if less (`ensure_context`, `use_chat_model`). | Never reloads a loaded model. *Settings → Reading → Load* loads a chat model with **16,384** tokens, or its maximum if less (`page_reader.load_model`). |
| Page reader context | Loads OvisOCR2 with **20,480** tokens before reading; a shorter instance is replaced, the chat model left alone (`load_for_reading`). | The same: **20,480**, reloaded only when shorter, failures not retried for 30 min (`page_reader.load_reader`). |
| Page image | OvisOCR2: **200 DPI, long side ≤ 2,048 px** (LM Studio shrinks larger ones anyway). General model: 100 DPI, ≤ 1,600 px. | OvisOCR2: the same 200 DPI / 2,048 px (`page_reader.OVIS`, `_render`). A general model only if a developer names one: 150 DPI / 1,600 px. |
| Page reading tokens | OvisOCR2 **12,288**; a general model 8,192. Greedy (temperature 0); one retry with Qwen's sampling when it loops. | The same (`page_reader.transcribe`). |
| Pages | ≤ 20 per request, one page at a time. | ≤ 5 per invoice (`AP_PAGE_READER_MAX_PAGES`), **one page at a time** per process (`page_reader.READ_LOCK`). |
| Chat reply tokens | 450 (filing), 500 (chat); 2,048 when the model thinks. | 4,096 (`AP_LLM_MAX_TOKENS`); the accounts reply is about 60 tokens a line. |
| Prompt size | Filing packet ≤ 6,000 characters; chat sized to the loaded context. | Invoice text ≤ 24,000 characters, start and end kept (`AP_LLM_MAX_PROMPT_CHARS`): with the chart and the reply it fits 16,384 tokens. |
| Temperature | 0 (filing), 0.2 (chat). | 0 (`AP_LLM_TEMPERATURE`). |
| Thinking | Off: `reasoning_effort` "none" (or "low") as LM Studio 0.4.8+ reports the model takes; answers also read from `reasoning_content` and `<think>`. | Off: `reasoning_effort: "none"` and `chat_template_kwargs.enable_thinking: false`, dropped one by one if a server refuses them; answers also read from the reasoning and `<think>` (`local_llm.THINKING_OFF`). Qwen 3.5 has no `/no_think` switch. Gemma 4 thinks only when its template is told to (off by default); a thought channel (`<|channel>thought … <channel|>`) is never read as the answer. |
| Timeouts | Filing 90 s; chat and thinking 300 s; a page 900 s; loading a model ≥ 120 s; model list 2 s. | Chat 600 s (`AP_LLM_TIMEOUT_SECONDS`); a page 1,200 s for each word (`AP_PAGE_READER_TIMEOUT_SECONDS`); loading 300 s; model list 1.5–3 s. |
| Retries | Never sends a timed-out request again by itself; stops after two timeouts or three failures in a row. | **Never sends a request again by itself** (`max_retries=0` for chat and pages); a page that failed is read later. |
| Parallel requests | One reading job at a time; no parallel filing. | **One chat request at a time** per process, whatever `--workers` says (`local_llm.CHAT_LOCK`), and one page at a time. The chat model and OvisOCR2 may answer at once: they are different models. |
| Unloading | Only its own chat model when you switch it in Setup; never OvisOCR2 or the embedding model. | **Never unloads the chat or embedding model.** Only an OvisOCR2 instance loaded shorter than a page needs, once the 20,480-token one is in (as CloseDesk does). |
| Model missing | Setup says what to load; OCR alone meanwhile. | *Settings → Reading* says what to load; invoices are read by OCR and wait for a person; lines are left for AP. |

## Settings in LM Studio for both apps

1. **Developer → Start server** (`http://127.0.0.1:1234`). LM Studio 0.4.8 or newer (it honours "thinking off").
2. **My Models → Qwen 3.5 9B → Context Length 16384**, and **OvisOCR2 → Context Length 20480**. Then a model LM
   Studio loads on its own when a request names it (just-in-time loading) gets the same size the apps ask for, and
   neither app has to reload it.
3. Leave **Just-in-Time model loading** on, but turn off the server setting that keeps only the last model loaded
   just in time (*auto-evict*): both apps use the chat model and OvisOCR2 side by side, and that setting would
   unload one to load the other.
4. Pick one build of each model (GGUF or MLX) and keep it. LM Studio treats `qwen3.5-9b` (GGUF) and
   `qwen3.5-9b-mlx` as two models: if one app ends up loading the other, both sit in memory. AP Coder uses
   whichever is loaded. OvisOCR2 is a GGUF model.

**Memory.** Gemma 4 12B (QAT Q4_0) is about 7.0 GB plus its 0.18 GB vision file; Qwen 3.5 9B at Q4_K_M is about 5.6 GB plus its 0.9 GB vision file; OvisOCR2 at Q8_0 about 1 GB, an
embedding model under 1 GB, plus the context of each (16,384 and 20,480 tokens). A 16 GB Mac holds all three; on
8 GB, keep only OvisOCR2 loaded (AP Coder codes from AP's history without the chat model). AP Coder and CloseDesk
then share these copies; neither loads a second one.

## Using Gemma 4 12B instead of Qwen 3.5 9B (Windows laptop)

Gemma 4 12B (Google, Apache 2.0) does the same two jobs as Qwen 3.5 9B: it suggests GL accounts and reads the
invoice header from page images. Nothing in AP Coder needs changing: it uses whichever chat model is loaded. The
page reader stays OvisOCR2.

1. **LM Studio itself**, not *Bionic*: <https://lmstudio.ai/download/latest/win32/x64> (0.4.8 or newer).
2. **Download the two Gemma files** (in a browser if LM Studio's *Discover* can't reach the internet):
   * model, 6.98 GB: <https://huggingface.co/lmstudio-community/gemma-4-12B-it-QAT-GGUF/resolve/main/gemma-4-12B-it-QAT-Q4_0.gguf?download=true>
   * vision file, 175 MB: <https://huggingface.co/lmstudio-community/gemma-4-12B-it-QAT-GGUF/resolve/main/mmproj-gemma-4-12B-it-QAT-BF16.gguf?download=true>

   QAT is Google's own 4-bit build, trained to keep its quality at 4 bits. And OvisOCR2, if it isn't there yet:
   * <https://huggingface.co/bartowski/ATH-MaaS_OvisOCR2-GGUF/resolve/main/ATH-MaaS_OvisOCR2-Q8_0.gguf?download=true>
   * <https://huggingface.co/bartowski/ATH-MaaS_OvisOCR2-GGUF/resolve/main/mmproj-ATH-MaaS_OvisOCR2-f16.gguf?download=true>
3. **Put each model's two files in its own folder** in LM Studio's models folder (*My Models* shows where; usually
   `C:\Users\<you>\.lmstudio\models`), the vision file next to its model:
   `lmstudio-community\gemma-4-12B-it-QAT-GGUF\` and `bartowski\ATH-MaaS_OvisOCR2-GGUF\`. Restart LM Studio.
4. **My Models → Gemma 4 12B → Context Length 16384**, GPU offload at the maximum; **OvisOCR2 → 20480**, left
   unloaded (AP Coder loads it).
5. **Developer → Start server**; Just-in-Time loading on, auto-evict off (above).
6. **Load Gemma 4 12B, and unload Qwen** if it is loaded: with both loaded, AP Coder uses the first one LM Studio
   lists. Leave Gemma's *Thinking* off (its default).
7. **Double-click APProcessor.** Its summary says *Chat model: …gemma-4-12B… loaded in LM Studio (can see pages)*
   and *Chat model test passed*; *Settings → Reading* shows the chat model as Gemma and OvisOCR2's self-test once
   it has run. Nothing else to check.

On an 8 GB graphics card Gemma and OvisOCR2 together (about 8.2 GB) spill a little into the computer's memory:
slightly slower, nothing else. To go back to Qwen, load Qwen and unload Gemma.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| *Settings → Reading*: "no model is loaded" | Load Qwen 3.5 9B in LM Studio (or *Models in LM Studio → Load*). AP Coder no longer picks some other downloaded model by itself. |
| "LM Studio couldn't load OvisOCR2 … not enough memory" | Close other apps or unload a model you don't need. AP Coder asks again in half an hour, or when Settings are saved. |
| AP Coder uses Qwen although Gemma is loaded (or the other way round) | Both are loaded: unload the one you don't want. |
| Two copies of Qwen in LM Studio's loaded list | One app loaded a different build or context. Unload one; set the per-model Context Length (step 2). |
| CloseDesk is slow while AP Coder reads pages | Expected on a laptop CPU: they share it. AP Coder sends one page at a time; *Process invoices* never waits for the page reader. |
| "The model spent its answer thinking" | Update LM Studio (0.4.8+), or set the model's Thinking off in LM Studio. |
| A request times out | LM Studio may be loading a model or busy with CloseDesk. Try again, or raise `AP_LLM_TIMEOUT_SECONDS` / `AP_PAGE_READER_TIMEOUT_SECONDS`. |
| The embedding model shows up somewhere | It shouldn't: AP Coder ignores it. Report it. |
