"""TopRank Sales — funciones independientes (F1–F5) sobre NetSuite SuiteQL.

Reproducen los dashboards de "TopRank Sales" de Vive La Baja (cuenta NetSuite 9628682)
sin FastAPI, caché, sesión, SQLite ni estado global mutable. Cada función recibe un
cliente SuiteQL (`ns`) y parámetros, y devuelve un dict JSON-serializable.

    ns.query(sql: str, label: str) -> list[dict]   # SuiteQL paginado; columnas en minúsculas

Firma estándar:
    fn(ns, start=None, end=None, gerencia_ids=None, labels=None) -> dict
    - start/end: rango INCLUSIVO (date). Ambos None = mes en curso (F2: día 1 .. hoy).
    - gerencia_ids: None = todas las gerencias activas; lista = solo esas (el alcance por rol
      lo resuelve quien llama). Una lista vacía o un id no activo lanzan ValueError.
    - labels: {gerencia_id: "Responsable - Sucursal"}; si falta: "Gerencia - <Sucursal>".

Correcciones respecto al documento original (no cambian el filtro base ni las métricas):
    1. Q-F1 usa el alias `eg` y admite filtro de gerencia (antes usaba `e` y no filtraba).
    2. Valor ingresado: el divisor del billing schedule va con NULLIF(...,0) y el resultado
       con NVL(...,0) -> nunca divide entre cero ni devuelve NULL.
    3. Las metas (Q-M1, Q-M2) se filtran por las gerencias en alcance.
    4. expected_pct generalizado: días transcurridos (incl. hoy) / días del rango.
    5. Defensa en Python: se descartan filas de gerencias fuera de alcance.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

# ---------------------------------------------------------------------------
# Constantes de negocio
# ---------------------------------------------------------------------------

GERENCIAS: dict[int, str] = {  # employee.id -> sucursal
    60594: "Campestre",
    55556: "Costa 2",
    60586: "Costa 1",
    45956: "Satelite",
    34823: "Urbano",
}
GERENCIAS_ACTIVAS_IDS: tuple[int, ...] = tuple(GERENCIAS)
LOCATIONS_EXCLUIR: tuple[int, ...] = (248, 282, 286)  # Andares SF Casas, Habittad, Habittad-Apartado
FASE_CERRADO_GANADO = 2
TIPO_ORDEN_VALIDA = 1
TREND_TOLERANCE = 0.8
MAX_RANGO_DIAS = 730
TOP_PODIO = 5

MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre")

# Etiquetas de las queries (las recibe ns.query; útiles para logs y para los stubs de test)
LBL_METAS_GERENCIA = "metas_gerencia"            # Q-M1
LBL_METAS_VENDEDOR = "metas_vendedor"            # Q-M2
LBL_VENTAS_GERENCIA = "ventas_gerencia"          # Q-F1
LBL_VENTAS_GER_VEND = "ventas_gerencia_vendedor"  # Q-F2
LBL_VENTAS_VENDEDOR = "ventas_vendedor"          # Q-F3
LBL_DETALLE_SO = "detalle_so"                    # Q-F4


class SuiteQLClient(Protocol):
    def query(self, sql: str, label: str) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Periodo
# ---------------------------------------------------------------------------

def _today() -> date:
    """Punto único para la fecha actual (los tests lo sustituyen)."""
    return date.today()


def _last_day(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


@dataclass(frozen=True)
class Period:
    start: date                          # inclusivo
    end: date                            # inclusivo
    months: tuple[tuple[int, int], ...]  # (anio, mes) cubiertos

    @property
    def start_iso(self) -> str:
        return self.start.isoformat()

    @property
    def end_excl_iso(self) -> str:
        return (self.end + timedelta(days=1)).isoformat()

    @property
    def label(self) -> str:
        if self.start.day == 1 and self.end == _last_day(self.start):
            return f"{MESES[self.start.month - 1]} {self.start.year}"
        return f"{self.start:%d/%m/%Y} - {self.end:%d/%m/%Y}"

    def as_dict(self, include_months: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {"start": self.start_iso, "end": self.end.isoformat(), "label": self.label}
        if include_months:
            d["months"] = len(self.months)
        return d


def _as_date(x: date | datetime | None) -> date | None:
    return x.date() if isinstance(x, datetime) else x


def resolve_period(start: date | None, end: date | None, *, default_end: str = "month",
                   max_days: int | None = None) -> Period:
    """Valida el rango inclusivo y calcula los meses que cubre.

    default_end: "month" -> sin fechas = mes en curso completo; "today" -> día 1 .. hoy.
    """
    start, end = _as_date(start), _as_date(end)
    if (start is None) != (end is None):
        raise ValueError("Se requieren ambas fechas (start y end) o ninguna.")
    if start is None:
        today = _today()
        start = today.replace(day=1)
        end = today if default_end == "today" else _last_day(today)
    assert end is not None
    if end < start:
        raise ValueError("end no puede ser anterior a start.")
    if max_days is not None and (end - start).days > max_days:
        raise ValueError(f"El rango no puede exceder {max_days} días.")
    months: list[tuple[int, int]] = []
    cur = start.replace(day=1)
    while cur <= end:
        months.append((cur.year, cur.month))
        cur = _last_day(cur) + timedelta(days=1)
    return Period(start, end, tuple(months))


def expected_pct(period: Period) -> float:
    """Ritmo esperado: días transcurridos (incl. hoy) / días del rango * 100.
    Si hoy no cae dentro del rango -> 100."""
    today = _today()
    if not (period.start <= today <= period.end):
        return 100.0
    total = (period.end - period.start).days + 1
    return ((today - period.start).days + 1) / total * 100


# ---------------------------------------------------------------------------
# Helpers generales
# ---------------------------------------------------------------------------

def _i(x: Any) -> int:
    return int(float(x or 0))


def _f(x: Any) -> float:
    return float(x or 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scope(gerencia_ids: Iterable[int] | None) -> tuple[list[int], list[int] | None]:
    """-> (gerencias en alcance, filtro SQL o None). Valida y castea con int()."""
    if gerencia_ids is None:
        return list(GERENCIAS_ACTIVAS_IDS), None
    ids: list[int] = []
    for g in gerencia_ids:
        gid = int(g)
        if gid not in GERENCIAS:
            raise ValueError(f"La gerencia {gid} no es una gerencia activa.")
        if gid not in ids:
            ids.append(gid)
    if not ids:
        raise ValueError("gerencia_ids vacío: usa None para todas las gerencias.")
    return ids, ids


def _label(gid: int, labels: dict[int, str] | None) -> str:
    return (labels or {}).get(gid) or f"Gerencia - {GERENCIAS.get(gid, gid)}"


def _gerencia_label(gids: list[int], gfilter: list[int] | None, labels: dict[int, str] | None) -> str:
    if gfilter is None:
        return "Todas las gerencias"
    return ", ".join(_label(g, labels) for g in gids)


def _available_gerencias(labels: dict[int, str] | None) -> list[dict[str, Any]]:
    return [{"id": g, "label": _label(g, labels)} for g in GERENCIAS_ACTIVAS_IDS]


def meta_has_value(r: dict[str, Any]) -> bool:
    return (_i(r.get("meta_ventas")) > 0 or _f(r.get("meta_colocado")) > 0
            or _f(r.get("meta_ingresado")) > 0)


def _pct_int(valor: float, meta: float) -> int:
    """F1/F3: round() entero; 0 si no hay meta."""
    return round(valor / meta * 100) if meta > 0 else 0


def _pct_int_or_none(valor: float, meta: float) -> int | None:
    """F2: round() entero; None si no hay meta."""
    return round(valor / meta * 100) if meta > 0 else None


def _pct_1d_or_none(valor: float, meta: float) -> float | None:
    """F4: 1 decimal; None si no hay meta."""
    return round(valor / meta * 100, 1) if meta > 0 else None


def _trend(pct: float, threshold: float) -> str:
    return "up" if pct >= threshold else "down"


def _rank(rows: list[dict[str, Any]], value_key: str, fields: dict[str, str],
          trend_key: str | None = None) -> list[dict[str, Any]]:
    """Ordena desc por value_key (estable) y agrega percent_of_leader."""
    ordered = sorted(rows, key=lambda r: -r[value_key])
    leader = ordered[0][value_key] if ordered else 0
    items = []
    for r in ordered:
        item = {out: r[src] for out, src in fields.items()}
        item["value"] = r[value_key]
        item["trend"] = r[trend_key] if trend_key else None
        item["percent_of_leader"] = round(r[value_key] / leader * 100, 1) if leader > 0 else 0.0
        items.append(item)
    return items


def _clean(obj: Any) -> Any:
    """Redondea floats a 2 decimales (quita ruido de punto flotante) recursivamente."""
    if isinstance(obj, float):
        return round(obj, 2)
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _name(raw: Any, fallback: str) -> str:
    return (str(raw).strip() if raw else "") or fallback


# ---------------------------------------------------------------------------
# SQL (plantillas SuiteQL — sección 3 del prompt maestro)
# ---------------------------------------------------------------------------

def _ids_sql(ids: Iterable[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


ACTIVAS_SQL = _ids_sql(GERENCIAS_ACTIVAS_IDS)
EXCLUIR_SQL = _ids_sql(LOCATIONS_EXCLUIR)

# Q-ING (corregida: sin división entre cero ni NULL silencioso)
ING_EXPR = r"""CASE
         WHEN UPPER(bs.name) LIKE 'CONTADO%'        THEN NVL(t.custbody_vlb_so_lot_value, 0)
         WHEN NVL(t.custbodyenganche_total, 0) = 0  THEN NVL(NVL(t.custbody_vlb_so_lot_value, 0)
              / NULLIF(TO_NUMBER(REGEXP_SUBSTR(bs.name, '^\d+')), 0), 0)
         ELSE t.custbodyenganche_total
       END"""


def periodos_clause(months: Iterable[tuple[int, int]], alias: str) -> str:
    return " OR ".join(
        f"({alias}.custrecord_periodocomercial = {int(y)} AND {alias}.custrecord_mesmeta = {int(m)})"
        for y, m in months)


def gerencia_clause(gerencia_ids: list[int] | None) -> str:
    return f"AND eg.id IN ({_ids_sql(gerencia_ids)})" if gerencia_ids else ""


def _base_where(p: Period, gfilter: list[int] | None) -> str:
    """FILTRO BASE obligatorio en toda query de ventas."""
    return f"""t.type = 'SalesOrd'
  AND t.trandate >= TO_DATE('{p.start_iso}','YYYY-MM-DD')
  AND t.trandate <  TO_DATE('{p.end_excl_iso}','YYYY-MM-DD')
  AND t.custbody_fase_trato    = {FASE_CERRADO_GANADO}
  AND t.custbody_tipodedeorden = {TIPO_ORDEN_VALIDA}
  AND (t.location IS NULL OR t.location NOT IN ({EXCLUIR_SQL}))
  AND t.status <> 'C'
  AND (t.memo IS NULL OR t.memo NOT LIKE 'VOID%')
  AND eg.id IN ({ACTIVAS_SQL})
  {gerencia_clause(gfilter)}"""


def sql_metas_gerencia(p: Period, gids: list[int]) -> str:
    """Q-M1 — metas de gerencia (tipodemeta = 1): MAX por mes, SUM entre meses."""
    return f"""SELECT metas.gid                 AS gerencia_id,
       SUM(metas.meta_ventas)    AS meta_ventas,
       SUM(metas.meta_colocado)  AS meta_colocado,
       SUM(metas.meta_ingresado) AS meta_ingresado
