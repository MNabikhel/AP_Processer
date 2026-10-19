# .github: GitHub configuration (CI only)

This folder holds GitHub's configuration for AP Coder. Today that is only the CI workflow, in `workflows/`.
There are no issue templates, no Dependabot file and no release workflow. The app itself never talks to GitHub:
it is fully offline at run time (Azure is an opt-in exception).

**Depends on:** `scripts`, `scripts/ci`, `tests`, `ap_coder`
**Used by:** GitHub Actions (on every push to `main`, every pull request, and by hand)

## Interface
- `workflows/ci.yml` is the one workflow; what each job runs and how to read a failure is in `.github/workflows/CLAUDE.md`.

## Gotchas and decisions
- GitHub rejects a workflow whose `env:` block repeats a name in another case (HTTP_PROXY and http_proxy): a
  rejected file runs no job at all, so nothing is tested. `tests/test_ci_workflow.py` guards this.

## Files
- `workflows/`: the CI workflow (has its own CLAUDE.md).
