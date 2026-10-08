import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import erp_profile as sp

ROOT = Path(__file__).resolve().parent.parent


def skill_files():
    return sorted(p for p in ROOT.rglob("*")
                  if p.is_file() and not any(part in {".git", "__pycache__", ".pytest_cache"} for part in p.parts))


@pytest.fixture
def profile_file(tmp_path, monkeypatch):
    path = tmp_path / "home" / ".config" / "asistente-erp" / "profile.yaml"
    monkeypatch.setenv(sp.ENV_VAR, str(path))
    return path


def test_default_path_is_in_home_config(monkeypatch, tmp_path):
    monkeypatch.delenv(sp.ENV_VAR, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert sp.profile_path() == (tmp_path / ".config" / "asistente-erp" / "profile.yaml").resolve()


@pytest.mark.parametrize("inside", ["profile.yaml", "knowledge/profile.yaml", "scripts/x/profile.yaml"])
def test_refuses_to_use_a_path_inside_the_skill(monkeypatch, inside):
    monkeypatch.setenv(sp.ENV_VAR, str(ROOT / inside))
    with pytest.raises(sp.ProfileError):
        sp.profile_path()


def test_load_missing_profile_gives_defaults(profile_file):
    data = sp.load()
    assert data["sap_product"] == "unknown" and data["custom_names"] == []
    assert not profile_file.exists()


def test_save_and_load_roundtrip(profile_file):
    before = skill_files()
    data = sp.load()
    sp.set_field(data, "sap_product", "s4hana")
    sp.set_field(data, "sap_release", "S/4HANA 2023")
    sp.set_field(data, "ui", "fiori")
    sp.set_field(data, "uses_z_transactions", "sí")
    sp.add_name(data, "consulta de stock", tcode="zmm_stock", entry="stock-overview", note="la usa almacén")
    sp.save(data)
    assert profile_file.exists()
    again = sp.load()
    assert again["sap_product"] == "s4hana" and again["ui"] == "fiori"
    assert again["uses_z_transactions"] is True
    assert again["custom_names"][0]["tcode"] == "ZMM_STOCK"
    assert again["custom_names"][0]["entry"] == "stock-overview"
    assert again["updated"]
    assert skill_files() == before          # nothing written inside the skill folder


def test_add_name_updates_instead_of_duplicating(profile_file):
    data = sp.load()
    sp.add_name(data, "Pedido rápido", tcode="ZME21")
    sp.add_name(data, "pedido rapido", tcode="ZME21N")      # same term, different accents/case
    assert len(data["custom_names"]) == 1 and data["custom_names"][0]["tcode"] == "ZME21N"
    assert data["uses_z_transactions"] is True


@pytest.mark.parametrize("kwargs", [
    {"term": "", "tcode": "ZX"},
    {"term": "algo"},                                   # no tcode and no entry
    {"term": "algo", "entry": "no-existe"},
])
def test_add_name_validation(profile_file, kwargs):
    with pytest.raises(sp.ProfileError):
        sp.add_name(sp.load(), **kwargs)


@pytest.mark.parametrize("key, value", [("ui", "web"), ("sap_product", "r3"), ("nope", "x"), ("uses_z_transactions", "quizá")])
def test_set_field_validation(key, value):
    with pytest.raises(sp.ProfileError):
        sp.set_field(sp.default_profile(), key, value)


def test_remove_name(profile_file):
    data = sp.load()
    sp.add_name(data, "consulta de stock", tcode="ZMM_STOCK")
    assert sp.remove_name(data, "Consulta de Stock") is True
    assert sp.remove_name(data, "consulta de stock") is False


def run_cli(*args, profile):
    env = {**os.environ, sp.ENV_VAR: str(profile), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "erp_profile.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_cli_writes_only_to_the_profile(tmp_path):
    before = skill_files()
    profile = tmp_path / "cfg" / "profile.yaml"
    assert run_cli("set", "sap_product", "ecc", profile=profile).returncode == 0
    r = run_cli("add-name", "--term", "aquí lo llamamos stock rápido", "--tcode", "ZMM_STOCK",
                "--entry", "stock-overview", profile=profile)
    assert r.returncode == 0, r.stderr
    data = yaml.safe_load(profile.read_text(encoding="utf-8"))
    assert data["sap_product"] == "ecc" and data["custom_names"][0]["tcode"] == "ZMM_STOCK"
    assert "ZMM_STOCK" in run_cli("show", profile=profile).stdout
    assert run_cli("set", "ui", "web", profile=profile).returncode == 2
    assert skill_files() == before


def test_cli_refuses_profile_inside_skill():
    r = run_cli("set", "ui", "gui", profile=ROOT / "profile.yaml")
    assert r.returncode == 2 and "carpeta del skill" in r.stderr
    assert not (ROOT / "profile.yaml").exists()
