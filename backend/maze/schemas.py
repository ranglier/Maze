"""Modèles de validation des données entrantes."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

from . import entries, limits

Kind = Literal["npc", "faction"]
NpcStatus = Literal[entries.NPC_STATUSES]

# Clé d'une nature de lien (table relation_types de la campagne).
RelationTypeKey = Annotated[str, Field(pattern=r"^[a-z0-9-]{1,40}$")]

Name = Annotated[str, Field(min_length=1, max_length=200)]
Short = Annotated[str, Field(max_length=200)]
# Texte long : Markdown (anciennes fiches) ou HTML de l'éditeur, plus volumineux.
Long = Annotated[str, Field(max_length=200_000)]
Coord = Annotated[float, Field(ge=-1e7, le=1e7)]

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# Étiquettes : 100 au plus à l'entrée, 30 conservées (sans doublon, casse ignorée).
Tags = Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=100)]


def _clean_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    kept: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = raw.strip()[:50]
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            kept.append(tag)
            if len(kept) == 30:
                break
    return kept


# --- Campagnes -------------------------------------------------------------

class CampaignCreate(_Strict):
    name: Name
    description: Long = ""


class CampaignUpdate(_Strict):
    name: Name | None = None
    description: Long | None = None
    timeline_enabled: bool | None = None   # module Chronologie affiché (migration 032)


# --- PNJ, factions et lieux (champs : registre entries.py, audit M23) -------------------------------

# Clé d'un type de lieu de la campagne (table place_kinds), vérifiée à l'enregistrement.
PlaceKind = Annotated[str, Field(min_length=1, max_length=40)]

_FIELD_TYPES: dict[str, Any] = {
    "name": Name, "short": Short, "summary": Annotated[str, Field(max_length=500)], "long": Long,
    "status": NpcStatus, "color": str, "place_kind": PlaceKind, "parent": int, "tags": Tags,
}
# Champs des modèles qui ne sont pas du texte de la fiche : couverture, position sur la carte des relations.
_EXTRA: dict[str, dict[str, Any]] = {
    "npc": {"portrait_image_id": (int | None, None), "pos_x": (Coord | None, None), "pos_y": (Coord | None, None)},
    "faction": {"emblem_image_id": (int | None, None), "pos_x": (Coord | None, None), "pos_y": (Coord | None, None)},
    "place": {"image_id": (int | None, None)},
}


class _EntryBase(_Strict):
    @field_validator("tags", check_fields=False)
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return _clean_tags(v)

    @field_validator("color", check_fields=False)
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not HEX_COLOR.match(v):
            raise ValueError("Couleur attendue au format #RRGGBB")
        return v.lower()


class _EntryUpdate(_EntryBase):
    # Révision sur laquelle le formulaire est parti (audit M1) : 409 si la fiche a changé entre-temps ;
    # facultative (connecteur Claude, scripts : aucune vérification).
    base_revision: int | None = None
    # Enregistrement repris après un 409 : jamais fondu avec la version précédente (celle de l'autre).
    new_version: bool = False


def _entry_model(name: str, kind: str, create: bool) -> type[BaseModel]:
    """Modèle d'une fiche : champs modifiables du registre (anciens champs exclus : refusés, extra="forbid"),
    tous facultatifs sauf le nom à la création."""
    spec: dict[str, Any] = {}
    for f in entries.fields(kind):
        if not f.form:
            continue
        annotation = _FIELD_TYPES[f.type]
        if f.type == "name" and create:
            spec[f.key] = (annotation, ...)
        else:
            spec[f.key] = (annotation | None, None)
    spec.update(_EXTRA[kind])
    return create_model(name, __base__=_EntryUpdate if not create else _EntryBase, __module__=__name__, **spec)


NpcCreate = _entry_model("NpcCreate", "npc", create=True)
NpcUpdate = _entry_model("NpcUpdate", "npc", create=False)
FactionCreate = _entry_model("FactionCreate", "faction", create=True)
FactionUpdate = _entry_model("FactionUpdate", "faction", create=False)
PlaceCreate = _entry_model("PlaceCreate", "place", create=True)
PlaceUpdate = _entry_model("PlaceUpdate", "place", create=False)


# --- Lieux ----------------------------------------------------------------

IconName = Annotated[str, Field(pattern=r"^[a-z][a-zA-Z0-9-]{0,30}$")]


class PlaceKindCreate(_Strict):
    label: Annotated[str, Field(min_length=1, max_length=40)]
    icon: IconName = "pin"


class PlaceKindUpdate(_Strict):
    label: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    icon: IconName | None = None


class KeyOrder(_Strict):
    ids: Annotated[list[int], Field(max_length=200)]


class EntityOrder(_Strict):
    # Ordre complet des PNJ (ou des factions) de la campagne, du premier au dernier.
    ids: Annotated[list[int], Field(max_length=20000)]


class PlaceMove(_Strict):
    parent_id: int | None = None
    index: Annotated[int, Field(ge=0, le=100_000)]


class PlaceOrder(_Strict):
    parent_id: int | None = None
    ids: Annotated[list[int], Field(max_length=2000)]


class PlaceLinkCreate(_Strict):
    entity_kind: Kind
    entity_id: int
    role: Annotated[str, Field(max_length=80)] = ""


class PlaceLinkUpdate(_Strict):
    role: Annotated[str, Field(max_length=80)]


# --- Appartenances ---------------------------------------------------------

class MembershipCreate(_Strict):
    """Membre : un PNJ (npc_id) ou une autre faction (member_faction_id), l'un ou l'autre (vérifié par la route)."""
    npc_id: int | None = None
    member_faction_id: int | None = None
    faction_id: int
    role: Short = ""
    is_leader: bool = False


class MembershipUpdate(_Strict):
    role: Short | None = None
    is_leader: bool | None = None


# --- Relations -------------------------------------------------------------

class RelationCreate(_Strict):
    source_kind: Kind
    source_id: int
    target_kind: Kind
    target_id: int
    type: RelationTypeKey = "autre"
    label: Short = ""
    description: Long = ""
    directed: bool = False
    secret: bool = False


class RelationUpdate(_Strict):
    source_kind: Kind | None = None
    source_id: int | None = None
    target_kind: Kind | None = None
    target_id: int | None = None
    type: RelationTypeKey | None = None
    label: Short | None = None
    description: Long | None = None
    directed: bool | None = None
    secret: bool | None = None


# --- Natures de lien ------------------------------------------------------

class _RelationTypeBase(_Strict):
    @field_validator("color", check_fields=False)
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not HEX_COLOR.match(v):
            raise ValueError("Couleur attendue au format #RRGGBB")
        return v.lower()


class RelationTypeCreate(_RelationTypeBase):
    label: Annotated[str, Field(min_length=1, max_length=40)]
    color: str = "#8a93a3"
    directed: bool = False
    hint: Annotated[str, Field(max_length=120)] = ""


class RelationTypeUpdate(_RelationTypeBase):
    label: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    color: str | None = None
    directed: bool | None = None
    hint: Annotated[str, Field(max_length=120)] | None = None
    sort_order: int | None = None


# --- Positions et images ---------------------------------------------------

class NodePosition(_Strict):
    kind: Kind
    id: int
    x: Coord
    y: Coord


class ScenarioNodeState(_Strict):
    # added : ajoutée à la main ; excluded : retirée de la carte ; auto : selon les citations.
    state: Literal["auto", "added", "excluded"]
    # Position où placer une fiche ajoutée (facultative).
    x: Coord | None = None
    y: Coord | None = None


class PositionsUpdate(_Strict):
    # Une réorganisation complète renvoie toutes les fiches de la carte.
    nodes: Annotated[list[NodePosition], Field(max_length=20000)]


class ImageUpdate(_Strict):
    caption: Annotated[str, Field(max_length=500)] | None = None
    sort_order: int | None = None


# --- Fiches rédigées (scénarios) ---------------------------------------------

DocStatus = Literal["brouillon", "pret", "en_cours", "joue"]


class DocumentCreate(_Strict):
    title: Annotated[str, Field(max_length=200)] = ""
    parent_id: int | None = None
    # Contenu de départ (modèle) : HTML produit par l'éditeur.
    content: Annotated[str, Field(max_length=2_000_000)] = ""


class VersionCreate(_Strict):
    label: Annotated[str, Field(max_length=120)] = ""
    content: Annotated[str, Field(max_length=4_000_000)] | None = None   # comme le HTML du temps réel


class VersionRestore(_Strict):
    current: Annotated[str, Field(max_length=4_000_000)] | None = None


class VersionUpdate(_Strict):
    label: Annotated[str, Field(max_length=120)]


class TemplateCreate(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(max_length=300)] = ""
    content: Annotated[str, Field(max_length=2_000_000)]


class TemplateUpdate(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    description: Annotated[str, Field(max_length=300)] | None = None


class DocumentUpdate(_Strict):
    title: Annotated[str, Field(max_length=200)] | None = None
    status: DocStatus | None = None
    content: Annotated[str, Field(max_length=2_000_000)] | None = None
    # Révision sur laquelle l'édition est basée : refus (409) si la fiche a changé entre-temps.
    base_revision: int | None = None


class DocumentMove(_Strict):
    # Nouvelle fiche parente et rang parmi ses sous-fiches (0 = en tête ; au-delà de la fin = en dernier).
    parent_id: int
    index: Annotated[int, Field(ge=0, le=100_000)]


class DocumentOrder(_Strict):
    parent_id: int
    ids: Annotated[list[int], Field(max_length=1000)]


class CommentCreate(_Strict):
    body: Annotated[str, Field(min_length=1, max_length=5000)]
    quote: Annotated[str, Field(max_length=500)] = ""
    parent_id: int | None = None

    @field_validator("body")
    @classmethod
    def _body(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Commentaire vide")
        return v


class EntryCommentCreate(_Strict):
    """Commentaire sur une fiche PNJ, faction ou lieu : section visée (vide = la fiche) et extrait."""
    body: Annotated[str, Field(min_length=1, max_length=5000)]
    quote: Annotated[str, Field(max_length=500)] = ""
    field: Annotated[str, Field(max_length=40)] = ""
    parent_id: int | None = None

    @field_validator("body")
    @classmethod
    def _body(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Commentaire vide")
        return v


class EntryVersionCreate(_Strict):
    label: Annotated[str, Field(max_length=120)] = ""


class CommentUpdate(_Strict):
    body: Annotated[str, Field(min_length=1, max_length=5000)] | None = None
    resolved: bool | None = None

    @field_validator("body")
    @classmethod
    def _body(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Commentaire vide")
        return v


# --- Comptes ----------------------------------------------------------------

Username = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{2,40}$")]
Password = Annotated[str, Field(min_length=8, max_length=200)]
UserRole = Literal["admin", "mj"]


class Login(BaseModel):
    username: Annotated[str, Field(max_length=100)]
    password: Annotated[str, Field(max_length=500)]


class Setup(_Strict):
    username: Username
    display_name: Short = ""
    password: Password


class PasswordChange(BaseModel):
    current_password: Annotated[str, Field(max_length=500)]
    new_password: Password


class LocaleChange(_Strict):
    """Langue de l'interface du compte : code de deux ou trois lettres (fr, en…), ou vide pour « Automatique »
    (langue du navigateur, puis MAZE_DEFAULT_LOCALE). Tout code est admis et gardé tel quel (l'interface et le serveur
    peuvent ne pas connaître les mêmes langues) ; une langue sans catalogue est ignorée là où elle sert : par le serveur
    (i18n.account_locale), par l'interface (resolveLocale), qui passent alors à la suite du choix."""
    locale: Annotated[str, Field(pattern=r"^([a-z]{2,3})?$")]


