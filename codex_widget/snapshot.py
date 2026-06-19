from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time

from .hook_state import HookStateReader
from .models import CodexSnapshot, HookSignal, StatusName
from .quota_reader import CodexQuotaReader, format_reset_time, quota_is_exhausted, quota_text


class CodexSnapshotReader:
    def __init__(
        self,
        sessions_dir: Path,
        hook_events_path: Path,
        hook_stale_after_minutes: int = 360,
        hook_max_events_to_read: int = 5000,
        fallback_working_window_seconds: int = 60,
    ) -> None:
        self.quota_reader = CodexQuotaReader(sessions_dir)
        self.hook_reader = HookStateReader(
            hook_events_path,
            stale_after_minutes=hook_stale_after_minutes,
            max_events_to_read=hook_max_events_to_read,
        )
        self.fallback_working_window_seconds = fallback_working_window_seconds

    def read_snapshot(self) -> CodexSnapshot:
        now = datetime.now().astimezone()
        quota = self.quota_reader.read_quota()
        hook_signal = self.hook_reader.read_signal()

        status = self._resolve_status(quota, hook_signal, now)
        notes = _split_notes([hook_signal.note, quota.note])
        note = '\n'.join(_dedupe_preserve_order(notes))

        return CodexSnapshot(
            status=status,
            status_text=self._status_text(status),
            primary=quota.primary,
            secondary=quota.secondary,
            primary_text=f'5小时：{quota_text(quota.primary)} {format_reset_time(quota.primary.resets_at)}',
            secondary_text=f'周额度：{quota_text(quota.secondary)} {format_reset_time(quota.secondary.resets_at, with_weekday=True)}',
            reset_text='',
            updated_text=f'{now:%H:%M:%S}',
            note=note,
            latest_file=quota.latest_file,
            quota_file=quota.quota_file,
            hook_signal=hook_signal,
        )

    def mark_idle(self) -> None:
        self.hook_reader.mark_idle()

    def _resolve_status(self, quota, hook_signal: HookSignal, now: datetime) -> StatusName:  # noqa: ANN001
        if (
            quota.has_limit_signal
            or quota_is_exhausted(quota.primary, now)
            or quota_is_exhausted(quota.secondary, now)
        ):
            return 'cooldown'

        if hook_signal.status == 'working':
            return 'working'
        if hook_signal.status == 'idle':
            return 'idle'

        # Hook not installed/trusted yet: fall back to a conservative mtime check.
        if quota.latest_file is not None:
            try:
                age_seconds = time.time() - quota.latest_file.stat().st_mtime
            except OSError:
                age_seconds = float('inf')
            if age_seconds <= self.fallback_working_window_seconds:
                return 'working'

        return 'idle'

    @staticmethod
    def _status_text(status: StatusName) -> str:
        return {
            'idle': '闲置中',
            'working': '工作中',
            'cooldown': '等待CD',
        }[status]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _split_notes(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        for part in item.split('；'):
            stripped = part.strip()
            if stripped:
                result.append(stripped)
    return result
