import asyncio
import logging
import os
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import (
    BOT_TOKEN, OWNER_ID, CHANNEL_ID, CHANNEL_URL, SITE_URL, SUPPORT_URL,
    H1_API_BASE, H1_API_TOKEN, H1_AUTH_HEADER, H1_INBOUND_IDS, PLANS, DB_PATH
)
from database import Database

# Канал обязательной подписки
CHANNEL_ID = "@trepowvpn"
CHANNEL_URL = "https://t.me/trepowvpn"
TRIAL_DAYS = 2
REFERRAL_DAYS = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
if not BOT_TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN в настройках")
if not H1_API_TOKEN:
    logging.warning("Не указан H1_API_TOKEN. Создание VPN работать не будет.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = Database()
user_states = {}
START_PHOTO = os.path.join("assets", "trepow_vpn_start.jpg")
DEVICE_LIMIT = 2

def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[blue_button(text="⬅️ Назад", callback_data="back_home")]])

async def render_callback(call: CallbackQuery, text: str, reply_markup=None):
    try:
        if call.message.photo:
            await call.message.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await call.message.edit_text(text=text, reply_markup=reply_markup)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            logging.warning("Не удалось обновить экран: %s", exc)
            await call.message.answer(text, reply_markup=reply_markup)



def blue_button(**kwargs):
    """Создаёт синюю inline-кнопку Telegram (style=primary)."""
    return InlineKeyboardButton(style="primary", **kwargs)


