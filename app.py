"""Servidor HTTP mínimo para consultar los datos guardados del radar."""

from __future__ import annotations

import argparse
import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from radar import DB_PATH, get_connection, init_db


INDEX_PATH = Path(__file__).resolve().parent / "index.html"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def list_routes(path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(path)
    with get_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT id, origen, destino, activa, precio_objetivo
            FROM rutas
            ORDER BY id
            """
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "origen": row["origen"],
            "destino": row["destino"],
            "activa": bool(row["activa"]),
            "precio_objetivo": row["precio_objetivo"],
        }
        for row in rows
    ]


def list_prices(
    path: Path = DB_PATH,
    *,
    route_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db(path)
    limit = max(1, min(limit, 1000))
    query = """
        SELECT id, ruta_id, precio, moneda, aerolinea, fecha_vuelo, fecha_consulta
        FROM precios
    """
    parameters: tuple[Any, ...]
    if route_id is None:
        query += " ORDER BY fecha_consulta DESC, id DESC LIMIT ?"
        parameters = (limit,)
    else:
        query += " WHERE ruta_id = ? ORDER BY fecha_consulta DESC, id DESC LIMIT ?"
        parameters = (route_id, limit)
    with get_connection(path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


class RadarHandler(BaseHTTPRequestHandler):
    """Expone solo lecturas de la base local."""

    server_version = "RadarDeVuelos/0.1"

    def _send_json(self, status: HTTPStatus, value: Any) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        if not INDEX_PATH.is_file():
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "No existe index.html"})
            return
        body = INDEX_PATH.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - nombre exigido por http.server
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._send_index()
            elif parsed.path == "/api/rutas":
                self._send_json(HTTPStatus.OK, {"data": list_routes()})
            elif parsed.path == "/api/precios":
                route_id = None
                if query.get("route_id"):
                    route_id = int(query["route_id"][0])
                limit = int(query.get("limit", [100])[0])
                self._send_json(
                    HTTPStatus.OK,
                    {"data": list_prices(route_id=route_id, limit=limit)},
                )
            else:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Recurso no encontrado"},
                )
        except (ValueError, sqlite3.Error) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[app] {self.address_string()} - {format % args}")


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    init_db()
    server = ThreadingHTTPServer((host, port), RadarHandler)
    print(f"Radar de Vuelos disponible en http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Servidor local del Radar de Vuelos")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
