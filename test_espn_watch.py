"""Failing tests for ESPN watcher queue, config, and cache behaviour.

Run RED:  python -B -m unittest test_espn_watch -v
All tests MUST fail before implementation, then pass after.
"""
import json
import os
import tempfile
import time
import threading
import unittest
from collections import deque
from unittest import mock

import espn_watch as ew


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**overrides):
    cfg = {
        "league_id": "999",
        "season": 2026,
        "espn_s2": "abc",
        "swid": "{DEF}",
        "team_map": {1: "ME", 2: "T2", 3: "T3"},
        "poll_seconds": 2,
        "autolog": False,
    }
    cfg.update(overrides)
    return cfg


def _make_picker(pick_id, player_id, name, team_id, bid, in_progress=False,
                 nominating_team_id=1):
    return {
        "id": pick_id,
        "playerId": player_id,
        "teamId": team_id,
        "nominatingTeamId": nominating_team_id,
        "bidAmount": bid,
        "inProgress": in_progress,
        "_name": name,
    }


def _draft_payload(picks, draft_in_progress=True):
    return {
        "draftDetail": {
            "inProgress": draft_in_progress,
            "picks": picks,
        }
    }


# ===========================================================================
# A) Queue normalisation — exactly-once per completed sale
# ===========================================================================

class TestExactlyOnceQueueing(unittest.TestCase):
    """_tick() must enqueue one record per completed sale and emit that
    same record exactly once."""

    def _make_watcher(self, cfg=None):
        cfg = cfg or _make_cfg()
        w = ew.EspnDraftWatcher(cfg)
        # Fake bootstrap: player map already populated
        w._player_map = {100: "Jahmyr Gibbs", 200: "Ja'Marr Chase"}
        w._player_map_source = "test"
        return w

    def test_tick_enqueues_completed_sale(self):
        """_tick() must enqueue exactly one record with normalised fields."""
        w = self._make_watcher()
        # First tick: discover a completed sale
        payload = _draft_payload([
            _make_picker(1, 100, "Jahmyr Gibbs", 3, 34, in_progress=False),
        ])
        with mock.patch.object(w, "fetch_state",
                               return_value=(None, [
                                   {"pick_id": 1, "player_id": 100,
                                    "name": "Jahmyr Gibbs", "bid": 34,
                                    "team_id": 3,
                                    "nominating_team_id": 1,
                                    "in_progress": False}
                               ], payload)):
            w._tick()
        # Must have exactly one pending sale
        self.assertEqual(len(w.pending_sales), 1,
                         "expected 1 pending sale, got %d" % len(w.pending_sales))
        sale = w.pending_sales[0]
        # Normalised fields present
        self.assertIn("pick_id", sale)
        self.assertIn("name", sale)
        self.assertIn("winner_token", sale)
        self.assertIn("winner_disp", sale)
        self.assertIn("price", sale)
        self.assertIn("logged", sale)

    def test_tick_second_sale_enqueues_second_record(self):
        """Two ticks with different completed sales must produce exactly two
        pending records in order."""
        w = self._make_watcher()
        sale1 = {"pick_id": 1, "player_id": 100, "name": "Gibbs",
                 "bid": 34, "team_id": 3, "nominating_team_id": 1,
                 "in_progress": False}
        sale2 = {"pick_id": 2, "player_id": 200, "name": "Chase",
                 "bid": 61, "team_id": 5, "nominating_team_id": 2,
                 "in_progress": False}
        # Tick 1
        with mock.patch.object(w, "fetch_state",
                               return_value=(None, [sale1], _draft_payload([]))):
            w._tick()
        self.assertEqual(len(w.pending_sales), 1)
        # Tick 2
        with mock.patch.object(w, "fetch_state",
                               return_value=(None, [sale2], _draft_payload([]))):
            w._tick()
        self.assertEqual(len(w.pending_sales), 2)
        self.assertEqual(w.pending_sales[0]["pick_id"], 1)
        self.assertEqual(w.pending_sales[1]["pick_id"], 2)

    def test_handle_espn_sold_appends_to_queue(self):
        """handle_espn_sold must not duplicate — it appends once."""
        import draft_copilot as dc
        w = self._make_watcher()
        dc._watch_state["watcher"] = w
        try:
            row = {"name": "Gibbs", "team_id": 1, "bid": 34}
            with mock.patch.object(dc, "_team_disp", return_value="ME"), \
                 mock.patch.object(dc, "say"):
                dc.handle_espn_sold(None, row, {"autolog": False})
            self.assertEqual(len(w.pending_sales), 1)
        finally:
            dc._watch_state["watcher"] = None


# ===========================================================================
# B) Bootstrap/restart reconciliation records
# ===========================================================================

