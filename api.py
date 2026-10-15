"""API HTTP de TopRank para n8n (o cualquier otro sistema).

Arranque:
    python api.py                     # escucha en http://0.0.0.0:8085
    set TOPRANK_DEMO=1 para usar datos de ejemplo sin NetSuite.

Variables de entorno:
    NS_ACCOUNT, NS_CONSUMER_KEY, NS_CONSUMER_SECRET, NS_TOKEN_ID, NS_TOKEN_SECRET   (NetSuite)
    TOPRANK_API_KEY   clave que n8n debe mandar en el header  X-API-Key  (obligatoria)
    TOPRANK_PORT      puerto (default 8085)
    TOPRANK_CACHE_SEG segundos que se reutiliza una respuesta (default 600 = 10 min)

Endpoints (todos GET, todos aceptan ?desde=YYYY-MM-DD&hasta=YYYY-MM-DD&gerencia=ID[,ID],
o bien ?periodo=mes_anterior | mes_actual en lugar de desde/hasta):
    /salud                          sin clave; para comprobar que está arriba
    /asignacion?gerencia=60586&limit=10
                                    lista ordenada y compacta para repartir clientes
    /top-vendedores                 F5 completo (acepta limit)
    /top-gerencias                  F1
    /vendedores-por-gerencia        F2
    /detalle-desarrollo             F3
    /vendedor-detalle               F4

Requisitos:  pip install flask requests requests-oauthlib
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

from flask import Flask, jsonify, request

import toprank as tr

app = Flask(__name__)
app.json.ensure_ascii = False
app.json.sort_keys = False

API_KEY = os.environ.get("TOPRANK_API_KEY", "")
CACHE_SEG = int(os.environ.get("TOPRANK_CACHE_SEG", "600"))
DEMO = os.environ.get("TOPRANK_DEMO") == "1"

_cache: dict[tuple, tuple[float, dict]] = {}
_client = None


def get_ns():
    global _client
    if DEMO:
        from datos_ejemplo import StubNS
        return StubNS()
    if _client is None:
        from ver_resultados import NetSuiteClient
        _client = NetSuiteClient.from_env()
    return _client


class BadRequest(Exception):
    pass


def params():
    def fecha(nombre):
        v = request.args.get(nombre)
        if not v:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise BadRequest(f"'{nombre}' debe tener formato YYYY-MM-DD")

    ger = request.args.get("gerencia")
    try:
        gerencia_ids = [int(x) for x in ger.split(",") if x.strip()] if ger else None
    except ValueError:
        raise BadRequest("'gerencia' debe ser uno o varios ids separados por coma")
    periodo = request.args.get("periodo")
    if periodo:
        if request.args.get("desde") or request.args.get("hasta"):
            raise BadRequest("Usa 'periodo' o 'desde'/'hasta', no los dos")
        primero_mes_actual = tr._today().replace(day=1)
        if periodo == "mes_anterior":
            hasta = primero_mes_actual - timedelta(days=1)
            return hasta.replace(day=1), hasta, gerencia_ids
        if periodo == "mes_actual":
            return None, None, gerencia_ids
        raise BadRequest("'periodo' debe ser 'mes_anterior' o 'mes_actual'")
    return fecha("desde"), fecha("hasta"), gerencia_ids


def run(fn, **extra):
    desde, hasta, gerencia_ids = params()
    key = (fn.__name__, desde, hasta, tuple(gerencia_ids or ()), tuple(sorted(extra.items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SEG:
        return hit[1]
    res = fn(get_ns(), desde, hasta, gerencia_ids=gerencia_ids, **extra)
    _cache[key] = (time.time(), res)
    return res


def limit_arg(default):
    try:
        return int(request.args.get("limit", default))
    except ValueError:
        raise BadRequest("'limit' debe ser un número")


@app.before_request
def check_key():
    if request.path == "/salud":
        return None
    if not API_KEY:
        return jsonify(error="Falta configurar TOPRANK_API_KEY en el servidor"), 500
    if request.headers.get("X-API-Key") != API_KEY:
        return jsonify(error="API key inválida"), 401
    return None


@app.errorhandler(BadRequest)
@app.errorhandler(ValueError)
def bad_request(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(RuntimeError)
def upstream_error(e):
    return jsonify(error=str(e)), 502


@app.get("/salud")
def salud():
    return jsonify(ok=True, demo=DEMO)


@app.get("/asignacion")
def asignacion():
    """Lista compacta para repartir clientes: un item por vendedor, ya ordenado (rank 1 = mejor)."""
    res = run(tr.top_ranking_vendedores, limit=limit_arg(15))
    return jsonify(
        periodo=res["period"]["label"],
        gerencia=res["gerencia_label"],
        total=len(res["vendedores"]),
        vendedores=[{
            "rank": v["rank"],
            "vendedor_id": v["vendedor_id"],
            "vendedor": v["vendedor"],
            "gerencia_id": v["gerencia_id"],
            "gerencia": tr.GERENCIAS.get(v["gerencia_id"], v["gerencia"]),
            "num_ventas": v["num_ventas"],
            "valor_colocado": v["valor_colocado"],
        } for v in res["vendedores"]],
    )


@app.get("/top-vendedores")
def top_vendedores():
    return jsonify(run(tr.top_ranking_vendedores, limit=limit_arg(15)))


@app.get("/top-gerencias")
def top_gerencias():
    return jsonify(run(tr.top_rank_por_gerencia))


@app.get("/vendedores-por-gerencia")
def vendedores_por_gerencia():
    return jsonify(run(tr.vendedores_por_gerencia))


@app.get("/detalle-desarrollo")
def detalle_desarrollo():
    return jsonify(run(tr.detalle_vendedor_por_desarrollo))


@app.get("/vendedor-detalle")
def vendedor_detalle():
    return jsonify(run(tr.vendedor_a_detalle))


if __name__ == "__main__":
    port = int(os.environ.get("TOPRANK_PORT", "8085"))
    print(f"TopRank API en http://0.0.0.0:{port}  (demo={DEMO}, cache={CACHE_SEG}s)")
    app.run(host="0.0.0.0", port=port)
