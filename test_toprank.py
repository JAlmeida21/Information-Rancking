"""Tests de las funciones F1–F5 con un cliente SuiteQL falso (datos_ejemplo.StubNS)."""

from datetime import date

import pytest

import toprank as tr
from datos_ejemplo import LABELS, StubNS

OCT_1, OCT_31 = date(2026, 10, 1), date(2026, 10, 31)


@pytest.fixture(autouse=True)
def hoy_8_de_octubre(monkeypatch):
    monkeypatch.setattr(tr, "_today", lambda: date(2026, 10, 8))


def _metric(out, metric_id):
    return next(m for m in out["metrics"] if m["id"] == metric_id)


BASE_FILTER = [
    "t.type = 'SalesOrd'",
    "t.custbody_fase_trato    = 2",
    "t.custbody_tipodedeorden = 1",
    "t.location NOT IN (248,282,286)",
    "t.status <> 'C'",
    "t.memo NOT LIKE 'VOID%'",
    "eg.id IN (60594,55556,60586,45956,34823)",
]


# --- helpers ---------------------------------------------------------------

def test_resolve_period_rango_varios_meses_y_ritmo():
    p = tr.resolve_period(date(2026, 9, 15), date(2026, 11, 2))
    assert p.months == ((2026, 9), (2026, 10), (2026, 11))
    assert p.end_excl_iso == "2026-11-03"
    assert round(tr.expected_pct(tr.resolve_period(None, None)), 1) == 25.8   # 8/31
    assert tr.expected_pct(tr.resolve_period(date(2026, 9, 1), date(2026, 9, 30))) == 100.0


# --- F1 --------------------------------------------------------------------

def test_f1_esqueleto_rankings_y_totales():
    out = tr.top_rank_por_gerencia(StubNS(), labels=LABELS)
    assert out["period"] == {"start": "2026-10-01", "end": "2026-10-31", "label": "Octubre 2026"}
    assert (out["expected_pct"], out["trend_threshold_pct"]) == (25.8, 20.6)
    assert out["metas_missing"] is False
    assert (out["metas_count"], out["metas_expected"]) == (4, 5)

    pct = _metric(out, "pct_colocado")["items"]
    assert len(pct) == 5                                   # incluye gerencias con 0 ventas
    assert pct[0] == {"gerencia_id": 60586, "manager": "Petra Sanchez - Costa 1", "zona": "Costa 1",
                      "value": 42, "trend": "up", "percent_of_leader": 100.0}
    assert (pct[1]["zona"], pct[1]["value"], pct[1]["percent_of_leader"]) == ("Urbano", 36, 85.7)
    assert (pct[2]["zona"], pct[2]["trend"]) == ("Satelite", "down")
    assert {i["zona"] for i in pct[3:]} == {"Campestre", "Costa 2"}
    assert _metric(out, "num_ventas")["items"][0]["value"] == 4

    assert out["totals"] == {"num_ventas": 8, "valor_colocado": 6_450_000, "valor_ingresado": 610_000,
                             "meta_colocado": 23_000_000, "meta_ingresado": 2_200_000, "pct_colocado": 28}


def test_f1_filtro_gerencia_sql_y_metas_missing():
    ns = StubNS()
    ns.responses[tr.LBL_METAS_GERENCIA] = []
    out = tr.top_rank_por_gerencia(ns, OCT_1, OCT_31, gerencia_ids=[60586])
    assert out["metas_missing"] is True
    assert [i["gerencia_id"] for i in _metric(out, "valor_colocado")["items"]] == [60586]
    assert out["totals"]["num_ventas"] == 4                # descarta filas de otras gerencias

    sql = ns.sql(tr.LBL_VENTAS_GERENCIA)
    for frag in BASE_FILTER + ["AND eg.id IN (60586)", "NULLIF(TO_NUMBER(REGEXP_SUBSTR",
                               "t.trandate <  TO_DATE('2026-11-01','YYYY-MM-DD')"]:
        assert frag in sql
    assert "custrecord_gerenciacomercial IN (60586)" in ns.sql(tr.LBL_METAS_GERENCIA)
    with pytest.raises(ValueError):
        tr.top_rank_por_gerencia(ns, gerencia_ids=[])
    with pytest.raises(ValueError):
        tr.top_rank_por_gerencia(ns, gerencia_ids=[999])


