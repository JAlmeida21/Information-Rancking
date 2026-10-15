"""Imprime resultados/F1_top_rank_por_gerencia.json como tabla, para compararlo con el dashboard.

Uso:  python resumen_f1.py
"""

import json
from pathlib import Path

data = json.loads(Path("resultados/F1_top_rank_por_gerencia.json").read_text(encoding="utf-8"))
ger = data["gerencias"]


def money(x):
    return f"${x:,.0f}"


cols = [("Gerencia", 12), ("Ventas", 7), ("Colocado", 14), ("Meta colocado", 14), ("% Col", 6),
        ("Ingresado", 13), ("Meta ingresado", 14), ("% Ing", 6)]
line = "  ".join(f"{name:>{w}}" if i else f"{name:<{w}}" for i, (name, w) in enumerate(cols))

print(f"\nTop Rank por Gerencia — {data['period']['label']}  "
      f"(ritmo esperado {data['expected_pct']}%, umbral {data['trend_threshold_pct']}%)\n")
print(line)
print("-" * len(line))
for g in sorted(ger, key=lambda g: -g["pct_colocado"]):
    vals = [g["zona"], g["num_ventas"], money(g["valor_colocado"]), money(g["meta_colocado"]),
            f"{g['pct_colocado']}%", money(g["valor_ingresado"]), money(g["meta_ingresado"]),
            f"{g['pct_ingresado']}%"]
    print("  ".join(f"{str(v):>{w}}" if i else f"{str(v):<{w}}" for i, (v, (_, w)) in enumerate(zip(vals, cols))))
t = data["totals"]
print("-" * len(line))
tot = ["TOTAL", t["num_ventas"], money(t["valor_colocado"]), money(t["meta_colocado"]),
       f"{t['pct_colocado']}%", money(t["valor_ingresado"]), money(t["meta_ingresado"]), ""]
print("  ".join(f"{str(v):>{w}}" if i else f"{str(v):<{w}}" for i, (v, (_, w)) in enumerate(zip(tot, cols))))
if data["metas_missing"]:
    print("\n(!) No hay metas de gerencia cargadas para este periodo.")
print()
