from collections import defaultdict
import pandas as pd
import math


def safe_int(val, default=1):
    try:
        v = int(float(str(val)))
        return v if v >= 1 else default
    except (ValueError, TypeError):
        return default


def parse_demo(file_path):
    import awpy.demo
    import awpy.parsers

    def empty_with_tick(events):
        return pd.DataFrame({'tick': pd.Series(dtype='int'), 'start_tick': pd.Series(dtype='int')})

    awpy.parsers.parse_smokes = empty_with_tick
    awpy.demo.parse_smokes = empty_with_tick
    awpy.parsers.parse_bomb = empty_with_tick
    awpy.demo.parse_bomb = empty_with_tick
    awpy.parsers.parse_infernos = empty_with_tick
    awpy.demo.parse_infernos = empty_with_tick
    awpy.parsers.parse_grenades = empty_with_tick
    awpy.demo.parse_grenades = empty_with_tick
    awpy.parsers.parse_weapon_fires = lambda events: pd.DataFrame({'tick': pd.Series(dtype='int')})
    awpy.demo.parse_weapon_fires = awpy.parsers.parse_weapon_fires
    awpy.parsers.parse_ticks = lambda *a, **kw: pd.DataFrame({'tick': pd.Series(dtype='int')})
    awpy.demo.parse_ticks = awpy.parsers.parse_ticks

    original_remove = awpy.parsers.remove_nonplay_ticks

    def patched_remove(parsed_df):
        required = [
            "is_freeze_period", "is_warmup_period", "is_terrorist_timeout",
            "is_ct_timeout", "is_technical_timeout", "is_waiting_for_resume",
            "is_match_started", "game_phase"
        ]
        missing = [c for c in required if c not in parsed_df.columns]
        if missing:
            return parsed_df
        for col in required[:7]:
            parsed_df[col] = parsed_df[col].fillna(False).astype(bool)
        parsed_df = parsed_df[
            (~parsed_df["is_freeze_period"])
            & (~parsed_df["is_warmup_period"])
            & (~parsed_df["is_terrorist_timeout"])
            & (~parsed_df["is_ct_timeout"])
            & (~parsed_df["is_technical_timeout"])
            & (~parsed_df["is_waiting_for_resume"])
            & (parsed_df["is_match_started"])
            & (parsed_df["game_phase"].isin([2, 3]))
        ]
        return parsed_df.drop(columns=required)

    awpy.parsers.remove_nonplay_ticks = patched_remove

    from awpy.demo import Demo

    demo = Demo(file_path)
    awpy.parsers.remove_nonplay_ticks = original_remove

    header = getattr(demo, 'header', None) or {}
    map_name = '练习模式'
    demo_guid = ''
    if isinstance(header, dict):
        mn = (header.get('mapName') or header.get('map_name', '') or '')
        if mn:
            map_name = mn
        demo_guid = header.get('demo_version_guid', '') or ''

    kills_df = getattr(demo, 'kills', None)
    damages_df = getattr(demo, 'damages', None)
    rounds_data = getattr(demo, 'rounds', None)

    total_rounds = len(rounds_data) if rounds_data is not None and hasattr(rounds_data, '__len__') else 1

    # Extract player_info: name, team_number, entity_id
    player_items = []
    team_groups = defaultdict(list)
    try:
        pi = demo.parser.parse_player_info()
        if pi is not None and len(pi) > 0:
            for _, row in pi.iterrows():
                name = str(row.get('name', '')).strip()
                if not name or name == 'nan':
                    continue
                tn = row.get('team_number', '') or ''
                try:
                    tn_int = int(float(str(tn)))
                except (ValueError, TypeError):
                    tn_int = 0
                eid = None
                for c in pi.columns:
                    if c.lower() in ('entity_id', 'entindex', 'entityid', 'userid'):
                        val = row[c]
                        if pd.notna(val):
                            try:
                                eid = int(float(str(val)))
                                break
                            except (ValueError, TypeError):
                                pass
                player_items.append((name, tn_int, eid))
                team_groups[tn_int].append(name)
    except Exception:
        pass

    # ── Phase 1: parse kills for kills/deaths/assists + multikill + trade ──
    name_kill_counts = defaultdict(int)
    name_death_counts = defaultdict(int)
    name_assist_counts = defaultdict(int)
    named_kill_total = 0

    # Per-round kill tracking for multikill and trade calculation
    # round_kills_data[round_num] = [(tick, killer, victim), ...]
    round_kills_data = defaultdict(list)

    if kills_df is not None and hasattr(kills_df, 'iterrows'):
        for _, kill in kills_df.iterrows():
            kill_dict = kill.to_dict() if hasattr(kill, 'to_dict') else dict(kill)
            killer = str(kill_dict.get('attacker_name', '') or '').strip()
            victim = str(kill_dict.get('victim_name', '') or '').strip()
            assister = kill_dict.get('assister_name', None)
            if assister is not None:
                assister = str(assister).strip()
                if assister in ('nan', ''):
                    assister = None
            round_num = safe_int(kill_dict.get('round', 1))
            tick = int(kill_dict.get('tick', 0) or 0)

            if killer and killer != 'nan':
                name_kill_counts[killer] += 1
                named_kill_total += 1

            if victim and victim != 'nan':
                name_death_counts[victim] += 1

            if assister:
                name_assist_counts[assister] += 1

            if killer and killer != 'nan' and victim and victim != 'nan':
                round_kills_data[round_num].append((tick, killer, victim))

    # ── Phase 2: parse entity_killed + player_death for entity-indexed kills ──
    ent_kill_counts = defaultdict(int)
    ent_death_counts = defaultdict(int)
    ent_kill_total = 0
    ek = None

    try:
        ek = demo.parser.parse_event('entity_killed')
        pd_ev = demo.parser.parse_event('player_death')

        if ek is not None and len(ek) > 0:
            for _, kr in ek.iterrows():
                a_ent = int(kr['entindex_attacker'])
                v_ent = int(kr['entindex_killed'])
                ent_kill_counts[a_ent] += 1
                ent_death_counts[v_ent] += 1
                ent_kill_total += 1
    except Exception:
        pass

    # Build entity→name mapping
    ent_to_name = {}
    if player_items:
        for name, tn, eid in player_items:
            if eid is not None:
                ent_to_name[eid] = name

    if not ent_to_name:
        all_ents = sorted(set(ent_kill_counts.keys()) | set(ent_death_counts.keys()))
        for i, (name, tn, eid) in enumerate(player_items):
            if i < len(all_ents):
                ent_to_name[all_ents[i]] = name

    # ── Phase 2b: if round_kills_data is empty, populate from entity_killed ──
    if not round_kills_data and ent_to_name and ek is not None and len(ek) > 0:
        # Build tick→round mapping from rounds_data
        tick_to_round = {}
        if rounds_data is not None and hasattr(rounds_data, 'iterrows'):
            for _, rnd_row in rounds_data.iterrows():
                rnd_num = safe_int(rnd_row.get('round', 1))
                start_tick = int(rnd_row.get('start', 0) or 0)
                end_tick = int(rnd_row.get('end', 0) or 0)
                tick_to_round[(start_tick, end_tick)] = rnd_num

        def find_round_for_tick(tick):
            for (start, end), rnd_num in tick_to_round.items():
                if start <= tick <= end:
                    return rnd_num
            # Fallback: find closest round start before tick
            best_round = 0
            best_start = -1
            for (start, end), rnd_num in tick_to_round.items():
                if start <= tick and start > best_start:
                    best_start = start
                    best_round = rnd_num
            return best_round if best_round > 0 else 1

        for _, kr in ek.iterrows():
            a_ent = int(kr['entindex_attacker'])
            v_ent = int(kr['entindex_killed'])
            tick = int(kr['tick'])
            killer_name = ent_to_name.get(a_ent)
            victim_name = ent_to_name.get(v_ent)
            if killer_name and victim_name:
                rnd_num = find_round_for_tick(tick)
                round_kills_data[rnd_num].append((tick, killer_name, victim_name))

    # ── Phase 3: merge kill/death/assist data ──
    named_players = set(name_kill_counts.keys()) | set(name_death_counts.keys())

    kill_source = defaultdict(int)
    death_source = defaultdict(int)
    assist_source = defaultdict(int)

    for name in named_players:
        kill_source[name] = name_kill_counts.get(name, 0)
        death_source[name] = name_death_counts.get(name, 0)
        assist_source[name] = name_assist_counts.get(name, 0)

    if named_kill_total < ent_kill_total and ent_to_name:
        for ent, name in ent_to_name.items():
            if name not in named_players:
                kill_source[name] += ent_kill_counts.get(ent, 0)
                death_source[name] += ent_death_counts.get(ent, 0)
    elif named_kill_total == 0:
        for ent, name in ent_to_name.items():
            kill_source[name] += ent_kill_counts.get(ent, 0)
            death_source[name] += ent_death_counts.get(ent, 0)

    # ── Phase 4: calculate damage using player_hurt events ──
    # Track victim HP per round, compute actual damage as hp_before - health_after
    damage_source = defaultdict(int)
    # round_damage[player_name][round_num] = total damage in that round
    round_damage = defaultdict(lambda: defaultdict(int))
    # round_attacker_victim_dmg[attacker][victim][round] = damage dealt by attacker to victim in that round
    # Used for KAST assist validation (>25 dmg to victim)
    round_attacker_victim_dmg = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    try:
        ph = demo.parser.parse_event('player_hurt')
        if ph is not None and len(ph) > 0:
            # Build tick→round mapping from damages_df
            tick_round = {}
            if damages_df is not None and 'round' in damages_df.columns:
                for _, row in damages_df.iterrows():
                    tick_round[row['tick']] = int(row['round'])

            # Track victim HP state: victim_state[(victim_name, round)] = current_hp
            victim_state = {}

            ph_sorted = ph.sort_values('tick')

            for _, row in ph_sorted.iterrows():
                attacker = str(row.get('attacker_name', '') or '').strip()
                victim = str(row.get('user_name', '') or '').strip()
                tick = row['tick']
                health_after = int(row.get('health', 0) or 0)
                rnd = tick_round.get(tick, 0)

                if not attacker or attacker == 'nan' or not victim or victim == 'nan':
                    continue
                if not rnd:
                    continue

                key = (victim, rnd)

                if key not in victim_state:
                    victim_state[key] = 100

                hp_before = victim_state[key]
                actual_dmg = hp_before - health_after
                if actual_dmg < 0:
                    actual_dmg = 0

                damage_source[attacker] += actual_dmg
                round_damage[attacker][rnd] += actual_dmg
                round_attacker_victim_dmg[attacker][victim][rnd] += actual_dmg
                victim_state[key] = health_after
    except Exception:
        pass

    # Fallback: if player_hurt parsing failed, use damages_df dmg_health_real
    if not damage_source and damages_df is not None and hasattr(damages_df, 'iterrows'):
        if 'dmg_health_real' in damages_df.columns:
            for _, row in damages_df.iterrows():
                attacker = str(row.get('attacker_name', '') or '').strip()
                if attacker and attacker != 'nan':
                    dmg = int(row.get('dmg_health_real', 0) or 0)
                    rnd = safe_int(row.get('round', 1))
                    damage_source[attacker] += dmg
                    round_damage[attacker][rnd] += dmg
        elif 'dmg_health' in damages_df.columns:
            for _, row in damages_df.iterrows():
                attacker = str(row.get('attacker_name', '') or '').strip()
                if attacker and attacker != 'nan':
                    dmg = int(row.get('dmg_health', 0) or 0)
                    rnd = safe_int(row.get('round', 1))
                    damage_source[attacker] += dmg
                    round_damage[attacker][rnd] += dmg

    # ── Phase 5: calculate multikill, trade, and KAST components ──
    # Build team mapping from player_info
    name_to_team_full = {}
    for name, tn, eid in player_items:
        name_to_team_full[name] = tn

    # Multikill: rounds where a player got 2+ kills
    player_multikill_rounds = defaultdict(int)
    for rnd, kills_list in round_kills_data.items():
        player_round_kills = defaultdict(int)
        for tick, killer, victim in kills_list:
            player_round_kills[killer] += 1
        for name, cnt in player_round_kills.items():
            if cnt >= 2:
                player_multikill_rounds[name] += 1

    # Trade kill: a player kills the enemy who killed their teammate within 5 seconds
    # Traded death: a player was killed, but a teammate killed the killer within 5 seconds
    TRADE_WINDOW_TICKS = 128 * 5  # 5 seconds at 128 tick
    player_trade_kill_rounds = defaultdict(int)
    player_traded_death_rounds = defaultdict(set)  # name -> set of rounds where death was traded

    for rnd, kills_list in round_kills_data.items():
        kills_sorted = sorted(kills_list, key=lambda x: x[0])
        trade_killers_this_round = set()
        for i, (tick_i, killer_i, victim_i) in enumerate(kills_sorted):
            victim_team = name_to_team_full.get(victim_i, 0)
            for j in range(i + 1, len(kills_sorted)):
                tick_j, killer_j, victim_j = kills_sorted[j]
                if tick_j - tick_i > TRADE_WINDOW_TICKS:
                    break
                killer_j_team = name_to_team_full.get(killer_j, 0)
                if killer_j_team == victim_team and victim_j == killer_i:
                    trade_killers_this_round.add(killer_j)
                    player_traded_death_rounds[victim_i].add(rnd)
        for name in trade_killers_this_round:
            player_trade_kill_rounds[name] += 1

    # Kill rounds: rounds where a player got at least 1 kill (K in KAST)
    player_kill_rounds_set = defaultdict(set)  # name -> set of rounds with kills
    for rnd, kills_list in round_kills_data.items():
        for tick, killer, victim in kills_list:
            player_kill_rounds_set[killer].add(rnd)

    # Assist rounds with >25 damage validation (A in KAST)
    # A player gets an assist round if they dealt >25 damage to a victim who was killed in that round
    player_assist_rounds_set = defaultdict(set)  # name -> set of rounds with valid assists
    if kills_df is not None and hasattr(kills_df, 'iterrows'):
        for _, kill in kills_df.iterrows():
            kill_dict = kill.to_dict() if hasattr(kill, 'to_dict') else dict(kill)
            assister = kill_dict.get('assister_name', None)
            if assister is not None:
                assister = str(assister).strip()
                if assister in ('nan', ''):
                    assister = None
            victim = str(kill_dict.get('victim_name', '') or '').strip()
            rnd = safe_int(kill_dict.get('round', 1))

            if assister and victim and victim != 'nan':
                # Check if assister dealt >25 damage to this victim in this round
                dmg_to_victim = round_attacker_victim_dmg.get(assister, {}).get(victim, {}).get(rnd, 0)
                if dmg_to_victim > 25:
                    player_assist_rounds_set[assister].add(rnd)

    # Survive rounds (S in KAST): rounds where player did not die
    player_death_rounds = defaultdict(set)
    for rnd, kills_list in round_kills_data.items():
        for tick, killer, victim in kills_list:
            player_death_rounds[victim].add(rnd)

    # Traded death rounds (T in KAST): already tracked in player_traded_death_rounds

    # ── Phase 6: build result with KAST calculation ──
    all_names = set()
    name_to_team = {}
    for name, tn, eid in player_items:
        all_names.add(name)
        name_to_team[name] = tn
    for name in kill_source:
        all_names.add(name)
    for name in death_source:
        all_names.add(name)
    for name in damage_source:
        all_names.add(name)

    result = []
    for name in all_names:
        rp = max(total_rounds if total_rounds > 0 else 1, 1)
        kills = kill_source.get(name, 0)
        deaths = death_source.get(name, 0)
        assists = assist_source.get(name, 0)
        dmg = damage_source.get(name, 0)
        tn = name_to_team.get(name, 0)

        kpr = kills / rp
        dpr = deaths / rp
        adr = dmg / rp

        # KAST = 100 * sum(1 for i in range(n) if K[i] or A[i] or S[i] or T[i]) / n
        # K[i]: got at least 1 kill in round i
        # A[i]: assist with >25 dmg to victim who was killed in round i
        # S[i]: survived round i
        # T[i]: died in round i but was traded (teammate killed killer within 5s)
        k_set = player_kill_rounds_set.get(name, set())
        a_set = player_assist_rounds_set.get(name, set())
        death_set = player_death_rounds.get(name, set())
        t_set = player_traded_death_rounds.get(name, set())

        n = rp
        kast = 100.0 * sum(1 for i in range(1, n + 1)
                           if (i in k_set) or (i in a_set) or (i not in death_set) or (i in t_set)) / n

        multikill_rounds = player_multikill_rounds.get(name, 0)
        trade_rounds = player_trade_kill_rounds.get(name, 0)
        kill_rounds = len(k_set)
        assist_rounds_val = len(a_set)
        survive_rounds = n - len(death_set)

        impact = 2.13 * kpr + 0.42 * (assists / rp) - 0.41

        rating = (0.0073 * adr + 0.3591 * kpr - 0.5329 * dpr +
                  0.2372 * impact + 0.0032 * kast + 0.1587)

        result.append({
            'player_name': name,
            'kills': kills,
            'deaths': deaths,
            'assists': assists,
            'total_damage': dmg,
            'rounds_played': rp,
            'adr': round(adr, 1),
            'kpr': round(kpr, 3),
            'dpr': round(dpr, 3),
            'kast': round(kast, 1),
            'impact': round(impact, 3),
            'rating': round(rating, 3),
            'multikill_rounds': multikill_rounds,
            'kill_rounds': kill_rounds,
            'assist_rounds': assist_rounds_val,
            'survive_rounds': survive_rounds,
            'trade_rounds': trade_rounds,
            'team_number': tn,
        })

    result.sort(key=lambda x: x['rating'], reverse=True)
    return map_name, result, total_rounds, demo_guid, team_groups
