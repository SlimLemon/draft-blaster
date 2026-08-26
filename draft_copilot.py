"""
DRAFT CO-PILOT (LibreOffice edition) - drives the Fantasy Football 2026 Draft
Command Center workbook live via UNO. Runs under LibreOffice's bundled python:
    "C:\\Program Files\\LibreOffice\\program\\python.exe" draft_copilot.py

Design rule: this script NEVER computes strategy. It writes sale rows, reads the
workbook's own outputs, and relays them. All intelligence stays in the workbook.

Commands:
  <name> [winner] [price] [slot]   log a sale (any token order): gibbs t7 34
  who <name>                       pre-bid briefing + loads HQ nomination panel
  watch / watch off                ESPN live on-block sync (needs espn_config.json)
  block                            force-refresh current ESPN nominee into HQ
  !                                log the last ESPN-announced sale
  last                             reprint last ESPN SOLD + suggested log line
  nom                              nomination helper (relays sheet's action+drains)
  status                           budget / needs / phase / threat snapshot
  log                              show last 5 logged sales
  undo                             clear the last sale row
  help
"""
import os
import re
import sys
import time
import math
import difflib
import threading
try:
    import queue
except ImportError:
    import Queue as queue  # noqa: N813

SOFFICE = r"C:\Program Files\LibreOffice\program\soffice.exe"
PORT = 2002
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# EXACT draft-day workbook filename (no fuzzy matching - see RUNBOOK)
DRAFT_WORKBOOK_NAME = "Draft_Command_Center_DRAFT_DAY.xlsx"
DRAFT_WORKBOOK_PATH = os.path.join(SCRIPT_DIR, DRAFT_WORKBOOK_NAME)
# --test refuses the live book and anything resembling it
TEST_REFUSALS = (
    DRAFT_WORKBOOK_NAME,
    "fantasy_football_2026_draft_command_center_final_live_draft 1.xlsx",
    "fantasy_football_2026_draft_command_center.xlsx",
)
# dedicated LibreOffice profile: our config changes (recalc mode) stay scoped
# here AND our instance never collides with the user's own LibreOffice
PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "DraftCopilotLOProfile")
AUTOSAVE_EVERY = 10          # sales between automatic doc.store() calls
JOURNAL_PATH = os.path.join(SCRIPT_DIR, "journal.txt")

WINNER_TOKEN_RE = re.compile(r"^t(\d{1,2})$", re.I)
PRICE_TOKEN_RE = re.compile(r"^\$?(\d{1,3})$")
SLOT_ALIASES = {
    "qb": "QB", "rb": None, "rb1": "RB1", "rb2": "RB2",
    "wr": None, "wr1": "WR1", "wr2": "WR2", "te": "TE",
    "flex": "FLEX", "dst": "D/ST", "d/st": "D/ST", "k": "K",
    "be": None, "be1": "BE1", "be2": "BE2", "be3": "BE3",
    "be4": "BE4", "be5": "BE5", "be6": "BE6",
}
SLOT_ORDER = {
    "QB": ["QB"], "RB": ["RB1", "RB2", "FLEX"], "WR": ["WR1", "WR2", "FLEX"],
    "TE": ["TE", "FLEX"], "D/ST": ["D/ST"], "K": ["K"],
}
# Every slot name that is valid for at least one position
ALL_VALID_SLOTS = set()
for _pos_slots in SLOT_ORDER.values():
    ALL_VALID_SLOTS.update(_pos_slots)
ALL_VALID_SLOTS.update(["BE1", "BE2", "BE3", "BE4", "BE5", "BE6"])
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
# fast nicknames -> normalized full names (must match norm() output)
NICKNAMES = {
    "jsn": "jaxon smithnjigba",
    "jaxon": "jaxon smithnjigba",
    "cmc": "christian mccaffrey",
    "mccaffrey": "christian mccaffrey",
    "arsb": "amonra st brown",
    "amon ra": "amonra st brown",
    "amonra": "amonra st brown",
    "st brown": "amonra st brown",
    "cd": "ceedee lamb",
    "ceedee": "ceedee lamb",
    "lamb": "ceedee lamb",
    "jj": "justin jefferson",
    "jefferson": "justin jefferson",
    "bijan": "bijan robinson",
    "puka": "puka nacua",
    "nacua": "puka nacua",
    "breece": "breece hall",
    "saquon": "saquon barkley",
    "barkley": "saquon barkley",
    "mahomes": "patrick mahomes",
    "hurts": "jalen hurts",
    "lamar": "lamar jackson",
    "achane": "devon achane",
    "nabers": "malik nabers",
    "btj": "brian thomas",
    "nico": "nico collins",
    "kelce": "travis kelce",
    "bowers": "brock bowers",
    "hock": "tj hockenson",
    "hockenson": "tj hockenson",
    "tyreek": "tyreek hill",
    "cheetah": "tyreek hill",
    "hill": "tyreek hill",
    "henry": "derrick henry",
    "king henry": "derrick henry",
    "kamara": "alvin kamara",
    "jacobs": "josh jacobs",
    "jt": "jonathan taylor",
    "taylor": "jonathan taylor",
    "gibbs": "jahmyr gibbs",
    "olave": "chris olave",
    "waddle": "jaylen waddle",
    "rice": "rashee rice",
    "flowers": "zay flowers",
    "ajb": "aj brown",
    "aj brown": "aj brown",
    "dk": "dk metcalf",
    "metcalf": "dk metcalf",
    "jamarr": "jamarr chase",
    "jamar": "jamarr chase",
    "chase": "jamarr chase",
    "kyren": "kyren williams",
    "monty": "david montgomery",
    "montgomery": "david montgomery",
    "baker": "baker mayfield",
    "dak": "dak prescott",
    "diggs": "stefon diggs",
    "adams": "davante adams",
    "evans": "mike evans",
    "godwin": "chris godwin",
    "london": "drake london",
    "mhj": "marvin harrison",
    "odunze": "rome odunze",
    "brooks": "jonathan brooks",
    "conner": "james conner",
    "pollard": "tony pollard",
    "kw3": "kenneth walker",
    "walker": "kenneth walker",
    "purdy": "brock purdy",
    "stroud": "cj stroud",
    "nix": "bo nix",
    "daniels": "jayden daniels",
    "caleb": "caleb williams",
    "maye": "drake maye",
    "mcbride": "trey mcbride",
    "kittle": "george kittle",
    "laporta": "sam laporta",
    "kraft": "tucker kraft",
    "njoku": "david njoku",
    "engram": "evan engram",
}
LOG_FIRST, LOG_LAST = 5, 304
# HQ cells that must be numeric after load (schema assert)
HQ_NUMERIC_CELLS = ("B7", "B10", "B15")  # budget left, max bid, noms done


