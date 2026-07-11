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


def _write_auth(path: Path, account_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'tokens': {'account_id': account_id}}), encoding='utf-8')


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
    limit_id: str | None = None,
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
    if limit_id is not None:
        rate_limits['limit_id'] = limit_id

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


def test_prefers_general_codex_limit_over_newer_spark_limit_in_same_session(tmp_path: Path) -> None:
    path = tmp_path / 'sessions' / 'a.jsonl'
    _write_jsonl(
        path,
        [
            _token_count(12, 34, limit_id='codex'),
            _token_count(0, 0, limit_id='codex_bengalfox'),
        ],
    )

    snap = CodexQuotaReader(tmp_path / 'sessions').read_quota()

    assert snap.primary.used_percent == 12
    assert snap.secondary.used_percent == 34
    assert snap.note == ''


def test_prefers_recent_general_codex_limit_over_latest_spark_session(tmp_path: Path) -> None:
    old = tmp_path / 'sessions' / 'old.jsonl'
    new = tmp_path / 'sessions' / 'new.jsonl'
    _write_jsonl(old, [_token_count(12, 34, limit_id='codex')])
    _write_jsonl(new, [_token_count(0, 0, limit_id='codex_bengalfox')])
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    snap = CodexQuotaReader(tmp_path / 'sessions').read_quota()

    assert snap.primary.used_percent == 12
    assert snap.secondary.used_percent == 34
    assert snap.note == '额度来自最近记录'
    assert snap.latest_file == new
    assert snap.quota_file == old


def test_skips_transient_all_zero_token_count_when_recent_real_quota_exists(tmp_path: Path) -> None:
    path = tmp_path / 'sessions' / 'a.jsonl'
    _write_jsonl(path, [_token_count(79, 31, limit_id='codex'), _token_count(0, 0, limit_id='codex')])

    snap = CodexQuotaReader(tmp_path / 'sessions').read_quota()

    assert snap.primary.used_percent == 79
    assert snap.secondary.used_percent == 31


def test_all_zero_token_count_is_allowed_when_it_is_the_only_quota(tmp_path: Path) -> None:
    path = tmp_path / 'sessions' / 'a.jsonl'
    _write_jsonl(path, [_token_count(0, 0, limit_id='codex')])

    snap = CodexQuotaReader(tmp_path / 'sessions').read_quota()

    assert snap.primary.used_percent == 0
    assert snap.secondary.used_percent == 0


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


