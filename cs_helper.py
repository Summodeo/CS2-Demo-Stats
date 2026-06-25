#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CS2 助手 - 命令提示符集成工具

通过数字菜单集成：
  1) 启动 CS2（-insecure 模式）+ GOTV 自动录制脚本
  2) 启动 GOTV 自动录制脚本
  0) 退出

所有路径通过 Windows 注册表自动定位 Steam / CS2 安装目录，
无需手动修改脚本中的盘符路径。
"""

import os
import sys
import time
import subprocess
import threading
import datetime


# ── 注册表自动定位 Steam / CS2 ──────────────────────────────

def detect_steam_path():
    """通过注册表读取 Steam 安装路径"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam')
        steam_path, _ = winreg.QueryValueEx(key, 'SteamPath')
        winreg.CloseKey(key)
        return steam_path.replace('/', '\\').rstrip('\\')
    except Exception:
        return ''


def find_cs2_install(steam_path):
    """在 Steam 主库及 libraryfolders.vdf 列出的其他库中查找 CS2 安装目录

    返回: (cs2_game_dir, cs2_bin_dir, csgo_dir) 三元组，找不到则对应项为 ''
    """
    candidates = [steam_path]
    # 解析其他库
    vdf_path = os.path.join(steam_path, 'steamapps', 'libraryfolders.vdf')
    if os.path.isfile(vdf_path):
        try:
            import re
            with open(vdf_path, 'r', encoding='utf-8') as f:
                content = f.read()
            for p in re.findall(r'"path"\s*"([^"]+)"', content):
                candidates.append(p.replace('\\\\', '\\').replace('/', '\\'))
        except Exception:
            pass

    for base in candidates:
        cs2_game = os.path.join(base, 'steamapps', 'common',
                                'Counter-Strike Global Offensive', 'game')
        if not os.path.isdir(cs2_game):
            continue
        bin_dir = os.path.join(cs2_game, 'bin', 'win64')
        csgo_dir = os.path.join(cs2_game, 'csgo')
        cs2_exe = os.path.join(bin_dir, 'cs2.exe')
        if os.path.isfile(cs2_exe):
            return cs2_game, bin_dir, csgo_dir
    return '', '', ''


# ── 启动 CS2（insecure 模式）────────────────────────────────

def launch_cs2_insecure(bin_dir):
    """以 -insecure 模式启动 CS2（用于加载 server.cfg / 录制 demo）"""
    cs2_exe = os.path.join(bin_dir, 'cs2.exe')
    if not os.path.isfile(cs2_exe):
        print('[错误] 未找到 cs2.exe：', cs2_exe)
        return False

    args = [
        '-insecure',
        '+exec', 'server.cfg',
        '-disable_workshop_command_filtering',
    ]
    print('  程序：', cs2_exe)
    print('  参数：', ' '.join(args))
    try:
        subprocess.Popen([cs2_exe] + args, close_fds=True)
        print('CS2 启动指令已发出。')
        return True
    except Exception as e:
        print('[错误] 启动 CS2 失败：', e)
        return False


# ── GOTV 自动录制脚本 ───────────────────────────────────────

def _ensure_cfg_dir(csgo_dir):
    """确保 cfg 与 gotv 目录存在，返回 (cfg_dir, gotv_dir)"""
    cfg_dir = os.path.join(csgo_dir, 'cfg')
    gotv_dir = os.path.join(csgo_dir, 'gotv')
    if not os.path.isdir(cfg_dir):
        os.makedirs(cfg_dir, exist_ok=True)
    if not os.path.isdir(gotv_dir):
        os.makedirs(gotv_dir, exist_ok=True)
    return cfg_dir, gotv_dir


def _timestamp_now():
    """返回 YYYYMMDD-HHMMSS 格式时间戳"""
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')


