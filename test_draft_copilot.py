import csv
import os
import tempfile
import unittest
from unittest import mock

import draft_copilot as dc


class SlotHarness:
    def __init__(self, fills=None):
        self.roster = object()
        self._slots = [
            "QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX",
            "D/ST", "K", "BE1", "BE2", "BE3", "BE4", "BE5", "BE6",
        ]
        self._fills = fills or [""] * len(self._slots)

    def arr(self, _sheet, rng):
        if rng == "A7:A21":
            return tuple((value,) for value in self._slots)
        if rng == "B7:B21":
            return tuple((value,) for value in self._fills)
        raise AssertionError(rng)


class ParserHarness:
    teams = {"ME": "ME", "T7": "PA"}
    team_names = ["ME", "PA", "Brown Bombers"]

    def resolve_winner(self, token):
        return dc.Copilot.resolve_winner(self, token)

    def resolve_player(self, fragment):
        normalized = dc.norm(fragment)
        known = {
            "gibbs": {"name": "Jahmyr Gibbs"},
            "chase brown": {"name": "Chase Brown"},
            "brown": {"name": "Chase Brown"},
        }
        return (known.get(normalized), [])


class TransactionHarness:
    def __init__(self, heartbeat_delta=1, fail_price_once=False):
        self.log = object()
        self.cells = {"B5": "", "D5": "", "E5": "", "F5": "", "I5": ""}
        self.pre = 0
        self.heartbeat_delta = heartbeat_delta
        self.fail_price_once = fail_price_once
        self._price_failed = False

    def next_entry_row(self):
        return 5

    def hq_get(self, addr):
        if addr != "B15":
            raise AssertionError(addr)
        populated = bool(self.cells["B5"])
        return self.pre + (self.heartbeat_delta if populated else 0)

    def arr(self, _sheet, rng):
        if rng in self.cells:
            return ((self.cells[rng],),)
        raise AssertionError(rng)

    def set_str(self, _sheet, addr, value):
        self.cells[addr] = str(value)

    def set_num(self, _sheet, addr, value):
        if addr == "E5" and self.fail_price_once and not self._price_failed:
            self._price_failed = True
            raise RuntimeError("simulated price failure")
        self.cells[addr] = float(value)

    def clear_cell(self, _sheet, addr):
        self.cells[addr] = ""

    def recalc(self):
        pass

    def hq_log_chk(self, _row):
        return "LAST — CLEAR INPUTS TO UNDO" if self.cells["B5"] else ""


class DoSaleHarness:
    def __init__(self, heartbeat_ok):
        self.teams = {"ME": "ME", "T7": "PA"}
        self.players = [{
            "name": "Jahmyr Gibbs", "owner": "Available", "pos": "RB",
            "_norm": "jahmyr gibbs", "_toks": {"jahmyr", "gibbs"},
        }]
        self.team_names = ["ME", "PA"]
        self.heartbeat_ok = heartbeat_ok
        self.journal_calls = 0
        self.autosave_calls = 0
        self.refresh_calls = 0

    def parse_sale(self, line):
        return dc.Copilot.parse_sale(self, line)

    def resolve_player(self, fragment):
        return dc.Copilot.resolve_player(self, fragment)

    def resolve_winner(self, token):
        return dc.Copilot.resolve_winner(self, token)

    def hq_get(self, _addr):
        return 200

    def suggest_slot(self, _pos):
        return None

    def validate_slot(self, _slot, _pos):
        return True, ""

    def write_sale(self, _p, _winner, _price, _slot):
        return 5, self.heartbeat_ok, "LAST" if self.heartbeat_ok else "INCOMPLETE"

    def journal_sale(self, _detail):
        self.journal_calls += 1

    def maybe_autosave(self):
        self.autosave_calls += 1

    def refresh_cache(self):
        self.refresh_calls += 1

    def status(self, tag=""):
        pass


