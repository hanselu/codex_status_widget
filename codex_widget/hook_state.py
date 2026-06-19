from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .models import HookSignal


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

@dataclass(slots=True)
class _SessionState:
    status: str = 'idle'
    last_event_name: str = ''
    last_event_at: datetime | None = None
    session_id: str = ''
    turn_id: str = ''
    cwd: str = ''
    model: str = ''


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
            last_event = event
            event_name = _as_str(event.get('hook_event_name'))
            if event_name == 'WidgetManualIdle':
                for existing in sessions.values():
                    existing.status = 'idle'
                    existing.last_event_name = event_name
                    existing.last_event_at = _parse_datetime(event.get('recorded_at')) or existing.last_event_at
                session_id = '__global__'
            else:
                session_id = _as_str(event.get('session_id')) or '__global__'

            state = sessions.setdefault(session_id, _SessionState(session_id=session_id))
            self._apply_event(state, event)

        now = datetime.now(timezone.utc)
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
            return HookSignal(
                status='working',
                last_event_name=state.last_event_name,
                last_event_at=state.last_event_at,
                session_id=state.session_id if state.session_id != '__global__' else '',
                turn_id=state.turn_id,
                cwd=state.cwd,
                model=state.model,
                note=_format_note('hook 工作中', state.last_event_name, state.last_event_at),
                events_path=self.events_path,
            )

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

        if event_name in WORKING_EVENTS:
            state.status = 'working'
            return
        if event_name in IDLE_EVENTS:
            # WidgetManualIdle is global and should force all known states idle.
            state.status = 'idle'
            return
        if event_name in NEUTRAL_EVENTS:
            if state.status != 'working':
                state.status = 'idle'
            return


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
