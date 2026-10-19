"""Fill the presentation template ("automated ontology generation so far.pptx") and write a new .pptx next to it.

  python Presentation/build_deck.py       (from the repository root; needs python-pptx, pillow, pymupdf and the WBA PDFs in wba_data/ or $WBA_PDF_DIR)

The template file is never modified. Numbers come from the real files (ontology .ttl, run logs, CQ files, eval reports),
code snippets are cut out of the real source files by line number, so they cannot drift from the code.
"""
import glob
import json
import logging
import os
import re
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)
from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PDF_DIR = Path(os.environ.get("WBA_PDF_DIR", REPO / "wba_data"))   # the WBA PDFs are not stored in the repository
TEMPLATE = HERE / "automated ontology generation so far.pptx"
OUT = HERE / "automated ontology generation - filled.pptx"
DATA = HERE / "data"
ASSETS = HERE / "assets"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
from final_numbers import compute as final_compute   # noqa: E402  (final numbers of the run, from the real files)

BLUE, GREEN, ORANGE = RGBColor(0x05, 0x8D, 0xC7), RGBColor(0x50, 0xB4, 0x32), RGBColor(0xED, 0x56, 0x1B)
DARK, GREY, LIGHT, MID = RGBColor(0x22, 0x22, 0x22), RGBColor(0x66, 0x66, 0x66), RGBColor(0xF3, 0xF3, 0xF3), RGBColor(0xD9, 0xDE, 0xE3)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLUE_L, GREEN_L, ORANGE_L = RGBColor(0xE1, 0xF2, 0xFA), RGBColor(0xE6, 0xF5, 0xDF), RGBColor(0xFD, 0xE6, 0xDC)
CODE_BG, CODE_FG, CODE_NUM, CODE_CMT, CODE_STR, CODE_KEY = (RGBColor(0x1E, 0x1E, 0x1E), RGBColor(0xD4, 0xD4, 0xD4), RGBColor(0x85, 0x85, 0x85),
                                                              RGBColor(0x6A, 0x99, 0x55), RGBColor(0xCE, 0x91, 0x78), RGBColor(0x56, 0x9C, 0xD6))
FONT, MONO = "Arial", "Consolas"


# ====================================================================== facts from the real files
def run_facts():
    """Everything the slides quote, read from the files at build time."""
    F = {}
    docs = {"Technical Specification": "technical-specification", "Product Description": "product-description",
            "Price List": "price-list", "Service Levels": "service-levels"}
    F["docs"] = docs
    # --- pages that really went into each ontology (last run of each log; pages that added 0 triples are not counted)
    F["pages"], F["failed"], F["empty"], F["total_pages"], F["selected"] = {}, {}, {}, {}, {}
    import fitz
    for name, key in docs.items():
        log = Path(glob.glob(str(REPO / f"ontology/lite/reference/logs/*{key}*coredownload.log"))[0]).read_text(encoding="utf-8", errors="ignore")
        first = int(re.search(r"--- Starting Page (\d+) of", log).group(1))
        tail = log[log.rfind(f"--- Starting Page {first} of"):]
        added = {int(p): int(t) for p, t in re.findall(r"\[added\] page=(\d+) triples=(\d+)", tail)}
        F["pages"][name] = sorted(p for p, t in added.items() if t > 0)
        F["empty"][name] = sorted(p for p, t in added.items() if t == 0 and f"Page {p} FAILED" not in tail)
        F["failed"][name] = sorted(int(x) for x in re.findall(r"Page (\d+) FAILED", tail))
        m = re.search(r"--page-list ([\d,\-]+)", log)
        F["selected"][name] = m.group(1)
        stem = Path(glob.glob(str(REPO / f"ontology/lite/reference/*{key}*__reference.ttl"))[0]).name.replace("__reference.ttl", "")
        F["total_pages"][name] = len(fitz.open(str(PDF_DIR / (stem + ".pdf"))))
        F["pages"][name] = [p for p in F["pages"][name] if p not in F["failed"][name]]
        F["failed"][name] = [p for p in F["failed"][name] if p not in F["pages"][name]]
    # --- CQs (CQ files use 0-based page keys, the pipeline 1-based pages)
    F["cq"] = {}
    for name, key in docs.items():
        d = next(iter(json.load(open(glob.glob(str(REPO / f"cqs/wba/*{key}*.json"))[0], encoding="utf-8")).values()))
        allq = [(int(p) + 1, q) for p, l in d.items() for q in l]
        used = [q for p, q in allq if p in F["pages"][name]]
        F["cq"][name] = dict(total=len(allq), used=used, numeric=sum(1 for q in used if re.search(r"\d", str(q["expected_answer"]))))
    # --- ontology numbers on a namespace-normalised union (the Price List file was written in another namespace, see slide 'Known problems')
    import rdflib
    from rdflib import OWL, RDF, URIRef
    from helper import grounding as G
    from helper.reference import PIPE_NS, Reference
    os.chdir(REPO)   # rdflib reads "C:\…" as a URL; the repo is used with relative paths
    ref = Reference("reference/dart_core.ttl", "core")
    seed = ref.seed_graph()
    seed_terms = {s for s in seed.all_nodes() if isinstance(s, URIRef)}
    SRC = URIRef(PIPE_NS + "sourceClause")

    def facts_of(gr):
        """Facts = individuals typed with a class of the ontology (the viewer's definition), not part of the reference seed."""
        cl = {c for c in gr.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)}
        pr = {p for t_ in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty) for p in gr.subjects(RDF.type, t_)}
        return {s for s, o in gr.subject_objects(RDF.type) if isinstance(s, URIRef) and str(s).startswith(PIPE_NS) and o in cl and s not in cl and s not in pr} - seed_terms
    union = rdflib.Graph()
    F["onto"] = {}
    for name, key in docs.items():
        f = glob.glob(str(REPO / f"ontology/lite/reference/*{key}*__reference.ttl"))[0]
        txt = re.sub(r"@prefix : <[^>]*> \.", f"@prefix : <{PIPE_NS}> .", Path(f).read_text(encoding="utf-8"), count=1)
        g = rdflib.Graph(); g.parse(data=txt, format="turtle"); union += g
        cls = {c for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)} - seed_terms
        props = {p for t in (OWL.ObjectProperty, OWL.DatatypeProperty) for p in g.subjects(RDF.type, t) if isinstance(p, URIRef)} - seed_terms
        inds = facts_of(g)
        src = {i for i in inds if (i, SRC, None) in g}
        F["onto"][name] = dict(classes=len(cls), props=len(props), facts=len(inds), sourced=len(src), triples=len(g),
                               example_named=sum(1 for i in inds if re.search(r"(Example|Sample)", G.local(i))))
    cu = {c for c in union.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)} - seed_terms
    pu = {p for t_ in (OWL.ObjectProperty, OWL.DatatypeProperty) for p in union.subjects(RDF.type, t_) if isinstance(p, URIRef)} - seed_terms
    iu = facts_of(union)
    F["union"] = dict(classes=len(cu), props=len(pu), facts=len(iu), sourced=len({i for i in iu if (i, SRC, None) in union}))
    F["union_triples"] = len(union)
    F["union_classes"] = len({c for c in union.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)})
    F["dups"] = len(G.duplicates(seed, union))
    F["ref"] = dict(classes=len(ref.classes), props=len(ref.props), triples=len(seed))
    # --- values the grounding step deleted because the page did not state them
    F["stripped"] = {}
    for name, key in docs.items():
        txt = Path(glob.glob(str(REPO / f"ontology/lite/reference/logs/*{key}*coredownload.log"))[0]).read_text(encoding="utf-8", errors="ignore")
        F["stripped"][name] = sum(int(x) for x in re.findall(r"unsupported_values_removed=(\d+)", txt))
    # --- final numbers of the run: pages, per-page tokens, and the pipeline's own SPARQL + judge evaluation (60 CQs per document + held-out CQs)
    F["final"] = final_compute(REPO)
    for name in docs:   # RAG coverage, when the RAG evaluation was run on the existing ontologies
        rf = DATA / "rag" / f"rag_{re.sub(r'[^a-z]+', '_', name.lower()).strip('_')}.json"
        F["final"][name]["rag"] = None
        if rf.exists():
            try:
                F["final"][name]["rag"] = json.load(open(rf, encoding="utf-8")).get("accuracy")
            except Exception:
                pass
    F["eval"] = {}
    for name in docs:
        e = F["final"][name]["eval"]
        F["eval"][name] = dict(n=e["n"], raw=e["raw"], processed=e["processed"], perfect=e["perfect"], partial=e["partial"], zero=e["zero"],
                               n_held=e["n_held"], held_processed=e["held_processed"])
    return F


