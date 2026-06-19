from __future__ import annotations

from collections.abc import Iterable
import csv
import io
import subprocess
import sys


CODEX_APP_IMAGE_NAME = 'Codex.exe'


def codex_app_is_running(process_names: Iterable[str] | None = None) -> bool | None:
    if process_names is None:
        process_names = _read_windows_process_names()
        if process_names is None:
            return None

    return any(name == CODEX_APP_IMAGE_NAME for name in process_names)


def _read_windows_process_names() -> list[str] | None:
    if sys.platform != 'win32':
        return None

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

    names: list[str] = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if row:
            names.append(row[0])
    return names
