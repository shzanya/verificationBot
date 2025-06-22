import os
import asyncio
from typing import Callable
from datetime import datetime
import discord
from discord.sinks import WaveSink
from utils.logger import logger
from utils.helpers import sanitize_filename
from core.exceptions import RecordingException

class CustomWaveSink(WaveSink):
    """Кастомный Sink для записи аудио"""
    def __init__(self):
        super().__init__()

class RecordingService:
    """Сервис управления голосовыми записями"""
   
    def __init__(self):
        self.active_recordings = {}
   
    async def start_recording(
        self,
        voice_client: discord.VoiceClient,
        duration: int,
        callback: Callable,
        session_id: str
    ) -> bool:
        """Запустить запись на заданную длительность"""
        try:
            if session_id in self.active_recordings:
                logger.warning(f"🎙️ Запись уже активна для сессии {session_id}")
                return False
           
            sink = CustomWaveSink()
            self.active_recordings[session_id] = {
                'sink': sink,
                'start_time': datetime.utcnow(),
                'duration': duration,
                'voice_client': voice_client,
                'stop_task': None
            }
           
            # Запускаем запись
            voice_client.start_recording(sink, callback=callback)
            logger.info(f"🎙️ Запись начата на {duration} секунд для сессии {session_id}")
           
            # Создаем задачу для автоматической остановки
            stop_task = asyncio.create_task(self._auto_stop_recording(voice_client, session_id, duration))
            self.active_recordings[session_id]['stop_task'] = stop_task
           
            # Показываем индикатор записи (неблокирующий)
            asyncio.create_task(self._show_recording_indicator(voice_client.guild, session_id, duration))
           
            return True
       
        except Exception as e:
            logger.error(f"❌ Ошибка запуска записи: {e}")
            if session_id in self.active_recordings:
                del self.active_recordings[session_id]
            raise RecordingException(f"Не удалось начать запись: {e}")

    async def _auto_stop_recording(self, voice_client: discord.VoiceClient, session_id: str, duration: int):
        """Автоматически остановить запись через заданное время"""
        try:
            await asyncio.sleep(duration)
            
            if session_id in self.active_recordings and voice_client.recording:
                voice_client.stop_recording()
                
                # Получаем информацию о записи
                recording_info = self.active_recordings[session_id]
                start_time = recording_info.get('start_time', datetime.utcnow())
                actual_duration = (datetime.utcnow() - start_time).total_seconds()
                
                logger.success(f"🎙️ Запись автоматически завершена для сессии {session_id}")
                logger.success(f"📊 Запись {session_id}: фактическая длительность {actual_duration:.1f}с")
                
        except asyncio.CancelledError:
            logger.info(f"⏹️ Автоостановка записи отменена для сессии {session_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка автоостановки записи: {e}")
   
    async def _show_recording_indicator(self, guild: discord.Guild, session_id: str, duration: int):
        """Показать индикатор записи в системном канале (если есть)"""
        try:
            # Попытаемся найти системный канал
            system_channel = None
            for channel in guild.text_channels:
                if 'верификация' in channel.name.lower() or 'verification' in channel.name.lower():
                    system_channel = channel
                    break
            
            if not system_channel:
                return  # Если нет подходящего канала, просто пропускаем
            
            # Создаем красивый индикатор
            embed = discord.Embed(
                title="🔴 СИСТЕМА ЗАПИСИ АКТИВНА",
                description=f"""
```yaml
┌─ СТАТУС ЗАПИСИ ────┐
│ Сессия:   {session_id[:20]}{'...' if len(session_id) > 20 else ''}
│ Таймер:   {duration} секунд
│ Режим:    🔴 LIVE RECORDING
│ Время:    {datetime.utcnow().strftime('%H:%M:%S')} UTC
└────────────────────┘
```

⏺️ **Идет запись голосового ответа...**

> 🎤 Пользователь отвечает на вопрос
> ⏱️ Автоматическая остановка через **{duration}с**
                """,
                color=0xff0000,  # Красный для активной записи
                timestamp=datetime.utcnow()
            )

            embed.add_field(
                name="📊 Параметры записи",
                value=f"""```ini
[AUDIO_SETTINGS]
Format=WAV
Quality=44.1kHz
Channels=Stereo
Duration={duration}s
```""",
                inline=False
            )

            embed.set_footer(
                text="🎵 Система автоматической записи • REC MODE",
                icon_url="https://cdn.discordapp.com/emojis/741339402298785864.gif"
            )

            await system_channel.send(embed=embed, delete_after=duration + 5)
            
        except Exception as e:
            logger.debug(f"Не удалось показать индикатор записи: {e}")
   
    def stop_recording(self, voice_client: discord.VoiceClient, session_id: str) -> bool:
        """Остановить активную запись"""
        try:
            if session_id not in self.active_recordings:
                logger.warning(f"⏹️ Нет активной записи для сессии {session_id}")
                return False
           
            recording_info = self.active_recordings[session_id]
            
            # Отменяем задачу автоостановки, если она существует
            if recording_info.get('stop_task'):
                recording_info['stop_task'].cancel()
           
            if voice_client.recording:
                voice_client.stop_recording()
                logger.info(f"⏹️ Запись остановлена для сессии {session_id}")
           
            # Получаем информацию о записи перед удалением
            start_time = recording_info.get('start_time', datetime.utcnow())
            actual_duration = (datetime.utcnow() - start_time).total_seconds()
            
            logger.success(f"📊 Запись {session_id}: фактическая длительность {actual_duration:.1f}с")
           
            del self.active_recordings[session_id]
            return True
       
        except Exception as e:
            logger.error(f"❌ Ошибка остановки записи: {e}")
            return False
   
    async def save_audio_files(
        self,
        sink: WaveSink,
        guild: discord.Guild,
        output_dir: str = "temp_recordings"
    ) -> list:
        """Сохранить записанные аудиофайлы с детальной статистикой"""
        saved_files = []
        stats = {
            'total_files': 0,
            'total_size': 0,
            'users_recorded': [],
            'processing_start': datetime.utcnow()
        }
       
        try:
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"📁 Создана/проверена директория: {output_dir}")
           
            for user_id, audio in sink.audio_data.items():
                member = guild.get_member(user_id)
                if not member:
                    logger.warning(f"⚠️ Пользователь {user_id} не найден в гильдии")
                    continue
               
                safe_name = sanitize_filename(member.display_name)
                filename = f"{safe_name}_{user_id}_{datetime.utcnow().strftime('%H%M%S')}.wav"
                filepath = os.path.join(output_dir, filename)
               
                # Сохраняем файл
                with open(filepath, "wb") as f:
                    audio_buffer = audio.file.getbuffer()
                    f.write(audio_buffer)
                    file_size = len(audio_buffer)
               
                # Собираем статистику
                stats['total_files'] += 1
                stats['total_size'] += file_size
                stats['users_recorded'].append(member.display_name)
               
                file_info = {
                    'user_id': user_id,
                    'member': member,
                    'filename': filename,
                    'filepath': filepath,
                    'size_bytes': file_size,
                    'size_kb': file_size / 1024,
                    'timestamp': datetime.utcnow()
                }
                saved_files.append(file_info)
               
                logger.success(f"💾 Сохранена запись: {member.display_name} → {filename} ({file_size/1024:.1f} KB)")
           
            # Финальная статистика
            processing_time = (datetime.utcnow() - stats['processing_start']).total_seconds()
            
            logger.info(f"""
📊 СТАТИСТИКА СОХРАНЕНИЯ:
┌─────────────────────────┐
│ Файлов сохранено: {stats['total_files']:5d} │
│ Общий размер:     {stats['total_size']/1024:.1f} KB │
│ Время обработки:  {processing_time:.2f}s │
│ Пользователей:    {len(stats['users_recorded']):5d} │
└─────────────────────────┘
Участники: {', '.join(stats['users_recorded'][:3])}{'...' if len(stats['users_recorded']) > 3 else ''}
            """)
           
            return saved_files
       
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при сохранении аудио: {e}")
            
            # Создаем эмбед ошибки для отправки в канал (если возможно)
            try:
                error_embed = discord.Embed(
                    title="💥 ОШИБКА СОХРАНЕНИЯ АУДИОФАЙЛОВ",
                    description=f"""
```yaml
┌─ КРИТИЧЕСКАЯ ОШИБКА ─┐
│ Модуль:  RecordingService
│ Метод:   save_audio_files
│ Время:   {datetime.utcnow().strftime('%H:%M:%S')} UTC
│ Файлов:  {stats['total_files']} из {len(sink.audio_data)}
└──────────────────────┘
```

❌ **Ошибка при сохранении записей:**
```fix
{str(e)[:150]}{'...' if len(str(e)) > 150 else ''}
```

> 🔧 **Частично сохранено:**
> • Успешно: **{len(saved_files)}** файлов
> • Размер: **{stats['total_size']/1024:.1f} KB**
> • Время: **{processing_time:.2f}s**

**Обратитесь к администратору!** 🚨
                    """,
                    color=0xff0000,
                    timestamp=datetime.utcnow()
                )

                error_embed.add_field(
                    name="📋 Частично сохраненные файлы",
                    value=f"```\n{chr(10).join([f'✓ {f['filename']}' for f in saved_files[:5]])}{'...' if len(saved_files) > 5 else ''}\n```" if saved_files else "```\nНи одного файла не сохранено\n```",
                    inline=False
                )

                error_embed.set_footer(
                    text="⚠️ Система записи • Требуется вмешательство администратора"
                )

                # Попытаемся найти канал для отправки ошибки
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        await channel.send(embed=error_embed)
                        break

            except Exception as embed_error:
                logger.error(f"Не удалось отправить эмбед ошибки: {embed_error}")

            return saved_files
    
    def get_active_recordings_info(self) -> dict:
        """Получить информацию о всех активных записях"""
        info = {
            'count': len(self.active_recordings),
            'sessions': {},
            'total_duration': 0
        }
        
        current_time = datetime.utcnow()
        
        for session_id, recording_data in self.active_recordings.items():
            start_time = recording_data.get('start_time', current_time)
            duration = recording_data.get('duration', 0)
            elapsed = (current_time - start_time).total_seconds()
            remaining = max(0, duration - elapsed)
            
            info['sessions'][session_id] = {
                'start_time': start_time.isoformat(),
                'duration': duration,
                'elapsed': elapsed,
                'remaining': remaining,
                'status': 'active' if remaining > 0 else 'finishing'
            }
            info['total_duration'] += duration
        
        return info
    
    async def create_recording_status_embed(self, guild: discord.Guild) -> discord.Embed:
        """Создать красивый эмбед со статусом всех записей"""
        info = self.get_active_recordings_info()
        
        if info['count'] == 0:
            embed = discord.Embed(
                title="🔇 СИСТЕМА ЗАПИСИ • ПРОСТОЙ",
                description="""
```yaml
┌─ СТАТУС СИСТЕМЫ ───┐
│ Активных записей: 0
│ Режим:          STANDBY
│ Нагрузка:       0%
└────────────────────┘
```

💤 **Система записи в режиме ожидания**

> 🎤 Готова к началу новых записей
> 📊 Все ресурсы свободны
> ✅ Система полностью готова
                """,
                color=0x808080,  # Серый для простоя
                timestamp=datetime.utcnow()
            )
        else:
            status_lines = []
            for session_id, session_info in info['sessions'].items():
                status_emoji = "🔴" if session_info['status'] == 'active' else "🟡"
                status_lines.append(f"{status_emoji} {session_id[:15]}... ({session_info['remaining']:.1f}s)")
            
            embed = discord.Embed(
                title=f"🎙️ СИСТЕМА ЗАПИСИ • {info['count']} АКТИВНЫХ СЕССИЙ",
                description=f"""
```yaml
┌─ СТАТУС СИСТЕМЫ ───┐
│ Активных записей: {info['count']:3d}
│ Режим:          RECORDING
│ Нагрузка:       {min(100, info['count'] * 25)}%
└────────────────────┘
```

🔴 **Активные записи:**
{chr(10).join(status_lines[:5])}
{'...' if len(status_lines) > 5 else ''}

> ⏱️ Общее время записи: **{info['total_duration']}с**
> 🎵 Все сессии под контролем
                """,
                color=0xff0000,  # Красный для активных записей
                timestamp=datetime.utcnow()
            )

        embed.add_field(
            name="📊 Системная информация",
            value=f"""```ini
[RECORDING_SYSTEM]
Version=2.0
Status={'ACTIVE' if info['count'] > 0 else 'IDLE'}
Sessions={info['count']}
Max_Concurrent=10
```""",
            inline=False
        )

        embed.set_footer(
            text="🎵 RecordingService • Мониторинг системы",
            icon_url="https://cdn.discordapp.com/emojis/741339402298785864.gif"
        )

        return embed
