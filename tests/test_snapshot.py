from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from codex_widget.snapshot import CodexSnapshotReader


def test_hook_working_overrides_stale_session_mtime(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                'payload': {
                    'type': 'token_count',
                    'rate_limits': {
                        'primary': {'used_percent': 10, 'resets_at': None, 'window_minutes': 300},
                        'secondary': {'used_percent': 20, 'resets_at': None, 'window_minutes': 10080},
                    },
                }
            }
        )
        + '\n',
        encoding='utf-8',
    )

    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        json.dumps(
            {
                'recorded_at': datetime.now(timezone.utc).isoformat(),
                'hook_event_name': 'UserPromptSubmit',
                'session_id': 's1',
                'turn_id': 't1',
                'cwd': '',
                'model': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    snapshot = CodexSnapshotReader(sessions, events).read_snapshot()

    assert snapshot.status == 'working'
    assert snapshot.primary.used_percent == 10


def test_snapshot_text_matches_compact_widget_layout(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                'payload': {
                    'type': 'token_count',
                    'rate_limits': {
                        'limit_name': 'test-source',
                        'primary': {'used_percent': 25, 'resets_at': None, 'window_minutes': 300},
                        'secondary': {'used_percent': 40, 'resets_at': None, 'window_minutes': 10080},
                    },
                }
            }
        )
        + '\n',
        encoding='utf-8',
    )

    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        json.dumps(
            {
                'recorded_at': datetime.now(timezone.utc).isoformat(),
                'hook_event_name': 'Stop',
                'session_id': 's1',
                'turn_id': 't1',
                'cwd': '',
                'model': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    snapshot = CodexSnapshotReader(sessions, events).read_snapshot()

    assert snapshot.primary_text == '5小时：75% 未知'
    assert snapshot.secondary_text == '周额度：60% 未知'
    assert snapshot.reset_text == ''
    assert snapshot.updated_text.count(':') == 2
    assert '更新：' not in snapshot.updated_text
    assert snapshot.note.startswith('hook 闲置: Stop')
    assert len(snapshot.note.splitlines()) == 1


def test_codex_app_not_running_forces_red_status(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                'payload': {
                    'type': 'token_count',
                    'rate_limits': {
                        'primary': {'used_percent': 10, 'resets_at': None, 'window_minutes': 300},
                        'secondary': {'used_percent': 20, 'resets_at': None, 'window_minutes': 10080},
                    },
                }
            }
        )
        + '\n',
        encoding='utf-8',
    )

    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        json.dumps(
            {
                'recorded_at': datetime.now(timezone.utc).isoformat(),
                'hook_event_name': 'Stop',
                'session_id': 's1',
                'turn_id': 't1',
                'cwd': '',
                'model': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    snapshot = CodexSnapshotReader(sessions, events, codex_app_running=lambda: False).read_snapshot()

    assert snapshot.status == 'offline'
    assert snapshot.status_text == 'Codex 未运行'
    assert snapshot.codex_app_running is False
    assert 'Codex App 未运行' in snapshot.note


def test_exhausted_quota_forces_red_status(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                'payload': {
                    'type': 'token_count',
                    'rate_limits': {
                        'primary': {
                            'used_percent': 100,
                            'resets_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                            'window_minutes': 300,
                        },
                        'secondary': {'used_percent': 20, 'resets_at': None, 'window_minutes': 10080},
                    },
                }
            }
        )
        + '\n',
        encoding='utf-8',
    )

    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        json.dumps(
            {
                'recorded_at': datetime.now(timezone.utc).isoformat(),
                'hook_event_name': 'Stop',
                'session_id': 's1',
                'turn_id': 't1',
                'cwd': '',
                'model': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    snapshot = CodexSnapshotReader(sessions, events, codex_app_running=lambda: True).read_snapshot()

    assert snapshot.status == 'cooldown'
    assert snapshot.status_text == '无额度'
