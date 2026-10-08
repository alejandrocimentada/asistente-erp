#!/usr/bin/env python3
"""Search the asistente-erp knowledge base (knowledge/**/*.yaml) in Spanish or English.

The agent answers from what this prints, not from memory. If nothing matches, the script
says so and exits with code 1: the agent must not invent a transaction.

Usage:
    python scripts/erp_lookup.py "ver stock en todas las tiendas"
    python scripts/erp_lookup.py "create purchase order" --top 2
    python scripts/erp_lookup.py --id stock-overview
    python scripts/erp_lookup.py --list
    python scripts/erp_lookup.py "MIGO" --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import erp_profile

SKILL_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = SKILL_DIR / "knowledge"

MIN_SCORE = 6.0         # below this, a match is too weak to show
RELATIVE_CUTOFF = 0.5   # also hide matches weaker than half of the best one

STOPWORDS = set("""
a al algo alguna alguno como con cual cuales de del donde el en entre es esta este esto hacer hago
la las le lo los me mi mis o para pero por que quiero se si sin su sus un una uno unos unas y yo
necesito puedo debo tengo hay
the a an and or of to in on for from by with how do does i my me is are can what which where
want need show please using use sap
""".split())

# Words people use -> the words the knowledge base uses. Applied after normalization and stemming.
SYNONYMS = {
    "tienda": {"centro", "plant", "store"}, "sucursal": {"centro", "plant", "tienda"},
    "planta": {"centro", "plant"}, "store": {"plant", "tienda"}, "branch": {"plant"},
    "bodega": {"almacen", "storage"}, "deposito": {"almacen"}, "warehouse": {"almacen", "storage"},
    "existencia": {"stock"}, "inventario": {"stock"}, "inventory": {"stock"},
    "orden": {"pedido"}, "oc": {"pedido", "orden", "compra"}, "po": {"purchase", "order"},
    "requisicion": {"solicitud"}, "solped": {"solicitud", "pedido"}, "pr": {"requisition"},
    "supplier": {"vendor", "proveedor"}, "vendor": {"supplier", "proveedor"}, "proveedor": {"vendor", "supplier"},
    "toda": {"todo"}, "todas": {"todo"}, "all": {"todo"},
    "ver": {"consultar", "visualizar", "display", "view"}, "mirar": {"ver"}, "revisar": {"ver", "consultar"},
    "consultar": {"ver"}, "show": {"display", "view"}, "see": {"display", "view"},
    "cambiar": {"modificar", "change"}, "editar": {"modificar"}, "actualizar": {"modificar"},
    "modificar": {"cambiar", "change"}, "edit": {"change"}, "update": {"change"},
    "crear": {"create", "nuevo"}, "hacer": {"crear"}, "generar": {"crear"}, "new": {"create"},
    "recibir": {"entrada", "receipt"}, "recepcion": {"entrada", "receipt"}, "receive": {"receipt"},
    "mover": {"traspaso", "transfer"}, "pasar": {"traspaso", "traspasar"}, "trasladar": {"traspaso", "traslado"}, "move": {"transfer"},
    "excel": {"exportar", "spreadsheet"}, "descargar": {"exportar"}, "bajar": {"exportar"},
    "download": {"export"}, "minimo": {"punto", "reorder"}, "reorden": {"punto", "pedido"},
    "articulo": {"material"}, "producto": {"material"}, "product": {"material"}, "item": {"material"},
}


# Words that point to SAP areas v1 does not cover. If one appears and no entry uses it as a
# name or keyword, the question is out of scope and nothing is returned.
OUT_OF_SCOPE = {
    **dict.fromkeys(["venta", "sale", "cliente", "customer", "va01", "facturacion", "billing", "cotizacion", "quotation"], "SD (ventas)"),
    **dict.fromkeys(["factura", "invoice", "miro", "pago", "payment", "asiento", "contabilidad", "accounting", "balance", "cuenta", "account"], "FI/CO (finanzas)"),
    **dict.fromkeys(["nomina", "payroll", "empleado", "employee", "vacacion", "vacation"], "HR (recursos humanos)"),
    **dict.fromkeys(["produccion", "production", "fabricacion", "manufacturing"], "PP (producción)"),
    **dict.fromkeys(["mantenimiento", "maintenance", "averia"], "PM (mantenimiento)"),
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9_/]+", " ", text).strip()


def stem(word: str) -> str:
    """Very light plural stripping for Spanish and English."""
    if len(word) > 5 and word.endswith("es") and word[-3] in "nlrdz":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and word[-2] not in "0123456789":
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in normalize(text).split() if w not in STOPWORDS]


def expand(token: str) -> set[str]:
    out = {token}
    for syn in SYNONYMS.get(token, ()):
        out.add(stem(syn))
    return out


@dataclass
class Match:
    entry: dict
    score: float
    reasons: list[str] = field(default_factory=list)
    company_names: list[dict] = field(default_factory=list)


def load_entries(knowledge_dir: Path = KNOWLEDGE_DIR) -> list[dict]:
    entries = []
    for f in sorted(knowledge_dir.rglob("*.yaml")):
        entry = yaml.safe_load(f.read_text(encoding="utf-8"))
        entry["_file"] = str(f.relative_to(knowledge_dir.parent)).replace("\\", "/")
        entries.append(entry)
    return entries


def _index(entry: dict) -> dict[str, float]:
    """token -> best weight for this entry."""
    weights: dict[str, float] = {}

    def add(text, w):
        for t in tokens(text):
            weights[t] = max(weights.get(t, 0), w)

    for lang in ("es", "en"):
        add(entry["names"].get(lang, ""), 3)
        for kw in entry.get("keywords", {}).get(lang, []):
            add(kw, 3)
    add(entry.get("summary", ""), 1)
    fiori = entry.get("fiori_app") or {}
    add(fiori.get("name", ""), 1.5)
    return weights


def _phrases(entry: dict) -> list[list[str]]:
    out = []
    for lang in ("es", "en"):
        for kw in entry.get("keywords", {}).get(lang, []):
            t = tokens(kw)
            if len(t) >= 2:
                out.append(t)
    return out


def _company_names_for(entry: dict, profile: dict) -> list[dict]:
    tcodes = {entry.get("tcode", "").upper()}
    return [n for n in profile.get("custom_names", [])
            if n.get("entry") == entry["id"] or (n.get("tcode") and n.get("tcode", "").upper() in tcodes)]


def out_of_scope(query: str, entries: list[dict]) -> str | None:
    """Return the SAP area a question belongs to when it is clearly outside this knowledge base."""
    known = set()
    for entry in entries:
        known.update(t for t, w in _index(entry).items() if w >= 3)
    for t in tokens(query):
        if t in OUT_OF_SCOPE and t not in known:
            return OUT_OF_SCOPE[t]
    return None


def search(query: str, entries: list[dict], profile: dict | None = None, top: int = 3) -> list[Match]:
    profile = profile or {}
    if out_of_scope(query, entries) and not any(
            normalize(n.get("term", "")) in normalize(query) for n in profile.get("custom_names", []) if n.get("term")):
        return []
    q_tokens = tokens(query)
    q_raw = set(normalize(query).split())
    q_expanded = set().union(*(expand(t) for t in q_tokens)) if q_tokens else set()
    q_norm = normalize(query)
    results = []
    for entry in entries:
        index = _index(entry)
        score, reasons = 0.0, []
        tcode = normalize(entry.get("tcode", ""))
        if tcode and tcode in q_raw:
            score += 20
            reasons.append(f"código {entry['tcode']}")
        fiori_id = normalize((entry.get("fiori_app") or {}).get("id", ""))
        if fiori_id and fiori_id in q_raw:
            score += 15
            reasons.append(f"app {fiori_id.upper()}")
        for t in q_tokens:
            best = max((index.get(x, 0) for x in expand(t)), default=0)
            score += best
        # Only the best keyword phrase counts, so overlapping phrases don't inflate the score.
        hits = [ph for ph in _phrases(entry) if all(p in q_expanded for p in ph)]
        if hits:
            best_phrase = max(hits, key=len)
            score += 2 * len(best_phrase)
            reasons.append("frase: " + " ".join(best_phrase))
        # The company's own names ("aquí lo llamamos X", "usamos ZMM_STOCK") point to an entry.
        for n in _company_names_for(entry, profile):
            term = normalize(n.get("term", ""))
            if (term and term in q_norm) or (n.get("tcode") and normalize(n["tcode"]) in q_raw):
                score += 20
                reasons.append(f"nombre de tu empresa: {n.get('term')}")
        results.append(Match(entry, score, reasons, _company_names_for(entry, profile)))
    results.sort(key=lambda m: (-m.score, m.entry["id"]))
    if not results or results[0].score < MIN_SCORE:
        return []
    cutoff = max(MIN_SCORE, results[0].score * RELATIVE_CUTOFF)
    return [m for m in results if m.score >= cutoff][:top]


def format_entry(entry: dict, company_names: list[dict] | None = None, profile: dict | None = None) -> str:
    profile = profile or {}
    out = []
    status = entry["verification"]["status"]
    out.append(f"## {entry['tcode']} · {entry['names']['es']}")
    out.append(f"   ({entry['names']['en']}) · id: {entry['id']} · verificación: {status} · última revisión: {entry['last_verified']}")
    for n in company_names or []:
        target = n.get("tcode") or entry["tcode"]
        out.append(f"   EN TU EMPRESA: \"{n.get('term')}\" → {target}" + (f" ({n['note']})" if n.get("note") else ""))
    out.append("")
    out.append(entry["summary"].strip())
    out.append("")
    out.append("ECC vs S/4HANA: " + entry["s4hana_notes"].strip())
    fiori = entry.get("fiori_app")
    if fiori:
        out.append(f"Fiori: {fiori['name']} ({fiori.get('id') or 'ID sin verificar'})" + (f". {fiori['note']}" if fiori.get("note") else ""))
    if profile.get("ui") == "fiori" and not fiori:
        out.append("Fiori: no hay app verificada para esta tarea; usa la transacción desde SAP GUI (o SAP GUI for HTML).")
    out.append("")
    out.append("Pasos:")
    for i, step in enumerate(entry["steps"], 1):
        out.append(f"  {i}. {step}")
    if entry.get("key_fields"):
        out.append("Campos clave:")
        for f in entry["key_fields"]:
            tech = f" [{f['technical']}]" if f.get("technical") else ""
            out.append(f"  - {f['label']}{tech}: {f['meaning']}")
    if entry.get("tables"):
        out.append("Tablas: " + "; ".join(f"{t['name']} ({t['description']})" for t in entry["tables"]))
    if entry.get("common_errors"):
        out.append("Errores comunes:")
        for e in entry["common_errors"]:
            mark = "" if e.get("verified") else " [sin verificar]"
            out.append(f"  - {e['error']}{mark} → {e['meaning']} Qué hacer: {e['what_to_do']}")
    unverified = entry["verification"].get("sin_verificar") or []
    if unverified:
        out.append("Sin verificar (dilo si lo usas):")
        out.extend(f"  - {u}" for u in unverified)
    out.append("Fuentes:")
    out.extend(f"  - {s['title']}: {s['url']}" for s in entry["sources"])
    if entry.get("related_tcodes"):
        out.append("Relacionadas: " + ", ".join(entry["related_tcodes"]))
    return "\n".join(out)


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Busca en la base de conocimiento de asistente-erp")
    p.add_argument("query", nargs="*", help="pregunta en español o inglés")
    p.add_argument("--top", type=int, default=3, help="máximo de entradas (por defecto 3)")
    p.add_argument("--id", help="muestra una entrada por su id")
    p.add_argument("--list", action="store_true", help="lista todas las entradas")
    p.add_argument("--json", action="store_true", help="salida en JSON")
    p.add_argument("--no-profile", action="store_true", help="ignora el perfil de empresa")
    args = p.parse_args(argv)

    entries = load_entries()
    profile = {}
    if not args.no_profile:
        try:
            profile = erp_profile.load()
        except erp_profile.ProfileError as exc:
            print(f"Aviso: {exc}", file=sys.stderr)

    if args.list:
        rows = [{"id": e["id"], "tcode": e["tcode"], "es": e["names"]["es"], "status": e["verification"]["status"]} for e in entries]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f"{r['tcode']:<20} {r['id']:<30} [{r['status']}] {r['es']}")
        return 0

    if args.id:
        found = [e for e in entries if e["id"] == args.id]
        if not found:
            print(f"No existe la entrada '{args.id}'. Usa --list para ver las disponibles.", file=sys.stderr)
            return 1
        e = found[0]
        if args.json:
            print(json.dumps(_public(e), ensure_ascii=False, indent=2, default=str))
        else:
            print(format_entry(e, _company_names_for(e, profile), profile))
        return 0

    query = " ".join(args.query).strip()
    if not query:
        p.error("escribe una pregunta, o usa --id / --list")
    matches = search(query, entries, profile, top=args.top)
    if args.json:
        print(json.dumps([{"score": m.score, "reasons": m.reasons, "company_names": m.company_names,
                           "entry": _public(m.entry)} for m in matches], ensure_ascii=False, indent=2, default=str))
        return 0 if matches else 1
    if not matches:
        print(f"SIN RESULTADOS para: \"{query}\".")
        area = out_of_scope(query, entries)
        if area:
            print(f"Parece una pregunta de {area}, que todavía no está en la base de conocimiento.")
        print("La base de conocimiento (v1: MM inventario y compras) no cubre esta pregunta.")
        print("No inventes una transacción: dilo así y sugiere preguntar al usuario clave (key user) de SAP.")
        return 1
    print(f"Resultados para: \"{query}\" ({len(matches)})\n")
    for m in matches:
        print(format_entry(m.entry, m.company_names, profile))
        print(f"   (puntaje {m.score:g}{': ' + ', '.join(m.reasons) if m.reasons else ''})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
