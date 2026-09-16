"""Shared ESPN boundary tests: all HTTP and cache files are controlled."""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit
import espn_client as ec
from league_snapshot import LeagueSnapshot

CFG = {"league_id": "999", "season": 2025, "espn_s2": "test-s2", "swid": "{test}",
       "team_map": {2: "ME"}, "team_labels": {"2": "Local"}}

def payload():
    return {"id": 999, "seasonId": 2025, "settings": {"name": "Example"},
            "status": {"currentScoringPeriod": 2}, "members": [],
            "teams": [{"id": 2, "name": "Current", "roster": {"entries": [
                {"playerId": 3, "playerPoolEntry": {"player": {
                    "id": 3, "fullName": "Payload Player", "defaultPositionId": 2}}}]}}],
            "schedule": [], "transactions": [], "pendingTransactions": [],
            "draftDetail": {"picks": [
                {"id": 1, "playerId": 1, "teamId": 2, "bidAmount": 40},
                {"id": 2, "playerId": 3, "teamId": 2, "bidAmount": 1},
                {"id": 3, "playerId": 999, "teamId": 2, "bidAmount": 0},
                {"id": 4, "playerId": -16001, "teamId": 2, "bidAmount": 1}]}}

class Network:
    def __init__(self, catalog=None, league=None, fail_catalog=False):
        self.catalog = catalog if catalog is not None else [
            {"id": 1, "fullName": "Fresh Player", "defaultPositionId": 1},
            {"id": -16001, "fullName": "Defense", "defaultPositionId": 16}]
        self.league = payload() if league is None else league
        self.fail_catalog = fail_catalog
        self.calls = []
    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if "/seasons/2025/players?" in request.full_url:
            if self.fail_catalog:
                raise OSError("network " + CFG["espn_s2"])
            data = self.catalog
        else:
            data = self.league
        response = io.BytesIO(json.dumps(data).encode())
        response.headers = {"content-type": "application/json"}
        return response
    @property
    def catalog_calls(self):
        return [r for r, _ in self.calls if "/seasons/2025/players?" in r.full_url]

class ClientHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "cache.json"
    def client(self, network):
        return ec.EspnClient(CFG, cache_path=str(self.cache), opener=network)
    def cache_names(self, season=2025, age=0):
        self.cache.write_text(json.dumps({"season": season, "fetched_at": time.time()-age,
                                         "players": {"1": "Old", "2": "Cache Only"}}))

class ClientTests(ClientHarness):
    def test_environment_credentials_override_placeholders_before_validation(self):
        path = Path(self.tmp.name) / "config.json"
        cfg = dict(CFG, espn_s2="PASTE_COOKIE", swid="")
        path.write_text(json.dumps(cfg))
        with mock.patch.dict(os.environ, {"DRAFT_COPILOT_ESPN_S2": "env-s2",
                                         "DRAFT_COPILOT_SWID": "{env}"}):
            actual = ec.load_config(str(path))
        self.assertEqual((actual["espn_s2"], actual["swid"]), ("env-s2", "{env}"))
    def test_repeated_views_encoded_and_cookie_header(self):
        net = Network()
        self.client(net).request("", views=("mTeam", "mRoster"), params={"q": "a & b"})
        req, timeout = net.calls[0]
        self.assertEqual(parse_qs(urlsplit(req.full_url).query),
                         {"view": ["mTeam", "mRoster"], "q": ["a & b"]})
        self.assertEqual(req.get_header("Cookie"), "espn_s2=test-s2; SWID={test}")
        self.assertEqual(timeout, 15)
    def test_html_and_network_errors_are_safe(self):
        def html(*args, **kwargs):
            response = io.BytesIO(b"<html>test-s2</html>")
            response.headers = {"content-type": "text/html"}
            return response
        with self.assertRaisesRegex(ec.EspnError, "cookies") as caught:
            self.client(html).draft_detail()
        self.assertNotIn("test-s2", str(caught.exception))
        with self.assertRaises(ec.EspnError) as caught:
            self.client(Network(fail_catalog=True)).request("players", ("players_wl",), catalog=True)
        self.assertNotIn("test-s2", str(caught.exception))
    def test_cache_uses_configured_season_and_rejects_expired_or_wrong_season(self):
        for season, age, expected in [(2025, 0, True), (2026, 0, False),
                                      (2025, 8*86400, False)]:
            self.cache_names(season, age)
            client = self.client(Network())
            self.assertEqual(bool(client.load_player_cache()), expected)
    def test_forced_refresh_once_keeps_per_player_provenance(self):
        self.cache_names()
        net = Network()
        client = self.client(net)
        names, source = client.build_player_map(force_refresh=True)
        self.assertEqual(len(net.catalog_calls), 1)
        self.assertEqual(names[1], "Fresh Player")
        self.assertEqual(names[2], "Cache Only")
        self.assertEqual(client.player_sources[1], "fresh_catalog")
        self.assertEqual(client.player_sources[2], "cache_fallback")
        self.assertEqual(source, "espn")
        self.assertTrue(net.catalog_calls[0].get_header("X-fantasy-filter"))
    def test_failed_refresh_does_not_renew_cache_or_claim_freshness(self):
        self.cache_names()
        before = self.cache.read_bytes()
        client = self.client(Network(fail_catalog=True))
        names, source = client.build_player_map(force_refresh=True)
        self.assertEqual(names[1], "Old")
        self.assertEqual(source, "cache_fallback")
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertTrue(client.player_refresh_error)
    def test_team_identity_uses_explicit_token_without_guessing_seats(self):
        resolver = ec.TeamResolver(CFG["team_map"], payload()["teams"], CFG["team_labels"])
        self.assertEqual(resolver.resolve("2"), {"team_id": 2, "token": "ME", "label": "Local"})
        self.assertEqual(resolver.resolve(99), {"team_id": 99, "token": "", "label": ""})
        self.assertEqual(ec.TeamResolver({}, payload()["teams"]).resolve(2)["label"], "Current")

