from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from codex_widget.hook_state import HookStateReader


def _append(path: Path, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'hook_event_name': '',
        'session_id': 's1',
        'turn_id': 't1',
        'cwd': 'D:/Project/Test',
        'model': 'gpt-test',
    }
    event.update(kwargs)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(event) + '\n')


def test_user_prompt_submit_sets_working(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'UserPromptSubmit'


def test_stop_sets_idle(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    _append(events, hook_event_name='Stop')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'idle'
    assert signal.last_event_name == 'Stop'


def test_tool_events_do_not_set_working(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='PreToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'idle'
    assert signal.last_event_name == 'PreToolUse'


def test_tool_events_after_stop_stay_idle(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    _append(events, hook_event_name='Stop')
    _append(events, hook_event_name='PostToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'idle'


def test_tool_events_do_not_replace_working_lifecycle_event(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    _append(events, hook_event_name='PreToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'UserPromptSubmit'


def test_long_thinking_stays_working_until_stale_cutoff(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    old = datetime.now(timezone.utc) - timedelta(minutes=20)
    _append(events, hook_event_name='UserPromptSubmit', recorded_at=old.isoformat())

    signal = HookStateReader(events, stale_after_minutes=360).read_signal()
    assert signal.status == 'working'

    stale_signal = HookStateReader(events, stale_after_minutes=5).read_signal()
    assert stale_signal.status == 'idle'
    assert '过期' in stale_signal.note


def test_manual_idle_forces_idle(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    reader = HookStateReader(events)
    reader.mark_idle()

    signal = reader.read_signal()

    assert signal.status == 'idle'
