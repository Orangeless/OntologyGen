"""Compare a generated ontology with the reference and show what the documents ADDED.

Writes (default: next to the ontology):
  <name>_report.md        reference coverage, integrity of the seed, extensions grouped by document, facts and their sources
  <name>_extensions.ttl   only the new classes / properties the documents introduced (the "extensions")
  <name>_facts.ttl        only the individuals (contract facts) with their :sourceClause

Extensions = generated terms that do not match a reference term (by IRI, name, label or alias).
Agents tag them `[extension: <document>, <clause>]` in rdfs:comment (see helper/reference.py); the tag is used to group them.

Usage: python agents/reference_report.py --ontology ontology/wba_combined.ttl [--reference reference/dart_core.ttl]
       [--scope core|wba|all] [--cqs cqs/reference] [--out-dir DIR]
"""
import argparse
import difflib
import json
import re
from collections import defaultdict
from pathlib import Path

import rdflib
from rdflib import BNode, Literal, OWL, RDF, RDFS, URIRef
from rdflib.namespace import SKOS

from helper.reference import EXTENSION_TAG, PIPE_NS, Reference, local, normalize_term

_BUILTIN = ("http://www.w3.org/", "http://purl.org/dc/", "http://xmlns.com/foaf/")
SOURCE = URIRef(PIPE_NS + "sourceClause")


def is_builtin(u) -> bool:
    return not isinstance(u, URIRef) or str(u).startswith(_BUILTIN)


def tags(g, x) -> list:
    return [m.strip() for c in g.objects(x, RDFS.comment) for m in EXTENSION_TAG.findall(str(c))]


def first(g, x, p=RDFS.label) -> str:
    v = g.value(x, p)
    return str(v) if v is not None else ""


