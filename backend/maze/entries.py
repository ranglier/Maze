"""Registre unique des champs des fiches PNJ, factions et lieux (audit M23 = QUAL-3).

Chaque champ y est décrit une fois, avec ce que chaque partie de Maze en fait. En dérivent :
- les modèles Pydantic de l'API (schemas.py : NpcCreate, NpcUpdate… ; champs `form`) ;
- l'historique des versions et les commentaires (entry_history.FIELDS, COMMENT_FIELDS) ;
- le connecteur Claude (mcp.ENTRY_FIELDS, FIELDS_DOC, champs retirés de get_entry) ;
- la composition des pages de Foundry (foundry.build_export) ;
- les champs longs dont l'import réécrit les mentions (transfer.py) ;
- l'interface (frontend/src/entry-fields.generated.js, écrit par `python -m maze.entries`, voir ce fichier).
Les déclencheurs SQL ne peuvent pas en dériver (migrations figées) : tests.test_registry vérifie que ceux de la
base migrée (révision trg_*_revision, index de recherche trg_search_*) suivent exactement le registre, que
chaque colonne de npcs, factions et places y est décrite (ou listée dans OTHER_COLUMNS), et que le fichier de
l'interface est à jour. Ajouter un champ : une migration (colonne, déclencheurs), une ligne ici, puis
`python -m maze.entries > ../frontend/src/entry-fields.generated.js`, et les tests disent ce qui manque.

Anciens champs (`legacy`, PNJ : people, age, location) : retirés de l'interface le 5 octobre (commit 278f537),
ils restaient écrits par le connecteur, publiés aux joueurs dans Foundry et trouvés par la recherche. Tant qu'ils
sont invisibles dans Maze : ni modifiables (API, connecteur), ni publiés dans Foundry, ni lus par le connecteur, ni
indexés (migration 028) ; colonnes et valeurs gardées (aucune perte), exportées et importées avec la campagne,
toujours suivies par l'historique (état des versions inchangé, aperçu d'une version) et par la révision.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass

KINDS = ("npc", "faction", "place")
TABLES = {"npc": "npcs", "faction": "factions", "place": "places"}
NPC_STATUSES = ("vivant", "mort", "disparu", "inconnu")

# Types de valeur (validation dans schemas.py, formulaire dans l'interface).
#   name : nom (obligatoire à la création) ; short : texte court ; summary : une phrase (500 caractères) ;
#   long : champ long (HTML de l'éditeur ou ancien Markdown, mentions @) ; status : statut d'un PNJ ;
#   color : #RRGGBB ; place_kind : clé d'un type de lieu de la campagne ; parent : lieu parent ; tags : étiquettes.
TYPES = ("name", "short", "summary", "long", "status", "color", "place_kind", "parent", "tags")
# Place dans l'interface : en-tête (identity), résumé, rubriques (section), étiquettes ; legacy : nulle part.
GROUPS = ("identity", "summary", "section", "tags", "legacy")
# Foundry : fact (liste « Titre : … » en tête de la page Fiche), read (texte à lire, en tête, encadré), public
# (rubrique de la page Fiche ; biographie de l'acteur pour un PNJ), secret (page « Secrets (MJ) »), None : absent
# (nom : titre du journal ; couleur : propriété de l'entrée).
FOUNDRY_ROLES = (None, "fact", "read", "public", "secret")


@dataclass(frozen=True)
class EntryField:
    key: str
    label: str                      # libellé dans Maze (interface, historique)
    type: str
    group: str
    secret: bool = False            # rubrique secrète (cadenas dans l'interface)
    history: bool = True            # suivi par l'historique des versions et la révision (trg_*_revision)
    search: bool = False            # indexé par la recherche (trg_search_*)
    connector: bool = True          # lu et modifié par le connecteur Claude
    foundry: str | None = None
    foundry_label: str = ""         # intitulé dans Foundry (défaut : label)
    doc: str = ""                   # précision pour la description des outils du connecteur (FIELDS_DOC)

    @property
    def legacy(self) -> bool:
        return self.group == "legacy"

    @property
    def form(self) -> bool:
        """Modifiable par l'interface et l'API (formulaire, PATCH) : tout sauf les anciens champs."""
        return not self.legacy

    @property
    def rich(self) -> bool:
        """Champ long de l'éditeur : mentions réécrites à l'import, encadrés traités pour Foundry."""
        return self.type == "long"

    @property
    def comment(self) -> bool:
        """Section qu'un commentaire peut viser."""
        return self.type in ("summary", "long")


