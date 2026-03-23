import datetime

from mlb_stats import (
    get_probable_starters,
    get_pitcher_stats,
    get_batter_stats,
    get_transactions,
    get_schedule_density,
    get_player_teams,
    get_team_batting_stats,
)

INJURY_STATUSES = {'DTD', 'IL10', 'IL15', 'IL60', 'NA', 'SUSP', 'IL'}
ACTIVE_BATTER_SLOTS = {'C', '1B', '2B', '3B', 'SS', 'OF', 'Util'}
ACTIVE_PITCHER_SLOTS = {'SP', 'RP', 'P'}
IL_SLOTS = {'IL'}
BN_SLOTS = {'BN'}
BATTER_POSITIONS = ['C', '1B', '2B', '3B', 'SS', 'OF']

# Scoring weights for SP streaming: lower result = better pickup
# Rewards low ERA/WHIP, high K/9, home starts, weak opponent offense
def _stream_score(stats, home, opp_ops=None):
    era  = stats.get('era',  5.00) or 5.00
    whip = stats.get('whip', 1.50) or 1.50
    k9   = stats.get('k9',   7.00) or 7.00
    home_bonus = 0.10 if home else 0
    # League average OPS ~0.720; scale penalty/bonus around that
    opp_bonus  = (0.720 - (opp_ops or 0.720)) * 2
    return era + (whip * 2) - (k9 * 0.15) - home_bonus - opp_bonus


def analyze(lg, section=None, days=3, progress=None):
    """
    Run analysis and return a structured dict with keys:
      injuries, streaming, waivers, categories

    All values are JSON-serializable so callers (CLI, API, UI) can format freely.
    Passing section= restricts to one key.
    progress: optional callable(msg: str) for status updates — keeps this layer print-free.
    """
    def _prog(msg):
        if progress:
            progress(msg)

    _prog("Fetching roster...")
    tm = lg.to_team(lg.team_key())
    roster = tm.roster(day=datetime.date.today())

    runners = {
        'injuries':        lambda: _injuries(roster),
        'streaming':       lambda: _streaming(lg, roster, days, _prog),
        'waivers':         lambda: _waivers(lg, roster, _prog),
        'waiver_pitchers': lambda: _waiver_pitchers(lg, _prog),
        'categories':      lambda: _categories(lg),
        'news':            lambda: _news(roster, _prog),
    }

    if section:
        if section not in runners:
            raise ValueError(f"Unknown section '{section}'. Choose from: {list(runners)}")
        _prog(f"Running {section} analysis...")
        return {section: runners[section]()}

    results = {}
    for key, fn in runners.items():
        _prog(f"Running {key} analysis...")
        results[key] = fn()
    return results


# ---------------------------------------------------------------------------
# Section: injuries
# ---------------------------------------------------------------------------

def _injuries(roster):
    """
    Returns list of alert dicts. Each has:
      type, player_name, player_id, status, current_slot, action
    """
    alerts = []
    for p in roster:
        status = p.get('status', '')
        slot   = p['selected_position']
        name   = p['name']
        pid    = p['player_id']

        if status in INJURY_STATUSES:
            if slot not in IL_SLOTS and slot not in BN_SLOTS:
                alerts.append({
                    'type': 'injured_in_active_slot',
                    'player_name': name,
                    'player_id': pid,
                    'status': status,
                    'current_slot': slot,
                    'action': 'Move to IL or BN immediately',
                })
            elif slot in BN_SLOTS:
                alerts.append({
                    'type': 'il_eligible_on_bench',
                    'player_name': name,
                    'player_id': pid,
                    'status': status,
                    'current_slot': slot,
                    'action': 'Move to IL slot to free up a BN spot',
                })
        elif not status and slot in IL_SLOTS:
            alerts.append({
                'type': 'healthy_in_il_slot',
                'player_name': name,
                'player_id': pid,
                'status': 'Active',
                'current_slot': slot,
                'action': 'Return to active roster',
            })

    return alerts


# ---------------------------------------------------------------------------
# Section: streaming
# ---------------------------------------------------------------------------

