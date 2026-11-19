#!/usr/bin/env python3
"""Build a reviewable list of name candidates from Wikidata and Open Food Facts.

Writes, relative to the repository root:

  data/name-candidates.tsv   one row per food concept, English variants and
                             Nordic labels, plus a suggested rename where the
                             sources give real evidence for one
  data/names-sv.tsv          English name -> Swedish name, for a later
                             "translate to Swedish" mode
  data/names-candidates.toml the suggested renames in names.toml format, all
                             commented out, for a person to pick from

Sources:
  Wikidata (CC0), via https://query.wikidata.org/sparql
  Open Food Facts ingredient taxonomy (openfoodfacts-server, AGPL-3.0),
  taxonomies/food/ingredients.txt

The taxonomy is downloaded into a fresh temporary directory, parsed as plain
text and deleted afterwards. Nothing from it is executed.

Usage:  python3 tools/build_name_candidates.py [--off-file PATH] [--out DIR]
Stdlib only.
"""

import argparse
import csv
import difflib
import json
import re
import shutil
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES_TOML = ROOT / "cookbook_metric" / "names.toml"

UA = "cookbook-metric/0.1 (name table builder)"
SPARQL = "https://query.wikidata.org/sparql"
OFF_URL = ("https://raw.githubusercontent.com/openfoodfacts/openfoodfacts-server/"
           "main/taxonomies/food/ingredients.txt")

NORDIC = ("sv", "da", "nb", "fi")

# Wikidata classes whose subclasses (and instances) are home-kitchen things.
# Each is queried on its own so no single query times out.
WD_ROOTS = {
    "Q11004": "vegetable",
    "Q20134": "leaf vegetable",
    "Q20136": "root vegetable",
    "Q1470762": "fruit vegetable",
    "Q3314483": "fruit (food)",
    "Q207123": "herb",
    "Q42527": "spice",
    "Q185217": "dairy product",
    "Q8495": "milk",
    "Q36465": "flour",
    "Q11002": "sugar",
    "Q12117": "cereal",
    "Q145909": "legume",
    "Q11009": "nut",
    "Q654236": "edible mushroom",
    "Q10990": "meat",
    "Q139960435": "cut of meat",
    "Q6418247": "primal cut",
    "Q192935": "seafood",
    "Q427457": "cooking oil",
    "Q912613": "edible fats and oils",
    "Q41354": "vinegar",
    "Q93189": "egg as food",
    "Q2596997": "condiment",
}

# Items that are instances of these are left out: cultivars, brands, products.
WD_EXCLUDE_P31 = ("Q4886", "Q431289", "Q167270", "Q2424752", "Q10429667")

# Open Food Facts nodes whose descendants are home-kitchen things.
OFF_KEEP = {
    "fruit", "vegetable", "herb", "spice", "dairy", "milk", "cheese", "cream",
    "butter", "flour", "sugar", "meat", "fish", "seafood", "crustacean",
    "mollusc", "nut", "tree nut", "pulse", "legume", "cereal", "rice", "pasta",
    "mushroom", "egg", "vegetable oil", "vinegar", "salt", "chocolate", "syrup",
    "honey", "seed", "starch", "seaweed", "poultry", "condiment", "yeast",
    "baking powder", "leaf vegetable", "root vegetable", "fruit vegetable",
    "citrus fruit", "berries", "alliaceae", "onion family vegetable",
}
OFF_DROP = {"varietal", "flavouring", "natural flavouring", "additive",
            "food additive", "e-number", "preparation", "colour", "vitamins",
            "minerals", "lactic ferments", "alcohol", "wine", "liqueur"}

WORD_RX = re.compile(r"^[a-z][a-z' -]*[a-z]$")

