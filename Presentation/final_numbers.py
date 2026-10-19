"""Final numbers of the lite run, read from the real files (logs, CQ files, evaluation reports). Used by build_deck.py.

For every document:
  pages        selected / in the ontology / failed / ran-but-added-nothing (from the last run's log)
  eval         the pipeline's own SPARQL + judge evaluation (60 CQs sampled over all selected pages + the held-out reference CQs):
               "processed" = the paper's rule (an empty query result counts as 0), also split by whether the CQ's page is in the ontology
  per_page     [(page, triples added, input tokens, output tokens, calls, ontology triples before the page)]  - from the [added]/[usage] log lines
"""
import glob
import json
import re
from pathlib import Path

DOCS = {"Technical Specification": "technical-specification", "Product Description": "product-description",
        "Price List": "price-list", "Service Levels": "service-levels"}
SEED_TRIPLES = 617          # the reference seed (scope core) that every ontology starts with


def _pages(spec):
    return sorted({x for part in spec.split(",") for x in range(int(part.split("-")[0]), int(part.split("-")[-1]) + 1)})


def compute(repo: Path):
    repo = Path(repo)
    out = {}
    for name, key in DOCS.items():
        ttl = glob.glob(str(repo / f"ontology/lite/reference/*{key}*__reference.ttl"))[0]
        stem = Path(ttl).name.replace("__reference.ttl", "")
        log = (repo / f"ontology/lite/reference/logs/{stem}.log").read_text(encoding="utf-8", errors="ignore")
        first = int(re.search(r"--- Starting Page (\d+) of", log).group(1))
        tail = log[log.rfind(f"--- Starting Page {first} of"):]
        total_pages_doc = int(re.search(r"--- Starting Page \d+ of (\d+)", log).group(1))
        sel = _pages(re.search(r"--page-list ([\d,\-]+)", log).group(1))

        # ---- per page: triples added, tokens (the [usage] lines are cumulative per process, so take differences)
        chunks = re.split(r"--- Starting Page (\d+) of \d+ ---", tail)
        per_page, prev, size = [], (0, 0, 0), SEED_TRIPLES
        for i in range(1, len(chunks), 2):
            page, body = int(chunks[i]), chunks[i + 1]
            a = re.search(r"\[added\] page=\d+ triples=(\d+)", body)
            u = re.search(r"\[usage\] calls=(\d+) in=(\d+) out=(\d+)", body)
            if not a or not u:
                continue
            cur = (int(u.group(1)), int(u.group(2)), int(u.group(3)))
            if any(c < p for c, p in zip(cur, prev)):
                prev = (0, 0, 0)                      # a new process started: counters restart
            d = tuple(c - p for c, p in zip(cur, prev))
            prev = cur
            per_page.append(dict(page=page, added=int(a.group(1)), calls=d[0], tin=d[1], tout=d[2], size_before=size,
                                 failed=f"Page {page} FAILED" in body))
            size += int(a.group(1))
        added = {p["page"]: p["added"] for p in per_page}
        failed = sorted(p["page"] for p in per_page if p["failed"] and p["added"] == 0)
        ing = sorted(p for p, t in added.items() if t > 0)
        empty = sorted(p for p, t in added.items() if t == 0 and p not in failed)

        # ---- evaluation
        cq = next(iter(json.load(open(repo / f"cqs/wba/{stem}.json", encoding="utf-8")).values()))
        q2p = {q["competency_question"]: int(p) + 1 for p, l in cq.items() for q in l}
        rep = json.load(open(repo / f"ontology/lite/reference/eval/evaluation_report_{stem}__reference.json", encoding="utf-8"))
        hf = repo / f"cqs/reference/{stem}.json"
        held = {q["competency_question"] for d in json.load(open(hf, encoding="utf-8")).values() for v in d.values() for q in v} if hf.exists() else set()
        proc = lambda x: 0.0 if isinstance(x["query_result"], list) and not x["query_result"] else x["score"]
        res = rep["results"]
        main = [x for x in res if x["competency_question"] not in held]
        hd = [x for x in res if x["competency_question"] in held]
        avg = lambda xs, f=proc: round(sum(f(x) for x in xs) / len(xs), 3) if xs else None
        inp = [x for x in main if q2p.get(x["competency_question"]) in ing]
        outp = [x for x in main if q2p.get(x["competency_question"]) not in ing]
        num = lambda x: bool(re.search(r"\d", str(x["expected_answer"])))
        out[name] = dict(
            stem=stem, doc_pages=total_pages_doc, selected=len(sel), selected_pages=sel, in_ontology=ing, failed=failed, empty=empty,
            eval=dict(n=len(main), raw=avg(main, lambda x: x["score"]), processed=avg(main), n_held=len(hd), held_processed=avg(hd),
                      in_pages=dict(n=len(inp), score=avg(inp)), out_pages=dict(n=len(outp), score=avg(outp)),
                      numeric=dict(n=sum(1 for x in main if num(x)), score=avg([x for x in main if num(x)])),
                      text=dict(n=sum(1 for x in main if not num(x)), score=avg([x for x in main if not num(x)])),
                      perfect=sum(1 for x in main if proc(x) >= 0.999), partial=sum(1 for x in main if 0.001 < proc(x) < 0.999),
                      zero=sum(1 for x in main if proc(x) <= 0.001), report_average=round(rep["average_score"], 3)),
            per_page=per_page,
            tokens=dict(tin=sum(p["tin"] for p in per_page), tout=sum(p["tout"] for p in per_page), calls=sum(p["calls"] for p in per_page)))
    return out


if __name__ == "__main__":
    import sys
    r = compute(Path(sys.argv[1]))
    for n, d in r.items():
        t = d["tokens"]
        print(f"{n:24s} pages in {len(d['in_ontology'])}/{d['selected']} failed {d['failed']} empty {d['empty']} | tokens in {t['tin']/1e6:.1f}M out {t['tout']/1e6:.2f}M calls {t['calls']} | eval {d['eval']['processed']} raw {d['eval']['raw']} in/out pages {d['eval']['in_pages']['score']}/{d['eval']['out_pages']['score']}")
        print("   per page:", [(p["page"], p["added"], round(p["tin"] / 1e6, 2), p["size_before"]) for p in d["per_page"]][:40])