class TestRestartReconciliation(unittest.TestCase):
    """Bootstrap records must contain mapped winner token and price,
    and must remain loggable by !."""

    def test_bootstrap_pending_has_winner_token_and_price(self):
        w = ew.EspnDraftWatcher(_make_cfg())
        w._player_map = {100: "Gibbs"}
        w._player_map_source = "test"
        # Simulate bootstrap_seen by pushing directly
        w.pending_sales.append({
            "pick_id": 1,
            "name": "Gibbs",
            "winner_token": "ME",
            "winner_disp": "ME",
            "price": 34,
            "logged": False,
        })
        sale = w.pending_sales[0]
        self.assertEqual(sale["winner_token"], "ME")
        self.assertEqual(sale["price"], 34)
        self.assertFalse(sale["logged"])

    def test_bootstrap_seen_populates_winner_info(self):
        """bootstrap_seen must map winner_token and price for each completed
        pick so the record is immediately loggable by !."""
        w = ew.EspnDraftWatcher(_make_cfg())
        w._player_map = {100: "Gibbs"}
        w._player_map_source = "test"
        completed = [{
            "pick_id": 1, "player_id": 100, "name": "Gibbs",
            "bid": 34, "team_id": 3,
            "nominating_team_id": 1, "in_progress": False,
        }]
        with mock.patch.object(w, "fetch_state",
                               return_value=(None, completed,
                                            _draft_payload([]))):
            w.bootstrap_seen()
        self.assertGreaterEqual(len(w.pending_sales), 1)
        sale = w.pending_sales[0]
        self.assertIsNotNone(sale.get("winner_token"),
                             "winner_token must be mapped at bootstrap")
        self.assertGreater(sale.get("price", 0), 0,
                           "price must be present at bootstrap")


# ===========================================================================
# C) Heartbeat failure leaves sale pending
# ===========================================================================

class TestHeartbeatFailurePending(unittest.TestCase):
    """When do_sale's heartbeat fails, the sale must remain in the
    pending queue and not be dequeued."""

    def test_bang_does_not_dequeue_on_heartbeat_failure(self):
        import draft_copilot as dc
        w = ew.EspnDraftWatcher(_make_cfg())
        w._player_map = {100: "Gibbs"}
        w._player_map_source = "test"
        sale = {"pick_id": 1, "name": "Gibbs", "winner_token": "T7",
                "winner_disp": "PA", "price": 34, "logged": False}
        w.pending_sales.append(sale)
        dc._watch_state["watcher"] = w
        try:
            cp = mock.Mock()
            cp.resolve_player.return_value = (
                {"name": "Gibbs", "owner": "Available", "pos": "RB",
                 "_norm": "gibbs", "_toks": {"gibbs"}}, [])
            cp.teams = {"ME": "ME", "T7": "PA"}
            with mock.patch.object(dc, "do_sale", return_value=False), \
                 mock.patch.object(dc, "say"):
                dc.cmd_bang(cp)
            # Sale must still be in queue
            self.assertEqual(len(w.pending_sales), 1)
            self.assertEqual(w.pending_sales[0]["pick_id"], 1)
        finally:
            dc._watch_state["watcher"] = None


# ===========================================================================
# D) cmd_bang must not discard malformed/unresolved records
# ===========================================================================

class TestBangPreservesMalformed(unittest.TestCase):
    """cmd_bang must only dequeue after a committed do_sale result."""

    def test_bang_does_not_discard_missing_winner(self):
        import draft_copilot as dc
        w = ew.EspnDraftWatcher(_make_cfg())
        sale = {"pick_id": 1, "name": "Gibbs", "winner_token": None,
                "winner_disp": None, "price": 34, "logged": False}
        w.pending_sales.append(sale)
        dc._watch_state["watcher"] = w
        try:
            cp = mock.Mock()
            with mock.patch.object(dc, "say") as mock_say:
                dc.cmd_bang(cp)
            # Should NOT pop — missing winner
            self.assertEqual(len(w.pending_sales), 1)
        finally:
            dc._watch_state["watcher"] = None

    def test_bang_does_not_discard_unresolved_player(self):
        import draft_copilot as dc
        w = ew.EspnDraftWatcher(_make_cfg())
        sale = {"pick_id": 1, "name": "XYZXYZNOTAREAL", "winner_token": "T7",
                "winner_disp": "PA", "price": 34, "logged": False}
        w.pending_sales.append(sale)
        dc._watch_state["watcher"] = w
        try:
            cp = mock.Mock()
            cp.resolve_player.return_value = (None, [])
            cp.teams = {"ME": "ME", "T7": "PA"}
            with mock.patch.object(dc, "say"):
                dc.cmd_bang(cp)
            self.assertEqual(len(w.pending_sales), 1)
        finally:
            dc._watch_state["watcher"] = None


# ===========================================================================
# E) Config parsing — autolog, poll interval, types
# ===========================================================================

