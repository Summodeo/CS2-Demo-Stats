#!/usr/bin/env python
"""Build script to package CS2 Demo Stats Desktop App into a standalone executable."""
import subprocess
import sys
import os

def main():
    # Install pyinstaller if not present
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Build PyInstaller command
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=CS2DemoStats',
        '--onefile',
        '--noconsole',
        '--noconfirm',
        '--clean',
    ]

    # Add data directories if they exist
    templates_dir = os.path.join(base_dir, 'templates')
    if os.path.isdir(templates_dir):
        cmd.append(f'--add-data={templates_dir};templates')

    static_dir = os.path.join(base_dir, 'static')
    if os.path.isdir(static_dir):
        cmd.append(f'--add-data={static_dir};static')

    # Hidden imports
    cmd.extend([
        '--hidden-import=awpy',
        '--hidden-import=awpy.demo',
        '--hidden-import=awpy.parsers',
        '--hidden-import=polars',
        '--hidden-import=polars._plr',
        '--hidden-import=_polars_runtime_32',
        '--hidden-import=flask',
        '--hidden-import=waitress',
        '--hidden-import=sqlite3',
        '--hidden-import=database',
        '--hidden-import=demo_parser',
        '--hidden-import=webview',
        '--hidden-import=webview.platforms',
        '--hidden-import=webview.platforms.winforms',
        '--hidden-import=watchdog',
        '--hidden-import=watchdog.observers',
        '--hidden-import=watchdog.events',
        '--collect-all=polars',
        '--collect-all=_polars_runtime_32',
        'desktop_app.py'
    ])

    print("Building CS2 Demo Stats Desktop executable...")
    print(' '.join(cmd))
    subprocess.check_call(cmd)

    print("\n" + "=" * 50)
    print("Build complete!")
    exe_path = os.path.join(base_dir, 'dist', 'CS2DemoStats.exe')
    print(f"Output: {exe_path}")
    print("\nDouble-click CS2DemoStats.exe to start the desktop app.")
    print("=" * 50)

if __name__ == '__main__':
    main()
