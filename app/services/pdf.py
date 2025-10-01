from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from num2words import num2words
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from weasyprint import HTML, CSS

FONT_NAME = "DejaVuSans"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
TEMPLATE_RASHOD_HTML = Path(__file__).resolve().parents[2] / "data" / "rashod_demo.html"
TEMPLATE_PRIHOD_HTML = Path(__file__).resolve().parents[2] / "data" / "prihod_demo.html"

pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ShipmentItemData:
    line_number: int
    product_name: str
    product_code: int
    quantity: Decimal
    sale_price_cents: int
    purchase_price_cents: int

    @property
    def total_sale_cents(self) -> int:
        return int(self.quantity * self.sale_price_cents)

    @property
    def total_purchase_cents(self) -> int:
        return int(self.quantity * self.purchase_price_cents)


@dataclass(slots=True)
class ShipmentPdfData:
    doc_number: int
    created_at: datetime
    counterparty_name: str
    items: list[ShipmentItemData] = field(default_factory=list)

    @property
    def total_sale_cents(self) -> int:
        return sum(item.total_sale_cents for item in self.items)

    @property
    def total_purchase_cents(self) -> int:
        return sum(item.total_purchase_cents for item in self.items)

    @property
    def total_profit_cents(self) -> int:
        return self.total_sale_cents - self.total_purchase_cents


@dataclass(slots=True)
class ReceiptItemData:
    line_number: int
    product_name: str
    product_code: int
    quantity: Decimal
    purchase_price_cents: int

    @property
    def total_purchase_cents(self) -> int:
        return int(self.quantity * self.purchase_price_cents)


@dataclass(slots=True)
class ReceiptPdfData:
    doc_number: int
    created_at: datetime
    counterparty_name: str
    items: list[ReceiptItemData] = field(default_factory=list)

    @property
    def total_purchase_cents(self) -> int:
        return sum(item.total_purchase_cents for item in self.items)


def _format_money(cents: int) -> str:
    rub = cents // 100
    kop = abs(cents) % 100
    return f"{rub},{kop:02d} ₽"