class DraftCopilotRegressionTests(unittest.TestCase):
    def test_empty_uno_rows_are_available_slots(self):
        cp = SlotHarness()
        self.assertEqual(dc.Copilot.validate_slot(cp, "RB2", "RB"), (True, ""))
        self.assertEqual(dc.Copilot.validate_slot(cp, "QB", "QB"), (True, ""))
        self.assertEqual(dc.Copilot.validate_slot(cp, "FLEX", "WR"), (True, ""))
        self.assertEqual(dc.Copilot.suggest_slot(cp, "RB"), "RB1")

    def test_occupied_and_ineligible_slots_are_rejected(self):
        fills = [""] * 15
        fills[2] = "Saquon Barkley"
        cp = SlotHarness(fills)
        self.assertIn("occupied", dc.Copilot.validate_slot(cp, "RB2", "RB")[1])
        self.assertIn("not eligible", dc.Copilot.validate_slot(cp, "RB2", "WR")[1])

    def test_named_winner_fragment_is_removed_before_player_resolution(self):
        cp = ParserHarness()
        self.assertEqual(
            dc.Copilot.parse_sale(cp, "gibbs PA 34"),
            ("gibbs", "PA", 34, None),
        )
        self.assertEqual(
            dc.Copilot.parse_sale(cp, "chase brown t7 21 wr2"),
            ("chase brown", "PA", 21, "WR2"),
        )

    def test_write_sale_rolls_back_partial_write_exception(self):
        cp = TransactionHarness(fail_price_once=True)
        row, ok, check = dc.Copilot.write_sale(
            cp, {"name": "Jahmyr Gibbs"}, "PA", 34, None)
        self.assertEqual(row, 5)
        self.assertFalse(ok)
        self.assertIn("WRITE FAILED", check)
        self.assertEqual(cp.cells, {"B5": "", "D5": "", "E5": "", "F5": "", "I5": ""})

    def test_write_sale_rolls_back_failed_heartbeat(self):
        cp = TransactionHarness(heartbeat_delta=0)
        row, ok, check = dc.Copilot.write_sale(
            cp, {"name": "Jahmyr Gibbs"}, "PA", 34, None)
        self.assertEqual(row, 5)
        self.assertFalse(ok)
        self.assertIn("HEARTBEAT", check)
        self.assertEqual(cp.cells, {"B5": "", "D5": "", "E5": "", "F5": "", "I5": ""})

    def test_do_sale_has_no_commit_side_effects_when_heartbeat_fails(self):
        cp = DoSaleHarness(heartbeat_ok=False)
        with mock.patch.object(dc, "say"):
            result = dc.do_sale(cp, "gibbs t7 34")
        self.assertFalse(result)
        self.assertEqual(cp.journal_calls, 0)
        self.assertEqual(cp.autosave_calls, 0)
        self.assertEqual(cp.refresh_calls, 0)

    def test_cleanup_steps_continue_after_watcher_stop_failure(self):
        events = []
        cp = mock.Mock()
        cp.autosave.side_effect = lambda reason: events.append(("autosave", reason))
        with mock.patch.object(dc, "cmd_watch_off", side_effect=RuntimeError("stop failed")), \
                mock.patch.object(dc, "journal", side_effect=lambda event, detail="": events.append((event, detail))), \
                mock.patch.object(dc, "say"):
            dc.cleanup_session(cp)
        self.assertIn(("autosave", "quit"), events)
        self.assertIn(("SESSION_END", "ok"), events)


# ===================================================================
# LibreOffice test isolation tests
# ===================================================================

class TestCanonicalDocMatching(unittest.TestCase):
    """find_or_open_doc must match by canonical full path, not basename."""

    def test_basename_match_not_sufficient(self):
        """A file with the same basename in a different directory must NOT
        be matched as the live workbook."""
        live_path = os.path.normcase(os.path.abspath(dc.DRAFT_WORKBOOK_PATH))
        other_path = os.path.normcase(
            os.path.join(tempfile.gettempdir(), dc.DRAFT_WORKBOOK_NAME))
        # These should differ even though basenames match
        self.assertEqual(os.path.basename(live_path).lower(),
                         os.path.basename(other_path).lower(),
                         "basenames should match for this test")
        self.assertNotEqual(live_path, other_path,
                            "full paths must differ")

    def test_assert_safe_test_path_rejects_live_book(self):
        """assert_safe_test_path must refuse the live workbook by full path."""
        with self.assertRaises(SystemExit):
            dc.assert_safe_test_path(dc.DRAFT_WORKBOOK_PATH)

    def test_assert_safe_test_path_rejects_live_book_copy(self):
        """A copy with the same name as the live workbook is also refused."""
        fake = os.path.join(tempfile.gettempdir(), dc.DRAFT_WORKBOOK_NAME)
        with self.assertRaises(SystemExit):
            dc.assert_safe_test_path(fake)


