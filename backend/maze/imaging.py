"""Traitement des images envoyées : validation, redimensionnement, miniature."""

from __future__ import annotations

import ctypes
import math
import ctypes.util
import io
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from .mediafiles import temp_name

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
MAX_SIDE = 1600
MAP_SIDE = 8192          # fonds des cartes interactives : pleine définition (zoom), en JPEG qualité 92 (*)
MAP_PREVIEW_SIDE = 4096  # et version d'affichage en WebP, chargée d'abord (la pleine définition au zoom)
# (*) L'encodeur WebP d'une image de 40 Mpx demande près de 300 Mo de plus que l'image elle-même : la recette
# (service plafonné à 768 Mo) était tuée sur une carte de 8000 × 5333. L'encodeur JPEG travaille par bandes
# (quelques Mo) et 25 fois plus vite. Seule la pleine définition d'un fond plus grand que sa version
# d'affichage est en JPEG ; /media sert donc aussi des .jpg (toujours produits ici, jamais le fichier reçu).
THUMB_SIDE = 256
# Limites vérifiées avant tout décodage (une petite image très compressée peut sinon occuper près d'un Go
# une fois décodée). Les JPEG sont décodés directement à échelle réduite (draft, 1/2 à 1/8) : une carte de
# 200 Mpx tient en quelques dizaines de Mo. Les autres formats sont décodés en pleine taille.
MAX_PIXELS_JPEG = 250_000_000              # JPEG classique : environ 220 Mo au pire
MAX_PIXELS_PROGRESSIVE = 80_000_000        # JPEG progressif (tout le fichier en mémoire) : environ 280 Mo
MAX_PIXELS_FULL_DECODE = 25_000_000
# Fonds de cartes gardés en pleine définition : pixels décodés (après réduction JPEG 1/2 à 1/8) + pixels du
# résultat s'il faut encore réduire, présents ensemble en mémoire (4 octets chacun). Bornés pour tenir sous les
# 768 Mo du service de recette, qui garde lui-même 100 à 180 Mo (recette du 9 octobre : 9000 × 6000, soit
# 99 Mpx de travail, 464 Mo mesurés ; 8940 × 8940, 147 Mpx, 670 Mo, désormais refusé).
MAX_MAP_WORK = 110_000_000
MAX_MAP_WORK_PROGRESSIVE = 96_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS_JPEG  # garde-fou de Pillow (erreur au double), au-delà de nos limites
# Une image décodée à la fois : avec la recette (VM de 4 Go, service plafonné à 768 Mo), trois photos
# de 24 Mpx traitées ensemble faisaient tuer le service ; en file, le pic ne s'additionne plus.
_ONE_AT_A_TIME = threading.Lock()