# ====================================================================== drawing helpers
def _runs(par, runs, size, color, bold, font, italic=False):
    if isinstance(runs, str):
        runs = [(runs, {})]
    for item in runs:
        text, o = (item, {}) if isinstance(item, str) else item
        r = par.add_run()
        r.text = text
        f = r.font
        f.name = o.get("font", font)
        f.size = Pt(o.get("size", size))
        f.bold = o.get("bold", bold)
        f.italic = o.get("italic", italic)
        f.color.rgb = o.get("color", color)


def tb(slide, x, y, w, h, paras, size=12, color=DARK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT,
       fill=None, line=None, shape=MSO_SHAPE.RECTANGLE, margin=0.06, space_after=0, line_spacing=None, italic=False, line_w=1.0):
    """A text box / rounded box. paras: str | [run-list | str, ...]; a run is (text, {bold,color,size,font,italic})."""
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = 0.08
    s.shadow.inherit = False
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid(); s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line; s.line.width = Pt(line_w)
    tf = s.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(margin * 0.6)
    tf.vertical_anchor = anchor
    if isinstance(paras, str):
        paras = [paras]
    for i, p in enumerate(paras):
        par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        par.alignment = align
        par.space_after = Pt(space_after)
        if line_spacing:
            par.line_spacing = line_spacing
        _runs(par, p, size, color, bold, font, italic)
    return s


def arrow(slide, x1, y1, x2, y2, color=GREY, width=1.75, head=True):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    if head:
        ln = c.line._get_or_add_ln()
        etree.SubElement(ln, qn("a:tailEnd")).set("type", "triangle")
    return c


_TOK = re.compile(r'(#.*$)|((?:f|r)?"(?:[^"\\]|\\.)*"?|(?:f|r)?\'(?:[^\'\\]|\\.)*\'?)|\b(def|if|else|elif|return|from|import|for|in|not|is|None|and|or|with|class|raise|try|except|lambda)\b')


def code_box(slide, x, y, w, h, lines, size=8.5, numbers=True, title=None):
    """lines: [(lineno | None, text)]. Dark box, Consolas, tiny highlighter (comments green, strings orange, keywords blue)."""
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    box.adjustments[0] = 0.02
    box.shadow.inherit = False
    box.fill.solid(); box.fill.fore_color.rgb = CODE_BG
    box.line.fill.background()
    tf = box.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = Inches(0.1)
    tf.margin_top = tf.margin_bottom = Inches(0.06)
    tf.vertical_anchor = MSO_ANCHOR.TOP
    first = True
    if title:
        par = tf.paragraphs[0]; first = False
        par.alignment = PP_ALIGN.LEFT
        _runs(par, [(title, {"color": RGBColor(0x9C, 0xDC, 0xFE), "bold": True})], size, CODE_FG, False, MONO)
    for no, text in lines:
        par = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        par.space_after = Pt(0)
        par.alignment = PP_ALIGN.LEFT
        label = None
        if "  ◂ " in text:
            text, label = text.split("  ◂ ", 1)
        if numbers:
            _runs(par, [((f"{no:>4}  " if no else "      "), {"color": CODE_NUM})], size, CODE_FG, False, MONO)
        pos = 0
        for m in _TOK.finditer(text):
            if m.start() > pos:
                _runs(par, text[pos:m.start()], size, CODE_FG, False, MONO)
            col = CODE_CMT if m.group(1) else CODE_STR if m.group(2) else CODE_KEY
            _runs(par, [(m.group(0), {"color": col})], size, CODE_FG, False, MONO)
            pos = m.end()
        if pos < len(text):
            _runs(par, text[pos:], size, CODE_FG, False, MONO)
        if label:
            _runs(par, [("   ◂ " + label, {"color": RGBColor(0xDC, 0xDC, 0xAA), "italic": True})], size, CODE_FG, False, MONO)
    return box


def src_lines(path, a, b, width=None, skip_blank=False):
    """Exact lines a..b (1-based, inclusive) of a repo file; optionally cut to `width` characters (marked with an ellipsis)."""
    raw = (REPO / path).read_text(encoding="utf-8").splitlines()
    out = []
    for n in range(a, b + 1):
        t = raw[n - 1].rstrip()
        if skip_blank and not t.strip():
            continue
        if width and len(t) > width:
            t = t[: width - 1] + "…"
        out.append((n, t))
    return out


def dedent(lines):
    """Remove the common left indent of a block of (no, text) lines (display only)."""
    ind = min((len(t) - len(t.lstrip()) for _, t in lines if t.strip()), default=0)
    return [(n, t[ind:]) for n, t in lines]


def set_title(slide, text, size=26, y=0.22, h=0.62):
    t = slide.shapes.title
    t.left, t.top, t.width, t.height = Inches(0.34), Inches(y), Inches(9.32), Inches(h)
    tf = t.text_frame
    tf.clear()
    r = tf.paragraphs[0].add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.name = FONT
    r.font.color.rgb = DARK
    bp = tf._txBody.find(qn("a:bodyPr"))
    for ch in list(bp):
        bp.remove(ch)
    etree.SubElement(bp, qn("a:normAutofit"))
    return t


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def new_slide(prs, title, layout=4, **kw):
    s = prs.slides.add_slide(prs.slide_layouts[layout])
    set_title(s, title, **kw)
    return s


def clear_placeholder(ph, paras, size=12, space=4, bold_first=False):
    """Fill a template body placeholder with paragraphs (keeps its position, font, bullets off)."""
    tf = ph.text_frame
    tf.clear()
    bp = tf._txBody.find(qn("a:bodyPr"))
    for ch in list(bp):
        bp.remove(ch)
    etree.SubElement(bp, qn("a:normAutofit"))
    for i, p in enumerate(paras):
        par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        par.space_before = Pt(0)
        par.space_after = Pt(space)
        pPr = par._p.get_or_add_pPr()
        pPr.set("marL", "171450"); pPr.set("indent", "-171450")
        for ch in list(pPr):
            if ch.tag in (qn("a:buNone"), qn("a:buChar"), qn("a:buSzPts"), qn("a:buFont")):
                pPr.remove(ch)
        bu = etree.SubElement(pPr, qn("a:buChar")); bu.set("char", "•")
        _runs(par, p, size, DARK, False, FONT)


def move_slide(prs, slide, new_index):
    lst = prs.slides._sldIdLst
    for el in list(lst):
        if prs.slides.get(int(el.get("id"))) is slide:
            lst.remove(el); lst.insert(new_index, el); return
    raise ValueError("slide not found")


def fmt_pages(pages):
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}–{pages[j]}")
        i = j + 1
    return ", ".join(out)


def table(slide, x, y, w, col_w, rows, size=10, header_fill=BLUE, row_h=0.3, first_col_bold=True, aligns=None):
    nr, nc = len(rows), len(rows[0])
    gt = slide.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(w), Inches(row_h * nr))
    tbl = gt.table
    tblPr = tbl._tbl.tblPr
    tblPr.set("firstRow", "0"); tblPr.set("bandRow", "0")
    sid = tblPr.find(qn("a:tableStyleId"))
    if sid is not None:
        sid.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"   # 'No Style, No Grid'
    for i, cw in enumerate(col_w):
        tbl.columns[i].width = Inches(cw)
    for r in range(nr):
        tbl.rows[r].height = Inches(row_h)
        for c in range(nc):
            cell = tbl.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = header_fill if r == 0 else (LIGHT if r % 2 == 0 else WHITE)
            tf = cell.text_frame
            tf.word_wrap = True
            val = rows[r][c]
            par = tf.paragraphs[0]
            par.alignment = (aligns[c] if aligns else PP_ALIGN.LEFT)
            _runs(par, val, size, WHITE if r == 0 else DARK, r == 0 or (c == 0 and first_col_bold), FONT)
    return tbl


def fmt_score(v):
    return "–" if v is None else f"{v:.2f}"


# ====================================================================== the deck
def _chart_base(ch, title, legend=True):
    """Title, fonts, an explicit white chart area (some viewers paint transparent charts on a dark background) and the legend."""
    ch.has_title = True
    ch.chart_title.text_frame.text = title
    r = ch.chart_title.text_frame.paragraphs[0].runs[0]
    r.font.size = Pt(11); r.font.bold = True; r.font.name = FONT; r.font.color.rgb = DARK
    ch.font.size = Pt(9); ch.font.name = FONT; ch.font.color.rgb = DARK
    cs = ch._chartSpace
    for old_sp in cs.findall(qn("c:spPr")):
        cs.remove(old_sp)
    sp = etree.SubElement(cs, qn("c:spPr"))
    fill = etree.SubElement(sp, qn("a:solidFill")); etree.SubElement(fill, qn("a:srgbClr")).set("val", "FFFFFF")
    ln = etree.SubElement(sp, qn("a:ln")); etree.SubElement(ln, qn("a:noFill"))
    cs.remove(sp); cs.find(qn("c:chart")).addnext(sp)
    ch.has_legend = legend
    if legend:
        ch.legend.position = XL_LEGEND_POSITION.BOTTOM; ch.legend.include_in_layout = False; ch.legend.font.size = Pt(9); ch.legend.font.color.rgb = DARK


