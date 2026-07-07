from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any


MAX_SESSION_FILES_TO_SCAN = 30
MAX_TRANSCRIPT_BYTES_TO_READ = 512 * 1024


@dataclass(frozen=True, slots=True)
class PendingApproval:
    requested_at: datetime
    session_id: str
    turn_id: str
    cwd: str
    tool_name: str
    call_id: str
    transcript_path: Path


def read_pending_approvals(sessions_dir: Path, stale_after_minutes: int = 360) -> list[PendingApproval]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=stale_after_minutes)
    approvals: list[PendingApproval] = []
    for path in _find_latest_jsonl_files(Path(sessions_dir).expanduser(), MAX_SESSION_FILES_TO_SCAN):
        approvals.extend(_read_pending_approvals_from_file(path, cutoff))
    approvals.sort(key=lambda item: item.requested_at, reverse=True)
    return approvals


def _read_pending_approvals_from_file(path: Path, cutoff: datetime) -> list[PendingApproval]:
    session_id = ''
    cwd = ''
    pending: dict[str, PendingApproval] = {}

    try:
        lines = _read_recent_transcript_lines(path)
    except OSError:
        return []

    for line in lines:
        event = _loads_json_line(line)
        if not isinstance(event, dict):
            continue

        payload = event.get('payload')
        if not isinstance(payload, dict):
            continue

        if event.get('type') == 'session_meta':
            session_id = _as_str(payload.get('session_id')) or session_id
            cwd = _as_str(payload.get('cwd')) or cwd
            continue

        if event.get('type') == 'turn_context':
            cwd = _as_str(payload.get('cwd')) or cwd
            continue

        if event.get('type') == 'response_item':
            payload_type = _as_str(payload.get('type'))
            if payload_type == 'function_call':
                approval = _approval_from_function_call(event, payload, path, session_id, cwd)
                if approval is not None and approval.requested_at >= cutoff:
                    pending[approval.call_id] = approval
                continue
            if payload_type == 'function_call_output':
                pending.pop(_as_str(payload.get('call_id')), None)
                continue

        if event.get('type') == 'event_msg' and payload.get('type') in {'task_complete', 'turn_aborted'}:
            finished_turn_id = _as_str(payload.get('turn_id'))
            if finished_turn_id:
                pending = {
                    call_id: approval
                    for call_id, approval in pending.items()
                    if approval.turn_id != finished_turn_id
                }

    return list(pending.values())


def _approval_from_function_call(
    event: dict[str, Any],
    payload: dict[str, Any],
    transcript_path: Path,
    session_id: str,
    cwd: str,
) -> PendingApproval | None:
    arguments = _parse_arguments(payload.get('arguments'))
    if not _requires_approval(arguments):
        return None

    call_id = _as_str(payload.get('call_id'))
    if not call_id:
        return None

    event_at = _parse_datetime(event.get('timestamp'))
    if event_at is None:
        return None

    metadata = payload.get('internal_chat_message_metadata_passthrough')
    turn_id = ''
    if isinstance(metadata, dict):
        turn_id = _as_str(metadata.get('turn_id'))

    return PendingApproval(
        requested_at=event_at,
        session_id=session_id,
        turn_id=turn_id,
        cwd=cwd,
        tool_name=_as_str(payload.get('name')),
        call_id=call_id,
        transcript_path=transcript_path,
    )


def _requires_approval(arguments: dict[str, Any]) -> bool:
    sandbox_permissions = _as_str(arguments.get('sandbox_permissions')).lower()
    if sandbox_permissions == 'require_escalated':
        return True
    approval = _as_str(arguments.get('approval')).lower()
    return approval in {'required', 'require', 'requires_approval'}


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _find_latest_jsonl_files(sessions_dir: Path, limit: int) -> list[Path]:
    if not sessions_dir.exists():
        return []

    files: list[tuple[float, Path]] = []
    try:
        candidates = sessions_dir.rglob('*.jsonl')
        for path in candidates:
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return []

    files.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in files[:limit]]


def _read_recent_transcript_lines(transcript_path: Path) -> list[str]:
    with transcript_path.open('rb') as f:
        f.seek(0, 2)
        file_size = f.tell()
        start = max(0, file_size - MAX_TRANSCRIPT_BYTES_TO_READ)
        f.seek(start)
        if start:
            f.readline()
        return [line.decode('utf-8', errors='replace') for line in f]


def _loads_json_line(line: str) -> dict[str, Any] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return str(value)
