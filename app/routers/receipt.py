from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from sqlalchemy import select
from sqlalchemy.sql import func, desc

from ..db import AsyncSessionLocal
from ..keyboards import list_buttons, main_menu_kb
from ..models import Counterparty, Product, Receipt, ReceiptItem, Constants
from ..services.pdf import (
    ReceiptItemData,
    ReceiptPdfData,
    build_receipt_form_pdf,
    build_receipt_pdf,
    format_quantity,
)


router = Router(name="receipt")


class ReceiptStates(StatesGroup):
    waiting_counterparty = State()
    waiting_product = State()
    waiting_qty = State()
    waiting_price = State()
    waiting_new_price = State()
    confirming_add_more = State()
    stock_insufficient = State()


@router.callback_query(F.data == "start_receipt")
async def start_receipt(call: CallbackQuery, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        counterparties = (
            await session.execute(select(Counterparty).order_by(Counterparty.name))
        ).scalars().all()

    items = [(c.name, f"rcp_cp:{c.id}") for c in counterparties]
    await state.set_state(ReceiptStates.waiting_counterparty)

    try:
        await call.message.edit_text(
            "Выберите поставщика:",
            reply_markup=list_buttons(items, columns=1, back="main"),
        )
    except TelegramBadRequest:
        await call.answer("Меню уже отображается", show_alert=False)
        return

    await call.answer()


@router.callback_query(F.data.startswith("rcp_cp:"))
async def chosen_counterparty(call: CallbackQuery, state: FSMContext) -> None:
    counterparty_id = int(call.data.split(":", 1)[1])
    await state.update_data(counterparty_id=counterparty_id, items=[])

    async with AsyncSessionLocal() as session:
        products = (
            await session.execute(select(Product).order_by(Product.name))
        ).scalars().all()

    items = [(p.name, f"rcp_p:{p.id}") for p in products]
    await state.set_state(ReceiptStates.waiting_product)

    try:
        await call.message.edit_text(
            "Выберите товар:",
            reply_markup=list_buttons(items, columns=1, back="main"),
        )
    except TelegramBadRequest:
        await call.answer("Меню уже отображается", show_alert=False)
        return

    await call.answer()


@router.callback_query(F.data.startswith("rcp_p:"))
async def chosen_product(call: CallbackQuery, state: FSMContext) -> None:
    product_id = int(call.data.split(":", 1)[1])
    await state.update_data(current_product_id=product_id)
    await state.set_state(ReceiptStates.waiting_qty)

    await call.message.edit_text("Укажите количество (можно с точкой):", reply_markup=None)
    await call.answer()


@router.message(ReceiptStates.waiting_qty)
async def input_quantity(message: Message, state: FSMContext) -> None:
    text = (message.text or "").replace(",", ".").strip()

    try:
        quantity = Decimal(text)
        if quantity <= 0:
            raise ValueError
    except Exception:
        await message.answer("Некорректное количество. Введите положительное число.")
        return

    # Проверяем остатки
    data = await state.get_data()
    product_id = int(data["current_product_id"])  # type: ignore[index]
    
    # Импортируем функцию проверки остатков
    from ..routers.reports import get_product_stock
    
    current_stock = await get_product_stock(product_id)
    
    if quantity > current_stock:
        # Недостаточно остатков
        await state.update_data(requested_quantity=str(quantity))
        await state.set_state(ReceiptStates.stock_insufficient)
        
        buttons = [
            ("Указать другое число", "rcp_enter_different_qty"),
        ]
        
        # Добавляем кнопку "отпустить {остаток}" если остаток больше 0
        if current_stock > 0:
            buttons.append((f"Отпустить {format_quantity(current_stock)}", f"rcp_use_stock_qty:{current_stock}"))
        
        buttons.append(("⬅️ Назад", "rcp_back_to_product"))
        
        await message.answer(
            f"Недостаточно остатков!\n"
            f"Запрошено: {format_quantity(quantity)}\n"
            f"Доступно: {format_quantity(current_stock)}\n\n"
            f"Выберите действие:",
            reply_markup=list_buttons(buttons, columns=1)
        )
        return

    # Остатков достаточно, продолжаем как обычно
    await state.update_data(current_quantity=str(quantity))
    
    # Получаем прошлую цену для этого товара и контрагента
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]
    
    async with AsyncSessionLocal() as session:
        last_price = (
            await session.execute(
                select(ReceiptItem.purchase_price_cents)
                .select_from(Receipt)
                .join(Receipt.items)
                .where(
                    Receipt.counterparty_id == counterparty_id,
                    ReceiptItem.product_id == product_id,
                )
                .order_by(desc(Receipt.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        
        if last_price is not None:
            last_price_rub = last_price / 100
            price_text = f"Последняя закупочная цена: {last_price_rub:.2f} ₽"
            buttons = [
                ("Использовать прошлую цену", f"rcp_use_last_price:{last_price}"),
                ("Ввести новую цену", "rcp_enter_new_price"),
            ]
        else:
            product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one()
            default_price_cents = product.purchase_price_cents
            default_price_rub = default_price_cents / 100
            price_text = f"Закупочная цена: {default_price_rub:.2f} ₽"
            buttons = [
                ("Использовать закупочную цену", f"rcp_use_last_price:{default_price_cents}"),
                ("Ввести новую цену", "rcp_enter_new_price"),
            ]
    
    await state.set_state(ReceiptStates.waiting_price)
    await message.answer(
        f"{price_text}\n\nВыберите действие:",
        reply_markup=list_buttons(buttons, columns=1, back="rcp_back_to_product")
    )


@router.callback_query(ReceiptStates.waiting_price, F.data.startswith("rcp_use_last_price:"))
async def rcp_use_last_price(call: CallbackQuery, state: FSMContext):
    price_cents = int(call.data.split(":", 1)[1])
    await rcp_process_price_selection(call, state, price_cents)

@router.callback_query(ReceiptStates.waiting_price, F.data == "rcp_enter_new_price")
async def rcp_enter_new_price(call: CallbackQuery, state: FSMContext):
    await state.set_state(ReceiptStates.waiting_new_price)
    await call.message.edit_text("Введите новую закупочную цену в рублях (можно с точкой):")
    await call.answer()


@router.callback_query(ReceiptStates.waiting_price, F.data == "rcp_back_to_product")
async def rcp_back_to_product_from_price(call: CallbackQuery, state: FSMContext):
    # Возвращаемся к выбору товара
    await state.set_state(ReceiptStates.waiting_product)
    
    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()
    
    if not products:
        await call.message.edit_text("Нет товаров в базе.", reply_markup=main_menu_kb())
        await call.answer()
        return
    
    buttons = [(f"{p.code} {p.name}", f"rcp_p:{p.id}") for p in products]
    buttons.append(("🏠 Главное меню", "main"))
    
    await call.message.edit_text(
        "Выберите товар:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()

@router.message(ReceiptStates.waiting_new_price)
async def rcp_input_new_price(message: Message, state: FSMContext):
    raw_price = (message.text or "").strip().replace(",", ".")
    try:
        price_rub = float(raw_price)
        if price_rub < 0:
            raise ValueError
        price_cents = int(price_rub * 100)
    except Exception:
        await message.answer("Некорректная цена. Введите число в рублях (можно с точкой).")
        return
    
    await rcp_process_price_selection(message, state, price_cents)

async def rcp_process_price_selection(message_or_call, state: FSMContext, price_cents: int):
    data = await state.get_data()
    product_id = int(data["current_product_id"])  # type: ignore[index]
    quantity = Decimal(data["current_quantity"])  # type: ignore[index]
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]

    # Импортируем функцию расчета средней закупочной цены
    from ..routers.reports import get_average_purchase_price

    async with AsyncSessionLocal() as session:
        product = (
            await session.execute(select(Product).where(Product.id == product_id))
        ).scalar_one()
        
        # Получаем среднюю закупочную цену по продажам
        average_purchase_price = await get_average_purchase_price(product_id)

        item = ReceiptItemData(
            line_number=len(data.get("items", [])) + 1,  # type: ignore[arg-type]
            product_name=product.name,
            product_code=product.code,
            quantity=quantity,
            purchase_price_cents=average_purchase_price,
        )

    items = data.get("items", [])  # type: ignore[assignment]
    items.append(
        {
            "line_number": item.line_number,
            "product_id": product_id,
            "product_name": item.product_name,
            "product_code": item.product_code,
            "quantity": str(item.quantity),
            "purchase_price_cents": item.purchase_price_cents,
        }
    )

    await state.update_data(items=items)
    await state.set_state(ReceiptStates.confirming_add_more)
    
    # Определяем, что это за объект - message или call
    if hasattr(message_or_call, 'answer'):
        await message_or_call.answer(
            "Товар добавлен. Добавить ещё?",
            reply_markup=list_buttons(
                [
                    ("Добавить ещё", "rcp_add_more"),
                    ("Завершить", "rcp_finish"),
                    ("⬅️ Назад", "rcp_back_to_product"),
                ],
                columns=1,
            ),
        )
    else:
        await message_or_call.message.edit_text(
            "Товар добавлен. Добавить ещё?",
            reply_markup=list_buttons(
                [
                    ("Добавить ещё", "rcp_add_more"),
                    ("Завершить", "rcp_finish"),
                    ("⬅️ Назад", "rcp_back_to_product"),
                ],
                columns=1,
            ),
        )
        await message_or_call.answer()


@router.callback_query(ReceiptStates.confirming_add_more, F.data == "rcp_add_more")
async def add_more_items(call: CallbackQuery, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        products = (
            await session.execute(select(Product).order_by(Product.name))
        ).scalars().all()

    items = [(p.name, f"rcp_p:{p.id}") for p in products]

    await state.set_state(ReceiptStates.waiting_product)

    try:
        await call.message.edit_text(
            "Выберите товар:",
            reply_markup=list_buttons(items, columns=1, back="rcp_finish"),
        )
    except TelegramBadRequest:
        await call.answer("Меню уже отображается", show_alert=False)
        return

    await call.answer()


@router.callback_query(ReceiptStates.confirming_add_more, F.data == "rcp_back_to_product")
async def rcp_back_to_product_from_confirm(call: CallbackQuery, state: FSMContext):
    # Возвращаемся к выбору товара
    await state.set_state(ReceiptStates.waiting_product)
    
    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()
    
    if not products:
        await call.message.edit_text("Нет товаров в базе.", reply_markup=main_menu_kb())
        await call.answer()
        return
    
    buttons = [(f"{p.code} {p.name}", f"rcp_p:{p.id}") for p in products]
    buttons.append(("🏠 Главное меню", "main"))
    
    await call.message.edit_text(
        "Выберите товар:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()


@router.callback_query(ReceiptStates.confirming_add_more, F.data == "rcp_finish")
async def finish_receipt(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]
    items_data = data.get("items", [])

    if not items_data:
        await call.answer("Нет добавленных товаров", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        counterparty = (
            await session.execute(select(Counterparty).where(Counterparty.id == counterparty_id))
        ).scalar_one()

        # Получаем номер из констант или из последнего документа
        const_number = (
            await session.execute(
                select(Constants.value).where(Constants.key == "last_receipt_number")
            )
        ).scalar_one_or_none()
        
        if const_number:
            doc_number = int(const_number) + 1
        else:
            last_doc = (
                await session.execute(
                    select(func.max(Receipt.doc_number))
                )
            ).scalar_one()
            doc_number = (last_doc or 0) + 1

        receipt = Receipt(doc_number=doc_number, counterparty_id=counterparty_id)
        session.add(receipt)
        await session.flush()

        created_items: list[ReceiptItem] = []

        for stored_item in items_data:
            product = (
                await session.execute(select(Product).where(Product.id == stored_item["product_id"]))
            ).scalar_one()

            receipt_item = ReceiptItem(
                receipt_id=receipt.id,
                product_id=product.id,
                line_number=stored_item["line_number"],
                product_name=stored_item["product_name"],
                product_code=stored_item["product_code"],
                quantity=Decimal(stored_item["quantity"]),
                purchase_price_cents=stored_item["purchase_price_cents"],
            )

            session.add(receipt_item)
            created_items.append(receipt_item)

            product.purchase_price_cents = stored_item["purchase_price_cents"]

        await session.commit()
        await session.refresh(receipt)

        # Обновляем константу с новым номером документа
        existing_const = (
            await session.execute(
                select(Constants).where(Constants.key == "last_receipt_number")
            )
        ).scalar_one_or_none()
        
        if existing_const:
            existing_const.value = str(doc_number)
        else:
            session.add(Constants(key="last_receipt_number", value=str(doc_number)))
        
        await session.commit()

    await call.message.edit_reply_markup(None)

    pdf_items = [
        ReceiptItemData(
            line_number=item.line_number,
            product_name=item.product_name,
            product_code=item.product_code,
            quantity=item.quantity,
            purchase_price_cents=item.purchase_price_cents,
        )
        for item in created_items
    ]

    pdf_data = ReceiptPdfData(
        doc_number=receipt.doc_number,
        created_at=receipt.created_at,
        counterparty_name=counterparty.name,
        items=pdf_items,
    )

    form_pdf = build_receipt_form_pdf(pdf_data)

    total_purchase = pdf_data.total_purchase_cents

    await call.message.answer_document(
        document=BufferedInputFile(form_pdf, filename="prihodnaya.pdf"),
        caption="Приходная накладная",
    )

    await call.message.answer(
        f"Приход оформлен. Сумма закупки: {total_purchase // 100},{total_purchase % 100:02d} ₽",
        reply_markup=main_menu_kb(),
    )

    await state.clear()
    await call.answer()


@router.callback_query(ReceiptStates.stock_insufficient, F.data == "rcp_enter_different_qty")
async def rcp_enter_different_qty(call: CallbackQuery, state: FSMContext):
    await state.set_state(ReceiptStates.waiting_qty)
    await call.message.edit_text("Укажите количество (можно с точкой):", reply_markup=None)
    await call.answer()


@router.callback_query(ReceiptStates.stock_insufficient, F.data.startswith("rcp_use_stock_qty:"))
async def rcp_use_stock_qty(call: CallbackQuery, state: FSMContext):
    stock_qty = Decimal(call.data.split(":", 1)[1])
    await state.update_data(current_quantity=str(stock_qty))
    
    # Получаем данные для продолжения с ценой
    data = await state.get_data()
    product_id = int(data["current_product_id"])  # type: ignore[index]
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]
    
    async with AsyncSessionLocal() as session:
        last_price = (
            await session.execute(
                select(ReceiptItem.purchase_price_cents)
                .select_from(Receipt)
                .join(Receipt.items)
                .where(
                    Receipt.counterparty_id == counterparty_id,
                    ReceiptItem.product_id == product_id,
                )
                .order_by(desc(Receipt.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        
        if last_price is not None:
            last_price_rub = last_price / 100
            price_text = f"Последняя закупочная цена: {last_price_rub:.2f} ₽"
            buttons = [
                ("Использовать прошлую цену", f"rcp_use_last_price:{last_price}"),
                ("Ввести новую цену", "rcp_enter_new_price"),
            ]
        else:
            product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one()
            default_price_cents = product.purchase_price_cents
            default_price_rub = default_price_cents / 100
            price_text = f"Закупочная цена: {default_price_rub:.2f} ₽"
            buttons = [
                ("Использовать закупочную цену", f"rcp_use_last_price:{default_price_cents}"),
                ("Ввести новую цену", "rcp_enter_new_price"),
            ]
    
    await state.set_state(ReceiptStates.waiting_price)
    await call.message.edit_text(
        f"{price_text}\n\nВыберите действие:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()


@router.callback_query(ReceiptStates.stock_insufficient, F.data == "rcp_back_to_product")
async def rcp_back_to_product(call: CallbackQuery, state: FSMContext):
    # Возвращаемся к выбору товара
    await state.set_state(ReceiptStates.waiting_product)
    
    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()
    
    if not products:
        await call.message.edit_text("Нет товаров в базе.", reply_markup=main_menu_kb())
        await call.answer()
        return
    
    buttons = [(f"{p.code} {p.name}", f"rcp_p:{p.id}") for p in products]
    buttons.append(("🏠 Главное меню", "main"))
    
    await call.message.edit_text(
        "Выберите товар:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()


