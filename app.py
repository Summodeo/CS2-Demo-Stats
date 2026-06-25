import os
import sys
import uuid
import json
import hashlib
import webbrowser
import threading
import time
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

from database import (
    init_db, save_match, get_all_matches, get_match_detail, get_leaderboard,
    delete_match, delete_all_matches, find_match_by_filename, get_all_parsed_filenames,
    delete_match_by_filename, is_file_parsed, record_parsed_file, get_parsed_md5_set,
    create_series, get_all_series, get_series_detail, delete_series
)
from demo_parser import parse_demo

# Support PyInstaller bundled executable
if getattr(sys, 'frozen', False):
    _INTERNAL_DIR = sys._MEIPASS
    BASE_DIR = os.path.dirname(sys.executable)
else:
    _INTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = _INTERNAL_DIR

app = Flask(__name__,
            template_folder=os.path.join(_INTERNAL_DIR, 'templates'),
            static_folder=os.path.join(_INTERNAL_DIR, 'static'))
app.secret_key = 'cs2-demo-stats-secret-key'

# Use system temp directory for uploads (no folder created in app directory)
import tempfile
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'cs2_demo_stats_uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024

# ── Global config.json ──
_CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

_DEFAULT_CONFIG = {
    'cs2_demo_folder': '',
    'scan_keyword': '2026',
    'auto_detect': True,
}


def load_config():
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        # Merge with defaults
        merged = dict(_DEFAULT_CONFIG)
        merged.update(cfg)
        return merged
    except Exception:
        return dict(_DEFAULT_CONFIG)


