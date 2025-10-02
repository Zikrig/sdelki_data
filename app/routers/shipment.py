from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..keyboards import list_buttons, main_menu_kb
from ..models import Counterparty, Product, Shipment, ShipmentItem, Constants
from ..services.pdf import ShipmentPdfData, ShipmentItemData, build_shipment_pdf, build_shipment_form_pdf
from ..services.pdf import format_quantity


router = Router(name="shipment")


class ShipmentStates(StatesGroup):
    waiting_counterparty = State()
    waiting_product = State()
    waiting_qty = State()
    waiting_price = State()
    waiting_new_price = State()
    confirming_add_more = State()
    stock_insufficient = State()


@router.callback_query(F.data == "start_shipment")
async def start_shipment(call: CallbackQuery, state: FSMContext):
    async with AsyncSessionLocal() as session:
        cps = (await session.execute(select(Counterparty).order_by(Counterparty.name))).scalars().all()
    items = [(c.name, f"cp:{c.id}") for c in cps]
    await state.set_state(ShipmentStates.waiting_counterparty)
    try:
        await call.message.edit_text(
            "Выберите контрагента:",
            reply_markup=list_buttons(items, columns=1, back="main"),
        )
    except TelegramBadRequest:
        await call.answer("Меню уже отображается", show_alert=False)
        return
    await call.answer()


@router.callback_query(F.data == "main")
async def back_main(call: CallbackQuery):
    try:
        await call.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    except TelegramBadRequest:
        await call.answer()
        return
    await call.answer()


@router.callback_query(F.data.startswith("cp:"))
async def chosen_counterparty(call: CallbackQuery, state: FSMContext):
    cp_id = int(call.data.split(":", 1)[1])
    await state.update_data(counterparty_id=cp_id, items=[])

    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()

    items = [(p.name, f"p:{p.id}") for p in products]
    await state.set_state(ShipmentStates.waiting_product)
    try:
        await call.message.edit_text(
            "Выберите товар:",
            reply_markup=list_buttons(items, columns=1, back="main"),
        )
    except TelegramBadRequest:
        await call.answer("Меню уже отображается", show_alert=False)
        return
    await call.answer()


@router.callback_query(F.data.startswith("p:"))
async def chosen_product(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split(":", 1)[1])
    await state.update_data(current_product_id=product_id)
    await state.set_state(ShipmentStates.waiting_qty)
    await call.message.edit_text("Укажите количество (число, можно с точкой):", reply_markup=None)
    await call.answer()