class UserCreate(_Strict):
    username: Username
    display_name: Short = ""
    role: UserRole = "mj"
    password: Password
    campaign_ids: list[int] = []


class UserUpdate(_Strict):
    display_name: Short | None = None
    role: UserRole | None = None
    active: bool | None = None
    password: Password | None = None


class MembersUpdate(_Strict):
    user_ids: Annotated[list[int], Field(max_length=500)]


# --- Connecteur Claude ------------------------------------------------------

class ConnectorGrant(_Strict):
    campaign_id: int
    mode: Literal["none", "read", "write"]


class ConnectorUpdate(_Strict):
    enabled: bool | None = None
    # Liste complète des campagnes ouvertes à Claude (remplace la précédente).
    grants: Annotated[list[ConnectorGrant], Field(max_length=2000)] | None = None
    # Ou une seule campagne (les autres restent telles quelles).
    grant: ConnectorGrant | None = None


# --- Bibliothèque d'images (Administration > Images) ---------------------------

class LibraryUpdate(_Strict):
    caption: Annotated[str, Field(max_length=500)]


class LibraryAssign(_Strict):
    kind: Literal["npc", "faction", "place"]
    entity_id: int
    set_cover: bool = False
    remove: bool = False      # retirer de la bibliothèque une fois copiée