# --- F2 --------------------------------------------------------------------

def test_f2_vendedores_metas_y_sin_ventas():
    out = tr.vendedores_por_gerencia(StubNS(), labels=LABELS)
    assert out["period"]["start"] == "2026-10-01" and out["period"]["end"] == "2026-10-08"
    assert out["period"]["months"] == 1

    costa1 = out["gerencias"][0]
    assert costa1["id"] == 60586                            # más ventas primero
    assert costa1["totales"] == {"ventas": 4, "colocado": 4_150_000, "ingresado": 415_000}
    assert costa1["pct"] == {"ventas": 40, "colocado": 42, "ingresado": 42}

    nombres = [v["nombre"] for v in costa1["vendedores"]]
    assert nombres == ["ANA LOPEZ", "BRUNO DIAZ", "GABRIEL SOTO"]
    ana, bruno, gabriel = costa1["vendedores"]
    assert (ana["tratos"], ana["pct_ventas"], ana["pct_colocado"]) == (3, 75, 80)
    assert bruno["pct_ingresado"] is None                   # meta de ingresado = 0
    assert gabriel["sin_ventas"] is True and gabriel["tratos"] == 0 and gabriel["meta_colocado"] == 3_000_000

    campestre = next(g for g in out["gerencias"] if g["id"] == 60594)
    assert campestre["vendedores"] == [] and campestre["pct"]["colocado"] is None


def test_f2_validaciones_de_rango():
    ns = StubNS()
    with pytest.raises(ValueError):
        tr.vendedores_por_gerencia(ns, start=OCT_1)                       # solo una fecha
    with pytest.raises(ValueError):
        tr.vendedores_por_gerencia(ns, OCT_31, OCT_1)                     # end < start
    with pytest.raises(ValueError):
        tr.vendedores_por_gerencia(ns, date(2024, 1, 1), date(2026, 1, 2))  # > 730 días
    out = tr.vendedores_por_gerencia(ns, date(2026, 9, 1), OCT_31)
    assert out["period"]["months"] == 2
    assert "custrecord_mesmeta = 9" in ns.sql(tr.LBL_METAS_VENDEDOR)
    assert "custrecord_mesmeta = 10" in ns.sql(tr.LBL_METAS_GERENCIA)


# --- F3 --------------------------------------------------------------------

def test_f3_podio_y_metricas():
    out = tr.detalle_vendedor_por_desarrollo(StubNS(), labels=LABELS)
    assert out["total_vendedores"] == 6
    assert out["gerencia_label"] == "Todas las gerencias"
    assert len(out["available_gerencias"]) == 5
    assert len(out["top_items"]) == 5 and out["top_items"][0]["vendedor"] == "ANA LOPEZ"

    ana = out["vendedores"][0]
    assert (ana["pct_colocado"], ana["pct_ingresado"], ana["trend_colocado"]) == (80, 80, "up")
    assert ana["branch"] == "Petra Sanchez - Costa 1"
    bruno = next(v for v in out["vendedores"] if v["vendedor_id"] == 102)
    assert (bruno["pct_colocado"], bruno["pct_ingresado"]) == (32, 0)

    ranking = _metric(out, "pct_colocado")["items"]
    assert [i["vendedor_id"] for i in ranking[:3]] == [101, 301, 102]
    assert ranking[0]["percent_of_leader"] == 100.0


def test_f3_drill_desarrollo_lote_y_filtro():
    ns = StubNS()
    out = tr.detalle_vendedor_por_desarrollo(ns, gerencia_ids=[60586, 45956])
    assert set(out["detalle"]) == {"101", "102", "201"}
    ana = out["detalle"]["101"]
    assert [d["desarrollo"] for d in ana] == ["Villa Toscana", "Punta Azul"]
    assert ana[0]["totales"] == {"num_ventas": 2, "colocado": 2_300_000, "ingresado": 230_000}
    assert [l["nombre"] for l in ana[0]["lotes"]] == ["VILLA TOSCANA3 PORTOFINO M49-L55",
                                                      "VILLA TOSCANA3 PORTOFINO M49-L56"]
    carla = out["detalle"]["201"][0]
    assert (carla["desarrollo_id"], carla["desarrollo"], carla["lotes"][0]["nombre"]) == (
        0, "Sin desarrollo", "Sin trato")
    assert "LEFT JOIN" in ns.sql(tr.LBL_VENTAS_VENDEDOR)
    assert "AND eg.id IN (60586,45956)" in ns.sql(tr.LBL_DETALLE_SO)
    assert "tl.mainline = 'T'" in ns.sql(tr.LBL_DETALLE_SO)


