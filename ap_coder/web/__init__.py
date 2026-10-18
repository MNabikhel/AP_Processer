"""AP Coder's dashboard as a local web app (FastAPI + Jinja templates + a little plain JavaScript), the way CloseDesk
does it. Start it with ``python -m ap_coder serve`` (``web/server.py``): it listens on 127.0.0.1 and opens the
browser. Nothing is loaded from the internet: fonts, icons, styles and scripts are all in this package.

Layout
------
* ``app.py``: ``create_app(settings=None, store=None)``, the security middleware, /healthz, /shutdown, /jobs/<name>.
* ``core.py``: what pages share: ``render``, ``get_store`` / ``get_settings``, the rail, messages after an action
  (``done`` / ``message`` / ``refuse``), ``reviewer``, ``money``, the ``ui`` helpers in templates.
* ``jobs.py``: long work in a background thread, polled by the page (``Job.start``, ``GET /jobs/<name>``).
* ``security.py``: only this computer's addresses; only AP Coder's own pages may POST.
* ``pages/__init__.py``: ``PAGES``, the one list of pages, in rail order. ``pages/<name>.py``: one module per page.
* ``templates/``: ``base.html`` (rail, header, messages), one template per page, ``_*.html`` partials.
* ``static/``: ``app.css`` (the Ledger look of ap_coder/ui.py), ``js/app.js`` (POSTs, messages, jobs), page scripts.

Adding a page
-------------
1. Write ``ap_coder/web/pages/<name>.py`` with an ``APIRouter`` called ``router``::

       from fastapi import APIRouter, Form, Request
       from fastapi.responses import HTMLResponse, JSONResponse

       from ap_coder.web.core import done, get_store, render

       router = APIRouter()

       @router.get("/vendors", response_class=HTMLResponse)
       def vendors_page(request: Request) -> HTMLResponse:
           return render(request, "vendors.html", "vendors", vendors=get_store(request).vendor_summaries())

       @router.post("/vendors/{key}/terms")
       def save_terms(request: Request, key: str, terms: str = Form("")) -> JSONResponse:
           get_store(request).set_vendor_terms(key, terms)
           return done(request, "/vendors", "Terms saved.")

2. Write ``templates/<name>.html``::

       {% extends "base.html" %}
       {% block content %}
       <section class="card">
         <h4>Vendors</h4>
         {% for v in vendors %}<div>{{ v.vendor_name }} {{ ui.pill("Touchless", "ok") }}</div>{% endfor %}
         <form method="post" action="/vendors/abc/terms"><input name="terms"><button class="btn">Save</button></form>
       </section>
       {% endblock %}

   The header (breadcrumb, title, the subtitle from ``PAGES``) comes from base.html; override the blocks
   ``heading``, ``subtitle`` or ``aside`` (right of the title) when needed, and ``scripts`` for a page script.
3. In ``pages/__init__.py`` set ``module="<name>"`` on the page's entry. That's all: the rail already lists it.

Rules
-----
* GET never changes anything. Every change is a ``<form method="post">``: ``static/js/app.js`` sends it with the
  header ``X-AP-Coder: 1`` (the server refuses a POST without it) and follows the JSON answer of ``done`` (go to a
  page, with a message), ``message`` (stay, toast) or ``refuse`` (stay, show the problem). Add ``data-confirm`` to
  ask first. A page's own fetch calls use ``post(url, formData)`` from ``app.js``.
* Text from invoices and files is escaped (Jinja autoescape). Never use ``|safe`` or ``Markup`` on it; the ``ui``
  helpers (``{{ ui.pill(...) }}``, ``ui.status``, ``ui.step``, ``ui.empty_note``...) escape what they are given.
* Work that takes more than a few seconds runs as a job: ``jobs(request).get("name").start(work)``, and the page
  polls its status (``pollJob`` in app.js). Today that is reading invoices, the settings doctor and the Azure test;
  backups, exports and the other downloads run inside the request.
* Tests: ``TestClient(create_app(settings, store), base_url="http://localhost")`` (the Host check refuses the
  default "testserver"), and ``headers={"X-AP-Coder": "1"}`` on POSTs. See tests/test_web_*.py.
"""
