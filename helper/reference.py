"""Reference-ontology support for the multi-agent pipeline.

A reference ontology (e.g. reference/dart_core.ttl) is shown to the agents in three ways:
  1. SEED      the schema is written into the output .ttl before generation (so QA / checkers see it too),
               rewritten into the pipeline namespace so the agents can reuse the terms directly;
  2. VOCABULARY a short summary (names, "also called" names, attributes, links) for agents that never see the file;
  3. POLICY    rules: reuse reference terms, ADD whatever the documents contain that the reference lacks
               (tagged "[extension: <document>, <clause>]"), attach a :sourceClause to every contract fact.
agents/reference_report.py reads the same tags afterwards to show what matched and what the documents added.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import rdflib
from rdflib import BNode, Namespace, OWL, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.namespace import SKOS, XSD

REF_NS = "http://www.example.com/dart#"      # namespace of dart_core.ttl
PIPE_NS = "http://www.example.org/test#"     # namespace the pipeline's coder is told to use
D = Namespace(REF_NS)
WBA_LAYER, NPO_LAYER = "WBA/legacy fixture", "DART-NPO"

SEED_MARKER = "# ===== REFERENCE SEED: reference ontology schema (reuse these terms; do not delete) ====="
EXT_MARKER = "# ===== EXTENSIONS FROM DOCUMENTS: append new classes / properties / facts BELOW this line ====="
EXTENSION_TAG = re.compile(r"\[extension:\s*([^\]]+)\]", re.I)

_DROP = {D.layer, D.npoModule, D.legacyName, D.relationSource, D.endpointsInferred, D.simulatorRelationship,
         D.origin, D.wbaOnly, D.simulationOnly}
_SHORT = {XSD.integer: "int", XSD.double: "decimal", XSD.boolean: "bool", XSD.string: "text", XSD.dateTime: "datetime"}
_HEADER = (
    "@prefix : <" + PIPE_NS + "> .\n"
    "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
    "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n\n"
)


def local(u) -> str:
    return str(u).split("#")[-1].split("/")[-1]


def normalize_term(s: str) -> str:
    """Name used for matching: lower case, no separators, no Cl_/Op_/Dp_ style prefixes."""
    s = re.sub(r"^(cl|op|dp|dtp)_", "", str(s).strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Reference:
    """scope: 'core' = the WBA-named entities (+ parents, attributes, enumerations, directly linked classes);
              'wba'  = every class of the wholesale (WBA/legacy) layer;  'all' = also the DART-NPO layer."""

    def __init__(self, path, scope: str = "wba"):
        if scope not in ("core", "wba", "all"):
            raise ValueError("scope must be core, wba or all")
        self.path, self.scope = Path(path), scope
        self.g = rdflib.Graph()
        self.g.parse(str(path), format="turtle")
        g = self.g
        self.enums = {c for c in g.subjects(OWL.equivalentClass, None) if isinstance(c, URIRef)}
        self.all_classes = {c for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef) and c not in self.enums}
        self._select()

    # ------------------------------------------------------------ scope selection
    def layers(self, x) -> set:
        return {str(o) for o in self.g.objects(x, D.layer)}

    def members(self, x) -> list:
        if isinstance(x, BNode):
            u = self.g.value(x, OWL.unionOf)
            return list(Collection(self.g, u)) if u else []
        return [x]

    def _npo_only(self, p) -> bool:
        lay, org = self.layers(p), {str(o) for o in self.g.objects(p, D.origin)}
        return lay == {NPO_LAYER} or (bool(org) and all(NPO_LAYER in o for o in org))

    def _select(self):
        g = self.g
        if self.scope == "all":
            S = set(self.all_classes)
        elif self.scope == "wba":
            S = {c for c in self.all_classes if WBA_LAYER in self.layers(c)}
        else:
            S = {c for c in self.all_classes if WBA_LAYER in self.layers(c) and any(g.objects(c, SKOS.altLabel))}
        while True:  # parents
            add = {p for c in S for p in g.objects(c, RDFS.subClassOf) if p in self.all_classes} - S
            if not add:
                break
            S |= add
        all_props = set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(g.subjects(RDF.type, OWL.DatatypeProperty))
        props, extra, enums = {}, set(), set()
        for p in all_props:
            if self.scope != "all" and self._npo_only(p):
                continue
            dom = [d for x in g.objects(p, RDFS.domain) for d in self.members(x) if d in S]
            if not dom:
                continue
            rng = [r for x in g.objects(p, RDFS.range) for r in self.members(x)]
            for r in rng:
                if r in self.enums:
                    enums.add(r)
                elif r in self.all_classes and r not in S and (self.scope == "all" or WBA_LAYER in self.layers(r)):
                    extra.add(r)
            props[p] = (dom, rng)
        S |= extra
        self.classes, self.props, self.used_enums = S, props, enums

    # ------------------------------------------------------------ naming helpers
    def comment(self, x) -> str:
        return str(self.g.value(x, RDFS.comment) or "")

    def aliases(self, x) -> list:
        return sorted(str(a) for a in self.g.objects(x, SKOS.altLabel))

    def enum_members(self, e) -> list:
        return sorted(str(self.g.value(m, RDFS.label) or local(m)) for m in self.g.subjects(RDF.type, e) if m != e)

    def match_index(self) -> dict:
        """normalised name -> reference term (class or property) for matching generated terms."""
        idx = {}
        for c in self.classes:
            for n in [local(c), str(self.g.value(c, RDFS.label) or "")] + self.aliases(c):
                if n:
                    idx.setdefault(normalize_term(n), c)
        for p in self.props:
            for n in (local(p), str(self.g.value(p, RDFS.label) or "")):
                if n:
                    idx.setdefault(normalize_term(n), p)
        return idx

    # ------------------------------------------------------------ 1. seed
    def _m(self, n):
        return URIRef(PIPE_NS + str(n)[len(REF_NS):]) if isinstance(n, URIRef) and str(n).startswith(REF_NS) else n

    def _copy_bnodes(self, out, node, seen):
        for p, o in self.g.predicate_objects(node):
            out.add((node, self._m(p), self._m(o)))
            if isinstance(o, BNode) and o not in seen:
                seen.add(o)
                self._copy_bnodes(out, o, seen)

    def _union(self, out, xs):
        xs = [self._m(x) for x in xs]
        if len(xs) == 1:
            return xs[0]
        b, head = BNode(), BNode()
        out.add((b, RDF.type, OWL.Class))
        Collection(out, head, xs)
        out.add((b, OWL.unionOf, head))
        return b

    def seed_graph(self) -> rdflib.Graph:
        g, out = self.g, rdflib.Graph()
        S, E = self.classes, self.used_enums
        for c in S:
            for p, o in g.predicate_objects(c):
                if p in _DROP or (p == RDF.type and o != OWL.Class) or p == OWL.equivalentClass:
                    continue
                if p == RDFS.subClassOf and o not in S:
                    continue
                out.add((self._m(c), self._m(p), self._m(o)))
        for e in E:
            for p, o in g.predicate_objects(e):
                if p in _DROP:
                    continue
                out.add((self._m(e), self._m(p), self._m(o)))
                if isinstance(o, BNode):
                    self._copy_bnodes(out, o, {o})
            for m in g.subjects(RDF.type, e):
                if m == e:
                    continue
                for p, o in g.predicate_objects(m):
                    if p in (RDF.type, RDFS.label, RDFS.comment):
                        out.add((self._m(m), self._m(p), self._m(o)))
        keep_ranges = S | E
        for p, (dom, rng) in self.props.items():
            for pp, o in g.predicate_objects(p):
                if pp in _DROP or pp in (RDFS.domain, RDFS.range) or (pp == RDF.type and o == OWL.FunctionalProperty):
                    continue
                out.add((self._m(p), self._m(pp), self._m(o)))
            out.add((self._m(p), RDFS.domain, self._union(out, dom)))
            rk = [r for r in rng if str(r).startswith(str(XSD)) or r in keep_ranges]
            if rk:
                out.add((self._m(p), RDFS.range, self._union(out, rk)))
        kept = {self._m(p) for p in self.props}
        for c in S:  # named individuals of WBA classes that carry reference data (e.g. TC-1/TC-2/TC-4)
            if local(c) != "TrafficClass":
                continue
            for i in g.subjects(RDF.type, c):
                for p, o in g.predicate_objects(i):
                    if p in (RDF.type, RDFS.label) or self._m(p) in kept:
                        out.add((self._m(i), self._m(p), self._m(o)))
        sc = URIRef(PIPE_NS + "sourceClause")
        out.add((sc, RDF.type, OWL.AnnotationProperty))
        out.add((sc, RDFS.comment, rdflib.Literal("Document, clause and page a contract fact comes from.")))
        return out

    def seed_turtle(self) -> str:
        out = self.seed_graph()
        for pfx, ns in (("", PIPE_NS), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD), ("owl", OWL), ("skos", SKOS)):
            out.bind(pfx, ns, override=True)
        body = out.serialize(format="turtle")
        body = re.sub(r"(?m)^@prefix[^\n]*\n", "", body).lstrip()
        return f"{_HEADER}{SEED_MARKER}\n{body}\n\n{EXT_MARKER}\n"

    # ------------------------------------------------------------ 2. vocabulary summary
    def vocabulary_summary(self) -> str:
        g = self.g
        by_class = defaultdict(list)
        for p, (dom, rng) in self.props.items():
            if rng and all(r in self.enums for r in rng):
                txt = f"{local(p)} in {{{'|'.join(self.enum_members(rng[0]))}}}"
            elif rng and all(str(r).startswith(str(XSD)) for r in rng):
                txt = f"{local(p)}({_SHORT.get(rng[0], 'text')})"
            else:
                txt = f"{local(p)}->{'/'.join(local(r) for r in rng) or '?'}"
            for d in dom:
                by_class[d].append(txt)
        lines = []
        for c in sorted(self.classes, key=local):
            parents = [local(p) for p in g.objects(c, RDFS.subClassOf) if p in self.classes]
            desc = self.comment(c).split(". ")[0][:160]
            seg = [f"- {local(c)}" + (f" (is-a {', '.join(parents)})" if parents else "") + (f": {desc}" if desc else "")]
            # single lower-case words ("order", "price", "charge") are too generic to steer the agents
            alias = [a for a in self.aliases(c) if not (a == a.lower() and " " not in a)]
            if alias:
                seg.append("also called: " + "; ".join(alias))
            if by_class.get(c):
                seg.append("properties: " + ", ".join(sorted(by_class[c])))
            lines.append(" | ".join(seg))
        return "\n".join(lines)

    # ------------------------------------------------------------ 3. policy / prompt block
    _POLICY = (
        "REFERENCE ONTOLOGY (authoritative starting vocabulary)\n"
        "The ontology file already contains the reference schema between the REFERENCE SEED and EXTENSIONS markers.\n"
        "1. REUSE: when the text refers to a concept that matches a reference class, property or enumeration value - by its name or by any "
        "'also called' name - use that reference term exactly (same IRI). Never create a duplicate such as NNI_Group when NNIGroup exists.\n"
        "2. EXTEND: the documents may state things the reference lacks (new defined terms, products, charges, service levels, rebates, "
        "obligations, limits, conditions, exceptions). ADD them below the EXTENSIONS marker as new classes/properties, placed under the closest "
        "reference class with rdfs:subClassOf / rdfs:subPropertyOf. Every NEW class or property needs an rdfs:label and an rdfs:comment that ends "
        "with `[extension: <document name>, <clause or page>]`.\n"
        "3. FACTS: represent every contract fact (numbers, rates, percentages, charges, durations, thresholds, conditions) as triples. A simple "
        "limit is a data-property value on an individual of the governed class (a property holding the number the text states). A limit "
        "that depends on rate, mode or product is one individual per condition carrying the condition and the value. Keep units (Gbps, Mbps, AUD, "
        "ISO-8601 durations). Add `:sourceClause \"<document>, <clause>, p.<page>\"` to every fact individual.\n"
        "4. NEVER delete or weaken reference axioms. If the document disagrees with a reference value or constraint, keep both: record the "
        "document's value as its own fact with its :sourceClause and add `rdfs:comment \"[conflicts with reference: <what>]\"`.\n"
        "5. Only instantiate reference concepts the document actually talks about; do not invent facts."
    )
    _ROLE = {
        "expert": ("In `key_concepts`, use the reference term as `term` when the text matches it (by name or alias). Concepts that are NOT in the "
                   "reference are still wanted: list them normally - they become documented extensions. Put every numeric limit/condition in the SRD."),
        "manager": ("In 'Reuse & Extension Plan': under **Extend/Reuse** name the reference terms to reuse; under **Create New** list only what the "
                    "reference lacks (these are extensions and must be tagged as described). Plan which contract facts become individuals."),
        "coder": ("Append new material AFTER the EXTENSIONS marker (the end of the file). Do not rewrite the reference seed. Reuse reference IRIs; "
                  "tag extensions; add :sourceClause to facts."),
        "qa": ("Also check: reference terms are reused (no renamed duplicates), reference axioms are intact, every new class/property carries an "
               "`[extension: ...]` comment, and every contract fact has a :sourceClause. Flag violations as CHANGES_REQUIRED."),
        "qa_coder": "Keep the reference seed intact; fix only what the feedback asks. New terms keep their `[extension: ...]` comment.",
    }

    def prompt_block(self, role: str) -> str:
        parts = ["\n\n" + self._POLICY]
        if role in ("expert", "manager"):
            parts.append("REFERENCE VOCABULARY (class (is-a) | also called | properties):\n" + self.vocabulary_summary())
        parts.append(self._ROLE.get(role, ""))
        return "\n".join(p for p in parts if p) + "\n"