def save_config(cfg):
    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def detect_cs2_demo_folder():
    """通过Windows注册表自动检索CS2 demo存放目录"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam')
        steam_path, _ = winreg.QueryValueEx(key, 'SteamPath')
        winreg.CloseKey(key)
        steam_path = steam_path.replace('/', '\\').rstrip('\\')
        # CS2 默认安装路径: steamapps\common\Counter-Strike Global Offensive\game\csgo
        cs2_csgo = os.path.join(steam_path, 'steamapps', 'common',
                                'Counter-Strike Global Offensive', 'game', 'csgo')
        gotv_dir = os.path.join(cs2_csgo, 'gotv')
        if os.path.isdir(gotv_dir):
            return gotv_dir
        if os.path.isdir(cs2_csgo):
            return cs2_csgo
        # 尝试读取libraryfolders.vdf查找其他库
        vdf_path = os.path.join(steam_path, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf_path):
            try:
                with open(vdf_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                import re
                paths = re.findall(r'"path"\s*"([^"]+)"', content)
                for p in paths:
                    p = p.replace('\\\\', '\\').replace('/', '\\')
                    csgo = os.path.join(p, 'steamapps', 'common',
                                        'Counter-Strike Global Offensive', 'game', 'csgo')
                    gotv = os.path.join(csgo, 'gotv')
                    if os.path.isdir(gotv):
                        return gotv
                    if os.path.isdir(csgo):
                        return csgo
            except Exception:
                pass
        return ''
    except Exception:
        return ''


def compute_file_md5(file_path, chunk_size=8192):
    """计算文件MD5"""
    h = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ''


# Global state for folder scanning (watchdog mode)
scan_state = {
    'running': False,
    'folder': '',
    'keyword': '',
    'total': 0,
    'success': 0,
    'skipped': 0,
    'failed': 0,
    'errors': [],
    'current_file': '',
    'stop_requested': False,
    'mode': 'idle',  # 'idle' | 'watching'
}

# Watchdog observer (global, so we can stop it)
_watchdog_observer = None
_watchdog_lock = threading.Lock()


def save_and_parse_demo(file):
    original_name = file.filename

    if find_match_by_filename(original_name):
        return None

    unique_name = f"{uuid.uuid4().hex}_{original_name}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_name)
    file.save(file_path)

    try:
        map_name, players_data, total_rounds, demo_guid, team_groups, match_info = parse_demo(file_path)
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass

    if not players_data:
        raise Exception('未从 Demo 中解析到任何玩家数据')

    return map_name, total_rounds, len(players_data), players_data, demo_guid, match_info


def parse_demo_from_path(file_path, original_name):
    """Parse a demo from a local file path."""
    map_name, players_data, total_rounds, demo_guid, team_groups, match_info = parse_demo(file_path)

    if not players_data:
        raise Exception('未从 Demo 中解析到任何玩家数据')

    return map_name, total_rounds, len(players_data), players_data, demo_guid, match_info


def _process_demo_file(fpath, fname, retry_count=0):
    """处理单个demo文件：去重检测 → 文件锁检测 → 解析（含重试） → 保存"""
    # 计算MD5
    file_md5 = compute_file_md5(fpath)
    if not file_md5:
        scan_state['failed'] += 1
        if len(scan_state['errors']) < 20:
            scan_state['errors'].append(f'{fname}: 无法读取文件')
        return

    # 去重逻辑：只有当比赛记录存在且MD5匹配时才跳过
    match_exists = find_match_by_filename(fname)
    if match_exists and is_file_parsed(fpath, file_md5):
        scan_state['skipped'] += 1
        return

    # 方案二：Windows文件独占锁检测（使用Windows API精准检测）
    if not _is_file_released(fpath):
        if retry_count < 5:
            scan_state['current_file'] = f'{fname} (录制中，10秒后重试 {retry_count + 1}/5)'
            time.sleep(10)
            if scan_state['stop_requested']:
                return
            _process_demo_file(fpath, fname, retry_count + 1)
        else:
            scan_state['failed'] += 1
            if len(scan_state['errors']) < 20:
                scan_state['errors'].append(f'{fname}: 文件被占用，重试5次仍失败')
        return

    # 方案三：解析器试错+重试（屏蔽解析器的英文输出）
    import io
    from contextlib import redirect_stdout, redirect_stderr
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = parse_demo_from_path(fpath, fname)
        map_name, total_rounds, player_count, players_data, demo_guid, match_info = result
    except BaseException as e:
        err_msg = str(e)
        if retry_count < 5 and ('EOF' in err_msg or 'ClassMapperNotFoundFirstPass' in err_msg
                                or 'incomplete' in err_msg.lower() or 'truncated' in err_msg.lower()
                                or 'unexpected' in err_msg.lower()):
            scan_state['current_file'] = f'{fname} (解析不完整，10秒后重试 {retry_count + 1}/5)'
            time.sleep(10)
            if scan_state['stop_requested']:
                return
            _process_demo_file(fpath, fname, retry_count + 1)
            return
        scan_state['failed'] += 1
        if len(scan_state['errors']) < 20:
            if 'ClassMapperNotFoundFirstPass' in err_msg:
                scan_state['errors'].append(f'{fname}: 正在录制中')
            else:
                scan_state['errors'].append(f'{fname}: {err_msg}')
        return

    # 防止保存不完整的比赛（0回合 = 录制中的假解析）
    if total_rounds == 0 or not players_data:
        if retry_count < 5:
            scan_state['current_file'] = f'{fname} (无回合数据，10秒后重试 {retry_count + 1}/5)'
            time.sleep(10)
            if scan_state['stop_requested']:
                return
            _process_demo_file(fpath, fname, retry_count + 1)
            return
        scan_state['failed'] += 1
        if len(scan_state['errors']) < 20:
            scan_state['errors'].append(f'{fname}: 解析到0回合，可能仍在录制')
        return

    # 覆盖同名记录（比赛记录存在时删除旧的）
    if match_exists:
        delete_match_by_filename(fname)

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    duration = total_rounds * 2
    save_match(
        map_name=map_name, date=now, duration=duration,
        demo_file_name=fname, players_data=players_data,
        demo_guid=demo_guid,
        team_a_score=match_info.get('team_a_score', 0),
        team_b_score=match_info.get('team_b_score', 0),
        team_a_number=match_info.get('team_a_number', 0),
        team_b_number=match_info.get('team_b_number', 0),
        winner_team=match_info.get('winner_team', 0),
    )

    # 记录MD5
    try:
        file_size = os.path.getsize(fpath)
    except OSError:
        file_size = 0
    record_parsed_file(fpath, file_md5, file_size, fname)

    scan_state['success'] += 1


def _is_file_released(file_path):
    """方案二：使用Windows API精准检测文件是否被CS2独占占用"""
    try:
        import ctypes
        from ctypes import wintypes

        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        FILE_SHARE_NONE = 0  # 不允许共享 = 检测独占锁
        INVALID_HANDLE_VALUE = -1

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
        ]

        handle = kernel32.CreateFileW(
            file_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_NONE,  # 关键：不允许共享，如果CS2占用会失败
            None,
            OPEN_EXISTING,
            0,
            None
        )

        if handle == INVALID_HANDLE_VALUE or handle == 0:
            # 打开失败 = 文件被占用
            error_code = kernel32.GetLastError()
            # ERROR_SHARING_VIOLATION (32) = 文件被其他进程占用
            return False
        else:
            kernel32.CloseHandle(handle)
            return True
    except Exception:
        # 非Windows或API调用失败，回退到简单检测
        try:
            fd = os.open(file_path, os.O_RDWR)
            os.close(fd)
            return True
        except PermissionError:
            return False
        except OSError:
            return True


def _initial_scan(folder, keyword, recursive=True):
    """启动时先扫描一次现有文件"""
    dem_files = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            for fname in files:
                if fname.lower().endswith('.dem'):
                    if keyword and keyword.lower() not in fname.lower():
                        continue
                    dem_files.append(os.path.join(root, fname))
    else:
        # 仅扫描当前文件夹，不递归子目录
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath) and fname.lower().endswith('.dem'):
                if keyword and keyword.lower() not in fname.lower():
                    continue
                dem_files.append(fpath)

    scan_state['total'] = len(dem_files)

    for fpath in dem_files:
        if scan_state['stop_requested']:
            break
        fname = os.path.basename(fpath)
        scan_state['current_file'] = fname
        _process_demo_file(fpath, fname)


def _start_watchdog(folder, keyword, recursive=True):
    """使用watchdog监听文件夹变化（组合方案：稳态检测+文件锁+重试）"""
    global _watchdog_observer

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        # 方案一：文件大小稳态检测
        # pending: {path: last_modified_time}
        pending_files = {}
        pending_lock = threading.Lock()
        STABLE_THRESHOLD = 15  # 15秒内无变化认为稳定（CS2录制中场间暂停也不会误触发）

        class DemoFileHandler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                if not event.src_path.lower().endswith('.dem'):
                    return
                fname = os.path.basename(event.src_path)
                if keyword and keyword.lower() not in fname.lower():
                    return
                if scan_state['stop_requested']:
                    return
                with pending_lock:
                    pending_files[event.src_path] = time.time()

            def on_modified(self, event):
                if event.is_directory:
                    return
                if not event.src_path.lower().endswith('.dem'):
                    return
                fname = os.path.basename(event.src_path)
                if keyword and keyword.lower() not in fname.lower():
                    return
                if scan_state['stop_requested']:
                    return
                # 防抖：每次修改都重置倒计时
                with pending_lock:
                    pending_files[event.src_path] = time.time()

        def _stability_checker():
            """稳态检测线程：检查文件是否在N秒内无变化"""
            while not scan_state['stop_requested']:
                time.sleep(5)  # 每5秒检查一次
                if scan_state['stop_requested']:
                    break
                now = time.time()
                to_process = []
                with pending_lock:
                    for path, last_time in list(pending_files.items()):
                        if now - last_time > STABLE_THRESHOLD:
                            # 超过阈值未变化，双重确认文件大小
                            try:
                                size1 = os.path.getsize(path)
                                time.sleep(3)  # 再等3秒确认
                                if scan_state['stop_requested']:
                                    break
                                size2 = os.path.getsize(path)
                                if size1 == size2:
                                    to_process.append(path)
                                else:
                                    # 大小还在变化，重置倒计时
                                    pending_files[path] = time.time()
                            except OSError:
                                to_process.append(path)
                    for path in to_process:
                        pending_files.pop(path, None)

                for path in to_process:
                    if scan_state['stop_requested']:
                        break
                    fname = os.path.basename(path)
                    scan_state['total'] += 1
                    scan_state['current_file'] = fname
                    _process_demo_file(path, fname)

        with _watchdog_lock:
            if _watchdog_observer:
                try:
                    _watchdog_observer.stop()
                    _watchdog_observer.join(timeout=2)
                except Exception:
                    pass

            _watchdog_observer = Observer()
            _watchdog_observer.schedule(DemoFileHandler(), folder, recursive=recursive)
            _watchdog_observer.start()

        # 启动稳态检测线程
        checker_thread = threading.Thread(target=_stability_checker, daemon=True)
        checker_thread.start()

        scan_state['mode'] = 'watching'
        scan_state['current_file'] = '监听中...'

        # 保持线程存活，直到停止
        while not scan_state['stop_requested']:
            time.sleep(0.5)

    except ImportError:
        # watchdog未安装，回退到轮询模式
        scan_state['errors'].append('watchdog未安装，回退到轮询模式')
        _fallback_polling(folder, keyword, recursive)
    except Exception as e:
        scan_state['errors'].append(f'监听异常: {str(e)}')
    finally:
        with _watchdog_lock:
            if _watchdog_observer:
                try:
                    _watchdog_observer.stop()
                    _watchdog_observer.join(timeout=2)
                except Exception:
                    pass
                _watchdog_observer = None
        scan_state['mode'] = 'idle'


def _fallback_polling(folder, keyword, recursive=True):
    """无watchdog时的轮询回退模式"""
    while not scan_state['stop_requested']:
        dem_files = []
        if recursive:
            for root, dirs, files in os.walk(folder):
                for fname in files:
                    if fname.lower().endswith('.dem'):
                        if keyword and keyword.lower() not in fname.lower():
                            continue
                        dem_files.append(os.path.join(root, fname))
        else:
            for fname in os.listdir(folder):
                fpath = os.path.join(folder, fname)
                if os.path.isfile(fpath) and fname.lower().endswith('.dem'):
                    if keyword and keyword.lower() not in fname.lower():
                        continue
                    dem_files.append(fpath)

        scan_state['total'] = len(dem_files)

        if not dem_files:
            scan_state['current_file'] = '等待新文件...'
            for _ in range(100):
                if scan_state['stop_requested']:
                    break
                time.sleep(0.1)
            continue

        for fpath in dem_files:
            if scan_state['stop_requested']:
                break
            fname = os.path.basename(fpath)
            scan_state['current_file'] = fname
            _process_demo_file(fpath, fname)

        if not scan_state['stop_requested']:
            scan_state['current_file'] = '扫描完成，等待下一轮...'
            for _ in range(100):
                if scan_state['stop_requested']:
                    break
                time.sleep(0.1)


@app.route('/')
def index():
    matches = get_all_matches()
    return render_template('index.html', matches=matches)


@app.route('/upload', methods=['POST'])
def upload():
    if 'demo_file' not in request.files:
        flash('请选择一个 .dem 文件', 'error')
        return redirect(url_for('index'))

    file = request.files['demo_file']
    if file.filename == '':
        flash('请选择一个文件', 'error')
        return redirect(url_for('index'))

    if not file.filename.lower().endswith('.dem'):
        flash('请上传 .dem 格式的 Demo 文件', 'error')
        return redirect(url_for('index'))

    result = save_and_parse_demo(file)
    if result is None:
        flash(f'Demo "{file.filename}" 已存在，跳过重复导入', 'info')
        return redirect(url_for('index'))

    map_name, total_rounds, player_count, players_data, demo_guid, match_info = result

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    duration = total_rounds * 2

    match_id = save_match(
        map_name=map_name, date=now, duration=duration,
        demo_file_name=file.filename, players_data=players_data,
        demo_guid=demo_guid,
        team_a_score=match_info.get('team_a_score', 0),
        team_b_score=match_info.get('team_b_score', 0),
        team_a_number=match_info.get('team_a_number', 0),
        team_b_number=match_info.get('team_b_number', 0),
        winner_team=match_info.get('winner_team', 0),
    )

    flash(f'Demo 解析成功！地图: {map_name}, 回合数: {total_rounds}, 玩家数: {player_count}', 'success')
    return redirect(url_for('match_detail', match_id=match_id))


@app.route('/api/scan-folder', methods=['POST'])
def scan_folder():
    """Start watching a folder for .dem files."""
    global scan_state

    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}

    folder = data.get('folder', '').strip()
    keyword = data.get('keyword', '').strip()
    recursive = data.get('recursive', True)  # 默认包含子目录

    if not folder or not os.path.isdir(folder):
        return jsonify({'ok': False, 'message': f'文件夹路径无效: {folder}'})

    # 停止之前的监听
    scan_state['stop_requested'] = True
    time.sleep(0.5)

    scan_state = {
        'running': True,
        'folder': folder,
        'keyword': keyword,
        'recursive': recursive,
        'total': 0,
        'success': 0,
        'skipped': 0,
        'failed': 0,
        'errors': [],
        'current_file': '',
        'stop_requested': False,
        'mode': 'idle',
    }

    def _run():
        # 先做初始扫描
        scan_state['current_file'] = '初始扫描中...'
        _initial_scan(folder, keyword, recursive)
        if scan_state['stop_requested']:
            scan_state['running'] = False
            return
        # 启动watchdog监听
        _start_watchdog(folder, keyword, recursive)
        scan_state['running'] = False
        scan_state['current_file'] = ''

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({'ok': True, 'message': '监听已启动'})


@app.route('/api/scan-status')
def scan_status():
    """Get current scan status."""
    return jsonify(scan_state)


@app.route('/api/scan-stop', methods=['POST'])
def scan_stop():
    """Request to stop the current scan."""
    global scan_state
    scan_state['stop_requested'] = True
    return jsonify({'ok': True})


# ── Config API ──

@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置（含自动检测路径）"""
    cfg = load_config()
    # 如果启用自动检测或路径为空，尝试检测
    detected = ''
    if cfg.get('auto_detect', True) or not cfg.get('cs2_demo_folder'):
        detected = detect_cs2_demo_folder()
    return jsonify({
        'cs2_demo_folder': cfg.get('cs2_demo_folder', ''),
        'scan_keyword': cfg.get('scan_keyword', '2026'),
        'auto_detect': cfg.get('auto_detect', True),
        'detected_folder': detected,
    })


