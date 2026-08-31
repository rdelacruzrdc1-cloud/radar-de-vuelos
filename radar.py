"""Consulta precios de Travelpayouts y guarda observaciones en SQLite.

El módulo no realiza llamadas al importar. La consulta se ejecuta únicamente
desde ``fetch_and_store`` o desde la interfaz de línea de comandos.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DB_PATH = ROOT / "radar.db"
PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
REQUIRED_OFFER_FIELDS = ("price", "airline", "departure_at", "transfers")


class RadarError(Exception):
    """Error controlado del proyecto."""


class ConfigurationError(RadarError):
    """Falta una configuración local obligatoria."""


class APIResponseError(RadarError):
    """La respuesta no cumple el contrato validado."""


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Lee un archivo .env sencillo sin imprimir ninguno de sus valores."""

    if not path.is_file():
        raise ConfigurationError(f"No existe el archivo de configuración: {path.name}")

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def require_env(name: str, values: dict[str, str] | None = None) -> str:
    """Obtiene una variable obligatoria sin revelar su contenido."""

    values = load_env() if values is None else values
    value = values.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Falta la variable {name} en .env")
    return value


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: Path = DB_PATH) -> None:
    """Crea las tablas requeridas si todavía no existen."""

    with get_connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS rutas (
                id INTEGER PRIMARY KEY,
                origen TEXT NOT NULL,
                destino TEXT NOT NULL,
                activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
                precio_objetivo REAL,
                UNIQUE (origen, destino)
            );

            CREATE TABLE IF NOT EXISTS precios (
                id INTEGER PRIMARY KEY,
                ruta_id INTEGER NOT NULL,
                precio REAL NOT NULL,
                moneda TEXT NOT NULL,
                aerolinea TEXT,
                fecha_vuelo TEXT,
                fecha_consulta TEXT NOT NULL,
                FOREIGN KEY (ruta_id) REFERENCES rutas (id) ON DELETE CASCADE,
                UNIQUE (ruta_id, fecha_consulta)
            );
            """
        )


def _validate_iata(code: str) -> str:
    normalized = code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2,3}", normalized):
        raise RadarError(f"Código IATA inválido: {code!r}")
    return normalized


def add_route(
    origin: str,
    destination: str,
    target_price: float | None = None,
    path: Path = DB_PATH,
) -> int:
    """Crea una ruta o actualiza su objetivo y la activa."""

    origin = _validate_iata(origin)
    destination = _validate_iata(destination)
    init_db(path)
    with get_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO rutas (origen, destino, activa, precio_objetivo)
            VALUES (?, ?, 1, ?)
            ON CONFLICT (origen, destino) DO UPDATE SET
                activa = 1,
                precio_objetivo = COALESCE(excluded.precio_objetivo, rutas.precio_objetivo)
            """,
            (origin, destination, target_price),
        )
        row = connection.execute(
            "SELECT id FROM rutas WHERE origen = ? AND destino = ?",
            (origin, destination),
        ).fetchone()
    if row is None:
        raise RadarError("No se pudo crear la ruta")
    return int(row["id"])


def get_route_target(route_id: int, path: Path = DB_PATH) -> float | None:
    """Devuelve el precio objetivo de una ruta, si está configurado."""

    init_db(path)
    with get_connection(path) as connection:
        row = connection.execute(
            "SELECT precio_objetivo FROM rutas WHERE id = ?",
            (route_id,),
        ).fetchone()
    if row is None or row["precio_objetivo"] is None:
        return None
    return float(row["precio_objetivo"])


