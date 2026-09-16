"""Export a fresh ESPN snapshot to a new multi-tab workbook and exact raw JSON."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from espn_client import EspnClient, EspnError, load_config
from league_snapshot import LeagueSnapshot

SCRIPT_DIR = Path(__file__).resolve().parent

def renderer_paths():
    bundle = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
    default_node = bundle / ("bin/node.exe" if os.name == "nt" else "bin/node")
    node = os.environ.get("DRAFT_COPILOT_NODE") or (
        str(default_node) if default_node.is_file() else shutil.which("node"))
    modules = Path(os.environ.get("DRAFT_COPILOT_ARTIFACT_MODULES",
                                  str(bundle / "node_modules")))
    return node, modules

def renderer_available():
    node, modules = renderer_paths()
    return bool(node and (modules / "@oai/artifact-tool/package.json").is_file())

def write_current_league_workbook(source, target, previews=False):
    node, modules = renderer_paths()
    if not renderer_available():
        raise RuntimeError("Workbook renderer unavailable. Configure DRAFT_COPILOT_NODE and "
                           "DRAFT_COPILOT_ARTIFACT_MODULES or use --json-only.")
    env = os.environ.copy()
    env["DRAFT_COPILOT_ARTIFACT_MODULES"] = str(modules)
    # The renderer needs normalized data only, never the authentication environment.
    env.pop("DRAFT_COPILOT_ESPN_S2", None)
    env.pop("DRAFT_COPILOT_SWID", None)
    args = [node, str(SCRIPT_DIR / "espn_workbook.mjs"), str(source), str(target)]
    if previews:
        args.append("--previews")
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Workbook renderer failed: " + result.stderr[-1500:])
    if not Path(target).is_file():
        raise RuntimeError("Workbook renderer produced no workbook")

def export_current_league(cfg, out_dir, client=None, renderer=None, json_only=False, previews=False):
    if renderer is None and not json_only and not renderer_available():
        raise RuntimeError("Workbook renderer unavailable; configure runtime or use --json-only.")
    client = client or EspnClient(cfg)
    snapshot = LeagueSnapshot.from_client(client, force_refresh_players=True)
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Publish the directory only after both data files and the workbook succeed.
    with tempfile.TemporaryDirectory(prefix=".espn-building-", dir=str(out_dir)) as tmp:
        staging = Path(tmp) / "snapshot"
        staging.mkdir()
        raw_path = staging / "league_snapshot_raw.json"
        raw_bytes = json.dumps(snapshot.raw_snapshot, ensure_ascii=False, indent=2).encode("utf-8")
        raw_path.write_bytes(raw_bytes)
        data = snapshot.to_dict()
        data["metadata"]["raw_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        data["metadata"]["raw_filename"] = raw_path.name
        source = staging / "league_snapshot.json"
        source.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        workbook = staging / "ESPN_current_league_export.xlsx"
        if not json_only:
            (renderer or write_current_league_workbook)(source, workbook, previews=previews)
            if not workbook.is_file():
                raise RuntimeError("Export renderer produced no workbook")
        name = "espn_current_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        destination = out_dir / name
        staging.rename(destination)
    return {
        "workbook_path": str(destination / workbook.name) if not json_only else None,
        "raw_path": str(destination / raw_path.name),
        "snapshot_path": str(destination / source.name),
        "unresolved_player_ids": snapshot.unresolved_player_ids,
        "coverage": snapshot.coverage,
        "player_refresh_error": snapshot.metadata["player_refresh_error"],
        "counts": {"teams": len(snapshot.teams), "rosters": len(snapshot.rosters),
                   "draft_picks": len(snapshot.draft_picks), "schedule": len(snapshot.schedule),
                   "transactions": None if snapshot.transactions is None else len(snapshot.transactions)},
    }

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--out-dir", default=str(SCRIPT_DIR / "exports"))
    parser.add_argument("--json-only", action="store_true", help="Export the same refreshed snapshot without XLSX")
    parser.add_argument("--previews", action="store_true", help="Render a preview of each workbook tab")
    args = parser.parse_args(argv)
    try:
        result = export_current_league(load_config(args.config), args.out_dir,
                                       json_only=args.json_only, previews=args.previews)
    except Exception as exc:
        print("Export failed: %s" % exc)
        return 1
    print(json.dumps(result, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

