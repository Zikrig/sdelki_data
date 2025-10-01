from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..keyboards import list_buttons, main_menu_kb
from ..models import Constants


router = Router(name="constants")


class ConstantsStates(StatesGroup):
    waiting_shipment_number = State()
    waiting_receipt_number = State()


@router.callback_query(F.data == "manage_constants")
async def manage_constants(call: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        shipment_const = (
            await session.execute(
                select(Constants.value).where(Constants.key == "last_shipment_number")
            )
        ).scalar_one_or_none()
        
        receipt_const = (
            await session.execute(
                select(Constants.value).where(Constants.key == "last_receipt_number")
            )
        ).scalar_one_or_none()

    shipment_num = shipment_const or "0"
    receipt_num = receipt_const or "0"

    text = (
        "Текущие константы:\n"
        f"• Последний номер отгрузки: {shipment_num}\n"
        f"• Последний номер прихода: {receipt_num}\n\n"
        "Выберите, что изменить:"
    )

    items = [
        ("Изменить номер отгрузки", "edit_shipment_number"),
        ("Изменить номер прихода", "edit_receipt_number"),
    ]

    try:
        await call.message.edit_text(
            text,
            reply_markup=list_buttons(items, columns=1, back="main"),
        )
    except Exception:
        await call.message.answer(
            text,
            reply_markup=list_buttons(items, columns=1, back="main"),
        )
    
    await call.answer()


@router.callback_query(F.data == "edit_shipment_number")
async def edit_shipment_number(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ConstantsStates.waiting_shipment_number)
    
    text = (
        "Введите номер последней отгрузки (целое число):\n"
        "Напишите 'отмена' для возврата в меню."
    )
    
    try:
        await call.message.edit_text(text)
    except Exception:
        await call.message.answer(text)
    
    await call.answer()


@router.callback_query(F.data == "edit_receipt_number")
async def edit_receipt_number(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ConstantsStates.waiting_receipt_number)
    
    text = (
        "Введите номер последнего прихода (целое число):\n"
        "Напишите 'отмена' для возврата в меню."
    )
    
    try:
        await call.message.edit_text(text)
    except Exception:
        await call.message.answer(text)
    
    await call.answer()


@router.message(ConstantsStates.waiting_shipment_number)
async def save_shipment_number(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    
    if text in {"отмена", "cancel"}:
        await state.clear()
        await message.answer("Отмена. Главное меню:", reply_markup=main_menu_kb())
        return
    
    try:
        number = int(text)
        if number < 0:
            raise ValueError
    except ValueError:
        await message.answer("Некорректный номер. Введите целое неотрицательное число.")
        return
    
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(Constants).where(Constants.key == "last_shipment_number")
            )
        ).scalar_one_or_none()
        
        if existing:
            existing.value = str(number)
        else:
            session.add(Constants(key="last_shipment_number", value=str(number)))
        
        await session.commit()
    
    await state.clear()
    await message.answer(
        f"Номер последней отгрузки установлен: {number}",
        reply_markup=main_menu_kb()
    )


@router.message(ConstantsStates.waiting_receipt_number)
async def save_receipt_number(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    
    if text in {"отмена", "cancel"}:
        await state.clear()
        await message.answer("Отмена. Главное меню:", reply_markup=main_menu_kb())
        return
    
    try:
        number = int(text)
        if number < 0:
            raise ValueError
    except ValueError:
        await message.answer("Некорректный номер. Введите целое неотрицательное число.")
        return
    
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(Constants).where(Constants.key == "last_receipt_number")
            )
        ).scalar_one_or_none()
        
        if existing:
            existing.value = str(number)
        else:
            session.add(Constants(key="last_receipt_number", value=str(number)))
        
        await session.commit()
    
    await state.clear()
    await message.answer(
        f"Номер последнего прихода установлен: {number}",
        reply_markup=main_menu_kb()
    )
