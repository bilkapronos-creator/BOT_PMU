"""
Résilience réseau : timeouts, réessais et erreurs typées pour PMU / Supabase.
"""

from __future__ import annotations

import os
import time
from typing import Callable, TypeVar

import httpx
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

T = TypeVar("T")

HTTP_TIMEOUT_SECONDS = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "35"))
HTTP_MAX_RETRIES = int(os.environ.get("HTTP_MAX_RETRIES", "3"))
HTTP_RETRY_BACKOFF = float(os.environ.get("HTTP_RETRY_BACKOFF", "0.6"))

RETRYABLE_REQUESTS_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

RETRYABLE_HTTPX_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    ConnectionResetError,
    ConnectionError,
    BrokenPipeError,
    OSError,
)


class ErreurReseauExterne(Exception):
    """Échec réseau vers un service externe (PMU, Supabase…) après réessais."""


class ArchivesStorageError(ErreurReseauExterne):
    """Échec persistance archives Supabase après réessais."""


_pmu_session: requests.Session | None = None


def _creer_session_pmu() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=HTTP_MAX_RETRIES,
        connect=HTTP_MAX_RETRIES,
        read=HTTP_MAX_RETRIES,
        status=HTTP_MAX_RETRIES,
        backoff_factor=HTTP_RETRY_BACKOFF,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def pmu_get(url: str, headers: dict | None = None) -> requests.Response:
    """
    GET PMU avec session persistante, timeout long et réessais urllib3.
    Lève ErreurReseauExterne si la connexion échoue encore après réessais.
    """
    global _pmu_session
    if _pmu_session is None:
        _pmu_session = _creer_session_pmu()

    headers = headers or {}
    derniere_erreur: Exception | None = None

    for tentative in range(HTTP_MAX_RETRIES):
        try:
            return _pmu_session.get(
                url,
                headers=headers,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except RETRYABLE_REQUESTS_EXCEPTIONS as exc:
            derniere_erreur = exc
            if tentative < HTTP_MAX_RETRIES - 1:
                time.sleep(HTTP_RETRY_BACKOFF * (2**tentative))

    raise ErreurReseauExterne(
        f"API PMU inaccessible après {HTTP_MAX_RETRIES} tentatives : {derniere_erreur}",
    ) from derniere_erreur


def supabase_execute(
    operation: Callable[[], T],
    *,
    description: str = "requête Supabase",
) -> T:
    """Exécute un appel Supabase (.execute()) avec réessais sur erreurs réseau httpx."""
    derniere_erreur: Exception | None = None

    for tentative in range(HTTP_MAX_RETRIES):
        try:
            return operation()
        except RETRYABLE_HTTPX_EXCEPTIONS as exc:
            derniere_erreur = exc
            if tentative < HTTP_MAX_RETRIES - 1:
                time.sleep(HTTP_RETRY_BACKOFF * (2**tentative))

    raise ArchivesStorageError(
        f"{description} impossible après {HTTP_MAX_RETRIES} tentatives : {derniere_erreur}",
    ) from derniere_erreur


def options_client_supabase():
    """Timeout httpx plus long pour le client Supabase Python."""
    try:
        from supabase import ClientOptions
    except ImportError:
        return None

    try:
        client_http = httpx.Client(
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS, connect=15.0),
        )
        return ClientOptions(
            postgrest_client_timeout=int(HTTP_TIMEOUT_SECONDS),
            storage_client_timeout=int(HTTP_TIMEOUT_SECONDS),
            httpx_client=client_http,
        )
    except Exception:
        return ClientOptions(
            postgrest_client_timeout=int(HTTP_TIMEOUT_SECONDS),
            storage_client_timeout=int(HTTP_TIMEOUT_SECONDS),
        )