FROM (
  SELECT m.custrecord_gerenciacomercial        AS gid,
         m.custrecord_periodocomercial         AS anio,
         m.custrecord_mesmeta                  AS mes,
         MAX(m.custrecord_metanumventas)       AS meta_ventas,
         MAX(m.custrecord_metavalorcolocado)   AS meta_colocado,
         MAX(m.custrecord_metavaloringresado)  AS meta_ingresado
  FROM customrecord_metasdecomercial m
  WHERE m.custrecord_tipodemeta = 1
    AND ({periodos_clause(p.months, "m")})
    AND m.custrecord_gerenciacomercial IN ({_ids_sql(gids)})
  GROUP BY m.custrecord_gerenciacomercial, m.custrecord_periodocomercial, m.custrecord_mesmeta
) metas
GROUP BY metas.gid"""


def sql_metas_vendedor(p: Period, gids: list[int]) -> str:
    """Q-M2 — metas de vendedor (tipodemeta = 2): MAX por mes, SUM entre meses."""
    return f"""SELECT metas.gid AS gerencia_id, metas.vid AS vendedor_id, metas.vendedor AS vendedor,
       SUM(metas.meta_ventas)    AS meta_ventas,
       SUM(metas.meta_colocado)  AS meta_colocado,
       SUM(metas.meta_ingresado) AS meta_ingresado
