"""Crée une campagne de démonstration.

Usage (dans le conteneur) : python -m maze.demo
"""

from __future__ import annotations

from . import db, defaults
from .config import load_settings

FACTIONS = [
    ("main", "La Main Grise", "Guilde de voleurs", "#8e5aa8",
     "Ceux qui ont tout volé à Ys, sauf la mer.",
     "Contrôle la contrebande du port et la moitié des tavernes de la basse ville.",
     "Mettre la main sur les registres de la Capitainerie avant l'enquête royale."),
    ("garde", "Garde du Port", "Milice", "#4f7fb5",
     "Mal payée, mal équipée, mais tenace.",
     "Une centaine d'hommes répartis entre les quais et la tour de guet.",
     "Garder la paix jusqu'à l'arrivée de l'inspecteur royal."),
    ("valcourt", "Maison Valcourt", "Maison noble", "#c9a45c",
     "Vieille noblesse ruinée qui tient encore le port par ses titres.",
     "Propriétaire des entrepôts du quai Est. Ses dettes dépassent sa fortune.",
     "Marier l'héritière à un armateur et effacer ses dettes."),
    ("maree", "Culte de la Marée Noire", "Culte", "#3f9c8a",
     "On dit qu'ils prient ce qui dort sous la baie.",
     "Une vingtaine de fidèles, surtout des pêcheurs ruinés par les tempêtes.",
     "Rouvrir le sanctuaire englouti à la prochaine grande marée."),
]

NPCS = [
    ("maelis", "Maëlis Corvin", "Receleuse", "vivant", "Humaine", "38 ans", "Taverne du Hareng Saur",
     "Elle sait tout ce qui entre au port, et le prix de chaque chose.",
     "Cheveux gris coupés court, bagues à chaque doigt, rire rauque.",
     "Joviale en surface, calculatrice en dessous. Ne pardonne jamais une dette.",
     "Prendre la tête de la Main Grise sans faire couler de sang.",
     "Informe secrètement la Garde depuis dix ans en échange de sa tranquillité."),
    ("hart", "Capitaine Hart", "Capitaine de la Garde", "vivant", "Humain", "52 ans", "Tour de guet",
     "Vieux soldat honnête dans une ville qui ne l'est plus.",
     "Grand, voûté, une cicatrice qui lui ferme l'œil gauche.",
     "Droit, rigide, fatigué. Boit trop le soir.",
     "Partir à la retraite avec son honneur intact.",
     "Il sait que [[Maëlis Corvin]] est son indicatrice et la protège."),
    ("ysolde", "Ysolde de Valcourt", "Héritière", "vivant", "Humaine", "24 ans", "Hôtel Valcourt",
     "Promise à un armateur qu'elle méprise.",
     "Robe sombre, gants de cuir, regard d'acier.",
     "Brillante et impatiente. Déteste qu'on décide à sa place.",
     "Sauver la maison sans se vendre.",
     "Finance en cachette le Culte de la Marée Noire, persuadée qu'il peut lui rendre la fortune de sa famille."),
    ("aurele", "Aurèle de Valcourt", "Chef de la maison", "vivant", "Humain", "61 ans", "Hôtel Valcourt",
     "Un vieux nom, des poches vides.",
     "Perruque poudrée, toux persistante.",
     "Fier, aveugle à la ruine qui l'entoure.",
     "Préserver le rang de la famille coûte que coûte.",
     "A mis en gage les titres du quai Est auprès de la Main Grise."),
    ("silas", "Silas le Muet", "Coupe-jarret", "vivant", "Demi-elfe", "", "Ruelles de la basse ville",
     "On ne l'entend jamais arriver.",
     "Silhouette sèche, langue coupée, signe des mains.",
     "Loyal jusqu'à l'absurde.",
     "Protéger Maëlis, qui l'a recueilli enfant.", ""),
    ("oriane", "Mère Oriane", "Grande prêtresse", "vivant", "Humaine", "70 ans", "Grotte des Soupirs",
     "Elle entend la mer chanter et répète ses paroles.",
     "Pieds nus, couverte de coquillages, voix douce.",
     "Sereine, inquiétante, sincère.",
     "Réveiller ce qui dort sous la baie.",
     "Elle n'est plus vraiment humaine depuis la dernière grande marée."),
    ("brann", "Brann Coque-de-Noix", "Pêcheur", "disparu", "Halfelin", "45 ans", "Quai des pêcheurs",
     "Disparu depuis trois nuits. Sa barque est revenue vide.",
     "Bonnet rouge, sourire édenté.", "Bavard, superstitieux.",
     "Rembourser ses dettes au Culte.", ""),
    ("elias", "Elias Morn", "Ancien capitaine de la Garde", "mort", "Humain", "", "",
     "Retrouvé noyé l'hiver dernier. Personne n'y a cru.",
     "", "", "",
     "Avait découvert les registres falsifiés de la Capitainerie."),
    ("tess", "Tess Vif-Argent", "Sergent de la Garde", "vivant", "Humaine", "29 ans", "Quais",
     "Ambitieuse, rapide, un peu trop curieuse.",
     "Tresse noire, uniforme impeccable.", "Franche, courageuse, impulsive.",
     "Devenir capitaine et nettoyer le port.", ""),
]

