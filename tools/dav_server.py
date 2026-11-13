"""Локальный WebDAV-сервер для проверки синхронизации (wsgidav + cheroot).

Запуск для ручной проверки:

    python tools/dav_server.py --root .davserver --port 8099 --user user --password secret

Затем в ``.env``:

    WEBDAV_URL=http://127.0.0.1:8099/
    WEBDAV_USERNAME=user
    WEBDAV_PASSWORD=secret

Сервер работает, пока не нажмёте Ctrl+C. Второй экземпляр приложения с другим
каталогом данных (``PROMPTMANAGER_DATA``) можно направить на тот же сервер.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("davserver")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _config(root: Path, user: str, password: str, port: int) -> dict[str, Any]:
    return {
        "host": "127.0.0.1",
        "port": port,
        "provider_mapping": {"/": str(root)},
        "http_authenticator": {
            "domain_controller": None,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        "simple_dc": {"user_mapping": {"*": {user: {"password": password}}}},
        "verbose": 0,
        "logging": {"enable_loggers": []},
        "property_manager": True,
        "lock_storage": True,
    }


def create_server(root: Path, port: int, user: str, password: str):
    """Поднимает сервер в фоновом потоке и возвращает объект cheroot-сервера."""
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp

    root.mkdir(parents=True, exist_ok=True)
    app = WsgiDAVApp(_config(root, user, password, port))
    server = wsgi.Server(("127.0.0.1", port), app)
    server.prepare()
    thread = threading.Thread(target=server.serve, daemon=True, name="dav-server")
    thread.start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Локальный WebDAV для Prompt Manager")
    parser.add_argument("--root", default=".davserver", help="каталог, отдаваемый по WebDAV")
    parser.add_argument("--port", type=int, default=0, help="порт (0 — свободный)")
    parser.add_argument("--user", default="user")
    parser.add_argument("--password", default="secret")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    port = args.port or free_port()
    root = Path(args.root).resolve()
    server = create_server(root, port, args.user, args.password)
    logger.info("WebDAV: http://127.0.0.1:%s/ (каталог %s)", port, root)
    logger.info("Логин: %s / %s", args.user, args.password)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Остановка…")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
