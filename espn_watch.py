"""ESPN live auction watcher for Draft Co-Pilot.

Polls mDraftDetail for the in-progress nominee and completed sales.
Uses stdlib only (urllib/json/threading) so LibreOffice's Python can import it.
"""
from __future__ import print_function

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "espn_config.json")
CONFIG_EXAMPLE = os.path.join(SCRIPT_DIR, "espn_config.example.json")
FIXTURE_PATH = os.path.join(SCRIPT_DIR, "fixtures", "mDraftDetail_sample.json")

EVENT_NOMINEE = "nominee"
EVENT_BID = "bid"          # same player, bid changed only
EVENT_SOLD = "sold"
EVENT_ERROR = "error"
EVENT_INFO = "info"
EVENT_STALE = "stale"      # polls failing ~15s+

STALE_AFTER_S = 15.0

PLAYER_CACHE_PATH = os.path.join(SCRIPT_DIR, "fixtures", "espn_players_cache.json")
# ESPN returns ~11k with this filter; without it only ~50
PLAYERS_FILTER = json.dumps({
    "players": {
        "limit": 20000,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }
})


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
    required = ("league_id", "season", "espn_s2", "swid")
    missing = [k for k in required if not cfg.get(k)
               or str(cfg.get(k)).startswith("YOUR_")
               or str(cfg.get(k)).startswith("PASTE_")]
    if missing:
        raise ConfigError("espn_config.json incomplete fields: %s" % ", ".join(missing))
    team_map = cfg.get("team_map") or {}
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
    if poll <= 0 or poll > 60:
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
    # environment variable overrides for cookies
    env_s2 = os.environ.get("DRAFT_COPILOT_ESPN_S2")
    if env_s2:
        cfg["espn_s2"] = env_s2
    env_swid = os.environ.get("DRAFT_COPILOT_SWID")
    if env_swid:
        cfg["swid"] = env_swid
    cfg["season"] = int(cfg["season"])
    cfg["league_id"] = str(cfg["league_id"]).strip()
    return cfg


def draft_detail_url(league_id, season):
    return (
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%d"
        "/segments/0/leagues/%s?view=mDraftDetail&view=mRoster"
        % (int(season), league_id)
    )


def players_url(season):
    return (
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%d"
        "/players?scoringPeriodId=0&view=players_wl" % int(season)
    )


def _cookie_header(cfg):
    return "espn_s2=%s; SWID=%s" % (cfg["espn_s2"], cfg["swid"])


def fetch_json(url, cfg, timeout=60, fantasy_filter=None):
    headers = {
        "User-Agent": "DraftCopilot/1.0",
        "Accept": "application/json",
        "Cookie": _cookie_header(cfg),
    }
    if fantasy_filter:
        headers["X-Fantasy-Filter"] = fantasy_filter
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ct = resp.headers.get("content-type", "")
        raw = resp.read().decode("utf-8", errors="replace")
    if "json" not in ct and "javascript" not in ct and not raw.lstrip().startswith("{") and not raw.lstrip().startswith("["):
        # ESPN returned HTML (usually expired cookies / login page)
        raise RuntimeError("ESPN returned HTML (content-type=%s) - cookies may be expired" % ct)
    return json.loads(raw)


def extract_player_map(payload):
    """Build playerId -> fullName from mDraftDetail/players payloads."""
    out = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            pid = item.get("id")
            name = item.get("fullName")
            if isinstance(item.get("player"), dict):
                name = name or item["player"].get("fullName")
                pid = pid or item["player"].get("id")
            if pid is not None and name:
                out[int(pid)] = str(name)
        return out
    if not isinstance(payload, dict):
        return out
    for block in (payload.get("players"), payload.get("elements")):
        if not isinstance(block, list):
            continue
        for item in block:
            if not isinstance(item, dict):
                continue
            pid = item.get("id")
            name = None
            if isinstance(item.get("player"), dict):
                name = item["player"].get("fullName")
                pid = pid or item["player"].get("id")
            name = name or item.get("fullName")
            if pid is not None and name:
                out[int(pid)] = str(name)
    dd = payload.get("draftDetail") or {}
    for pick in dd.get("picks") or []:
        if not isinstance(pick, dict):
            continue
        pl = pick.get("player")
        if isinstance(pl, dict) and pl.get("id") and pl.get("fullName"):
            out[int(pl["id"])] = str(pl["fullName"])
    return out


CACHE_MAX_AGE_DAYS = 7  # draft-day maximum cache age


