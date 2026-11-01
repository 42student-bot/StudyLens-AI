"""Client HTTP minimal et injectable.

Le code métier ne dépend que de l'interface `HttpClient` : en production on
utilise `RequestsHttp` (appelé dans des threads), dans les tests un faux client.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class HttpResponse:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return _json.loads(self.body.decode("utf-8"))

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        data: Any = None,
        files: Any = None,
        timeout: float = 30.0,
        max_bytes: int | None = None,
    ) -> HttpResponse: ...


class RequestsHttp:
    """Implémentation via `requests`. Les redirections ne sont jamais suivies
    (protection SSRF : l'hôte vérifié est celui qui est réellement contacté)."""

    def __init__(self) -> None:
        import requests

        self._requests = requests
        self._session = requests.Session()

    def request(self, method, url, *, headers=None, json=None, data=None, files=None, timeout=30.0, max_bytes=None):
        try:
            resp = self._session.request(
                method,
                url,
                headers=dict(headers or {}),
                json=json,
                data=data,
                files=files,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
        except self._requests.RequestException as exc:  # réseau, DNS, timeout
            raise HttpError(f"{type(exc).__name__}", retryable=True) from None
        try:
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise HttpError("réponse trop volumineuse", status=resp.status_code)
                chunks.append(chunk)
        except self._requests.RequestException as exc:
            raise HttpError(f"{type(exc).__name__}", retryable=True) from None
        finally:
            resp.close()
        return HttpResponse(
            status=resp.status_code,
            body=b"".join(chunks),
            headers={k.lower(): v for k, v in resp.headers.items()},
        )
