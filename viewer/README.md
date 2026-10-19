# Ontology viewer

Offline, self-contained viewer for the generated WBA ontology (no server, no network: d3 is embedded).

```
python agents/merge_documents.py ontology/lite/reference/*__reference.ttl --out merged.ttl   # one ontology of the four documents
python viewer/build_viz.py merged.ttl [reference_terms.json] [out.html]
```
Then open the HTML file by double-click (default output `viewer/ontology_viewer.html`). Rebuild after every ontology change.

* **Overview** - totals, extension classes per source document (click a bar to filter everything), the reference classes, and a *data quality* panel (near-duplicate names, flat classes, classes without instances, undescribed classes, facts without `:sourceClause`, invalid dates) - click a row to list the offenders.
* **Class tree** - `rdfs:subClassOf` hierarchy; blue dot = reference, orange = added from the documents. Reference classes first.
* **Graph** - neighbourhood of the selected class (depth 1-3, hubs are not expanded, capped at 120 nodes, "Show more") or the reference core. Export SVG/PNG.
  The export is a print-style picture, independent of the light/dark theme: white background, dark text with a halo, the whole graph (not just the current zoom), FULL node names (nothing truncated or hidden), a large legend, crowded layouts spread out and labels placed so they do not overlap; the PNG is at least 2400 px wide (up to 8000 px for a 120-node graph - use depth 1-2 for slide-sized pictures). File name: `ontology-graph-<class>.png`.
* **Properties**, **Facts** - sortable/filterable tables; facts export to CSV.
* Search supports `kind:class|property|fact` and `doc:"Head Terms"`. Document names are normalised ("WBA Dictionary" = "Wholesale Broadband Agreement - Dictionary").
* Deep links: `ontology_viewer.html#tab=graph&sel=c:NNIGroup`.

"Reference" = terms listed in `ontology/reports/reference_terms.json`; everything else was added by the pipeline.
