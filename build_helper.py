#!/usr/bin/env python
"""Build script for CS2 Helper CLI - 打包成控制台 exe（保留命令提示符窗口）"""
import subprocess
import sys
import os


def main():
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])

    base_dir = os.path.dirname(os.path.abspath(__file__))

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=CS2Helper',
        '--onefile',
        '--noconfirm',
        '--clean',
        # 注意：不加 --noconsole，保留命令提示符窗口
        '--hidden-import=winreg',
        'cs_helper.py'
    ]

    print("Building CS2 Helper CLI executable...")
    subprocess.check_call(cmd)

    print("\n" + "=" * 50)
    print("Build complete!")
    exe_path = os.path.join(base_dir, 'dist', 'CS2Helper.exe')
    print(f"Output: {exe_path}")
    print("双击 CS2Helper.exe 启动菜单。")
    print("=" * 50)


if __name__ == '__main__':
    main()
