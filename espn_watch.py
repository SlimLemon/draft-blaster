"""ESPN live auction watcher for Draft Co-Pilot.

Polls mDraftDetail for the in-progress nominee and completed sales.
Uses stdlib only (urllib/json/threading) so LibreOffice's Python can import it.
"""
from __future__ import print_function

import json
import os
import threading
import time
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

# Re-exports: draft_copilot.py and other scripts import these from espn_watch.
from espn_client import (
    EspnClient, ConfigError, load_config, draft_detail_url, players_url,
    extract_player_map, map_team_token, valid_player_id,
)


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
        if not valid_player_id(pid):
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


class EspnDraftWatcher(object):
    """Daemon poller; pushes events onto a thread-safe deque for the REPL."""

    def __init__(self, cfg, events=None, client=None):
        self.cfg = cfg
        self.client = client or EspnClient(cfg)
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
        self._player_map, self._player_map_source = self.client.build_player_map(
            force_refresh=force_refresh)
        return len(self._player_map)

    def fetch_state(self):
        # 10s timeout: draft detail is a small response; well under 15s stale threshold
        payload = self.client.draft_detail(timeout=10)
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
        tok = self.client.team_resolver().resolve(team_id)["token"] or None
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


def probe_to_file(cfg, out_path, refresh_players=False, client=None):
    """Fetch live mDraftDetail and write JSON for inspection."""
    client = client or EspnClient(cfg)
    pmap, src = client.build_player_map(force_refresh=refresh_players)
    url = draft_detail_url(cfg["league_id"], cfg["season"])
    payload = client.draft_detail()
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
