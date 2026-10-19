#!/usr/bin/env python3
"""Build a self-contained, offline HTML viewer for the generated ontology.

  python viewer/build_viz.py <ontology.ttl> [reference_terms.json] [out.html]

Defaults: reference terms ontology/reports/reference_terms.json, output viewer/ontology_viewer.html. The ontology is converted to compact JSON and inlined together with viewer/template.html and
viewer/vendor/d3.min.js, so the result opens by double-click with no server and no network.
"""
import collections
import json
import logging
import re
import sys
from pathlib import Path

import rdflib
from rdflib import BNode, Literal, OWL, RDF, RDFS, URIRef, XSD
from rdflib.collection import Collection
from rdflib.namespace import SKOS

logging.disable(logging.CRITICAL)  # rdflib complains loudly about malformed typed literals; we report those ourselves

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NS = "http://www.example.org/test#"
TAG = re.compile(r"\[extension:\s*([^\]]+)\]", re.I)
SRC = URIRef(NS + "sourceClause")
SKIP_VALUE_PREDICATES = {RDF.type, RDFS.label, RDFS.comment, SRC}


def local(u) -> str:
    return str(u).split("#")[-1].split("/")[-1]


def norm_doc(name: str) -> str:
    """Same document, different spellings ('WBA Dictionary' / 'Wholesale Broadband Agreement - Dictionary') -> one name."""
    s = (name or "").strip()
    if not s:
        return ""
    s = re.sub(r"^wholesale broadband agreement\s*[-:–]\s*", "", s, flags=re.I)
    s = re.sub(r"^wba\s+", "", s, flags=re.I)
    s = s.strip(" ,.-")
    for key, canon in (("price list", "Price List"), ("technical specification", "Technical Specification"), ("product module", "Technical Specification"),
                       ("service level", "Service Levels Schedule"), ("product description", "Product Description"), ("fair use", "Fair Use Policy")):
        if key in s.lower():  # the NBN Ethernet documents are quoted in many spellings by the agents
            return canon
    return s or "Wholesale Broadband Agreement"


def split_comments(comments):
    """-> (clean texts, [(doc, location)]) from rdfs:comment values carrying '[extension: doc, location]' tags."""
    texts, tags = [], []
    for c in comments:
        c = str(c)
        for m in TAG.findall(c):
            doc, _, loc = m.partition(",")
            tags.append((doc.strip(), loc.strip()))
        t = TAG.sub("", c).strip()
        if t and t not in texts:
            texts.append(t)
    return texts, tags


