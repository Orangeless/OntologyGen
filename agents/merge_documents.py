"""Merge the per-document ontologies of a run into one Turtle file.

    python agents/merge_documents.py ontology/lite/reference/*__reference.ttl --out merged.ttl

Every document is built on the same reference seed in the pipeline namespace (helper.reference.PIPE_NS). A file whose default prefix
points to another namespace (the Price List of our run) is rewritten to the pipeline namespace first, so that the same term is
not counted twice. The result is the union of all triples; nothing else is changed.
"""
import argparse
import logging
import re
from pathlib import Path

import rdflib

from helper.reference import PIPE_NS

logging.disable(logging.CRITICAL)   # rdflib is noisy about malformed typed literals; the viewer reports those instead


def load(path: Path) -> rdflib.Graph:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"@prefix : <[^>]*> \.", f"@prefix : <{PIPE_NS}> .", text, count=1)
    g = rdflib.Graph()
    g.parse(data=text, format="turtle")
    return g


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ontologies", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    merged = rdflib.Graph()
    for p in args.ontologies:
        g = load(p)
        merged += g
        print(f"{p.name}: {len(g)} triples")
    merged.serialize(destination=str(args.out), format="turtle")
    print(f"merged: {len(merged)} triples -> {args.out}")


if __name__ == "__main__":
    main()
