# Getting the AP mailbox's emails into AP Coder (for IT)

AP Coder **never connects to the mailbox**, to Exchange or to the internet. A tool the company already runs saves each email,
as a file, into AP Coder's **invoices folder** on the AP computer (`C:\Users\<user>\APCoder\invoices` by default;
on a company server, `invoices` in its data folder, e.g. `D:\APCoderData\invoices`; *Settings → Data & backups*
shows the data folder). AP Coder then sorts every email it finds there, reads the invoices and sets the rest
aside for a person. No mailbox password, token or network access is given to AP Coder.

**What to deliver:** the whole email, as **.eml** or **.msg** (not only its attachments): the subject and text are what
reveal a bank-detail change or a payment reminder. One file per email, any unique name. A file still being written is
fine: AP Coder waits until a file has not changed for 5 seconds.

## Option 1: Power Automate on the shared mailbox (recommended)

A cloud flow on the AP shared mailbox. The developer builds it in Phase 0 (D-147), and IT gives the flow's account
access to the mailbox; IT can own the flow instead if it prefers:

| Step | Connector and action | Settings |
|---|---|---|
| 1 | Office 365 Outlook: **When a new email arrives in a shared mailbox (V2)** | Mailbox: the AP address; folder: Inbox |
| 2 | Office 365 Outlook: **Export email (V2)** | Message Id: the trigger's *Message Id* (gives the email as .eml) |
| 3 | SharePoint: **Create file** (or OneDrive for Business) | Folder: an *AP Coder inbox* library; name: `@{formatDateTime(utcNow(),'yyyyMMdd-HHmmss')}-@{guid()}.eml`; content: step 2's body |
| 4 (optional) | Office 365 Outlook: **Move email (V2)** | To a *Sent to AP Coder* folder, so AP sees what was handed over |

On the AP computer, the *AP Coder inbox* library is synced with OneDrive (*Always keep on this device*), and a
scheduled task moves new files into the invoices folder every 5 minutes:

```
robocopy "<synced AP Coder inbox folder>" "%USERPROFILE%\APCoder\invoices" *.eml /MOV /R:1 /W:5
```

On a company server, the target is the server's invoices folder instead (e.g. `D:\APCoderData\invoices`), and the
task runs as an account that may write there.

`/MOV` removes each file from the library once copied, so the library only holds what AP Coder has not taken yet.
Where an on-premises data gateway is already in use, step 3 can instead be **File System: Create file** on a company file
share, with the same scheduled `robocopy` from that share. On a Linux or Mac server: *The last hop on a Linux or Mac
server*, below.

## Option 2: a mail-saving tool the company already has

Any server-side tool that saves messages as .eml or .msg (an archiving, journaling or capture product) can write to a
company file share; the same scheduled `robocopy ... /MOV` moves them into the invoices folder.

## Option 3: no setup (before the flow is built, e.g. for a first try)

AP staff drag emails from **classic Outlook** into the invoices folder (Outlook saves them as .msg), or in **new
Outlook / Outlook on the web** use *Download* (.eml) and save there. Nothing to install; it shows the triage working
before a flow is built.

## The last hop on a Linux or Mac server