def norm(s):
    s = re.sub(r"[.,'\u2019\-]", "", str(s)).lower()
    toks = [t for t in s.split() if t not in SUFFIXES]
    return " ".join(toks)


def say(msg):
    print(msg, flush=True)


def num(v):
    """pretty-print workbook numbers: 62.0 -> 62"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def journal(event, detail=""):
    """Append a line to journal.txt (survives Calc/script crash if unsaved)."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = "%s\t%s\t%s\n" % (ts, event, detail)
    try:
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        say("(warn) journal write failed: %s" % e)


def assert_safe_test_path(path):
    """Refuse --test against the live draft workbook or known live filenames."""
    abs_path = os.path.abspath(path)
    base = os.path.basename(abs_path)
    refusals = {n.lower() for n in TEST_REFUSALS}
    live = os.path.abspath(DRAFT_WORKBOOK_PATH)
    if base.lower() in refusals or os.path.normcase(abs_path) == os.path.normcase(live):
        say("ERROR: --test refuses live workbook '%s'." % base)
        say("        Copy the .xlsx first, then: draft_copilot.py --test <copy.xlsx>")
        sys.exit(2)
    if not os.path.isfile(abs_path):
        say("ERROR: test workbook not found: %s" % abs_path)
        sys.exit(2)


# ------------------------------------------------------------------ uno layer
def _import_uno():
    lo_prog = os.path.dirname(SOFFICE)
    if lo_prog not in sys.path:
        sys.path.append(lo_prog)
    import uno
    return uno


UNO = None  # populated lazily


def connect(timeout_s):
    uno = UNO
    localContext = uno.getComponentContext()
    resolver = localContext.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", localContext)
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            return resolver.resolve(
                "uno:socket,host=127.0.0.1,port=%d;urp;"
                "StarOffice.ComponentContext" % PORT)
        except Exception as e:
            last = e
            time.sleep(0.4)
    raise last


def soffice_process_running():
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq soffice.bin"],
            capture_output=True, text=True, timeout=10).stdout
        return "soffice.bin" in out.lower()
    except Exception:
        return False