def main_menu(user_id: int):
    # Кнопки расположены в две колонки: слева и справа.
    rows = [
        [blue_button(text="Купить VPN", callback_data="menu_buy"),
         blue_button(text="Мой VPN", callback_data="menu_my_vpn")],
        [blue_button(text="Пробный период", callback_data="menu_trial"),
         blue_button(text="Тарифы", callback_data="menu_tariffs")],
        [blue_button(text="Промокод", callback_data="menu_promo"),
         blue_button(text="Рефералы", callback_data="menu_referral")],
    ]
    links = []
    if SITE_URL:
        links.append(blue_button(text="Наш сайт", url=SITE_URL))
    if SUPPORT_URL:
        links.append(blue_button(text="Поддержка", url=SUPPORT_URL))
    if links:
        rows.append(links)
    if user_id == OWNER_ID:
        rows.append([blue_button(text="Админ-панель", callback_data="menu_admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [blue_button(text="Подписаться на канал", url=CHANNEL_URL)],
        [blue_button(text="Проверить подписку", callback_data="check_subscription")]
    ])


def plans_keyboard():
    buttons = []
    for key, plan in PLANS.items():
        buttons.append([blue_button(
            text=f"{plan['name']} — {plan['price']} ₽", callback_data=f"buy_{key}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(order_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [blue_button(text="Отменить заказ", callback_data=f"cancel_{order_id}")]
    ])


def admin_order_keyboard(order_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [blue_button(text="Подтвердить оплату", callback_data=f"approve_{order_id}")],
        [blue_button(text="Отклонить", callback_data=f"reject_{order_id}")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [blue_button(text="Статистика бота", callback_data="admin_stats"),
         blue_button(text="Статистика VPN", callback_data="admin_vpn_stats")],
        [blue_button(text="Ожидающие заявки", callback_data="admin_orders"),
         blue_button(text="Рассылка", callback_data="admin_broadcast")],
        [blue_button(text="Создать промокод", callback_data="admin_promo_help")]
    ])


async def is_subscribed(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("creator", "administrator", "member") or (
            member.status == "restricted" and member.is_member
        )
    except Exception:
        logging.exception("Ошибка проверки подписки")
        return False


async def subscription_required(message: Message) -> bool:
    if await is_subscribed(message.from_user.id):
        return True
    await message.answer(
        "<b>Для доступа к TREPOW VPN необходимо подписаться на наш канал.</b>\n\n"
        "<b>1. Нажми «Подписаться на канал».</b>\n"
        "<b>2. Подпишись на @trepowvpn.</b>\n"
        "<b>3. Вернись и нажми «Проверить подписку».</b>",
        reply_markup=subscription_keyboard()
    )
    return False


async def h1_request(method: str, endpoint: str, payload=None):
    if not H1_API_TOKEN:
        raise RuntimeError("Не задан H1_API_TOKEN")
    headers = {H1_AUTH_HEADER: H1_API_TOKEN, "Content-Type": "application/json"}
    url = f"{H1_API_BASE.rstrip('/')}/{endpoint.lstrip('/')}"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, headers=headers, json=payload) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"H1 API HTTP {response.status}: {data}")
            if isinstance(data, dict) and data.get("ok") is False:
                raise RuntimeError(str(data))
            return data


async def create_h1_client(user_id: int, days: int, order_id: int, client_suffix: str = "") -> str:
    client_name = f"tg_{user_id}_trial" if client_suffix == "_trial" else f"tg_{user_id}_{order_id}{client_suffix}"
    payload = {
        "name": client_name,
        "days": int(days),
        "device_limit": 2,
        "traffic_limit_gb": 0,
        "manual": True,
        "inbound_ids": H1_INBOUND_IDS
    }
    result = await h1_request("POST", "/create", payload)
    client = result.get("client", result) if isinstance(result, dict) else {}
    sub_url = client.get("sub_url") or client.get("subscription_url") or client.get("url")
    if not sub_url:
        raise RuntimeError(f"H1 API не вернул ссылку подписки: {result}")
    await db.save_vpn_client(user_id=user_id, order_id=order_id,
                             client_name=client_name, sub_url=sub_url, days=days)
    return sub_url


async def extend_user_vpn_or_credit(user_id: int, days: int):
    """Добавляет дни к существующему клиенту; если клиента нет — сохраняет дни в бонусный баланс."""
    clients = await db.get_user_vpn(user_id)
    if clients:
        client = clients[0]
        try:
            await h1_request("PATCH", f"/clients/{client['client_name']}", {"days": int(days)})
            await db.add_vpn_days(user_id, days)
            return "extended"
        except Exception:
            logging.exception("Не удалось продлить VPN через H1; дни сохранены в бонусный баланс")
    await db.add_bonus_days(user_id, days)
    return "credited"


async def create_client_with_bonus(user_id: int, base_days: int, order_id: int, suffix=""):
    bonus = await db.get_bonus_days(user_id)
    total_days = base_days + bonus
    sub_url = await create_h1_client(user_id, total_days, order_id, suffix)
    if bonus:
        await db.clear_bonus_days(user_id)
    return sub_url, total_days


@dp.message(CommandStart())
async def start(message: Message, command: CommandObject):
    is_new = await db.add_user(message.from_user)
    if is_new and command.args and command.args.startswith("ref_"):
        try:
            referrer_id = int(command.args[4:])
            if referrer_id != message.from_user.id:
                created = await db.add_referral(referrer_id, message.from_user.id)
                if created:
                    result = await extend_user_vpn_or_credit(referrer_id, REFERRAL_DAYS)
                    try:
                        if result == "extended":
                            await bot.send_message(referrer_id, "За приглашённого друга начислено 2 дня к VPN.")
                        else:
                            await bot.send_message(referrer_id, "За приглашённого друга начислено 2 бонусных дня. Они добавятся к следующему VPN.")
                    except Exception:
                        pass
        except (ValueError, Exception):
            logging.exception("Не удалось обработать реферальную ссылку")

    cleanup = await message.answer("Обновляю меню…", reply_markup=ReplyKeyboardRemove())
    try:
        await cleanup.delete()
    except Exception:
        pass
    if not await subscription_required(message):
        return
    welcome_text = (
        "<b>TREPOW VPN — свобода в интернете без границ</b>\\n\\n"
        "<b>• Доступ ко всем сайтам, Telegram, YouTube, Twitch, Discord</b>\\n"
        "<b>• Быстрый и стабильный интернет</b>\\n"
        "<b>• Поддержка Android, iOS, Windows, macOS, Linux</b>\\n"
        "<b>• Установка за 2 минуты</b>\\n"
        "<b>• Шифрование и защита трафика</b>\\n\\n"
        "<b>TREPOW VPN — доступ на выбранный срок</b>\\n"
        "<b>Пошаговая инструкция • Поддержка в Telegram</b>\\n"
        "<b>Безлимитный интернет без границ</b>"
    )
    if os.path.exists(START_PHOTO):
        await message.answer_photo(FSInputFile(START_PHOTO), caption=welcome_text,
                                   reply_markup=main_menu(message.from_user.id))
    else:
        await message.answer(welcome_text, reply_markup=main_menu(message.from_user.id))


@dp.callback_query(F.data == "check_subscription")
async def check_subscription(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await render_callback(call, "<b>Подписка подтверждена.</b>", main_menu(call.from_user.id))
        await call.answer()
    else:
        await call.answer("Сначала подпишись на @trepowvpn.", show_alert=True)


@dp.callback_query(F.data == "menu_trial")
async def menu_trial(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    if await db.has_used_trial(call.from_user.id):
        await call.answer("Пробный период уже использован. Он доступен один раз.", show_alert=True)
        return
    await call.answer("Создаю пробный доступ на 2 дня…")
    try:
        sub_url, total_days = await create_client_with_bonus(
            call.from_user.id, TRIAL_DAYS, -call.from_user.id, "_trial"
        )
        await db.mark_trial_used(call.from_user.id)
        await call.message.answer(
            f"<b>Твой пробный TREPOW VPN активирован на {total_days} дня.</b>\n\n"
            f"Ссылка подписки:\n<code>{sub_url}</code>\n\n"
            "Добавь ссылку в приложение Happ."
        )
    except Exception:
        logging.exception("Не удалось выдать пробный период")
        await call.message.answer("Не удалось создать пробный VPN. Попробуй позже или обратись в поддержку.")


@dp.callback_query(F.data == "menu_referral")
async def menu_referral(call: CallbackQuery):
    link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{call.from_user.id}"
    count = await db.get_referral_count(call.from_user.id)
    await render_callback(
        call,
        "<b>Реферальная программа TREPOW VPN</b>\n\n"
        "Приглашай друзей по своей ссылке. За каждого нового пользователя начисляется 2 дня VPN.\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено: <b>{count}</b>",
        back_keyboard()
    )
    await call.answer()


@dp.callback_query(F.data == "menu_promo")
async def menu_promo(call: CallbackQuery):
    user_states[call.from_user.id] = {"state": "promo"}
    await render_callback(call, "<b>Отправь промокод одним сообщением.</b>", back_keyboard())
    await call.answer()


@dp.message(Command("promo"))
async def promo_command(message: Message, command: CommandObject):
    if not command.args:
        user_states[message.from_user.id] = {"state": "promo"}
        await message.answer("Отправь промокод одним сообщением или используй /promo КОД.")
        return
    await redeem_promo_message(message, command.args.strip())


async def redeem_promo_message(message: Message, code: str):
    if not await is_subscribed(message.from_user.id):
        await subscription_required(message)
        return
    try:
        days = await db.redeem_promo(code.upper(), message.from_user.id)
        await extend_user_vpn_or_credit(message.from_user.id, days)
        await message.answer(f"Промокод активирован. Начислено {days} дней VPN.")
    except ValueError as error:
        await message.answer(str(error))
    except Exception:
        logging.exception("Ошибка активации промокода")
        await message.answer("Не удалось активировать промокод. Попробуй позже.")


@dp.message(Command("promo_create"))
async def promo_create(message: Message, command: CommandObject):
    if message.from_user.id != OWNER_ID:
        return
    parts = (command.args or "").split()
    if len(parts) not in (2, 3):
        await message.answer("Формат: <code>/promo_create КОД ДНИ [ЛИМИТ]</code>\nПример: <code>/promo_create WINTER 7 100</code>")
        return
    try:
        code, days = parts[0].upper(), int(parts[1])
        limit = int(parts[2]) if len(parts) == 3 else None
        if not code.isalnum() or not (1 <= days <= 365) or (limit is not None and limit < 1):
            raise ValueError
        await db.create_promo(code, days, limit)
        await message.answer(f"Промокод <code>{code}</code> создан на {days} дней. Лимит: {limit or 'без ограничений'}.")
    except ValueError:
        await message.answer("Проверь формат и значения. Дни: 1–365, лимит — от 1.")
    except Exception:
        logging.exception("Ошибка создания промокода")
        await message.answer("Такой промокод уже существует или произошла ошибка.")


@dp.callback_query(F.data == "menu_buy")
async def menu_buy(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    kb = plans_keyboard()
    kb.inline_keyboard.append([blue_button(text="⬅️ Назад", callback_data="back_home")])
    await render_callback(call, "<b>Выбери тариф TREPOW VPN:</b>", kb)
    await call.answer()


@dp.callback_query(F.data == "menu_tariffs")
async def menu_tariffs(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    text = "<b>Тарифы TREPOW VPN</b>\n\n" + "\n".join(
        f"{p['name']} — <b>{p['price']} ₽</b>" for p in PLANS.values()
    )
    await render_callback(call, text, back_keyboard())
    await call.answer()


@dp.callback_query(F.data == "menu_my_vpn")
async def menu_my_vpn(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    clients = await db.get_user_vpn(call.from_user.id)
    username = f"@{call.from_user.username}" if call.from_user.username else "не указан"
    text = (
        "<b>ВАШ АККАУНТ TREPOW VPN</b>\\n\\n"
        f"<b>Имя:</b> {call.from_user.full_name}\\n"
        f"<b>Username:</b> {username}\\n"
        f"<b>ID:</b> <code>{call.from_user.id}</code>\\n\\n"
    )
    if not clients:
        text += "<b>VPN-профиль пока не создан.</b>\\nВыбери тариф или активируй пробный период."
    else:
        client = clients[0]
        text += (
            "<b>Статус:</b> Активный профиль\\n"
            f"<b>Устройства:</b> 0 из {DEVICE_LIMIT}\\n"
            f"<b>Можно добавить:</b> {DEVICE_LIMIT}\\n\\n"
            "<b>Ссылка для подключения:</b>\\n"
            f"<code>{client['sub_url']}</code>\\n\\n"
            "<i>Важно: текущий H1 API не сообщает боту число реально подключённых устройств. "
            "Здесь указан лимит и доступные слоты.</i>"
        )
    await render_callback(call, text, back_keyboard())
    await call.answer()


@dp.callback_query(F.data == "back_home")
async def back_home(call: CallbackQuery):
    await render_callback(call, "<b>TREPOW VPN — главное меню</b>\\n\\nВыбери нужный раздел:",
                          main_menu(call.from_user.id))
    await call.answer()


@dp.callback_query(F.data == "menu_admin")
async def menu_admin(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.answer("<b>Панель администратора</b>", reply_markup=admin_keyboard())
    await call.answer()


@dp.callback_query(F.data == "admin_promo_help")
async def admin_promo_help(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.answer(
        "Создать промокод можно командой:\n"
        "<code>/promo_create КОД ДНИ [ЛИМИТ]</code>\n\n"
        "Например: <code>/promo_create TREPOW7 7 50</code>"
    )
    await call.answer()


@dp.callback_query(F.data.startswith("buy_"))
async def choose_plan(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    key = call.data.replace("buy_", "", 1)
    if key not in PLANS:
        await call.answer("Неизвестный тариф", show_alert=True)
        return
    plan = PLANS[key]
    order_id = await db.create_order(call.from_user.id, key, plan)
    user_states[call.from_user.id] = {"state": "payment", "order_id": order_id}
    await call.message.answer(
        f"<b>Заказ №{order_id}</b>\n\nТариф: {plan['name']}\n"
        f"Сумма к оплате: <b>{plan['price']} ₽</b>\nСрок: {plan['days']} дней\n\n"
        "<b>Оплата переводом через СБП</b>\n"
        "Телефон получателя: <code>+79876370928</code>\n"
        "Получатель: <b>Егор Н.</b>\n\n"
        f"Переведи ровно <b>{plan['price']} ₽</b> и отправь сюда скриншот или файл чека.\n"
        "VPN будет выдан после ручной проверки поступления денег.",
        reply_markup=payment_keyboard(order_id)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_order(call: CallbackQuery):
    try:
        order_id = int(call.data.split("_", 1)[1])
    except ValueError:
        await call.answer("Некорректный заказ", show_alert=True)
        return
    order = await db.get_order(order_id)
    if not order or order["user_id"] != call.from_user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order["status"] not in ("awaiting_proof", "review"):
        await call.answer("Этот заказ уже обрабатывается", show_alert=True)
        return
    await db.set_order_status(order_id, "cancelled")
    user_states.pop(call.from_user.id, None)
    await call.message.answer("Заказ отменён.")
    await call.answer()


@dp.message(F.photo)
async def payment_photo(message: Message):
    state = user_states.get(message.from_user.id)
    if not state or state.get("state") != "payment":
        return
    order_id = state["order_id"]
    order = await db.get_order(order_id)
    if not order or order["status"] != "awaiting_proof":
        await message.answer("Этот заказ уже не ожидает чек.")
        return
    file_id = message.photo[-1].file_id
    await db.add_payment_proof(order_id, file_id)
    user_states.pop(message.from_user.id, None)
    await bot.send_photo(OWNER_ID, file_id,
        caption=(f"<b>Новая заявка на оплату</b>\n\nЗаказ: #{order_id}\n"
                 f"Пользователь: {message.from_user.id}\nИмя: {message.from_user.full_name}\n"
                 f"Тариф: {order['plan_name']}\nСумма: {order['price']} ₽\n\n"
                 "Проверь поступление денег в банковском приложении."),
        reply_markup=admin_order_keyboard(order_id))
    await message.answer("Чек отправлен владельцу. После проверки оплаты тебе придёт сообщение.")


@dp.message(F.document)
async def payment_document(message: Message):
    state = user_states.get(message.from_user.id)
    if not state or state.get("state") != "payment":
        return
    order_id = state["order_id"]
    order = await db.get_order(order_id)
    if not order or order["status"] != "awaiting_proof":
        await message.answer("Этот заказ уже не ожидает чек.")
        return
    file_id = message.document.file_id
    await db.add_payment_proof(order_id, file_id)
    user_states.pop(message.from_user.id, None)
    await bot.send_document(OWNER_ID, file_id,
        caption=(f"<b>Новая заявка на оплату</b>\n\nЗаказ: #{order_id}\n"
                 f"Пользователь: {message.from_user.id}\nТариф: {order['plan_name']}\n"
                 f"Сумма: {order['price']} ₽\n\nПроверь оплату в банковском приложении."),
        reply_markup=admin_order_keyboard(order_id))
    await message.answer("Документ отправлен владельцу на проверку.")


@dp.callback_query(F.data.startswith("approve_"))
async def approve_payment(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        order_id = int(call.data.split("_", 1)[1])
        order = await db.get_order(order_id)
        if not order or order["status"] != "review":
            await call.answer("Заявка уже обработана", show_alert=True)
            return
        await db.set_order_status(order_id, "processing")
        sub_url, total_days = await create_client_with_bonus(
            order["user_id"], order["days"], order_id
        )
        await db.set_order_status(order_id, "paid")
        await bot.send_message(order["user_id"],
            f"<b>Оплата подтверждена!</b>\n\nVPN готов на {total_days} дней.\n\n"
            f"Ссылка подписки:\n<code>{sub_url}</code>\n\nДобавь ссылку в приложение Happ.")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(f"Заказ #{order_id} подтверждён. VPN создан.")
        await call.answer("Готово!")
    except Exception:
        logging.exception("Ошибка выдачи VPN")
        try:
            await db.set_order_status(order_id, "review")
        except Exception:
            pass
        await call.answer("Ошибка H1 API. Подробности смотри в логах Bothost.", show_alert=True)


@dp.callback_query(F.data.startswith("reject_"))
async def reject_payment(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    order_id = int(call.data.split("_", 1)[1])
    order = await db.get_order(order_id)
    if not order or order["status"] != "review":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    await db.set_order_status(order_id, "rejected")
    await bot.send_message(order["user_id"], "<b>Оплата не подтверждена.</b> Если ты оплатил заказ, свяжись с поддержкой.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Заявка отклонена")


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id == OWNER_ID:
        await message.answer("<b>Панель администратора</b>", reply_markup=admin_keyboard())


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    s = await db.get_stats()
    await call.message.answer(
        f"<b>Статистика бота</b>\n\nПользователей: {s['users']}\nЗаказов: {s['orders']}\n"
        f"Чеков на проверке: {s['pending']}\nВыдано VPN: {s['vpn_clients']}"
    )
    await call.answer()


@dp.callback_query(F.data == "admin_vpn_stats")
async def admin_vpn_stats(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    try:
        result = await h1_request("GET", "/clients")
        clients = result.get("clients", []) if isinstance(result, dict) else []
        await call.message.answer(f"<b>Статистика H1 VPN</b>\n\nВсего клиентов: {len(clients)}")
    except Exception as error:
        logging.exception("Ошибка статистики H1")
        await call.message.answer(f"Не удалось получить статистику H1: <code>{error}</code>")
    await call.answer()


@dp.callback_query(F.data == "admin_orders")
async def admin_orders(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    orders = await db.get_pending_orders()
    if not orders:
        await call.message.answer("Новых заявок нет.")
    for order in orders:
        await call.message.answer(
            f"<b>Заказ #{order['id']}</b>\nПользователь: {order['user_id']}\n"
            f"Тариф: {order['plan_name']}\nСумма: {order['price']} ₽",
            reply_markup=admin_order_keyboard(order["id"])
        )
    await call.answer()


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    user_states[OWNER_ID] = {"state": "broadcast"}
    await call.message.answer("Отправь сообщение для рассылки. Можно текст, фото или документ.")
    await call.answer()


@dp.message()
async def other_messages(message: Message):
    uid = message.from_user.id
    state = user_states.get(uid)
    if uid == OWNER_ID and state and state.get("state") == "broadcast":
        user_states.pop(OWNER_ID, None)
        user_ids = await db.get_user_ids()
        success = failed = 0
        status = await message.answer("Рассылка запущена…")
        for user_id in user_ids:
            try:
                await message.copy_to(user_id)
                success += 1
            except Exception:
                failed += 1
        await status.edit_text(f"<b>Рассылка завершена</b>\n\nУспешно: {success}\nОшибок: {failed}")
        return
    if state and state.get("state") == "promo":
        user_states.pop(uid, None)
        await redeem_promo_message(message, message.text.strip() if message.text else "")
        return
    if not await is_subscribed(uid):
        await subscription_required(message)
        return
    await message.answer("Выбери нужный раздел в меню TREPOW VPN.", reply_markup=main_menu(uid))


async def main():
    os.makedirs("data", exist_ok=True)
    await db.connect()
    await db.create_tables()
    logging.info("Trepow VPN bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
