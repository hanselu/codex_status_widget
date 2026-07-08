from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .config import CONFIG_DIR
from .models import QuotaSnapshot, QuotaWindow


LIMIT_PATTERNS = (
    re.compile(r'\bcool\s*down\b', re.I),
    re.compile(r'\brate[-_\s]*limited\b', re.I),
    re.compile(r'\brate\s+limit(?:ed)?\b', re.I),
    re.compile(r'\busage\s+limit\b', re.I),
    re.compile(r'\bquota\b', re.I),
    re.compile(r'\blimit\s+(?:reached|exceeded)\b', re.I),
    re.compile(r'\btoo\s+many\s+requests\b', re.I),
    re.compile(r'\b429\b', re.I),
)

ERRORISH_TYPE_KEYWORDS = (
    'error',
    'failed',
    'failure',
    'limit',
    'quota',
    'cooldown',
)

MESSAGE_KEYS = {
    'message',
    'error',
    'detail',
    'details',
    'reason',
    'text',
    'content',
    'body',
    'description',
}

LOG_ROWS_TO_SCAN = 2000
QUOTA_CACHE_PATH = CONFIG_DIR / 'quota_cache.json'
# Codex writes separate token_count pools. The desktop quota widget should show
# the general Codex pool, not model-specific pools such as Codex-Spark.
PREFERRED_LIMIT_ID = 'codex'


