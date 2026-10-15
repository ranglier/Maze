"""Distribution d'un scénario : trois implémentations de la même règle comparées (audit QUAL-12).

Règle : PNJ et factions cités (@) dans le scénario et toutes ses pages, plus les ajouts manuels, moins les retraits.
- api.scenario_maps_of : fiche d'un PNJ ou d'une faction > « Sur les cartes » ;
- foundry.scenarios_of : export vers Foundry (cast) ;
- castOf (frontend/src/cast.js) : carte d'un scénario dans le navigateur, qui doit suivre aussitôt un ajout ou un
  retrait (d'où le calcul côté interface plutôt qu'une distribution envoyée par le serveur).

Campagnes aléatoires (arbres de fiches, mentions, réglages) décrites dans frontend/test/fixtures/cast-cases.json :
ce test les recharge dans une base, vérifie que les deux règles du serveur donnent la distribution attendue et que la
route GET /api/campaigns/{id}/documents/mentions (documents.mentions_payload) renvoie les données du fichier ;
frontend/test/cast.test.mjs (node --test) vérifie que castOf donne la même distribution à partir de ces données.
Règle changée volontairement : `python -m tests.test_cast_rules --write` réécrit le fichier (puis npm test).

Lancer : cd backend && python -m unittest -q tests.test_cast_rules
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

from maze import api, db, documents, foundry, scenemap

FIXTURE = Path(__file__).resolve().parents[2] / "frontend" / "test" / "fixtures" / "cast-cases.json"
CASES = 150
SEED = 7


def _generate(path: Path) -> list[dict]:
    rng = random.Random(SEED)
    cases = []
    with db.transaction(path) as conn:
        for trial in range(CASES):
            cid = db.insert(conn, "campaigns", {"name": f"c{trial}"})
            root = db.insert(conn, "documents", {"campaign_id": cid, "kind": "campaign", "title": ""})
            docs = [(root, None, "campaign", 0)]
            for i in range(rng.randint(1, 9)):
                parent = rng.choice(docs)
                kind = "scenario" if parent[2] == "campaign" else "page"
                order = rng.randint(0, 3)
                did = db.insert(conn, "documents", {"campaign_id": cid, "parent_id": parent[0], "kind": kind,
                                                    "title": f"d{i}", "sort_order": order})
                docs.append((did, parent[0], kind, order))
            ents = [("npc", db.insert(conn, "npcs", {"campaign_id": cid, "name": f"n{i}"})) for i in range(4)]
            ents += [("faction", db.insert(conn, "factions", {"campaign_id": cid, "name": f"f{i}"})) for i in range(2)]
            places = [("place", db.insert(conn, "places", {"campaign_id": cid, "name": f"p{i}"})) for i in range(2)]
            mentions, nodes = [], []
            for did, _, _, _ in docs:
                for kind, eid in rng.sample(ents + places, rng.randint(0, 3)):
                    mentions.append([did, kind, eid])
                # Réglages de carte (sur une page aussi : sans effet tant qu'elle n'est pas un scénario).
                for kind, eid in rng.sample(ents, rng.randint(0, 2)):
                    nodes.append([did, kind, eid, rng.choice(["auto", "added", "excluded"])])
            for did, kind, eid in mentions:
                conn.execute("INSERT INTO document_mentions (document_id, target_kind, target_id) VALUES (?, ?, ?)",
                             (did, kind, eid))
            for did, kind, eid, state in nodes:
                conn.execute("INSERT INTO scenario_nodes (document_id, entity_kind, entity_id, state) "
                             "VALUES (?, ?, ?, ?)", (did, kind, eid, state))
            case = {"campaign": cid, "documents": [list(d) for d in docs],
                    "npcs": [e for k, e in ents if k == "npc"], "factions": [e for k, e in ents if k == "faction"],
                    "places": [e for _, e in places], "mentions": mentions, "nodes": nodes}
            case["payload"], case["maps"] = _payload(conn, case)
            case["expected"] = _expected(conn, case)
            cases.append(case)
    return cases


def _load(path: Path, cases: list[dict]) -> None:
    """Campagnes du fichier, avec leurs identifiants, dans une base neuve."""
    with db.transaction(path) as conn:
        for case in cases:
            cid = case["campaign"]
            conn.execute("INSERT INTO campaigns (id, name) VALUES (?, ?)", (cid, f"c{cid}"))
            for did, parent, kind, order in case["documents"]:
                conn.execute("INSERT INTO documents (id, campaign_id, parent_id, kind, title, sort_order) "
                             "VALUES (?, ?, ?, ?, '', ?)", (did, cid, parent, kind, order))
            for table, key in (("npcs", "npcs"), ("factions", "factions"), ("places", "places")):
                for eid in case[key]:
                    conn.execute(f"INSERT INTO {table} (id, campaign_id, name) VALUES (?, ?, ?)", (eid, cid, str(eid)))
            for did, kind, eid in case["mentions"]:
                conn.execute("INSERT INTO document_mentions (document_id, target_kind, target_id) VALUES (?, ?, ?)",
                             (did, kind, eid))
            for did, kind, eid, state in case["nodes"]:
                conn.execute("INSERT INTO scenario_nodes (document_id, entity_kind, entity_id, state) "
                             "VALUES (?, ?, ?, ?)", (did, kind, eid, state))


def _payload(conn, case: dict) -> tuple[dict, dict]:
    """Ce que l'interface reçoit (champs lus par castOf) : route des mentions et carte de chaque scénario."""
    root = next(d[0] for d in case["documents"] if d[2] == "campaign")
    data = documents.mentions_payload(conn, case["campaign"], root)
    payload = {"root_id": data["root_id"],
               "documents": [{k: d[k] for k in ("id", "parent_id", "kind", "sort_order")} for d in data["documents"]],
               "mentions": {str(k): v for k, v in data["mentions"].items()},
               "overrides": {str(k): v for k, v in data["overrides"].items()}}
    maps = {str(d[0]): [{k: r[k] for k in ("kind", "id", "state")} for r in scenemap.nodes_of(conn, d[0])]
            for d in case["documents"] if d[2] == "scenario"}
    return json.loads(json.dumps(payload)), maps


