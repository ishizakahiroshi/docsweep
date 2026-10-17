# P-20261018-006 docsweep: prepare strict doxguard CI migration

Updated: 2026-10-18. Issue: pending publication by owner.

Read CLAUDE.md, AGENTS.md, FACTS.md, REVIEW.md and PROGRESS.md first.
Repository: ishizakahiroshi/docsweep (public). Behavior baseline and PR base:
develop at 6f6184d41d06cad3901630d17cd91e345882bb5e. The owner publishes
instructions on wt/doxguard-ci-20261018. Create your implementation branch
dots/doxguard-ci-P-20261018-006 from the final instruction commit and open a
Draft PR into develop. The first instruction commit is the implementation
review diff base; it is distinct from the behavior baseline above.

## Objective

Prepare a safe transition from scripts/secrets-scan.mjs to doxguard 0.2.0.
Retain the old script and hook invocation until the owner observes a successful
real dual CI run with the published package. The owner handles release, Windows
acceptance, merge and final legacy removal; prepare the dual workflow now.

1. Review the 39 structural differences listed in FACTS. Replace unnecessary
   machine-specific examples with portable examples, or add narrow scoped
   `doxguard: allow TOKEN` directives for intentional synthetic examples. Strict
   tokens must be at least four characters and must occur inside the actual
   matched substring. The Windows matcher matches only the drive/Users or
   drive/dev prefix, not the rest of the path. Do not exempt entire source,
   test, documentation or workflow trees. Do not disable structural rules or
   strict mode. Preserve old scanner compatibility while it runs alongside.
2. Add a checked-in structural CI config with empty watchlists and
   requireWatchlists false. Allow only verified public author metadata and
   reserved example mail domains actually needed by this repository. Explain
   each allowance. Do not transfer local/private lists or invent author data.
3. Add a dedicated dual workflow (or a separate job in the existing workflow)
   for PRs into main/develop and pushes to main/develop. Use read-only contents
   permission, explicit config, Node 22 or newer, and exact doxguard@0.2.0.
   Run both scanners on the same checked-out SHA even if the other fails; fail
   the gate if either fails. Retain all existing gates. No continue-on-error
   outcome may make a failed scan green. Count hits and coverage skips without
   emitting matched values or source lines. Fail if watchlist count is nonzero.
   Report version, source SHA, commands and results; package availability is
   owned by the release coordinator, not a reason to weaken or unpin the gate.
4. Add focused synthetic positive/negative controls proving both scanners block
   unexpected data and only intended examples are allowed. Keep application
   behavior and Python syntax unchanged for comment-only edits. Preserve public
   package metadata. No new dependencies. Use existing CI for full acceptance.

## Delivery and coordination

Acknowledgement, progress, questions, stop and submission must start
`#P-20261018-006 docsweep:` and remain in the same request thread. At every stage
also comment on the dedicated management Issue. Acknowledge the instruction
commit, collisions, repository access, available tools and execution limits.
The owner updates PROGRESS; do not edit it. Report exact implementation and
reviewed SHAs, commands with exit codes, failed/skipped checks, and rollback.
An independent implementer/reviewer split is preferred when capacity permits.
If baseline facts differ from code, treat code as authoritative and report it.

Allowed: source/comment/config/workflow/test edits above, commits and pushes
only to the specified dots branch, Draft PR creation/update into develop,
management Issue comments, fixes after review, existing CI execution. Non-code
reports go to Drive bot/blob/2026/10/P-20261018-006/v1/ and all its subfolders;
folder creation is allowed. Read back all saved reports and write manifest.json
last with path/bytes/SHA256; report those details to Issue and Slack. Resubmission
uses v2 or later. Do not put report files into GitHub commits.

Forbidden: merge, direct main/develop push, release/publish/deploy, private
watchlists/configuration, credentials, dependency installation or new services,
global settings, editing unrelated repositories, blanket scanner exclusions,
legacy removal before the owner verifies published-package dual CI.

For cancelled code saves check branch head/PR existence first and retry at most
twice if state is unchanged; never force push. Blob/tree/commit creation may be
retried before a ref moves. On a third cancellation report base SHA and place
format-patch in the authorized Drive version. On an explicit review refusal
stop and report action/reason; do not switch methods. For cancelled Drive saves
check same-name/same-bytes existence before at most two retries. Never overwrite
a different file or retry an explicitly refused operation. This limit applies
to the cancelled operation, not new management Issue status comments.
