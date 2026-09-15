# Release tracking migration guide

This guide describes how to opt an existing workspace into release-aware archive
without changing the product default for projects that have no release setting.

## One repository

Add the following to the repository's `.docsweep.yaml`:

```yaml
archive_dir: docs/local/archive
archive_partition: release
release_tracking:
  mode: enabled
  tag_pattern: semver
  archive_group_by: minor
  default_target: v0.9.x
```

Use `target_release` for a planned series or exact planned version. It does not
require a tag to exist. Use `release close <tag>` only after the exact local Git
tag exists; the command records that exact value as `released_in` and calculates
the archive bucket from the configured grouping.

```bash
python -m docsweep find --missing-target-release --json
python -m docsweep target-release set --path docs/local/plan_existing.md --to v0.9.x
python -m docsweep release close v0.9.1 --dry-run --json
python -m docsweep release close v0.9.1 --json
```

Changing configuration or frontmatter alone never moves a document. Existing
flat archive layouts remain valid until an explicit archive or release-close
operation is requested.

For newly generated documents, the target resolution order is explicit
`--target-release`, then an enabled project/global
`release_tracking.default_target`. The same value is used for the parent and
all children of `new --split`. A disabled project never auto-adds a target. On
an interactive TTY, `new` asks an unconfigured repository once whether to
enable tracking, persist disabled, or cancel. Enabling collects the default
target, grouping, and archive directory before creating the document. Non-TTY
execution never prompts and retains the legacy unconfigured generation path.
When the new document has an explicit exact target, the wizard keeps that value
separate from the repository default (for example, exact `v0.9.1` and future
default `v0.9.x`).

## Workspace migration

Start with a metadata-only inventory. The manifest contains repository paths,
counts, selected configuration values, proposed operations, preconditions, and
diagnostic reasons. It does not contain document bodies, secrets, Git diffs, or
exact frontmatter snapshots. A configured `work_dir` is the migration queue;
documents outside that queue are diagnostics and do not receive bulk actions.

```bash
python -m docsweep workspace migrate-release-tracking \
  --root <workspace-root> --review --manifest release-migration.json
```

On an interactive TTY, `--review` displays inventory first, then asks once for each repository with no
release-tracking mode: enable the effective default/group/archive settings, or
skip and persist `release_tracking.mode: disabled`. Enabling may add the chosen
`target_release` to eligible queue documents, but it never archives files.
It displays the final config and document actions and asks once more before
applying. Choose `q` or close stdin to cancel; cancellation does not write config,
documents, a manifest, or a journal. Disabled repositories are not asked again.

With `--json`, `--auto`, CI, or non-TTY stdin, the command is fully
non-interactive and returns `needs_review` / `needs_target` diagnostics instead
of asking questions. Review the `repositories` and `excluded` entries and add
or change per-document targets in the manifest when a repository has no
trustworthy default. Never invent a `released_in` patch tag from a minor
archive directory. The inventory's `ignored_tag_count` reports Git tags that do
not match the configured pattern; they are diagnostics, not release candidates.

Apply the reviewed manifest explicitly:

```bash
python -m docsweep workspace migrate-release-tracking \
  --root <workspace-root> --apply-manifest release-migration.json
```

Applying is repository-scoped. A repository preflight failure is recorded in
the migration journal and does not roll back successful repositories. Repeating
the same manifest uses the journal to skip repositories already applied. The
default exclusion list covers generated worktrees, imported source trees,
vendor/cache directories, and common dependency directories; add project-
specific patterns with repeated `--exclude` flags.

The manifest fingerprint, project-config precondition, and per-document
mtime/content preconditions are checked before any write in a repository.
Stale or changed input therefore produces `needs_review` without a partial
repository update. The journal keeps cumulative applied state separate from
individual attempts, so a later retry cannot silently reapply an already
completed repository.

## Disabled repositories and rollback

Set `release_tracking.mode: disabled` in a repository to override an enabled
global policy. The workspace migrator leaves disabled repositories and their
documents unchanged. Migration writes use atomic replacement and retain the
pre-apply content in memory long enough to restore that repository if a later
operation in the same repository fails.

The migration journal is stored below the user's `.docsweep` data directory by
default. It contains the manifest and status metadata only. Remove or archive
the journal according to the host's normal local-data policy after the migration
has been independently verified.
