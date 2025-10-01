from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Constants(Base):
    __tablename__ = "constants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(255))


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    shipments: Mapped[list[Shipment]] = relationship(back_populates="counterparty")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="counterparty")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    retail_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    purchase_price_cents: Mapped[int] = mapped_column(Integer, default=0)

    items: Mapped[list["ShipmentItem"]] = relationship(back_populates="product")
    receipt_items: Mapped[list["ReceiptItem"]] = relationship(back_populates="product")


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    doc_number: Mapped[int] = mapped_column(Integer, nullable=False)

    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"))

    counterparty: Mapped[Counterparty] = relationship(back_populates="shipments")
    items: Mapped[list["ShipmentItem"]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentItem.line_number",
    )

    @property
    def total_sale_cents(self) -> int:
        return sum(item.total_sale_cents for item in self.items)

    @property
    def total_purchase_cents(self) -> int:
        return sum(item.total_purchase_cents for item in self.items)

    @property
    def total_profit_cents(self) -> int:
        return self.total_sale_cents - self.total_purchase_cents


class ShipmentItem(Base):
    __tablename__ = "shipment_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    product_name: Mapped[str] = mapped_column(String(255))
    product_code: Mapped[int] = mapped_column(Integer)

    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    sale_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    purchase_price_cents: Mapped[int] = mapped_column(Integer, default=0)

    shipment: Mapped[Shipment] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="items")

    @property
    def total_sale_cents(self) -> int:
        return int(self.quantity * self.sale_price_cents)

    @property
    def total_purchase_cents(self) -> int:
        return int(self.quantity * self.purchase_price_cents)


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    doc_number: Mapped[int] = mapped_column(Integer, nullable=False)

    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"))

    counterparty: Mapped[Counterparty] = relationship(back_populates="receipts")
    items: Mapped[list["ReceiptItem"]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        order_by="ReceiptItem.line_number",
    )

    @property
    def total_purchase_cents(self) -> int:
        return sum(item.total_purchase_cents for item in self.items)


class ReceiptItem(Base):
    __tablename__ = "receipt_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    product_name: Mapped[str] = mapped_column(String(255))
    product_code: Mapped[int] = mapped_column(Integer)

    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    purchase_price_cents: Mapped[int] = mapped_column(Integer, default=0)

    receipt: Mapped[Receipt] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="receipt_items")

    @property
    def total_purchase_cents(self) -> int:
        return int(self.quantity * self.purchase_price_cents)


