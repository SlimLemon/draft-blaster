"""Shared, stdlib-only ESPN transport, configuration, identity and player cache."""
import json
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "espn_config.json")
CONFIG_EXAMPLE = os.path.join(SCRIPT_DIR, "espn_config.example.json")
PLAYER_CACHE_PATH = os.path.join(SCRIPT_DIR, "fixtures", "espn_players_cache.json")
CACHE_MAX_AGE_DAYS = 7
BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
PLAYERS_FILTER = json.dumps({"players": {
    "limit": 20000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}})

class EspnError(RuntimeError):
    """Safe diagnostic containing neither cookie values nor response bodies."""

class ConfigError(Exception):
    pass


def load_config(path=None):
    path = path or CONFIG_PATH
    if not os.path.isfile(path):
        raise ConfigError(
            "Missing %s — copy espn_config.example.json and fill league_id + cookies"
            % path)
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # environment variable overrides for cookies
    env_s2 = os.environ.get("DRAFT_COPILOT_ESPN_S2")
    if env_s2:
        cfg["espn_s2"] = env_s2
    env_swid = os.environ.get("DRAFT_COPILOT_SWID")
    if env_swid:
        cfg["swid"] = env_swid
    required = ("league_id", "season", "espn_s2", "swid")
    missing = [k for k in required if not cfg.get(k)
               or str(cfg.get(k)).startswith("YOUR_")
               or str(cfg.get(k)).startswith("PASTE_")]
    if missing:
        raise ConfigError("espn_config.json incomplete fields: %s" % ", ".join(missing))
    team_map = cfg.get("team_map", {})
    # normalize keys to int — reject non-dict types
    if not isinstance(team_map, dict):
        raise ConfigError("team_map must be a JSON object, got %s" % type(team_map).__name__)
    try:
        cfg["team_map"] = {int(k): str(v).upper() for k, v in team_map.items()}
    except (TypeError, ValueError) as e:
        raise ConfigError("team_map has invalid keys/values: %s" % e)
    # poll_seconds: positive and bounded (0 < x <= 60)
    raw_poll = cfg.get("poll_seconds")
    if raw_poll is None:
        raw_poll = 2
    try:
        poll = float(raw_poll)
    except (TypeError, ValueError):
        raise ConfigError("poll_seconds must be a number, got %r" % raw_poll)
    if not math.isfinite(poll) or poll <= 0 or poll > 60:
        raise ConfigError("poll_seconds must be > 0 and <= 60, got %s" % poll)
    cfg["poll_seconds"] = poll
    # autolog: parse booleans and accepted strings; "false" must not become True
    raw_autolog = cfg.get("autolog")
    if isinstance(raw_autolog, str):
        low = raw_autolog.strip().lower()
        if low in ("true", "1", "yes"):
            cfg["autolog"] = True
        elif low in ("false", "0", "no", ""):
            cfg["autolog"] = False
        else:
            raise ConfigError("autolog must be a boolean or 'true'/'false', got %r"
                              % raw_autolog)
    else:
        cfg["autolog"] = bool(raw_autolog or False)
    try:
        cfg["season"] = int(cfg["season"])
        if cfg["season"] < 2000 or int(cfg["league_id"]) <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        raise ConfigError("season and league_id must be valid positive identifiers")
    cfg["league_id"] = str(cfg["league_id"]).strip()
    return cfg



