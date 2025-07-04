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
from bs4 import BeautifulSoup

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
        import aiohttp  # Добавляем импорт здесь для предотвращения UnboundLocalError
        
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
                        logger.debug(f"Gemini API response structure: {list(data.keys())}")
                        
                        if 'candidates' in data and len(data['candidates']) > 0:
                            candidate = data['candidates'][0]
                            
                            # Безопасная проверка структуры ответа с улучшенной обработкой ошибок
                            if 'content' in candidate:
                                content = candidate['content']
                                logger.debug(f"Gemini API content keys: {list(content.keys())}")
                                
                                if 'parts' in content and len(content['parts']) > 0:
                                    part = content['parts'][0]
                                    logger.debug(f"Gemini API part keys: {list(part.keys())}")
                                    
                                    if 'text' in part and part['text']:
                                        result = part['text']
                                        logger.info(f"Gemini API success: received {len(result)} characters")
                                        return result
                                    else:
                                        logger.error(f"Gemini API: No 'text' in parts or empty text. Part structure: {list(part.keys())}")
                                        logger.error(f"Part content: {part}")
                                else:
                                    logger.error(f"Gemini API: No 'parts' in content. Content structure: {list(content.keys())}")
                                    logger.error(f"Full content: {content}")
                                    
                                    # Попытка альтернативной структуры ответа
                                    if 'text' in content:
                                        result = content['text']
                                        logger.info(f"Gemini API alternative structure success: received {len(result)} characters")
                                        return result
                                    
                                    # Если содержимое пустое, возвращаем ошибку
                                    logger.error("Gemini API: Empty content structure detected")
                                    return "🤖 Извините, сервис временно недоступен. Попробуйте повторить запрос через несколько секунд."
                            else:
                                logger.error(f"Gemini API: No 'content' in candidate. Candidate structure: {list(candidate.keys())}")
                                logger.error(f"Full candidate: {candidate}")
                                
                                # Проверяем альтернативные структуры ответа
                                if 'text' in candidate:
                                    result = candidate['text']
                                    logger.info(f"Gemini API direct text success: received {len(result)} characters")
                                    return result
                        else:
                            logger.error(f"Gemini API: No candidates in response. Full response: {data}")
                            # Попытка найти текст в других местах ответа
                            if 'text' in data:
                                result = data['text']
                                logger.info(f"Gemini API direct response text success: received {len(result)} characters")
                                return result
                            return "🤖 Извините, сервис ИИ временно недоступен. Попробуйте повторить запрос через несколько секунд."
                    else:
                        error_text = await response.text()
                        logger.error(f"Gemini API Error: {response.status}")
                        logger.error(f"Error details: {error_text}")
                        
                        # Возвращаем понятную ошибку пользователю
                        if response.status == 429:
                            return "⏰ Слишком много запросов. Подождите немного и попробуйте снова."
                        elif response.status == 403:
                            return "🔐 Ошибка доступа к сервису ИИ. Попробуйте позже."
                        elif response.status >= 500:
                            return "🔧 Сервис ИИ временно недоступен. Попробуйте через несколько минут."
                        else:
                            return "❌ Произошла ошибка при обработке запроса. Попробуйте изменить формулировку."
        except Exception as e:
            logger.error(f"Exception calling Gemini API: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            
            # Возвращаем понятную ошибку вместо None
            import aiohttp
            if isinstance(e, aiohttp.ClientError):
                return "🌐 Ошибка сетевого соединения. Проверьте подключение к интернету."
            elif isinstance(e, asyncio.TimeoutError):
                return "⏱️ Превышено время ожидания ответа. Попробуйте повторить запрос."
            else:
                return "🤖 Произошла техническая ошибка. Попробуйте повторить запрос через несколько секунд."

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        # Начинаем печатать для UX
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # Получаем сообщение пользователя
        user_message = update.message.text
        user_id = update.message.from_user.id
        
        # Логируем сообщение пользователя (обрезаем для предотвращения спама в логах)
        logger.info(f"Received message from user {user_id}: {user_message[:50]}...")
        
        # Проверка, можно ли сделать запрос (ограничения по частоте)
        if not self.can_make_request(user_id):
            remaining_minute, remaining_day = self.get_remaining_requests(user_id)
            await self.safe_send_message(
                update,
                "⚠️ Превышен лимит запросов. Пожалуйста, подождите немного перед следующим запросом.",
                remaining_minute, remaining_day, user_id
            )
            return
            
        # Увеличиваем счетчик запросов
        self.add_request(user_id)
        
        # Отправляем "печатает" для лучшего UX
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        try:
            # Проверяем, нужны ли актуальные данные
            needs_current = self.needs_current_data(user_message)
            logger.info(f"Current data needed for query '{user_message[:50]}...': {needs_current}")
            
            # Детектируем запросы новостей (политических и других)
            is_politics = self.is_politics_query(user_message)
            has_news_keywords = 'новост' in user_message.lower() or 'news' in user_message.lower()
            
            # Для запросов про погоду, используем специальный поиск погоды
            if self.is_weather_query(user_message):
                logger.info(f"Weather query detected from user {user_id}")
                weather_result = await self.search_weather_data(user_message)
                if weather_result:
                    await self.safe_send_message(update, weather_result)
                    return
            
            # Для политических новостей или явных запросов новостей, используем поиск новостей
            if is_politics or has_news_keywords:
                logger.info(f"News query detected from user {user_id}: politics={is_politics}")
                news_result = await self.search_news(user_message)
                if news_result:
                    await self.safe_send_message(update, news_result)
                    return
            
            # Для запросов валют, используем специальный поиск
            if self.is_currency_query(user_message):
                logger.info(f"Currency query detected from user {user_id}")
                currency_result = await self.search_currency_rates(user_message)
                if currency_result:
                    await self.safe_send_message(update, currency_result)
                    return
            
            # Если нужны актуальные данные, получаем их с внешних источников
            if needs_current:
                logger.info(f"Getting current data for user {user_id}")
                result = await self.get_current_data(user_message)
                if result:
                    await self.safe_send_message(update, result)
                    return
                
            # Стандартный запрос (Gemini API)
            logger.info(f"Regular query for user {user_id} (no current data needed)")
            
            # Получение истории сообщений пользователя
            if user_id not in user_sessions:
                user_sessions[user_id] = []
            
            # Чистка старых запросов, если нужно
            self.clean_old_requests(user_id)
            
            # Добавляем новое сообщение в историю
            user_sessions[user_id].append({
                "role": "user",
                "parts": [{"text": user_message}],
                "timestamp": datetime.now()
            })
            
            # Собираем сообщения для отправки в API
            messages = []
            for message in user_sessions[user_id]:
                messages.append({
                    "role": message["role"],
                    "parts": message["parts"]
                })
                
            logger.info(f"Calling Gemini API for user {user_id} with {len(messages)} messages")
            
            # Вызываем Gemini API
            response = await self.call_gemini_api(messages)
            
            # Если ответ получен
            if response:
                logger.info(f"Received response from Gemini API for user {user_id}: {len(response)} characters")
                
                # Добавляем ответ в историю сообщений
                user_sessions[user_id].append({
                    "role": "model",
                    "parts": [{"text": response}],
                    "timestamp": datetime.now()
                })
                
                # Отправка ответа пользователю с информацией об оставшихся запросах
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                await self.safe_send_message(update, response, remaining_minute, remaining_day, user_id)
            else:
                # Обработка ошибки Gemini API
                error_message = "⚠️ Извините, возникла проблема при обработке вашего запроса. Попробуйте еще раз или измените запрос."
                await self.safe_send_message(update, error_message)
                
        except Exception as e:
            logger.error(f"Error in handle_message: {e}")
            await self.safe_send_message(update, "⚠️ Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже.")
            
    def is_politics_query(self, query: str) -> bool:
        """Проверяет, является ли запрос поиском политических новостей"""
        politics_keywords = [
            'политика', 'политические', 'политических', 'политические новости',
            'политика россии', 'политика в мире', 'новости политики', 'последние новости политики',
            'политическая ситуация', 'политические события', 'политическое', 'политических новостей',
            'политика сша', 'политика китая', 'политика европы', 'геополитика', 'внешняя политика'
        ]
        
        query_lower = query.lower()
        
        # Проверяем наличие ключевых слов
        has_politics = any(keyword in query_lower for keyword in politics_keywords)
        has_news = any(word in query_lower for word in ['новости', 'новость', 'последние', 'свежие', 'актуальные', 'сегодняшние'])
        
        # Также проверяем более сложные фразы
        complex_phrases = [
            'предоставь новости', 'покажи новости', 'расскажи о новостях', 
            'что происходит', 'что нового', 'что случилось',
            'новостей политики', 'новостей о политике', 'политических новостей'
        ]
        
        has_complex = any(phrase in query_lower for phrase in complex_phrases)
        
        return has_politics or (has_news and 'политик' in query_lower) or has_complex

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
            
            # Отправка уведомления о том, что речь распознана
            await update.message.reply_text(f"✅ Распознано: \"{transcribed_text}\"")
            
            # 🚀 ДОБАВЛЯЕМ БЫСТРЫЕ ОТВЕТЫ НА ПРОСТЫЕ ВОПРОСЫ О ВРЕМЕНИ ДЛЯ ГОЛОСОВЫХ СООБЩЕНИЙ
            simple_time_patterns = [
                r'(какой|какое)\s+(сейчас|сегодня)\s+(год|число|день|время|дата)',
                r'который\s+час',
                r'какое\s+время',
                r'какая\s+дата'
            ]
            
            import re
            is_simple_time_query = any(re.search(pattern, transcribed_text.lower()) for pattern in simple_time_patterns)
            
            if is_simple_time_query:
                logger.info(f"Simple time query detected for voice from user {user_id}")
                simple_answer = self.get_simple_datetime_info()
                
                # Добавляем запрос в счетчик
                self.add_request(user_id)
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                
                # Проверка настроек голосовых ответов пользователя для быстрого ответа
                if voice_settings[user_id]:
                    # Генерация голосового ответа для быстрого ответа
                    await update.message.reply_text("🎵 Генерирую голосовой ответ...")
                    
                    # Очистка текста от markdown символов
                    clean_response = self.clean_text_for_speech(simple_answer)
                    
                    # Синтез речи
                    voice_bytes = await self.text_to_speech(clean_response, "ru")
                    
                    if voice_bytes:
                        try:
                            await update.message.reply_voice(
                                voice=BytesIO(voice_bytes),
                                caption=f"🎤➡️🎵 Быстрый голосовой ответ\n\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
                            )
                            logger.info(f"Successfully sent quick voice response to user {user_id}")
                            return
                        except Exception as e:
                            logger.error(f"Failed to send quick voice response to user {user_id}: {e}")
                            # Fallback к текстовому ответу
                            await self.safe_send_message(update, simple_answer, remaining_minute, remaining_day, user_id)
                            return
                    else:
                        # Fallback к текстовому ответу
                        await self.safe_send_message(update, simple_answer, remaining_minute, remaining_day, user_id)
                        return
                else:
                    # Текстовый ответ если голосовые отключены
                    await self.safe_send_message(update, simple_answer, remaining_minute, remaining_day, user_id)
                    return
            
            # 🔥 ДОБАВЛЯЕМ ЛОГИКУ ПОИСКА АКТУАЛЬНОЙ ИНФОРМАЦИИ ДЛЯ ГОЛОСОВЫХ СООБЩЕНИЙ
            current_info = None
            
            # Проверяем, нужны ли актуальные данные для голосового запроса
            if self.needs_current_data(transcribed_text):
                await update.message.reply_text("🔍 Ищу актуальную информацию в интернете...")
                current_info = await self.get_current_data(transcribed_text)
                
                if current_info:
                    # Формируем расширенный запрос с актуальными данными (как в handle_message)
                    enhanced_message = f"""❗❗❗ КРИТИЧЕСКИ ВАЖНО: ИСПОЛЬЗУЙ ТОЛЬКО АКТУАЛЬНУЮ ИНФОРМАЦИЮ НИЖЕ ❗❗❗

Голосовой вопрос пользователя: {transcribed_text}

🔥 АКТУАЛЬНАЯ ИНФОРМАЦИЯ ИЗ ИНТЕРНЕТА (ОБЯЗАТЕЛЬНО К ИСПОЛЬЗОВАНИЮ):
{current_info}

📋 ИНСТРУКЦИИ ДЛЯ ОТВЕТА:
1. ОБЯЗАТЕЛЬНО используй предоставленную актуальную дату и время
2. ИГНОРИРУЙ свои устаревшие данные о дате/времени
3. Если вопрос о дате/времени - отвечай ТОЛЬКО на основе актуальной информации выше
4. Для новостей - используй найденные актуальные новости
5. Отвечай кратко и по существу на русском языке
6. НЕ упоминай что у тебя ограниченные данные - просто используй актуальную информацию

❗ ВНИМАНИЕ: Если это вопрос о текущей дате/времени, твой ответ должен быть основан ИСКЛЮЧИТЕЛЬНО на актуальной информации выше!"""
                    
                    # Подготовка сообщений для Gemini API с актуальными данными
                    messages = [{'text': enhanced_message}]
                    logger.info(f"Enhanced voice query prepared for user {user_id} with current data")
                else:
                    # Если актуальные данные не найдены, используем обычный запрос
                    messages = [{'text': transcribed_text}]
                    logger.info(f"No current data found for voice, using regular query for user {user_id}")
            else:
                # Обычный запрос без поиска актуальных данных
                messages = [{'text': transcribed_text}]
                logger.info(f"Regular voice query for user {user_id} (no current data needed)")

            # Уведомление о начале обработки
            await update.message.reply_text("💭 Думаю над ответом...")
            
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

    async def search_currency_rates(self, query: str) -> Optional[str]:
        """Специальный поиск курсов валют через финансовые API"""
        logger.info(f"Starting currency rates search for: {query[:50]}...")
        
        try:
            # Определяем валютную пару из запроса
            currency_pairs = []
            query_lower = query.lower()
            
            if any(word in query_lower for word in ['доллар', 'usd', 'dollar']):
                currency_pairs.append(('USD', 'RUB', 'доллар США'))
            if any(word in query_lower for word in ['евро', 'eur', 'euro']):
                currency_pairs.append(('EUR', 'RUB', 'евро'))
            if any(word in query_lower for word in ['биткоин', 'bitcoin', 'btc']):
                currency_pairs.append(('BTC', 'USD', 'биткоин'))
            if any(word in query_lower for word in ['юань', 'yuan', 'cny']):
                currency_pairs.append(('CNY', 'RUB', 'китайский юань'))
            
            # Если валютная пара не определена, используем USD/RUB по умолчанию
            if not currency_pairs:
                currency_pairs.append(('USD', 'RUB', 'доллар США'))
            
            currency_info = []
            
            for from_currency, to_currency, currency_name in currency_pairs:
                try:
                    # Используем бесплатный API exchangerate-api.com
                    url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=10) as response:
                            if response.status == 200:
                                data = await response.json()
                                
                                if to_currency in data.get('rates', {}):
                                    rate = data['rates'][to_currency]
                                    date = data.get('date', '')
                                    
                                    currency_info.append(
                                        f"💱 {currency_name}: {rate:.2f} {to_currency} за 1 {from_currency}"
                                    )
                                    
                                    if date:
                                        currency_info.append(f"📅 Дата обновления: {date}")
                                    
                                    logger.info(f"Currency rate found: {from_currency}/{to_currency} = {rate}")
                                else:
                                    logger.warning(f"Currency {to_currency} not found in rates")
                            else:
                                logger.error(f"Currency API error: HTTP {response.status}")
                        
                except Exception as e:
                    logger.error(f"Currency API error for {from_currency}/{to_currency}: {e}")
                    continue
            
            if currency_info:
                from datetime import datetime
                import pytz
                moscow_tz = pytz.timezone('Europe/Moscow')
                now = datetime.now(moscow_tz)
                
                result = f"💰 АКТУАЛЬНЫЕ КУРСЫ ВАЛЮТ (обновлено {now.strftime('%d.%m.%Y %H:%M')} МСК):\n\n"
                result += "\n".join(currency_info)
                
                logger.info(f"Currency search completed: {len(currency_info)} rates found")
                return result
            else:
                logger.warning("No currency rates found")
                return None
                
        except Exception as e:
            logger.error(f"Currency search error: {e}")
            return None

    def is_currency_query(self, query: str) -> bool:
        """Определяет, является ли запрос вопросом о курсе валют"""
        currency_keywords = [
            'курс', 'валют', 'доллар', 'евро', 'рубл', 'биткоин', 'юань',
            'usd', 'eur', 'rub', 'btc', 'cny', 'exchange', 'rate',
            'стоимость доллара', 'стоимость евро', 'цена биткоина',
            'обменный курс', 'валютный курс'
        ]
        
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in currency_keywords)

    async def search_duckduckgo(self, query: str) -> Optional[str]:
        """Улучшенный поиск в DuckDuckGo с обходом блокировок"""
        logger.info(f"Starting alternative web search for: {query[:50]}...")
        
        # Если это запрос о валютах, используем специальный поиск
        if self.is_currency_query(query) and not self.is_politics_query(query):
            logger.info("Currency query detected, using specialized currency search")
            return await self.search_currency_rates(query)
        
        # Если это запрос о погоде, используем специальный поиск
        if self.is_weather_query(query):
            logger.info("Weather query detected, using specialized weather search")
            weather_result = await self.search_weather_data(query)
            if weather_result:
                return weather_result
            # Если специальный поиск не дал результатов, продолжаем обычный поиск
        
        # Если это политический запрос, не ищем валютную информацию
        if self.is_politics_query(query):
            logger.info("Politics query detected, skipping currency search in DuckDuckGo")
            # Проводим только политический поиск
            query = f"политика россия {query}"
        
        try:
            # Несколько разных подходов к поиску
            search_approaches = [
                {
                    'url': 'https://html.duckduckgo.com/html/',
                    'params': {'q': query, 'kl': 'ru-ru'},
                    'headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
                        'Accept-Encoding': 'gzip, deflate',  # Убираем br (brotli) чтобы избежать ошибок
                        'DNT': '1',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1'
                    }
                },
                {
                    'url': 'https://duckduckgo.com/lite/',
                    'params': {'q': query, 'kl': 'ru-ru'},
                    'headers': {
                        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }
                }
            ]
            
            for approach in search_approaches:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            approach['url'], 
                            params=approach['params'], 
                            headers=approach['headers'], 
                            timeout=15
                        ) as response:
                            
                            logger.info(f"Alternative search response status: {response.status}")
                            
                            if response.status == 202:
                                logger.info("Alternative search: Request accepted (202), retrying...")
                                await asyncio.sleep(2)  # Увеличиваем ожидание
                                
                                # Повторная попытка с другими заголовками
                                retry_headers = approach['headers'].copy()
                                retry_headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                                
                                async with session.get(
                                    approach['url'], 
                                    params=approach['params'], 
                                    headers=retry_headers, 
                                    timeout=15
                                ) as retry_response:
                                    if retry_response.status == 200:
                                        response = retry_response
                                        logger.info("Alternative search: Retry successful")
                                    else:
                                        logger.warning(f"Alternative search: Retry failed with {retry_response.status}")
                                        continue
                            
                            if response.status == 200:
                                html_content = await response.text()
                                
                                # Улучшенное извлечение результатов
                                import re
                                
                                # Пробуем разные паттерны для извлечения результатов
                                patterns = [
                                    r'<a[^>]*class="result__a"[^>]*>([^<]+)</a>',
                                    r'<h3[^>]*><a[^>]*>([^<]+)</a></h3>',
                                    r'<a[^>]*href="[^"]*"[^>]*>([^<]+)</a>[^<]*<span[^>]*class="result__snippet"[^>]*>([^<]+)</span>',
                                    r'<div[^>]*class="links_main"[^>]*>.*?<a[^>]*>([^<]+)</a>'
                                ]
                                
                                results = []
                                for pattern in patterns:
                                    matches = re.findall(pattern, html_content)
                                    if matches:
                                        results.extend(matches[:3])
                                        break
                                
                                if results:
                                    # Фильтруем результаты
                                    filtered_results = []
                                    for result in results[:3]:
                                        if isinstance(result, tuple):
                                            result = result[0]
                                        result = result.strip()
                                        if result and len(result) > 10:
                                            filtered_results.append(result)
                                    
                                    if filtered_results:
                                        logger.info(f"Alternative search: Found {len(filtered_results)} results")
                                        return f"🔍 Результаты поиска: {'; '.join(filtered_results)}"
                                
                                logger.warning("Alternative search: No useful results found")
                                continue
                            else:
                                logger.error(f"Alternative search error: HTTP {response.status}")
                                continue
                                
                except Exception as search_error:
                    logger.error(f"Search approach failed: {search_error}")
                    continue
            
            logger.warning("All search approaches failed")
            return None
                        
        except Exception as e:
            logger.error(f"Alternative search error: {e}")
            return None

    async def search_wikipedia(self, query: str) -> Optional[str]:
        """Улучшенный поиск в Wikipedia с учетом типа запроса"""
        logger.info(f"Starting Wikipedia search for: {query[:50]}...")
        
        # Если это валютный запрос, пропускаем Wikipedia
        if self.is_currency_query(query):
            logger.info("Currency query detected, skipping Wikipedia search")
            return None
            
        try:
            # Улучшаем поисковый запрос в зависимости от типа
            search_terms = []
            
            # Для общих вопросов о времени/дате
            if any(word in query.lower() for word in ['какой день', 'какое число', 'какой год', 'время']):
                search_terms = ['календарь', 'текущая дата', 'время']
            # Для новостей
            elif any(word in query.lower() for word in ['новости', 'события', 'происходит']):
                search_terms = ['новости', 'события', 'россия сегодня']
            # Для остальных запросов используем оригинальный запрос
            else:
                search_terms = [query]
            
            # Поиск на русском языке
            wikipedia.set_lang("ru")
            logger.info("Wikipedia: Searching in Russian")
            
            for search_term in search_terms:
                search_results = wikipedia.search(search_term, results=5)
                logger.info(f"Wikipedia RU search for '{search_term}': {len(search_results)} found: {search_results}")
                
                if search_results:
                    # Фильтруем результаты, исключая персональные страницы
                    filtered_results = []
                    for result in search_results:
                        # Пропускаем персональные страницы
                        if not any(word in result.lower() for word in [', ', 'владислав', 'михаил', 'александрович', 'борисович']):
                            filtered_results.append(result)
                    
                    if filtered_results:
                        try:
                            # Возвращаем краткое описание (первые 3 предложения)
                            logger.info(f"Wikipedia: Getting summary for '{filtered_results[0]}'")
                            summary = wikipedia.summary(filtered_results[0], sentences=3)
                            logger.info(f"Wikipedia search completed: {len(summary)} characters")
                            return f"📚 Wikipedia: {summary}"
                        except wikipedia.exceptions.DisambiguationError as e:
                            # Если неоднозначность, берем первый вариант
                            if e.options:
                                logger.info(f"Wikipedia: Disambiguation error, using '{e.options[0]}'")
                                summary = wikipedia.summary(e.options[0], sentences=3)
                                logger.info(f"Wikipedia disambiguation resolved: {len(summary)} characters")
                                return f"📚 Wikipedia: {summary}"
                        except Exception as summary_error:
                            logger.error(f"Wikipedia summary error: {summary_error}")
                            continue
            
            # Если на русском ничего подходящего не найдено, пробуем английский
            logger.info("Wikipedia: No relevant Russian results, trying English")
            wikipedia.set_lang("en")
            
            for search_term in search_terms:
                search_results = wikipedia.search(search_term, results=3)
                logger.info(f"Wikipedia EN search for '{search_term}': {len(search_results)} found: {search_results}")
                
                if search_results:
                    try:
                        summary = wikipedia.summary(search_results[0], sentences=2)
                        logger.info(f"Wikipedia EN search completed: {len(summary)} characters")
                        return f"📚 Wikipedia (EN): {summary}"
                    except Exception as en_error:
                        logger.error(f"Wikipedia EN error: {en_error}")
                        continue
            
            logger.warning(f"Wikipedia: No relevant results found for any search term")
                    
        except Exception as e:
            logger.error(f"Wikipedia search error: {e}")
            
        return None
    
    def is_politics_query(self, query: str) -> bool:
        """Проверяет, является ли запрос поиском политических новостей"""
        politics_keywords = [
            'политика', 'политические', 'политических', 'политические новости',
            'политика россии', 'политика в мире', 'новости политики', 'последние новости политики',
            'политическая ситуация', 'политические события', 'политическое', 'политических новостей',
            'политика сша', 'политика китая', 'политика европы', 'геополитика', 'внешняя политика'
        ]
        
        query_lower = query.lower()
        
        # Проверяем наличие ключевых слов
        has_politics = any(keyword in query_lower for keyword in politics_keywords)
        has_news = any(word in query_lower for word in ['новости', 'новость', 'последние', 'свежие', 'актуальные', 'сегодняшние'])
        
        # Также проверяем более сложные фразы
        complex_phrases = [
            'предоставь новости', 'покажи новости', 'расскажи о новостях', 
            'что происходит', 'что нового', 'что случилось',
            'новостей политики', 'новостей о политике', 'политических новостей'
        ]
        
        has_complex = any(phrase in query_lower for phrase in complex_phrases)
        
        return has_politics or (has_news and 'политик' in query_lower) or has_complex

    def extract_numbers_from_query(self, query: str) -> List[int]:
        """Извлекает числа из запроса для определения количества результатов"""
        import re
        numbers = re.findall(r'\b(\d+)\b', query)
        return [int(num) for num in numbers if int(num) > 0]

    async def search_news(self, query: str) -> Optional[str]:
        """Поиск новостей с улучшенной обработкой политических запросов"""
        logger.info(f"Starting news search for: {query[:50]}...")
        
        # Проверяем, является ли запрос политическим
        is_politics = self.is_politics_query(query)
        query_lower = query.lower()
        
        # Извлекаем необходимое количество новостей из запроса
        max_news = 10  # По умолчанию
        numbers = self.extract_numbers_from_query(query)
        if numbers:
            # Если пользователь указал количество новостей, используем его значение (с ограничением)
            max_news = min(max(numbers), 50)  # Максимум 50 новостей
            logger.info(f"User requested {max_news} news articles")
        
        # Формируем поисковый запрос в зависимости от типа
        search_query = "последние новости россия сегодня"
        if is_politics:
            search_query = "последние политические новости россия сегодня"
        elif "украин" in query_lower:
            search_query = "последние новости украина сегодня"
        elif "экономик" in query_lower or "финанс" in query_lower or "бизнес" in query_lower:
            search_query = "последние экономические новости сегодня россия"
        else:
            # Если это общий новостной запрос, извлекаем ключевые слова
            words = query_lower.split()
            content_words = [w for w in words if len(w) > 3 and w not in ["новости", "последние", "свежие", "актуальные", "сегодня", "предоставь", "покажи", "расскажи"]]
            if content_words:
                search_query = f"последние новости {' '.join(content_words[:3])} сегодня"
        
        logger.info(f"Generated search query: {search_query}")
        
        # Инициализируем NewsAPI клиент, если есть ключ
        newsapi = None
        if NEWS_API_KEY:
            try:
                newsapi = NewsApiClient(api_key=NEWS_API_KEY)
                logger.info("NewsAPI initialized: Yes")
            except Exception as e:
                logger.error(f"Error initializing NewsAPI: {e}")
        else:
            logger.warning("NewsAPI not initialized: No API key")

        try:
            # Поиск через NewsAPI
            news_results = []
            if newsapi:
                try:
                    # Пробуем получить главные новости
                    top_headlines = newsapi.get_top_headlines(
                        q=search_query,
                        language='ru',
                        country='ru',
                        page_size=min(max_news, 20)  # NewsAPI ограничивает до 20
                    )
                    
                    # Если недостаточно результатов, дополняем поиском
                    if top_headlines.get('totalResults', 0) < max_news:
                        everything = newsapi.get_everything(
                            q=search_query,
                            language='ru',
                            sort_by='publishedAt',
                            page_size=max_news
                        )
                        
                        # Объединяем результаты
                        all_articles = top_headlines.get('articles', []) + everything.get('articles', [])
                        # Убираем дубликаты
                        seen_titles = set()
                        unique_articles = []
                        for article in all_articles:
                            if article.get('title') and article['title'] not in seen_titles:
                                seen_titles.add(article['title'])
                                unique_articles.append(article)
                                
                        news_results = unique_articles[:max_news]  # Ограничиваем нужным количеством
                    else:
                        news_results = top_headlines.get('articles', [])[:max_news]
                        
                    logger.info(f"NewsAPI found {len(news_results)} articles")
                    
                    # Если NewsAPI не вернул результатов, пробуем еще раз с более широким запросом
                    if not news_results and is_politics:
                        try:
                            wider_query = "новости политика международные"
                            logger.info(f"Retrying NewsAPI with wider query: {wider_query}")
                            everything = newsapi.get_everything(
                                q=wider_query,
                                language='ru',
                                sort_by='publishedAt',
                                page_size=max_news
                            )
                            news_results = everything.get('articles', [])[:max_news]
                            logger.info(f"NewsAPI wider search found {len(news_results)} articles")
                        except Exception as wider_error:
                            logger.error(f"NewsAPI wider search error: {wider_error}")
                except Exception as e:
                    logger.error(f"NewsAPI error: {e}")
                    # Сбрасываем NewsAPI при ошибке, чтобы попробовать резервный метод
                    newsapi = None
            
            # Если нет результатов из NewsAPI, используем DuckDuckGo
            if not news_results:
                logger.info("Using DuckDuckGo as fallback for news")
                
                # Поиск через DuckDuckGo
                try:
                    async with aiohttp.ClientSession() as session:
                        # Создаем URL-safe строку запроса
                        from urllib.parse import quote
                        safe_search_terms = quote(search_query)
                        
                        response = await session.get(
                            f"https://html.duckduckgo.com/html/?q={safe_search_terms}", 
                            headers={
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                                'Accept': 'text/html,application/xhtml+xml',
                                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
                            }
                        )
                        if response.status == 200:
                            html = await response.text()
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            # Извлекаем результаты поиска
                            results = soup.find_all('div', {'class': 'result__body'})
                            
                            for result in results[:max_news]:  # Ограничиваем количеством
                                title_elem = result.find('a', {'class': 'result__a'})
                                snippet_elem = result.find('a', {'class': 'result__snippet'})
                                
                                if title_elem and snippet_elem:
                                    title_text = title_elem.get_text().strip()
                                    url = title_elem.get('href')
                                    description = snippet_elem.get_text().strip()
                                    
                                    # Получаем домен как источник
                                    source_name = "Новостной ресурс"
                                    try:
                                        from urllib.parse import urlparse
                                        domain = urlparse(url).netloc
                                        if domain:
                                            source_name = domain.replace('www.', '')
                                    except:
                                        pass
                                    
                                    # Формируем временную структуру новости
                                    if len(title_text) > 10 and len(description) > 15:  # Минимальная валидация
                                        news_results.append({
                                            'title': title_text,
                                            'url': url,
                                            'description': description,
                                            'source': {'name': source_name}
                                        })
                except Exception as e:
                    logger.error(f"DuckDuckGo search error: {e}")
            
            # Формируем результат
            if news_results:
                from datetime import datetime
                import pytz
                
                moscow_tz = pytz.timezone('Europe/Moscow')
                now = datetime.now(moscow_tz)
                
                # Заголовок с указанием типа новостей и времени
                if is_politics:
                    result = f"🗞️ ПОЛИТИЧЕСКИЕ НОВОСТИ (обновлено {now.strftime('%d.%m.%Y %H:%M')} МСК):\n\n"
                else:
                    result = f"📰 НОВОСТИ ПО ЗАПРОСУ (обновлено {now.strftime('%d.%m.%Y %H:%M')} МСК):\n\n"
                
                # Формируем список новостей
                news_items = []
                for i, article in enumerate(news_results, 1):
                    title = article.get('title', '').replace('\n', ' ').strip()
                    # Удаляем ' - Источник' из заголовка
                    if ' - ' in title:
                        title = ' - '.join(title.split(' - ')[:-1])
                    
                    source = article.get('source', {}).get('name', 'Неизвестный источник')
                    url = article.get('url', '')
                    description = article.get('description', '').replace('\n', ' ').strip()
                    
                    # Усечение слишком длинных описаний
                    if description and len(description) > 200:
                        description = description[:197] + "..."
                    
                    # Формируем текст новости
                    news_text = f"{i}. *{title}*\n" + \
                            f"_{source}_\n" + \
                            f"{description}\n" + \
                            f"[Подробнее]({url})"
                    
                    news_items.append(news_text)
                
                # Объединяем новости в один текст
                result += "\n\n".join(news_items)
                
                # Добавляем информацию о возможности запросить больше новостей
                result += f"\n\n_Вы можете запросить до 50 новостей, указав количество в запросе, например: «{max_news} политических новостей»._"
                
                logger.info(f"News search completed: {len(news_items)} articles found")
                return result
            else:
                logger.warning("No news found")
                return "⚠️ К сожалению, не удалось найти новости по вашему запросу. Попробуйте изменить запрос или повторить позже."
        except Exception as e:
            logger.error(f"News search error: {e}")
            return "⚠️ Произошла ошибка при поиске новостей. Попробуйте повторить запрос позже."

    def needs_current_data(self, query: str) -> bool:
        """Определяет, нужны ли актуальные данные для ответа"""
        current_keywords = [
            # Временные индикаторы
            'сегодня', 'вчера', 'сейчас', 'текущий', 'актуальн', 'последн',
            'новости', 'события', 'происходит', 'случилось', 'недавно',
            
            # Вопросы о времени и дате
            'какой год', 'какой месяц', 'какое число', 'какой день', 'какое сегодня', 
            'какой сейчас', 'какая дата', 'какое время', 'который час',
            'время', 'дата', 'число', 'день', 'месяц', 'год',
            
            # Валютные и финансовые данные (расширенный список)
            'курс', 'цена', 'стоимость', 'валют', 'рубл', 'доллар', 'евро', 'юань',
            'биткоин', 'криптовалют', 'котировки', 'обменный курс', 'валютный курс',
            'курс доллара', 'курс евро', 'курс рубля', 'usd', 'eur', 'rub', 'btc', 'cny',
            'стоимость доллара', 'стоимость евро', 'цена биткоина',
            'финансы', 'экономика', 'рынок', 'торги', 'биржа',
            
            # Погода и изменяющиеся данные (расширенный список)
            'погода', 'температура', 'прогноз', 'климат', 'дождь', 'снег', 
            'солнце', 'облачно', 'ветер', 'влажность', 'давление',
            'тепло', 'холодно', 'жарко', 'морозно', 'градус',
            
            # Свежая информация
            '2024', '2025', 'этот год', 'этот месяц', 'на данный момент',
            'что нового', 'обновления', 'изменения',
            
            # Английские аналоги
            'today', 'now', 'current', 'latest', 'recent', 'news', 'update',
            'what date', 'what time', 'what day', 'what month', 'what year',
            'exchange rate', 'currency', 'dollar', 'euro', 'ruble', 'bitcoin',
            'weather', 'temperature', 'forecast', 'rain', 'snow', 'sunny'
        ]
        
        query_lower = query.lower()
        result = any(keyword in query_lower for keyword in current_keywords)
        
        # Дополнительная проверка для вопросов о времени/дате
        time_patterns = [
            r'какой\s+(сейчас|теперь|нынче)',
            r'какое\s+(сегодня|сейчас|число)',
            r'что\s+за\s+(день|дата|время)',
            r'сколько\s+(сейчас|времени)',
            r'который\s+час',
            r'\b(дата|время|год|месяц|число|день)\b',
            r'(сегодня|вчера|сейчас)',
            r'(новости|последние)'
        ]
        
        # Дополнительная проверка для валютных запросов
        currency_patterns = [
            r'курс\s+(доллара|евро|рубля|юаня|биткоина)',
            r'(доллар|евро|рубль)\s+к\s+(рублю|доллару)',
            r'стоимость\s+(доллара|евро|биткоина)',
            r'цена\s+(биткоина|доллара|евро)',
            r'обменный\s+курс',
            r'валютный\s+курс'
        ]
        
        import re
        for pattern in time_patterns + currency_patterns:
            if re.search(pattern, query_lower):
                result = True
                logger.info(f"Date/time/currency pattern matched: {pattern}")
                break
        
        # Специальная проверка для валютных запросов
        if self.is_currency_query(query):
            result = True
            logger.info("Currency query detected, current data needed")
        
        # Специальная проверка для погодных запросов
        if self.is_weather_query(query):
            result = True
            logger.info("Weather query detected, current data needed")
        
        logger.info(f"Current data needed for query '{query[:50]}...': {result}")
        return result
    
    def get_simple_datetime_info(self) -> str:
        """Простое получение актуальной даты и времени"""
        from datetime import datetime
        import pytz
        
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        
        month_names = {
            1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля', 5: 'мая', 6: 'июня',
            7: 'июля', 8: 'августа', 9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
        }
        
        weekday_names = {
            0: 'понедельник', 1: 'вторник', 2: 'среда', 3: 'четверг', 
            4: 'пятница', 5: 'суббота', 6: 'воскресенье'
        }
        
        return f"Сегодня {now.day} {month_names[now.month]} {now.year} года, {weekday_names[now.weekday()]}, время {now.strftime('%H:%M')}"
    
    async def get_current_data(self, query: str) -> str:
        """Получает актуальные данные из различных источников"""
        results = []
        
        logger.info(f"Starting current data search for: {query[:50]}...")
        
        # Добавляем текущую дату и время для всех запросов
        from datetime import datetime
        import pytz
        
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        
        # Создаем детальную информацию о текущем времени
        month_names = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель', 5: 'Май', 6: 'Июнь',
            7: 'Июль', 8: 'Август', 9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
        
        weekday_names = {
            0: 'Понедельник', 1: 'Вторник', 2: 'Среда', 3: 'Четверг', 
            4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'
        }
        
        current_time_info = f"""🕐 АКТУАЛЬНАЯ ДАТА И ВРЕМЯ (ОБЯЗАТЕЛЬНО ИСПОЛЬЗУЙ ЭТУ ИНФОРМАЦИЮ):

⚠️ ВНИМАНИЕ: Сейчас {now.day} {month_names[now.month]} {now.year} года, {weekday_names[now.weekday()]}
⚠️ ТОЧНОЕ ВРЕМЯ: {now.strftime('%H:%M:%S')} по московскому времени

ДЕТАЛЬНАЯ ИНФОРМАЦИЯ:
• Полная дата: {now.strftime('%d.%m.%Y')}
• День недели: {weekday_names[now.weekday()]}
• Число: {now.day}
• Месяц: {month_names[now.month]} ({now.month})
• Год: {now.year}
• Время: {now.strftime('%H:%M:%S')} МСК
• Часовой пояс: Europe/Moscow (UTC+3)

❗ ЭТА ИНФОРМАЦИЯ АКТУАЛЬНА НА МОМЕНТ ЗАПРОСА! ИСПОЛЬЗУЙ ИМЕННО ЭТИ ДАННЫЕ О ДАТЕ И ВРЕМЕНИ!"""
        
        results.append(current_time_info)
        
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

    def is_weather_query(self, query: str) -> bool:
        """Определяет, является ли запрос о погоде"""
        weather_keywords = [
            'погода', 'температура', 'прогноз', 'климат', 'дождь', 'снег', 
            'солнце', 'облачно', 'ветер', 'влажность', 'давление',
            'weather', 'temperature', 'forecast', 'rain', 'snow', 'sunny',
            'градус', 'тепло', 'холодно', 'жарко', 'морозно'
        ]
        
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in weather_keywords)

    async def search_weather_data(self, query: str) -> Optional[str]:
        """Специальный поиск погодных данных через API погоды"""
        logger.info(f"Starting weather search for: {query[:50]}...")
        
        # Импортируем необходимые модули
        import aiohttp
        from bs4 import BeautifulSoup
        
        try:
            # Извлекаем города из запроса
            cities = []
            query_lower = query.lower()
            
            # Популярные города для погодного поиска
            city_patterns = {
                'москва': ['москва', 'moscow'],
                'анталия': ['анталия', 'анталья', 'antalya'],
                'стамбул': ['стамбул', 'istanbul'],
                'сочи': ['сочи', 'sochi'],
                'санкт-петербург': ['петербург', 'спб', 'питер', 'saint petersburg'],
                'екатеринбург': ['екатеринбург', 'yekaterinburg'],
                'новосибирск': ['новосибирск', 'novosibirsk'],
                'казань': ['казань', 'kazan'],
                'нижний новгород': ['нижний новгород', 'nizhny novgorod'],
                'красноярск': ['красноярск', 'krasnoyarsk']
            }
            
            for city, patterns in city_patterns.items():
                if any(pattern in query_lower for pattern in patterns):
                    cities.append(city)
            
            # Если города не найдены, используем общий поиск
            if not cities:
                cities = ['москва']
            
            weather_info = []
            
            for city in cities[:3]:  # Максимум 3 города
                # Используем несколько методов и собираем все успешные результаты
                city_weather_found = False
                
                # Метод 1: OpenWeather Map API
                if not city_weather_found:
                    try:
                        city_map = {
                            'москва': 'Moscow',
                            'анталия': 'Antalya',
                            'стамбул': 'Istanbul',
                            'сочи': 'Sochi',
                            'санкт-петербург': 'Saint Petersburg',
                            'екатеринбург': 'Yekaterinburg',
                            'новосибирск': 'Novosibirsk',
                            'казань': 'Kazan',
                            'нижний новгород': 'Nizhny Novgorod',
                            'красноярск': 'Krasnoyarsk'
                        }
                        
                        city_en = city_map.get(city.lower(), city)
                        
                        async with aiohttp.ClientSession() as session:
                            url = f"https://api.openweathermap.org/data/2.5/weather?q={city_en}&units=metric&lang=ru&appid=12464dd6965b11c90563e796495fc334"
                            async with session.get(url, timeout=10) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    
                                    # Перевод направления ветра
                                    wind_dir = ""
                                    deg = data.get('wind', {}).get('deg', 0)
                                    if deg > 337.5 or deg <= 22.5: wind_dir = "северный"
                                    elif deg <= 67.5: wind_dir = "северо-восточный"
                                    elif deg <= 112.5: wind_dir = "восточный"
                                    elif deg <= 157.5: wind_dir = "юго-восточный"
                                    elif deg <= 202.5: wind_dir = "южный"
                                    elif deg <= 247.5: wind_dir = "юго-западный"
                                    elif deg <= 292.5: wind_dir = "западный"
                                    else: wind_dir = "северо-западный"
                                    
                                    temp = data.get('main', {}).get('temp', 'н/д')
                                    feels_like = data.get('main', {}).get('feels_like', 'н/д')
                                    description = data.get('weather', [{}])[0].get('description', 'н/д')
                                    humidity = data.get('main', {}).get('humidity', 'н/д')
                                    wind_speed = data.get('wind', {}).get('speed', 'н/д')
                                    pressure = data.get('main', {}).get('pressure', 'н/д')
                                    
                                    text = f"Температура: {temp}°C, " + \
                                        f"ощущается как {feels_like}°C. " + \
                                        f"{description.capitalize()}. " + \
                                        f"Влажность: {humidity}%, " + \
                                        f"давление: {int(pressure * 0.75)} мм рт.ст., " + \
                                        f"ветер {wind_speed} м/с ({wind_dir})."
                                        
                                    weather_info.append(f"🌤️ {city.title()}: {text}")
                                    city_weather_found = True
                                    logger.info(f"OpenWeatherMap data found for {city}")
                                    continue
                    except Exception as e:
                        logger.error(f"OpenWeather API error for {city}: {e}")
                
                # Метод 2: RealTimeWeb API
                if not city_weather_found:
                    try:
                        city_encoded = city.replace(' ', '+')
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                            'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
                            'Connection': 'keep-alive'
                        }
                        
                        # Попытка через realtime погоду
                        async with aiohttp.ClientSession() as session:
                            url = f"https://api.realtimeweb.ru/api/getweather?city={city_encoded}"
                            async with session.get(url, headers=headers, timeout=10) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    if data.get('success'):
                                        w = data.get('data', {})
                                        text = f"Температура: {w.get('temperature', 'н/д')}°C, " + \
                                            f"ощущается как {w.get('feels_like', 'н/д')}°C. " + \
                                            f"{w.get('description', 'н/д')}. " + \
                                            f"Влажность: {w.get('humidity', 'н/д')}%, " + \
                                            f"ветер {w.get('wind_speed', 'н/д')} м/с ({w.get('wind_direction', 'н/д')})."
                                        weather_info.append(f"🌤️ {city.title()}: {text}")
                                        city_weather_found = True
                                        logger.info(f"RealTimeWeb data found for {city}")
                                        continue
                    except Exception as e:
                        logger.error(f"RealTimeWeb API error for {city}: {e}")
                
                # Метод 3: DuckDuckGo парсинг (резервный)
                if not city_weather_found:
                    try:
                        from urllib.parse import quote
                        search_query = quote(f"погода {city} сегодня прогноз")
                        
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                f"https://html.duckduckgo.com/html/?q={search_query}", 
                                headers={
                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                                    'Accept': 'text/html,application/xhtml+xml',
                                    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
                                },
                                timeout=15
                            ) as response:
                                if response.status == 200:
                                    html = await response.text()
                                    soup = BeautifulSoup(html, 'html.parser')
                                    
                                    # Извлекаем результаты поиска
                                    results = soup.find_all('div', {'class': 'result__body'})
                                    
                                    # Получаем фрагменты текста с погодной информацией
                                    weather_snippets = []
                                    for result in results[:3]:
                                        snippet = result.find('a', {'class': 'result__snippet'})
                                        if snippet:
                                            snippet_text = snippet.text.strip()
                                            # Проверяем, что текст похож на погодную информацию
                                            if any(kw in snippet_text.lower() for kw in ['°c', 'градус', 'температур', 'погода']):
                                                weather_snippets.append(snippet_text)
                                    
                                    if weather_snippets:
                                        # Берем самый длинный и информативный фрагмент
                                        best_snippet = max(weather_snippets, key=len)
                                        weather_info.append(f"🌤️ {city.title()}: {best_snippet}")
                                        city_weather_found = True
                                        logger.info(f"DuckDuckGo data found for {city}")
                                        continue
                                        
                                    # Если специфичную информацию не нашли, используем общую информацию
                                    if not city_weather_found:
                                        for result in results[:2]:
                                            title = result.find('a', {'class': 'result__a'})
                                            snippet = result.find('a', {'class': 'result__snippet'})
                                            
                                            if title and snippet and "погода" in snippet.text.lower():
                                                url = title.get('href', '')
                                                source = "метеоданных"
                                                try:
                                                    from urllib.parse import urlparse
                                                    domain = urlparse(url).netloc
                                                    if domain:
                                                        source = domain.replace('www.', '')
                                                except:
                                                    pass
                                                
                                                weather_info.append(f"🌤️ {city.title()}: Прогноз погоды доступен на сайте {source}: {url}")
                                                city_weather_found = True
                                                logger.info(f"DuckDuckGo URL found for {city}")
                                                break
                    except Exception as e:
                        logger.error(f"DuckDuckGo weather search error for {city}: {e}")
                
                # Если ни один метод не сработал, добавляем сообщение об ошибке
                if not city_weather_found:
                    weather_info.append(f"🌤️ {city.title()}: Не удалось получить прогноз погоды.")
                    logger.warning(f"No weather data found for {city}")
            
            if weather_info:
                from datetime import datetime
                import pytz
                moscow_tz = pytz.timezone('Europe/Moscow')
                now = datetime.now(moscow_tz)
                
                result = f"🌤️ ПОГОДНАЯ ИНФОРМАЦИЯ (обновлено {now.strftime('%d.%m.%Y %H:%M')} МСК):\n\n"
                result += "\n\n".join(weather_info)
                
                logger.info(f"Weather search completed: {len(weather_info)} forecasts found")
                return result
            else:
                logger.warning("No weather data found, returning generic message")
                return "⚠️ К сожалению, не удалось получить актуальный прогноз погоды. Попробуйте уточнить название города или повторить запрос позже."
                
        except Exception as e:
            logger.error(f"Weather search error: {e}")
            return "⚠️ Произошла ошибка при получении прогноза погоды. Попробуйте повторить запрос позже."

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





