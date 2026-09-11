---
type: integration
title: Web UI (FastAPI Board)
description: The local FastAPI + htmx kanban board — app wiring, token-based auth, path-scope enforcement, and the strict CSP/sanitization invariants that keep it safe to preview untrusted Markdown.
tags: [web-ui, fastapi, security, csp]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-f2f492d70f01fae2962b99b3
    resource: repo://docsweep/server/app.py
  - id: openwiki-source-cb1849674637ff7ab009fd5f
    resource: repo://docsweep/server/sanitize.py
  - id: openwiki-source-b9c04006bc7a95fc4e35c41e
    resource: repo://docsweep/server/security.py
  - id: openwiki-source-d1a8ddf6b4d5b2a9896ef3bb
    resource: repo://tests/test_offline_assets.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# Web UI (FastAPI Board)

`docsweep serve` starts a local FastAPI application (`docsweep/server/app.py`'s
`create_app()`) that renders docsweep's kanban-style board and several
supporting pages (brief, cross, capture, graph, resurrect) via Jinja2
templates and htmx, backed by the same `Config`/`run_scan`/`apply_action`
core described in [Architecture Overview](/openwiki/architecture/overview.md).
The module docstring frames the UI itself as "preview-first, opening the
default app second" — the board is the primary surface, and "open in default
app" (`/api/open`) is a secondary convenience.

## Binding and authentication: local-only, token-gated

`docsweep/server/security.py`'s module docstring states the security posture
directly: the server binds to `127.0.0.1` only, every mutating or
content-revealing request requires a token, and anything the server lets a
client view/preview/open must resolve to a path under a configured scan root.
`check_token()` accepts the token from an `HttpOnly` cookie, an
`X-Docsweep-Token` header, or a query parameter — checked with
`secrets.compare_digest` to avoid timing side-channels — and a middleware in
`app.py` (`_token_cookie_exchange`) exchanges a valid first-visit `?token=`
query parameter for a `SameSite=Strict` `HttpOnly` cookie and 302-redirects
to strip the token out of the URL (except for `POST` requests, which are
answered in place, since redirecting a `POST` would silently downgrade many
clients to `GET`). A `--read-only` server additionally runs an
`_read_only_guard` middleware that 403s any `POST`/`PUT`/`PATCH`/`DELETE`
except `/api/shutdown`, so a demo instance cannot be made to mutate files no
matter what token is presented.

## Path-scope enforcement: only `.md` files under a scan root

`resolve_under_roots()` is the single choke point every file-touching route
uses to decide whether a client-supplied path is allowed: it resolves the
path with `os.path.realpath` (defeating `..` traversal and symlink tricks),
rejects anything whose suffix isn't `.md`, and accepts it only if it falls
under one of the configured roots — compared with `os.path.normcase` so
Windows's case-insensitive filesystem can't be used to slip past the
boundary. Preview, apply, open-in-default-app, and reveal-in-file-manager
routes all reject with `403 path outside scan roots` for anything that
doesn't resolve this way, rather than trusting a client-supplied absolute
path directly. `/api/config/roots`'s `add` operation adds a further,
separate guard (`_is_protected_root_target`) specifically to refuse adding
the OS home directory, its immediate parent, or a filesystem/drive root as a
scan root — since that would recursively expose far more of the filesystem
than a normal project root — and refuses `add` entirely unless the server was
started with `--allow-root-mutation`.

## Rendering untrusted Markdown safely: sanitize, then lock down with CSP

Because docsweep may scan third-party clones or otherwise-untrusted `.md`
content, `_render_markdown()` never trusts `markdown.markdown()`'s raw HTML
output directly — Python-Markdown passes through raw `<script>` tags,
`onerror=` attributes, and `javascript:` URLs unchanged. Every rendered
preview is piped through `docsweep/server/sanitize.py`'s `sanitize_html()`,
which is deliberately implemented on top of `nh3` (a Rust/ammonia binding,
a hard `web` extra dependency rather than an optional one) with an explicit
allowlist of tags and per-tag attributes; `on*` event-handler attributes and
inline `style` are never in the allowed-attribute list for any tag, and only
`http`/`https`/`mailto` URL schemes are accepted for `href`/`src` — closing
off `javascript:`/`data:` payloads. The module docstring is explicit that
exceptions from `nh3` are allowed to propagate rather than being caught and
silently falling back to unsanitized output.

