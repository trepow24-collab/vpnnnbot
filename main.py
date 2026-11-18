
import asyncio
import logging
import os
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import (
    BOT_TOKEN,
    OWNER_ID,
    CHANNEL_ID,
    CHANNEL_URL,
    SITE_URL,
    SUPPORT_URL,
    H1_API_BASE,
    H1_API_TOKEN,
    H1_AUTH_HEADER,
    H1_INBOUND_IDS,
    PLANS,
    DB_PATH
)
from database import Database

# Обязательная подписка на официальный канал TREPOW VPN
CHANNEL_ID = "@trepowvpn"
CHANNEL_URL = "https://t.me/trepowvpn"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

if not BOT_TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN в .env")

if not H1_API_TOKEN:
    logging.warning("Не указан H1_API_TOKEN. Создание VPN работать не будет.")


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
db = Database()

# Временное хранение состояний пользователя
user_states = {}


def main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton(
            text="Купить VPN",
            callback_data="menu_buy",
            style="primary"
        )],
        [InlineKeyboardButton(
            text="Мой VPN",
            callback_data="menu_my_vpn",
            style="primary"
        )],
        [InlineKeyboardButton(
            text="Тарифы",
            callback_data="menu_tariffs",
            style="primary"
        )],
    ]

    if SITE_URL:
        keyboard.append([
            InlineKeyboardButton(
                text="Наш сайт",
                url=SITE_URL,
                style="primary"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text="Наш сайт",
                callback_data="menu_site_missing",
                style="primary"
            )
        ])

    if SUPPORT_URL:
        keyboard.append([
            InlineKeyboardButton(
                text="Поддержка",
                url=SUPPORT_URL,
                style="primary"
            )
        ])

    if user_id == OWNER_ID:
        keyboard.append([
            InlineKeyboardButton(
                text="Админ-панель",
                callback_data="menu_admin",
                style="primary"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Подписаться на канал",
            url=CHANNEL_URL
        )],
        [InlineKeyboardButton(
            text="Проверить подписку",
            callback_data="check_subscription"
        )]
    ])


def plans_keyboard():
    buttons = []

    for key, plan in PLANS.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"{plan['name']} — {plan['price']} ₽",
                callback_data=f"buy_{key}"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ Отменить заказ",
            callback_data=f"cancel_{order_id}"
        )]
    ])


def admin_order_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить оплату",
                callback_data=f"approve_{order_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject_{order_id}"
            )
        ]
    ])


async def is_subscribed(user_id):
    if user_id == OWNER_ID:
        return True

    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )

        return (
            member.status in ("creator", "administrator", "member")
            or (
                member.status == "restricted"
                and member.is_member
            )
        )

    except Exception:
        logging.exception("Ошибка проверки подписки")
        return False


async def subscription_required(message):
    if await is_subscribed(message.from_user.id):
        return True

    await message.answer(
        "<b>Для доступа к TREPOW VPN необходимо подписаться на наш канал.</b>\n\n"
        "<b>1. Нажми кнопку «Подписаться на канал».</b>\n"
        "<b>2. Подпишись на @trepowvpn.</b>\n"
        "<b>3. Вернись сюда и нажми «Проверить подписку».</b>",
        reply_markup=subscription_keyboard()
    )
    return False


async def h1_request(method, endpoint, payload=None):
    if not H1_API_TOKEN:
        raise RuntimeError("Не задан H1_API_TOKEN")

    headers = {
        H1_AUTH_HEADER: H1_API_TOKEN,
        "Content-Type": "application/json"
    }

    url = f"{H1_API_BASE.rstrip('/')}/{endpoint.lstrip('/')}"

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            url,
            headers=headers,
            json=payload
        ) as response:

            data = await response.json(content_type=None)

            if response.status >= 400:
                raise RuntimeError(
                    f"H1 API HTTP {response.status}: {data}"
                )

            if isinstance(data, dict) and data.get("ok") is False:
                raise RuntimeError(str(data))

            return data


