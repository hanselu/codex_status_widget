from __future__ import annotations

from datetime import datetime
import io
import json
from pathlib import Path
import time

import pytest

from codex_widget.app_server_quota_reader import (
    AppServerQuotaError,
    AppServerQuotaReader,
    find_app_server_executable,
)


class _CapturingStdin(io.StringIO):
    def close(self) -> None:
        self.closed_by_reader = True


class _FakeProcess:
    def __init__(self, stdout_lines: list[str], stderr_lines: list[str] | None = None) -> None:
        self.stdin = _CapturingStdin()
        self.stdout = io.StringIO('\n'.join(stdout_lines) + ('\n' if stdout_lines else ''))
        self.stderr = io.StringIO('\n'.join(stderr_lines or []) + ('\n' if stderr_lines else ''))
        self.killed = False
        self.waited = False

    def poll(self) -> int | None:
        return -9 if self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return -9 if self.killed else 0


def _response(response_id: int, result: dict | None = None, error: dict | None = None) -> str:
    payload: dict = {'id': response_id}
    if error is not None:
        payload['error'] = error
    else:
        payload['result'] = result or {}
    return json.dumps(payload)


def _rate_limits_response() -> dict:
    return {
        'rateLimits': {
            'limitId': 'codex',
            'primary': {
                'usedPercent': 25.5,
                'windowDurationMins': 300,
                'resetsAt': 1786169234,
            },
            'secondary': {
                'usedPercent': 40,
                'windowDurationMins': 10080,
                'resetsAt': 1786170000,
            },
            'rateLimitReachedType': None,
        },
        'rateLimitsByLimitId': None,
        'rateLimitResetCredits': None,
    }


def _reader_with_process(process: _FakeProcess, timeout_seconds: float = 0.2) -> AppServerQuotaReader:
    return AppServerQuotaReader(
        executable=Path('codex.exe'),
        timeout_seconds=timeout_seconds,
        process_factory=lambda *args, **kwargs: process,
    )


def _sent_methods(process: _FakeProcess) -> list[str]:
    return [json.loads(line)['method'] for line in process.stdin.getvalue().splitlines()]


def test_discovers_plugin_app_server_before_sandbox_bin(tmp_path: Path) -> None:
    plugin = tmp_path / '.codex' / 'plugins' / '.plugin-appserver' / 'codex.exe'
    sandbox = tmp_path / '.codex' / '.sandbox-bin' / 'codex.exe'
    plugin.parent.mkdir(parents=True)
    sandbox.parent.mkdir(parents=True)
    plugin.write_text('', encoding='utf-8')
    sandbox.write_text('', encoding='utf-8')

    assert find_app_server_executable(tmp_path) == plugin


def test_reads_quota_snapshot_from_app_server_response() -> None:
    process = _FakeProcess(
        [
            _response(1),
            _response(2, {'account': {'id': 'account-redacted-in-reader'}}),
            _response(3, _rate_limits_response()),
        ],
        stderr_lines=['debug stderr noise'],
    )

    snap = _reader_with_process(process).read_quota()

    assert _sent_methods(process) == [
        'initialize',
        'initialized',
        'account/read',
        'account/rateLimits/read',
    ]
    assert snap.primary.used_percent == 25.5
    assert snap.primary.window_minutes == 300
    assert isinstance(snap.primary.resets_at, datetime)
    assert snap.secondary.used_percent == 40
    assert snap.secondary.window_minutes == 10080
    assert snap.quota_source == 'app-server'
    assert snap.has_limit_signal is False
    assert process.killed is True
    assert process.waited is True


def test_rate_limit_reached_type_sets_limit_signal() -> None:
    result = _rate_limits_response()
    result['rateLimits']['rateLimitReachedType'] = 'primary'
    process = _FakeProcess([_response(1), _response(2), _response(3, result)])

    snap = _reader_with_process(process).read_quota()

    assert snap.has_limit_signal is True


def test_start_failure_is_reported_safely() -> None:
    def fail_start(*args, **kwargs):
        raise OSError('cannot start bearer sk-testsecret1234567890 for user@example.com')

    reader = AppServerQuotaReader(
        executable=Path('codex.exe'),
        process_factory=fail_start,
    )

    with pytest.raises(AppServerQuotaError) as exc_info:
        reader.read_quota()

    message = str(exc_info.value)
    assert '启动失败' in message
    assert 'sk-testsecret' not in message
    assert 'user@example.com' not in message
    assert 'bearer' not in message.lower()


def test_json_rpc_error_is_redacted_and_process_is_cleaned_up() -> None:
    process = _FakeProcess(
        [
            _response(1),
            _response(2),
            _response(
                3,
                error={
                    'code': 401,
                    'message': 'auth failed for user@example.com bearer sess-secret1234567890',
                },
            ),
        ]
    )

    with pytest.raises(AppServerQuotaError) as exc_info:
        _reader_with_process(process).read_quota()

    message = str(exc_info.value)
    assert '协议错误 401' in message
    assert 'user@example.com' not in message
    assert 'sess-secret' not in message
    assert 'bearer' not in message.lower()
    assert process.killed is True
    assert process.waited is True


def test_initialize_error_is_safe_failure() -> None:
    process = _FakeProcess(
        [
            _response(1, error={'code': -32000, 'message': 'not initialized user@example.com'}),
        ]
    )

    with pytest.raises(AppServerQuotaError) as exc_info:
        _reader_with_process(process).read_quota()

    message = str(exc_info.value)
    assert '协议错误 -32000' in message
    assert 'user@example.com' not in message
    assert process.killed is True


def test_invalid_json_is_safe_failure_and_process_is_cleaned_up() -> None:
    process = _FakeProcess(['not-json'])

    with pytest.raises(AppServerQuotaError, match='无效 JSON'):
        _reader_with_process(process).read_quota()

    assert process.killed is True
    assert process.waited is True


def test_unrecognizable_response_structure_is_safe_failure() -> None:
    process = _FakeProcess([_response(1), _response(2), _response(3, {'rateLimits': None})])

    with pytest.raises(AppServerQuotaError, match='结构不可识别'):
        _reader_with_process(process).read_quota()

    assert process.killed is True


def test_timeout_is_safe_failure_and_process_is_cleaned_up() -> None:
    process = _FakeProcess([])
    started = time.monotonic()

    with pytest.raises(AppServerQuotaError, match='响应超时'):
        _reader_with_process(process, timeout_seconds=0.01).read_quota()

    assert time.monotonic() - started < 1
    assert process.killed is True
    assert process.waited is True


def test_process_exit_reports_redacted_stderr() -> None:
    class _ExitedProcess(_FakeProcess):
        def poll(self) -> int | None:
            return 1

    process = _ExitedProcess([], ['fatal user@example.com bearer sess-secret1234567890'])

    with pytest.raises(AppServerQuotaError) as exc_info:
        _reader_with_process(process).read_quota()

    message = str(exc_info.value)
    assert '已退出' in message
    assert 'user@example.com' not in message
    assert 'sess-secret' not in message
    assert 'bearer' not in message.lower()
