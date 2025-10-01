from __future__ import annotations

import csv
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import StringIO

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from sqlalchemy import func, select

from ..db import AsyncSessionLocal
from ..keyboards import list_buttons, main_menu_kb
from ..models import Counterparty, Product, ReceiptItem, Shipment, ShipmentItem
from ..services.pdf import format_quantity, format_money_numeric


router = Router(name="reports")


class SalesExportStates(StatesGroup):
    waiting_start_date = State()
    waiting_end_date = State()
    viewing_shipments = State()
    selecting_period = State()
    selecting_specific_date = State()


@router.callback_query(F.data == "current_stock")
async def current_stock(call: CallbackQuery) -> None:
    receipt_subq = (
        select(
            ReceiptItem.product_id.label("product_id"),
            func.coalesce(func.sum(ReceiptItem.quantity), 0).label("total_receipt"),
        )
        .group_by(ReceiptItem.product_id)
        .subquery()
    )

    shipment_subq = (
        select(
            ShipmentItem.product_id.label("product_id"),
            func.coalesce(func.sum(ShipmentItem.quantity), 0).label("total_shipment"),
        )
        .group_by(ShipmentItem.product_id)
        .subquery()
    )

    stmt = (
        select(
            Product.code,
            Product.name,
            func.coalesce(receipt_subq.c.total_receipt, 0) - func.coalesce(shipment_subq.c.total_shipment, 0),
        )
        .select_from(Product)
        .join(receipt_subq, receipt_subq.c.product_id == Product.id, isouter=True)
        .join(shipment_subq, shipment_subq.c.product_id == Product.id, isouter=True)
        .order_by(Product.name)
    )

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt)).all()

    if not rows:
        await call.answer("Нет данных по товарам", show_alert=True)
        return

    lines = ["Текущие остатки:"]
    total_positions = 0

    for code, name, balance in rows:
        balance_decimal = Decimal(balance or 0)
        if balance_decimal == 0:
            continue
        total_positions += 1
        lines.append(
            f"• {code} {name}: {format_quantity(balance_decimal)}"
        )

    if total_positions == 0:
        lines.append("Все остатки равны нулю.")

    try:
        await call.message.edit_text("\n".join(lines), reply_markup=main_menu_kb())
    except Exception:
        await call.message.answer("\n".join(lines), reply_markup=main_menu_kb())

    await call.answer()


@router.callback_query(F.data == "export_sales")
async def export_sales_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SalesExportStates.selecting_period)
    
    buttons = [
        ("📅 Сегодня", "period_today"),
        ("📅 Эта неделя", "period_this_week"),
        ("📅 Этот месяц", "period_this_month"),
        ("📅 Выбрать период", "period_custom"),
        ("📅 Выбрать конкретный день", "period_specific_day"),
        ("🏠 Главное меню", "main")
    ]
    
    text = "Выберите период для выгрузки продаж:"
    
    try:
        await call.message.edit_text(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )
    except Exception:
        await call.message.answer(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )
    await call.answer()