def _streaming(lg, roster, days, progress=None):
    """
    Returns:
      targets      - ranked list of available pitchers with upcoming starts
      drop_candidates - your active pitchers with no start in the window
    """
    def _p(msg):
        if progress: progress(msg)

    _p("Fetching probable starters from MLB...")
    probable = get_probable_starters(days)
    if not probable:
        return {'targets': [], 'drop_candidates': []}

    probable_by_name = {}
    for p in probable:
        # A pitcher can start multiple times; keep their next start
        if p['name'] not in probable_by_name:
            probable_by_name[p['name']] = p
        else:
            # Accumulate extra start dates
            existing = probable_by_name[p['name']]
            existing.setdefault('extra_starts', []).append(p['date'])

    # Collect available pitchers from FA and waivers
    _p("Fetching available pitchers from Yahoo...")
    available = {}
    for p in lg.free_agents('P'):
        available[p['name']] = {'source': 'FA', **p}
    for p in lg.waivers():
        pos = p.get('eligible_positions', [])
        if any(x in pos for x in ('SP', 'RP', 'P')):
            available[p['name']] = {'source': 'W', **p}

    # Intersect: available pitchers who have an upcoming start
    matched_names = [n for n in available if n in probable_by_name]
    _p(f"Fetching stats for {len(matched_names)} streaming candidates...")
    stats = get_pitcher_stats(matched_names)

    _p("Fetching team batting stats for matchup quality...")
    team_batting = get_team_batting_stats()

    targets = []
    for name in matched_names:
        player   = available[name]
        start    = probable_by_name[name]
        pstats   = stats.get(name, {})
        opp_ops  = team_batting.get(start['opponent'], {}).get('ops')
        targets.append({
            'player_name':   name,
            'player_id':     player['player_id'],
            'source':        player['source'],
            'percent_owned': player.get('percent_owned', 0),
            'start_date':    start['date'],
            'opponent':      start['opponent'],
            'opp_ops':       opp_ops,
            'home':          start['home'],
            'extra_starts':  start.get('extra_starts', []),
            'stats':         pstats,
            'score':         _stream_score(pstats, start['home'], opp_ops),
        })

    targets.sort(key=lambda x: x['score'])

    # Flag active pitchers on your roster with no start in the window
    probable_names = set(probable_by_name)
    drop_candidates = [
        {
            'player_name': p['name'],
            'player_id':   p['player_id'],
            'slot':        p['selected_position'],
        }
        for p in roster
        if p['selected_position'] in ACTIVE_PITCHER_SLOTS
        and p['name'] not in probable_names
        and p.get('status', '') not in INJURY_STATUSES
    ]

    return {'targets': targets, 'drop_candidates': drop_candidates}


# ---------------------------------------------------------------------------
# Section: waivers
# ---------------------------------------------------------------------------

def _waivers(lg, roster, progress=None):
    """
    Returns dict keyed by position. Each value is a list of upgrade dicts:
      candidate  - {name, player_id, source, percent_owned, stats}
      replaces   - {name, player_id, stats}
      ops_delta  - OPS difference (candidate - your player)

    Primary comparison metric: OPS (covers AVG, OBP, SLG).
    Full stats returned so you can weigh SB/HR/R yourself.
    Only surfaces candidates with >= 20 PA this season.
    """
    def _p(msg):
        if progress: progress(msg)

    starters_by_pos = {}
    for p in roster:
        slot = p['selected_position']
        if slot in ACTIVE_BATTER_SLOTS:
            starters_by_pos.setdefault(slot, []).append(p)

    results = {}

    for pos in BATTER_POSITIONS:
        _p(f"Checking {pos} free agents...")
        candidates = list(lg.free_agents(pos))
        for p in lg.waivers():
            if pos in p.get('eligible_positions', []):
                candidates.append(p)

        # Rank by percent_owned, evaluate top 10 only to limit API calls
        candidates.sort(key=lambda p: p.get('percent_owned', 0), reverse=True)
        top = candidates[:10]
        if not top:
            continue

        cand_stats  = get_batter_stats([p['name'] for p in top])

        your_players = starters_by_pos.get(pos, [])
        your_stats   = get_batter_stats([p['name'] for p in your_players])

        upgrades = []
        for c in top:
            cs = cand_stats.get(c['name'], {})
            if not cs or cs.get('pa', 0) < 20:
                continue
            c_ops = cs.get('obp', 0) + cs.get('slg', 0)

            if not your_players:
                # Empty slot — flag as fill
                upgrades.append({
                    'candidate': _batter_summary(c, cs),
                    'replaces': None,
                    'ops_delta': c_ops,
                })
                continue

            worst = min(
                your_players,
                key=lambda p: (
                    (your_stats.get(p['name'], {}).get('obp', 0) or 0)
                    + (your_stats.get(p['name'], {}).get('slg', 0) or 0)
                )
            )
            ws = your_stats.get(worst['name'], {})
            w_ops = ws.get('obp', 0) + ws.get('slg', 0)
            delta = c_ops - w_ops

            if delta > 0.060:  # meaningful OPS gap
                upgrades.append({
                    'candidate': _batter_summary(c, cs),
                    'replaces':  _batter_summary(worst, ws),
                    'ops_delta': round(delta, 3),
                })

        if upgrades:
            results[pos] = upgrades

    return results


