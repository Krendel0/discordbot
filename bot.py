import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

from economy import EconomyDatabase

logging.basicConfig(level=logging.INFO)


def get_token() -> str:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable is required")
    return token


def human_timedelta(delta: timedelta) -> str:
    minutes, seconds = divmod(int(delta.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    if seconds or not parts:
        parts.append(f"{seconds}с")
    return " ".join(parts)


class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot, economy: EconomyDatabase) -> None:
        self.bot = bot
        self.economy = economy
        self.currency = "💰"

    async def cog_load(self) -> None:
        await self.economy.setup()

    @staticmethod
    def _resolve_amount(argument: str, available: int) -> Optional[int]:
        if argument.lower() in {"all", "max"}:
            return available if available > 0 else None
        try:
            amount = int(argument)
            if amount <= 0 or amount > available:
                return None
            return amount
        except ValueError:
            return None

    async def _balance_embed(self, member: discord.Member) -> discord.Embed:
        balance = await self.economy.get_user(member.id)
        embed = discord.Embed(title=f"Баланс {member.display_name}", color=discord.Color.blurple())
        embed.add_field(name="Наличные", value=f"{self.currency}{balance.cash}")
        embed.add_field(name="Банк", value=f"{self.currency}{balance.bank}")
        embed.add_field(name="Всего", value=f"{self.currency}{balance.cash + balance.bank}")
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed

    @commands.command(name="balance", aliases=["bal", "cash", "wallet"])
    async def balance(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        """Показать баланс пользователя."""
        target = member or ctx.author
        embed = await self._balance_embed(target)
        await ctx.send(embed=embed)

    @commands.command(name="daily")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def daily(self, ctx: commands.Context) -> None:
        """Ежедневная награда с откатом в 24 часа."""
        user = await self.economy.get_user(ctx.author.id)
        remaining = EconomyDatabase.remaining_cooldown(user.last_daily, timedelta(hours=24))
        if remaining:
            await ctx.send(
                f"Ты уже забирал ежедневную награду. Попробуй снова через {human_timedelta(remaining)}."
            )
            return

        reward = random.randint(250, 500)
        await self.economy.grant_cash(ctx.author.id, reward)
        await self.economy.set_last_daily(ctx.author.id, datetime.now(timezone.utc))
        await ctx.send(f"{ctx.author.mention}, ты получил {self.currency}{reward} за ежедневный бонус!")

    @commands.command(name="work")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def work(self, ctx: commands.Context) -> None:
        """Получить зарплату с часовым откатом."""
        user = await self.economy.get_user(ctx.author.id)
        remaining = EconomyDatabase.remaining_cooldown(user.last_work, timedelta(hours=1))
        if remaining:
            await ctx.send(f"Ты недавно работал. Возвращайся через {human_timedelta(remaining)}.")
            return

        reward = random.randint(50, 200)
        job = random.choice(
            [
                "бариста",
                "разработчик ботов",
                "стример",
                "повар",
                "дизайнер",
                "строитель",
            ]
        )
        await self.economy.grant_cash(ctx.author.id, reward)
        await self.economy.set_last_work(ctx.author.id, datetime.now(timezone.utc))
        await ctx.send(f"{ctx.author.mention} работал как {job} и заработал {self.currency}{reward}!")

    @commands.command(name="deposit", aliases=["dep"])
    async def deposit(self, ctx: commands.Context, amount: str) -> None:
        """Положить деньги в банк."""
        balance = await self.economy.get_user(ctx.author.id)
        resolved = self._resolve_amount(amount, balance.cash)
        if resolved is None:
            await ctx.send("Укажи сумму или `all`, чтобы внести всё.")
            return

        success = await self.economy.deposit(ctx.author.id, resolved)
        if success:
            await ctx.send(f"{ctx.author.mention} внёс {self.currency}{resolved} в банк.")
        else:
            await ctx.send("Недостаточно наличных для внесения.")

    @commands.command(name="withdraw", aliases=["with"])
    async def withdraw(self, ctx: commands.Context, amount: str) -> None:
        """Снять деньги из банка."""
        balance = await self.economy.get_user(ctx.author.id)
        resolved = self._resolve_amount(amount, balance.bank)
        if resolved is None:
            await ctx.send("Укажи сумму или `all`, чтобы снять всё.")
            return

        success = await self.economy.withdraw(ctx.author.id, resolved)
        if success:
            await ctx.send(f"{ctx.author.mention} снял {self.currency}{resolved} из банка.")
        else:
            await ctx.send("Недостаточно средств в банке.")

    @commands.command(name="pay", aliases=["give", "transfer"])
    async def pay(self, ctx: commands.Context, member: discord.Member, amount: str) -> None:
        """Перевести наличные другому участнику."""
        if member.id == ctx.author.id:
            await ctx.send("Нельзя переводить деньги самому себе.")
            return

        balance = await self.economy.get_user(ctx.author.id)
        resolved = self._resolve_amount(amount, balance.cash)
        if resolved is None:
            await ctx.send("Укажи сумму или `all`, доступную для перевода.")
            return

        success = await self.economy.transfer(ctx.author.id, member.id, resolved)
        if success:
            await ctx.send(
                f"{ctx.author.mention} перевёл {self.currency}{resolved} пользователю {member.mention}."
            )
        else:
            await ctx.send("Недостаточно средств для перевода.")

    @commands.command(name="leaderboard", aliases=["top"])
    async def leaderboard(self, ctx: commands.Context) -> None:
        """Топ пользователей по общему капиталу."""
        leaders = await self.economy.leaderboard(limit=10)
        description_lines = []
        for index, user in enumerate(leaders, start=1):
            member = ctx.guild.get_member(user.user_id) if ctx.guild else None
            name = member.display_name if member else f"User {user.user_id}"
            total = user.cash + user.bank
            description_lines.append(
                f"**{index}.** {name} — {self.currency}{total} (нал: {self.currency}{user.cash}, банк: {self.currency}{user.bank})"
            )

        if not description_lines:
            await ctx.send("Пока нет данных для таблицы лидеров.")
            return

        embed = discord.Embed(title="Таблица лидеров", description="\n".join(description_lines))
        await ctx.send(embed=embed)


async def main() -> None:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, description="Экономика как UnbelievaBoat")
    economy = EconomyDatabase()
    await bot.add_cog(EconomyCog(bot, economy))

    @bot.event
    async def on_ready() -> None:
        logging.info("Logged in as %s", bot.user)

    await bot.start(get_token())


if __name__ == "__main__":
    asyncio.run(main())
