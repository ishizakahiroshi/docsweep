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

Linux x64 CI installs exact 0.2.0 once per Node process into an isolated
prefix/cache with scripts disabled. Before every native invocation it verifies
root name/version/type/bin, the exact selected optional dependency, platform
name/version/os/cpu, launcher resolution to that platform manifest, executable
native payload, and canonical SHA256 of both launcher and payload. The accepted
Linux x64 payload hash is 5458f08f85753cf6d33ad869063af16ca8f55a800bb9d17a78c4b6adc0651279
(owner release acceptance); launcher hash is 5374a0b83149b3ca78ad897c2475c6dbbbb44b55c4c6671f762c4e2bd8312b28
from the public v0.2.0 bin/doxguard.js. Other CI architectures fail closed until
their canonical identities are independently accepted; no hash is guessed.

The bounded three-minute preflight uses the actual scan runtime, after both
tracked results, and checks direct-native and root-launcher versions. It does
not install a second diagnostic runtime. The separate controls process verifies
its own installation against the same canonical hashes before every invocation.
Fixed runtime_use/identity/hash receipts link version, tracked scan and all
controls to the accepted payload without printing paths or raw errors. Cleanup
failure fails the process. Unit tests use synthetic files and no downloads.

The observed npm exec resolution failure is retained in historical receipts.
The Linux CI route uses the verified absolute public launcher through Node;
no npm exec or system PATH scanner is used. Actual native scans, version,
coverage and controls must still pass, and legacy always runs independently.