# --- F4 --------------------------------------------------------------------

def test_f4_arbol_y_sumas_por_nivel():
    out = tr.vendedor_a_detalle(StubNS(), labels=LABELS)
    costa1 = out["gerencias"][0]
    assert costa1["id"] == 60586
    assert costa1["totales"] == {"colocado": 4_150_000, "ingresado": 415_000,
                                 "meta_colocado": 10_000_000, "meta_ingresado": 700_000}
    assert (costa1["pct_colocado"], costa1["pct_ingresado"]) == (41.5, 59.3)

    ana = costa1["vendedores"][0]
    assert (ana["nombre"], ana["pct_colocado"]) == ("ANA LOPEZ", 80.0)
    assert ana["totales"] == {"colocado": 3_200_000, "ingresado": 320_000}
    vt = ana["desarrollos"][0]
    assert (vt["id"], vt["nombre"], vt["totales"]["colocado"]) == (17, "Villa Toscana", 2_300_000)
    assert vt["lotes"][0]["id"] == "VILLA TOSCANA3 PORTOFINO M49-L55"
    assert sum(l["colocado"] for l in vt["lotes"]) == vt["totales"]["colocado"]


def test_f4_metas_vendedor_sin_ventas_y_pct_none():
    out = tr.vendedor_a_detalle(StubNS())
    by_id = {g["id"]: g for g in out["gerencias"]}
    costa1 = by_id[60586]
    gabriel = costa1["vendedores"][-1]
    assert gabriel["nombre"] == "GABRIEL SOTO" and gabriel["desarrollos"] == []
    assert gabriel["metas"]["colocado"] == 3_000_000 and gabriel["pct_colocado"] == 0.0
    bruno = next(v for v in costa1["vendedores"] if v["id"] == 102)
    assert bruno["pct_ingresado"] is None
    assert by_id[34823]["pct_colocado"] == 128.6           # solo Diego tiene meta
    assert by_id[45956]["pct_colocado"] is None            # sin metas de vendedor
    assert by_id[60594]["vendedores"] == []


# --- F5 --------------------------------------------------------------------

def test_f5_orden_desempate_limit_y_rank():
    out = tr.top_ranking_vendedores(StubNS(), labels=LABELS, limit=4)
    assert out["total_vendedores"] == 6 and out["limit"] == 4
    vs = out["vendedores"]
    assert [v["rank"] for v in vs] == [1, 2, 3, 4]
    assert [v["vendedor"] for v in vs] == ["Ana Lopez", "Bruno Diaz", "Diego Mora", "Elena Paz"]
    assert vs[0] == {"rank": 1, "vendedor_id": 101, "vendedor": "Ana Lopez",
                     "gerencia": "Petra Sanchez - Costa 1", "gerencia_id": 60586,
                     "valor_colocado": 3_200_000, "valor_ingresado": 320_000, "num_ventas": 3}


def test_f5_sql_sin_metas_y_filtro():
    ns = StubNS()
    out = tr.top_ranking_vendedores(ns, gerencia_ids=[34823])
    assert out["gerencia_filter"] == [34823]
    assert out["gerencia_label"] == "Gerencia - Urbano"
    assert [v["vendedor_id"] for v in out["vendedores"]] == [301, 302, 303]
    sql = ns.sql(tr.LBL_VENTAS_VENDEDOR)
    assert "LEFT JOIN" not in sql and "meta_v_" not in sql
    for frag in BASE_FILTER:
        assert frag in sql
    with pytest.raises(ValueError):
        tr.top_ranking_vendedores(ns, limit=0)