def _waiver_pitchers(lg, progress=None):
    """
    Returns list of available RP/closer targets ranked by saves + holds.
    Each dict: {name, player_id, source, percent_owned, stats, sv_hld}
    """
    def _p(msg):
        if progress: progress(msg)

    _p("Fetching available relief pitchers...")
    candidates = {}
    for p in lg.free_agents('RP'):
        candidates[p['name']] = {'source': 'FA', **p}
    for p in lg.waivers():
        if 'RP' in p.get('eligible_positions', []):
            candidates[p['name']] = {'source': 'W', **p}

    top = sorted(candidates.values(), key=lambda p: p.get('percent_owned', 0), reverse=True)[:15]
    if not top:
        return []

    _p(f"Fetching stats for {len(top)} RP candidates...")
    cand_stats = get_pitcher_stats([p['name'] for p in top])

    results = []
    for c in top:
        cs = cand_stats.get(c['name'], {})
        if not cs or cs.get('ip', 0) < 5:
            continue
        sv_hld = cs.get('saves', 0) + cs.get('holds', 0)
        results.append({
            'name':          c['name'],
            'player_id':     c['player_id'],
            'source':        c['source'],
            'percent_owned': c.get('percent_owned', 0),
            'stats':         cs,
            'sv_hld':        sv_hld,
        })

    results.sort(key=lambda x: (-x['sv_hld'], x['stats'].get('era', 99)))
    return results


def _batter_summary(player, stats):
    return {
        'name':          player['name'],
        'player_id':     player['player_id'],
        'source':        player.get('source', player.get('status', 'FA')),
        'percent_owned': player.get('percent_owned', 0),
        'stats':         stats,
    }


# ---------------------------------------------------------------------------
# Section: categories
# ---------------------------------------------------------------------------

LOWER_IS_BETTER = {'ERA', 'WHIP'}


def _build_stat_id_map(lg):
    """Return {stat_id_str: display_name} from league settings."""
    import objectpath
    t = objectpath.Tree(lg.yhandler.get_settings_raw(lg.league_id))
    mapping = {}
    for s in t.execute('$..stat_categories..stat'):
        if 'stat_id' in s and 'display_name' in s:
            mapping[str(s['stat_id'])] = s['display_name']
    return mapping


