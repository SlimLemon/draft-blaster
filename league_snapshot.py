"""Normalized league data. Independent of spreadsheet/UNO dependencies."""
import copy
from datetime import datetime, timezone
from espn_client import EspnError, extract_player_records, integer_id, valid_player_id

REQUIRED_LEAGUE_VIEWS = (
    "mSettings", "mTeam", "mRoster", "mMatchup", "mDraftDetail",
    "mStatus", "mPendingTransactions", "mTransactions2",
)

class LeagueSnapshot:
    @classmethod
    def from_client(cls, client, force_refresh_players=False):
        # Exactly one refresh invocation; consumers never refresh individual rows.
        client.build_player_map(force_refresh=force_refresh_players)
        raw = client.league_views(REQUIRED_LEAGUE_VIEWS, timeout=60)
        for key, kind in (("settings", dict), ("status", dict), ("teams", list),
                          ("draftDetail", dict), ("schedule", list)):
            if key not in raw or not isinstance(raw[key], kind):
                raise EspnError("Required ESPN section missing or malformed: " + key)
        if "picks" not in raw["draftDetail"] or not isinstance(raw["draftDetail"]["picks"], list):
            raise EspnError("Required ESPN section missing or malformed: draftDetail.picks")
        if any(not isinstance(t, dict) or not isinstance(t.get("roster"), dict)
               or not isinstance(t["roster"].get("entries"), list) for t in raw["teams"]):
            raise EspnError("Required ESPN roster entries missing or malformed")
        return cls(raw, client)

    def __init__(self, raw, client):
        self.raw_snapshot = copy.deepcopy(raw)
        self.metadata = {
            "league_id": str(client.cfg["league_id"]), "season": int(client.cfg["season"]),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "requested_views": list(REQUIRED_LEAGUE_VIEWS),
            "player_map_source": client._map_source,
            "player_refresh_error": client.player_refresh_error,
            "cache_write_error": client.cache_write_error,
        }
        payload_players = extract_player_records(raw)
        resolver = client.team_resolver(raw["teams"])
        unknown = set()

        def identity(pid):
            pid = integer_id(pid)
            record = client.player_records.get(pid, {})
            source = client.player_sources.get(pid, "unresolved")
            if source != "fresh_catalog" and pid in payload_players:
                record, source = payload_players[pid], "payload"
            if not record.get("fullName"):
                record, source = {}, "unresolved"
                if valid_player_id(pid):
                    unknown.add(pid)
            return {"player_id": pid, "player_name": record.get("fullName"),
                    "player_name_source": source,
                    "position_id": record.get("defaultPositionId"),
                    "pro_team_id": record.get("proTeamId")}

        self.settings = copy.deepcopy(raw["settings"])
        self.status = copy.deepcopy(raw["status"])
        self.draft_detail = {key: copy.deepcopy(value) for key, value in raw["draftDetail"].items()
                             if key != "picks"}
        self.coverage = {}
        for key in ("members", "transactions", "pendingTransactions"):
            value = raw.get(key)
            if value is not None and (not isinstance(value, list) or
                                      any(not isinstance(row, dict) for row in value)):
                raise EspnError("Malformed ESPN collection: " + key)
            self.coverage[key] = "available" if value is not None else "unavailable"
        self.members = copy.deepcopy(raw.get("members"))
        self.transactions = copy.deepcopy(raw.get("transactions"))
        self.pending_transactions = copy.deepcopy(raw.get("pendingTransactions"))
        self.schedule = copy.deepcopy(raw["schedule"])
        self.teams, self.rosters, self.draft_picks = [], [], []
        for team in raw["teams"]:
            resolved = resolver.resolve(team.get("id"))
            self.teams.append({
                "team_id": resolved["team_id"], "team_token": resolved["token"],
                "team_name": team.get("name") or resolved["label"], "team_label": resolved["label"],
                "abbrev": team.get("abbrev"), "projected_rank": team.get("currentProjectedRank"),
                "points": team.get("points"), "record": copy.deepcopy(team.get("record")),
                "raw": {k: copy.deepcopy(v) for k, v in team.items() if k != "roster"},
            })
            for entry in team["roster"]["entries"]:
                if not isinstance(entry, dict):
                    raise EspnError("Malformed ESPN roster entry")
                ppe = entry.get("playerPoolEntry") or {}
                player = ppe.get("player") or {}
                row = identity(player.get("id", entry.get("playerId", ppe.get("id"))))
                row.update({"team_id": resolved["team_id"], "team_token": resolved["token"],
                            "team_name": team.get("name") or resolved["label"],
                            "lineup_slot_id": entry.get("lineupSlotId"),
                            "points": ppe.get("appliedStatTotal"),
                            "acquisition_type": entry.get("acquisitionType"),
                            "injury_status": player.get("injuryStatus"),
                            "raw": copy.deepcopy(entry)})
                self.rosters.append(row)
        for pick in raw["draftDetail"]["picks"]:
            if not isinstance(pick, dict):
                raise EspnError("Malformed ESPN draft pick")
            row = identity(pick.get("playerId"))
            team = resolver.resolve(pick.get("teamId"))
            nominator = resolver.resolve(pick.get("nominatingTeamId"))
            row.update({
                "pick_id": pick.get("id"), "round_id": pick.get("roundId"),
                "round_pick_number": pick.get("roundPickNumber"),
                "overall_pick_number": pick.get("overallPickNumber"),
                "team_id": team["team_id"], "team_token": team["token"], "team_name": team["label"],
                "nominating_team_id": nominator["team_id"], "nominating_team_name": nominator["label"],
                "bid_amount": pick.get("bidAmount"), "in_progress": pick.get("inProgress"),
                "raw": copy.deepcopy(pick)})
            self.draft_picks.append(row)
        self.unresolved_player_ids = sorted(unknown)
        self.metadata["unresolved_player_count"] = len(unknown)
        self.metadata["unresolved_draft_pick_count"] = sum(
            valid_player_id(p["player_id"]) and not p["player_name"] for p in self.draft_picks)

    def to_dict(self):
        return {key: copy.deepcopy(getattr(self, key)) for key in (
            "metadata", "coverage", "teams", "rosters", "draft_picks", "schedule",
            "transactions", "pending_transactions", "members", "settings", "status",
            "draft_detail", "unresolved_player_ids")}

