from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from codex_widget.hook_installer import HookSetupStatus
from codex_widget.models import HookSignal, QuotaSnapshot, QuotaWindow
from codex_widget.snapshot import CodexSnapshotReader


def test_cached_quota_refreshes_status_without_querying_quota(tmp_path: Path) -> None:
    reader = CodexSnapshotReader(tmp_path / 'sessions', tmp_path / 'events.jsonl')

    def unexpected_query():
        raise AssertionError('状态刷新不能同步查询额度')

    reader.quota_reader.read_quota = unexpected_query
    reader.hook_reader.read_signal = lambda: HookSignal(status='working')
    quota = QuotaSnapshot(primary=QuotaWindow(used_percent=20, window_minutes=300))
    assert reader.read_snapshot(quota=quota).status == 'working'
    reader.hook_reader.read_signal = lambda: HookSignal(status='idle')
    snapshot = reader.read_snapshot(quota=quota)
    assert snapshot.status == 'idle'
    assert snapshot.primary.used_percent == 20


def _read_snapshot_for_windows(
    tmp_path: Path,
    primary: QuotaWindow,
    secondary: QuotaWindow,
):
    reader = CodexSnapshotReader(tmp_path / 'sessions', tmp_path / 'hook_events.jsonl')
    reader.quota_reader.read_quota = lambda: QuotaSnapshot(primary=primary, secondary=secondary)
    reader.hook_reader.read_signal = lambda: HookSignal(status='idle')
    return reader.read_snapshot()


def test_hook_activity_overrides_stale_session_mtime(tmp_path: Path) -> None:
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


def test_waiting_status_overrides_other_active_sessions(tmp_path: Path) -> None:
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

    now = datetime.now(timezone.utc).isoformat()
    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        'recorded_at': now,
                        'hook_event_name': 'UserPromptSubmit',
                        'session_id': 's1',
                        'turn_id': 't1',
                        'cwd': 'D:/Project/A',
                        'model': '',
                    }
                ),
                json.dumps(
                    {
                        'recorded_at': now,
                        'hook_event_name': 'PreToolUse',
                        'session_id': 's2',
                        'turn_id': 't2',
                        'cwd': 'D:/Project/B',
                        'model': '',
                        'tool_name': 'Bash',
                    }
                ),
                json.dumps(
                    {
                        'recorded_at': now,
                        'hook_event_name': 'PermissionRequest',
                        'session_id': 's3',
                        'turn_id': 't3',
                        'cwd': 'D:/Project/C',
                        'model': '',
                        'tool_name': 'apply_patch',
                    }
                ),
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    snapshot = CodexSnapshotReader(sessions, events).read_snapshot()

    assert snapshot.status == 'waiting'
    assert snapshot.status_text == '待确认'
    assert snapshot.note == '待确认 × 1 · 工作 × 2'
    assert '待确认' in snapshot.detail
    assert '工作中' in snapshot.detail
    assert '待确认 1' not in snapshot.detail
    assert '工作中 2' not in snapshot.detail


