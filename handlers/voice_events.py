import asyncio
import discord
from discord.ext import commands

from services.verification_service import VerificationService
from utils.logger import logger
from config.settings import settings

class VoiceEventHandler:
    """Обработчик событий голосовых каналов"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.verification_service = VerificationService()
    
    async def handle_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """Обработка обновления состояния в голосовом канале"""
        if member.bot:
            return
        
        if after.channel and after.channel.id == settings.voice_channel_id:
            await self._handle_user_joined(member, after.channel)
        
        if before.channel and before.channel.id == settings.voice_channel_id:
            await self._handle_user_left(member, before.channel)
    
    async def _handle_user_joined(self, member: discord.Member, channel: discord.VoiceChannel):
        """Обработка подключения пользователя к каналу верификации"""
        try:
            voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
            text_channel = self.bot.get_channel(settings.text_channel_id)
            
            if not text_channel:
                logger.error(f"❌ Текстовый канал с ID {settings.text_channel_id} не найден.")
                return
            
            if not voice_client:
                voice_client = await channel.connect()
                await asyncio.sleep(1)
                logger.info(f"🔊 Бот подключился к голосовому каналу: #{channel.name}")
            
            logger.info(f"🟢 Пользователь {member.display_name} присоединился к каналу верификации.")
            
            await self.verification_service.start_verification(
                member, voice_client, text_channel
            )
            
        except Exception as e:
            logger.error(f"💥 Ошибка при подключении пользователя {member.display_name}: {e}")
    
    async def _handle_user_left(self, member: discord.Member, channel: discord.VoiceChannel):
        """Обработка выхода пользователя из канала верификации"""
        try:
            voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
            if not voice_client or not voice_client.is_connected():
                return
            
            human_members = [m for m in channel.members if not m.bot]
            
            logger.info(f"🔴 Пользователь {member.display_name} покинул канал верификации.")
            
            if len(human_members) == 0:
                self.verification_service.cleanup_session(member.id)
                await voice_client.disconnect()
                logger.info(f"🔌 Бот отключился от голосового канала: #{channel.name} (канал пуст)")
                
        except Exception as e:
            logger.error(f"⚠️ Ошибка при выходе пользователя {member.display_name}: {e}")