async def main():
    """Основная функция"""
    logger.info("Starting Gemini Telegram Bot...")
    
    if not TELEGRAM_TOKEN or not AI_API_KEY:
        logger.error("Missing required environment variables: TELEGRAM_TOKEN or AI_API_KEY")
        return
    
    # Проверка доступности NewsAPI ключа
    if NEWS_API_KEY:
        logger.info("NewsAPI key is available")
    else:
        logger.warning("NewsAPI key is missing - news searches will use fallback methods only")
    
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
    
    # Определяем, в каком окружении мы находимся - Render или локальное
    is_production = os.environ.get('RENDER') is not None
    
    # Инициализация приложения
    await application.initialize()
    
    # Очистка webhook
    try:
        logger.info("Clearing webhook...")
        await application.bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Error clearing webhook: {e}")
    
    # Запуск в зависимости от окружения
    if is_production:
        # В продакшене используем webhook
        webhook_url = "https://google-gemini-bot.onrender.com/webhook"
        logger.info(f"Production environment detected, setting webhook to {webhook_url}")
        
        try:
            # Устанавливаем webhook
            await application.bot.set_webhook(url=webhook_url)
            
            # Запускаем приложение
            await application.start()
            
            # Запускаем webhook сервер
            await application.updater.start_webhook(
                listen="0.0.0.0",
                port=int(os.environ.get("PORT", 10000)),
                url_path="webhook",
                webhook_url=webhook_url
            )
            logger.info("Webhook started successfully")
            
        except Exception as e:
            logger.error(f"Error setting up webhook: {e}")
            logger.error("Falling back to polling")
            await start_polling_mode(application)
    else:
        # В локальной среде используем поллинг
        logger.info("Local environment detected, using polling")
        await start_polling_mode(application)
    
    # Запуск дополнительных сервисов
    logger.info("🚀 Starting additional services: HTTP server + Watchdog...")
    await asyncio.gather(
        start_server(),
        watchdog(),
        return_exceptions=True
    )

async def start_polling_mode(application):
    """Запуск в режиме поллинга"""
    try:
        logger.info("Starting polling mode...")
        
        # Убедимся, что webhook отключен
        await application.bot.delete_webhook(drop_pending_updates=True)
        
        # Запускаем приложение
        await application.start()
        
        # Запускаем поллинг
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("Polling started successfully")
        
    except Exception as e:
        logger.error(f"Error starting polling: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main()) 