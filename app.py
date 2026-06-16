import os
import sys
import uuid
import json
import webbrowser
import threading
import time
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

from database import init_db, save_match, get_all_matches, get_match_detail, get_leaderboard, delete_match, delete_all_matches, find_match_by_filename, get_all_parsed_filenames, delete_match_by_filename
from demo_parser import parse_demo

# Support PyInstaller bundled executable
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle - resources are in _internal dir
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

# Global state for folder scanning
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
}


def save_and_parse_demo(file):
    original_name = file.filename

    # Dedup by filename: if already parsed, skip
    if find_match_by_filename(original_name):
        return None  # indicates duplicate

    unique_name = f"{uuid.uuid4().hex}_{original_name}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_name)
    file.save(file_path)

    try:
        map_name, players_data, total_rounds, demo_guid, team_groups = parse_demo(file_path)
    finally:
        # Always delete the temp file after parsing
        try:
            os.remove(file_path)
        except Exception:
            pass

    if not players_data:
        raise Exception('未从 Demo 中解析到任何玩家数据')

    return map_name, total_rounds, len(players_data), players_data, demo_guid


def parse_demo_from_path(file_path, original_name):
    """Parse a demo from a local file path (for folder scanning). No file copy needed."""
    map_name, players_data, total_rounds, demo_guid, team_groups = parse_demo(file_path)

    if not players_data:
        raise Exception('未从 Demo 中解析到任何玩家数据')

    return map_name, total_rounds, len(players_data), players_data, demo_guid


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

    map_name, total_rounds, player_count, players_data, demo_guid = result

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    duration = total_rounds * 2

    match_id = save_match(
        map_name=map_name, date=now, duration=duration,
        demo_file_name=file.filename, players_data=players_data,
        demo_guid=demo_guid
    )

    flash(f'Demo 解析成功！地图: {map_name}, 回合数: {total_rounds}, 玩家数: {player_count}', 'success')
    return redirect(url_for('match_detail', match_id=match_id))


@app.route('/api/scan-folder', methods=['POST'])
def scan_folder():
    """Start scanning a folder for .dem files and parse them."""
    global scan_state

    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}

    folder = data.get('folder', '').strip()
    keyword = data.get('keyword', '').strip()

    if not folder or not os.path.isdir(folder):
        return jsonify({'ok': False, 'message': f'文件夹路径无效: {folder}'})

    # Reset state
    scan_state = {
        'running': True,
        'folder': folder,
        'keyword': keyword,
        'total': 0,
        'success': 0,
        'skipped': 0,
        'failed': 0,
        'errors': [],
        'current_file': '',
        'stop_requested': False,
    }

    # Start scanning in background thread
    t = threading.Thread(target=_do_scan, daemon=True)
    t.start()

    return jsonify({'ok': True, 'message': '扫描已启动'})


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


# ── Scan path persistence ──
_CONFIG_PATH = os.path.join(BASE_DIR, 'scan_config.json')

def _load_scan_config():
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_scan_config(config):
    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@app.route('/api/scan-config', methods=['GET'])
def get_scan_config():
    """Get saved scan folder and keyword."""
    config = _load_scan_config()
    return jsonify({
        'folder': config.get('folder', r'H:\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\gotv'),
        'keyword': config.get('keyword', '2026')
    })


@app.route('/api/scan-config', methods=['POST'])
def save_scan_config():
    """Save scan folder and keyword."""
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip()
    keyword = data.get('keyword', '').strip()
    _save_scan_config({'folder': folder, 'keyword': keyword})
    return jsonify({'ok': True})


def _do_scan():
    """Background task to continuously scan folder and parse demos."""
    global scan_state

    try:
        folder = scan_state['folder']
        keyword = scan_state['keyword']

        while not scan_state['stop_requested']:
            # Collect all .dem files
            dem_files = []
            for root, dirs, files in os.walk(folder):
                for fname in files:
                    if fname.lower().endswith('.dem'):
                        # Apply keyword filter
                        if keyword and keyword.lower() not in fname.lower():
                            continue
                        dem_files.append(os.path.join(root, fname))

            scan_state['total'] = len(dem_files)

            if not dem_files:
                # No files found, wait and retry
                scan_state['current_file'] = '等待新文件...'
                for _ in range(100):  # 10 seconds
                    if scan_state['stop_requested']:
                        break
                    time.sleep(0.1)
                continue

            for fpath in dem_files:
                if scan_state['stop_requested']:
                    break

                fname = os.path.basename(fpath)
                scan_state['current_file'] = fname

                # If already in database, check file size stability
                if find_match_by_filename(fname):
                    try:
                        size1 = os.path.getsize(fpath)
                    except OSError:
                        continue
                    # Wait 10 seconds and check again
                    for _ in range(100):  # 10 seconds
                        if scan_state['stop_requested']:
                            break
                        time.sleep(0.1)
                    try:
                        size2 = os.path.getsize(fpath)
                    except OSError:
                        continue
                    # Size unchanged → file is stable, skip overwrite
                    if size1 == size2:
                        scan_state['skipped'] += 1
                        continue

                try:
                    result = parse_demo_from_path(fpath, fname)
                    map_name, total_rounds, player_count, players_data, demo_guid = result
                except BaseException as e:
                    scan_state['failed'] += 1
                    if len(scan_state['errors']) < 20:
                        err_msg = str(e)
                        if 'ClassMapperNotFoundFirstPass' in err_msg:
                            scan_state['errors'].append(f'{fname}: 正在录制中')
                        else:
                            scan_state['errors'].append(f'{fname}: {err_msg}')
                    continue

                # Overwrite existing record with same filename
                delete_match_by_filename(fname)

                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                duration = total_rounds * 2
                save_match(
                    map_name=map_name, date=now, duration=duration,
                    demo_file_name=fname, players_data=players_data,
                    demo_guid=demo_guid
                )
                scan_state['success'] += 1

            # After one full pass, wait before scanning again
            if not scan_state['stop_requested']:
                scan_state['current_file'] = '扫描完成，等待下一轮...'
                for _ in range(100):  # 10 seconds
                    if scan_state['stop_requested']:
                        break
                    time.sleep(0.1)
    except Exception as e:
        scan_state['errors'].append(f'扫描线程异常: {str(e)}')
    finally:
        scan_state['running'] = False
        scan_state['current_file'] = ''


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


@app.errorhandler(413)
def request_entity_too_large(error):
    flash('上传文件过大！当前限制为 10GB。请减少文件数量，或使用单文件上传。', 'error')
    return redirect(url_for('index'))


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    url = f'http://127.0.0.1:{port}'
    print(f"CS2 Demo 数据统计系统启动于 {url}")

    # Only auto-open browser in web mode (when not running as desktop app)
    import os as _os
    if not _os.environ.get('CS2_DESKTOP_MODE'):
        def _open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

    from waitress import serve
    serve(app, host='0.0.0.0', port=port, max_request_body_size=10737418240)
else:
    # Also init when running as PyInstaller bundle or imported as module
    init_db()

    # Only auto-start server when NOT in desktop mode
    # (desktop_app.py handles server startup itself)
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
