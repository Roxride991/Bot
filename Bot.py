# cycle_gate_bot.py
# Python 3.9+
# Install: python -m pip install -U "aiogram>=3,<4"

import asyncio
import logging
import os
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message, CallbackQuery, ChatPermissions
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# ==========================
# НАСТРОЙКИ
# ==========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it in Render Environment Variables.")
# <-- ВСТАВЬ НОВЫЙ ТОКЕН (старый Revoke!)

# Удаление сообщений бота
DELETE_ON_SUCCESS_SECONDS = 3
DELETE_ON_FAIL_SECONDS = 30

# Кто всегда может писать (твои аккаунты админов). Узнай свой user_id и добавь сюда.
# Можно оставить пустым, но лучше добавить себя.
ADMIN_IDS = {
    # 123456789,
}

# Список групп ПО ПОРЯДКУ ЦИКЛА:
# Чтобы писать в GROUPS[i] -> нужно состоять в GROUPS[i+1] (а для последней -> в первой)
GROUPS: List[dict] = [
    {
        "chat": "@pokupkaprodajaoren",
        "link": "https://t.me/pokupkaprodajaoren",
        "title": "Группа 1",
    },
    {
        "chat": "@kupluprodamorenburg",
        "link": "https://t.me/kupluprodamorenburg",
        "title": "Группа 2",
    },
    # Добавляй сколько угодно:
    # {"chat":"@group3", "link":"https://t.me/group3", "title":"Группа 3"},
    # {"chat":"@group4", "link":"https://t.me/group4", "title":"Группа 4"},
]
# ==========================
# КОНЕЦ НАСТРОЕК
# ==========================

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

# Реальные числовые chat_id (получим при запуске)
GROUP_CHAT_IDS: List[int] = []
CHAT_ID_TO_INDEX: Dict[int, int] = {}


def next_index(i: int) -> int:
    return (i + 1) % len(GROUPS)


def build_kb(current_idx: int):
    """Кнопка подписки на следующую группу + проверка."""
    nxt = GROUPS[next_index(current_idx)]
    kb = InlineKeyboardBuilder()
    kb.button(text=f"📌 Вступить в следующую: {nxt['title']}", url=nxt["link"])
    kb.button(text="✅ Проверить подписку", callback_data=f"check:{current_idx}")
    kb.adjust(1)
    return kb.as_markup()


async def delete_later(bot: Bot, chat_id: int, message_id: int, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def is_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Проверяем, состоит ли пользователь в нужной (следующей) группе."""
    try:
        m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return m.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED,   # <-- ДОБАВЬ ЭТО
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }
    except (TelegramForbiddenError, TelegramBadRequest):
        return False

async def restrict_user(bot: Bot, target_chat_id: int, user_id: int) -> bool:
    """Запрещаем писать в текущей группе."""
    try:
        await bot.restrict_chat_member(
            chat_id=target_chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


async def unlock_user(bot: Bot, target_chat_id: int, user_id: int) -> bool:
    """Разрешаем писать в текущей группе."""
    try:
        await bot.restrict_chat_member(
            chat_id=target_chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


@dp.message(F.new_chat_members)
async def on_new_members(message: Message, bot: Bot):
    """
    Новый участник в одной из групп цикла:
    - мутим (запрещаем писать)
    - отправляем инструкцию (вступи в следующую + проверь)
    - удаляем инструкцию через 30 секунд
    """
    if message.chat.id not in CHAT_ID_TO_INDEX:
        return

    current_idx = CHAT_ID_TO_INDEX[message.chat.id]

    for u in message.new_chat_members:
        if u.is_bot:
            continue

        # админы/создатели (и ты) — без ограничений
        if u.id in ADMIN_IDS:
            continue

        # мутим в текущей группе
        await restrict_user(bot, message.chat.id, u.id)

        sent = await message.answer(
            f"👋 {u.full_name}\n"
            f"Чтобы писать в этом чате — вступи в следующую группу и нажми «Проверить подписку».",
            reply_markup=build_kb(current_idx),
        )
        asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_FAIL_SECONDS))


@dp.callback_query(F.data.startswith("check:"))
async def check_sub(call: CallbackQuery, bot: Bot):
    """
    Проверка подписки: чтобы писать в GROUPS[current_idx],
    надо состоять в GROUPS[next_idx].
    """
    user_id = call.from_user.id

    # если ты в ADMIN_IDS — просто открываем
    if user_id in ADMIN_IDS:
        current_idx = int(call.data.split(":")[1])
        ok_unlock = await unlock_user(bot, GROUP_CHAT_IDS[current_idx], user_id)
        txt = "✅ Админ-доступ: ограничения сняты." if ok_unlock else "⚠️ Не смог снять ограничения (проверь права бота)."
        sent = await call.message.answer(txt)
        asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_SUCCESS_SECONDS))
        await call.answer()
        return

    current_idx = int(call.data.split(":")[1])
    if current_idx < 0 or current_idx >= len(GROUPS):
        await call.answer("Неверные данные", show_alert=True)
        return

    nxt_idx = next_index(current_idx)
    required_chat_id = GROUP_CHAT_IDS[nxt_idx]

    ok = await is_member(bot, required_chat_id, user_id)

    if ok:
        # открываем писать в текущей группе
        unlocked = await unlock_user(bot, GROUP_CHAT_IDS[current_idx], user_id)
        if unlocked:
            sent = await call.message.answer("✅ Подписка подтверждена! Доступ открыт — можешь писать.")
            asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_SUCCESS_SECONDS))
        else:
            sent = await call.message.answer(
                "✅ Подписка подтверждена, но я не смог открыть доступ.\n"
                "Проверь, что бот админ в этой группе и у него есть Restrict/Ban users."
            )
            asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_FAIL_SECONDS))
    else:
        nxt = GROUPS[nxt_idx]
        sent = await call.message.answer(
            f"❌ Подписки нет.\n"
            f"Нужно вступить в следующую группу: {nxt['title']}",
            reply_markup=build_kb(current_idx),
        )
        asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_FAIL_SECONDS))

    await call.answer()


async def main():
    global GROUP_CHAT_IDS, CHAT_ID_TO_INDEX

    if len(GROUPS) < 2:
        raise RuntimeError("Нужно минимум 2 группы в цикле.")

    bot = Bot(BOT_TOKEN)

    # резолвим @username -> chat_id
    GROUP_CHAT_IDS = []
    CHAT_ID_TO_INDEX = {}

    for i, g in enumerate(GROUPS):
        chat = await bot.get_chat(g["chat"])
        GROUP_CHAT_IDS.append(chat.id)
        CHAT_ID_TO_INDEX[chat.id] = i

    logging.info("Resolved groups:")
    for i, g in enumerate(GROUPS):
        logging.info(f"  [{i}] {g['chat']} -> {GROUP_CHAT_IDS[i]} (next -> {GROUPS[next_index(i)]['chat']})")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
