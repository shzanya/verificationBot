import discord
from discord.ext import commands

from handlers.voice_events import VoiceEventHandler
from utils.logger import logger
from config.settings import settings

class VerificationBot(commands.Bot):
    """Главный класс бота"""

    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix=settings.command_prefix,
            intents=intents,
            help_command=None
        )

        self.voice_handler = VoiceEventHandler(self)

    async def on_ready(self):
        """Вызывается, когда бот готов к работе"""
        logger.info(f"✅ Бот {self.user} успешно запущен!")
        logger.info(f"📡 Подключено к {len(self.guilds)} серверам.")

        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="голосовую верификацию"
        )
        await self.change_presence(activity=activity)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Обработка обновлений голосовых состояний"""
        await self.voice_handler.handle_voice_state_update(member, before, after)

    async def on_error(self, event: str, args, *kwargs):
        """Обработка ошибок"""
        logger.error(f"⚠️ Ошибка в событии '{event}': {args}")

    def run_bot(self):
        """Запуск бота"""
        if not settings.token:
            logger.error("❌ Токен бота не указан! Убедитесь, что переменная окружения настроена.")
            return

        try:
            logger.info("🔄 Попытка запуска бота...")
            self.run(settings.token)
        except Exception as e:
            logger.error(f"💥 Не удалось запустить бота: {e}")
