import json
import subprocess
import sys
from pathlib import Path

import pytest

import erp_lookup as lk
import erp_profile

ROOT = Path(__file__).resolve().parent.parent
ENTRIES = lk.load_entries()


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv(erp_profile.ENV_VAR, str(tmp_path / "profile.yaml"))


def top(query, profile=None):
    matches = lk.search(query, ENTRIES, profile or {})
    return matches[0].entry["id"] if matches else None


@pytest.mark.parametrize("query, expected", [
    # Spanish
    ("ver stock en todas las tiendas", "stock-overview"),
    ("cuánto hay de un material en la bodega", "stock-overview"),
    ("lista de stock de todo el almacén", "warehouse-stock"),
    ("movimientos de un material", "material-documents"),
    ("cuándo llega lo que pedimos", "stock-requirements-list"),
    ("ver datos maestros del material", "material-display"),
    ("modificar material", "material-change"),
    ("stock mínimo de un material", "mrp-reorder-fields"),
    ("crear orden de compra", "purchase-order-create"),
    ("cambiar la cantidad de un pedido", "purchase-order-change"),
    ("ver orden de compra", "purchase-order-display"),
    ("ver pedidos de un proveedor", "po-list-by-vendor"),
    ("qué pedidos de compra hay del material 4711", "po-list-by-material"),
    ("crear solicitud de pedido", "purchase-requisition-create"),
    ("solicitud de pedido 1000123 ya fue aprobada", "purchase-requisition-display"),
    ("registrar entrada de mercancía", "goods-receipt-po"),
    ("pasar stock de un almacén a otro", "stock-transfer"),
    ("enviar mercancía a otra tienda", "stock-transport-order"),
    ("quiero bajar el reporte a Excel", "export-to-excel"),
    # English
    ("create purchase order", "purchase-order-create"),
    ("change purchase order", "purchase-order-change"),
    ("stock overview all plants", "stock-overview"),
    ("material document list", "material-documents"),
    ("display material master", "material-display"),
    ("reorder point", "mrp-reorder-fields"),
    ("purchase orders by vendor", "po-list-by-vendor"),
    ("goods receipt", "goods-receipt-po"),
    ("transfer stock between storage locations", "stock-transfer"),
    ("export list to excel", "export-to-excel"),
    # Transaction codes and Fiori IDs
    ("MMBE", "stock-overview"),
    ("me21n", "purchase-order-create"),
    ("F1595", "warehouse-stock"),
])
def test_lookup_finds_the_right_entry(query, expected):
    assert top(query) == expected


def test_create_po_is_not_confused_with_requisition():
    matches = lk.search("crear orden de compra", ENTRIES)
    assert matches[0].entry["tcode"] == "ME21N"
    assert matches[0].score > matches[1].score if len(matches) > 1 else True


@pytest.mark.parametrize("query", [
    "how do I create a sales order",
    "registrar una factura de proveedor",
    "cómo pago la nómina",
    "xyzzy plugh",
])
def test_out_of_scope_or_nonsense_returns_nothing(query):
    assert lk.search(query, ENTRIES) == []


def test_stemming_and_accents():
    assert lk.tokens("Almacenes TIENDAS Órdenes") == ["almacen", "tienda", "orden"]


def test_company_name_from_profile_points_to_entry():
    profile = {"custom_names": [{"term": "consulta rápida", "tcode": "ZMM_STOCK", "entry": "stock-overview", "note": ""}]}
    assert top("cómo abro la consulta rápida", profile) == "stock-overview"
    assert top("ZMM_STOCK", profile) == "stock-overview"
    m = lk.search("ver stock en todas las tiendas", ENTRIES, profile)[0]
    assert m.company_names and m.company_names[0]["tcode"] == "ZMM_STOCK"
    assert "EN TU EMPRESA" in lk.format_entry(m.entry, m.company_names)


def test_company_name_overrides_out_of_scope_word():
    # If the company calls something "consulta de clientes" and maps it, it must be found.
    profile = {"custom_names": [{"term": "stock de clientes", "tcode": "ZMM_CLI", "entry": "stock-overview"}]}
    assert top("abrir stock de clientes", profile) == "stock-overview"


def test_format_entry_contains_everything_the_agent_needs():
    e = next(x for x in ENTRIES if x["id"] == "stock-transport-order")
    text = lk.format_entry(e)
    assert text.startswith("## ME21N")
    for part in ["Pasos:", "Tablas:", "Errores comunes:", "Sin verificar", "Fuentes:", "https://help.sap.com/"]:
        assert part in text


def run_cli(*args, env_profile):
    env = {**__import__("os").environ, erp_profile.ENV_VAR: str(env_profile), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "erp_lookup.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_cli_answer_and_exit_codes(tmp_path):
    r = run_cli("crear orden de compra", env_profile=tmp_path / "p.yaml")
    assert r.returncode == 0 and "## ME21N" in r.stdout
    r = run_cli("how do I create a sales order", env_profile=tmp_path / "p.yaml")
    assert r.returncode == 1 and "SIN RESULTADOS" in r.stdout and "No inventes" in r.stdout
    r = run_cli("--id", "stock-overview", "--json", env_profile=tmp_path / "p.yaml")
    assert r.returncode == 0 and json.loads(r.stdout)["tcode"] == "MMBE"
    r = run_cli("--list", env_profile=tmp_path / "p.yaml")
    assert r.returncode == 0 and len(r.stdout.strip().splitlines()) == len(ENTRIES)
    assert not (tmp_path / "p.yaml").exists()   # lookup only reads the profile
