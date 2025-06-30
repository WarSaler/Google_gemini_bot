import os
import logging
import asyncio
import base64
import tempfile
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Dict, List, Optional
import aiohttp
from io import BytesIO
import speech_recognition as sr
from pydub import AudioSegment

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
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'

# Лимиты запросов (официальные лимиты Google Gemini 2.5 Flash Free Tier)
MINUTE_LIMIT = 10  # 10 запросов в минуту
DAILY_LIMIT = 250  # 250 запросов в день

# Хранилище данных
user_sessions: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
request_counts: Dict[int, Dict[str, List[datetime]]] = defaultdict(lambda: {'minute': [], 'day': []})

class GeminiBot:
    def __init__(self):
        self.bot = None
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        user_id = update.effective_user.id
        welcome_message = """🤖 Добро пожаловать в Gemini Bot!

Я могу помочь вам с:
• 💬 Ответами на текстовые вопросы
• 🎤 Обработкой голосовых сообщений (расшифровка речи)
• 🖼️ Анализом изображений (НЕ создаю картинки!)
• 💻 Работой с кодом

Доступные команды:
/start - Показать это сообщение
/help - Справка по командам  
/clear - Очистить историю чата
/limits - Показать лимиты запросов

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

🔄 Как пользоваться:
• Отправьте текстовое сообщение для получения ответа
• 🎤 Отправьте голосовое сообщение - я расшифрую речь и отвечу
• Отправьте изображение с подписью или без для анализа
• Отправьте код для его анализа или объяснения

⚠️ ВАЖНО про изображения:
• Я АНАЛИЗИРУЮ изображения (описываю что вижу)
• Я НЕ СОЗДАЮ новые картинки или фото
• Я НЕ РЕДАКТИРУЮ существующие изображения

🎤 ВАЖНО про голосовые:
• Я РАСШИФРОВЫВАЮ ваши голосовые сообщения в текст
• Отвечаю текстом на расшифрованное сообщение
• Работаю с русским и английским языками

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
        """Обработка текстовых сообщений"""
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
            user_sessions[user_id] = []

        # Добавление сообщения пользователя в историю
        user_sessions[user_id].append({
            'role': 'user',
            'content': message_text,
            'timestamp': datetime.now()
        })

        # Ограничение количества сообщений в истории
        if len(user_sessions[user_id]) > 100:  # 50 пар сообщений
            user_sessions[user_id] = user_sessions[user_id][-100:]

        # Подготовка сообщений для API
        messages = []
        for msg in user_sessions[user_id]:
            if msg['role'] == 'user':
                messages.append({'text': msg['content']})
            elif msg['role'] == 'assistant':
                messages.append({'text': f"Assistant: {msg['content']}"})

        # Показать индикатор набора
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
            logger.info(f"Sent typing indicator for user {user_id}")
        except Exception as e:
            logger.warning(f"Could not send typing indicator for user {user_id}: {e}")

        # Вызов API
        logger.info(f"Calling Gemini API for user {user_id} with {len(messages)} messages")
        response = await self.call_gemini_api(messages)
        
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
            
            # Отправка ответа с безопасной обработкой
            await self.safe_send_message(update, response, remaining_minute, remaining_day, user_id)
        else:
            logger.error(f"No response received from Gemini API for user {user_id}")
            await self.safe_send_message(update, "❌ Произошла ошибка при обращении к AI. Попробуйте позже.", None, None, user_id)

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
            
            # Добавление расшифровки в историю
            user_sessions[user_id].append({
                'role': 'user',
                'content': f"[Голосовое сообщение]: {transcribed_text}",
                'timestamp': datetime.now()
            })

            # Подготовка сообщения для Gemini API
            messages = [{'text': transcribed_text}]
            
            # Добавление контекста из истории
            for session_msg in list(user_sessions[user_id])[-10:]:  # Последние 10 сообщений
                if session_msg['role'] == 'user':
                    messages.insert(0, {'text': f"Пользователь: {session_msg['content']}"})
                else:
                    messages.insert(0, {'text': f"Ассистент: {session_msg['content']}"})

            # Отправка уведомления о том, что речь распознана
            await update.message.reply_text(f"✅ Распознано: \"{transcribed_text}\"\n\n💭 Думаю над ответом...")
            
            logger.info(f"Calling Gemini API for voice message from user {user_id}")
            response = await self.call_gemini_api(messages)
            
            if response:
                logger.info(f"Received response from Gemini API for voice message from user {user_id}: {len(response)} characters")
                
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
                
                # Отправка ответа с информацией о распознанной речи
                full_response = f"🎤 **Ваше сообщение:** {transcribed_text}\n\n📝 **Мой ответ:** {response}"
                await self.safe_send_message(update, full_response, remaining_minute, remaining_day, user_id)
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, bot.handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, bot.handle_voice))
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("Starting bot...")
    await application.initialize()
    await application.start()
    
    # Агрессивная очистка webhook и предыдущих подключений
    logger.info("Performing aggressive webhook cleanup...")
    cleanup_attempts = 5
    for attempt in range(cleanup_attempts):
        try:
            logger.info(f"Webhook cleanup attempt {attempt + 1}/{cleanup_attempts}")
            await application.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(2)
            
            # Дополнительная попытка очистки
            await application.bot.delete_webhook()
            await asyncio.sleep(1)
            
            logger.info(f"Webhook cleanup attempt {attempt + 1} completed")
            break
        except Exception as e:
            logger.warning(f"Webhook cleanup attempt {attempt + 1} failed: {e}")
            if attempt < cleanup_attempts - 1:
                await asyncio.sleep(3)
    
    logger.info("Waiting for complete cleanup...")
    await asyncio.sleep(5)  # Дополнительная пауза для полной очистки
    
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