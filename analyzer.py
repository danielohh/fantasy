import datetime

from mlb_stats import (
    get_probable_starters,
    get_projected_starters,
    get_pitcher_stats,
    get_recent_pitcher_stats,
    get_batter_stats,
    get_recent_batter_stats,
    get_transactions,
    get_schedule_density,
    get_player_teams,
    get_team_batting_stats,
)

INJURY_STATUSES = {'DTD', 'IL10', 'IL15', 'IL60', 'NA', 'SUSP', 'IL'}
HOT_BATTER_OPS_DELTA  = +0.100
COLD_BATTER_OPS_DELTA = -0.100
HOT_PITCHER_ERA_DELTA  = -1.00
COLD_PITCHER_ERA_DELTA = +1.00
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
      injuries, streaming, waivers, waiver_pitchers, categories, news,
      recent_form, two_start_pitchers, category_targets, trade_candidates

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

    _prog("Fetching waiver wire...")
    waiver_players = lg.waivers()

    runners = {
        'injuries':           lambda: _injuries(roster),
        'streaming':          lambda: _streaming(lg, roster, days, waiver_players, _prog),
        'waivers':            lambda: _waivers(lg, roster, waiver_players, _prog),
        'waiver_pitchers':    lambda: _waiver_pitchers(lg, waiver_players, _prog),
        'categories':         lambda: _categories(lg),
        'news':               lambda: _news(roster, _prog),
        'recent_form':        lambda: _recent_form(roster, _prog),
        'two_start_pitchers': lambda: _two_start_pitchers(roster, lg, waiver_players, _prog),
        'standings':          lambda: _standings(lg),
    }

    # Derived sections depend on other sections' results
    ALL_SECTIONS = list(runners) + ['category_targets', 'trade_candidates']

    if section:
        if section not in ALL_SECTIONS:
            raise ValueError(f"Unknown section '{section}'. Choose from: {ALL_SECTIONS}")
        _prog(f"Running {section} analysis...")
        if section == 'category_targets':
            return {'category_targets': _category_targets(
                runners['categories'](), runners['waivers'](), runners['waiver_pitchers'](),
            )}
        if section == 'trade_candidates':
            return {'trade_candidates': _trade_candidates(
                runners['recent_form'](), runners['waivers'](), runners['categories'](),
            )}
        return {section: runners[section]()}

    results = {}
    for key, fn in runners.items():
        _prog(f"Running {key} analysis...")
        results[key] = fn()

    _prog("Running category_targets analysis...")
    results['category_targets'] = _category_targets(
        results['categories'], results['waivers'], results['waiver_pitchers'],
    )
    _prog("Running trade_candidates analysis...")
    results['trade_candidates'] = _trade_candidates(
        results['recent_form'], results['waivers'], results['categories'],
    )
    return results


# ---------------------------------------------------------------------------
# Section: injuries
# ---------------------------------------------------------------------------

