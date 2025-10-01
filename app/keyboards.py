from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Оформить отгрузку", callback_data="start_shipment")
    kb.button(text="Оформить приход", callback_data="start_receipt")
    kb.button(text="Текущие остатки", callback_data="current_stock")
    kb.button(text="Выгрузка продаж", callback_data="export_sales")
    kb.button(text="Указать константы", callback_data="manage_constants")
    kb.button(text="Поставщики", callback_data="manage_suppliers")
    kb.button(text="Ассортимент", callback_data="manage_products")
    kb.adjust(1)
    return kb.as_markup()


def list_buttons(items: list[tuple[str, str]], columns: int = 1, back: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for text, cd in items:
        kb.button(text=text, callback_data=cd)
    if back:
        kb.button(text="⬅ Назад", callback_data=back)
    kb.adjust(columns)
    return kb.as_markup()


def admin_list_kb(items: list[tuple[str, str]], back_cd: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for text, cd in items:
        kb.button(text=text, callback_data=cd)
    kb.button(text="➕ Добавить", callback_data=f"{back_cd}_add")
    kb.button(text="⬅ Назад", callback_data="admin_back_to_main")
    kb.adjust(1)
    return kb.as_markup()


def confirm_delete_kb(entity_id: int, prefix: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Удалить", callback_data=f"{prefix}_delete:{entity_id}")
    kb.button(text="⬅ Отмена", callback_data=f"{prefix}_cancel")
    kb.adjust(1)
    return kb.as_markup()


