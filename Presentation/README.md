# Presentation

The deck and its figures. Everything is built from the files of this repository, so the numbers cannot drift from the results.

* `automated ontology generation so far.pptx`: the template (never modified). `automated ontology generation - filled.pptx`: the filled deck
  (20 slides, speaker notes on every slide).
* `ontologygenautomatic.pdf`: the paper.
* `build_deck.py` builds the deck. Numbers come from `ontology/lite/reference/` (ontologies, logs, evaluation reports), `cqs/` and `data/rag/`;
  the code snippets on the slides are cut out of the source files by line number.
* `final_numbers.py`: the final numbers of the run (pages in / failed / empty, tokens per page from the `[usage]` log lines, the SPARQL + judge
  evaluation split by whether a CQ's page is in the ontology).
* `data/rag/`: the paper's RAG evaluation (`agents/vector_eval.py`) run on the existing ontologies, same 60 CQs per document.
* `annotate_paper_figure.py`: the paper's Figure 2 with five numbered additions. `pipeline_diagram.py`: a more detailed drawing of the pipeline.
* `assets/`: viewer screenshots, figures, and `viewer_merged.html` (the four ontologies merged). `share/`: the offline viewer + the merged ontology.

Rebuild from the repository root (needs the packages of `requirements.txt` and the WBA PDFs in `wba_data/` or `$WBA_PDF_DIR`):

    python Presentation/build_deck.py
    python Presentation/annotate_paper_figure.py
    python Presentation/pipeline_diagram.py
