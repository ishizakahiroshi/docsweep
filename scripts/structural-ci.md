# Public structural gate

`structural-dual.yml` runs the unchanged legacy scanner and exactly
`doxguard@0.2.0` on the same checkout. A scanner failure does not prevent the
other scan, and every failed result fails the job. Synthetic scanner controls
run even when the tracked scan fails. Existing CI and the legacy workflow/hook
remain; the legacy workflow now uses the count-only wrapper for safe output.
Only the owner may remove legacy after real dual CI acceptance.

The explicit `doxguard.ci.json` has no watchlist sources, no path exemptions and
no default exemptions. All four default structural rules are enabled; strict
mode rejects bare directives and coverage skips. This public gate does not
replace private local watchlists. The wrapper removes watchlist/config override
environment variables for children, rejects nonzero watchlist counts and
baseline suppression, captures both output streams and logs only numeric
summaries. It does not print matched values, source lines or raw child errors.

Allowances:
- The single exact author email is already public in `pyproject.toml` at the
  behavior baseline; its metadata is preserved.
- `example.com` is the reserved documentation domain used by the source.
- `example.invalid` is the reserved fixture domain used by five Git identity
  tests. Neither allowance permits arbitrary email domains.
- Scoped inline path allowances preserve intentional Windows / junction /
  root-configuration fixtures and per-user documentation. A token appears
  inside the actual matched prefix; multi-match lines have separate tokens.
  Legacy bare comments remain compatible while the native strict scanner uses
  the additional scoped tokens. No tree-wide fixture exemption is introduced.
- The four escaped JSON command examples now use a generic Tools location;
  the surrounding instructions require the actual interpreter's absolute path.

Commands:
- `node --test scripts/structural-ci.test.mjs` tests fail-closed orchestration,
  output redaction, both-scanners-on-failure, isolation and config invariants.
- `node scripts/structural-ci.mjs --legacy-only` runs the legacy gate safely.
- `node scripts/structural-ci.mjs` runs the full dual gate.
- `node scripts/structural-controls.mjs` runs isolated real-scanner controls.
  Controls include positive matches, scoped/bare/wrong/short/reversed tokens,
  escaped Windows paths, multiple hits, reserved examples and a coverage skip.

The last two commands resolve the exact npm package and are intended for CI;
no dependency download is required for the orchestration unit tests. A missing
0.2.0 package fails the gate and waits for the release coordinator. Do not
unpin, skip the native job, soften strict mode or print raw npm/scanner output.
Failure summaries intentionally omit raw diagnostics; investigate a package
availability/configuration problem without exposing scan input values.