async def create_h1_client(order):
    client_name = f"tg_{order['user_id']}_{order['id']}"

    payload = {
        "name": client_name,
        "days": order["days"],
        "device_limit": 2,
        "traffic_limit_gb": 0,
        "manual": True,
        "inbound_ids": H1_INBOUND_IDS
    }

    result = await h1_request(
        "POST",
        "/create",
        payload
    )

    client = result.get("client", result)

    sub_url = (
        client.get("sub_url")
        or client.get("subscription_url")
        or client.get("url")
    )

    if not sub_url:
        raise RuntimeError(
            f"H1 API не вернул ссылку подписки: {result}"
        )

    await db.save_vpn_client(
        user_id=order["user_id"],
        order_id=order["id"],
        client_name=client_name,
        sub_url=sub_url
    )

    return sub_url


@dp.message(CommandStart())
async def start(message: Message):
    await db.add_user(message.from_user)

    # Удаляем старую нижнюю клавиатуру без отправки пустого сообщения.
    # Telegram запрещает пустой текст, поэтому временное сообщение сразу удаляем.
    cleanup_message = await message.answer(
        "Обновляю меню…",
        reply_markup=ReplyKeyboardRemove()
    )
    try:
        await cleanup_message.delete()
    except Exception:
        logging.debug("Не удалось удалить временное сообщение обновления меню")

    if not await subscription_required(message):
        return

    await message.answer(
        "<b>TREPOW VPN — свобода в интернете без границ</b>\n\n"
        "<b>Что ты получаешь:</b>\n"
        "<b>• Доступ ко всем сайтам, Telegram, YouTube, Twitch, Discord</b>\n"
        "<b>• Быстрый и стабильный интернет</b>\n"
        "<b>• Поддержка Android, iOS, Windows, macOS, Linux</b>\n"
        "<b>• Установка за 2 минуты</b>\n"
        "<b>• Шифрование и защита трафика</b>\n\n"
        "<b>После активации:</b>\n"
        "<b>TREPOW VPN — доступ на выбранный срок</b>\n"
        "<b>Пошаговая инструкция</b>\n"
        "<b>Поддержка в Telegram</b>\n"
        "<b>Безлимитный интернет без границ</b>",
        reply_markup=main_menu(message.from_user.id)
    )


