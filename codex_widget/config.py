from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


CONFIG_DIR = Path.home() / '.codex_widget'
CONFIG_PATH = CONFIG_DIR / 'config.toml'
HOOK_EVENTS_PATH = CONFIG_DIR / 'hook_events.jsonl'
HOOK_WRITER_PATH = CONFIG_DIR / 'hook_writer.py'
CODEX_HOOKS_PATH = Path.home() / '.codex' / 'hooks.json'


@dataclass(slots=True)
class CodexConfig:
    sessions_dir: Path


@dataclass(slots=True)
class HookConfig:
    events_path: Path
    stale_after_minutes: int = 360
    max_events_to_read: int = 5000


@dataclass(slots=True)
class UiConfig:
    x: int | None = None
    y: int | None = None
    width: int = 220
    height: int = 132
    opacity: float = 0.92
    locked: bool = False


@dataclass(slots=True)
class StatusConfig:
    # Fallback only. Hook status is preferred when hook events exist.
    working_window_seconds: int = 60
    refresh_interval_seconds: int = 5


@dataclass(slots=True)
class ScreenConfig:
    enabled: bool = False
    port: str = ''


@dataclass(slots=True)
class AppConfig:
    codex: CodexConfig
    hook: HookConfig
    ui: UiConfig
    status: StatusConfig
    screen: ScreenConfig = field(default_factory=ScreenConfig)

    @classmethod
    def default(cls) -> 'AppConfig':
        return cls(
            codex=CodexConfig(sessions_dir=Path.home() / '.codex' / 'sessions'),
            hook=HookConfig(events_path=HOOK_EVENTS_PATH),
            ui=UiConfig(),
            status=StatusConfig(),
        )

    @classmethod
    def load(cls) -> 'AppConfig':
        config = cls.default()
        if not CONFIG_PATH.exists():
            config.save()
            return config

        try:
            with CONFIG_PATH.open('rb') as f:
                data = tomllib.load(f)
        except Exception:
            return config

        codex_data = data.get('codex', {}) if isinstance(data, dict) else {}
        hook_data = data.get('hook', {}) if isinstance(data, dict) else {}
        ui_data = data.get('ui', {}) if isinstance(data, dict) else {}
        status_data = data.get('status', {}) if isinstance(data, dict) else {}
        screen_data = data.get('screen', {}) if isinstance(data, dict) else {}

        sessions_dir_raw = codex_data.get('sessions_dir')
        if isinstance(sessions_dir_raw, str) and sessions_dir_raw.strip():
            config.codex.sessions_dir = _expand_path(sessions_dir_raw)

        events_path_raw = hook_data.get('events_path')
        if isinstance(events_path_raw, str) and events_path_raw.strip():
            config.hook.events_path = _expand_path(events_path_raw)
        config.hook.stale_after_minutes = _int_in_range(
            hook_data.get('stale_after_minutes'), config.hook.stale_after_minutes, 5, 1440
        )
        config.hook.max_events_to_read = _int_in_range(
            hook_data.get('max_events_to_read'), config.hook.max_events_to_read, 100, 100000
        )

        config.ui.x = _optional_int(ui_data.get('x'), config.ui.x)
        config.ui.y = _optional_int(ui_data.get('y'), config.ui.y)
        config.ui.width = _int_in_range(ui_data.get('width'), config.ui.width, 220, 800)
        config.ui.height = _int_in_range(ui_data.get('height'), config.ui.height, 110, 600)
        config.ui.opacity = _float_in_range(ui_data.get('opacity'), config.ui.opacity, 0.35, 1.0)
        config.ui.locked = _bool_value(ui_data.get('locked'), config.ui.locked)

        config.status.working_window_seconds = _int_in_range(
            status_data.get('working_window_seconds'), config.status.working_window_seconds, 5, 600
        )
        config.status.refresh_interval_seconds = _int_in_range(
            status_data.get('refresh_interval_seconds'), config.status.refresh_interval_seconds, 2, 300
        )
        if isinstance(screen_data, dict):
            config.screen.enabled = _bool_value(screen_data.get('enabled'), False)
            port = screen_data.get('port')
            if isinstance(port, str):
                config.screen.port = port.strip()
        return config

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        x_line = f'x = {self.ui.x}\n' if self.ui.x is not None else 'x = -1\n'
        y_line = f'y = {self.ui.y}\n' if self.ui.y is not None else 'y = -1\n'
        text = (
            '[codex]\n'
            f'sessions_dir = "{_toml_escape(str(self.codex.sessions_dir))}"\n'
            '\n'
            '[hook]\n'
            f'events_path = "{_toml_escape(str(self.hook.events_path))}"\n'
            f'stale_after_minutes = {self.hook.stale_after_minutes}\n'
            f'max_events_to_read = {self.hook.max_events_to_read}\n'
            '\n'
            '[ui]\n'
            f'{x_line}'
            f'{y_line}'
            f'width = {self.ui.width}\n'
            f'height = {self.ui.height}\n'
            f'opacity = {self.ui.opacity:.2f}\n'
            f'locked = {str(self.ui.locked).lower()}\n'
            '\n'
            '[status]\n'
            f'working_window_seconds = {self.status.working_window_seconds}\n'
            f'refresh_interval_seconds = {self.status.refresh_interval_seconds}\n'
            '\n'
            '[screen]\n'
            f'enabled = {str(self.screen.enabled).lower()}\n'
            f'port = "{_toml_escape(self.screen.port)}"\n'
        )
        CONFIG_PATH.write_text(text, encoding='utf-8')


def _expand_path(value: str) -> Path:
    expanded = value.replace('%USERPROFILE%', str(Path.home()))
    return Path(expanded).expanduser()


def _toml_escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


def _optional_int(value: object, default: int | None) -> int | None:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return None
    return parsed


def _int_in_range(value: object, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _float_in_range(value: object, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _bool_value(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1', 'yes', 'on'}:
            return True
        if normalized in {'false', '0', 'no', 'off'}:
            return False
    return default
