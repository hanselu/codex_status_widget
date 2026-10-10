from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from codex_widget.approval_state import PendingApproval
from codex_widget.hook_state import HookStateReader
from codex_widget.models import QuotaSnapshot
from codex_widget.snapshot import CodexSnapshotReader, _merge_pending_approvals


def _event(root: Path, name: str, *, session='memory-hook', turn='memory-turn', at=None) -> None:
    with (root / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({
            'recorded_at': (at or datetime.now(timezone.utc)).isoformat(),
            'hook_event_name': name, 'session_id': session, 'turn_id': turn,
            'cwd': 'C:/Users/Test/.codex/memories', 'tool_name': 'Bash',
        }) + '\n')


def _submission(root: Path, *, turn='memory-turn', internal='memory-core',
                trigger='memory_consolidation', quoted_prompt=False) -> None:
    now = datetime.now(timezone.utc)
    prompt = json.dumps('start: TurnStartOptions { turn_trigger: Some("memory_consolidation"),')
    body = (
        f'session_loop{{thread_id={internal}}}: Submission sub=Submission {{ id: "{turn}", '
        'op: TurnInput { request: TurnInputRequest { input: UserInput { content: '
        + (f'[Text {{ text: {prompt} }}]' if quoted_prompt else '[]')
        + f' }}, start: TurnStartOptions {{ turn_trigger: Some("{trigger}"), '
        'final_output_json_schema: None } } } }'
    )
    with sqlite3.connect(root / 'logs_2.sqlite') as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS logs (thread_id TEXT, ts INTEGER, '
                           'ts_nanos INTEGER, target TEXT, feedback_log_body TEXT)')
        connection.execute('INSERT INTO logs VALUES (?, ?, ?, ?, ?)',
                           (internal, int(now.timestamp()), now.microsecond * 1000,
                            'codex_core::session::handlers', body))


def _reader(root: Path) -> HookStateReader:
    return HookStateReader(root / 'events.jsonl', sessions_dir=root / 'sessions')


def test_memory_only_is_idle_with_recent_activity_hint(tmp_path: Path) -> None:
    _submission(tmp_path)
    _event(tmp_path, 'PreToolUse')
    _event(tmp_path, 'PostToolUse')

    signal = _reader(tmp_path).read_signal()

    assert signal.status == 'idle'
    assert signal.working_count == signal.waiting_count == 0
    assert signal.background_count == 1
    assert signal.background_turn_ids == frozenset({'memory-turn'})
    assert signal.note == '后台正在整理记忆'
    assert '最近活动' in signal.detail
    assert 'Bash（调用已结束）' in signal.detail
    assert 'memories' not in signal.detail


@pytest.mark.parametrize(('event', 'status'), [('PreToolUse', 'working'), ('PermissionRequest', 'waiting')])
def test_user_task_controls_status_alongside_memory(tmp_path: Path, event, status) -> None:
    _submission(tmp_path)
    _event(tmp_path, 'PreToolUse')
    _event(tmp_path, event, session='user', turn='user-turn')

    signal = _reader(tmp_path).read_signal()

    assert signal.status == status
    assert signal.session_id == 'user'
    assert signal.working_count + signal.waiting_count == 1
    assert signal.background_count == 1
    assert '后台正在整理记忆' in signal.note


@pytest.mark.parametrize('case', ['missing', 'suffix', 'other-trigger', 'quoted-prompt', 'ambiguous', 'broken'])
def test_unconfirmed_memory_task_stays_in_user_counts(tmp_path: Path, case) -> None:
    _event(tmp_path, 'PreToolUse')
    if case == 'suffix':
        _submission(tmp_path, turn='memory-turn-suffix')
    elif case == 'other-trigger':
        _submission(tmp_path, trigger='user')
    elif case == 'quoted-prompt':
        _submission(tmp_path, trigger='user', quoted_prompt=True)
    elif case == 'ambiguous':
        _submission(tmp_path, internal='core-a')
        _submission(tmp_path, internal='core-b')
    elif case == 'broken':
        (tmp_path / 'logs_2.sqlite').write_text('invalid database', encoding='utf-8')

    signal = _reader(tmp_path).read_signal()
    assert signal.status == 'working'
    assert signal.working_count == 1
    assert signal.background_count == 0


