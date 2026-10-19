"""Build competency questions (CQs) with expected answers from the reference's confirmed WBA evidence.

Source: rule_trace/register/links.yaml (rule id -> WBA value + the quoted clause, document and page).
Output: one CQ file per WBA document in the format multi_agent.py / eval.py already use:
    {"<document stem>": {"<0-based page index>": [{"competency_question": ..., "expected_answer": ..., "source": ...}]}}
so the agents are asked to capture these contract facts on the right page, and eval.py can later check them.

Page numbers in links.yaml are 1-based PDF pages; if PyMuPDF is installed the quote is located in the PDF
to confirm (or correct) the page.

Usage: python agents/make_reference_cqs.py --links <path to rule_trace/register/links.yaml> [--pdf-dir wba_data] [--out-dir cqs/reference]
"""
import argparse
import json
from pathlib import Path

import yaml

# Hand-written so the question is natural and the expected answer is exactly the WBA value.
QUESTIONS = {
    "PL-01": [
        ("What is the maximum number of NNI Bearers an NNI Group can support for 1Gbps and 10Gbps NNI Bearers?", "8"),
        ("What is the maximum number of 100Gbps NNI Bearers in an NNI Group in Single Chassis mode and in Diverse Chassis mode?",
         "2 in Single Chassis mode; 4 in Diverse Chassis mode"),
    ],
    "PL-03": [("How many CVCs can an NNI Group support in aggregate?", "4,000")],
    "PL-04": [("What is the Bearer Limit for an NNI Group of 2 x 100Gbps NNI Bearers in Diverse Chassis mode?", "31,000")],
    "RD-02": [("Which interface rates can the NNI Bearers of an NNI Group have, and must all bearers in the group use the same rate?",
               "1, 10 or 100 Gbps; every NNI Bearer must be consistent with the group interface rate")],
    "RD-03": [("Which redundancy modes can an NNI Group be configured in?", "Single Chassis or Diverse Chassis")],
    "RD-09": [("What Network Availability does nbn aim to achieve for the NBN Ethernet product?", "99.90% (99.70% for satellite)")],
    "RD-10": [("Do Unicast 1:1 AVCs count toward the Bearer Limit?", "Yes, the Bearer Limit includes the total number of Unicast 1:1 AVCs")],
}


def find_page(pdf: Path, quote: str, hint0: int):
    """0-based page whose text contains the quote, searching around the hint. Returns (page0, verified)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return hint0, False
    if not pdf.exists():
        return hint0, False
    needle = " ".join(quote.split()).lower()[:70]
    doc = fitz.open(pdf)
    try:
        order = [hint0] + [hint0 + d for d in (1, -1, 2, -2)] + list(range(len(doc)))
        for p in dict.fromkeys(order):
            if 0 <= p < len(doc) and needle in " ".join(doc[p].get_text("text").split()).lower():
                return p, True
    finally:
        doc.close()
    return hint0, False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--links", required=True, help="links.yaml of the reference project (rule_trace/register/links.yaml)")
    ap.add_argument("--pdf-dir", default=str(root / "wba_data"))
    ap.add_argument("--out-dir", default=str(root / "cqs" / "reference"))
    args = ap.parse_args()

    links = yaml.safe_load(Path(args.links).read_text(encoding="utf-8"))["links"]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    per_doc: dict[str, dict[str, list]] = {}
    for rid, qas in QUESTIONS.items():
        link = links.get(rid)
        if not link or not link.get("clauses"):
            print(f"skip {rid}: no confirmed clause in links.yaml")
            continue
        clause = link["clauses"][0]
        doc = clause["document"]
        page0, ok = find_page(Path(args.pdf_dir) / doc, clause.get("quote", ""), int(clause["page"]) - 1)
        for q, a in qas:
            entry = {"competency_question": q, "expected_answer": a,
                     "source": f"reference {rid}: {doc} section {clause.get('section')} page {page0 + 1}"
                               + ("" if ok else " (page not verified)")}
            per_doc.setdefault(doc, {}).setdefault(str(page0), []).append(entry)
        print(f"{rid}: page {page0 + 1} {'verified' if ok else 'NOT verified'}  {doc}")
    for doc, pages in per_doc.items():
        f = out_dir / f"{Path(doc).stem}.json"
        f.write_text(json.dumps({Path(doc).stem: pages}, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {f}  ({sum(len(v) for v in pages.values())} CQs)")
    (out_dir / "_empty.json").write_text(json.dumps({"empty": {}}), encoding="utf-8")  # for documents without reference CQs


if __name__ == "__main__":
    main()
