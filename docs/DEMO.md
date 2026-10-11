# The public demo

A free, public copy of the review dashboard on Streamlit Community Cloud, so anyone with the link can
try AP Coder in their browser: **https://ap-coder-demo.streamlit.app**.

It runs `streamlit_app.py`, which starts the normal dashboard in *public demo mode*:

- **Made-up invoices only.** The ten sample invoices in `samples/` are loaded on the first visit, as
  if AP Coder had read and coded them, with the sample GL accounts, purchase orders and vendor list.
- **No readers, no secrets.** No `.env` is read and no readers run (no OCR, no LM Studio), so nothing is
  sent anywhere. *Process invoices* and the touchless switch in *Settings → Automation* say they are not
  available in the demo; *Settings → Reading* explains how invoices are read.
- **Nothing is kept.** The database lives in a temporary folder on the server and starts over whenever
  the app restarts. A banner on every page says so and has a **Reset demo** button that starts over
  with the original invoices.
- Everything else works: review, correct and approve, bulk approve, exports and approved PDFs,
  Insights and the business case, Sales tax, Spend, Activity and the duplicate audit, and so on.

All visitors share the one demo. If an earlier visitor approved every invoice, the next new visitor
gets a fresh demo automatically.

## Deploy it (once, about 5 minutes)

The repository must be public, and the demo files must be on the branch you deploy (`main` once
this work is merged).

1. Go to **https://share.streamlit.io** and sign in with GitHub (allow Streamlit to see your
   repositories).
2. Click **Create app**, then choose to deploy from a GitHub repository.
3. Fill in:
   - **Repository:** `MNabikhel/AP_Processer`
   - **Branch:** `main` (or `claude/epic-feynman-r6ns86` until it is merged)
   - **Main file path:** `streamlit_app.py`
   - **App URL:** `ap-coder-demo` (this gives `ap-coder-demo.streamlit.app`, the link in the README)
4. Open **Advanced settings**, choose **Python 3.12**, leave **Secrets** empty, and save.
5. Click **Deploy**. The first start installs the packages in `requirements.txt` and takes a few
   minutes; then the review queue opens with the demo invoices.

If the `ap-coder-demo` name is taken, pick another and change the links under *Try the live demo*
at the top of the README.

## Looking after it

- **Updates:** every push to the deployed branch updates the demo automatically.
- **Start over:** anyone can click **Reset demo** in the banner. To restart the whole app (for
  example if it seems stuck), open https://share.streamlit.io, click **⋮** next to the app and
  choose **Reboot**. After a reboot the demo starts over.
- **Sleeping:** after a while without visitors Community Cloud puts the app to sleep. The next
  visitor sees a button to wake it up; it takes about a minute.
- **Another branch:** the branch and main file of a deployed app cannot be changed. To switch (for
  example from the work branch to `main`), delete the app in share.streamlit.io and deploy it again
  with the same App URL.
- **Never add secrets.** The demo needs none, and it ignores Azure settings even if some are added.

## Run the demo on your computer

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

It uses a temporary folder too, never your AP Coder data folder. For real work, use `start.bat` or
`python -m ap_coder dashboard` as in [GETTING_STARTED.md](GETTING_STARTED.md).
