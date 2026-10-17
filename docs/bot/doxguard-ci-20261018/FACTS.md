# Verified baseline facts

Updated: 2026-10-18. All source references refer to develop
6f6184d41d06cad3901630d17cd91e345882bb5e unless stated otherwise.

The owner compared the same unchanged tracked snapshot with the legacy scanner
and a pinned current native doxguard candidate, both without private watchlists.
Legacy: exit 0, 348 files scanned, zero hits; native strict: exit 1, 350 scanned,
39 hits on 37 lines, zero coverage skips. This is local preparation, not GitHub
CI evidence. Current published doxguard is 0.1.0; 0.2.0 release is in parallel.

Differences by source inspection: 28 bare legacy allow directives rejected by
strict mode, 4 newly recognized doubled-backslash paths, 5 reserved example
emails missing from new config, 1 existing public author email, 1 legacy
whole-file workflow exemption. These are candidates for explicit source review,
not an assertion that every match is a false positive or a leak.

| Source | Lines at baseline |
|---|---|
| .github/workflows/openwiki-update.yml | 12 |
| .github/workflows/secrets-scan.yml | 7 |
| README.en.md | 61,90,91,92,114,184 |
| README.md | 57,86,87,88,109,178,648,651 |
| docs/D-skill-update-proposal.md | 8,19 |
| docsweep/config.py | 216 |
| docsweep/scan.py | 719,771 (two matches at 771) |
| pyproject.toml | 12 |
| templates/.docsweep.yaml | 39,40,41 |
| tests/test_audit_2026_07_21_followup.py | 38,55,69 |
| tests/test_db_index.py | 692 (two matches) |
| tests/test_extras.py | 500,652,656 |
| tests/test_i18n_labels.py | 182 |
| tests/test_move_cli.py | 24 |
| tests/test_move_links.py | 331 |
| tests/test_release_tracking.py | 36 |
| tests/test_undo_batches.py | 226 |

Legacy marker parsing is in scripts/secrets-scan.mjs. A legacy bare marker
allows its whole line; doxguard --strict refuses bare markers. Keep existing
legacy comments working while adding separate scoped doxguard markers.
Avoid invalid double hyphens inside HTML comments. Windows doxguard matches
the prefix only, and matches doubled slashes too. Scoped token matching is
one-directional: the matched substring must contain the token. Read current
doxguard public src/patterns.rs and src/scan.rs before choosing tokens.

Legacy JSON contains matched values. Never upload or print that raw output.
Persist only counts, filenames, line numbers and detector names after parsing.
The public config is not a substitute for private local watchlists. Keep local
hooks protected during this dual-CI preparation.
