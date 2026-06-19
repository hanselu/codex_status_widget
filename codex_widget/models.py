from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


StatusName = Literal['idle', 'working', 'cooldown', 'offline']
HookSignalName = Literal['idle', 'working', 'unknown']


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
    events_path: Path | None = None


@dataclass(slots=True)
class CodexSnapshot:
    status: StatusName
    status_text: str
    primary: QuotaWindow
    secondary: QuotaWindow
    primary_text: str
    secondary_text: str
    reset_text: str
    updated_text: str
    note: str = ''
    latest_file: Path | None = None
    quota_file: Path | None = None
    hook_signal: HookSignal = field(default_factory=HookSignal)
    codex_app_running: bool | None = None
