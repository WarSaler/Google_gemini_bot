import os
import logging
import asyncio
import base64
import tempfile
import re
import json
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Dict, List, Optional
import aiohttp
from io import BytesIO
import speech_recognition as sr
from pydub import AudioSegment
from gtts import gTTS
import wikipedia
from newsapi import NewsApiClient

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from keep_alive import start_server

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
AI_API_KEY = os.getenv('AI_API_KEY')
NEWS_API_KEY = os.getenv('NEWS_API_KEY')
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'

# Лимиты запросов (официальные лимиты Google Gemini 2.5 Flash Free Tier)
MINUTE_LIMIT = 10  # 10 запросов в минуту
DAILY_LIMIT = 250  # 250 запросов в день

# Хранилище данных
user_sessions: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
request_counts: Dict[int, Dict[str, List[datetime]]] = defaultdict(lambda: {'minute': [], 'day': []})
voice_settings: Dict[int, bool] = defaultdict(lambda: True)  # По умолчанию голосовые ответы включены

class GeminiBot:
    def __init__(self):
        self.bot = None
        # Инициализация NewsAPI если ключ есть
        self.news_client = NewsApiClient(api_key=NEWS_API_KEY) if NEWS_API_KEY else None
        logger.info(f"NewsAPI initialized: {'Yes' if self.news_client else 'No (missing API key)'}")
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        user_id = update.effective_user.id
        welcome_message = """🤖 Добро пожаловать в Gemini Bot!

Я могу помочь вам с:
• 💬 Ответами на текстовые вопросы
• 🎤➡️🎵 Голосовыми диалогами (отправьте голосовое - получите голосовой ответ!)
• 🖼️ Анализом изображений (НЕ создаю картинки!)
• 💻 Работой с кодом
• 🌐 Актуальной информацией из интернета (новости, Wikipedia, поиск)

Доступные команды:
/start - Показать это сообщение
/help - Справка по командам  
/clear - Очистить историю чата
/limits - Показать лимиты запросов
/voice - Включить/отключить голосовые ответы

🎵 НОВИНКА: Отправьте голосовое сообщение - я отвечу голосом!
🌐 НОВИНКА: Бот автоматически ищет актуальную информацию в интернете!

⚠️ ВАЖНО: Я могу только анализировать изображения и рассказать что на них, но НЕ МОГУ создавать или редактировать картинки!

Просто отправьте мне текст, голосовое сообщение или изображение, и я помогу вам!"""
        
        await update.message.reply_text(welcome_message)
        
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        help_message = """📋 Справка по командам:

/start - Приветствие и основная информация
/help - Показать эту справку
/clear - Очистить историю переписки (бот забудет предыдущие сообщения)
/limits - Показать текущие лимиты запросов
/voice - Включить/отключить голосовые ответы (по умолчанию включены)

🔄 Как пользоваться:
• Отправьте текстовое сообщение для получения ответа
• 🎤➡️🎵 Отправьте голосовое сообщение - я отвечу голосом!
• Отправьте изображение с подписью или без для анализа
• Отправьте код для его анализа или объяснения

⚠️ ВАЖНО про изображения:
• Я АНАЛИЗИРУЮ изображения (описываю что вижу)
• Я НЕ СОЗДАЮ новые картинки или фото
• Я НЕ РЕДАКТИРУЮ существующие изображения

🎵 НОВИНКА - Голосовые диалоги:
• Отправьте голосовое сообщение - получите голосовой ответ!
• Автоматическое распознавание русского и английского
• Автоматический выбор языка для ответа
• Полноценный голосовой диалог с AI

🌐 НОВИНКА - Актуальная информация:
• Бот автоматически определяет когда нужны свежие данные
• Ищет информацию в Wikipedia, DuckDuckGo и новостях
• Работает со словами: "сегодня", "сейчас", "новости", "актуальный", "курс", "цена"
• Пример: "Какие новости сегодня?" или "Курс доллара сейчас?"

⚡ Лимиты:
• 10 запросов в минуту
• 250 запросов в день

История чата сохраняется (последние 50 сообщений) для контекста разговора."""
        
        await update.message.reply_text(help_message)
        
    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /clear"""
        user_id = update.effective_user.id
        user_sessions[user_id].clear()
        await update.message.reply_text("🗑️ История чата очищена! Я забыл все предыдущие сообщения.")
        
    async def limits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /limits"""
        user_id = update.effective_user.id
        remaining_minute, remaining_day = self.get_remaining_requests(user_id)
        
        limits_message = f"""📊 Ваши текущие лимиты:

🕐 В этой минуте: {remaining_minute}/{MINUTE_LIMIT}
📅 Сегодня: {remaining_day}/{DAILY_LIMIT}

Лимиты обновляются автоматически."""
        
        await update.message.reply_text(limits_message)
        
    async def voice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /voice - управление голосовыми ответами"""
        user_id = update.effective_user.id
        
        # Переключение состояния голосовых ответов
        voice_settings[user_id] = not voice_settings[user_id]
        
        if voice_settings[user_id]:
            message = """🎵 Голосовые ответы ВКЛЮЧЕНЫ!

