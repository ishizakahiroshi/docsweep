---
type: integration
title: OKF (Open Knowledge Format) Compatibility
description: How docsweep's own frontmatter fields map onto OKF v0.2's status/type model, the version-profile mechanism behind okf-check and export --okf, and the Bundle export format.
tags: [okf, export, interoperability, frontmatter]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-10a7b3e94ed0f0bd7973667c
    resource: repo://docs/okf-export-format.md
  - id: openwiki-source-b1d5efa48f45504046264862
    resource: repo://docs/okf-mapping.md
  - id: openwiki-source-3ded912d826ae4768af44c33
    resource: repo://docsweep/detect.py
  - id: openwiki-source-155fd3d446240c35cf82dec4
    resource: repo://docsweep/okf_check.py
  - id: openwiki-source-6c338a34dd59704edb82bf27
    resource: repo://docsweep/okf.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# OKF (Open Knowledge Format) Compatibility

docsweep aligns its Markdown frontmatter with [OKF v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md),
a vendor-neutral, Markdown-centric knowledge format, while keeping docsweep's
own work-tracking concerns as clearly-labeled producer extensions rather than
overloading OKF's own fields.

## Two separate axes: OKF `status` vs. docsweep `docsweep_state`

`docs/okf-mapping.md` states the core design decision: OKF's `status` field is
a **document lifecycle** axis with exactly three v0.2 values —
`draft`/`stable`/`deprecated` — and is not the same thing as *docsweep's own
work state* (`planned`/`in-progress`/`watching`/`done`/`discarded`/`pending`,
covered in [State Model and Document Lifecycle](/openwiki/architecture/state-model.md)).
docsweep keeps its work state in a separate `docsweep_state` frontmatter key
specifically so the two axes never collide. `docsweep/detect.py`'s
`_detect_frontmatter()` implements the disambiguation rule this mapping
describes: a legacy top-level `status:` value is still accepted as a docsweep
work state for backward compatibility, but *only* when that value is not one
of the profile's own OKF lifecycle values (checked via `is_okf_lifecycle_status`);
if it is a lifecycle value, detection falls through to H1/filename instead of
misreading `status: draft` as a work state.

`type` is treated the OKF way — an open, non-enumerable field. `okf-check`
does not reject unrecognized `type` values; only `plan`/`bugfix`/`pending` are
"standard" enough for docsweep's own archive/due/triage automation to
interpret their required sections and states, while `manual`/`reference`/
`setup`/anything else passes OKF conformance checks untouched and simply falls
outside docsweep's automated work-state and due-date management.

Other producer-extension fields docsweep adds — `tags`, `owner` (written by
`docsweep claim`), `review_status`, `related`, `docsweep_parent` (a
docsweep-specific *directional* parent reference, deliberately kept separate
from the generic many-to-many `related` list), and `last_reviewed` (consumed
by `docsweep stale`) — are all preserved verbatim by any of docsweep's own
read paths, alongside unknown fields it does not itself interpret (`sources`,
`generated`, `verified`, `stale_after`, etc.), which are carried through
unmodified rather than stripped.

## Version profiles: the enforceable subset lives in JSON, not Python

`docsweep/okf.py`'s module docstring explains a deliberate architectural
choice: the OKF spec itself is prose, but the small part of it docsweep can
mechanically enforce is kept as a versioned, machine-readable `OkfProfile`
loaded from `docsweep/okf_profiles/<version>.json` (currently only `0.2.json`
ships), rather than hardcoded in Python — so that a new OKF spec version can
be adopted as a data update, without a code change, as long as its rules fit
the same profile schema. A profile encodes: `required_frontmatter` (must
include `type`), `reserved_files` (e.g. `index.md`/`log.md` semantics),
policies for `unknown_types`/`unknown_fields`/`broken_links`/
`missing_optional_fields` (each `allow`/`warning`/`error`), the set of
`lifecycle_values`, a `lifecycle_default`, and a `docsweep_status_map` mapping
each docsweep work-state key to one lifecycle value — every value in that map
is validated at load time to actually be one of the profile's own
`lifecycle_values`, and `_decode_profile()` otherwise rejects a malformed
profile outright (missing `type` in `required_frontmatter`, non-unique
`lifecycle_values`, a default not present in the lifecycle set, etc.) rather
than partially trusting it.