def _port_in_use(port):
    """Check if a port is already listening (avoids spawning a duplicate)."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _recover_orphaned_lock():
    """Rename an orphaned LibreOffice profile lock when no process holds it.

    LibreOffice creates PROFILE_DIR/.lock while running.  If the process
    crashes or is killed, the lock remains and blocks the next launch with
    a generic 45-second timeout.  This detects that situation and recovers.
    """
    lock_path = os.path.join(PROFILE_DIR, ".lock")
    if not os.path.isfile(lock_path):
        return False
    # Only refuse recovery when OUR listener is up. An unrelated soffice
    # window must not block clearing an orphaned Draft Copilot profile lock.
    if _port_in_use(PORT):
        return False
    # Unique suffix so repeated recoveries never collide with .lock.stale
    stale = "%s.stale.%d" % (lock_path, int(time.time() * 1000))
    try:
        os.rename(lock_path, stale)
        say("(recovered orphaned profile lock -> %s)" % os.path.basename(stale))
        return True
    except OSError as e:
        say("(warn) could not rename orphaned lock: %s" % e)
        return False


def ensure_office_ctx(spawn_visible):
    uno = UNO
    try:
        return connect(2.0)          # reuse our own running instance if alive
    except Exception:
        pass
    # No listener on port 2002 — either nothing is running, or an orphaned
    # lock is blocking the new instance from starting.
    _recover_orphaned_lock()
    os.makedirs(PROFILE_DIR, exist_ok=True)
    profile_url = uno.systemPathToFileUrl(PROFILE_DIR)
    args = ["--norestore", "--nologo",
            "-env:UserInstallation=" + profile_url,
            "--accept=socket,host=127.0.0.1,port=%d;urp;"
            "StarOffice.ServiceManager" % PORT]
    if not spawn_visible:
        args.append("--headless")
    import subprocess
    subprocess.Popen([SOFFICE] + args,
                     creationflags=subprocess.CREATE_NO_WINDOW
                     if not spawn_visible else 0)
    return connect(45)


def apply_recalc_config(smgr, ctx):
    """OOXMLRecalcMode=0 -> always recalc on load, no prompt ever."""
    from com.sun.star.beans import PropertyValue

    def pv(n, v):
        p = PropertyValue(); p.Name = n; p.Value = v; return p
    try:
        cp = smgr.createInstanceWithContext(
            "com.sun.star.configuration.ConfigurationProvider", ctx)
        node = cp.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationUpdateAccess",
            (pv("nodepath", "/org.openoffice.Office.Calc/Formula/Load"),))
        node.OOXMLRecalcMode = 0
        node.commitChanges()
    except Exception as e:
        say("(warn) could not pin recalc-on-load setting: %s" % e)


class Copilot:
    def __init__(self, ctx, doc, spawned, testing=False):
        global UNO
        uno = UNO
        self.ctx = ctx
        self.smgr = ctx.ServiceManager
        self.doc = doc
        self.spawned = spawned
        self.testing = testing
        self.sales_since_save = 0
        need = {"Draft HQ": "hq", "Player Board": "board",
                "Auction Log": "log", "Team Tracker": "tracker",
                "My Roster": "roster"}
        have = set()
        for i in range(doc.Sheets.Count):
            have.add(doc.Sheets.getByIndex(i).Name)
        missing = set(need) - have
        if missing:
            say("ERROR: workbook is missing sheets: %s" % sorted(missing))
            sys.exit(1)
        for name, attr in need.items():
            setattr(self, attr, doc.Sheets.getByName(name))
        doc.calculateAll()
        self.refresh_cache()
        self.assert_schema()

    def assert_schema(self):
        """Fail loud if HQ/log contract cells are missing or wrong type."""
        errors = []
        for addr in HQ_NUMERIC_CELLS:
            v = self.hq_get(addr)
            if not isinstance(v, (int, float)):
                errors.append("Draft HQ!%s expected number, got %r" % (addr, v))
        try:
            # J = column index 9; LOG_FIRST row -> 0-based row index LOG_FIRST-1
            jcell = self.log.getCellByPosition(9, LOG_FIRST - 1)
            formula = (jcell.Formula or "").strip()
            if not formula:
                errors.append(
                    "Auction Log!J%d has no formula (heartbeat column missing?)"
                    % LOG_FIRST)
        except Exception as e:
            errors.append("Auction Log!J%d unreadable: %s" % (LOG_FIRST, e))
        if not (250 <= len(self.players) <= 350):
            errors.append("Player Board cache size %d not in 250-350"
                          % len(self.players))
        if not self.teams.get("ME") or not self.teams.get("T14"):
            errors.append("Team Tracker missing ME/T14 mapping: %s" % self.teams)
        if errors:
            say("ERROR: workbook schema check failed:")
            for e in errors:
                say("  - %s" % e)
            sys.exit(1)
        say("schema OK: HQ B7/B10/B15 numeric | log J%d formula | "
            "%d players | 14 teams" % (LOG_FIRST, len(self.players)))

    def autosave(self, reason=""):
        """Persist workbook to disk via UNO store()."""
        if self.testing:
            return
        try:
            self.doc.store()
            self.sales_since_save = 0
            say("(autosaved%s)" % ((" " + reason) if reason else ""))
            journal("AUTOSAVE", reason or "ok")
        except Exception as e:
            say("!! AUTOSAVE FAILED: %s — hit Ctrl+S in Calc now" % e)
            journal("AUTOSAVE_FAIL", str(e))

    def maybe_autosave(self):
        if self.testing:
            return
        self.sales_since_save += 1
        if self.sales_since_save >= AUTOSAVE_EVERY:
            self.autosave("every %d sales" % AUTOSAVE_EVERY)

    def journal_sale(self, detail):
        if not self.testing:
            journal("SALE", detail)

    def journal_undo(self, detail):
        if not self.testing:
            journal("UNDO", detail)

    # ------------------------------------------------------------ primitives
    def arr(self, sheet, rng):
        return sheet.getCellRangeByName(rng).getDataArray()

    def hq_get(self, addr):
        return norm_cell(self.arr(self.hq, addr)[0][0])

    def set_str(self, sheet, addr, s):
        sheet.getCellRangeByName(addr).setString(str(s))

    def set_num(self, sheet, addr, v):
        sheet.getCellRangeByName(addr).setValue(float(v))

    def clear_cell(self, sheet, addr):
        sheet.getCellRangeByName(addr).clearContents(1 | 2 | 4 | 16)

    def recalc(self):
        self.doc.calculateAll()

    # ---- cached board/team data (read-cache only; refreshed after mutations)
    def refresh_cache(self):
        raw = self.arr(self.board, "A5:AA%d" % LOG_LAST)
        self.players = []
        for i, row in enumerate(raw):
            name = row[0]
            if name in (None, ""):
                continue
            self.players.append({
                "name": str(name),
                "pos": row[6],
                "market": norm_cell(row[13]),
                "ceiling": norm_cell(row[17]),
                "target": norm_cell(row[18]),
                "tag": norm_cell(row[19]),
                "injury": norm_cell(row[20]),
                "owner": str(row[3]) if row[3] not in (None, "") else "Available",
                "board_row": LOG_FIRST + i,
                "_norm": norm(name),
                "_toks": set(norm(name).split()),
            })
        tnames = self.arr(self.tracker, "A5:A18")
        self.teams = {}
        self.team_names = []
        for idx, row in enumerate(tnames):
            disp = str(row[0]).strip()
            self.team_names.append(disp)
            key = "ME" if idx == 0 else "T%d" % (idx + 1)
            self.teams[key] = disp

    # ------------------------------------------------------------- resolution
    def resolve_player(self, frag):
        q = norm(frag)
        q = NICKNAMES.get(q, q)
        if not q:
            return None, []
        exact = [p for p in self.players if p["_norm"] == q]
        if len(exact) == 1:
            return exact[0], []
        if exact:
            return None, exact
        qtoks = set(q.split())
        contains = [p for p in self.players if qtoks <= p["_toks"]]
        if len(contains) == 1:
            return contains[0], []
        if contains:
            contains.sort(key=lambda p: -len(p["_toks"]))
            return None, contains[:6]
        near = difflib.get_close_matches(q, [p["_norm"] for p in self.players],
                                         n=6, cutoff=0.6)
        hits = [p for p in self.players if p["_norm"] in set(near)]
        if not hits:
            scored = []
            for p in self.players:
                best = max(
                    (difflib.SequenceMatcher(None, t, nt).ratio()
                     for t in q.split() for nt in p["_toks"]),
                    default=0)
                if best >= 0.8:
                    scored.append((best, p))
            scored.sort(key=lambda x: -x[0])
            hits = [p for _s, p in scored[:6]]
        if len(hits) == 1:
            return hits[0], []
        return None, hits[:6]

    def resolve_winner(self, tok):
        t = tok.strip().upper()
        if t in self.teams:
            return self.teams[t], None
        frag = norm(tok)
        hits = [n for n in self.team_names if frag in norm(n)]
        if len(hits) == 1:
            return hits[0], None
        return None, hits

    def parse_sale(self, line):
        """Order-independent: name fragment(s), winner token, price, slot."""
        toks = [t for t in re.split(r"[,\s]+", line.strip()) if t]
        price = winner = slot = None
        name_toks = []
        for t in toks:
            m = PRICE_TOKEN_RE.match(t)
            if m and price is None:
                price = int(m.group(1))
                continue
            tl = t.lower()
            if tl in SLOT_ALIASES:
                sl = SLOT_ALIASES[tl]
                if sl is not None:
                    slot = sl
                continue  # bare rb/wr/be -> let auto-suggestion decide
            if WINNER_TOKEN_RE.match(t) or tl == "me":
                w, _ = self.resolve_winner(t)
                if w:
                    winner = w
                    continue
            name_toks.append(t)
        # A Team Tracker name fragment (for example "PA") is also a valid
        # winner token, but only consume it when removing it leaves a unique
        # player match. This prevents a team fragment from stealing a player
        # surname such as "Brown".
        if winner is None and len(name_toks) >= 2:
            for i, token in enumerate(name_toks):
                w, _hits = self.resolve_winner(token)
                if not w:
                    continue
                remaining = name_toks[:i] + name_toks[i + 1:]
                if not remaining:
                    continue
                player, candidates = self.resolve_player(" ".join(remaining))
                if player is not None or len(candidates) == 1:
                    winner = w
                    name_toks = remaining
                    break
        return " ".join(name_toks), winner, price, slot

    def suggest_slot(self, pos):
        fills = [r[0] for r in self.arr(self.roster, "B7:B21")]
        slots = [r[0] for r in self.arr(self.roster, "A7:A21")]
        used = {s for s, f in zip(slots, fills) if f not in (None, "")}
        for cand in SLOT_ORDER.get(pos, []) + ["BE1", "BE2", "BE3",
                                               "BE4", "BE5", "BE6"]:
            if cand not in used:
                return cand
        return None

    def validate_slot(self, slot, pos):
        """Return True if *slot* is a legal roster slot for position *pos*.

        Checks:
        1. Slot must be a known roster slot (QB, RB1, WR2, FLEX, BE3, etc.)
        2. Slot must be eligible for the player's position
        3. Slot must not already be occupied on My Roster
        """
        if slot not in ALL_VALID_SLOTS:
            return False, "unknown slot '%s'" % slot
        eligible = SLOT_ORDER.get(pos, []) + ["BE1", "BE2", "BE3",
                                              "BE4", "BE5", "BE6"]
        if slot not in eligible:
            return False, "%s is not eligible for %s (valid: %s)" % (
                slot, pos, ", ".join(eligible))
        fills = [r[0] for r in self.arr(self.roster, "B7:B21")]
        slots = [r[0] for r in self.arr(self.roster, "A7:A21")]
        for s, f in zip(slots, fills):
            if s == slot and f not in (None, ""):
                return False, "slot %s already occupied by %s" % (slot, f)
        return True, ""

    # ---------------------------------------------------------------- actions
    def next_entry_row(self):
        v = self.hq_get("B34")
        try:
            r = int(v)
            bcol = self.arr(self.log, "B%d:B%d" % (r, r))[0][0]
            if LOG_FIRST <= r <= LOG_LAST and bcol in (None, ""):
                return r
        except (TypeError, ValueError):
            pass
        col = self.arr(self.log, "B5:B%d" % LOG_LAST)
        for i, row in enumerate(col):
            if row[0] in (None, ""):
                return LOG_FIRST + i
        raise RuntimeError("Auction Log is full")

    def write_sale(self, p, winner_disp, price, slot):
        row = self.next_entry_row()
        pre = self.hq_get("B15")
        columns = ("B", "D", "E", "F", "I")
        snapshot = {
            col: self.arr(self.log, "%s%d" % (col, row))[0][0]
            for col in columns
        }

        def rollback(reason):
            restore_errors = []
            for col in columns:
                addr = "%s%d" % (col, row)
                value = snapshot[col]
                try:
                    if value in (None, ""):
                        self.clear_cell(self.log, addr)
                    elif isinstance(value, (int, float)):
                        self.set_num(self.log, addr, value)
                    else:
                        self.set_str(self.log, addr, value)
                except Exception as e:
                    restore_errors.append("%s: %s" % (addr, e))
            try:
                self.recalc()
            except Exception as e:
                restore_errors.append("recalc: %s" % e)
            if restore_errors:
                reason += " | ROLLBACK FAILED: " + "; ".join(restore_errors)
            return row, False, reason

        try:
            self.set_str(self.log, "B%d" % row, p["name"])
            self.set_str(self.log, "D%d" % row, winner_disp)
            self.set_num(self.log, "E%d" % row, price)
            if slot:
                self.set_str(self.log, "F%d" % row, slot)
            self.recalc()
            post = self.hq_get("B15")
            chk = str(self.hq_log_chk(row))
            ok = (isinstance(post, (int, float)) and isinstance(pre, (int, float))
                  and int(post) == int(pre) + 1 and "LAST" in chk)
            if not ok:
                return rollback("HEARTBEAT FAILED: %s" % chk)
            return row, True, chk
        except Exception as e:
            return rollback("WRITE FAILED: %s" % e)

    def hq_log_chk(self, row):
        return self.arr(self.log, "J%d" % row)[0][0]

    def undo(self):
        col = self.arr(self.log, "B5:B%d" % LOG_LAST)
        last_row = None
        for i in range(len(col) - 1, -1, -1):
            if col[i][0] not in (None, ""):
                last_row = LOG_FIRST + i
                break
        if last_row is None:
            say("Nothing to undo.")
            return
        pre = self.hq_get("B15")
        nm = self.arr(self.log, "B%d" % last_row)[0][0]
        winner = self.arr(self.log, "D%d" % last_row)[0][0]
        price = self.arr(self.log, "E%d" % last_row)[0][0]
        for colL in ("B", "D", "E", "F", "I"):
            self.clear_cell(self.log, "%s%d" % (colL, last_row))
        self.recalc()
        post = self.hq_get("B15")
        ok = (isinstance(post, (int, float)) and isinstance(pre, (int, float))
              and int(post) == int(pre) - 1)
        say("%s UNDONE (row %d)%s" % (
            nm, last_row, "" if ok else "  !! recount mismatch"))
        self.journal_undo("row=%d player=%s winner=%s price=%s ok=%s" % (
            last_row, nm, winner, price, ok))
        self.maybe_autosave()

    # ---------------------------------------------------------------- readout
    def fmt_time(self):
        v = self.hq_get("B16")
        try:
            mins = int(float(v) * 1440)          # fraction of a day
            return "%dh%02dm" % (mins // 60, mins % 60)
        except Exception:
            return "?"

    def status(self, tag=""):
        g = self.hq_get
        needs = []
        for r in range(16, 22):
            pos = g("H%d" % r)
            if pos:
                needs.append("%s %s/%s %s" % (pos, num(g("I%d" % r)),
                                              num(g("J%d" % r)), g("L%d" % r)))
        infl = g("B13")
        infl = float(infl) if isinstance(infl, (int, float)) else 1.0
        say("-" * 62)
        say("%sBudget $%s | Won %s | Slots %s | MAX BID $%s | Infl %.2f" % (
            tag, num(g("B7")), num(g("B8")), num(g("B9")),
            num(g("B10")), infl))
        say("Phase %s (%s noms, ~%s left) | Market %s | Threat %s" % (
            g("B17"), num(g("B15")), self.fmt_time(), g("E16"), g("M9")))
        say("ACTION: %s" % g("M10"))
        say("Needs: " + " | ".join(needs))

    def who(self, p):
        self.set_str(self.hq, "E5", p["name"])
        self.recalc()
        g = self.hq_get
        peers = [x for x in self.players
                 if x["owner"] == "Available" and x["pos"] == p["pos"]
                 and isinstance(x["target"], (int, float))
                 and x["target"] >= 25]
        rivals_need = 0
        try:
            needs_col = self.arr(self.tracker, "N6:N18")
            rivals_need = sum(1 for r in needs_col if r[0] == p["pos"])
        except Exception:
            pass
        say("=" * 62)
        say("%s  [%s]  Market $%s | Ceiling $%s | TARGET $%s" % (
            p["name"], p["pos"], num(p["market"]), num(p["ceiling"]),
            num(p["target"])))
        say("Signal: %s | Tag: %s | SAFE BID now: $%s" % (
            g("G6"), g("G9"), num(g("G8"))))
        if p["injury"]:
            say("NEWS: %s" % str(p["injury"])[:160])
        say("Scarcity: %d available %s priced $25+ | %d rivals list %s as "
            "biggest need" % (len(peers), p["pos"], rivals_need, p["pos"]))

    def nom(self):
        g = self.hq_get
        say("=" * 62)
        say("PHASE %s | %s | Threat %s" % (g("B17"), g("E16"), g("M9")))
        say("Sheet says: %s" % g("M10"))
        say("Advice: %s" % str(g("E17"))[:170])
        drains = [p for p in self.players
                  if p["owner"] == "Available"
                  and str(p.get("tag", "") or "").upper() == "DRAIN"
                  and isinstance(p["market"], (int, float))]
        drains.sort(key=lambda p: -float(p["market"]))
        if drains:
            say("Top DRAIN candidates (nom these to burn rival cash):")
            for p in drains[:5]:
                say("  %-24s %-4s mkt $%-4s target $%-4s" % (
                    p["name"], p["pos"], num(p["market"]), num(p["target"])))
        say("(type 'who <name>' to load any nominee onto the dashboard)")

    def recent(self):
        rows = []
        grid = self.arr(self.log, "A5:H%d" % LOG_LAST)
        for r in reversed(grid):
            if r[1] not in (None, ""):
                rows.append(r)
            if len(rows) == 5:
                break
        say("Last sales:")
        for r in rows:
            say("  #%s %-26s -> %-12s $%s" % (
                num(r[0]), r[1], r[3], num(r[4])))

    def reconcile_espn_picks(self, watcher):
        """Compare ESPN completed picks against the Auction Log on watch start.

        Surfaces any ESPN-reported sales that are missing from the workbook
        (e.g. from a previous session crash).
        """
        if not watcher or not hasattr(watcher, "pending_sales"):
            return
        # Build a set of player names already in the Auction Log
        logged_names = set()
        grid = self.arr(self.log, "B5:B%d" % LOG_LAST)
        for row in grid:
            name = row[0]
            if name not in (None, ""):
                logged_names.add(str(name).strip().lower())
        # Check pending_sales for already-logged players
        for sale in list(watcher.pending_sales):
            name = (sale.get("name") or "").strip().lower()
            if name and name in logged_names:
                sale["logged"] = True  # suppress — already in workbook
        # Surface any unlogged ESPN sales from previous session
        pending_unlogged = [s for s in watcher.pending_sales if not s.get("logged")]
        if pending_unlogged:
            say("!! ESPN has %d unlogged sale(s) from previous session:" % len(pending_unlogged))
            for s in pending_unlogged[:5]:
                say("   %s" % _sold_line(s))
            say("  Type ! to log each, or ignore if already entered manually.")


def norm_cell(v):
    if v in (None, ""):
        return None
    if isinstance(v, float):
        if math.isnan(v):
            return "ERR"
        return int(v) if v.is_integer() else v
    return v


# --------------------------------------------------------------- attach/boot
def find_or_open_doc(desktop, visible=True):
    """Reuse the exact DRAFT_DAY workbook if open, else open that file only."""
    uno = UNO
    if not os.path.isfile(DRAFT_WORKBOOK_PATH):
        say("ERROR: draft workbook not found:")
        say("       %s" % DRAFT_WORKBOOK_PATH)
        say("       Place %s in the script folder." % DRAFT_WORKBOOK_NAME)
        sys.exit(1)
    comps = desktop.Components.createEnumeration()
    while comps.hasMoreElements():
        d = comps.nextElement()
        try:
            url = d.getURL()
        except Exception:
            continue
        if not url:
            continue
        try:
            base = os.path.basename(uno.fileUrlToSystemPath(url))
        except Exception:
            continue
        if base.lower() == DRAFT_WORKBOOK_NAME.lower():
            return d, False, base
    from com.sun.star.beans import PropertyValue

    def pv(n, v):
        p = PropertyValue(); p.Name = n; p.Value = v; return p
    url = uno.systemPathToFileUrl(DRAFT_WORKBOOK_PATH)
    doc = desktop.loadComponentFromURL(url, "_blank", 0, (pv("Hidden", False),))
    return doc, True, DRAFT_WORKBOOK_NAME


def attach(test_path=None):
    global UNO
    UNO = _import_uno()
    uno = UNO
    ctx = ensure_office_ctx(spawn_visible=(test_path is None))
    smgr = ctx.ServiceManager
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    apply_recalc_config(smgr, ctx)
    if test_path:
        assert_safe_test_path(test_path)
        from com.sun.star.beans import PropertyValue

        def pv(n, v):
            p = PropertyValue(); p.Name = n; p.Value = v; return p
        url = uno.systemPathToFileUrl(os.path.abspath(test_path))
        doc = desktop.loadComponentFromURL(url, "_blank", 0,
                                           (pv("Hidden", True),))
        return Copilot(ctx, doc, spawned=True, testing=True)
    doc, opened, name = find_or_open_doc(desktop)
    say("workbook locked: %s" % name)
    return Copilot(ctx, doc, spawned=opened, testing=False)


HELP = """
COMMANDS
  <player> [winner] [price] [slot]   e.g.  gibbs t7 34      | olave me 21 wr2
  who <player>      pre-bid briefing, loads nominee onto Draft HQ panel
  watch             start ESPN on-block sync (espn_config.json)
  watch off         stop ESPN watcher
  block             force-fetch current ESPN nominee -> who
  !                 log next unlogged ESPN sale (queues multiple sales)
  last              show most recent unlogged ESPN sale
  nom               nomination helper: sheet's action + top DRAIN names
  status            full snapshot (budget/needs/phase/threat)
  log               last 5 sales
  undo              clear last sale row
  help              this