def format_money_numeric(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    rub = abs(cents) // 100
    kop = abs(cents) % 100
    return f"{sign}{rub:,}".replace(",", " ") + f",{kop:02d}"


def _choose_plural(value: int, forms: tuple[str, str, str]) -> str:
    value = abs(value) % 100
    if 10 < value < 20:
        return forms[2]
    value %= 10
    if value == 1:
        return forms[0]
    if 2 <= value <= 4:
        return forms[1]
    return forms[2]


def money_to_words(cents: int) -> str:
    sign = "минус " if cents < 0 else ""
    cents_abs = abs(cents)
    rub = cents_abs // 100
    kop = cents_abs % 100

    rub_words = num2words(rub, lang="ru") if rub else "ноль"
    kop_words = num2words(kop, lang="ru", gender="feminine") if kop else "ноль"

    phrase = (
        f"{sign}{rub_words} {_choose_plural(rub, ('рубль', 'рубля', 'рублей'))} "
        f"{kop_words} {_choose_plural(kop, ('копейка', 'копейки', 'копеек'))}"
    )
    return phrase[:1].upper() + phrase[1:]


def format_quantity(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_shipment_pdf(data: ShipmentPdfData) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont(FONT_NAME, 14)
    c.drawString(50, y, "Документ отгрузки")
    y -= 25

    c.setFont(FONT_NAME, 11)
    c.drawString(50, y, f"№: {data.doc_number}")
    y -= 18
    c.drawString(50, y, f"Дата: {data.created_at.strftime('%d.%m.%Y %H:%М')}")
    y -= 18
    c.drawString(50, y, f"Контрагент: {data.counterparty_name}")
    y -= 28

    total_sale = data.total_sale_cents
    total_profit = data.total_profit_cents
    line_spacing = 16

    for item in data.items:
        c.drawString(50, y, f"{item.line_number}. {item.product_name} (код {item.product_code})")
        y -= line_spacing
        c.drawString(
            50,
            y,
            f"    Кол-во: {format_quantity(item.quantity)} | Цена: {_format_money(item.sale_price_cents)} | Сумма: {_format_money(item.total_sale_cents)}",
        )
        y -= line_spacing

        if y < 100:
            c.showPage()
            y = height - 50
            c.setFont(FONT_NAME, 11)

    total_sale = data.total_sale_cents
    total_profit = data.total_profit_cents

    y -= line_spacing
    c.drawString(50, y, f"Итого продажа: {_format_money(total_sale)}")
    y -= line_spacing
    c.drawString(50, y, f"Итого прибыль: {_format_money(total_profit)}")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


def build_receipt_pdf(data: ReceiptPdfData) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont(FONT_NAME, 14)
    c.drawString(50, y, "Документ прихода")
    y -= 25

    c.setFont(FONT_NAME, 11)
    c.drawString(50, y, f"№: {data.doc_number}")
    y -= 18
    c.drawString(50, y, f"Дата: {data.created_at.strftime('%d.%m.%Y %H:%М')}")
    y -= 18
    c.drawString(50, y, f"Контрагент: {data.counterparty_name}")
    y -= 28

    total_purchase = data.total_purchase_cents
    line_spacing = 16

    for item in data.items:
        c.drawString(50, y, f"{item.line_number}. {item.product_name} (код {item.product_code})")
        y -= line_spacing
        c.drawString(
            50,
            y,
            f"    Кол-во: {format_quantity(item.quantity)} | Цена: {_format_money(item.purchase_price_cents)} | Сумма: {_format_money(item.total_purchase_cents)}",
        )
        y -= line_spacing

        if y < 100:
            c.showPage()
            y = height - 50
            c.setFont(FONT_NAME, 11)

    y -= line_spacing
    c.drawString(50, y, f"Итого закупка: {_format_money(total_purchase)}")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


def fill_template_pdf(template_path: Path, replacements: dict[str, str]) -> bytes:
    html = template_path.read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    pdf_buffer = BytesIO()
    HTML(string=html, base_url=str(template_path.parent)).write_pdf(
        pdf_buffer,
        stylesheets=[
            CSS(
                string=(
                    "@page { size: A4; margin: 20mm; } "
                    f"body {{ font-family: '{FONT_NAME}', sans-serif; }}"
                )
            )
        ],
    )

    pdf_buffer.seek(0)
    return pdf_buffer.read()


def _build_form_pdf(template: Path, replacements: dict[str, str], rows: list[str]) -> bytes:
    replacements["[ItemsRows]"] = "".join(rows)

    if not template.exists():
        raise FileNotFoundError(f"Template not found at {template}")

    return fill_template_pdf(template, replacements)


def build_shipment_form_pdf(data: ShipmentPdfData) -> bytes:
    replacements = {
        "[Number]": str(data.doc_number),
        "[Data]": data.created_at.strftime("%d.%m.%Y"),
        "[Kontr]": data.counterparty_name,
        "[Sum]": format_money_numeric(data.total_sale_cents),
        "[SumPr]": money_to_words(data.total_sale_cents),
        "[ItemsCount]": str(len(data.items)),
    }

    rows = [
        (
            "<tr>"
            f"<td>{item.line_number}</td>"
            f"<td>{item.product_name}</td>"
            f"<td>кг</td>"
            f"<td>{format_money_numeric(item.sale_price_cents)}</td>"
            f"<td>{format_quantity(item.quantity)}</td>"
            f"<td>{format_money_numeric(item.total_sale_cents)}</td>"
            "</tr>"
        )
        for item in data.items
    ]

    return _build_form_pdf(TEMPLATE_RASHOD_HTML, replacements, rows)


def build_receipt_form_pdf(data: ReceiptPdfData) -> bytes:
    replacements = {
        "[Number]": str(data.doc_number),
        "[Data]": data.created_at.strftime("%d.%m.%Y"),
        "[Kontr]": data.counterparty_name,
        "[Sum]": format_money_numeric(data.total_purchase_cents),
        "[SumPr]": money_to_words(data.total_purchase_cents),
        "[ItemsCount]": str(len(data.items)),
    }

    rows = [
        (
            "<tr>"
            f"<td>{item.line_number}</td>"
            f"<td>{item.product_name}</td>"
            f"<td>кг</td>"
            f"<td>{format_money_numeric(item.purchase_price_cents)}</td>"
            f"<td>{format_quantity(item.quantity)}</td>"
            f"<td>{format_money_numeric(item.total_purchase_cents)}</td>"
            "</tr>"
        )
        for item in data.items
    ]

    return _build_form_pdf(TEMPLATE_PRIHOD_HTML, replacements, rows)


