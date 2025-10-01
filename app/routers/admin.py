from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from sqlalchemy import delete, select

from ..db import AsyncSessionLocal
from ..keyboards import admin_list_kb, confirm_delete_kb, list_buttons, main_menu_kb
from ..models import Counterparty, Product

router = Router(name="admin")


class SupplierStates(StatesGroup):
    waiting_name = State()
    editing_name = State()


class ProductStates(StatesGroup):
    waiting_name = State()
    waiting_code = State()
    waiting_retail = State()
    waiting_purchase = State()
    editing_field = State()


@router.callback_query(F.data.in_({"manage_suppliers", "manage_products"}))
async def entry_point(call: CallbackQuery) -> None:
    if call.data == "manage_suppliers":
        await show_suppliers(call)
    elif call.data == "manage_products":
        await show_products(call)


async def show_suppliers(call: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        supplier_rows = (
            await session.execute(select(Counterparty).order_by(Counterparty.name))
        ).scalars().all()
    items = [(c.name, f"supplier_edit:{c.id}") for c in supplier_rows]
    await call.message.edit_text(
        "Поставщики:",
        reply_markup=admin_list_kb(items, "supplier"),
    )
    await call.answer()


async def show_products(call: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        product_rows = (
            await session.execute(select(Product).order_by(Product.name))
        ).scalars().all()
    items = [(f"{p.code} {p.name}", f"product_edit:{p.id}") for p in product_rows]
    await call.message.edit_text(
        "Ассортимент:",
        reply_markup=admin_list_kb(items, "product"),
    )
    await call.answer()


@router.callback_query(F.data == "admin_back_to_main")
async def back_to_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    await call.answer()


# --- Supplier management ---
@router.callback_query(F.data == "supplier_add")
async def supplier_add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupplierStates.waiting_name)
    await call.message.edit_text("Введите имя поставщика:")
    await call.answer()


@router.message(SupplierStates.waiting_name)
async def supplier_save_name(message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не может быть пустым. Попробуйте снова:")
        return
    async with AsyncSessionLocal() as session:
        session.add(Counterparty(name=name))
        await session.commit()
    await state.clear()
    await message.answer("Поставщик добавлен.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("supplier_edit:"))
async def supplier_edit(call: CallbackQuery, state: FSMContext) -> None:
    supplier_id = int(call.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        supplier = (
            await session.execute(
                select(Counterparty).where(Counterparty.id == supplier_id)
            )
        ).scalar_one()
    await state.update_data(edit_supplier_id=supplier_id)
    await call.message.edit_text(
        f"Поставщик: {supplier.name}",
        reply_markup=list_buttons(
            [
                ("✏ Изменить имя", "supplier_edit_name"),
                ("🗑 Удалить", f"supplier_delete_confirm:{supplier_id}"),
            ],
            columns=1,
            back="manage_suppliers",
        ),
    )
    await call.answer()


@router.callback_query(F.data == "supplier_edit_name")
async def supplier_edit_name(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupplierStates.editing_name)
    await call.message.edit_text("Введите новое имя поставщика:")
    await call.answer()


@router.message(SupplierStates.editing_name)
async def supplier_update_name(message, state: FSMContext) -> None:
    data = await state.get_data()
    supplier_id = int(data["edit_supplier_id"])  # type: ignore[index]
    new_name = (message.text or "").strip()
    if not new_name:
        await message.answer("Имя не может быть пустым. Попробуйте снова:")
        return
    async with AsyncSessionLocal() as session:
        supplier = (
            await session.execute(
                select(Counterparty).where(Counterparty.id == supplier_id)
            )
        ).scalar_one()
        supplier.name = new_name
        await session.commit()
    await state.clear()
    await message.answer("Имя обновлено.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("supplier_delete_confirm:"))
async def supplier_delete_confirm(call: CallbackQuery) -> None:
    supplier_id = int(call.data.split(":", 1)[1])
    await call.message.edit_text(
        "Удалить поставщика?",
        reply_markup=confirm_delete_kb(supplier_id, "supplier"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("supplier_delete:"))
async def supplier_delete(call: CallbackQuery) -> None:
    supplier_id = int(call.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Counterparty).where(Counterparty.id == supplier_id))
        await session.commit()
    await show_suppliers(call)


@router.callback_query(F.data == "supplier_cancel")
async def supplier_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_suppliers(call)


# --- Product management ---
PRODUCT_FIELDS = ["name", "code", "retail", "purchase"]


@router.callback_query(F.data == "product_add")
async def product_add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(product_form={})
    await state.set_state(ProductStates.waiting_name)
    await call.message.edit_text("Введите наименование товара:")
    await call.answer()


@router.message(ProductStates.waiting_name)
async def product_add_name(message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Наименование не может быть пустым. Попробуйте снова:")
        return
    data = await state.get_data()
    form = data.get("product_form", {})
    form["name"] = name
    await state.update_data(product_form=form)
    await state.set_state(ProductStates.waiting_code)
    await message.answer("Введите код товара (число):")


@router.message(ProductStates.waiting_code)
async def product_add_code(message, state: FSMContext) -> None:
    try:
        code = int((message.text or "").strip())
    except Exception:
        await message.answer("Код должен быть числом. Попробуйте снова:")
        return
    data = await state.get_data()
    form = data.get("product_form", {})
    form["code"] = code
    await state.update_data(product_form=form)
    await state.set_state(ProductStates.waiting_retail)
    await message.answer("Введите цену продажи в копейках:")


@router.message(ProductStates.waiting_retail)
async def product_add_retail(message, state: FSMContext) -> None:
    try:
        retail = int((message.text or "").strip())
    except Exception:
        await message.answer("Цена продажи должна быть целым числом в копейках.")
        return
    data = await state.get_data()
    form = data.get("product_form", {})
    form["retail_price_cents"] = retail
    await state.update_data(product_form=form)
    await state.set_state(ProductStates.waiting_purchase)
    await message.answer("Введите закупочную цену в копейках:")


@router.message(ProductStates.waiting_purchase)
async def product_add_purchase(message, state: FSMContext) -> None:
    try:
        purchase = int((message.text or "").strip())
    except Exception:
        await message.answer("Закупочная цена должна быть целым числом в копейках.")
        return
    data = await state.get_data()
    form = data.get("product_form", {})
    form["purchase_price_cents"] = purchase
    await state.update_data(product_form=form)
    async with AsyncSessionLocal() as session:
        session.add(
            Product(
                code=form["code"],
                name=form["name"],
                retail_price_cents=form["retail_price_cents"],
                purchase_price_cents=form["purchase_price_cents"],
            )
        )
        await session.commit()

    await state.clear()
    await message.answer("Товар добавлен.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("product_edit:"))
async def product_edit(call: CallbackQuery, state: FSMContext) -> None:
    product_id = int(call.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        product = (
            await session.execute(select(Product).where(Product.id == product_id))
        ).scalar_one()
    await state.update_data(edit_product_id=product_id)
    await call.message.edit_text(
        f"Товар: {product.code} {product.name}",
        reply_markup=list_buttons(
            [
                ("✏ Изменить название", "product_edit_name"),
                ("✏ Изменить код", "product_edit_code"),
                ("✏ Изменить цену продажи", "product_edit_retail"),
                ("✏ Изменить закупку", "product_edit_purchase"),
                ("🗑 Удалить", f"product_delete_confirm:{product_id}"),
            ],
            columns=1,
            back="manage_products",
        ),
    )
    await call.answer()


async def _update_product_field(state: FSMContext, field: str, prompt: str, next_state: State) -> str:
    await state.update_data(product_edit_field=field)
    await state.set_state(next_state)
    return prompt


@router.callback_query(F.data == "product_edit_name")
async def product_edit_name(call: CallbackQuery, state: FSMContext) -> None:
    prompt = await _update_product_field(state, "name", "Введите новое наименование:", ProductStates.editing_field)
    await call.message.edit_text(prompt)
    await call.answer()


@router.callback_query(F.data == "product_edit_code")
async def product_edit_code(call: CallbackQuery, state: FSMContext) -> None:
    prompt = await _update_product_field(state, "code", "Введите новый код (число):", ProductStates.editing_field)
    await call.message.edit_text(prompt)
    await call.answer()


@router.callback_query(F.data == "product_edit_retail")
async def product_edit_retail(call: CallbackQuery, state: FSMContext) -> None:
    prompt = await _update_product_field(state, "retail_price_cents", "Введите новую цену продажи (в копейках):", ProductStates.editing_field)
    await call.message.edit_text(prompt)
    await call.answer()


@router.callback_query(F.data == "product_edit_purchase")
async def product_edit_purchase(call: CallbackQuery, state: FSMContext) -> None:
    prompt = await _update_product_field(state, "purchase_price_cents", "Введите новую закупочную цену (в копейках):", ProductStates.editing_field)
    await call.message.edit_text(prompt)
    await call.answer()




@router.message(ProductStates.editing_field)
async def product_save_field(message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("product_edit_field")
    product_id = int(data["edit_product_id"])  # type: ignore[index]
    if not field:
        await message.answer("Неизвестное поле. Попробуйте снова.")
        return

    value = (message.text or "").strip()
    try:
        if field == "code":
            value_parsed = int(value)
        elif field in {"retail_price_cents", "purchase_price_cents"}:
            value_parsed = int(value)
        else:
            value_parsed = value
    except Exception:
        await message.answer("Неверный формат. Попробуйте снова.")
        return

    async with AsyncSessionLocal() as session:
        product = (
            await session.execute(select(Product).where(Product.id == product_id))
        ).scalar_one()
        setattr(product, field, value_parsed)
        await session.commit()

    await state.clear()
    await message.answer("Изменения сохранены.", reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("product_delete_confirm:"))
async def product_delete_confirm(call: CallbackQuery) -> None:
    product_id = int(call.data.split(":", 1)[1])
    await call.message.edit_text(
        "Удалить товар?",
        reply_markup=confirm_delete_kb(product_id, "product"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("product_delete:"))
async def product_delete(call: CallbackQuery) -> None:
    product_id = int(call.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Product).where(Product.id == product_id))
        await session.commit()
    await show_products(call)


@router.callback_query(F.data == "product_cancel")
async def product_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_products(call)
