from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, TextIO

from .models import QuotaSnapshot, QuotaWindow


APP_SERVER_RELATIVE_PATHS = (
    Path('.codex') / 'plugins' / '.plugin-appserver' / 'codex.exe',
    Path('.codex') / '.sandbox-bin' / 'codex.exe',
)
PREFERRED_LIMIT_ID = 'codex'


class AppServerQuotaError(RuntimeError):
    """Raised when app-server quota reading fails with a redacted message."""


class AppServerQuotaReader:
    def __init__(
        self,
        *,
        executable: Path | str | None = None,
        home: Path | None = None,
        timeout_seconds: float = 8.0,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.executable = Path(executable) if executable is not None else None
        self.home = Path(home).expanduser() if home is not None else Path.home()
        self.timeout_seconds = timeout_seconds
        self._process_factory = process_factory

    def read_quota(self) -> QuotaSnapshot:
        executable = self.executable or find_app_server_executable(self.home)
        if executable is None:
            raise AppServerQuotaError('未找到 codex app-server 可执行文件')

        client = _AppServerClient(
            executable=executable,
            timeout_seconds=self.timeout_seconds,
            process_factory=self._process_factory,
        )
        result = client.read_rate_limits()
        return _snapshot_from_response(result)


def find_app_server_executable(home: Path | None = None) -> Path | None:
    base = Path(home).expanduser() if home is not None else Path.home()
    for relative in APP_SERVER_RELATIVE_PATHS:
        candidate = base / relative
        if candidate.exists():
            return candidate

    found = shutil.which('codex')
    return Path(found) if found else None


class _AppServerClient:
    def __init__(
        self,
        *,
        executable: Path,
        timeout_seconds: float,
        process_factory: Callable[..., Any],
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._process_factory = process_factory

    def read_rate_limits(self) -> dict[str, Any]:
        process = self._start_process()
        output_queue: queue.Queue[str] = queue.Queue()
        stderr_queue: queue.Queue[str] = queue.Queue()
        stdout_thread = _start_reader_thread(process.stdout, output_queue)
        stderr_thread = _start_reader_thread(process.stderr, stderr_queue)
        try:
            self._send(process, {'method': 'initialize', 'id': 1, 'params': _initialize_params()})
            self._read_response(process, output_queue, stderr_queue, 1)
            self._send(process, {'method': 'initialized'})
            self._send(
                process,
                {'method': 'account/read', 'id': 2, 'params': {'refreshToken': False}},
            )
            self._read_response(process, output_queue, stderr_queue, 2)
            self._send(process, {'method': 'account/rateLimits/read', 'id': 3, 'params': None})
            response = self._read_response(process, output_queue, stderr_queue, 3)
            result = response.get('result')
            if not isinstance(result, dict):
                raise AppServerQuotaError('app-server 返回结构不可识别')
            return result
        finally:
            _close_process(process)
            _join_threads((stdout_thread, stderr_thread))

    def _start_process(self) -> Any:
        try:
            return self._process_factory(
                [str(self.executable), 'app-server', '--stdio'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError as exc:
            raise AppServerQuotaError(f'app-server 启动失败：{_redact(str(exc))}') from exc

    def _send(self, process: Any, payload: dict[str, Any]) -> None:
        stdin = process.stdin
        if stdin is None:
            raise AppServerQuotaError('app-server stdin 不可用')
        try:
            stdin.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n')
            stdin.flush()
        except OSError as exc:
            raise AppServerQuotaError(f'app-server 写入失败：{_redact(str(exc))}') from exc

    def _read_response(
        self,
        process: Any,
        output_queue: queue.Queue[str],
        stderr_queue: queue.Queue[str],
        response_id: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if _process_exited(process) and output_queue.empty():
                raise AppServerQuotaError(_process_exit_message(stderr_queue))
            try:
                line = output_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppServerQuotaError('app-server 返回无效 JSON') from exc

            if not isinstance(response, dict):
                raise AppServerQuotaError('app-server 返回结构不可识别')
            if response.get('id') != response_id:
                continue
            error = response.get('error')
            if error is not None:
                raise AppServerQuotaError(_json_rpc_error_message(error))
            return response

        raise AppServerQuotaError('app-server 响应超时')


def _initialize_params() -> dict[str, Any]:
    return {
        'clientInfo': {
            'name': 'codex-status-widget',
            'version': '0.0.0',
        },
        'capabilities': None,
    }


def _start_reader_thread(stream: TextIO | None, output_queue: queue.Queue[str]) -> threading.Thread:
    def read_lines() -> None:
        if stream is None:
            return
        try:
            for line in stream:
                stripped = line.strip()
                if stripped:
                    output_queue.put(stripped)
        except OSError as exc:
            output_queue.put(f'<read-error:{type(exc).__name__}>')

    thread = threading.Thread(target=read_lines, daemon=True)
    thread.start()
    return thread


def _close_process(process: Any) -> None:
    try:
        stdin = getattr(process, 'stdin', None)
        if stdin is not None:
            stdin.close()
    except OSError:
        pass

    if not _process_exited(process):
        try:
            process.kill()
        except OSError:
            return

    try:
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _join_threads(threads: Iterable[threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=0.2)


def _process_exited(process: Any) -> bool:
    try:
        return process.poll() is not None
    except AttributeError:
        return False


def _process_exit_message(stderr_queue: queue.Queue[str]) -> str:
    stderr = _drain_queue(stderr_queue)
    if stderr:
        return f'app-server 已退出：{_redact(stderr[0])}'
    return 'app-server 已退出'


def _json_rpc_error_message(error: Any) -> str:
    if isinstance(error, dict):
        code = error.get('code')
        message = error.get('message')
        if code is not None and message:
            return f'app-server 协议错误 {code}：{_redact(str(message))}'
        if code is not None:
            return f'app-server 协议错误 {code}'
        if message:
            return f'app-server 协议错误：{_redact(str(message))}'
    return 'app-server 协议错误'


def _drain_queue(source: queue.Queue[str]) -> list[str]:
    result: list[str] = []
    while True:
        try:
            result.append(source.get_nowait())
        except queue.Empty:
            return result


def _snapshot_from_response(result: dict[str, Any]) -> QuotaSnapshot:
    pool = _select_rate_limit_pool(result)
    if pool is None:
        raise AppServerQuotaError('app-server 返回结构不可识别')

    primary = _quota_window_from_app_server(pool.get('primary'))
    secondary = _quota_window_from_app_server(pool.get('secondary'))
    return QuotaSnapshot(
        primary=primary,
        secondary=secondary,
        quota_source='app-server',
        has_limit_signal=bool(pool.get('rateLimitReachedType')),
    )


def _select_rate_limit_pool(result: dict[str, Any]) -> dict[str, Any] | None:
    by_id = result.get('rateLimitsByLimitId')
    if isinstance(by_id, dict):
        preferred = by_id.get(PREFERRED_LIMIT_ID)
        if isinstance(preferred, dict):
            return preferred

    candidates = _rate_limit_candidates(result.get('rateLimits'))
    for candidate in candidates:
        if candidate.get('limitId') == PREFERRED_LIMIT_ID:
            return candidate

    return candidates[0] if candidates else None


def _rate_limit_candidates(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _quota_window_from_app_server(raw: Any) -> QuotaWindow:
    if not isinstance(raw, dict):
        return QuotaWindow()
    return QuotaWindow(
        used_percent=_as_float(raw.get('usedPercent')),
        resets_at=_parse_reset_time(raw.get('resetsAt')),
        window_minutes=_as_int(raw.get('windowDurationMins')),
    )


def _parse_reset_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
        except (OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone()
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _redact(value: str) -> str:
    redacted = value
    redacted = _redact_bearer(redacted)
    redacted = _redact_emails(redacted)
    redacted = _redact_account_ids(redacted)
    redacted = _redact_tokens(redacted)
    return redacted


def _redact_account_ids(value: str) -> str:
    return re.sub(r'\b(?:account|acct)_[A-Za-z0-9_-]{8,}\b', '<account>', value)


def _redact_bearer(value: str) -> str:
    return re.sub(r'(?i)bearer\s+[A-Za-z0-9._~+/=-]+', '<auth>', value)


def _redact_emails(value: str) -> str:
    return re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<email>', value)


def _redact_tokens(value: str) -> str:
    return re.sub(
        r'\b(?:sk|sess|eyJ)[A-Za-z0-9._~+/=-]{12,}\b',
        '<redacted>',
        value,
    )