@app.route('/api/config', methods=['POST'])
def save_config_route():
    """保存配置"""
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    if 'cs2_demo_folder' in data:
        cfg['cs2_demo_folder'] = data['cs2_demo_folder'].strip()
    if 'scan_keyword' in data:
        cfg['scan_keyword'] = data['scan_keyword'].strip()
    if 'auto_detect' in data:
        cfg['auto_detect'] = bool(data['auto_detect'])
    save_config(cfg)
    return jsonify({'ok': True})


@app.route('/api/detect-cs2-path', methods=['GET'])
def detect_cs2_path():
    """手动触发CS2路径检测"""
    path = detect_cs2_demo_folder()
    if path:
        return jsonify({'ok': True, 'path': path})
    return jsonify({'ok': False, 'message': '未找到CS2安装路径，请手动输入'})


@app.route('/match/<int:match_id>')
def match_detail(match_id):
    data = get_match_detail(match_id)
    if data is None:
        flash('比赛记录未找到', 'error')
        return redirect(url_for('index'))
    players = data['players']
    team_map = {}
    for p in players:
        tn = p.get('team_number', 0)
        if tn not in team_map:
            team_map[tn] = []
        team_map[tn].append(p)

    if len(team_map) >= 2:
        groups = sorted(team_map.items(), key=lambda x: x[0])
        ct_players = sorted(groups[0][1], key=lambda x: x['rating'], reverse=True)
        t_players = sorted(groups[1][1], key=lambda x: x['rating'], reverse=True)
        team_a_label = f'队伍 {groups[0][0]}' if groups[0][0] not in (0, -1) else 'A 队'
        team_b_label = f'队伍 {groups[1][0]}' if groups[1][0] not in (0, -1) else 'B 队'
    else:
        players.sort(key=lambda x: x['rating'], reverse=True)
        mid = len(players) // 2
        ct_players = players[:mid]
        t_players = players[mid:]
        team_a_label = 'A 队'
        team_b_label = 'B 队'
    other_players = []

    return render_template('match.html', match=data['match'], players=players,
                           ct_players=ct_players, t_players=t_players, other_players=other_players,
                           team_a_label=team_a_label, team_b_label=team_b_label)