def test_reads_received_message_logged_rate_limits(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'a.jsonl'
    logs = codex_home / 'logs_2.sqlite'
    _write_jsonl(session, [_token_count(12, 34, limit_name='session')])
    _write_logs_db(
        logs,
        [
            'Received message '
            + json.dumps(
                {
                    'type': 'codex.rate_limits',
                    'plan_type': 'pro',
                    'rate_limits': {
                        'allowed': True,
                        'limit_reached': False,
                        'primary': {'used_percent': 6, 'reset_at': 1781813603, 'window_minutes': 300},
                        'secondary': {'used_percent': 36, 'reset_at': 1782363500, 'window_minutes': 10080},
                    },
                }
            )
        ],
    )

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.primary.used_percent == 6
    assert snap.secondary.used_percent == 36
    assert snap.quota_source == 'codex.rate_limits'
    assert snap.note == ''
    assert snap.quota_file == logs


def test_ignores_spark_logged_rate_limits_when_session_has_general_limit(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'a.jsonl'
    logs = codex_home / 'logs_2.sqlite'
    _write_jsonl(session, [_token_count(12, 34, limit_id='codex')])
    _write_logs_db(
        logs,
        [
            'Received message '
            + json.dumps(
                {
                    'type': 'codex.rate_limits',
                    'rate_limits': {
                        'allowed': True,
                        'limit_reached': False,
                        'limit_id': 'codex_bengalfox',
                        'primary': {'used_percent': 0, 'reset_at': 1781813603, 'window_minutes': 300},
                        'secondary': {'used_percent': 0, 'reset_at': 1782363500, 'window_minutes': 10080},
                    },
                }
            )
        ],
    )

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.primary.used_percent == 12
    assert snap.secondary.used_percent == 34
    assert snap.quota_file == session


def test_ignores_top_level_spark_logged_rate_limits_when_session_has_general_limit(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'a.jsonl'
    logs = codex_home / 'logs_2.sqlite'
    _write_jsonl(session, [_token_count(12, 34, limit_id='codex')])
    _write_logs_db(
        logs,
        [
            'Received message '
            + json.dumps(
                {
                    'type': 'codex.rate_limits',
                    'limit_id': 'codex_bengalfox',
                    'rate_limits': {
                        'allowed': True,
                        'limit_reached': False,
                        'primary': {'used_percent': 0, 'reset_at': 1781813603, 'window_minutes': 300},
                        'secondary': {'used_percent': 0, 'reset_at': 1782363500, 'window_minutes': 10080},
                    },
                }
            )
        ],
    )

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.primary.used_percent == 12
    assert snap.secondary.used_percent == 34
    assert snap.quota_file == session


def test_skips_transient_all_zero_logged_rate_limits(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'a.jsonl'
    logs = codex_home / 'logs_2.sqlite'
    _write_jsonl(session, [_token_count(12, 34, limit_name='session')])
    _write_logs_db(
        logs,
        [
            'Received message '
            + json.dumps(
                {
                    'type': 'codex.rate_limits',
                    'rate_limits': {
                        'allowed': True,
                        'limit_reached': False,
                        'limit_id': 'codex',
                        'primary': {'used_percent': 79, 'reset_at': 1781813603, 'window_minutes': 300},
                        'secondary': {'used_percent': 31, 'reset_at': 1782363500, 'window_minutes': 10080},
                    },
                }
            ),
            'Received message '
            + json.dumps(
                {
                    'type': 'codex.rate_limits',
                    'rate_limits': {
                        'allowed': True,
                        'limit_reached': False,
                        'limit_id': 'codex',
                        'primary': {'used_percent': 0, 'reset_at': 1781813603, 'window_minutes': 300},
                        'secondary': {'used_percent': 0, 'reset_at': 1782363500, 'window_minutes': 10080},
                    },
                }
            ),
        ],
    )

    snap = CodexQuotaReader(sessions).read_quota()

    assert snap.primary.used_percent == 79
    assert snap.secondary.used_percent == 31
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


def test_reuses_logged_rate_limits_after_same_account_auth_refresh(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'current-account.jsonl'
    logs = codex_home / 'logs_2.sqlite'
    auth = codex_home / 'auth.json'
    _write_auth(auth, 'account-a')
    _write_jsonl(session, [{'payload': {'type': 'task_started'}}])
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
    os.utime(auth, (1000, 1000))
    os.utime(session, (1500, 1500))
    os.utime(logs, (1500, 1500))
    reader = CodexQuotaReader(sessions, tmp_path / 'quota_cache.json')

    first = reader.read_quota()
    assert first.primary.used_percent == 44
    assert first.quota_file == logs

    _write_auth(auth, 'account-a')
    os.utime(auth, (2000, 2000))

    second = reader.read_quota()

    assert second.primary.used_percent == 44
    assert second.secondary.used_percent == 55
    assert second.note == '额度沿用上次读取'
    assert second.quota_file == logs


def test_reuses_last_quota_after_same_account_auth_refresh(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'current-account.jsonl'
    auth = codex_home / 'auth.json'
    _write_auth(auth, 'account-a')
    _write_jsonl(session, [_token_count(42, 58)])
    os.utime(auth, (1000, 1000))
    os.utime(session, (1500, 1500))
    reader = CodexQuotaReader(sessions, tmp_path / 'quota_cache.json')

    first = reader.read_quota()
    assert first.primary.used_percent == 42
    assert first.note == ''

    _write_auth(auth, 'account-a')
    os.utime(auth, (2000, 2000))

    second = reader.read_quota()

    assert second.primary.used_percent == 42
    assert second.secondary.used_percent == 58
    assert second.note == '额度沿用上次读取'
    assert second.quota_file == session


def test_does_not_reuse_last_quota_after_account_change(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'old-account.jsonl'
    auth = codex_home / 'auth.json'
    _write_auth(auth, 'account-a')
    _write_jsonl(session, [_token_count(42, 58)])
    os.utime(auth, (1000, 1000))
    os.utime(session, (1500, 1500))
    reader = CodexQuotaReader(sessions, tmp_path / 'quota_cache.json')

    first = reader.read_quota()
    assert first.primary.used_percent == 42

    _write_auth(auth, 'account-b')
    os.utime(auth, (2000, 2000))

    second = reader.read_quota()

    assert second.primary.used_percent is None
    assert second.secondary.used_percent is None
    assert second.note == '未读取到当前账号 token_count'
    assert second.quota_file is None


def test_reuses_persisted_quota_cache_after_same_account_auth_refresh(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'current-account.jsonl'
    auth = codex_home / 'auth.json'
    cache = tmp_path / 'quota_cache.json'
    _write_auth(auth, 'account-a')
    _write_jsonl(session, [_token_count(42, 58)])
    os.utime(auth, (1000, 1000))
    os.utime(session, (1500, 1500))

    first = CodexQuotaReader(sessions, cache).read_quota()
    assert first.primary.used_percent == 42
    assert cache.exists()

    _write_auth(auth, 'account-a')
    os.utime(auth, (2000, 2000))

    second = CodexQuotaReader(sessions, cache).read_quota()

    assert second.primary.used_percent == 42
    assert second.secondary.used_percent == 58
    assert second.note == '额度沿用上次读取'
    assert second.quota_file == session


def test_does_not_reuse_persisted_quota_cache_after_account_change(tmp_path: Path) -> None:
    codex_home = tmp_path / '.codex'
    sessions = codex_home / 'sessions'
    session = sessions / 'old-account.jsonl'
    auth = codex_home / 'auth.json'
    cache = tmp_path / 'quota_cache.json'
    _write_auth(auth, 'account-a')
    _write_jsonl(session, [_token_count(42, 58)])
    os.utime(auth, (1000, 1000))
    os.utime(session, (1500, 1500))

    first = CodexQuotaReader(sessions, cache).read_quota()
    assert first.primary.used_percent == 42

    _write_auth(auth, 'account-b')
    os.utime(auth, (2000, 2000))

    second = CodexQuotaReader(sessions, cache).read_quota()

    assert second.primary.used_percent is None
    assert second.secondary.used_percent is None
    assert second.note == '未读取到当前账号 token_count'
    assert second.quota_file is None


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
