import os
import logging
import asyncio
import base64
import re
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Dict, List, Optional
import aiohttp
from aiohttp import web
from newsapi import NewsApiClient
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
PORT = int(os.getenv('PORT', 10000))

# Лимиты запросов
MINUTE_LIMIT = 10
DAILY_LIMIT = 250

# Хранилище данных
user_sessions: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
request_counts: Dict[int, Dict[str, List[datetime]]] = defaultdict(lambda: {'minute': [], 'day': []})

# Глобальная переменная для приложения
telegram_app = None

class GeminiBot:
    def __init__(self):
        self.news_client = NewsApiClient(api_key=NEWS_API_KEY) if NEWS_API_KEY else None
        logger.info(f"NewsAPI initialized: {'Yes' if self.news_client else 'No'}")
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        welcome_message = """🤖 Добро пожаловать в Gemini Bot!

Я могу помочь вам с:
• 💬 Ответами на текстовые вопросы
• 🖼️ Анализом изображений
• 🌐 Поиском актуальной информации

Команды:
/start - Показать это сообщение
/help - Справка
/clear - Очистить историю чата
/limits - Показать лимиты запросов

Просто отправьте мне текст или изображение!"""
        
        await update.message.reply_text(welcome_message)
        
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        help_message = """📋 Справка по командам:

/start - Приветствие
/help - Показать эту справку
/clear - Очистить историю переписки
/limits - Показать лимиты запросов

🔄 Как пользоваться:
• Отправьте текстовое сообщение для получения ответа
• Отправьте изображение для анализа
• Бот автоматически ищет актуальную информацию при необходимости

⚡ Лимиты: 10 запросов в минуту, 250 в день"""
        
        await update.message.reply_text(help_message)
        
    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /clear"""
        user_id = update.effective_user.id
        user_sessions[user_id].clear()
        await update.message.reply_text("🗑️ История чата очищена!")
        
    async def limits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /limits"""
        user_id = update.effective_user.id
        remaining_minute, remaining_day = self.get_remaining_requests(user_id)
        
        limits_message = f"""📊 Ваши лимиты:

🕐 В этой минуте: {remaining_minute}/{MINUTE_LIMIT}
📅 Сегодня: {remaining_day}/{DAILY_LIMIT}"""
        
        await update.message.reply_text(limits_message)

    def clean_old_requests(self, user_id: int):
        """Очистка старых запросов"""
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        day_ago = now - timedelta(days=1)
        
        # Очистка запросов старше минуты
        request_counts[user_id]['minute'] = [
            req_time for req_time in request_counts[user_id]['minute'] 
            if req_time > minute_ago
        ]
        
        # Очистка запросов старше дня
        request_counts[user_id]['day'] = [
            req_time for req_time in request_counts[user_id]['day'] 
            if req_time > day_ago
        ]

    def get_remaining_requests(self, user_id: int) -> tuple:
        """Получение оставшихся запросов"""
        self.clean_old_requests(user_id)
        
        minute_requests = len(request_counts[user_id]['minute'])
        day_requests = len(request_counts[user_id]['day'])
        
        remaining_minute = max(0, MINUTE_LIMIT - minute_requests)
        remaining_day = max(0, DAILY_LIMIT - day_requests)
        
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
        """Вызов Gemini API"""
        try:
            # Добавляем информацию о текущей дате
            current_date = datetime.now().strftime("%d.%m.%Y")
            system_message = f"ВАЖНО: Сегодня {current_date} год. При расчете возраста людей используй эту дату."
            
            headers = {
                'Content-Type': 'application/json',
            }
            
            # Создаем список сообщений с системным сообщением
            all_messages = [{"role": "system", "content": system_message}] + messages
            
            data = {
                "contents": [
                    {
                        "parts": [
                            {"text": msg["content"]} for msg in all_messages
                        ]
                    }
                ]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{GEMINI_API_URL}?key={AI_API_KEY}",
                    headers=headers,
                    json=data,
                    timeout=30
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if 'candidates' in result and len(result['candidates']) > 0:
                            return result['candidates'][0]['content']['parts'][0]['text']
                    else:
                        logger.error(f"Gemini API error: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error calling Gemini API: {e}")
            return None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        user_message = update.message.text
        user_id = update.message.from_user.id
        
        logger.info(f"Message from user {user_id}: {user_message[:50]}...")
        
        # Проверка лимитов
        if not self.can_make_request(user_id):
            remaining_minute, remaining_day = self.get_remaining_requests(user_id)
            await update.message.reply_text(
                f"⚠️ Превышен лимит запросов.\n"
                f"🕐 Осталось в минуте: {remaining_minute}\n"
                f"📅 Осталось сегодня: {remaining_day}"
            )
            return
            
        self.add_request(user_id)
        
        try:
            # Проверяем, нужны ли актуальные данные
            if self.needs_current_data(user_message):
                response = await self.get_current_data(user_message)
            else:
                # Обычный запрос к Gemini
                user_sessions[user_id].append({"role": "user", "content": user_message})
                messages = list(user_sessions[user_id])
                response = await self.call_gemini_api(messages)
                
                if response:
                    user_sessions[user_id].append({"role": "assistant", "content": response})
                else:
                    response = "Извините, произошла ошибка при обработке вашего запроса."
            
            # Отправляем ответ
            await self.safe_send_message(update, response)
            
        except Exception as e:
            logger.error(f"Error in handle_message: {e}")
            await update.message.reply_text("Произошла ошибка при обработке сообщения.")

    def needs_current_data(self, query: str) -> bool:
        """Проверка, нужны ли актуальные данные"""
        keywords = [
            'новости', 'сегодня', 'сейчас', 'актуальн', 'свеж', 'последн',
            'курс', 'цена', 'стоимость', 'погода', 'текущ', 'политическ',
            'сколько лет', 'возраст', 'лет', 'годы', 'года'
        ]
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in keywords)

    async def get_current_data(self, query: str) -> str:
        """Получение актуальных данных"""
        try:
            # Определяем тип запроса
            if any(word in query.lower() for word in ['новости', 'новость', 'политическ']):
                return await self.search_news(query)
            elif any(word in query.lower() for word in ['курс', 'цена', 'стоимость']):
                return await self.search_currency_rates(query)
            elif any(word in query.lower() for word in ['погода']):
                return await self.search_weather_data(query)
            elif any(word in query.lower() for word in ['сколько лет', 'возраст', 'лет']):
                return await self.handle_age_query(query)
            else:
                # Общий поиск
                return await self.search_duckduckgo(query)
                
        except Exception as e:
            logger.error(f"Error getting current data: {e}")
            return "Не удалось получить актуальную информацию."

    async def search_news(self, query: str) -> Optional[str]:
        """Поиск новостей"""
        try:
            if self.news_client:
                # Определяем количество новостей из запроса
                numbers = re.findall(r'\d+', query)
                count = int(numbers[0]) if numbers else 10
                count = min(count, 50)  # Максимум 50 новостей
                
                articles = self.news_client.get_everything(
                    q='россия OR политика OR путин OR правительство',
                    language='ru',
                    sort_by='publishedAt',
                    page_size=count
                )
                
                if articles['articles']:
                    news_list = []
                    for i, article in enumerate(articles['articles'][:count], 1):
                        title = article['title']
                        description = article.get('description', '')
                        url = article['url']
                        
                        news_item = f"{i}. {title}"
                        if description:
                            news_item += f"\n{description[:100]}..."
                        news_item += f"\n🔗 {url}\n"
                        
                        news_list.append(news_item)
                    
                    return f"📰 ПОСЛЕДНИЕ НОВОСТИ ({count} шт.):\n\n" + "\n".join(news_list)
            
            # Fallback к поиску в интернете
            return await self.search_duckduckgo(query)
            
        except Exception as e:
            logger.error(f"News search error: {e}")
            return "Не удалось найти новости."

    async def search_duckduckgo(self, query: str) -> Optional[str]:
        """Поиск в DuckDuckGo"""
        try:
            from urllib.parse import quote
            search_query = quote(query)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://html.duckduckgo.com/html/?q={search_query}",
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
                    timeout=15
                ) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        results = []
                        for result in soup.find_all('div', {'class': 'result__body'})[:5]:
                            title_elem = result.find('a', {'class': 'result__a'})
                            snippet_elem = result.find('a', {'class': 'result__snippet'})
                            
                            if title_elem and snippet_elem:
                                title = title_elem.get_text().strip()
                                snippet = snippet_elem.get_text().strip()
                                url = title_elem.get('href', '')
                                
                                results.append(f"• {title}\n{snippet}\n🔗 {url}\n")
                        
                        if results:
                            return f"🔍 РЕЗУЛЬТАТЫ ПОИСКА:\n\n" + "\n".join(results)
                        
            return "Не удалось найти информацию."
            
        except Exception as e:
            logger.error(f"DuckDuckGo search error: {e}")
            return "Ошибка поиска."

    async def search_currency_rates(self, query: str) -> Optional[str]:
        """Поиск курсов валют"""
        try:
            # Простой поиск курса доллара
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.cbr-xml-daily.ru/daily_json.js",
                    timeout=10
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        usd = data['Valute']['USD']['Value']
                        eur = data['Valute']['EUR']['Value']
                        
                        return f"💰 КУРСЫ ВАЛЮТ (ЦБ РФ):\n\n💵 USD: {usd:.2f} ₽\n💶 EUR: {eur:.2f} ₽"
                        
        except Exception as e:
            logger.error(f"Currency search error: {e}")
            
        return "Не удалось получить курсы валют."

    async def search_weather_data(self, query: str) -> Optional[str]:
        """Поиск погоды"""
        return await self.search_duckduckgo(f"погода {query}")

    async def handle_age_query(self, query: str) -> Optional[str]:
        """Обработка запросов о возрасте с актуальной датой"""
        try:
            current_date = datetime.now().strftime("%d.%m.%Y")
            current_year = datetime.now().year
            
            # Создаем промпт с актуальной датой
            age_prompt = f"""ВАЖНАЯ ИНФОРМАЦИЯ: Сегодня {current_date} ({current_year} год).
            
Пользователь спрашивает: {query}

При расчете возраста используй ТОЛЬКО текущий {current_year} год. 
Например, если человек родился в 1971 году, то в {current_year} году ему {current_year - 1971} лет.

Отвечай точно и кратко, указывая текущий возраст на {current_year} год."""

            # Отправляем в Gemini с актуальной датой
            messages = [{"role": "user", "content": age_prompt}]
            response = await self.call_gemini_api(messages)
            
            if response:
                return response
            else:
                return "Не удалось рассчитать возраст."
                
        except Exception as e:
            logger.error(f"Age query error: {e}")
            return "Ошибка при обработке запроса о возрасте."

    async def safe_send_message(self, update: Update, response: str):
        """Безопасная отправка сообщения"""
        try:
            if len(response) > 4096:
                # Разбиваем длинное сообщение
                for i in range(0, len(response), 4096):
                    await update.message.reply_text(response[i:i+4096])
            else:
                await update.message.reply_text(response)
                
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await update.message.reply_text("Ошибка при отправке сообщения.")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка изображений"""
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        user_id = update.message.from_user.id
        
        if not self.can_make_request(user_id):
            remaining_minute, remaining_day = self.get_remaining_requests(user_id)
            await update.message.reply_text(
                f"⚠️ Превышен лимит запросов.\n"
                f"🕐 Осталось в минуте: {remaining_minute}\n"
                f"📅 Осталось сегодня: {remaining_day}"
            )
            return
            
        self.add_request(user_id)
        
        try:
            # Получаем изображение
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            
            # Скачиваем изображение
            async with aiohttp.ClientSession() as session:
                async with session.get(file.file_path) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        
                        # Кодируем в base64
                        image_base64 = base64.b64encode(image_data).decode('utf-8')
                        
                        # Отправляем в Gemini
                        headers = {'Content-Type': 'application/json'}
                        data = {
                            "contents": [
                                {
                                    "parts": [
                                        {"text": "Опиши что ты видишь на этом изображении подробно."},
                                        {
                                            "inline_data": {
                                                "mime_type": "image/jpeg",
                                                "data": image_base64
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                        
                        async with session.post(
                            f"{GEMINI_API_URL}?key={AI_API_KEY}",
                            headers=headers,
                            json=data,
                            timeout=30
                        ) as api_response:
                            if api_response.status == 200:
                                result = await api_response.json()
                                if 'candidates' in result and len(result['candidates']) > 0:
                                    response = result['candidates'][0]['content']['parts'][0]['text']
                                    await self.safe_send_message(update, response)
                                else:
                                    await update.message.reply_text("Не удалось обработать изображение.")
                            else:
                                await update.message.reply_text("Ошибка при анализе изображения.")
                    else:
                        await update.message.reply_text("Не удалось скачать изображение.")
                        
        except Exception as e:
            logger.error(f"Error processing photo: {e}")
            await update.message.reply_text("Произошла ошибка при обработке изображения.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Exception: {context.error}")
    if update and hasattr(update, 'message') and update.message:
        try:
            await update.message.reply_text("Произошла ошибка. Попробуйте еще раз.")
        except:
            pass

# HTTP сервер и webhook
async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="Bot is running! Status: Active")

async def webhook_handler(request):
    """Обработчик webhook"""
    try:
        logger.info(f"Webhook received: {request.method} {request.path}")
        data = await request.json()
        logger.info(f"Webhook data keys: {list(data.keys())}")
        
        if not telegram_app:
            logger.error("telegram_app is None!")
            return web.Response(status=500, text="Bot not initialized")
            
        update = Update.de_json(data, telegram_app.bot)
        logger.info(f"Update processed: {update.update_id if update else 'None'}")
        
        await telegram_app.process_update(update)
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return web.Response(status=500, text=f"Error: {str(e)}")

async def start_web_server():
    """Запуск веб сервера"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_post('/webhook', webhook_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")
    logger.info(f"Routes: {[route.resource.canonical for route in app.router.routes()]}")

