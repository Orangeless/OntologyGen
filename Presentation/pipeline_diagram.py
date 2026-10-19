"""Our pipeline drawn on the paper's Figure 2 layout (the baseline of Figure 1 is a subset of it).

One spec, two renderers: build_deck.py draws it as editable PowerPoint shapes, `python pipeline_diagram.py` writes an SVG
(+ PNG through headless Chrome) for use outside the deck. Coordinates are in inches on a 10 x 5.625 slide.

Colour code
  paper colours (yellow / green / blue / red / purple) = components of the paper's figures
  orange                                              = added by us
  dashed grey                                         = in the paper but replaced / not run by us
"""
from pathlib import Path

W, H = 10.0, 5.625
ORANGE = "#ED561B"

# kind -> (fill, line, text colour, dashed)
STYLE = {
    "input":    ("#FFFFFF", "#8A94A0", "#222222", True),
    "yellow":   ("#FFF3C4", "#E3B94F", "#222222", False),     # CQ generator (paper)
    "green":    ("#E3F5D5", "#7FBF5A", "#222222", False),     # agents (paper)
    "blue":     ("#DCEBFA", "#5B9BD5", "#222222", False),     # checks / evaluation (paper)
    "red":      ("#FAD9D9", "#D9534F", "#222222", False),     # bug fixer (paper)
    "purple":   ("#E6E3FA", "#8E7CC3", "#222222", False),     # file tools / ontology file (paper)
    "ours":     ("#FDE6DC", ORANGE, "#222222", False),        # added by us
    "replaced": ("#F3F3F3", "#9AA5B1", "#7A8591", True),      # in the paper, replaced / not run by us
}
GROUPS = [  # (x, y, w, h, fill, line)
    (0.25, 1.98, 5.95, 1.02, "#F1FAEA", "#7FBF5A"),     # the paper's agent group (Domain Expert -> Manager -> Coder)
    (0.25, 3.30, 5.95, 0.82, "#EEF5FC", "#5B9BD5"),     # the paper's QA gates
    (6.45, 1.88, 3.30, 1.32, "#F1EFFB", "#8E7CC3"),     # file tools + ontology file
]
# id, x, y, w, h, kind, lines (first line bold), badge
BOXES = [
    ("in",     0.25, 1.00, 1.40, 0.66, "input",    ["WBA PDFs", "4 documents", "188 pages"], None),
    ("sel",    1.90, 1.00, 1.65, 0.66, "ours",     ["Page selector", "scores pages by reference", "terms: keeps 51 of 188"], "NEW"),
    ("cq",     3.80, 1.00, 1.55, 0.66, "yellow",   ["CQ Generator", "questions + answers", "for every page"], None),
    ("ref",    6.45, 1.00, 3.30, 0.66, "ours",     ["Reference ontology (Farzad's)", "24 classes · 73 properties · 30 values", "(1) seeds the file    (2) briefs every agent"], "NEW"),
    ("expert", 0.40, 2.10, 1.75, 0.60, "green",    ["Domain Expert", "→ SRD", "paper: multi-agent only"], None),
    ("manager", 2.30, 2.10, 1.75, 0.60, "green",   ["Manager", "→ TIP with design patterns", "paper: multi-agent only"], None),
    ("coder",  4.20, 2.10, 1.85, 0.60, "green",    ["Coder", "writes Turtle with file tools", "(also in the baseline)"], None),
    ("brief",  0.40, 2.76, 5.65, 0.20, "ours",     ["(2)  + reference brief: rules + term list, added to every agent's prompt"], "NEW"),
    ("seed",   6.55, 2.12, 3.10, 0.30, "ours",     ["(1) REFERENCE SEED: Farzad's terms, never deleted"], None),
    ("ext",    6.55, 2.46, 3.10, 0.30, "ours",     ["EXTENSIONS: new terms tagged [extension: doc, page]"], None),
    ("facts",  6.55, 2.80, 3.10, 0.30, "ours",     ["FACTS: every number with :sourceClause"], "NEW"),
    ("qa",     0.40, 3.40, 1.40, 0.62, "replaced", ["QA Agent (LLM)", "replaced by lite checks", "paper: multi-agent only"], None),
    ("lite",   1.95, 3.40, 2.00, 0.62, "ours",     ["Lite checks (no LLM)", "numbers on the page · duplicates", "source clause · CQ answered · cycles"], "NEW"),
    ("syn",    4.10, 3.40, 0.95, 0.62, "blue",     ["RDFLib", "syntax check"], None),
    ("her",    5.15, 3.40, 0.95, 0.62, "blue",     ["HermiT", "logic check"], None),
    ("bug",    6.45, 3.40, 1.40, 0.72, "red",      ["Bug Fixer", "fixes syntax /", "logic errors"], None),
    ("safe",   8.05, 3.40, 1.70, 0.72, "ours",     ["Safeguards", "max 3 repair rounds, then", "restore file + drop page;", "delete values not on the page"], "NEW"),
    ("ev",     0.25, 4.30, 2.95, 0.78, "blue",     ["Evaluation (paper's Figure 3)", "LLM writes SPARQL per CQ → runs it", "→ judge scores 0 to 1"], None),
    ("ev2",    3.35, 4.30, 2.85, 0.78, "ours",     ["Our evaluation additions", "score split by whether the CQ's page", "is in the ontology; held-out CQs"], "NEW"),
    ("out",    6.45, 4.30, 3.30, 0.78, "ours",     ["Outputs", "one ontology per document → merged", "→ offline viewer (PNG / SVG export)", "tokens + cost logged per page"], "NEW"),
    ("notrun", 0.25, 5.17, 9.50, 0.28, "replaced", ["In the paper, not run by us:  RAG evaluation · panel of 5 LLM judges for design quality · direct-baseline comparison · 1,000-cycle bug-fixer limit (ours: 3 rounds)"], None),
]
# x1, y1, x2, y2, colour, dashed
ARROWS = [
    (1.65, 1.33, 1.90, 1.33, "#555555", False), (3.55, 1.33, 3.80, 1.33, "#555555", False),
    (2.72, 1.66, 2.72, 1.98, "#555555", False), (4.57, 1.66, 4.57, 1.98, "#555555", False),
    (2.15, 2.40, 2.30, 2.40, "#555555", False), (4.05, 2.40, 4.20, 2.40, "#555555", False),
    (6.05, 2.40, 6.45, 2.40, "#555555", False),
    (8.10, 1.66, 8.10, 1.88, ORANGE, False),
    (1.10, 3.00, 1.10, 3.40, "#555555", False),
    (1.80, 3.71, 1.95, 3.71, "#9AA5B1", True),
    (3.95, 3.71, 4.10, 3.71, "#555555", False), (5.05, 3.71, 5.15, 3.71, "#555555", False),
    (6.10, 3.71, 6.45, 3.71, "#555555", False),
    (2.95, 3.40, 2.95, 3.00, ORANGE, False),
    (7.15, 3.40, 7.15, 3.20, "#D9534F", False),
    (7.85, 3.76, 8.05, 3.76, ORANGE, False),
    (1.70, 4.12, 1.70, 4.30, "#555555", False),
    (3.20, 4.69, 3.35, 4.69, "#555555", False),
    (7.60, 4.12, 7.60, 4.30, "#555555", False),
]
# x, y, w, text, colour, size(pt), bold, align   (small free labels)
LABELS = [
    (3.05, 3.05, 3.1, "feedback to the Coder · max 2 rounds", ORANGE, 7.5, True, "left"),
    (6.55, 1.90, 3.1, "Ontology file · LLM file tools", "#5B4A9E", 8, True, "left"),
]
LEGEND_Y = 0.74
LEGEND = [  # x, kind, text
    (0.25, "paper", "In the paper (its own colours)"),
    (2.75, "tag", "“paper: multi-agent only” = in its Figure 2, not in the baseline"),
    (6.80, "ours", "Added by us"),
    (8.05, "replaced", "Replaced / not run"),
]
TITLE = "Our pipeline on the paper's figure: what we added"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg():
    k = 100.0           # 1 inch = 100 svg units
    pt = k / 72.0       # 1 pt in svg units
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W * k:.0f} {H * k:.0f}" width="{W * k:.0f}" height="{H * k:.0f}" font-family="Arial, Helvetica, sans-serif">',
         '<defs>' + "".join(f'<marker id="a{i}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
                            for i, c in enumerate(["#555555", ORANGE, "#D9534F", "#9AA5B1"])) + '</defs>',
         f'<rect width="{W * k:.0f}" height="{H * k:.0f}" fill="#ffffff"/>',
         f'<text x="{0.25 * k}" y="{0.55 * k}" font-size="{22 * pt:.1f}" fill="#222">{_esc(TITLE)}</text>']
    for x, y, w, h, fill, line in GROUPS:
        o.append(f'<rect x="{x * k}" y="{y * k}" width="{w * k}" height="{h * k}" rx="8" fill="{fill}" stroke="{line}" stroke-width="1.2"/>')
    for bid, x, y, w, h, kind, lines, badge in BOXES:
        fill, line, tc, dashed = STYLE[kind]
        sw = 2.2 if kind == "ours" else 1.2
        o.append(f'<rect x="{x * k}" y="{y * k}" width="{w * k}" height="{h * k}" rx="6" fill="{fill}" stroke="{line}" stroke-width="{sw}"' + (' stroke-dasharray="5,3"' if dashed else '') + '/>')
        n = len(lines)
        big = 8.5 if (h > 0.25 and n > 1) else 7.5
        lh = 0.125 if h > 0.25 else 0.15
        y0 = y + h / 2 - (n - 1) * lh / 2 + 0.035
        for i, ln in enumerate(lines):
            bold = i == 0
            size = big if bold else 7.5
            fill_t = tc if (bold or kind != "replaced") else tc
            if i == n - 1 and n > 1 and "paper:" in ln:
                size, fill_t = 7, "#6B7785"
            o.append(f'<text x="{(x + w / 2) * k:.1f}" y="{(y0 + i * lh) * k:.1f}" font-size="{size * pt:.1f}" font-weight="{"bold" if bold else "normal"}" text-anchor="middle" fill="{fill_t}"'
                     + (' text-decoration="line-through"' if False else '') + f'>{_esc(ln)}</text>')
        if badge:
            o.append(f'<rect x="{(x + w - 0.38) * k:.1f}" y="{(y - 0.075) * k:.1f}" width="{0.34 * k:.1f}" height="{0.15 * k:.1f}" rx="{0.07 * k:.1f}" fill="{ORANGE}"/>'
                     f'<text x="{(x + w - 0.21) * k:.1f}" y="{(y + 0.03) * k:.1f}" font-size="{6.5 * pt:.1f}" font-weight="bold" fill="#fff" text-anchor="middle">{badge}</text>')
    cmap = {"#555555": 0, ORANGE: 1, "#D9534F": 2, "#9AA5B1": 3}
    for x1, y1, x2, y2, c, dashed in ARROWS:
        o.append(f'<line x1="{x1 * k}" y1="{y1 * k}" x2="{x2 * k}" y2="{y2 * k}" stroke="{c}" stroke-width="{1.6 * pt * 1.2:.1f}"' + (' stroke-dasharray="4,3"' if dashed else '') + f' marker-end="url(#a{cmap[c]})"/>')
    for x, y, w, t, c, size, bold, align in LABELS:
        o.append(f'<text x="{x * k}" y="{(y + 0.1) * k}" font-size="{size * pt:.1f}" font-weight="{"bold" if bold else "normal"}" fill="{c}">{_esc(t)}</text>')
    for x, kind, text in LEGEND:
        y = LEGEND_Y
        if kind == "paper":
            for i, c in enumerate(["#FFF3C4", "#E3F5D5", "#DCEBFA", "#FAD9D9", "#E6E3FA"]):
                o.append(f'<rect x="{(x + i * 0.17) * k}" y="{(y + 0.02) * k}" width="{0.15 * k}" height="{0.13 * k}" fill="{c}" stroke="#999" stroke-width="0.8"/>')
            tx = x + 0.92
        else:
            fill, line, _, dashed = STYLE["ours" if kind == "ours" else "replaced" if kind == "replaced" else "green"]
            if kind == "tag":
                fill, line, dashed = "#E3F5D5", "#7FBF5A", False
            o.append(f'<rect x="{x * k}" y="{(y + 0.02) * k}" width="{0.26 * k}" height="{0.13 * k}" fill="{fill}" stroke="{line}" stroke-width="{2 if kind == "ours" else 1}"' + (' stroke-dasharray="4,2"' if dashed else '') + '/>')
            tx = x + 0.33
        o.append(f'<text x="{tx * k}" y="{(y + 0.13) * k}" font-size="{8 * pt:.1f}" fill="#333">{_esc(text)}</text>')
    o.append('</svg>')
    return "\n".join(o)


if __name__ == "__main__":
    import subprocess
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "assets"
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "pipeline_comparison.svg").write_text(svg(), encoding="utf-8")
    html = out / "pipeline_comparison.html"
    html.write_text(f'<!doctype html><meta charset="utf-8"><body style="margin:0">{svg().replace("width=" + chr(34) + "1000" + chr(34), "width=" + chr(34) + "2000" + chr(34), 1).replace("height=" + chr(34) + "562" + chr(34), "height=" + chr(34) + "1125" + chr(34), 1)}</body>', encoding="utf-8")
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=2000,1125", f"--screenshot={out / 'pipeline_comparison.png'}", "file:///" + str(html).replace("\\", "/")], capture_output=True)
    html.unlink()
    print("wrote", out / "pipeline_comparison.svg", out / "pipeline_comparison.png")