class ImageRef(_Strict):
    """Une image de la campagne, quelle que soit sa sorte (Administration > Images)."""
    source: Literal["library", "gallery", "document", "map"]
    id: int


class ImagesDelete(_Strict):
    """Suppression groupée (Administration > Images) ; doublons ignorés."""
    items: Annotated[list[ImageRef], Field(min_length=1, max_length=limits.IMAGES_DELETE_MAX)]


class ImageCopy(_Strict):
    """Image de la campagne à copier : bibliothèque, galerie d'une fiche, illustration d'une fiche rédigée
    ou fond d'une carte interactive."""
    source: Literal["library", "gallery", "document", "map"]
    id: int
    set_cover: bool = False


# --- Cartes interactives -----------------------------------------------------------------------

MapName = Annotated[str, Field(min_length=1, max_length=120)]
Ratio = Annotated[float, Field(ge=0, le=1)]


class MapCopy(_Strict):
    """Nouvelle carte (ou nouveau fond) depuis une image déjà dans Maze."""
    name: MapName | None = None
    place_id: int | None = None
    source: Literal["library", "gallery", "document", "map"]
    id: int


class MapUpdate(_Strict):
    name: MapName | None = None
    place_id: int | None = None     # null : carte qui ne représente aucun lieu
    pin_style: Literal["dot", "icon", "hidden"] | None = None   # pastille, icône du type, discrète


