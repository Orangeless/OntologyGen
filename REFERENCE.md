# Reference-guided generation

Give the agents an existing ontology (`reference/dart_core.ttl`, from the DART project) as the starting point and let the documents
extend it. Facts from the contract are stored with their source clause; anything the reference lacks is added as a tagged
**extension** and can be listed separately.

## How the reference reaches the agents (`helper/reference.py`)

| Where | What |
|---|---|
| The `.ttl` file | Seeded **once**, before page 1, with the reference schema (classes, properties, enumerations; WBA names as `skos:altLabel`) between a `REFERENCE SEED` and an `EXTENSIONS FROM DOCUMENTS` marker. An existing file is never overwritten. |
| Domain Expert, Manager | A short vocabulary summary (class, "also called" names, properties) + the policy below. |
| Coder, QA, QA-Coder | The policy below, plus one sentence about their role. |

The policy: **reuse** reference terms (same IRI; match by name or alias) &middot; **extend** with new classes/properties under the closest
reference class, each with an `rdfs:comment` ending `[extension: <document>, <clause or page>]` &middot; record every **fact** (limits, rates,
charges, durations, conditions) as triples with a `:sourceClause` &middot; **never delete or weaken** reference axioms (a conflicting value is kept as its
own fact marked `[conflicts with reference: ...]`) &middot; only instantiate what the document talks about.

In the code this is `Reference.prompt_block(role)`, called from five places in `agents/multi_agent.py` (`_ref_block("expert" | "manager" |
"coder" | "qa" | "qa_coder")`) and `Reference.seed_turtle()` for the seed. Without `--reference` nothing changes.

`--reference-scope`: `core` (default; the WBA-named entities and what they link to: 24 classes, 73 properties, 30 individuals, 617
triples) &middot; `wba` (the whole wholesale layer) &middot; `all` (also the DART-NPO classes). Larger scopes cost more context on every call.

## Run

```bash
# the whole pipeline for several documents, with the reference (see README.md for the full command)
python agents/run_faithful.py --profile ours --model <model> --docs <documents>

# one document by hand
python agents/multi_agent.py --contract wba_data/<doc>.pdf --cqs cqs/wba/<doc>.json --ontology-out ontology/x.ttl \
    --domain wba --reference reference/dart_core.ttl --reference-scope core --mode lite

# held-out CQs from the reference project's confirmed clauses (needs PyMuPDF and PyYAML); the 8 files in cqs/reference/ were made this way
python agents/make_reference_cqs.py --links <path to rule_trace/register/links.yaml>

# what matched the reference, what the documents added, which facts have sources
python agents/reference_report.py --ontology ontology/lite/reference/<doc>__reference.ttl --cqs cqs/reference
```

## Outputs of `reference_report.py`

* `<name>_report.md`: reference coverage per class, integrity of the seed (lost / edited reference triples), duplicates of reference
  terms under another name, extensions grouped by source document, facts with their sources, and a check that the numbers expected by
  the reference CQs appear in the ontology.
* `<name>_extensions.ttl`: only the new classes and properties. `<name>_facts.ttl`: only the individuals (contract facts).

## Known limits

* The whole ontology file is pasted into the Manager / Coder / QA prompts (as in the paper), so it grows with every page.
* Only 8 held-out reference CQs exist, and some of their answers are visible in the seed or the brief, so they are not fully blind.
* `eval.py` and the CQ-generation prompt of the default domain are insurance-specific; `--domain wba` changes the wording only.