@app.route('/match/<int:match_id>/delete', methods=['POST'])
def delete_match_route(match_id):
    data = get_match_detail(match_id)
    if data is None:
        flash('比赛记录未找到', 'error')
        return redirect(url_for('index'))
    delete_match(match_id)
    flash(f'比赛 #{match_id} ({data["match"]["map_name"]}) 已删除', 'success')
    return redirect(url_for('index'))


@app.route('/matches/delete-all', methods=['POST'])
def delete_all_matches_route():
    matches = get_all_matches()
    count = len(matches)
    delete_all_matches()
    flash(f'已删除全部 {count} 场比赛数据', 'success')
    return redirect(url_for('index'))


@app.route('/matches/batch-delete', methods=['POST'])
def batch_delete_matches():
    match_ids = request.form.getlist('match_ids')
    match_ids = [int(mid) for mid in match_ids if mid]
    if not match_ids:
        flash('未选择任何比赛', 'error')
        return redirect(url_for('index'))
    count = 0
    for mid in match_ids:
        data = get_match_detail(mid)
        if data:
            delete_match(mid)
            count += 1
    flash(f'已批量删除 {count} 场比赛', 'success')
    return redirect(url_for('index'))


@app.route('/leaderboard')
def leaderboard():
    players = get_leaderboard()
    return render_template('leaderboard.html', players=players)


