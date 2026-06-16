"""
CS2 Demo Stats - Desktop Application
使用 PyWebView 将 Flask 应用包装成桌面应用
"""
import os
import sys

# 必须在导入 app 之前设置，阻止自动打开浏览器和自动启动服务器
os.environ['CS2_DESKTOP_MODE'] = '1'

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview
import threading
import time
import socket


def is_port_in_use(host='127.0.0.1', port=5000):
    """检查端口是否已被占用"""
    try:
        s = socket.create_connection((host, port), timeout=0.5)
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def wait_for_server(host='127.0.0.1', port=5000, timeout=10):
    """等待服务器启动就绪"""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_in_use(host, port):
            return True
        time.sleep(0.2)
    return False


def start_server():
    """在后台线程启动Flask服务器"""
    from app import app
    from waitress import serve
    try:
        serve(app, host='127.0.0.1', port=5000)
    except Exception as e:
        print(f'服务器启动失败: {e}')


if __name__ == '__main__':
    if is_port_in_use():
        # 端口已被占用，直接连接
        print('检测到已有服务在运行，直接连接...')
    else:
        # 启动Flask服务器（后台线程）
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()

        # 等待服务器就绪
        if not wait_for_server():
            print('服务器启动超时，请重试')
            sys.exit(1)

    # 创建桌面窗口
    window = webview.create_window(
        title='CS2 Demo Stats',
        url='http://127.0.0.1:5000',
        width=1280,
        height=800,
        resizable=True,
        min_size=(1024, 600)
    )

    # 启动桌面应用（阻塞，关闭窗口后退出）
    webview.start()
