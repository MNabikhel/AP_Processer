# AP Coder on a company Windows Server (for IT)

This guide sets AP Coder up on one Windows Server for the whole AP team. It runs as a Windows service, staff open it
in their browser over HTTPS and log in with their own account, and nothing is installed on their PCs. Everything stays
offline: AP Coder and LM Studio run on the server and connect to nothing on the internet.

The laptop pilot is not changed by any of this: a laptop still runs AP Coder for one person at 127.0.0.1, with no
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
| Windows | Windows Server 2019, 2022 or 2025, 64-bit | |
| Processor | 8 cores (the OCR of scans runs on the processor) | 16 cores |
| Memory | 32 GB | 64 GB |
| Graphics card (for LM Studio) | NVIDIA with 12 GB of VRAM | 16 GB of VRAM or more |
| Disk | 60 GB free on an SSD | 200 GB |
| Python | Python 3.12, 64-bit, installed for all users | |
| LM Studio | 0.4.8 or newer, with its models copied in | |

Why these numbers:

- **The models** (docs/LM_STUDIO.md): the chat model Gemma 4 12B is about 7.2 GB, or Qwen 3.5 9B about 6.5 GB;
  OvisOCR2, the page reader that reads every page of every invoice, about 1 GB; the embedding model under 1 GB; plus
  the room each needs for its context (16,384 and 20,480 tokens). On 8 GB of VRAM they spill into the server's
  memory and are slower; 12 GB holds them, 16 GB leaves room. Without a graphics card AP Coder still works, but
  reading each page takes minutes instead of seconds.
- **Disk**: each AP Coder version is 2 to 3 GB (its packages and OCR models), and two are kept (the current one and
  the previous one, to go back to). The models are about 10 GB. The data grows with the invoices (about 0.5 MB each,
  kept with the database) and 14 daily copies of the database.
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

   This makes `C:\APCoder\APProcessor`. Unzip it on the server's own disk, not a network drive.

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
   (the database holds every invoice). The code folder: read and run for `APCoderService`.

   ```bat
   icacls D:\APCoderData /inheritance:r /grant:r "APCoderService:(OI)(CI)F" "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
   icacls C:\APCoder /grant "APCoderService:(OI)(CI)RX"
   ```

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
is on this server), so AP Coder believes the forwarded client address from that one address only.

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
runs in; `server-update` changes it), `--no-start`.

| To | Run |
|---|---|
| See whether it runs, its version and folder | `.venv\Scripts\python.exe -m ap_coder service status` |
| Stop it (it stays stopped, also after a restart of the server) | `.venv\Scripts\python.exe -m ap_coder service stop` |
| Start it again | `.venv\Scripts\python.exe -m ap_coder service start` |
| Change a setting (port, certificate, names) | `service install` again with the new options (the others are kept) |
| Remove it (the data, settings and logs are kept) | `.venv\Scripts\python.exe -m ap_coder service remove` |

What it does: the task runs `run.cmd`, which runs AP Coder's *service host* (`python -m ap_coder service run`). The
host starts the dashboard (`python -m ap_coder serve --server ...` with the settings of `server.json`) and starts it
again if it stops (after 5 seconds, then longer, up to 5 minutes). The task itself comes back within 5 minutes if the
host was ended. Settings can also come from environment variables, which win over `server.json`: `AP_SERVER_PORT`,
`AP_DASHBOARD_ADDRESS`, `AP_TLS_CERT`, `AP_TLS_KEY`, `AP_ALLOWED_HOSTS`, `AP_SERVER_PROXY`.

**Logs**: `D:\APCoderData\logs\service.log` (what the host and the dashboard say; 5 MB, then up to 5 older files,
`service.log.1` to `.5`). The daily database backup runs as on a laptop (step 9).

Don't double-click `APProcessor.bat` on the server once the service runs: it would start a second AP Coder on the same
data. Use `service stop` / `service start`.

## 7. The first admin

On the server's console (nobody can sign up from a browser):

```bat
.venv\Scripts\python.exe -m ap_coder users add --admin jsmith
```

It asks for a password, twice (at least 12 characters, not the user name). That password is a temporary one: tell it
to the person, who must choose their own the first time they sign in at `https://apcoder.company.local:8501`, before
anything else. They then add everyone else (step 10).

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

## 9. Backups

- **Every day, automatically**: a copy of the database in `D:\APCoderData\backups` (`ap_coder-<date>-<time>.db`), when
  AP Coder starts and once a day while it runs; the newest 14 are kept. An update adds its own
  (`...-before-update-<version>.db`), kept until you delete it.
- **A copy elsewhere**: an Admin sets *Settings* → *Data & backups* → *Also copy each backup to* a network share, for
  example `\\fileserver\Backups\APCoder` (the `backup_copy_dir` setting). Each daily backup is then copied there too
  (the newest 14 kept), and the page says when the last copy was made or why it failed. The service's account must be
  able to write there: a domain account (step 2), with *Modify* on that share folder.
- **The server's own backup** (Windows Server Backup, Veeam...): include `D:\APCoderData`, but restore from the
  `backups` copies, not from a copy of the open `ap_coder.db` file.
