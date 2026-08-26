"""Dry-run self-test: drives draft_copilot core against a scratch workbook copy.
Run:  python draft_copilot.py --test <path-to-copy>
"""
import time

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    print("%s  %s%s" % (PASS if cond else FAIL, name,
                        ("  -> " + str(detail)) if (detail and not cond) else ""),
          flush=True)


def run(cp):
    t0 = time.time()

    # --- ESPN parser (no network) ------------------------------------------
    try:
        from espn_watch import load_fixture, parse_draft_state, map_team_token
        payload = load_fixture()
        nom, done, pmap = parse_draft_state(payload)
        check("espn fixture nominee = Ja'Marr Chase @ $12",
              nom and nom["name"] == "Ja'Marr Chase" and nom["bid"] == 12, nom)
        check("espn fixture completed Gibbs $34",
              len(done) == 1 and done[0]["name"] == "Jahmyr Gibbs"
              and done[0]["bid"] == 34, done)
        check("espn player_map has Gibbs id",
              pmap.get(4427366) == "Jahmyr Gibbs", pmap.get(4427366))
        check("espn team_map 1 -> ME",
              map_team_token(1, {1: "ME", 3: "T3"}) == "ME")
        # transition: clear inProgress -> sold, new nominee
        payload2 = load_fixture()
        for pick in payload2["draftDetail"]["picks"]:
            pick["inProgress"] = False
        payload2["draftDetail"]["picks"].append({
            "id": 3, "playerId": 2976499, "teamId": 5, "nominatingTeamId": 2,
            "bidAmount": 5, "inProgress": True,
        })
        nom2, done2, _ = parse_draft_state(payload2)
        check("espn transition new nominee CMC",
              nom2 and "McCaffrey" in nom2["name"], nom2)
        check("espn transition completed count = 2", len(done2) == 2, len(done2))
    except Exception as e:
        check("espn parser suite", False, str(e))

    board_n = len(cp.players)
    me = cp.teams.get("ME")
    t7 = cp.teams.get("T7")
    t14 = cp.teams.get("T14")
    check("board cache loaded (~300 players)", 250 <= board_n <= 350, board_n)
    check("teams mapped (ME + T2..T14 seats)",
          bool(me) and bool(t14) and "ME" in cp.teams and "T2" in cp.teams,
          cp.teams)

    # --- resolution -------------------------------------------------------
    p, cands = cp.resolve_player("gibbs")
    check("'gibbs' unique -> Jahmyr Gibbs",
          p and p["name"] == "Jahmyr Gibbs", [c["name"] for c in cands])
    p2, _ = cp.resolve_player("jacbos")           # typo drill
    check("typo 'jacbos' -> Josh Jacobs",
          bool(p2) and p2["name"] == "Josh Jacobs",
          p2["name"] if p2 else "no unique match")
    amb_name, amb = cp.resolve_player("brown")    # ambiguity drill
    check("'brown' ambiguous list >= 2", len(amb) >= 2,
          [c["name"] for c in amb])
    lj, _ = cp.resolve_player("jackson")
    check("'jackson' unique -> Lamar Jackson",
          lj and lj["name"] == "Lamar Jackson",
          lj["name"] if lj else "no match")
    p3, _ = cp.resolve_player("chase brown")
    check("'chase brown' -> Chase Brown",
          p3 and p3["name"] == "Chase Brown")
    jsn, _ = cp.resolve_player("jsn")
    check("nickname 'jsn' -> Jaxon Smith-Njigba",
          jsn and "Smith" in jsn["name"] and "Njigba" in jsn["name"],
          jsn["name"] if jsn else None)
    cd, _ = cp.resolve_player("cd")
    check("nickname 'cd' -> CeeDee Lamb",
          cd and "Lamb" in cd["name"], cd["name"] if cd else None)

    # --- parser -----------------------------------------------------------
    n1, w1, pr1, s1 = cp.parse_sale("gibbs t7 34")
    check("parse 'gibbs t7 34'",
          (n1, w1, pr1, s1) == ("gibbs", t7, 34, None),
          (n1, w1, pr1, s1))
    n2, w2, pr2, s2 = cp.parse_sale("34 me rb2 olave")   # shuffled order
    check("parse '34 me rb2 olave' (shuffled)",
          (n2, w2, pr2, s2) == ("olave", me, 34, "RB2"),
          (n2, w2, pr2, s2))
    n3, w3, pr3, s3 = cp.parse_sale("$12 d/st me denver")
    check("parse '$12 d/st me denver' (d/st slot alias)",
          (n3, w3, pr3, s3) == ("denver", me, 12, "D/ST"),
          (n3, w3, pr3, s3))

    # --- slot validation ---------------------------------------------------
    ok_r1, warn_r1 = cp.validate_slot("RB2", "RB")
    check("validate_slot RB2 for RB pos -> (True, '')",
          ok_r1 and warn_r1 == "", (ok_r1, warn_r1))
    ok_w, warn_w = cp.validate_slot("RB2", "WR")
    check("validate_slot RB2 for WR pos -> (False, warning)",
          not ok_w and "WR" in warn_w, (ok_w, warn_w))
    ok_q, warn_q = cp.validate_slot("QB", "QB")
    check("validate_slot QB for QB pos -> (True, '')",
          ok_q and warn_q == "", (ok_q, warn_q))
    ok_flex, warn_flex = cp.validate_slot("FLEX", "WR")
    check("validate_slot FLEX for WR pos -> (True, '')",
          ok_flex and warn_flex == "", (ok_flex, warn_flex))
    ok_flex2, warn_flex2 = cp.validate_slot("FLEX", "QB")
    check("validate_slot FLEX for QB pos -> (False, warning)",
          not ok_flex2 and "QB" in warn_flex2, (ok_flex2, warn_flex2))

    # --- write / heartbeat / dashboard cascade -----------------------------
    g = cp.hq_get
    row, ok, chk = cp.write_sale(p, me, 62, "RB1")
    check("sale 1 heartbeat OK (row %s)" % row, ok, chk)
    check("HQ spend = 62", g("B6") == 62, g("B6"))
    check("HQ budget left = 138", g("B7") == 138, g("B7"))
    check("HQ players won = 1", g("B8") == 1, g("B8"))
    check("My Roster RB1 filled", cp.arr(cp.roster, "B8")[0][0] ==
          "Jahmyr Gibbs", cp.arr(cp.roster, "B8")[0][0])
    cp.refresh_cache()
    owner = next(x for x in cp.players if x["name"] == "Jahmyr Gibbs")["owner"]
    check("board owner flips to ME seat", owner == me, owner)

    jt, _ = cp.resolve_player("jonathan taylor")
    row2, ok2, _ = cp.write_sale(jt, t7, 47, None)
    check("sale 2 (T7) heartbeat OK", ok2)
    spent_t7 = cp.arr(cp.tracker, "C11")[0][0]  # T7 seat is tracker row 11
    check("Team Tracker C11 spent = 47", spent_t7 == 47, spent_t7)

    # --- duplicate guard data ----------------------------------------------
    dup, _ = cp.resolve_player("gibbs")
    check("duplicate sale would be caught (owner != Available)",
          dup["owner"] != "Available", dup["owner"])

    # --- undo ----------------------------------------------------------------
    cp.undo()
    check("undo restores B15=1", g("B15") == 1, g("B15"))
    check("undo clears log B6 (JT row)",
          cp.arr(cp.log, "B6")[0][0] in (None, ""),
          repr(cp.arr(cp.log, "B6")[0][0]))

    # --- who / nom / status smoke --------------------------------------------
    cb, _ = cp.resolve_player("chase brown")
    cp.who(cb)
    check("who sets HQ nomination E5", g("E5") == "Chase Brown", g("E5"))
    check("who target panel G7 = 36", g("G7") == 36, g("G7"))
    cp.nom()
    cp.status()
    cp.recent()

    # --- timed 10-sale pressure drill ----------------------------------------
    seq = [
        ("ja'marr chase", "T4", 61, None),
        ("bijan robinson", "T9", 58, None),
        ("jsn", None, None, None),
        ("puka nacua", "T3", 55, None),
        ("st brown", "ME", 50, "WR1"),
        ("christian mccaffrey", "T11", 49, None),
        ("ceeDee lamb", "T13", 39, None),
        ("drake london", "T6", 38, None),
        ("justin jefferson", "T10", 40, None),
        ("achane", "ME", 41, "RB2"),
    ]
    t1 = time.time()
    done = 0
    for nm, win, price, slot in seq:
        pp, cc = cp.resolve_player(nm)
        if pp is None:
            print("SKIP (unresolved): %s -> %s" %
                  (nm, [c["name"] for c in cc])); continue
        wd = cp.teams[win.upper()] if win else cp.teams["T5"]
        r_, okk, _ = cp.write_sale(pp, wd, price or 30, slot)
        done += 1 if okk else 0
        if not okk:
            print("HEARTBEAT FAIL at %s" % nm)
    dt = time.time() - t1
    check("drill 9-10 sales logged, all heartbeats OK", done >= 9, done)
    check("drill under 25s (%.1fs)" % dt, dt < 25, "%.1fs" % dt)
    check("nominations completed = 1 + %d" % done, g("B15") == 1 + done,
          g("B15"))
    cp.status(tag="POST-DRILL >> ")

    print("\n%d/%d checks passed  (%.1fs total)" %
          (sum(1 for _, c in results if c), len(results), time.time() - t0))
    return 0 if all(c for _, c in results) else 1


def p_amb_ok(amb):
    return len(amb) >= 2  # kept for reference; superseded by direct len check