def _parse_date(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        return None


def _cents_to_str(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    rub = abs(cents) // 100
    kop = abs(cents) % 100
    return f"{sign}{rub}.{kop:02d}"


def _get_today_period() -> tuple[datetime, datetime]:
    """Возвращает период для сегодняшнего дня"""
    today = datetime.now().date()
    start = datetime.combine(today, time.min)
    end = datetime.combine(today, time.max)
    return start, end


def _get_this_week_period() -> tuple[datetime, datetime]:
    """Возвращает период для текущей недели (понедельник - воскресенье)"""
    today = datetime.now().date()
    # Получаем понедельник текущей недели
    monday = today - timedelta(days=today.weekday())
    # Получаем воскресенье текущей недели
    sunday = monday + timedelta(days=6)
    
    start = datetime.combine(monday, time.min)
    end = datetime.combine(sunday, time.max)
    return start, end


def _get_this_month_period() -> tuple[datetime, datetime]:
    """Возвращает период для текущего месяца"""
    today = datetime.now().date()
    # Первое число текущего месяца
    first_day = today.replace(day=1)
    # Последнее число текущего месяца
    if today.month == 12:
        last_day = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last_day = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    
    start = datetime.combine(first_day, time.min)
    end = datetime.combine(last_day, time.max)
    return start, end


@router.callback_query(SalesExportStates.selecting_period, F.data == "period_today")
async def period_today(call: CallbackQuery, state: FSMContext):
    start_dt, end_dt = _get_today_period()
    await _process_sales_export(call, state, start_dt, end_dt)


@router.callback_query(SalesExportStates.selecting_period, F.data == "period_this_week")
async def period_this_week(call: CallbackQuery, state: FSMContext):
    start_dt, end_dt = _get_this_week_period()
    await _process_sales_export(call, state, start_dt, end_dt)


@router.callback_query(SalesExportStates.selecting_period, F.data == "period_this_month")
async def period_this_month(call: CallbackQuery, state: FSMContext):
    start_dt, end_dt = _get_this_month_period()
    await _process_sales_export(call, state, start_dt, end_dt)


@router.callback_query(SalesExportStates.selecting_period, F.data == "period_custom")
async def period_custom(call: CallbackQuery, state: FSMContext):
    await state.update_data(selecting_specific_day=False)
    await state.set_state(SalesExportStates.waiting_start_date)
    text = (
        "Введите дату начала периода в формате ДД.ММ.ГГГГ.\n"
        "Напишите 'отмена' для возврата в меню."
    )
    try:
        await call.message.edit_text(text)
    except Exception:
        await call.message.answer(text)
    await call.answer()


@router.callback_query(SalesExportStates.selecting_period, F.data == "period_specific_day")
async def period_specific_day(call: CallbackQuery, state: FSMContext):
    await state.set_state(SalesExportStates.selecting_specific_date)
    
    today = datetime.now().date()
    first_of_month = today.replace(day=1)
    first_of_year = today.replace(month=1, day=1)
    
    buttons = [
        (f"📅 Сегодня ({today.strftime('%d.%m.%Y')})", f"specific_today"),
        (f"📅 {first_of_month.strftime('%d.%m.%Y')} (первое число месяца)", f"specific_first_month"),
    ]
    
    # Добавляем первый день года только если это не 1 января
    if first_of_year != today and first_of_year != first_of_month:
        buttons.append((f"📅 {first_of_year.strftime('%d.%m.%Y')} (первый день года)", f"specific_first_year"))
    
    buttons.extend([
        ("📅 Выбрать другую дату", "specific_custom"),
        ("⬅️ Назад", "back_to_periods"),
        ("🏠 Главное меню", "main")
    ])
    
    text = "Выберите конкретный день:"
    
    try:
        await call.message.edit_text(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )
    except Exception:
        await call.message.answer(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )
    await call.answer()


@router.callback_query(SalesExportStates.selecting_period, F.data == "main")
async def back_to_main_from_periods(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    except Exception:
        await call.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await call.answer()


@router.callback_query(SalesExportStates.selecting_specific_date, F.data == "specific_today")
async def specific_today(call: CallbackQuery, state: FSMContext):
    start_dt, end_dt = _get_today_period()
    await _process_sales_export(call, state, start_dt, end_dt)


@router.callback_query(SalesExportStates.selecting_specific_date, F.data == "specific_first_month")
async def specific_first_month(call: CallbackQuery, state: FSMContext):
    today = datetime.now().date()
    first_of_month = today.replace(day=1)
    start_dt = datetime.combine(first_of_month, time.min)
    end_dt = datetime.combine(first_of_month, time.max)
    await _process_sales_export(call, state, start_dt, end_dt)


@router.callback_query(SalesExportStates.selecting_specific_date, F.data == "specific_first_year")
async def specific_first_year(call: CallbackQuery, state: FSMContext):
    today = datetime.now().date()
    first_of_year = today.replace(month=1, day=1)
    start_dt = datetime.combine(first_of_year, time.min)
    end_dt = datetime.combine(first_of_year, time.max)
    await _process_sales_export(call, state, start_dt, end_dt)


@router.callback_query(SalesExportStates.selecting_specific_date, F.data == "specific_custom")
async def specific_custom(call: CallbackQuery, state: FSMContext):
    await state.update_data(selecting_specific_day=True)
    await state.set_state(SalesExportStates.waiting_start_date)
    text = (
        "Введите дату в формате ДД.ММ.ГГГГ.\n"
        "Напишите 'отмена' для возврата в меню."
    )
    try:
        await call.message.edit_text(text)
    except Exception:
        await call.message.answer(text)
    await call.answer()


@router.callback_query(SalesExportStates.selecting_specific_date, F.data == "back_to_periods")
async def back_to_periods(call: CallbackQuery, state: FSMContext):
    await state.set_state(SalesExportStates.selecting_period)
    
    buttons = [
        ("📅 Сегодня", "period_today"),
        ("📅 Эта неделя", "period_this_week"),
        ("📅 Этот месяц", "period_this_month"),
        ("📅 Выбрать период", "period_custom"),
        ("📅 Выбрать конкретный день", "period_specific_day"),
        ("🏠 Главное меню", "main")
    ]
    
    text = "Выберите период для выгрузки продаж:"
    
    try:
        await call.message.edit_text(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )
    except Exception:
        await call.message.answer(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )
    await call.answer()


@router.callback_query(SalesExportStates.selecting_specific_date, F.data == "main")
async def back_to_main_from_specific(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    except Exception:
        await call.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await call.answer()


async def _process_sales_export(message_or_call, state: FSMContext, start_dt: datetime, end_dt: datetime):
    """Обрабатывает экспорт продаж для заданного периода"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(
                Shipment.created_at,
                Shipment.doc_number,
                Counterparty.name,
                ShipmentItem.line_number,
                ShipmentItem.product_name,
                ShipmentItem.product_code,
                ShipmentItem.quantity,
                ShipmentItem.sale_price_cents,
                ShipmentItem.purchase_price_cents,
            )
            .join(Shipment.items)
            .join(Shipment.counterparty)
            .where(Shipment.created_at >= start_dt, Shipment.created_at <= end_dt)
            .order_by(Shipment.created_at, Shipment.doc_number, ShipmentItem.line_number)
        )
        rows = result.all()

    if not rows:
        await state.clear()
        if hasattr(message_or_call, 'message'):
            # Это CallbackQuery - отправляем новое сообщение
            await message_or_call.message.answer(
                "Продажи за выбранный период отсутствуют.",
                reply_markup=main_menu_kb()
            )
            await message_or_call.answer()
        else:
            # Это Message
            await message_or_call.answer(
                "Продажи за выбранный период отсутствуют.",
                reply_markup=main_menu_kb()
        )
        return

    # Группируем по накладным
    shipments = {}
    for row in rows:
        created_at, doc_number, counterparty_name, line_number, product_name, product_code, quantity, sale_price_cents, purchase_price_cents = row
        if doc_number not in shipments:
            shipments[doc_number] = {
                'created_at': created_at,
                'counterparty_name': counterparty_name,
                'items': []
            }
        shipments[doc_number]['items'].append({
            'line_number': line_number,
            'product_name': product_name,
            'product_code': product_code,
            'quantity': quantity,
            'sale_price_cents': sale_price_cents,
            'purchase_price_cents': purchase_price_cents,
        })

    # Сохраняем данные в состоянии для пагинации
    shipment_list = list(shipments.items())
    await state.update_data(
        shipments=shipment_list,
        current_page=0,
        start_date=start_dt.date(),
        end_date=end_dt.date()
    )
    await state.set_state(SalesExportStates.viewing_shipments)
    
    await show_shipments_page(message_or_call, state)


@router.message(SalesExportStates.waiting_start_date)
async def sales_export_start_date(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    if text in {"отмена", "cancel"}:
        await state.clear()
        await message.answer("Отмена. Главное меню:", reply_markup=main_menu_kb())
        return

    start = _parse_date(text)
    if start is None:
        await message.answer("Некорректная дата. Используйте формат ДД.ММ.ГГГГ.")
        return

    # Проверяем, находимся ли мы в режиме выбора конкретного дня
    data = await state.get_data()
    if data.get("selecting_specific_day"):
        # Для конкретного дня используем только одну дату
        start_dt = datetime.combine(start.date(), time.min)
        end_dt = datetime.combine(start.date(), time.max)
        await _process_sales_export(message, state, start_dt, end_dt)
    else:
        # Для периода запрашиваем дату окончания
        await state.update_data(start_date=start.date())
        await state.set_state(SalesExportStates.waiting_end_date)
        await message.answer("Введите дату окончания периода (ДД.ММ.ГГГГ):")


@router.message(SalesExportStates.waiting_end_date)
async def sales_export_end_date(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    if text in {"отмена", "cancel"}:
        await state.clear()
        await message.answer("Отмена. Главное меню:", reply_markup=main_menu_kb())
        return

    end = _parse_date(text)
    if end is None:
        await message.answer("Некорректная дата. Используйте формат ДД.ММ.ГГГГ.")
        return

    data = await state.get_data()
    start_date = data.get("start_date")

    if start_date is None:
        await state.clear()
        await message.answer("Неизвестная ошибка. Попробуйте снова.", reply_markup=main_menu_kb())
        return

    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end.date(), time.max)

    if end_dt < start_dt:
        await message.answer("Дата окончания не может быть раньше даты начала.")
        return

    await _process_sales_export(message, state, start_dt, end_dt)


async def show_shipments_page(message_or_call, state: FSMContext):
    data = await state.get_data()
    shipments = data.get("shipments", [])
    current_page = data.get("current_page", 0)
    
    if not shipments:
        await message_or_call.answer("Нет накладных для отображения.", reply_markup=main_menu_kb())
        await state.clear()
        return
    
    # Показываем по 5 накладных на страницу
    items_per_page = 5
    start_idx = current_page * items_per_page
    end_idx = start_idx + items_per_page
    page_shipments = shipments[start_idx:end_idx]
    
    text_lines = [f"Накладные за период (страница {current_page + 1}):"]
    buttons = []
    
    for doc_number, shipment_data in page_shipments:
        created_at = shipment_data['created_at']
        counterparty_name = shipment_data['counterparty_name']
        items_count = len(shipment_data['items'])
        
        # Подсчитываем общую сумму
        total_sale_cents = sum(
            int(item['quantity'] * item['sale_price_cents']) 
            for item in shipment_data['items']
        )
        total_sale_rub = total_sale_cents / 100
        
        text_lines.append(
            f"• №{doc_number} от {created_at.strftime('%d.%m.%Y')} "
            f"({counterparty_name}) - {total_sale_rub:.2f} ₽ ({items_count} позиций)"
        )
        buttons.append((f"№{doc_number}", f"download_shipment:{doc_number}"))
    
    # Добавляем кнопки навигации
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(("⬅️ Предыдущая", "prev_page"))
    if end_idx < len(shipments):
        nav_buttons.append(("➡️ Следующая", "next_page"))
    
    if nav_buttons:
        buttons.extend(nav_buttons)
    
    buttons.append(("📊 Скачать CSV", "download_csv"))
    buttons.append(("🏠 Главное меню", "main"))
    
    text = "\n".join(text_lines)
    
    if hasattr(message_or_call, 'message'):
        # Это CallbackQuery - редактируем сообщение
        await message_or_call.message.edit_text(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )
        await message_or_call.answer()
    else:
        # Это Message - отправляем новое сообщение
        await message_or_call.answer(
            text,
            reply_markup=list_buttons(buttons, columns=1)
        )


@router.callback_query(SalesExportStates.viewing_shipments, F.data == "prev_page")
async def prev_page(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_page = data.get("current_page", 0)
    if current_page > 0:
        await state.update_data(current_page=current_page - 1)
        await show_shipments_page(call, state)


@router.callback_query(SalesExportStates.viewing_shipments, F.data == "next_page")
async def next_page(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_page = data.get("current_page", 0)
    await state.update_data(current_page=current_page + 1)
    await show_shipments_page(call, state)


@router.callback_query(SalesExportStates.viewing_shipments, F.data.startswith("download_shipment:"))
async def download_shipment(call: CallbackQuery, state: FSMContext):
    doc_number = int(call.data.split(":", 1)[1])
    
    # Находим накладную в данных
    data = await state.get_data()
    shipments = data.get("shipments", [])
    
    shipment_data = None
    for num, data_item in shipments:
        if num == doc_number:
            shipment_data = data_item
            break
    
    if not shipment_data:
        await call.answer("Накладная не найдена", show_alert=True)
        return
    
    # Создаем PDF для накладной
    from ..services.pdf import ShipmentPdfData, ShipmentItemData, build_shipment_form_pdf
    
    pdf_items = [
        ShipmentItemData(
            line_number=item['line_number'],
            product_name=item['product_name'],
            product_code=item['product_code'],
            quantity=item['quantity'],
            sale_price_cents=item['sale_price_cents'],
            purchase_price_cents=item['purchase_price_cents'],
        )
        for item in shipment_data['items']
    ]
    
    pdf_data = ShipmentPdfData(
        doc_number=doc_number,
        created_at=shipment_data['created_at'],
        counterparty_name=shipment_data['counterparty_name'],
        items=pdf_items,
    )
    
    form_pdf = build_shipment_form_pdf(pdf_data)
    
    await call.message.answer_document(
        document=BufferedInputFile(form_pdf, filename=f"shipment_{doc_number}.pdf"),
        caption=f"Накладная №{doc_number}",
    )
    await call.answer()


@router.callback_query(SalesExportStates.viewing_shipments, F.data == "download_csv")
async def download_csv(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    shipments = data.get("shipments", [])
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    
    if not shipments:
        await call.answer("Нет данных для экспорта", show_alert=True)
        return
    
    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Дата",
            "Накладная",
            "Контрагент",
            "Строка",
            "Товар",
            "Код",
            "Количество",
            "Цена (руб)",
            "Сумма (руб)",
            "Себестоимость (руб)",
            "Прибыль (руб)",
        ]
    )

    total_sale_cents = 0
    total_purchase_cents = 0

    for doc_number, shipment_data in shipments:
        for item in shipment_data['items']:
            total_sale = int(item['quantity'] * item['sale_price_cents'])
            total_purchase = int(item['quantity'] * item['purchase_price_cents'])
            total_sale_cents += total_sale
            total_purchase_cents += total_purchase

            writer.writerow(
                [
                    shipment_data['created_at'].strftime("%d.%m.%Y"),
                    doc_number,
                    shipment_data['counterparty_name'],
                    item['line_number'],
                    item['product_name'],
                    item['product_code'],
                    format_quantity(item['quantity']),
                    _cents_to_str(item['sale_price_cents']),
                    _cents_to_str(total_sale),
                    _cents_to_str(total_purchase),
                    _cents_to_str(total_sale - total_purchase),
                ]
            )

    csv_bytes = ("\ufeff" + output.getvalue()).encode("utf-8")
    filename = f"sales_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    await call.message.answer_document(
        BufferedInputFile(csv_bytes, filename=filename),
        caption="Отчёт по продажам",
    )

    profit_cents = total_sale_cents - total_purchase_cents
    summary = (
        "Итоги по продажам:\n"
        f"Выручка: {format_money_numeric(total_sale_cents)} ₽\n"
        f"Себестоимость: {format_money_numeric(total_purchase_cents)} ₽\n"
        f"Прибыль: {format_money_numeric(profit_cents)} ₽"
    )

    await call.message.answer(summary)
    await call.answer()


@router.callback_query(SalesExportStates.viewing_shipments, F.data == "main")
async def back_to_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    except Exception:
        # Если не удалось отредактировать, отправляем новое сообщение
        await call.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await call.answer()

