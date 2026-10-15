"""Datos de ejemplo (octubre 2026) y un cliente SuiteQL falso para tests y ejemplos.

Las filas se derivan de una lista de Sales Orders para que todas las queries cuadren
entre sí. Los valores llegan como string, igual que en SuiteQL REST.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import toprank as tr

LABELS = {60586: "Petra Sanchez - Costa 1"}

GERENTES = {  # employee.id de la gerencia -> nombre del gerente (firstname || ' ' || lastname)
    60594: "LUIS CAMPOS", 55556: "MARTA COSTA", 60586: "PETRA SANCHEZ",
    45956: "RAUL SATO", 34823: "UMA RIOS",
}

# (so_id, gerencia_id, vendedor_id, vendedor, desarrollo_id, desarrollo, memo, colocado, ingresado)
SALES_ORDERS = [
    (1, 60586, 101, "ANA LOPEZ", 17, "Villa Toscana", "VILLA TOSCANA3 PORTOFINO M49-L55", 1_200_000, 120_000),
    (2, 60586, 101, "ANA LOPEZ", 17, "Villa Toscana", "VILLA TOSCANA3 PORTOFINO M49-L56", 1_100_000, 110_000),
    (3, 60586, 101, "ANA LOPEZ", 22, "Punta Azul", "PUNTA AZUL M3-L8", 900_000, 90_000),
    (4, 60586, 102, "BRUNO DIAZ", 22, "Punta Azul", "PUNTA AZUL M3-L9", 950_000, 95_000),
    (5, 45956, 201, "CARLA RUIZ", None, None, None, 500_000, 50_000),
    (6, 34823, 301, "DIEGO MORA", 30, "Bosque Real", "BOSQUE REAL L1", 700_000, 70_000),
    (7, 34823, 302, "ELENA PAZ", 30, "Bosque Real", "BOSQUE REAL L2", 700_000, 35_000),
    (8, 34823, 303, "FER GIL", 30, "Bosque Real", "BOSQUE REAL L3", 400_000, 40_000),
]

# Metas de gerencia (tipodemeta = 1): gerencia -> (ventas, colocado, ingresado). 60594 sin meta.
METAS_GERENCIA = {
    60586: (10, 10_000_000, 1_000_000),
    45956: (5, 5_000_000, 500_000),
    34823: (6, 5_000_000, 500_000),
    55556: (4, 3_000_000, 200_000),
}

# Metas de vendedor (tipodemeta = 2): (gerencia, vendedor, nombre, ventas, colocado, ingresado)
METAS_VENDEDOR = [
    (60586, 101, "ANA LOPEZ", 4, 4_000_000, 400_000),
    (60586, 102, "BRUNO DIAZ", 3, 3_000_000, 0),        # sin meta de ingresado
    (60586, 103, "GABRIEL SOTO", 3, 3_000_000, 300_000),  # meta sin ventas
    (34823, 301, "DIEGO MORA", 2, 1_400_000, 140_000),
]


def _s(x: Any) -> str | None:
    return None if x is None else str(x)


def sample_responses() -> dict[str, list[dict[str, Any]]]:
    """label de query -> filas (como las devolvería SuiteQL)."""
    by_ger: dict[int, list] = defaultdict(list)
    by_ger_vend: dict[tuple, list] = defaultdict(list)
    for so in SALES_ORDERS:
        by_ger[so[1]].append(so)
        by_ger_vend[(so[1], so[2], so[3])].append(so)

    metas_v = {(g, v): (n, mv, mc, mi) for g, v, n, mv, mc, mi in METAS_VENDEDOR}

    def agg(sos):
        return {"tratos": str(len(sos)), "valor_colocado": str(sum(s[7] for s in sos)),
                "valor_ingresado": str(sum(s[8] for s in sos))}

    ventas_gerencia = [{"gerencia_id": str(g), "gerencia_label": GERENTES[g], **agg(sos)}
                       for g, sos in by_ger.items()]
    ventas_ger_vend = [{"gerencia_id": str(g), "gerencia_label": GERENTES[g], "vendedor_id": str(v),
                        "vendedor": n, **agg(sos)} for (g, v, n), sos in by_ger_vend.items()]
    ventas_vendedor = []
    for (g, v, n), sos in by_ger_vend.items():
        m = metas_v.get((g, v))
        ventas_vendedor.append({
            "gerencia_id": str(g), "gerencia_label": GERENTES[g], "vendedor_id": str(v), "vendedor": n,
            **agg(sos),
            "meta_v_ventas": _s(m[1]) if m else None,
            "meta_v_colocado": _s(m[2]) if m else None,
            "meta_v_ingresado": _s(m[3]) if m else None,
        })
    detalle = [{
        "so_id": str(so[0]), "so_tranid": f"SO{so[0]:05d}", "gerencia_id": str(so[1]),
        "gerencia": GERENTES[so[1]], "vendedor_id": str(so[2]), "vendedor": so[3],
        "desarrollo_id": _s(so[4]), "desarrollo": so[5], "trato": so[6] or "Sin trato",
        "valor_colocado": str(so[7]), "valor_ingresado": str(so[8]),
    } for so in SALES_ORDERS]

    return {
        tr.LBL_METAS_GERENCIA: [{"gerencia_id": str(g), "meta_ventas": str(v), "meta_colocado": str(c),
                                 "meta_ingresado": str(i)} for g, (v, c, i) in METAS_GERENCIA.items()],
        tr.LBL_METAS_VENDEDOR: [{"gerencia_id": str(g), "vendedor_id": str(v), "vendedor": n,
                                 "meta_ventas": str(mv), "meta_colocado": str(mc), "meta_ingresado": str(mi)}
                                for g, v, n, mv, mc, mi in METAS_VENDEDOR],
        tr.LBL_VENTAS_GERENCIA: ventas_gerencia,
        tr.LBL_VENTAS_GER_VEND: ventas_ger_vend,
        tr.LBL_VENTAS_VENDEDOR: ventas_vendedor,
        tr.LBL_DETALLE_SO: detalle,
    }


class StubNS:
    """Cliente SuiteQL falso: devuelve filas por label y registra el SQL recibido."""

    def __init__(self, responses: dict[str, list[dict[str, Any]]] | None = None):
        self.responses = sample_responses() if responses is None else responses
        self.calls: list[tuple[str, str]] = []

    def query(self, sql: str, label: str) -> list[dict[str, Any]]:
        self.calls.append((label, sql))
        return [dict(r) for r in self.responses.get(label, [])]

    def sql(self, label: str) -> str:
        return next(s for lbl, s in self.calls if lbl == label)