def _categories(lg):
    """
    Parse the current week's H2H scoreboard.
    Returns:
      week    - current week number
      my_team - team name
      opp     - opponent team name
      cats    - list of {category, mine, theirs, winning}
      leading - count of categories you're winning
    """
    try:
        stat_map = _build_stat_id_map(lg)
        raw = lg.matchups()

        # Yahoo wraps league data as [meta_dict, sub_list_or_dict].
        # league[1] can be either a dict or a list of dicts depending on endpoint version.
        league = raw.get('fantasy_content', {}).get('league', [{}, {}])
        league_tail = league[1] if len(league) > 1 else {}
        if isinstance(league_tail, list):
            league_tail = {k: v for d in league_tail if isinstance(d, dict) for k, v in d.items()}

        scoreboard = (league_tail.get('scoreboard', {})
                                 .get('0', {})
                                 .get('matchups', {}))
        my_key = lg.team_key()
        week   = league_tail.get('scoreboard', {}).get('week')

        my_stats  = {}
        opp_stats = {}
        my_name   = ''
        opp_name  = ''

        for i in range(len(scoreboard) - 1):  # last key is 'count'
            matchup = scoreboard.get(str(i), {}).get('matchup', {})
            teams   = matchup.get('0', {}).get('teams', {})

            for slot in ('0', '1'):
                team_data = teams.get(slot, {}).get('team', [{}, {}])
                meta  = team_data[0] if isinstance(team_data, list) else {}
                tkey  = _extract_team_key(meta)
                tname = _extract_team_name(meta)
                stats = _extract_stats(team_data, stat_map)
                if tkey == my_key:
                    my_stats = stats
                    my_name  = tname
                else:
                    opp_stats = stats
                    opp_name  = tname

            if my_stats:
                break

        # Build ordered list using the league's own stat categories
        cats = []
        for display_name in stat_map.values():
            mine   = my_stats.get(display_name)
            theirs = opp_stats.get(display_name)
            if mine is None and theirs is None:
                continue
            try:
                mine   = float(mine   or 0)
                theirs = float(theirs or 0)
            except (TypeError, ValueError):
                continue
            if display_name in LOWER_IS_BETTER:
                winning = mine < theirs
            else:
                winning = mine > theirs
            cats.append({
                'category': display_name,
                'mine':     mine,
                'theirs':   theirs,
                'winning':  winning,
            })

        return {
            'week':    week,
            'my_team': my_name,
            'opp':     opp_name,
            'cats':    cats,
            'leading': sum(1 for c in cats if c['winning']),
        }

    except Exception as e:
        return {'error': str(e)}


def _extract_team_key(meta):
    if isinstance(meta, list):
        for item in meta:
            if isinstance(item, dict) and 'team_key' in item:
                return item['team_key']
    if isinstance(meta, dict):
        return meta.get('team_key', '')
    return ''


def _extract_team_name(meta):
    if isinstance(meta, list):
        for item in meta:
            if isinstance(item, dict) and 'name' in item:
                return item['name']
    if isinstance(meta, dict):
        return meta.get('name', '')
    return ''


def _extract_stats(team_data, stat_map):
    """Return {display_name: value} using the stat_id -> name map."""
    stats = {}
    if not isinstance(team_data, list) or len(team_data) < 2:
        return stats
    stat_block = team_data[1]
    if not isinstance(stat_block, dict):
        return stats
    raw_stats = stat_block.get('team_stats', {}).get('stats', [])
    for entry in raw_stats:
        if isinstance(entry, dict):
            stat = entry.get('stat', {})
            sid  = str(stat.get('stat_id', ''))
            name = stat_map.get(sid)
            if name:
                stats[name] = stat.get('value')
    return stats


# ---------------------------------------------------------------------------
# Section: news (transactions + schedule density)
# ---------------------------------------------------------------------------

def _news(roster, progress=None):
    """
    Returns:
      transactions      - recent notable MLB moves (last 3 days)
      schedule_density  - {team: game_count} for next 7 days, sorted descending
      roster_schedule   - [{name, team, games}] for your active players
    """
    def _p(msg):
        if progress: progress(msg)

    _p('Fetching MLB transactions...')
    transactions = get_transactions(days=3)

    _p('Fetching schedule density...')
    density = get_schedule_density(days=7)
    sorted_density = dict(sorted(density.items(), key=lambda x: x[1], reverse=True))

    active_names = [p['name'] for p in roster
                    if p['selected_position'] not in ('BN', 'IL')]
    _p(f'Looking up teams for {len(active_names)} active players...')
    player_teams = get_player_teams(active_names)

    roster_schedule = sorted([
        {
            'name':  name,
            'team':  team,
            'games': density.get(team, 0),
        }
        for name, team in player_teams.items()
    ], key=lambda x: x['games'], reverse=True)

    return {
        'transactions':     transactions,
        'schedule_density': sorted_density,
        'roster_schedule':  roster_schedule,
    }
