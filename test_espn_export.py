"""Offline export acceptance tests with real snapshot generation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from espn_client import EspnClient, EspnError
from test_espn_client import CFG, Network, payload
import espn_export as ee

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def client(self, net):
        return EspnClient(CFG, opener=net, cache_path=str(self.root / "cache.json"))
    def test_export_refresh_once_and_sidecar_matches_exact_source(self):
        net = Network()
        def renderer(source, target, **kwargs):
            data = json.loads(Path(source).read_text())
            self.assertEqual(data["draft_picks"][2]["player_id"], 999)
            self.assertIsNone(data["draft_picks"][2]["player_name"])
            Path(target).write_bytes(b"rendered")
        result = ee.export_current_league(CFG, self.root, client=self.client(net), renderer=renderer)
        self.assertEqual(len(net.catalog_calls), 1)
        self.assertEqual(result["unresolved_player_ids"], [999])
        self.assertEqual(json.loads(Path(result["raw_path"]).read_text()), net.league)
        self.assertTrue(Path(result["workbook_path"]).is_file())
        combined = "".join(p.read_text(errors="replace") for p in
                           Path(result["workbook_path"]).parent.glob("*.json"))
        self.assertNotIn("test-s2", combined)
    def test_required_view_or_renderer_failure_publishes_no_export(self):
        broken = payload()
        del broken["status"]
        with self.assertRaises(EspnError):
            ee.export_current_league(CFG, self.root, client=self.client(Network(league=broken)))
        self.assertEqual(list(self.root.iterdir()), [])
        def fail(*args, **kwargs):
            raise RuntimeError("renderer failed")
        with self.assertRaises(RuntimeError):
            ee.export_current_league(CFG, self.root, client=self.client(Network()), renderer=fail)
        self.assertEqual(list(self.root.iterdir()), [])
    def test_repeated_exports_never_overwrite(self):
        def renderer(source, target, **kwargs):
            Path(target).write_bytes(b"rendered")
        a = ee.export_current_league(CFG, self.root, client=self.client(Network()), renderer=renderer)
        b = ee.export_current_league(CFG, self.root, client=self.client(Network()), renderer=renderer)
        self.assertNotEqual(a["workbook_path"], b["workbook_path"])
        self.assertTrue(Path(a["workbook_path"]).is_file())
    def test_real_workbook_preserves_unknowns_defenses_and_literal_formula_text(self):
        # This integration test requires the bundled Node/artifact runtime.
        if not ee.renderer_available():
            self.skipTest("Node/artifact runtime not installed; unit export contracts still run")
        net = Network()
        net.league["teams"][0]["name"] = "=1+1"
        result = ee.export_current_league(CFG, self.root, client=self.client(net))
        import zipfile
        from xml.etree import ElementTree as ET
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(result["workbook_path"]) as book:
            wb = ET.fromstring(book.read("xl/workbook.xml"))
            names = [s.attrib["name"] for s in wb.find("m:sheets", ns)]
            self.assertIn("Pending Transactions", names)
            self.assertIn("Draft Picks", names)
            xml = b"".join(book.read(n) for n in book.namelist() if n.startswith("xl/worksheets/"))
            strings = book.read("xl/sharedStrings.xml")
            self.assertIn(b"unresolved", xml + strings)
            self.assertIn(b"-16001", xml)
            self.assertNotIn(b"player#999", xml + strings)
            for name in book.namelist():
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    formulas = ET.fromstring(book.read(name)).findall(".//m:f", ns)
                    self.assertNotIn("1+1", [f.text for f in formulas])
