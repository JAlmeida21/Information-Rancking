"""Muestra los vendedores en tabla, a partir de los JSON de resultados/.

Uso:
    python resumen_vendedores.py          # vendedores agrupados por gerencia (F2)
    python resumen_vendedores.py top      # top ranking de vendedores (F5)
"""

import json
import sys
from pathlib import Path


def load(name):
    return json.loads(Path(f"resultados/{name}.json").read_text(encoding="utf-8"))


def money(x):
    return f"${x:,.0f}"


def pct(x):
    return "-" if x is None else f"{x}%"


def row(vals, widths):
    return "  ".join(f"{str(v):<{w}}" if i == 0 else f"{str(v):>{w}}" for i, (v, w) in enumerate(zip(vals, widths)))


def por_gerencia():
    data = load("F2_vendedores_por_gerencia")
    print(f"\nVendedores por Gerencia — {data['period']['label']}")
    heads = ["Vendedor", "Ventas", "Meta", "% Vtas", "Colocado", "Meta colocado", "% Col",
             "Ingresado", "Meta ingresado", "% Ing"]
    widths = [28, 6, 5, 6, 13, 13, 6, 12, 14, 6]
    for g in data["gerencias"]:
        t, m, p = g["totales"], g["metas"], g["pct"]
        print(f"\n== {g['zona']} ({g['manager']}) ==  ventas {t['ventas']}/{m['ventas']} ({pct(p['ventas'])})  |  "
              f"colocado {money(t['colocado'])} ({pct(p['colocado'])})  |  "
              f"ingresado {money(t['ingresado'])} ({pct(p['ingresado'])})")
        line = row(heads, widths)
        print(line)
        print("-" * len(line))
        if not g["vendedores"]:
            print("  (sin vendedores ni metas en el periodo)")
        for v in g["vendedores"]:
            nombre = v["nombre"][:26] + (" *" if v["sin_ventas"] else "")
            print(row([nombre, v["tratos"], v["meta_ventas"], pct(v["pct_ventas"]),
                       money(v["valor_colocado"]), money(v["meta_colocado"]), pct(v["pct_colocado"]),
                       money(v["valor_ingresado"]), money(v["meta_ingresado"]), pct(v["pct_ingresado"])], widths))
    print("\n* = tiene meta pero no ha vendido en el periodo.   - = sin meta cargada.\n")


def top():
    data = load("F5_top_ranking_vendedores")
    print(f"\nTop Ranking Vendedores — {data['period']['label']}  "
          f"(mostrando {len(data['vendedores'])} de {data['total_vendedores']})\n")
    heads = ["#", "Vendedor", "Gerencia", "Ventas", "Colocado", "Ingresado"]
    widths = [3, 28, 26, 6, 13, 12]
    line = "  ".join(f"{h:<{w}}" if i in (1, 2) else f"{h:>{w}}" for i, (h, w) in enumerate(zip(heads, widths)))
    print(line)
    print("-" * len(line))
    for v in data["vendedores"]:
        vals = [v["rank"], v["vendedor"][:28], v["gerencia"][:26], v["num_ventas"],
                money(v["valor_colocado"]), money(v["valor_ingresado"])]
        print("  ".join(f"{str(x):<{w}}" if i in (1, 2) else f"{str(x):>{w}}"
                        for i, (x, w) in enumerate(zip(vals, widths))))
    print()


if __name__ == "__main__":
    top() if len(sys.argv) > 1 and sys.argv[1] == "top" else por_gerencia()
