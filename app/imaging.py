"""Prétraitement des photos : orientation, taille, détection de flou."""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 50_000_000  # protection contre les « decompression bombs »


class ImageError(Exception):
    """Image illisible, trop petite ou invalide."""


@dataclass
class ProcessedImage:
    data: bytes  # JPEG
    width: int
    height: int
    blur_score: float
    is_blurry: bool


def blur_score(img: Image.Image) -> float:
    """Variance du laplacien sur une version réduite en niveaux de gris.

    Plus la valeur est basse, plus l'image est floue. Les photos de feuilles
    très blanches donnent des valeurs naturellement basses : le seuil
    (BLUR_THRESHOLD) doit être calibré avec vos propres photos.
    """
    g = ImageOps.grayscale(img)
    g.thumbnail((1000, 1000))
    a = np.asarray(g, dtype=np.float64)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    lap = (
        -4.0 * a[1:-1, 1:-1]
        + a[:-2, 1:-1]
        + a[2:, 1:-1]
        + a[1:-1, :-2]
        + a[1:-1, 2:]
    )
    return float(lap.var())


def process_image(
    raw: bytes,
    *,
    max_side: int = 2000,
    min_side: int = 300,
    blur_threshold: float = 60.0,
) -> ProcessedImage:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise ImageError("image illisible") from None

    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if min(img.size) < min_side:
        raise ImageError("image trop petite")

    score = blur_score(img)
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return ProcessedImage(
        data=out.getvalue(),
        width=img.width,
        height=img.height,
        blur_score=score,
        is_blurry=score < blur_threshold,
    )
