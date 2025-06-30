import os
import logging
import asyncio
import base64
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Dict, List, Optional
import aiohttp
from io import BytesIO

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
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent'

# Лимиты запросов
HOURLY_LIMIT = 15
DAILY_LIMIT = 1500

# Хранилище данных
user_sessions: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
request_counts: Dict[int, Dict[str, List[datetime]]] = defaultdict(lambda: {'hour': [], 'day': []})

class GeminiBot:
    def __init__(self):
        self.bot = None
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        user_id = update.effective_user.id
        welcome_message = """🤖 Добро пожаловать в Gemini Bot!

Я могу помочь вам с:
• 💬 Ответами на текстовые вопросы
• 🖼️ Анализом изображений (НЕ создаю картинки!)
• 💻 Работой с кодом

Доступные команды:
/start - Показать это сообщение
/help - Справка по командам  
/clear - Очистить историю чата
/limits - Показать лимиты запросов

⚠️ ВАЖНО: Я могу только анализировать изображения и рассказать что на них, но НЕ МОГУ создавать или редактировать картинки!

Просто отправьте мне сообщение или изображение, и я помогу вам!"""
        
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
• Отправьте изображение с подписью или без для анализа
• Отправьте код для его анализа или объяснения

⚠️ ВАЖНО про изображения:
• Я АНАЛИЗИРУЮ изображения (описываю что вижу)
• Я НЕ СОЗДАЮ новые картинки или фото
• Я НЕ РЕДАКТИРУЮ существующие изображения

⚡ Лимиты:
• 15 запросов в час
• 1500 запросов в сутки

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
        remaining_hour, remaining_day = self.get_remaining_requests(user_id)
        
        limits_message = f"""📊 Ваши текущие лимиты:

🕐 В этом часе: {remaining_hour}/{HOURLY_LIMIT}
📅 Сегодня: {remaining_day}/{DAILY_LIMIT}

Лимиты обновляются автоматически."""
        
        await update.message.reply_text(limits_message)

    def clean_old_requests(self, user_id: int):
        """Очистка старых запросов"""
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        
        # Очистка часовых запросов
        request_counts[user_id]['hour'] = [
            req_time for req_time in request_counts[user_id]['hour'] 
            if req_time > hour_ago
        ]
        
        # Очистка дневных запросов
        request_counts[user_id]['day'] = [
            req_time for req_time in request_counts[user_id]['day'] 
            if req_time > day_ago
        ]

    def get_remaining_requests(self, user_id: int) -> tuple:
        """Получение оставшихся запросов"""
        self.clean_old_requests(user_id)
        
        hour_count = len(request_counts[user_id]['hour'])
        day_count = len(request_counts[user_id]['day'])
        
        remaining_hour = max(0, HOURLY_LIMIT - hour_count)
        remaining_day = max(0, DAILY_LIMIT - day_count)
        
        return remaining_hour, remaining_day

    def can_make_request(self, user_id: int) -> bool:
        """Проверка возможности сделать запрос"""
        remaining_hour, remaining_day = self.get_remaining_requests(user_id)
        return remaining_hour > 0 and remaining_day > 0

    def add_request(self, user_id: int):
        """Добавление запроса в счетчик"""
        now = datetime.now()
        request_counts[user_id]['hour'].append(now)
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
        user_message = update.message.text
        
        # Проверка лимитов
        if not self.can_make_request(user_id):
            remaining_hour, remaining_day = self.get_remaining_requests(user_id)
            await update.message.reply_text(
                f"❌ Превышен лимит запросов!\n\nОсталось запросов: {remaining_hour}/{HOURLY_LIMIT} в этом часе, {remaining_day}/{DAILY_LIMIT} сегодня."
            )
            return

        # Добавление сообщения в историю
        user_sessions[user_id].append({
            'role': 'user',
            'content': user_message,
            'timestamp': datetime.now()
        })

        # Подготовка сообщений для API
        messages = []
        for msg in user_sessions[user_id]:
            if msg['role'] == 'user':
                messages.append({'text': msg['content']})
            elif msg['role'] == 'assistant':
                messages.append({'text': f"Assistant: {msg['content']}"})

        # Показать индикатор набора
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

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
            remaining_hour, remaining_day = self.get_remaining_requests(user_id)
            
            # Отправка ответа с лимитами
            full_response = f"{response}\n\n📊 Осталось запросов: {remaining_hour}/{HOURLY_LIMIT} в этом часе, {remaining_day}/{DAILY_LIMIT} сегодня."
            
            # Безопасная отправка без принудительного Markdown
            try:
                await update.message.reply_text(full_response)
                logger.info(f"Message sent successfully to user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send message to user {user_id}: {e}")
                # Попытка отправить без эмодзи и спецсимволов
                simple_response = f"Ответ: {response}\n\nОсталось запросов: {remaining_hour}/{HOURLY_LIMIT} в час, {remaining_day}/{DAILY_LIMIT} в день"
                await update.message.reply_text(simple_response)
        else:
            await update.message.reply_text("❌ Произошла ошибка при обращении к AI. Попробуйте позже.")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка изображений"""
        user_id = update.effective_user.id
        
        # Проверка лимитов
        if not self.can_make_request(user_id):
            remaining_hour, remaining_day = self.get_remaining_requests(user_id)
            await update.message.reply_text(
                f"❌ Превышен лимит запросов!\n\nОсталось запросов: {remaining_hour}/{HOURLY_LIMIT} в этом часе, {remaining_day}/{DAILY_LIMIT} сегодня."
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
                remaining_hour, remaining_day = self.get_remaining_requests(user_id)
                
                # Отправка ответа с лимитами
                full_response = f"{response}\n\n📊 Осталось запросов: {remaining_hour}/{HOURLY_LIMIT} в этом часе, {remaining_day}/{DAILY_LIMIT} сегодня."
                
                # Безопасная отправка
                try:
                    await update.message.reply_text(full_response)
                    logger.info(f"Image analysis response sent successfully to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to send image analysis response to user {user_id}: {e}")
                    # Попытка отправить простой ответ
                    simple_response = f"Анализ изображения: {response}\n\nОсталось запросов: {remaining_hour}/{HOURLY_LIMIT} в час, {remaining_day}/{DAILY_LIMIT} в день"
                    await update.message.reply_text(simple_response)
            else:
                await update.message.reply_text("❌ Произошла ошибка при анализе изображения. Попробуйте позже.")
                
        except Exception as e:
            logger.error(f"Error handling photo: {e}")
            await update.message.reply_text("❌ Произошла ошибка при обработке изображения.")

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
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("Starting bot...")
    await application.initialize()
    await application.start()
    
    # Принудительная очистка webhook и предыдущих подключений
    try:
        logger.info("Clearing webhook and previous connections...")
        await application.bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(2)  # Пауза для полной очистки
        logger.info("Webhook cleared successfully")
    except Exception as e:
        logger.warning(f"Could not clear webhook: {e}")
    
    # Запуск polling с retry механизмом
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"Starting polling (attempt {attempt + 1}/{max_retries})...")
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES, 
                drop_pending_updates=True,
                timeout=30,
                pool_timeout=30
            )
            logger.info("Polling started successfully")
            break
        except Exception as e:
            logger.error(f"Polling failed on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 10
                logger.info(f"Waiting {wait_time} seconds before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error("All polling attempts failed")
                return
    
    # Поддержание работы
    try:
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
    
    # Запуск HTTP сервера и бота параллельно
    try:
        await asyncio.gather(
            start_server(),
            run_bot()
        )
    except Exception as e:
        logger.error(f"Critical error in main: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main()) 