def numbers(text: str) -> set:
    out = set()
    for n in re.findall(r"(?<![\d:])\d[\d,]*\.?\d*(?![\d:])", str(text)):
        try:
            out.add(format(float(n.replace(",", "").rstrip(".")), "g"))
        except ValueError:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--ontology", required=True)
    ap.add_argument("--reference", default=str(root / "reference" / "dart_core.ttl"))
    ap.add_argument("--scope", choices=["core", "wba", "all"], default="core")
    ap.add_argument("--cqs", default=None, help="CQ json file or directory: check that the expected numbers appear in the ontology")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    onto = Path(args.ontology)
    out_dir = Path(args.out_dir) if args.out_dir else onto.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = onto.stem

    gen = rdflib.Graph()
    gen.parse(str(onto), format="turtle")
    ref = Reference(args.reference, args.scope)
    seed = ref.seed_graph()
    seed_terms = {s for s in seed.subjects() if isinstance(s, URIRef)}
    idx = ref.match_index()

    # ---- generated vocabulary
    classes = {c for c in gen.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef) and not is_builtin(c)}
    classes |= {o for o in gen.objects(None, RDF.type) if isinstance(o, URIRef) and not is_builtin(o)}
    props = {p for p in gen.subjects(RDF.type, OWL.ObjectProperty)} | {p for p in gen.subjects(RDF.type, OWL.DatatypeProperty)}
    props |= {p for p in gen.predicates() if isinstance(p, URIRef) and not is_builtin(p)}
    props = {p for p in props if isinstance(p, URIRef) and not is_builtin(p) and p != SOURCE}
    enum_members = {m for e in ref.used_enums for m in ref.g.subjects(RDF.type, e)}
    enum_member_iris = {ref._m(m) for m in enum_members}
    individuals = {s for s in set(gen.subjects(RDF.type, None))
                   if isinstance(s, URIRef) and not is_builtin(s) and s not in classes and s not in props
                   and s not in seed_terms and s not in enum_member_iris
                   and any(not is_builtin(t) for t in gen.objects(s, RDF.type))}

    def match(term):
        """Reference term a generated term corresponds to (ReferenceIRI-mapped), or None."""
        if term in seed_terms:
            return term
        for name in (local(term), first(gen, term)):
            if name and normalize_term(name) in idx:
                return ref._m(idx[normalize_term(name)])
        return None

    cls_match = {c: match(c) for c in classes}
    prop_match = {p: match(p) for p in props}
    ext_classes = sorted((c for c in classes if cls_match[c] is None), key=local)
    ext_props = sorted((p for p in props if prop_match[p] is None), key=local)
    ref_class_iris = {ref._m(c): c for c in ref.classes}
    ref_prop_iris = {ref._m(p): p for p in ref.props}

    # ---- subclass closure for instance counts
    def subclasses(c):
        out, todo = {c}, [c]
        while todo:
            for s in gen.subjects(RDFS.subClassOf, todo.pop()):
                if s not in out:
                    out.add(s); todo.append(s)
        return out

    def n_instances(c):
        return len({i for k in subclasses(c) for i in gen.subjects(RDF.type, k)} & individuals)

    # ---- integrity: every seed triple must still be there
    seed_flat = [(s, p, o) for s, p, o in seed if not any(isinstance(x, BNode) for x in (s, p, o))]
    lost = [t for t in seed_flat if t not in gen]

    L = [f"# Ontology vs reference: {onto.name}", "",
         f"Reference: `{args.reference}` (scope {args.scope}: {len(ref.classes)} classes, {len(ref.props)} properties). "
         f"Generated: {len(classes)} classes, {len(props)} properties, {len(individuals)} individuals.", ""]

    L += ["## 1. Reference coverage", "",
          "| Reference class | In ontology | Instances | New subclasses | New properties on it |", "|---|---|---|---|---|"]
    found = 0
    for riri, rc in sorted(ref_class_iris.items(), key=lambda kv: local(kv[1])):
        gc = next((c for c, m in cls_match.items() if m == riri), None)
        found += gc is not None
        subs = sorted(local(s) for s in subclasses(gc) - {gc} if cls_match.get(s) is None) if gc else []
        newp = sorted(local(p) for p in ext_props
                      if gc and any(d in subclasses(gc) for x in gen.objects(p, RDFS.domain)
                                    for d in ([x] + (list(rdflib.collection.Collection(gen, gen.value(x, OWL.unionOf)))
                                                     if isinstance(x, BNode) and gen.value(x, OWL.unionOf) else []))))
        L.append(f"| {local(rc)} | {'yes' if gc else '**MISSING**'} | {n_instances(gc) if gc else 0} | "
                 f"{', '.join(subs) or '-'} | {', '.join(newp) or '-'} |")
    used = {m for p, m in prop_match.items() if m in ref_prop_iris
            and any(s not in seed_terms and not isinstance(s, BNode) for s in gen.subjects(p, None))}
    L += ["", f"{found}/{len(ref_class_iris)} reference classes present; "
              f"{len(used)}/{len(ref_prop_iris)} reference properties used in facts.", ""]
    unused = sorted(local(c) for riri, c in ref_class_iris.items()
                    if (gc := next((x for x, m in cls_match.items() if m == riri), None)) is not None and n_instances(gc) == 0)
    L += [f"Reference classes with **no instances yet** (the documents may not cover them): {', '.join(unused) or 'none'}", ""]

    alias_cls = sorted((c for c, m in cls_match.items() if m is not None and c not in seed_terms and m in ref_class_iris), key=local)
    alias_prp = sorted((p for p, m in prop_match.items() if m is not None and p not in seed_terms and m in ref_prop_iris), key=local)
    L += ["## 2. Reference integrity", ""]
    if alias_cls or alias_prp:
        L += ["**Duplicates of reference terms under a different name** (should reuse the reference IRI):", ""]
        L += [f"- class `{local(c)}` -> reference `{local(ref_class_iris[cls_match[c]])}`" for c in alias_cls]
        L += [f"- property `{local(p)}` -> reference `{local(ref_prop_iris[prop_match[p]])}`" for p in alias_prp]
        L.append("")
    L += ([f"All {len(seed_flat)} seed triples are intact."] if not lost else
          [f"**{len(lost)} of {len(seed_flat)} seed triples are missing or changed** (agents must not delete reference axioms). First 15:", ""]
          + [f"- `{local(s)} {local(p)} {local(o) if isinstance(o, URIRef) else str(o)[:60]}`" for s, p, o in lost[:15]])
    L.append("")

    # ---- extensions grouped by source document
    groups = defaultdict(lambda: {"classes": [], "props": []})
    for c in ext_classes:
        groups[(tags(gen, c) or ["UNTAGGED (no [extension: ...] comment)"])[0].split(",")[0].strip()]["classes"].append(c)
    for p in ext_props:
        groups[(tags(gen, p) or ["UNTAGGED (no [extension: ...] comment)"])[0].split(",")[0].strip()]["props"].append(p)
    L += ["## 3. Extensions: what the documents added beyond the reference", "",
          f"{len(ext_classes)} new classes and {len(ext_props)} new properties.", ""]
    ref_names = {normalize_term(n): n for n in idx}
    for doc in sorted(groups):
        L += [f"### {doc}", ""]
        for c in groups[doc]["classes"]:
            parents = sorted({local(ref._m(p)) if p in ref_class_iris.values() else local(p) for p in gen.objects(c, RDFS.subClassOf)
                              if isinstance(p, URIRef)})
            near = difflib.get_close_matches(normalize_term(local(c)), list(ref_names), n=1, cutoff=0.84)
            L.append(f"- **class {local(c)}**" + (f" is-a {', '.join(parents)}" if parents else "")
                     + f" ({n_instances(c)} instances)" + (f" - possible duplicate of `{ref_names[near[0]]}`" if near else ""))
            cm = EXTENSION_TAG.sub("", first(gen, c, RDFS.comment)).strip()
            if cm:
                L.append(f"  - {cm[:200]}")
        for p in groups[doc]["props"]:
            kind = "object" if (p, RDF.type, OWL.ObjectProperty) in gen else ("data" if (p, RDF.type, OWL.DatatypeProperty) in gen else "used")
            near = difflib.get_close_matches(normalize_term(local(p)), list(ref_names), n=1, cutoff=0.84)
            L.append(f"- property {local(p)} ({kind})" + (f" - possible duplicate of `{ref_names[near[0]]}`" if near else ""))
        L.append("")

    # ---- facts
    with_src = [i for i in individuals if (i, SOURCE, None) in gen]
    L += ["## 4. Contract facts", "",
          f"{len(individuals)} individuals; {len(with_src)} carry a `:sourceClause`; {len(individuals) - len(with_src)} do not.", ""]
    for i in sorted(individuals, key=local)[:25]:
        types = ", ".join(local(t) for t in gen.objects(i, RDF.type) if not is_builtin(t))
        src = first(gen, i, SOURCE) or "NO SOURCE"
        vals = "; ".join(f"{local(p)}={str(o)[:40]}" for p, o in gen.predicate_objects(i)
                         if isinstance(o, Literal) and p not in (RDFS.label, RDFS.comment, SOURCE))[:150]
        L.append(f"- `{local(i)}` : {types} | {vals} | source: {src}")
    if len(individuals) > 25:
        L.append(f"- ... {len(individuals) - 25} more (see `{stem}_facts.ttl`)")
    L.append("")

    # ---- CQ expected numbers
    if args.cqs:
        p = Path(args.cqs)
        files = sorted(p.glob("*.json")) if p.is_dir() else [p]
        lits = {n for s_, p_, o in gen if isinstance(o, Literal) and s_ not in seed_terms
                and p_ not in (RDFS.comment, RDFS.label, SKOS.altLabel) for n in numbers(o)}
        L += ["## 5. Reference CQ facts (cheap check: expected numbers present as literals; use eval.py for the real answer check)", "",
              "| Question | Expected | Numbers present |", "|---|---|---|"]
        for f in files:
            if f.name.startswith("_"):
                continue
            for _, pages in json.loads(f.read_text(encoding="utf-8")).items():
                for _, cqs in pages.items():
                    for cq in cqs:
                        need = numbers(cq["expected_answer"])
                        ok = "n/a" if not need else ("yes" if need <= lits else "MISSING " + ", ".join(sorted(need - lits)))
                        L.append(f"| {cq['competency_question'][:90]} | {cq['expected_answer'][:50]} | {ok} |")
        L.append("")

    (out_dir / f"{stem}_report.md").write_text("\n".join(L), encoding="utf-8")

    # ---- split files
    def dump(subjects, name):
        out = rdflib.Graph()
        for pfx, ns in gen.namespaces():
            out.bind(pfx, ns)
        for s in subjects:
            for t in gen.triples((s, None, None)):
                out.add(t)
                if isinstance(t[2], BNode):
                    for t2 in gen.triples((t[2], None, None)):
                        out.add(t2)
        (out_dir / name).write_text(out.serialize(format="turtle"), encoding="utf-8")
        return len(out)

    n_ext = dump(set(ext_classes) | set(ext_props), f"{stem}_extensions.ttl")
    n_fact = dump(individuals, f"{stem}_facts.ttl")
    print(f"{found}/{len(ref_class_iris)} reference classes present | extensions: {len(ext_classes)} classes, {len(ext_props)} properties "
          f"| {len(individuals)} individuals ({len(with_src)} sourced) | seed triples lost: {len(lost)}")
    print(f"wrote {out_dir / (stem + '_report.md')}, {stem}_extensions.ttl ({n_ext} triples), {stem}_facts.ttl ({n_fact} triples)")


if __name__ == "__main__":
    main()