OneDrive's sync runs only for someone signed in on Windows or a Mac, and `robocopy` and the on-premises data gateway
are Windows programs (the gateway's requirements: Windows 10, 11 or Server 2019 to 2025). So the emails reach a
Windows file share first, and the server takes them from there:

1. **The emails to a share**, for example `\\fileserver\APCoderInbox`: step 3 is **File System: Create file** through
   a data gateway on a Windows machine that is always on (one the company already runs), or the synced library and
   its `robocopy` run on a Windows PC that is always on, with that share as their target. Option 2's tool writes to
   the share itself. The server reads it with an account of its own for that share (a domain account), with
   *Modify* there.
2. **The server mounts the share at boot**, with that account:
   - **Linux** (the `cifs-utils` package): a line in `/etc/fstab`, each `<...>` replaced (the service's account and
     the data folder are those of docs/SERVER.md section 15), then `sudo mkdir /mnt/ap-inbox` and
     `sudo mount /mnt/ap-inbox` to try it. The credentials file is root's (`chmod 600`) and holds the lines `username=`,
     `password=` and `domain=`; `uid` and `gid` make the share's files the service's account's, and only its.

     ```
     //fileserver.corp.example.com/APCoderInbox /mnt/ap-inbox cifs credentials=/etc/apcoder-inbox.cred,uid=<the service's account>,gid=<its group>,file_mode=0600,dir_mode=0700,_netdev,nofail 0 0
     ```

   - **Mac**: as root, as docs/SERVER.md section 14 does for the backup share (*A network share*), to
     `/Library/APCoderShare/inbox`.
3. **A scheduled job moves the new emails into the invoices folder, as the service's account**, every 5 minutes,
   those unchanged for a minute (one the flow is still writing waits for the next run):
   - **Linux**, a systemd timer. `/etc/systemd/system/apcoder-inbox.service`:

     ```ini
     [Unit]
     Description=The AP inbox's emails into AP Coder's invoices folder (docs/MAILBOX_SETUP.md)
     RequiresMountsFor=/mnt/ap-inbox

     [Service]
     Type=oneshot
     User=<the service's account>
     ExecStart=/usr/bin/find /mnt/ap-inbox -maxdepth 1 -type f -name *.eml -mmin +1 -exec mv -n -t <the data folder>/invoices {} +
     ```

     and `/etc/systemd/system/apcoder-inbox.timer`:

     ```ini
     [Unit]
     Description=Every 5 minutes, the AP inbox's emails into AP Coder

     [Timer]
     OnBootSec=2min
     OnUnitActiveSec=5min

     [Install]
     WantedBy=timers.target
     ```

     then `sudo systemctl daemon-reload` and `sudo systemctl enable --now apcoder-inbox.timer`;
     `systemctl list-timers` shows when it runs next, `journalctl -u apcoder-inbox` what it did.
   - **Mac**, a LaunchDaemon, `/Library/LaunchDaemons/com.apcoder.inbox.plist`, loaded as docs/SERVER.md section 14
     loads LM Studio's (its point 3):

     ```xml
     <?xml version="1.0" encoding="UTF-8"?>
     <plist version="1.0">
     <dict>
       <key>Label</key><string>com.apcoder.inbox</string>
       <key>ProgramArguments</key><array>
         <string>/usr/bin/find</string><string>/Library/APCoderShare/inbox</string><string>-maxdepth</string><string>1</string>
         <string>-type</string><string>f</string><string>-name</string><string>*.eml</string><string>-mmin</string><string>+1</string>
         <string>-exec</string><string>/bin/mv</string><string>-n</string><string>{}</string><string>/Library/APCoderData/invoices/</string><string>;</string>
       </array>
       <key>UserName</key><string>_apcoder</string>
       <key>StartInterval</key><integer>300</integer>
     </dict>
     </plist>
     ```

Moving a file out of the share removes it there, as `robocopy /MOV` does, so the share holds only what AP Coder has
not taken yet. A tool that saves `.msg` files (Option 2): `*.msg` in place of `*.eml`.

## Not recommended

* **AP Coder signing in to the mailbox** (Microsoft Graph, IMAP or EWS): it would need internet access and mailbox
  credentials, which this build is designed never to have.
* **Outlook "run a script" rules** (VBA): usually blocked by policy, and they only run while Outlook is open.

## What AP Coder does with each email

| The email is | Its attachments go to | A person sees it on |
|---|---|---|
| An invoice or credit note | the review queue (next to the email in the invoices folder) | Review queue |
| A statement of account | `statements\` | Statements → *Statements received by email* |
| A payment reminder (no invoice attached) | `emails\needs a person\` | Import invoices → *Emails that need a person* |
| A bank-detail change request | `emails\needs a person\`, flagged as **possible fraud** | Import invoices (in red) and the Activity log |
| Not for AP (quote, PO, packing slip, remittance advice, out-of-office, newsletter, a portal link) | `emails\needs a person\` | Import invoices |

An invoice attached to a reminder or to a bank-change email is still read into the review queue (the duplicate check
says if AP already has it); a document that does not say what it is goes to the review queue too, and so does a quote, PO, packing
slip or remittance advice whose top also says *invoice* (a remittance listing the invoices it pays, a PO printed
with an invoice number): **no invoice is held back by the sorting**, and AP rejects such a document on the review
screen. An attachment AP Coder does not read (a .zip, a Word file, a damaged one) is never
dropped silently: the email waits under *Emails that need a person*, with what was left out, while its other invoices
are read. An email attached to an email (.eml or .msg) is opened like a forwarded one. Each email's sorting and the
reason are kept in `emails\triage.jsonl`.

## Checking the setup (10 minutes)

Send three test emails to the AP mailbox: one with a PDF invoice, one with a vendor statement, and one saying "please
note our new bank details". Within a few minutes, with AP Coder's folder pick-up running (*Import invoices → Process*,
or the scheduled `python -m ap_coder watch --once`), the invoice is in the review queue, the statement on the
Statements page and the bank-detail email in red under *Emails that need a person*.
