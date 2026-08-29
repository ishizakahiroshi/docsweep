"""CLI handlers for ``docsweep provenance``."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from ...config import load_config
from ...provenance_hint import warn_if_unresolved
from ...provenance import (
    AIMetadata,
    ProvenanceError,
    check_document,
    finish_execution,
    initialize_document,
    start_execution,
)
from ...work_queue import find_project_dir


def _context(args: argparse.Namespace) -> tuple[Path, object]:
    project_dir = (
        Path(args.project_dir).resolve()
        if getattr(args, "project_dir", None)
        else find_project_dir(cwd=Path.cwd()).resolve()
    )
    cfg = load_config(
        project_dir=project_dir,
        global_path=Path(args.config) if getattr(args, "config", None) else None,
    )
    return project_dir, cfg


def _path(value: str, project_dir: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    return Path(os.path.abspath(os.path.normpath(os.fspath(candidate))))


def _metadata(args: argparse.Namespace, cfg) -> AIMetadata:
    return AIMetadata.resolve(
        actor_default=cfg.provenance_actor_key,
        agent=getattr(args, "agent", None),
        runtime=getattr(args, "runtime", None),
        provider=getattr(args, "provider", None),
        model_id=getattr(args, "model_id", None),
        model_display=getattr(args, "model_display", None),
        reasoning_profile=getattr(args, "reasoning", None),
        model_source=getattr(args, "model_source", None),
        actor_key=getattr(args, "actor_key", None),
    )


def _emit(result: dict, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("status") == "delegated":
        skill = result.get("delegate_skill") or "repo固有skill"
        print(f"provenance: repo管理へ委譲（{skill}）。汎用台帳は変更していません")
    elif result.get("status") == "checked":
        label = "OK" if result.get("valid") else "NG"
        print(f"provenance check: {label} ({result.get('path', '')})")
        for message in result.get("errors", []):
            print(f"  error: {message}")
        for message in result.get("warnings", []):
            print(f"  warning: {message}")
    else:
        execution = result.get("execution_id")
        suffix = f" execution={execution}" if execution else ""
        print(f"provenance {result.get('status')}:{suffix}")
    return 0 if result.get("valid", True) else 1


def cmd_provenance(args: argparse.Namespace) -> int:
    try:
        project_dir, cfg = _context(args)
        action = args.provenance_action
        if action == "init":
            metadata = _metadata(args, cfg)
            if not getattr(args, "update", False):
                warn_if_unresolved(metadata, config=cfg, command="provenance init")
            result = initialize_document(
                _path(args.path, project_dir),
                project_dir=project_dir,
                config=cfg,
                metadata=metadata,
                update=bool(getattr(args, "update", False)),
            )
        elif action == "start":
            contexts = [part for part in re.split(r"[;,]", args.context) if part.strip()]
            warn_if_unresolved(_metadata(args, cfg), config=cfg, command="provenance start")
            result = start_execution(
                _path(args.path, project_dir),
                project_dir=project_dir,
                config=cfg,
                contexts=contexts,
                role=args.role,
                metadata=_metadata(args, cfg),
                notes=args.notes,
            )
        elif action == "finish":
            evidence_refs = ";".join(
                [value for value in getattr(args, "evidence_ref", []) if value]
            ) or args.evidence_refs
            result = finish_execution(
                args.execution,
                config=cfg,
                result=args.result,
                evidence_refs=evidence_refs,
                notes=args.notes,
            )
        elif action == "check":
            result = check_document(
                _path(args.path, project_dir),
                project_dir=project_dir,
                config=cfg,
            )
        else:  # pragma: no cover - argparse requires a known action
            raise ProvenanceError(f"未知のprovenance actionです: {action}")
    except (OSError, ProvenanceError, ValueError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"provenance error: {exc}", file=sys.stderr)
        return 2
    return _emit(result, as_json=bool(getattr(args, "json", False)))