- **Restoring**: *Settings* → *Data & backups* → restore a backup (an Admin). By hand: `service stop`, copy the backup
  over `D:\APCoderData\ap_coder.db` (delete `ap_coder.db-wal` and `ap_coder.db-shm` beside it), `service start`.

## 10. Users and roles

An Admin adds people on the *Users* page (`https://apcoder.company.local:8501/admin/users`): a user name, their name
and a role. AP Coder makes up a temporary password and shows it once, on that page: give it to the person (it is not
kept anywhere to look up later). They must change it when they first sign in. The same page resets a password (a new
temporary one, shown once), changes a role, and turns an account off or on again (an Admin can't turn off the last
Admin).

| Role | Can |
|---|---|
| Read-only | Look at invoices, the review queue, reports, the exports list and Help. Change nothing. |
| Clerk | Also import and process invoices, review and correct them, approve within the limit, park, reject, reopen, bulk approve, and export batches. |
| Approver | Also give the second approval (always by another account than the first). |
| Admin | Everything else: Settings, users, the JD Edwards setup, GL accounts and vendors imports, backups and restore, deleting, the Azure check, updating. |

Pages show each person only what their role may use; the server refuses the rest anyway. The JD Edwards settings
are on *Settings* → *JD Edwards E1* from the start on the server (step 2).

From the server's console, the same:

```bat
.venv\Scripts\python.exe -m ap_coder users list
.venv\Scripts\python.exe -m ap_coder users add mlee --name "Mary Lee" --role approver
.venv\Scripts\python.exe -m ap_coder users role mlee clerk
.venv\Scripts\python.exe -m ap_coder users reset mlee
.venv\Scripts\python.exe -m ap_coder users deactivate mlee
.venv\Scripts\python.exe -m ap_coder users activate mlee
```

`users add` takes `--name` (the name on their approvals; default: the user name) and `--role` (`readonly`, `clerk`,
`approver` or `admin`; default `clerk`; `--admin` is `--role admin`). On the console the temporary password is the one
you type, twice (`add` and `reset`); the person changes it at their first sign-in. `activate` turns an account that
was turned off on again, with its password as it was.

Five wrong passwords in a row lock an account for 15 minutes. Every sign-in, lockout and change to a user is in
*Activity*.

## 11. Updating and going back

A new version comes as a new offline bundle. Copy it to the server, then, in the current AP Coder folder:

```bat
.venv\Scripts\python.exe -m ap_coder server-update D:\Install\APProcessor-offline-0.5.0-20261201.zip --dry-run
.venv\Scripts\python.exe -m ap_coder server-update D:\Install\APProcessor-offline-0.5.0-20261201.zip
```

The first line shows the steps with the folders and files, and changes nothing (on a server where the service isn't
installed yet, it says there is no service to stop or start). The second does them, in order:

1. checks the bundle: one AP Coder folder, every file it lists, nothing damaged, and newer than the one running;
2. stops the service (staff see AP Coder as closed for a few minutes: pick a quiet time);
3. backs up the database: `backups\ap_coder-<date>-<time>-before-update-0.5.0.db`;
4. unzips the bundle next to the current folder: `C:\APCoder\APProcessor-0.5.0`;
5. sets that folder up from its own `wheelhouse` (offline; its launcher's window shows it);
6. updates the database (the new version opens it once);
7. runs the self-check (the sample invoices read right);
8. starts the service from the new folder, and waits for it to answer.

If step 5, 6, 7 or 8 fails, it puts the database backup back, switches the service back to the old folder and starts
it again, and says why. The new folder is kept to look at; delete it when done.

**Going back** after an update that went through, when the new version turns out wrong:

```bat
.venv\Scripts\python.exe -m ap_coder server-update --back
```

It stops the service, keeps a copy of the database as it is now (`backups\...-before-going-back.db`), puts back the
backup made before the update, and starts the previous folder again. What was done in AP Coder since the update is
not in that backup (the older version can't open a database the newer one changed), so go back soon, or not at all.
It asks first (`--yes` doesn't); `--dry-run` shows what it would do.

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
| `server-update` says the bundle is not newer | It is the version running, or older. To go back to the previous version: `server-update --back`. |
| `server-update --back` says "Nothing to go back to" | No update was made with `server-update` since the last `--back`. To install an older version, ask for its bundle and the backup made before the update. |
| Someone forgot their password, or is locked out | An Admin: *Users* → *Reset password*; or on the console `users reset <username>`. A lockout ends by itself after 15 minutes. |
| Anything else | `.venv\Scripts\python.exe -m ap_coder doctor`, and the log. *Help* → *Report a problem* makes a report without invoice data. |

**What `doctor` checks on the server** (when `AP_SERVER_MODE=1`, or `server.json` is in the data folder), besides
what it checks on a laptop: *dashboard network* (the address and port the server listens on, and the address staff
open), *server HTTPS certificate* and *private key* (set, there, and readable by the account you run it as; run it
as `APCoderService` to check the service's own rights), *server names* (the allowed names, a warning when none is set)
and, on Windows, *server service* (the scheduled task: installed, its state, the account, and whether it was stopped
on purpose).
