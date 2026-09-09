---
type: operations
title: Testing and Development Workflow
description: How docsweep is developed and verified — the pytest suite's isolation fixtures and dedicated security/audit regressions, the opt-in pre-push hook, and the staged ruff/mypy configuration.
tags: [testing, ci, security, development]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-baf30c604828cfde90a8ab63
    resource: repo://.githooks/pre-push
  - id: openwiki-source-1c7fddf79e87c7d1a1e9341f
    resource: repo://docsweep/security/path.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# Testing and Development Workflow

docsweep's test suite (`tests/`, run with `pytest -q`) is large (75 test
modules) and includes a distinct category of tests that exist specifically to
codify lessons from real incidents — both correctness regressions and
security findings — rather than only exercising new features.

## Isolating tests from the developer's real environment

`tests/conftest.py` defines two `autouse=True` fixtures whose sole purpose is
preventing a test run from silently reading the *developer's own* machine
state instead of the test's isolated `tmp_path`:

- `isolate_index_db` redirects `DOCSWEEP_INDEX_DB` to a path under `tmp_path`
  for every test. Its docstring documents the concrete incident that
  motivated it: because `scan_records()` prefers the SQLite index at
  `~/.docsweep/index.db` when one exists and only falls back to a full scan
  otherwise (see [Architecture Overview](/openwiki/architecture/overview.md)),
  a developer machine that runs `docsweep index-sync` regularly caused tests
  to read that developer's real, full project index instead of the
  temporary workspace a test constructed — a 2026-07-26 measurement found 22
  test failures caused purely by the index file's *existence* (not its
  contents), and all 653 tests passed once the index was moved aside. Because
  CI has no such index file, this class of failure was invisible there and
  showed up only as a persistent, unexplained local red state.
- `isolate_session_log_env` clears `CLAUDE_CODE_SESSION_ID`,
  `CLAUDE_CONFIG_DIR`, `DOCSWEEP_AI_SESSION_LOG`, and `CODEX_HOME` for every
  test, and monkeypatches `session_logs._home()` to point at a nonexistent
  directory under `tmp_path`. Its docstring explains why clearing environment
  variables alone isn't sufficient: several of the provider resolvers in
  [AI Authorship and Execution Provenance](/openwiki/integrations/provenance.md)
  match sessions by the *current working directory* recorded in a session's own
  metadata file under the real home directory, so a developer who has ever run
  one of those AI CLIs from the same `cwd` as a test would otherwise have that
  real session silently picked up by a provenance test's expectations. Only
  `_home` is patched rather than `HOME`/`USERPROFILE` wholesale, because
  redirecting those env vars entirely would also perturb unrelated tooling
  (e.g. `git config` resolution) that the test isn't trying to isolate.

## A dedicated category of tests: audit and incident regressions

Beyond ordinary unit/feature tests, a substantial fraction of `tests/` is
explicitly named after audits and specific incidents rather than after a
feature: `test_audit_c1_security_boundaries.py`,
`test_audit_c1_security_write_boundaries.py`,
`test_audit_c2_document_integrity.py`, `test_audit_c3_windows_cli_export.py`,
`test_audit_c4_dependency_security.py`, `test_audit_c5_index_correctness.py`,
`test_audit_server.py`, `test_audit_security_regressions.py`,
`test_audit_fixes.py`, `test_audit_fixes_2026_07_16.py`,
`test_audit_v0_4_0.py`, and `test_audit_2026_07_21_followup.py`. This
naming convention — carrying the audit/date/context in the filename rather
than folding the assertions into the feature's own test file — keeps a
security or correctness finding traceable back to *why* the check exists,
mirroring the pattern already seen in
[Web UI](/openwiki/integrations/web-ui.md)'s `test_offline_assets.py`, whose
own docstring cites the specific pre-release incident (silently-broken
`graph`/`brief`/`capture` pages under the strict CSP) that made the test
necessary.

## Path-scope enforcement is centralized and shared, not reimplemented per-writer

