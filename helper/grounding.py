"""Deterministic checks for the simplified ("lite") loop: they replace the open-ended LLM QA review.

Everything here is free (no LLM) and precise. It looks only at what the coder ADDED on the current page (graph difference):
  1. GROUNDING   every number / date / time / duration the coder wrote must be present in the page text (invented example values
                 such as 2024-01-01 or a made-up "P42D" are rejected);
  2. DUPLICATES  a new class or property whose name is the same as an existing one (apart from Cl_ prefix, case, separators, plural);
  3. SOURCE      a new individual with values must carry :sourceClause;
  4. CQ COVERAGE the page's competency-question answers must be present in the ontology;
  5. REFERENCE   reference individuals (e.g. TC-1) must not get extra properties from the documents.
build_feedback() turns the findings into short instructions for the QA-coder; strip_ungrounded() removes whatever numeric value is
still unsupported when the repair rounds are used up (an unsupported value is worse than a missing one).
"""
from __future__ import annotations

import re
from pathlib import Path

import rdflib
from rdflib import BNode, Literal, OWL, RDF, RDFS, URIRef, XSD
from rdflib.namespace import SKOS

NS = "http://www.example.org/test#"
SRC = URIRef(NS + "sourceClause")
HAS_SRC = URIRef(NS + "hasSourceClause")
SKIP_PREDICATES = {RDFS.label, RDFS.comment, SKOS.altLabel, SRC, HAS_SRC}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
          "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
          "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}
_STOP = set("the a an of to and or in for by with is are be as that this which on at from its it any under means has have not shall must may".split())


def local(u) -> str:
    return str(u).split("#")[-1].split("/")[-1]


def norm(s: str) -> str:
    s = re.sub(r"^(cl|op|dp|dtp)_", "", str(s).strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower()).rstrip("s")


# ---------------------------------------------------------------- page text helpers
class Page:
    def __init__(self, text: str):
        self.low = " ".join(text.split()).lower()
        t = self.low.replace(",", "")
        self.nums = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)}
        self.nums |= {float(v) for w, v in _UNITS.items() if re.search(rf"\b{w}\b", t)}
        self.words = set(re.findall(r"[a-z]{4,}", self.low))


def supported(value: str, page: Page):
    """True / False when the value can be checked against the page, None when it is not a checkable value."""
    v = value.strip()
    m = re.fullmatch(r"(-?\d{4})-(\d\d)-(\d\d)(?:[T ].*)?", v)
    if m:  # a date: year, day and month (name or number) must all be on the page
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        mon = MONTHS[mo - 1] if 1 <= mo <= 12 else ""
        return (y in page.low and float(d) in page.nums and (mon in page.low or mon[:3] in page.low or f"{mo:02d}/{y}" in page.low or f"/{mo}/" in page.low))
    m = re.fullmatch(r"(\d\d):(\d\d)(?::\d\d)?", v)
    if m:
        h, mi = int(m.group(1)), m.group(2); h12 = (h % 12) or 12; ap = "am" if h < 12 else "pm"
        return any(x in page.low for x in (f"{h}:{mi}", f"{h:02d}:{mi}", f"{h12}:{mi}{ap}", f"{h12}:{mi} {ap}", f"{h12}{ap}" if mi == "00" else "\0"))
    m = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T.*)?", v)
    if m and any(m.groups()):
        nums = [int(x) for x in m.groups() if x]
        return all(float(n) in page.nums for n in nums) if any(n != 0 for n in nums) else True  # P0D carries no number to check
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", v.replace(",", ""))]
    if nums:
        return all(n in page.nums for n in nums)
    return None


def phrase_supported(value: str, page: Page) -> bool:
    """Long free text: most of its content words appear on the page."""
    w = {x for x in re.findall(r"[a-z]{4,}", value.lower()) if x not in _STOP}
    return len(w) < 6 or sum(1 for x in w if x in page.words) / len(w) >= 0.5


# ---------------------------------------------------------------- what is new on this page
def new_triples(base: rdflib.Graph, now: rdflib.Graph):
    return [t for t in now if t not in base and not any(isinstance(x, BNode) for x in t)]