class Recorder:
    """GOTV 录制脚本：每 60 秒更新 luzhi.cfg 中的 tv_record 命令"""

    def __init__(self, cfg_path, interval=60):
        self.cfg_path = cfg_path
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            ts = _timestamp_now()
            try:
                with open(self.cfg_path, 'w', encoding='utf-8') as f:
                    f.write(f'tv_record "gotv\\{ts}"\n')
                print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] '
                      f'已更新 luzhi.cfg - 录制文件名: {ts}')
            except Exception as e:
                print('[错误] 写入 luzhi.cfg 失败：', e)
            # 分段 sleep 以便快速响应停止请求
            for _ in range(self.interval):
                if self._stop.is_set():
                    break
                time.sleep(1)

    def start(self):
        if self._thread and self._thread.is_alive():
            print('录制脚本已在运行。')
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=5)
            print('录制脚本已停止。')
        else:
            print('录制脚本未运行。')


def run_recorder(csgo_dir, with_cs2=False, bin_dir=''):
    """启动录制脚本（可选同时启动 CS2），在前台等待用户按回车返回"""
    cfg_dir, gotv_dir = _ensure_cfg_dir(csgo_dir)
    cfg_path = os.path.join(cfg_dir, 'luzhi.cfg')

    if with_cs2:
        print('=' * 56)
        print('   启动 CS2 + GOTV 自动录制')
        print('=' * 56)
        # 1) 启动 CS2
        if not launch_cs2_insecure(bin_dir):
            print('[错误] CS2 启动失败，无法继续录制流程。')
            input('按回车键返回主菜单...')
            return
        print()
    else:
        print('=' * 56)
        print('   GOTV 自动录制 (每 60s 更新 luzhi.cfg)')
        print('=' * 56)

    print('  cfg 目录 :', cfg_dir)
    print('  gotv 目录:', gotv_dir)
    print('  请勿关闭此窗口。')
    print('  在 CS2 控制台执行: exec luzhi')
    print('  Demo 文件名使用当前日期时间。')
    print('  按回车键停止录制并返回主菜单。')
    print('=' * 56)

    recorder = Recorder(cfg_path, interval=60)
    recorder.start()
    try:
        input()
    except KeyboardInterrupt:
        pass
    recorder.stop()


# ── 主菜单 ──────────────────────────────────────────────────

BANNER = r"""
==================================================
        CS2 助手 - 命令提示符集成工具
==================================================
"""


def print_menu(steam_path, cs2_game_dir):
    print(BANNER)
    if steam_path:
        print(f'  Steam 安装目录 : {steam_path}')
    else:
        print('  [警告] 未找到 Steam 安装目录（注册表）')

    if cs2_game_dir:
        print(f'  CS2  安装目录  : {cs2_game_dir}')
    else:
        print('  [警告] 未找到 CS2 安装目录')

    print('-' * 50)
    print('  [1] 启动 CS2 + GOTV 自动录制')
    print('  [2] 启动 GOTV 自动录制脚本')
    print('  [0] 退出')
    print('=' * 50)


def main():
    steam_path = detect_steam_path()
    cs2_game_dir, bin_dir, csgo_dir = '', '', ''
    if steam_path:
        cs2_game_dir, bin_dir, csgo_dir = find_cs2_install(steam_path)

    while True:
        print_menu(steam_path, cs2_game_dir)
        choice = input('请输入选项: ').strip()

        if choice == '1':
            if not bin_dir or not csgo_dir:
                print('[错误] 未找到 CS2 安装目录，无法启动。')
                input('按回车键返回菜单...')
            else:
                run_recorder(csgo_dir, with_cs2=True, bin_dir=bin_dir)
        elif choice == '2':
            if not csgo_dir:
                print('[错误] 未找到 CS2 csgo 目录，无法录制。')
                input('按回车键返回菜单...')
            else:
                run_recorder(csgo_dir, with_cs2=False)
        elif choice == '0':
            print('再见！')
            break
        else:
            print('无效选项，请重新输入。')
            time.sleep(0.5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已退出。')
        sys.exit(0)
