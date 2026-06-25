import sqlite3
import os
import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, 'cs_demo.db')


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_name TEXT NOT NULL,
            date TEXT NOT NULL,
            duration INTEGER DEFAULT 0,
            demo_file_name TEXT NOT NULL,
            demo_guid TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            side TEXT DEFAULT '',
            kills INTEGER DEFAULT 0,
            deaths INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            adr REAL DEFAULT 0.0,
            kpr REAL DEFAULT 0.0,
            dpr REAL DEFAULT 0.0,
            kast REAL DEFAULT 0.0,
            impact REAL DEFAULT 0.0,
            rating REAL DEFAULT 0.0,
            rounds_played INTEGER DEFAULT 0,
            total_damage INTEGER DEFAULT 0,
            multikill_rounds INTEGER DEFAULT 0,
            kill_rounds INTEGER DEFAULT 0,
            assist_rounds INTEGER DEFAULT 0,
            survive_rounds INTEGER DEFAULT 0,
            trade_rounds INTEGER DEFAULT 0,
            FOREIGN KEY (match_id) REFERENCES matches(id)
        );
    ''')
    try:
        cursor.execute('ALTER TABLE matches ADD COLUMN demo_guid TEXT DEFAULT ""')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE player_stats ADD COLUMN side TEXT DEFAULT ""')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE player_stats ADD COLUMN team_number INTEGER DEFAULT 0')
    except Exception:
        pass

    # 比分和胜负字段
    for col_def in [
        ('team_a_score', 'INTEGER DEFAULT 0'),
        ('team_b_score', 'INTEGER DEFAULT 0'),
        ('team_a_number', 'INTEGER DEFAULT 0'),
        ('team_b_number', 'INTEGER DEFAULT 0'),
        ('winner_team', 'INTEGER DEFAULT 0'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE matches ADD COLUMN {col_def[0]} {col_def[1]}')
        except Exception:
            pass

    # 系列赛表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            bo_count INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS series_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            match_id INTEGER NOT NULL,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (series_id) REFERENCES series(id),
            FOREIGN KEY (match_id) REFERENCES matches(id)
        )
    ''')

    # MD5 dedup table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parsed_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            file_md5 TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            parsed_at TEXT NOT NULL,
            demo_file_name TEXT DEFAULT ''
        )
    ''')
    # 兼容旧表：添加 demo_file_name 列（如果不存在）
    try:
        cursor.execute('ALTER TABLE parsed_files ADD COLUMN demo_file_name TEXT DEFAULT ""')
    except Exception:
        pass
    conn.commit()
    conn.close()


def save_match(map_name, date, duration, demo_file_name, players_data, demo_guid='',
               team_a_score=0, team_b_score=0, team_a_number=0, team_b_number=0, winner_team=0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO matches (map_name, date, duration, demo_file_name, demo_guid,
           team_a_score, team_b_score, team_a_number, team_b_number, winner_team)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (map_name, date, duration, demo_file_name, demo_guid,
         team_a_score, team_b_score, team_a_number, team_b_number, winner_team)
    )
    match_id = cursor.lastrowid

    for p in players_data:
        cursor.execute('''
            INSERT INTO player_stats (
                match_id, player_name, side, kills, deaths, assists,
                adr, kpr, dpr, kast, impact, rating, rounds_played,
                total_damage, multikill_rounds,
                kill_rounds, assist_rounds, survive_rounds, trade_rounds,
                team_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            match_id, p['player_name'], p.get('team', ''), p['kills'], p['deaths'], p['assists'],
            p['adr'], p['kpr'], p['dpr'], p['kast'], p['impact'], p['rating'], p['rounds_played'],
            p['total_damage'], p['multikill_rounds'],
            p['kill_rounds'], p['assist_rounds'], p['survive_rounds'], p['trade_rounds'],
            p.get('team_number', 0)
        ))

    conn.commit()
    conn.close()
    return match_id


def find_match_by_filename(demo_file_name):
    """Check if a match with the same demo_file_name already exists."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id FROM matches WHERE demo_file_name = ? LIMIT 1',
        (demo_file_name,)
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


def delete_match_by_filename(demo_file_name):
    """Delete a match and its player stats by demo_file_name."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM matches WHERE demo_file_name = ?', (demo_file_name,))
    row = cursor.fetchone()
    if row:
        match_id = row['id']
        cursor.execute('DELETE FROM player_stats WHERE match_id = ?', (match_id,))
        cursor.execute('DELETE FROM matches WHERE id = ?', (match_id,))
    # 同步清理MD5去重记录
    cursor.execute('DELETE FROM parsed_files WHERE demo_file_name = ?', (demo_file_name,))
    conn.commit()
    conn.close()


