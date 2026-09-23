from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
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


def _write_task_complete(transcript: Path, turn_id: str, completed_at: datetime | None = None) -> None:
    completed_at = completed_at or datetime.now(timezone.utc)
    transcript.write_text(
        json.dumps(
            {
                'timestamp': completed_at.isoformat(),
                'type': 'event_msg',
                'payload': {
                    'type': 'task_complete',
                    'turn_id': turn_id,
                    'completed_at': int(completed_at.timestamp()),
                },
            }
        )
        + '\n',
        encoding='utf-8',
    )


def _write_turn_aborted(transcript: Path, turn_id: str, completed_at: datetime | None = None) -> None:
    completed_at = completed_at or datetime.now(timezone.utc)
    transcript.write_text(
        json.dumps(
            {
                'timestamp': completed_at.isoformat(),
                'type': 'event_msg',
                'payload': {
                    'type': 'turn_aborted',
                    'turn_id': turn_id,
                    'completed_at': int(completed_at.timestamp()),
                    'reason': 'interrupted',
                },
            }
        )
        + '\n',
        encoding='utf-8',
    )


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


def test_pre_tool_use_sets_working(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='PreToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'PreToolUse'


def test_tool_events_after_stop_stay_idle(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    _append(events, hook_event_name='Stop')
    _append(events, hook_event_name='PostToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'idle'


def test_pre_tool_use_keeps_working_lifecycle_event(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    _append(events, hook_event_name='PreToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'PreToolUse'


def test_post_tool_use_stays_working(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    transcript = tmp_path / 'rollout.jsonl'
    transcript.write_text('', encoding='utf-8')
    _append(events, hook_event_name='UserPromptSubmit', transcript_path=str(transcript))
    _append(events, hook_event_name='PreToolUse')
    _append(events, hook_event_name='PostToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'PostToolUse'


def test_transcriptless_post_tool_use_keeps_working_until_stop(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='PreToolUse')
    _append(events, hook_event_name='PostToolUse')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'PostToolUse'

    _append(events, hook_event_name='Stop')
    assert HookStateReader(events).read_signal().status == 'idle'


def test_waiting_has_priority_over_working(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit', session_id='s1', turn_id='t1', cwd='D:/Project/A')
    _append(
        events,
        hook_event_name='PreToolUse',
        session_id='s2',
        turn_id='t2',
        cwd='D:/Project/B',
        tool_name='Bash',
    )
    _append(
        events,
        hook_event_name='PermissionRequest',
        session_id='s3',
        turn_id='t3',
        cwd='D:/Project/C',
        tool_name='apply_patch',
    )

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'waiting'
    assert signal.waiting_count == 1
    assert signal.working_count == 2
    assert signal.note == '待确认 × 1 · 工作 × 2'
    assert '待确认' in signal.detail
    assert '待确认 1' not in signal.detail
    assert '- C' in signal.detail


def test_same_session_new_prompt_replaces_previous_turn(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit', session_id='s1', turn_id='t1')
    _append(events, hook_event_name='UserPromptSubmit', session_id='s1', turn_id='t2')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.working_count == 1
    assert signal.turn_id == 't2'


def test_same_session_stop_clears_previous_unfinished_turn(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit', session_id='s1', turn_id='t1')
    _append(events, hook_event_name='UserPromptSubmit', session_id='s1', turn_id='t2')
    _append(events, hook_event_name='Stop', session_id='s1', turn_id='t2')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'idle'
    assert signal.last_event_name == 'Stop'
    assert signal.turn_id == 't2'


def test_long_working_stays_active_until_stale_cutoff(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    transcript = tmp_path / 'rollout.jsonl'
    transcript.write_text('', encoding='utf-8')
    old = datetime.now(timezone.utc) - timedelta(minutes=20)
    _append(
        events,
        hook_event_name='UserPromptSubmit',
        recorded_at=old.isoformat(),
        transcript_path=str(transcript),
    )

    signal = HookStateReader(events, stale_after_minutes=360).read_signal()
    assert signal.status == 'working'

    stale_signal = HookStateReader(events, stale_after_minutes=5).read_signal()
    assert stale_signal.status == 'idle'
    assert '自动恢复闲置' in stale_signal.note


def test_transcript_task_complete_sets_idle_without_stop(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    transcript = tmp_path / 'rollout.jsonl'
    completed_at = datetime.now(timezone.utc)
    _write_task_complete(transcript, 't1', completed_at)
    _append(events, hook_event_name='UserPromptSubmit', transcript_path=str(transcript))

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'idle'
    assert signal.last_event_name == 'TaskComplete'
    assert signal.last_event_at == completed_at.replace(microsecond=0)
    assert 'transcript' in signal.note


def test_transcript_turn_aborted_sets_idle_without_stop(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    transcript = tmp_path / 'rollout.jsonl'
    completed_at = datetime.now(timezone.utc)
    _write_turn_aborted(transcript, 't1', completed_at)
    _append(events, hook_event_name='UserPromptSubmit', transcript_path=str(transcript))

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'idle'
    assert signal.last_event_name == 'TaskComplete'
    assert signal.last_event_at == completed_at.replace(microsecond=0)
    assert 'transcript' in signal.note


def test_transcript_completion_for_other_turn_stays_working(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    transcript = tmp_path / 'rollout.jsonl'
    _write_task_complete(transcript, 'other-turn')
    _append(events, hook_event_name='UserPromptSubmit', transcript_path=str(transcript))

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'UserPromptSubmit'


def test_transcriptless_working_uses_normal_stale_cutoff(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    old = datetime.now(timezone.utc) - timedelta(seconds=31)
    _append(events, hook_event_name='UserPromptSubmit', recorded_at=old.isoformat())

    signal = HookStateReader(events, stale_after_minutes=360).read_signal()

    assert signal.status == 'working'
    assert signal.last_event_name == 'UserPromptSubmit'

    _append(events, hook_event_name='PreToolUse',
            recorded_at=(datetime.now(timezone.utc) - timedelta(hours=7)).isoformat())
    assert HookStateReader(events).read_signal().status == 'idle'


def test_archived_transcript_clears_aborted_turn(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    old_path = sessions / '2026' / '09' / '24' / 'rollout-s1.jsonl'
    archive = tmp_path / 'archived_sessions'
    archive.mkdir()
    _write_turn_aborted(archive / old_path.name, 't1')
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='PreToolUse', transcript_path=str(old_path))

    signal = HookStateReader(events, sessions_dir=sessions).read_signal()

    assert signal.status == 'idle'
    assert signal.last_event_name == 'TaskComplete'


def test_missing_hook_path_finds_transcript_by_session(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    _write_task_complete(sessions / 'rollout-s1.jsonl', 't1')
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='PreToolUse')

    assert HookStateReader(events, sessions_dir=sessions).read_signal().status == 'idle'


def test_archive_completion_does_not_clear_another_turn(tmp_path: Path) -> None:
    archive = tmp_path / 'archived_sessions'
    archive.mkdir()
    _write_task_complete(archive / 'rollout-s1.jsonl', 'older-turn')
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')

    assert HookStateReader(events, sessions_dir=tmp_path / 'sessions').read_signal().status == 'working'


def test_logged_shutdown_clears_transcriptless_session_but_not_new_work(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    with sqlite3.connect(tmp_path / 'logs_2.sqlite') as connection:
        connection.execute('CREATE TABLE logs (thread_id TEXT, ts INTEGER, ts_nanos INTEGER, '
                           'target TEXT, feedback_log_body TEXT)')
        connection.execute('INSERT INTO logs VALUES (?, ?, ?, ?, ?)',
                           ('s1', int(now.timestamp()), now.microsecond * 1000,
                            'codex_core::session::handlers',
                            'session_loop: Shutting down Codex instance'))
    events = tmp_path / 'events.jsonl'
    _append(events, hook_event_name='PreToolUse',
            recorded_at=(now - timedelta(minutes=5)).isoformat())
    reader = HookStateReader(events, sessions_dir=tmp_path / 'sessions')
    signal = reader.read_signal()
    assert signal.status == 'idle'
    assert signal.last_event_name == 'SessionShutdown'

    _append(events, hook_event_name='UserPromptSubmit', turn_id='t2',
            recorded_at=(now + timedelta(seconds=1)).isoformat())
    assert reader.read_signal().status == 'working'


def test_unreadable_diagnostic_database_does_not_break_hooks(tmp_path: Path) -> None:
    (tmp_path / 'logs_2.sqlite').write_text('invalid database', encoding='utf-8')
    events = tmp_path / 'events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    assert HookStateReader(events, sessions_dir=tmp_path / 'sessions').read_signal().status == 'working'


def test_manual_idle_forces_idle(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='UserPromptSubmit')
    reader = HookStateReader(events)
    reader.mark_idle()

    signal = reader.read_signal()

    assert signal.status == 'idle'


def test_invalid_hook_events_are_ignored(tmp_path: Path) -> None:
    events = tmp_path / 'hook_events.jsonl'
    _append(events, hook_event_name='')

    signal = HookStateReader(events).read_signal()

    assert signal.status == 'unknown'
