# Public structural gate

`structural.yml` runs exactly `doxguard@0.2.0` on the checked-out PR/push head.
Synthetic scanner controls run even when the tracked scan fails. Python CI,
pre-push pytest and the independent omitnix hook remain separate gates.
The accepted dual-run pilot is complete; the obsolete scanner and its workflow
have been removed.

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
  The native strict scanner requires scoped tokens. No tree-wide fixture
  exemption is introduced.
- The four escaped JSON command examples now use a generic Tools location;
  the surrounding instructions require the actual interpreter's absolute path.

Commands:
- `node --test scripts/structural-ci.test.mjs scripts/structural-hook.test.mjs`
  tests fail-closed native orchestration, synthetic installed hook commands,
  output redaction, isolation and config invariants without downloads.
- `node scripts/structural-ci.mjs` runs the native structural gate.
- `node scripts/structural-controls.mjs` runs isolated real-scanner controls.
  Controls include positive matches, scoped/bare/wrong/short/reversed tokens,
  escaped Windows paths, multiple hits, reserved examples and a coverage skip.

In Linux CI the last two commands provision the verified exact npm package.
Outside CI they require an installed doxguard 0.2.0; install once with
`npm install --global doxguard@0.2.0`. No commit-time download occurs.
Do not unpin, skip the native job, soften strict mode or print raw npm/scanner output.
Failures report fixed reason categories without raw diagnostics. The workflow
checks out the exact pull-request head and requires git HEAD to equal the expected
SHA. Windows invokes the installed scanner through a constrained cmd.exe command line; shell
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

The bounded three-minute preflight uses the actual scan runtime, after the
tracked result, and checks direct-native and root-launcher versions. It does
not install a second diagnostic runtime. The separate controls process verifies
its own installation against the same canonical hashes before every invocation.
Fixed runtime_use/identity/hash receipts link version, tracked scan and all
controls to the accepted payload without printing paths or raw errors. Cleanup
failure fails the process. Unit tests use synthetic files and no downloads.

The observed npm exec resolution failure is retained in historical receipts.
The Linux CI route uses the verified absolute public launcher through Node;
no npm exec or system PATH scanner is used in CI. Actual native scans, version,
coverage and all 15 detection controls plus the coverage-skip control must pass.

The tracked `.githooks/pre-commit` requires installed exact 0.2.0, preserves
`DOXGUARD_CONFIG` as one argument when supplied, otherwise uses the public
`doxguard.ci.json`, and runs `scan --staged --block --strict`. Missing or wrong
versions fail closed with the installation command. Scan failure stops the hook;
success continues to the unchanged omitnix freshness check. Git-private managed
hooks/config/watchlists are maintained separately by their owner.