def get_all_parsed_filenames():
    """Return set of all demo_file_name values already in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT demo_file_name FROM matches')
    names = {row['demo_file_name'] for row in cursor.fetchall()}
    conn.close()
    return names


def get_all_matches():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.*, COUNT(ps.id) as player_count
        FROM matches m
        LEFT JOIN player_stats ps ON ps.match_id = m.id
        GROUP BY m.id
        ORDER BY m.id DESC
    ''')
    matches = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return matches


def get_match_detail(match_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM matches WHERE id = ?', (match_id,))
    match = cursor.fetchone()
    if not match:
        conn.close()
        return None
    match = dict(match)

    cursor.execute(
        'SELECT * FROM player_stats WHERE match_id = ? ORDER BY rating DESC',
        (match_id,)
    )
    players = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {'match': match, 'players': players}


def delete_match(match_id):
    conn = get_connection()
    cursor = conn.cursor()
    # 先获取demo_file_name用于清理MD5记录
    cursor.execute('SELECT demo_file_name FROM matches WHERE id = ?', (match_id,))
    row = cursor.fetchone()
    demo_file_name = row['demo_file_name'] if row else ''
    cursor.execute('DELETE FROM player_stats WHERE match_id = ?', (match_id,))
    cursor.execute('DELETE FROM matches WHERE id = ?', (match_id,))
    # 同步清理MD5去重记录
    if demo_file_name:
        cursor.execute('DELETE FROM parsed_files WHERE demo_file_name = ?', (demo_file_name,))
    # 如果matches表已清空，重置自增序号，让新比赛从1开始
    cursor.execute('SELECT COUNT(*) FROM matches')
    if cursor.fetchone()[0] == 0:
        cursor.execute('DELETE FROM sqlite_sequence WHERE name IN ("matches", "player_stats")')
    conn.commit()
    conn.close()


def delete_all_matches():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM player_stats')
    cursor.execute('DELETE FROM matches')
    # 清空MD5去重记录
    cursor.execute('DELETE FROM parsed_files')
    # 重置自增序号，让新比赛从1开始
    cursor.execute('DELETE FROM sqlite_sequence WHERE name IN ("matches", "player_stats", "parsed_files")')
    conn.commit()
    conn.close()


def get_leaderboard():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            player_name,
            COUNT(DISTINCT match_id) as matches_played,
            SUM(kills) as total_kills,
            SUM(deaths) as total_deaths,
            SUM(assists) as total_assists,
            ROUND(AVG(adr), 1) as avg_adr,
            ROUND(AVG(kpr), 3) as avg_kpr,
            ROUND(AVG(dpr), 3) as avg_dpr,
            ROUND(AVG(kast), 1) as avg_kast,
            ROUND(AVG(impact), 3) as avg_impact,
            ROUND(AVG(rating), 3) as avg_rating
        FROM player_stats
        GROUP BY player_name
        ORDER BY avg_rating DESC
    ''')
    players = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return players


# ── MD5 dedup functions ──

def is_file_parsed(file_path, file_md5):
    """Check if a file has already been parsed by path+md5."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id FROM parsed_files WHERE file_path = ? AND file_md5 = ?',
        (file_path, file_md5)
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