def test_pending_transcript_approval_overrides_working_status(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    session.write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        'timestamp': now.isoformat(),
                        'type': 'session_meta',
                        'payload': {'session_id': 'approval-session', 'cwd': 'D:/Project/NeedsApproval'},
                    }
                ),
                json.dumps(
                    {
                        'timestamp': now.isoformat(),
                        'type': 'event_msg',
                        'payload': {
                            'type': 'token_count',
                            'rate_limits': {
                                'primary': {'used_percent': 10, 'resets_at': None, 'window_minutes': 300},
                                'secondary': {'used_percent': 20, 'resets_at': None, 'window_minutes': 10080},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        'timestamp': now.isoformat(),
                        'type': 'response_item',
                        'payload': {
                            'type': 'function_call',
                            'name': 'shell_command',
                            'call_id': 'call-needs-approval',
                            'arguments': json.dumps(
                                {
                                    'command': 'Remove-Item temp.txt',
                                    'sandbox_permissions': 'require_escalated',
                                }
                            ),
                            'internal_chat_message_metadata_passthrough': {'turn_id': 'approval-turn'},
                        },
                    }
                ),
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        json.dumps(
            {
                'recorded_at': now.isoformat(),
                'hook_event_name': 'UserPromptSubmit',
                'session_id': 'working-session',
                'turn_id': 'working-turn',
                'cwd': 'D:/Project/Working',
                'model': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    snapshot = CodexSnapshotReader(sessions, events).read_snapshot()

    assert snapshot.status == 'waiting'
    assert snapshot.status_text == '待确认'
    assert snapshot.note == '待确认 × 1 · 工作 × 1'
    assert '待确认' in snapshot.detail
    assert '待确认 1' not in snapshot.detail
    assert 'NeedsApproval' in snapshot.detail


def test_completed_transcript_approval_does_not_stay_waiting(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    session.write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        'timestamp': now.isoformat(),
                        'type': 'session_meta',
                        'payload': {'session_id': 'approval-session', 'cwd': 'D:/Project/NeedsApproval'},
                    }
                ),
                json.dumps(
                    {
                        'timestamp': now.isoformat(),
                        'type': 'event_msg',
                        'payload': {
                            'type': 'token_count',
                            'rate_limits': {
                                'primary': {'used_percent': 10, 'resets_at': None, 'window_minutes': 300},
                                'secondary': {'used_percent': 20, 'resets_at': None, 'window_minutes': 10080},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        'timestamp': now.isoformat(),
                        'type': 'response_item',
                        'payload': {
                            'type': 'function_call',
                            'name': 'shell_command',
                            'call_id': 'call-needs-approval',
                            'arguments': json.dumps({'sandbox_permissions': 'require_escalated'}),
                            'internal_chat_message_metadata_passthrough': {'turn_id': 'approval-turn'},
                        },
                    }
                ),
                json.dumps(
                    {
                        'timestamp': now.isoformat(),
                        'type': 'response_item',
                        'payload': {'type': 'function_call_output', 'call_id': 'call-needs-approval'},
                    }
                ),
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        json.dumps(
            {
                'recorded_at': now.isoformat(),
                'hook_event_name': 'UserPromptSubmit',
                'session_id': 'working-session',
                'turn_id': 'working-turn',
                'cwd': 'D:/Project/Working',
                'model': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    snapshot = CodexSnapshotReader(sessions, events).read_snapshot()

    assert snapshot.status == 'working'
    assert '待确认' not in snapshot.detail


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
    assert snapshot.note == ''
    assert snapshot.detail == ''


def test_quota_display_slots_support_legacy_and_weekly_only_structures(tmp_path: Path) -> None:
    legacy = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=25, window_minutes=300),
        QuotaWindow(used_percent=40, window_minutes=10080),
    )

    assert legacy.primary_title == '5小时'
    assert legacy.primary.window_minutes == 300
    assert legacy.primary_text == '5小时：75% 未知'
    assert legacy.primary_visible is True
    assert legacy.secondary_title == '周额度'
    assert legacy.secondary.window_minutes == 10080
    assert legacy.secondary_text == '周额度：60% 未知'
    assert legacy.secondary_visible is True

    weekly_only = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=3, window_minutes=10080),
        QuotaWindow(),
    )

    assert weekly_only.primary_title == '周额度'
    assert weekly_only.primary.window_minutes == 10080
    assert weekly_only.primary_text == '周额度：97% 未知'
    assert weekly_only.primary_visible is True
    assert weekly_only.secondary_text == ''
    assert weekly_only.secondary_visible is False

    five_hour_only = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=25, window_minutes=300),
        QuotaWindow(),
    )

    assert five_hour_only.primary_title == '5小时'
    assert five_hour_only.primary_text == '5小时：75% 未知'
    assert five_hour_only.secondary_visible is False


def test_quota_display_slots_sort_recognized_windows_by_duration(tmp_path: Path) -> None:
    snapshot = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=40, window_minutes=10080),
        QuotaWindow(used_percent=25, window_minutes=300),
    )

    assert snapshot.primary_title == '5小时'
    assert snapshot.primary.window_minutes == 300
    assert snapshot.secondary_title == '周额度'
    assert snapshot.secondary.window_minutes == 10080


def test_quota_display_slots_use_neutral_names_for_unknown_windows(tmp_path: Path) -> None:
    single = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=10, window_minutes=1440),
        QuotaWindow(),
    )

    assert single.primary_title == '额度'
    assert single.primary_text == '额度：90% 未知'
    assert single.secondary_visible is False

    snapshot = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=10, window_minutes=1440),
        QuotaWindow(used_percent=20),
    )

    assert snapshot.primary_title == '额度'
    assert snapshot.primary_text == '额度：90% 未知'
    assert snapshot.secondary_title == '额外额度'
    assert snapshot.secondary_text == '额外额度：80% 未知'
    assert snapshot.primary_visible is True
    assert snapshot.secondary_visible is True


