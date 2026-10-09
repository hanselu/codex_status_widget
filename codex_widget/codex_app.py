from __future__ import annotations

from collections.abc import Iterable
import csv
import io
import subprocess
import sys

from .app_server_quota_reader import QUOTA_PROCESS_IDS


CODEX_IMAGE_NAMES = frozenset({'chatgpt.exe', 'codex.exe'})


def codex_app_is_running(process_names: Iterable[str] | None = None) -> bool | None:
    if process_names is None:
        process_names = _read_windows_process_names()
        if process_names is None:
            return None

    # VS Code and CLI clients use codex.exe without the desktop application.
    return any(name.lower() in CODEX_IMAGE_NAMES for name in process_names)


def _read_windows_process_names() -> list[str] | None:
    if sys.platform != 'win32':
        return None

    ignored_ids = set(QUOTA_PROCESS_IDS)
    try:
        result = subprocess.run(
            ['tasklist', '/fo', 'csv', '/nh'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    ignored_ids.update(QUOTA_PROCESS_IDS)
    names: list[str] = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 2:
            continue
        try:
            process_id = int(row[1])
        except ValueError:
            continue
        if process_id not in ignored_ids:
            names.append(row[0])
    return names