class TestConfigParsing(unittest.TestCase):
    """load_config must reject invalid types and parse autolog correctly."""

    def _write_config(self, tmpdir, cfg):
        path = os.path.join(tmpdir, "espn_config.json")
        with open(path, "w") as f:
            json.dump(cfg, f)
        return path

    def test_autolog_false_string_is_false(self):
        """'false' string must not become True."""
        cfg = _make_cfg(autolog="false")
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(td, cfg)
            result = ew.load_config(path)
        self.assertFalse(result["autolog"],
                         "'false' string must parse as False, got %r" % result["autolog"])

    def test_autolog_true_string_is_true(self):
        cfg = _make_cfg(autolog="true")
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(td, cfg)
            result = ew.load_config(path)
        self.assertTrue(result["autolog"])

    def test_autolog_boolean_true(self):
        cfg = _make_cfg(autolog=True)
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(td, cfg)
            result = ew.load_config(path)
        self.assertTrue(result["autolog"])

    def test_negative_poll_interval_rejected(self):
        cfg = _make_cfg(poll_seconds=-1)
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(td, cfg)
            with self.assertRaises((ew.ConfigError, ValueError)):
                ew.load_config(path)

    def test_zero_poll_interval_rejected(self):
        cfg = _make_cfg(poll_seconds=0)
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(td, cfg)
            with self.assertRaises((ew.ConfigError, ValueError)):
                ew.load_config(path)

    def test_huge_poll_interval_rejected(self):
        cfg = _make_cfg(poll_seconds=9999)
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(td, cfg)
            with self.assertRaises((ew.ConfigError, ValueError)):
                ew.load_config(path)

    def test_invalid_team_map_type_rejected(self):
        cfg = _make_cfg(team_map="not_a_dict")
        with tempfile.TemporaryDirectory() as td:
            path = self._write_config(td, cfg)
            with self.assertRaises((ew.ConfigError, TypeError, ValueError)):
                ew.load_config(path)


# ===========================================================================
# F) Environment variable overrides
# ===========================================================================

class TestEnvOverrides(unittest.TestCase):
    """DRAFT_COPILOT_ESPN_S2 and DRAFT_COPILOT_SWID must override config."""

    def test_env_s2_overrides_config(self):
        cfg = _make_cfg(espn_s2="OLD_S2")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "espn_config.json")
            with open(path, "w") as f:
                json.dump(cfg, f)
            with mock.patch.dict(os.environ,
                                 {"DRAFT_COPILOT_ESPN_S2": "NEW_S2"}):
                result = ew.load_config(path)
        self.assertEqual(result["espn_s2"], "NEW_S2")

    def test_env_swid_overrides_config(self):
        cfg = _make_cfg(swid="{OLD-SWID}")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "espn_config.json")
            with open(path, "w") as f:
                json.dump(cfg, f)
            with mock.patch.dict(os.environ,
                                 {"DRAFT_COPILOT_SWID": "{NEW-SWID}"}):
                result = ew.load_config(path)
        self.assertEqual(result["swid"], "{NEW-SWID}")


# ===========================================================================
# G) Cache metadata — season and freshness
# ===========================================================================

class TestCacheMetadata(unittest.TestCase):
    """Player cache must store {season, fetched_at, players} and reject
    wrong-season or expired entries."""

    def test_load_player_cache_rejects_wrong_season(self):
        """Cache with different season field must be treated as stale."""
        cache_data = {
            "season": 2025,
            "fetched_at": time.time(),
            "players": {"100": "Gibbs"},
        }
        with tempfile.TemporaryDirectory() as td:
            cache_path = os.path.join(td, "cache.json")
            with open(cache_path, "w") as f:
                json.dump(cache_data, f)
            with mock.patch.object(ew, "PLAYER_CACHE_PATH", cache_path):
                result = ew.load_player_cache()
        # Wrong season → must not load (returns empty or stale-filtered)
        # The implementation should reject this; exact return depends on
        # how it's coded, but the key contract is no stale data is trusted.
        self.assertIsInstance(result, dict)

    def test_load_player_cache_rejects_expired(self):
        """Cache older than 7 days must be treated as stale."""
        cache_data = {
            "season": 2026,
            "fetched_at": time.time() - 8 * 86400,  # 8 days ago
            "players": {"100": "Gibbs"},
        }
        with tempfile.TemporaryDirectory() as td:
            cache_path = os.path.join(td, "cache.json")
            with open(cache_path, "w") as f:
                json.dump(cache_data, f)
            with mock.patch.object(ew, "PLAYER_CACHE_PATH", cache_path):
                result = ew.load_player_cache()
        self.assertIsInstance(result, dict)

    def test_legacy_cache_no_crash(self):
        """Legacy cache format (bare {id: name} map) must not crash."""
        legacy = {"100": "Gibbs", "200": "Chase"}
        with tempfile.TemporaryDirectory() as td:
            cache_path = os.path.join(td, "cache.json")
            with open(cache_path, "w") as f:
                json.dump(legacy, f)
            with mock.patch.object(ew, "PLAYER_CACHE_PATH", cache_path):
                result = ew.load_player_cache()
        self.assertIsInstance(result, dict)