@router.message(ShipmentStates.waiting_qty)
async def input_quantity(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", ".").strip()
    try:
        qty = Decimal(text)
        if qty <= 0:
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
    
    if qty > current_stock:
        # Недостаточно остатков
        await state.update_data(requested_quantity=str(qty))
        await state.set_state(ShipmentStates.stock_insufficient)
        
        buttons = [
            ("Указать другое число", "enter_different_qty"),
        ]
        
        # Добавляем кнопку "отпустить {остаток}" если остаток больше 0
        if current_stock > 0:
            buttons.append((f"Отпустить {format_quantity(current_stock)}", f"use_stock_qty:{current_stock}"))
        
        buttons.append(("⬅️ Назад", "back_to_product"))
        
        await message.answer(
            f"Недостаточно остатков!\n"
            f"Запрошено: {format_quantity(qty)}\n"
            f"Доступно: {format_quantity(current_stock)}\n\n"
            f"Выберите действие:",
            reply_markup=list_buttons(buttons, columns=1)
        )
        return
    
    # Остатков достаточно, продолжаем как обычно
    await state.update_data(current_quantity=str(qty))
    
    # Получаем прошлую цену для этого товара и контрагента
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]
    
    async with AsyncSessionLocal() as session:
        from sqlalchemy import desc
        # Получаем последнюю цену продажи напрямую из запроса
        last_price_result = (
            await session.execute(
                select(ShipmentItem.sale_price_cents)
                .join(Shipment)
                .where(
                    Shipment.counterparty_id == counterparty_id, 
                    ShipmentItem.product_id == product_id
                )
                .order_by(desc(Shipment.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        
        if last_price_result:
            last_price_rub = last_price_result / 100
            price_text = f"Последняя цена: {last_price_rub:.2f} ₽"
            buttons = [
                ("Использовать прошлую цену", f"use_last_price:{last_price_result}"),
                ("Ввести новую цену", "enter_new_price"),
            ]
        else:
            product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one()
            default_price_cents = product.retail_price_cents
            default_price_rub = default_price_cents / 100
            price_text = f"Розничная цена: {default_price_rub:.2f} ₽"
            buttons = [
                ("Использовать розничную цену", f"use_last_price:{default_price_cents}"),
                ("Ввести новую цену", "enter_new_price"),
            ]
    
    await state.set_state(ShipmentStates.waiting_price)
    await message.answer(
        f"{price_text}\n\nВыберите действие:",
        reply_markup=list_buttons(buttons, columns=1, back="back_to_product")
    )


@router.callback_query(ShipmentStates.waiting_price, F.data.startswith("use_last_price:"))
async def use_last_price(call: CallbackQuery, state: FSMContext):
    price_cents = int(call.data.split(":", 1)[1])
    await process_price_selection(call, state, price_cents)

@router.callback_query(ShipmentStates.waiting_price, F.data == "enter_new_price")
async def enter_new_price(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShipmentStates.waiting_new_price)
    await call.message.edit_text("Введите новую цену в рублях (можно с точкой):")
    await call.answer()


@router.callback_query(ShipmentStates.waiting_price, F.data == "back_to_product")
async def back_to_product_from_price(call: CallbackQuery, state: FSMContext):
    # Возвращаемся к выбору товара
    await state.set_state(ShipmentStates.waiting_product)
    
    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()
    
    if not products:
        await call.message.edit_text("Нет товаров в базе.", reply_markup=main_menu_kb())
        await call.answer()
        return
    
    buttons = [(f"{p.code} {p.name}", f"p:{p.id}") for p in products]
    buttons.append(("🏠 Главное меню", "main"))
    
    await call.message.edit_text(
        "Выберите товар:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()

@router.message(ShipmentStates.waiting_new_price)
async def input_new_price(message: Message, state: FSMContext):
    raw_price = (message.text or "").strip().replace(",", ".")
    try:
        price_rub = float(raw_price)
        if price_rub < 0:
            raise ValueError
        price_cents = int(price_rub * 100)
    except Exception:
        await message.answer("Некорректная цена. Введите число в рублях (можно с точкой).")
        return
    
    await process_price_selection(message, state, price_cents)

async def process_price_selection(message_or_call, state: FSMContext, price_cents: int):
    data = await state.get_data()
    product_id = int(data["current_product_id"])  # type: ignore[index]
    quantity = Decimal(data["current_quantity"])  # type: ignore[index]
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]

    # Импортируем функцию расчета средней закупочной цены
    from ..routers.reports import get_average_purchase_price

    async with AsyncSessionLocal() as session:
        product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one()
        
        # Получаем среднюю закупочную цену по продажам
        average_purchase_price = await get_average_purchase_price(product_id)

        item = ShipmentItemData(
            line_number=len(data.get("items", [])) + 1,  # type: ignore[arg-type]
            product_name=product.name,
            product_code=product.code,
            quantity=quantity,
            sale_price_cents=price_cents,
            purchase_price_cents=average_purchase_price,
        )

    items = data.get("items", [])  # type: ignore[assignment]
    items.append({
        "line_number": item.line_number,
        "product_id": product_id,
        "product_name": item.product_name,
        "product_code": item.product_code,
        "quantity": str(item.quantity),
        "sale_price_cents": item.sale_price_cents,
        "purchase_price_cents": item.purchase_price_cents,
    })

    await state.update_data(items=items)
    await state.set_state(ShipmentStates.confirming_add_more)
    
    # Определяем, что это за объект - message или call
    if hasattr(message_or_call, 'answer'):
        await message_or_call.answer(
            "Товар добавлен. Добавить ещё?",
            reply_markup=list_buttons([
                ("Добавить ещё", "add_more"),
                ("Завершить", "finish_shipment"),
                ("⬅️ Назад", "back_to_product"),
            ], columns=1),
        )
    else:
        await message_or_call.message.edit_text(
            "Товар добавлен. Добавить ещё?",
            reply_markup=list_buttons([
                ("Добавить ещё", "add_more"),
                ("Завершить", "finish_shipment"),
                ("⬅️ Назад", "back_to_product"),
            ], columns=1),
        )
        await message_or_call.answer()


@router.callback_query(ShipmentStates.confirming_add_more, F.data == "add_more")
async def add_more_items(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]

    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()
    items = [(p.name, f"p:{p.id}") for p in products]

    await state.set_state(ShipmentStates.waiting_product)
    try:
        await call.message.edit_text(
            "Выберите товар:",
            reply_markup=list_buttons(items, columns=1, back="finish_shipment"),
        )
    except TelegramBadRequest:
        await call.answer("Меню уже отображается", show_alert=False)
        return
    await call.answer()


@router.callback_query(ShipmentStates.confirming_add_more, F.data == "back_to_product")
async def back_to_product_from_confirm(call: CallbackQuery, state: FSMContext):
    # Возвращаемся к выбору товара
    await state.set_state(ShipmentStates.waiting_product)
    
    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()
    
    if not products:
        await call.message.edit_text("Нет товаров в базе.", reply_markup=main_menu_kb())
        await call.answer()
        return
    
    buttons = [(f"{p.code} {p.name}", f"p:{p.id}") for p in products]
    buttons.append(("🏠 Главное меню", "main"))
    
    await call.message.edit_text(
        "Выберите товар:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()


@router.callback_query(ShipmentStates.confirming_add_more, F.data == "finish_shipment")
async def finish_shipment(call: CallbackQuery, state: FSMContext):
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
                select(Constants.value).where(Constants.key == "last_shipment_number")
            )
        ).scalar_one_or_none()
        
        if const_number:
            doc_number = int(const_number) + 1
        else:
            last_doc = (
                await session.execute(
                    select(Shipment.doc_number).order_by(Shipment.doc_number.desc()).limit(1)
                )
            ).scalar_one_or_none()
            doc_number = (last_doc or 0) + 1

        shipment = Shipment(doc_number=doc_number, counterparty_id=counterparty_id)
        session.add(shipment)
        await session.flush()

        shipment_items = []
        for stored_item in items_data:
            product = (
                await session.execute(select(Product).where(Product.id == stored_item["product_id"]))
            ).scalar_one()
            shipment_item = ShipmentItem(
                shipment_id=shipment.id,
                product_id=product.id,
                line_number=stored_item["line_number"],
                product_name=stored_item["product_name"],
                product_code=stored_item["product_code"],
                quantity=Decimal(stored_item["quantity"]),
                sale_price_cents=stored_item["sale_price_cents"],
                purchase_price_cents=stored_item["purchase_price_cents"],
            )
            session.add(shipment_item)
            shipment_items.append(shipment_item)

        await session.commit()
        await session.refresh(shipment)

        # Обновляем константу с новым номером документа
        await session.execute(
            select(Constants).where(Constants.key == "last_shipment_number")
        )
        existing_const = (
            await session.execute(
                select(Constants).where(Constants.key == "last_shipment_number")
            )
        ).scalar_one_or_none()
        
        if existing_const:
            existing_const.value = str(doc_number)
        else:
            session.add(Constants(key="last_shipment_number", value=str(doc_number)))
        
        await session.commit()

        await call.message.edit_reply_markup(None)

    pdf_items = [
        ShipmentItemData(
            line_number=item.line_number,
            product_name=item.product_name,
            product_code=item.product_code,
            quantity=item.quantity,
            sale_price_cents=item.sale_price_cents,
            purchase_price_cents=item.purchase_price_cents,
        )
        for item in shipment_items
    ]

    pdf_data = ShipmentPdfData(
        doc_number=shipment.doc_number,
        created_at=shipment.created_at,
        counterparty_name=counterparty.name,
        items=pdf_items,
    )

    summary_pdf = build_shipment_pdf(pdf_data)
    form_pdf = build_shipment_form_pdf(pdf_data)

    profit = pdf_data.total_profit_cents

    await call.message.answer_document(
        document=BufferedInputFile(form_pdf, filename="rashodnaya.pdf"),
        caption="Расходная накладная",
    )

    await call.message.answer(
        f"Отгрузка оформлена. Прибыль: {profit // 100},{profit % 100:02d} ₽",
    )

    await state.clear()
    await call.answer()


@router.callback_query(ShipmentStates.stock_insufficient, F.data == "enter_different_qty")
async def enter_different_qty(call: CallbackQuery, state: FSMContext):
    await state.set_state(ShipmentStates.waiting_qty)
    await call.message.edit_text("Укажите количество (число, можно с точкой):", reply_markup=None)
    await call.answer()


@router.callback_query(ShipmentStates.stock_insufficient, F.data.startswith("use_stock_qty:"))
async def use_stock_qty(call: CallbackQuery, state: FSMContext):
    stock_qty = Decimal(call.data.split(":", 1)[1])
    await state.update_data(current_quantity=str(stock_qty))
    
    # Получаем данные для продолжения с ценой
    data = await state.get_data()
    product_id = int(data["current_product_id"])  # type: ignore[index]
    counterparty_id = int(data["counterparty_id"])  # type: ignore[index]
    
    async with AsyncSessionLocal() as session:
        from sqlalchemy import desc
        # Получаем последнюю цену продажи напрямую из запроса
        last_price_result = (
            await session.execute(
                select(ShipmentItem.sale_price_cents)
                .join(Shipment)
                .where(
                    Shipment.counterparty_id == counterparty_id, 
                    ShipmentItem.product_id == product_id
                )
                .order_by(desc(Shipment.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        
        if last_price_result:
            last_price_rub = last_price_result / 100
            price_text = f"Последняя цена: {last_price_rub:.2f} ₽"
            buttons = [
                ("Использовать прошлую цену", f"use_last_price:{last_price_result}"),
                ("Ввести новую цену", "enter_new_price"),
            ]
        else:
            product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one()
            default_price_cents = product.retail_price_cents
            default_price_rub = default_price_cents / 100
            price_text = f"Розничная цена: {default_price_rub:.2f} ₽"
            buttons = [
                ("Использовать розничную цену", f"use_last_price:{default_price_cents}"),
                ("Ввести новую цену", "enter_new_price"),
            ]
    
    await state.set_state(ShipmentStates.waiting_price)
    await call.message.edit_text(
        f"{price_text}\n\nВыберите действие:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()


@router.callback_query(ShipmentStates.stock_insufficient, F.data == "back_to_product")
async def back_to_product(call: CallbackQuery, state: FSMContext):
    # Возвращаемся к выбору товара
    await state.set_state(ShipmentStates.waiting_product)
    
    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.name))).scalars().all()
    
    if not products:
        await call.message.edit_text("Нет товаров в базе.", reply_markup=main_menu_kb())
        await call.answer()
        return
    
    buttons = [(f"{p.code} {p.name}", f"p:{p.id}") for p in products]
    buttons.append(("🏠 Главное меню", "main"))
    
    await call.message.edit_text(
        "Выберите товар:",
        reply_markup=list_buttons(buttons, columns=1)
    )
    await call.answer()


