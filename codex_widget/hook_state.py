from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .models import HookSignal, HookSignalName


WORKING_EVENTS = {
    'UserPromptSubmit',
}

NEUTRAL_EVENTS = {
    'SessionStart',
    'PreToolUse',
    'PostToolUse',
    'PermissionRequest',
    'SubagentStart',
    'SubagentStop',
    'PreCompact',
    'PostCompact',
}

IDLE_EVENTS = {
    'Stop',
    'WidgetManualIdle',
}

TRANSCRIPT_FINISHED_EVENTS = {
    'task_complete',
    'turn_aborted',
}

TRANSCRIPTLESS_STALE_AFTER_MINUTES = 10
MAX_TRANSCRIPT_BYTES_TO_READ = 512 * 1024


@dataclass(slots=True)
class _SessionState:
    status: str = 'idle'
    last_event_name: str = ''
    last_event_at: datetime | None = None
    session_id: str = ''
    turn_id: str = ''
    cwd: str = ''
    model: str = ''
    transcript_path: str = ''
    note: str = ''


class HookStateReader:
    def __init__(
        self,
        events_path: Path,
        stale_after_minutes: int = 360,
        max_events_to_read: int = 5000,
    ) -> None:
        self.events_path = Path(events_path).expanduser()
        self.stale_after_minutes = stale_after_minutes
        self.max_events_to_read = max_events_to_read

    def read_signal(self) -> HookSignal:
        events = self._read_events()
        if not events:
            return HookSignal(status='unknown', note='未读取到 hook 事件', events_path=self.events_path)

        sessions: dict[str, _SessionState] = {}
        last_event: dict[str, Any] | None = None
        for event in events:
            event_name = _as_str(event.get('hook_event_name'))
            if not event_name:
                continue
            last_event = event
            if event_name == 'WidgetManualIdle':
                for existing in sessions.values():
                    existing.status = 'idle'
                    existing.last_event_name = event_name
                    existing.last_event_at = _parse_datetime(event.get('recorded_at')) or existing.last_event_at
                    existing.note = ''
                session_id = '__global__'
            else:
                session_id = _as_str(event.get('session_id')) or '__global__'

            state = sessions.setdefault(session_id, _SessionState(session_id=session_id))
            self._apply_event(state, event)

        now = datetime.now(timezone.utc)
        self._apply_transcript_completion(sessions)
        _expire_transcriptless_working_states(sessions, now)

        stale_cutoff = now - timedelta(minutes=self.stale_after_minutes)

        working_states = [
            state
            for state in sessions.values()
            if state.status == 'working'
            and state.last_event_at is not None
            and state.last_event_at >= stale_cutoff
        ]
        if working_states:
            state = max(working_states, key=lambda item: item.last_event_at or datetime.min.replace(tzinfo=timezone.utc))
            return _signal_from_state('working', state, 'hook 工作中', self.events_path)

        # If there is a working state but it is stale, treat as idle and say why.
        stale_working_states = [state for state in sessions.values() if state.status == 'working']
        if stale_working_states:
            state = max(
                stale_working_states,
                key=lambda item: item.last_event_at or datetime.min.replace(tzinfo=timezone.utc),
            )
            return HookSignal(
                status='idle',
                last_event_name=state.last_event_name,
                last_event_at=state.last_event_at,
                session_id=state.session_id if state.session_id != '__global__' else '',
                turn_id=state.turn_id,
                cwd=state.cwd,
                model=state.model,
                note='hook 工作状态已过期，视为闲置',
                events_path=self.events_path,
            )

        latest_states = [state for state in sessions.values() if state.last_event_at is not None]
        if latest_states:
            state = max(latest_states, key=lambda item: item.last_event_at or datetime.min.replace(tzinfo=timezone.utc))
            return _signal_from_state('idle', state, 'hook 闲置', self.events_path)

        if last_event is None:
            return HookSignal(status='unknown', note='未读取到 hook 事件', events_path=self.events_path)

        event_name = _as_str(last_event.get('hook_event_name'))
        event_at = _parse_datetime(last_event.get('recorded_at'))
        return HookSignal(
            status='idle',
            last_event_name=event_name,
            last_event_at=event_at,
            session_id=_as_str(last_event.get('session_id')),
            turn_id=_as_str(last_event.get('turn_id')),
            cwd=_as_str(last_event.get('cwd')),
            model=_as_str(last_event.get('model')),
            note=_format_note('hook 闲置', event_name, event_at),
            events_path=self.events_path,
        )

    def mark_idle(self) -> None:
        event = {
            'recorded_at': datetime.now(timezone.utc).isoformat(),
            'hook_event_name': 'WidgetManualIdle',
            'session_id': '__global__',
            'turn_id': '',
            'cwd': '',
            'model': '',
            'permission_mode': '',
            'transcript_path': '',
            'tool_name': '',
            'tool_use_id': '',
            'source': 'widget',
            'agent_type': '',
            'pid': 0,
            'input_keys': [],
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open('a', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(event, ensure_ascii=False, separators=(',', ':')))
            f.write('\n')

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        try:
            with self.events_path.open('r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            return []

        events: list[dict[str, Any]] = []
        for line in lines[-self.max_events_to_read :]:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def _apply_event(self, state: _SessionState, event: dict[str, Any]) -> None:
        event_name = _as_str(event.get('hook_event_name'))
        event_at = _parse_datetime(event.get('recorded_at'))
        should_record_lifecycle = event_name not in NEUTRAL_EVENTS or state.status != 'working'

        if event_at is not None and should_record_lifecycle:
            state.last_event_at = event_at
        if event_name and should_record_lifecycle:
            state.last_event_name = event_name
        state.turn_id = _as_str(event.get('turn_id')) or state.turn_id
        state.cwd = _as_str(event.get('cwd')) or state.cwd
        state.model = _as_str(event.get('model')) or state.model
        state.transcript_path = _as_str(event.get('transcript_path')) or state.transcript_path

        if event_name in WORKING_EVENTS:
            state.status = 'working'
            state.note = ''
            return
        if event_name in IDLE_EVENTS:
            # WidgetManualIdle is global and should force all known states idle.
            state.status = 'idle'
            state.note = ''
            return
        if event_name in NEUTRAL_EVENTS:
            if state.status != 'working':
                state.status = 'idle'
            return

    def _apply_transcript_completion(self, sessions: dict[str, _SessionState]) -> None:
        for state in sessions.values():
            if state.status != 'working' or not state.transcript_path or not state.turn_id:
                continue
            completed_at = _read_task_finished_at(Path(state.transcript_path), state.turn_id)
            if completed_at is None:
                continue
            state.status = 'idle'
            state.last_event_name = 'TaskComplete'
            state.last_event_at = completed_at
            state.note = 'transcript 已记录结束，视为闲置'


def _signal_from_state(
    status: HookSignalName, state: _SessionState, note_prefix: str, events_path: Path
) -> HookSignal:
    note = state.note or _format_note(note_prefix, state.last_event_name, state.last_event_at)
    return HookSignal(
        status=status,
        last_event_name=state.last_event_name,
        last_event_at=state.last_event_at,
        session_id=state.session_id if state.session_id != '__global__' else '',
        turn_id=state.turn_id,
        cwd=state.cwd,
        model=state.model,
        note=note,
        events_path=events_path,
    )


def _expire_transcriptless_working_states(sessions: dict[str, _SessionState], now: datetime) -> None:
    cutoff = now - timedelta(minutes=TRANSCRIPTLESS_STALE_AFTER_MINUTES)
    for state in sessions.values():
        if (
            state.status == 'working'
            and not state.transcript_path
            and state.last_event_at is not None
            and state.last_event_at < cutoff
        ):
            state.status = 'idle'
            state.last_event_name = 'UserPromptSubmitExpired'
            state.note = '无 transcript 的 hook 工作状态已过期，视为闲置'


def _read_task_finished_at(transcript_path: Path, turn_id: str) -> datetime | None:
    try:
        lines = _read_recent_transcript_lines(transcript_path.expanduser())
    except OSError:
        return None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get('payload')
        if not isinstance(payload, dict):
            continue
        if event.get('type') != 'event_msg' or payload.get('type') not in TRANSCRIPT_FINISHED_EVENTS:
            continue
        if _as_str(payload.get('turn_id')) != turn_id:
            continue
        completed_at = _datetime_from_unix_seconds(payload.get('completed_at'))
        return completed_at or _parse_datetime(event.get('timestamp'))
    return None


def _read_recent_transcript_lines(transcript_path: Path) -> list[str]:
    with transcript_path.open('rb') as f:
        f.seek(0, 2)
        file_size = f.tell()
        start = max(0, file_size - MAX_TRANSCRIPT_BYTES_TO_READ)
        f.seek(start)
        if start:
            f.readline()
        return [line.decode('utf-8', errors='replace') for line in f]


def _datetime_from_unix_seconds(value: Any) -> datetime | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _format_note(prefix: str, event_name: str, event_at: datetime | None) -> str:
    if not event_name:
        return prefix
    if event_at is None:
        return f'{prefix}: {event_name}'
    return f'{prefix}: {event_name} {event_at.astimezone():%H:%M:%S}'


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