def test_late_startup_log_is_recognized_but_next_turn_is_not_inherited(tmp_path: Path) -> None:
    _event(tmp_path, 'PreToolUse')
    reader = _reader(tmp_path)
    assert reader.read_signal().status == 'working'

    _submission(tmp_path)
    assert reader.read_signal().status == 'idle'
    assert reader.read_signal().background_count == 1

    _event(tmp_path, 'UserPromptSubmit', turn='user-turn')
    signal = reader.read_signal()
    assert signal.status == 'working'
    assert signal.background_count == 0


@pytest.mark.parametrize('ending', ['Stop', 'transcript', 'shutdown', 'stale'])
def test_finished_memory_task_clears_background_hint(tmp_path: Path, ending) -> None:
    _submission(tmp_path)
    now = datetime.now(timezone.utc)
    _event(tmp_path, 'PreToolUse', at=now - timedelta(minutes=1))
    reader = _reader(tmp_path)
    assert reader.read_signal().background_count == 1

    if ending == 'Stop':
        _event(tmp_path, 'Stop')
    elif ending == 'transcript':
        sessions = tmp_path / 'sessions'
        sessions.mkdir()
        (sessions / 'rollout-memory-hook.jsonl').write_text(json.dumps({
            'timestamp': now.isoformat(), 'type': 'event_msg',
            'payload': {'type': 'task_complete', 'turn_id': 'memory-turn'},
        }) + '\n', encoding='utf-8')
    elif ending == 'shutdown':
        with sqlite3.connect(tmp_path / 'logs_2.sqlite') as connection:
            connection.executemany('INSERT INTO logs VALUES (?, ?, ?, ?, ?)', [
                ('memory-core', int(now.timestamp()), 0, 'codex_core::session::turn',
                 'turn{thread.id=memory-core turn.id=memory-turn model=test}: sampling'),
                ('memory-core', int(now.timestamp()), 0, 'codex_core::session::handlers',
                 'session_loop: Shutting down Codex instance'),
            ])
    else:
        _event(tmp_path, 'PreToolUse', at=now - timedelta(hours=7))

    signal = reader.read_signal()
    assert signal.status == 'idle'
    assert signal.background_count == 0
    assert '后台' not in signal.note


def test_background_activity_prevents_offline_and_preserves_snapshot_hint(tmp_path: Path) -> None:
    _submission(tmp_path)
    _event(tmp_path, 'PreToolUse')
    reader = CodexSnapshotReader(tmp_path / 'sessions', tmp_path / 'events.jsonl',
                                 codex_app_running=lambda: False)

    snapshot = reader.read_snapshot(quota=QuotaSnapshot())
    assert snapshot.status == 'idle'
    assert snapshot.note == '后台正在整理记忆'
    assert '最近活动' in snapshot.detail
    assert reader.read_snapshot(quota=QuotaSnapshot(has_limit_signal=True)).status == 'cooldown'

    _event(tmp_path, 'Stop')
    finished = reader.read_snapshot(quota=QuotaSnapshot())
    assert finished.status == 'offline'
    assert '后台' not in finished.note


def test_approval_merge_keeps_user_approval_and_background_hint(tmp_path: Path) -> None:
    _submission(tmp_path)
    _event(tmp_path, 'PermissionRequest')
    signal = _reader(tmp_path).read_signal()
    memory_approval = PendingApproval(datetime.now(timezone.utc), 'memory-hook', 'memory-turn',
                                     '', 'Bash', 'call-memory', tmp_path / 'memory.jsonl')
    user_approval = PendingApproval(datetime.now(timezone.utc), 'user', 'user-turn',
                                   '', 'Bash', 'call-user', tmp_path / 'user.jsonl')

    assert _merge_pending_approvals(signal, [memory_approval]).status == 'idle'
    merged = _merge_pending_approvals(signal, [memory_approval, user_approval])
    assert merged.status == 'waiting'
    assert merged.waiting_count == 1
    assert merged.background_count == 1
    assert '后台正在整理记忆' in merged.note
