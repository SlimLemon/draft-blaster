"""Probe ESPN mDraftDetail for your league and dump a fixture.

Usage:
  python espn_probe.py
  python espn_probe.py --refresh-players   # force rebuild id→name cache

Requires espn_config.json. Writes fixtures/mDraftDetail_live.json.
"""
import json
import os
import sys

from espn_watch import probe_to_file
from espn_client import CONFIG_PATH, load_config, ConfigError, EspnClient

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "mDraftDetail_live.json")


def main():
    refresh = "--refresh-players" in sys.argv
    try:
        cfg = load_config(CONFIG_PATH)
    except ConfigError as e:
        print("ERROR:", e)
        print("Copy espn_config.example.json -> espn_config.json and fill values.")
        return 2
    try:
        client = EspnClient(cfg)
        summary = probe_to_file(cfg, OUT, refresh_players=refresh, client=client)
    except Exception as e:
        print("ERROR fetching ESPN:", e)
        return 1
    print("Wrote", summary["out_path"])
    print("URL:", summary["url"])
    print("player_map size: %s (source=%s)" % (
        summary["player_map_n"], summary.get("player_map_source")))
    if summary["player_map_n"] < 500:
        print("WARNING: player map thin — run: python espn_probe.py --refresh-players")
        rc_warn = 1
    else:
        print("player map OK for draft night")
        rc_warn = 0
    print("draft: live=%s drafted=%s | completed picks: %s" % (
        summary.get("draft_live"), summary.get("drafted"),
        summary["completed_n"]))
    nom = summary["nominee"]
    if nom:
        print("ON BLOCK:", nom["name"], "| bid $%s | nomTeam %s | winTeam %s" % (
            nom["bid"], nom["nominating_team_id"], nom["team_id"]))
    else:
        print("ON BLOCK: (none — lobby OK; watch will engage when auction starts)")
    with open(OUT, "r", encoding="utf-8") as f:
        payload = json.load(f)
    dd = payload.get("draftDetail") or {}
    print("draftDetail: drafted=%s inProgress=%s picks=%d" % (
        dd.get("drafted"), dd.get("inProgress"), len(dd.get("picks") or [])))
    picks = dd.get("picks") or []
    if picks:
        real = [p for p in picks if (p.get("playerId") or 0) > 0]
        print("picks with real playerId:", len(real))

    # smoke: known star should resolve from map
    pmap, _ = client.build_player_map()
    sample_ids = {
        4429795: "Ashton Jeanty",  # often #1 auction in 2026 kits; may vary
    }
    # prefer Gibbs if present
    for pid, hint in list(pmap.items())[:1]:
        pass
    gibbs = [pid for pid, nm in pmap.items() if "Gibbs" in nm and "Jahmyr" in nm]
    if gibbs:
        print("smoke name lookup: id %s -> %s" % (gibbs[0], pmap[gibbs[0]]))
    else:
        # any RB-ish name check
        any_name = next(iter(pmap.values())) if pmap else None
        print("smoke name sample:", any_name)

    try:
        tdata = client.league_views(("mTeam",))
        print("--- ESPN teams (align team_map / Team Tracker) ---")
        for t in sorted(tdata.get("teams") or [], key=lambda x: x.get("id", 0)):
            label = t.get("abbrev") or t.get("nickname") or "?"
            mapped = client.team_resolver().resolve(t["id"])["token"] or "?"
            mark = " <-- YOU" if mapped == "ME" else ""
            print("  espnId=%s  %-6s  -> %s%s" % (t.get("id"), label, mapped, mark))
    except Exception as e:
        print("(team list fetch skipped:", e, ")")

    print("--- lobby smoke ---")
    print("  1) Config + cookies: OK")
    print("  2) Player map: %s" % ("OK" if summary["player_map_n"] >= 500 else "THIN"))
    print("  3) When draft goes live: co-pilot -> watch -> block")
    return rc_warn


if __name__ == "__main__":
    sys.exit(main())