class PinSet(_Strict):
    x: Ratio
    y: Ratio


class MapOrder(_Strict):
    ids: Annotated[list[int], Field(max_length=500)]


# --- Chronologie (timeline.py, migration 032) ------------------------------------

class _EventBase(_Strict):
    @field_validator("tags", check_fields=False)
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return _clean_tags(v)


class EventCreate(_EventBase):
    title: Name
    date_label: Short = ""
    description: Long = ""   # HTML de l'éditeur, borné comme les champs longs des fiches
    tags: Tags = []
    before_id: int | None = None   # placé juste avant cet événement ; absent ou null : à la fin


class EventUpdate(_EventBase):
    title: Name | None = None
    date_label: Short | None = None
    description: Long | None = None
    tags: Tags | None = None
    base_revision: int | None = None


class EventOrder(_Strict):
    # Ordre complet des événements de la campagne, du premier au dernier.
    ids: Annotated[list[int], Field(max_length=20000)]


# --- Accès de Foundry VTT (Administration > Foundry) ----------------------------

class FoundryTokenCreate(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    campaigns: Annotated[list[int], Field(max_length=2000)] = []


class FoundryTokenUpdate(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    # Liste complète des campagnes lisibles avec ce jeton (remplace la précédente).
    campaigns: Annotated[list[int], Field(max_length=2000)] | None = None
