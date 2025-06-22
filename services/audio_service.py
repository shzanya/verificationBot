import os
import asyncio
import discord
from utils.logger import logger
from core.exceptions import AudioFileNotFoundException

class AudioService:
    """Сервис для управления воспроизведением аудио"""
   
    @staticmethod
    async def play_audio_file(
        voice_client: discord.VoiceClient,
        file_path: str,
        timeout: int = 30
    ) -> bool:
        """Воспроизвести аудиофайл через голосовой клиент"""
        try:
            if not voice_client or not voice_client.is_connected():
                logger.warning("🔇 Голосовой клиент не подключён.")
                return False
           
            if not os.path.exists(file_path):
                raise AudioFileNotFoundException(f"📁 Файл не найден: {file_path}")
           
            # Проверяем размер файла
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f"📁 Файл пуст: {file_path}")
                return False
           
            source = discord.FFmpegPCMAudio(file_path)
            voice_client.play(source)
            logger.info(f"🎵 Начато воспроизведение: {os.path.basename(file_path)} ({file_size} bytes)")
            
            # Ожидание завершения воспроизведения с таймаутом
            start_time = asyncio.get_event_loop().time()
            while voice_client.is_playing():
                if asyncio.get_event_loop().time() - start_time > timeout:
                    voice_client.stop()
                    logger.warning(f"⏱️ Таймаут: воспроизведение остановлено — {os.path.basename(file_path)}")
                    return False
                await asyncio.sleep(0.1)
           
            logger.success(f"✅ Аудио успешно воспроизведено: {os.path.basename(file_path)}")
            return True
            
        except AudioFileNotFoundException as e:
            logger.error(f"📂 Аудиофайл не найден: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка при воспроизведении: {e}")
            return False
    
    @staticmethod
    async def play_question_audio(
        voice_client: discord.VoiceClient,
        question_text: str,
        audio_files: dict
    ) -> bool:
        """Воспроизвести аудио для указанного вопроса"""
        file_path = audio_files.get(question_text)
        if not file_path:
            logger.warning(f"🎤 Нет аудиофайла для вопроса: «{question_text}»")
            return False
       
        return await AudioService.play_audio_file(voice_client, file_path)
