from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any


# This file is intentionally standalone. The installer copies it to
# %USERPROFILE%\.codex_widget\hook_writer.py, and Codex hooks call that copy.
# Do not import PySide6 or the rest of this package from here.

WORK_DIR = Path.home() / '.codex_widget'
EVENTS_PATH = WORK_DIR / 'hook_events.jsonl'
ERROR_LOG_PATH = WORK_DIR / 'hook_writer_error.log'
MAX_EVENT_BYTES = 256 * 1024
MAX_EVENTS_TO_KEEP = 500


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = _safe_loads(raw)
        event = _normalize_event(payload)
        _append_jsonl(EVENTS_PATH, event)
        # No stdout: for UserPromptSubmit, stdout can be interpreted
        # as extra context. Stay silent so this hook never changes Codex behavior.
        return 0
    except Exception:
        _write_error_log()
        # Do not block Codex because the widget hook failed.
        return 0


def _safe_loads(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {'_raw': raw[:2000], '_json_error': True}
    if isinstance(parsed, dict):
        return parsed
    return {'_value': parsed}


def _normalize_event(payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    hook_event_name = _as_str(payload.get('hook_event_name'))
    # Older or experimental builds may use a different key; keep a fallback.
    if not hook_event_name:
        hook_event_name = _as_str(payload.get('event') or payload.get('type'))

    event = {
        'recorded_at': now,
        'hook_event_name': hook_event_name,
        'session_id': _as_str(payload.get('session_id')),
        'turn_id': _as_str(payload.get('turn_id')),
        'cwd': _as_str(payload.get('cwd')),
        'model': _as_str(payload.get('model')),
        'permission_mode': _as_str(payload.get('permission_mode')),
        'transcript_path': _as_str(payload.get('transcript_path')),
        'tool_name': _as_str(payload.get('tool_name')),
        'tool_use_id': _as_str(payload.get('tool_use_id')),
        'source': _as_str(payload.get('source')),
        'agent_type': _as_str(payload.get('agent_type')),
        'pid': os.getpid(),
    }
    # Keep only top-level key names for diagnostics. Do not persist prompt text,
    # tool input, or tool output; the widget only needs lifecycle state.
    event['input_keys'] = sorted(str(key) for key in payload.keys())[:80]
    return event


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
    with path.open('a', encoding='utf-8', newline='\n') as f:
        f.write(line)
        f.write('\n')
        f.flush()
    _trim_events_file_if_needed(path)


def _trim_events_file_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size <= MAX_EVENT_BYTES:
            return

        with path.open('r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        kept = _tail_with_size_limit(lines, max_lines=MAX_EVENTS_TO_KEEP, max_bytes=MAX_EVENT_BYTES)
        tmp_path = path.with_name(f'{path.name}.tmp')
        with tmp_path.open('w', encoding='utf-8', newline='\n') as f:
            f.writelines(kept)
        os.replace(tmp_path, path)
    except OSError:
        return


def _tail_with_size_limit(lines: list[str], max_lines: int, max_bytes: int) -> list[str]:
    kept: list[str] = []
    total_bytes = 0
    for line in reversed(lines[-max_lines:]):
        line_bytes = len(line.encode('utf-8'))
        if kept and total_bytes + line_bytes > max_bytes:
            break
        kept.append(line)
        total_bytes += line_bytes
    kept.reverse()
    return kept


def _write_error_log() -> None:
    try:
        ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f'[{datetime.now(timezone.utc).isoformat()}]\n')
            traceback.print_exc(file=f)
            f.write('\n')
    except Exception:
        pass


def _as_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return str(value)


if __name__ == '__main__':
    raise SystemExit(main())
