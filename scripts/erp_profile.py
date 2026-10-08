#!/usr/bin/env python3
"""Company profile for asistente-erp: SAP version, UI, language and the company's own names.

The profile lives in the user's home folder (~/.config/asistente-erp/profile.yaml), never
inside the skill folder, so updating or reinstalling the skill does not erase it.
Set ASISTENTE_ERP_PROFILE to use another path (used by the tests).

Usage:
    python scripts/erp_profile.py show
    python scripts/erp_profile.py path
    python scripts/erp_profile.py set sap_product s4hana
    python scripts/erp_profile.py add-name --term "consulta de stock" --tcode ZMM_STOCK --entry stock-overview
    python scripts/erp_profile.py remove-name --term "consulta de stock"
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
import unicodedata
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = SKILL_DIR / "knowledge"
ENV_VAR = "ASISTENTE_ERP_PROFILE"

# key -> allowed values (None = free text). Booleans are parsed from yes/no.
FIELDS = {
    "sap_product": {"ecc", "s4hana", "s4hana_cloud", "unknown"},
    "sap_release": None,          # e.g. "EHP8", "S/4HANA 2023", as shown in System > Status
    "ui": {"gui", "fiori", "both"},
    "sap_language": {"es", "en", "pt", "other"},
    "uses_z_transactions": "bool",
    "company": None,
    "notes": None,
}
BOOL_WORDS = {"yes": True, "si": True, "true": True, "1": True,
              "no": False, "false": False, "0": False}


class ProfileError(ValueError):
    """Invalid profile path, key or value."""


def default_profile() -> dict:
    return {
        "sap_product": "unknown",
        "sap_release": "",
        "ui": "gui",
        "sap_language": "es",
        "uses_z_transactions": False,
        "company": "",
        "notes": "",
        "custom_names": [],
        "updated": "",
    }


def profile_path() -> Path:
    raw = os.environ.get(ENV_VAR)
    path = Path(raw).expanduser() if raw else Path.home() / ".config" / "asistente-erp" / "profile.yaml"
    path = path.resolve()
    if path == SKILL_DIR or SKILL_DIR in path.parents:
        raise ProfileError(f"El perfil no puede guardarse dentro de la carpeta del skill ({SKILL_DIR}).")
    return path


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def load(path: Path | None = None) -> dict:
    path = path or profile_path()
    data = default_profile()
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ProfileError(f"El perfil {path} no tiene un formato válido.")
        data.update(loaded)
    data["custom_names"] = list(data.get("custom_names") or [])
    return data


def save(data: dict, path: Path | None = None) -> Path:
    path = path or profile_path()
    data = dict(data)
    data["updated"] = dt.date.today().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".profile-", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
    return path


def set_field(data: dict, key: str, value: str) -> dict:
    if key not in FIELDS:
        raise ProfileError(f"Campo desconocido '{key}'. Campos válidos: {', '.join(FIELDS)}.")
    allowed = FIELDS[key]
    if allowed == "bool":
        word = norm(value)
        if word not in BOOL_WORDS:
            raise ProfileError(f"'{key}' debe ser yes o no.")
        data[key] = BOOL_WORDS[word]
    elif allowed is None:
        data[key] = value.strip()
    else:
        value = value.strip().lower()
        if value not in allowed:
            raise ProfileError(f"'{key}' debe ser uno de: {', '.join(sorted(allowed))}.")
        data[key] = value
    return data


def known_entry_ids() -> set[str]:
    ids = set()
    for f in KNOWLEDGE_DIR.rglob("*.yaml"):
        entry = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        ids.add(entry.get("id"))
    return ids


def add_name(data: dict, term: str, tcode: str = "", entry: str = "", note: str = "") -> dict:
    term = term.strip()
    if not term:
        raise ProfileError("Falta el nombre que usa la empresa (--term).")
    if not tcode and not entry:
        raise ProfileError("Indica a qué corresponde: --tcode (por ejemplo ZMM_STOCK), --entry o ambos.")
    if entry and entry not in known_entry_ids():
        raise ProfileError(f"No existe la entrada '{entry}' en la base de conocimiento.")
    item = {"term": term, "tcode": tcode.strip().upper(), "entry": entry.strip(), "note": note.strip(),
            "added": dt.date.today().isoformat()}
    names = [n for n in data.get("custom_names", []) if norm(n.get("term", "")) != norm(term)]
    names.append(item)
    data["custom_names"] = names
    if item["tcode"].startswith(("Z", "Y")):
        data["uses_z_transactions"] = True
    return data


def remove_name(data: dict, term: str) -> bool:
    before = len(data.get("custom_names", []))
    data["custom_names"] = [n for n in data.get("custom_names", []) if norm(n.get("term", "")) != norm(term)]
    return len(data["custom_names"]) < before


def describe(data: dict, path: Path) -> str:
    exists = path.exists()
    lines = [f"Perfil: {path}" + ("" if exists else " (todavía no existe: primer uso)")]
    for key in FIELDS:
        lines.append(f"  {key}: {data.get(key)!r}")
    names = data.get("custom_names") or []
    lines.append(f"  custom_names: {len(names)}")
    for n in names:
        target = " / ".join(x for x in (n.get("tcode"), n.get("entry")) if x)
        lines.append(f"    - \"{n.get('term')}\" -> {target}" + (f"  ({n['note']})" if n.get("note") else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Perfil de empresa de asistente-erp")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="muestra el perfil")
    sub.add_parser("path", help="muestra la ruta del perfil")
    s = sub.add_parser("set", help="cambia un campo")
    s.add_argument("key")
    s.add_argument("value")
    a = sub.add_parser("add-name", help="guarda un nombre propio de la empresa")
    a.add_argument("--term", required=True, help='como lo llaman en la empresa, p. ej. "consulta de stock"')
    a.add_argument("--tcode", default="", help="transacción propia, p. ej. ZMM_STOCK")
    a.add_argument("--entry", default="", help="id de la entrada estándar, p. ej. stock-overview")
    a.add_argument("--note", default="")
    r = sub.add_parser("remove-name", help="borra un nombre propio")
    r.add_argument("--term", required=True)
    args = p.parse_args(argv)

    try:
        path = profile_path()
        if args.cmd == "path":
            print(path)
            return 0
        data = load(path)
        if args.cmd == "show":
            print(describe(data, path))
            return 0
        if args.cmd == "set":
            set_field(data, args.key, args.value)
        elif args.cmd == "add-name":
            add_name(data, args.term, args.tcode, args.entry, args.note)
        elif args.cmd == "remove-name":
            if not remove_name(data, args.term):
                print(f"No había ningún nombre guardado como \"{args.term}\".")
                return 1
        save(data, path)
        print(f"Guardado en {path}")
        print(describe(data, path))
        return 0
    except ProfileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
