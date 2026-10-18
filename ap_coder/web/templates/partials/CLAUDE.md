# ap_coder/web/templates/partials: self-contained template pieces

Template pieces that draw themselves from a template global, so any page can include them with nothing to pass.
Today there is one: the card of the optional Azure check (a cloud second opinion, off by default; the app is otherwise fully offline).
The Azure check's logic is in `ap_coder/azure_check.py`; its routes are in `ap_coder/web/pages/azure.py`. Shared macros live in the parent folder's `_*.html` files instead.

**Depends on:** `ap_coder/web/pages` (azure.py sets the `azure_check_card` global it calls)
**Used by:** `ap_coder/web/templates` (settings_reading.html and azure_check_page.html include it)

## Interface
- `{% include "partials/azure_check.html" %}`: no context needed; it calls `azure_check_card(request)` (azure.py `card`), which never raises and returns `{"broken": ...}` when the state can't be read.
- Its forms post to `/settings/azure-check` (save the settings) and `/settings/azure-check/test` (start the "azure-test" job), with `data-busy` labels; app.js sends them with `X-AP-Coder: 1`.

## Gotchas and decisions
- The API key is never put in the page: the password field is always empty, an empty field keeps the saved key, "Remove the saved key" (forget_key) clears it.
- settings_reading.html includes it with `ignore missing`; azure_check_page.html includes it plainly.
- Autoescape is on, as for every template: text from Azure or the settings is never marked safe.
- The `https://<name>...` text is placeholder help, not a request: nothing is loaded from the internet.
- Its few styles are an inline `<style>` in the card (`.azure-check`, using the `--lg-*` tokens), not a static/ stylesheet.

## Files
- `azure_check.html`: the Azure check card (on/off, endpoint, key, model, spot-check share, daily cap, test button, pages checked, agreement and disagreements).
