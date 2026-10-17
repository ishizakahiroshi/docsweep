# Public structural gate

`structural-dual.yml` runs the unchanged legacy scanner and exactly
`doxguard@0.2.0` on the same checkout. A scanner failure does not prevent the
other scan, and every failed result fails the job. Synthetic scanner controls
run even when the tracked scan fails. Existing CI and the legacy workflow/hook
remain; the legacy workflow now uses the metadata-filtering wrapper for safe output.
Only the owner may remove legacy after real dual CI acceptance.

The explicit `doxguard.ci.json` has no watchlist sources, no path exemptions and
no default exemptions. All four default structural rules are enabled; strict
mode rejects bare directives and coverage skips. This public gate does not
replace private local watchlists. The wrapper removes watchlist/config override
environment variables for children, rejects nonzero watchlist counts and
baseline suppression, captures both output streams and logs numeric summaries plus safe file, line,
kind and detector metadata. Matched strings stay in memory and are removed
from metadata too, including when a filename itself contains a matched value. It does not print matched values, source lines or raw child errors.

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
Failures report fixed reason categories without raw diagnostics. Both workflows
check out the exact pull-request head and require git HEAD to equal the expected
SHA. Windows invokes npm through a constrained cmd.exe command line; shell
metacharacters are rejected. Child environment filtering is case-insensitive.
Investigate availability/configuration failures without exposing scan input values.

Linux CI additionally performs a bounded three-minute public-package preflight,
after both tracked scanner results have been emitted. It installs only exact
0.2.0 into a temporary prefix/cache with scripts disabled, checks root/platform
identity, hashes the payload and separates direct-native, root-launcher and npm
exec version probes. Only fixed categories, exact versions and hashes are logged;
cleanup failures fail closed without path-bearing errors. Outside CI this
additional installation is not requested. Existing scans remain the acceptance
requirement; diagnostics cannot turn missing or failed native execution green.

The observed Linux npm exec command-resolution failure is bypassed only by
launching the same exact installed public root launcher with Node and an
absolute path. The temporary installation has scripts disabled and root and
platform identities fixed at0.2.0; no system/package PATH command is selected.
Both scanners still receive the same checkout and flags. npm exec failure remains
a diagnostic field; actual native scans, version, coverage and controls must pass.
