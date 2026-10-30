import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.http import HttpResponse  # noqa: E402


class FakeHttp:
    """Client HTTP scripté : enregistre les requêtes, répond via des règles."""

    def __init__(self):
        self.requests: list[dict] = []
        self.rules: list = []  # (predicate(method,url), callable -> HttpResponse | Exception)

    def when(self, predicate, response):
        self.rules.append((predicate, response))

    def request(self, method, url, *, headers=None, json=None, timeout=30.0, max_bytes=None):
        self.requests.append({"method": method, "url": url, "headers": dict(headers or {}), "json": json})
        for pred, resp in self.rules:
            if pred(method, url):
                out = resp(method, url, json) if callable(resp) else resp
                if isinstance(out, Exception):
                    raise out
                return out
        return HttpResponse(404, b"")


def make_page_jpeg(sharp: bool = True, size=(1200, 1600), seed: int = 1) -> bytes:
    """Image synthétique : blocs noirs/blancs de 4 px (« texte ») ou flou fort."""
    rng = np.random.default_rng(seed)
    h, w = size[1] // 4, size[0] // 4
    blocks = (rng.random((h, w)) > 0.5).astype(np.uint8) * 255
    arr = np.kron(blocks, np.ones((4, 4), dtype=np.uint8))
    img = Image.fromarray(arr, mode="L").convert("RGB")
    if not sharp:
        img = img.filter(ImageFilter.GaussianBlur(radius=12))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


@pytest.fixture
def sharp_jpeg():
    return make_page_jpeg(True)


@pytest.fixture
def blurry_jpeg():
    return make_page_jpeg(False)
