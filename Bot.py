# Bot.py
# Python 3.9+ (у тебя 3.12 — ок)
# requirements.txt: aiogram>=3,<4

import asyncio
import logging
import os
from typing import Dict, List

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
    raise RuntimeError("BOT_TOKEN is not set. Add it to systemd Environment (or export BOT_TOKEN=...)")

DELETE_ON_SUCCESS_SECONDS = 3
DELETE_ON_FAIL_SECONDS = 30

ADMIN_IDS = {
    # 123456789,
}

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
]

PIN_MARK = "🔓 Разблокировка доступа (gate)"

# ==========================
# КОНЕЦ НАСТРОЕК
# ==========================

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

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
    """
    Проверка подписки:
    - MEMBER/ADMIN/CREATOR = да
    - RESTRICTED = да, только если is_member=True (то есть реально в группе)
    """
    try:
        m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)

        if m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return True

        if m.status == ChatMemberStatus.RESTRICTED:
            # Важно: restricted может быть "не участник" => is_member=False
            return bool(getattr(m, "is_member", False))

        return False

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


async def ensure_pinned_gate(bot: Bot):
    """
    В каждой группе создаём/обновляем закреплённое сообщение с кнопками.
    Так кнопки есть всегда, даже если входное сообщение удалилось.
    """
    me = await bot.get_me()

    for idx, chat_id in enumerate(GROUP_CHAT_IDS):
        chat = await bot.get_chat(chat_id)
        pinned = getattr(chat, "pinned_message", None)

        # Если уже закреплено наше сообщение — обновим клавиатуру
        if (
            pinned
            and pinned.from_user
            and pinned.from_user.id == me.id
            and pinned.text
            and PIN_MARK in pinned.text
        ):
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=pinned.message_id,
                    reply_markup=build_kb(idx),
                )
                logging.info(f"Updated pinned gate in chat_id={chat_id}")
            except Exception as e:
                logging.warning(f"Cannot update pinned gate in {chat_id}: {e}")
            continue

        # Иначе — отправим новое и закрепим
        msg = await bot.send_message(
            chat_id,
            f"{PIN_MARK}\n\n"
            "Если тебе запрещено писать — вступи в следующую группу и нажми «Проверить подписку».",
            reply_markup=build_kb(idx),
            disable_notification=True,
        )
        try:
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
            logging.info(f"Pinned new gate in chat_id={chat_id}")
        except Exception as e:
            logging.warning(f"Cannot pin message in {chat_id}: {e}")


# ==========================
# ГЛАВНЫЙ ГЕЙТ: проверка на каждое сообщение
# ==========================
@dp.message()
async def gate_on_every_message(message: Message, bot: Bot):
    # работаем только в наших группах
    if message.chat.id not in CHAT_ID_TO_INDEX:
        return

    # не трогаем сервисные события (вступил/вышел и т.п.)
    if message.new_chat_members or message.left_chat_member:
        return

    # если нет отправителя — выходим
    if not message.from_user:
        return

    user_id = message.from_user.id

    # ботов и админов не трогаем
    if message.from_user.is_bot or user_id in ADMIN_IDS:
        return

    current_idx = CHAT_ID_TO_INDEX[message.chat.id]
    nxt_idx = next_index(current_idx)
    required_chat_id = GROUP_CHAT_IDS[nxt_idx]

    ok = await is_member(bot, required_chat_id, user_id)

    if ok:
        # если был замучен, снимем (на всякий случай)
        await unlock_user(bot, message.chat.id, user_id)
        return

    # НЕ подписан — удаляем сообщение, мутим и показываем кнопки
    try:
        await message.delete()
    except Exception:
        pass

    await restrict_user(bot, message.chat.id, user_id)

    sent = await message.answer(
        "❌ Чтобы писать в этом чате — вступи в следующую группу и нажми «Проверить подписку» (кнопки также в закрепе).",
        reply_markup=build_kb(current_idx),
    )
    asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_FAIL_SECONDS))


# ==========================
# Новый участник: сразу мут + подсказка (и закреп есть всегда)
# ==========================
@dp.message(F.new_chat_members)
async def on_new_members(message: Message, bot: Bot):
    if message.chat.id not in CHAT_ID_TO_INDEX:
        return

    current_idx = CHAT_ID_TO_INDEX[message.chat.id]

    for u in message.new_chat_members:
        if u.is_bot:
            continue
        if u.id in ADMIN_IDS:
            continue

        await restrict_user(bot, message.chat.id, u.id)

        sent = await message.answer(
            f"👋 {u.full_name}\n"
            "Чтобы писать — вступи в следующую группу и нажми «Проверить подписку» (кнопки есть в закрепе).",
            reply_markup=build_kb(current_idx),
        )
        asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_FAIL_SECONDS))


# ==========================
# Кнопка "Проверить подписку"
# ==========================
@dp.callback_query(F.data.startswith("check:"))
async def check_sub(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    try:
        current_idx = int(call.data.split(":")[1])
    except Exception:
        await call.answer("Неверные данные", show_alert=True)
        return

    if current_idx < 0 or current_idx >= len(GROUPS):
        await call.answer("Неверные данные", show_alert=True)
        return

    # Админов просто открываем
    if user_id in ADMIN_IDS:
        ok_unlock = await unlock_user(bot, GROUP_CHAT_IDS[current_idx], user_id)
        txt = "✅ Админ-доступ: ограничения сняты." if ok_unlock else "⚠️ Не смог снять ограничения (проверь права бота)."
        sent = await call.message.answer(txt)
        asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_SUCCESS_SECONDS))
        await call.answer()
        return

    nxt_idx = next_index(current_idx)
    required_chat_id = GROUP_CHAT_IDS[nxt_idx]

    ok = await is_member(bot, required_chat_id, user_id)

    if ok:
        unlocked = await unlock_user(bot, GROUP_CHAT_IDS[current_idx], user_id)
        if unlocked:
            sent = await call.message.answer("✅ Подписка подтверждена! Доступ открыт — можешь писать.")
            asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_SUCCESS_SECONDS))
        else:
            sent = await call.message.answer(
                "✅ Подписка подтверждена, но я не смог открыть доступ.\n"
                "Проверь, что бот админ в этой группе и у него есть Restrict users."
            )
            asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_FAIL_SECONDS))
    else:
        nxt = GROUPS[nxt_idx]
        sent = await call.message.answer(
            f"❌ Подписки нет.\nНужно вступить в следующую группу: {nxt['title']}",
            reply_markup=build_kb(current_idx),
        )
        asyncio.create_task(delete_later(bot, sent.chat.id, sent.message_id, DELETE_ON_FAIL_SECONDS))

    await call.answer()


async def main():
    global GROUP_CHAT_IDS, CHAT_ID_TO_INDEX

    if len(GROUPS) < 2:
        raise RuntimeError("Нужно минимум 2 группы в цикле.")

    bot = Bot(BOT_TOKEN)

    GROUP_CHAT_IDS = []
    CHAT_ID_TO_INDEX = {}

    for i, g in enumerate(GROUPS):
        chat = await bot.get_chat(g["chat"])
        GROUP_CHAT_IDS.append(chat.id)
        CHAT_ID_TO_INDEX[chat.id] = i

    logging.info("Resolved groups:")
    for i, g in enumerate(GROUPS):
        logging.info(f"  [{i}] {g['chat']} -> {GROUP_CHAT_IDS[i]} (next -> {GROUPS[next_index(i)]['chat']})")

    await ensure_pinned_gate(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