def _save(img: Image.Image, dest: Path, fmt: str, **params) -> None:
    """Enregistre sous un nom temporaire puis renomme (contre-revue du lot 1) : un fichier sous son nom définitif est
    toujours complet, même après un arrêt brutal ou pour une sauvegarde faite pendant l'écriture."""
    tmp = dest.with_name(temp_name(dest.name))
    try:
        img.save(tmp, fmt, **params)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _copy(source: Path, dest: Path) -> None:
    tmp = dest.with_name(temp_name(dest.name))
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _release_memory() -> None:
    """Rend au système la mémoire libérée par Pillow (sinon le service garde 200 à 350 Mo)."""
    try:
        ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _shrink(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Réduction de qualité Lanczos par bandes horizontales : en mémoire, l'image d'origine, le résultat et une
    bande de quelques Mo (Pillow garde 4 octets par pixel ; une réduction d'un coup passe par une image
    intermédiaire presque aussi grande que l'originale : 580 Mo pour une carte de 9000 × 6000 au lieu de 400).
    Chaque bande est prise avec une marge : le filtre lit les pixels voisins, sans raccord visible."""
    w, h = img.size
    width, height = size
    out = Image.new(img.mode, size)
    scale = h / height
    band = max(1, (16 * 1024 * 1024) // (width * 4))
    margin = int(math.ceil(3 * max(scale, w / width))) + 2
    for y0 in range(0, height, band):
        y1 = min(height, y0 + band)
        sy0, sy1 = y0 * scale, y1 * scale
        top = max(0, int(sy0) - margin)
        bottom = min(h, int(math.ceil(sy1)) + margin)
        region = img.crop((0, top, w, bottom))
        out.paste(region.resize((width, y1 - y0), Image.Resampling.LANCZOS, box=(0, sy0 - top, w, sy1 - top)), (0, y0))
    return out


def _decoded_pixels(size: tuple[int, int], side: int) -> int:
    """Pixels d'un JPEG une fois décodé à échelle réduite (draft : 1/2, 1/4 ou 1/8, en gardant au moins la
    taille finale)."""
    w, h = size
    tw, th = _fitted(size, side) if max(w, h) > side else size
    scale = next(s for s in (8, 4, 2, 1) if s == 1 or (w // s >= tw and h // s >= th))
    return math.ceil(w / scale) * math.ceil(h / scale)


def _map_work(size: tuple[int, int], side: int, fmt: str) -> int:
    """Pixels en mémoire ensemble pour un fond de carte : image décodée, plus le résultat s'il faut la réduire."""
    decoded = _decoded_pixels(size, side) if fmt == "JPEG" else size[0] * size[1]
    if max(size) <= side:
        return decoded
    w, h = _fitted(size, side)
    return decoded + w * h


def _fitted(size: tuple[int, int], side: int) -> tuple[int, int]:
    w, h = size
    ratio = side / max(w, h)
    return max(1, round(w * ratio)), max(1, round(h * ratio))


class ImageError(ValueError):
    pass


@dataclass
class StoredImage:
    file: str
    thumb: str
    width: int
    height: int
    preview: str = ""   # version réduite (cartes interactives) ; vide si l'image n'est pas plus grande


def process_upload(raw: bytes, media_dir: Path, campaign_id: int, max_side: int = MAX_SIDE,
                   preview_side: int | None = None) -> StoredImage:
    """Enregistre l'image en WebP (max 1600 px, ou max_side) et une miniature carrée de 256 px ;
    avec preview_side, aussi une version réduite à ce côté si l'image le dépasse (fonds de cartes)."""
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            pixels = probe.width * probe.height
            if fmt == "JPEG" and pixels > MAX_PIXELS_JPEG:
                raise ImageError("Image trop grande (250 millions de pixels au plus pour un JPEG)")
            if fmt == "JPEG" and probe.info.get("progressive") and pixels > MAX_PIXELS_PROGRESSIVE:
                raise ImageError("Image trop grande pour un JPEG progressif (80 millions de pixels au plus) : "
                                 "enregistrez-la en JPEG classique (non progressif) pour aller jusqu'à 250 millions")
            if fmt != "JPEG" and pixels > MAX_PIXELS_FULL_DECODE:
                raise ImageError("Image trop grande (25 millions de pixels au plus ; enregistrez-la en JPEG "
                                 "pour aller jusqu'à 250 millions)")
            if max_side > MAX_SIDE:
                limit = MAX_MAP_WORK_PROGRESSIVE if probe.info.get("progressive") else MAX_MAP_WORK
                if _map_work(probe.size, max_side, fmt) > limit:
                    raise ImageError(f"Image trop grande pour un fond de carte : ramenez-la à {max_side} px de côté "
                                     "au plus (elle le serait de toute façon), en JPEG de préférence")
            probe.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageError("Fichier image illisible ou non pris en charge") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageError("Formats acceptés : JPEG, PNG, WebP, GIF")

    folder = media_dir / str(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    file_rel = f"{campaign_id}/{stem}.webp"
    thumb_rel = f"{campaign_id}/{stem}_t.webp"
    preview_rel = ""
    # Mémoire : une seule copie de l'image en pleine taille à la fois (pas de copie pour l'orientation ou le
    # mode quand c'est inutile, version d'affichage calculée directement en petit) ; enregistrement compris
    # dans le verrou. Carte de 8000 × 5333 : pic mesuré divisé par deux (682 Mo avant).
    with _ONE_AT_A_TIME:
        try:
            img = Image.open(io.BytesIO(raw))
            # JPEG : décodé à 1/2, 1/4 ou 1/8 si possible. La taille demandée est celle du résultat (et non un
            # carré max_side × max_side, que le petit côté d'une carte panoramique n'atteint jamais : 250 Mpx
            # décodés en entier, 1,5 Go mesurés).
            ratio = min(1.0, max_side / max(img.size))
            img.draft(None, (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
            img.load()
            if img.getexif().get(0x0112, 1) != 1:
                img = ImageOps.exif_transpose(img)
            has_alpha = img.mode in ("RGBA", "LA", "P") and (
                img.mode != "P" or "transparency" in img.info
            )
            mode = "RGBA" if has_alpha else "RGB"
            if img.mode != mode:
                img = img.convert(mode)
            if max(img.size) > max_side:
                img = _shrink(img, _fitted(img.size, max_side))  # l'originale est libérée aussitôt
            preview = None
            if preview_side and max(img.size) > preview_side:
                preview = _shrink(img, _fitted(img.size, preview_side))
                preview_rel = f"{campaign_id}/{stem}_p.webp"
            # Recadrage légèrement remonté : les visages sont souvent dans le tiers supérieur.
            thumb = ImageOps.fit(preview or img, (THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS,
                                 centering=(0.5, 0.35) if max_side == MAX_SIDE else (0.5, 0.5))
            width, height = img.size
            if preview is not None and img.mode == "RGB":
                file_rel = f"{campaign_id}/{stem}.jpg"
                _save(img, media_dir / file_rel, "JPEG", quality=92)
            else:
                _save(img, media_dir / file_rel, "WEBP", quality=85 if max_side == MAX_SIDE else 90, method=4)
            del img   # pleine définition : jusqu'à 200 Mo, rendus avant la suite
            _release_memory()
            if preview is not None:
                _save(preview, media_dir / preview_rel, "WEBP", quality=85, method=4)
            _save(thumb, media_dir / thumb_rel, "WEBP", quality=82, method=4)
        except (OSError, Image.DecompressionBombError) as exc:
            delete_files(media_dir, file_rel, preview_rel, thumb_rel)
            raise ImageError("Impossible de traiter cette image") from exc
        finally:
            img = preview = thumb = None  # noqa: F841 - références lâchées avant de rendre la mémoire
            _release_memory()
    return StoredImage(file=file_rel, thumb=thumb_rel, width=width, height=height, preview=preview_rel)


def copy_stored(media_dir: Path, file: str, thumb: str, campaign_id: int, width: int, height: int,
                preview: str = "") -> StoredImage:
    """Copie à l'identique (sans réencodage) d'une image déjà enregistrée par Maze, sous de nouveaux noms :
    chaque fiche a ses propres fichiers, supprimés avec elle."""
    folder = media_dir / str(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    file_rel = f"{campaign_id}/{stem}{Path(file).suffix or '.webp'}"   # .jpg : pleine définition d'une carte
    thumb_rel = f"{campaign_id}/{stem}_t.webp"
    preview_rel = f"{campaign_id}/{stem}_p.webp" if preview else ""
    try:
        _copy(media_dir / file, media_dir / file_rel)
        _copy(media_dir / thumb, media_dir / thumb_rel)
        if preview:
            _copy(media_dir / preview, media_dir / preview_rel)
    except OSError as exc:
        delete_files(media_dir, file_rel, thumb_rel, preview_rel)
        raise ImageError("Fichier de l'image introuvable sur le serveur") from exc
    return StoredImage(file=file_rel, thumb=thumb_rel, width=width, height=height, preview=preview_rel)


def size_of(path: Path) -> tuple[int, int]:
    """Dimensions d'une image enregistrée (en-tête seulement)."""
    try:
        with Image.open(path) as img:
            return img.size
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageError("Fichier de l'image introuvable sur le serveur") from exc


def delete_files(media_dir: Path, *relative_paths: str) -> None:
    for rel in relative_paths:
        if not rel:
            continue
        path = (media_dir / rel).resolve()
        if media_dir.resolve() in path.parents:
            path.unlink(missing_ok=True)


ICON_SIDE = 128             # icônes des types de lieux : stockées en 128 px, affichées de 13 à 30 px
ICON_MAX_PIXELS = 25_000_000


def _clear_background(img: Image.Image) -> Image.Image:
    """Rend transparent le fond clair uni qui touche les bords (symbole dessiné sur fond blanc). Une image qui
    a déjà de la transparence, ou dont les coins ne sont pas clairs, est laissée telle quelle."""
    w, h = img.size
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    if img.getchannel("A").getextrema()[0] < 255:
        return img
    cleared = img.copy()
    for xy in corners:
        r, g, b, a = cleared.getpixel(xy)
        if min(r, g, b) >= 225 and a == 255:
            ImageDraw.floodfill(cleared, xy, (255, 255, 255, 0), thresh=60)
    # Image presque entièrement claire (symbole pâle, conversion de couleurs ratée) : fond gardé plutôt que vidé.
    visible = cleared.getchannel("A").point(lambda v: 255 if v > 8 else 0).histogram()[255]
    return img if visible < 0.01 * w * h else cleared


def process_icon(raw: bytes, media_dir: Path, campaign_id: int) -> str:
    """Icône d'un type de lieu : fond clair des bords rendu transparent, recadrée sur le symbole, carrée,
    WebP de 128 px avec transparence. Renvoie le chemin relatif du fichier."""
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            if probe.width * probe.height > ICON_MAX_PIXELS:
                raise ImageError("Image trop grande pour une icône (25 millions de pixels au plus)")
            probe.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageError("Fichier image illisible ou non pris en charge") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageError("Formats acceptés : JPEG, PNG, WebP, GIF")
    # Décodage sous le verrou commun ; le reste (remplissage du fond, en Python : lent sur une grande image)
    # sur une image ramenée à 512 px, hors du verrou, pour ne pas retenir les autres traitements d'images.
    with _ONE_AT_A_TIME:
        try:
            img = Image.open(io.BytesIO(raw))
            img.draft(None, (512, 512))
            img = ImageOps.exif_transpose(img).convert("RGBA")
            if max(img.size) > 512:
                img.thumbnail((512, 512), Image.Resampling.LANCZOS)
        except (OSError, Image.DecompressionBombError) as exc:
            raise ImageError("Impossible de traiter cette image") from exc
        finally:
            _release_memory()
    try:
        img = _clear_background(img)
        box = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
        if box is None:
            raise ImageError("Image vide (rien d'autre que le fond)")
        img = img.crop(box)
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        icon = square.resize((ICON_SIDE, ICON_SIDE), Image.Resampling.LANCZOS) if side > ICON_SIDE else square
    except OSError as exc:
        raise ImageError("Impossible de traiter cette image") from exc
    folder = media_dir / str(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    rel = f"{campaign_id}/{uuid.uuid4().hex}_i.webp"
    _save(icon, media_dir / rel, "WEBP", quality=90, method=6)
    return rel