def _axis_title(axis, text):
    axis.has_title = True
    axis.axis_title.text_frame.text = text
    r = axis.axis_title.text_frame.paragraphs[0].runs[0]
    r.font.size = Pt(9); r.font.bold = False; r.font.name = FONT; r.font.color.rgb = GREY


def style_chart(gf, title, legend=True, vmax=1.0, unit=0.25, fmt="0.00", stacked=False):
    ch = gf.chart
    _chart_base(ch, title, legend)
    va = ch.value_axis
    va.minimum_scale = 0.0
    if vmax is not None:
        va.maximum_scale = vmax
    if unit:
        va.major_unit = unit
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = MID
    va.tick_labels.font.size = Pt(9); va.tick_labels.font.color.rgb = DARK; va.format.line.fill.background()
    ch.category_axis.tick_labels.font.size = Pt(9); ch.category_axis.tick_labels.font.color.rgb = DARK; ch.category_axis.format.line.color.rgb = MID
    pl = ch.plots[0]
    pl.gap_width = 60
    pl.overlap = 100 if stacked else -5
    pl.has_data_labels = True
    dl = pl.data_labels
    dl.number_format = fmt; dl.number_format_is_linked = False
    dl.position = XL_LABEL_POSITION.CENTER if stacked else XL_LABEL_POSITION.OUTSIDE_END
    dl.font.size = Pt(8.5); dl.font.color.rgb = WHITE if stacked else DARK
    return ch


def bar_chart(slide, x, y, w, h, cats, series, title, stacked=False, **kw):
    """series: [(name, values, RGBColor)]; None values stay empty."""
    cd = CategoryChartData()
    cd.categories = cats
    for name, vals, _ in series:
        cd.add_series(name, vals)
    kind = XL_CHART_TYPE.COLUMN_STACKED if stacked else XL_CHART_TYPE.COLUMN_CLUSTERED
    ch = style_chart(slide.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), cd), title, stacked=stacked, **kw)
    for s_, (_, _, col) in zip(ch.plots[0].series, series):
        s_.format.fill.solid(); s_.format.fill.fore_color.rgb = col
    return ch


def scatter_chart(slide, x, y, w, h, series, title, xtitle, ytitle):
    """series: [(name, [(x, y), ...], RGBColor)]"""
    cd = XyChartData()
    for name, pts, _ in series:
        s = cd.add_series(name)
        for px, py in pts:
            s.add_data_point(px, py)
    ch = slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(x), Inches(y), Inches(w), Inches(h), cd).chart
    _chart_base(ch, title, True)
    for ax, ttl in ((ch.category_axis, xtitle), (ch.value_axis, ytitle)):
        ax.minimum_scale = 0
        ax.has_major_gridlines = True
        ax.major_gridlines.format.line.color.rgb = MID
        ax.tick_labels.font.size = Pt(9); ax.tick_labels.font.color.rgb = DARK
        ax.format.line.color.rgb = MID
        _axis_title(ax, ttl)
    for s_, (_, _, col) in zip(ch.plots[0].series, series):
        s_.marker.style = XL_MARKER_STYLE.CIRCLE; s_.marker.size = 8
        s_.marker.format.fill.solid(); s_.marker.format.fill.fore_color.rgb = col
        s_.marker.format.line.color.rgb = WHITE
        s_.format.line.fill.background()
    return ch


short_doc = {"Technical Specification": "Tech Spec", "Product Description": "Product Desc.", "Price List": "Price List", "Service Levels": "Service Levels"}


