import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import aiosqlite


@dataclass
class UserBalance:
    user_id: int
    cash: int
    bank: int
    last_daily: Optional[datetime]
    last_work: Optional[datetime]


class EconomyDatabase:
    def __init__(self, database_path: str = "data/economy.db") -> None:
        self.database_path = database_path
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    cash INTEGER NOT NULL DEFAULT 0,
                    bank INTEGER NOT NULL DEFAULT 0,
                    last_daily TEXT,
                    last_work TEXT
                )
                """
            )
            await db.commit()

    async def ensure_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, cash, bank) VALUES (?, 0, 0)",
                (user_id,),
            )
            await db.commit()

    async def get_user(self, user_id: int) -> UserBalance:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.database_path) as db:
            async with db.execute(
                "SELECT user_id, cash, bank, last_daily, last_work FROM users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()

        last_daily = (
            datetime.fromisoformat(row[3]).replace(tzinfo=timezone.utc) if row[3] else None
        )
        last_work = (
            datetime.fromisoformat(row[4]).replace(tzinfo=timezone.utc) if row[4] else None
        )
        return UserBalance(user_id=row[0], cash=row[1], bank=row[2], last_daily=last_daily, last_work=last_work)

    async def _update_balance(self, user_id: int, cash_delta: int = 0, bank_delta: int = 0) -> None:
        async with self._lock:
            await self.ensure_user(user_id)
            async with aiosqlite.connect(self.database_path) as db:
                await db.execute(
                    "UPDATE users SET cash = cash + ?, bank = bank + ? WHERE user_id = ?",
                    (cash_delta, bank_delta, user_id),
                )
                await db.commit()

    async def deposit(self, user_id: int, amount: int) -> bool:
        user = await self.get_user(user_id)
        if amount <= 0 or user.cash < amount:
            return False
        await self._update_balance(user_id, cash_delta=-amount, bank_delta=amount)
        return True

    async def withdraw(self, user_id: int, amount: int) -> bool:
        user = await self.get_user(user_id)
        if amount <= 0 or user.bank < amount:
            return False
        await self._update_balance(user_id, cash_delta=amount, bank_delta=-amount)
        return True

    async def grant_cash(self, user_id: int, amount: int) -> None:
        await self._update_balance(user_id, cash_delta=amount)

    async def transfer(self, sender_id: int, recipient_id: int, amount: int) -> bool:
        if amount <= 0:
            return False

        async with self._lock:
            sender = await self.get_user(sender_id)
            if sender.cash < amount:
                return False

            async with aiosqlite.connect(self.database_path) as db:
                await db.execute("UPDATE users SET cash = cash - ? WHERE user_id = ?", (amount, sender_id))
                await db.execute(
                    "INSERT INTO users (user_id, cash, bank) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET cash = cash + excluded.cash",
                    (recipient_id, amount, 0),
                )
                await db.commit()
        return True

    async def set_last_daily(self, user_id: int, timestamp: datetime) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.database_path) as db:
                await db.execute(
                    "UPDATE users SET last_daily = ? WHERE user_id = ?",
                    (timestamp.replace(tzinfo=timezone.utc).isoformat(), user_id),
                )
                await db.commit()

    async def set_last_work(self, user_id: int, timestamp: datetime) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.database_path) as db:
                await db.execute(
                    "UPDATE users SET last_work = ? WHERE user_id = ?",
                    (timestamp.replace(tzinfo=timezone.utc).isoformat(), user_id),
                )
                await db.commit()

    async def leaderboard(self, limit: int = 10) -> List[UserBalance]:
        async with aiosqlite.connect(self.database_path) as db:
            async with db.execute(
                "SELECT user_id, cash, bank, last_daily, last_work FROM users ORDER BY cash + bank DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()

        leaders: List[UserBalance] = []
        for row in rows:
            last_daily = (
                datetime.fromisoformat(row[3]).replace(tzinfo=timezone.utc) if row[3] else None
            )
            last_work = (
                datetime.fromisoformat(row[4]).replace(tzinfo=timezone.utc) if row[4] else None
            )
            leaders.append(
                UserBalance(
                    user_id=row[0],
                    cash=row[1],
                    bank=row[2],
                    last_daily=last_daily,
                    last_work=last_work,
                )
            )
        return leaders

    @staticmethod
    def remaining_cooldown(last_time: Optional[datetime], cooldown: timedelta) -> Optional[timedelta]:
        if last_time is None:
            return None
        now = datetime.now(timezone.utc)
        elapsed = now - last_time
        if elapsed >= cooldown:
            return None
        return cooldown - elapsed
