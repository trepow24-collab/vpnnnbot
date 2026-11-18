
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "8799714877"))

CHANNEL_ID = os.getenv("CHANNEL_ID", "@trepowvpn")
CHANNEL_URL = "https://t.me/trepowvpn"

SITE_URL = os.getenv("SITE_URL", "")
SUPPORT_URL = "https://t.me/durov_xyesos"

H1_API_BASE = os.getenv(
    "H1_API_BASE",
    "https://s-25988.de4.h1clayd.click/api"
)

H1_API_TOKEN = os.getenv("H1_API_TOKEN", "")
H1_AUTH_HEADER = os.getenv("H1_AUTH_HEADER", "X-API-Key")

H1_INBOUND_IDS = [
    item.strip()
    for item in os.getenv(
        "H1_INBOUND_IDS",
        "ib_61328408c4"
    ).split(",")
    if item.strip()
]

PLANS = {
    "month": {
        "name": "1 месяц",
        "days": 30,
        "price": 160
    },
    "3months": {
        "name": "3 месяца",
        "days": 90,
        "price": 400
    },
    "6months": {
        "name": "6 месяцев",
        "days": 180,
        "price": 850
    },
    "year": {
        "name": "12 месяцев",
        "days": 365,
        "price": 1400
    }
}

DB_PATH = "data/trepow.sqlite3"