MEMBERSHIPS = [
    ("maelis", "main", "Lieutenante", False),
    ("silas", "main", "Lame", False),
    ("hart", "garde", "Capitaine", True),
    ("tess", "garde", "Sergent", False),
    ("elias", "garde", "Ancien capitaine", False),
    ("aurele", "valcourt", "Chef de maison", True),
    ("ysolde", "valcourt", "Héritière", False),
    ("oriane", "maree", "Grande prêtresse", True),
    ("brann", "maree", "Fidèle", False),
]

RELATIONS = [
    ("npc", "maelis", "npc", "hart", "affaires", "Indicatrice", True, True),
    ("npc", "silas", "npc", "maelis", "famille", "Fils adoptif", False, False),
    ("npc", "aurele", "npc", "ysolde", "famille", "Père", False, False),
    ("npc", "ysolde", "faction", "maree", "affaires", "Finance le culte", True, True),
    ("npc", "aurele", "faction", "main", "dette", "Titres du quai Est en gage", True, False),
    ("npc", "hart", "npc", "tess", "mentor", "", True, False),
    ("npc", "tess", "npc", "hart", "rival", "Veut sa place", False, False),
    ("npc", "brann", "npc", "oriane", "dette", "", True, False),
    ("npc", "elias", "faction", "main", "ennemi", "Enquêtait sur eux", False, False),
    ("faction", "garde", "faction", "main", "ennemi", "", False, False),
    ("faction", "valcourt", "faction", "garde", "allie", "Paie une partie des soldes", False, False),
    ("npc", "maelis", "npc", "ysolde", "rival", "", False, False),
]


# Lieux : clé, parent, nom, type, accroche, texte à lire, description, secrets.
PLACES = [
    ("ys", None, "Ys", "ville", "Port marchand battu par les brumes, adossé à la baie engloutie.",
     "Les cloches de la ville basse sonnent dans le brouillard. On sent le sel, le goudron et le poisson.",
     "Trois quartiers étagés entre la mer et la falaise. La ville vit du commerce et de la contrebande.", ""),
    ("basse", "ys", "Basse ville", "quartier", "Tavernes, entrepôts et ruelles où la Garde ne s'aventure plus.",
     "", "", ""),
    ("hareng", "basse", "Taverne du Hareng Saur", "batiment", "La taverne de Maëlis : on y vend de tout, surtout ce qui n'existe pas.",
     "Fumée de pipe, odeur de bière tiède. La conversation baisse d'un ton quand vous entrez.",
     "Salle commune bruyante, arrière-boutique fermée à clé, cave reliée aux égouts.",
     "La cave rejoint un ancien tunnel de contrebande qui mène sous le quai Est."),
    ("ruelles", "basse", "Ruelles de la basse ville", "lieu", "Un dédale où l'on se perd, et où l'on perd parfois sa bourse.",
     "", "", ""),
    ("port", "ys", "Le Port", "quartier", "Quais, docks et capitainerie.", "", "", ""),
    ("tour", "port", "Tour de guet", "batiment", "Quartier général de la Garde du Port.", "", "", ""),
    ("quai-est", "port", "Quai Est", "lieu", "Les entrepôts de la maison Valcourt, gagés auprès de la Main Grise.",
     "", "", "Un entrepôt sert de cache au culte de la Marée Noire."),
    ("quai-pecheurs", "port", "Quai des pêcheurs", "lieu", "Barques, filets et misère depuis les tempêtes.", "", "", ""),
    ("haute", "ys", "Haute ville", "quartier", "Hôtels particuliers et jardins en terrasse.", "", "", ""),
    ("hotel", "haute", "Hôtel Valcourt", "batiment", "Une façade magnifique, des salons à moitié vides.",
     "Le grand escalier de marbre est fendu ; les tableaux ont laissé des traces claires sur les murs.", "", ""),
    ("baie", None, "Baie engloutie", "nature", "La vieille ville noyée, visible à marée très basse.", "", "", ""),
    ("grotte", "baie", "Grotte des Soupirs", "donjon", "Le vent y gémit à chaque marée. Le culte s'y réunit.",
     "Un souffle froid monte du fond de la grotte, comme une respiration.", "", ""),
    ("sanctuaire", "grotte", "Sanctuaire englouti", "salle", "Accessible seulement lors des grandes marées.",
     "", "", "Ce qui dort sous la baie s'y éveille si l'on rallume les trois braseros."),
]