def record_parsed_file(file_path, file_md5, file_size=0, demo_file_name=''):
    """Record a parsed file for dedup."""
    from datetime import datetime
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO parsed_files (file_path, file_md5, file_size, parsed_at, demo_file_name)
        VALUES (?, ?, ?, ?, ?)
    ''', (file_path, file_md5, file_size, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), demo_file_name))
    conn.commit()
    conn.close()


def delete_parsed_file_by_name(demo_file_name):
    """删除指定demo文件名对应的MD5去重记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM parsed_files WHERE demo_file_name = ?', (demo_file_name,))
    conn.commit()
    conn.close()


def clear_all_parsed_files():
    """清空所有MD5去重记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM parsed_files')
    conn.commit()
    conn.close()


def get_parsed_md5_set():
    """Return set of all parsed file MD5 values."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT file_md5 FROM parsed_files')
    md5s = {row['file_md5'] for row in cursor.fetchall()}
    conn.close()
    return md5s


# ── Series functions ──

def create_series(name, bo_count, match_ids):
    """创建系列赛并关联多场比赛"""
    from datetime import datetime
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO series (name, bo_count, created_at) VALUES (?, ?, ?)',
                   (name, bo_count, now))
    series_id = cursor.lastrowid
    for idx, mid in enumerate(match_ids):
        cursor.execute('INSERT INTO series_matches (series_id, match_id, sort_order) VALUES (?, ?, ?)',
                       (series_id, mid, idx))
    conn.commit()
    conn.close()
    return series_id


def get_all_series():
    """获取所有系列赛"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, COUNT(sm.match_id) as match_count
        FROM series s
        LEFT JOIN series_matches sm ON sm.series_id = s.id
        GROUP BY s.id
        ORDER BY s.id DESC
    ''')
    series = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return series


def get_series_detail(series_id):
    """获取系列赛详情，包含所有关联比赛和综合统计"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM series WHERE id = ?', (series_id,))
    series = cursor.fetchone()
    if not series:
        conn.close()
        return None
    series = dict(series)

    cursor.execute('''
        SELECT m.* FROM matches m
        JOIN series_matches sm ON sm.match_id = m.id
        WHERE sm.series_id = ?
        ORDER BY sm.sort_order
    ''', (series_id,))
    matches = [dict(row) for row in cursor.fetchall()]

    # 获取所有比赛的玩家数据
    match_ids = [m['id'] for m in matches]
    all_players = {}
    for mid in match_ids:
        cursor.execute('SELECT * FROM player_stats WHERE match_id = ?', (mid,))
        for row in cursor.fetchall():
            p = dict(row)
            name = p['player_name']
            if name not in all_players:
                all_players[name] = {
                    'player_name': name,
                    'team_number': p.get('team_number', 0),
                    'kills': 0, 'deaths': 0, 'assists': 0,
                    'total_damage': 0, 'rounds_played': 0,
                    'multikill_rounds': 0, 'kill_rounds': 0,
                    'assist_rounds': 0, 'survive_rounds': 0, 'trade_rounds': 0,
                    'matches_played': 0,
                }
            ap = all_players[name]
            ap['kills'] += p['kills']
            ap['deaths'] += p['deaths']
            ap['assists'] += p['assists']
            ap['total_damage'] += p['total_damage']
            ap['rounds_played'] += p['rounds_played']
            ap['multikill_rounds'] += p['multikill_rounds']
            ap['kill_rounds'] += p['kill_rounds']
            ap['assist_rounds'] += p['assist_rounds']
            ap['survive_rounds'] += p['survive_rounds']
            ap['trade_rounds'] += p['trade_rounds']
            ap['matches_played'] += 1

    # 计算综合rating
    players_list = []
    for p in all_players.values():
        rp = max(p['rounds_played'], 1)
        p['kpr'] = round(p['kills'] / rp, 3)
        p['dpr'] = round(p['deaths'] / rp, 3)
        p['adr'] = round(p['total_damage'] / rp, 1)
        p['kast'] = round(100.0 * sum(1 for _ in range(rp) if True) / rp, 1) if rp > 0 else 0
        p['impact'] = round(2.13 * p['kpr'] + 0.42 * (p['assists'] / rp) - 0.41, 3)
        p['rating'] = round(0.0073 * p['adr'] + 0.3591 * p['kpr'] - 0.5329 * p['dpr'] +
                            0.2372 * p['impact'] + 0.0032 * p['kast'] + 0.1587, 3)
        players_list.append(p)

    players_list.sort(key=lambda x: x['rating'], reverse=True)

    # 计算系列赛总比分
    team_a_total = sum(m.get('team_a_score', 0) for m in matches)
    team_b_total = sum(m.get('team_b_score', 0) for m in matches)

    conn.close()
    return {
        'series': series,
        'matches': matches,
        'players': players_list,
        'team_a_total': team_a_total,
        'team_b_total': team_b_total,
    }


def delete_series(series_id):
    """删除系列赛（不删除关联的比赛）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM series_matches WHERE series_id = ?', (series_id,))
    cursor.execute('DELETE FROM series WHERE id = ?', (series_id,))
    conn.commit()
    conn.close()
