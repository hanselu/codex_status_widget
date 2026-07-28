from __future__ import annotations

from datetime import timedelta, timezone
import json
from pathlib import Path

import pytest

from codex_widget.reset_credits import (
    ResetCreditsError,
    format_reset_credits,
    parse_reset_credits_response,
    read_access_token,
)


def test_reads_only_access_token_from_auth_file(tmp_path: Path) -> None:
    auth_path = tmp_path / 'auth.json'
    auth_path.write_text(
        json.dumps(
            {
                'tokens': {
                    'access_token': ' access-secret ',
                    'refresh_token': 'refresh-secret',
                    'account_id': 'account-secret',
                }
            }
        ),
        encoding='utf-8',
    )

    assert read_access_token(auth_path) == 'access-secret'


@pytest.mark.parametrize(
    'content',
    [
        '{',
        '{}',
        '{"tokens": {}}',
        '{"tokens": {"access_token": "  "}}',
    ],
)
def test_rejects_invalid_or_missing_access_token(tmp_path: Path, content: str) -> None:
    auth_path = tmp_path / 'auth.json'
    auth_path.write_text(content, encoding='utf-8')

    with pytest.raises(ResetCreditsError) as exc_info:
        read_access_token(auth_path)

    assert 'access-secret' not in str(exc_info.value)


def test_missing_auth_file_has_safe_error(tmp_path: Path) -> None:
    with pytest.raises(ResetCreditsError, match='未找到 Codex 凭证文件'):
        read_access_token(tmp_path / 'missing.json')


def test_invalid_auth_file_encoding_has_safe_error(tmp_path: Path) -> None:
    auth_path = tmp_path / 'auth.json'
    auth_path.write_bytes(b'\xff')

    with pytest.raises(ResetCreditsError, match='无法读取 Codex 凭证'):
        read_access_token(auth_path)


def test_formats_whitelisted_fields_and_converts_utc_to_local_time() -> None:
    raw = json.dumps(
        {
            'available_count': 1,
            'total_earned_count': 99,
            'credits': [
                {
                    'id': 'credit-secret',
                    'status': 'available',
                    'title': 'Full reset',
                    'granted_at': '2026-07-25T16:30:00Z',
                    'expires_at': '2026-08-24T16:30:00+00:00',
                    'profile_user_id': 'user-secret',
                    'description': 'hidden-description',
                }
            ],
        }
    ).encode()

    summary = parse_reset_credits_response(raw)
    message = format_reset_credits(summary, local_tz=timezone(timedelta(hours=8)))

    assert '可用重置额度：1' in message
    assert 'Full reset' in message
    assert '状态：available' in message
    assert '发放时间：2026-07-26 00:30:00' in message
    assert '过期时间：2026-08-25 00:30:00' in message
    assert '99' not in message
    assert 'credit-secret' not in message
    assert 'user-secret' not in message
    assert 'hidden-description' not in message


def test_formats_empty_credit_list() -> None:
    summary = parse_reset_credits_response(b'{"available_count": 0, "credits": []}')

    message = format_reset_credits(summary, local_tz=timezone.utc)

    assert '可用重置额度：0' in message
    assert '暂无重置额度记录。' in message


def test_bad_or_missing_credit_times_show_unknown() -> None:
    raw = b'{"available_count": 1, "credits": [{"status": "", "title": null, "granted_at": "bad"}]}'

    summary = parse_reset_credits_response(raw)
    message = format_reset_credits(summary, local_tz=timezone.utc)

    assert '1. 未知' in message
    assert '状态：未知' in message
    assert '发放时间：未知' in message
    assert '过期时间：未知' in message


@pytest.mark.parametrize(
    'raw',
    [
        b'not-json',
        b'[]',
        b'{}',
        b'{"available_count": true, "credits": []}',
        b'{"available_count": 1, "credits": {}}',
        b'{"available_count": 1, "credits": [null]}',
    ],
)
def test_rejects_unrecognized_response_shapes(raw: bytes) -> None:
    with pytest.raises(ResetCreditsError):
        parse_reset_credits_response(raw)