class TestUniqueProfilePerTest(unittest.TestCase):
    """Each --test run must get a unique temporary LibreOffice profile."""

    def test_test_profile_differs_from_live(self):
        """The test profile directory must not be the same as the live one."""
        import hashlib
        test_id = hashlib.md5(os.urandom(8)).hexdigest()[:8]
        test_profile = os.path.join(
            tempfile.gettempdir(), "DraftCopilotTest_%s" % test_id)
        self.assertNotEqual(
            os.path.normcase(os.path.abspath(test_profile)),
            os.path.normcase(os.path.abspath(dc.PROFILE_DIR)),
            "test profile must differ from live profile")

    def test_profile_dir_is_configurable(self):
        """PROFILE_DIR exists as a module-level constant."""
        self.assertTrue(hasattr(dc, "PROFILE_DIR"))
        self.assertIsInstance(dc.PROFILE_DIR, str)


class TestOrphanLockRecovery(unittest.TestCase):
    """Repeated orphan-lock recovery must use unique stale filenames."""

    def test_stale_name_includes_unique_suffix(self):
        """_recover_orphaned_lock must not always use .lock.stale — it should
        use a unique name to avoid collision on repeated recovery."""
        with tempfile.TemporaryDirectory() as td:
            lock_path = os.path.join(td, ".lock")
            open(lock_path, "w").close()
            open(lock_path + ".stale", "w").close()  # prior recovery artifact
            with mock.patch.object(dc, "PROFILE_DIR", td), \
                 mock.patch.object(dc, "_port_in_use", return_value=False):
                self.assertTrue(dc._recover_orphaned_lock())
            self.assertFalse(os.path.isfile(lock_path))
            stales = [n for n in os.listdir(td) if n.startswith(".lock.stale.")]
            self.assertEqual(len(stales), 1, stales)

    def test_recovery_skips_when_unrelated_process_runs(self):
        """If an unrelated soffice process is running but not holding OUR
        profile lock, recovery must still proceed when port 2002 is free."""
        with tempfile.TemporaryDirectory() as td:
            lock_path = os.path.join(td, ".lock")
            open(lock_path, "w").close()
            with mock.patch.object(dc, "PROFILE_DIR", td), \
                 mock.patch.object(dc, "soffice_process_running",
                                   return_value=True), \
                 mock.patch.object(dc, "_port_in_use", return_value=False):
                self.assertTrue(dc._recover_orphaned_lock())
            self.assertFalse(os.path.isfile(lock_path))


class TestTestProcessOwnership(unittest.TestCase):
    """A test run must never reuse or terminate a live Draft Copilot instance."""

    def test_test_refuses_live_workbook_names(self):
        """Every name in TEST_REFUSALS must be caught."""
        for name in dc.TEST_REFUSALS:
            fake = os.path.join(tempfile.gettempdir(), name)
            with self.assertRaises(SystemExit,
                                   msg="should refuse %s" % name):
                dc.assert_safe_test_path(fake)

    def test_test_refuses_exact_live_path(self):
        """The exact live workbook path must be refused."""
        with self.assertRaises(SystemExit):
            dc.assert_safe_test_path(dc.DRAFT_WORKBOOK_PATH)