# Names the sources do pair with a Nordic or British word, but which mean
# something else in a US recipe, so renaming them would be wrong. Checked by
# hand on 2026-11-19 while reviewing the first run.
DENY_FROM = {
    "tomato sauce",     # en-gb label of ketchup; a US tomato sauce is passata-like
    "corn",             # en-us label of maize; in a US recipe it is sweetcorn
    "perry",            # pear cider, not must
    "chocolate milk",   # not a milkshake
    "pickle", "pickles", "pickled cucumber",  # US "pickle" is a dill pickle
    "breba",            # first-crop fig, not every fig
    "quail meat",       # en-gb label "quails in cookery" is a page title
    "fillet",           # "filet" is the Danish word, not better English
    "chinese cabbage",  # napa cabbage (kinakal), lumped with pak choi in OFF
    "peppercorn", "peppercorns",  # whole pepper is a different thing to ground
    "gourd",            # every gourd is not a squash
    "tangerine", "tangerines",  # not the same fruit as satsuma
    "strawberry milk",  # not a milkshake
    "snap bean",        # a green bean, not a runner bean
}

# Latin-looking: "Gadus morhua", "Aristeidae", "Bambuseae".
LATIN_RX = re.compile(r"^(?:[A-Z][a-z]+ [a-z]+(?: [a-z]+)?|[A-Z][a-z]+(?:idae|inae|eae|aceae))$")


def log(*a):
    print(*a, file=sys.stderr)


def norm(s):
    return re.sub(r"[\s-]+", " ", s.strip().lower())


def clean_name(s, max_words=4):
    """A usable English kitchen name, or None for junk."""
    s = s.strip()
    n = norm(s)
    if not n or len(n) > 40 or len(n.split()) > max_words:
        return None
    if not WORD_RX.match(n) or re.fullmatch(r"q\d+", n):
        return None
    return s


def lower_if_plain(s):
    """Lower-case a label unless it looks like a proper name inside (Dijon)."""
    return s if any(w[:1].isupper() for w in s.split()[1:]) else s[:1].lower() + s[1:]


# ---- Wikidata

