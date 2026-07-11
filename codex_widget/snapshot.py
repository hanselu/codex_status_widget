from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import time

from .approval_state import PendingApproval, read_pending_approvals
from .hook_installer import HookSetupStatus
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
        codex_app_running: Callable[[], bool | None] | None = None,
        hook_setup_status_reader: Callable[[], HookSetupStatus] | None = None,
    ) -> None:
        self.quota_reader = CodexQuotaReader(sessions_dir)
        self.hook_reader = HookStateReader(
            hook_events_path,
            stale_after_minutes=hook_stale_after_minutes,
            max_events_to_read=hook_max_events_to_read,
        )
        self.fallback_working_window_seconds = fallback_working_window_seconds
        self._codex_app_running = codex_app_running
        self._hook_setup_status_reader = hook_setup_status_reader

    def read_snapshot(self) -> CodexSnapshot:
        now = datetime.now().astimezone()
        quota = self.quota_reader.read_quota()
        hook_signal = self.hook_reader.read_signal()
        pending_approvals = read_pending_approvals(
            self.quota_reader.sessions_dir,
            stale_after_minutes=self.hook_reader.stale_after_minutes,
        )
        hook_signal = _merge_pending_approvals(hook_signal, pending_approvals)
        codex_app_running = self._read_codex_app_running()
        hook_setup_note = _visible_hook_setup_note(self._read_hook_setup_status())

        status = self._resolve_status(quota, hook_signal, now, codex_app_running)
        app_note = 'ChatGPT App 未运行' if codex_app_running is False else ''
        hook_note = _visible_hook_note(hook_signal)
        hook_detail = _visible_hook_detail(hook_signal)
        notes = _split_notes([app_note, hook_setup_note, hook_note, quota.note])
        note = '\n'.join(_dedupe_preserve_order(notes))
        detail_notes = _split_notes([app_note, hook_setup_note, hook_detail, quota.note])
        detail = '\n'.join(_dedupe_preserve_order(detail_notes))

        return CodexSnapshot(
            status=status,
            status_text=self._status_text(status),
            primary=quota.primary,
            secondary=quota.secondary,
            primary_text=f'5小时：{quota_text(quota.primary)} {format_reset_time(quota.primary.resets_at)}',
            secondary_text=f'周额度：{quota_text(quota.secondary)} {format_reset_time(quota.secondary.resets_at, with_date=True)}',
            reset_text='',
            updated_text=f'{now:%H:%M:%S}',
            note=note,
            detail=detail,
            latest_file=quota.latest_file,
            quota_file=quota.quota_file,
            hook_signal=hook_signal,
            codex_app_running=codex_app_running,
        )

    def mark_idle(self) -> None:
        self.hook_reader.mark_idle()

    def _read_codex_app_running(self) -> bool | None:
        if self._codex_app_running is None:
            return None
        return self._codex_app_running()

    def _read_hook_setup_status(self) -> HookSetupStatus | None:
        if self._hook_setup_status_reader is None:
            return None
        return self._hook_setup_status_reader()

    def _resolve_status(  # noqa: ANN001
        self, quota, hook_signal: HookSignal, now: datetime, codex_app_running: bool | None
    ) -> StatusName:
        if codex_app_running is False:
            return 'offline'

        if (
            quota.has_limit_signal
            or quota_is_exhausted(quota.primary, now)
            or quota_is_exhausted(quota.secondary, now)
        ):
            return 'cooldown'

        if hook_signal.status in {'waiting', 'working'}:
            return hook_signal.status
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
            'waiting': '待确认',
            'cooldown': '无额度',
            'offline': '未运行',
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


def _visible_hook_note(hook_signal: HookSignal) -> str:
    if hook_signal.status == 'idle':
        return ''
    return hook_signal.note


def _visible_hook_detail(hook_signal: HookSignal) -> str:
    if hook_signal.status == 'idle':
        return ''
    return hook_signal.detail or hook_signal.note


def _visible_hook_setup_note(status: HookSetupStatus | None) -> str:
    if status is None or status.is_complete:
        return ''

    parts: list[str] = []
    if not status.hooks_file_exists:
        parts.append('未找到 hooks.json')
    if not status.writer_exists:
        parts.append('hook_writer.py 不存在')
    if status.missing_events:
        parts.append('缺少 ' + '、'.join(status.missing_events))
    if not parts:
        parts.append('需要重新添加钩子')

    return '钩子配置不完整：' + '，'.join(parts)


def _merge_pending_approvals(hook_signal: HookSignal, approvals: list[PendingApproval]) -> HookSignal:
    if not approvals or hook_signal.status == 'waiting':
        return hook_signal

    latest = approvals[0]
    working_count = hook_signal.working_count
    if hook_signal.status == 'working' and working_count == 0:
        working_count = 1

    parts = [f'待确认 × {len(approvals)}']
    if working_count:
        parts.append(f'工作 × {working_count}')

    detail_parts = [_format_pending_approval_detail(approvals)]
    if hook_signal.detail:
        detail_parts.append(hook_signal.detail)
    elif hook_signal.note and hook_signal.status == 'working':
        detail_parts.append(hook_signal.note)

    return HookSignal(
        status='waiting',
        last_event_name='TranscriptApprovalRequest',
        last_event_at=latest.requested_at,
        session_id=latest.session_id,
        turn_id=latest.turn_id,
        cwd=latest.cwd,
        model=hook_signal.model,
        note=' · '.join(parts),
        detail='\n\n'.join(part for part in detail_parts if part),
        working_count=working_count,
        waiting_count=len(approvals),
        events_path=hook_signal.events_path,
    )


def _format_pending_approval_detail(approvals: list[PendingApproval]) -> str:
    lines = ['待确认']
    for approval in approvals:
        parts = [_approval_label(approval)]
        parts.append(f'{approval.requested_at.astimezone():%H:%M:%S}')
        if approval.tool_name:
            parts.append(approval.tool_name)
        lines.append('- ' + ' · '.join(parts))
    return '\n'.join(lines)


def _approval_label(approval: PendingApproval) -> str:
    if approval.cwd:
        return Path(approval.cwd).name or approval.cwd
    if approval.session_id:
        return approval.session_id[:8]
    return '未知对话'


def _split_notes(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        for part in item.split('；'):
            stripped = part.strip()
            if stripped:
                result.append(stripped)
    return result
