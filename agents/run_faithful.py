"""Reproduce the paper's pipeline on a set of WBA documents, as faithfully as the environment allows.

Per document (the paper builds one ontology per contract; documents run in parallel):
  1. cq_generator.py   ODP-driven competency questions (CQs) for every page          -> cqs/wba/<doc>.json
  2. multi_agent.py    domain expert -> manager -> coder -> QA review -> syntax -> OWL consistency (HermiT)
                       page by page into ONE ontology, CQs as requirements             -> ontology/faithful/<arm>/<doc>__<arm>.ttl
  3. eval.py           the paper's SPARQL-based CQ evaluation of that ontology         -> ontology/faithful/<arm>/eval/
Arms: `baseline` = the paper's method as published; `reference` = same + the reference ontology (this project's extension).

Defaults are the PAPER's settings (published QA loop on every page, 4096-token outputs, no tool-loop cap, bug-fixer limit 1000, every CQ
evaluated, the paper's insurance prompt wording). `--profile ours` switches to the settings the results in ontology/lite were produced with
(reference arm, lite checks on the important pages, 16000-token outputs, tool-loop cap 40, 60 sampled CQs, WBA wording) - see REPRODUCTION.md.

Settings follow the paper (agents see the WHOLE ontology, 20 QA cycles, 200-line reads, HermiT gate). Deviations, all forced
by the environment and listed in REPRODUCTION.md: LLM = DeepSeek V4 Flash instead of Qwen3-30B; output cap raised to 16000 tokens
(a page of Turtle does not fit in 4096); a safety cap on tool loops; Java runtime + two bug fixes in the repo (see REPRODUCTION.md).

  python agents/run_faithful.py --dry-run
  python agents/run_faithful.py --pilot --arm both --model deepseek/deepseek-v4-flash --extra-body '{"reasoning":{"enabled":false}}' --max-cost-usd 5
  python agents/run_faithful.py --arm baseline --model ... --max-cost-usd 25            # the whole scope
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The documents that matter for the NNI / CVC / AVC / bearer / service-level model (the NBN Ethernet product family). The 179-page
# DCR annexure (rebate tables), letter agreements and the Enterprise/Smart Places/Sandpit variants are left out on purpose.
SCOPE = [
    "wba-nbn-ethernet-fair-use-policy-20231201",
    "wba-nbn-ethernet-price-list-20260701",
    "wba-nbn-ethernet-product-description-20260901",
    "wba-nbn-ethernet-product-terms-20240914",
    "wba-nbn-ethernet-service-levels-schedule-20260701",
    "wba-nbn-ethernet-product-technical-specification-20260901",
]
PILOT = ["wba-nbn-ethernet-fair-use-policy-20231201"]
# The settings the results in ontology/lite were produced with (everything else is the paper's default).
OURS = dict(arm="reference", mode="lite", max_tokens=16000, qa_max_tokens=4096, max_tool_messages=40, eval_sample=60, min_words=25, domain="wba")
NET_ERRORS = ("ConnectionError", "APITimeoutError", "RateLimitError", "APIConnectionError", "InternalServerError", "Error code: 402")


# Weighted occurrences of the reference concepts on a page: where the NNI / CVC / AVC / bearer / service-level content is.
_TERMS = [(r"NNI Group", 3), (r"NNI Bearer", 3), (r"Bearer Limit", 3), (r"\bCVCs?\b", 2), (r"\bAVCs?\b", 2), (r"\bCSAs?\b", 2),
          (r"\bPOIs?\b|Point of Interconnect", 1), (r"Product Component", 2), (r"\bTC-[124]\b", 2), (r"Network Availability", 2),
          (r"\bV-NNI\b|\bS-TAG|NNI Link", 2), (r"Single Chassis|Diverse Chassis|Redundancy Mode", 3), (r"Unicast (1:1|N:1)", 3),
          (r"Connectivity Serving Area|Connectivity Virtual Circuit|Access Virtual Circuit", 2), (r"Service Level", 1),
          (r"frame (delay|loss)", 2)]


def core_pages(pdf, threshold):
    """1-based pages whose reference-concept score is >= threshold, as a '5,6,24-33' string."""
    import re
    import fitz
    rx = [(re.compile(p, re.I), w) for p, w in _TERMS]
    with fitz.open(pdf) as d:
        pages = [i + 1 for i, pg in enumerate(d) if sum(w * len(r.findall(pg.get_text("text"))) for r, w in rx) >= threshold]
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}-{pages[j]}")
        i = j + 1
    return ",".join(out)


def balance(key):
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/credits", headers={"Authorization": f"Bearer {key}"})
        d = json.load(urllib.request.urlopen(req, timeout=20))["data"]
        return d["total_credits"] - d["total_usage"]
    except Exception:
        return None


def prices(model):
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
            for m in json.load(r)["data"]:
                if m["id"] == model:
                    return float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", default=str(ROOT / "wba_data"), help="folder with the WBA PDFs (not in the repository)")
    ap.add_argument("--docs", nargs="*", default=None, help="Document name fragments (default: the NBN Ethernet scope)")
    ap.add_argument("--pilot", action="store_true", help="Only the Fair Use Policy (8 pages)")
    ap.add_argument("--arm", choices=["baseline", "reference", "both"], default="baseline")
    ap.add_argument("--reference", default=str(ROOT / "reference" / "dart_core.ttl"))
    ap.add_argument("--reference-scope", default="core")
    ap.add_argument("--out-dir", default=str(ROOT / "ontology" / "faithful"))
    ap.add_argument("--cq-dir", default=str(ROOT / "cqs" / "wba"))
    ap.add_argument("--model", default=os.getenv("MODEL_NAME"))
    ap.add_argument("--extra-body", default="", help='e.g. \'{"reasoning":{"enabled":false}}\'')
    ap.add_argument("--workers", type=int, default=3, help="Documents processed in parallel")
    ap.add_argument("--mode", choices=["paper", "lite"], default="paper",
                    help="paper = the published LLM QA loop; lite = deterministic checks (grounding, duplicates, source, CQ coverage) instead, "
                         "on the important pages only (--page-threshold)")
    ap.add_argument("--page-threshold", type=int, default=25, help="lite: keep pages whose reference-concept score is at least this")
    ap.add_argument("--page-list", default=None, help="Override the page selection for every document, e.g. \"29-30\" (tests)")
    ap.add_argument("--profile", choices=["paper", "ours"], default="paper",
                    help="paper (default) = the paper's settings; ours = the settings used for the results in ontology/lite (see REPRODUCTION.md)")
    ap.add_argument("--domain", default="insurance", help="Prompt wording: insurance = the paper's prompts (default); wba = WBA wording (ours)")
    ap.add_argument("--qa-max-tokens", type=int, default=None, help="Output cap of the QA reviewer (default: same as --max-tokens, as in the paper)")
    ap.add_argument("--eval-sample", type=int, default=0, help="CQs per document sent to eval.py (+ the held-out reference CQs); 0 = all (paper)")
    ap.add_argument("--qa-cycles", type=int, default=None, help="Paper: 20 (default); lite: 2 repair rounds")
    ap.add_argument("--read-lines", type=int, default=200, help="Paper: the QA-coder reads 200 lines around an error")
    ap.add_argument("--max-tool-messages", type=int, default=0, help="Cap on a tool loop's history; 0 = none (paper)")
    ap.add_argument("--max-tokens", type=int, default=4096, help="Output cap per LLM call (paper: 4096; our DeepSeek run needed 16000)")
    ap.add_argument("--snapshot-max-lines", type=int, default=0, help="0 = agents see the whole ontology (paper)")
    ap.add_argument("--min-words", type=int, default=0, help="Skip pages with fewer words (paper: none; ours: 25)")
    ap.add_argument("--recursion-limit", type=int, default=None, help="Default 2000 (paper) / 500 (lite)")
    ap.add_argument("--max-repair-rounds", type=int, default=None, help="Syntax/consistency repair rounds per page before the page is dropped (default: lite 3; paper 1000, the paper's limit)")
    ap.add_argument("--max-pages", type=int, default=None, help="Pilot: first N pages of each document")
    ap.add_argument("--max-cost-usd", type=float, default=None)
    ap.add_argument("--min-balance", type=float, default=2.0, help="Refuse to start if the OpenRouter balance is below this")
    ap.add_argument("--skip-eval", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    pre, _ = ap.parse_known_args()
    if pre.profile == "ours":
        ap.set_defaults(**OURS)      # explicit command-line flags still win
    args = ap.parse_args()

    lite = args.mode == "lite"
    if args.qa_cycles is None:
        args.qa_cycles = 2 if lite else 20
    if args.recursion_limit is None:
        args.recursion_limit = 500 if lite else 2000
    if args.max_repair_rounds is None:
        args.max_repair_rounds = 3 if lite else 1000
    names = PILOT if args.pilot else (args.docs or SCOPE)
    all_pdfs = sorted(Path(args.pdf_dir).glob("*.pdf"))
    pdfs = []
    for n in names:
        m = [p for p in all_pdfs if n.lower() in p.name.lower()]
        if len(m) != 1:
            sys.exit(f"'{n}' matches {len(m)} documents: {[p.name for p in m]}")
        pdfs.append(m[0])
    arms = ["baseline", "reference"] if args.arm == "both" else [args.arm]
    pagesets = {p: (args.page_list or (core_pages(p, args.page_threshold) if lite else None)) for p in pdfs}
    if lite:
        empty = [p.name for p in pdfs if not pagesets[p]]
        if empty:
            print(f"(no important pages found, skipped: {empty})")
        pdfs = [p for p in pdfs if pagesets[p]]
    out, cqd = Path(args.out_dir), Path(args.cq_dir)

    import fitz
    pages = {p: len(fitz.open(p)) for p in pdfs}
    pr = prices(args.model) if args.model else None
    print(f"profile {args.profile} | mode {args.mode} | arm(s) {args.arm} | domain {args.domain} | max tokens {args.max_tokens} | tool cap {args.max_tool_messages or 'none'} | "
          f"eval sample {args.eval_sample or 'all'} | repair rounds {args.max_repair_rounds} | QA cycles {args.qa_cycles}")
    print(f"model {args.model} | prices/M in/out: {'unknown' if not pr else f'${pr[0]*1e6:.3f}/${pr[1]*1e6:.3f}'} | arms {arms} | workers {args.workers}")
    for p in pdfs:
        sel = pagesets[p]
        n_sel = sum((int(b) - int(a) + 1) if "-" in x else 1 for x in sel.split(",") for a, _, b in [x.partition("-")]) if sel else pages[p]
        print(f"  {p.name:75s} {pages[p]:4d} pages" + (f" -> {n_sel} important pages: {sel}" if lite else ""))
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env"); key = os.getenv("OPENROUTER_API_KEY")
    bal = balance(key) if key else None
    print(f"OpenRouter balance: {'unknown' if bal is None else f'${bal:.2f}'}")
    if args.dry_run:
        return
    if bal is not None and bal < args.min_balance:
        sys.exit(f"Balance ${bal:.2f} is below --min-balance ${args.min_balance:.2f}. Add credits and re-run (progress is saved).")
    if args.max_cost_usd is not None and not pr:
        sys.exit("--max-cost-usd needs prices: pass an OpenRouter --model id")

    from helper.usage import total_usage
    (out).mkdir(parents=True, exist_ok=True); cqd.mkdir(parents=True, exist_ok=True)
    for a in arms:
        (out / a / "logs").mkdir(parents=True, exist_ok=True); (out / a / "eval").mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)
    state_f = out / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else {"done": [], "failed": []}
    lock = threading.Lock()

    def mark(stage, ok):
        with lock:
            k = "done" if ok else "failed"
            state[k] = sorted(set(state[k]) | {stage})
            if ok:
                state["failed"] = [x for x in state["failed"] if x != stage]
            state_f.write_text(json.dumps(state, indent=1), encoding="utf-8")

    def spend():
        ti = to = n = 0
        for f in out.rglob("*.usage.jsonl"):
            a, b, c = total_usage(f); n += a; ti += b; to += c
        return n, ti, to, (ti * pr[0] + to * pr[1]) if pr else 0.0

    jre = next(iter(sorted((ROOT / ".jre").glob("*/bin"))), None)
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONUNBUFFERED="1", MAX_QA_CYCLES=str(args.qa_cycles), READ_CONTEXT_LINES=str(args.read_lines),
               MAX_TOOL_MESSAGES=str(args.max_tool_messages), LLM_MAX_TOKENS=str(args.max_tokens), QA_MAX_TOKENS=str(args.qa_max_tokens or args.max_tokens),
               ONTOLOGY_SNAPSHOT_MAX_LINES=str(args.snapshot_max_lines), PIPELINE_MODE=args.mode, MAX_DEBUG_CYCLES=str(args.max_repair_rounds))
    if jre:
        env["PATH"] = str(jre) + os.pathsep + env.get("PATH", "")
    if args.model:
        env["MODEL_NAME"] = args.model
    if args.extra_body:
        env["LLM_EXTRA_BODY"] = args.extra_body
    procs, stop = [], threading.Event()

    def stage(name, cmd, log_path, usage_path, tries=3):
        """Run one subprocess; retry on network trouble. Returns (ok, log text)."""
        e = dict(env, USAGE_LOG=str(usage_path))
        text, rc = "", 1
        for attempt in range(1, tries + 1):
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\n# attempt {attempt}: {' '.join(map(str, cmd[1:]))}\n"); log.flush()
                p = subprocess.Popen(cmd, cwd=ROOT, env=e, stdout=log, stderr=subprocess.STDOUT)
                procs.append(p); rc = p.wait()
            text = Path(log_path).read_text(encoding="utf-8", errors="ignore")
            tail = text[-20000:]
            trouble = sum(tail.count(k) for k in NET_ERRORS)
            if (rc == 0 and trouble < 3) or stop.is_set() or attempt == tries:
                break
            wait = 60 * attempt * attempt
            print(f"  [{time.strftime('%H:%M:%S')}] {name}: network trouble ({trouble} errors) - retry {attempt}/{tries - 1} in {wait}s", flush=True)
            time.sleep(wait)
        return rc == 0 and not stop.is_set(), text

    def cq_stage(pdf):
        name = f"cq:{pdf.stem}"
        cq = cqd / f"{pdf.stem}.json"
        if name in state["done"]:
            return
        cmd = [sys.executable, str(ROOT / "agents" / "cq_generator.py"), "--contract", str(pdf), "--out", str(cq), "--domain", args.domain,
               "--min-words", str(args.min_words)] + (["--max-pages", str(args.max_pages)] if args.max_pages else [])               + (["--page-list", pagesets[pdf]] if pagesets[pdf] else [])
        ok, _ = stage(name, cmd, out / "logs" / f"{pdf.stem}.cq.log", out / "logs" / f"{pdf.stem}.cq.usage.jsonl")
        n = sum(len(v) for d in json.loads(cq.read_text(encoding="utf-8")).values() for v in d.values()) if cq.exists() else 0
        mark(name, ok and n > 0)
        print(f"  [{time.strftime('%H:%M:%S')}] CQs {pdf.stem[:55]}: {n} questions ({'ok' if ok else 'FAILED'})", flush=True)

    def onto_stage(pdf, arm):
        name = f"onto:{arm}:{pdf.stem}"
        ttl = out / arm / f"{pdf.stem}__{arm}.ttl"
        if name in state["done"]:
            return
        prog = ttl.with_suffix(".progress.json")
        start = json.loads(prog.read_text()).get(pdf.stem, 0) if prog.exists() else 0
        cmd = [sys.executable, str(ROOT / "agents" / "multi_agent.py"), "--contract", str(pdf), "--cqs", str(cqd / f"{pdf.stem}.json"),
               "--ontology-out", str(ttl), "--domain", args.domain, "--recursion-limit", str(args.recursion_limit), "--min-words", str(args.min_words),
               "--start-page", str(start)]
        if args.snapshot_max_lines:
            cmd += ["--snapshot-max-lines", str(args.snapshot_max_lines)]
        if arm == "reference":
            cmd += ["--reference", args.reference, "--reference-scope", args.reference_scope]
        if args.max_pages:
            cmd += ["--max-pages", str(args.max_pages)]
        if pagesets[pdf]:
            cmd += ["--page-list", pagesets[pdf]]
        if lite:
            cmd += ["--mode", "lite"]
        ok, text = stage(name, cmd, out / arm / "logs" / f"{pdf.stem}.log", out / arm / "logs" / f"{pdf.stem}.usage.jsonl")
        good, bad, empty = text.count("processed successfully"), text.count("FAILED after retries"), text.count("added NO new")
        ok = ok and bad <= max(1, (good + bad) // 2)
        mark(name, ok)
        n, ti, to, cost = spend()
        print(f"  [{time.strftime('%H:%M:%S')}] ontology {arm:9s} {pdf.stem[:50]}: {good} pages ok, {bad} failed, {empty} empty | total {n} calls ~${cost:.2f}"
              f" {'' if ok else '(FAILED)'}", flush=True)

    def eval_stage(pdf, arm):
        name = f"eval:{arm}:{pdf.stem}"
        ttl = out / arm / f"{pdf.stem}__{arm}.ttl"
        if name in state["done"] or not ttl.exists() or f"onto:{arm}:{pdf.stem}" not in state["done"]:
            return
        cq_file = cqd / f"{pdf.stem}.json"
        if args.eval_sample:
            allq = [q for d in json.loads(cq_file.read_text(encoding="utf-8")).values() for v in d.values() for q in v]
            step = max(1, len(allq) // args.eval_sample)
            picked = allq[::step][: args.eval_sample]
            held = ROOT / "cqs" / "reference" / f"{pdf.stem}.json"  # the 8 reference CQs were never shown to the agents
            if held.exists():
                picked += [q for d in json.loads(held.read_text(encoding="utf-8")).values() for v in d.values() for q in v]
            cq_file = out / arm / "eval" / f"{pdf.stem}__eval_cqs.json"
            cq_file.write_text(json.dumps({pdf.stem: {"0": [{"competency_question": q["competency_question"], "expected_answer": q["expected_answer"]} for q in picked]}}, indent=1), encoding="utf-8")
        cmd = [sys.executable, str(ROOT / "agents" / "eval.py"), str(cq_file), str(ttl)]
        ok, _ = stage(name, cmd, out / arm / "logs" / f"{pdf.stem}.eval.log", out / arm / "logs" / f"{pdf.stem}.eval.usage.jsonl")
        rep = ROOT / "agents" / "eval_results" / f"evaluation_report_{ttl.stem}.json"
        score = None
        if rep.exists():
            shutil.copyfile(rep, out / arm / "eval" / rep.name)
            score = json.loads(rep.read_text(encoding="utf-8")).get("average_score")
        mark(name, ok and score is not None)
        print(f"  [{time.strftime('%H:%M:%S')}] eval     {arm:9s} {pdf.stem[:50]}: average score {score if score is None else round(score, 3)}", flush=True)

    def watchdog():
        while not stop.is_set():
            time.sleep(15)
            if args.max_cost_usd is not None and spend()[3] >= args.max_cost_usd:
                print(f"!! spend cap ${args.max_cost_usd:.2f} reached - stopping", flush=True)
                stop.set()
                for p in procs:
                    if p.poll() is None:
                        p.kill()
    threading.Thread(target=watchdog, daemon=True).start()

    def phase(title, fn, jobs):
        print(f"\n=== {title} ===", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(lambda j: fn(*j), jobs))
        if stop.is_set():
            sys.exit("STOPPED at the spend cap. Progress is saved; re-run to continue.")

    t0 = time.time()
    phase("1/3 competency questions (cq_generator.py)", cq_stage, [(p,) for p in pdfs])
    phase("2/3 ontology generation (multi_agent.py)", onto_stage, [(p, a) for p in pdfs for a in arms])
    if not args.skip_eval:
        phase("3/3 evaluation (eval.py)", eval_stage, [(p, a) for p in pdfs for a in arms])
    n, ti, to, cost = spend()
    print(f"\nDONE in {(time.time() - t0) / 60:.0f} min. {n} calls, {ti / 1e6:.1f}M in / {to / 1e6:.2f}M out" + (f", ~${cost:.2f}" if pr else "")
          + f". Failed stages: {state['failed'] or 'none'}")


if __name__ == "__main__":
    main()
