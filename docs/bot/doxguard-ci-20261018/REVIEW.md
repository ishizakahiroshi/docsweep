# Independent review

Review the full diff from the first published instruction commit to the exact
implementation SHA. Verify all 39 positions by intent, not just green counters.
Reject blanket exemptions, strict disablement, newly leaked paths, silent
coverage skips, swallowed exits and loss of legacy coverage. Check config
schema against public doxguard source, shell exit handling, config isolation,
both scanners running on failure, YAML trigger semantics and exact package pin.

Use synthetic values only. Positive controls must fail and negative controls
must pass with zero private watchlist entries. No application behavior should
change for comment-only edits. Compare relevant Python ASTs where possible.
Check marker behavior with escaped Windows paths, POSIX paths and multi-match
lines. Confirm the author email already existed publicly before allowing it.

Report severity, precise location, reproduction and review SHA. Re-review each
new SHA after fixes; do not carry a previous pass over changed source. Reports
belong to the authorized Drive version with final manifest, not GitHub files.
The owner performs a further independent local review before integration.
