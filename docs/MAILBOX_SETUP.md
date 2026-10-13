# Getting the AP mailbox's emails into AP Coder (for IT)

AP Coder **never connects to the mailbox**, to Exchange or to the internet. A tool PCL already runs saves each email,
as a file, into AP Coder's **invoices folder** on the AP computer (`C:\Users\<user>\APCoder\invoices` by default;
*Settings → Data* shows it). AP Coder then sorts every email it finds there, reads the invoices and sets the rest
aside for a person. No mailbox password, token or network access is given to AP Coder.

**What to deliver:** the whole email, as **.eml** or **.msg** (not only its attachments): the subject and text are what
reveal a bank-detail change or a payment reminder. One file per email, any unique name. A file still being written is
fine: AP Coder waits until a file has not changed for 5 seconds.

## Option 1: Power Automate on the shared mailbox (recommended)

A cloud flow, owned by IT, on the AP shared mailbox:

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

`/MOV` removes each file from the library once copied, so the library only holds what AP Coder has not taken yet.
Where an on-premises data gateway is already in use, step 3 can instead be **File System: Create file** on a PCL file
share, with the same scheduled `robocopy` from that share.

## Option 2: a mail-saving tool PCL already has

Any server-side tool that saves messages as .eml or .msg (an archiving, journaling or capture product) can write to a
PCL file share; the same scheduled `robocopy ... /MOV` moves them into the invoices folder.

## Option 3: no setup (the pilot's first weeks)

AP staff drag emails from **classic Outlook** into the invoices folder (Outlook saves them as .msg), or in **new
Outlook / Outlook on the web** use *Download* (.eml) and save there. Nothing to install; it shows the triage working
before a flow is built.

## Not recommended

* **AP Coder signing in to the mailbox** (Microsoft Graph, IMAP or EWS): it would need internet access and mailbox
  credentials, which this build is designed never to have.
* **Outlook "run a script" rules** (VBA): usually blocked by policy, and they only run while Outlook is open.

## What AP Coder does with each email

| The email is | Its attachments go to | A person sees it on |
|---|---|---|
| An invoice or credit note | the review queue (next to the email in the invoices folder) | Review queue |
| A statement of account | `statements\` | Statements → *Statements received by email* |
| A payment reminder (no invoice attached) | `emails\needs a person\` | Process invoices → *Emails that need a person* |
| A bank-detail change request | `emails\needs a person\`, flagged as **possible fraud** | Process invoices (in red) and the Activity log |
| Not for AP (quote, PO, packing slip, remittance advice, out-of-office, newsletter, a portal link) | `emails\needs a person\` | Process invoices |

An invoice attached to a reminder or to a bank-change email is still read into the review queue (the duplicate check
says if AP already has it); a document that does not say what it is goes to the review queue too, so **no invoice is
held back by the sorting**. Each email's sorting and the reason are kept in `emails\triage.jsonl`.

## Checking the setup (10 minutes)

Send three test emails to the AP mailbox: one with a PDF invoice, one with a vendor statement, and one saying "please
note our new bank details". Within a few minutes, with AP Coder's folder pick-up running (*Process invoices → Process*,
or the scheduled `python -m ap_coder watch --once`), the invoice is in the review queue, the statement on the
Statements page and the bank-detail email in red under *Emails that need a person*.
