import os
import asyncio
import wave
import audioop
import struct
from pathlib import Path
from typing import Dict
from functools import partial
from datetime import datetime

import discord

from models.verification_session import VerificationSession, VerificationStatus
from services.audio_service import AudioService
from services.recording_service import RecordingService
from services.role_service import RoleService
from utils.logger import logger
from config.settings import settings


class VerificationService:
    """Основной сервис обработки верификации"""

    def __init__(self):
        self.active_sessions: Dict[int, VerificationSession] = {}
        self.audio_service = AudioService()
        self.recording_service = RecordingService()
        self.role_service = RoleService()

    async def start_verification(self, member: discord.Member, voice_client: discord.VoiceClient, text_channel: discord.TextChannel) -> bool:
        if member.id in self.active_sessions:
            logger.warning(f"Верификация уже активна для {member.display_name}")
            return False

        session = VerificationSession(
            user_id=member.id,
            guild_id=member.guild.id,
            status=VerificationStatus.IN_PROGRESS
        )
        self.active_sessions[member.id] = session

        # 📋 КОМПАКТНЫЙ ЭМБЕД ДЛЯ САППОРТОВ
        embed = discord.Embed(
            title="🎯 Верификация запущена",
            description=f"**{member.mention}** (`{member.id}`)\n💬 Вопросов: **{len(settings.questions)}** | ⏱️ Время: **~{sum(settings.recording_durations)}с**",
            color=0x3498db,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="📊 Прогресс", 
            value=f"`0/{len(settings.questions)}` {'▱' * 10}", 
            inline=True
        )
        embed.add_field(
            name="🎙️ Статус", 
            value="```✅ Готов к записи```", 
            inline=True
        )
        
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ID: {member.id} • {member.guild.name}")

        await text_channel.send(embed=embed)
        await self._ask_question(voice_client, text_channel, session)
        return True

    async def _ask_question(self, voice_client: discord.VoiceClient, text_channel: discord.TextChannel, session: VerificationSession):
        try:
            question = settings.questions[session.current_question_index]
            duration = settings.recording_durations[session.current_question_index]

            # 🎤 ИНФОРМАТИВНЫЙ ЭМБЕД ДЛЯ МОНИТОРИНГА
            progress = session.current_question_index + 1
            total = len(settings.questions)
            progress_bar = "▰" * progress + "▱" * (total - progress)

            embed = discord.Embed(
                title=f"🎤 Вопрос {progress}/{total}",
                description=f"**Вопрос:** {question}\n🔴 **Запись:** {duration}с",
                color=0xe74c3c,
                timestamp=datetime.utcnow()
            )

            user = voice_client.guild.get_member(session.user_id)
            if user:
                embed.add_field(name="👤 Пользователь", value=f"{user.mention}\n`{user.id}`", inline=True)
                embed.add_field(name="📊 Прогресс", value=f"`{progress}/{total}` {progress_bar}", inline=True)
                embed.add_field(name="⏰ Таймер", value=f"```🔴 {duration}с```", inline=True)
                embed.set_thumbnail(url=user.display_avatar.url)

            embed.set_footer(text=f"Осталось: {total - progress} вопросов")

            await text_channel.send(embed=embed)
            await self.audio_service.play_question_audio(voice_client, question, settings.audio_files)

            callback = partial(self._handle_recording_complete, text_channel=text_channel, voice_client=voice_client, session=session)
            session_id = f"{session.user_id}_{session.current_question_index}"

            await self.recording_service.start_recording(voice_client, duration, callback, session_id)

        except Exception as e:
            logger.error(f"Ошибка при отправке вопроса: {e}")
            await self._handle_verification_error(text_channel, session, str(e))

    async def _analyze_audio_file(self, filepath: str, expected_duration: int) -> dict:
        """Улучшенный анализ аудиофайла с proper handling"""
        
        # Defaults
        result = {
            'duration': 0.0,
            'file_size_kb': 0.0,
            'avg_volume': 0,
            'sample_rate': 48000,
            'quality': 0,
            'quality_emoji': '🔴',
            'quality_color': 0xe74c3c,
            'channels': 2,
            'sample_width': 2
        }
        
        try:
            # File size check
            if not os.path.exists(filepath):
                logger.warning(f"Audio file not found: {filepath}")
                return result
                
            file_size = os.path.getsize(filepath)
            result['file_size_kb'] = file_size / 1024
            
            # Too small file check
            if file_size < 1024:  # Less than 1KB
                logger.warning(f"Audio file too small: {file_size} bytes")
                result['quality'] = 5
                return result
            
            # Wait for file to be fully written
            await asyncio.sleep(0.5)
            
            # Primary analysis with wave module
            try:
                with wave.open(filepath, 'rb') as wf:
                    frames = wf.getnframes()
                    result['sample_rate'] = wf.getframerate()
                    result['channels'] = wf.getnchannels()
                    result['sample_width'] = wf.getsampwidth()
                    
                    # Calculate duration
                    if frames > 0 and result['sample_rate'] > 0:
                        result['duration'] = frames / result['sample_rate']
                    
                    # Volume analysis - read up to 5 seconds of audio
                    max_frames = min(frames, result['sample_rate'] * 5)
                    if max_frames > 0:
                        audio_data = wf.readframes(max_frames)
                        if len(audio_data) > 0:
                            try:
                                result['avg_volume'] = audioop.rms(audio_data, result['sample_width'])
                            except audioop.error as e:
                                logger.warning(f"RMS calculation failed: {e}")
                                # Fallback: manual RMS calculation
                                result['avg_volume'] = self._calculate_manual_rms(audio_data, result['sample_width'])
                                
            except (wave.Error, OSError) as e:
                logger.warning(f"Wave analysis failed: {e}")
                # Fallback to file-based estimation
                result.update(self._estimate_audio_properties(filepath, file_size))
            
            # Quality calculation
            result.update(self._calculate_quality_metrics(result, expected_duration))
            
            logger.info(f"Audio analysis: {result['duration']:.1f}s, {result['avg_volume']} RMS, {result['quality']}%")
            
        except Exception as e:
            logger.error(f"Audio analysis failed completely: {e}")
            # Minimal fallback
            result['duration'] = max(1.0, expected_duration * 0.5)
            result['quality'] = 20
            
        return result

    def _calculate_manual_rms(self, audio_data: bytes, sample_width: int) -> int:
        """Manual RMS calculation as fallback"""
        try:
            if sample_width == 1:
                # 8-bit unsigned
                samples = [abs(b - 128) for b in audio_data]
            elif sample_width == 2:
                # 16-bit signed
                samples = []
                for i in range(0, len(audio_data) - 1, 2):
                    sample = struct.unpack('<h', audio_data[i:i+2])[0]
                    samples.append(abs(sample))
            elif sample_width == 4:
                # 32-bit signed
                samples = []
                for i in range(0, len(audio_data) - 3, 4):
                    sample = struct.unpack('<i', audio_data[i:i+4])[0]
                    samples.append(abs(sample))
            else:
                return 1000  # Default fallback
                
            if samples:
                # RMS calculation
                mean_square = sum(x * x for x in samples) / len(samples)
                return int(mean_square ** 0.5)
            else:
                return 0
                
        except Exception as e:
            logger.warning(f"Manual RMS calculation failed: {e}")
            return 1000  # Safe fallback

    def _estimate_audio_properties(self, filepath: str, file_size: int) -> dict:
        """Estimate audio properties when wave analysis fails"""
        # Basic estimates for common Discord audio format
        estimated_sample_rate = 48000
        estimated_channels = 2
        estimated_sample_width = 2
        
        # Estimate duration based on file size
        # WAV header is ~44 bytes, rest is audio data
        audio_data_size = max(0, file_size - 44)
        bytes_per_second = estimated_sample_rate * estimated_channels * estimated_sample_width
        estimated_duration = audio_data_size / bytes_per_second if bytes_per_second > 0 else 1.0
        
        # Estimate volume based on file size vs duration ratio
        estimated_volume = min(5000, max(500, int(file_size / 100)))
        
        return {
            'duration': estimated_duration,
            'sample_rate': estimated_sample_rate,
            'channels': estimated_channels,
            'sample_width': estimated_sample_width,
            'avg_volume': estimated_volume
        }

    def _calculate_quality_metrics(self, audio_data: dict, expected_duration: int) -> dict:
        """Calculate quality score and related metrics"""
        
        duration = audio_data['duration']
        file_size_kb = audio_data['file_size_kb']
        avg_volume = audio_data['avg_volume']
        
        # Duration score (0-1)
        if expected_duration > 0:
            duration_ratio = duration / expected_duration
            # Penalize too short or too long recordings
            if duration_ratio < 0.3:
                duration_score = duration_ratio / 0.3 * 0.5  # Severe penalty for very short
            elif duration_ratio > 1.5:
                duration_score = max(0.5, 1.0 - (duration_ratio - 1.5) * 0.5)  # Penalty for too long
            else:
                duration_score = 1.0  # Perfect range
        else:
            duration_score = 0.8 if duration > 1.0 else 0.3
        
        # File size score (0-1)
        expected_size_kb = expected_duration * 12  # ~12KB per second for Discord quality
        if expected_size_kb > 0:
            size_ratio = file_size_kb / expected_size_kb
            if size_ratio < 0.2:
                size_score = 0.1  # Too small
            elif size_ratio > 3.0:
                size_score = 0.7  # Too large but not necessarily bad
            else:
                size_score = min(1.0, size_ratio)
        else:
            size_score = 0.5 if file_size_kb > 10 else 0.1
        
        # Volume score (0-1)
        if avg_volume > 0:
            # Volume ranges: 0-1000 (silence), 1000-5000 (normal), 5000+ (loud)
            if avg_volume < 200:
                volume_score = 0.1  # Too quiet/silence
            elif avg_volume < 1000:
                volume_score = avg_volume / 1000 * 0.6  # Quiet but audible
            elif avg_volume < 8000:
                volume_score = 0.6 + (avg_volume - 1000) / 7000 * 0.4  # Normal range
            else:
                volume_score = 1.0  # Loud enough
        else:
            volume_score = 0.1  # No audio detected
        
        # Combined quality score
        # Weights: duration 50%, volume 35%, size 15%
        quality = int((duration_score * 0.5 + volume_score * 0.35 + size_score * 0.15) * 100)
        quality = max(5, min(100, quality))  # Clamp between 5-100
        
        # Quality indicators
        if quality >= 85:
            quality_emoji = "🟢"
            quality_color = 0x27ae60
        elif quality >= 70:
            quality_emoji = "🟡"
            quality_color = 0xf39c12
        elif quality >= 50:
            quality_emoji = "🟠"
            quality_color = 0xe67e22
        else:
            quality_emoji = "🔴"
            quality_color = 0xe74c3c
        
        return {
            'quality': quality,
            'quality_emoji': quality_emoji,
            'quality_color': quality_color,
            'duration_score': duration_score,
            'size_score': size_score,
            'volume_score': volume_score
        }

    async def _handle_recording_complete(self, sink, text_channel: discord.TextChannel, voice_client: discord.VoiceClient, session: VerificationSession):
        try:
            guild = text_channel.guild
            saved_files = await self.recording_service.save_audio_files(sink, guild)

            for file_info in saved_files:
                member = file_info['member']
                filepath = file_info['filepath']
                filename = Path(filepath).name
                
                # Improved audio analysis
                expected_duration = settings.recording_durations[session.current_question_index]
                audio_analysis = await self._analyze_audio_file(filepath, expected_duration)

                # 📊 АНАЛИТИЧЕСКИЙ ЭМБЕД ДЛЯ САППОРТОВ
                progress = session.current_question_index + 1
                total = len(settings.questions)
                
                embed = discord.Embed(
                    title=f"✅ Запись {progress}/{total} завершена",
                    description=f"**{member.mention}** • Качество: **{audio_analysis['quality']}%** {audio_analysis['quality_emoji']}",
                    color=audio_analysis['quality_color'],
                    timestamp=datetime.utcnow()
                )

                embed.add_field(
                    name="📈 Анализ",
                    value=f"```yaml\nДлительность: {audio_analysis['duration']:.1f}s/{expected_duration}s\nРазмер: {audio_analysis['file_size_kb']:.1f} KB\nГромкость: {audio_analysis['avg_volume']:,} RMS\nОценка: {audio_analysis['quality']}%```",
                    inline=False
                )

                embed.add_field(
                    name="📁 Файл",
                    value=f"`{filename}`",
                    inline=True
                )

                # Обновленный прогресс бар
                progress_bar = "▰" * progress + "▱" * (total - progress)
                embed.add_field(
                    name="📊 Прогресс",
                    value=f"`{progress}/{total}` {progress_bar}",
                    inline=True
                )

                embed.add_field(
                    name="⏭️ Статус",
                    value="```css\n✅ Обработано```" if progress < total else "```diff\n+ ЗАВЕРШЕНО```",
                    inline=True
                )

                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"ID: {member.id} • Осталось: {total - progress}")

                await text_channel.send(embed=embed)
                
                # Отправка файла с компактным сообщением
                await text_channel.send(f"📎 **Аудиофайл:** `{filename}` • {audio_analysis['quality']}% качества", file=discord.File(filepath))

                logger.success(f"🎙️ {member.display_name} — Q{progress}: {audio_analysis['quality']}% ({filename})")
                os.remove(filepath)

            if session.current_question_index + 1 < len(settings.questions):
                session.next_question()
                
                # Минималистичное уведомление о паузе
                pause_embed = discord.Embed(
                    description="⏳ **Пауза 3с** • Подготовка следующего вопроса...",
                    color=0x95a5a6
                )
                pause_msg = await text_channel.send(embed=pause_embed)
                
                await asyncio.sleep(3)
                await pause_msg.delete()
                
                await self._ask_question(voice_client, text_channel, session)
            else:
                await self._complete_verification(voice_client, text_channel, session, saved_files)

        except Exception as e:
            logger.error(f"Ошибка при завершении записи: {e}")
            await self._handle_verification_error(text_channel, session, str(e))

    async def _complete_verification(self, voice_client: discord.VoiceClient, text_channel: discord.TextChannel, session: VerificationSession, saved_files: list):
        try:
            await self.audio_service.play_audio_file(voice_client, settings.audio_files["completion"])

            for file_info in saved_files:
                member = file_info['member']
                await self.role_service.assign_verified_role(member, settings.verified_role_id, settings.unverified_role_id)

                # 🎉 ФИНАЛЬНЫЙ ОТЧЕТ ДЛЯ САППОРТОВ
                embed = discord.Embed(
                    title="🎉 Верификация завершена успешно",
                    description=f"**{member.mention}** • `{member.id}`\n✅ **Все {len(settings.questions)} вопросов пройдены**",
                    color=0x27ae60,
                    timestamp=datetime.utcnow()
                )

                embed.add_field(
                    name="📊 Статистика",
                    value=f"```yaml\nВопросов: {len(settings.questions)}/{len(settings.questions)}\nВремя: {sum(settings.recording_durations)}с\nФайлов: {len(saved_files)}\nУспех: 100%```",
                    inline=False
                )

                embed.add_field(
                    name="🎭 Изменения",
                    value="```diff\n+ Роль VERIFIED\n- Роль UNVERIFIED```",
                    inline=True
                )

                embed.add_field(
                    name="🔄 Действия",
                    value="```css\n• Роль назначена\n• Кик запланирован```",
                    inline=True
                )

                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"Верификация завершена • {datetime.utcnow().strftime('%H:%M:%S UTC')}")

                await text_channel.send(embed=embed)
                
                # Проверка прав на кик
                if member.guild.me.guild_permissions.kick_members:
                    try:
                        await self.role_service.kick_member_after_verification(member)
                    except discord.Forbidden:
                        logger.warning(f"⚠️ Нет прав на кик {member.display_name}")
                        # Уведомление саппортов о необходимости ручного кика
                        kick_embed = discord.Embed(
                            title="⚠️ Требуется ручной кик",
                            description=f"**{member.mention}** • `{member.id}`\nВерификация завершена, но бот не может кикнуть пользователя",
                            color=0xf39c12
                        )
                        await text_channel.send(embed=kick_embed)
                else:
                    logger.warning("⚠️ У бота нет прав на кик участников")
                    # Уведомление о настройке прав
                    perm_embed = discord.Embed(
                        title="🔧 Настройка прав бота",
                        description="Боту нужны права **Kick Members** для автоматического кика после верификации",
                        color=0xe67e22
                    )
                    await text_channel.send(embed=perm_embed)

            session.complete()

            if voice_client and voice_client.is_connected():
                await voice_client.disconnect()
                logger.info("Бот отключился после завершения верификации")

            if session.user_id in self.active_sessions:
                del self.active_sessions[session.user_id]

        except Exception as e:
            logger.error(f"Ошибка при завершении верификации: {e}")
            await self._handle_verification_error(text_channel, session, str(e))

    async def _handle_verification_error(self, text_channel: discord.TextChannel, session: VerificationSession, error_message: str):
        # ⚠️ КРИТИЧЕСКИЙ АЛЕРТ ДЛЯ САППОРТОВ
        embed = discord.Embed(
            title="🚨 ОШИБКА ВЕРИФИКАЦИИ",
            description=f"**Критическая ошибка системы**\n`Session ID: {session.user_id if session else 'Unknown'}`",
            color=0xe74c3c,
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="❌ Детали ошибки",
            value=f"```python\n{error_message[:300]}{'...' if len(error_message) > 300 else ''}\n```",
            inline=False
        )

        if session:
            user = text_channel.guild.get_member(session.user_id)
            if user:
                embed.add_field(
                    name="👤 Пользователь",
                    value=f"{user.mention}\n`{user.id}`",
                    inline=True
                )
                embed.add_field(
                    name="📊 Прогресс",
                    value=f"`{session.current_question_index}/{len(settings.questions)}`",
                    inline=True
                )

        embed.add_field(
            name="🛠️ Действия",
            value="```\n• Сессия очищена\n• Требуется перезапуск\n• Уведомить пользователя```",
            inline=False
        )

        embed.set_footer(text="Системная ошибка • Требуется вмешательство саппорта")

        await text_channel.send(embed=embed)

        if session and session.user_id in self.active_sessions:
            del self.active_sessions[session.user_id]

        logger.error(f"Verification error: {error_message}")

    def cleanup_session(self, user_id: int) -> bool:
        if user_id in self.active_sessions:
            del self.active_sessions[user_id]
            logger.info(f"Сессия {user_id} очищена")
            return True
        return False
