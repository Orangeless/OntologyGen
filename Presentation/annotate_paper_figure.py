"""The paper's Figure 2 (taken from the PDF, unchanged) with five small numbered orange badges marking where our implementation differs.

  python Presentation/annotate_paper_figure.py        (needs pymupdf and pillow)

Writes assets/paper_fig2_plus_ours.png (figure + badges) and assets/paper_fig2_plus_ours_legend.png (same, with the list underneath).
"""
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
PDF = HERE / "ontologygenautomatic.pdf"
OUT = HERE / "assets"
ORANGE = (237, 86, 27)
PAD = 46

ADDITIONS = [   # number, centre on the paper's figure (px of the original 2354 x 1520 image), text
    (1, (150, 22), "Only the 51 key pages of the 4 documents are used (of 188), chosen by how often they mention the reference concepts."),
    (2, (1778, 280), "The ontology file starts from Farzad's reference ontology instead of an empty file. New terms are tagged [extension: document, page]; every fact carries a source clause."),
    (3, (20, 452), "The reference rules and term list are added to the prompt of every agent."),
    (4, (36, 1104), "The QA review runs as plain code (numbers on the page, duplicates, source clause, CQ answered), not as an LLM. The RDFLib syntax and HermiT logic checks stay as in the paper."),
    (5, (1698, 1090), "Repair is capped at 3 rounds; then the file is restored and the page dropped (paper: up to 1,000 cycles)."),
]
FOOT = "Everything else is the paper's Figure 2: CQ Generator, Domain Expert (SRD), Manager (TIP), Coder, file tools, RDFLib, HermiT, Bug Fixer."


def figure() -> Image.Image:
    d = pymupdf.open(str(PDF))
    xref = d[6].get_images(full=True)[0][0]     # Figure 2 is the only image on page 7
    pm = pymupdf.Pixmap(d, xref)
    if pm.n - pm.alpha >= 4:
        pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
    pm.save(str(OUT / "_paper_fig2.png"))
    return Image.open(OUT / "_paper_fig2.png").convert("RGB")


def font(size, bold=False):
    return ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf", size)


def badges(img):
    big = Image.new("RGB", (img.width + 2 * PAD, img.height + 2 * PAD), "white")
    big.paste(img, (PAD, PAD))
    dr = ImageDraw.Draw(big)
    r = 36
    for n, (cx, cy), _ in ADDITIONS:
        cx, cy = cx + PAD, cy + PAD
        dr.ellipse((cx - r - 5, cy - r - 5, cx + r + 5, cy + r + 5), fill="white")
        dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ORANGE)
        f = font(46, True)
        w = dr.textlength(str(n), font=f)
        dr.text((cx - w / 2, cy - 27), str(n), font=f, fill="white")
    return big


def wrap(dr, text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if dr.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


def with_legend(big):
    f, fb = font(44), font(44, True)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    rows = []
    for n, _, text in ADDITIONS:
        rows.append((n, wrap(probe, text, f, big.width - 2 * PAD - 110)))
    foot = wrap(probe, FOOT, font(40), big.width - 2 * PAD)
    h = PAD + sum(len(ls) * 58 + 16 for _, ls in rows) + len(foot) * 52 + 2 * PAD
    out = Image.new("RGB", (big.width, big.height + h), "white")
    out.paste(big, (0, 0))
    dr = ImageDraw.Draw(out)
    y = big.height + PAD // 2
    for n, ls in rows:
        dr.ellipse((PAD, y + 4, PAD + 56, y + 60), fill=ORANGE)
        w = dr.textlength(str(n), font=fb)
        dr.text((PAD + 28 - w / 2, y + 6), str(n), font=fb, fill="white")
        for i, ln in enumerate(ls):
            dr.text((PAD + 90, y + i * 58), ln, font=f, fill=(34, 34, 34))
        y += len(ls) * 58 + 16
    y += 12
    for ln in foot:
        dr.text((PAD, y), ln, font=font(40), fill=(110, 118, 130))
        y += 52
    return out


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    big = badges(figure())
    big.save(OUT / "paper_fig2_plus_ours.png")
    with_legend(big).save(OUT / "paper_fig2_plus_ours_legend.png")
    (OUT / "_paper_fig2.png").unlink()
    print("wrote", OUT / "paper_fig2_plus_ours.png", big.size)