FROM (
  SELECT mv.custrecord_gerenciacomercial        AS gid,
         mv.custrecord_metavendedor             AS vid,
         ev.firstname || ' ' || ev.lastname     AS vendedor,
         mv.custrecord_periodocomercial         AS anio,
         mv.custrecord_mesmeta                  AS mes,
         MAX(mv.custrecord_metanumventas)       AS meta_ventas,
         MAX(mv.custrecord_metavalorcolocado)   AS meta_colocado,
         MAX(mv.custrecord_metavaloringresado)  AS meta_ingresado
  FROM customrecord_metasdecomercial mv
    JOIN employee ev ON ev.id = mv.custrecord_metavendedor
  WHERE mv.custrecord_tipodemeta = 2
    AND ({periodos_clause(p.months, "mv")})
    AND mv.custrecord_gerenciacomercial IN ({_ids_sql(gids)})
  GROUP BY mv.custrecord_gerenciacomercial, mv.custrecord_metavendedor,
           ev.firstname || ' ' || ev.lastname,
           mv.custrecord_periodocomercial, mv.custrecord_mesmeta
) metas
GROUP BY metas.gid, metas.vid, metas.vendedor"""


def sql_ventas_gerencia(p: Period, gfilter: list[int] | None) -> str:
    """Q-F1 — ventas agregadas por gerencia (alias eg, corregido)."""
    return f"""SELECT eg.id                              AS gerencia_id,
       eg.firstname || ' ' || eg.lastname AS gerencia_label,
       COUNT(t.id)                        AS tratos,
       SUM(t.custbody_vlb_so_lot_value)   AS valor_colocado,
       SUM({ING_EXPR}) AS valor_ingresado
FROM transaction t
  JOIN employee eg        ON eg.entityid = t.custbody_gerencia
  JOIN billingschedule bs ON bs.id = t.billingschedule
WHERE {_base_where(p, gfilter)}
GROUP BY eg.id, eg.firstname, eg.lastname"""


def sql_ventas_gerencia_vendedor(p: Period, gfilter: list[int] | None) -> str:
    """Q-F2 — ventas agregadas por gerencia + vendedor."""
    return f"""SELECT eg.id                              AS gerencia_id,
       eg.firstname || ' ' || eg.lastname AS gerencia_label,
       ev.id                              AS vendedor_id,
       ev.firstname || ' ' || ev.lastname AS vendedor,
       COUNT(t.id)                        AS tratos,
       SUM(t.custbody_vlb_so_lot_value)   AS valor_colocado,
       SUM({ING_EXPR}) AS valor_ingresado
FROM transaction t
  JOIN employee eg        ON eg.entityid = t.custbody_gerencia
  JOIN employee ev        ON ev.id = t.employee
  JOIN billingschedule bs ON bs.id = t.billingschedule
WHERE {_base_where(p, gfilter)}
GROUP BY eg.id, eg.firstname, eg.lastname, ev.id, ev.firstname || ' ' || ev.lastname
ORDER BY eg.firstname || ' ' || eg.lastname ASC, COUNT(t.id) DESC"""


def sql_ventas_vendedor(p: Period, gfilter: list[int] | None, with_metas: bool = True) -> str:
    """Q-F3 — ventas por vendedor; con with_metas=True agrega su meta (LEFT JOIN, soporta rango)."""
    metas_cols = """,
       mv.meta_ventas                     AS meta_v_ventas,
       mv.meta_colocado                   AS meta_v_colocado,
       mv.meta_ingresado                  AS meta_v_ingresado""" if with_metas else ""
    metas_join = f"""
  LEFT JOIN (
    SELECT x.gid, x.vid,
           SUM(x.mv_v) AS meta_ventas, SUM(x.mv_c) AS meta_colocado, SUM(x.mv_i) AS meta_ingresado
    FROM (
      SELECT mv.custrecord_gerenciacomercial       AS gid,
             mv.custrecord_metavendedor            AS vid,
             MAX(mv.custrecord_metanumventas)      AS mv_v,
             MAX(mv.custrecord_metavalorcolocado)  AS mv_c,
             MAX(mv.custrecord_metavaloringresado) AS mv_i
      FROM customrecord_metasdecomercial mv
      WHERE mv.custrecord_tipodemeta = 2
        AND ({periodos_clause(p.months, "mv")})
      GROUP BY mv.custrecord_gerenciacomercial, mv.custrecord_metavendedor,
               mv.custrecord_periodocomercial, mv.custrecord_mesmeta
    ) x
    GROUP BY x.gid, x.vid
  ) mv ON mv.gid = eg.id AND mv.vid = ev.id""" if with_metas else ""
    metas_group = ",\n         mv.meta_ventas, mv.meta_colocado, mv.meta_ingresado" if with_metas else ""
    return f"""SELECT eg.id                              AS gerencia_id,
       eg.firstname || ' ' || eg.lastname AS gerencia_label,
       ev.id                              AS vendedor_id,
       ev.firstname || ' ' || ev.lastname AS vendedor,
       COUNT(t.id)                        AS tratos,
       SUM(t.custbody_vlb_so_lot_value)   AS valor_colocado,
       SUM({ING_EXPR}) AS valor_ingresado{metas_cols}
