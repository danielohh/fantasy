import os
import pickle

import yahoo_fantasy_api as yfa
from yahoo_oauth import OAuth2

CACHE_FILE = '.yahoo_cache.pkl'


def get_league(oauth_file=None, cache=False):
    if oauth_file is None:
        oauth_file = os.environ.get('YAHOO_OAUTH_FILE', 'oauth2.json')
    sc = OAuth2(None, None, from_file=oauth_file)
    gm = yfa.Game(sc, 'mlb')
    ids = gm.league_ids(game_codes=['mlb'])
    if not ids:
        raise RuntimeError('No MLB leagues found')
    lg = gm.to_league(ids[0])
    if cache:
        _patch_cache(lg.yhandler)
    return lg


def _patch_cache(yhandler):
    """Wrap yhandler.get() to cache responses keyed by URI."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'rb') as f:
            store = pickle.load(f)
    else:
        store = {}

    original_get = yhandler.get.__func__  # unbound method

    def cached_get(self, uri):
        if uri in store:
            print(f'  [cache] {uri[:80]}', flush=True)
            return store[uri]
        result = original_get(self, uri)
        store[uri] = result
        with open(CACHE_FILE, 'wb') as f:
            pickle.dump(store, f)
        return result

    import types
    yhandler.get = types.MethodType(cached_get, yhandler)