F = EntryField
REGISTRY: dict[str, tuple[EntryField, ...]] = {
    "npc": (
        F("name", "Nom", "name", "identity", search=True),
        F("title", "Fonction", "short", "identity", search=True, foundry="fact", foundry_label="Titre",
          doc="titre ou fonction"),
        F("status", "Statut", "status", "identity", foundry="fact", doc="|".join(NPC_STATUSES)),
        F("people", "Peuple", "short", "legacy", connector=False),
        F("age", "Âge", "short", "legacy", connector=False),
        F("location", "Lieu (ancien champ)", "short", "legacy", connector=False),
        F("summary", "Résumé", "summary", "summary", search=True, foundry="public",
          doc="une phrase d'aperçu, 500 caractères"),
        F("appearance", "Apparence", "long", "section", search=True, foundry="public"),
        F("personality", "Personnalité", "long", "section", search=True, foundry="public"),
        F("motivations", "Motivations", "long", "section", search=True, foundry="public"),
        F("secrets", "Secret", "long", "section", secret=True, search=True, foundry="secret", foundry_label="Secrets"),
        F("notes", "Notes", "long", "section", search=True, foundry="secret", foundry_label="Notes du MJ"),
        F("tags", "Étiquettes", "tags", "tags", search=True, foundry="fact", doc="liste"),
    ),
    "faction": (
        F("name", "Nom", "name", "identity", search=True),
        F("kind", "Type", "short", "identity", search=True, foundry="fact", doc="type"),
        F("color", "Couleur", "color", "identity", doc="#RRGGBB"),
        F("summary", "Résumé", "summary", "summary", search=True, foundry="public"),
        F("description", "Description", "long", "section", search=True, foundry="public"),
        F("goals", "Objectifs", "long", "section", search=True, foundry="public"),
        F("resources", "Ressources", "long", "section", search=True, foundry="public"),
        F("secrets", "Secret", "long", "section", secret=True, search=True, foundry="secret", foundry_label="Secrets"),
        F("notes", "Notes", "long", "section", search=True, foundry="secret", foundry_label="Notes du MJ"),
        F("tags", "Étiquettes", "tags", "tags", search=True, foundry="fact"),
    ),
    "place": (
        F("name", "Nom", "name", "identity", search=True),
        F("kind", "Type", "place_kind", "identity", foundry="fact",
          doc="clé d'un type de lieu de la campagne, voir get_campaign"),
        # Rangement dans l'arbre : ni texte de la fiche (historique, révision), ni recherche.
        F("parent_id", "Rangé dans", "parent", "identity", history=False, foundry="fact",
          foundry_label="Fait partie de", doc="lieu parent ou null"),
        F("summary", "Résumé", "summary", "summary", search=True, foundry="public"),
        F("ambiance", "Accroche", "long", "section", search=True, foundry="read", foundry_label="Ambiance",
          doc="texte à lire aux joueurs"),
        F("description", "Description", "long", "section", search=True, foundry="public"),
        F("secrets", "Secret", "long", "section", secret=True, search=True, foundry="secret", foundry_label="Secrets"),
        F("notes", "Notes", "long", "section", search=True, foundry="secret", foundry_label="Notes du MJ"),
        F("tags", "Étiquettes", "tags", "tags", search=True, foundry="fact"),
    ),
}
del F

# Colonnes de npcs, factions et places qui ne sont pas des champs de texte de la fiche (images, positions sur la
# carte des relations, rangement, révision, dates) : vérifié par tests.test_registry.
OTHER_COLUMNS: dict[str, frozenset[str]] = {
    "npc": frozenset({"id", "campaign_id", "portrait_image_id", "pos_x", "pos_y", "sort_order", "revision",
                      "created_at", "updated_at"}),
    "faction": frozenset({"id", "campaign_id", "emblem_image_id", "pos_x", "pos_y", "sort_order", "revision",
                          "created_at", "updated_at"}),
    "place": frozenset({"id", "campaign_id", "image_id", "sort_order", "revision", "created_at", "updated_at"}),
}


def fields(kind: str) -> tuple[EntryField, ...]:
    return REGISTRY[kind]


def field(kind: str, key: str) -> EntryField:
    return next(f for f in REGISTRY[kind] if f.key == key)


def keys(kind: str, **flags: bool) -> tuple[str, ...]:
    """Clés des champs, dans l'ordre du registre, dont chaque propriété nommée a la valeur donnée
    (keys("npc", history=True), keys("place", rich=True)…)."""
    return tuple(f.key for f in REGISTRY[kind] if all(getattr(f, name) == value for name, value in flags.items()))


def foundry_fields(kind: str, role: str) -> tuple[EntryField, ...]:
    return tuple(f for f in REGISTRY[kind] if f.foundry == role)


def connector_fields(kind: str) -> frozenset[str]:
    """Champs que le connecteur Claude peut écrire (create_entry, update_entry)."""
    return frozenset(f.key for f in REGISTRY[kind] if f.connector and f.form)


def hidden_from_connector(kind: str) -> tuple[str, ...]:
    """Colonnes retirées de get_entry (et des réponses de create_entry, update_entry)."""
    return tuple(f.key for f in REGISTRY[kind] if not f.connector)


# --- Interface ---------------------------------------------------------------------------------

UI_HEADER = ("// Généré par « cd backend && python -m maze.entries > ../frontend/src/entry-fields.generated.js » "
             "depuis\n// backend/maze/entries.py (registre des champs, audit M23) : ne pas modifier à la main "
             "(vérifié par tests.test_registry).\n")


def ui_registry() -> dict[str, list[dict[str, object]]]:
    """Ce que l'interface lit du registre (frontend/src/fields.js) : champs du formulaire et de l'historique."""
    keep = ("key", "label", "type", "group", "secret", "history")
    out: dict[str, list[dict[str, object]]] = {}
    for kind in KINDS:
        out[kind] = [{**{k: v for k, v in asdict(f).items() if k in keep}, "comment": f.comment}
                     for f in REGISTRY[kind]]
    return out


def ui_module() -> str:
    body = json.dumps(ui_registry(), ensure_ascii=False, indent=2)
    return f"{UI_HEADER}export const ENTRY_REGISTRY = {body};\n"


if __name__ == "__main__":
    sys.stdout.write(ui_module())
