# Ontology generation for NBN Wholesale Broadband Agreement documents

A fork of [brains-group/towards_automated_ontology_generation](https://github.com/brains-group/towards_automated_ontology_generation),
the code of *Towards Automated Ontology Generation from Unstructured Text: A Multi-Agent LLM Approach* (Talukder, Mridul, Seneviratne).
The original README is kept as [README_upstream.md](README_upstream.md).

This fork applies the paper's pipeline to four NBN Ethernet WBA documents (Product Technical Specification, Product Description,
Price List, Service Levels Schedule) and starts from a reference ontology (Farzad's DART ontology) instead of an empty file.
**The pipeline and its defaults are the paper's; every addition is opt-in.** `git diff aa2fb01` shows exactly what was changed
(`aa2fb01` is the last upstream commit this fork is based on).

## What the pipeline does (unchanged)

CQ generator &rarr; Domain Expert (SRD) &rarr; Manager (TIP with ODPs) &rarr; Coder (edits a Turtle file with file tools) &rarr; QA
(architectural review, RDFLib syntax check, HermiT consistency check, bug fixer) &rarr; SPARQL + LLM-judge evaluation. See
[README_upstream.md](README_upstream.md) and the paper.

## What this fork adds

### New files

| File | Purpose |
|---|---|
| `agents/run_faithful.py` | One command for several documents in parallel: CQs &rarr; ontology &rarr; evaluation. Resumable, spend cap, retries on network errors. `--profile paper` (default) or `--profile ours` (see below). |
| `helper/reference.py`, `reference/dart_core.ttl`, [REFERENCE.md](REFERENCE.md) | **Reference ontology support** (`--reference`): the reference schema is written into the ontology file before page 1, and a short brief (reuse / extend / source-clause rules, plus a term list for Expert and Manager) is added to each agent's prompt. New terms must carry `[extension: document, page]`, every fact a `:sourceClause`. |
| `helper/grounding.py` | Deterministic checks of `--mode lite` (no LLM): numbers/dates on the page, duplicate classes, source clause, CQ answer present, subclass cycles, reference individuals untouched; and the last-resort removal of values the page does not state. |
| `helper/domain_profiles.py` | Prompt wording per domain. `insurance` reproduces the original prompts exactly; `wba` is ours. |
| `helper/snapshot.py` | Optional compact ontology view for the prompts (`ONTOLOGY_SNAPSHOT_MAX_LINES`, off by default: the paper pastes the whole file). |
| `helper/usage.py` | Token usage log per LLM call (used for the spend cap and the cost numbers). |
| `models/competency_questions.py` | CQ schema. The original imports this module but does not ship it; reconstructed from the prompt and the published CQ files. |
| `agents/make_reference_cqs.py`, `agents/reference_report.py` | Held-out CQs from the reference project's confirmed clauses; report of what matched the reference and what the documents added. |
| `agents/merge_documents.py` | Union of the per-document ontologies into one file. |
| `viewer/` | `build_viz.py` + `template.html`: offline single-file viewer (class tree, graph with a readable PNG/SVG export, properties, facts with their sources). |
| `cqs/wba/`, `cqs/reference/` | Generated WBA competency questions; the 8 held-out reference CQs. |
| `ontology/lite/reference/` | **Results of the run** (ontologies, evaluation reports, logs); `ontology/RESULTS_MANIFEST.json` holds a SHA-256 of every result file. |
| `Presentation/` | The deck, the figures and the scripts that build them from the files above. |

### Changes to files of the original repository

| File | Diff | What changed and why |
|---|---|---|
| `agents/multi_agent.py` | +235 / &minus;29 | `--reference` wiring (one line per agent: `_ref_block(role)`) and seeding of an empty file; `--mode lite` (verify node using `helper/grounding.py`, bounded repair rounds); each page is isolated (a failed page is rolled back; the number of triples a page really added is reported); `--page-list`, `--min-words`, `--domain`; token logging, request timeout, own output cap for the QA reviewer; optional compact snapshot; resumable progress file; fix of the undefined variable `review_type`. Without `--reference` and with the default mode the behaviour is the original. |
| `agents/cq_generator.py` | +170 / &minus;68 | Import fixes (`langchain_core.messages`, `get_vllm_llm`); `--domain` wording (insurance = original prompt); `--page-list`, `--min-words`, `--max-pages`, `--start-page`; resumable; stores exactly the two published fields; retries malformed JSON. |
| `tools/syntax_checks.py` | +15 / &minus;4 | HermiT now reasons over the world the ontology was loaded into (the original ran it on the default, empty world and **never detected an inconsistency**); clearer messages for inconsistency and inheritance cycles; UTF-8. |
| `tools/file_management.py` | +32 / &minus;4 | `delete_lines_from_file` (used by `multi_agent.py`, defined nowhere in the original); explicit UTF-8. |
| `helper/connections.py` | +27 / &minus;9 | Endpoint from `LLM_BASE_URL` / `LLM_API_KEY`, or OpenRouter when `OPENROUTER_API_KEY` is set, else the original local vLLM server; `get_vllm_llm` alias (imported by `cq_generator.py`); optional `LLM_MAX_TOKENS`, `LLM_TIMEOUT`, `LLM_EXTRA_BODY`. |
| `rag_eval/configurations.py` | +5 / &minus;3 | Same environment overrides for the DSPy model. |
| `agents/ontology_generator.py`, `agents/ontology_fixer.py` | +2 / &minus;2, +1 / &minus;1 | Endpoint and key from the environment instead of hard-coded `localhost`. |

Upstream's tracked `__pycache__` files were removed; nothing else of the original was deleted.

### Paper defaults vs `--profile ours`

`agents/run_faithful.py` runs the paper's method with the paper's settings by default. `--profile ours` is the set of settings that
produced the results in `ontology/lite/`; explicit flags override either profile.

| Setting | Default (paper) | `--profile ours` |
|---|---|---|
| Arm | `baseline` (method as published) | `reference` (seed + brief + source clauses) |
| QA | LLM architectural review + bug fixer (limit 1000) | `--mode lite`: deterministic checks, 2 QA rounds, 3 repair rounds, then restore the file and drop the page |
| Pages | every page | the important pages only (reference-concept score &ge; 25), pages under 25 words skipped |
| Output cap | 4096 tokens | 16000 (4096 truncated the Turtle tool call), QA reviewer 4096 |
| Tool-loop cap | none | 40 messages |
| Prompt wording | `--domain insurance` | `--domain wba` |
| CQs evaluated | all | 60 per document (+ held-out reference CQs) |
| LLM | `Qwen3-30B-A3B` on local vLLM | `deepseek/deepseek-v4-flash` on OpenRouter, reasoning off |

More detail, including the six bugs of the original that had to be fixed to run it, is in [REPRODUCTION.md](REPRODUCTION.md).

## Setup

1. Python 3.10+ (tested with 3.13) and `pip install -r requirements.txt`.
2. Java 17+ on `PATH` (the HermiT gate), or a portable JRE unpacked to `.jre/` (the runner adds it to `PATH`).
3. `cp .env.example .env` and set the model / endpoint (local vLLM as in the paper, or `OPENROUTER_API_KEY`).
4. The WBA PDFs are **not** stored here. Put the NBN documents into `wba_data/` (file names as in `cqs/wba/*.json`, e.g.
   `sfaa-wba-nbn-ethernet-service-levels-schedule-20260701.pdf.coredownload.pdf`) or point `--pdf-dir` at them.

## Run

```bash
# the paper's method with the paper's settings (check the plan first: --dry-run)
python agents/run_faithful.py --docs wba-nbn-ethernet-service-levels-schedule-20260701 --model <model> --dry-run

# the settings behind our results (reference arm + lite checks)
python agents/run_faithful.py --profile ours --model deepseek/deepseek-v4-flash \
    --extra-body '{"reasoning":{"enabled":false}}' --max-cost-usd 9 \
    --docs wba-nbn-ethernet-price-list-20260701 wba-nbn-ethernet-product-description-20260901 \
           wba-nbn-ethernet-service-levels-schedule-20260701 wba-nbn-ethernet-product-technical-specification-20260901

# the original scripts still work as before (see README_upstream.md); the evaluations can be run on any ontology
python agents/eval.py <cqs.json> <ontology.ttl>                    # SPARQL + judge
python agents/vector_eval.py --ontology-file <ontology.ttl> --cq-file <cqs.json> --output-file <out.json>   # RAG

# one merged ontology and the offline viewer
python agents/merge_documents.py ontology/lite/reference/*__reference.ttl --out merged.ttl
python viewer/build_viz.py merged.ttl ontology/reports/reference_terms.json viewer.html
```

## Results of the run in this repository

Four documents, 51 chosen pages, 46 of them made it into the ontologies (Technical Specification pages 32, 41, 51 and Price List
page 8 failed; Product Description page 9 added nothing). Reference given to the agents: 24 classes, 73 properties, 30 individuals
(617 triples).

| | New classes | New properties | Facts | SPARQL CQ coverage | RAG CQ coverage |
|---|---|---|---|---|---|
| Technical Specification | 179 | 349 | 597 | 0.702 | 0.608 |
| Product Description | 65 | 150 | 257 | 0.858 | 0.592 |
| Price List | 76 | 114 | 332 | 0.820 | 0.342 |
| Service Levels | 47 | 72 | 80 | 0.899 | 0.733 |
| **Merged** | **351** | **651** | **1,239** | | |

The merged ontology has 14,770 triples; 891 of the 1,239 facts (72%) carry a source clause. CQ coverage = average CQ score of 60
sampled questions per document (the paper's SPARQL + judge test, empty query results counted as 0, and its RAG test).

**Read these numbers with care**
- The CQs and their expected answers are generated automatically from the same pages the ontology is built from, and the
  judge is an LLM; nothing was reviewed by hand. The numbers are not a blind test and are not comparable with the paper's.
- Only our method was run; the paper's direct baseline was not, so nothing here shows that the additions help.
- Most lost score comes from pages that failed (Technical Specification questions on failed pages score 0.09; on pages that made it in, 0.94).
- The Price List file was written in a different namespace from the other three; the source-clause check then removed most of its
  `:sourceClause` values (about 20% of its facts keep one). `agents/merge_documents.py` normalises the namespace.
- 32 pairs of near-duplicate classes (e.g. `Cl_AVC` next to `AVC`); only 39 of the 351 new classes sit under a reference class.

## Layout

```
agents/       original agents + run_faithful.py, merge_documents.py, make_reference_cqs.py, reference_report.py
helper/       connections.py (original, extended) + reference, grounding, domain_profiles, snapshot, usage
tools/  models/  rag_eval/  original (see the table above for what changed)
reference/    the reference ontology (dart_core.ttl)
cqs/          generated/ (original) + wba/ + reference/ (ours)
ontology/     original sample ontologies + lite/reference/ (results of our run) + RESULTS_MANIFEST.json
viewer/       offline ontology viewer
Presentation/ deck, figures, and the scripts that build them (see Presentation/README.md)
```

Verify that the results are untouched:

```bash
python -c "import hashlib,json; m=json.load(open('ontology/RESULTS_MANIFEST.json')); bad=[f for f,h in m.items() if hashlib.sha256(open(f,'rb').read()).hexdigest()!=h]; print('changed:', bad or 'none')"
```

## Credit and licence

The pipeline, the evaluation code and the sample data are by the authors of the paper (see the repository linked above); the upstream
repository has no licence file, so check with them before reusing their code. The NBN documents are not redistributed here.
