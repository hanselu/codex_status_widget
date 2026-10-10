from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


StatusName = Literal['idle', 'working', 'waiting', 'cooldown', 'offline']
HookSignalName = Literal['idle', 'working', 'waiting', 'unknown']


@dataclass(slots=True)
class QuotaWindow:
    used_percent: float | None = None
    resets_at: datetime | None = None
    window_minutes: int | None = None

    @property
    def remaining_percent(self) -> float | None:
        if self.used_percent is None:
            return None
        return max(0.0, 100.0 - self.used_percent)


@dataclass(slots=True)
class QuotaSnapshot:
    primary: QuotaWindow = field(default_factory=QuotaWindow)
    secondary: QuotaWindow = field(default_factory=QuotaWindow)
    latest_file: Path | None = None
    quota_file: Path | None = None
    quota_source: str = ''
    has_limit_signal: bool = False
    note: str = ''


@dataclass(slots=True)
class HookSignal:
    status: HookSignalName = 'unknown'
    last_event_name: str = ''
    last_event_at: datetime | None = None
    session_id: str = ''
    turn_id: str = ''
    cwd: str = ''
    model: str = ''
    note: str = ''
    detail: str = ''
    working_count: int = 0
    waiting_count: int = 0
    background_count: int = 0
    background_turn_ids: frozenset[str] = field(default_factory=frozenset)
    events_path: Path | None = None

    @property
    def background_summary(self) -> str:
        if not self.background_count:
            return ''
        suffix = f' × {self.background_count}' if self.background_count > 1 else ''
        return '后台正在整理记忆' + suffix


@dataclass(slots=True)
class CodexSnapshot:
    status: StatusName
    status_text: str
    primary_title: str
    primary: QuotaWindow
    primary_text: str
    primary_visible: bool
    secondary_title: str
    secondary: QuotaWindow
    secondary_text: str
    secondary_visible: bool
    reset_text: str
    updated_text: str
    note: str = ''
    detail: str = ''
    latest_file: Path | None = None
    quota_file: Path | None = None
    hook_signal: HookSignal = field(default_factory=HookSignal)
    codex_app_running: bool | None = None
