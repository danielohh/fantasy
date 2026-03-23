import datetime
import json
import os
import pickle
import unicodedata

import requests

MLB_CACHE_FILE = '.mlb_cache.pkl'
_cache = None
_base_url = 'https://statsapi.mlb.com/api/v1'


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


def _lookup_player_id(name):
    """Look up player ID by name."""
    for query in [name, _normalize(name)]:
        try:
            results = _api_get('lookup/players', {'lookupNames': query})
            if results and len(results) > 0:
                return results[0]['id']
        except Exception:
            pass
    return None


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
    Used to assess matchup difficulty for streaming pitchers.
    """
    data = _api_get('teams', {'sportId': 1})
    result = {}
    for team in data.get('teams', []):
        tid = team['id']
        name = team['name']
        try:
            stats = _api_get('teams/' + str(tid), {
                'hydrate': 'stats(group=hitting,type=season)',
            })
            splits = (stats.get('teams', [{}])[0]
                      .get('stats', [{}])[0]
                      .get('splits', []))
            if splits:
                s = splits[0]['stat']
                obp = float(s.get('obp', 0) or 0)
                slg = float(s.get('slg', 0) or 0)
                result[name] = {
                    'avg': float(s.get('avg', 0) or 0),
                    'ops': obp + slg,
                }
        except Exception:
            pass
    return result


def get_schedule_density(days=7):
    """
    Return dict of {team_name: game_count} for the next N days.
    """
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days - 1)
    data = _api_get('schedule', {
        'startDate': today.strftime('%Y-%m-%d'),
        'endDate': end.strftime('%Y-%m-%d'),
        'sportId': 1,
    })
    counts = {}
    for game in data.get('games', []):
        for side in ('home', 'away'):
            team = game.get(f'{side}Team', {}).get('name', '')
            if team:
                counts[team] = counts.get(team, 0) + 1
    return counts


def get_player_teams(names):
    """
    Return {name: team_name} for each player in names.
    Uses currentTeam from the MLB person endpoint.
    """
    result = {}
    for name in names:
        pid = _lookup_player_id(name)
        if pid is None:
            continue
        try:
            data = _api_get('people/' + str(pid), {'hydrate': 'currentTeam'})
            team = (data.get('people', [{}])[0]
                    .get('currentTeam', {})
                    .get('name', ''))
            if team:
                result[name] = team
        except Exception:
            pass
    return result


def get_probable_starters(days=3):
    """
    Return list of dicts for all probable starters over the next N days.
    Each dict: {name, team, opponent, date, home}
    """
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days - 1)
    data = _api_get('schedule', {
        'startDate': today.strftime('%Y-%m-%d'),
        'endDate': end.strftime('%Y-%m-%d'),
        'sportId': 1,
    })
    starters = []
    for game in data.get('games', []):
        for side, opp in [('home', 'away'), ('away', 'home')]:
            pitcher = game.get(f'{side}ProbablePitcher', '')
            if pitcher:
                starters.append({
                    'name': pitcher,
                    'team': game.get(f'{side}Team', {}).get('name', ''),
                    'opponent': game.get(f'{opp}Team', {}).get('name', ''),
                    'date': game.get('gameDateTime', '')[:10],
                    'home': side == 'home',
                })
    return starters


def get_pitcher_stats(names):
    """
    Return dict of name -> {era, whip, k9, ip, wins, saves, holds, qs} for season.
    Only fetches for names provided; skips any that can't be found.
    """
    result = {}
    for name in names:
        pid = _lookup_player_id(name)
        if pid is None:
            continue
        try:
            data = _api_get('people/' + str(pid), {
                'hydrate': 'stats(group=pitching,type=season)',
            })
            splits = (data.get('people', [{}])[0]
                      .get('stats', [{}])[0]
                      .get('splits', []))
            if not splits:
                continue
            s = splits[0]['stat']
            result[name] = {
                'era': float(s.get('era', 0) or 0),
                'whip': float(s.get('whip', 0) or 0),
                'k9': float(s.get('strikeoutsPer9Inn', 0) or 0),
                'ip': float(s.get('inningsPitched', 0) or 0),
                'wins': int(s.get('wins', 0) or 0),
                'saves': int(s.get('saves', 0) or 0),
                'holds': int(s.get('holds', 0) or 0),
                'qs': int(s.get('qualityStarts', 0) or 0),
            }
        except Exception:
            pass
    return result


def get_batter_stats(names):
    """
    Return dict of name -> {avg, obp, slg, hr, rbi, r, sb, bb, pa} for season.
    """
    result = {}
    for name in names:
        pid = _lookup_player_id(name)
        if pid is None:
            continue
        try:
            data = _api_get('people/' + str(pid), {
                'hydrate': 'stats(group=hitting,type=season)',
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
                'hr': int(s.get('homeRuns', 0) or 0),
                'rbi': int(s.get('rbi', 0) or 0),
                'r': int(s.get('runs', 0) or 0),
                'sb': int(s.get('stolenBases', 0) or 0),
                'bb': int(s.get('baseOnBalls', 0) or 0),
                'pa': int(s.get('plateAppearances', 0) or 0),
            }
        except Exception:
            pass
    return result