def test_quota_reset_format_follows_window_duration(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    today_five_hour = now.replace(hour=4, minute=22, second=0, microsecond=0)
    today_weekly = now.replace(hour=16, minute=23, second=0, microsecond=0)
    tomorrow = (now + timedelta(days=1)).replace(hour=4, minute=22, second=0, microsecond=0)

    same_day = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=25, resets_at=today_five_hour, window_minutes=300),
        QuotaWindow(),
    )

    assert same_day.primary_text == '5小时：75% 04:22'

    snapshot = _read_snapshot_for_windows(
        tmp_path,
        QuotaWindow(used_percent=40, resets_at=today_weekly, window_minutes=10080),
        QuotaWindow(used_percent=25, resets_at=tomorrow, window_minutes=300),
    )

    assert snapshot.primary_text == f'5小时：75% {tomorrow.month}-{tomorrow.day} 04:22'
    assert snapshot.secondary_text == f'周额度：60% {today_weekly.month}-{today_weekly.day} 16:23'


def test_quota_display_slots_keep_one_neutral_placeholder_when_unread(tmp_path: Path) -> None:
    snapshot = _read_snapshot_for_windows(tmp_path, QuotaWindow(), QuotaWindow())

    assert snapshot.primary_title == '额度'
    assert snapshot.primary_text == '额度：未读取 未知'
    assert snapshot.primary_visible is True
    assert snapshot.secondary_text == ''
    assert snapshot.secondary_visible is False


def test_weekly_quota_reset_time_uses_month_day_format(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    weekly_reset = datetime.now().astimezone().replace(
        year=2026,
        month=7,
        day=12,
        hour=16,
        minute=23,
        second=0,
        microsecond=0,
    )
    session.write_text(
        json.dumps(
            {
                'payload': {
                    'type': 'token_count',
                    'rate_limits': {
                        'primary': {'used_percent': 25, 'resets_at': None, 'window_minutes': 300},
                        'secondary': {
                            'used_percent': 40,
                            'resets_at': weekly_reset.isoformat(),
                            'window_minutes': 10080,
                        },
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
    assert snapshot.secondary_text == '周额度：60% 7-12 16:23'


def test_idle_cleanup_note_is_hidden_from_compact_widget(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                'payload': {
                    'type': 'token_count',
                    'rate_limits': {
                        'primary': {'used_percent': 25, 'resets_at': None, 'window_minutes': 300},
                        'secondary': {'used_percent': 40, 'resets_at': None, 'window_minutes': 10080},
                    },
                }
            }
        )
        + '\n',
        encoding='utf-8',
    )

    old = datetime.now(timezone.utc) - timedelta(hours=7)
    events = tmp_path / 'hook_events.jsonl'
    events.write_text(
        json.dumps(
            {
                'recorded_at': old.isoformat(),
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

    assert snapshot.status == 'idle'
    assert snapshot.status_text == '闲置中'
    assert snapshot.hook_signal.note
    assert snapshot.note == ''
    assert snapshot.detail == ''


def test_snapshot_shows_hook_setup_note_when_incomplete(tmp_path: Path) -> None:
    sessions = tmp_path / 'sessions'
    session = sessions / 'a.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                'payload': {
                    'type': 'token_count',
                    'rate_limits': {
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
    hook_status = HookSetupStatus(
        hooks_path=tmp_path / 'hooks.json',
        writer_path=tmp_path / 'hook_writer.py',
        hooks_file_exists=True,
        writer_exists=True,
        installed_events=('UserPromptSubmit', 'Stop'),
        missing_events=('PreToolUse', 'PostToolUse'),
    )

    snapshot = CodexSnapshotReader(
        sessions,
        events,
        hook_setup_status_reader=lambda: hook_status,
    ).read_snapshot()

    assert snapshot.status == 'idle'
    assert '钩子配置不完整' in snapshot.note
    assert 'PreToolUse' in snapshot.note
    assert 'PostToolUse' in snapshot.detail


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
    assert snapshot.status_text == '未运行'
    assert snapshot.codex_app_running is False
    assert 'ChatGPT App 未运行' in snapshot.note


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