`docsweep/security/path.py`'s `resolve_writable_md()` is the write-side
counterpart to the web server's `resolve_under_roots()` (covered in
[Web UI](/openwiki/integrations/web-ui.md)) and is explicitly documented as
sharing that same core scope-check rather than reimplementing it: MCP write
tools (`update_status`, `update_due`, `update_content`, `archive_done` —
see [MCP Server and AI Agent Integration](/openwiki/integrations/mcp-server.md))
all funnel through this one function. Its docstring lists three invariants
inherited from prior audit work (cited as "親 plan C6 と plan_v0.1.0 §8 /
state-tag C3 の合成" — the union of constraints from several earlier planning
documents): writes are confined to configured scan roots (checked after
`realpath` resolution), a path containing a literal `..` segment is rejected
outright at the *input* stage (a defense-in-depth measure, since `realpath`
alone would already resolve `..` away — the function instead treats the mere
presence of an escape attempt in the input as itself disqualifying), and only
`.md` files may ever be written this way. When resolution fails,
`PathScopeError`'s message is deliberately generic ("must be a `.md` under a
scan root") rather than listing which roots were checked, so a failed
resolution attempt cannot be used to enumerate the server's configured scan
roots.

## Opt-in pre-push hook: catching CI failures before they're pushed

`.githooks/pre-push` is a POSIX shell hook, enabled per-clone with `git
config core.hooksPath .githooks` (a per-clone git setting, so it must be run
once by each contributor — it is never committed or automatically active). Its
own header comment cites the concrete incident that motivated adding it: a
2026-07-16 v0.3.0 release where pushing without running `pytest` locally first
led to five consecutive CI failures once latent bugs surfaced remotely all at
once. The hook degrades gracefully rather than blocking unrelated work: it
exits `0` (allowing the push) with just a printed notice if no `python`/
`python3` is found at all, and likewise exits `0` with a suggestion to `pip
install -e '.[dev]'` if `pytest` isn't importable — only an actual `pytest -q`
failure blocks the push, and even then `git push --no-verify` remains an
explicit escape hatch for genuine emergencies.

## Staged linting and typing: intentionally incomplete by design

`pyproject.toml`'s `[tool.ruff.lint]` section selects only `E`, `F`, `I`,
`UP`, and `B` rule groups and explicitly ignores several within those
(`E501`, `E402`, `F841`, `B008`, `UP035`, `I001`), each with an inline
rationale rather than a blanket suppression: `E501`'s 100-character line
limit is called out as impractical for a Japanese-first codebase where CJK
characters occupy roughly double the display width of a Latin character;
`E402` is disabled because large dependencies are deliberately
lazily-imported mid-module to reduce startup time; `B008` is kept off because
FastAPI's idiomatic `Query()`/`Body()` defaults are themselves a
framework-sanctioned use of a mutable-looking default; and `I001` (import
ordering) is left for a planned bulk `ruff --fix` pass rather than 31 scattered
hand-fixes. `[tool.mypy]` similarly stops short of `strict` mode, with a
comment noting that strict mode against the existing codebase would produce a
large volume of errors that hasn't yet been worked through, and
`ignore_missing_imports = true` specifically to tolerate optional
dependencies (`mcp`, `sentence_transformers`) that ship without type stubs.
Both configurations are staged, incremental adoptions of stricter tooling
rather than an assertion that the codebase is already fully compliant.
`[tool.pytest.ini_options]`'s `addopts = "-ra --strict-markers
--strict-config"` does hold a hard line, though: an undeclared `@pytest.mark.foo`
or a malformed pytest config fails immediately rather than being silently
accepted, specifically to catch a typo'd marker the moment it's introduced.

## Related pages

- [Web UI (FastAPI Board)](/openwiki/integrations/web-ui.md) — `test_offline_assets.py`, the CSP-and-template-safety test this page's audit-test category generalizes from.
- [MCP Server and AI Agent Integration](/openwiki/integrations/mcp-server.md) — the write tools that route through the same `resolve_writable_md()` path-scope check.
- [AI Authorship and Execution Provenance](/openwiki/integrations/provenance.md) — the session-log resolution logic `isolate_session_log_env` isolates tests from.