def sparql(query, tries=5):
    data = urllib.parse.urlencode({"query": query}).encode()
    for attempt in range(tries):
        req = urllib.request.Request(SPARQL, data=data, headers={
            "User-Agent": UA, "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)["results"]["bindings"]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            wait = 5 * (attempt + 1)
            if isinstance(e, urllib.error.HTTPError) and e.headers.get("Retry-After", "").isdigit():
                wait = int(e.headers["Retry-After"]) + 1
            log(f"  sparql error {e}; retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError("SPARQL query kept failing")


LABELS = """
  ?i rdfs:label ?en FILTER(lang(?en) = "en")
  OPTIONAL { ?i rdfs:label ?engb FILTER(lang(?engb) = "en-gb") }
  OPTIONAL { ?i rdfs:label ?enus FILTER(lang(?enus) = "en-us") }
  OPTIONAL { ?i rdfs:label ?sv FILTER(lang(?sv) = "sv") }
  OPTIONAL { ?i rdfs:label ?da FILTER(lang(?da) = "da") }
  OPTIONAL { ?i rdfs:label ?nb FILTER(lang(?nb) = "nb") }
  OPTIONAL { ?i rdfs:label ?fi FILTER(lang(?fi) = "fi") }
  OPTIONAL { ?i skos:altLabel ?alt FILTER(lang(?alt) = "en") }
  OPTIONAL { ?i wdt:P225 ?taxon }
"""
SELECT = """SELECT ?i ?en ?engb ?enus ?sv ?da ?nb ?fi
  (GROUP_CONCAT(DISTINCT ?alt; separator="|") AS ?alts)
  (GROUP_CONCAT(DISTINCT ?taxon; separator="|") AS ?taxa) WHERE {"""
GROUP = "} GROUP BY ?i ?en ?engb ?enus ?sv ?da ?nb ?fi"


def wd_rows(bindings, root):
    out = {}
    for b in bindings:
        qid = b["i"]["value"].rsplit("/", 1)[-1]
        v = {k: b[k]["value"] for k in b if k != "i"}
        row = out.setdefault(qid, {"id": qid, "roots": {root}})
        for k in ("en", "engb", "enus", "sv", "da", "nb", "fi"):
            if k in v and k not in row:
                row[k] = v[k]
        row.setdefault("alts", set()).update(a for a in v.get("alts", "").split("|") if a)
        row.setdefault("taxa", set()).update(t for t in v.get("taxa", "").split("|") if t)
    return out


def fetch_wikidata(extra_qids):
    """Two steps per class: the item ids (cheap), then labels in chunks."""
    excl = " ".join("wd:" + q for q in WD_EXCLUDE_P31)
    roots = {}
    for qid, name in WD_ROOTS.items():
        log(f"wikidata: {name} ({qid})")
        q = f"""SELECT DISTINCT ?i WHERE {{
  ?i wdt:P31?/wdt:P279* wd:{qid} .
  FILTER NOT EXISTS {{ VALUES ?x {{ {excl} }} ?i wdt:P31 ?x }}
}}"""
        for b in sparql(q):
            roots.setdefault(b["i"]["value"].rsplit("/", 1)[-1], set()).add(name)
        time.sleep(2)
    for q in extra_qids:
        roots.setdefault(q, set()).add("linked from OFF")
    ids = sorted((q for q in roots if re.fullmatch(r"Q\d+", q)), key=lambda q: int(q[1:]))
    items = {}
    step = 250
    for i in range(0, len(ids), step):
        chunk = ids[i:i + step]
        log(f"wikidata: labels {i + 1}-{i + len(chunk)} of {len(ids)}")
        q = f"{SELECT}\n  VALUES ?i {{ {' '.join('wd:' + x for x in chunk)} }}\n{LABELS}\n{GROUP}"
        for k, r in wd_rows(sparql(q), "").items():
            r["roots"] = roots[k]
            items[k] = r
        time.sleep(2)
    return items


# ---- Open Food Facts

def download_off():
    tmp = Path(tempfile.mkdtemp(prefix="off-taxonomy-"))
    path = tmp / "ingredients.txt"
    log(f"open food facts: downloading into {tmp}")
    req = urllib.request.Request(OFF_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)
    return tmp, path


def split_names(value):
    return [s.strip() for s in value.split(",") if s.strip()]


def parse_off(path):
    """Entries with en names, Nordic names, parents and Wikidata link."""
    entries = []
    block = []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines() + [""]:
        line = line.rstrip()
        if line.strip():
            block.append(line)
            continue
        if not block:
            continue
        e = {"parents": [], "langs": {}}
        for ln in block:
            if ln.startswith("#"):
                continue
            if ln.startswith("<"):
                p = ln[1:].strip()
                if p.startswith("en:"):
                    e["parents"].append(norm(p[3:]))
                continue
            m = re.match(r"^([a-z]{2,3}(?:_[a-z]+)?):\s*(.*)$", ln)
            if m and m[1] in ("en",) + NORDIC:
                e["langs"][m[1]] = split_names(m[2])
            elif ln.startswith("wikidata:en:"):
                q = ln.split(":", 2)[2].strip()
                if re.fullmatch(r"Q\d+", q):
                    e["wikidata"] = q
        block = []
        if e["langs"].get("en"):
            e["id"] = "en:" + re.sub(r"\s+", "-", norm(e["langs"]["en"][0]))
            entries.append(e)
    # Resolve parents by any English name, then keep home-kitchen descendants.
    by_name = {}
    for e in entries:
        for n in e["langs"]["en"]:
            by_name.setdefault(norm(n), e)

    def ancestors(e, seen=None):
        seen = set() if seen is None else seen
        for p in e["parents"]:
            if p in seen:
                continue
            seen.add(p)
            if p in by_name:
                ancestors(by_name[p], seen)
        return seen

    kept = []
    for e in entries:
        anc = ancestors(e) | {norm(e["langs"]["en"][0])}
        if anc & OFF_DROP or not anc & OFF_KEEP:
            continue
        kept.append(e)
    return kept


# ---- Merge and suggest

def existing_names():
    data = tomllib.loads(NAMES_TOML.read_text(encoding="utf-8"))
    news = {norm(k) for k in data}
    olds = {norm(o) for v in data.values() for o in v}
    return news, olds


def singular_plural_same(a, b):
    a, b = norm(a), norm(b)
    return a == b or a + "s" == b or b + "s" == a or a + "es" == b or b + "es" == a


TAXA = set()


def is_latin(name):
    return norm(name) in TAXA or bool(LATIN_RX.match(name.strip()))


def make_concepts(wd, off):
    concepts = []
    by_qid = {}
    by_en = {}
    for r in wd.values():
        TAXA.update(norm(t) for t in r.get("taxa", ()))
    for qid, r in sorted(wd.items(), key=lambda kv: int(kv[0][1:])):
        en = r.get("en", "")
        if not clean_name(en):
            continue
        if not any(r.get(k) for k in ("engb", "enus") + NORDIC):
            continue

        def ok(name):
            return bool(name) and bool(clean_name(name)) and not is_latin(name)

        alts = sorted({a for a in r.get("alts", ()) if ok(a) and norm(a) != norm(en)},
                      key=str.lower)
        note = ["wikidata class: " + ", ".join(sorted(r["roots"]))]
        nordic = {}
        for k in NORDIC:
            v = r.get(k, "")
            if v and is_latin(v):
                note.append(f"Wikidata {k} label is the scientific name ({v})")
                v = ""
            nordic[k] = v
        c = {
            "source": "wikidata", "id": qid, "off_id": "",
            "en": lower_if_plain(en),
            "en_gb": lower_if_plain(r.get("engb", "")) if ok(r.get("engb", "")) else "",
            "en_us": lower_if_plain(r.get("enus", "")) if ok(r.get("enus", "")) else "",
            "syn": [lower_if_plain(a) for a in alts],
            **nordic,
            "note": "; ".join(note),
            "taxon": bool(r.get("taxa")),
        }
        concepts.append(c)
        by_qid[qid] = c
        for n in (c["en"], c["en_gb"], c["en_us"]):
            if n:
                by_en.setdefault(norm(n), []).append(c)
    off_only = 0
    merged = 0
    for e in off:
        names = [n for n in e["langs"]["en"] if clean_name(n) and not is_latin(n)]
        if not names:
            continue
        target = by_qid.get(e.get("wikidata"))
        if target is None:
            hits = {id(c): c for n in names[:1] for c in by_en.get(norm(n), [])}
            if len(hits) == 1:
                target = next(iter(hits.values()))
        langs = {k: ", ".join(x for x in v if not is_latin(x)) for k, v in e["langs"].items() if k in NORDIC}
        langs = {k: v for k, v in langs.items() if v}
        if target is not None:
            merged += 1
            target["source"] = "wikidata+off"
            target["off_id"] = e["id"]
            have = {norm(target["en"]), norm(target["en_gb"]), norm(target["en_us"])}
            have |= {norm(s) for s in target["syn"]}
            target["syn"] += [n for n in names if norm(n) not in have]
            target["off_names"] = names
            if is_latin(target["en"]):
                target["en"] = names[0][:1].lower() + names[0][1:]
            for k in NORDIC:
                if not target[k] and langs.get(k):
                    target[k] = langs[k]
                elif langs.get(k) and norm(langs[k].split(",")[0]) != norm(target[k].split(",")[0]):
                    target.setdefault("off_nordic", {})[k] = langs[k]
            continue
        if not langs:
            continue
        off_only += 1
        c = {
            "source": "off", "id": e["id"], "off_id": e["id"],
            "en": names[0][:1].lower() + names[0][1:], "en_gb": "", "en_us": "",
            "syn": names[1:], "off_names": names, "note": "", "taxon": False,
            **{k: langs.get(k, "") for k in NORDIC},
        }
        concepts.append(c)
    log(f"merged {merged} OFF entries into Wikidata rows; {off_only} OFF-only rows")
    return concepts


def proper(name):
    """A proper noun or brand ("Monterey Jack", "Kalleh Dairy")."""
    return any(ch.isupper() for ch in lower_if_plain(name))


def contains_words(a, b):
    """True if the words of b appear in a, in order and together."""
    a, b = f" {norm(a)} ", f" {norm(b)} "
    return b in a


def spelling_only(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.8


def suggest(c, news, olds):
    """(from_list, to, reason) or None, only on real evidence."""
    sv = {norm(w) for w in (c.get("sv") or "").split(",") if w.strip()}
    nordic = sv | {norm(w) for k in ("da", "nb") for w in (c.get(k) or "").split(",") if w.strip()}
    eng = [c["en"], c["en_gb"], c["en_us"]] + list(c["syn"])
    eng = [e for e in eng if e]
    to = None
    reason = ""
    # 1. An English name that is also the Swedish word (paprika).
    for e in eng:
        if norm(e) in sv and norm(e) not in olds:
            to, reason = e, "an English name equals the Swedish word"
            break
    # 2. A British label differing from the main (usually US) label.
    if to is None and c["en_gb"] and not singular_plural_same(c["en_gb"], c["en"]):
        if norm(c["en"]) in nordic:
            return None
        to, reason = c["en_gb"], "Wikidata en-gb label differs from en"
    if to is None and c["en_us"] and not singular_plural_same(c["en_us"], c["en"]):
        if norm(c["en_us"]) in nordic:
            return None
        to, reason = c["en"], "Wikidata en-us label differs from en"
    if to is None or norm(to) in olds or proper(to) or is_latin(to):
        return None
    # Which names to rename: the labels a US recipe would use. For rule 1 also
    # the main OFF names, but never a Nordic word or a near variant.
    cands = [c["en_us"], c["en"]] if reason.startswith("Wikidata") else [c["en_us"], c["en"], c["en_gb"]]
    if reason.startswith("an English"):
        cands += c.get("off_names", [])[:3]
    froms = []
    for f in cands:
        if not f or singular_plural_same(f, to) or norm(f) in nordic or proper(f) or is_latin(f):
            continue
        if norm(f) in olds or norm(f) in DENY_FROM or any(singular_plural_same(f, n) for n in news):
            continue
        if contains_words(f, to) or contains_words(to, f):
            continue  # "Grana Padano cheese" -> "Grana Padano", "red pepper" -> "pepper"
        if f not in froms:
            froms.append(f)
    if not froms:
        return None
    if all(spelling_only(f, to) for f in froms):
        reason += " (spelling only)"
    return froms, to, reason


SV_GROUP_RX = re.compile(r"(släktet|familjen|fiskar|växter|arter|ordningen)$")


def usable_sv(w):
    return (bool(w) and len(w) <= 40 and not is_latin(w)
            and not SV_GROUP_RX.search(w.lower())
            and bool(re.fullmatch(r"[^\W\d_][\w' -]*", w)))


def write_outputs(concepts, out):
    news, olds = existing_names()
    out.mkdir(parents=True, exist_ok=True)
    cols = ["source", "id", "off_id", "en", "en_gb", "en_us", "en_synonyms",
            "sv", "da", "nb", "fi", "suggested_from", "suggested_to", "note"]
    sugg = []
    rows = []
    for c in concepts:
        s = suggest(c, news, olds)
        note = c["note"]
        if c.get("off_nordic"):
            note = "; ".join(filter(None, [note] + [f"OFF {k}: {v}" for k, v in c["off_nordic"].items()]))
        if s:
            note = "; ".join(filter(None, [note, s[2]]))
            sugg.append((c, s))
        rows.append({
            "source": c["source"], "id": c["id"], "off_id": c["off_id"],
            "en": c["en"], "en_gb": c["en_gb"], "en_us": c["en_us"],
            "en_synonyms": " | ".join(c["syn"]),
            "sv": c["sv"], "da": c["da"], "nb": c["nb"], "fi": c["fi"],
            "suggested_from": " | ".join(s[0]) if s else "",
            "suggested_to": s[1] if s else "", "note": note,
        })
    rows.sort(key=lambda r: (norm(r["en"]), r["id"]))
    with open(out / "name-candidates.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    # English -> Swedish. One line per English name. A kitchen item beats a
    # botanical one (oregano is "oregano", not the plant's "kungsmynta"), and
    # genus or family labels ("tonfisksläktet", "laxfiskar") are never used.
    sv = {}
    ranked = sorted(concepts, key=lambda c: (c.get("taxon", False), c["source"] == "wikidata"))
    # Main labels first, synonyms after, so "corn" goes to maize (majs) and
    # not to the item that only lists it as an old word for grain (korn).
    for main in (True, False):
        for c in ranked:
            words = (c.get("sv") or "").split(",") + c.get("off_nordic", {}).get("sv", "").split(",")
            first = next((w.strip() for w in words if usable_sv(w.strip())), "")
            if not first:
                continue
            names = [c["en"], c["en_gb"], c["en_us"]] if main else list(c["syn"])
            for e in names:
                if e and not is_latin(e) and norm(e) not in sv:
                    sv[norm(e)] = (e, first, c["source"], c["id"])
    with open(out / "names-sv.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["en", "sv", "source", "id"])
        for k in sorted(sv):
            e, s, src, i = sv[k]
            w.writerow([e.lower(), lower_if_plain(s), src, i])

    # names.toml candidates, commented out.
    lines = [
        "# Candidate renames for cookbook_metric/names.toml, built by",
        "# tools/build_name_candidates.py. Nothing here is active: every rename is",
        "# commented out. Uncomment the ones you want and move them into names.toml",
        "# (or ~/.config/cookbook-metric/names.toml). Names already in names.toml",
        "# are left out.",
        "#",
        "# Sources: Wikidata (CC0) and the Open Food Facts ingredient taxonomy",
        "# (AGPL-3.0). The comment above each line says which item and why.",
        "",
    ]
    seen = set()
    count = 0
    ordered = sorted(sugg, key=lambda x: ("spelling only" in x[1][2], norm(x[1][1])))
    spelling_header = False
    for c, (froms, to, reason) in ordered:
        if "spelling only" in reason and not spelling_header:
            spelling_header = True
            lines += ["", "# ---- Spelling only (US -ize/-or/chili -> British). Not Nordic words;",
                      "# take these only if you want British spelling as well.", ""]
        key = (norm(to), tuple(norm(f) for f in froms))
        if key in seen:
            continue
        seen.add(key)
        src = c["id"] if c["source"] == "off" else c["id"] + (f", OFF {c['off_id']}" if c["off_id"] else "")
        sv_word = f", sv {c['sv']}" if c.get("sv") else ""
        lines.append(f"# {c['source']} {src}: {reason}{sv_word}")
        lines.append("# " + json.dumps(to, ensure_ascii=False) + " = ["
                     + ", ".join(json.dumps(f, ensure_ascii=False) for f in froms) + "]")
        count += 1
    (out / "names-candidates.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    by_src = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    log(f"rows: {len(rows)} {by_src}; suggestions: {count}; Swedish names: {len(sv)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--off-file", help="use a local copy of ingredients.txt instead of downloading")
    ap.add_argument("--out", default=str(ROOT / "data"))
    ap.add_argument("--wd-cache", help="JSON file to reuse (or save) the Wikidata answers")
    args = ap.parse_args()
    tmp = None
    try:
        if args.off_file:
            off_path = Path(args.off_file)
        else:
            tmp, off_path = download_off()
        off = parse_off(off_path)
        log(f"open food facts: {len(off)} home-kitchen entries")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    cache = Path(args.wd_cache) if args.wd_cache else None
    if cache and cache.is_file():
        wd = json.loads(cache.read_text(encoding="utf-8"))
        for r in wd.values():
            r["roots"], r["alts"], r["taxa"] = set(r["roots"]), set(r["alts"]), set(r.get("taxa", ()))
    else:
        wd = fetch_wikidata([e["wikidata"] for e in off if e.get("wikidata")])
        if cache:
            cache.write_text(json.dumps(wd, default=sorted, ensure_ascii=False), encoding="utf-8")
    log(f"wikidata: {len(wd)} items")
    write_outputs(make_concepts(wd, off), Path(args.out))


if __name__ == "__main__":
    main()