Теперь когда вы отправляете голосовое сообщение, я буду отвечать голосом.

Для отключения используйте команду /voice снова."""
        else:
            message = """🔇 Голосовые ответы ОТКЛЮЧЕНЫ!

Теперь на голосовые сообщения я буду отвечать только текстом.

Для включения используйте команду /voice снова."""
        
        await update.message.reply_text(message)

    def clean_text_for_speech(self, text: str) -> str:
        """Очистка текста от markdown и специальных символов для лучшего озвучивания"""
        # Убираем markdown символы
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # **жирный** -> жирный
        text = re.sub(r'\*([^*]+)\*', r'\1', text)      # *курсив* -> курсив
        text = re.sub(r'__([^_]+)__', r'\1', text)      # __подчеркнутый__ -> подчеркнутый
        text = re.sub(r'_([^_]+)_', r'\1', text)        # _курсив_ -> курсив
        text = re.sub(r'`([^`]+)`', r'\1', text)        # `код` -> код
        text = re.sub(r'```[^`]*```', '', text)         # Удаляем блоки кода полностью
        
        # Убираем специальные символы
        text = re.sub(r'[#•→←↑↓⚡🔥💡📊🎯🔧⚙️]', '', text)  # Убираем эмодзи и символы
        text = re.sub(r'[-–—]{2,}', ' ', text)          # Длинные тире
        text = re.sub(r'[|]', ' ', text)                # Вертикальные линии
        
        # Заменяем сокращения на полные слова для лучшего произношения
        replacements = {
            'API': 'А-П-И',
            'HTTP': 'Х-Т-Т-П',
            'URL': 'Ю-Р-Л',
            'CSS': 'Ц-С-С',
            'HTML': 'Х-Т-М-Л',
            'JSON': 'Д-Ж-Е-Й-С-О-Н',
            'AI': 'А-И',
            'ML': 'М-Л',
            'CI/CD': 'Ц-И слэш Ц-Д',
        }
        
        for abbr, replacement in replacements.items():
            text = re.sub(r'\b' + abbr + r'\b', replacement, text, flags=re.IGNORECASE)
        
        # Очистка множественных пробелов и переносов строк
        text = re.sub(r'\n+', ' ', text)               # Переносы строк -> пробелы
        text = re.sub(r'\s+', ' ', text)               # Множественные пробелы -> один пробел
        text = text.strip()                            # Убираем пробелы в начале и конце
        
        return text

    def clean_old_requests(self, user_id: int):
        """Очистка старых запросов"""
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        day_ago = now - timedelta(days=1)
        
        # Очистка минутных запросов
        request_counts[user_id]['minute'] = [
            req_time for req_time in request_counts[user_id]['minute'] 
            if req_time > minute_ago
        ]
        
        # Очистка дневных запросов
        request_counts[user_id]['day'] = [
            req_time for req_time in request_counts[user_id]['day'] 
            if req_time > day_ago
        ]

    def get_remaining_requests(self, user_id: int) -> tuple:
        """Получение оставшихся запросов"""
        self.clean_old_requests(user_id)
        
        minute_count = len(request_counts[user_id]['minute'])
        day_count = len(request_counts[user_id]['day'])
        
        remaining_minute = max(0, MINUTE_LIMIT - minute_count)
        remaining_day = max(0, DAILY_LIMIT - day_count)
        
        return remaining_minute, remaining_day

    def can_make_request(self, user_id: int) -> bool:
        """Проверка возможности сделать запрос"""
        remaining_minute, remaining_day = self.get_remaining_requests(user_id)
        return remaining_minute > 0 and remaining_day > 0

    def add_request(self, user_id: int):
        """Добавление запроса в счетчик"""
        now = datetime.now()
        request_counts[user_id]['minute'].append(now)
        request_counts[user_id]['day'].append(now)

    async def call_gemini_api(self, messages: List[dict]) -> Optional[str]:
        """Вызов API Gemini"""
        headers = {
            'Content-Type': 'application/json',
        }
        
        payload = {
            'contents': [
                {
                    'parts': messages
                }
            ],
            'generationConfig': {
                'temperature': 0.7,
                'topK': 40,
                'topP': 0.95,
                'maxOutputTokens': 2048,
            }
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                logger.debug(f"Sending request to Gemini API...")
                async with session.post(
                    f"{GEMINI_API_URL}?key={AI_API_KEY}",
                    headers=headers,
                    json=payload
                ) as response:
                    logger.debug(f"Gemini API responded with status: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        if 'candidates' in data and len(data['candidates']) > 0:
                            result = data['candidates'][0]['content']['parts'][0]['text']
                            logger.info(f"Gemini API success: received {len(result)} characters")
                            return result
                        else:
                            logger.error(f"Gemini API: No candidates in response. Full response: {data}")
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(f"Gemini API Error: {response.status}")
                        logger.error(f"Error details: {error_text}")
                        return None
        except Exception as e:
            logger.error(f"Exception calling Gemini API: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            return None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная обработка текстовых сообщений с актуальными данными"""
        user_id = update.effective_user.id
        message_text = update.message.text
        
        logger.info(f"Received message from user {user_id}: {message_text[:100]}...")
        
        # Проверка лимитов
        if not self.can_make_request(user_id):
            remaining_minute, remaining_day = self.get_remaining_requests(user_id)
            await update.message.reply_text(
                f"❌ Превышен лимит запросов!\n\nОсталось запросов: {remaining_minute}/{MINUTE_LIMIT} в этой минуте, {remaining_day}/{DAILY_LIMIT} сегодня."
            )
            return

        # Инициализация сессии пользователя
        if user_id not in user_sessions:
            user_sessions[user_id] = deque(maxlen=50)

        # Добавление сообщения пользователя в историю
        user_sessions[user_id].append({
            'role': 'user',
            'content': message_text,
            'timestamp': datetime.now()
        })

        # Отправка индикатора печати
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

        try:
            # Проверяем, нужны ли актуальные данные
            if self.needs_current_data(message_text):
                await update.message.reply_text("🔍 Ищу актуальную информацию в интернете...")
                current_info = await self.get_current_data(message_text)
                
                if current_info:
                    # Формируем расширенный запрос с актуальными данными
                    enhanced_message = f"""Пользователь спрашивает: {message_text}

Актуальная информация из интернета:
{current_info}

Пожалуйста, ответь на вопрос пользователя, используя как свои базовые знания, так и предоставленную актуальную информацию. Если актуальная информация противоречит твоим данным, отдавай приоритет свежей информации из интернета. Отвечай на русском языке."""
                    
                    # Подготовка сообщений для Gemini API
                    messages = [{'text': enhanced_message}]
                    logger.info(f"Enhanced query prepared for user {user_id} with current data")
                else:
                    # Если актуальные данные не найдены, используем обычный запрос
                    messages = [{'text': message_text}]
                    logger.info(f"No current data found, using regular query for user {user_id}")
            else:
                # Обычный запрос без поиска актуальных данных
                messages = [{'text': message_text}]
                logger.info(f"Regular query for user {user_id} (no current data needed)")

            # Добавление контекста из истории (последние 10 сообщений)
            context_messages = []
            for session_msg in list(user_sessions[user_id])[-10:]:
                if session_msg['role'] == 'user':
                    context_messages.insert(0, {'text': f"Пользователь ранее: {session_msg['content']}"})
                else:
                    context_messages.insert(0, {'text': f"Ассистент ранее: {session_msg['content']}"})
            
            # Объединяем контекст с текущим сообщением
            all_messages = context_messages + messages

            # Вызов Gemini API
            logger.info(f"Calling Gemini API for user {user_id} with {len(all_messages)} messages")
            response = await self.call_gemini_api(all_messages)
            
            if response:
                logger.info(f"Received response from Gemini API for user {user_id}: {len(response)} characters")
                
                # Добавление запроса в счетчик
                self.add_request(user_id)
                
                # Добавление ответа в историю
                user_sessions[user_id].append({
                    'role': 'assistant',
                    'content': response,
                    'timestamp': datetime.now()
                })
                
                # Получение оставшихся запросов
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                
                # Отправка ответа
                await self.safe_send_message(update, response, remaining_minute, remaining_day, user_id)
            else:
                logger.error(f"No response received from Gemini API for user {user_id}")
                await self.safe_send_message(update, "❌ Произошла ошибка при обращении к AI. Попробуйте позже.", None, None, user_id)
                
        except Exception as e:
            logger.error(f"Error in enhanced_handle_message for user {user_id}: {e}")
            await self.safe_send_message(update, "❌ Произошла ошибка при обработке сообщения.", None, None, user_id)

    async def safe_send_message(self, update: Update, response: str, remaining_minute: int = None, remaining_day: int = None, user_id: int = None):
        """Безопасная отправка сообщения с множественными fallback вариантами"""
        try:
            # Формирование полного ответа
            if remaining_minute is not None and remaining_day is not None:
                full_response = f"{response}\n\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в этой минуте, {remaining_day}/{DAILY_LIMIT} сегодня."
            else:
                full_response = response
            
            # Попытка 1: отправка как есть (без принудительного парсинга)
            try:
                await update.message.reply_text(full_response)
                logger.info(f"Message sent successfully to user {user_id}")
                return
            except Exception as e:
                logger.warning(f"First send attempt failed for user {user_id}: {e}")
            
            # Попытка 2: без эмодзи
            try:
                if remaining_minute is not None and remaining_day is not None:
                    simple_response = f"{response}\n\nОсталось запросов: {remaining_minute}/{MINUTE_LIMIT} в этой минуте, {remaining_day}/{DAILY_LIMIT} сегодня."
                else:
                    simple_response = response
                await update.message.reply_text(simple_response)
                logger.info(f"Message sent successfully (without emoji) to user {user_id}")
                return
            except Exception as e:
                logger.warning(f"Second send attempt failed for user {user_id}: {e}")
            
            # Попытка 3: только основной ответ
            try:
                await update.message.reply_text(response)
                logger.info(f"Message sent successfully (response only) to user {user_id}")
                return
            except Exception as e:
                logger.warning(f"Third send attempt failed for user {user_id}: {e}")
            
            # Попытка 4: экранированный ответ
            try:
                escaped_response = response.replace('.', '\\.')
                await update.message.reply_text(escaped_response)
                logger.info(f"Message sent successfully (escaped) to user {user_id}")
                return
            except Exception as e:
                logger.warning(f"Fourth send attempt failed for user {user_id}: {e}")
            
            # Попытка 5: крайний случай - общее сообщение об ошибке
            await update.message.reply_text("Ответ получен, но произошла ошибка при отправке. Попробуйте еще раз.")
            logger.error(f"All send attempts failed for user {user_id}, sent generic error message")
            
        except Exception as e:
            logger.error(f"Critical error in safe_send_message for user {user_id}: {e}")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка изображений"""
        user_id = update.effective_user.id
        
        logger.info(f"Received photo from user {user_id}")
        
        # Проверка лимитов
        if not self.can_make_request(user_id):
            remaining_minute, remaining_day = self.get_remaining_requests(user_id)
            await update.message.reply_text(
                f"❌ Превышен лимит запросов!\n\nОсталось запросов: {remaining_minute}/{MINUTE_LIMIT} в этой минуте, {remaining_day}/{DAILY_LIMIT} сегодня."
            )
            return

        try:
            # Показать индикатор набора
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
            
            # Получение файла изображения
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            
            # Конвертация в base64
            photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
            
            # Получение подписи к изображению
            caption = update.message.caption or "Проанализируй это изображение"
            
            # Подготовка сообщения для API
            messages = [
                {'text': caption},
                {
                    'inline_data': {
                        'mime_type': 'image/jpeg',
                        'data': photo_base64
                    }
                }
            ]
            
            # Добавление в историю
            user_sessions[user_id].append({
                'role': 'user',
                'content': f"[Изображение] {caption}",
                'timestamp': datetime.now()
            })

            # Вызов API
            logger.info(f"Calling Gemini API for image analysis from user {user_id}")
            response = await self.call_gemini_api(messages)
            
            if response:
                logger.info(f"Received image analysis response for user {user_id}: {len(response)} characters")
                
                # Добавление запроса в счетчик
                self.add_request(user_id)
                
                # Добавление ответа в историю
                user_sessions[user_id].append({
                    'role': 'assistant',
                    'content': response,
                    'timestamp': datetime.now()
                })
                
                # Получение оставшихся запросов
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                
                # Безопасная отправка ответа
                await self.safe_send_message(update, response, remaining_minute, remaining_day, user_id)
            else:
                logger.error(f"No response received from Gemini API for image analysis from user {user_id}")
                await self.safe_send_message(update, "❌ Произошла ошибка при анализе изображения. Попробуйте позже.", None, None, user_id)
                
        except Exception as e:
            logger.error(f"Error handling photo from user {user_id}: {e}")
            await self.safe_send_message(update, "❌ Произошла ошибка при обработке изображения.", None, None, user_id)

    async def speech_to_text(self, audio_bytes: bytes) -> Optional[str]:
        """Конвертация аудио в текст"""
        try:
            # Создаем временные файлы
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
                ogg_file.write(audio_bytes)
                ogg_path = ogg_file.name
            
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name
            
            try:
                # Конвертация OGG в WAV с помощью pydub
                logger.debug("Converting OGG to WAV...")
                audio = AudioSegment.from_ogg(ogg_path)
                audio = audio.set_frame_rate(16000).set_channels(1)  # Оптимизация для распознавания
                audio.export(wav_path, format="wav")
                
                # Распознавание речи
                logger.debug("Recognizing speech...")
                recognizer = sr.Recognizer()
                
                with sr.AudioFile(wav_path) as source:
                    audio_data = recognizer.record(source)
                
                # Пробуем сначала русский, потом английский
                try:
                    text = recognizer.recognize_google(audio_data, language="ru-RU")
                    logger.info(f"Speech recognized (Russian): {len(text)} characters")
                    return text
                except sr.UnknownValueError:
                    # Если русский не сработал, пробуем английский
                    try:
                        text = recognizer.recognize_google(audio_data, language="en-US")
                        logger.info(f"Speech recognized (English): {len(text)} characters")
                        return text
                    except sr.UnknownValueError:
                        logger.warning("Could not understand audio in both Russian and English")
                        return None
                        
            finally:
                # Очистка временных файлов
                try:
                    os.unlink(ogg_path)
                    os.unlink(wav_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"Error in speech recognition: {e}")
            return None

    async def text_to_speech(self, text: str, language: str = "ru") -> Optional[bytes]:
        """Синтез речи из текста с помощью gTTS"""
        try:
            # Проверка на минимальную длину текста
            if not text or len(text.strip()) < 3:
                logger.warning("Text too short for TTS")
                return None
                
            # Ограничение длины текста (gTTS имеет лимиты)
            if len(text) > 1000:
                text = text[:1000] + "..."
            
            logger.debug(f"Converting text to speech: {len(text)} characters")
            
            # Создание временного файла для аудио
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                temp_path = temp_file.name
            
            try:
                # Создание TTS объекта
                tts = gTTS(text=text, lang=language, slow=False)
                
                # Сохранение в временный файл
                tts.save(temp_path)
                
                # Чтение байтов из файла
                with open(temp_path, 'rb') as audio_file:
                    audio_bytes = audio_file.read()
                
                logger.info(f"Text-to-speech success: generated {len(audio_bytes)} bytes")
                return audio_bytes
                
            finally:
                # Очистка временного файла
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"Error in text-to-speech: {e}")
            return None

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка голосовых сообщений"""
        user_id = update.effective_user.id
        logger.info(f"Received voice message from user {user_id}")
        
        try:
            # Проверка лимитов
            if not self.can_make_request(user_id):
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                await update.message.reply_text(
                    f"❌ Превышен лимит запросов!\n\nОсталось запросов: {remaining_minute}/{MINUTE_LIMIT} в этой минуте, {remaining_day}/{DAILY_LIMIT} сегодня."
                )
                return

            # Отправка индикатора печати
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            logger.info(f"Sent typing indicator for voice processing from user {user_id}")
            
            # Получение голосового файла
            voice_file = await update.message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            
            logger.info(f"Downloaded voice message: {len(voice_bytes)} bytes")
            
            # Распознавание речи
            await update.message.reply_text("🎤 Распознаю речь...")
            transcribed_text = await self.speech_to_text(bytes(voice_bytes))
            
            if not transcribed_text:
                await self.safe_send_message(update, "❌ Не удалось распознать речь. Попробуйте еще раз или говорите четче.", None, None, user_id)
                return
            
            logger.info(f"Voice transcribed for user {user_id}: {transcribed_text[:100]}...")
            
            # Подготовка сообщения для Gemini API (БЕЗ контекста истории для независимых ответов)
            messages = [{'text': transcribed_text}]

            # Отправка уведомления о том, что речь распознана
            await update.message.reply_text(f"✅ Распознано: \"{transcribed_text}\"\n\n💭 Думаю над ответом...")
            
            logger.info(f"Calling Gemini API for voice message from user {user_id}")
            response = await self.call_gemini_api(messages)
            
            if response:
                logger.info(f"Received response from Gemini API for voice message from user {user_id}: {len(response)} characters")
                
                # Добавление запроса в счетчик
                self.add_request(user_id)
                
                # Добавление в историю только если нужно (для обычных сообщений)
                # Голосовые сообщения теперь независимы и не сохраняются в историю
                
                # Получение оставшихся запросов
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                
                # Проверка настроек голосовых ответов пользователя
                if voice_settings[user_id]:
                    # Генерация голосового ответа
                    await update.message.reply_text("🎵 Генерирую голосовой ответ...")
                    
                    # Очистка текста от markdown символов для лучшего озвучивания
                    clean_response = self.clean_text_for_speech(response)
                    
                    # Определение языка для TTS (русский если в тексте есть кириллица, иначе английский)
                    tts_language = "ru" if any('\u0400' <= char <= '\u04FF' for char in clean_response) else "en"
                    
                    # Синтез речи
                    voice_bytes = await self.text_to_speech(clean_response, tts_language)
                    
                    if voice_bytes:
                        try:
                            # Отправка голосового сообщения
                            await update.message.reply_voice(
                                voice=BytesIO(voice_bytes),
                                caption=f"🎤➡️🎵 Голосовой ответ\n\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня\n\n💡 Отключить голосовые ответы: /voice"
                            )
                            logger.info(f"Successfully sent voice response to user {user_id}")
                        except Exception as e:
                            logger.error(f"Failed to send voice message to user {user_id}: {e}")
                            # Fallback к текстовому ответу
                            await self.safe_send_message(update, f"❌ Не удалось отправить голосовой ответ, вот текст:\n\n{response}", remaining_minute, remaining_day, user_id)
                    else:
                        logger.error(f"Voice synthesis failed for user {user_id}")
                        # Fallback к текстовому ответу
                        await self.safe_send_message(update, f"❌ Не удалось создать голосовой ответ, вот текст:\n\n{response}", remaining_minute, remaining_day, user_id)
                else:
                    # Текстовый ответ если голосовые отключены
                    await self.safe_send_message(update, f"📝 {response}\n\n💡 Включить голосовые ответы: /voice", remaining_minute, remaining_day, user_id)
            else:
                logger.error(f"No response received from Gemini API for voice message from user {user_id}")
                await self.safe_send_message(update, "❌ Произошла ошибка при обращении к AI. Попробуйте позже.", None, None, user_id)
                
        except Exception as e:
            logger.error(f"Error handling voice message from user {user_id}: {e}")
            await self.safe_send_message(update, "❌ Произошла ошибка при обработке голосового сообщения.", None, None, user_id)

    def is_markdown(self, text: str) -> bool:
        """Проверка наличия markdown в тексте (не используется)"""
        # Функция оставлена для совместимости, но не используется
        return False

    async def search_duckduckgo(self, query: str) -> Optional[str]:
        """Поиск через DuckDuckGo Instant Answer API"""
        try:
            url = "https://api.duckduckgo.com/"
            params = {
                'q': query,
                'format': 'json',
                'no_html': '1',
                'skip_disambig': '1'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Извлекаем полезную информацию
                        result = []
                        
                        if data.get('Abstract'):
                            result.append(f"Краткое описание: {data['Abstract']}")
                        
                        if data.get('Definition'):
                            result.append(f"Определение: {data['Definition']}")
                        
                        # Связанные темы
                        if data.get('RelatedTopics'):
                            topics = [topic.get('Text', '') for topic in data['RelatedTopics'][:3] if topic.get('Text')]
                            if topics:
                                result.append(f"Связанные темы: {'; '.join(topics)}")
                        
                        logger.info(f"DuckDuckGo search result: {len(result)} items found")
                        return '\n'.join(result) if result else None
                        
        except Exception as e:
            logger.error(f"DuckDuckGo search error: {e}")
            return None
    
    async def search_wikipedia(self, query: str) -> Optional[str]:
        """Поиск в Wikipedia"""
        try:
            # Поиск на русском языке
            wikipedia.set_lang("ru")
            
            # Поиск страницы
            search_results = wikipedia.search(query, results=3)
            if not search_results:
                # Если на русском ничего не найдено, пробуем английский
                wikipedia.set_lang("en")
                search_results = wikipedia.search(query, results=3)
            
            if search_results:
                try:
                    # Возвращаем краткое описание (первые 3 предложения)
                    summary = wikipedia.summary(search_results[0], sentences=3)
                    logger.info(f"Wikipedia search result: {len(summary)} characters")
                    return f"Wikipedia: {summary}"
                except wikipedia.exceptions.DisambiguationError as e:
                    # Если неоднозначность, берем первый вариант
                    summary = wikipedia.summary(e.options[0], sentences=3)
                    logger.info(f"Wikipedia disambiguation resolved: {len(summary)} characters")
                    return f"Wikipedia: {summary}"
                    
        except Exception as e:
            logger.error(f"Wikipedia search error: {e}")
            
        return None
    
    async def search_news(self, query: str) -> Optional[str]:
        """Поиск актуальных новостей через NewsAPI"""
        if not self.news_client:
            logger.warning("NewsAPI client not initialized - missing API key")
            return None
            
        try:
            # Поиск новостей за последние 7 дней
            news = self.news_client.get_everything(
                q=query,
                language='ru',
                sort_by='publishedAt',
                page_size=3,
                from_param=(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            )
            
            if news['articles']:
                articles = []
                for article in news['articles'][:3]:
                    title = article.get('title', '')
                    description = article.get('description', '')
                    published = article.get('publishedAt', '')
                    
                    if title:
                        article_text = f"{title}"
                        if description:
                            article_text += f": {description}"
                        if published:
                            date = published.split('T')[0]
                            article_text += f" ({date})"
                        articles.append(article_text)
                
                logger.info(f"News search result: {len(articles)} articles found")
                return f"Актуальные новости:\n" + '\n'.join(articles)
                
        except Exception as e:
            logger.error(f"News search error: {e}")
            
        return None
    
    def needs_current_data(self, query: str) -> bool:
        """Определяет, нужны ли актуальные данные для ответа"""
        current_keywords = [
            # Временные индикаторы
            'сегодня', 'вчера', 'сейчас', 'текущий', 'актуальн', 'последн',
            'новости', 'события', 'происходит', 'случилось', 'недавно',
            
            # Изменяющиеся данные
            'курс', 'цена', 'стоимость', 'погода', 'температура',
            'котировки', 'валют', 'биткоин', 'криптовалют', 'доллар', 'евро',
            
            # Свежая информация
            '2024', '2025', 'этот год', 'этот месяц', 'на данный момент',
            'что нового', 'обновления', 'изменения',
            
            # Английские аналоги
            'today', 'now', 'current', 'latest', 'recent', 'news', 'update'
        ]
        
        query_lower = query.lower()
        result = any(keyword in query_lower for keyword in current_keywords)
        logger.info(f"Current data needed for query '{query[:50]}...': {result}")
        return result
    
    async def get_current_data(self, query: str) -> str:
        """Получает актуальные данные из различных источников"""
        results = []
        
        logger.info(f"Starting current data search for: {query[:50]}...")
        
        # Поиск в DuckDuckGo
        ddg_result = await self.search_duckduckgo(query)
        if ddg_result:
            results.append(f"🔍 Поиск: {ddg_result}")
        
        # Поиск в Wikipedia
        wiki_result = await self.search_wikipedia(query)
        if wiki_result:
            results.append(f"📚 {wiki_result}")
        
        # Поиск новостей (если есть ключ API)
        news_result = await self.search_news(query)
        if news_result:
            results.append(f"📰 {news_result}")
        
        combined_result = '\n\n'.join(results) if results else ""
        logger.info(f"Current data search completed: {len(results)} sources found")
        return combined_result

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    import traceback
    
    error_msg = f"Exception while handling an update: {context.error}"
    
    # Специальная обработка для конфликта экземпляров
    if "Conflict" in str(context.error) and "getUpdates" in str(context.error):
        logger.error("CONFLICT DETECTED: Multiple bot instances running!")
        logger.error("This usually means:")
        logger.error("1. Bot is running locally while also on Render")
        logger.error("2. Multiple Render deployments with same token")
        logger.error("3. Webhook was not properly cleared")
        return
    
    logger.error(error_msg)
    logger.error(f"Traceback: {traceback.format_exc()}")
    
    # Если есть update, логируем его для отладки
    if update:
        logger.error(f"Update that caused error: {update}")

async def watchdog():
    """Функция watchdog для мониторинга здоровья бота и предотвращения засыпания"""
    start_time = datetime.now()
    last_activity = datetime.now()
    
    while True:
        try:
            await asyncio.sleep(180)  # Каждые 3 минуты
            
            current_time = datetime.now()
            uptime = current_time - start_time
            
            # Логирование активности для предотвращения засыпания
            logger.info(f"🔍 Bot Watchdog: Uptime {uptime}, Last activity: {current_time}")
            logger.info(f"📊 Active users: {len(user_sessions)}, Total request counters: {len(request_counts)}")
            
            # Очистка старых данных для экономии памяти
            if current_time.minute % 10 == 0:  # Каждые 10 минут
                logger.info("🧹 Cleaning old session data...")
                cleanup_old_data()
                
        except Exception as e:
            logger.error(f"Watchdog error: {e}")

def cleanup_old_data():
    """Очистка старых данных сессий"""
    now = datetime.now()
    cutoff = now - timedelta(hours=24)
    
    # Очистка старых пользовательских сессий
    users_to_remove = []
    for user_id, session in user_sessions.items():
        if session and len(session) > 0:
            # Если последнее сообщение очень старое, удаляем сессию
            try:
                if session[-1].get('timestamp', now) < cutoff:
                    users_to_remove.append(user_id)
            except (AttributeError, IndexError):
                # Если структура данных неправильная, очищаем
                users_to_remove.append(user_id)
    
    for user_id in users_to_remove:
        del user_sessions[user_id]
        logger.debug(f"Cleaned old session for user {user_id}")
    
    if users_to_remove:
        logger.info(f"🧹 Cleaned {len(users_to_remove)} old user sessions")

async def run_bot():
    """Запуск бота"""
    if not TELEGRAM_TOKEN or not AI_API_KEY:
        logger.error("Missing required environment variables: TELEGRAM_TOKEN or AI_API_KEY")
        return
    
    # Создание приложения
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    bot = GeminiBot()
    
    # Добавление обработчиков
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("clear", bot.clear_command))
    application.add_handler(CommandHandler("limits", bot.limits_command))
    application.add_handler(CommandHandler("voice", bot.voice_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, bot.handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, bot.handle_voice))
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("Starting bot...")
    await application.initialize()
    await application.start()
    
    # Агрессивная очистка webhook и предыдущих подключений
    logger.info("Performing ULTRA-aggressive webhook cleanup...")
    
    # Принудительная остановка всех предыдущих экземпляров
    try:
        logger.info("Force clearing all pending updates...")
        # Получаем и очищаем все pending updates
        try:
            updates = await application.bot.get_updates(timeout=1, limit=100)
            if updates:
                logger.info(f"Found {len(updates)} pending updates - clearing...")
                # Получаем последний update_id для пропуска
                last_update_id = updates[-1].update_id
                await application.bot.get_updates(offset=last_update_id + 1, timeout=1)
        except Exception as e:
            logger.info(f"Pending updates clear attempt: {e}")
    except Exception as e:
        logger.warning(f"Force clear failed: {e}")
    
    cleanup_attempts = 8  # Увеличено с 5 до 8
    for attempt in range(cleanup_attempts):
        try:
            logger.info(f"Webhook cleanup attempt {attempt + 1}/{cleanup_attempts}")
            
            # Множественная очистка webhook
            await application.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(1)
            await application.bot.delete_webhook()
            await asyncio.sleep(1)
            
            # Дополнительная очистка с различными параметрами
            try:
                await application.bot.set_webhook("")  # Пустой webhook
                await asyncio.sleep(1)
                await application.bot.delete_webhook(drop_pending_updates=True)
            except:
                pass
                
            logger.info(f"Webhook cleanup attempt {attempt + 1} completed")
            break
        except Exception as e:
            logger.warning(f"Webhook cleanup attempt {attempt + 1} failed: {e}")
            if attempt < cleanup_attempts - 1:
                await asyncio.sleep(5)  # Увеличено время ожидания
    
    logger.info("Waiting for COMPLETE cleanup...")
    await asyncio.sleep(10)  # Увеличено с 5 до 10 секунд
    
    # Запуск polling с улучшенным retry механизмом
    max_retries = 5
    for attempt in range(max_retries):
        try:
            logger.info(f"Starting polling (attempt {attempt + 1}/{max_retries})...")
            
            # Дополнительная очистка перед каждой попыткой
            if attempt > 0:
                logger.info("Additional cleanup before retry...")
                try:
                    await application.bot.delete_webhook(drop_pending_updates=True)
                    await asyncio.sleep(2)
                except:
                    pass
            
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES, 
                drop_pending_updates=True,
                timeout=45,
                pool_timeout=45,
                connect_timeout=30,
                read_timeout=30
            )
            logger.info("Polling started successfully")
            break
        except Exception as e:
            logger.error(f"Polling failed on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 15  # Увеличиваем время ожидания
                logger.info(f"Waiting {wait_time} seconds before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error("All polling attempts failed - this indicates a serious configuration issue")
                logger.error("Possible causes:")
                logger.error("1. Bot token is being used by another instance")
                logger.error("2. Webhook is set externally")
                logger.error("3. Network connectivity issues")
                return
    
    # Поддержание работы
    try:
        logger.info("Bot is now running and waiting for messages...")
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Stopping bot...")
    finally:
        await application.stop()

async def main():
    """Основная функция"""
    logger.info("Starting Gemini Telegram Bot...")
    
    # Небольшая задержка для стабильности
    await asyncio.sleep(1)
    
    # Запуск HTTP сервера, бота и watchdog параллельно
    try:
        logger.info("🚀 Starting all services: HTTP server + Bot + Watchdog...")
        await asyncio.gather(
            start_server(),
            run_bot(),
            watchdog()
        )
    except Exception as e:
        logger.error(f"Critical error in main: {e}")
        # Попытка перезапуска через 30 секунд
        logger.info("Attempting restart in 30 seconds...")
        await asyncio.sleep(30)
        raise

if __name__ == '__main__':
    asyncio.run(main()) 