def _injuries(roster):
    """
    Returns list of alert dicts. Each has:
      type, player_name, player_id, status, current_slot, action
    """
    il_used     = sum(1 for p in roster if p['selected_position'] in IL_SLOTS)
    il_capacity = sum(1 for p in roster if 'IL' in p.get('eligible_positions', []))
    il_available = max(0, il_capacity - il_used)

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
                if il_available > 0:
                    action = 'Move to IL slot to free up a BN spot'
                else:
                    action = 'IL full — drop a player or activate a healthy IL occupant first'
                alerts.append({
                    'type': 'il_eligible_on_bench',
                    'player_name': name,
                    'player_id': pid,
                    'status': status,
                    'current_slot': slot,
                    'action': action,
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

def _streaming(lg, roster, days, waiver_players, progress=None):
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
    for p in waiver_players:
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
        if p['selected_position'] in ('SP', 'P')
        and p['name'] not in probable_names
        and p.get('status', '') not in INJURY_STATUSES
    ]

    return {'targets': targets, 'drop_candidates': drop_candidates}


# ---------------------------------------------------------------------------
# Section: waivers
# ---------------------------------------------------------------------------

def _waivers(lg, roster, waiver_players, progress=None):
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

    # Pre-index waiver players by position to avoid re-iterating per position
    waivers_by_pos = {}
    for p in waiver_players:
        for pos in p.get('eligible_positions', []):
            waivers_by_pos.setdefault(pos, []).append(p)

    results = {}

    for pos in BATTER_POSITIONS:
        _p(f"Checking {pos} free agents...")
        candidates = list(lg.free_agents(pos))
        candidates.extend(waivers_by_pos.get(pos, []))

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


def _waiver_pitchers(lg, waiver_players, progress=None):
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
    for p in waiver_players:
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
        if not cs:
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
_stat_id_map_cache = {}


def _build_stat_id_map(lg):
    """Return {stat_id_str: display_name} from league settings, cached per league."""
    if lg.league_id in _stat_id_map_cache:
        return _stat_id_map_cache[lg.league_id]
    import objectpath
    t = objectpath.Tree(lg.yhandler.get_settings_raw(lg.league_id))
    mapping = {}
    for s in t.execute('$..stat_categories..stat'):
        if 'stat_id' in s and 'display_name' in s:
            mapping[str(s['stat_id'])] = s['display_name']
    _stat_id_map_cache[lg.league_id] = mapping
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
            if mine == theirs:
                winning = False
                tied = True
            elif display_name in LOWER_IS_BETTER:
                winning = mine < theirs
                tied = False
            else:
                winning = mine > theirs
                tied = False
            cats.append({
                'category': display_name,
                'mine':     mine,
                'theirs':   theirs,
                'winning':  winning,
                'tied':     tied,
            })

        return {
            'week':    week,
            'my_team': my_name,
            'opp':     opp_name,
            'cats':    cats,
            'leading': sum(1 for c in cats if c['winning']),
            'tied':    sum(1 for c in cats if c.get('tied')),
        }

    except Exception as e:
        return {'error': str(e)}


def _standings(lg):
    """Return my team's standings: rank, wins, losses, ties, total_teams."""
    try:
        my_key = lg.team_key()
        raw = lg.standings()
        total = len(raw)
        for team in raw:
            if not isinstance(team, dict):
                continue
            if team.get('team_key', '') != my_key:
                continue
            ot = team.get('outcome_totals', {})
            return {
                'rank':        team.get('rank', '?'),
                'wins':        ot.get('wins', '?'),
                'losses':      ot.get('losses', '?'),
                'ties':        ot.get('ties', 0),
                'total_teams': total,
            }
        return {'rank': '?', 'wins': '?', 'losses': '?', 'ties': 0, 'total_teams': total}
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


# ---------------------------------------------------------------------------
# Section: recent_form
# ---------------------------------------------------------------------------

def _recent_form(roster, progress=None):
    """
    Compares last-14-day stats vs season stats for active roster players.
    Returns:
      hot_batters   - [{player_name, player_id, slot, season_ops, recent_ops, ops_delta,
                        recent_avg, recent_hr, recent_sb, recent_pa}]
      cold_batters  - same shape
      hot_pitchers  - [{player_name, player_id, slot, season_era, recent_era, era_delta,
                        recent_whip, recent_k9, recent_ip}]
      cold_pitchers - same shape
    """
    def _p(msg):
        if progress: progress(msg)

    active_batters = [
        p for p in roster
        if p['selected_position'] in ACTIVE_BATTER_SLOTS
        and p.get('status', '') not in INJURY_STATUSES
    ]
    active_pitchers = [
        p for p in roster
        if p['selected_position'] in ACTIVE_PITCHER_SLOTS
        and p.get('status', '') not in INJURY_STATUSES
    ]

    batter_names  = [p['name'] for p in active_batters]
    pitcher_names = [p['name'] for p in active_pitchers]

    _p(f"Fetching season batter stats for {len(batter_names)} active batters...")
    season_bat = get_batter_stats(batter_names)
    _p("Fetching last-14-day batter stats...")
    recent_bat = get_recent_batter_stats(batter_names)

    _p(f"Fetching season pitcher stats for {len(pitcher_names)} active pitchers...")
    season_pit = get_pitcher_stats(pitcher_names)
    _p("Fetching last-14-day pitcher stats...")
    recent_pit = get_recent_pitcher_stats(pitcher_names)

    hot_batters, cold_batters = [], []
    batter_slot = {p['name']: p['selected_position'] for p in active_batters}
    for p in active_batters:
        name = p['name']
        ss = season_bat.get(name, {})
        rs = recent_bat.get(name, {})
        if not ss or not rs or rs.get('pa', 0) < 5:
            continue
        season_ops = ss.get('obp', 0) + ss.get('slg', 0)
        recent_ops = rs.get('obp', 0) + rs.get('slg', 0)
        delta = recent_ops - season_ops
        entry = {
            'player_name': name,
            'player_id':   p['player_id'],
            'slot':        p['selected_position'],
            'season_ops':  round(season_ops, 3),
            'recent_ops':  round(recent_ops, 3),
            'ops_delta':   round(delta, 3),
            'recent_avg':  rs.get('avg', 0),
            'recent_hr':   rs.get('hr', 0),
            'recent_sb':   rs.get('sb', 0),
            'recent_pa':   rs.get('pa', 0),
        }
        if delta >= HOT_BATTER_OPS_DELTA:
            hot_batters.append(entry)
        elif delta <= COLD_BATTER_OPS_DELTA:
            cold_batters.append(entry)

    hot_batters.sort(key=lambda x: -x['ops_delta'])
    cold_batters.sort(key=lambda x:  x['ops_delta'])

    hot_pitchers, cold_pitchers = [], []
    for p in active_pitchers:
        name = p['name']
        ss = season_pit.get(name, {})
        rs = recent_pit.get(name, {})
        if not ss or not rs or rs.get('ip', 0) < 3 or ss.get('era', 0) == 0:
            continue
        season_era = ss.get('era', 0)
        recent_era = rs.get('era', 0)
        delta = recent_era - season_era
        entry = {
            'player_name': name,
            'player_id':   p['player_id'],
            'slot':        p['selected_position'],
            'season_era':  season_era,
            'recent_era':  recent_era,
            'era_delta':   round(delta, 2),
            'recent_whip': rs.get('whip', 0),
            'recent_k9':   rs.get('k9', 0),
            'recent_ip':   rs.get('ip', 0),
        }
        if delta <= HOT_PITCHER_ERA_DELTA:
            hot_pitchers.append(entry)
        elif delta >= COLD_PITCHER_ERA_DELTA:
            cold_pitchers.append(entry)

    hot_pitchers.sort(key=lambda x:  x['era_delta'])
    cold_pitchers.sort(key=lambda x: -x['era_delta'])

    return {
        'hot_batters':   hot_batters,
        'cold_batters':  cold_batters,
        'hot_pitchers':  hot_pitchers,
        'cold_pitchers': cold_pitchers,
    }


# ---------------------------------------------------------------------------
# Section: two_start_pitchers
# ---------------------------------------------------------------------------

def _two_start_pitchers(roster, lg, waiver_players, progress=None):
    """
    Returns list of pitchers (on roster or available as FA/waiver) with 2+ starts in the
    current fantasy week (Mon-Sun). Only runs on Monday/Tuesday; returns [] otherwise.
    Each dict: {player_name, player_id, slot, starts: [{date, opponent, home, opp_ops}]}
    Sorted by number of starts descending, then player name.
    """
    def _p(msg):
        if progress: progress(msg)

    today = datetime.date.today()
    if today.weekday() > 1:  # 0=Mon, 1=Tue
        return []

    # Days remaining in the week including today (Mon=7, Tue=6)
    days_left = 7 - today.weekday()
    week_end = today + datetime.timedelta(days=days_left - 1)

    active_pitchers = {
        p['name']: {'player_id': p['player_id'], 'slot': p['selected_position']}
        for p in roster
        if p['selected_position'] in ACTIVE_PITCHER_SLOTS
        and p.get('status', '') not in INJURY_STATUSES
    }

    _p("Fetching available pitchers for two-start check...")
    available_pitchers = {}
    for p in lg.free_agents('P'):
        available_pitchers[p['name']] = {'player_id': p['player_id'], 'slot': 'FA'}
    for p in waiver_players:
        pos = p.get('eligible_positions', [])
        if any(x in pos for x in ('SP', 'RP', 'P')):
            available_pitchers[p['name']] = {'player_id': p['player_id'], 'slot': 'W'}

    all_pitchers = {**available_pitchers, **active_pitchers}  # roster takes precedence

    if not all_pitchers:
        return []

    _p("Fetching probable starters for two-start check...")
    probable = get_probable_starters(days=days_left)

    _p("Projecting unannounced starts via rotation depth charts...")
    projected = get_projected_starters(days=days_left)

    _p("Fetching team batting stats for opponent quality...")
    team_batting = get_team_batting_stats()

    # Merge confirmed + projected; confirmed takes priority for same pitcher+date
    seen_keys = set()
    all_starts = []
    for s in probable:
        key = (s['name'], s['date'])
        seen_keys.add(key)
        all_starts.append({**s, 'projected': False})
    for s in projected:
        key = (s['name'], s['date'])
        if key not in seen_keys:
            seen_keys.add(key)
            all_starts.append(s)

    starts_by_name = {}
    for s in all_starts:
        name = s['name']
        if name not in all_pitchers:
            continue
        if datetime.date.fromisoformat(s['date']) > week_end:
            continue
        opp_ops = team_batting.get(s['opponent'], {}).get('ops')
        starts_by_name.setdefault(name, []).append({
            'date':      s['date'],
            'opponent':  s['opponent'],
            'home':      s['home'],
            'opp_ops':   opp_ops,
            'projected': s.get('projected', False),
        })

    # Remove projected starts that are too close (<4 days) to any confirmed start.
    # Confirmed starts always win; projections are only valid if far enough away.
    for name in list(starts_by_name.keys()):
        start_list = sorted(starts_by_name[name], key=lambda s: s['date'])
        confirmed = [s for s in start_list if not s['projected']]
        valid = list(confirmed)
        for s in start_list:
            if not s['projected']:
                continue
            s_date = datetime.date.fromisoformat(s['date'])
            too_close = any(
                abs((s_date - datetime.date.fromisoformat(o['date'])).days) < 4
                for o in valid
            )
            if not too_close:
                valid.append(s)
        starts_by_name[name] = sorted(valid, key=lambda s: s['date'])

    results = []
    for name, start_list in starts_by_name.items():
        if len(start_list) >= 2:
            p = all_pitchers[name]
            results.append({
                'player_name': name,
                'player_id':   p['player_id'],
                'slot':        p['slot'],
                'starts':      start_list,
            })

    results.sort(key=lambda x: (-len(x['starts']), x['player_name']))
    return results


# ---------------------------------------------------------------------------
# Section: category_targets (derived — no extra API calls)
# ---------------------------------------------------------------------------

# Map Yahoo H2H category display names to batter/pitcher stat keys
# Minimum absolute gap required before a category can be conceded.
# Prevents "0 vs 1 = 100% behind" from triggering concede early in the week.
_MIN_CONCEDE_GAP = {
    'R': 4, 'HR': 3, 'RBI': 4, 'SB': 3, 'BB': 4,
    'K': 10, 'W': 2, 'SV': 3, 'HLD': 3, 'QS': 2, 'IP': 8,
    'AVG': 0.040, 'OBP': 0.060, 'SLG': 0.060,
}

_CAT_BATTER = {'R': 'r', 'HR': 'hr', 'RBI': 'rbi', 'SB': 'sb',
                'AVG': 'avg', 'OBP': 'obp', 'SLG': 'slg', 'BB': 'bb'}
_CAT_PITCHER = {'W': 'wins', 'SV': 'saves', 'K': 'k9', 'QS': 'qs',
                'ERA': 'era', 'WHIP': 'whip', 'HLD': 'holds'}


def _concede_threshold():
    """
    Tighten the concede threshold as the week progresses.
    Mon=0 ... Sun=6; Yahoo weeks typically run Mon-Sun.
    Early week: more can be chased. Late week: less time to close gaps.
    """
    day = datetime.date.today().weekday()  # Mon=0, Sun=6
    if day <= 1:   # Mon-Tue: 6-7 days left
        return 0.25
    elif day <= 3: # Wed-Thu: 3-5 days left
        return 0.20
    else:          # Fri-Sun: 1-2 days left
        return 0.10


def _category_targets(categories_result, waivers_result, waiver_pitchers_result):
    """
    Classify each H2H category as chase, protect, or concede, and surface the
    best available waiver player for each "chase" category.

    Returns:
      chase   - [{'category', 'mine', 'theirs', 'gap_pct', 'suggestion', 'player'}]
      protect - [{'category', 'mine', 'theirs'}]
      concede - [{'category', 'mine', 'theirs', 'gap_pct'}]
    """
    if not categories_result or 'error' in categories_result:
        return {'chase': [], 'protect': [], 'concede': []}

    cats = categories_result.get('cats', [])
    chase, protect, concede = [], [], []
    concede_pct = _concede_threshold()

    # Pre-index top waiver batter by each stat for quick lookup
    best_waiver_batter = {}  # stat_key -> (name, value)
    for pos_upgrades in waivers_result.values() if isinstance(waivers_result, dict) else []:
        for u in pos_upgrades:
            cs = u['candidate'].get('stats', {})
            for stat_key in _CAT_BATTER.values():
                val = cs.get(stat_key, 0) or 0
                if val > best_waiver_batter.get(stat_key, (None, -1))[1]:
                    best_waiver_batter[stat_key] = (u['candidate']['name'], val)

    # Best waiver RP by stat
    _lower_stat_keys = {_CAT_PITCHER[c] for c in LOWER_IS_BETTER if c in _CAT_PITCHER}
    best_waiver_pitcher = {}  # stat_key -> (name, value)
    for p in waiver_pitchers_result if isinstance(waiver_pitchers_result, list) else []:
        cs = p.get('stats', {})
        for stat_key in _CAT_PITCHER.values():
            val = cs.get(stat_key, 0) or 0
            if stat_key in _lower_stat_keys:
                if val > 0 and val < best_waiver_pitcher.get(stat_key, (None, float('inf')))[1]:
                    best_waiver_pitcher[stat_key] = (p['name'], val)
            else:
                if val > best_waiver_pitcher.get(stat_key, (None, -1))[1]:
                    best_waiver_pitcher[stat_key] = (p['name'], val)

    for c in cats:
        cat   = c['category']
        mine  = float(c['mine']   or 0)
        theirs = float(c['theirs'] or 0)

        if c['winning'] or c.get('tied'):
            protect.append({'category': cat, 'mine': mine, 'theirs': theirs})
            continue

        # Compute gap percentage relative to opponent's value (skip if both zero)
        if theirs == 0:
            gap_pct = 0.0
        else:
            # For lower-is-better cats, gap is how much worse you are proportionally
            if cat in LOWER_IS_BETTER:
                gap_pct = (mine - theirs) / theirs if theirs else 0.0
            else:
                gap_pct = (theirs - mine) / theirs

        entry_base = {
            'category': cat,
            'mine':     mine,
            'theirs':   theirs,
            'gap_pct':  round(gap_pct, 3),
        }

        abs_gap = abs(theirs - mine)
        min_gap = _MIN_CONCEDE_GAP.get(cat, 0)
        if gap_pct > concede_pct and abs_gap >= min_gap:
            concede.append(entry_base)
        else:
            # Chase — find best matching waiver target
            stat_key   = _CAT_BATTER.get(cat) or _CAT_PITCHER.get(cat)
            source     = best_waiver_batter if cat in _CAT_BATTER else best_waiver_pitcher
            suggestion = None
            player     = None
            if stat_key and stat_key in source:
                player_name, val = source[stat_key]
                is_lower_better = stat_key in _lower_stat_keys
                useful = (is_lower_better and val < mine) or (not is_lower_better and val != 0)
                if useful:
                    suggestion = f"Add {player_name} (leads available in {stat_key}: {val})"
                    player     = player_name
            chase.append({**entry_base, 'suggestion': suggestion, 'player': player})

    return {'chase': chase, 'protect': protect, 'concede': concede}


# ---------------------------------------------------------------------------
# Section: trade_candidates (derived — no extra API calls)
# ---------------------------------------------------------------------------

_SELL_HIGH_OPS   = +0.150   # recent OPS delta above this → sell-high candidate
_SELL_HIGH_ERA   = -1.50    # recent ERA delta below this → sell-high candidate
_BUY_LOW_OPS_SEASON = 0.750  # season OPS must be at least this to be a true buy-low


def _trade_candidates(recent_form_result, waivers_result, categories_result=None):
    """
    Derives sell-high and buy-low trade suggestions from recent_form and waivers.

    sell_high: your roster players with extreme positive recent form (likely to regress),
               skipping anyone whose hot stat contributes to a category you're losing.
    buy_low:   waiver targets with strong season OPS (>= threshold) and some ownership,
               indicating market undervaluation rather than permanent decline.

    Returns:
      sell_high - [{'player_name', 'player_id', 'reason', 'ops_delta' or 'era_delta'}]
      buy_low   - [{'player_name', 'player_id', 'reason', 'season_ops', 'recent_ops'}]
    """
    # Categories you're currently losing — don't sell players contributing to these
    losing_cats = set()
    if categories_result and 'cats' in categories_result:
        losing_cats = {c['category'] for c in categories_result['cats'] if not c['winning']}

    # Reverse maps: stat_key -> category name
    _STAT_TO_CAT_BATTER  = {v: k for k, v in _CAT_BATTER.items()}
    _STAT_TO_CAT_PITCHER = {v: k for k, v in _CAT_PITCHER.items()}

    sell_high = []
    if isinstance(recent_form_result, dict):
        for p in recent_form_result.get('hot_batters', []):
            if p['ops_delta'] < _SELL_HIGH_OPS:
                continue
            # Skip if their core stats help categories you're losing
            batter_cats = {'AVG', 'OBP', 'R', 'HR', 'RBI', 'SB', 'BB'}
            if batter_cats & losing_cats:
                continue
            sell_high.append({
                'player_name': p['player_name'],
                'player_id':   p['player_id'],
                'reason':      f"OPS {p['recent_ops']:.3f} last 14d vs {p['season_ops']:.3f} season (+{p['ops_delta']:.3f}) — likely to regress",
                'ops_delta':   p['ops_delta'],
            })
        for p in recent_form_result.get('hot_pitchers', []):
            if p['era_delta'] > _SELL_HIGH_ERA:
                continue
            pitcher_cats = {'ERA', 'WHIP', 'W', 'K', 'QS', 'SV', 'HLD'}
            if pitcher_cats & losing_cats:
                continue
            sell_high.append({
                'player_name': p['player_name'],
                'player_id':   p['player_id'],
                'reason':      f"ERA {p['recent_era']:.2f} last 14d vs {p['season_era']:.2f} season ({p['era_delta']:.2f}) — may not sustain",
                'era_delta':   p['era_delta'],
            })

    sell_high.sort(key=lambda x: -x.get('ops_delta', -x.get('era_delta', 0)))

    # Buy-low: waiver candidates with strong season OPS and meaningful ownership
    buy_low = []
    if isinstance(waivers_result, dict):
        seen = set()
        for pos_upgrades in waivers_result.values():
            for u in pos_upgrades:
                c  = u['candidate']
                cs = c.get('stats', {})
                name = c['name']
                if name in seen:
                    continue
                season_ops = (cs.get('obp', 0) or 0) + (cs.get('slg', 0) or 0)
                if season_ops < _BUY_LOW_OPS_SEASON:
                    continue
                if c.get('percent_owned', 0) < 5:
                    continue  # skip complete unknowns
                seen.add(name)
                buy_low.append({
                    'player_name':   name,
                    'player_id':     c['player_id'],
                    'reason':        f"Season OPS {season_ops:.3f} but available on waivers — undervalued",
                    'season_ops':    round(season_ops, 3),
                    'percent_owned': c.get('percent_owned', 0),
                })

    buy_low.sort(key=lambda x: -x['season_ops'])
    return {'sell_high': sell_high, 'buy_low': buy_low}
