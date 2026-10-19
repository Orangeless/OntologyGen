"""Bounded ontology view for the agents.

The original pipeline pastes the WHOLE ontology (as one JSON object per line) into the manager, coder and QA prompts.
That grows with every page processed. compact_snapshot() replaces it, once the file is larger than `max_lines`, with
  1. an INDEX of every class / property / individual name (so agents can reuse terms instead of re-defining them), and
  2. the last `tail_lines` lines (where new content is appended) with their real line numbers.
The QA-coder keeps its read_lines_from_file / grep_file tools for anything else.
"""
from __future__ import annotations

import re
from pathlib import Path

_W3 = ("http://www.w3.org/", "http://purl.org/", "http://xmlns.com/")


def _local(u) -> str:
    return str(u).split("#")[-1].split("/")[-1]


def _members(g, x):
    from rdflib import BNode, OWL
    from rdflib.collection import Collection
    if isinstance(x, BNode):
        u = g.value(x, OWL.unionOf)
        return [_local(i) for i in Collection(g, u)] if u else []
    return [_local(x)]


def _clip(items, limit):
    items = sorted(items)
    return ", ".join(items[:limit]) + (f", ... (+{len(items) - limit} more; use grep_file)" if len(items) > limit else "")


def term_index(text: str, known=None, limits=(400, 700, 120)) -> str:
    """One compact paragraph per kind of term. `known` = names of reference terms (to separate them from extensions)."""
    cl_lim, pr_lim, in_lim = limits
    try:
        import rdflib
        from rdflib import OWL, RDF, RDFS, URIRef
        g = rdflib.Graph()
        g.parse(data=text, format="turtle")
        builtin = lambda u: not isinstance(u, URIRef) or str(u).startswith(_W3)
        classes = {c for c in g.subjects(RDF.type, OWL.Class) if not builtin(c)}
        props = {p for t in (OWL.ObjectProperty, OWL.DatatypeProperty) for p in g.subjects(RDF.type, t) if not builtin(p)}
        cls = []
        for c in classes:
            par = [_local(p) for p in g.objects(c, RDFS.subClassOf) if isinstance(p, URIRef)]
            cls.append(_local(c) + (f"<{'/'.join(par)}" if par else ""))
        prs = []
        for p in props:
            d = "/".join(x for o in g.objects(p, RDFS.domain) for x in _members(g, o)) or "?"
            r = "/".join(x for o in g.objects(p, RDFS.range) for x in _members(g, o)) or "?"
            prs.append(f"{_local(p)}:{d}->{r}")
        enum_classes = set(g.subjects(OWL.equivalentClass, None))  # enumeration members are vocabulary, not contract facts
        inds = {s for s in set(g.subjects(RDF.type, None)) if not builtin(s) and s not in classes and s not in props
                and any(not builtin(t) and t in classes for t in g.objects(s, RDF.type))
                and not any(t in enum_classes for t in g.objects(s, RDF.type))}
        ind_txt = [f"{_local(i)}:{_local(next(t for t in g.objects(i, RDF.type) if t in classes))}" for i in inds]
    except Exception:  # file is momentarily invalid Turtle (mid-edit): fall back to a regex scan
        cls = re.findall(r"^\s*:(\w+)\s+a\s+owl:Class", text, re.M)
        prs = re.findall(r"^\s*:(\w+)\s+a\s+owl:(?:Object|Datatype)Property", text, re.M)
        ind_txt = []
    out = []
    if known:
        ref_c = [c for c in cls if c.split("<")[0] in known]
        ext_c = [c for c in cls if c.split("<")[0] not in known]
        out.append(f"Reference classes ({len(ref_c)}): {_clip(ref_c, cl_lim)}")
        out.append(f"Classes ADDED from documents ({len(ext_c)}): {_clip(ext_c, cl_lim) or 'none yet'}")
        ref_p = [p for p in prs if p.split(":")[0] in known]
        ext_p = [p for p in prs if p.split(":")[0] not in known]
        out.append(f"Reference properties ({len(ref_p)}): {_clip(ref_p, pr_lim)}")
        out.append(f"Properties ADDED from documents ({len(ext_p)}): {_clip(ext_p, pr_lim) or 'none yet'}")
    else:
        out.append(f"Classes ({len(cls)}): {_clip(cls, cl_lim)}")
        out.append(f"Properties ({len(prs)}): {_clip(prs, pr_lim)}")
    if ind_txt:
        out.append(f"Individuals ({len(ind_txt)}): {_clip(ind_txt, in_lim)}")
    return "\n".join(out)


def compact_snapshot(path, max_lines: int, tail_lines: int = 250, known=None):
    """Return the bounded view, or None when the file is small enough to show in full."""
    p = Path(path)
    if max_lines <= 0 or not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return None
    start = max(1, len(lines) - tail_lines + 1)
    tail = "\n".join(f"{i}: {l}" for i, l in enumerate(lines[start - 1:], start))
    return (f"[COMPACT VIEW of {p.name}: {len(lines)} lines in total. Below: (1) an index of all terms already defined - REUSE them, "
            f"do not define them again; (2) the last {len(lines) - start + 1} lines, with real line numbers. New content is appended at the end.]\n"
            f"--- TERM INDEX (class<parent, property:domain->range, individual:Type) ---\n{term_index(text, known)}\n"
            f"--- LAST LINES (line numbers {start}-{len(lines)}) ---\n{tail}")