def ungrounded_literals(base, now, page_text: str):
    """[(subject, predicate, value, kind)] for numeric/date/time/duration values (and long free text) not found on the page."""
    page, out = Page(page_text), []
    for s, p, o in new_triples(base, now):
        if not isinstance(o, Literal) or p in SKIP_PREDICATES or o.datatype == XSD.boolean:
            continue
        v = str(o)
        if v.lower() in ("true", "false"):
            continue
        r = supported(v, page)
        if r is False:
            out.append((s, p, v, "value"))
        elif r is None and len(v) >= 40 and not phrase_supported(v, page):
            out.append((s, p, v, "text"))
    return out


def _classes(g):
    return {c for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef) and str(c).startswith(NS)}


def _properties(g):
    return {p for t in (OWL.ObjectProperty, OWL.DatatypeProperty) for p in g.subjects(RDF.type, t) if isinstance(p, URIRef) and str(p).startswith(NS)}


def duplicates(base, now):
    """[(kind, new_term, existing_term)]: new classes/properties that repeat an existing name."""
    out = []
    for kind, get in (("class", _classes), ("property", _properties)):
        old = {}
        for c in get(base):
            old.setdefault(norm(local(c)), c)
            lab = base.value(c, RDFS.label)
            if lab:
                old.setdefault(norm(str(lab)), c)
        seen = {}
        for c in sorted(get(now) - get(base), key=str):
            keys = {norm(local(c))} | ({norm(str(now.value(c, RDFS.label)))} if now.value(c, RDFS.label) else set())
            hit = next((old[k] for k in keys if k in old and old[k] != c), None) or next((seen[k] for k in keys if k in seen), None)
            if hit is not None:
                out.append((kind, c, hit))
            else:
                for k in keys:
                    seen[k] = c
    return out


def _is_individual(g, s):
    if not isinstance(s, URIRef) or not str(s).startswith(NS) or s in _classes(g) or s in _properties(g):
        return False
    return any(isinstance(o, URIRef) and o in _classes(g) for o in g.objects(s, RDF.type))


def individuals_without_source(base, now):
    out = []
    subjects = {s for s, p, o in new_triples(base, now) if isinstance(o, Literal) and p not in SKIP_PREDICATES}
    cls = _classes(now)
    for s in subjects:
        if isinstance(s, URIRef) and str(s).startswith(NS) and s not in cls and any(isinstance(o, URIRef) and o in cls for o in now.objects(s, RDF.type)):
            if (s, SRC, None) not in now and (s, HAS_SRC, None) not in now:
                out.append(s)
    return sorted(out, key=str)


def uncovered_cqs(cqs, now, limit=5):
    """CQs whose expected answer is not (yet) in the ontology: numbers must all be present as values, words mostly present."""
    lits, corpus = set(), []
    for s, p, o in now:
        if isinstance(o, Literal):
            corpus.append(str(o).lower())
            lits |= {float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(o).replace(",", ""))}
    for s in set(now.subjects()):
        if isinstance(s, URIRef):
            corpus.append(re.sub(r"(?<=[a-z])(?=[A-Z])|_", " ", local(s)).lower())
    text = " ".join(corpus)
    words = set(re.findall(r"[a-z]{4,}", text))
    out = []
    for cq in cqs:
        ans = str(cq.get("expected_answer", ""))
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", ans.replace(",", ""))]
        if nums:
            ok = all(n in lits for n in nums)
        else:
            w = {x for x in re.findall(r"[a-z]{4,}", ans.lower()) if x not in _STOP}
            ok = (not w) or sum(1 for x in w if x in words) / len(w) >= 0.7
        if not ok:
            out.append(cq)
    return out[:limit]


def reference_contamination(base, now, seed_individuals):
    return sorted({(s, p) for s, p, o in new_triples(base, now) if s in seed_individuals and p not in (RDF.type, RDFS.comment, SKOS.altLabel)}, key=str)


def subclass_cycles(base, now, limit=3):
    """Cycles in rdfs:subClassOf that involve a statement added on this page: [[A, B, ...]] (they break the reasoner)."""
    import collections
    sub = collections.defaultdict(set)
    for s, o in now.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            sub[s].add(o)
    added = {(s, o) for s, o in now.subject_objects(RDFS.subClassOf) if (s, RDFS.subClassOf, o) not in base and isinstance(s, URIRef) and isinstance(o, URIRef)}
    out, seen = [], set()
    for s, o in added:
        # a new edge s -> o closes a cycle if o can reach s
        stack, visited = [o], set()
        while stack:
            cur = stack.pop()
            if cur == s:
                key = frozenset((s, o))
                if key not in seen:
                    seen.add(key); out.append([s, o])
                break
            for nx in sub.get(cur, ()):
                if nx not in visited:
                    visited.add(nx); stack.append(nx)
    return out[:limit]


