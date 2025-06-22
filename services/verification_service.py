import asyncio
import math
import os
import struct
import tempfile
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Dict, Tuple

import discord

from config.settings import settings
from models.verification_session import VerificationSession, VerificationStatus
from services.audio_service import AudioService
from services.recording_service import RecordingService
from services.role_service import RoleService
from utils.logger import logger

try:
    import librosa
    import numpy as np
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False
    librosa = None
    np = None  # Добавь это, чтобы избежать NameError

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False


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

    async def _convert_to_pcm16(self, input_path: str, output_path: str) -> bool:
        """Конвертировать аудио в PCM16 формат с помощью ffmpeg"""
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", input_path,
                "-ar", "44100", "-ac", "1", "-sample_fmt", "s16",
                output_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await process.wait()
            return process.returncode == 0
        except Exception as e:
            logger.warning(f"FFmpeg conversion failed: {e}")
            return False

    def _interpret_rms(self, rms: float) -> Tuple[str, str, int]:
        """Интерпретировать RMS значение в удобочитаемый формат"""
        if rms <= 0.0:
            return "0.0000", "🔴 Тишина", 5
        
        try:
            db = 20 * math.log10(rms + 1e-10)  # dBFS громкость
            rms_str = f"{rms:.4f}"
            
            if db < -40:
                return rms_str, "🔴 Очень тихо", 15
            elif db < -30:
                return rms_str, "🟡 Тихо", 35
            elif db < -20:
                return rms_str, "🟢 Нормально", 70
            elif db < -10:
                return rms_str, "🟢 Громко", 85
            else:
                return rms_str, "🟦 Очень громко", 90
        except Exception:
            return f"{rms:.4f}", "🟡 Неопределенно", 50

    async def _analyze_with_librosa(self, file_path: str) -> Dict:
        """Анализ аудио с помощью librosa (наиболее точный)"""
        try:
            # Создаем временный файл для конвертации
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                temp_path = tmp_file.name

            # Конвертируем в совместимый формат
            if not await self._convert_to_pcm16(file_path, temp_path):
                os.unlink(temp_path)
                raise Exception("FFmpeg conversion failed")

            # Загружаем аудио
            y, sr = librosa.load(temp_path, sr=None, mono=True)
            
            # Вычисляем метрики
            duration = len(y) / sr
            rms = float(librosa.feature.rms(y=y).mean())
            
            # Очищаем временный файл
            os.unlink(temp_path)
            
            return {
                'duration': duration,
                'sample_rate': sr,
                'rms': rms,
                'method': 'librosa'
            }
            
        except Exception as e:
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.unlink(temp_path)
            raise Exception(f"Librosa analysis failed: {e}")

    async def _analyze_with_pydub(self, file_path: str) -> Dict:
        """Анализ аудио с помощью pydub (средний уровень точности)"""
        try:
            # Пытаемся загрузить напрямую
            try:
                audio = AudioSegment.from_wav(file_path)
            except Exception:
                # Если не получается, конвертируем через ffmpeg
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                    temp_path = tmp_file.name
                
                if not await self._convert_to_pcm16(file_path, temp_path):
                    os.unlink(temp_path)
                    raise Exception("FFmpeg conversion failed")
                
                audio = AudioSegment.from_wav(temp_path)
                os.unlink(temp_path)

            duration = len(audio) / 1000.0  # в секундах
            sample_rate = audio.frame_rate
            
            # Простой расчет RMS
            samples = audio.get_array_of_samples()
            if len(samples) > 0:
                rms = math.sqrt(sum(x*x for x in samples) / len(samples)) / 32768.0
            else:
                rms = 0.0

            return {
                'duration': duration,
                'sample_rate': sample_rate,
                'rms': rms,
                'method': 'pydub'
            }
            
        except Exception as e:
            raise Exception(f"Pydub analysis failed: {e}")

    async def _analyze_with_ffprobe(self, file_path: str) -> Dict:
        """Анализ аудио с помощью ffprobe (базовый уровень)"""
        try:
            # Получаем информацию о файле
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-print_format", "json", 
                "-show_format", "-show_streams", file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                raise Exception(f"ffprobe failed: {stderr.decode()}")
            
            import json
            data = json.loads(stdout.decode())
            
            duration = 0.0
            sample_rate = 48000
            
            # Извлекаем информацию о формате
            if 'format' in data and 'duration' in data['format']:
                duration = float(data['format']['duration'])
            
            # Извлекаем информацию о потоке
            if 'streams' in data and len(data['streams']) > 0:
                stream = data['streams'][0]
                if 'sample_rate' in stream:
                    sample_rate = int(stream['sample_rate'])
            
            # Оценка RMS на основе размера файла (очень приблизительно)
            file_size = os.path.getsize(file_path)
            estimated_rms = min(0.1, max(0.001, file_size / (duration * 100000))) if duration > 0 else 0.001

            return {
                'duration': duration,
                'sample_rate': sample_rate,
                'rms': estimated_rms,
                'method': 'ffprobe'
            }
            
        except Exception as e:
            raise Exception(f"FFprobe analysis failed: {e}")

    async def _analyze_audio_file(self, filepath: str, expected_duration: int) -> dict:
        """Улучшенный анализ аудиофайла с каскадным подходом"""
        
        # Базовые значения по умолчанию
        result = {
            'duration': 0.0,
            'file_size_kb': 0.0,
            'avg_volume': 0,
            'sample_rate': 48000,
            'quality': 5,
            'quality_emoji': '🔴',
            'quality_color': 0xe74c3c,
            'channels': 2,
            'sample_width': 2,
            'analysis_method': 'fallback'
        }
        
        try:
            # Проверка существования файла
            if not os.path.exists(filepath):
                logger.warning(f"Audio file not found: {filepath}")
                return result
                
            file_size = os.path.getsize(filepath)
            result['file_size_kb'] = file_size / 1024
            
            # Проверка минимального размера
            if file_size < 1024:  # Меньше 1KB
                logger.warning(f"Audio file too small: {file_size} bytes")
                result['quality'] = 10
                return result
            
            # Ждем завершения записи файла
            await asyncio.sleep(0.5)
            
            # Каскадный анализ: пробуем методы от лучшего к худшему
            analysis_result = None
            
            # 1. Пробуем librosa (самый точный)
            if HAS_LIBROSA:
                try:
                    logger.debug("Trying librosa analysis...")
                    analysis_result = await self._analyze_with_librosa(filepath)
                    logger.info("✅ Librosa analysis successful")
                except Exception as e:
                    logger.debug(f"Librosa failed: {e}")
            
            # 2. Пробуем pydub (средний уровень)
            if not analysis_result and HAS_PYDUB:
                try:
                    logger.debug("Trying pydub analysis...")
                    analysis_result = await self._analyze_with_pydub(filepath)
                    logger.info("✅ Pydub analysis successful")
                except Exception as e:
                    logger.debug(f"Pydub failed: {e}")
            
            # 3. Пробуем ffprobe (базовый уровень)
            if not analysis_result:
                try:
                    logger.debug("Trying ffprobe analysis...")
                    analysis_result = await self._analyze_with_ffprobe(filepath)
                    logger.info("✅ FFprobe analysis successful")
                except Exception as e:
                    logger.debug(f"FFprobe failed: {e}")
            
            # Если все методы не сработали, используем fallback
            if not analysis_result:
                logger.warning("All analysis methods failed, using fallback estimation")
                result.update(self._estimate_audio_properties(filepath, file_size))
                result['analysis_method'] = 'fallback_estimation'
            else:
                # Обновляем результат данными анализа
                result['duration'] = analysis_result['duration']
                result['sample_rate'] = analysis_result['sample_rate']
                result['avg_volume'] = int(analysis_result['rms'] * 10000)  # Приводим к интегральному RMS
                result['analysis_method'] = analysis_result['method']
            
            # Интерпретируем RMS
            rms_str, rms_label, rms_quality = self._interpret_rms(analysis_result['rms'] if analysis_result else 0.001)
            result['rms_string'] = rms_str
            result['rms_label'] = rms_label
            
            # Вычисляем качество
            result.update(self._calculate_quality_metrics(result, expected_duration))
            
            logger.info(f"🎵 Audio analysis complete: {result['duration']:.1f}s, {result['quality']}%, method: {result['analysis_method']}")
            
        except Exception as e:
            logger.error(f"❌ Complete audio analysis failure: {e}")
            # Аварийный fallback
            result['duration'] = max(1.0, expected_duration * 0.5)
            result['quality'] = 25
            result['analysis_method'] = 'emergency_fallback'
            
        return result

    def _estimate_volume_from_file_size(self, file_size: int, duration: float, expected_duration: int) -> int:
        """Улучшенная оценка громкости с учетом ожидаемой длительности"""
        if duration <= 0:
            return 800 if expected_duration <= 3 else 1000
        
        bytes_per_second = file_size / duration
        
        # Адаптивные пороги в зависимости от ожидаемой длительности
        if expected_duration <= 3:
            # Для коротких ответов более мягкие требования
            if bytes_per_second < 30000:
                return 400
            elif bytes_per_second < 60000:
                return 1000
            elif bytes_per_second < 100000:
                return 2000
            else:
                return 3500
        else:
            # Для длинных ответов стандартные требования
            if bytes_per_second < 50000:
                return 500
            elif bytes_per_second < 100000:
                return 1500
            elif bytes_per_second < 150000:
                return 3000
            else:
                return 5000

    def _calculate_manual_rms(self, audio_data: bytes, sample_width: int) -> int:
        """Manual RMS calculation as fallback"""
        try:
            if len(audio_data) < sample_width:
                return 0
                
            if sample_width == 1:
                # 8-bit unsigned
                samples = [abs(b - 128) for b in audio_data]
            elif sample_width == 2:
                # 16-bit signed
                samples = []
                for i in range(0, len(audio_data) - 1, 2):
                    if i + 1 < len(audio_data):
                        sample = struct.unpack('<h', audio_data[i:i+2])[0]
                        samples.append(abs(sample))
            elif sample_width == 4:
                # 32-bit signed
                samples = []
                for i in range(0, len(audio_data) - 3, 4):
                    if i + 3 < len(audio_data):
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

    def _estimate_audio_properties(self, filepath: str, file_size: int, expected_duration: int = 3) -> dict:
        """Улучшенная оценка свойств аудио с учетом ожидаемой длительности"""
        estimated_sample_rate = 48000
        estimated_channels = 2
        estimated_sample_width = 2
        
        # Более точная оценка длительности
        audio_data_size = max(0, file_size - 44)
        bytes_per_second = estimated_sample_rate * estimated_channels * estimated_sample_width
        estimated_duration_calc = audio_data_size / bytes_per_second if bytes_per_second > 0 else 1.0
        
        # Ограничиваем оценку разумными пределами
        if expected_duration <= 3:
            estimated_duration_calc = min(estimated_duration_calc, expected_duration * 2)
        
        # Улучшенная оценка громкости
        estimated_volume = self._estimate_volume_from_file_size(file_size, estimated_duration_calc, expected_duration)
        
        return {
            'duration': estimated_duration_calc,
            'sample_rate': estimated_sample_rate,
            'channels': estimated_channels,
            'sample_width': estimated_sample_width,
            'avg_volume': estimated_volume
        }

    def _calculate_quality_metrics(self, audio_data: dict, expected_duration: int) -> dict:
        """Calculate quality score with adaptive scoring based on expected duration"""
        
        duration = audio_data['duration']
        file_size_kb = audio_data['file_size_kb']
        avg_volume = audio_data['avg_volume']
        
        # ИСПРАВЛЕНО: Адаптивная оценка длительности
        if expected_duration > 0:
            duration_ratio = duration / expected_duration
            
            # Для коротких ответов (≤3 сек) - более мягкие требования
            if expected_duration <= 3:
                if duration_ratio >= 0.3:  # Минимум 30% от ожидаемого
                    if duration_ratio <= 2.0:  # Максимум в 2 раза больше
                        duration_score = 1.0
                    else:
                        duration_score = max(0.7, 1.0 - (duration_ratio - 2.0) * 0.2)
                else:
                    # Очень короткие ответы - мягкий штраф
                    duration_score = duration_ratio / 0.3 * 0.8
            
            # Для длинных ответов (>3 сек) - стандартные требования
            else:
                if duration_ratio >= 0.6:  # Минимум 60% от ожидаемого
                    if duration_ratio <= 1.3:  # До 130% - отлично
                        duration_score = 1.0
                    else:
                        duration_score = max(0.8, 1.0 - (duration_ratio - 1.3) * 0.3)
                else:
                    duration_score = duration_ratio / 0.6 * 0.7
        else:
            duration_score = 0.8 if duration > 0.5 else 0.3
        
        # ИСПРАВЛЕНО: Адаптивная оценка размера файла
        # Базовая оценка: 12-18 KB/сек для коротких записей, 15-20 KB/сек для длинных
        if expected_duration <= 3:
            expected_size_kb = expected_duration * 12  # Меньше ожидаемый размер для коротких
            min_acceptable_ratio = 0.2  # Более мягкие требования
        else:
            expected_size_kb = expected_duration * 15
            min_acceptable_ratio = 0.3
        
        if expected_size_kb > 0:
            size_ratio = file_size_kb / expected_size_kb
            if size_ratio >= min_acceptable_ratio:
                if size_ratio <= 2.5:
                    size_score = 1.0
                else:
                    size_score = 0.8  # Большой файл не критично
            else:
                size_score = size_ratio / min_acceptable_ratio * 0.6
        else:
            size_score = 0.7 if file_size_kb > 10 else 0.3
        
        # ИСПРАВЛЕНО: Адаптивная оценка громкости
        if avg_volume > 0:
            # Для коротких ответов требования к громкости мягче
            if expected_duration <= 3:
                if avg_volume >= 300:  # Минимальный порог для коротких
                    if avg_volume <= 8000:
                        volume_score = 1.0
                    else:
                        volume_score = 0.9
                else:
                    volume_score = max(0.4, avg_volume / 300 * 0.8)
            else:
                # Для длинных ответов стандартные требования
                if avg_volume >= 500:
                    if avg_volume <= 6000:
                        volume_score = 1.0
                    else:
                        volume_score = 0.9
                else:
                    volume_score = max(0.3, avg_volume / 500 * 0.7)
        else:
            volume_score = 0.1  # Тишина всегда плохо
        
        # ИСПРАВЛЕНО: Адаптивные веса в зависимости от длительности
        if expected_duration <= 3:
            # Для коротких ответов: громкость важнее длительности
            quality = int((duration_score * 0.3 + volume_score * 0.5 + size_score * 0.2) * 100)
        else:
            # Для длинных ответов: сбалансированно
            quality = int((duration_score * 0.4 + volume_score * 0.4 + size_score * 0.2) * 100)
        
        quality = max(15, min(100, quality))  # Минимум 15% для любого ответа
        
        # ИСПРАВЛЕНО: Адаптивные пороги качества
        if expected_duration <= 3:
            # Для коротких ответов более мягкие пороги
            if quality >= 70:
                quality_emoji = "🟢"
                quality_color = 0x27ae60
            elif quality >= 50:
                quality_emoji = "🟡"
                quality_color = 0xf39c12
            elif quality >= 30:
                quality_emoji = "🟠"
                quality_color = 0xe67e22
            else:
                quality_emoji = "🔴"
                quality_color = 0xe74c3c
        else:
            # Для длинных ответов стандартные пороги
            if quality >= 80:
                quality_emoji = "🟢"
                quality_color = 0x27ae60
            elif quality >= 60:
                quality_emoji = "🟡"
                quality_color = 0xf39c12
            elif quality >= 40:
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
            'volume_score': volume_score,
            'expected_duration': expected_duration,
            'is_short_answer': expected_duration <= 3
        }

    async def _handle_recording_complete(self, sink, text_channel: discord.TextChannel, voice_client: discord.VoiceClient, session: VerificationSession):
        try:
            guild = text_channel.guild
            saved_files = await self.recording_service.save_audio_files(sink, guild)
            
            # ИСПРАВЛЕНО: Обрабатываем все файлы, но отправляем сводку
            total_files_processed = 0
            session_user_file = None
            
            # Находим файл текущего пользователя
            for file_info in saved_files:
                if file_info['user_id'] == session.user_id:
                    session_user_file = file_info
                    break
            
            if session_user_file:
                member = session_user_file['member']
                filepath = session_user_file['filepath']
                filename = Path(filepath).name
                
                # Improved audio analysis
                expected_duration = settings.recording_durations[session.current_question_index]
                audio_analysis = await self._analyze_audio_file(filepath, expected_duration)

                # 📊 АНАЛИТИЧЕСКИЙ ЭМБЕД ДЛЯ САППОРТОВ
                progress = session.current_question_index + 1
                total = len(settings.questions)
                total_files_processed = len(saved_files)
                
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
                    name="📁 Файлы",
                    value=f"`{filename}`\n*+{total_files_processed-1} других*" if total_files_processed > 1 else f"`{filename}`",
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
                embed.set_footer(text=f"ID: {member.id} • Файлов записано: {total_files_processed}")

                await text_channel.send(embed=embed)
                
                # Отправка файла с компактным сообщением
                await text_channel.send(f"📎 **Аудиофайл:** `{filename}` • {audio_analysis['quality']}% качества", file=discord.File(filepath))

                logger.success(f"🎙️ {member.display_name} — Q{progress}: {audio_analysis['quality']}% ({filename})")
            
            # Удаляем все файлы после обработки
            for file_info in saved_files:
                try:
                    os.remove(file_info['filepath'])
                except OSError as e:
                    logger.warning(f"Couldn't remove file {file_info['filepath']}: {e}")

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
                # ИСПРАВЛЕНО: Передаем корректное количество файлов
                await self._complete_verification(voice_client, text_channel, session, total_files_processed)

        except Exception as e:
            logger.error(f"Ошибка при завершении записи: {e}")
            await self._handle_verification_error(text_channel, session, str(e))

    async def _complete_verification(self, voice_client: discord.VoiceClient, text_channel: discord.TextChannel, session: VerificationSession, total_files_count: int):
        try:
            await self.audio_service.play_audio_file(voice_client, settings.audio_files["completion"])

            member = voice_client.guild.get_member(session.user_id)
            if member:
                await self.role_service.assign_verified_role(member, settings.verified_role_id, settings.unverified_role_id)

                # 🎉 ИСПРАВЛЕННЫЙ ФИНАЛЬНЫЙ ОТЧЕТ
                embed = discord.Embed(
                    title="🎉 Верификация завершена успешно",
                    description=f"**{member.mention}** • `{member.id}`\n✅ **Все {len(settings.questions)} вопросов пройдены**",
                    color=0x27ae60,
                    timestamp=datetime.utcnow()
                )

                # ИСПРАВЛЕНО: Показываем правильное количество файлов
                embed.add_field(
                    name="📊 Статистика",
                    value=f"```yaml\nВопросов: {len(settings.questions)}/{len(settings.questions)}\nВремя: {sum(settings.recording_durations)}с\nФайлов: {len(settings.questions)}\nУспех: 100%```",
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