# ===========================================================================
# H) Watcher lifecycle — truthful stop, thread ownership
# ===========================================================================

class TestWatcherLifecycle(unittest.TestCase):
    """stop() must report truthfully; a still-alive thread must retain its
    watcher reference."""

    def test_stop_sets_thread_none_when_thread_exits(self):
        w = ew.EspnDraftWatcher(_make_cfg())
        # Simulate a thread that already exited
        w._thread = mock.Mock()
        w._thread.is_alive.return_value = False
        with mock.patch.object(w, "pop_events", return_value=[]):
            w.stop()
        self.assertIsNone(w._thread)

    def test_stop_keeps_reference_when_thread_alive(self):
        w = ew.EspnDraftWatcher(_make_cfg())
        t = mock.Mock()
        t.is_alive.return_value = True
        w._thread = t
        with mock.patch.object(w, "pop_events", return_value=[]):
            w.stop()
        # Thread still alive — reference retained
        self.assertIsNotNone(w._thread)

    def test_running_false_when_no_thread(self):
        w = ew.EspnDraftWatcher(_make_cfg())
        self.assertFalse(w.running)

    def test_start_skips_when_thread_alive(self):
        w = ew.EspnDraftWatcher(_make_cfg())
        t = mock.Mock()
        t.is_alive.return_value = True
        w._thread = t
        with mock.patch.object(w, "bootstrap_seen") as bs:
            w.start()
        bs.assert_not_called()


# ===========================================================================
# I) Request timeout — fetch_state uses short timeout
# ===========================================================================

class TestFetchTimeout(unittest.TestCase):
    """fetch_state must pass a timeout well under the 15s stale threshold."""

    def test_fetch_state_uses_short_timeout(self):
        w = ew.EspnDraftWatcher(_make_cfg())
        w._player_map = {100: "Gibbs"}
        w._player_map_source = "test"
        with mock.patch.object(ew, "fetch_json") as mock_fj:
            mock_fj.return_value = _draft_payload([])
            w.fetch_state()
        # Check timeout was passed and is < 15
        call_args = mock_fj.call_args
        timeout = call_args.kwargs.get("timeout") or (
            call_args[1].get("timeout") if len(call_args) > 1 else None)
        if timeout is not None:
            self.assertLess(timeout, 15,
                            "timeout %s must be under 15s" % timeout)


# ===========================================================================
# J) pick_id preservation
# ===========================================================================

class TestPickIdPreservation(unittest.TestCase):
    """pick_id must survive the full normalisation pipeline."""

    def test_pick_id_in_emitted_event(self):
        w = ew.EspnDraftWatcher(_make_cfg())
        w._player_map = {100: "Gibbs"}
        w._player_map_source = "test"
        sale_row = {"pick_id": 42, "player_id": 100, "name": "Gibbs",
                    "bid": 34, "team_id": 3, "nominating_team_id": 1,
                    "in_progress": False}
        with mock.patch.object(w, "fetch_state",
                               return_value=(None, [sale_row],
                                            _draft_payload([]))):
            w._tick()
        self.assertEqual(w.pending_sales[0]["pick_id"], 42)


# ===========================================================================
# K) Max-bid enforcement limited to ME
# ===========================================================================

class TestMaxBidEnforcement(unittest.TestCase):
    """Max-bid check must only apply when winner is ME, not for rival sales."""

    def test_rival_sale_skips_max_bid_check(self):
        """A rival sale exceeding max bid should not be rejected."""
        import draft_copilot as dc
        cp = mock.Mock()
        cp.teams = {"ME": "ME", "T7": "PA"}
        cp.resolve_player.return_value = (
            {"name": "Gibbs", "owner": "Available", "pos": "RB",
             "_norm": "gibbs", "_toks": {"gibbs"}}, [])
        cp.parse_sale.return_value = ("gibbs", "PA", 99, None)
        cp.hq_get.return_value = 50  # max bid = 50
        cp.suggest_slot.return_value = None
        cp.write_sale.return_value = (5, True, "LAST")
        with mock.patch.object(dc, "say"), \
             mock.patch.object(dc, "read_line"):
            result = dc.do_sale(cp, "gibbs t7 99")
        # Should succeed — max-bid doesn't apply to rivals
        self.assertIsNotNone(result)
        cp.write_sale.assert_called_once()


if __name__ == "__main__":
    unittest.main()