def members(g, x):
    if isinstance(x, BNode):
        u = g.value(x, OWL.unionOf)
        return [m for m in Collection(g, u)] if u else []
    return [x]


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python viewer/build_viz.py <ontology.ttl> [reference_terms.json] [out.html]")
    onto = Path(sys.argv[1])
    refp = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "ontology" / "reports" / "reference_terms.json"
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE / "ontology_viewer.html"

    ref = json.loads(refp.read_text(encoding="utf-8")) if refp.exists() else {"classes": [], "enumeration_classes": [], "properties": []}
    ref_c = set(ref["classes"]) | set(ref.get("enumeration_classes", []))
    ref_p = set(ref["properties"])

    g = rdflib.Graph()
    g.parse(str(onto), format="turtle")

    # ---------------------------------------------------------------- classes
    class_iris = {c for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef) and str(c).startswith(NS)}
    enum_classes = {c for c in g.subjects(OWL.equivalentClass, None) if isinstance(c, URIRef)}
    classes = {}
    for c in class_iris:
        texts, tags = split_comments(g.objects(c, RDFS.comment))
        parents_all = [local(p) for p in g.objects(c, RDFS.subClassOf) if isinstance(p, URIRef)]
        classes[local(c)] = {
            "id": local(c), "l": str(g.value(c, RDFS.label) or ""), "ref": int(local(c) in ref_c),
            "c": texts, "t": sorted(set(tags)), "alt": sorted(str(a) for a in g.objects(c, SKOS.altLabel)),
            "pa": parents_all, "p": [], "enum": [],
        }
    for cid, c in classes.items():
        c["p"] = sorted({p for p in c["pa"] if p in classes and p != cid})
        c["flat"] = int(bool(c["pa"]) and set(c["pa"]) <= {"Thing", "Entity"})
    for e in enum_classes:
        if local(e) in classes:
            lst = g.value(g.value(e, OWL.equivalentClass), OWL.oneOf)
            if lst:
                classes[local(e)]["enum"] = [local(m) for m in Collection(g, lst)]

    # ---------------------------------------------------------------- properties
    prop_iris = {p for t in (OWL.ObjectProperty, OWL.DatatypeProperty) for p in g.subjects(RDF.type, t) if isinstance(p, URIRef)}
    usage = collections.Counter(p for _, p, _ in g)
    props = {}
    for p in prop_iris:
        texts, tags = split_comments(g.objects(p, RDFS.comment))
        dom = sorted({local(m) for x in g.objects(p, RDFS.domain) for m in members(g, x) if isinstance(m, URIRef)})
        rng = sorted({local(m) for x in g.objects(p, RDFS.range) for m in members(g, x) if isinstance(m, URIRef)})
        props[local(p)] = {"id": local(p), "l": str(g.value(p, RDFS.label) or ""), "k": "o" if (p, RDF.type, OWL.ObjectProperty) in g else "d",
                           "dom": dom, "rng": rng, "ref": int(local(p) in ref_p), "c": texts, "t": sorted(set(tags)), "use": usage.get(p, 0)}

    # ---------------------------------------------------------------- individuals
    ind_iris = {s for s in set(g.subjects(RDF.type, None)) if isinstance(s, URIRef) and str(s).startswith(NS)
                and s not in class_iris and s not in prop_iris and local(s) not in classes
                and any(isinstance(o, URIRef) and local(o) in classes for o in g.objects(s, RDF.type))}
    enum_members = {m for e in enum_classes for m in g.subjects(RDF.type, e)}
    inds, bad_dates = [], []
    for s in ind_iris - enum_members:
        vals = []
        for p, o in g.predicate_objects(s):
            if p in SKIP_VALUE_PREDICATES or not isinstance(p, URIRef):
                continue
            if isinstance(o, Literal):
                vals.append([local(p), str(o)[:240], 0])
                if o.datatype == XSD.date and not re.fullmatch(r"-?\d{4}-\d\d-\d\d", str(o)):
                    bad_dates.append(local(s))
            elif isinstance(o, URIRef):
                vals.append([local(p), local(o), 1])
        src = str(g.value(s, SRC) or "")
        texts, _ = split_comments(g.objects(s, RDFS.comment))
        inds.append({"id": local(s), "l": str(g.value(s, RDFS.label) or ""), "cls": sorted(local(o) for o in g.objects(s, RDF.type) if local(o) in classes),
                     "v": vals[:25], "nv": len(vals), "src": src, "doc": norm_doc(src.split(",")[0]), "c": texts[:2]})
    inds.sort(key=lambda i: i["id"])

    # ---------------------------------------------------------------- derived: documents, quality
    for obj in list(classes.values()) + list(props.values()):
        obj["docs"] = sorted({norm_doc(d) for d, _ in obj["t"] if d})
    children = collections.defaultdict(list)
    for c in classes.values():
        for p in c["p"]:
            children[p].append(c["id"])
    direct = collections.Counter(k for i in inds for k in i["cls"])

    def total(cid, seen=None):
        seen = seen or set()
        if cid in seen:
            return 0
        seen.add(cid)
        return direct.get(cid, 0) + sum(total(k, seen) for k in children.get(cid, []))

    no_inst = sorted(cid for cid in classes if total(cid) == 0)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"^cl_", "", s.lower())).rstrip("s")
    groups = collections.defaultdict(list)
    for cid in classes:
        groups[norm(cid)].append(cid)
    dups = sorted((sorted(v) for v in groups.values() if len(v) > 1), key=lambda v: v[0].lower())
    quality = {
        "dups": dups,
        "flat": sorted(cid for cid, c in classes.items() if c["flat"]),
        "noInst": no_inst,
        "badDates": sorted(set(bad_dates)),
        "noSource": sorted(i["id"] for i in inds if not i["src"]),
        "noDesc": sorted(cid for cid, c in classes.items() if not c["c"]),
    }
    data = {
        "meta": {"built": __import__("time").strftime("%Y-%m-%d %H:%M"), "file": onto.name, "triples": len(g), "classes": len(classes), "props": len(props), "inds": len(inds),
                 "refClasses": sum(c["ref"] for c in classes.values())},
        "classes": sorted(classes.values(), key=lambda c: c["id"].lower()),
        "props": sorted(props.values(), key=lambda p: p["id"].lower()),
        "inds": inds, "quality": quality,
    }

    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (HERE / "template.html").read_text(encoding="utf-8")
    d3 = (HERE / "vendor" / "d3.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    html = html.replace("/*__D3__*/", d3).replace("/*__DATA__*/null", blob)
    out.write_text(html, encoding="utf-8")
    print(f"{onto.name}: {len(g)} triples -> {len(classes)} classes ({data['meta']['refClasses']} reference), {len(props)} properties, {len(inds)} individuals")
    print(f"quality: {len(dups)} near-duplicate groups, {len(quality['flat'])} flat classes, {len(no_inst)} without instances, "
          f"{len(quality['badDates'])} invalid dates, {len(quality['noSource'])} facts without source")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB; data {len(blob) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