That sanitizer is treated as one layer, not the only one: a
`_security_headers` middleware sets a `Content-Security-Policy` of
`default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; ...`
on every response, plus `Referrer-Policy: no-referrer` (so a token-bearing
URL in a rendered document's outbound link never leaks via the `Referer`
header) and `X-Content-Type-Options: nosniff`. `script-src 'self'` with no
`'unsafe-inline'` means that even a hypothetical script payload that slipped
past `nh3` still cannot execute in the browser — this is explicitly called
out in the app's own comment as defense in depth. `style-src` does allow
`'unsafe-inline'`, a deliberate narrower exception to let the board's
health-bar width be set inline, judged lower-risk than allowing inline
script.

### The CSP invariant is enforced on templates, not just documented

Because a browser silently drops any inline `<script>` or `on*=` handler
under this CSP — with **no visible error** — an accidental inline script in a
Jinja2 template doesn't fail loudly, it just makes part of the page quietly
stop working. `tests/test_offline_assets.py` turns this into a mechanical,
CI-enforced invariant rather than a convention someone has to remember:
`test_templates_have_no_inline_script` scans every template under
`docsweep/server/templates/` for a non-empty `<script>` body lacking a `src=`
attribute or a `type="application/json"` attribute, and for any
`onclick=`/`onchange=`/`onsubmit=`/etc. attribute, failing the build if
either is found. Its own docstring cites a concrete incident this caught
retroactively: just before the v0.4.0 release, the `graph`, `brief`, and
`capture` pages were found to be silently broken this way (graph's canvas
never rendered even after cytoscape.js was vendored; capture's form was
completely inert) and were fixed by moving logic into external `/static/*.js`
files that read `data-*` attributes and non-executed `<script
type="application/json">` "data island" tags instead. A sibling test,
`test_templates_have_no_external_script_or_stylesheet`, separately enforces
that the tool works fully offline: no template may load a script or
stylesheet from an external `http(s)://` URL — this test's own docstring
records that the `graph` page fetched `cytoscape.js` from `unpkg` until
v0.4.0, when it was vendored into `docsweep/server/static/cytoscape.min.js`
instead.

## Route surface

`create_app()` wires a handful of routes directly (`/`, `/preview`,
`/api/apply`, `/api/open`, `/api/reveal`, `/api/sweep`,
`/api/config/roots`, `/api/shutdown`, `/api/inject`, `/api/eject`) and then
mounts seven `APIRouter`s from `docsweep/server/routes/`, each pairing an
HTML page with its own JSON API endpoints:

- `board.py` — the main kanban board (`/board`, `/board/fragment`), its card
  detail/context/raw endpoints, and picker partials (label/due/change
  pickers, settings panel).
- `cards.py` — the board's mutation surface: status/due/content/frontmatter
  edits, archive (single and bulk), claim, undo, snooze/pin.
- `brief.py`, `cross.py`, `graph.py`, `resurrect.py`, `capture.py` — one page
  plus API endpoint each for the `brief`/`cross`/`graph`/`resurrect`/`capture`
  CLI features covered elsewhere, giving each a browser-native counterpart to
  its CLI/MCP form.

`app.py`'s inline comment notes that an older `/`, `/list`, `/fragment`
"dashboard" stack was retired and physically removed once the kanban board
consolidated all of that functionality — `/` is now purely a redirect to
`/board`.

## Related pages

- [Architecture Overview](/openwiki/architecture/overview.md) — the `run_scan`/`apply_action` functions the board's routes call into.
- [State Model and Document Lifecycle](/openwiki/architecture/state-model.md) — the snooze/pin per-viewer overrides (`docsweep/state.py`) the board's cards read and write.
- [Testing and Development Workflow](/openwiki/testing/development-workflow.md) — where `test_offline_assets.py` sits among the rest of the test suite.