# ---------------------------------------------------------------- feedback for the QA-coder
def build_feedback(base, now, page_text, cqs, seed_individuals=frozenset(), limit=8):
    fb = []
    for cyc in subclass_cycles(base, now):
        a, b = cyc
        fb.append(f"CIRCULAR HIERARCHY: {local(a)} rdfs:subClassOf {local(b)} creates a cycle (a class cannot be its own ancestor). Delete the "
                  f"statement '{local(a)} rdfs:subClassOf {local(b)}' that you added (grep_file for '{local(a)}'); keep the more general class as the parent.")
    for s, p, v, kind in ungrounded_literals(base, now, page_text):
        fb.append(f"UNSUPPORTED VALUE: {local(s)} {local(p)} \"{v[:80]}\" is not stated on this page. Remove it, or replace it with the "
                  f"value the page text actually states (use grep_file for '{local(s)}'). Never invent example values.")
    for kind, new, old in duplicates(base, now):
        fb.append(f"DUPLICATE {kind.upper()}: {local(new)} repeats the existing {kind} {local(old)}. Delete {local(new)} and use {local(old)} "
                  f"everywhere instead (grep_file for '{local(new)}').")
    for s, p in reference_contamination(base, now, seed_individuals):
        fb.append(f"REFERENCE INDIVIDUAL MODIFIED: {local(s)} is a reference individual; do not add {local(p)} to it. Remove that triple.")
    for s in individuals_without_source(base, now):
        fb.append(f"NO SOURCE: individual {local(s)} has values but no :sourceClause. Add :sourceClause \"<document>, <clause>, p.<page>\".")
    for cq in uncovered_cqs(cqs, now):
        fb.append(f"UNCOVERED QUESTION: \"{cq['competency_question'][:140]}\" expects \"{str(cq['expected_answer'])[:80]}\", which is not in the "
                  f"ontology. If the page states it, add it as a fact with :sourceClause.")
    return fb[:limit]


def strip_ungrounded(path, base, page_text: str, seed_individuals=frozenset()):
    """Last resort after the repair rounds: delete numeric/date/time/duration values still not supported by the page,
    and any property a document added to a reference individual.
    Returns the number of triples removed (the file is rewritten only if something was removed)."""
    p = Path(path)
    now = rdflib.Graph()
    now.parse(str(p), format="turtle")
    bad = [(s, pr, rdflib.Literal(v, datatype=o.datatype, lang=o.language)) for (s, pr, v, kind), o in
           ((x, next(iter(now.objects(x[0], x[1])), None)) for x in ungrounded_literals(base, now, page_text)) if kind == "value" and o is not None]
    removed = 0
    for s_, p_ in reference_contamination(base, now, seed_individuals):  # a document may never alter the reference individuals
        for o in list(now.objects(s_, p_)):
            if (s_, p_, o) not in base:
                now.remove((s_, p_, o)); removed += 1
    for s, pr, lit in bad:
        for o in list(now.objects(s, pr)):
            if str(o) == str(lit):
                now.remove((s, pr, o)); removed += 1
    if removed:
        from helper.reference import SEED_MARKER
        if SEED_MARKER in p.read_text(encoding="utf-8"):
            _write_with_markers(now, p)
        else:
            p.write_text(now.serialize(format="turtle"), encoding="utf-8")
    return removed


def _write_with_markers(g, path) -> None:
    """Serialize with the reference-seed / extensions markers restored (the agents look for them)."""
    from helper.reference import EXT_MARKER, SEED_MARKER
    lines = g.serialize(format="turtle").splitlines()
    head = 0
    while head < len(lines) and (lines[head].startswith("@prefix") or not lines[head].strip()):
        head += 1
    body = "\n".join(lines[head:])
    Path(path).write_text("\n".join(lines[:head]) + f"\n{SEED_MARKER}\n{body}\n\n{EXT_MARKER}\n", encoding="utf-8")