def integer_id(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

def valid_player_id(value):
    pid = integer_id(value)
    return pid is not None and pid not in (0, -1)

class TeamResolver:
    def __init__(self, team_map=None, teams=(), labels=None):
        self.tokens = {integer_id(k): v for k, v in (team_map or {}).items()}
        self.labels = {}
        for team in teams:
            self.labels[integer_id(team.get("id"))] = (
                team.get("name") or
                " ".join(str(team.get(k) or "") for k in ("location", "nickname")).strip()
                or team.get("abbrev") or "")
        self.labels.update({integer_id(k): v for k, v in (labels or {}).items()})
    def resolve(self, team_id):
        tid = integer_id(team_id)
        return {"team_id": tid, "token": self.tokens.get(tid, ""),
                "label": self.labels.get(tid, "")}

def map_team_token(team_id, team_map):
    return TeamResolver(team_map).resolve(team_id)["token"] or None

def extract_player_records(payload):
    """Extract nested player identities, including roster PPEs and negative D/ST IDs."""
    result = {}
    def walk(value):
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            pid = integer_id(value.get("id"))
            if valid_player_id(pid) and value.get("fullName"):
                result[pid] = {key: value.get(key) for key in
                               ("id", "fullName", "defaultPositionId", "proTeamId")}
                result[pid]["id"] = pid
            for key, child in value.items():
                # Stats/rankings have no identity and can be very large.
                if key not in ("stats", "rankings", "ownership", "ratings"):
                    if isinstance(child, (dict, list)):
                        walk(child)
    walk(payload)
    return result

def extract_player_map(payload):
    return {pid: row["fullName"] for pid, row in extract_player_records(payload).items()}

def league_url(cfg, path="", views=(), params=None, catalog=False):
    base = "%s/%d" % (BASE_URL, int(cfg["season"]))
    if not catalog:
        base += "/segments/0/leagues/" + urllib.parse.quote(str(cfg["league_id"]), safe="")
    if path:
        base += "/" + path.strip("/")
    query = [("view", view) for view in views]
    query.extend((params or {}).items())
    return base + ("?" + urllib.parse.urlencode(query, doseq=True) if query else "")

def draft_detail_url(league_id, season):
    return league_url({"league_id": league_id, "season": season},
                      views=("mDraftDetail", "mRoster"))

def players_url(season):
    return league_url({"season": season}, "players", ("players_wl",),
                      {"scoringPeriodId": 0}, catalog=True)

class EspnClient:
    def __init__(self, cfg, cache_path=None, opener=None):
        self.cfg = cfg
        self.cache_path = cache_path or PLAYER_CACHE_PATH
        self.opener = opener or urllib.request.urlopen
        self.player_records = {}
        self.player_sources = {}
        self.player_refresh_error = None
        self.cache_write_error = None
        self._map_source = None
        self._loaded = False

    def fetch_json(self, url, timeout=15, fantasy_filter=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "lm-api-reads.fantasy.espn.com":
            raise EspnError("Refusing ESPN credentials for an unexpected host")
        headers = {"User-Agent": "DraftCopilot/1.0", "Accept": "application/json",
                   "Cookie": "espn_s2=%s; SWID=%s" % (self.cfg["espn_s2"], self.cfg["swid"])}
        if fantasy_filter:
            headers["X-Fantasy-Filter"] = fantasy_filter
        try:
            with self.opener(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            if raw.lstrip().startswith("<"):
                raise EspnError("ESPN returned HTML; cookies may be expired")
            data = json.loads(raw)
            if not isinstance(data, (dict, list)):
                raise EspnError("ESPN returned an unexpected JSON shape")
            return data
        except EspnError:
            raise
        except Exception as exc:
            # Do not propagate response text, URLs, or user-provided secrets.
            raise EspnError("ESPN request failed (%s)" % type(exc).__name__) from None

    def request(self, path="", views=(), params=None, timeout=15,
                fantasy_filter=None, catalog=False):
        return self.fetch_json(league_url(self.cfg, path, views, params, catalog),
                               timeout, fantasy_filter)

    def league_views(self, views, timeout=15, params=None):
        data = self.request(views=views, params=params, timeout=timeout)
        if not isinstance(data, dict):
            raise EspnError("Expected a league object from ESPN")
        return data

    def draft_detail(self, timeout=10):
        return self.league_views(("mDraftDetail", "mRoster"), timeout=timeout)

    def teams(self):
        return self.request("teams", ("mTeam",))

    def rosters(self, scoring_period=None):
        params = {} if scoring_period is None else {"scoringPeriodId": scoring_period}
        return self.request("teams", ("mRoster",), params)

    def free_agents(self, scoring_period=None, limit=25):
        params = {"sort": "appliedStatTotal:1", "offset": 0, "limit": min(limit * 3, 200)}
        if scoring_period is not None:
            params["scoringPeriodId"] = scoring_period
        return self.request("players", ("players_wl",), params)

    def transactions(self, scoring_period=None):
        params = {} if scoring_period is None else {"scoringPeriodId": scoring_period}
        return self.request("transactions", params=params)

    def scoreboard(self, week=None):
        params = {} if week is None else {"matchupPeriodId": week, "mSPID": week}
        return self.request("scoreboard", params=params)

    def team_resolver(self, teams=()):
        return TeamResolver(self.cfg.get("team_map"), teams, self.cfg.get("team_labels"))

    def _cache_records(self):
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("season") != int(self.cfg["season"]):
                return {}
            stamp = data.get("fetched_at", 0)
            records = data.get("records")
            if records is None:
                records = {key: {"id": integer_id(key), "fullName": name, "fetched_at": stamp}
                           for key, name in data.get("players", {}).items()}
            now = time.time()
            return {int(key): row for key, row in records.items()
                    if valid_player_id(key) and isinstance(row, dict)
                    and isinstance(row.get("fullName"), str) and row["fullName"]
                    and 0 <= now - row.get("fetched_at", 0) <= CACHE_MAX_AGE_DAYS * 86400}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def load_player_cache(self):
        return {pid: row["fullName"] for pid, row in self._cache_records().items()}

    def _save_records(self, records):
        parent = os.path.dirname(os.path.abspath(self.cache_path))
        os.makedirs(parent, exist_ok=True)
        data = {"season": int(self.cfg["season"]), "fetched_at": time.time(),
                "players": {str(pid): row["fullName"] for pid, row in records.items()},
                "records": {str(pid): row for pid, row in records.items()}}
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=parent,
                                             delete=False) as handle:
                tmp = handle.name
                json.dump(data, handle)
            os.replace(tmp, self.cache_path)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    def build_player_map(self, force_refresh=False):
        if self._loaded and not force_refresh:
            return {pid: row["fullName"] for pid, row in self.player_records.items()}, self._map_source
        cached = self._cache_records()
        self.player_records = cached.copy()
        self.player_sources = {pid: "cache_fallback" for pid in cached}
        self.player_refresh_error = None
        self.cache_write_error = None
        source = "cache"
        if force_refresh or len(cached) < 500:
            try:
                raw = self.request("players", ("players_wl",), {"scoringPeriodId": 0},
                                   timeout=60, fantasy_filter=PLAYERS_FILTER, catalog=True)
                fresh = extract_player_records(raw)
                if not fresh:
                    raise EspnError("ESPN player catalog contained no usable identities")
                now = time.time()
                for pid, row in fresh.items():
                    row["fetched_at"] = now
                    self.player_records[pid] = row
                    self.player_sources[pid] = "fresh_catalog"
                source = "espn"
                # Do not promote a thin response into a complete cache.
                if len(fresh) >= 100:
                    try:
                        self._save_records(self.player_records)
                    except OSError:
                        self.cache_write_error = "Player cache could not be saved"
            except EspnError as exc:
                self.player_refresh_error = str(exc)
                source = "cache_fallback" if cached else "unavailable"
        self._map_source = source
        self._loaded = True
        return {pid: row["fullName"] for pid, row in self.player_records.items()}, source


