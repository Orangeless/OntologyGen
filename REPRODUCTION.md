# Reproducing the method on WBA documents: what is faithful, what is not

Run: `python agents/run_faithful.py ...` (see its docstring and README.md). Everything below is traceable to code in this repository.

## Pipeline (as in the paper / README)
1. **CQ generator** (`agents/cq_generator.py`) - ODP-driven competency questions per page (four patterns: event reification &
   participation, quantities, time instants/intervals, situations & states).
2. **Domain expert -> Manager -> Coder** (`agents/multi_agent.py`) - SRD, then a technical implementation plan naming ODPs, then
   incremental edits of a Turtle file with file tools; one ontology per contract, built page by page with the CQs as requirements.
3. **QA review + bug fixer** - three gates in a loop: architectural review, RDF syntax (rdflib), OWL consistency (HermiT).
4. **Evaluation** (`agents/eval.py`) - each CQ is turned into SPARQL by an LLM, executed on the ontology, and the answer is judged.

## Defaults are the paper's settings; `--profile ours` is how our results were made
`python agents/run_faithful.py` now runs the paper's method with the paper's settings by default: the published LLM QA loop on every page
(20 QA cycles, 200-line reads, bug-fixer limit 1000), 4096-token outputs, no tool-loop cap, the paper's insurance prompt wording, every
CQ evaluated, the whole ontology in every prompt. Our extensions are opt-in:

| Setting | Default (paper) | `--profile ours` (what produced `ontology/lite/`) |
|---|---|---|
| Arm | `baseline` (method as published) | `reference` (+ reference ontology seed, brief, source clauses) |
| QA | LLM architectural review + bug fixer, 1000-round limit | `--mode lite`: deterministic checks, 2 QA rounds, 3 repair rounds, then restore + drop page |
| Pages | every page | the important pages only (`--page-threshold 25`), pages under 25 words skipped |
| Output cap | 4096 tokens | 16000 (4096 truncated the Turtle tool call), QA reviewer 4096 |
| Tool-loop cap | none | 40 messages |
| Prompt wording | `--domain insurance` | `--domain wba` |
| CQs evaluated | all | 60 per document (+ held-out reference CQs) |

```
python agents/run_faithful.py --profile ours --model deepseek/deepseek-v4-flash --extra-body '{"reasoning":{"enabled":false}}'     --docs <the four documents> --max-cost-usd 9          # reproduces the command behind ontology/lite/
```
Explicit flags still override either profile. `--profile ours --dry-run` lists the same 51 pages as the logs (Technical Specification
5-6,10,24-33,38-44,50-52,64,79; Product Description 6-10,13,15,18-19,21,26; Price List 5-11,18-20; Service Levels 13,15,33-35).

**The results were not changed by this.** `ontology/RESULTS_MANIFEST.json` holds the SHA-256 of every result file (ontologies, logs,
evaluation reports, CQ files) taken before the defaults were changed; compare to verify. Only argument defaults changed
(`agents/run_faithful.py`) plus one default in `agents/multi_agent.py` (the QA reviewer uses the coder's output cap unless
`QA_MAX_TOKENS` is set).

## Settings of the paper-mode run (the defaults above)
| Item | Paper / repo | Faithful run |
|---|---|---|
| Ontology per document | yes (single-contract mode) | yes, documents in parallel |
| What agents see | the whole ontology file (line-numbered JSON) | same (`--snapshot-max-lines 0`) |
| QA cycles | 20 | 20 |
| QA-coder read window | 200 lines | 200 |
| Gates | architectural QA, rdflib syntax, HermiT consistency | same (needs Java, see below) |
| CQs | generated per page | generated per page (WBA wording) |
| Evaluation | `eval.py` SPARQL-based, LLM judge | same |

## Deviations (and why)
| Deviation | Reason |
|---|---|
| LLM: DeepSeek V4 Flash on OpenRouter, reasoning off (paper: Qwen3-30B-A3B-Instruct on local vLLM) | No local GPU. Qwen3-30B via OpenRouter never produced a tool call (the coder wrote nothing); Gemini 2.5 Flash-Lite looped on reads; reasoning models spent the whole output budget thinking. |
| Output cap 16000 tokens (paper: 4096) | A dense page needs more than 4096 tokens of Turtle in the single `append_to_file` call the coder prompt demands; the call was truncated and the page added nothing. |
| Safety cap on a tool loop's message history (40) and 120 s request timeout | Uncapped read/edit loops re-send the whole history on each call (1.4M tokens for two pages in one test). |
| Pages with fewer than 25 words are skipped | Blank / separator pages. |
| A page that fails is isolated; an invalid-Turtle failure restores the previous ontology; pages that add no triples are reported | The original reports "processed successfully" even when nothing was written. |
| WBA wording in the prompts (`helper/domain_profiles.py`, `--domain wba`; CQ prompt `--domain wba`) | The published prompts are written for life-insurance contracts. Insurance wording is the default and is unchanged. |
| CQ generator stores only `competency_question` and `expected_answer`; retries malformed JSON; WBA prompt adds "never invent, empty list for pages without facts" | Matches the published CQ files; boilerplate pages have no answerable facts. |
| Portable Java 17 in `.jre/` | The HermiT gate needs Java. |

## Bugs in the published repository that had to be fixed to run it at all
1. `models/competency_questions.py` is imported but not shipped - reconstructed from the prompt and the published CQ files.
2. `agents/cq_generator.py`: `langchain_core.schema` does not exist (`langchain_core.messages`); `helper.connections.get_vllm_llm` did not exist.
3. `agents/multi_agent.py` uses a `delete_lines_from_file` tool that is not defined anywhere - added to `tools/file_management.py`.
4. `agents/multi_agent.py`: undefined variable `review_type` in the QA failure path (NameError).
5. `tools/syntax_checks.py`: the consistency check loads the ontology into a new Owlready2 `World` but runs HermiT on the default (empty) world, so it **never detected any inconsistency**. Fixed to reason over the right world (verified: unsatisfiable classes and disjointness violations are now reported); an inconsistent ontology now returns an actionable message.
6. File tools opened files without an encoding (cp1252 on Windows) while the rest reads UTF-8 - made explicit.

## Extensions beyond the paper (separate arm)
`--arm reference` seeds each ontology with the project's reference ontology (`reference/dart_core.ttl`) and adds the reuse / extension /
source-clause policy (`helper/reference.py`, `REFERENCE.md`). The `baseline` arm is the method as published (with the fixes above).

## Not the faithful method (removed)
An earlier exploratory batch runner (12 parallel page-chunks, compact ontology view, 2 QA cycles; Dictionary and Head Terms documents)
caused duplicated upper classes and was not the paper's method. It and its results were removed from the repository.
