import aiosqlite
from datetime import datetime, timedelta, timezone


class Database:
    def __init__(self, db_path=None):
        # Поддерживает старый config.py с DB_PATH и стандартный путь data/bot.db.
        if db_path is None:
            try:
                from config import DB_PATH
                db_path = DB_PATH
            except Exception:
                db_path = "data/bot.db"
        self.db_path = db_path
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")

    async def create_tables(self):
        await self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_key TEXT,
            plan_name TEXT,
            price INTEGER,
            days INTEGER,
            status TEXT DEFAULT 'awaiting_proof',
            proof_file_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vpn_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_id INTEGER UNIQUE,
            client_name TEXT UNIQUE,
            sub_url TEXT,
            expires_at TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS trials (
            user_id INTEGER PRIMARY KEY,
            used_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS referrals (
            referred_id INTEGER PRIMARY KEY,
            referrer_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bonus_days (
            user_id INTEGER PRIMARY KEY,
            days INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            days INTEGER NOT NULL,
            max_uses INTEGER,
            used_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            redeemed_at TEXT NOT NULL,
            PRIMARY KEY (code, user_id)
        );
        """)
        # Миграция для старой таблицы vpn_clients без expires_at.
        columns = await (await self.conn.execute("PRAGMA table_info(vpn_clients)")).fetchall()
        names = {row["name"] for row in columns}
        if "expires_at" not in names:
            await self.conn.execute("ALTER TABLE vpn_clients ADD COLUMN expires_at TEXT")
        await self.conn.commit()

    async def add_user(self, user):
        cur = await self.conn.execute("SELECT 1 FROM users WHERE user_id=?", (user.id,))
        exists = await cur.fetchone()
        await self.conn.execute(
            "INSERT OR IGNORE INTO users(user_id, username, first_name, joined_at) VALUES(?,?,?,?)",
            (user.id, user.username, user.first_name, datetime.now(timezone.utc).isoformat())
        )
        await self.conn.commit()
        return exists is None

    async def get_user_ids(self):
        cur = await self.conn.execute("SELECT user_id FROM users")
        return [r["user_id"] for r in await cur.fetchall()]

    async def create_order(self, user_id, plan_key, plan):
        now = datetime.now(timezone.utc).isoformat()
        cur = await self.conn.execute(
            "INSERT INTO orders(user_id,plan_key,plan_name,price,days,status,created_at,updated_at) VALUES(?,?,?,?,?,'awaiting_proof',?,?)",
            (user_id, plan_key, plan["name"], plan["price"], plan["days"], now, now)
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_order(self, order_id):
        cur = await self.conn.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def add_payment_proof(self, order_id, file_id):
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute("UPDATE orders SET proof_file_id=?, status='review', updated_at=? WHERE id=?",
                                (file_id, now, order_id))
        await self.conn.commit()

    async def get_pending_orders(self):
        cur = await self.conn.execute("SELECT * FROM orders WHERE status='review' ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]

    async def set_order_status(self, order_id, status):
        await self.conn.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?",
                                (status, datetime.now(timezone.utc).isoformat(), order_id))
        await self.conn.commit()

    async def save_vpn_client(self, user_id, order_id, client_name, sub_url, days=0):
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=int(days))).isoformat() if days else None
        await self.conn.execute(
            "INSERT INTO vpn_clients(user_id,order_id,client_name,sub_url,expires_at,created_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(order_id) DO UPDATE SET client_name=excluded.client_name, sub_url=excluded.sub_url, expires_at=excluded.expires_at",
            (user_id, order_id, client_name, sub_url, expires, now.isoformat())
        )
        await self.conn.commit()

    async def get_user_vpn(self, user_id):
        cur = await self.conn.execute("SELECT * FROM vpn_clients WHERE user_id=? ORDER BY id DESC", (user_id,))
        return [dict(r) for r in await cur.fetchall()]

    async def get_stats(self):
        async def count(sql):
            cur = await self.conn.execute(sql)
            row = await cur.fetchone()
            return row[0]
        return {
            "users": await count("SELECT COUNT(*) FROM users"),
            "orders": await count("SELECT COUNT(*) FROM orders"),
            "pending": await count("SELECT COUNT(*) FROM orders WHERE status='review'"),
            "vpn_clients": await count("SELECT COUNT(*) FROM vpn_clients")
        }

    async def has_used_trial(self, user_id):
        cur = await self.conn.execute("SELECT 1 FROM trials WHERE user_id=?", (user_id,))
        return await cur.fetchone() is not None

    async def mark_trial_used(self, user_id):
        await self.conn.execute("INSERT OR IGNORE INTO trials(user_id,used_at) VALUES(?,?)",
                                (user_id, datetime.now(timezone.utc).isoformat()))
        await self.conn.commit()

    async def add_referral(self, referrer_id, referred_id):
        if referrer_id == referred_id:
            return False
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO referrals(referred_id,referrer_id,created_at) VALUES(?,?,?)",
            (referred_id, referrer_id, datetime.now(timezone.utc).isoformat())
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def get_referral_count(self, referrer_id):
        cur = await self.conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (referrer_id,))
        row = await cur.fetchone()
        return row[0]

    async def add_bonus_days(self, user_id, days):
        await self.conn.execute(
            "INSERT INTO bonus_days(user_id,days) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET days=days+excluded.days",
            (user_id, int(days))
        )
        await self.conn.commit()

    async def get_bonus_days(self, user_id):
        cur = await self.conn.execute("SELECT days FROM bonus_days WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return int(row["days"]) if row else 0

    async def clear_bonus_days(self, user_id):
        await self.conn.execute("DELETE FROM bonus_days WHERE user_id=?", (user_id,))
        await self.conn.commit()

    async def add_vpn_days(self, user_id, days):
        # Сохраняем локальную дату окончания как ориентир; реальное продление выполняет H1 API.
        cur = await self.conn.execute("SELECT id,expires_at FROM vpn_clients WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        row = await cur.fetchone()
        if not row:
            return
        now = datetime.now(timezone.utc)
        try:
            current = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else now
        except ValueError:
            current = now
        base = max(current, now)
        expires = (base + timedelta(days=int(days))).isoformat()
        await self.conn.execute("UPDATE vpn_clients SET expires_at=? WHERE id=?", (expires, row["id"]))
        await self.conn.commit()

    async def create_promo(self, code, days, max_uses=None):
        await self.conn.execute(
            "INSERT INTO promo_codes(code,days,max_uses,created_at) VALUES(?,?,?,?)",
            (code.upper(), int(days), max_uses, datetime.now(timezone.utc).isoformat())
        )
        await self.conn.commit()

    async def redeem_promo(self, code, user_id):
        code = code.upper().strip()
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self.conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,))
            promo = await cur.fetchone()
            if not promo:
                raise ValueError("Промокод не найден.")
            if promo["max_uses"] is not None and promo["used_count"] >= promo["max_uses"]:
                raise ValueError("Лимит активаций этого промокода исчерпан.")
            cur = await self.conn.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?", (code, user_id))
            if await cur.fetchone():
                raise ValueError("Ты уже использовал этот промокод.")
            await self.conn.execute("INSERT INTO promo_redemptions(code,user_id,redeemed_at) VALUES(?,?,?)",
                                    (code, user_id, datetime.now(timezone.utc).isoformat()))
            await self.conn.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
            await self.conn.commit()
            return int(promo["days"])
        except Exception:
            await self.conn.rollback()
            raise