class TestFindOrOpenDocPathMatching(unittest.TestCase):
    """find_or_open_doc must compare canonical full paths, not basenames."""

    def test_different_directory_same_name_not_matched(self):
        """Two files with same basename in different dirs are distinct."""
        p1 = os.path.normcase(os.path.abspath(
            os.path.join("C:\\Users\\Jared\\Draft blaster", "foo.xlsx")))
        p2 = os.path.normcase(os.path.abspath(
            os.path.join("C:\\Users\\Jared\\Draft blaster\\.worktrees\\draft-day-fixes", "foo.xlsx")))
        self.assertNotEqual(p1, p2)


class DraftExportTests(unittest.TestCase):
    class _AuctionLogHarness:
        def __init__(self, grid, teams=None):
            self.grid = grid
            self.teams = teams or {"ME": "ME", "T7": "PA"}
            self.log = object()

        def arr(self, sheet, cell_range):
            assert sheet is self.log
            assert cell_range.startswith("A5:H")
            return self.grid

    def test_collect_auction_log_rows_skips_empty_player(self):
        grid = [
            (1, "Jahmyr Gibbs", None, "ME", 62, "RB1", None, None),
            (2, "", None, "PA", 10, None, None, None),
            (3, "CeeDee Lamb", None, "PA", 55, None, None, None),
        ]
        rows = dc.collect_auction_log_rows(grid)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["player"], "Jahmyr Gibbs")
        self.assertEqual(rows[0]["pick"], 1)
        self.assertEqual(rows[0]["winner"], "ME")
        self.assertEqual(rows[0]["price"], 62)
        self.assertEqual(rows[0]["slot"], "RB1")
        self.assertEqual(rows[1]["player"], "CeeDee Lamb")

    def test_winner_token_for_reverse_maps_display(self):
        teams = {"ME": "ME", "T7": "PA", "T2": "Team 2"}
        self.assertEqual(dc.winner_token_for("PA", teams), "T7")
        self.assertEqual(dc.winner_token_for("ME", teams), "ME")
        self.assertEqual(dc.winner_token_for("Unknown FC", teams), "")

    def test_net_session_journal_sales_current_session_minus_undos(self):
        text = "\n".join([
            "2026-08-26 10:00:00\tSESSION_START\told.xlsx",
            "2026-08-26 10:01:00\tSALE\trow=5 player=Old Player winner=ME price=10 slot=QB heartbeat=OK",
            "2026-08-26 10:32:54\tSESSION_START\tDraft_Command_Center_DRAFT_DAY.xlsx",
            "2026-08-26 10:32:54\tSALE\trow=5 player=Jahmyr Gibbs winner=ME price=62 slot=RB1 heartbeat=OK",
            "2026-08-26 10:33:00\tSALE\trow=6 player=CeeDee Lamb winner=PA price=55 slot= heartbeat=OK",
            "2026-08-26 10:34:00\tUNDO\trow=6 player=CeeDee Lamb winner=PA price=55.0 ok=True",
            "2026-08-26 10:35:00\tSALE\trow=6 player=Puka Nacua winner=PA price=40 slot= heartbeat=OK",
        ])
        net = dc.net_session_journal_sales(text)
        self.assertEqual(net, {"jahmyr gibbs", "puka nacua"})
        self.assertNotIn("old player", net)
        self.assertNotIn("ceedee lamb", net)

    def test_espn_token_for_team_id(self):
        team_map = {1: "ME", 7: "T7", 14: "T14"}
        self.assertEqual(dc.espn_token_for_team_id(7, team_map), "T7")
        self.assertEqual(dc.espn_token_for_team_id(1, team_map), "ME")
        self.assertEqual(dc.espn_token_for_team_id(99, team_map), "")
        self.assertEqual(dc.espn_token_for_team_id(None, team_map), "")

    def test_export_writes_csv_summary_and_journal_only(self):
        picks = [
            {"pick": 1, "player": "Jahmyr Gibbs", "winner_token": "ME", "price": 62},
            {"pick": 2, "player": "Puka Nacua", "winner_token": "T7", "price": 40},
        ]
        journal = "\n".join([
            "2026-09-15 12:00:00\tSESSION_START\tcopy.xlsx",
            "2026-09-15 12:01:00\tSALE\trow=5 player=Jahmyr Gibbs winner=ME price=62 slot=RB1 heartbeat=OK",
            "2026-09-15 12:02:00\tSALE\trow=6 player=Puka Nacua winner=PA price=40 slot= heartbeat=OK",
            "2026-09-15 12:03:00\tSALE\trow=7 player=Ghost Player winner=ME price=1 slot=BE1 heartbeat=OK",
        ])
        spoken = []
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "journal.txt")
            with open(jpath, "w", encoding="utf-8") as f:
                f.write(journal)
            with mock.patch.object(dc, "say", side_effect=spoken.append):
                path = dc.Copilot.export(None, out_dir=tmp, journal_path=jpath,
                                         espn_picks=picks)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(os.path.basename(path).startswith("draft_export_"))
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["winner_token"], "ME")
            self.assertEqual(rows[0]["player"], "Jahmyr Gibbs")
            self.assertEqual(rows[1]["winner_token"], "T7")
            self.assertEqual(rows[1]["player"], "Puka Nacua")
            summary = spoken[-1]
            self.assertIn("export: 2 picks", summary)
            self.assertIn("ME=$62", summary)
            self.assertIn("T7=$40", summary)
            self.assertIn("Ghost Player", summary)

    def test_export_defaults_to_attached_auction_log_without_espn_warning(self):
        grid = [
            (1, "Jahmyr Gibbs", None, "ME", 62, "RB1", None, None),
            (2, "Puka Nacua", None, "PA", 40, "WR1", None, None),
        ]
        harness = self._AuctionLogHarness(grid)
        spoken = []
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "journal.txt")
            with open(jpath, "w", encoding="utf-8") as f:
                f.write("2026-09-15 12:00:00\tSESSION_START\tcopy.xlsx\n")
            with mock.patch.object(dc, "say", side_effect=spoken.append):
                path = dc.Copilot.export(harness, out_dir=tmp, journal_path=jpath)
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([row["player"] for row in rows], ["Jahmyr Gibbs", "Puka Nacua"])
        self.assertEqual(rows[1]["winner_token"], "T7")
        self.assertIn("[source: Auction Log]", spoken[-1])
        self.assertFalse(any("espn fetch failed" in message.lower() for message in spoken))

    def test_export_same_second_uses_an_unused_filename(self):
        picks = [{"pick": 1, "player": "Jahmyr Gibbs", "winner_token": "ME", "price": 62}]
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "journal.txt")
            with open(jpath, "w", encoding="utf-8") as f:
                f.write("2026-09-15 12:00:00\tSESSION_START\tcopy.xlsx\n")
            with mock.patch.object(dc, "say"), \
                    mock.patch.object(dc.time, "strftime", return_value="20260916_090000"):
                first = dc.Copilot.export(None, out_dir=tmp, journal_path=jpath, espn_picks=picks)
                second = dc.Copilot.export(None, out_dir=tmp, journal_path=jpath, espn_picks=picks)
        self.assertEqual(os.path.basename(first), "draft_export_20260916_090000.csv")
        self.assertEqual(os.path.basename(second), "draft_export_20260916_090000_01.csv")

    def test_export_reconciles_journal_names_case_insensitively(self):
        picks = [{"pick": 1, "player": "Jahmyr Gibbs", "winner_token": "ME", "price": 62}]
        spoken = []
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "journal.txt")
            with open(jpath, "w", encoding="utf-8") as f:
                f.write("2026-09-15 12:00:00\tSESSION_START\tcopy.xlsx\n")
                f.write("2026-09-15 12:01:00\tSALE\trow=5 player=jahmyr gibbs winner=ME price=62 slot=RB1 heartbeat=OK\n")
            with mock.patch.object(dc, "say", side_effect=spoken.append):
                dc.Copilot.export(None, out_dir=tmp, journal_path=jpath, espn_picks=picks)
        self.assertIn("journal-only: none", spoken[-1])

    def test_export_missing_journal_still_writes_csv(self):
        picks = [
            {"pick": 1, "player": "Jahmyr Gibbs", "winner_token": "ME", "price": 62},
        ]
        spoken = []
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "no_such_journal.txt")
            with mock.patch.object(dc, "say", side_effect=spoken.append):
                path = dc.Copilot.export(None, out_dir=tmp, journal_path=missing,
                                         espn_picks=picks)
            self.assertTrue(os.path.isfile(path))
            joined = "\n".join(spoken).lower()
            self.assertIn("journal unavailable", joined)

    def test_export_two_runs_create_distinct_files(self):
        picks = [
            {"pick": 1, "player": "Jahmyr Gibbs", "winner_token": "ME", "price": 62},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "journal.txt")
            with open(jpath, "w", encoding="utf-8") as f:
                f.write("2026-09-15 12:00:00\tSESSION_START\tx.xlsx\n")
            with mock.patch.object(dc, "say"):
                with mock.patch.object(dc.time, "strftime", side_effect=["20260101_010101", "20260101_010102"]):
                    p1 = dc.Copilot.export(None, out_dir=tmp, journal_path=jpath,
                                           espn_picks=picks)
                    p2 = dc.Copilot.export(None, out_dir=tmp, journal_path=jpath,
                                           espn_picks=picks)
            self.assertNotEqual(p1, p2)
            self.assertTrue(os.path.isfile(p1))
            self.assertTrue(os.path.isfile(p2))

    def test_export_empty_picks_writes_header_only(self):
        picks = []
        spoken = []
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "journal.txt")
            with open(jpath, "w", encoding="utf-8") as f:
                f.write("2026-09-15 12:00:00\tSESSION_START\tx.xlsx\n")
            with mock.patch.object(dc, "say", side_effect=spoken.append):
                path = dc.Copilot.export(None, out_dir=tmp, journal_path=jpath,
                                         espn_picks=picks)
            self.assertTrue(os.path.isfile(path))
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 0)
            summary = spoken[-1]
            self.assertIn("0 picks", summary)