@dp.callback_query(F.data == "check_subscription")
async def check_subscription(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.message.answer(
            "<b>Подписка подтверждена.</b>\n"
            "<b>Теперь можешь пользоваться TREPOW VPN.</b>",
            reply_markup=main_menu(call.from_user.id)
        )
    else:
        await call.answer(
            "Сначала подпишись на канал @trepowvpn, затем нажми проверку.",
            show_alert=True
        )


@dp.callback_query(F.data == "menu_buy")
async def menu_buy(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    await call.message.answer(
        "<b>Выбери тариф TREPOW VPN:</b>",
        reply_markup=plans_keyboard()
    )
    await call.answer()


@dp.callback_query(F.data == "menu_tariffs")
async def menu_tariffs(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    text = "<b>Тарифы TREPOW VPN</b>\n\n"
    for plan in PLANS.values():
        text += f"{plan['name']} — <b>{plan['price']} ₽</b>\n"
    await call.message.answer(text)
    await call.answer()


@dp.callback_query(F.data == "menu_my_vpn")
async def menu_my_vpn(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer("Сначала подпишись на канал!", show_alert=True)
        return
    clients = await db.get_user_vpn(call.from_user.id)
    if not clients:
        await call.message.answer("У тебя пока нет активных VPN-профилей.")
    else:
        for client in clients:
            await call.message.answer(
                "<b>Твой VPN-профиль</b>\n\n"
                f"Ссылка подписки:\n{client['sub_url']}\n\n"
                "Устройства: 2"
            )
    await call.answer()


@dp.callback_query(F.data == "menu_site_missing")
async def menu_site_missing(call: CallbackQuery):
    await call.answer("Ссылка на сайт пока не указана в настройках.", show_alert=True)


@dp.callback_query(F.data == "menu_admin")
async def menu_admin(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Статистика бота", callback_data="admin_stats")],
        [InlineKeyboardButton(text="Статистика VPN", callback_data="admin_vpn_stats")],
        [InlineKeyboardButton(text="Ожидающие заявки", callback_data="admin_orders")],
        [InlineKeyboardButton(text="Рассылка", callback_data="admin_broadcast")]
    ])
    await call.message.answer("<b>Панель администратора</b>", reply_markup=keyboard)
    await call.answer()


@dp.callback_query(F.data.startswith("buy_"))
async def choose_plan(call: CallbackQuery):
    if not await is_subscribed(call.from_user.id):
        await call.answer(
            "Сначала подпишись на канал @trepowvpn, затем нажми проверку.",
            show_alert=True
        )
        return

    plan_key = call.data.replace("buy_", "", 1)

    if plan_key not in PLANS:
        await call.answer("Неизвестный тариф", show_alert=True)
        return

    plan = PLANS[plan_key]

    order_id = await db.create_order(
        call.from_user.id,
        plan_key,
        plan
    )

    user_states[call.from_user.id] = {
        "state": "payment",
        "order_id": order_id
    }

    await call.message.answer(
        f"<b>Заказ №{order_id}</b>\n\n"
        f"Тариф: {plan['name']}\n"
        f"Сумма к оплате: <b>{plan['price']} ₽</b>\n"
        f"Срок: {plan['days']} дней\n\n"
        "<b>Оплата переводом через СБП</b>\n"
        "Телефон получателя: <code>+79876370928</code>\n"
        "Получатель: <b>Егор Н.</b>\n\n"
        f"Переведи ровно <b>{plan['price']} ₽</b> и после оплаты отправь сюда скриншот или файл чека.\n\n"
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

    await call.message.answer("❌ Заказ отменён.")
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

    await bot.send_photo(
        OWNER_ID,
        file_id,
        caption=(
            f"🧾 <b>Новая заявка на оплату</b>\n\n"
            f"Заказ: #{order_id}\n"
            f"Пользователь: {message.from_user.id}\n"
            f"Имя: {message.from_user.full_name}\n"
            f"Тариф: {order['plan_name']}\n"
            f"Сумма: {order['price']} ₽\n\n"
            "Проверь поступление денег в банковском приложении."
        ),
        reply_markup=admin_order_keyboard(order_id)
    )

    await message.answer(
        "✅ Чек отправлен владельцу!\n\n"
        "После проверки оплаты тебе придёт сообщение."
    )


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

    await bot.send_document(
        OWNER_ID,
        file_id,
        caption=(
            f"🧾 <b>Новая заявка на оплату</b>\n\n"
            f"Заказ: #{order_id}\n"
            f"Пользователь: {message.from_user.id}\n"
            f"Тариф: {order['plan_name']}\n"
            f"Сумма: {order['price']} ₽\n\n"
            "Проверь оплату в банковском приложении."
        ),
        reply_markup=admin_order_keyboard(order_id)
    )

    await message.answer(
        "✅ Документ отправлен владельцу на проверку."
    )


@dp.callback_query(F.data.startswith("approve_"))
async def approve_payment(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split("_", 1)[1])
    order = await db.get_order(order_id)

    if not order or order["status"] != "review":
        await call.answer("Заявка уже обработана", show_alert=True)
        return

    await db.set_order_status(order_id, "processing")

    try:
        sub_url = await create_h1_client(order)

        await db.set_order_status(order_id, "paid")

        await bot.send_message(
            order["user_id"],
            "🎉 <b>Оплата подтверждена!</b>\n\n"
            f"Твой VPN на {order['days']} дней готов.\n\n"
            f"🔗 <b>Ссылка подписки:</b>\n{sub_url}\n\n"
            "Добавь ссылку в приложение Happ."
        )

        await call.message.edit_reply_markup(
            reply_markup=None
        )

        await call.message.answer(
            f"✅ Заказ #{order_id} подтверждён. VPN создан."
        )

        await call.answer("Готово!")

    except Exception:
        logging.exception("Ошибка выдачи VPN")

        await db.set_order_status(order_id, "review")

        await call.answer(
            "Ошибка H1 API. Заявка возвращена на проверку.",
            show_alert=True
        )


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

    await bot.send_message(
        order["user_id"],
        "❌ <b>Оплата не подтверждена.</b>\n\n"
        "Если ты оплатил заказ, свяжись с поддержкой."
    )

    await call.message.edit_reply_markup(
        reply_markup=None
    )

    await call.answer("Заявка отклонена")


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📊 Статистика бота",
            callback_data="admin_stats"
        )],
        [InlineKeyboardButton(
            text="🛡 Статистика VPN",
            callback_data="admin_vpn_stats"
        )],
        [InlineKeyboardButton(
            text="🧾 Ожидающие заявки",
            callback_data="admin_orders"
        )],
        [InlineKeyboardButton(
            text="📢 Рассылка",
            callback_data="admin_broadcast"
        )]
    ])

    await message.answer(
        "👑 <b>Панель администратора</b>",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    stats = await db.get_stats()

    await call.message.answer(
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: {stats['users']}\n"
        f"🧾 Всего заказов: {stats['orders']}\n"
        f"⏳ Чеков на проверке: {stats['pending']}\n"
        f"🛡 Выдано VPN: {stats['vpn_clients']}"
    )

    await call.answer()


@dp.callback_query(F.data == "admin_vpn_stats")
async def admin_vpn_stats(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    try:
        result = await h1_request("GET", "/clients")

        clients = result.get("clients", [])

        await call.message.answer(
            "🛡 <b>Статистика H1 VPN</b>\n\n"
            f"Всего клиентов: {len(clients)}\n\n"
            "Для точной статистики активных и истёкших "
            "профилей потребуется обработка полей ответа H1 API."
        )

    except Exception as error:
        logging.exception("Ошибка статистики H1")
        await call.message.answer(
            f"❌ Не удалось получить статистику H1:\n{error}"
        )

    await call.answer()


@dp.callback_query(F.data == "admin_orders")
async def admin_orders(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    orders = await db.get_pending_orders()

    if not orders:
        await call.message.answer("Новых заявок нет.")
        await call.answer()
        return

    for order in orders:
        text = (
            f"🧾 <b>Заказ #{order['id']}</b>\n"
            f"Пользователь: {order['user_id']}\n"
            f"Тариф: {order['plan_name']}\n"
            f"Сумма: {order['price']} ₽"
        )

        keyboard = admin_order_keyboard(order["id"])

        if order["proof_file_id"]:
            await bot.send_message(
                OWNER_ID,
                text,
                reply_markup=keyboard
            )
        else:
            await call.message.answer(text, reply_markup=keyboard)

    await call.answer()


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    user_states[OWNER_ID] = {
        "state": "broadcast"
    }

    await call.message.answer(
        "📢 Отправь сообщение для рассылки.\n"
        "Можно отправить текст, фото или документ."
    )

    await call.answer()


@dp.message()
async def other_messages(message: Message):
    state = user_states.get(message.from_user.id)

    if (
        message.from_user.id == OWNER_ID
        and state
        and state.get("state") == "broadcast"
    ):
        user_states.pop(OWNER_ID, None)

        user_ids = await db.get_user_ids()

        success = 0
        failed = 0

        status_message = await message.answer(
            "📢 Рассылка запущена..."
        )

        for user_id in user_ids:
            try:
                await message.copy_to(user_id)
                success += 1
            except Exception:
                failed += 1

        await status_message.edit_text(
            "✅ <b>Рассылка завершена</b>\n\n"
            f"Успешно: {success}\n"
            f"Ошибок: {failed}"
        )
        return

    if not await is_subscribed(message.from_user.id):
        await subscription_required(message)
        return

    await message.answer(
        "Выбери нужный раздел в меню TREPOW VPN 👇",
        reply_markup=main_menu(message.from_user.id)
    )


async def main():
    os.makedirs("data", exist_ok=True)

    await db.connect()
    await db.create_tables()

    logging.info("Trepow VPN bot started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
