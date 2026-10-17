# P-20261018-006 docsweep

Updated: 2026-10-18. Management Issue: https://github.com/ishizakahiroshi/docsweep/issues/1
Public instructions: ishizakahiroshi/docsweep docs/bot/doxguard-ci-20261018/.
Behavior baseline: develop 6f6184d41d06cad3901630d17cd91e345882bb5e.
Implementation branch: dots/doxguard-ci-P-20261018-006. PR base: develop.

| Stage | Owner | Evidence / remaining |
|---|---|---|
| Source corrections and dual CI | dots | Acknowledged; Draft PR https://github.com/ishizakahiroshi/docsweep/pull/2 submitted. Latest head 689c61873a903fe4e95eccf7d8aba89b5f4aface includes safe file/line/reason diagnostics and exact source binding |
| Independent review | separate reviewer and local coordinator | Exact 689c618 under review; earlier local candidate comparison had zero hits and zero native skips, and changed Python ASTs were unchanged outside docstrings |
| Package release | local coordinator | GitHub v0.2.0 archives published from 281b954; npm job failed in run 39058075004. Authentication recovery and fresh install acceptance pending |
| Real dual CI and Windows acceptance | local coordinator | Latest legacy run 39059525708 and Python CI 39059525482 succeeded. Dual run 39059525579 failed before native acceptance; package and final dual evidence remain pending |
| Legacy removal and rollout | local coordinator | Only after preceding gates pass |

Issue comments are the source of truth for dispatch and acceptance status.
Instruction/code/review SHAs and board commits are distinct evidence states.
Initial published instruction/diff base: 0b46d0674a2e7f62075df327da4e2b10e9cc258e.