PLACE_LINKS = [
    ("hareng", "npc", "maelis", "Travaille ici"),
    ("tour", "npc", "hart", "Travaille ici"),
    ("port", "npc", "tess", "Patrouille"),
    ("hotel", "npc", "ysolde", "Vit ici"),
    ("hotel", "npc", "aurele", "Vit ici"),
    ("ruelles", "npc", "silas", "Se cache ici"),
    ("grotte", "npc", "oriane", "Officie ici"),
    ("quai-pecheurs", "npc", "brann", "Travaillait ici"),
    ("hareng", "faction", "main", "Repaire"),
    ("basse", "faction", "main", "Contrôle"),
    ("tour", "faction", "garde", "Siège"),
    ("hotel", "faction", "valcourt", "Siège"),
    ("quai-est", "faction", "valcourt", "Possède"),
    ("grotte", "faction", "maree", "Sanctuaire"),
]


def create_demo(db_path) -> int:
    with db.transaction(db_path) as conn:
        cid = db.insert(conn, "campaigns", {
            "name": "Les Brumes d'Ys (démo)",
            "description": "Port marchand rongé par la contrebande, une maison noble ruinée et un culte qui attend la grande marée.",
        })
        defaults.seed_relation_types(conn, cid)
        defaults.seed_place_kinds(conn, cid)  # types des lieux ci-dessous (ville, quartier, bâtiment…)
        fids, nids = {}, {}
        for key, name, kind, color, summary, description, goals in FACTIONS:
            fids[key] = db.insert(conn, "factions", {
                "campaign_id": cid, "name": name, "kind": kind, "color": color,
                "summary": summary, "description": description, "goals": goals,
            })
        for key, name, title, status, _people, _age, _location, summary, appearance, personality, motivations, secrets in NPCS:
            nids[key] = db.insert(conn, "npcs", {
                "campaign_id": cid, "name": name, "title": title, "status": status, "summary": summary, "appearance": appearance,
                "personality": personality, "motivations": motivations, "secrets": secrets,
            })
        for npc, fac, role, leader in MEMBERSHIPS:
            db.insert(conn, "memberships", {"npc_id": nids[npc], "faction_id": fids[fac], "role": role, "is_leader": leader})
        ids = {"npc": nids, "faction": fids}
        for sk, s, tk, t, rtype, label, directed, secret in RELATIONS:
            db.insert(conn, "relations", {
                "campaign_id": cid, "source_kind": sk, "source_id": ids[sk][s], "target_kind": tk,
                "target_id": ids[tk][t], "type": rtype, "label": label, "directed": directed, "secret": secret,
            })
        pids: dict[str, int] = {}
        for order, (key, parent, name, kind, summary, ambiance, description, secrets) in enumerate(PLACES):
            pids[key] = db.insert(conn, "places", {
                "campaign_id": cid, "parent_id": pids.get(parent) if parent else None, "name": name, "kind": kind,
                "summary": summary, "ambiance": ambiance, "description": description, "secrets": secrets,
                "sort_order": order,
            })
        for place, kind, key, role in PLACE_LINKS:
            db.insert(conn, "place_links", {"place_id": pids[place], "entity_kind": kind,
                                            "entity_id": ids[kind][key], "role": role})
    return cid


def main() -> int:
    """Base de MAZE_DATA_DIR migrée (créée si besoin), puis campagne de démonstration (tests.test_demo)."""
    settings = load_settings()
    db.migrate(settings.db_path)
    campaign_id = create_demo(settings.db_path)
    print(f"Campagne de démonstration créée (id {campaign_id}).")
    return campaign_id


if __name__ == "__main__":
    main()
