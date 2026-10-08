import datetime as dt
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "knowledge").rglob("*.yaml"))
REQUIRED = ["id", "names", "tcode", "s4hana_notes", "fiori_app", "steps", "key_fields", "tables",
            "common_errors", "sources", "last_verified", "keywords", "summary", "verification"]
TCODE = re.compile(r"^[A-Z][A-Z0-9_/]{2,19}$")
TABLE = re.compile(r"^[A-Z][A-Z0-9_]{2,15}$")
TRUSTED_SOURCES = ("https://help.sap.com/",)


def load(f):
    return yaml.safe_load(f.read_text(encoding="utf-8"))


ENTRIES = {f.stem: load(f) for f in FILES}


def test_knowledge_base_has_all_v1_tasks():
    tcodes = {e["tcode"] for e in ENTRIES.values()}
    for code in ["MMBE", "MB52", "MB51", "MD04", "MM03", "MM02", "ME21N", "ME22N", "ME23N",
                 "ME2L", "ME2M", "ME51N", "ME53N", "MIGO"]:
        assert code in tcodes, f"falta la transacción {code}"
    ids = set(ENTRIES)
    assert {"mrp-reorder-fields", "stock-transfer", "stock-transport-order", "export-to-excel"} <= ids


def test_ids_are_unique_and_match_file_names():
    ids = [e["id"] for e in ENTRIES.values()]
    assert len(ids) == len(set(ids))
    for stem, e in ENTRIES.items():
        assert e["id"] == stem


@pytest.mark.parametrize("stem", sorted(ENTRIES))
def test_entry_has_all_fields(stem):
    e = ENTRIES[stem]
    for key in REQUIRED:
        assert key in e, f"{stem}: falta el campo {key}"
    assert e["module"] == "MM"
    assert e["names"]["es"].strip() and e["names"]["en"].strip()
    assert e["keywords"]["es"] and e["keywords"]["en"]
    assert e["summary"].strip() and e["s4hana_notes"].strip()


@pytest.mark.parametrize("stem", sorted(ENTRIES))
def test_tcodes_are_well_formed(stem):
    e = ENTRIES[stem]
    if e["id"] == "export-to-excel":
        assert e["tcode"].startswith("(")   # not a transaction: works in any report
    else:
        assert TCODE.match(e["tcode"]), e["tcode"]
    for code in e.get("related_tcodes", []):
        assert TCODE.match(code), code


@pytest.mark.parametrize("stem", sorted(ENTRIES))
def test_steps_fields_tables_and_errors(stem):
    e = ENTRIES[stem]
    assert len(e["steps"]) >= 3 and all(isinstance(s, str) and s.strip() for s in e["steps"])
    for f in e["key_fields"]:
        assert f["label"] and f["meaning"] and "technical" in f
    for t in e["tables"]:
        assert TABLE.match(t["name"]) and t["description"]
    if e["id"] != "export-to-excel":
        assert e["tables"], f"{stem}: sin tablas"
    assert e["common_errors"]
    for err in e["common_errors"]:
        assert err["error"] and err["meaning"] and err["what_to_do"]
        assert isinstance(err["verified"], bool)


@pytest.mark.parametrize("stem", sorted(ENTRIES))
def test_fiori_app(stem):
    app = ENTRIES[stem]["fiori_app"]
    if app is not None:
        assert app["name"]
        assert re.match(r"^F\d{4}[A-Z]?$", app["id"]), app["id"]


@pytest.mark.parametrize("stem", sorted(ENTRIES))
def test_sources_and_verification(stem):
    e = ENTRIES[stem]
    assert len(e["sources"]) >= 1
    for s in e["sources"]:
        assert s["title"].strip()
        assert s["url"].startswith(TRUSTED_SOURCES), s["url"]
        assert " " not in s["url"]
    assert isinstance(e["last_verified"], dt.date)
    v = e["verification"]
    assert v["status"] in {"verificado", "parcial"}
    assert isinstance(v["sin_verificar"], list)
    if v["status"] == "parcial":
        assert v["sin_verificar"], f"{stem}: parcial sin decir qué falta verificar"


def test_s4hana_table_notes_are_consistent():
    # Every entry that lists the ECC material document tables must also list MATDOC (S/4HANA).
    for stem, e in ENTRIES.items():
        names = {t["name"] for t in e["tables"]}
        if names & {"MKPF", "MSEG"}:
            assert "MATDOC" in names, stem