FROM transaction t
  JOIN employee eg        ON eg.entityid = t.custbody_gerencia
  JOIN employee ev        ON ev.id = t.employee
  JOIN billingschedule bs ON bs.id = t.billingschedule{metas_join}
WHERE {_base_where(p, gfilter)}
GROUP BY eg.id, eg.firstname, eg.lastname,
         ev.id, ev.firstname || ' ' || ev.lastname{metas_group}"""


def sql_detalle_so(p: Period, gfilter: list[int] | None) -> str:
    """Q-F4 — una fila por Sales Order (nivel lote)."""
    return f"""SELECT t.id                                AS so_id,
       t.tranid                            AS so_tranid,
       eg.id                               AS gerencia_id,
       eg.firstname || ' ' || eg.lastname  AS gerencia,
       ev.id                               AS vendedor_id,
       ev.firstname || ' ' || ev.lastname  AS vendedor,
       tl.location                         AS desarrollo_id,
       BUILTIN.DF(tl.location)             AS desarrollo,
       NVL(t.memo, 'Sin trato')            AS trato,
       NVL(t.custbody_vlb_so_lot_value, 0) AS valor_colocado,
       {ING_EXPR} AS valor_ingresado
FROM transaction t
  JOIN transactionline tl ON tl.transaction = t.id AND tl.mainline = 'T'
  JOIN employee eg        ON eg.entityid = t.custbody_gerencia
  JOIN employee ev        ON ev.id = t.employee
  JOIN billingschedule bs ON bs.id = t.billingschedule
