from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
import json
from pathlib import Path
from typing import Any


RESET_CREDITS_URL = 'https://chatgpt.com/backend-api/wham/rate-limit-reset-credits'
RESET_CREDITS_TIMEOUT_MS = 15_000


class ResetCreditsError(RuntimeError):
    """A safe, user-facing reset-credit query error."""


@dataclass(frozen=True, slots=True)
class ResetCredit:
    status: str
    title: str
    granted_at: str | None
    expires_at: str | None


@dataclass(frozen=True, slots=True)
class ResetCreditsSummary:
    available_count: int
    credits: tuple[ResetCredit, ...]


def read_access_token(auth_path: Path) -> str:
    try:
        payload = json.loads(Path(auth_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ResetCreditsError(f'未找到 Codex 凭证文件：{auth_path}') from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ResetCreditsError('无法读取 Codex 凭证，请确认 auth.json 文件有效。') from None

    tokens = payload.get('tokens') if isinstance(payload, dict) else None
    access_token = tokens.get('access_token') if isinstance(tokens, dict) else None
    if not isinstance(access_token, str) or not access_token.strip():
        raise ResetCreditsError('Codex 凭证中没有 access_token，请在 ChatGPT App 中重新登录。')
    return access_token.strip()


def parse_reset_credits_response(raw: bytes) -> ResetCreditsSummary:
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ResetCreditsError('查询接口返回了无法识别的数据。') from None

    if not isinstance(payload, dict):
        raise ResetCreditsError('查询接口返回了无法识别的数据。')

    available_count = payload.get('available_count')
    raw_credits = payload.get('credits')
    if (
        isinstance(available_count, bool)
        or not isinstance(available_count, int)
        or available_count < 0
        or not isinstance(raw_credits, list)
    ):
        raise ResetCreditsError('查询接口返回的数据格式已发生变化。')

    credits: list[ResetCredit] = []
    for raw_credit in raw_credits:
        if not isinstance(raw_credit, dict):
            raise ResetCreditsError('查询接口返回的数据格式已发生变化。')
        credits.append(
            ResetCredit(
                status=_text_field(raw_credit, 'status'),
                title=_text_field(raw_credit, 'title'),
                granted_at=_optional_text_field(raw_credit, 'granted_at'),
                expires_at=_optional_text_field(raw_credit, 'expires_at'),
            )
        )

    return ResetCreditsSummary(available_count=available_count, credits=tuple(credits))


def format_reset_credits(
    summary: ResetCreditsSummary,
    *,
    local_tz: tzinfo | None = None,
) -> str:
    lines = [
        f'可用重置额度：{summary.available_count}',
        '以下时间均已换算为本地时间。',
    ]
    if not summary.credits:
        lines.extend(['', '暂无重置额度记录。'])
        return '\n'.join(lines)

    for index, credit in enumerate(summary.credits, start=1):
        lines.extend(
            [
                '',
                f'{index}. {credit.title}',
                f'状态：{credit.status}',
                f'发放时间：{_format_local_time(credit.granted_at, local_tz)}',
                f'过期时间：{_format_local_time(credit.expires_at, local_tz)}',
            ]
        )
    return '\n'.join(lines)


def _text_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else '未知'


def _optional_text_field(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _format_local_time(value: str | None, local_tz: tzinfo | None) -> str:
    if value is None:
        return '未知'

    normalized = value[:-1] + '+00:00' if value.endswith(('Z', 'z')) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return '未知'
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(local_tz) if local_tz is not None else parsed.astimezone()
    return local.strftime('%Y-%m-%d %H:%M:%S')
