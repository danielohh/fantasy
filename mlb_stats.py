import datetime
import json
import os
import pickle
import unicodedata

import requests

MLB_CACHE_FILE = '.mlb_cache.pkl'
_cache = None
_base_url = 'https://statsapi.mlb.com/api/v1'
_player_cache = {}   # name -> {'id': int|None, 'team': str|None}
_schedule_cache = {} # days -> raw schedule JSON


def enable_cache():
    """Load cache from MLB_CACHE_FILE and patch _api_get to cache responses."""
    global _cache
    if os.path.exists(MLB_CACHE_FILE):
        with open(MLB_CACHE_FILE, 'rb') as f:
            _cache = pickle.load(f)
    else:
        _cache = {}


def _save_cache():
    with open(MLB_CACHE_FILE, 'wb') as f:
        pickle.dump(_cache, f)


def _api_get(endpoint, params=None):
    """GET from MLB Stats API with optional caching."""
    if _cache is None:
        # Caching disabled
        resp = requests.get(f'{_base_url}/{endpoint}', params=params, timeout=10)
        return resp.json()

    # Caching enabled
    key = ('get', endpoint, json.dumps(params or {}, sort_keys=True))
    if key in _cache:
        return _cache[key]

    resp = requests.get(f'{_base_url}/{endpoint}', params=params, timeout=10)
    data = resp.json()
    _cache[key] = data
    _save_cache()
    return data


def _normalize(name):
    nfkd = unicodedata.normalize('NFKD', name)
    s = ''.join(c for c in nfkd if not unicodedata.combining(c))
    for suffix in (' Jr.', ' Sr.', ' III', ' II', ' IV'):
        s = s.replace(suffix, '')
    return s.strip().lower()


def _lookup_player(name):
    """Return {'id', 'team'} for a player, cached to avoid repeat lookups."""
    if name in _player_cache:
        return _player_cache[name]
    result = {'id': None, 'team': None}
    for query in [name, _normalize(name)]:
        try:
            data = _api_get('people/search', {'names': query, 'hydrate': 'currentTeam'})
            people = data.get('people', []) if isinstance(data, dict) else []
            if people:
                p = people[0]
                result['id'] = p.get('id')
                result['team'] = p.get('currentTeam', {}).get('name', '')
                break
        except Exception:
            pass
    _player_cache[name] = result
    return result


def _lookup_player_id(name):
    return _lookup_player(name)['id']


