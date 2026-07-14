from __future__ import annotations

import argparse
import sys

from codex_widget.codex_app import codex_app_is_running
from codex_widget.config import AppConfig
from codex_widget.hook_installer import install_hooks, read_hook_setup_status, uninstall_hooks
from codex_widget.snapshot import CodexSnapshotReader


def print_once() -> int:
    config = AppConfig.load()
    reader = CodexSnapshotReader(
        sessions_dir=config.codex.sessions_dir,
        hook_events_path=config.hook.events_path,
        hook_stale_after_minutes=config.hook.stale_after_minutes,
        hook_max_events_to_read=config.hook.max_events_to_read,
        fallback_working_window_seconds=config.status.working_window_seconds,
        codex_app_running=codex_app_is_running,
        hook_setup_status_reader=read_hook_setup_status,
    )
    snapshot = reader.read_snapshot()

    print(f'状态：{snapshot.status_text}')
    if snapshot.primary_visible:
        print(snapshot.primary_text)
    if snapshot.secondary_visible:
        print(snapshot.secondary_text)
    if snapshot.reset_text:
        print(snapshot.reset_text)
    if snapshot.note:
        print(f'提示：{snapshot.note}')
    if snapshot.latest_file:
        print(f'最新 session：{snapshot.latest_file}')
    if snapshot.quota_file and snapshot.quota_file != snapshot.latest_file:
        print(f'额度来源：{snapshot.quota_file}')
    if snapshot.hook_signal.events_path:
        print(f'hook 事件：{snapshot.hook_signal.events_path}')
    return 0


def install_hook() -> int:
    writer_path, hooks_path, backup_path = install_hooks()
    print(f'已安装 hook writer：{writer_path}')
    print(f'已更新 Codex hooks：{hooks_path}')
    if backup_path:
        print(f'已备份原 hooks.json：{backup_path}')
    print('下一步：在 ChatGPT App 的 Codex 中打开 /hooks，review/trust 新 hook。')
    return 0


def uninstall_hook() -> int:
    hooks_path, backup_path = uninstall_hooks()
    print(f'已移除 Codex Widget hooks：{hooks_path}')
    if backup_path:
        print(f'已备份原 hooks.json：{backup_path}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Codex 状态与额度桌面小挂件')
    parser.add_argument('--once', action='store_true', help='只在控制台读取并打印一次，不启动窗口')
    parser.add_argument('--install-hook', action='store_true', help='安装/刷新 Codex hook 配置')
    parser.add_argument('--uninstall-hook', action='store_true', help='从 hooks.json 移除本工具的 hook')
    args = parser.parse_args()

    if args.install_hook:
        return install_hook()
    if args.uninstall_hook:
        return uninstall_hook()
    if args.once:
        return print_once()

    from codex_widget.app import run_app

    return run_app()


if __name__ == '__main__':
    sys.exit(main())
