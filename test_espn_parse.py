"""Quick parser smoke for espn_watch fixture (no LibreOffice needed)."""
from espn_watch import load_fixture, parse_draft_state, map_team_token


def main():
    p = load_fixture()
    nom, done, pmap = parse_draft_state(p)
    assert nom and nom["name"] == "Ja'Marr Chase" and nom["bid"] == 12, nom
    assert len(done) == 1 and done[0]["name"] == "Jahmyr Gibbs" and done[0]["bid"] == 34
    assert pmap[4427366] == "Jahmyr Gibbs"
    assert map_team_token(1, {1: "ME", 3: "T3"}) == "ME"
    print("fixture parse OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
