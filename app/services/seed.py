from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Counterparty, Product


COUNTERPARTIES = [
    "Абил Вешки",
    "АЙК",
    "Ариф",
    "Вектор",
    "Витя камри",
    "ВЛДВ",
    "Вова Снек",
]


PRODUCTS = [
    # code, name, retail_price_cents, purchase_price_cents
    (23, "Лосось 6-7", 131150, 130000),
    (40, "Форель радужная 1.8-2.7", 99500, 96018),
    (49, "Навага М", 0, 0),
]


async def seed_initial_data(session: AsyncSession) -> None:
    # Seed counterparties
    existing_cp = (await session.execute(select(Counterparty))).scalars().all()
    if not existing_cp:
        session.add_all([Counterparty(name=name) for name in COUNTERPARTIES])

    existing_products = (await session.execute(select(Product))).scalars().all()
    if not existing_products:
        session.add_all(
            [
                Product(
                    code=code,
                    name=name,
                    retail_price_cents=retail,
                    purchase_price_cents=purchase,
                )
                for code, name, retail, purchase in PRODUCTS
            ]
        )

    await session.commit()