def get_transactions(days=3):
    """
    Return recent notable transactions: call-ups, IL moves, activations, DFA.
    Each dict: {date, type, player, team}
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)
    data = _api_get('transactions', {
        'startDate': start.strftime('%m/%d/%Y'),
        'endDate': today.strftime('%m/%d/%Y'),
        'sportId': 1,
    })
    keep = ('Recalled', 'Selected', 'Injured List', 'Activated', 'Designated')
    results = []
    for t in data.get('transactions', []):
        type_desc = t.get('typeDesc', '')
        if any(k in type_desc for k in keep):
            # Use toTeam if available, otherwise fromTeam
            team = t.get('toTeam', {}).get('name', '') or t.get('fromTeam', {}).get('name', '')
            results.append({
                'date': t.get('date', ''),
                'type': type_desc,
                'player': t.get('person', {}).get('fullName', ''),
                'team': team,
            })
    return results


def get_team_batting_stats():
    """
    Return {team_name: {avg, ops}} for all MLB teams this season.
    Uses a single bulk /teams/stats call. Falls back to spring training
    if regular season data isn't available yet (early season).
    """
    today = datetime.date.today()
    for game_type in ('R', 'S'):  # regular season, then spring training
        data = _api_get('teams/stats', {
            'sportId': 1,
            'group': 'hitting',
            'gameType': game_type,
            'season': today.year,
            'stats': 'season',
        })
        splits = data.get('stats', [{}])[0].get('splits', []) if data.get('stats') else []
        if not splits:
            continue
        result = {}
        for split in splits:
            name = split.get('team', {}).get('name', '')
            s = split.get('stat', {})
            if name:
                obp = float(s.get('obp', 0) or 0)
                slg = float(s.get('slg', 0) or 0)
                result[name] = {
                    'avg': float(s.get('avg', 0) or 0),
                    'ops': obp + slg,
                }
        return result
    return {}


def _get_schedule_raw(days=7):
    """Fetch schedule for next N days, cached in-memory for the process lifetime."""
    if days in _schedule_cache:
        return _schedule_cache[days]
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days - 1)
    raw = _api_get('schedule', {
        'startDate': today.strftime('%Y-%m-%d'),
        'endDate': end.strftime('%Y-%m-%d'),
        'sportId': 1,
        'hydrate': 'probablePitcher',
    })
    games = [g for d in raw.get('dates', []) for g in d.get('games', [])]
    data = {'games': games}
    _schedule_cache[days] = data
    return data


def get_schedule_density(days=7):
    """Return dict of {team_name: game_count} for the next N days."""
    data = _get_schedule_raw(days)
    counts = {}
    for game in data.get('games', []):
        teams = game.get('teams', {})
        for side in ('home', 'away'):
            team = teams.get(side, {}).get('team', {}).get('name', '')
            if team:
                counts[team] = counts.get(team, 0) + 1
    return counts


def get_player_teams(names):
    """Return {name: team_name} for each player using cached lookup data."""
    result = {}
    for name in names:
        team = _lookup_player(name)['team']
        if team:
            result[name] = team
    return result


def get_probable_starters(days=3):
    """
    Return list of dicts for all probable starters over the next N days.
    Each dict: {name, team, opponent, date, home}
    Fetches 7-day schedule (shared with get_schedule_density) and filters by window.
    """
    today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=days - 1)
    data = _get_schedule_raw(7)
    starters = []
    for game in data.get('games', []):
        date_str = game.get('gameDate', game.get('gameDateTime', ''))[:10]
        if date_str > cutoff.strftime('%Y-%m-%d'):
            continue
        teams = game.get('teams', {})
        for side, opp in [('home', 'away'), ('away', 'home')]:
            probable = teams.get(side, {}).get('probablePitcher', {})
            pitcher = probable.get('fullName', '') if isinstance(probable, dict) else ''
            if pitcher:
                starters.append({
                    'name': pitcher,
                    'team': teams.get(side, {}).get('team', {}).get('name', ''),
                    'opponent': teams.get(opp, {}).get('team', {}).get('name', ''),
                    'date': date_str,
                    'home': side == 'home',
                })
    return starters


def _fetch_pitcher_stats(names, stat_type):
    year = datetime.date.today().year
    # season param must be inside the hydrate string for pitching stats
    if stat_type == 'season':
        hydrate_type = 'statsSingleSeason'
    else:
        hydrate_type = 'lastXGames'
    result = {}
    for name in names:
        pid = _lookup_player_id(name)
        if pid is None:
            continue
        try:
            data = _api_get('people/' + str(pid), {
                'hydrate': f'stats(group=pitching,type={hydrate_type},season={year})',
            })
            splits = (data.get('people', [{}])[0]
                      .get('stats', [{}])[0]
                      .get('splits', []))
            if not splits:
                continue
            s = splits[0]['stat']
            result[name] = {
                'era':   float(s.get('era', 0) or 0),
                'whip':  float(s.get('whip', 0) or 0),
                'k9':    float(s.get('strikeoutsPer9Inn', 0) or 0),
                'ip':    float(s.get('inningsPitched', 0) or 0),
                'wins':  int(s.get('wins', 0) or 0),
                'saves': int(s.get('saves', 0) or 0),
                'holds': int(s.get('holds', 0) or 0),
                'qs':    int(s.get('qualityStarts', 0) or 0),
            }
        except Exception:
            pass
    return result


def _fetch_batter_stats(names, stat_type):
    year = datetime.date.today().year
    if stat_type == 'season':
        hydrate_type = 'statsSingleSeason'
    else:
        hydrate_type = 'lastXGames'
    result = {}
    for name in names:
        pid = _lookup_player_id(name)
        if pid is None:
            continue
        try:
            data = _api_get('people/' + str(pid), {
                'hydrate': f'stats(group=hitting,type={hydrate_type})',
                'season':  year,
            })
            splits = (data.get('people', [{}])[0]
                      .get('stats', [{}])[0]
                      .get('splits', []))
            if not splits:
                continue
            s = splits[0]['stat']
            result[name] = {
                'avg': float(s.get('avg', 0) or 0),
                'obp': float(s.get('obp', 0) or 0),
                'slg': float(s.get('slg', 0) or 0),
                'hr':  int(s.get('homeRuns', 0) or 0),
                'rbi': int(s.get('rbi', 0) or 0),
                'r':   int(s.get('runs', 0) or 0),
                'sb':  int(s.get('stolenBases', 0) or 0),
                'bb':  int(s.get('baseOnBalls', 0) or 0),
                'pa':  int(s.get('plateAppearances', 0) or 0),
            }
        except Exception:
            pass
    return result


def get_pitcher_stats(names):
    return _fetch_pitcher_stats(names, 'season')


def get_recent_pitcher_stats(names):
    return _fetch_pitcher_stats(names, 'last14')


def get_batter_stats(names):
    return _fetch_batter_stats(names, 'season')


def get_recent_batter_stats(names):
    return _fetch_batter_stats(names, 'last14')