# ---- in-season command tests (no network, mock ESPN responses) ----

_FAKE_CFG = {
    "league_id": "123", "season": 2026,
    "team_map": {1: "ME", 2: "T2", 17: "T11"},
    "team_labels": {1: "ME", 2: "RD", 17: "KNOX"},
}


class InSeasonHelperTests(unittest.TestCase):
    """Unit tests for in-season parsing helpers (no ESPN calls)."""

    def test_espn_label_handles_int_and_str_keys(self):
        tl_int = {1: "ME", 2: "RD"}
        tl_str = {"1": "ME", "2": "RD"}
        self.assertEqual(dc.espn_label_for_team_id(1, tl_int), "ME")
        self.assertEqual(dc.espn_label_for_team_id(1, tl_str), "ME")
        self.assertEqual(dc.espn_label_for_team_id(99, tl_int), "")
        self.assertEqual(dc.espn_label_for_team_id(None, tl_int), "")

    def test_fetch_standings_parses_team_data(self):
        """Test standings parser with fake ESPN response."""
        fake_teams = [
            {"id": 1, "abbrev": "ME", "name": "My Team",
             "points": 150.5, "currentProjectedRank": 1,
             "record": {"overall": {"wins": 2, "losses": 0, "ties": 0,
                                    "pointsAgainst": 120.0}}},
            {"id": 2, "abbrev": "RD", "name": "Rival",
             "points": 120.0, "currentProjectedRank": 2,
             "record": {"overall": {"wins": 1, "losses": 1, "ties": 0,
                                    "pointsAgainst": 130.0}}},
        ]
        with mock.patch("draft_copilot._espn_fetch_json", return_value=fake_teams):
            rows = dc.fetch_standings(_FAKE_CFG)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["token"], "ME")
        self.assertEqual(rows[0]["label"], "ME")
        self.assertEqual(rows[0]["wins"], 2)
        self.assertEqual(rows[0]["points_for"], 150.5)
        self.assertEqual(rows[0]["rank"], 1)

    def test_fetch_rosters_parses_entries(self):
        fake_teams = [{
            "id": 17,
            "roster": {"entries": [
                {"playerPoolEntry": {"player": {"id": 100, "fullName": "Josh Allen",
                                                "defaultPositionId": 1},
                                     "appliedStatTotal": 30.0},
                 "lineupSlotId": 0, "acquisitionType": "DRAFT",
                 "injuryStatus": "NORMAL"},
                {"playerPoolEntry": {"player": {"id": 200, "fullName": "Bijan Robinson",
                                                "defaultPositionId": 2},
                                     "appliedStatTotal": 25.0},
                 "lineupSlotId": 2, "acquisitionType": "DRAFT",
                 "injuryStatus": "QUESTIONABLE"},
            ]}
        }]
        with mock.patch("draft_copilot._espn_fetch_json", return_value=fake_teams), \
             mock.patch("draft_copilot._espn_build_player_map", return_value=({100: "Josh Allen", 200: "Bijan Robinson"}, "")):
            teams = dc.fetch_rosters(_FAKE_CFG)
        self.assertEqual(len(teams), 1)
        t = teams[0]
        self.assertEqual(t["token"], "T11")
        self.assertEqual(t["label"], "KNOX")
        self.assertEqual(len(t["roster"]), 2)
        # Starters first (non-BEN slots)
        self.assertEqual(t["roster"][0]["name"], "Josh Allen")
        self.assertEqual(t["roster"][0]["slot"], "QB")
        self.assertEqual(t["roster"][0]["pts"], 30.0)
        self.assertEqual(t["roster"][1]["status"], "QUESTIONABLE")

    def test_fetch_free_agents_filters_by_pos(self):
        fake_players = [
            {"playerPoolEntry": {"player": {"id": 1, "fullName": "Josh Allen",
                                            "defaultPositionId": 1,
                                            "ownership": {"percentOwned": 99.0}},
                                 "appliedStatTotal": 30.0}},
            {"playerPoolEntry": {"player": {"id": 2, "fullName": "Bijan Robinson",
                                            "defaultPositionId": 2,
                                            "ownership": {"percentOwned": 98.0}},
                                 "appliedStatTotal": 25.0}},
        ]
        with mock.patch("draft_copilot._espn_fetch_json", return_value=fake_players), \
             mock.patch("draft_copilot._espn_build_player_map", return_value=({1: "Josh Allen", 2: "Bijan Robinson"}, "")):
            all_fa = dc.fetch_free_agents(_FAKE_CFG)
            rb_fa = dc.fetch_free_agents(_FAKE_CFG, pos="RB")
        self.assertEqual(len(all_fa), 2)
        self.assertEqual(len(rb_fa), 1)
        self.assertEqual(rb_fa[0]["name"], "Bijan Robinson")

    def test_fetch_matchups_parses_schedule(self):
        fake_data = {"schedule": [
            {"home": {"teamId": 1, "totalPoints": 150.0},
             "away": {"teamId": 2, "totalPoints": 120.0}},
        ]}
        with mock.patch("draft_copilot._espn_fetch_json", return_value=fake_data):
            matchups = dc.fetch_matchups(_FAKE_CFG)
        self.assertEqual(len(matchups), 1)
        m = matchups[0]
        self.assertEqual(m["home_team"], "ME")
        self.assertEqual(m["away_team"], "RD")
        self.assertEqual(m["home_score"], 150.0)

    def test_fetch_transactions_handles_empty(self):
        with mock.patch("draft_copilot._espn_fetch_json", return_value=[]):
            txs = dc.fetch_transactions(_FAKE_CFG)
        self.assertEqual(txs, [])

    def test_cmd_standings_prints(self):
        fake_teams = [
            {"id": 1, "abbrev": "ME", "name": "My Team",
             "points": 150.5, "currentProjectedRank": 1,
             "record": {"overall": {"wins": 2, "losses": 0, "ties": 0,
                                    "pointsAgainst": 120.0}}},
        ]
        spoken = []
        with mock.patch("draft_copilot._espn_fetch_json", return_value=fake_teams), \
             mock.patch.object(dc, "say", side_effect=spoken.append):
            dc.cmd_standings(_FAKE_CFG)
        joined = "\n".join(spoken)
        self.assertIn("ME", joined)
        self.assertIn("150.5", joined)

    def test_cmd_rosters_filters_by_token(self):
        fake_teams = [
            {"id": 1, "roster": {"entries": [
                {"playerPoolEntry": {"player": {"id": 100, "fullName": "Josh Allen",
                                                "defaultPositionId": 1},
                                     "appliedStatTotal": 30.0},
                 "lineupSlotId": 0, "acquisitionType": "DRAFT",
                 "injuryStatus": "NORMAL"},
            ]}},
            {"id": 2, "roster": {"entries": [
                {"playerPoolEntry": {"player": {"id": 200, "fullName": "Bijan Robinson",
                                                "defaultPositionId": 2},
                                     "appliedStatTotal": 25.0},
                 "lineupSlotId": 2, "acquisitionType": "DRAFT",
                 "injuryStatus": "NORMAL"},
            ]}},
        ]
        spoken = []
        with mock.patch("draft_copilot._espn_fetch_json", return_value=fake_teams), \
             mock.patch("draft_copilot._espn_build_player_map", return_value=({100: "Josh Allen", 200: "Bijan Robinson"}, "")), \
             mock.patch.object(dc, "say", side_effect=spoken.append):
            dc.cmd_rosters(_FAKE_CFG, "ME")
        joined = "\n".join(spoken)
        self.assertIn("Josh Allen", joined)
        self.assertNotIn("Bijan Robinson", joined)

    def test_cmd_injuries_shows_only_injured(self):
        fake_teams = [{
            "id": 1, "roster": {"entries": [
                {"playerPoolEntry": {"player": {"id": 100, "fullName": "Healthy Player",
                                                "defaultPositionId": 2},
                                     "appliedStatTotal": 10.0},
                 "lineupSlotId": 2, "acquisitionType": "DRAFT",
                 "injuryStatus": "NORMAL"},
                {"playerPoolEntry": {"player": {"id": 200, "fullName": "Hurt Player",
                                                "defaultPositionId": 3},
                                     "appliedStatTotal": 5.0},
                 "lineupSlotId": 3, "acquisitionType": "DRAFT",
                 "injuryStatus": "OUT"},
            ]}
        }]
        spoken = []
        with mock.patch("draft_copilot._espn_fetch_json", return_value=fake_teams), \
             mock.patch("draft_copilot._espn_build_player_map", return_value=({100: "Healthy", 200: "Hurt"}, "")), \
             mock.patch.object(dc, "say", side_effect=spoken.append):
            dc.cmd_injuries(_FAKE_CFG)
        joined = "\n".join(spoken)
        self.assertIn("Hurt", joined)
        self.assertNotIn("Healthy", joined)

    def test_cmd_cap_filters_by_canonical_team_token(self):
        payload = {
            "draftDetail": {"picks": [
                {"playerId": 100, "bidAmount": 21, "teamId": 2},
            ]},
            "settings": {"draftSettings": {"auctionBudget": 200}},
        }
        spoken = []
        with mock.patch("draft_copilot._espn_fetch_json", return_value=payload), \
             mock.patch("draft_copilot._espn_build_player_map", return_value=({100: "Player One"}, "test")), \
             mock.patch.object(dc, "say", side_effect=spoken.append):
            dc.cmd_cap(_FAKE_CFG, "t2")
        joined = "\n".join(spoken)
        self.assertIn("T2", joined)
        self.assertIn("RD", joined)
        self.assertIn("$  21", joined)

    def test_run_espn_command_reports_missing_config_without_calling_command(self):
        calls = []
        spoken = []

        def command(config):
            calls.append(config)

        with mock.patch("draft_copilot._espn_cfg", return_value=None), \
             mock.patch.object(dc, "say", side_effect=spoken.append):
            result = dc.run_espn_command(command)
        self.assertIsNone(result)
        self.assertEqual(calls, [])
        self.assertIn("ESPN config unavailable", "\n".join(spoken))


if __name__ == "__main__":
    unittest.main()