def fetch_prices(
    origin: str,
    destination: str,
    *,
    currency: str = "USD",
    departure_at: str | None = None,
    return_at: str | None = None,
    one_way: bool = True,
    direct: bool = False,
    limit: int = 30,
    page: int = 1,
    timeout: int = 30,
) -> dict[str, Any]:
    """Consulta y valida el contrato de prices_for_dates."""

    token = require_env("FLIGHT_API_TOKEN")
    origin = _validate_iata(origin)
    destination = _validate_iata(destination)
    if limit < 1 or limit > 1000:
        raise RadarError("limit debe estar entre 1 y 1000")
    if page < 1:
        raise RadarError("page debe ser mayor que cero")

    params: dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "currency": currency.upper(),
        "one_way": str(one_way).lower(),
        "direct": str(direct).lower(),
        "sorting": "price",
        "limit": limit,
        "page": page,
    }
    if departure_at:
        params["departure_at"] = departure_at
    if return_at and not one_way:
        params["return_at"] = return_at

    try:
        response = requests.get(
            PRICES_URL,
            params=params,
            headers={
                "X-Access-Token": token,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RadarError(f"Error HTTP al consultar Travelpayouts: {exc}") from exc
    except ValueError as exc:
        raise APIResponseError("Travelpayouts no devolvió JSON válido") from exc

    if not isinstance(payload, dict):
        raise APIResponseError("El JSON raíz no es un objeto")
    if payload.get("success") is not True:
        raise APIResponseError("La API indicó success=false")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise APIResponseError("La respuesta no contiene ofertas en data[]")
    if not isinstance(payload.get("currency"), str):
        raise APIResponseError("Falta currency en la respuesta")

    for index, offer in enumerate(data):
        if not isinstance(offer, dict):
            raise APIResponseError(f"data[{index}] no es un objeto")
        missing = [field for field in REQUIRED_OFFER_FIELDS if field not in offer]
        if missing:
            raise APIResponseError(
                f"Faltan campos en data[{index}]: {', '.join(missing)}"
            )
    return payload


def cheapest_offer(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise APIResponseError("No hay ofertas para seleccionar")
    try:
        return min(data, key=lambda offer: float(offer["price"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise APIResponseError("price no es numérico en la respuesta") from exc


def save_observation(
    route_id: int,
    offer: dict[str, Any],
    currency: str,
    *,
    consultation_date: str | None = None,
    path: Path = DB_PATH,
) -> bool:
    """Guarda una sola observación por ruta y fecha de consulta."""

    consultation_date = consultation_date or datetime.now(timezone.utc).date().isoformat()
    try:
        price = float(offer["price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise APIResponseError("La oferta no tiene un price válido") from exc
    with get_connection(path) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO precios
                (ruta_id, precio, moneda, aerolinea, fecha_vuelo, fecha_consulta)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                route_id,
                price,
                currency.upper(),
                str(offer.get("airline", "")),
                str(offer.get("departure_at", "")),
                consultation_date,
            ),
        )
    return cursor.rowcount == 1


def fetch_and_store(
    origin: str,
    destination: str,
    *,
    target_price: float | None = None,
    currency: str = "USD",
    departure_at: str | None = None,
    return_at: str | None = None,
    one_way: bool = True,
    direct: bool = False,
    path: Path = DB_PATH,
) -> dict[str, Any]:
    """Consulta una ruta, elige la oferta más barata y guarda una observación."""

    route_id = add_route(origin, destination, target_price, path)
    payload = fetch_prices(
        origin,
        destination,
        currency=currency,
        departure_at=departure_at,
        return_at=return_at,
        one_way=one_way,
        direct=direct,
    )
    offer = cheapest_offer(payload)
    inserted = save_observation(route_id, offer, payload["currency"], path=path)
    return {
        "route_id": route_id,
        "origin": _validate_iata(origin),
        "destination": _validate_iata(destination),
        "currency": payload["currency"],
        "price": offer["price"],
        "airline": offer["airline"],
        "departure_at": offer["departure_at"],
        "transfers": offer["transfers"],
        "saved": inserted,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consulta y guarda precios de vuelos")
    parser.add_argument("origin", help="IATA de origen, por ejemplo LIM")
    parser.add_argument("destination", help="IATA de destino, por ejemplo CUZ")
    parser.add_argument("--target", type=float, default=None, help="Precio objetivo")
    parser.add_argument("--currency", default="USD", help="Moneda (por defecto USD)")
    parser.add_argument("--departure-at", default=None, help="Fecha YYYY-MM o YYYY-MM-DD")
    parser.add_argument("--return-at", default=None, help="Fecha de regreso")
    parser.add_argument("--round-trip", action="store_true", help="Solicitar ida y vuelta")
    parser.add_argument("--direct", action="store_true", help="Solicitar solo vuelos directos")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = fetch_and_store(
            args.origin,
            args.destination,
            target_price=args.target,
            currency=args.currency,
            departure_at=args.departure_at,
            return_at=args.return_at,
            one_way=not args.round_trip,
            direct=args.direct,
        )
    except RadarError as exc:
        print(f"RADAR_ERROR: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