Winners: me, t2-t14, or name fragment. Slots: qb rb1 rb2 wr1 wr2 te flex d/st k be1-be6
"""


# ------------------------------------------------------------------ ESPN watch
_watch_state = {
    "watcher": None,
}


def _team_disp(cp, token):
    """Map ME/T7 config token to Team Tracker display name."""
    if not token:
        return None
    t = str(token).strip().upper()
    if t in cp.teams:
        return cp.teams[t]
    w, _ = cp.resolve_winner(token)
    return w


def _sold_suggest(sale):
    name = sale.get("name") or "?"
    tok = sale.get("winner_token") or "t?"
    price = int(sale.get("price") or 0)
    return "%s %s %d" % (name, str(tok).lower(), price)


def _sold_line(sale):
    """ESPN SOLD: Gibbs -> PA (t7) $34"""
    name = sale.get("name") or "?"
    tok = sale.get("winner_token")
    disp = sale.get("winner_disp") or tok or "?"
    price = int(sale.get("price") or 0)
    if tok:
        return "ESPN SOLD: %s -> %s (%s) $%d" % (name, disp, str(tok).lower(), price)
    return "ESPN SOLD: %s -> %s $%d" % (name, disp, price)


def handle_espn_nominee(cp, row, cfg):
    from espn_watch import map_team_token
    name = row.get("name") or ""
    p, cands = cp.resolve_player(name)
    if p is None and len(cands) == 1:
        p = cands[0]
    if p is None:
        if cands:
            say("ESPN on block ambiguous '%s' — type: who %s" % (name, name))
        else:
            say("ESPN on block unmatched '%s' — type: who <name>" % name)
        return
    # Suppress duplicate full who if HQ already showing this nominee
    try:
        cur = cp.hq_get("E5")
        already = (cur and str(cur).strip().lower() == p["name"].strip().lower())
    except Exception:
        already = False
    if already:
        say("(HQ already on %s — skip who reprint)" % p["name"])
    else:
        cp.who(p)
    nom_tok = map_team_token(row.get("nominating_team_id"), cfg.get("team_map") or {})
    win_tok = map_team_token(row.get("team_id"), cfg.get("team_map") or {})
    say("ESPN block: bid $%s | nominating %s | high bidder %s" % (
        row.get("bid"), nom_tok or row.get("nominating_team_id"),
        win_tok or row.get("team_id")))


def handle_espn_bid(cp, row, cfg):
    """Same nominee, bid changed — one line only (no full who reprint)."""
    from espn_watch import map_team_token
    win_tok = map_team_token(row.get("team_id"), cfg.get("team_map") or {})
    say("ESPN bid: %s now $%s (high %s)" % (
        row.get("name") or "?", row.get("bid"),
        win_tok or row.get("team_id") or "?"))


def handle_espn_sold(cp, row, cfg):
    from espn_watch import map_team_token
    name = row.get("name") or ""
    tok = map_team_token(row.get("team_id"), cfg.get("team_map") or {})
    winner = _team_disp(cp, tok) if tok else None
    price = int(row.get("bid") or 0)
    sale = {
        "name": name,
        "winner_token": tok,
        "winner_disp": winner,
        "price": price,
        "logged": False,
    }
    # Append to watcher's pending queue (not a single last_sale)
    w = _watch_state.get("watcher")
    if w:
        w.pending_sales.append(sale)
    say(_sold_line(sale))
    say("  type ! to log, or paste: %s" % _sold_suggest(sale))
    if cfg.get("autolog") and winner and price >= 1 and tok:
        say("(autolog on — writing sale)")
        result = do_sale(cp, "%s %s %d" % (name, tok.lower(), price))
        if result:
            sale["logged"] = True


def start_stdin_queue():
    """Background stdin reader so we can drain ESPN events while idle."""
    q = queue.Queue()

    def _reader():
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                q.put(None)
                break
            if line == "":
                q.put(None)
                break
            q.put(line.rstrip("\r\n"))
    t = threading.Thread(target=_reader, name="StdinReader", daemon=True)
    t.start()
    return q


_line_q = None  # set in main() for live REPL


def read_line(prompt=""):
    """Read a line; shares stdin queue with ESPN event loop when active."""
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    if _line_q is None:
        return input()
    while True:
        try:
            line = _line_q.get(timeout=0.4)
        except queue.Empty:
            continue
        if line is None:
            raise EOFError
        return line


def drain_watch_events(cp):
    w = _watch_state.get("watcher")
    if not w:
        return 0
    cfg = getattr(w, "cfg", {}) or {}
    n = 0
    for ev in w.pop_events():
        n += 1
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind == "info":
            say(payload.get("msg") or "")
        elif kind in ("error", "stale"):
            say("!! %s" % (payload.get("msg") or "espn error"))
        elif kind == "nominee":
            handle_espn_nominee(cp, payload, cfg)
        elif kind == "bid":
            handle_espn_bid(cp, payload, cfg)
        elif kind == "sold":
            handle_espn_sold(cp, payload, cfg)
    return n


def cmd_watch_on(cp):
    from espn_watch import load_config, EspnDraftWatcher, ConfigError
    if _watch_state.get("watcher") and _watch_state["watcher"].running:
        say("watch already running")
        return
    try:
        cfg = load_config()
    except ConfigError as e:
        say("ERROR: %s" % e)
        return
    w = EspnDraftWatcher(cfg)
    try:
        w.start()
    except Exception as e:
        say("ERROR: could not start watch: %s" % e)
        return
    _watch_state["watcher"] = w
    drain_watch_events(cp)
    # Reconcile: mark already-logged sales, surface missing ones
    cp.reconcile_espn_picks(w)


def cmd_watch_off(silent=False):
    w = _watch_state.get("watcher")
    if not w:
        if not silent:
            say("watch is not running")
        return
    w.stop()
    for ev in w.pop_events():
        if ev.get("kind") == "info":
            say((ev.get("payload") or {}).get("msg") or "")
    _watch_state["watcher"] = None


def cmd_block(cp):
    from espn_watch import load_config, EspnDraftWatcher, ConfigError
    w = _watch_state.get("watcher")
    try:
        if w and w.running:
            nominee, _, _ = w.fetch_state()
            cfg = w.cfg
        else:
            cfg = load_config()
            tmp = EspnDraftWatcher(cfg)
            tmp.ensure_player_map()
            nominee, _, _ = tmp.fetch_state()
    except ConfigError as e:
        say("ERROR: %s" % e)
        return
    except Exception as e:
        say("ERROR: ESPN fetch failed: %s" % e)
        return
    if not nominee:
        say("ESPN: no player currently on the block (draft not live yet?)")
        return
    handle_espn_nominee(cp, nominee, cfg)


def cmd_last():
    w = _watch_state.get("watcher")
    pending = list(w.pending_sales) if w else []
    # Find the most recent unlogged sale (from the end)
    sale = None
    for s in reversed(pending):
        if not s.get("logged"):
            sale = s
            break
    if not sale:
        say("? no unlogged ESPN sale — all pending sales have been logged")
        return
    say(_sold_line(sale))
    flag = " (already logged)" if sale.get("logged") else ""
    say("  type ! to log, or paste: %s%s" % (_sold_suggest(sale), flag))


def cmd_bang(cp):
    w = _watch_state.get("watcher")
    if not w:
        say("? watch is not running — type 'watch' first, or log manually")
        return
    # Pop the first unlogged sale from the pending queue
    sale = None
    while w.pending_sales:
        candidate = w.pending_sales[0]
        if candidate.get("logged"):
            w.pending_sales.popleft()
            continue
        sale = candidate
        break
    if not sale:
        say("? no unlogged ESPN sale pending — wait for a SOLD announce, or log manually")
        return
    tok = sale.get("winner_token")
    if not tok or not sale.get("price"):
        say("? ESPN sale missing winner/price — log manually")
        # Do NOT discard — leave in queue for manual resolution
        return
    name = sale.get("name") or ""
    p, cands = cp.resolve_player(name)
    if p is None and len(cands) == 1:
        p = cands[0]
    if p is None:
        say("? cannot resolve '%s' for ! — log manually" % name)
        return  # leave in queue for retry
    if p["owner"] != "Available":
        say("! refuse: %s already SOLD (owner: %s)" % (p["name"], p["owner"]))
        say("  if you just mis-logged, type: undo")
        w.pending_sales.popleft()  # already in workbook, remove from queue
        return
    line = "%s %s %d" % (name, tok.lower(), int(sale["price"]))
    say("(logging ESPN sale) %s" % line)
    result = do_sale(cp, line)
    if result:
        w.pending_sales.popleft()  # committed — remove from queue
    # If result is False, sale stays at front of queue for retry


def do_sale(cp, line):
    """Log a sale into the Auction Log.  Returns a dict on success, False on failure."""
    frag, winner, price, slot = cp.parse_sale(line)
    if not frag:
        say("? no player name found"); return False
    p, cands = cp.resolve_player(frag)
    if p is None:
        if not cands:
            say("? no player matches '%s'" % frag); return False
        p = pick("Ambiguous '%s':" % frag, cands)
        if p is None:
            say("cancelled"); return False
    if p["owner"] != "Available":
        say("! %s already SOLD (owner: %s) - aborted" % (p["name"], p["owner"]))
        return False
    if winner is None:
        winner = None
        for t in frag.split():
            w, _hits = cp.resolve_winner(t)
            if w:
                winner = w
                break
        if winner is None:
            raw = read_line("Winner (me / t2-t14 / name): ").strip()
            winner, hits = cp.resolve_winner(raw)
            if winner is None:
                say("? winner '%s' not in Team Tracker - aborted "
                    "(winners must match tracker names)" % raw)
                return False
    if price is None:
        raw = read_line("Price: $").strip()
        m = PRICE_TOKEN_RE.match(raw)
        if not m:
            say("? bad price"); return False
        price = int(m.group(1))
    if price < 1:
        say("? price must be >= 1"); return False
    # Max-bid safety check: only applies to YOUR purchases, not rival sales
    if winner == cp.teams.get("ME"):
        maxbid = cp.hq_get("B10")
        if isinstance(maxbid, (int, float)) and price > maxbid:
            if not confirm("! $%d exceeds legal max bid $%s. Force?" %
                           (price, num(maxbid))):
                return False
    # Slot handling: only MY purchases get roster slots
    if winner != cp.teams.get("ME"):
        if slot:
            say("(slot ignored — only YOUR purchases get roster slots)")
            slot = None
    else:
        if slot:
            # Validate the user-supplied slot
            ok_slot, err = cp.validate_slot(slot, p["pos"])
            if not ok_slot:
                say("? bad slot: %s" % err)
                return False
        else:
            slot = cp.suggest_slot(p["pos"])
            if slot:
                say("(slot auto: %s)" % slot)
    row, ok, chk = cp.write_sale(p, winner, price, slot)
    if not ok:
        say("!! ROW %d NOT COMMITTED: %s -> %s  $%d%s  [%s]" % (
            row, p["name"], winner, price,
            (" slot " + slot) if slot else "", chk.split("\n")[0][:80]))
        say("!! sale was rolled back; correct the issue and retry")
        return False
    say("OK ROW %d LOGGED: %s -> %s  $%d%s  [%s]" % (
        row, p["name"], winner, price,
        (" slot " + slot) if slot else "", chk.split("\n")[0][:40]))
    cp.journal_sale("row=%d player=%s winner=%s price=%d slot=%s heartbeat=%s" % (
        row, p["name"], winner, price, slot or "", "OK" if ok else "FAIL"))
    cp.maybe_autosave()
    cp.refresh_cache()
    if winner == cp.teams.get("ME"):
        cp.status(tag=">> ")
    else:
        b = cp.hq_get
        say("budget $%s | max bid $%s | slots %s" %
            (num(b("B7")), num(b("B10")), num(b("B9"))))
    return {"row": row, "heartbeat": True, "committed": True}


def cleanup_session(cp):
    """Run every shutdown step even if an earlier cleanup action fails."""
    try:
        cmd_watch_off(silent=True)
    except Exception as e:
        say("!! watcher shutdown failed: %s" % e)
    try:
        cp.autosave("quit")
    except Exception as e:
        say("!! final autosave failed: %s" % e)
    try:
        journal("SESSION_END", "ok")
    except Exception as e:
        say("!! session journal failed: %s" % e)
    say("bye (workbook stays open)")


def pick(prompt, cands):
    say(prompt)
    for i, c in enumerate(cands, 1):
        meta = ("  [%s target $%s]" % (c["pos"], num(c["target"]))
                if isinstance(c, dict) else "")
        nm = c["name"] if isinstance(c, dict) else c
        say("  %d) %s%s" % (i, nm, meta))
    raw = read_line("# (1-%d, x=cancel): " % len(cands)).strip()
    if raw.isdigit() and 1 <= int(raw) <= len(cands):
        return cands[int(raw) - 1]
    return None


def confirm(msg):
    return read_line("%s [y/N]: " % msg).strip().lower() == "y"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test = len(sys.argv) > 1 and sys.argv[1] == "--test"
    path = sys.argv[2] if len(sys.argv) > 2 else None
    if test and not path:
        say("ERROR: usage: draft_copilot.py --test <copy.xlsx>")
        sys.exit(2)
    cp = attach(path if test else None)
    if test:
        from selftest import run
        rc = run(cp)
        if cp.spawned and not test_keep_alive():
            try:
                cp.doc.close(False)
                cp.smgr.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", cp.ctx).terminate()
            except Exception:
                pass
        sys.exit(rc)
    say("DRAFT CO-PILOT attached to: %s" % cp.doc.Title)
    say("Autosave every %d sales + on quit | journal: %s" % (
        AUTOSAVE_EVERY, JOURNAL_PATH))
    say("ESPN: type 'watch' after espn_config.json is filled (see RUNBOOK)")
    say("Type 'help' for commands. Ctrl+C twice to quit.\n")
    journal("SESSION_START", cp.doc.Title)
    cp.status(tag="READY >> ")
    global _line_q
    _line_q = start_stdin_queue()
    sys.stdout.write("draft> ")
    sys.stdout.flush()
    try:
        while True:
            n = drain_watch_events(cp)
            if n:
                sys.stdout.write("draft> ")
                sys.stdout.flush()
            try:
                line = _line_q.get(timeout=0.4)
            except queue.Empty:
                continue
            if line is None:
                say("")
                break
            line = line.strip()
            low = line.lower()
            try:
                if low in ("q", "quit", "exit"):
                    break
                elif low.startswith("help"):
                    say(HELP)
                elif low in ("watch off", "watch stop") or low.startswith("watch off"):
                    cmd_watch_off()
                elif low in ("watch", "watch on", "watch start"):
                    cmd_watch_on(cp)
                elif low.startswith("watch"):
                    say("? use: watch | watch off")
                elif low == "block":
                    cmd_block(cp)
                elif low == "!":
                    cmd_bang(cp)
                elif low == "last":
                    cmd_last()
                elif low.startswith("undo"):
                    cp.undo(); cp.refresh_cache(); cp.status(tag=">> ")
                elif low.startswith("who "):
                    p, cands = cp.resolve_player(line[4:])
                    if p is None and cands:
                        p = pick("", cands)
                    if p:
                        cp.who(p)
                    else:
                        say("? no match")
                elif low.startswith("status"):
                    cp.status()
                elif low.startswith("log"):
                    cp.recent()
                elif low.startswith("nom"):
                    cp.nom()
                elif not line:
                    pass
                else:
                    do_sale(cp, line)
            except Exception as e:
                say("!! %s" % e)
            sys.stdout.write("draft> ")
            sys.stdout.flush()
    except (KeyboardInterrupt, EOFError):
        say("")
    finally:
        cleanup_session(cp)


def test_keep_alive():
    return False


if __name__ == "__main__":
    main()
