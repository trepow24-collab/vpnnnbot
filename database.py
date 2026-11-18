
import os
import aiosqlite
from datetime import datetime, timezone

from config import DB_PATH


class Database:
    def __init__(self):
        self.path = DB_PATH

    async def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.commit()

    async def create_tables(self):
        async with aiosqlite.connect(self.path) as db:

            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    joined_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    plan_key TEXT NOT NULL,
                    plan_name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    days INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'awaiting_proof',
                    proof_file_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS vpn_clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL UNIQUE,
                    client_name TEXT NOT NULL UNIQUE,
                    sub_url TEXT NOT NULL,
                    expires_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_orders_user
                ON orders(user_id)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_orders_status
                ON orders(status)
            """)

            await db.commit()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    async def add_user(self, user):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO users (
                    user_id, username, first_name, joined_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
            """, (
                user.id,
                user.username,
                user.first_name,
                self.now()
            ))
            await db.commit()

    async def get_user_ids(self):
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT user_id FROM users"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def create_order(self, user_id, plan_key, plan):
        now = self.now()

        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("""
                INSERT INTO orders (
                    user_id, plan_key, plan_name,
                    price, days, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'awaiting_proof', ?, ?)
            """, (
                user_id,
                plan_key,
                plan["name"],
                plan["price"],
                plan["days"],
                now,
                now
            ))

            await db.commit()
            return cursor.lastrowid

    async def get_order(self, order_id):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute("""
                SELECT * FROM orders WHERE id = ?
            """, (order_id,))

            row = await cursor.fetchone()
            return dict(row) if row else None

    async def add_payment_proof(self, order_id, file_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE orders
                SET proof_file_id = ?,
                    status = 'review',
                    updated_at = ?
                WHERE id = ?
                  AND status = 'awaiting_proof'
            """, (file_id, self.now(), order_id))

            await db.commit()

    async def get_pending_orders(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute("""
                SELECT * FROM orders
                WHERE status = 'review'
                ORDER BY id DESC
            """)

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def set_order_status(self, order_id, status):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE orders
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status, self.now(), order_id))

            await db.commit()

    async def save_vpn_client(
        self,
        user_id,
        order_id,
        client_name,
        sub_url,
        expires_at=None
    ):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO vpn_clients (
                    user_id, order_id, client_name,
                    sub_url, expires_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                order_id,
                client_name,
                sub_url,
                expires_at,
                self.now()
            ))

            await db.commit()

    async def get_user_vpn(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute("""
                SELECT * FROM vpn_clients
                WHERE user_id = ?
                ORDER BY id DESC
            """, (user_id,))

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_stats(self):
        async with aiosqlite.connect(self.path) as db:

            users = await db.execute_fetchall("""
                SELECT COUNT(*) FROM users
            """)

            orders = await db.execute_fetchall("""
                SELECT COUNT(*) FROM orders
            """)

            pending = await db.execute_fetchall("""
                SELECT COUNT(*) FROM orders
                WHERE status = 'review'
            """)

            vpn = await db.execute_fetchall("""
                SELECT COUNT(*) FROM vpn_clients
            """)

            return {
                "users": users[0][0],
                "orders": orders[0][0],
                "pending": pending[0][0],
                "vpn_clients": vpn[0][0]
            }
