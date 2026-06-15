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
    conn.commit()
    conn.close()


def save_match(map_name, date, duration, demo_file_name, players_data, demo_guid=''):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO matches (map_name, date, duration, demo_file_name, demo_guid) VALUES (?, ?, ?, ?, ?)',
        (map_name, date, duration, demo_file_name, demo_guid)
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
    cursor.execute('DELETE FROM player_stats WHERE match_id = ?', (match_id,))
    cursor.execute('DELETE FROM matches WHERE id = ?', (match_id,))
    conn.commit()
    conn.close()


def delete_all_matches():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM player_stats')
    cursor.execute('DELETE FROM matches')
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
