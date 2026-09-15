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

## Workspace migration

Start with a metadata-only inventory. The manifest contains repository paths,
counts, selected configuration values, proposed operations, and diagnostic
reasons. It does not contain document bodies, secrets, Git diffs, or exact
frontmatter snapshots.

```bash
python -m docsweep workspace migrate-release-tracking \
  --root <workspace-root> --review --manifest release-migration.json
```

Review the `repositories` and `excluded` entries. Add or change per-document
targets in the manifest when a repository has no trustworthy default. Never
invent a `released_in` patch tag from a minor archive directory. The inventory's
`ignored_tag_count` reports Git tags that do not match the configured pattern;
they are diagnostics, not release candidates.

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