class SnapshotTests(ClientHarness):
    def test_snapshot_preserves_identity_source_fields_and_forces_once(self):
        self.cache_names()
        net = Network()
        snap = LeagueSnapshot.from_client(self.client(net), force_refresh_players=True)
        self.assertEqual(len(net.catalog_calls), 1)
        self.assertEqual(len(net.calls), 2)
        rows = snap.draft_picks
        self.assertEqual([r["player_id"] for r in rows], [1, 3, 999, -16001])
        self.assertEqual([r["player_name"] for r in rows],
                         ["Fresh Player", "Payload Player", None, "Defense"])
        self.assertEqual([r["player_name_source"] for r in rows],
                         ["fresh_catalog", "payload", "unresolved", "fresh_catalog"])
        self.assertEqual(rows[2]["bid_amount"], 0)
        self.assertEqual(snap.unresolved_player_ids, [999])
        self.assertEqual(snap.raw_snapshot, net.league)
        self.assertEqual(snap.teams[0]["team_id"], 2)
        self.assertIsNone(snap.teams[0]["projected_rank"])
        self.assertEqual(snap.rosters[0]["player_id"], 3)
        query = parse_qs(urlsplit(net.calls[-1][0].full_url).query)
        self.assertEqual(query["view"], ["mSettings", "mTeam", "mRoster", "mMatchup",
                         "mDraftDetail", "mStatus", "mPendingTransactions", "mTransactions2"])
    def test_missing_required_section_fails_but_missing_transactions_is_unavailable(self):
        missing = payload()
        del missing["settings"]
        with self.assertRaisesRegex(ec.EspnError, "settings"):
            LeagueSnapshot.from_client(self.client(Network(league=missing)), True)
        missing = payload()
        del missing["transactions"]
        snap = LeagueSnapshot.from_client(self.client(Network(league=missing)), True)
        self.assertIsNone(snap.transactions)
        self.assertEqual(snap.coverage["transactions"], "unavailable")
    def test_payload_beats_cache_fallback_and_failure_is_recorded(self):
        self.cache_names()
        data = payload()
        data["draftDetail"]["picks"][0]["player"] = {"id": 1, "fullName": "Payload Name"}
        snap = LeagueSnapshot.from_client(self.client(Network(league=data, fail_catalog=True)), True)
        self.assertEqual(snap.draft_picks[0]["player_name"], "Payload Name")
        self.assertEqual(snap.draft_picks[0]["player_name_source"], "payload")
        self.assertTrue(snap.metadata["player_refresh_error"])

class ConsumerTests(ClientHarness):
    def test_watcher_uses_shared_client_and_preserves_pending_sales(self):
        import espn_watch as ew
        net = Network()
        watcher = ew.EspnDraftWatcher(dict(CFG, poll_seconds=2), client=self.client(net))
        watcher.bootstrap_seen()
        watcher._tick()
        self.assertEqual([sale["price"] for sale in watcher.pending_sales], [40, 1, 1])
        self.assertEqual(watcher.pending_sales[0]["winner_token"], "ME")
        draft_calls = [(r, t) for r, t in net.calls if "mDraftDetail" in r.full_url]
        self.assertEqual([t for _, t in draft_calls], [10, 10])
        self.assertEqual(len(net.catalog_calls), 1)

    def test_inseason_rosters_use_shared_request_and_team_resolver(self):
        import draft_copilot as dc
        net = Network(league=payload()["teams"])
        rows = dc.fetch_rosters(CFG, client=self.client(net))
        self.assertEqual(rows[0]["token"], "ME")
        self.assertEqual(rows[0]["roster"][0]["name"], "Payload Player")
        self.assertIn("view=mRoster", net.calls[0][0].full_url)
        self.assertEqual(len(net.catalog_calls), 1)
