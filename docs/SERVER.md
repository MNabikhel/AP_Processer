# AP Coder on a company server (for IT)

This guide sets AP Coder up on one company server for the whole AP team: a Windows Server, a Mac (section 14) or a
Linux computer (section 15). On Windows it runs as a Windows scheduled task (not a Windows service). Staff open it in
their browser over HTTPS and log in with their own account, and nothing is installed on their PCs. Everything stays
offline: AP Coder and LM Studio run on the server and connect to nothing on the internet.

Laptop mode is not changed by any of this: a laptop still runs AP Coder for one person at 127.0.0.1, with no
logins. The server mode is switched on by `AP_SERVER_MODE=1` (step 2). A laptop that ran AP Coder for one person and
now lets a few colleagues in, on the same data:
[section 16](#16-from-one-persons-laptop-to-a-few-people-on-the-same-laptop).

On a Mac (a Mac mini, one of the pilot's layouts in section 1) AP Coder runs as a LaunchDaemon instead of a
scheduled task: [section 14](#14-on-a-mac) says what is different there; on Linux it is a systemd unit:
[section 15](#15-on-linux). The rest of this guide holds for them too.

In this guide (on Windows; sections 14 and 15 give the Mac's and Linux's own):

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
· [12. Health and monitoring](#12-health-and-monitoring) · [13. Troubleshooting](#13-troubleshooting) ·
[14. On a Mac](#14-on-a-mac) · [15. On Linux](#15-on-linux) ·
[16. From a laptop to a few people](#16-from-one-persons-laptop-to-a-few-people-on-the-same-laptop)

## 1. What the server needs

| | Minimum | Better |
|---|---|---|
| Windows | Windows Server 2019, 2022 or 2025, 64-bit (Windows 11 Pro: its licence, below) | |
| Processor | 8 cores (the OCR of scans runs on the processor) | 16 cores |
| Memory | 32 GB | 64 GB |
| Graphics card (for LM Studio) | NVIDIA with 12 GB of VRAM (an estimate) | 16 GB of VRAM or more (an estimate) |
| Disk | 60 GB free on an SSD | 200 GB (about 1 TB for 25,000 invoices a month: below) |
| Python | Python 3.12, 64-bit, installed for all users | |
| LM Studio | 0.4.8 or newer, with its models copied in | |

This table is for 25 people or more, with LM Studio on the same server (the last table below). A pilot of up to 10
people runs on one machine that does everything, AP Coder and both AI models: AP Coder's part measured on 4 vCPUs and
16 GB (*The pilot* below).

Why these numbers:

- **The models** (docs/LM_STUDIO.md): the chat model Gemma 4 12B is about 7.2 GB, or Qwen 3.5 9B about 6.5 GB;
  OvisOCR2, the page reader that reads every page of every invoice, about 1 GB; the embedding model under 1 GB; plus
  the room each needs for its context (16,384 and 20,480 tokens). Estimated from those sizes (none of the
  graphics-card figures in this section has been measured on a graphics card): on 8 GB of VRAM they spill into the
  server's memory and are slower; 12 GB holds them, 16 GB leaves room. Without a graphics card AP Coder still works,
  but reading each page takes minutes on the processor (4.5 to 5.5 minutes on 4 vCPUs, measured: *The pilot* below)
  instead of seconds (estimated) on a graphics card.
- **Disk**: each AP Coder version is 2 to 3 GB (its packages and OCR models), and two are kept (the current one and
  the previous one, to go back to). The models are about 10 GB. The data grows with the invoices: the database about
  34 KB an invoice (measured: 3.5 GB at 100,000 invoices, 10 GB at 300,000, a year at 25,000 a month), the invoice
  files about 0.5 MB each, and the backups: up to 32 copies of the database on the server (14 days, 8 weeks, 12
  months, D-187) plus one copy of each invoice file (each content kept once, D-143).
- **How many people and invoices** (D-111, measured again for 300-400 people: docs/SCALE.md). *Measured* on a
  4-core test machine with the bench's stand-in for the models: simulated people, each clicking every 10 seconds on
  average through the review queue, invoices, their pages, approvals, search, the reports and the exports, against
  the service's own web workers (`--workers 4`, two capture processes):

  | Database | People at once | Half the pages within | 95% within | 99% within | Approvals and checks, 95% within |
  |---|---|---|---|---|---|
  | 100,000 invoices (four months at 25,000 a month) | 100 | 0.07 s | 0.28 s | 0.48 s | 0.16 s |
  | 300,000 invoices (a year), for an hour | 100 | 0.10 s | 0.65 s | 2.0 s | 0.39 s |
  | 300,000 invoices, a database's first 10 minutes (never served) | 100 | 0.12 s | 0.90-0.98 s | 2.2-3.2 s | 0.40-0.45 s |
  | the same since D-204 (made ready before it answers) | 100 | 0.13 s | 1.06 s | 2.5 s | 0.51 s |

  No error and no "database is locked" in any. The reports (Spend, Month-end, Vendors, Insights, Sales tax) are worked
  out again by the background process after a change (D-198): the person who opens one is given the figures of a moment
  before at once, and nothing waits behind that work in a web worker. At its start the background process works out
  again those asked in the last day and each report as it first opens, and from then on keeps those and the last view of
  each report people opened up to date (D-200), so the first person to open one, in the morning too, doesn't wait for it
  either; a report opened before that work is done says it is being worked out and opens by itself once it is ready
  (D-201). A big PO's invoices are kept with what each bills (D-202). Over the hour at 300,000 the slowest 1% are opens
  of the biggest vendor's invoices. In a database's first 10 minutes the slowest 1% are each web worker's first opens of
  an invoice (3 to 6 s: that worker's memories built) and the reports asked before the background process has worked
  them out (3 to 4 s), so its 99% within 3 s is at the edge (2.2 and 3.2 s in two runs; building each worker's memories
  as it starts was tried and made the whole worse: D-203). Since D-204 that work is done before the server answers
  (the table's last row): on a database that never had the reports they are worked out before the port opens (92 s
  at 300,000), and each web worker builds its memories before it takes a request (about 13 s), so a first start of a
  big database takes longer before the dashboard answers (below).
  Memory over the hour at 300,000: each web worker 0.7 to 0.75 GB at the end, flat in the second
  half (its caches bounded, D-195), the background process about 0.5 GB, each capture process 320 MB. 200 people at once
  on these 4 cores (50 a core, four times what 400 people put on each of Phase 2's 32) fill them: 95%
  within 5.3 s, no error.

  *Estimated* for Phase 2, the company server (from those numbers: 100 people on 4 cores is twice the load a core of
  400 people on 32; to be measured on the real server), beside the pilot's (measured: below):

  | | The pilot (Phase 1): up to 10 people | Phase 2: 300 to 400 people, 25,000 invoices a month |
  |---|---|---|
  | AP Coder | one machine for everything (below), recommended a Linux workstation with an NVIDIA graphics card (section 15): `--workers 4` and 2 to 3 capture processes (measured on 4 vCPUs, 16 GB: below) | a Windows virtual machine, 32 vCPUs, 64 GB: 28 web workers and 20 capture processes by default (step 6) |
  | The AI models | LM Studio on the same machine, in the graphics card's memory (step 8; *Memory on one machine*, below) | a model machine of their own, the pilot's machine as its backup (step 8, point 5) |
  | Disk | 200 GB | about 1 TB on an SSD: a year's database 10 GB and its backups about 320 GB, a year's invoice files 150 GB, and room to grow |
  | Reading the day's invoices | 60 (half of them scans) in the review queue 4.1 minutes after they came, every page read 30 minutes after (measured: below, the graphics card's speed assumed) | 1,250: in the review queue 64 minutes after they came (half of them scans, on the 4-core test machine, the stand-in model's time shortened); at full speed *estimated* 3.5 hours to come in and 8 hours of the model machine's reading with 2 requests at once: its speed decides (Phase 0 measures the model machine; `--llm-parallel`, step 8) |

  For 25 people or more, on one Windows Server with LM Studio on it (the layout first planned, D-111; the graphics
  card's rows estimated from an assumed 15 s a page, not measured on a graphics card):

  | | 5,000 invoices a month, 25 people | 10,000 invoices a month, 50 people at once |
  |---|---|---|
  | Processor | 8 cores, 7 workers (the default) | 16 cores, `--workers 8` (8 for OCR and the rest) |
  | Memory | 32 GB | 64 GB |
  | Graphics card | 16 GB of VRAM, `--llm-parallel 2` | 24 GB of VRAM or more, `--llm-parallel 4` (step 8) |
  | The graphics card's day | 2 to 3 hours of reading (about 500 pages a working day, every invoice read) | 4 to 6 hours; about half with clean digital PDFs left out (step 6) |

  The reading runs in the background: nobody waits on it, but invoices come into the queue later on a busy day.
- **The pilot: up to 10 people** (D-207). *Measured* on GitHub's Windows Server machine (4 vCPUs, 16 GB, no graphics
  card), the size of the smallest machine a pilot would get (`.github/workflows/pilot-size.yml`, 9 Nov 2026):
  - **10 simulated people for 10 minutes on 20,000 invoices** (a year of a pilot's), the service's 4 web workers, the
    benches' stand-in for the models: half the pages within 223 ms, 95% within 740 ms, 99% within 1.56 s;
    approvals and checks 95% within 156 ms; no error and no "database is locked". All of AP Coder's processes
    together used 2.0 GB of memory at the end of the run, 2.5 GB if each one's peak is added (each web worker 0.25 to
    0.29 GB, the background process 0.6 GB).
  - **A day's 60 invoices (half of them scans) dropped at once**, the real OCR on those 4 vCPUs: all 60 in the
    review queue in 4.1 minutes. With the stand-in model taking a graphics card's assumed 8 to 20 s a page, two at
    once, every page was read and reviewed 30 minutes after the drop.
  - **Without a graphics card** (the release check of `v0.3.0-pilot9`, 4 Nov 2026, `.github/workflows/release.yml`:
    the same kind of machine, the real models in LM Studio, OvisOCR2 Q8_0 and Qwen 3.5 9B Q4_K_M): a page read in
    4.4 to 5.5 minutes, a chat answer in 1.8 to 8.6 minutes (a whole coding question 4.4 to 8.6); the ten sample
    invoices took 76 minutes. So a
    pilot reading 50 to 60 invoices a day needs a graphics card (about 16 GB of VRAM, or Apple silicon with enough
    memory) on the machine that runs the models. Its speed there is estimated (above), and the burn-in measures it.

  **The pilot's machine: one machine that does everything**, AP Coder and both AI models (OvisOCR2 and the chat
  model, in LM Studio), each running as it is (docs/PILOT_PLAN.md):
  - **Recommended: a Linux workstation with an NVIDIA graphics card of 16 GB or more, on Ubuntu LTS**, AP Coder as a
    Linux service (section 15) and LM Studio's headless server beside it; 8 cores or more, 32 GB of memory, 200 GB on
    an SSD. The models sit in the card's own memory, so the machine's memory is AP Coder's and the system's; Linux
    needs no server licences or user licences; its disk is encrypted and unlocked by the machine's own TPM at boot,
    so it starts again by itself after a power cut; IT runs it with its usual Linux tools, over SSH. IT puts it on
    the company network as it would any server (a fixed address, the name staff type of step 4, a firewall that lets
    in only AP Coder's port and SSH), in a locked spot, on a UPS.
  - **Or a Mac mini** with memory enough for AP Coder and both models together (32 GB: *Memory on one machine*,
    below), as a macOS service (section 14). After every restart, a power cut included, FileVault waits until
    someone unlocks the Mac (section 14): a power cut at night leaves AP Coder down until then.
  - **Or a Windows Server virtual machine with a graphics card in a cloud region in the company's country**,
    everything on it (LM Studio as in step 8). Nothing to buy, but it needs a site-to-site link to the company
    network (for the staff's browsers, JD Edwards and the AP inbox's flow) and the cloud's quota for a virtual
    machine with a graphics card, asked for first.

  Still possible, no longer the recommendation: a small Windows Server virtual machine for AP Coder (4 vCPUs, 16 GB:
  measured above) and a machine on the network that only runs the models (step 8, point 5): two machines to run, and
  Windows Server's licences. A Windows PC with a graphics card would need Windows Server too (*Windows 11 Pro*,
  below). Whichever machine runs the models holds invoice content in LM Studio's log (step 8, point 7).
- **Memory on one machine.** AP Coder's own sizing (step 6; `default_workers` and `capture_processes` in
  `ap_coder/web/server.py`) counts the whole machine as AP Coder's: it doesn't know the models' memory. On a 12-core
  Mac with 32 GB it starts 11 web workers and 8 capture processes; on a 20-core workstation with 32 GB, 16 and 8. A
  pilot of up to 10 people sets `--workers 4` (`service install --workers 4`) and `"capture_processes": 2` (or 3) in
  the data folder's `server.json`, then restarts the service: the configuration measured above (4 web workers, and 2
  capture processes on those 4 vCPUs). On a Mac the models share the Mac's memory: about 12 GB for both (estimated
  from their sizes, above), AP Coder about 3 GB (2.0 to 2.5 GB measured above, more while scans are read) and macOS about
  4 GB, so 32 GB holds them with room for the database's cache. With a graphics card the models sit in the card's
  memory.
- **Windows 11 Pro and Windows 11 Enterprise** run the server mode too, technically: the service is a Task Scheduler
  task (step 6), not a Windows service, and Task Scheduler is part of Windows 11 too. But Microsoft's Windows 11
  licence (its OEM terms of April 2024, sections 2(c)(v) and 2(d)(iii)) says you may not "use the software as server
  software or to operate the device as a server", except that up to 20 other devices may use its file, print, Internet
  information, connection-sharing and telephony services. Those are Windows 11 Pro's terms as it comes with a
  computer. Windows 11 Enterprise is licensed under the company's own volume licensing agreement (Microsoft's Product
  Terms for the Windows desktop operating system), which this guide has not read for this use: do not take it to
  allow more than Pro's terms without checking. Whether serving AP Coder to the team from a Windows 11 computer,
  Pro or Enterprise, is allowed is the company's licensing decision (its licensing contact or IT knows which
  agreement applies); for anyone but the computer's own user, the safe choice is Windows Server. CI tests AP Coder
  on Windows Server only (GitHub's Windows machines; the task itself is not registered there, its commands are
  checked), so on Windows 11 run `service install` and `service status` on that computer before staff use it. A
  laptop serving others stays docked and plugged in, with sleep and hibernation off, closing the lid set to do
  nothing, and its battery set to stop charging at about 80% (its battery then covers a power cut);
  [section 16](#16-from-one-persons-laptop-to-a-few-people-on-the-same-laptop) has the steps.
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

### Without a company CA: a self-signed certificate (a prototype)

For a prototype among a few colleagues, before IT is asked for a certificate, AP Coder runs with one you make
yourself. AP Coder makes no certificate, and neither Windows nor the bundle's Python writes the two PEM files on its
own (PowerShell's `New-SelfSignedCertificate` makes one in the Windows certificate store, whose private key still
needs `openssl` to become a PEM file). So use `openssl`: Git for Windows has it
(`C:\Program Files\Git\usr\bin\openssl.exe`, version 1.1.1 or newer for `-addext`), and so has any Mac or Linux
computer, where the two files can be made and copied over. In the data folder's `tls` folder (make it first):

```bat
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=AP-LAPTOP" -addext "subjectAltName=DNS:AP-LAPTOP,DNS:ap-laptop.company.local" -keyout apcoder.key.pem -out apcoder.pem
```

It writes `apcoder.pem` (the certificate) and `apcoder.key.pem` (its private key: it never leaves the data folder).
`python -m ap_coder share` (section 16) runs this same command for the computer's own names when it finds `openssl`,
and says so when it does not.

- **The name** is the one colleagues type: the computer's own name (`hostname` shows it) and, when the office DNS
  gives it one, its full name. Not its address: a laptop's changes between the Wi-Fi and the dock. AP Coder answers to
  the computer's own names without `--allowed-hosts` (step 4).
- **825 days, and the same two files all that time.** AP Coder tells each browser to come back over HTTPS only, for
  a year (HSTS): once a browser trusts this certificate, another one it does not trust is an error with no way
  past. An Admin sees a banner on every page from 30 days before the certificate ends.
- **What colleagues see** at `https://AP-LAPTOP:8501`: the browser's warning page ("Your connection isn't private",
  `NET::ERR_CERT_AUTHORITY_INVALID` in Edge and Chrome), because nobody their PC trusts signed the certificate.
  *Advanced* → *Continue to AP-LAPTOP (unsafe)* opens AP Coder, the connection encrypted all the same; the warning
  comes back in each new browser session. Someone who clicks past it can't tell the laptop from another computer
  answering in its name, so this is for a few people on the office network, for a short time.
- **To trust it on a few PCs** (no warning): give each colleague `apcoder.pem` (never the key file) and, in their own
  Command Prompt, `certutil -user -addstore Root apcoder.pem`. Windows asks them to confirm and shows the
  certificate's thumbprint: it must be the one `openssl x509 -in apcoder.pem -noout -fingerprint -sha1` prints on the
  laptop. Edge and Chrome then open AP Coder without a warning once they are started again (Firefox keeps its own
  list). A company policy may forbid adding a certificate this way: then it is IT's to do, or to give a real one.
  This was not tried on Windows for this guide: try it on one colleague's PC first.
- **Ask IT for a real certificate** (above) before more than a handful of people use AP Coder, when colleagues may
  not trust a certificate on their own PCs, and at the latest when AP Coder moves to a server of its own: a
  certificate from the company's CA for a name in its DNS shows no warning and needs nothing on anyone's PC.

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
anyone is logged on or not. (Why a task and not a classic service, with nothing to download: docs/decisions.md D-100.
On a Mac it is a LaunchDaemon: section 14; on Linux a systemd unit: section 15.)

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

**Workers** (`--workers N`, kept in `server.json` as `"workers"`): one Python process works on one request at a time
(about 0.1 s of the processor a request on 300,000 invoices, so some 10 a second) however many cores the server has, so
the service runs N *web workers*, all answering on the same port, and one *background process* beside them for the work
nobody waits on (OvisOCR2's reading, the AI reviewer, the Azure check, JD Edwards, the review queue checked again after
a change of settings, the daily backup, the finance hub's schedule), and for the long downloads (search to Excel, the
Activity CSV, the duplicate audit, the controls report: the page says how far the file got and downloads it once made;
Activity's tiles count a month at most on the server, a longer period's report is that download; each person's own,
kept an hour in `.cache\downloads\made`). The invoices brought in (uploads, the invoices folder, a scheduled watch) wait
in an import queue in the database, kept over a restart, and are read by *capture processes* of their own, one per core
but two (`server.json` `"capture_processes"`, 1 to 64, or `AP_CAPTURE_PROCESSES` in the .env to choose; D-168), never by
a web worker: a big batch doesn't slow the pages, and colleagues' batches are read in turn. A file a scanner or a mail
rule saves in the invoices folder shows on *Import invoices* within a minute (the folder is listed once for every
worker, D-176). A worker, the background process or a capture process that stops is started again on its own (a download
it was making is made again). `--workers 1` runs everything in one process, as before. Stopping (`service stop`) gives
the requests under way 25 seconds, then ends them: the service is stopped within 30 seconds.

- **The default** (D-153): one worker per processor core but an eighth of them, and no more than fit in the memory at
  1.5 GB each, a quarter of the memory (at least 4 GB) being kept for the system, the background process and the
  database's cache. 32 cores and 64 GB: 28 workers; 16 cores and 32 GB: 14; 4 cores and 16 GB: 4; 8 GB: 2.
- **Each worker works on 4 requests at once** (`server.json` `"worker_threads"`, 1 to 40; the others wait their
  turn): one process works on one at a time anyway, and each request under way holds what it read (about 110 MB on
  a big database). With anyio's default of 40, 16 people at once took a worker to about 2 GB; with 4, to 1.3 GB, the
  same work done. A worker stays under 1 GB on 300,000 invoices (an hour of 100 people: 712-735 MB at the end, 814 MB at most).
- **Reports and exports take turns**: the reports (Activity, Spend and its workbook, Month-end, Sales tax and its
  CSVs, Insights), the exports (the Exports page, a batch, its files, the JD Edwards dry run and worksheet, the
  training ZIP), Report a problem, a backup or restore, and the buttons that wait on LM Studio or JD Edwards run 2 at
  a time in each worker, beside its 4, so they never hold up the other pages. One that waits more than 30 seconds
  for its turn says AP Coder is busy, to try again in a minute. The longest downloads (search to CSV or Excel, the
  Activity CSV, the duplicates CSV, Month-end's accruals CSV and the controls report) are made by the background
  process instead, the page saying how far the file got (D-169, D-206).
- **A worker is started afresh** past its share of the memory (that 1.5 GB share, at least 2 GB; the variable
  `AP_WORKER_MAX_MB`, 0 for never) or after about 50,000 requests (a number of its own, so they don't all go
  together; `AP_WORKER_MAX_REQUESTS`). It takes no new requests, finishes those under way and what they started (an
  import, a backup), and a new one takes its place as it ends; one worker at a time (the next only once the new
  one serves), and never while an import, a backup or a report of its own runs. Nobody is signed out (logins are in the database). The service log says "A web
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
again if it stops (after 5 seconds, then longer, up to 5 minutes). **A first start takes longer before the dashboard
answers** (D-204): on a database that never had the reports (a new one, or one brought in from elsewhere without
them), AP Coder works out each report's first view before it opens its port (connections are refused meanwhile;
`service.log` says "Working out the reports' default views before the dashboard answers (this database never had
them)..." and then how many and how long), and at every start each web worker builds what an invoice's first opening
reads before it takes a request (its `web-<n>.log` says so). Measured on a test computer (4 cores): at 60,000 invoices
and 2 web workers it answered 13 s after its start (the reports 8 s, each worker 3 s) where it took 2 s before; at
300,000 and 4 web workers the reports took 92 s (none is begun past 200 s, and their process is ended at 215 s, a few
seconds more to end it: the rest is then worked out after the start) and each worker about 13 s (none of its steps
begun past 25 s). A restart, or an update of a database that had the reports, works none out before it answers
(`service.log` says so), only the workers' memories (5 s at 60,000), and the reports are brought up to date while it
serves.
The task itself comes back within 5 minutes if the
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
`access-2.log`... And `D:\APCoderData\logs\background.log`: what the background process does, the work nobody waits on (it starts, the page reader's self-test, each invoice read, each AI review, JD Edwards; 5 MB, then up to 5 older files). With several workers, each also has its own log, `web-1.log`, `web-2.log`... (its access log's number; 5 MB, then up to 5 older files): what it says, a worker started afresh among it (its warnings also reach `service.log`, with their time). Each download (a batch's files or ZIP, the directory's export, a
backup, a report) is also in *Activity* → *Logins*, with the person. The daily database backup runs as on a laptop
(step 9).

**The system's log** (D-187): what goes wrong is also written to Windows' *Application* log (the Event Viewer, and
whatever monitoring reads it), source `AP Coder`, one event number for each kind, so a rule can pick one: **101** a
backup failed, or its copy to the second folder (a warning); **102** a full disk (a backup refused, or invoices no
longer read); **103** the background process or a capture process stopped (or, as `/readyz` finds, gives no
heartbeat); **104** the HTTPS certificate expires within 30 days (a warning) or has expired; **105** a model server's
calls moved to its backup (a warning). The same message at most once an hour by each process. Windows lets only an
administrator make a new source, so make it once, in an administrator Command Prompt:

```bat
eventcreate /L APPLICATION /T INFORMATION /SO "AP Coder" /ID 1 /D "AP Coder may write to this log."
```

Until then these lines are in AP Coder's own logs only. (On macOS and Linux they go to syslog as `ap-coder`.)

**HTTPS**: TLS 1.2 and 1.3 only; on TLS 1.2 only ECDHE with AES-GCM or ChaCha20 (no AES-CBC, no SHA-1 ciphers).
Browsers from the last ten years have them. Answers carry no `server:` header.

**Sizes**: a request before a login takes at most 64 KB (the login form); after it, 260 MB. *Import invoices* takes
up to 200 files and 250 MB per upload, each file up to 50 MB (a saved email 100 MB), and each person up to 2 GB or
120 uploads an hour; more is refused with a plain message. A larger batch: an Admin puts the files in the invoices
folder.

A double-click on `APProcessor.bat` on the server (or on a desktop shortcut left from before it was the server) starts
no second AP Coder on the server's data: while the server runs it opens the server's address in the browser, and
while it is stopped it says so and starts nothing, since the data has sign-in accounts (or the sign-in codes' key, or
its `server.json`). The same holds for `python -m ap_coder serve` without `--server`. Stop and start the server with
`service stop` / `service start`. To open that data on this computer alone again, with no sign-in, takes a step made
on purpose: `python -m ap_coder serve --without-sign-in`, only while the server is stopped (D-211).

## 7. The first admin

Two ways, both on the server itself (nobody can sign up from another computer):

**In a browser on the server** (D-243): while there is no account at all, open `https://127.0.0.1:8501` on the
server and log in with the username **admin** and the password **admin**. AP Coder asks for your name (the one on
your approvals), your own password (at least 12 characters, not "admin" and not your name) and the **setup code**,
then sets up the authenticator app on your phone (a QR code to scan, then a code from the app: *Sign-in codes*, step 10). From the
moment that password is saved, admin / admin no longer works; your account is `admin`. admin / admin is refused from
any other computer, and through a reverse proxy: with `--proxy` (behind IIS) this way is off, so make the first Admin
before putting the proxy in front, or use the console below. A proxy on this server must always be named with
`--proxy` (step 4): one that is not, and passes no forwarding header, makes every browser look like the server's own.

The setup code is what proves you are at the server: any program on it that passes connections on (a port forward
with `netsh interface portproxy`, `ssh -L`, socat, nginx's default `proxy_pass`) brings other computers' browsers in
from 127.0.0.1, under any name they give. While there is no account, the service makes a one-time code (four groups
of four letters and digits) in `D:\APCoderData\first_sign_in.code`, and says it in its log (`logs\service.log`:
"No account yet ... this setup code: ..."). `python -m ap_coder doctor`, run as the service's account, shows it
too. It works once: with the first account the file is removed. On Windows the file has the data folder's
permissions: with step 5 of section 2, the service's account, the administrators and SYSTEM may read it; the doctor
warns when Users, Authenticated Users or Everyone may read the folder. Made by another account (the doctor run as
an administrator before the service's first start), the service can't read it: the server's window, the doctor and
the page say so; delete the file and the service makes a new one. Each wrong code is in the audit trail.

**At the server's console**:

```bat
.venv\Scripts\python.exe -m ap_coder users add --admin jsmith
```

It asks for a password, twice (at least 12 characters, not the user name). That password is a temporary one (it
works for 3 days): tell it to the person, who signs in at `https://apcoder.company.local:8501` and, before anything
else, chooses their own password, then sets up an authenticator app on their phone (a QR code to scan, then a code
from the app: *Sign-in codes*, step 10). The first Admin does this too: every account signs in with a code. They
then add everyone else (step 10).

A server that already has accounts is unchanged: admin / admin works only while there is none.

## 8. LM Studio on the same server

LM Studio reads the pages and suggests GL accounts (docs/LM_STUDIO.md). On the server it runs headless, without its
window, started at boot like AP Coder. LM Studio's own system requirements (lmstudio.ai/docs) list Windows, macOS and
Linux, not Windows Server; AP Coder's release check (`.github/workflows/release.yml`) runs its headless server
(llmster) on GitHub's Windows Server machines for each pilot release, on the processor (no graphics card there).

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
   account it runs as; the model's exact name is the one `lms ls` shows; `unload --all` first, so a second start,
   point 8's, never loads the chat model twice):

   ```bat
   "%USERPROFILE%\.lmstudio\bin\lms.exe" server start --port 1234
   "%USERPROFILE%\.lmstudio\bin\lms.exe" unload --all
   "%USERPROFILE%\.lmstudio\bin\lms.exe" load gemma-4-12b-it-qat --context-length 16384 --gpu max
   ```

4. **Start it at boot**, as that account (Windows asks for its password):

   ```bat
   schtasks /create /tn "LM Studio server" /sc onstart /delay 0000:30 /ru APCoderService /rl LIMITED /tr C:\APCoder\lm-studio-start.cmd
   schtasks /run /tn "LM Studio server"
   ```

5. **Check it**: `lms server status` and `lms ps` (as that account) show the server and the loaded model; in AP Coder,
   *Settings* → *Reading* → *Check again*. AP Coder finds LM Studio at `http://127.0.0.1:1234/v1`, its usual place.
   On another computer of the company network instead (section 1's split layout, or Phase 2's model machine), give
   its address in the .env (`AP_LLM_BASE_URL`, `AP_PAGE_READER_BASE_URL`). A name in the company's DNS
   (`macstudio.corp.example.com`) also needs that DNS domain named, `AP_COMPANY_DOMAINS=corp.example.com` (several:
   comma-separated): such a name is used while every address it has is private, AP Coder connects to the very
   addresses it checked (never to a second lookup's), and no other name is looked up. **A second model machine as the
   backup** (in Phase 2, the pilot's machine, the same models in its LM Studio): its address in `AP_LLM_BACKUP_URL`
   (and `AP_PAGE_READER_BACKUP_URL` for a page reader on a server of its own), in *Settings* → *Reading* → *For IT*,
   or server.json's `llm_backup_url` and `page_reader_backup_url`. After two calls in a row the main one doesn't
   answer, the calls go to the backup (the calls under way are asked again there); the main one is asked every few
   seconds and taken back once it answered three times, after a minute at least (D-180). The Reading tab and
   `python -m ap_coder doctor` say which one answers, and how fast.
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
7. **LM Studio's log holds invoice content.** Its server log, in the folder `server-logs` of the account LM Studio
   runs as (`%USERPROFILE%\.lmstudio\server-logs`; on a Mac or Linux `~/.lmstudio/server-logs`: the folder the
   release check copies), keeps each model answer in full and the start and end of each request: an invoice's text,
   its vendor's name, its amounts. So the machine that runs the models holds invoice content, as AP Coder's data
   folder does: encrypt its disk (BitLocker; FileVault on a Mac, section 14), keep it as physically secure as AP
   Coder's server, and keep the logs a short time. LM Studio's documentation names no setting for how long it keeps
   them (checked 9 Nov 2026), so delete them on a schedule: for example, every night, those older than 7 days, with
   `C:\APCoder\lm-studio-logs.cmd` holding (`%USERPROFILE%` is the profile of the account it runs as, as in point 3):

   ```bat
   forfiles /p "%USERPROFILE%\.lmstudio\server-logs" /s /d -7 /c "cmd /c if @isdir==FALSE del /q @path"
   ```

   and its task, as that account:

   ```bat
   schtasks /create /tn "LM Studio logs" /sc daily /st 02:00 /ru APCoderService /rl LIMITED /tr C:\APCoder\lm-studio-logs.cmd
   ```

   On a Mac, section 14 has the same with launchd; on Linux, section 15 with a systemd timer.
8. **Started again when it stops, and never updated by itself.** The task of point 4 runs once, at boot: a crash of
   LM Studio would leave the models down until someone starts them, and AP Coder neither starts nor watches LM Studio.
   So a second task, every 5 minutes, starts it again when its server doesn't answer: `C:\APCoder\lm-studio-watch.cmd`,
   holding (`curl.exe` comes with Windows Server 2019 and newer)

   ```bat
   @echo off
   curl.exe -s -f -m 30 -o NUL http://127.0.0.1:1234/v1/models && exit /b 0
   "%USERPROFILE%\.lmstudio\bin\lms.exe" daemon down
   call C:\APCoder\lm-studio-start.cmd
   ```

   and its task, as the account of point 4:

   ```bat
   schtasks /create /tn "LM Studio watch" /sc minute /mo 5 /ru APCoderService /rl LIMITED /tr C:\APCoder\lm-studio-watch.cmd
   ```

   **Its updates:** LM Studio updates its runtimes by itself unless that is turned off in its settings (lmstudio.ai's
   notes on version 0.3.9), and its headless server (llmster) updates only with `lms daemon update`. On a server
   meant to stay offline, turn the runtimes' automatic update off when you open LM Studio to set the models up
   (point 2), and update LM Studio on purpose, as AP Coder is (section 11): a new version tried first, then copied in.
   On a Mac, section 14's LaunchDaemon watches LM Studio the same way; on Linux, section 15's systemd unit.

## 9. Backups

- **Every day, automatically**: a backup in `D:\APCoderData\backups`, at the time set on *Settings* → *Data &
  backups* (*Back up every day at*, 17:30 until another is saved; made at the next start when AP Coder was not
  running then, and the page says so; D-218). On a server nobody chose that 17:30, and it is in working hours: the
  page shows no time until an Admin saves one, and says that 17:30 applies until then. Save the time you want there,
  or set the hours below. A time changed while AP Coder runs is followed within a minute. Kept (D-187):
  the newest 14 daily ones, then the newest of each of the last 8 weeks and of each of the last
  12 months that have one, so a mistake found at the month end or the year end can still be undone; a backup with a
  label (yours, an update's, a restore's) is always kept, and the newest complete one is never deleted.
  - **When, on a big server:** set `AP_BACKUP_HOURS=1-5` in the `.env` so the daily copy starts between 1 and 5 in
    the morning, not during the working day; those hours then decide, and the Settings page shows them, with where
    they are set, in place of a time of day. The hours are the business time zone's (`time_zone` in `server.json`, step 12), else the
    server's clock. A year's database (300,000 invoices) is about 10 GB: the copy takes minutes of disk work. If the
    server was off during those hours, the backup is made as soon as the last one is 30 hours old, so no day is skipped
    (B-1671).
  - **Room:** a backup starts only when the copy and 512 MB more fit on the disk. A full disk would stop every write
    to the database. Otherwise *Settings* → *Data & backups* says it was not made and why (B-1670).
  - Plan the disk for about 32 copies of the database (14 days, 8 weeks and 12 months, which overlap: about 320 GB for a
    year's), plus the invoice files once. It is made in the background: AP Coder serves pages meanwhile, and *Settings*
    → *Data & backups* says while one is being made (B-1553). An update adds its own (`...-before-update-<version>`),
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
  (the same layout, and the same ones kept: 14 days, 8 weeks, 12 months), and the page says, for each backup, whether
  its copy was made or why it failed. Choose a share on another server: a folder on the database's own disk does not
  survive that disk's failure, and *Settings*, `doctor` and `/readyz` say so. That folder alone restores the server on a new computer. The service's account must be able to write
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
- **A new server** (the old one lost, or AP Coder moving from a laptop to a server of its own): install AP Coder
  (step 2) and the service **without starting it** (`service install ... --no-start`, step 6), then restore from the
  share alone, **before making the first Admin (step 7)**:
  `.venv\Scripts\python.exe -m ap_coder backup list --from \\fileserver\Backups\APCoder`, then
  `backup restore <name> --from \\fileserver\Backups\APCoder`, then `service start`. A restore from the console
  needs AP Coder stopped: when the service runs already (`service install` starts it), the restore says "AP Coder is
  running on D:\APCoderData: stop it first" and changes nothing, so run `service stop` first; the empty database
  that first start made is then replaced like any other. The database and every file come back into the new
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
| Clerk | Also import and process invoices, review and correct them, approve within the limit, park, reject, reopen, bulk approve, export batches, make the Spend page's Excel workbook, and put a vendor on hold or edit its notes (not take it off hold, nor change a held vendor's tax numbers, D-142). Add, change, remove and undo the coding rules: each change is kept with who made it (D-217). |
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
pattern repeated. A temporary password (a new account, a reset) works for 3 days: after that, reset it again (the
first Admin's, made at the console, works while it is the only Admin and nobody has signed in yet). Every
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
5. runs the self-check (the sample invoices read right);
6. copies the database for the backup of step 8, as it is, and keeps the invoice files that are new (staff work on:
   what they write meanwhile waits in the database's write-ahead log, `ap_coder.db-wal`, until step 8).

Then the switch, the only time staff see AP Coder closed:

7. stops the service;
8. backs up the database and the files (section 9): `backups\ap_coder-<date>-<time>-before-update-0.5.0.db`: the
   copy of step 6 with what was written since added, so it is the database as it was the moment the service
   stopped;
9. updates the database (the new version opens it once);
10. starts the service from the new folder, and waits for it to answer (5 minutes at most). The backup's SHA-256 is
    then written in its
    list, and the backup copied to the second backup folder, when one is set.

It prints how long the service was down (from the stop to the new version answering) and each step's time, and
keeps them in `server.json`'s `history` (`times`, in seconds). The downtime no longer grows with the database: the
whole copy is made in step 6, while staff work (about a minute per GB on a slow disk), and step 8 adds only what
was written during it. What step 8 still does for every invoice is to look at its file again (about 2 seconds per
20,000 files on a Linux test computer). Measured there (D-196): with 20,000 invoices (669 MB) down 6 s, and with
120,000 (2.45 GB) down 6 s, where it was 12 s and 16 s with the copy made in step 8. CI checks that with 20,000
invoices it stays under 5 minutes on Windows. The new version's start adds each web worker's memories built before it
serves (D-204: about 13 s at 300,000 invoices, none of its steps begun past 25 s). On a database that had the reports
(any served before), they are not worked out before it answers (an update keeps none of the version before's: they
are worked out while it serves): in the first minute or two after the update, as before D-204, a report opened
before the background process has worked it out says it is being worked out and opens by itself once it is ready;
invoices open fast from the first request. (A database that never had them has them worked out first, as at its
first start.)
If the database can't be copied in step 6 (a report that reads for over a
minute keeps it from being still), it says so and copies it whole in step 8, as before. If step 2 to 6 fails,
nothing was stopped or changed: the old version kept serving, and it says why. If step 7, 8, 9 or 10 fails, it puts
the database backup back, switches the service back to the old folder and starts it again, and says why. Either way
the new folder is kept to look at; delete it when done.

**If the power goes off (or the window is closed) during an update**, where it was is in
`D:\APCoderData\service\update.json` at each step. The service's next start (the server booting, or the scheduled
task's 5-minute retry) and the next `server-update` act on it before anything else (on a Mac, the service's start
leaves to `server-update` what only an administrator may do: below), and say what they did in `logs\service.log` or
on screen:

- cut short while it prepared (the copy of step 6 too), stopped the service or made the backup: the old version
  starts as it was (its database was only read: what staff did meanwhile is in it); a backup cut short is removed,
  with all SQLite left beside it;
- cut short while the new version updated the database: the backup made a moment before (the service already
  stopped, so nothing was done since) is put back, and the old version starts;
- cut short at or after the switch: the update is finished and the new version starts (`server-update --back` goes
  back if it turns out wrong). `server-update` goes back instead when the new version does not answer; the service
  goes back when it still runs the old folder (run.cmd not rewritten yet);
- cut short while it went back: going back is finished.

The database is put back once: the marker then says so, and a start that fails after it never puts it back again.
`server-update` then stops there: run it again to update. While one `server-update` runs, another (and the service's
retry) leaves it alone (`service\update.lock`).

On a Mac the service's start does all of this too, but for what only an administrator may do: the service runs as
`_apcoder`, and `/Library/APCoder/current` is root's. So when an update was cut short while it went back with the link
still on the new folder, the service changes nothing (no backup put back), stays stopped, and `logs/service.log` says
so: run `apc server-update` (section 14) again, which goes back and starts it. The same when the service can't tell an
update cut short from one running (it can't open `service/update.lock`, made by an older `sudo server-update`): it
serves on if the update had not stopped it yet, and else stays stopped until `apc server-update`.

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
versions back, which you can delete. A service installed by an earlier AP Coder than this guide's has its scheduled
task start in the folder it was installed from: once that folder is deleted, Task Scheduler can't start the task (its
*History*: error 267, `0x8007010B`, "the directory name is invalid"). Run `service install` once more from the
current folder (with the `--account`, `--task-name` and `--folder` it was given; Windows asks for the account's
password), before you delete it: the task then starts in `D:\APCoderData\service`, which no update changes. The
update's message says so when the task it finds is such a one.

What was done, when, is in `D:\APCoderData\server.json` (`history`), and the installs in the administrator's
`%USERPROFILE%\.ap_coder\settings.json`, as on a laptop.

## 12. Health and monitoring

**`GET /readyz`** (D-187) is for your monitoring (PRTG, Zabbix, SCOM, Nagios...) or a load balancer: **200** when
AP Coder is ready, **503** when it can't do its work, and a JSON body saying what was checked. Not ready means one of:
the database can't be read; the data folder's disk has less than 512 MB free (no invoice is read, no backup made); the
HTTPS certificate has expired; the background process gave no heartbeat for 2 minutes (it says it is alive every 30
seconds); none of the capture processes runs. Everything else is a warning (still 200, `"status": "warn"`): the
certificate expires within 30 days; the last backup failed, or the newest is over 36 hours old, or there is none; the
next backup won't fit on its disk; no second backup folder, or one that can't be reached or is on the database's disk;
fewer capture processes than started; a model server's calls on its backup; the server's clock in another time zone
than the business's. The body holds no password, token, path, person's or vendor's name:

```json
{"ready": true, "status": "warn", "app": "ap-coder", "version": "0.3.0", "server": true,
 "checked_at": "2026-11-06T09:00:05-05:00",
 "problems": [{"check": "certificate", "status": "warn", "text": "The server's HTTPS certificate expires on ..."}],
 "database": {"status": "ok"},
 "data_disk": {"free_bytes": 412000000000, "total_bytes": 999000000000, "reserve_bytes": 536870912, "status": "ok"},
 "backups_disk": {"same_disk_as_data": true, "free_bytes": 412000000000, "needed_bytes": 9100000000, "status": "ok"},
 "backup": {"newest": "2026-11-06T02:00:41-05:00", "age_hours": 7.0, "last_failed": null, "being_made": false,
            "status": "ok"},
 "second_backup_folder": {"set": true, "reachable": true, "free_bytes": 2000000000000, "same_disk_as_data": false,
                          "status": "ok"},
 "certificate": {"days_left": 21, "expires": "2026-11-27T23:59:59+00:00", "status": "warn"},
 "time_zone": {"business": "America/Toronto", "machine": "Eastern Standard Time (UTC-05:00)", "status": "ok"},
 "background": {"heartbeat_age_seconds": 12.4, "status": "ok"},
 "capture": {"alive": 6, "started": 6, "status": "ok"},
 "imports": {"waiting": 40, "reading": 6, "oldest_wait_seconds": 95},
 "page_reader": {"waiting": 310, "reading": 1, "oldest_wait_seconds": 5400},
 "model_servers": {"status": "ok", "model_server": {"on": "primary", "since": null, "moves": 0}}}
```

Each process works it out at most every 5 seconds (several monitors every 10 seconds cost nothing), and the second
backup folder (a share that is gone can take a minute to say so) is waited for 2 seconds at most.

**Who it answers**: the server's own console (`https://localhost:8501/readyz` on the server, not through a proxy), and
the monitors you name in `D:\APCoderData\server.json`; anyone else gets a 403. Add either or both (then `service
stop` and `service start`, or wait: it is read at each request):

- `"monitor_token": "<at least 24 characters>"`, which the monitor sends as the header `X-AP-Coder-Monitor: <token>`
  (or `Authorization: Bearer <token>`). Make one with `.venv\Scripts\python.exe -c "import secrets;
  print(secrets.token_urlsafe(32))"`, and keep it as private as a password.
- `"monitor_addresses": ["10.1.2.30", "10.1.5.0/24"]`: a monitor or load balancer at one of these addresses (or in
  one of these networks) is answered without a token, for a probe that can't send a header.

The Host check still applies: the monitor opens AP Coder by one of its names or by the server's IP address.

**`doctor --json`**: the same checks as `doctor`, as JSON (`{"checks": [{"area", "status", "detail"}], "failures",
"warnings"}`), with the same exit code (1 when one failed). On the server `doctor` also says when the certificate
expires, the time zone, the data folder's disk, the newest backup and the second backup folder.

**The business time zone**: add `"time_zone": "America/Toronto"` (its IANA name: `America/Vancouver`,
`America/Edmonton`, `America/Winnipeg`, `America/Halifax`...) to `server.json`. `doctor` and `/readyz` then warn
when the server's clock keeps another time (a virtual machine left on UTC makes "today", the day's counts, the due
dates and the backup hours wrong), and the daily backup's hours (`AP_BACKUP_HOURS`) are the business's. AP Coder
does not change the clock: set the server's time zone in Windows (*Settings* → *Time & language* → *Date & time*).

**The certificate**: from 30 days before it expires, an Admin sees a banner on every page, `doctor` and `/readyz`
warn, and the system's log says so (step 6); once expired, the banner says so in red and `/readyz` answers 503.

## 13. Troubleshooting

| What you see | What to do |
|---|---|
| The browser can't reach `https://apcoder.company.local:8501` | `service status` on the server. Not running: the end of `D:\APCoderData\logs\service.log` says why. Running: check the firewall rule (step 5) and the DNS alias (`nslookup apcoder.company.local` on a staff PC). |
| The browser warns about the certificate | The certificate doesn't name `apcoder.company.local`, has expired, or lacks the CA's intermediate certificates (step 3: add `ca-chain.pem` after it). |
| "not one of the server's names" (a 400 error) | The name typed is not one of the allowed names: `service install --allowed-hosts apcoder.company.local,apcoder` (each name staff use; it restarts AP Coder), or add it to `AP_ALLOWED_HOSTS` and restart. |
| AP Coder doesn't start: "Port 8501 is in use by another program" | On the server AP Coder keeps the port staff were given, and never moves to another. Find the other program (`netstat -ano \| findstr :8501`) and stop it, or install with another `--port` and tell staff the new address. |
| The task doesn't start; Task Scheduler's *History* says error 267 (`0x8007010B`, "the directory name is invalid") | It was installed by an older AP Coder to start in a folder since deleted (section 11): `service install` again from the folder `service status` names. |
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
| Your monitor gets 503 from `/readyz` | Its `problems` say which check failed and what to do (section 12). A 403: the monitor is not named in `server.json` (`monitor_token` or `monitor_addresses`). |
| Anything else | `.venv\Scripts\python.exe -m ap_coder doctor`, and the log. *Help* → *Report a problem* makes a report without invoice data. |

**What `doctor` checks on the server** (when `AP_SERVER_MODE=1`, or `server.json` is in the data folder), besides
what it checks on a laptop: *dashboard network* (the address and port the server listens on, and the address staff
open), *server HTTPS certificate* and *private key* (set, there, and readable by the account you run it as; run it
as `APCoderService` to check the service's own rights), *server names* (the allowed names, a warning when none is set)
and *server service* (on Windows the scheduled task, on a Mac the LaunchDaemon, on Linux the systemd unit: installed,
its state, the account, and whether it was stopped on purpose); and (D-187) *server HTTPS certificate expiry*,
*server time zone*, *data folder disk*, *backups* (the newest, and whether the last one failed) and *second backup
folder* (set, reachable, on another disk).

## 14. On a Mac

One of the pilot's alternatives (section 1) is a Mac mini that does everything: AP Coder and both AI models, with
memory enough for them together (32 GB: section 1, *Memory on one machine*). On a Mac, AP Coder's service is a
*LaunchDaemon*: launchd, the part of macOS that runs its services, starts it at boot under its own account, whether
anyone is logged in or not. Everything else in this guide holds, with the Mac's paths: the certificate (step 3), the
name staff type (step 4), the first admin (step 7), backups (step 9; their second copy on a network share: *A network
share*, below), users (step 10), updates (step 11). Why a LaunchDaemon, and how it was chosen: docs/decisions.md,
D-186.

In this section `/Library/APCoder/APProcessor` is the AP Coder folder, `/Library/APCoder/current` a link to the
AP Coder folder that runs (the service runs that link; `server-update` points it at a new version), `/Library/APCoderData`
the data folder and `_apcoder` the account AP Coder runs as. Commands run in Terminal, as an administrator, with
`sudo`. sudo leaves the environment out, so the data folder is given on each command line; once the service is
installed, these two save the typing (put them in your `~/.zshrc` to keep them):

```sh
# The service and updates, as root:
apc() { sudo AP_PRIVATE_DIR=/Library/APCoderData AP_SERVER_MODE=1 /Library/APCoder/current/.venv/bin/python -m ap_coder "$@"; }
# Everything else that opens the data folder (users, backup, doctor), as the service's account:
apcu() { sudo -u _apcoder AP_PRIVATE_DIR=/Library/APCoderData AP_SERVER_MODE=1 /Library/APCoder/current/.venv/bin/python -m ap_coder "$@"; }
```

Then `apc service status`, `apcu users add --admin jsmith`, `apcu doctor`. A file made with sudo is root's, and the
service's account could not change it: `apcu` makes it the account's from the start. What root makes in the data
folder anyway (`server-update`'s backups and restores) is handed back to the account at each `service install` and
`service start`: only root's own files and folders, never what a link there points at.

### The Mac itself

- **Never asleep, and back after a power cut**: *System Settings* → *Energy*: *Prevent automatic sleeping when the
  display is off*, *Wake for network access*, *Start up automatically after a power failure* on (or `sudo pmset -a
  sleep 0 disksleep 0 womp 1 autorestart 1`). Put the Mac on a UPS.
- **FileVault** (its disk encrypted): a Mac with FileVault on waits after every restart, a power cut included, until
  someone unlocks it with the password of an account that may unlock FileVault: at its unlock screen, or, on a Mac
  with Apple silicon and macOS 26 or later, over SSH from another computer, when *Remote Login* was turned on
  beforehand (*System Settings* → *General* → *Sharing*) and the Mac is on the network (Apple's Platform Security
  guide, *Managing FileVault in macOS*). Until then nothing of AP Coder runs (nor LM Studio): a power cut at night
  leaves AP Coder down until someone unlocks the Mac in the morning. So IT names who unlocks it after an unplanned
  restart, and who stands in for them; `sudo fdesetup authrestart` serves the planned ones (it restarts once without
  the unlock: use it for macOS updates too). Or FileVault off, on a Mac in a locked room (the invoices are then not
  encrypted on its disk). `fdesetup status` says which.
- **macOS updates**: turn off the automatic install of macOS updates (*General* → *Software Update* → *Automatic
  updates*), so the Mac restarts when you choose, at a quiet time.
- **A fixed address** on the company network (a DHCP reservation), and the DNS alias staff type (step 4).

### The service's account

A hidden account used for nothing else, with no password and no shell (it can't log in):

```sh
sudo dscl . -create /Groups/_apcoder PrimaryGroupID 450
sudo dscl . -create /Users/_apcoder UniqueID 450
sudo dscl . -create /Users/_apcoder PrimaryGroupID 450
sudo dscl . -create /Users/_apcoder UserShell /usr/bin/false
sudo dscl . -create /Users/_apcoder NFSHomeDirectory /var/empty
sudo dscl . -create /Users/_apcoder RealName "AP Coder service"
sudo dscl . -create /Users/_apcoder IsHidden 1
```

450 is a number no other account or group has here: `dscl . -list /Users UniqueID` and `dscl . -list /Groups
PrimaryGroupID` show those in use (below 500 keeps it off the login window). AP Coder refuses to run as root unless
`--allow-root` is given, for a first test only: as root, every invoice and email it reads would be handled with all
of the Mac's rights.

### Install from the offline bundle

The Mac's bundle (`scripts/build_offline_bundle.py --platform macosx_14_0_arm64`), copied in with its SHA-256
(`shasum -a 256 <zip>` shows a file's):

```sh
sudo mkdir -p /Library/APCoder /Library/APCoderData
sudo ditto -x -k ~/Downloads/APProcessor-offline-0.4.0-20261101.zip /Library/APCoder
sudo chown _apcoder:_apcoder /Library/APCoderData
sudo chmod 750 /Library/APCoderData
cd /Library/APCoder/APProcessor
sudo AP_SERVER_MODE=1 AP_PRIVATE_DIR=/Library/APCoderData ./APProcessor.command --no-start --yes
```

This makes `/Library/APCoder/APProcessor` and sets it up from its `wheelhouse` (offline, a few minutes, ending with
the self-check). The code folder stays root's: only administrators can change it, and the service's account reads and
runs it (whoever can change the code can read every invoice). The data folder is the account's, nobody else's (the
administrators reach it with sudo). Keep both on the Mac's own disk.

### The service

From the AP Coder folder, the first time:

```sh
cd /Library/APCoder/APProcessor
sudo AP_PRIVATE_DIR=/Library/APCoderData .venv/bin/python -m ap_coder service install --certfile /Library/APCoderData/tls/apcoder.pem --keyfile /Library/APCoderData/tls/apcoder.key.pem --allowed-hosts apcoder.company.local
```

`service install` saves the settings in `/Library/APCoderData/server.json`, makes the data folder's `service` and
`logs` folders the account's, points `/Library/APCoder/current` at this folder, writes the LaunchDaemon
`/Library/LaunchDaemons/com.apcoder.server.plist` (root's, as launchd wants it) and loads it: launchd starts AP Coder
at once, and at every boot, as `_apcoder`. Add `--dry-run` to see the plist and the commands first, changing nothing.
The options are those of step 6, but: `--account` (default `_apcoder`; `root` only with `--allow-root`), and
`--task-name`, which names the LaunchDaemon (the default, `AP Coder`, gives `com.apcoder.server`; a name like
`com.example.apcoder` is used as it is). There is no password to give. The data folder is kept in the LaunchDaemon,
so the other service commands find it even when sudo left `AP_PRIVATE_DIR` out.

| To | Run |
|---|---|
| See whether it runs, its version and folder | `apc service status` (`--json` for a monitoring script: installed, running, the process, version, folder, address and the dashboard's answer) |
| Stop it (it stays stopped, also after a restart of the Mac) | `apc service stop` |
| Start it again (when it runs, it is restarted) | `apc service start` |
| Change a setting (port, certificate, names) | `apc service install` with the new options (the others are kept, except `--account`, `--task-name` and `--folder`: give them again unless they are the defaults) |
| Remove it (the data, settings, logs and the `current` link are kept) | `apc service remove` |

How it runs: launchd starts `/Library/APCoder/current/.venv/bin/python -m ap_coder service run`, the service host of
step 6, which starts the dashboard and starts it again if it stops. The LaunchDaemon's `KeepAlive` is
`SuccessfulExit: false`: launchd starts the host again when it crashed or was ended (at most every 10 seconds), and
leaves it stopped when it ended on purpose. A host that was killed takes its dashboard with it (launchd ends the
processes it started) and is back within seconds. `service stop` leaves a `stopped` file in the data folder's
`service` folder, asks the dashboard to stop, and unloads the LaunchDaemon (`launchctl bootout`): launchd asks the host
to stop (SIGTERM), which stops its dashboard (the requests under way get 25 seconds; it is ended after 45 at most) and
ends with exit code 0. At the next boot launchd loads it again, and the host, seeing `stopped`, ends at once: the
service stays stopped until `service start`, which removes the file and loads it (`launchctl bootstrap`), or restarts
it when it runs (`launchctl kickstart -k`).

**Logs**: `/Library/APCoderData/logs/service.log`, `access.log` and `background.log`, as in step 6; and
`logs/launchd.out.log` and `launchd.err.log`, what Python said before the host's own log was open (a Python that
won't start, a folder it can't write). launchd's own view of it: `sudo launchctl print system/com.apcoder.server`
(its state, process and last exit code).

**The firewall**: when the Mac's firewall is on (*System Settings* → *Network* → *Firewall*), allow incoming
connections to Python in its *Options*. Staff reach AP Coder on the port given (8501).

**Updating** (section 11), from the folder that runs:

```sh
apc server-update ~/Downloads/APProcessor-offline-0.5.0-20261201.zip --sha256 <the 64 digits> --dry-run
apc server-update ~/Downloads/APProcessor-offline-0.5.0-20261201.zip --sha256 <the 64 digits>
```

It does the steps of section 11 with the LaunchDaemon: it stops it, unzips the bundle beside the current folder
(`/Library/APCoder/APProcessor-0.5.0`), and once the new folder is set up and checked, points `/Library/APCoder/current`
at it in one step (a new link renamed over the old one, while the service is stopped) and starts the service again. The
LaunchDaemon itself never changes. `apc server-update --back` points the link back. Leave the `current` link to
`server-update`; never change it while the service runs. An update cut short (a power cut) is dealt with at the Mac's
next start as section 11 says, except what only root can do (the link): then the service stays stopped and its log
says to run `apc server-update` again.

A double-click on `APProcessor.command` on the Mac mini starts no second AP Coder on the server's data (step 6 of the
Windows steps says what it does instead). Use `service stop` and `service start`.

### A network share (the backups' second copy, the AP inbox)

`_apcoder` has no password and can't log in, so it can't connect to a network share itself, and macOS mounts none at
boot by itself. So root mounts it at every boot, and AP Coder writes there as `_apcoder`. What IT does:

1. **A folder only `_apcoder` can open**, with the mount point in it. The mount point stays root's, so while the
   share is not mounted nothing is written there (the copy fails, and *Settings* → *Data & backups* says so):

   ```sh
   sudo mkdir -p /Library/APCoderShare/backups
   sudo chown _apcoder:_apcoder /Library/APCoderShare
   sudo chmod 700 /Library/APCoderShare
   ```

2. **A script that mounts the share when it isn't mounted**, `/Library/APCoder/mount-shares.sh`, root's only
   (`sudo chown root:wheel` and `sudo chmod 700`: it holds the password of the share's own account, a domain account
   with *Modify* on that share folder; an `@`, `:` or `/` in the password is written `%40`, `%3A`, `%2F`). The modes
   `-d 0777 -f 0666` let `_apcoder` write there; the folder above keeps every other account of the Mac out.

   ```sh
   #!/bin/sh
   # The network shares AP Coder writes to, mounted at boot and every 5 minutes (docs/SERVER.md, section 14).
   mount_share() {  # the share, the folder
     /sbin/mount | /usr/bin/grep -q " on $2 (" || /sbin/mount_smbfs -o nobrowse,soft -d 0777 -f 0666 "$1" "$2"
   }
   mount_share '//CORP;apcoder-share:<password>@fileserver.corp.example.com/Backups/APCoder' /Library/APCoderShare/backups
   ```

3. **Its LaunchDaemon**, as root (no `UserName`), `/Library/LaunchDaemons/com.apcoder.shares.plist`, loaded as in
   *LM Studio on the Mac*, point 3, below; `StartInterval` mounts the share again when the file server was away:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <plist version="1.0">
   <dict>
     <key>Label</key><string>com.apcoder.shares</string>
     <key>ProgramArguments</key><array><string>/Library/APCoder/mount-shares.sh</string></array>
     <key>RunAtLoad</key><true/>
     <key>StartInterval</key><integer>300</integer>
   </dict>
   </plist>
   ```

4. **Name it to AP Coder and check it after a restart**:
   `apc service install --allowed-folders /Library/APCoderShare/backups` (step 9), then *Settings* → *Data & backups*
   → *Also copy each backup to* `/Library/APCoderShare/backups`. After a restart,
   `sudo -u _apcoder touch /Library/APCoderShare/backups/check` works (then `sudo -u _apcoder rm` it), and the next
   backup's line on *Data & backups* says its copy was made.

The AP inbox's share, when the emails come through one (docs/MAILBOX_SETUP.md), is a second `mount_share` line, to
`/Library/APCoderShare/inbox` (made as `backups` was).

### LM Studio on the Mac (a recipe for IT)

AP Coder neither starts nor watches LM Studio: IT runs it as a LaunchDaemon of its own, which starts it at boot and
again when it stops. LM Studio installs per account and keeps its models in that account's `~/.lmstudio/models`, so it
runs as a standard account made for it, here `lmstudio` (with its own home folder). Install LM Studio as that account
(copied in: nothing is downloaded), open it once to set the models up (step 8, point 2), turn off its own *run the
LLM server on login*, and turn off its runtimes' automatic update (step 8, point 8).

1. **The start script and its watchdog**, `/Library/APCoder/lm-studio-start.sh` (the model's exact name is the one
   `lms ls` shows). It starts LM Studio, then asks its server every 30 seconds whether it answers; after three
   misses in a row it stops what is left of LM Studio and ends with an error, and launchd starts the script again
   (point 2's `KeepAlive`), which starts LM Studio again:

   ```sh
   #!/bin/sh
   # LM Studio's server at boot, and again when it stops, for AP Coder (docs/SERVER.md, section 14).
   LMS="$HOME/.lmstudio/bin/lms"
   ADDRESS=127.0.0.1
   "$LMS" daemon up
   "$LMS" server start --port 1234 --bind "$ADDRESS"
   "$LMS" unload --all
   "$LMS" load gemma-4-12b-it-qat --context-length 16384 --gpu max --yes
   misses=0
   while sleep 30; do
     if /usr/bin/curl -s -f -m 20 -o /dev/null "http://$ADDRESS:1234/v1/models"; then misses=0; else misses=$((misses + 1)); fi
     if [ "$misses" -ge 3 ]; then "$LMS" daemon down; exit 1; fi
   done
   ```

   `ADDRESS`: `127.0.0.1`, AP Coder running on the same Mac, so nothing else on the network reaches the models. On a
   Mac that only runs the models for AP Coder on another machine (section 1's split layout, step 8, point 5), its own
   address on the company network instead; anything else on the network can reach that port too, so let the Mac's
   firewall, or the network, allow only AP Coder's machine. `unload --all` before the load: a start after a stop
   never loads the chat model twice.

2. **Its LaunchDaemon**, `/Library/LaunchDaemons/com.apcoder.lmstudio.plist` (replace each `<account>`, brackets
   too, with the account it runs as, `lmstudio` above; `plutil -lint` finds one left):

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <plist version="1.0">
   <dict>
     <key>Label</key><string>com.apcoder.lmstudio</string>
     <key>ProgramArguments</key><array><string>/Library/APCoder/lm-studio-start.sh</string></array>
     <key>UserName</key><string><account></string>
     <key>EnvironmentVariables</key><dict><key>HOME</key><string>/Users/<account></string></dict>
     <key>RunAtLoad</key><true/>
     <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
     <key>AbandonProcessGroup</key><true/>
     <key>StandardOutPath</key><string>/Users/<account>/Library/Logs/lm-studio-start.log</string>
     <key>StandardErrorPath</key><string>/Users/<account>/Library/Logs/lm-studio-start.log</string>
   </dict>
   </plist>
   ```

   `KeepAlive` with `SuccessfulExit` false: launchd starts the script again whenever it ends with an error or is
   killed (at most every 10 seconds), as AP Coder's own LaunchDaemon does. `AbandonProcessGroup` keeps LM Studio's
   own daemon running if the script itself is ended (launchd would otherwise end what it started).

3. **Load it**:

   ```sh
   sudo chown root:wheel /Library/APCoder/lm-studio-start.sh /Library/LaunchDaemons/com.apcoder.lmstudio.plist
   sudo chmod 755 /Library/APCoder/lm-studio-start.sh
   sudo chmod 644 /Library/LaunchDaemons/com.apcoder.lmstudio.plist
   sudo launchctl bootstrap system /Library/LaunchDaemons/com.apcoder.lmstudio.plist
   ```

4. **Check it after a restart with nobody logged in**: `sudo -u <account> /Users/<account>/.lmstudio/bin/lms server
   status` and `... lms ps` (the model loaded), and AP Coder's *Settings* → *Reading* → *Check again*. On the same
   Mac, AP Coder finds LM Studio at `http://127.0.0.1:1234/v1`, its usual place; a model machine of its own is named
   in AP Coder's `.env` (`AP_LLM_BASE_URL`, `AP_PAGE_READER_BASE_URL`: step 8, point 5).

5. **Its logs, deleted on a schedule** (they hold invoice content: step 8, point 7; its disk encrypted with
   FileVault, *The Mac itself* above says what that means at a restart). A LaunchDaemon that deletes, every night at
   2:00, the logs older than 7 days, `/Library/LaunchDaemons/com.apcoder.lmstudio-logs.plist` (each `<account>`
   replaced as in point 2), loaded as in point 3:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <plist version="1.0">
   <dict>
     <key>Label</key><string>com.apcoder.lmstudio-logs</string>
     <key>ProgramArguments</key><array>
       <string>/usr/bin/find</string><string>/Users/<account>/.lmstudio/server-logs</string>
       <string>-type</string><string>f</string><string>-mtime</string><string>+7</string><string>-delete</string>
     </array>
     <key>UserName</key><string><account></string>
     <key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
   </dict>
   </plist>
   ```

### The Mac mini's burn-in

Before the pilot tester and AP staff rely on it, let the Mac mini run the service for a week. Restart it once on
purpose (`sudo fdesetup authrestart` with FileVault on, else `sudo shutdown -r now`); cut its power once (it comes
back by itself with *Start up automatically after a power failure*; with FileVault on it waits to be unlocked: unlock
it as *The Mac itself* says, over SSH too if that is the plan);
kill its host once (`sudo kill -9` of the process `apc service status` names: launchd brings it back within seconds);
and, when you have a newer bundle, run one `server-update` and one `server-update --back`. After each, `apc service
status` and the end of `logs/service.log`. Each morning, check that the night's backup is in `backups` (section 9)
and that the disk keeps room, and that the backup's copy on the share was made. LM Studio too: `lms ps` after each
restart, and stop it once (`sudo -u <account> /Users/<account>/.lmstudio/bin/lms daemon down`): its watchdog starts it
again within a few minutes.

| On a Mac, what you see | What to do |
|---|---|
| "Run it with sudo" | The service commands change the Mac's own launchd: run them with sudo (`apc`). |
| "This Mac has no account _apcoder" | Make it (above), or name another with `--account`. |
| "Give the data folder" | `sudo AP_PRIVATE_DIR=/Library/APCoderData ...` on the command line (sudo leaves the environment out). |
| `service status` says "not loaded" | Stopped on purpose (`apc service start`), or it could not load: `sudo launchctl print system/com.apcoder.server`, and `logs/launchd.err.log`. |
| Nothing answers after a power cut | FileVault: the Mac waits to be unlocked (*The Mac itself*); or *Start up automatically after a power failure* is off. |
| The log says it can't write a file in the data folder | A file root made there: `apc service start` hands root's files back to the account; else `ls -l` shows whose it is. |
| launchd says "last exit code = 75" | The host asked to be started again: another service host runs (someone ran `service run` in Terminal: end it), or an update cut short was dealt with as it started and the folder to run changed. launchd starts it again within 10 seconds; when it keeps coming back to 75, the end of `logs/service.log` says why. |

## 15. On Linux

The pilot's recommended machine (section 1) is a Linux workstation with an NVIDIA graphics card: AP Coder and LM
Studio's headless server on the same machine, the models in the card's own memory. On Linux, AP Coder's service is a
*systemd unit*: systemd, the part of Linux that runs its services, starts it at boot under its own account, whether
anyone is logged in or not. Everything else in this guide holds, with Linux's paths: the certificate (step 3), the
name staff type (step 4), the first admin (step 7), backups (step 9), users (step 10), updates (step 11). How the
unit behaves, and why: docs/decisions.md, D-208.

In this section `/opt/apcoder/APProcessor` is the AP Coder folder, `/opt/apcoder/current` a link to the AP Coder folder
that runs (the service runs that link; `server-update` points it at a new version), `/var/lib/apcoder` the data
folder, `/var/lib/apcoder/invoices` its invoices folder, and `apcoder` the account AP Coder runs as (its group is
`apcoder` too). IT works over SSH, as an administrator, with `sudo`. sudo leaves the environment out, so the data folder
is given on each command line; once the service is installed, these two save the typing (put them in your
`~/.bashrc`):

```sh
# The service and updates, as root:
apc() { sudo AP_PRIVATE_DIR=/var/lib/apcoder AP_SERVER_MODE=1 /opt/apcoder/current/.venv/bin/python -m ap_coder "$@"; }
# Everything else that opens the data folder (users, backup, doctor), as the service's account:
apcu() { sudo -u apcoder AP_PRIVATE_DIR=/var/lib/apcoder AP_SERVER_MODE=1 /opt/apcoder/current/.venv/bin/python -m ap_coder "$@"; }
```

Then `apc service status`, `apcu users add --admin jsmith`, `apcu doctor`. A file made with sudo is root's, and the
service's account could not change it: `apcu` makes it the account's from the start. What root makes in the data
folder anyway (`server-update`'s backups and restores) is handed back to the account at each `service install` and
`service start`: only root's own files and folders, never what a link there points at.

### The machine

- **Ubuntu Server 24.04 LTS** (its Python is 3.12, the one this guide uses, and its systemd 255). The server edition
  has no desktop, so nothing puts the machine to sleep. In its installer: *Use an entire disk*, *Set up this disk as an
  LVM group* and *Encrypt the LVM group with LUKS* (the passphrase goes in IT's password safe: *Encryption*, below),
  and *Install OpenSSH server*.
- **An NVIDIA graphics card with 16 GB of video memory or more**, 8 cores or more, 32 GB of memory, 200 GB on an SSD
  (section 1).
- **In its firmware** (BIOS/UEFI): the TPM 2.0 on, Secure Boot on, and the power restored after a power failure
  (*Restore on AC power loss*: *Power On*, or the like), so the machine comes back by itself.
- **On the company network** as any server: a fixed address (a DHCP reservation), the name staff type (step 4), on a
  UPS, in a locked spot.

**The NVIDIA driver**, from Ubuntu (Canonical's signed build, which works with Secure Boot and needs no key of your
own). `list` shows the server drivers this card can have: take the newest, 550 or newer (LM Studio's Linux installer
takes its CUDA 12 build from driver 550.54.14 on):

```sh
sudo ubuntu-drivers list --gpgpu
sudo ubuntu-drivers install --gpgpu nvidia:<version>-server
sudo apt install nvidia-utils-<version>-server
sudo reboot
nvidia-smi
```

`nvidia-smi` shows the card, its memory and the driver's version; once LM Studio runs (below), its process and the
memory the model takes.

**What AP Coder and LM Studio need from Ubuntu**: Python's `venv` (for AP Coder's `.venv`), the two libraries the OCR
uses (`libgl1`, `libglib2.0-0t64`), LM Studio's `libatomic1`, `unzip` for the bundle and `cifs-utils` for the
company's shares:

```sh
sudo apt install python3-venv libgl1 libglib2.0-0t64 libatomic1 unzip cifs-utils
```

### The service's account

An account used for nothing else, with no password and no shell (it can't log in). `service install` makes it when
this computer has none; make it first, so the data folder is its own before the setup:

```sh
sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --comment "AP Coder service" apcoder
sudo install -d -o apcoder -g apcoder -m 750 /var/lib/apcoder
```

AP Coder refuses to run as root unless `--allow-root` is given, for a first test only: as root, every invoice and email
it reads would be handled with all of the machine's rights.

### Install AP Coder

**From the offline bundle** (nothing is downloaded on the machine). Build it on an Ubuntu 24.04 computer that may reach
the internet, with that Ubuntu's own Python, so it holds the packages for this kind of computer (its manifest lists
"this computer (linux-x86_64, Python 3.12)"), from an AP Coder folder: `python3 -m venv ~/bundle-venv`, then
`~/bundle-venv/bin/python scripts/build_offline_bundle.py --python 3.12`. Copy the ZIP in with its SHA-256
(`sha256sum <zip>` shows a file's), then:

```sh
sudo mkdir -p /opt/apcoder
sudo unzip -q ~/APProcessor-offline-0.4.0-20261101.zip -d /opt/apcoder
cd /opt/apcoder/APProcessor
sudo AP_SERVER_MODE=1 AP_PRIVATE_DIR=/var/lib/apcoder bash ./APProcessor.command --no-start --yes
```

This sets up `/opt/apcoder/APProcessor` from its `wheelhouse` (offline, a few minutes, ending with the self-check).
**From the repository** instead: `sudo git clone <the repository's address> /opt/apcoder/APProcessor`, then the same
`APProcessor.command` line, which then downloads the packages and the OCR models once, saying so first. The code folder
stays root's: only administrators can change it, and the service's account reads and runs it (whoever can change the
code can read every invoice). The data folder is the account's, nobody else's (the administrators reach it with sudo).
Keep both on the machine's own disk. The certificate (step 3) goes in `/var/lib/apcoder/tls`, its key readable by
`apcoder` only (`sudo chown apcoder:apcoder` and `sudo chmod 600` on it).

### The service

From the AP Coder folder, the first time:

```sh
cd /opt/apcoder/APProcessor
sudo AP_PRIVATE_DIR=/var/lib/apcoder .venv/bin/python -m ap_coder service install --workers 4 --certfile /var/lib/apcoder/tls/apcoder.pem --keyfile /var/lib/apcoder/tls/apcoder.key.pem --allowed-hosts apcoder.company.local
```

`service install` makes the account when there is none, saves the settings in `/var/lib/apcoder/server.json`, makes
the data folder's `service` and `logs` folders the account's, points `/opt/apcoder/current` at this folder, writes the
unit `/etc/systemd/system/apcoder.service` (root's), and has systemd read it, start it at every boot (*enabled*) and
start it now. Add `--dry-run` to see the unit and the commands first, changing nothing. The options are those of
step 6, but: `--account` (default `apcoder`; `root` only with `--allow-root`), and `--task-name`, which names the unit
(the default, `AP Coder`, gives `apcoder.service`; another name gives `apcoder-<name>.service`, so it never replaces
one of the system's). There is no password to give. The data folder is kept in the unit, so the other service
commands find it even when sudo left `AP_PRIVATE_DIR` out.

**The pilot's size** (up to 10 people): `--workers 4` above, and 2 capture processes (3 on a day with many scans): add
`"capture_processes": 2` to `/var/lib/apcoder/server.json` (`sudoedit` keeps the file the account's), then `apc
service start`. Left to itself, AP Coder sizes these for the machine (step 6): on 20 cores and 32 GB, 16 web workers
and 8 capture processes. That is far more than a pilot needs: section 1 measured 10 people on 4 web workers (95% of the
pages within 0.74 s, no error) and a day's 60 invoices in the review queue 4.1 minutes after they came with 2 capture
processes. Each process holds its own memory (a web worker 0.25 to 0.29 GB there, a capture process up to 320 MB) and
its share of the cores, so the rest is left to the system, the database and the OCR.

| To | Run |
|---|---|
| See whether it runs, its version and folder | `apc service status` (`--json` for a monitoring script: installed, running, the process, version, folder, address, the dashboard's answer, and systemd's state, process, restarts and last exit code) |
| Stop it (it stays stopped, also after a restart of the machine) | `apc service stop` |
| Start it again (when it runs, it is restarted) | `apc service start` |
| Change a setting (port, certificate, names, workers) | `apc service install` with the new options (the others are kept, except `--account`, `--task-name` and `--folder`: give them again unless they are the defaults) |
| Remove it (the data, settings, logs, the `current` link and the account are kept) | `apc service remove` |

How it runs: systemd starts `/opt/apcoder/current/.venv/bin/python -m ap_coder service run` as `apcoder`, the service
host of step 6, which starts the dashboard and starts it again if it stops. The unit's `Restart=on-failure`: systemd
starts the host again 10 seconds after it crashed or was killed (and never gives up), and leaves it stopped when it
ended on purpose (exit code 0) or when systemd stopped it. A host that was killed takes its dashboard with it (systemd
ends what is left of it) and is back within seconds. `service stop` leaves a `stopped` file in the data folder's
`service` folder, asks the dashboard to stop, and stops the unit (`systemctl stop`): systemd asks the host to stop
(SIGTERM, to the host alone), which stops its dashboard (the requests under way get 25 seconds; it is ended after 45
at most) and ends with exit code 0. The unit stays enabled: at the next boot systemd starts the host, which, seeing
`stopped`, ends at once, so the service stays stopped until `service start`, which removes the file and restarts the
unit. A `sudo systemctl stop apcoder` of your own stops it until the next boot; after a `service stop`, start it with
`apc service start`, not `systemctl start` (the host would end at once).

**Logs**: `/var/lib/apcoder/logs/service.log`, `access.log` and `background.log`, as in step 6; and systemd's journal,
`sudo journalctl -u apcoder`, with what Python said before the host's own log was open (a Python that won't start, a
folder it can't write). systemd's own view of it: `systemctl status apcoder` (its state, process and last exit code).

**Updating** (section 11), from the folder that runs:

```sh
apc server-update ~/APProcessor-offline-0.5.0-20261201.zip --sha256 <the 64 digits> --dry-run
apc server-update ~/APProcessor-offline-0.5.0-20261201.zip --sha256 <the 64 digits>
```

It does the steps of section 11 with the unit: it unzips the bundle beside the current folder
(`/opt/apcoder/APProcessor-0.5.0`), and once the new folder is set up and checked, stops the service, points
`/opt/apcoder/current` at it in one step (a new link renamed over the old one) and starts the service again. The unit
itself never changes. `apc server-update --back` points the link back. Leave the `current` link to `server-update`;
never change it while the service runs. An update cut short (a power cut) is dealt with at the next start as section
11 says, except what only root can do (the link): then the service stays stopped and its log says to run `apc
server-update` again.

`APProcessor.command` run on the machine starts no second AP Coder on the server's data (step 6 of the Windows steps
says what it does instead). Use `service stop` and `service start`.

### The firewall and remote access

Ubuntu's firewall, `ufw`, is off until turned on. Let in SSH and AP Coder's port from the company network only (its
addresses, here `10.0.0.0/8`); LM Studio listens on `127.0.0.1` alone, so it needs no rule:

```sh
sudo ufw allow OpenSSH
sudo ufw allow from 10.0.0.0/8 to any port 8501 proto tcp
sudo ufw enable
sudo ufw status verbose
```

IT reaches the machine by SSH only. Copy your key in (`ssh-copy-id`), then turn password logins off: a file
`/etc/ssh/sshd_config.d/10-apcoder.conf` holding `PasswordAuthentication no` (the first file there to set it wins),
then `sudo systemctl restart ssh`. Keep a second SSH session open while you do it, in case.

### LM Studio's headless server

AP Coder neither starts nor watches LM Studio: IT runs LM Studio's headless server (*llmster*) as a systemd unit of its
own, which starts it at boot and again when it stops. LM Studio installs per account and keeps its models in that
account's `~/.lmstudio/models`, so it runs as an account made for it, `lmstudio`, with its own home folder and no
password (the administrators reach it with `sudo -iu lmstudio`):

```sh
sudo useradd --create-home --shell /bin/bash --comment "LM Studio" lmstudio
```

1. **Install llmster** as that account, once the NVIDIA driver is in (its installer then takes the CUDA build). Its
   installer downloads LM Studio, so run it while the machine is being set up, before it holds any invoice:
   `sudo -iu lmstudio sh -c 'curl -fsSL https://lmstudio.ai/install.sh | bash'`. It installs `lms` in
   `~lmstudio/.lmstudio/bin`. LM Studio updates its headless server only with `lms daemon update` (step 8, point
   8): update it on purpose, as AP Coder is.
2. **Copy the models in** (nothing is downloaded), into `~lmstudio/.lmstudio/models`, as step 8, point 2 says:
   OvisOCR2 and one chat model; then `sudo chown -R lmstudio:lmstudio ~lmstudio/.lmstudio/models`, and `sudo -iu
   lmstudio .lmstudio/bin/lms ls` lists them (the chat model's exact name, for point 3). The script of point 3 loads
   the chat model on the graphics card (`--gpu max`) with its 16,384 tokens; AP Coder loads OvisOCR2 itself, on the
   card too, with the 20,480 tokens a page needs, before it reads (docs/LM_STUDIO.md). Both are loaded on purpose, so
   LM Studio's *auto-evict*, which unloads only the models it loaded just in time, leaves them loaded.
3. **The start script and its watchdog**, `/opt/apcoder/lm-studio-start.sh` (root's: `sudo chmod 755`). It starts LM
   Studio, then asks its server every 30 seconds whether it answers; after three misses in a row it stops what is left
   of LM Studio and ends with an error, and systemd starts the script again 10 seconds later (point 4's
   `Restart=always`), which starts LM Studio again:

   ```sh
   #!/bin/sh
   # LM Studio's server for AP Coder, at boot and again when it stops (docs/SERVER.md, section 15).
   LMS="$HOME/.lmstudio/bin/lms"
   "$LMS" daemon up || exit 1
   "$LMS" server start --port 1234 --bind 127.0.0.1 || exit 1
   "$LMS" unload --all
   "$LMS" load gemma-4-12b-it-qat --context-length 16384 --gpu max --yes || exit 1
   misses=0
   while sleep 30; do
     if curl -s -f -m 20 -o /dev/null http://127.0.0.1:1234/v1/models; then misses=0; else misses=$((misses + 1)); fi
     if [ "$misses" -ge 3 ]; then "$LMS" daemon down; exit 1; fi
   done
   ```

   `unload --all` before the load: a start after a stop never loads the chat model twice.
4. **Its unit**, `/etc/systemd/system/lmstudio.service`. LM Studio's own example (its *headless* page) starts the
   server once and watches nothing; this one runs the script above, which stays up while LM Studio answers, and
   `Restart=always` starts it again whenever it ends (a crash, the watchdog, a kill):

   ```ini
   [Unit]
   Description=LM Studio's headless server for AP Coder (docs/SERVER.md, section 15)
   After=network-online.target
   Wants=network-online.target
   StartLimitIntervalSec=0

   [Service]
   Type=simple
   User=lmstudio
   Environment=HOME=/home/lmstudio
   ExecStart=/opt/apcoder/lm-studio-start.sh
   Restart=always
   RestartSec=10
   TimeoutStopSec=60

   [Install]
   WantedBy=multi-user.target
   ```

5. **Its logs, deleted every night** (LM Studio's server log keeps each answer in full and the start and end of each
   request: invoice content, step 8, point 7; on an encrypted disk, below). `/etc/systemd/system/lmstudio-logs.service`:

   ```ini
   [Unit]
   Description=LM Studio's server logs older than 7 days deleted (they hold invoice content)

   [Service]
   Type=oneshot
   User=lmstudio
   Environment=HOME=/home/lmstudio
   ExecStart=/usr/bin/find ${HOME}/.lmstudio/server-logs -type f -mtime +7 -delete
   ```

   and `/etc/systemd/system/lmstudio-logs.timer`:

   ```ini
   [Unit]
   Description=Every night at 2:00, LM Studio's old server logs deleted

   [Timer]
   OnCalendar=*-*-* 02:00:00
   Persistent=true

   [Install]
   WantedBy=timers.target
   ```

6. **Start them**, and check: `sudo systemd-analyze verify /etc/systemd/system/lmstudio*` says nothing when the units
   are right.

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now lmstudio.service lmstudio-logs.timer
   systemctl status lmstudio
   sudo -iu lmstudio .lmstudio/bin/lms ps
   nvidia-smi
   ```

   `lms ps` shows the chat model loaded; `nvidia-smi`, LM Studio's process and the card's memory it holds. AP Coder
   finds LM Studio at `http://127.0.0.1:1234/v1`, its usual place: nothing to set (`AP_LLM_BASE_URL` in
   `/var/lib/apcoder/.env` only when it is elsewhere, step 8, point 5). Then *Settings* → *Reading* → *Check again*.

### Encryption

The invoices, the database and LM Studio's logs are on the machine's disk, so it is encrypted (LUKS, chosen in the
installer, above), and the machine's own TPM unlocks it at boot, so it comes back by itself after a power cut. Ubuntu's
own TPM-backed encryption (the installer's) does not support the NVIDIA driver's kernel modules in 24.04, and
*Clevis* is the way Ubuntu's forum gives for that case: it keeps the disk's key sealed in the TPM. Its packages are in
Ubuntu 24.04 (*universe*):

1. Install them, with the NVIDIA driver already in: `sudo apt install clevis clevis-luks clevis-tpm2
   clevis-initramfs`.
2. Find the encrypted partition: `lsblk -o NAME,FSTYPE,SIZE` shows it as `crypto_LUKS` (here `/dev/nvme0n1p3`).
3. Seal its key in the TPM, bound to Secure Boot's state (PCR 7, which kernel and driver updates leave alone); it asks
   the passphrase once:

   ```sh
   sudo clevis luks bind -d /dev/nvme0n1p3 tpm2 '{"pcr_bank":"sha256","pcr_ids":"7"}'
   sudo update-initramfs -u -k all
   lsinitramfs /boot/initrd.img-$(uname -r) | grep -E "clevis|tpm2_unseal"
   ```

   The last line must list Clevis and `tpm2_unseal`: the boot's own small system (the initramfs) unseals the key with
   them, and it holds them only when `clevis-tpm2` was installed before `update-initramfs` ran.
4. Restart (`sudo reboot`): the machine must come back without asking the passphrase.

**Not yet proven on the pilot's machine**: none of this has been tried with its TPM and its NVIDIA card. Prove it in the
burn-in (below): a restart and a power cut, after which the machine comes back by itself, `nvidia-smi` shows the card
and `apc service status` says the dashboard answers. When the firmware is updated, or Secure Boot's settings or its
list of revoked keys change (some firmware and boot-loader updates do), the TPM refuses (PCR 7 changed) and the machine
waits for the passphrase at boot: type it (at the machine), then seal again,
`sudo clevis luks list -d /dev/nvme0n1p3` (the slot) and `sudo clevis luks regen -d /dev/nvme0n1p3 -s <slot>`. If the
TPM won't do, Ubuntu 24.04 has two other ways, IT's choice: the key from a *Tang* server on the company network
(`clevis luks bind ... tang`, the `tang` package on another server: the machine unlocks itself only while on that
network), or an unlock by someone over SSH at boot (`dropbear-initramfs`). Without either, someone types the
passphrase at the machine after every unplanned restart.

### The invoices from the AP inbox

The emails reach a company file share first (a Windows machine's job), and the machine moves them from there:
docs/MAILBOX_SETUP.md, *The last hop on a Linux or Mac server* (the share mounted at boot, then a systemd timer that
moves the new `.eml` files into the invoices folder as the service's account). Its `<...>` are, here: the account
`apcoder`, its group `apcoder`, the data folder `/var/lib/apcoder`, the invoices folder `/var/lib/apcoder/invoices`.

### Backups to a share

The daily backup (step 9) is copied to a share on another server, mounted at boot as root with an account of its own
for that share (a domain account with *Modify* there), its files the service's account's. `x-systemd.automount` mounts
it at its first use, and again after the file server was away; the mount point itself stays root's, so while the
share is not mounted nothing is written there (the copy fails, and *Settings* → *Data & backups* says so):

1. The credentials, `/etc/apcoder-share.cred`, root's only (`sudo chmod 600`), holding the lines `username=`,
   `password=` and `domain=`.
2. A line in `/etc/fstab`:

   ```
   //fileserver.corp.example.com/Backups/APCoder /mnt/apcoder-backups cifs credentials=/etc/apcoder-share.cred,uid=apcoder,gid=apcoder,file_mode=0600,dir_mode=0700,_netdev,nofail,x-systemd.automount,x-systemd.mount-timeout=30 0 0
   ```

3. `sudo mkdir /mnt/apcoder-backups`, `sudo systemctl daemon-reload`, then `ls /mnt/apcoder-backups` mounts it (`mount
   | grep apcoder-backups` shows it).
4. Allow the folder (`apc service install --allowed-folders /mnt/apcoder-backups`, the other settings kept), then an
   Admin sets *Settings* → *Data & backups* → *Also copy each backup to* `/mnt/apcoder-backups`.

### The burn-in

Before the pilot tester and AP staff rely on it, let the machine run the service for a week. Restart it once on
purpose (`sudo reboot`); cut its power once (it comes back by itself, its disk unlocked by the TPM); kill its host once
(`sudo kill -9` of the process `apc service status` names: systemd brings it back within seconds); stop LM Studio once
(`sudo -iu lmstudio .lmstudio/bin/lms daemon down`: its watchdog starts it again within about two minutes); and, when
you have a newer bundle, run one `server-update` and one `server-update --back`. After each, `apc service status`,
`nvidia-smi`, `sudo -iu lmstudio .lmstudio/bin/lms ps` and the end of `logs/service.log`. Each morning, check that the
night's backup is in `backups` and on the share (step 9) and that the disk keeps room.

| On Linux, what you see | What to do |
|---|---|
| "Run it with sudo" | The service commands change systemd's own units: run them with sudo (`apc`). |
| "This Linux is not run by systemd" | A container, or WSL without systemd: the service needs a machine (or a virtual machine) that systemd runs. |
| "This computer has no account ..." | Leave `--account` out (`service install` makes `apcoder`), or make the account named (the `useradd` line it shows). |
| "Give the data folder" | `sudo AP_PRIVATE_DIR=/var/lib/apcoder ...` on the command line (sudo leaves the environment out). |
| `service status` says "not loaded" | `sudo systemctl daemon-reload`, then `apc service start`; `sudo journalctl -u apcoder` says why it stopped. |
| Nothing answers after a power cut | The disk waits for its passphrase (*Encryption*: the TPM refused), or the firmware's power-on after a power failure is off. |
| The log says it can't write a file in the data folder | A file root made there: `apc service start` hands root's files back to the account; else `ls -l` shows whose it is. |
| `systemctl status apcoder` says "status=75" | The host asked to be started again: another service host runs (someone ran `service run` in a terminal: end it), or an update cut short was dealt with as it started and the folder to run changed. systemd starts it again within 10 seconds; when it keeps coming back to 75, the end of `logs/service.log` says why. |

## 16. From one person's laptop to a few people on the same laptop

A prototype often starts as one person's AP Coder on a Windows 11 laptop (laptop mode: its owner alone signs in, at
127.0.0.1, D-243) and then lets a few colleagues in, from the same laptop and the same data folder, during working hours. This section
is that path. It is a prototype's set-up: the licence question of section 1 comes first, and a server of its own
(the rest of this guide) comes after.

**What happens to the data.** Nothing is converted or copied: server mode opens the same data folder and database.
The invoices, their approvals, the parked ones, the lessons, the GL accounts and the tax setup are all there when the
first Admin signs in. The names on past approvals stay as they were written ("Approved by Pat Lee"); an account
whose name is the same is taken for that person (their approvals of the day count as theirs, and they may undo
them), so give the laptop's owner an account with the name they used in laptop mode. The same holds the other way:
the data folder opens in laptop mode again with everything done in server mode, the accounts left in the database,
unused (`serve --without-sign-in`, below). When the laptop's owner already signs in on the laptop (its account
`admin`, with the name of their approvals and their authenticator app), `share` takes that account as the first
Admin: nothing is asked, and they sign in to the shared laptop as before.

**What the laptop's own sign-in does not stop** (D-243). It keeps out other computers, and anyone at the laptop
without the password and the phone; not another program or Windows account on the laptop itself. Such a program can
read the data folder anyway, if Windows lets it; it can also send wrong passwords for admin from 127.0.0.1 and pause
the owner's logins from this computer for 15 minutes, since the owner's browser comes from the same address (the
pause for one address and username, and the account-wide one counted from that address's own tries). And the browser sends the
laptop's sign-in cookie (`ap_session`, plain HTTP on 127.0.0.1) to every program listening on another port of
this computer, as browsers do with cookies of `localhost`. On a laptop shared by several Windows users, or running
programs one doesn't trust, use `share` with HTTPS, or a server of its own.

**The one thing lost.** What was typed on a review screen in laptop mode and not yet saved (a draft, kept for "this
computer") is shown to nobody in server mode, where drafts belong to accounts. It is not deleted, and shows again in
laptop mode. So approve, park or finish what is open before the change.

**Decide first:** the licence (section 1: Windows 11 Pro and Enterprise); who the Admin is (the laptop's owner, as a
rule); where the backups' second copy goes (a folder that is not on the laptop's disk); how long a session may sit
unused (30 minutes unless `server.json` says more, section 10); and, when a second-approval limit is set, who gives
the second approval: for 7 days an account can't give it to invoices first approved by the Admin who added it from
the *Users* page, so add the second approver from the console (`users add`), or name a second Admin.

**The short way: one command.** On the laptop, in the AP Coder folder, as its owner (no administrator rights):

```bat
.venv\Scripts\python.exe -m ap_coder share
```

It checks what sharing needs, says each missing thing in plain words with its next step, does itself what needs no
administrator rights, then serves AP Coder with sign-in over HTTPS from the laptop's own data folder until its window
is closed (or `python -m ap_coder share --stop` from another window). Run it again after each step it names: it
starts once the certificate and the first Admin are there. To start it with a double-click, make a file `Share AP Coder.cmd` on the desktop
holding these three lines (the last keeps the window open, so what it said can be read):

```bat
cd /d <the AP Coder folder>
.venv\Scripts\python.exe -m ap_coder share
pause
```

What it does, and what it leaves to a person:

| What sharing needs | What `share` does |
|---|---|
| A certificate (step 3 below) | Uses `server.json`'s, else `tls\apcoder.pem` and `tls\apcoder.key.pem` in the data folder, else makes that self-signed one for the laptop's name when the laptop has `openssl` (Git for Windows brings it). Without `openssl` it says so, with the command: **this step cannot be made simpler**, Windows has no tool of its own that writes the two files, and installing Git needs administrator rights on most company laptops. |
| The first Admin (step 6) | The laptop's own Admin when its owner signs in on it already (D-243). Otherwise asks for the owner's name (the one on their approvals), a username and a password, and makes the account. A scripted set-up gives the three on its input, one a line: `share --admin-from-stdin` (Windows' password question reads only the keyboard). |
| `server.json` (step 4) | Writes the certificate's two files and the port, once the first Admin is there. From then on a double-click on AP Coder's own shortcut refuses to open this data without sign-in (D-211). |
| The second backup folder (step 7) | The first time, allows the folder chosen in laptop mode; later `share --allow-folder "<folder>"` allows another, also while AP Coder is being shared (no restart). |
| The firewall (step 5) | **Never changes it**: the rule needs administrator rights. It looks whether the rule "AP Coder" is there and, when it is not, prints the command to run once in an administrator Command Prompt (ask IT without those rights). It starts all the same: the owner can sign in on the laptop. |
| The address | Prints what colleagues type, `https://AP-LAPTOP:8501`. |
| Sleep | While it serves, asks Windows not to put the laptop to sleep by itself (the screen may still turn off), and says so; once it stops, the laptop sleeps as its power settings say. Closing the lid or pressing the power button still puts it to sleep (step 8). |

It needs no `setx`, no service and no second account: it runs as the owner, on the data folder AP Coder already
uses, while the window is open and the laptop is awake. That is the price of the short way: nobody can sign in while
the owner is logged off, and nothing starts it again after a restart (the service of step 4 does both). It always
serves over HTTPS with sign-in: `--insecure-http` is not its to start. Steps 1, 5, 8 and 9 below still apply; the
others are what `share` does.

It is sized for a laptop its owner works on while it serves: 2 web workers (enough for about 10 people) and 2
processes reading imported files (about 700 MB each while they read scans), not the company server's one per core.
`workers` and `capture_processes` in `server.json` (or the variable `AP_CAPTURE_PROCESSES`) give other numbers.

**What each colleague does the first time:**

1. Open the address `share` printed, `https://AP-LAPTOP:8501`, in Edge or Chrome. It is the laptop's name, not its
   IP address: the address changes between the Wi-Fi and the dock, the name and the certificate don't.
2. The browser warns "Your connection isn't private": the laptop made its own certificate, and nobody their PC
   trusts signed it. Either press *Advanced*, then *Continue to AP-LAPTOP (unsafe)* (the warning comes back in each
   new browser session), or trust the certificate once and it never warns:
   - the owner sends them `apcoder.pem` from the data folder's `tls` folder (never `apcoder.key.pem`, the other
     file there);
   - they double-click it, then *Install Certificate…*, *Current User*, *Place all certificates in the following
     store*, *Browse…*, *Trusted Root Certification Authorities*, *Next*, *Finish*, and *Yes* to Windows' warning
     (the thumbprint it shows is the *Cert Hash(sha1)* that `certutil -dump apcoder.pem` shows on the laptop);
   - they close every Edge or Chrome window and open the address again.

   Where the company does not let people add a certificate, IT can push `apcoder.pem` to everyone's PCs (Group
   Policy or Intune, as a trusted root), or give the laptop a certificate from the company's CA (section 3). These
   steps were not tried on Windows 11 for this guide: try them on one colleague's PC first.
3. Sign in with the username and the temporary password the owner gave them in person, choose their own password,
   and scan the QR code with an authenticator app on their phone. From then on: the password, then the app's
   6-digit code.

**If the laptop's name changes** (IT renames it, or it is replaced), its certificate still names the old one, and
browsers refuse the new address. Stop sharing (`share --stop`), delete `apcoder.pem` and `apcoder.key.pem` from the
data folder's `tls` folder, and run `share` again: it makes a certificate for the new name and prints the new
address. Send colleagues the new address, and the new `apcoder.pem` to those who trusted the old one (step 2). Don't
delete those files for any other reason: a browser that trusted the old certificate refuses a new one for the same
name with no way past it (section 3, *825 days*) until the new one is trusted too.

**The ten steps, by hand:**

1. **Close laptop mode cleanly.** Finish or approve what is open on a review screen, press *Settings* → *Data &
   backups* → *Back up now*, and close AP Coder's window. Note the data folder that page names.
2. **Say where the data is, and that this is the server**, in an administrator Command Prompt (then open a new one):

   ```bat
   setx /M AP_PRIVATE_DIR C:\Users\you\APCoder
   setx /M AP_SERVER_MODE 1
   ```

3. **The certificate**, by the laptop's name: the self-signed one of section 3 (*Without a company CA*), its two files
   in the data folder's `tls` folder.
4. **Start AP Coder as the server**, one of two ways:
   - *The guide's way, a service* (steps 2 and 6): it runs as its own account, `APCoderService`, whether anyone is
     logged on or not, and comes back after a restart. That account can't open a folder inside the owner's profile
     (`C:\Users\you\...`): give it the permissions of step 2 on the AP Coder folder and the data folder, or, with AP
     Coder closed, move both out of the profile first (`C:\APCoder\APProcessor`, `C:\APCoderData`: copy the whole
     data folder, and give step 2 of this list the new place). Then:

     ```bat
     .venv\Scripts\python.exe -m ap_coder service install --workers 4 --certfile C:\APCoderData\tls\apcoder.pem --keyfile C:\APCoderData\tls\apcoder.key.pem --allowed-folders "<the second backup folder>"
     .venv\Scripts\python.exe -m ap_coder service status
     ```

   - *The prototype's way, a window the owner opens each morning*: `python -m ap_coder share`, above. By hand it
     is this: it runs as the owner, only while they are logged
     on and the window is open, and nothing starts it again after a restart. Write `server.json` in the data folder:

     ```json
     {"port": 8501, "host": "0.0.0.0", "certfile": "C:\\Users\\you\\APCoder\\tls\\apcoder.pem",
      "keyfile": "C:\\Users\\you\\APCoder\\tls\\apcoder.key.pem", "workers": 4, "capture_processes": 2,
      "allowed_folders": ["<the second backup folder>"], "time_zone": "America/Toronto"}
     ```

     and a file `AP Coder server.cmd` holding the two lines `cd /d <the AP Coder folder>` and
     `.venv\Scripts\python.exe -m ap_coder service run`. Double-click it to start; close its window to stop. (It is
     the program the service's task runs, started by hand. Don't stop it with `service stop`: that leaves the
     file `service\stopped` in the data folder, and the next start then ends at once until the file is deleted.)
     This way was run on Linux for this guide, not on Windows: check on the laptop that closing the window leaves no
     `python.exe` of AP Coder in Task Manager.
5. **The firewall**, for the office network only (step 5). A laptop also joins other networks: limit the rule to
   the profile the office network has (`Get-NetConnectionProfile` in PowerShell shows it: *Domain* only on a
   computer joined to the company's domain) and to its address range:

   ```bat
   netsh advfirewall firewall add rule name="AP Coder" dir=in action=allow protocol=TCP localport=8501 profile=domain remoteip=10.0.0.0/16
   ```

6. **The first Admin** (step 7), with the name the owner used in laptop mode, then the others:

   ```bat
   .venv\Scripts\python.exe -m ap_coder users add --admin pat --name "Pat Lee"
   ```

   The owner opens `https://AP-LAPTOP:8501`, chooses a password and sets up the authenticator app, then adds the
   colleagues on the *Users* page (Clerk; an Approver when a second approval is used), giving each their temporary
   password in person: it works for 3 days.
7. **Backups** (step 9): *Settings* → *Data & backups* → *Also copy each backup to* the second folder (one
   `server.json` allows: `share --allow-folder`, or `allowed_folders`). The automatic backup is made every day at the
   time set on that page (*Back up every day at*, 17:30 until another is saved: the end of the day, with the day's
   work in it), while AP Coder is running. A laptop asleep or AP Coder closed at that time makes it at the next
   start, and the page says when the last one was made and why ("made at start: AP Coder was not running at
   17:30"): so leave the window open until that time, or press *Back up now* before closing it. After any restore,
   set the second folder again: a restore clears it, and the page says so.
8. **Power**: plugged in; in Windows' power settings sleep *Never*, hibernation off, closing the lid *Do nothing*
   (`share` keeps the laptop from sleeping by itself while it serves, but not from the lid or the power button);
   Windows Update's restarts outside working hours. A laptop asleep answers nobody; awake again it goes on, and
   whoever was away longer than the session's idle time logs in again, their typed edits kept. LM Studio: started
   in the owner's session, or as the task of step 8.
9. **Colleagues** open `https://AP-LAPTOP:8501` (the name, not an address) and accept the certificate's warning, or
   trust the certificate first (*What each colleague does the first time*, above). Try from one colleague's PC, on
   the Wi-Fi and on the dock, before telling the others.
10. **Later, a server of its own**: install AP Coder there with the service not started, restore the newest backup
    from the second folder before any `users add`, start the service, and set again the `.env`, the allowed folders
    and the second backup folder (step 9, *A new server*). Everyone signs in there with the same password and the same
    authenticator app.

**The old desktop shortcut** (and `APProcessor.bat`) is safe to keep: while the server runs, a double-click opens
`https://AP-LAPTOP:8501` in the browser and starts nothing; while it is stopped, it says so and starts nothing
(step 6). To go back to laptop mode for good, stop the server (and `service remove`), take `AP_SERVER_MODE` out of
the system's environment variables, and start with `APProcessor.bat --without-sign-in`: the data has sign-in
accounts, so each laptop-mode start asks for that option, on purpose (D-211).

**Tried on Windows by CI** (the job "share on Windows", `scripts/ci/share_end_to_end.py`, on GitHub's Windows
Server runner, not Windows 11): installed, `share` makes the certificate with Git for Windows' `openssl` (not on the
PATH) and the first Admin, and serves; a second browser signs in over HTTPS by the computer's name, trusting that
certificate, with the authenticator's code, and approves an invoice; Windows lists `share` as keeping the system
awake (`powercfg /requests`) until `share --stop`; `share` again, the data kept.

**Not tried on Windows 11 for this guide** (do each once on the laptop before colleagues rely on it):
that `netsh` shows the firewall rule without administrator rights, that closing `share`'s window leaves no
`python.exe` of AP Coder in Task Manager, and what Windows' own firewall question does when it is cancelled;
`service install` and `service status` there, and the task coming back after a Windows Update restart with nobody
logged on; the hand-started window; the firewall rule's profile on the office Wi-Fi and the dock, and that the port
is closed on a home network; that colleagues' PCs find the laptop by its name; the lid, sleep and the dock.
