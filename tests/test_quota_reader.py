from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

from codex_widget.quota_reader import CodexQuotaReader, quota_is_exhausted


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(event) for event in events) + '\n', encoding='utf-8')


def _write_logs_db(path: Path, bodies: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute('create table logs (id integer primary key autoincrement, feedback_log_body text)')
        for body in bodies:
            con.execute('insert into logs (feedback_log_body) values (?)', (body,))
        con.commit()
    finally:
        con.close()


def _token_count(
    primary: float,
    secondary: float,
    resets_at: str | None = None,
    limit_name: str | None = None,
) -> dict:
    rate_limits = {
        'primary': {
            'used_percent': primary,
            'resets_at': resets_at or (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            'window_minutes': 300,
        },
        'secondary': {
            'used_percent': secondary,
            'resets_at': (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            'window_minutes': 10080,
        },
    }
    if limit_name is not None:
        rate_limits['limit_name'] = limit_name

    return {
        'payload': {
            'type': 'token_count',
            'rate_limits': rate_limits,
        }
    }


def test_reads_latest_token_count(tmp_path: Path) -> None:
    path = tmp_path / 'sessions' / 'a.jsonl'
    _write_jsonl(path, [{'payload': {'type': 'task_started'}}, _token_count(29, 54, limit_name='Codex-Pro')])

    snap = CodexQuotaReader(tmp_path / 'sessions').read_quota()

    assert snap.primary.used_percent == 29
    assert snap.secondary.used_percent == 54
    assert snap.quota_source == 'Codex-Pro'
    assert snap.note == ''


def test_falls_back_to_recent_old_session_when_latest_has_no_token_count(tmp_path: Path) -> None:
    old = tmp_path / 'sessions' / 'old.jsonl'
    new = tmp_path / 'sessions' / 'new.jsonl'
    _write_jsonl(old, [_token_count(12, 34)])
    _write_jsonl(new, [{'payload': {'type': 'task_started'}}])
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    snap = CodexQuotaReader(tmp_path / 'sessions').read_quota()

    assert snap.primary.used_percent == 12
    assert snap.secondary.used_percent == 34
    assert snap.note == '额度来自最近记录'
    assert snap.latest_file == new
    assert snap.quota_file == old


def test_prefers_logged_rate_limits(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'a.jsonl'
    logs = codex_home / 'sqlite' / 'logs_2.sqlite'
    _write_jsonl(session, [_token_count(12, 34, limit_name='session')])
    _write_logs_db(
        logs,
        [
            'websocket event: '
            + json.dumps(
                {
                    'type': 'codex.rate_limits',
                    'rate_limits': {
                        'allowed': True,
                        'limit_reached': False,
                        'primary': {'used_percent': 44, 'reset_at': 1781813603, 'window_minutes': 300},
                        'secondary': {'used_percent': 55, 'reset_at': 1782363500, 'window_minutes': 10080},
                    },
                }
            )
        ],
    )

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.primary.used_percent == 44
    assert snap.secondary.used_percent == 55
    assert snap.quota_source == 'codex.rate_limits'
    assert snap.note == ''
    assert snap.quota_file == logs


def test_logged_limit_reached_sets_signal(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'a.jsonl'
    logs = codex_home / 'sqlite' / 'logs_2.sqlite'
    _write_jsonl(session, [_token_count(12, 34)])
    _write_logs_db(
        logs,
        [
            'websocket event: '
            + json.dumps(
                {
                    'type': 'codex.rate_limits',
                    'rate_limits': {
                        'allowed': False,
                        'limit_reached': True,
                        'primary': {'used_percent': 100, 'reset_at': 1781813603, 'window_minutes': 300},
                        'secondary': {'used_percent': 12, 'reset_at': 1782363500, 'window_minutes': 10080},
                    },
                }
            )
        ],
    )

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.has_limit_signal is True


def test_does_not_fall_back_before_auth_refresh(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    old = sessions / 'old-account.jsonl'
    new = sessions / 'new-account.jsonl'
    auth = codex_home / 'auth.json'
    _write_jsonl(old, [_token_count(77, 81)])
    _write_jsonl(new, [{'payload': {'type': 'task_started'}}])
    auth.write_text('{}', encoding='utf-8')
    os.utime(old, (1000, 1000))
    os.utime(auth, (1500, 1500))
    os.utime(new, (2000, 2000))

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.primary.used_percent is None
    assert snap.secondary.used_percent is None
    assert snap.note == '未读取到当前账号 token_count'
    assert snap.latest_file == new
    assert snap.quota_file is None


def test_ignores_limit_signal_before_auth_refresh(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    old = sessions / 'old-account.jsonl'
    auth = codex_home / 'auth.json'
    _write_jsonl(
        old,
        [
            _token_count(100, 100),
            {'payload': {'type': 'error', 'message': 'usage limit reached'}},
        ],
    )
    auth.write_text('{}', encoding='utf-8')
    os.utime(old, (1000, 1000))
    os.utime(auth, (1500, 1500))

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.primary.used_percent is None
    assert snap.has_limit_signal is False
    assert snap.note == '未读取到当前账号 token_count'


def test_quota_exhausted_requires_future_reset() -> None:
    future = datetime.now(timezone.utc) + timedelta(minutes=30)
    past = datetime.now(timezone.utc) - timedelta(minutes=30)

    class Window:
        used_percent = 100
        resets_at = future

    assert quota_is_exhausted(Window(), datetime.now(timezone.utc)) is True

    Window.resets_at = past
    assert quota_is_exhausted(Window(), datetime.now(timezone.utc)) is False