WHERE {_base_where(p, gfilter)}"""


def _metas_missing(metas_rows: list[dict[str, Any]], gids: list[int]) -> bool:
    return not any(meta_has_value(r) for r in metas_rows if _i(r.get("gerencia_id")) in gids)


def _desarrollo(r: dict[str, Any]) -> tuple[int, str]:
    did = _i(r.get("desarrollo_id"))
    if not did:
        return 0, "Sin desarrollo"
    return did, _name(r.get("desarrollo"), f"Desarrollo {did}")


def _lote(r: dict[str, Any]) -> str:
    return _name(r.get("trato"), "Sin trato")


# ---------------------------------------------------------------------------
# F1 · Top Rank por Gerencia
# ---------------------------------------------------------------------------

F1_METRICS = (
    ("pct_colocado", "Avance Valor Colocado vs Meta", "percent", "trend_colocado"),
    ("pct_ingresado", "Avance Valor Ingresado vs Meta", "percent", "trend_ingresado"),
    ("num_ventas", "Número de Ventas", "number", None),
    ("valor_ingresado", "Valor Ingresado", "currency", None),
    ("valor_colocado", "Valor Colocado", "currency", None),
)


def top_rank_por_gerencia(ns: SuiteQLClient, start: date | None = None, end: date | None = None,
                          gerencia_ids: list[int] | None = None,
                          labels: dict[int, str] | None = None) -> dict[str, Any]:
    """F1 — Ranking de gerencias por avance vs meta, ventas y valores.

    Queries: Q-M1 (metas de gerencia) + Q-F1 (ventas por gerencia).
    Todas las gerencias en alcance aparecen aunque tengan 0 ventas.
    pct = round(valor/meta*100) o 0 sin meta; trend "up" si pct >= expected_pct * 0.8.

    Ejemplo de salida (recortado):
        {"generated_at": "2026-10-08T18:30:00+00:00",
         "period": {"start": "2026-10-01", "end": "2026-10-31", "label": "Octubre 2026"},
         "expected_pct": 25.8, "trend_threshold_pct": 20.6,
         "metas_missing": false, "metas_count": 4, "metas_expected": 5,
         "totals": {"num_ventas": 8, "valor_colocado": 6450000.0, "valor_ingresado": 610000.0,
                    "meta_colocado": 23000000.0, "meta_ingresado": 2200000.0, "pct_colocado": 28},
         "metrics": [{"id": "pct_colocado", "label": "Avance Valor Colocado vs Meta",
                      "format": "percent",
                      "items": [{"gerencia_id": 60586, "manager": "Petra Sanchez - Costa 1",
                                 "zona": "Costa 1", "value": 42, "trend": "up",
                                 "percent_of_leader": 100.0}, ...]}, ...],
         "gerencias": [...]}
    """
    period = resolve_period(start, end)
    gids, gfilter = _scope(gerencia_ids)

    metas_rows = ns.query(sql_metas_gerencia(period, gids), LBL_METAS_GERENCIA)
    ventas_rows = ns.query(sql_ventas_gerencia(period, gfilter), LBL_VENTAS_GERENCIA)
    metas = {_i(r.get("gerencia_id")): r for r in metas_rows}
    ventas = {_i(r.get("gerencia_id")): r for r in ventas_rows}

    exp = expected_pct(period)
    thr = exp * TREND_TOLERANCE

    rows: list[dict[str, Any]] = []
    for gid in gids:
        v, m = ventas.get(gid, {}), metas.get(gid, {})
        row: dict[str, Any] = {
            "gerencia_id": gid,
            "manager": _label(gid, labels),
            "zona": GERENCIAS[gid],
            "num_ventas": _i(v.get("tratos")),
            "valor_colocado": _f(v.get("valor_colocado")),
            "valor_ingresado": _f(v.get("valor_ingresado")),
            "meta_ventas": _i(m.get("meta_ventas")),
            "meta_colocado": _f(m.get("meta_colocado")),
            "meta_ingresado": _f(m.get("meta_ingresado")),
        }
        row["pct_colocado"] = _pct_int(row["valor_colocado"], row["meta_colocado"])
        row["pct_ingresado"] = _pct_int(row["valor_ingresado"], row["meta_ingresado"])
        row["trend_colocado"] = _trend(row["pct_colocado"], thr)
        row["trend_ingresado"] = _trend(row["pct_ingresado"], thr)
        rows.append(row)

    fields = {"gerencia_id": "gerencia_id", "manager": "manager", "zona": "zona"}
    metrics = [
        {"id": key, "label": label, "format": fmt, "items": _rank(rows, key, fields, trend_key)}
        for key, label, fmt, trend_key in F1_METRICS
    ]

    totals: dict[str, Any] = {
        "num_ventas": sum(r["num_ventas"] for r in rows),
        "valor_colocado": sum(r["valor_colocado"] for r in rows),
        "valor_ingresado": sum(r["valor_ingresado"] for r in rows),
        "meta_colocado": sum(r["meta_colocado"] for r in rows),
        "meta_ingresado": sum(r["meta_ingresado"] for r in rows),
    }
    totals["pct_colocado"] = _pct_int(totals["valor_colocado"], totals["meta_colocado"])

    return _clean({
        "generated_at": _now_iso(),
        "period": period.as_dict(),
        "expected_pct": round(exp, 1),
        "trend_threshold_pct": round(thr, 1),
        "metas_missing": _metas_missing(metas_rows, gids),
        "metas_count": sum(1 for g in gids if g in metas and meta_has_value(metas[g])),
        "metas_expected": len(gids),
        "totals": totals,
        "metrics": metrics,
        "gerencias": rows,
    })


# ---------------------------------------------------------------------------
# F2 · Vendedores por Gerencia
# ---------------------------------------------------------------------------

def vendedores_por_gerencia(ns: SuiteQLClient, start: date | None = None, end: date | None = None,
                            gerencia_ids: list[int] | None = None,
                            labels: dict[int, str] | None = None) -> dict[str, Any]:
    """F2 — Vendedores agrupados por gerencia, con metas y % de avance.

    Queries: Q-F2 + Q-M2 + Q-M1 con el mismo rango (metas: MAX por mes, SUM entre meses).
    Validaciones: ambas fechas o ninguna, end >= start, rango <= 730 días.
    Sin fechas: día 1 del mes en curso .. hoy.
    Vendedores con meta y 0 ventas aparecen con sin_ventas=true.
    pct = round(valor/meta*100), None si meta = 0. Semáforo UI: >=100 verde, >=60 amarillo, <60 rojo.

    Ejemplo de salida (recortado):
        {"generated_at": "...",
         "period": {"start": "2026-10-01", "end": "2026-10-08",
                    "label": "01/10/2026 - 08/10/2026", "months": 1},
         "metas_missing": false,
         "gerencias": [{"id": 60586, "manager": "Petra Sanchez - Costa 1", "zona": "Costa 1",
                        "metas": {"ventas": 10, "colocado": 10000000.0, "ingresado": 1000000.0},
                        "totales": {"ventas": 4, "colocado": 4150000.0, "ingresado": 415000.0},
                        "pct": {"ventas": 40, "colocado": 42, "ingresado": 42},
                        "vendedores": [{"id": 101, "nombre": "ANA LOPEZ", "tratos": 3,
                                        "valor_colocado": 3200000.0, "valor_ingresado": 320000.0,
                                        "meta_ventas": 4, "meta_colocado": 4000000.0,
                                        "meta_ingresado": 400000.0, "sin_ventas": false,
                                        "pct_ventas": 75, "pct_colocado": 80,
                                        "pct_ingresado": 80}, ...]}, ...]}
    """
    period = resolve_period(start, end, default_end="today", max_days=MAX_RANGO_DIAS)
    gids, gfilter = _scope(gerencia_ids)

    ventas_rows = ns.query(sql_ventas_gerencia_vendedor(period, gfilter), LBL_VENTAS_GER_VEND)
    metas_v_rows = ns.query(sql_metas_vendedor(period, gids), LBL_METAS_VENDEDOR)
    metas_g_rows = ns.query(sql_metas_gerencia(period, gids), LBL_METAS_GERENCIA)
    metas_g = {_i(r.get("gerencia_id")): r for r in metas_g_rows}

    def new_vendedor(vid: int, nombre: Any) -> dict[str, Any]:
        return {"id": vid, "nombre": _name(nombre, f"Vendedor {vid}"), "tratos": 0,
                "valor_colocado": 0.0, "valor_ingresado": 0.0,
                "meta_ventas": 0, "meta_colocado": 0.0, "meta_ingresado": 0.0,
                "sin_ventas": True}

    ger: dict[int, dict[str, Any]] = {}
    vend: dict[int, dict[int, dict[str, Any]]] = {}
    for gid in gids:
        m = metas_g.get(gid, {})
        ger[gid] = {
            "id": gid, "manager": _label(gid, labels), "zona": GERENCIAS[gid],
            "metas": {"ventas": _i(m.get("meta_ventas")), "colocado": _f(m.get("meta_colocado")),
                      "ingresado": _f(m.get("meta_ingresado"))},
            "totales": {"ventas": 0, "colocado": 0.0, "ingresado": 0.0},
        }
        vend[gid] = {}

    for r in ventas_rows:
        gid = _i(r.get("gerencia_id"))
        if gid not in ger:
            continue
        vid = _i(r.get("vendedor_id"))
        tratos, col, ing = _i(r.get("tratos")), _f(r.get("valor_colocado")), _f(r.get("valor_ingresado"))
        tot = ger[gid]["totales"]
        tot["ventas"] += tratos
        tot["colocado"] += col
        tot["ingresado"] += ing
        v = vend[gid].setdefault(vid, new_vendedor(vid, r.get("vendedor")))
        v["tratos"] += tratos
        v["valor_colocado"] += col
        v["valor_ingresado"] += ing
        v["sin_ventas"] = False

    for r in metas_v_rows:
        gid = _i(r.get("gerencia_id"))
        if gid not in ger:
            continue
        vid = _i(r.get("vendedor_id"))
        v = vend[gid].setdefault(vid, new_vendedor(vid, r.get("vendedor")))
        v["meta_ventas"] = _i(r.get("meta_ventas"))
        v["meta_colocado"] = _f(r.get("meta_colocado"))
        v["meta_ingresado"] = _f(r.get("meta_ingresado"))

    gerencias = []
    for gid in gids:
        g = ger[gid]
        lista = list(vend[gid].values())
        for v in lista:
            v["pct_ventas"] = _pct_int_or_none(v["tratos"], v["meta_ventas"])
            v["pct_colocado"] = _pct_int_or_none(v["valor_colocado"], v["meta_colocado"])
            v["pct_ingresado"] = _pct_int_or_none(v["valor_ingresado"], v["meta_ingresado"])
        lista.sort(key=lambda v: (-v["tratos"], v["nombre"]))
        g["pct"] = {k: _pct_int_or_none(g["totales"][k], g["metas"][k])
                    for k in ("ventas", "colocado", "ingresado")}
        g["vendedores"] = lista
        gerencias.append(g)
    gerencias.sort(key=lambda g: -g["totales"]["ventas"])

    return _clean({
        "generated_at": _now_iso(),
        "period": period.as_dict(include_months=True),
        "metas_missing": _metas_missing(metas_g_rows, gids),
        "gerencias": gerencias,
    })


# ---------------------------------------------------------------------------
# F3 · Detalle Vendedor por Desarrollo
# ---------------------------------------------------------------------------

def detalle_vendedor_por_desarrollo(ns: SuiteQLClient, start: date | None = None,
                                    end: date | None = None,
                                    gerencia_ids: list[int] | None = None,
                                    labels: dict[int, str] | None = None) -> dict[str, Any]:
    """F3 — Panel izquierdo: vendedores con % vs meta y podio; panel derecho: drill
    vendedor -> desarrollo -> lote.

    Queries: Q-M1 (solo metas_missing) + Q-F3 (vendedores con su meta) + Q-F4 (órdenes).
    Nota: Q-F3 solo trae vendedores CON ventas (los de meta y 0 ventas no aparecen).
    `detalle` usa vendedor_id como string (llave JSON).

    Ejemplo de salida (recortado):
        {"generated_at": "...", "period": {...}, "expected_pct": 25.8,
         "trend_threshold_pct": 20.6, "metas_missing": false,
         "gerencia_label": "Todas las gerencias",
         "available_gerencias": [{"id": 60594, "label": "Gerencia - Campestre"}, ...],
         "total_vendedores": 6,
         "vendedores": [{"vendedor_id": 101, "vendedor": "ANA LOPEZ", "gerencia_id": 60586,
                         "branch": "Petra Sanchez - Costa 1", "num_ventas": 3,
                         "valor_colocado": 3200000.0, "valor_ingresado": 320000.0,
                         "meta_ventas": 4, "meta_colocado": 4000000.0, "meta_ingresado": 400000.0,
                         "pct_colocado": 80, "pct_ingresado": 80,
                         "trend_colocado": "up", "trend_ingresado": "up"}, ...],
         "top_items": [... primeros 5 ...],
         "totals": {...},
         "metrics": [{"id": "pct_colocado", ..., "items": [...]}, {"id": "pct_ingresado", ...}],
         "detalle": {"101": [{"desarrollo_id": 17, "desarrollo": "Villa Toscana",
                              "totales": {"num_ventas": 2, "colocado": 2300000.0,
                                          "ingresado": 230000.0},
                              "lotes": [{"nombre": "VILLA TOSCANA3 PORTOFINO M49-L55",
                                         "num_ventas": 1, "colocado": 1200000.0,
                                         "ingresado": 120000.0}, ...]}, ...]}}
    """
    period = resolve_period(start, end)
    gids, gfilter = _scope(gerencia_ids)

    metas_g_rows = ns.query(sql_metas_gerencia(period, gids), LBL_METAS_GERENCIA)
    vend_rows = ns.query(sql_ventas_vendedor(period, gfilter, with_metas=True), LBL_VENTAS_VENDEDOR)
    so_rows = ns.query(sql_detalle_so(period, gfilter), LBL_DETALLE_SO)

    exp = expected_pct(period)
    thr = exp * TREND_TOLERANCE

    vendedores: list[dict[str, Any]] = []
    for r in vend_rows:
        gid = _i(r.get("gerencia_id"))
        if gid not in gids:
            continue
        vid = _i(r.get("vendedor_id"))
        item: dict[str, Any] = {
            "vendedor_id": vid,
            "vendedor": _name(r.get("vendedor"), f"Vendedor {vid}"),
            "gerencia_id": gid,
            "branch": _label(gid, labels),
            "num_ventas": _i(r.get("tratos")),
            "valor_colocado": _f(r.get("valor_colocado")),
            "valor_ingresado": _f(r.get("valor_ingresado")),
            "meta_ventas": _i(r.get("meta_v_ventas")),
            "meta_colocado": _f(r.get("meta_v_colocado")),
            "meta_ingresado": _f(r.get("meta_v_ingresado")),
        }
        item["pct_colocado"] = _pct_int(item["valor_colocado"], item["meta_colocado"])
        item["pct_ingresado"] = _pct_int(item["valor_ingresado"], item["meta_ingresado"])
        item["trend_colocado"] = _trend(item["pct_colocado"], thr)
        item["trend_ingresado"] = _trend(item["pct_ingresado"], thr)
        vendedores.append(item)
    vendedores.sort(key=lambda v: -v["num_ventas"])
    top_items = vendedores[:TOP_PODIO]

    fields = {"vendedor_id": "vendedor_id", "vendedor": "vendedor", "branch": "branch"}
    metrics = [
        {"id": "pct_colocado", "label": "Avance Valor Colocado vs Meta", "format": "percent",
         "items": _rank(top_items, "pct_colocado", fields, "trend_colocado")},
        {"id": "pct_ingresado", "label": "Avance Valor Ingresado vs Meta", "format": "percent",
         "items": _rank(top_items, "pct_ingresado", fields, "trend_ingresado")},
    ]

    totals: dict[str, Any] = {
        "num_ventas": sum(v["num_ventas"] for v in vendedores),
        "valor_colocado": sum(v["valor_colocado"] for v in vendedores),
        "valor_ingresado": sum(v["valor_ingresado"] for v in vendedores),
        "meta_colocado": sum(v["meta_colocado"] for v in vendedores),
        "meta_ingresado": sum(v["meta_ingresado"] for v in vendedores),
    }
    totals["pct_colocado"] = _pct_int(totals["valor_colocado"], totals["meta_colocado"])
    totals["pct_ingresado"] = _pct_int(totals["valor_ingresado"], totals["meta_ingresado"])

    # Panel derecho: vendedor -> desarrollo -> lote
    tree: dict[str, dict[int, dict[str, Any]]] = {}
    for r in so_rows:
        if _i(r.get("gerencia_id")) not in gids:
            continue
        vid = str(_i(r.get("vendedor_id")))
        did, dname = _desarrollo(r)
        lote = _lote(r)
        col, ing = _f(r.get("valor_colocado")), _f(r.get("valor_ingresado"))
        d = tree.setdefault(vid, {}).setdefault(did, {
            "desarrollo_id": did, "desarrollo": dname,
            "totales": {"num_ventas": 0, "colocado": 0.0, "ingresado": 0.0}, "_lotes": {}})
        d["totales"]["num_ventas"] += 1
        d["totales"]["colocado"] += col
        d["totales"]["ingresado"] += ing
        lt = d["_lotes"].setdefault(lote, {"nombre": lote, "num_ventas": 0, "colocado": 0.0, "ingresado": 0.0})
        lt["num_ventas"] += 1
        lt["colocado"] += col
        lt["ingresado"] += ing

    detalle: dict[str, list[dict[str, Any]]] = {}
    for vid, devs in tree.items():
        lista = []
        for d in devs.values():
            lotes = sorted(d.pop("_lotes").values(), key=lambda x: -(x["colocado"] + x["ingresado"]))
            d["lotes"] = lotes
            lista.append(d)
        lista.sort(key=lambda d: -(d["totales"]["colocado"] + d["totales"]["ingresado"]))
        detalle[vid] = lista

    return _clean({
        "generated_at": _now_iso(),
        "period": period.as_dict(),
        "expected_pct": round(exp, 1),
        "trend_threshold_pct": round(thr, 1),
        "metas_missing": _metas_missing(metas_g_rows, gids),
        "gerencia_label": _gerencia_label(gids, gfilter, labels),
        "available_gerencias": _available_gerencias(labels),
        "total_vendedores": len(vendedores),
        "vendedores": vendedores,
        "top_items": top_items,
        "totals": totals,
        "metrics": metrics,
        "detalle": detalle,
    })


# ---------------------------------------------------------------------------
# F4 · Vendedor a Detalle
# ---------------------------------------------------------------------------

def vendedor_a_detalle(ns: SuiteQLClient, start: date | None = None, end: date | None = None,
                       gerencia_ids: list[int] | None = None,
                       labels: dict[int, str] | None = None) -> dict[str, Any]:
    """F4 — Árbol gerencia -> vendedor -> desarrollo -> lote.

    Queries: Q-F4 (órdenes) + Q-M2 (metas de vendedor) + Q-M1 (metas_missing).
    Metas de vendedor se suman a totales.meta_* de su gerencia; un vendedor con meta y sin
    ventas se crea con desarrollos vacíos. pct con 1 decimal, None si la meta es 0.
    Cada nivel ordenado por (colocado + ingresado) desc.

    Ejemplo de salida (recortado):
        {"generated_at": "...", "period": {...}, "metas_missing": false,
         "gerencias": [{"id": 60586, "manager": "Petra Sanchez - Costa 1", "zona": "Costa 1",
                        "pct_colocado": 41.5, "pct_ingresado": 59.3,
                        "totales": {"colocado": 4150000.0, "ingresado": 415000.0,
                                    "meta_colocado": 10000000.0, "meta_ingresado": 700000.0},
                        "vendedores": [{"id": 101, "nombre": "ANA LOPEZ",
                                        "pct_colocado": 80.0, "pct_ingresado": 80.0,
                                        "totales": {"colocado": 3200000.0, "ingresado": 320000.0},
                                        "metas": {"colocado": 4000000.0, "ingresado": 400000.0},
                                        "desarrollos": [{"id": 17, "nombre": "Villa Toscana",
                                                         "totales": {...},
                                                         "lotes": [{"id": "VILLA TOSCANA3 PORTOFINO M49-L55",
                                                                    "nombre": "...",
                                                                    "colocado": 1200000.0,
                                                                    "ingresado": 120000.0}]}]}]}]}
    """
    period = resolve_period(start, end)
    gids, gfilter = _scope(gerencia_ids)

    so_rows = ns.query(sql_detalle_so(period, gfilter), LBL_DETALLE_SO)
    metas_v_rows = ns.query(sql_metas_vendedor(period, gids), LBL_METAS_VENDEDOR)
    metas_g_rows = ns.query(sql_metas_gerencia(period, gids), LBL_METAS_GERENCIA)

    ger: dict[int, dict[str, Any]] = {
        gid: {"id": gid, "manager": _label(gid, labels), "zona": GERENCIAS[gid],
              "totales": {"colocado": 0.0, "ingresado": 0.0, "meta_colocado": 0.0, "meta_ingresado": 0.0},
              "_vend": {}}
        for gid in gids
    }

    def ensure_vendedor(g: dict[str, Any], vid: int, nombre: Any) -> dict[str, Any]:
        return g["_vend"].setdefault(vid, {
            "id": vid, "nombre": _name(nombre, f"Vendedor {vid}"),
            "totales": {"colocado": 0.0, "ingresado": 0.0},
            "metas": {"colocado": 0.0, "ingresado": 0.0}, "_devs": {}})

    for r in so_rows:
        gid = _i(r.get("gerencia_id"))
        g = ger.get(gid)
        if g is None:
            continue
        col, ing = _f(r.get("valor_colocado")), _f(r.get("valor_ingresado"))
        v = ensure_vendedor(g, _i(r.get("vendedor_id")), r.get("vendedor"))
        did, dname = _desarrollo(r)
        d = v["_devs"].setdefault(did, {"id": did, "nombre": dname,
                                        "totales": {"colocado": 0.0, "ingresado": 0.0}, "_lotes": {}})
        lote = _lote(r)
        lt = d["_lotes"].setdefault(lote, {"id": lote, "nombre": lote, "colocado": 0.0, "ingresado": 0.0})
        for node in (g["totales"], v["totales"], d["totales"], lt):
            node["colocado"] += col
            node["ingresado"] += ing

    for r in metas_v_rows:
        g = ger.get(_i(r.get("gerencia_id")))
        if g is None:
            continue
        v = ensure_vendedor(g, _i(r.get("vendedor_id")), r.get("vendedor"))
        mc, mi = _f(r.get("meta_colocado")), _f(r.get("meta_ingresado"))
        v["metas"]["colocado"] += mc
        v["metas"]["ingresado"] += mi
        g["totales"]["meta_colocado"] += mc
        g["totales"]["meta_ingresado"] += mi

    def peso(node: dict[str, Any]) -> float:
        t = node.get("totales", node)
        return -(t["colocado"] + t["ingresado"])

    gerencias = []
    for gid in gids:
        g = ger[gid]
        vendedores = []
        for v in g.pop("_vend").values():
            desarrollos = []
            for d in v.pop("_devs").values():
                d["lotes"] = sorted(d.pop("_lotes").values(), key=peso)
                desarrollos.append(d)
            desarrollos.sort(key=peso)
            v["desarrollos"] = desarrollos
            v["pct_colocado"] = _pct_1d_or_none(v["totales"]["colocado"], v["metas"]["colocado"])
            v["pct_ingresado"] = _pct_1d_or_none(v["totales"]["ingresado"], v["metas"]["ingresado"])
            vendedores.append(v)
        vendedores.sort(key=peso)
        t = g["totales"]
        g["pct_colocado"] = _pct_1d_or_none(t["colocado"], t["meta_colocado"])
        g["pct_ingresado"] = _pct_1d_or_none(t["ingresado"], t["meta_ingresado"])
        g["vendedores"] = vendedores
        gerencias.append(g)
    gerencias.sort(key=peso)

    return _clean({
        "generated_at": _now_iso(),
        "period": period.as_dict(),
        "metas_missing": _metas_missing(metas_g_rows, gids),
        "gerencias": gerencias,
    })


# ---------------------------------------------------------------------------
# F5 · Top Ranking Vendedores
# ---------------------------------------------------------------------------

def top_ranking_vendedores(ns: SuiteQLClient, start: date | None = None, end: date | None = None,
                           gerencia_ids: list[int] | None = None,
                           labels: dict[int, str] | None = None,
                           limit: int = 15) -> dict[str, Any]:
    """F5 — Top N vendedores por valor colocado (desempate: num_ventas).

    Query: Q-F3 sin el LEFT JOIN de metas. Corta a `limit` (default 15) y asigna rank 1..N.
    total_vendedores cuenta a todos antes del corte. La UI muestra los primeros 5 como tarjetas.

    Ejemplo de salida (recortado):
        {"generated_at": "...", "period": {...}, "gerencia_label": "Todas las gerencias",
         "gerencia_filter": null, "available_gerencias": [...], "limit": 15,
         "total_vendedores": 6,
         "vendedores": [{"rank": 1, "vendedor_id": 101, "vendedor": "Ana Lopez",
                         "gerencia": "Petra Sanchez - Costa 1", "gerencia_id": 60586,
                         "valor_colocado": 3200000.0, "valor_ingresado": 320000.0,
                         "num_ventas": 3}, ...]}
    """
    limit = int(limit)
    if limit < 1:
        raise ValueError("limit debe ser >= 1.")
    period = resolve_period(start, end)
    gids, gfilter = _scope(gerencia_ids)

    rows = ns.query(sql_ventas_vendedor(period, gfilter, with_metas=False), LBL_VENTAS_VENDEDOR)
    items = []
    for r in rows:
        gid = _i(r.get("gerencia_id"))
        if gid not in gids:
            continue
        vid = _i(r.get("vendedor_id"))
        items.append({
            "vendedor_id": vid,
            "vendedor": _name(r.get("vendedor"), f"Vendedor {vid}").title(),
            "gerencia": _label(gid, labels),
            "gerencia_id": gid,
            "valor_colocado": _f(r.get("valor_colocado")),
            "valor_ingresado": _f(r.get("valor_ingresado")),
            "num_ventas": _i(r.get("tratos")),
        })
    items.sort(key=lambda x: (-x["valor_colocado"], -x["num_ventas"], x["vendedor"]))
    top = [{"rank": n, **it} for n, it in enumerate(items[:limit], start=1)]

    return _clean({
        "generated_at": _now_iso(),
        "period": period.as_dict(),
        "gerencia_label": _gerencia_label(gids, gfilter, labels),
        "gerencia_filter": gfilter,
        "available_gerencias": _available_gerencias(labels),
        "limit": limit,
        "total_vendedores": len(items),
        "vendedores": top,
    })


__all__ = [
    "GERENCIAS", "Period", "resolve_period", "expected_pct",
    "top_rank_por_gerencia", "vendedores_por_gerencia", "detalle_vendedor_por_desarrollo",
    "vendedor_a_detalle", "top_ranking_vendedores",
]
