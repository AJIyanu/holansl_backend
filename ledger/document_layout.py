"""Shared Ledger document typography and private brand assets."""

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph

ASSETS = Path(__file__).with_name("assets")
pdfmetrics.registerFont(TTFont("Holan", str(ASSETS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("HolanBold", str(ASSETS / "DejaVuSans-Bold.ttf")))
STYLE = ParagraphStyle("body", fontName="Holan", fontSize=9, leading=13)


def paragraph(value):
    return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), STYLE)