def numbers_slides(prs, F):
    """Three chart slides built from the final numbers: accuracy, size/coverage, cost."""
    import statistics
    names, fin, onto, U = list(F["docs"]), F["final"], F["onto"], F["union"]
    cats = [short_doc[n] for n in names]
    EV = {n: fin[n]["eval"] for n in names}

    # ---------- 1. how well are the questions answered
    s1 = new_slide(prs, "Numbers 1/3 — how well are the questions answered?", y=0.25)
    bar_chart(s1, 0.34, 0.95, 4.6, 3.1, cats,
              [("CQ's page is in the ontology", [EV[n]["in_pages"]["score"] for n in names], BLUE),
               ("CQ's page failed / added nothing", [EV[n]["out_pages"]["score"] for n in names], ORANGE)],
              "Score by whether the CQ's page made it in", vmax=1.0, unit=0.25)
    bar_chart(s1, 5.06, 0.95, 4.6, 3.1, cats,
              [("Fully correct", [EV[n]["perfect"] for n in names], GREEN), ("Partly correct", [EV[n]["partial"] for n in names], BLUE),
               ("Wrong or no answer", [EV[n]["zero"] for n in names], ORANGE)],
              "Outcome of the sampled questions (count)", stacked=True, vmax=None, unit=10, fmt="0;;;")
    ins = [EV[n]["in_pages"]["score"] for n in names]
    worst = min(((n, EV[n]["out_pages"]["score"]) for n in names if EV[n]["out_pages"]["n"]), key=lambda x: x[1])
    tot = sum(EV[n]["n"] for n in names)
    perf = sum(EV[n]["perfect"] for n in names)
    part = sum(EV[n]["partial"] for n in names)
    zero = sum(EV[n]["zero"] for n in names)
    held = [f"{short_doc[n]} {EV[n]['n_held']} → {EV[n]['held_processed']:.2f}" for n in names if EV[n]["n_held"]]
    tb(s1, 0.34, 4.12, 4.6, 1.25, [[("Take-away", {"bold": True, "color": BLUE})],
        f"If a question's page is in the ontology, it scores {min(ins):.2f}–{max(ins):.2f}. If the page failed, it drops: {short_doc[worst[0]]} questions on failed pages score only {worst[1]:.2f}. "
        "So most of the lost score is missing pages, not wrong modelling."], size=10, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
    tb(s1, 5.06, 4.12, 4.6, 1.25, [[("Take-away", {"bold": True, "color": BLUE})],
        f"{perf} of {tot} sampled questions are fully correct ({round(100 * perf / tot)}%), {part} partly, {zero} wrong or without an answer. "
        f"Held-out reference questions (number → score): {'; '.join(held)}."], size=10, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
    notes(s1, "Score = the paper's SPARQL + judge test, 'processed' (an empty query result counts as 0). Source: ontology/lite/reference/eval/*.json. "
              "Each CQ is matched to its PDF page through the CQ files (CQ page keys are 0-based, PDF pages 1-based).")

    # ---------- 2. how much was built, and how complete is it
    s2 = new_slide(prs, "Numbers 2/3 — how much was built?", y=0.25)
    bar_chart(s2, 0.34, 0.95, 4.6, 3.1, cats,
              [("In the ontology", [len(fin[n]["in_ontology"]) for n in names], GREEN), ("Failed", [len(fin[n]["failed"]) for n in names], ORANGE),
               ("Ran, added nothing", [len(fin[n]["empty"]) for n in names], RGBColor(0x9A, 0xA5, 0xB1))],
              "Pages chosen for each document, and what happened", stacked=True, vmax=None, unit=5, fmt="0;;;")
    bar_chart(s2, 5.06, 0.95, 4.6, 3.1, cats,
              [("New classes", [onto[n]["classes"] for n in names], BLUE), ("New properties", [onto[n]["props"] for n in names], ORANGE),
               ("Facts (individuals)", [onto[n]["facts"] for n in names], GREEN)],
              "What the documents added to the reference ontology", vmax=None, unit=100, fmt="0")
    in_n = sum(len(fin[n]["in_ontology"]) for n in names)
    sel_n = sum(fin[n]["selected"] for n in names)
    tb(s2, 0.34, 4.12, 4.6, 1.25, [[("Take-away", {"bold": True, "color": BLUE})],
        f"{in_n} of the {sel_n} chosen pages made it into the ontology ({round(100 * in_n / sel_n)}%). The chosen pages are only {sel_n} of the {sum(fin[n]['doc_pages'] for n in names)} pages in these four documents: "
        "the ones that mention NNI Group, CVC, AVC, bearers and service levels most."], size=10, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
    tb(s2, 5.06, 4.12, 4.6, 1.25, [[("Take-away", {"bold": True, "color": BLUE})],
        f"Merged (a term found in two documents counted once): {U['classes']} new classes, {U['props']} new properties, {U['facts']} facts, on top of Farzad's "
        f"{F['ref']['classes']} classes and {F['ref']['props']} properties. {round(100 * U['sourced'] / U['facts'])}% of the facts carry a source clause."],
       size=10, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
    notes(s2, "Left: from the run logs ([added] page=N triples=K). Right: counted on the four ontology files after normalising the Price List namespace; "
              "'new' means not part of the reference seed (24 classes, 73 properties, scope core).")

    # ---------- 3. what does it cost
    s3_ = new_slide(prs, "Numbers 3/3 — what does it cost?", y=0.25)
    ok, bad = [], []
    for n in names:
        for pg in fin[n]["per_page"]:
            (ok if pg["added"] > 0 else bad).append((pg["size_before"], round(pg["tin"] / 1e6, 3)))
    scatter_chart(s3_, 0.34, 0.95, 4.6, 3.1, [("Page added content", ok, BLUE), ("Page failed / added nothing", bad, ORANGE)],
                  "Input tokens per page vs ontology size", "Ontology size before the page (triples)", "Input tokens for the page (millions)")
    worked = [round(sum(pg["tin"] for pg in fin[n]["per_page"] if pg["added"] > 0) / 1e6, 2) for n in names]
    wasted = [round(sum(pg["tin"] for pg in fin[n]["per_page"] if pg["added"] == 0) / 1e6, 2) for n in names]
    bar_chart(s3_, 5.06, 0.95, 4.6, 3.1, cats, [("Pages that added content", worked, BLUE), ("Failed / empty pages", wasted, ORANGE)],
              "Where the input tokens went (millions)", stacked=True, vmax=None, unit=5, fmt="0.0;;;")
    small = [t_ for s_, t_ in ok if s_ < 2000]
    large = [t_ for s_, t_ in ok if s_ >= 4000]
    med_ok = statistics.median(t_ for _, t_ in ok)
    med_bad = statistics.median(t_ for _, t_ in bad)
    tot_in = sum(worked) + sum(wasted)
    tot_out = sum(fin[n]["tokens"]["tout"] for n in names) / 1e6
    price = tot_in * 0.091 + tot_out * 0.182
    whole = ""
    try:   # the pipeline's own summary line: every stage (questions, ontology, evaluation) and every retry
        m = re.search(r"DONE in (\d+) min\. (\d+) calls, ([\d.]+)M in / ([\d.]+)M out, ~\$(\d+(?:\.\d+)?)", (REPO / "ontology" / "lite" / "run_faithful_stdout.txt").read_text(encoding="utf-8", errors="ignore"))
        whole = f" The whole run (questions, ontologies, evaluation, retries) was {m.group(3)}M input / {m.group(4)}M output tokens in {m.group(1)} minutes, about ${m.group(5)} at list price." if m else ""
    except Exception:
        pass
    tb(s3_, 0.34, 4.12, 4.6, 1.25, [[("Take-away", {"bold": True, "color": BLUE})],
        f"A page costs more as the ontology grows, because the whole file is pasted into every prompt: median {statistics.median(small):.1f}M input tokens below 2,000 triples, "
        f"{statistics.median(large):.1f}M above 4,000. A failed or empty page costs a median {med_bad:.1f}M, {med_bad / med_ok:.1f}× a normal page."],
       size=10, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
    tb(s3_, 5.06, 4.12, 4.6, 1.25, [[("Take-away", {"bold": True, "color": BLUE})],
        f"{round(100 * sum(wasted) / tot_in)}% of the input tokens ({sum(wasted):.1f}M of {tot_in:.1f}M) went on pages that produced nothing. "
        f"Ontology stage only: {tot_in:.1f}M input and {tot_out:.2f}M output tokens, about ${price:.1f} at list price (the real bill is lower: cached input is cheaper)." + whole],
       size=10, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
    notes(s3_, "Tokens are per page of the final run, taken from the [usage] lines in the run logs (they are cumulative per process, so the script subtracts). "
               "Input tokens dominate because every agent call carries the whole ontology (the paper's design). List price: $0.091 per million input, $0.182 per million output tokens "
               "(DeepSeek V4 Flash on OpenRouter). Failed or empty pages are the ones where repair rounds kept running.")
    return [s1, s2, s3_]


def _words(pdf, pages):
    import fitz
    d = fitz.open(str(pdf))
    return sum(len(d[p - 1].get_text("text").split()) for p in pages)


def _strip0(v):
    return "not run" if v is None else (f"{v:.4f}"[1:] if v < 1 else f"{v:.4f}")


def paper_style_slides(prs, F):
    """The paper's Table 1 (the documents) and Table 4 (CQ coverage, as bars and as the same table), for our documents."""
    import re as _re
    names, fin = list(F["docs"]), F["final"]
    cats = [short_doc[n] for n in names]
    # ---------- Table 1
    t1 = new_slide(prs, "Table 1 (paper style): the documents we used", y=0.25)
    sel_pages = {n: fin[n]["selected_pages"] for n in names}
    rows = [["Metric"] + cats]
    stems = {n: fin[n]["stem"] for n in names}
    ver = lambda n: (lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else "")(_re.search(r"-(\d{4})(\d\d)(\d\d)\.pdf", stems[n] + ".pdf"))
    rows += [["Document (nbn Ethernet)"] + [{"Technical Specification": "Product Technical Specification", "Product Description": "Product Description",
                                             "Price List": "Price List", "Service Levels": "Service Levels Schedule"}[n] for n in names],
             ["Version date"] + [ver(n) for n in names],
             ["Pages in the PDF"] + [str(fin[n]["doc_pages"]) for n in names],
             ["Pages chosen"] + [str(fin[n]["selected"]) for n in names],
             ["Pages that made it in"] + [str(len(fin[n]["in_ontology"])) for n in names],
             ["Words on the chosen pages"] + [f"{_words(PDF_DIR / (stems[n] + '.pdf'), sel_pages[n]):,}" for n in names],
             ["CQs generated (chosen pages)"] + [str(F["cq"][n]["total"]) for n in names],
             ["Number of CQs evaluated"] + [str(fin[n]["eval"]["n"]) + (f" (+{fin[n]['eval']['n_held']} held-out)" if fin[n]["eval"]["n_held"] else "") for n in names]]
    table(t1, 0.34, 1.0, 9.32, [2.6, 1.68, 1.68, 1.68, 1.68], rows, size=10, row_h=0.36, aligns=[PP_ALIGN.LEFT] + [PP_ALIGN.CENTER] * 4)
    tb(t1, 0.34, 4.5, 9.32, 0.75, [[("Same layout as the paper's Table 1 ", {"bold": True}),
        "(two life-insurance contracts, about 2,000 words each, 161 and 149 CQs). Ours are four real wholesale-broadband documents; only the pages that matter most are used, "
        "and 60 sampled CQs per document are evaluated, as in the paper's SPARQL test."]], size=10, fill=LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, anchor=MSO_ANCHOR.MIDDLE)
    notes(t1, "Mirrors Table 1 of the paper. Words are counted on the chosen pages only (PyMuPDF text). CQs generated = all CQs of the chosen pages; evaluated = the sample sent to the SPARQL test.")

    # ---------- Table 4
    t4 = new_slide(prs, "Table 4 (paper style): CQ coverage of our documents", y=0.25)
    spq = [fin[n]["eval"]["processed"] for n in names]
    rag = [None if fin[n].get("rag") is None else round(fin[n]["rag"], 4) for n in names]
    ser = [("Multi-agent (ours) · SPARQL", spq, BLUE)]
    if any(v is not None for v in rag):
        ser.append(("Multi-agent (ours) · RAG", rag, GREEN))
    bar_chart(t4, 0.34, 0.95, 4.5, 3.4, cats, ser, "CQ coverage (average CQ score)", vmax=1.0, unit=0.2, fmt="0.0000")
    rows = [["Sample", "Metric", "Direct Generation", "Multi-Agent (ours)"]]
    for n in names:
        rows.append([short_doc[n], "Processed SPARQL CQ Coverage", "not run", _strip0(fin[n]["eval"]["processed"])])
        rows.append(["", "RAG CQ Coverage", "not run", _strip0(None if fin[n].get("rag") is None else round(fin[n]["rag"], 4))])
    table(t4, 5.0, 0.95, 4.66, [1.05, 1.75, 0.9, 0.96], rows, size=8, row_h=0.31, first_col_bold=True)
    tb(t4, 0.34, 4.45, 9.32, 0.85, [[("Same chart and table layout as the paper's Table 4. ", {"bold": True}),
        "“Processed” = an empty query result counts as 0, as in the paper. We did not run the paper's direct-generation baseline, so that column is empty; "
        + ("RAG coverage was run on the existing ontologies (nothing was regenerated)." if any(v is not None for v in rag) else "the RAG evaluation was not run.")]],
       size=10, fill=LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, anchor=MSO_ANCHOR.MIDDLE)
    notes(t4, "Mirrors Table 4 of the paper (Sample / Metric / Direct Generation / Multi-Agent). Our SPARQL numbers are the pipeline's own final evaluation (60 CQs per document, "
              "empty query results set to 0). Direct generation was not run by us, so the column is empty rather than estimated.")
    return [t1, t4]


def build():
    F = run_facts()
    prs = Presentation(str(TEMPLATE))
    s1, s2, s3, s4, s5 = list(prs.slides)
    names = list(F["docs"])

    # ---------------------------------------------------------------- slide 1 (template, kept)
    tb(s1, 0.61, 5.33, 8.86, 0.25, "Applied to the NBN Wholesale Broadband Agreement (WBA) documents  ·  model: DeepSeek V4 Flash",
       size=10, color=GREY, align=PP_ALIGN.CENTER)
    notes(s1, "This is the repo we are reproducing: a pipeline of five roles (question writer, domain expert, manager, coder, QA) "
              "that turns a PDF into an OWL/Turtle ontology page by page. The next slide explains it in plain words.")

    # ---------------------------------------------------------------- A. the paper's pipeline in plain words (new)
    A = new_slide(prs, "The paper's pipeline in plain words")
    steps = [("PDF page", "One page of the document at a time", GREY),
             ("① Question writer", "Writes test questions with the right answers, e.g. “Max NNI Bearers in a group?” → 8", BLUE),
             ("② Analyst", "Lists the concepts, links and numbers needed to answer the questions", BLUE),
             ("③ Architect", "Plans how to model them with standard design patterns (ODPs)", BLUE),
             ("④ Builder", "Writes the Turtle (.ttl) triples into the ontology file", BLUE),
             ("⑤ Inspector", "Three gates: design review, syntax check, logic (OWL) check. Sends fixes back", BLUE),
             ("ontology.ttl", "Grows page by page. The whole file is pasted into every prompt", GREEN)]
    sub = ["", "CQ generator", "Domain Expert → SRD", "Manager → TIP", "Coder (edits file)", "QA + QA-Coder", "output"]
    bw, gap, x0 = 1.2, 0.15, 0.34
    for i, ((name, desc, col), sb) in enumerate(zip(steps, sub)):
        x = x0 + i * (bw + gap)
        tb(A, x, 1.0, bw, 0.62, [[(name, {"bold": True, "size": 11})], [(sb, {"size": 8})]] if sb else [[(name, {"bold": True, "size": 11})]],
           color=WHITE, fill=col, shape=MSO_SHAPE.ROUNDED_RECTANGLE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        tb(A, x, 1.68, bw, 1.55, desc, size=10, color=DARK, align=PP_ALIGN.CENTER)
        if i < len(steps) - 1:
            arrow(A, x + bw + 0.01, 1.31, x + bw + gap - 0.01, 1.31)
    tb(A, 0.34, 3.3, 9.32, 0.3, "Think of a small team: a question-writer, an analyst, an architect, a builder and an inspector, passing a document to each other.",
       size=11, color=GREY, italic=True, align=PP_ALIGN.CENTER)
    tb(A, 0.34, 3.75, 4.55, 1.45,
       [[("Then it is tested (evaluation)", {"bold": True, "color": BLUE, "size": 12})],
        "An LLM writes a SPARQL query for each question, the query runs on the ontology, and a judge scores the answer from 0 to 1. "
        "The average is the “CQ coverage”."], size=12, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, space_after=3)
    tb(A, 5.11, 3.75, 4.55, 1.45,
       [[("Paper vs this project", {"bold": True, "color": GREEN, "size": 12})],
        "Paper: 2 life-insurance contracts, a 30B local model, starts from an empty file.",
        "Here: NBN WBA documents, DeepSeek V4 Flash, starts from Farzad's reference ontology."], size=12, fill=GREEN_L,
       shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, space_after=3)
    notes(A, "Source: the paper, section 3.3 (multi-agent generator) and 3.4 (evaluation). Each page goes through the five roles. "
             "SRD = Semantic Requirements Document, TIP = Technical Implementation Plan, ODP = Ontology Design Pattern.")

    # ---------------------------------------------------------------- A2. the paper's Figure 2 with our five differences (annotate_paper_figure.py)
    A2 = new_slide(prs, "The paper's Figure 2, with our five differences", y=0.2, h=0.55, size=22)
    A2.shapes.add_picture(str(ASSETS / "paper_fig2_plus_ours.png"), Inches(0.3), Inches(0.95), width=Inches(6.25))
    diffs = [("Only the 51 key pages of the 4 documents are used (of 188)."),
             ("The ontology file starts from Farzad's reference ontology, not empty. New terms are tagged, every fact carries a source clause."),
             ("The reference rules and term list are added to every agent's prompt."),
             ("The QA review runs as plain code (numbers on the page, duplicates, source clause, CQ answered), not an LLM. RDFLib and HermiT checks stay."),
             ("Repair is capped at 3 rounds, then the file is restored and the page dropped (paper: up to 1,000 cycles).")]
    paras = []
    for i, d_ in enumerate(diffs, 1):
        paras.append([(f"{i}  ", {"bold": True, "color": ORANGE, "size": 12}), (d_, {"size": 10})])
    paras.append([("Everything else is the paper's pipeline: CQ Generator, Domain Expert, Manager, Coder, file tools, RDFLib, HermiT, Bug Fixer.", {"size": 9, "italic": True, "color": GREY})])
    tb(A2, 6.75, 0.95, 3.0, 4.2, paras, size=10, space_after=7)
    notes(A2, "The figure is the paper's Figure 2 (page 7 of the PDF), unchanged, with five numbered badges. 1 page selection, 2 reference ontology seeded into the file, "
              "3 reference brief in every agent's prompt, 4 deterministic checks instead of the LLM QA agent (the 'lite' loop), 5 bounded repair (3 rounds, then restore + drop page). "
              "Source: Presentation/annotate_paper_figure.py. A more detailed drawing is in assets/pipeline_comparison.png.")

    # ---------------------------------------------------------------- slide 2 (template: what had to be added / where it differs)
    shp = {sh.name: sh for sh in s2.shapes}
    ref_ph = shp["Google Shape;62;p14"]
    code_ph = shp["Google Shape;64;p14"]
    clear_placeholder(ref_ph, [
        [("The reference: ", {"bold": True}), "Farzad's ontology (dart-project-main in Documents) is the NBN “skeleton”: NNI Group, CVC, AVC, Bearer, POI, Service Level…"],
        [("We give the model ", {}), (f"{F['ref']['classes']} classes, {F['ref']['props']} properties and 30 fixed values", {"bold": True}), " from it (Single / Diverse Chassis, 1 / 10 / 100 Gbps…)."],
        [("Why: ", {"bold": True}), "the original repo starts from an empty file, so the model invents its own names. Now it must reuse these, and may add new things "
                                  "from the PDFs only if they are tagged with document + page."]], size=12.5, space=4)
    clear_placeholder(code_ph, [
        [("helper/reference.py", {"bold": True, "font": MONO, "size": 10.5}), " (new): loads the reference, writes it into the file, builds the text the agents get"],
        [("agents/multi_agent.py", {"bold": True, "font": MONO, "size": 10.5}), " (+235 / −29 lines): sends that text to all 5 agents, seeds the file, adds the “lite” checks"],
        [("helper/grounding.py", {"bold": True, "font": MONO, "size": 10.5}), " (new): checks every number against the PDF page"],
        [("Exact code on the next slides →", {"italic": True, "color": GREY})]], size=12.5, space=4)
    notes(s2, "The repo's other edited files are small fixes (Windows paths, OpenRouter endpoint, missing helper functions, two bugs) - listed in REPRODUCTION.md. "
              "Everything about the reference ontology is in the three files above.")

    # ---------------------------------------------------------------- B. how the reference reaches the LLM (new)
    B = new_slide(prs, "How the reference reaches the LLM")
    tb(B, 0.34, 1.0, 2.35, 1.1, [[("dart_core.ttl", {"bold": True, "font": MONO, "size": 11})], "Farzad's ontology", [(f"{F['ref']['classes']} classes · {F['ref']['props']} properties", {"size": 9.5})]],
       color=WHITE, fill=BLUE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, size=11)
    tb(B, 0.34, 2.55, 2.35, 0.8, [[("reference.py", {"bold": True, "font": MONO, "size": 11})], [("Reference class", {"size": 10})]],
       fill=LIGHT, line=GREY, shape=MSO_SHAPE.ROUNDED_RECTANGLE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    arrow(B, 1.5, 2.12, 1.5, 2.53)
    items = [("① Seed the file", "Writes the skeleton into ontology.ttl before page 1. The original starts from an empty file.", 1.0),
             ("② Brief the agents", "Adds the rules + a term list (class · also called · properties) to the prompt of all 5 agents.", 2.05),
             ("③ Check the result", "New things must carry [extension: document, page]; every fact must carry :sourceClause.", 3.1)]
    for t_, d, y in items:
        tb(B, 3.35, y, 3.05, 0.92, [[(t_, {"bold": True, "color": BLUE, "size": 12})], d], size=10, fill=WHITE, line=BLUE,
           shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
        arrow(B, 2.72, 2.95, 3.33, y + 0.46, color=BLUE)
    # the ontology file, one block in three bands, each band lined up with the box that acts on it
    tb(B, 6.85, 1.0, 2.8, 0.92, [[("ontology.ttl  ·  REFERENCE SEED", {"bold": True, "size": 10})], [("do not delete", {"size": 9})],
                                  [(f"{F['ref']['triples']} triples from Farzad", {"size": 9})]], fill=BLUE_L, line=BLUE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    tb(B, 6.85, 1.92, 2.8, 1.18, [[("The 5 agents read and write this file", {"bold": True, "size": 10})], [("Expert · Manager · Coder · QA · QA-Coder", {"size": 9})],
                                   [("each prompt now ends with the brief", {"size": 9})]], fill=LIGHT, line=GREY, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    tb(B, 6.85, 3.1, 2.8, 0.92, [[("EXTENSIONS FROM DOCUMENTS", {"bold": True, "size": 10})], [("new classes, properties, facts", {"size": 9})],
                                 [("each with page + source", {"size": 9})]], fill=ORANGE_L, line=ORANGE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    for y in (1.46, 2.51, 3.56):
        arrow(B, 6.42, y, 6.83, y, color=BLUE)
    # a real example from the result
    ex_file = glob.glob(str(REPO / "ontology/lite/reference/*technical-specification*__reference.ttl"))[0]
    ex = Path(ex_file).read_text(encoding="utf-8").splitlines()
    i0 = next(i for i, l in enumerate(ex) if l.startswith(":BearerLimit_1G_10G_Max8 "))
    ex_lines = [(None, l[:170]) for l in ex[i0:i0 + 7]]
    tb(B, 0.34, 4.1, 9.32, 0.22, "A real fact from the result (Technical Specification), with the page it came from:", size=9.5, color=GREY, italic=True)
    code_box(B, 0.34, 4.32, 9.32, 1.05, [(None, ex_lines[0][1] + " " + ex_lines[1][1].strip())] + ex_lines[2:7], size=8.5, numbers=False)
    notes(B, "Mechanically: the file is seeded once (lines 1060-1070 of multi_agent.py), the same text is appended to each agent's prompt "
             "(five one-line calls), and the extension tags let us tell Farzad's terms from the ones the PDFs added.")

    # ---------------------------------------------------------------- C. code 1: the rules (new)
    C = new_slide(prs, "Code 1 — the rules every agent receives")
    pol = dedent(src_lines("helper/reference.py", 241, 257, width=92)) + [(None, "")] + dedent(src_lines("helper/reference.py", 270, 275, width=92))
    code_box(C, 0.34, 0.98, 6.2, 4.4, pol, size=8.2, title="helper/reference.py  (long lines cut with … ; the file has the full text)")
    tb(C, 6.7, 0.98, 2.96, 4.4,
       [[("In simple words", {"bold": True, "color": BLUE, "size": 12})],
        [("1 REUSE  ", {"bold": True}), "If the PDF says “NNI Group”, use the existing NNIGroup. No copies."],
        [("2 EXTEND  ", {"bold": True}), "New things are allowed, but tagged [extension: doc, page]."],
        [("3 FACTS  ", {"bold": True}), "Numbers become data, each with :sourceClause (where it came from)."],
        [("4 NEVER delete  ", {"bold": True}), "If the PDF disagrees with the reference, keep both and flag it."],
        [("5 No inventing.", {"bold": True})],
        [("prompt_block() glues the rules to a short per-role sentence (_ROLE) and, for the Expert and Manager, the term list.", {"italic": True, "color": GREY, "size": 10})]],
       size=10.5, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, space_after=5)
    notes(C, "These are lines 241-257 and 270-275 of helper/reference.py. The text is plain English because the agents are LLMs: it is a prompt, not logic. "
             "An earlier version of rule 3 contained a real value from the PDFs (4000) as an example; that leaked an answer to one of our test questions and was removed.")

    # ---------------------------------------------------------------- D. code 2: wiring (new)
    D = new_slide(prs, "Code 2 — plugging it into the original pipeline")
    raw = (REPO / "agents/multi_agent.py").read_text(encoding="utf-8").splitlines()
    L = lambda n, label="": (n, raw[n - 1].strip() + (f"  ◂ {label}" if label else ""))   # the exact line, optionally with a slide label
    wl = [L(24), L(56), (None, "")] + dedent(src_lines("agents/multi_agent.py", 64, 66, width=78)) + [(None, "")] +          [L(266, "Domain Expert"), L(371, "Manager"), L(477, "Coder"), L(599, "QA reviewer"), L(829, "QA-Coder"), (None, ""),
          *dedent(src_lines("agents/multi_agent.py", 1026, 1027)), (None, "…")] + dedent(src_lines("agents/multi_agent.py", 1069, 1070))
    for n, frag in [(24, "from helper.reference import Reference"), (266, '_ref_block("expert")'), (371, "_ref_block('manager')"), (477, "_ref_block('coder')"),
                    (599, '_ref_block("qa")'), (829, '_ref_block("qa_coder")'), (1026, "if args.reference"), (1027, "REF = Reference(")]:
        assert frag in raw[n - 1], (n, raw[n - 1])
    code_box(D, 0.34, 0.98, 6.2, 4.4, wl, size=9.5, title="agents/multi_agent.py   (yellow ◂ labels added for the slide)")
    tb(D, 6.7, 0.98, 2.96, 4.4,
       [[("In simple words", {"bold": True, "color": BLUE, "size": 12})],
        [("5 one-line insertions", {"bold": True}), " — one per agent. Each adds the brief to that agent's prompt."],
        [("1 new command-line flag", {"bold": True}), ", --reference, loads the ontology."],
        [("2 lines", {"bold": True}), " write the seed into the file, only if the file is still empty (never overwrites work)."],
        [("How we start it:", {"bold": True})],
        [("--arm reference --reference-scope core --mode lite", {"font": MONO, "size": 8.5})],
        [("Without --reference nothing changes: it is still the paper's pipeline.", {"italic": True, "color": GREY, "size": 10})]],
       size=10.5, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, space_after=5)
    notes(D, "Line numbers are from the current agents/multi_agent.py. The code lines are cut straight out of the file; "
             "only the yellow ◂ labels were added here to say which agent each line belongs to.")

    # ---------------------------------------------------------------- E. the lite loop (new)
    E = new_slide(prs, "Code 3 — “lite” checks instead of an LLM reviewer")
    bx = [("④ Builder", "writes the page into the file", BLUE, 0.34, 1.9),
          ("Checks (no LLM)", "numbers · duplicates · sources · answers · cycles", ORANGE, 2.75, 2.5),
          ("Next page", "all checks pass", GREEN, 6.9, 1.4)]
    for t, d, c, x, w in bx:
        tb(E, x, 0.98, w, 0.78, [[(t, {"bold": True, "size": 11})], [(d, {"size": 9})]], color=WHITE, fill=c, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
           align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    arrow(E, 2.26, 1.37, 2.73, 1.37)
    arrow(E, 5.27, 1.37, 6.88, 1.37)
    tb(E, 5.4, 1.07, 1.4, 0.25, "all pass", size=9, color=GREY, align=PP_ALIGN.CENTER)
    arrow(E, 4.0, 1.78, 4.0, 2.12, color=ORANGE)
    arrow(E, 4.0, 2.12, 1.3, 2.12, color=ORANGE, head=False)
    arrow(E, 1.3, 2.12, 1.3, 1.8, color=ORANGE)
    tb(E, 1.4, 1.84, 2.55, 0.25, "problem → feedback, max 2 rounds", size=9, color=ORANGE, bold=True)
    tb(E, 8.45, 0.98, 1.21, 0.78, [[("Still wrong?", {"bold": True, "size": 10})], [("wrong numbers deleted; broken page dropped", {"size": 8})]],
       fill=ORANGE_L, line=ORANGE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, margin=0.04)
    g1 = dedent(src_lines("helper/grounding.py", 205, 224, width=77))
    code_box(E, 0.34, 2.42, 5.0, 2.95, g1, size=7.5, title="helper/grounding.py — what the checks tell the Coder")
    g2 = dedent(src_lines("helper/grounding.py", 68, 71, width=63)) + [(None, "")] + dedent(src_lines("agents/multi_agent.py", 1159, 1163, width=63))
    code_box(E, 5.45, 2.42, 4.21, 2.95, g2, size=7.5, title="the number check, then the last-resort clean-up")
    notes(E, "Why: the paper's LLM review loop is expensive and can loop for a very long time. In our first test the QA loops used ~1.4M tokens for 2 pages. "
             "The lite checks are plain Python, free, and stricter on numbers: a value that is not on the page is deleted. "
             "(Line numbers: grounding.py 205-224 build_feedback; 68-71 the numeric check; multi_agent.py 1159-1163 the strip_ungrounded call.)")
    # ---------------------------------------------------------------- slide 3 (template: accuracy & metrics)
    ev = F["eval"]
    done = [n for n in names if ev[n]]
    allq = sum(ev[n]["n"] for n in done) if done else 0
    perfect = sum(ev[n]["perfect"] for n in done) if done else 0
    o = F["onto"]
    tot = lambda k: sum(o[n][k] for n in names)
    U = F["union"]
    pages_in = sum(len(F["pages"][n]) for n in names)
    pages_sel = sum(sum((int(b) - int(a) + 1) if "-" in x else 1 for x in F["selected"][n].split(",") for a, _, b in [x.partition("-")]) for n in names)
    pages_all = sum(F["total_pages"].values())
    stripped = sum(v for k, v in F["stripped"].items() if k != "Price List")   # the Price List count is inflated by the namespace bug
    shp3 = {sh.name: sh for sh in s3.shapes}
    body = shp3["Google Shape;70;p15"]
    acc_txt = (f"Numbers, dates and durations are checked against the text of their own page; {stripped} that were not on the page were deleted (Price List excluded, see the last slide). "
               + (f"SPARQL + judge test (paper's method): {perfect} of {allq} sampled questions fully correct." if done else "SPARQL + judge test: running."))
    clear_placeholder(body, [
        [("How accurate is it?  ", {"bold": True, "color": BLUE}), acc_txt],
        [("How specific?  ", {"bold": True, "color": BLUE}),
         f"Each limit keeps its condition, value, unit and source, e.g. “Bearer Limit: 8 NNI Bearers for 1G/10G · section 6.1 · page 28”. "
         f"So far, merged: {U['classes']} new classes, {U['props']} new properties and {U['facts']} facts."],
        [("What is included / left out?  ", {"bold": True, "color": BLUE}),
         f"Included: 4 documents, {pages_in} key pages of the {pages_sel} chosen (out of {pages_all} pages). "
         f"Left out: {sum(len(F['failed'][n]) for n in names)} pages that failed, {sum(len(F['empty'][n]) for n in names)} that added nothing, "
         f"and the other 50 PDFs. Price List facts mostly lack a source clause (bug, slide 'Known problems')."]], size=13, space=8)
    for p in body.text_frame.paragraphs:
        p._p.get_or_add_pPr().set("marL", "0"); p._p.get_or_add_pPr().set("indent", "0")
        for ch in list(p._p.pPr):
            if ch.tag == qn("a:buChar"):
                p._p.pPr.remove(ch)
        etree.SubElement(p._p.pPr, qn("a:buNone"))
    s3.shapes.title.text_frame.paragraphs[0].runs[0].text = "Accuracy & metrics"
    body.top, body.height = Inches(1.05), Inches(2.75)
    tiles = [(f"{pages_in} / {pages_sel}", "key pages in the ontology"),
             (f"{round(100 * U['sourced'] / U['facts'])}%", "facts with a source clause"),
             (f"{perfect} / {allq}" if done else "…", "test questions fully correct"),
             (str(F["dups"]), "near-duplicate class pairs")]
    for i, (big, small) in enumerate(tiles):
        tb(s3, 0.34 + i * 2.37, 3.95, 2.22, 1.3, [[(big, {"bold": True, "size": 24, "color": BLUE if i != 3 else ORANGE})], [(small, {"size": 10.5, "color": GREY})]],
           fill=LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    notes(s3, "Numbers come from the files at build time. 'Accurate' here means two things: (1) values are on the source page (automatic check), "
              "(2) the paper's SPARQL + LLM-judge test on a sample. It does not mean a human has reviewed the ontology.")

    # ---------------------------------------------------------------- F. which pages (new)
    Fp = new_slide(prs, "Exactly which documents and pages")
    rows = [["Document (nbn Ethernet …)", "Pages", "In the ontology (PDF page numbers)", "Not in, and why"]]
    short = {"Technical Specification": "Product Technical Specification", "Product Description": "Product Description",
             "Price List": "Price List", "Service Levels": "Service Levels Schedule"}
    for n in names:
        why = []
        if F["failed"][n]:
            why.append(f"{fmt_pages(F['failed'][n])}: failed (file error / repair rounds used up)" if n != "Price List" else f"{fmt_pages(F['failed'][n])}: failed (file error)")
        if F["empty"][n]:
            why.append(f"{fmt_pages(F['empty'][n])}: ran, added nothing")
        notyet = sorted(set(int(x) for part in F["selected"][n].split(",") for x in (range(int(part.split('-')[0]), int(part.split('-')[-1]) + 1))) - set(F["pages"][n]) - set(F["failed"][n]) - set(F["empty"][n]))
        if notyet:
            why.append(f"{fmt_pages(notyet)}: not reached yet")
        rows.append([short[n], str(F["total_pages"][n]), fmt_pages(F["pages"][n]), "; ".join(why) or "–"])
    table(Fp, 0.34, 1.0, 9.32, [2.2, 0.6, 3.0, 3.52], rows, size=9.5, row_h=0.62)
    tb(Fp, 0.34, 4.25, 9.32, 1.1,
       [[("How the pages were chosen: ", {"bold": True}),
         "each page is scored by how often it mentions the reference concepts (NNI Group, Bearer Limit, CVC, AVC, Product Component, TC-1/2/4, Network Availability, "
         f"frame delay…). Score ≥ 25 → important. That gave {pages_sel} pages out of {pages_all} in these 4 documents; 50 other PDFs (letters, annexures, other products) are not used."],
        [("Fair Use Policy ", {"bold": True}), "(8 pages) was only used for a first test; it is not in this ontology."]], size=10.5, fill=LIGHT,
       shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, space_after=3)
    notes(Fp, "Page numbers are PDF page numbers (page 1 = cover). 'Ran, added nothing' means the agent finished without writing any new triples "
              "(Product Description page 9). 'Failed' means the page could not be repaired and was rolled back.")

    # ---------------------------------------------------------------- G. quality + CQ table (new)
    G_ = new_slide(prs, "Quality and competency questions (CQs) per document")
    rows = [["Document", "New classes", "New props", "Facts", "Facts with source", "CQs on pages used", "…numeric answers", "Answer in ontology*"]]
    for n in names:
        c = F["cq"][n]
        used = c["used"]
        rows.append([n, str(o[n]["classes"]), str(o[n]["props"]), str(o[n]["facts"]),
                     f"{o[n]['sourced']} ({round(100 * o[n]['sourced'] / max(1, o[n]['facts']))}%)", str(len(used)), str(c["numeric"]), "…"])
    # loose answer-in-ontology check, computed on the union
    import rdflib
    from helper import grounding as G
    from helper.reference import PIPE_NS
    union = rdflib.Graph()
    for n, key in F["docs"].items():
        f = glob.glob(str(REPO / f"ontology/lite/reference/*{key}*__reference.ttl"))[0]
        union.parse(data=re.sub(r"@prefix : <[^>]*> \.", f"@prefix : <{PIPE_NS}> .", Path(f).read_text(encoding="utf-8"), count=1), format="turtle")
    tot_used = tot_cov = 0
    for i, n in enumerate(names, start=1):
        used = F["cq"][n]["used"]
        unc = G.uncovered_cqs(used, union, limit=10 ** 6)
        rows[i][7] = f"{len(used) - len(unc)} / {len(used)}"
        tot_used += len(used); tot_cov += len(used) - len(unc)
    rows.append(["All four, merged", str(U["classes"]), str(U["props"]), str(U["facts"]), f"{U['sourced']} ({round(100 * U['sourced'] / U['facts'])}%)",
                 str(tot_used), str(sum(F['cq'][n]['numeric'] for n in names)), f"{tot_cov} / {tot_used}"])
    tq = table(G_, 0.34, 0.95, 9.32, [2.0, 0.95, 0.85, 0.7, 1.35, 1.2, 1.1, 1.17], rows, size=10, row_h=0.34,
               aligns=[PP_ALIGN.LEFT] + [PP_ALIGN.CENTER] * 7)
    tq.rows[0].height = Inches(0.5)
    tb(G_, 0.34, 3.4, 4.55, 1.95,
       [[("Read this carefully", {"bold": True, "color": ORANGE, "size": 11})],
        "* “Answer in ontology” is a loose check: the expected answer's words/numbers appear somewhere in the ontology. The build loop itself pushes for this, "
        "so it is optimistic. The real test is the SPARQL + judge score (next slides).",
        f"Duplicate-looking classes: {F['dups']} pairs (e.g. Cl_AVC next to AVC). {tot('example_named')} facts are named *_Example. Merged = a term found in two documents is counted once."],
       size=10, fill=ORANGE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, space_after=4)
    tb(G_, 5.11, 3.4, 4.55, 1.95,
       [[("What a CQ looks like", {"bold": True, "color": BLUE, "size": 11})],
        [("Q: ", {"bold": True}), "What is the Bearer Limit for an NNI Group of 2 × 100 Gbps NNI Bearers in Diverse Chassis mode?"],
        [("A: ", {"bold": True}), "31,000"],
        [("CQs are written from the PDF page by the question-writer agent (the paper's step 1); ", {"color": GREY, "size": 9.5}),
         ("they are the test and the to-do list for each page.", {"color": GREY, "size": 9.5})]],
       size=10.5, fill=BLUE_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, space_after=4)
    notes(G_, "Facts = individuals (data about specific things) added by the documents, not counting Farzad's own 30 values. "
              "Price List: facts without a source clause are a known bug (a namespace mismatch made the check delete the clauses), see 'Known problems'.")

    # ---------------------------------------------------------------- slide 4 (template: screenshots) + continued (new)
    s4.shapes.title.text_frame.paragraphs[0].runs[0].text = "Screenshot of the system"
    ph = [sh for sh in s4.shapes if sh.name == "Google Shape;76;p16"][0]
    ph._element.getparent().remove(ph._element)
    set_title(s4, "Screenshot of the system (1/2)", y=0.25)
    def shot(slide, name, x, y, w, caption):
        pic = slide.shapes.add_picture(str(ASSETS / name), Inches(x), Inches(y), width=Inches(w))
        pic.line.color.rgb = MID; pic.line.width = Pt(0.75)
        tb(slide, x, y + pic.height / 914400 + 0.03, w, 0.3, caption, size=10, color=GREY, align=PP_ALIGN.CENTER)
    shot(s4, "shot_overview.png", 0.34, 1.0, 4.55, "Overview: totals, extension classes per document, reference classes")
    shot(s4, "shot_graph.png", 5.11, 1.0, 4.55, "Graph: NNIGroup (blue = Farzad's terms, orange = added from the PDFs)")
    tb(s4, 0.34, 4.4, 9.32, 0.85, [[("What you see: ", {"bold": True}), "an offline web page (viewer/ontology_viewer.html: one file, no server). Blue = Farzad's terms, orange = what the PDFs added. "
                                    "Click a class to see its properties, parents and facts."]], size=11, fill=LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, anchor=MSO_ANCHOR.MIDDLE)
    s4b = new_slide(prs, "Screenshot of the system (2/2)", y=0.25)
    shot(s4b, "shot_tree.png", 0.34, 1.0, 4.55, "Class tree: reference classes first, additions below")
    shot(s4b, "shot_facts.png", 5.11, 1.0, 4.55, "Facts: each row names its source (document, section, page)")
    tb(s4b, 0.34, 4.4, 9.32, 0.85, [[("Why it matters: ", {"bold": True}), "every fact can be traced back to a section and page of the PDF, "
                                     "and a data-quality panel lists problems (near-duplicate names, facts without a source…)."]], size=11, fill=LIGHT,
       shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.12, anchor=MSO_ANCHOR.MIDDLE)
    notes(s4, "Screenshots are of viewer_merged.html: the four ontologies merged into one (the viewer is a single HTML file).")

    # ---------------------------------------------------------------- slide 5 (template: bar chart)
    ph5 = [sh for sh in s5.shapes if sh.name == "Google Shape;82;p17"][0]
    ph5._element.getparent().remove(ph5._element)
    set_title(s5, "CQ coverage — the paper's Table 4 as bars", y=0.25)
    # left: ours
    cd = CategoryChartData()
    cd.categories = [n for n in names if ev[n]] or ["(evaluation running)"]
    cd.add_series("Processed SPARQL CQ coverage", [ev[n]["processed"] if ev[n] else 0 for n in names if ev[n]] or [0])
    cd.add_series("Raw judge score", [ev[n]["raw"] if ev[n] else 0 for n in names if ev[n]] or [0])
    g1 = s5.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.34), Inches(0.95), Inches(4.7), Inches(3.2), cd)
    ch = style_chart(g1, "This project (WBA, DeepSeek V4 Flash)")
    for s_, col in zip(ch.plots[0].series, (BLUE, MID)):
        s_.format.fill.solid(); s_.format.fill.fore_color.rgb = col
    # right: the paper's Table 4
    cd2 = CategoryChartData()
    cd2.categories = ["Equivita", "Sentinel"]
    cd2.add_series("Direct · SPARQL", [0.6304, 0.4027]); cd2.add_series("Multi-agent · SPARQL", [0.6196, 0.4383])
    cd2.add_series("Direct · RAG", [0.5994, 0.4450]); cd2.add_series("Multi-agent · RAG", [0.7112, 0.5510])
    g2 = s5.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(5.2), Inches(0.95), Inches(4.46), Inches(3.2), cd2)
    ch2 = style_chart(g2, "The paper (Table 4, insurance contracts)")
    for s_, col in zip(ch2.plots[0].series, (MID, BLUE, RGBColor(0xB8, 0xC4, 0xCE), GREEN)):
        s_.format.fill.solid(); s_.format.fill.fore_color.rgb = col
    ev_n = {n: ev[n] for n in names if ev[n]}
    held = [f"{n} {e['n_held']} question{'s' if e['n_held'] != 1 else ''} → {e['held_processed']:.2f}" for n, e in ev_n.items() if e["n_held"]]
    n_each = sorted({e["n"] for e in ev_n.values()})
    tb(s5, 0.34, 4.2, 9.32, 1.17,
       [[("How to read it: ", {"bold": True}), f"same measure as the paper: an LLM writes SPARQL for each CQ, the query runs, a judge scores 0–1. “Processed” sets empty query results to 0, as the paper does. "
                                              f"Ours: {', '.join(str(x) for x in n_each)} CQs per document, sampled over all the pages chosen, including pages that failed (next slides split this up)."],
        [("Not a blind test: ", {"bold": True, "color": ORANGE}), "those CQs were written from the same pages and given to the agents as requirements (as in the paper). "
                                                                  + ("Held-out reference questions (never shown to the agents, though some answers sit in the reference seed): " + "; ".join(held) + ". " if held else "")
                                                                  + "Different documents, model and questions from the paper. The paper's RAG coverage for our ontologies is on the Table 4 slide."]],
       size=9.5, fill=LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, margin=0.1, space_after=2)
    notes(s5, "The paper has no bar chart; its Table 4 (CQ coverage) is drawn here as bars on the right. On the left is the same measure for our four ontologies. "
              "Our numbers are the pipeline's own final evaluation (agents/eval.py, 60 CQs per document, read from ontology/lite/reference/eval). "
              "If a score looks high: the questions were written from the same pages the ontology was built from, as in the paper.")

    NUM = numbers_slides(prs, F)
    PS = paper_style_slides(prs, F)

    # ---------------------------------------------------------------- H. known problems (new)
    H = new_slide(prs, "Known problems and next steps")
    bad_pages = [f"{fmt_pages(F['failed'][n] + F['empty'][n])} ({short_doc[n]})" for n in names if F["failed"][n] + F["empty"][n]]
    ev_out = [(n, F["final"][n]["eval"]["out_pages"]["score"]) for n in names if F["final"][n]["eval"]["out_pages"]["n"]]
    MISSING_TXT = ("Pages " + "; ".join(bad_pages) + " failed or added nothing. Questions about those pages score far lower ("
                   + ", ".join(f"{short_doc[n]} {s:.2f}" for n, s in ev_out) + ").")
    prob = [
        ("Price List is in another namespace", "Its file uses a different namespace from the other three (cause not traced). The logs show the source-clause check flagging its :sourceClause values "
                                                 "as “unsupported”, and only ~20% of its facts still have one. Its AVC, CVC… copies also count as duplicates. Fix: re-run it, or re-write the namespace and add the clauses back."),
        ("Duplicates", f"{F['dups']} near-duplicate class pairs (Cl_AVC vs AVC). The model invents a variant instead of reusing. The paper reports the same weakness."),
        ("Some pages are missing", MISSING_TXT),
        ("Test questions are not fully blind", "One policy example leaked a value (fixed) and a few reference values sit in the seed, so the 8 held-out questions are only partly clean."),
        ("Small evaluation", "60 CQs per document, one run, one judge model, questions from the same pages the ontology was built from. No direct-baseline comparison."),
    ]
    y = 0.98
    for t, d in prob:
        tb(H, 0.34, y, 2.85, 0.76, t, size=11.5, bold=True, color=WHITE, fill=ORANGE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, anchor=MSO_ANCHOR.MIDDLE, margin=0.1)
        tb(H, 3.3, y, 6.36, 0.76, d, size=10.5, fill=LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, anchor=MSO_ANCHOR.MIDDLE, margin=0.1)
        y += 0.86
    notes(H, "Honest list. Each of these is visible in the files, none is hidden.")

    # ---------------------------------------------------------------- order
    order = [s1, A, A2, s2, B, C, D, E, Fp, s3, G_, s4, s4b, s5] + PS + NUM + [H]
    for i, sl in enumerate(order):
        move_slide(prs, sl, i)
    prs.save(str(OUT))
    print("wrote", OUT, "slides:", len(prs.slides))
    return F


if __name__ == "__main__":
    F = build()
    print(json.dumps({k: F[k] for k in ("pages", "failed", "empty", "stripped", "eval", "onto", "dups")}, indent=1, default=str))