`load_okf_profile()` is offline by default: passing no `source` (or
`"bundled"`) always reads the packaged JSON with no network access. A caller
may explicitly pass a local file path or an `http(s)://` URL instead; a remote
profile fetched over plain HTTP triggers an explicit stderr tamper warning,
and fetching without a `sha256` pin likewise warns that a redefinition at the
same URL wouldn't be detected. When a `sha256` *is* supplied, the fetched
bytes are hashed and compared before the profile is accepted, and (if a cache
directory is given) a previously-verified profile matching that digest is
reused from a local cache rather than re-fetched. There is no silent fallback
to a different profile version on a failed fetch or hash mismatch — the call
raises `OkfProfileError` instead.

## `okf-check`: read-only conformance checking

`docsweep/okf_check.py` implements `docsweep okf-check <bundle>` as a
deliberately narrow, read-only checker: per its module docstring, it enforces
only what OKF makes *normative*, and treats unknown types, producer
extensions, optional-field families, and broken cross-links as warnings (or as
accepted, depending on the active profile's policy) rather than hard failures
that would make an otherwise-usable Bundle unreadable. `OkfCheckResult.ok` is
`True` whenever there are zero `error`-severity `OkfIssue`s, independent of
how many `warning`-severity issues exist — the checker is built to distinguish
"this Bundle violates the spec" from "this Bundle has things docsweep doesn't
recognize."

## `export --okf`: turning managed documents into a portable Bundle

`docsweep export --okf` (`docsweep/export.py`) is the reverse direction: a
read-only export of docsweep's own managed documents into a self-contained OKF
Bundle zip, meant — per `docs/okf-export-format.md`'s framing — to
demonstrate that a project's Markdown doesn't rot if it's ever moved off
docsweep entirely. A Bundle's layout mirrors project structure under Bundle
root (`<project>/docs/local/plan_xxx.md`, etc.), plus:

- A root `index.md` — an OKF reserved file carrying only an `okf_version`
  frontmatter field and Markdown links to every included concept.
- `okf-manifest.json` — explicitly **not** part of the OKF standard itself,
  but a docsweep-specific manifest recording the profile actually used
  (`spec_version`, `source`, `sha256`), the full
  `docsweep_state → OKF status` mapping applied
  (`status_vocabulary`), and a per-file record of `type`/`status`/
  `docsweep_state`/title/tags/owner/`review_status`/`related`/
  `docsweep_parent`/`last_reviewed`, with a `normalized: true` flag on any
  file whose original frontmatter was legacy or incomplete and had to be
  split/completed inside the Bundle copy — while leaving the source file on
  disk untouched.
- An optional `_archive/` top-level directory, included only when
  `--include-archive` is passed.

A name collision between two source files that would otherwise land at the
same Bundle path is resolved by appending a `__1`/`__2` suffix rather than
overwriting one of them. As with `okf-check`, this mapping — `done → stable`,
`discarded → deprecated`, everything else → `draft` in the bundled 0.2 profile
— is explicitly documented as docsweep's own operational approximation of
lifecycle, not a claim that, say, `done` and OKF `stable` mean the same thing.

## Bulk migration is dry-run first, non-destructive

`docsweep migrate-frontmatter` converts legacy single-field `status: <state>`
frontmatter into the two-axis form (`status: draft` + `docsweep_state:
<state>`) for files that predate this split. Per `docs/okf-mapping.md`, this
is always run `--dry-run` before `--apply`; the original H1, body text, and
any unknown existing fields are preserved through the conversion.

## Related pages

- [State Model and Document Lifecycle](/openwiki/architecture/state-model.md) — the docsweep-native work-state axis this page's `docsweep_state` extension carries.
- [CLI Command Surface](/openwiki/workflows/cli-reference.md) — where `export`, `okf-check`, `okf-profiles`, and `migrate-frontmatter` sit among the rest of the CLI.