def _expected(conn, case: dict) -> dict[str, list[str]]:
    """Distribution de chaque scénario selon foundry.scenarios_of (PNJ et factions), vérifiée contre
    api.scenario_maps_of fiche par fiche."""
    cid = case["campaign"]
    out = {str(sc["id"]): sorted([f"npc-{e}" for e in sc["cast"]["npc"]] + [f"faction-{e}" for e in sc["cast"]["faction"]])
           for sc in foundry.scenarios_of(conn, cid)}
    for kind in ("npc", "faction"):
        for eid in case[f"{kind}s"]:
            shown = {str(m["id"]) for m in api.scenario_maps_of(conn, kind, eid, cid)}
            by_foundry = {sid for sid, cast in out.items() if f"{kind}-{eid}" in cast}
            assert shown == by_foundry, (cid, kind, eid, sorted(shown), sorted(by_foundry))
    return out


class CastRulesTest(unittest.TestCase):
    def test_server_rules_match_fixture(self):
        self.assertTrue(FIXTURE.is_file(), f"{FIXTURE} absent : python -m tests.test_cast_rules --write")
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 100)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "maze.db"
            db.migrate(path)
            _load(path, cases)
            with db.transaction(path, write=False) as conn:
                for case in cases:
                    cid = case["campaign"]
                    payload, maps = _payload(conn, case)
                    self.assertEqual(payload, case["payload"], f"campagne {cid} : données de la route")
                    self.assertEqual(maps, case["maps"], f"campagne {cid} : cartes des scénarios")
                    self.assertEqual(_expected(conn, case), case["expected"], f"campagne {cid} : distribution")

    def test_fixture_covers_the_rule(self):
        """Le fichier exerce bien la règle : ajouts, retraits, pages imbriquées, réglages sur une page."""
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        states = {n[3] for c in cases for n in c["nodes"]}
        self.assertEqual(states, {"auto", "added", "excluded"})
        kinds = {d[2] for c in cases for d in c["documents"]}
        self.assertEqual(kinds, {"campaign", "scenario", "page"})
        nested = sum(1 for c in cases for d in c["documents"]
                     if d[2] == "page" and any(p[0] == d[1] and p[2] == "page" for p in c["documents"]))
        self.assertGreater(nested, 10)
        self.assertGreater(sum(len(v) for c in cases for v in c["expected"].values()), 300)


if __name__ == "__main__":
    if "--write" in sys.argv:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "maze.db"
            db.migrate(target)
            data = _generate(target)
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"{FIXTURE} : {len(data)} campagnes")
    else:
        unittest.main()