@app.route('/api/matches')
def api_matches():
    matches = get_all_matches()
    return jsonify(matches)


@app.route('/api/match/<int:match_id>')
def api_match(match_id):
    data = get_match_detail(match_id)
    if data is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(data)


@app.route('/api/leaderboard')
def api_leaderboard():
    players = get_leaderboard()
    return jsonify(players)


# ── Series routes ──

@app.route('/series')
def series_list():
    series = get_all_series()
    return render_template('series.html', series_list=series, mode='list')


@app.route('/series/create', methods=['POST'])
def series_create():
    name = request.form.get('name', '').strip()
    bo_count = int(request.form.get('bo_count', 1))
    match_ids = request.form.getlist('match_ids')
    match_ids = [int(mid) for mid in match_ids if mid]

    if not name:
        flash('请输入系列赛名称', 'error')
        return redirect(url_for('series_list'))
    if not match_ids:
        flash('请至少选择一场比赛', 'error')
        return redirect(url_for('series_list'))

    series_id = create_series(name, bo_count, match_ids)
    flash(f'系列赛 "{name}" 创建成功，包含 {len(match_ids)} 场比赛', 'success')
    return redirect(url_for('series_detail', series_id=series_id))


@app.route('/series/<int:series_id>')
def series_detail(series_id):
    data = get_series_detail(series_id)
    if data is None:
        flash('系列赛未找到', 'error')
        return redirect(url_for('series_list'))

    players = data['players']
    team_map = {}
    for p in players:
        tn = p.get('team_number', 0)
        if tn not in team_map:
            team_map[tn] = []
        team_map[tn].append(p)

    if len(team_map) >= 2:
        groups = sorted(team_map.items(), key=lambda x: x[0])
        ct_players = sorted(groups[0][1], key=lambda x: x['rating'], reverse=True)
        t_players = sorted(groups[1][1], key=lambda x: x['rating'], reverse=True)
        team_a_label = f'队伍 {groups[0][0]}' if groups[0][0] not in (0, -1) else 'A 队'
        team_b_label = f'队伍 {groups[1][0]}' if groups[1][0] not in (0, -1) else 'B 队'
    else:
        players.sort(key=lambda x: x['rating'], reverse=True)
        mid = len(players) // 2
        ct_players = players[:mid]
        t_players = players[mid:]
        team_a_label = 'A 队'
        team_b_label = 'B 队'

    return render_template('series.html', mode='detail', series=data['series'],
                           matches=data['matches'], players=players,
                           ct_players=ct_players, t_players=t_players,
                           team_a_label=team_a_label, team_b_label=team_b_label,
                           team_a_total=data['team_a_total'], team_b_total=data['team_b_total'])


@app.route('/series/<int:series_id>/delete', methods=['POST'])
def series_delete(series_id):
    data = get_series_detail(series_id)
    if data is None:
        flash('系列赛未找到', 'error')
        return redirect(url_for('series_list'))
    delete_series(series_id)
    flash(f'系列赛 "{data["series"]["name"]}" 已删除', 'success')
    return redirect(url_for('series_list'))


@app.errorhandler(413)
def request_entity_too_large(error):
    flash('上传文件过大！当前限制为 10GB。请减少文件数量，或使用单文件上传。', 'error')
    return redirect(url_for('index'))


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    url = f'http://127.0.0.1:{port}'
    print(f"CS2 Demo 数据统计系统启动于 {url}")

    if not os.environ.get('CS2_DESKTOP_MODE'):
        def _open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

    from waitress import serve
    serve(app, host='0.0.0.0', port=port, max_request_body_size=10737418240)
else:
    init_db()

    if not os.environ.get('CS2_DESKTOP_MODE'):
        port = int(os.environ.get('PORT', 5000))
        url = f'http://127.0.0.1:{port}'

        def _open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

        from waitress import serve
        serve(app, host='0.0.0.0', port=port, max_request_body_size=10737418240)
