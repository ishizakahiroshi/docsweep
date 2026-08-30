"""Shared record views keep sensitive fields masked consistently across commands.

The masking rules live here so every summary view applies the same policy.
"""

from __future__ import annotations

from pathlib import Path

from .models import FileRecord


def masked_title(rec: FileRecord) -> str | None:
    """Return a record title without exposing sensitive content."""
    return "[sensitive]" if rec.sensitive else rec.title


def masked_summary(rec: FileRecord) -> str | None:
    """Return a record summary without exposing sensitive content."""
    return "[sensitive]" if rec.sensitive else rec.summary


def short_record(rec: FileRecord) -> dict[str, object]:
    """Return the common 14-field summary used by brief and cross."""
    return {
        "path": rec.path,
        "rel": Path(rec.path).name,
        "project": rec.project,
        "type": rec.type,
        "state": rec.state,
        "state_label": rec.state_label,
        "title": masked_title(rec),
        "summary": masked_summary(rec),
        "sensitive": rec.sensitive,
        "age_days": rec.age_days,
        "due": rec.due,
        "owner": rec.owner,
        "flags": list(rec.flags or []),
        "tags": list(rec.tags or []),
    }


def is_open_state(rec: FileRecord) -> bool:
    """Return whether a record is in one of the non-terminal states."""
    return rec.state in {"in-progress", "planned", "watching", "pending"}