def load_player_cache():
    if not os.path.isfile(PLAYER_CACHE_PATH):
        return {}
    try:
        with open(PLAYER_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # New metadata format: {season, fetched_at, players}
        if isinstance(raw, dict) and "players" in raw:
            season = raw.get("season")
            fetched_at = raw.get("fetched_at", 0)
            # Reject wrong-season cache
            if season is not None and season != time.localtime().tm_year:
                return {}
            # Reject expired cache (> 7 days)
            age_days = (time.time() - fetched_at) / 86400 if fetched_at else 999
            if age_days > CACHE_MAX_AGE_DAYS:
                return {}
            players = raw["players"]
            if isinstance(players, dict):
                return {int(k): str(v) for k, v in players.items()}
            return {}
        # Legacy format: bare {id: name} map — treat as stale, return empty
        # (will trigger a fresh fetch)
        return {}
    except Exception:
        return {}


def save_player_cache(player_map):
    try:
        os.makedirs(os.path.dirname(PLAYER_CACHE_PATH), exist_ok=True)
        data = {
            "season": time.localtime().tm_year,
            "fetched_at": time.time(),
            "players": {str(k): v for k, v in player_map.items()},
        }
        with open(PLAYER_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def build_player_map(cfg, force_refresh=False):
    """Load id→name map: cache first, else ESPN players_wl (+ filter)."""
    cached = load_player_cache()
    if cached and len(cached) >= 500 and not force_refresh:
        return cached, "cache"
    url = players_url(cfg["season"])
    # Prefer filtered full catalog; fall back to unfiltered 50-slice
    try:
        extra = fetch_json(url, cfg, fantasy_filter=PLAYERS_FILTER)
        pmap = extract_player_map(extra)
    except Exception:
        pmap = {}
    if len(pmap) < 500:
        try:
            extra = fetch_json(url, cfg)
            pmap.update(extract_player_map(extra))
        except Exception:
            pass
    if cached:
        # merge so we never shrink on a partial fetch
        merged = dict(cached)
        merged.update(pmap)
        pmap = merged
    if len(pmap) >= 100:
        save_player_cache(pmap)
    return pmap, "espn"


def parse_draft_state(payload, player_map=None):
    """Return (nominee_dict_or_None, completed_picks_list, player_map).

    nominee keys: pick_id, player_id, name, bid, nominating_team_id, team_id
    completed pick keys: same + in_progress False

    Live auction notes (ESPN 2026):
    - Pre-draft placeholder picks use playerId=-1, teamId=-1, bidAmount=0.
    - On-block may be inProgress=true, OR draftDetail.inProgress with a real
      playerId and teamId still unset (<=0) while bidAmount is the live bid.
    - Completed sales have playerId>0, teamId>0, bidAmount>0.
    """
    player_map = dict(player_map or {})
    player_map.update(extract_player_map(payload))
    dd = payload.get("draftDetail") or {}
    draft_live = bool(dd.get("inProgress"))
    picks = dd.get("picks") or []
    nominee = None
    completed = []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        pid = pick.get("playerId")
        if pid is None or int(pid) <= 0:
            continue
        pid = int(pid)
        team_id = pick.get("teamId")
        try:
            team_id_i = int(team_id) if team_id is not None else -1
        except (TypeError, ValueError):
            team_id_i = -1
        bid = int(pick.get("bidAmount") or 0)
        in_prog = bool(pick.get("inProgress"))
        # Heuristic: nominated but not awarded yet
        on_block = in_prog or (
            draft_live and team_id_i <= 0 and bid >= 0)
        row = {
            "pick_id": pick.get("id"),
            "player_id": pid,
            "name": player_map.get(pid) or ("player#%d" % pid),
            "bid": bid,
            "nominating_team_id": pick.get("nominatingTeamId"),
            "team_id": team_id if team_id_i > 0 else None,
            "in_progress": on_block,
        }
        if on_block:
            nominee = row
        elif team_id_i > 0 and bid > 0:
            completed.append(row)
    return nominee, completed, player_map


def map_team_token(team_id, team_map):
    if team_id is None:
        return None
    return team_map.get(int(team_id))


class EspnDraftWatcher(object):
    """Daemon poller; pushes events onto a thread-safe deque for the REPL."""

    def __init__(self, cfg, events=None):
        self.cfg = cfg
        self.events = events if events is not None else deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._player_map = {}
        self._player_map_source = ""
        self._last_nominee_key = None
        self._seen_sold_ids = set()
        self._warn_network = False
        self._stale_announced = False
        self._last_ok_at = time.time()
        self.last_nominee = None
        self.pending_sales = deque()  # unlogged ESPN sales, keyed by pick_id

    def _push(self, kind, payload):
        with self._lock:
            self.events.append({"kind": kind, "payload": payload})

    def pop_events(self):
        out = []
        with self._lock:
            while self.events:
                out.append(self.events.popleft())
        return out

    def ensure_player_map(self, force_refresh=False):
        if self._player_map and len(self._player_map) >= 500 and not force_refresh:
            return len(self._player_map)
        self._player_map, self._player_map_source = build_player_map(
            self.cfg, force_refresh=force_refresh)
        return len(self._player_map)

    def fetch_state(self):
        url = draft_detail_url(self.cfg["league_id"], self.cfg["season"])
        # 10s timeout: draft detail is a small response; well under 15s stale threshold
        payload = fetch_json(url, self.cfg, timeout=10)
        self.ensure_player_map()
        nominee, completed, self._player_map = parse_draft_state(
            payload, self._player_map)
        return nominee, completed, payload

    def bootstrap_seen(self):
        """Mark already-completed picks so we don't replay sales on watch start.

        Also pushes completed picks into pending_sales so the REPL can
        reconcile them against the Auction Log (catching sales lost to a
        previous session crash).
        """
        try:
            n_players = self.ensure_player_map()
            nominee, completed, payload = self.fetch_state()
            dd = (payload or {}).get("draftDetail") or {}
            for row in completed:
                self._seen_sold_ids.add(row["pick_id"])
                # Normalize with mapped winner token and price
                sale = self._normalize_sale(row)
                self.pending_sales.append(sale)
            if nominee:
                self._last_nominee_key = (nominee["pick_id"], nominee["player_id"])
                self.last_nominee = nominee
            self._push(EVENT_INFO, {
                "msg": (
                    "watch connected: league %s | players mapped=%d (%s) | "
                    "completed=%d | draftLive=%s | block=%s"
                    % (self.cfg["league_id"], n_players,
                       self._player_map_source or "?",
                       len(self._seen_sold_ids),
                       bool(dd.get("inProgress")),
                       (nominee or {}).get("name") or "(none)"))
            })
            if n_players < 500:
                self._push(EVENT_ERROR, {
                    "msg": (
                        "player name map is thin (%d) — on-block may show "
                        "player#ID. Re-run: python espn_probe.py --refresh-players"
                        % n_players)
                })
            if nominee:
                payload_nom = dict(nominee)
                payload_nom["_kind"] = "nominee"
                self._push(EVENT_NOMINEE, payload_nom)
        except Exception as e:
            self._push(EVENT_ERROR, {"msg": "watch bootstrap failed: %s" % e})
            raise

    def _normalize_sale(self, row):
        """Normalize a completed pick into a standard sale record."""
        team_id = row.get("team_id")
        try:
            tok = self.cfg.get("team_map", {}).get(int(team_id)) if team_id else None
        except (TypeError, ValueError):
            tok = None
        return {
            "pick_id": row.get("pick_id"),
            "name": row.get("name", ""),
            "winner_token": tok,
            "winner_disp": tok,
            "price": int(row.get("bid") or 0),
            "logged": False,
        }

    def _tick(self):
        nominee, completed, _payload = self.fetch_state()
        for row in completed:
            pid = row["pick_id"]
            if pid in self._seen_sold_ids:
                continue
            self._seen_sold_ids.add(pid)
            sale = self._normalize_sale(row)
            self.pending_sales.append(sale)
            self._push(EVENT_SOLD, sale)
        if nominee:
            key = (nominee["pick_id"], nominee["player_id"])
            prev = self.last_nominee
            same_player = (prev and prev.get("player_id") == nominee["player_id"]
                           and prev.get("pick_id") == nominee["pick_id"])
            bid_changed = same_player and prev.get("bid") != nominee.get("bid")
            if key != self._last_nominee_key:
                self._last_nominee_key = key
                self.last_nominee = nominee
                row = dict(nominee)
                row["_kind"] = "nominee"
                self._push(EVENT_NOMINEE, row)
            elif bid_changed:
                self.last_nominee = nominee
                row = dict(nominee)
                row["_kind"] = "bid"
                self._push(EVENT_BID, row)
        else:
            self._last_nominee_key = None
            self.last_nominee = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
                self._warn_network = False
                self._last_ok_at = time.time()
                if self._stale_announced:
                    self._stale_announced = False
                    self._push(EVENT_INFO, {"msg": "watch recovered — ESPN polls OK again"})
            except Exception as e:
                if not self._warn_network:
                    self._push(EVENT_ERROR, {"msg": "ESPN poll error: %s" % e})
                    self._warn_network = True
                elapsed = time.time() - self._last_ok_at
                if elapsed >= STALE_AFTER_S and not self._stale_announced:
                    self._stale_announced = True
                    self._push(EVENT_STALE, {
                        "msg": "watch stale (%.0fs) — use who" % elapsed
                    })
            self._stop.wait(self.cfg["poll_seconds"])

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.bootstrap_seen()
        self._thread = threading.Thread(target=self._loop, name="EspnWatch",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=self.cfg["poll_seconds"] + 1)
            if t.is_alive():
                # Thread still running (blocked in network I/O) — leave it
                # as a daemon; it will die with the process.  Don't null
                # self._thread so running property stays accurate.
                self._push(EVENT_INFO, {
                    "msg": "watch stop: poll thread still alive (will exit with process)"})
                return
        self._thread = None
        self._push(EVENT_INFO, {"msg": "watch stopped"})

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())


def probe_to_file(cfg, out_path, refresh_players=False):
    """Fetch live mDraftDetail and write JSON for inspection."""
    pmap, src = build_player_map(cfg, force_refresh=refresh_players)
    url = draft_detail_url(cfg["league_id"], cfg["season"])
    payload = fetch_json(url, cfg)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    nominee, completed, pmap = parse_draft_state(payload, pmap)
    dd = payload.get("draftDetail") or {}
    return {
        "url": url,
        "nominee": nominee,
        "completed_n": len(completed),
        "player_map_n": len(pmap),
        "player_map_source": src,
        "draft_live": bool(dd.get("inProgress")),
        "drafted": bool(dd.get("drafted")),
        "out_path": out_path,
    }


def load_fixture(path=None):
    path = path or FIXTURE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