class CodexQuotaReader:
    """Read Codex quota from local session JSONL files.

    This follows the same data source verified by QuotaGem: recent
    `payload.type == "token_count"` events under `~/.codex/sessions`.
    The reader intentionally does not derive working/idle state from these
    files; that is handled by hook_state.py.
    """

    def __init__(self, sessions_dir: Path, cache_path: Path | None = None) -> None:
        self.sessions_dir = Path(sessions_dir).expanduser()
        self.cache_path = Path(cache_path).expanduser() if cache_path is not None else QUOTA_CACHE_PATH
        self._last_quota_event: dict[str, Any] | None = None
        self._last_quota_file: Path | None = None
        self._last_quota_account_id: str | None = None

    def read_quota(self) -> QuotaSnapshot:
        latest_file = self._find_latest_jsonl()
        if latest_file is None:
            return QuotaSnapshot(
                primary=QuotaWindow(),
                secondary=QuotaWindow(),
                note=f'未找到 session 文件：{self.sessions_dir}',
            )

        event, quota_file, note = self._find_latest_logged_rate_limits()
        if event is not None:
            self._remember_quota(event, quota_file, self._auth_account_id())
        else:
            event, quota_file, note = self._find_quota_event_with_fallback(latest_file)

        primary, secondary = self._extract_quota(event)
        quota_source = _quota_source_text(event)
        has_limit_signal = _rate_limit_reached(event)
        if not _file_is_older_than(latest_file, self._auth_state_mtime()):
            has_limit_signal = has_limit_signal or self._has_recent_limit_signal(latest_file)
        return QuotaSnapshot(
            primary=primary,
            secondary=secondary,
            latest_file=latest_file,
            quota_file=quota_file,
            quota_source=quota_source,
            has_limit_signal=has_limit_signal,
            note=note,
        )

    def _find_latest_jsonl(self) -> Path | None:
        files = self._find_latest_jsonl_files(limit=1)
        return files[0] if files else None

    def _find_latest_jsonl_files(self, limit: int = 30) -> list[Path]:
        if not self.sessions_dir.exists():
            return []

        files: list[tuple[float, Path]] = []
        try:
            candidates = self.sessions_dir.rglob('*.jsonl')
            for path in candidates:
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                files.append((mtime, path))
        except OSError:
            return []

        files.sort(key=lambda item: item[0], reverse=True)
        return [path for _, path in files[:limit]]

    def _find_quota_event_with_fallback(
        self, latest_file: Path
    ) -> tuple[dict[str, Any] | None, Path | None, str]:
        auth_mtime = self._auth_state_mtime()
        account_id = self._auth_account_id()
        if _file_is_older_than(latest_file, auth_mtime):
            cached = self._reusable_quota(account_id)
            if cached is not None:
                return cached[0], cached[1], '额度沿用上次读取'
            return None, None, '未读取到当前账号 token_count'

        latest_event = self._find_latest_token_count(latest_file, preferred_only=True)
        if latest_event is not None:
            self._remember_quota(latest_event, latest_file, account_id)
            return latest_event, latest_file, ''

        latest_any_event = self._find_latest_token_count(latest_file, preferred_only=False)
        skipped_pre_auth_session = False
        fallback_event: dict[str, Any] | None = latest_any_event
        fallback_file: Path | None = latest_file if latest_any_event is not None else None
        for path in self._find_latest_jsonl_files(limit=30):
            if path == latest_file:
                continue
            if _file_is_older_than(path, auth_mtime):
                skipped_pre_auth_session = True
                continue

            event = self._find_latest_token_count(path, preferred_only=True)
            if event is None:
                if fallback_event is None:
                    any_event = self._find_latest_token_count(path, preferred_only=False)
                    if any_event is not None:
                        fallback_event = any_event
                        fallback_file = path
                continue

            self._remember_quota(event, path, account_id)
            return event, path, '额度来自最近记录'

        if fallback_event is not None:
            self._remember_quota(fallback_event, fallback_file, account_id)
            return fallback_event, fallback_file, '未读取到主额度，显示其他额度池'

        if self._last_quota_event is not None:
            if _file_is_older_than(self._last_quota_file, auth_mtime) and not self._can_reuse_last_quota(
                account_id
            ):
                return None, None, '未读取到当前账号 token_count'
            return self._last_quota_event, self._last_quota_file, '额度沿用上次读取'

        cached = self._reusable_quota(account_id)
        if cached is not None:
            return cached[0], cached[1], '额度沿用上次读取'

        if skipped_pre_auth_session:
            return None, None, '未读取到当前账号 token_count'

        return None, None, '未读取到 token_count'

    def _auth_state_mtime(self) -> float | None:
        auth_path = self.sessions_dir.parent / 'auth.json'
        try:
            return auth_path.stat().st_mtime
        except OSError:
            return None

    def _auth_account_id(self) -> str | None:
        auth_path = self.sessions_dir.parent / 'auth.json'
        try:
            data = json.loads(auth_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None

        tokens = data.get('tokens') if isinstance(data, dict) else None
        if not isinstance(tokens, dict):
            return None

        account_id = tokens.get('account_id')
        return account_id.strip() if isinstance(account_id, str) and account_id.strip() else None

    def _remember_quota(
        self, event: dict[str, Any], quota_file: Path | None, account_id: str | None
    ) -> None:
        self._last_quota_event = event
        self._last_quota_file = quota_file
        self._last_quota_account_id = account_id
        self._write_quota_cache(event, quota_file, account_id)

    def _can_reuse_last_quota(self, account_id: str | None) -> bool:
        return (
            self._last_quota_event is not None
            and account_id is not None
            and account_id == self._last_quota_account_id
        )

    def _reusable_quota(self, account_id: str | None) -> tuple[dict[str, Any], Path | None] | None:
        if self._can_reuse_last_quota(account_id) and self._last_quota_event is not None:
            return self._last_quota_event, self._last_quota_file

        cached = self._read_quota_cache(account_id)
        if cached is None:
            return None

        event, quota_file = cached
        self._last_quota_event = event
        self._last_quota_file = quota_file
        self._last_quota_account_id = account_id
        return event, quota_file

    def _write_quota_cache(
        self, event: dict[str, Any], quota_file: Path | None, account_id: str | None
    ) -> None:
        if account_id is None:
            return

        cache_event = _cacheable_quota_event(event)
        if cache_event is None:
            return

        data = {
            'version': 1,
            'account_id': account_id,
            'quota_file': str(quota_file) if quota_file is not None else '',
            'event': cache_event,
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except OSError:
            return

    def _read_quota_cache(self, account_id: str | None) -> tuple[dict[str, Any], Path | None] | None:
        if account_id is None:
            return None

        try:
            data = json.loads(self.cache_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(data, dict) or data.get('account_id') != account_id:
            return None

        event = data.get('event')
        if not isinstance(event, dict) or _extract_rate_limits(event) is None:
            return None

        quota_file_raw = data.get('quota_file')
        quota_file = Path(quota_file_raw) if isinstance(quota_file_raw, str) and quota_file_raw else None
        return event, quota_file

    def _find_latest_logged_rate_limits(self) -> tuple[dict[str, Any] | None, Path | None, str]:
        auth_mtime = self._auth_state_mtime()
        latest: tuple[float, dict[str, Any], Path] | None = None
        for path in self._log_db_paths():
            if _file_is_older_than(path, auth_mtime):
                continue
            result = _read_latest_logged_rate_limits(path)
            if result is None:
                continue
            event, event_ts = result
            if event_ts is not None and auth_mtime is not None and event_ts < auth_mtime:
                continue
            sort_ts = event_ts if event_ts is not None else _path_mtime(path)
            if latest is None or sort_ts > latest[0]:
                latest = (sort_ts, event, path)

        if latest is None:
            return None, None, ''
        return latest[1], latest[2], ''

    def _log_db_paths(self) -> list[Path]:
        codex_home = self.sessions_dir.parent
        return [
            codex_home / 'sqlite' / 'logs_2.sqlite',
            codex_home / 'logs_2.sqlite',
        ]

    def _find_latest_token_count(self, path: Path, *, preferred_only: bool) -> dict[str, Any] | None:
        for line in _iter_lines_reversed(path, max_lines=2000):
            event = _loads_json_line(line)
            if not isinstance(event, dict):
                continue
            if _is_token_count_event(event):
                if preferred_only and not _is_preferred_quota_event(event):
                    continue
                return event
        return None

    def _extract_quota(self, event: dict[str, Any] | None) -> tuple[QuotaWindow, QuotaWindow]:
        if not event:
            return QuotaWindow(), QuotaWindow()

        payload = event.get('payload') if isinstance(event.get('payload'), dict) else event
        rate_limits = payload.get('rate_limits') if isinstance(payload, dict) else None
        if not isinstance(rate_limits, dict):
            return QuotaWindow(), QuotaWindow()

        primary = _parse_quota_window(rate_limits.get('primary'))
        secondary = _parse_quota_window(rate_limits.get('secondary'))
        return primary, secondary

    def _has_recent_limit_signal(self, path: Path) -> bool:
        inspected_after_token_count = 0
        for line in _iter_lines_reversed(path, max_lines=300):
            event = _loads_json_line(line)
            if not isinstance(event, dict):
                continue

            if _is_token_count_event(event):
                break

            inspected_after_token_count += 1
            if _event_has_limit_signal(event):
                return True

            if inspected_after_token_count >= 80:
                break

        return False


def quota_is_exhausted(window: QuotaWindow, now: datetime) -> bool:
    if window.used_percent is None:
        return False
    if window.used_percent < 100:
        return False
    if window.resets_at is None:
        return True
    return window.resets_at.astimezone() > now


def quota_text(window: QuotaWindow) -> str:
    if window.used_percent is None:
        return '未读取'
    remain = format_percent(window.remaining_percent if window.remaining_percent is not None else 0)
    return remain


def reset_text(primary: QuotaWindow, secondary: QuotaWindow) -> str:
    primary_text = format_reset_time(primary.resets_at)
    secondary_text = format_reset_time(secondary.resets_at, with_date=True)
    if primary_text == '未知' and secondary_text == '未知':
        return '未知'
    return f'{primary_text} / {secondary_text}'


def format_percent(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return f'{round(value):.0f}%'
    return f'{value:.1f}%'


def format_reset_time(value: datetime | None, with_date: bool = False) -> str:
    if value is None:
        return '未知'

    local = value.astimezone()
    now = datetime.now().astimezone()
    if local.date() == now.date() and not with_date:
        return local.strftime('%H:%M')

    return f'{local.month}-{local.day} {local:%H:%M}'


def _is_token_count_event(event: dict[str, Any]) -> bool:
    payload = event.get('payload')
    if isinstance(payload, dict) and payload.get('type') == 'token_count':
        return True
    return event.get('type') == 'token_count'


def _event_has_limit_signal(event: dict[str, Any]) -> bool:
    event_type = _event_type_text(event)
    message_text = ' '.join(_collect_message_text(event))
    haystack = f'{event_type}\n{message_text}'.strip()
    if not haystack:
        return False

    if not any(pattern.search(haystack) for pattern in LIMIT_PATTERNS):
        return False

    if _is_errorish_event_type(event_type):
        return True

    return bool(
        re.search(r'\b(?:cool\s*down|quota|usage\s+limit|too\s+many\s+requests|429)\b', haystack, re.I)
    )


def _event_type_text(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ('type', 'kind', 'name', 'status'):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)

    payload = event.get('payload')
    if isinstance(payload, dict):
        for key in ('type', 'kind', 'name', 'status'):
            value = payload.get(key)
            if isinstance(value, str):
                parts.append(value)

    return ' '.join(parts).lower()


def _is_errorish_event_type(event_type: str) -> bool:
    lowered = event_type.lower()
    return any(keyword in lowered for keyword in ERRORISH_TYPE_KEYWORDS)


def _collect_message_text(value: Any, *, parent_key: str = '', depth: int = 0) -> list[str]:
    if depth > 5:
        return []

    if isinstance(value, str):
        if parent_key in MESSAGE_KEYS:
            return [value]
        return []

    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_collect_message_text(item, parent_key=parent_key, depth=depth + 1))
        return collected

    if isinstance(value, dict):
        collected = []
        for key, item in value.items():
            key_text = str(key)
            if key_text in {'rate_limits', 'primary', 'secondary'}:
                continue
            collected.extend(_collect_message_text(item, parent_key=key_text, depth=depth + 1))
        return collected

    return []


def _parse_quota_window(raw: Any) -> QuotaWindow:
    if not isinstance(raw, dict):
        return QuotaWindow()

    used_percent = _as_float(raw.get('used_percent'))
    resets_at = _parse_datetime(raw.get('resets_at') or raw.get('reset_at'))
    window_minutes = _as_int(raw.get('window_minutes'))
    return QuotaWindow(used_percent=used_percent, resets_at=resets_at, window_minutes=window_minutes)


def _quota_source_text(event: dict[str, Any] | None) -> str:
    if event and isinstance(event.get('_quota_source'), str):
        return event['_quota_source']

    rate_limits = _extract_rate_limits(event)
    if rate_limits is None:
        return ''

    for key in ('limit_name', 'plan_type', 'limit_id'):
        value = rate_limits.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _extract_rate_limits(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not event:
        return None

    payload = event.get('payload') if isinstance(event.get('payload'), dict) else event
    rate_limits = payload.get('rate_limits') if isinstance(payload, dict) else None
    return rate_limits if isinstance(rate_limits, dict) else None


def _cacheable_quota_event(event: dict[str, Any]) -> dict[str, Any] | None:
    rate_limits = _extract_rate_limits(event)
    if rate_limits is None:
        return None

    cached = {
        'type': 'token_count',
        'rate_limits': rate_limits,
    }
    if isinstance(event.get('_quota_source'), str):
        cached['_quota_source'] = event['_quota_source']
    return cached


def _quota_limit_id(event: dict[str, Any] | None) -> str:
    rate_limits = _extract_rate_limits(event)
    if rate_limits is None:
        return ''
    value = rate_limits.get('limit_id')
    return value.strip() if isinstance(value, str) else ''


def _is_preferred_quota_event(event: dict[str, Any]) -> bool:
    limit_id = _quota_limit_id(event)
    return limit_id in {'', PREFERRED_LIMIT_ID}


def _rate_limit_reached(event: dict[str, Any] | None) -> bool:
    rate_limits = _extract_rate_limits(event)
    if rate_limits is None:
        return False
    return bool(rate_limits.get('limit_reached') or rate_limits.get('rate_limit_reached_type'))


def _read_latest_logged_rate_limits(path: Path) -> tuple[dict[str, Any], float | None] | None:
    if not path.exists():
        return None

    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    except sqlite3.Error:
        return None

    try:
        try:
            rows = con.execute(
                'select ts, feedback_log_body from logs order by id desc limit ?',
                (LOG_ROWS_TO_SCAN,),
            ).fetchall()
        except sqlite3.Error:
            rows = con.execute(
                'select null, feedback_log_body from logs order by id desc limit ?',
                (LOG_ROWS_TO_SCAN,),
            ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()

    for row_ts, body in rows:
        if not isinstance(body, str) or 'codex.rate_limits' not in body:
            continue
        event = _parse_logged_websocket_event(body)
        if event is None or event.get('type') != 'codex.rate_limits':
            continue
        rate_limits = event.get('rate_limits')
        if not isinstance(rate_limits, dict):
            continue
        quota_event = {
            'type': 'token_count',
            'rate_limits': rate_limits,
            '_quota_source': 'codex.rate_limits',
        }
        result = quota_event, _as_float(row_ts)
        if _is_preferred_quota_event(quota_event):
            return result

    return None


def _parse_logged_websocket_event(body: str) -> dict[str, Any] | None:
    raw = ''
    if body.startswith('Received message '):
        raw = body.removeprefix('Received message ').strip()
    else:
        for marker in ('websocket event: ', 'SSE event: '):
            if marker in body:
                raw = body.split(marker, 1)[1].strip()
                break
    if not raw:
        return None

    event = _loads_json_line(raw)
    return event if isinstance(event, dict) else None


def _file_is_older_than(path: Path | None, mtime: float | None) -> bool:
    if path is None or mtime is None:
        return False
    try:
        return path.stat().st_mtime < mtime
    except OSError:
        return False


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
        except (OSError, ValueError):
            return None

    if not isinstance(value, str):
        return None

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


def _loads_json_line(line: str) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _iter_lines_reversed(path: Path, max_lines: int = 1000) -> Iterable[str]:
    try:
        with path.open('r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return []

    count = 0
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        yield stripped
        count += 1
        if count >= max_lines:
            break