async def main():
    """Основная функция"""
    global telegram_app
    
    logger.info("Starting Gemini Telegram Bot...")
    logger.info(f"TELEGRAM_TOKEN: {'✓' if TELEGRAM_TOKEN else '✗'}")
    logger.info(f"AI_API_KEY: {'✓' if AI_API_KEY else '✗'}")
    logger.info(f"NEWS_API_KEY: {'✓' if NEWS_API_KEY else '✗'}")
    logger.info(f"PORT: {PORT}")
    logger.info(f"RENDER environment: {'✓' if os.environ.get('RENDER') else '✗'}")
    
    if not TELEGRAM_TOKEN or not AI_API_KEY:
        logger.error("Missing required environment variables")
        return
    
    # Создание приложения
    telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
    bot = GeminiBot()
    
    # Добавление обработчиков
    telegram_app.add_handler(CommandHandler("start", bot.start_command))
    telegram_app.add_handler(CommandHandler("help", bot.help_command))
    telegram_app.add_handler(CommandHandler("clear", bot.clear_command))
    telegram_app.add_handler(CommandHandler("limits", bot.limits_command))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, bot.handle_photo))
    telegram_app.add_error_handler(error_handler)
    
    # Определяем окружение
    is_production = os.environ.get('RENDER') is not None
    
    # Инициализация
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Очистка webhook
    try:
        await telegram_app.bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"Error clearing webhook: {e}")
    
    # Запуск веб сервера
    await start_web_server()
    
    # Запуск бота
    if is_production:
        # Webhook для продакшена
        webhook_url = "https://google-gemini-bot.onrender.com/webhook"
        logger.info(f"Setting webhook to {webhook_url}")
        
        try:
            await telegram_app.bot.set_webhook(url=webhook_url)
            logger.info("Webhook set successfully")
            
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            # Fallback к поллингу
            await telegram_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Fallback to polling")
    else:
        # Поллинг для локальной разработки
        logger.info("Starting polling mode")
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Polling started")
    
    # Ожидаем бесконечно
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main()) 