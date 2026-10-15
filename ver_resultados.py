"""Corre las 5 funciones de TopRank y guarda cada resultado en un .json.

Uso:
    python ver_resultados.py --demo                      # datos de ejemplo, sin NetSuite
    python ver_resultados.py                             # NetSuite real, mes en curso
    python ver_resultados.py --desde 2026-09-01 --hasta 2026-09-30
    python ver_resultados.py --gerencia 60586            # solo una gerencia

Para NetSuite real define estas variables de entorno (Token-Based Authentication):
    NS_ACCOUNT          9628682
    NS_CONSUMER_KEY     (de la Integration record)
    NS_CONSUMER_SECRET
    NS_TOKEN_ID         (del Access Token)
    NS_TOKEN_SECRET

Requisitos para NetSuite real:  pip install requests requests-oauthlib
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import toprank as tr


class NetSuiteClient:
    """Cliente SuiteQL REST con TBA (OAuth 1.0, HMAC-SHA256). Pagina de 1000 en 1000."""

    PAGE = 1000

    def __init__(self, account: str, consumer_key: str, consumer_secret: str,
                 token_id: str, token_secret: str):
        import requests
        from requests_oauthlib import OAuth1

        host = account.lower().replace("_", "-")
        self.url = f"https://{host}.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql"
        self.session = requests.Session()
        self.session.auth = OAuth1(consumer_key, consumer_secret, token_id, token_secret,
                                   signature_method="HMAC-SHA256", realm=account.upper())
        self.session.headers.update({"Prefer": "transient", "Content-Type": "application/json"})

    @classmethod
    def from_env(cls) -> "NetSuiteClient":
        names = ["NS_ACCOUNT", "NS_CONSUMER_KEY", "NS_CONSUMER_SECRET", "NS_TOKEN_ID", "NS_TOKEN_SECRET"]
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            sys.exit(f"Faltan variables de entorno: {', '.join(missing)}  (o usa --demo)")
        return cls(*(os.environ[n] for n in names))

    def query(self, sql: str, label: str) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            r = self.session.post(self.url, params={"limit": self.PAGE, "offset": offset},
                                  json={"q": sql}, timeout=120)
            if r.status_code >= 400:
                raise RuntimeError(f"[{label}] NetSuite {r.status_code}: {r.text[:500]}")
            data = r.json()
            for item in data.get("items", []):
                item.pop("links", None)
                rows.append({k.lower(): v for k, v in item.items()})
            if not data.get("hasMore"):
                break
            offset += self.PAGE
        print(f"  {label}: {len(rows)} filas")
        return rows


FUNCIONES = {
    "F1_top_rank_por_gerencia": tr.top_rank_por_gerencia,
    "F2_vendedores_por_gerencia": tr.vendedores_por_gerencia,
    "F3_detalle_vendedor_por_desarrollo": tr.detalle_vendedor_por_desarrollo,
    "F4_vendedor_a_detalle": tr.vendedor_a_detalle,
    "F5_top_ranking_vendedores": tr.top_ranking_vendedores,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Genera los JSON de TopRank Sales.")
    ap.add_argument("--demo", action="store_true", help="usa datos de ejemplo en vez de NetSuite")
    ap.add_argument("--desde", type=date.fromisoformat, help="fecha inicial YYYY-MM-DD")
    ap.add_argument("--hasta", type=date.fromisoformat, help="fecha final YYYY-MM-DD (inclusiva)")
    ap.add_argument("--gerencia", type=int, action="append", help="id de gerencia (repetible)")
    ap.add_argument("--salida", default="resultados", help="carpeta de salida")
    args = ap.parse_args()

    if args.demo:
        from datos_ejemplo import LABELS, StubNS
        tr._today = lambda: date(2026, 10, 8)   # los datos de ejemplo son de octubre 2026
        make_ns, labels = StubNS, LABELS
    else:
        client = NetSuiteClient.from_env()
        make_ns, labels = (lambda: client), None

    out_dir = Path(args.salida)
    out_dir.mkdir(exist_ok=True)
    for nombre, fn in FUNCIONES.items():
        print(f"{nombre} ...")
        res = fn(make_ns(), args.desde, args.hasta, gerencia_ids=args.gerencia, labels=labels)
        path = out_dir / f"{nombre}.json"
        path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> {path}")

    f1 = json.loads((out_dir / "F1_top_rank_por_gerencia.json").read_text(encoding="utf-8"))
    t = f1["totals"]
    print(f"\nPeriodo: {f1['period']['label']}  |  ventas: {t['num_ventas']}  |  "
          f"colocado: ${t['valor_colocado']:,.0f}  |  ingresado: ${t['valor_ingresado']:,.0f}  |  "
          f"avance colocado: {t['pct_colocado']}%  (esperado {f1['expected_pct']}%)")


if __name__ == "__main__":
    main()
