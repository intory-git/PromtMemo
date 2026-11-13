"""Тонкий WebDAV-клиент на ``requests``.

Почему не webdavclient3: он тянет lxml и не даёт условный PUT (``If-Match``),
а без него безопасное разрешение конфликтов невозможно. Здесь нужны ровно
PROPFIND/PUT/GET/MKCOL/MOVE с ETag и Basic/Digest-аутентификацией.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

logger = logging.getLogger(__name__)

DAV = "{DAV:}"
PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getetag/><d:getcontentlength/><d:getlastmodified/>"
    "</d:prop></d:propfind>"
)


class WebDAVError(Exception):
    """Общая ошибка протокола или сети."""


class WebDAVAuthError(WebDAVError):
    """Сервер отклонил учётные данные."""


class WebDAVHttpError(WebDAVError):
    """Неожиданный HTTP-статус."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RemoteEntry:
    path: str
    is_dir: bool
    etag: str | None = None
    size: int = 0
    modified: str | None = None


def _strip_etag(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip('"') or None


class WebDAVClient:
    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise WebDAVError("Не задан адрес WebDAV")
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PromptManager/0.1"})
        self._auth: requests.auth.AuthBase | None = (
            HTTPBasicAuth(username, password) if username else None
        )
        self._digest: HTTPDigestAuth | None = None
        self._base_path = unquote(urlsplit(self.base_url).path).rstrip("/")

    # --- HTTP ------------------------------------------------------------

    def _url(self, path: str) -> str:
        cleaned = (path or "").strip()
        if not cleaned:
            return self.base_url
        return self.base_url + quote(cleaned, safe="/-_.~")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify_ssl)
        kwargs.setdefault("allow_redirects", True)

        response = self.session.request(method, url, auth=self._digest or self._auth, **kwargs)
        if response.status_code == 401 and self.username and self._digest is None:
            # Сервер мог требовать Digest: пробуем один раз с ним.
            self._digest = HTTPDigestAuth(self.username, self.password)
            response = self.session.request(method, url, auth=self._digest, **kwargs)
            if response.status_code == 401:
                raise WebDAVAuthError("WebDAV отклонил логин или пароль")
        return response

    @staticmethod
    def _fail(method: str, path: str, response: requests.Response) -> WebDAVHttpError:
        return WebDAVHttpError(
            f"{method} {path or '/'}: HTTP {response.status_code}", response.status_code
        )

    # --- операции --------------------------------------------------------

    def list(self, path: str = "") -> list[RemoteEntry]:
        """PROPFIND Depth: 1 — содержимое каталога (без самого каталога)."""
        response = self._request(
            "PROPFIND",
            path,
            data=PROPFIND_BODY.encode("utf-8"),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        if response.status_code == 404:
            return []
        if response.status_code not in (200, 207):
            raise self._fail("PROPFIND", path, response)
        return self._parse_multistatus(response.content, path)

    def get(self, path: str) -> bytes:
        response = self._request("GET", path)
        if response.status_code != 200:
            raise self._fail("GET", path, response)
        return response.content

    def put(
        self,
        path: str,
        data: bytes,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str | None:
        headers = {"Content-Type": "application/octet-stream"}
        if if_match:
            headers["If-Match"] = f'"{if_match}"'
        elif if_none_match:
            headers["If-None-Match"] = "*"
        response = self._request("PUT", path, data=data, headers=headers)
        if response.status_code not in (200, 201, 204):
            raise self._fail("PUT", path, response)
        return _strip_etag(response.headers.get("ETag"))

    def mkcol(self, path: str) -> None:
        response = self._request("MKCOL", path)
        if response.status_code in (201, 204, 405):
            return
        raise self._fail("MKCOL", path, response)

    def ensure_dir(self, path: str) -> None:
        current = ""
        for part in [item for item in str(path or "").split("/") if item]:
            current = f"{current}/{part}" if current else part
            self.mkcol(current)

    def move(self, source: str, destination: str) -> None:
        response = self._request(
            "MOVE",
            source,
            headers={"Destination": self._url(destination), "Overwrite": "T"},
        )
        if response.status_code not in (201, 204):
            raise self._fail("MOVE", source, response)

    def walk(self) -> dict[str, RemoteEntry]:
        """Рекурсивный обход; ``_trash/`` (наш удалённый архив) пропускается."""
        found: dict[str, RemoteEntry] = {}
        pending = [""]
        while pending:
            folder = pending.pop()
            try:
                entries = self.list(folder)
            except WebDAVHttpError as exc:
                if exc.status == 404:
                    continue
                raise
            for entry in entries:
                if entry.is_dir:
                    if entry.path == "_trash" or entry.path.startswith("_trash/"):
                        continue
                    pending.append(entry.path)
                else:
                    found[entry.path] = entry
        return found

    # --- разбор ----------------------------------------------------------

    def _relative(self, href: str) -> str | None:
        path = unquote(urlsplit(href).path)
        if self._base_path and path.startswith(self._base_path):
            path = path[len(self._base_path) :]
        path = path.strip("/")
        return path or None

    def _parse_multistatus(self, payload: bytes, requested: str) -> list[RemoteEntry]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise WebDAVError(f"Некорректный XML в ответе PROPFIND: {exc}") from exc

        requested_path = str(requested or "").strip("/")
        entries: list[RemoteEntry] = []

        for item in root.findall(f".//{DAV}response"):
            href_element = item.find(f"{DAV}href")
            if href_element is None or not href_element.text:
                continue
            relative = self._relative(href_element.text.strip())
            if relative is None or relative == requested_path:
                continue

            props: dict[str, ET.Element] = {}
            for propstat in item.findall(f"{DAV}propstat"):
                prop = propstat.find(f"{DAV}prop")
                if prop is None:
                    continue
                for child in prop:
                    props[child.tag] = child

            resource_type = props.get(f"{DAV}resourcetype")
            is_dir = resource_type is not None and resource_type.find(f"{DAV}collection") is not None

            etag_element = props.get(f"{DAV}getetag")
            size_element = props.get(f"{DAV}getcontentlength")
            modified_element = props.get(f"{DAV}getlastmodified")

            size = 0
            if size_element is not None and size_element.text:
                try:
                    size = int(size_element.text.strip())
                except ValueError:
                    size = 0

            entries.append(
                RemoteEntry(
                    path=relative,
                    is_dir=is_dir,
                    etag=_strip_etag(etag_element.text if etag_element is not None else None),
                    size=size,
                    modified=(modified_element.text.strip() if modified_element is not None and modified_element.text else None),
                )
            )
        return entries
