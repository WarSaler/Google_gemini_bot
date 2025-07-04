import os
import logging
import asyncio
import base64
import re
import tempfile
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Dict, List, Optional
from io import BytesIO
import aiohttp
from aiohttp import web
from newsapi import NewsApiClient
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования (должно быть в начале!)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Импорты для голосовых функций
try:
    from gtts import gTTS
    from pydub import AudioSegment
    import speech_recognition as sr
    
    # Piper TTS не требует Python пакет - используем исполняемый файл
    PIPER_AVAILABLE = False  # Будет определяться динамически в setup_piper_if_needed()
    
    VOICE_FEATURES_AVAILABLE = True
    logger.info("Voice features available")
except ImportError as e:
    VOICE_FEATURES_AVAILABLE = False
    PIPER_AVAILABLE = False
    logger.warning(f"Voice features not available: {e}")

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
voice_settings: Dict[int, bool] = defaultdict(lambda: True)  # По умолчанию голосовые ответы включены
voice_engine_settings: Dict[int, str] = defaultdict(lambda: "piper_irina" if PIPER_AVAILABLE else "gtts")  # По умолчанию Piper Irina

# Голосовой движок по умолчанию - будет инициализирован в initialize_voice_engines()
DEFAULT_VOICE_ENGINE = "gtts"

# Хранилище служебных сообщений для автоудаления
user_service_messages: Dict[int, List[int]] = defaultdict(list)  # user_id -> [message_id, ...]

# Хранилище обработанных сообщений для предотвращения дублирования
processed_messages: Dict[str, bool] = {}  # message_id -> processed

# Доступные голосовые движки - инициализация после определения VOICE_FEATURES_AVAILABLE
VOICE_ENGINES = {}

def initialize_voice_engines():
    """Инициализация голосовых движков"""
    global VOICE_ENGINES
    VOICE_ENGINES = {
        "gtts": {
            "name": "Google TTS",
            "description": "Стандартный голос Google (женский)",
            "available": VOICE_FEATURES_AVAILABLE
        },
        "gtts_slow": {
            "name": "Google TTS (медленный)",
            "description": "Более медленная речь Google (женский)",
            "available": VOICE_FEATURES_AVAILABLE
        },
        # Мужские голоса Piper
        "piper_dmitri": {
            "name": "Piper TTS - Дмитрий",
            "description": "Высокое качество, мужской голос (Дмитрий)",
            "available": PIPER_AVAILABLE,
            "voice_model": "ru_RU-dmitri-medium"
        },
        "piper_ruslan": {
            "name": "Piper TTS - Руслан", 
            "description": "Высокое качество, мужской голос (Руслан)",
            "available": PIPER_AVAILABLE,
            "voice_model": "ru_RU-ruslan-medium"
        },
        "piper_pavel": {
            "name": "Piper TTS - Павел",
            "description": "Высокое качество, мужской голос (Павел)",
            "available": PIPER_AVAILABLE,
            "voice_model": "ru_RU-pavel-medium"
        },
        # Женские голоса Piper  
        "piper_irina": {
            "name": "Piper TTS - Ирина",
            "description": "Высокое качество, женский голос (Ирина)",
            "available": PIPER_AVAILABLE,
            "voice_model": "ru_RU-irina-medium"
        },
        "piper_anna": {
            "name": "Piper TTS - Анна",
            "description": "Высокое качество, женский голос (Анна)",
            "available": PIPER_AVAILABLE,
            "voice_model": "ru_RU-anna-medium"
        },
        "piper_elena": {
            "name": "Piper TTS - Елена",
            "description": "Высокое качество, женский голос (Елена)",
            "available": PIPER_AVAILABLE,
            "voice_model": "ru_RU-elena-medium"
        },
        "piper_arina": {
            "name": "Piper TTS - Арина",
            "description": "Премиум качество, женский голос (Арина)",
            "available": PIPER_AVAILABLE,
            "voice_model": "ru_RU-arina-high"
        },
        # Yandex SpeechKit голоса (Alice-like quality)
        "yandex_jane": {
            "name": "Yandex SpeechKit - Jane",
            "description": "Премиум качество, женский голос как у Алисы (Jane)",
            "available": VOICE_FEATURES_AVAILABLE,
            "yandex_voice": "jane"
        },
        "yandex_alena": {
            "name": "Yandex SpeechKit - Alena", 
            "description": "Премиум качество, женский голос (Alena)",
            "available": VOICE_FEATURES_AVAILABLE,
            "yandex_voice": "alena"
        },
        "yandex_filipp": {
            "name": "Yandex SpeechKit - Filipp",
            "description": "Премиум качество, мужской голос (Filipp)",
            "available": VOICE_FEATURES_AVAILABLE,
            "yandex_voice": "filipp"
        }
    }
    
    # Обновляем дефолтные настройки голоса для новых пользователей
    global voice_engine_settings, DEFAULT_VOICE_ENGINE
    default_engine = "piper_irina" if PIPER_AVAILABLE else "gtts"
    DEFAULT_VOICE_ENGINE = default_engine  # Добавляем глобальную переменную
    voice_engine_settings = defaultdict(lambda: default_engine)
    
    logger.info(f"Voice engines initialized. PIPER_AVAILABLE: {PIPER_AVAILABLE}")
    logger.info(f"Default voice engine: {default_engine}")
    
    # Логируем доступные движки
    available_engines = [engine_id for engine_id, info in VOICE_ENGINES.items() if info["available"]]
    logger.info(f"Available voice engines: {available_engines}")

# Глобальная переменная для приложения
telegram_app = None

class GeminiBot:
    def __init__(self):
        # Инициализация NewsAPI если ключ есть
        self.news_client = NewsApiClient(api_key=NEWS_API_KEY) if NEWS_API_KEY else None
        logger.info(f"NewsAPI initialized: {'Yes' if self.news_client else 'No (missing API key)'}")
        
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
        user_id = update.effective_user.id
        voice_status = "включены" if voice_settings[user_id] else "отключены"
        voice_features_status = "✅ доступны" if VOICE_FEATURES_AVAILABLE else "❌ недоступны"
        
        current_engine = voice_engine_settings[user_id]
        engine_info = VOICE_ENGINES.get(current_engine, VOICE_ENGINES["gtts"])
        
        help_message = f"""📋 Справка по командам:

/start - Приветствие
/help - Показать эту справку
/clear - Очистить историю переписки
/limits - Показать лимиты запросов
/voice - Включить/отключить голосовые ответы
/voice_select - Выбрать голосовой движок

🔄 Как пользоваться:
• 💬 Отправьте текстовое сообщение для получения ответа
• 🎤 Отправьте голосовое сообщение - я распознаю речь и отвечу голосом
• 🖼️ Отправьте изображение для анализа
• 📰 Бот автоматически ищет актуальную информацию при необходимости

🎵 Голосовые функции: {voice_features_status}
Голосовые ответы: {voice_status}
Текущий голос: {engine_info['name']}

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

    async def voice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /voice - переключение голосовых ответов"""
        user_id = update.effective_user.id
        voice_settings[user_id] = not voice_settings[user_id]
        
        if voice_settings[user_id]:
            current_engine = voice_engine_settings[user_id]
            engine_info = VOICE_ENGINES.get(current_engine, VOICE_ENGINES["gtts"])
            status_message = f"🎵 Голосовые ответы включены!\n\nТекущий голос: {engine_info['name']}\n{engine_info['description']}\n\nИспользуйте /voice_select для выбора голоса."
        else:
            status_message = "📝 Голосовые ответы отключены.\n\nБот будет отвечать только текстом."
            
        await update.message.reply_text(status_message)

    async def voice_select_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /voice_select - выбор голосового движка"""
        user_id = update.effective_user.id
        current_engine = voice_engine_settings[user_id]
        
        # Создаем список доступных движков, разделенных по категориям
        google_engines = []
        piper_male_engines = []
        piper_female_engines = []
        yandex_engines = []
        
        for engine_id, engine_info in VOICE_ENGINES.items():
            if engine_info["available"]:
                status = "✅ (текущий)" if engine_id == current_engine else "⚡"
                engine_line = f"{status} {engine_info['name']}\n   {engine_info['description']}"
                
                if engine_id.startswith("gtts"):
                    google_engines.append(engine_line)
                elif engine_id.startswith("piper_") and ("мужской" in engine_info['description'] or "Дмитрий" in engine_info['name'] or "Руслан" in engine_info['name'] or "Павел" in engine_info['name']):
                    piper_male_engines.append(engine_line)
                elif engine_id.startswith("piper_"):
                    piper_female_engines.append(engine_line)
                elif engine_id.startswith("yandex_"):
                    yandex_engines.append(engine_line)
        
        if not any([google_engines, piper_male_engines, piper_female_engines, yandex_engines]):
            await update.message.reply_text("❌ Голосовые движки недоступны.")
            return
        
        message = "🎤 Доступные голосовые движки:\n\n"
        
        if google_engines:
            message += "📱 *Google TTS:*\n" + "\n".join(google_engines) + "\n\n"
        
        if piper_male_engines:
            message += "👨 *Piper TTS - Мужские голоса:*\n" + "\n".join(piper_male_engines) + "\n\n"
        
        if piper_female_engines:
            message += "👩 *Piper TTS - Женские голоса:*\n" + "\n".join(piper_female_engines) + "\n\n"
            
        if yandex_engines:
            message += "🌟 *Yandex SpeechKit - Премиум качество:*\n" + "\n".join(yandex_engines) + "\n\n"
        
        # Команды для быстрого выбора (отправляем отдельным сообщением чтобы избежать лимита)  
        commands_message = "📝 Команды для выбора голоса:\n\n"
        commands_message += "Google TTS:\n"
        commands_message += "/voicegtts - Google TTS\n"
        commands_message += "/voicegttsslow - Google TTS (медленный)\n\n"
        
        if piper_male_engines or piper_female_engines:
            commands_message += "Piper TTS (высокое качество):\n"
            commands_message += "/voicedmitri - Дмитрий (мужской)\n"
            commands_message += "/voiceruslan - Руслан (мужской)\n"
            commands_message += "/voiceirina - Ирина (женский)\n"
            commands_message += "/voiceanna - Анна (женский)\n\n"
        
        if yandex_engines:
            commands_message += "Yandex SpeechKit:\n"
            commands_message += "/voicejane - Jane (женский, как Алиса)\n"
            commands_message += "/voicealena - Alena (женский)\n"
            commands_message += "/voicefilipp - Filipp (мужской)\n\n"
        
        commands_message += "💡 Команды работают и с подчеркиванием (/voice_ruslan) и без (/voiceruslan)"
        
        # Отправляем сообщения отдельно чтобы избежать лимитов
        await update.message.reply_text(message, parse_mode='Markdown')
        await update.message.reply_text(commands_message)

    async def set_voice_engine_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, engine: str):
        """Установка голосового движка"""
        user_id = update.effective_user.id
        
        logger.info(f"🎵 SET_VOICE_ENGINE_COMMAND CALLED! User {user_id} trying to set voice engine: {engine}")
        logger.info(f"🎵 Message text: '{update.message.text if update.message else 'No message'}'")
        logger.info(f"🎵 Available engines: {list(VOICE_ENGINES.keys())}")
        
        if engine not in VOICE_ENGINES:
            logger.warning(f"Unknown engine {engine} requested by user {user_id}")
            await update.message.reply_text("❌ Неизвестный голосовой движок.")
            return
        
        engine_info = VOICE_ENGINES[engine]
        logger.info(f"Engine info for {engine}: available={engine_info['available']}")
        
        if not engine_info["available"]:
            logger.warning(f"Engine {engine} not available for user {user_id}")
            await update.message.reply_text(f"❌ {engine_info['name']} недоступен.")
            return
        
        voice_engine_settings[user_id] = engine
        logger.info(f"Successfully set voice engine for user {user_id}: {engine}")
        
        await update.message.reply_text(
            f"✅ Голос успешно изменен!\n\n"
            f"🎵 Новый голос: {engine_info['name']}\n"
            f"📝 Описание: {engine_info['description']}\n\n"
            f"🎤 Отправьте голосовое сообщение для тестирования нового голоса!\n"
            f"💡 Выбрать другой голос: /voice_select"
        )

    def clean_text_for_speech(self, text: str) -> str:
        """Очистка текста для синтеза речи"""
        # Удаляем markdown символы
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Убираем жирный текст
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # Убираем курсив
        text = re.sub(r'`(.*?)`', r'\1', text)        # Убираем код
        text = re.sub(r'#{1,6}\s*', '', text)         # Убираем заголовки
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # Убираем ссылки
        text = re.sub(r'[_~]', '', text)              # Убираем подчеркивания и зачеркивания
        
        # Убираем эмодзи для лучшего озвучивания
        text = re.sub(r'[🎤🎵📝💬🖼️📰💰⚡❌✅🔍💭📊💡🔄]', '', text)
        
        # Убираем множественные пробелы и переносы
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        return text

    def smart_split_text(self, text: str, max_chars: int = 200) -> List[str]:
        """Умная разбивка текста на части для голосового синтеза"""
        # Увеличиваем лимит до 200 символов для меньшего количества частей
        if len(text) <= max_chars:
            return [text]
        
        parts = []
        
        # Сначала пробуем разбить по предложениям (точка, восклицательный, вопросительный знак)
        sentences = re.split(r'[.!?]+\s+', text)
        current_part = ""
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Если предложение очень длинное (больше max_chars), разбиваем его
            if len(sentence) > max_chars:
                # Сохраняем текущую часть, если есть
                if current_part:
                    parts.append(current_part.strip())
                    current_part = ""
                
                # Пробуем разбить длинное предложение по запятым
                clauses = sentence.split(',')
                for clause in clauses:
                    clause = clause.strip()
                    if not clause:
                        continue
                        
                    # Если добавление этой части не превышает лимит, добавляем
                    test_part = current_part + (", " if current_part else "") + clause
                    if len(test_part) <= max_chars:
                        current_part = test_part
                    else:
                        # Сохраняем текущую часть и начинаем новую
                        if current_part:
                            parts.append(current_part.strip())
                        current_part = clause
                
                # Если всё ещё слишком длинно, разбиваем принудительно
                if len(current_part) > max_chars:
                    # Принудительное разбитие по словам
                    words = current_part.split()
                    temp_part = ""
                    for word in words:
                        test_part = temp_part + (" " if temp_part else "") + word
                        if len(test_part) <= max_chars:
                            temp_part = test_part
                        else:
                            if temp_part:
                                parts.append(temp_part.strip())
                            temp_part = word
                    current_part = temp_part
            else:
                # Обычное предложение - пробуем добавить к текущей части
                test_part = current_part + (". " if current_part else "") + sentence
                if len(test_part) <= max_chars:
                    current_part = test_part
                else:
                    # Сохраняем текущую часть и начинаем новую
                    if current_part:
                        parts.append(current_part.strip())
                    current_part = sentence
        
        # Добавляем последнюю часть
        if current_part:
            parts.append(current_part.strip())
        
        # Фильтруем слишком короткие части и объединяем их
        final_parts = []
        for part in parts:
            part = part.strip()
            if len(part) < 10:  # Очень короткие части объединяем с предыдущими
                if final_parts and len(final_parts[-1] + " " + part) <= max_chars:
                    final_parts[-1] = final_parts[-1] + " " + part
                elif part:  # Если не можем объединить, всё равно добавляем
                    final_parts.append(part)
            else:
                final_parts.append(part)
        
        # Если ничего не получилось, возвращаем принудительно разбитый текст
        if not final_parts:
            # Принудительное разбитие на части по max_chars
            for i in range(0, len(text), max_chars):
                final_parts.append(text[i:i + max_chars])
        
        return final_parts

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
            # Определяем контекст запроса
            user_message = ""
            if messages and len(messages) > 0:
                user_message = messages[-1].get("content", "").lower()
            
            # Добавляем системное сообщение только для запросов, связанных с возрастом
            age_related_keywords = ['возраст', 'лет', 'года', 'годы', 'сколько лет', 'родился', 'родилась', 'дата рождения']
            is_age_query = any(keyword in user_message for keyword in age_related_keywords)
            
            headers = {
                'Content-Type': 'application/json',
            }
            
            # Создаем список сообщений, добавляя системное сообщение только для запросов о возрасте
            all_messages = []
            if is_age_query:
                current_date = datetime.now().strftime("%d.%m.%Y")
                system_message = f"ВАЖНО: Сегодня {current_date} год. При расчете возраста людей используй эту дату."
                all_messages.append({"role": "system", "content": system_message})
            
            all_messages.extend(messages)
            
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

    async def speech_to_text(self, audio_bytes: bytes) -> Optional[str]:
        """Конвертация аудио в текст"""
        if not VOICE_FEATURES_AVAILABLE:
            return None
            
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

    async def text_to_speech(self, text: str, user_id: int, language: str = "ru") -> Optional[bytes]:
        """Синтез речи из текста с поддержкой разных движков"""
        if not VOICE_FEATURES_AVAILABLE:
            return None
            
        try:
            # Проверка на минимальную длину текста
            if not text or len(text.strip()) < 3:
                logger.warning("Text too short for TTS")
                return None
                
            # Убираем ограничения длины текста - синтезируем полностью
            logger.info(f"Synthesizing text of {len(text)} characters")
            
            # Получаем выбранный пользователем движок
            engine = voice_engine_settings.get(user_id, "gtts")
            logger.info(f"User {user_id} selected engine: {engine}")
            logger.info(f"PIPER_AVAILABLE: {PIPER_AVAILABLE}")
            logger.debug(f"Converting text to speech with {engine}: {len(text)} characters")
            
            # Проверяем доступность движка
            engine_info = VOICE_ENGINES.get(engine)
            if engine_info:
                logger.info(f"Engine info for {engine}: name='{engine_info['name']}', available={engine_info['available']}")
            else:
                logger.warning(f"No engine info found for {engine}")
            
            if engine == "gtts":
                logger.info("Using Google TTS (standard)")
                return await self._gtts_synthesize(text, language, slow=False)
            elif engine == "gtts_slow":
                logger.info("Using Google TTS (slow)")
                return await self._gtts_synthesize(text, language, slow=True)
            elif engine.startswith("piper_") and PIPER_AVAILABLE:
                logger.info(f"Using Piper TTS with engine: {engine}")
                # Определяем голосовую модель из настроек движка
                engine_info = VOICE_ENGINES.get(engine)
                if engine_info and "voice_model" in engine_info:
                    voice_model = engine_info["voice_model"]
                    logger.info(f"Using voice model: {voice_model}")
                    return await self._piper_synthesize(text, voice_model)
                else:
                    # Fallback к Дмитрию если модель не найдена
                    logger.warning(f"Voice model not found for {engine}, using fallback: ru_RU-dmitri-medium")
                    return await self._piper_synthesize(text, "ru_RU-dmitri-medium")
            elif engine.startswith("yandex_"):
                logger.info(f"Using Yandex SpeechKit with engine: {engine}")
                # Yandex SpeechKit TTS
                engine_info = VOICE_ENGINES.get(engine)
                if engine_info and "yandex_voice" in engine_info:
                    yandex_voice = engine_info["yandex_voice"]
                    logger.info(f"Using Yandex voice: {yandex_voice}")
                    return await self._yandex_synthesize(text, yandex_voice, language)
                else:
                    # Fallback к gTTS
                    logger.warning(f"Yandex voice not configured for {engine}, falling back to gTTS")
                    return await self._gtts_synthesize(text, language, slow=False)
            else:
                # Fallback к gTTS
                logger.warning(f"Engine {engine} not available or not supported, falling back to gTTS")
                logger.warning(f"Available engines: {list(VOICE_ENGINES.keys())}")
                return await self._gtts_synthesize(text, language, slow=False)
                    
        except Exception as e:
            logger.error(f"Error in text-to-speech: {e}")
            return None

    async def _gtts_synthesize(self, text: str, language: str, slow: bool = False) -> Optional[bytes]:
        """Синтез с помощью Google TTS"""
        try:
            # Создание временного файла для аудио
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                temp_path = temp_file.name
            
            try:
                # Создание TTS объекта
                tts = gTTS(text=text, lang=language, slow=slow)
                
                # Сохранение в временный файл
                tts.save(temp_path)
                
                # Чтение байтов из файла
                with open(temp_path, 'rb') as audio_file:
                    audio_bytes = audio_file.read()
                
                logger.info(f"gTTS synthesis success: generated {len(audio_bytes)} bytes")
                return audio_bytes
                
            finally:
                # Очистка временного файла
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"Error in gTTS synthesis: {e}")
            return None

    async def _yandex_synthesize(self, text: str, voice: str = "jane", language: str = "ru") -> Optional[bytes]:
        """Синтез с помощью Yandex SpeechKit (демо версия без API ключа)"""
        try:
            # Для демонстрации используем публичный demo endpoint
            # В продакшене нужен API ключ Yandex Cloud
            logger.info(f"Attempting Yandex SpeechKit synthesis with voice: {voice}")
            
            # Убираем ограничения для demo версии - поддерживаем полную длину
            logger.info(f"Yandex SpeechKit processing {len(text)} characters")
            
            # Fallback к gTTS так как Yandex требует API ключ
            logger.info("Yandex SpeechKit requires API key, falling back to enhanced gTTS")
            
            # Используем gTTS с БЫСТРЫМИ настройками
            return await self._gtts_synthesize(text, language, slow=False)  # БЫСТРАЯ речь для скорости
            
        except Exception as e:
            logger.error(f"Error in Yandex synthesis: {e}")
            # Fallback к gTTS
            return await self._gtts_synthesize(text, language, slow=False)

    async def _piper_synthesize(self, text: str, voice_model: str = "ru_RU-dmitri-medium") -> Optional[bytes]:
        """Синтез с помощью Piper TTS - используя --output-raw метод для русского языка"""
        try:
            import tempfile
            import os
            import subprocess
            
            # Определяем модель голоса
            if not voice_model:
                voice_model = "ru_RU-dmitri-medium"
            
            # Список всех доступных моделей (в порядке приоритета для fallback)
            working_models = [
                voice_model,             # Сначала пробуем запрошенную модель
                "ru_RU-irina-medium",    # Женский голос, резерв
                "ru_RU-dmitri-medium",   # Мужской голос, резерв  
                "ru_RU-ruslan-medium",   # Мужской голос, резерв
                "ru_RU-anna-medium",     # Женский голос, резерв
            ]
            
            # Убираем дубликаты, сохраняя порядок
            seen = set()
            working_models = [x for x in working_models if not (x in seen or seen.add(x))]
            
            # Ищем первую рабочую модель из списка
            voices_dir = "/app/piper_tts/voices"
            final_voice_model = None
            final_model_path = None
            
            if os.path.exists(voices_dir):
                for test_model in working_models:
                    test_model_path = f"/app/piper_tts/voices/{test_model}.onnx"
                    test_config_path = f"/app/piper_tts/voices/{test_model}.onnx.json"
                    
                    if os.path.exists(test_model_path) and os.path.exists(test_config_path):
                        final_voice_model = test_model
                        final_model_path = test_model_path
                        if test_model == voice_model:
                            logger.info(f"Using requested voice model: {voice_model}")
                        else:
                            logger.info(f"Using fallback voice model: {test_model} (requested: {voice_model})")
                        break
                
                if not final_voice_model:
                    logger.error("No working voice models found with both .onnx and .onnx.json files")
                    return None
            else:
                logger.error("Voices directory not found")
                return None
            
            # Используем найденную модель
            voice_model = final_voice_model
            model_path = final_model_path
            
            # Проверяем наличие исполняемого файла piper
            piper_executable = "/app/piper_tts/bin/piper/piper"
            if not os.path.exists(piper_executable) or not os.access(piper_executable, os.X_OK):
                logger.error(f"Piper executable not found at: {piper_executable}")
                return None
            
            # Очистка и подготовка текста для русского языка
            clean_text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
            clean_text = ' '.join(clean_text.split())  # Убираем множественные пробелы
            clean_text = clean_text.strip()
            
            if not clean_text:
                logger.error("Empty text after cleaning")
                return None
            
            # Ограничиваем длину текста для стабильности
            if len(clean_text) > 500:
                clean_text = clean_text[:497] + "..."
                logger.info(f"Text truncated to 500 characters for stability")
            
            logger.info(f"Synthesizing Russian text: '{clean_text}' (length: {len(clean_text)})")
            
            # Создаем временный файл для результата
            wav_fd, wav_filename = tempfile.mkstemp(suffix=".wav")
            os.close(wav_fd)
            
            try:
                # Упрощенная команда для Piper TTS с выводом в файл напрямую
                # echo 'текст' | piper --model model.onnx --output_file output.wav
                piper_cmd = [
                    piper_executable,
                    "--model", model_path,
                    "--output_file", wav_filename
                ]
                
                logger.info(f"Piper command: {' '.join(piper_cmd)}")
                logger.info(f"Input text length: {len(clean_text)} characters")
                
                # Запускаем Piper TTS с увеличенным timeout
                process = subprocess.Popen(
                    piper_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Передаем текст и ждем завершения с увеличенным timeout
                try:
                    stdout, stderr = process.communicate(input=clean_text, timeout=30)  # Увеличен timeout до 30 секунд
                    return_code = process.returncode
                    
                    logger.info(f"Piper process completed with return code: {return_code}")
                    if stdout:
                        logger.info(f"Piper stdout: {stdout}")
                    if stderr:
                        logger.info(f"Piper stderr: {stderr}")
                    
                    if return_code != 0:
                        logger.error(f"Piper failed with return code {return_code}")
                        if stderr:
                            logger.error(f"Error details: {stderr}")
                        return None
                    
                except subprocess.TimeoutExpired:
                    logger.error("Piper TTS timeout after 30 seconds")
                    process.kill()
                    process.wait()
                    return None
                
                # Проверяем результат
                if not os.path.exists(wav_filename):
                    logger.error("Output audio file was not created")
                    return None
                
                file_size = os.path.getsize(wav_filename)
                logger.info(f"Generated audio file size: {file_size} bytes")
                
                if file_size == 0:
                    logger.error("Generated audio file is empty")
                    return None
                
                # Читаем аудио данные
                with open(wav_filename, 'rb') as f:
                    audio_data = f.read()
                
                if len(audio_data) > 0:
                    logger.info(f"✅ Successfully synthesized {len(audio_data)} bytes with Piper TTS")
                    return audio_data
                else:
                    logger.error("Audio data is empty after reading file")
                    return None
                    
            finally:
                # Обязательная очистка временного файла
                if os.path.exists(wav_filename):
                    try:
                        os.unlink(wav_filename)
                    except Exception as e:
                        logger.warning(f"Failed to delete temp file {wav_filename}: {e}")
                            
        except Exception as e:
            logger.error(f"Error in Piper TTS synthesis: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
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
        
        # Отправляем служебное сообщение о том, что думаем
        await self.send_service_message(update, context, "💭 Думаю над ответом...", user_id)
        
        # Добавление сообщения пользователя в историю
        user_sessions[user_id].append({"role": "user", "content": user_message})
        messages = list(user_sessions[user_id])
        
        # Вызов API
        response = await self.call_gemini_api(messages)
        
        if response:
            logger.info(f"Received response from Gemini API for user {user_id}: {len(response)} characters")
            
            # Добавление запроса в счетчик
            self.add_request(user_id)
            
            # Получение оставшихся запросов
            remaining_minute, remaining_day = self.get_remaining_requests(user_id)
            
            # Удаляем служебное сообщение перед отправкой ответа
            await self.cleanup_service_messages(update, context, user_id)
            
            # Для текстовых сообщений ВСЕГДА отвечаем только текстом
            # Отправка ответа через безопасную функцию
            full_response = f"{response}\n\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
            await self.safe_send_message(update, full_response)
            
            # Добавление ответа в историю
            user_sessions[user_id].append({"role": "assistant", "content": response})
            
            logger.info(f"Successfully sent response to user {user_id}: {len(response)} characters")
        else:
            # Fallback ответ если API не ответил
            await self.cleanup_service_messages(update, context, user_id)
            await update.message.reply_text(
                "❌ Не удалось получить ответ от ИИ.\n\n"
                "Попробуйте:\n"
                "• Переформулировать вопрос\n"
                "• Повторить запрос через несколько секунд\n"
                "• Проверить соединение с интернетом"
            )

    def needs_current_data(self, query: str) -> bool:
        """Проверка, нужны ли актуальные данные"""
        query_lower = query.lower()
        
        # Явные запросы актуальной информации
        current_keywords = [
            'новости', 'свежие новости', 'последние новости',
            'курс валют', 'курс доллара', 'курс евро', 'цена bitcoin',
            'погода сегодня', 'погода сейчас', 'текущая погода',
            'сколько лет', 'возраст', 'когда родился', 'когда родилась'
        ]
        
        # Временные маркеры
        time_keywords = [
            'сегодня', 'сейчас', 'вчера', 'завтра', 'на данный момент',
            'в настоящее время', 'текущий', 'актуальн', 'свеж', 'последн'
        ]
        
        # Проверяем явные запросы актуальной информации
        if any(keyword in query_lower for keyword in current_keywords):
            return True
            
        # Проверяем комбинацию временных маркеров с определенными темами
        has_time_marker = any(keyword in query_lower for keyword in time_keywords)
        
        if has_time_marker:
            # Исключаем вопросы об интересных фактах
            if 'интересн' in query_lower and 'факт' in query_lower:
                return False
            # Включаем другие запросы с временными маркерами
            return True
            
        return False

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

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка голосовых сообщений"""
        user_id = update.effective_user.id
        message_id = f"{user_id}_{update.message.message_id}"
        
        # Проверка дублирования
        if message_id in processed_messages:
            logger.info(f"Message {message_id} already processed, skipping")
            return
        
        # Отмечаем сообщение как обрабатываемое
        processed_messages[message_id] = True
        
        logger.info(f"Received voice message from user {user_id}")
        
        if not VOICE_FEATURES_AVAILABLE:
            await update.message.reply_text(
                "🎤 Извините, голосовые функции недоступны.\n\n"
                "Сервер не поддерживает обработку голосовых сообщений.\n"
                "Пожалуйста, отправьте ваш вопрос текстом."
            )
            return
        
        try:
            # Проверка лимитов
            if not self.can_make_request(user_id):
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                await update.message.reply_text(
                    f"❌ Превышен лимит запросов!\n\n"
                    f"Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в этой минуте, {remaining_day}/{DAILY_LIMIT} сегодня."
                )
                return

            # Отправка индикатора печати
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            logger.info(f"Sent typing indicator for voice processing from user {user_id}")
            
            # Получение голосового файла
            voice_file = await update.message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            
            logger.info(f"Downloaded voice message: {len(voice_bytes)} bytes")
            
            # Распознавание речи - отправляем служебное сообщение
            await self.send_service_message(update, context, "🎤 Распознаю речь...", user_id)
            
            transcribed_text = await self.speech_to_text(bytes(voice_bytes))
            
            if not transcribed_text:
                await self.cleanup_service_messages(update, context, user_id)
                await update.message.reply_text(
                    "❌ Не удалось распознать речь.\n\n"
                    "Попробуйте:\n"
                    "• Говорить четче и громче\n"
                    "• Уменьшить фоновый шум\n"
                    "• Записать сообщение заново"
                )
                return
            
            logger.info(f"Voice transcribed for user {user_id}: {transcribed_text[:50]}...")
            
            # Отправляем подтверждение распознавания - заменяем предыдущее служебное сообщение
            await self.send_service_message(update, context, f"✅ Распознано: \"{transcribed_text}\"", user_id)
            
            # Добавление сообщения пользователя в историю
            user_sessions[user_id].append({"role": "user", "content": transcribed_text})
            messages = list(user_sessions[user_id])

            # Уведомление о начале обработки - заменяем предыдущее служебное сообщение
            await self.send_service_message(update, context, "💭 Думаю над ответом...", user_id)
            
            logger.info(f"Calling Gemini API for voice message from user {user_id}")
            response = await self.call_gemini_api(messages)
            
            if response:
                logger.info(f"Received response from Gemini API for voice message from user {user_id}: {len(response)} characters")
                
                # Добавление запроса в счетчик
                self.add_request(user_id)
                
                # Получение оставшихся запросов
                remaining_minute, remaining_day = self.get_remaining_requests(user_id)
                
                # ГОЛОСОВЫЕ СООБЩЕНИЯ ВСЕГДА ОТВЕЧАЮТ ГОЛОСОМ (если есть выбранный движок)
                selected_engine = voice_engine_settings.get(user_id, DEFAULT_VOICE_ENGINE)
                if VOICE_ENGINES[selected_engine]["available"]:
                    # Генерация голосового ответа - заменяем предыдущее служебное сообщение
                    await self.send_service_message(update, context, "🎵 Генерирую голосовой ответ...", user_id)
                    
                    # Очистка текста от markdown символов для лучшего озвучивания
                    clean_response = self.clean_text_for_speech(response)
                    
                    # Умная разбивка длинного текста с увеличенным лимитом
                    text_parts = self.smart_split_text(clean_response, max_chars=200)
                    logger.info(f"Split text into {len(text_parts)} parts for voice synthesis")
                    
                    if len(text_parts) == 1:
                        # Короткий текст - отправляем одним сообщением
                        logger.info(f"Synthesizing single part of {len(text_parts[0])} characters")
                        voice_data = await self.text_to_speech(text_parts[0], user_id)
                        
                        if voice_data:
                            await self.cleanup_service_messages(update, context, user_id)
                            await update.message.reply_voice(
                                voice=BytesIO(voice_data),
                                caption=f"🎤 Голосовой ответ\n\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
                            )
                            logger.info(f"Successfully sent single voice response to user {user_id}")
                            user_sessions[user_id].append({"role": "assistant", "content": response})
                        else:
                            # Fallback к тексту
                            await self.cleanup_service_messages(update, context, user_id)
                            await update.message.reply_text(
                                f"💬 {response}\n\n⚠️ Не удалось создать голосовой ответ\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
                            )
                            user_sessions[user_id].append({"role": "assistant", "content": response})
                    else:
                        # Длинный текст - отправляем частями
                        logger.info(f"Sending long response as {len(text_parts)} voice parts")
                        await self.send_voice_parts(update, context, text_parts, user_id, remaining_minute, remaining_day)
                        user_sessions[user_id].append({"role": "assistant", "content": response})
                else:
                    # Текстовый ответ
                    await self.cleanup_service_messages(update, context, user_id)
                    await update.message.reply_text(
                        f"💬 {response}\n\n"
                        f"📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
                    )
                    
                    # Добавление ответа в историю
                    user_sessions[user_id].append({"role": "assistant", "content": response})
            else:
                await self.cleanup_service_messages(update, context, user_id)
                await update.message.reply_text(
                    "❌ Не удалось получить ответ от ИИ.\n\n"
                    "Попробуйте:\n"
                    "• Переформулировать вопрос\n"
                    "• Повторить запрос через несколько секунд\n"
                    "• Проверить соединение с интернетом"
                )
                
        except Exception as e:
            logger.error(f"Error processing voice message: {e}")
            await self.cleanup_service_messages(update, context, user_id)
            await update.message.reply_text("❌ Произошла ошибка при обработке голосового сообщения.")
        
        finally:
            # Очистка старых записей обработанных сообщений (оставляем только последние 100)
            if len(processed_messages) > 100:
                old_keys = list(processed_messages.keys())[:-50]  # Удаляем старые, оставляем 50 новых
                for key in old_keys:
                    processed_messages.pop(key, None)

    async def send_voice_parts(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                              text_parts: List[str], user_id: int, remaining_minute: int, remaining_day: int):
        """Отправляет несколько голосовых сообщений для длинного текста"""
        
        total_parts = len(text_parts)
        logger.info(f"Sending {total_parts} voice parts to user {user_id}")
        
        for i, part in enumerate(text_parts, 1):
            try:
                # Генерируем голосовое сообщение для каждой части
                await self.send_service_message(update, context, f"🎵 Генерирую часть {i}/{total_parts}...", user_id)
                
                voice_data = await self.text_to_speech(part, user_id)
                
                if voice_data:
                    await self.cleanup_service_messages(update, context, user_id)
                    
                    # Caption для первого и последнего сообщения
                    if i == 1 and total_parts > 1:
                        caption = f"🎤 Голосовой ответ (часть {i}/{total_parts})"
                    elif i == total_parts:
                        caption = f"🎤 Завершение (часть {i}/{total_parts})\n\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
                    else:
                        caption = f"🎤 Продолжение (часть {i}/{total_parts})"
                    
                    await update.message.reply_voice(
                        voice=BytesIO(voice_data),
                        caption=caption
                    )
                    
                    # Небольшая задержка между сообщениями
                    if i < total_parts:
                        await asyncio.sleep(0.5)
                        
                else:
                    # Если синтез не удался, отправляем текстом
                    await self.cleanup_service_messages(update, context, user_id)
                    await update.message.reply_text(
                        f"💬 Часть {i}/{total_parts}: {part}\n\n"
                        f"⚠️ Не удалось создать голосовой ответ для этой части"
                    )
                    
            except Exception as e:
                logger.error(f"Error sending voice part {i}: {e}")
                await self.cleanup_service_messages(update, context, user_id)
                await update.message.reply_text(f"💬 Часть {i}/{total_parts}: {part}")

    async def add_service_message(self, user_id: int, message_id: int):
        """Добавляет служебное сообщение в список для автоудаления"""
        user_service_messages[user_id].append(message_id)
        
    async def cleanup_service_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
        """Удаляет все накопленные служебные сообщения пользователя"""
        try:
            for message_id in user_service_messages[user_id]:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=message_id)
                except Exception as e:
                    logger.debug(f"Could not delete service message {message_id}: {e}")
            
            # Очищаем список после удаления
            user_service_messages[user_id].clear()
            logger.debug(f"Cleaned up service messages for user {user_id}")
        except Exception as e:
            logger.error(f"Error cleaning up service messages for user {user_id}: {e}")
            
    async def send_service_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, user_id: int) -> Optional[int]:
        """Отправляет служебное сообщение и добавляет его в список для автоудаления"""
        try:
            # Сначала удаляем предыдущие служебные сообщения
            await self.cleanup_service_messages(update, context, user_id)
            
            # Отправляем новое служебное сообщение
            message = await update.message.reply_text(text)
            
            # Добавляем в список для автоудаления
            await self.add_service_message(user_id, message.message_id)
            
            return message.message_id
        except Exception as e:
            logger.error(f"Error sending service message: {e}")
            return None

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
        
        # ДОБАВЛЕНО: Детальное логирование для команд
        if update and update.message and update.message.text:
            message_text = update.message.text
            logger.info(f"Message text: '{message_text}'")
            
            if message_text.startswith('/voice_'):
                logger.info(f"VOICE COMMAND DETECTED: {message_text}")
                logger.info(f"User ID: {update.effective_user.id if update.effective_user else 'Unknown'}")
                logger.info(f"Available handlers: {len(telegram_app.handlers)}")
                
                # Проверяем, есть ли обработчик для этой команды
                for group in telegram_app.handlers.values():
                    for handler in group:
                        if hasattr(handler, 'command') and isinstance(handler.command, (list, set)):
                            if message_text[1:] in handler.command:
                                logger.info(f"Found handler for command: {message_text}")
                        elif hasattr(handler, 'command') and handler.command == message_text[1:]:
                            logger.info(f"Found handler for command: {message_text}")
        
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

def setup_piper_if_needed():
    """Устанавливает Piper TTS если не установлен"""
    global PIPER_AVAILABLE
    
    # Проверяем наличие исполняемого файла Piper
    piper_executable_available = False
    executable_paths = [
        "piper_tts/bin/piper/piper",
        "/usr/local/bin/piper",
        "/usr/bin/piper"
    ]
    
    for path in executable_paths:
        if os.path.exists(path) and os.access(path, os.X_OK):
            try:
                result = subprocess.run([path, "--help"], 
                                      capture_output=True, timeout=5)
                if result.returncode == 0:
                    piper_executable_available = True
                    logger.info(f"Piper executable found at: {path}")
                    break
            except:
                continue
    
    # Проверяем наличие голосовых моделей
    voices_dir = "piper_tts/voices"
    models_exist = False
    
    if os.path.exists(voices_dir):
        onnx_files = [f for f in os.listdir(voices_dir) if f.endswith('.onnx')]
        if len(onnx_files) >= 4:  # Ожидаем 4 голосовые модели
            models_exist = True
            logger.info(f"Found {len(onnx_files)} voice models")
        else:
            logger.info(f"Found only {len(onnx_files)} voice models, need to download more")
    else:
        logger.info("Voices directory not found, will create and download models")
    
    # Запускаем установочный скрипт если нужно установить Piper или скачать модели
    if not piper_executable_available or not models_exist:
        logger.info("Running Piper TTS installation script...")
        result = subprocess.run(['bash', 'install_piper.sh'], 
                              capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("Piper TTS installation script completed successfully")
            logger.info(f"Installation stdout: {result.stdout}")
            
            # Проверяем исполняемый файл после установки
            if not piper_executable_available:
                for path in executable_paths:
                    if os.path.exists(path) and os.access(path, os.X_OK):
                        try:
                            result = subprocess.run([path, "--help"], 
                                                  capture_output=True, timeout=5)
                            if result.returncode == 0:
                                piper_executable_available = True
                                logger.info(f"Piper executable installed successfully at: {path}")
                                break
                        except:
                            continue
            
            # Проверяем наличие голосовых моделей
            if os.path.exists(voices_dir):
                onnx_files = [f for f in os.listdir(voices_dir) if f.endswith('.onnx')]
                logger.info(f"Voice models after installation: {len(onnx_files)} found")
                if len(onnx_files) > 0:
                    logger.info(f"Available models: {onnx_files}")
                    PIPER_AVAILABLE = True
                    return True
                else:
                    logger.warning("No voice models found after installation")
                    PIPER_AVAILABLE = False
                    return False
            else:
                logger.error("Voices directory still not found after installation")
                PIPER_AVAILABLE = False
                return False
        else:
            logger.error(f"Piper TTS installation failed: {result.stderr}")
            logger.error(f"Installation stdout: {result.stdout}")
            PIPER_AVAILABLE = False
            return False
    else:
        # Piper уже установлен
        PIPER_AVAILABLE = True
        logger.info("Piper TTS already available")
    
    return True

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
        
    # Устанавливаем Piper TTS если необходимо
    if os.environ.get('RENDER'):
        if setup_piper_if_needed():
            # Переинициализируем движки после установки Piper
            initialize_voice_engines()
            logger.info("Voice engines reinitialized after Piper setup")
    else:
        # Локальная разработка - просто инициализируем движки
        initialize_voice_engines()
        logger.info("Voice engines initialized for local development")
    
    # Создание приложения
    telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
    bot = GeminiBot()
    
    # Добавление обработчиков
    telegram_app.add_handler(CommandHandler("start", bot.start_command))
    telegram_app.add_handler(CommandHandler("help", bot.help_command))
    telegram_app.add_handler(CommandHandler("clear", bot.clear_command))
    telegram_app.add_handler(CommandHandler("limits", bot.limits_command))
    telegram_app.add_handler(CommandHandler("voice", bot.voice_command))
    telegram_app.add_handler(CommandHandler("voice_select", bot.voice_select_command))
    # Голосовые команды - используем отдельные методы вместо лямбда
    async def voice_gtts_command(u, c): await bot.set_voice_engine_command(u, c, "gtts")
    async def voice_gtts_slow_command(u, c): await bot.set_voice_engine_command(u, c, "gtts_slow")
    async def voice_dmitri_command(u, c): await bot.set_voice_engine_command(u, c, "piper_dmitri")
    async def voice_ruslan_command(u, c): await bot.set_voice_engine_command(u, c, "piper_ruslan")
    async def voice_pavel_command(u, c): await bot.set_voice_engine_command(u, c, "piper_pavel")
    async def voice_irina_command(u, c): await bot.set_voice_engine_command(u, c, "piper_irina")
    async def voice_anna_command(u, c): await bot.set_voice_engine_command(u, c, "piper_anna")
    async def voice_elena_command(u, c): await bot.set_voice_engine_command(u, c, "piper_elena")
    async def voice_arina_command(u, c): await bot.set_voice_engine_command(u, c, "piper_arina")
    async def voice_jane_command(u, c): await bot.set_voice_engine_command(u, c, "yandex_jane")
    async def voice_alena_command(u, c): await bot.set_voice_engine_command(u, c, "yandex_alena")
    async def voice_filipp_command(u, c): await bot.set_voice_engine_command(u, c, "yandex_filipp")
    
    # ДОБАВЛЯЕМ ОБРАБОТЧИКИ ДЛЯ КОМАНД С ПОДЧЕРКИВАНИЕМ И БЕЗ
    telegram_app.add_handler(CommandHandler("voice_gtts", voice_gtts_command))
    telegram_app.add_handler(CommandHandler("voicegtts", voice_gtts_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_gtts_slow", voice_gtts_slow_command))
    telegram_app.add_handler(CommandHandler("voicegttsslow", voice_gtts_slow_command))  # БЕЗ подчеркивания
    # Piper TTS голоса
    telegram_app.add_handler(CommandHandler("voice_dmitri", voice_dmitri_command))
    telegram_app.add_handler(CommandHandler("voicedmitri", voice_dmitri_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_ruslan", voice_ruslan_command))
    telegram_app.add_handler(CommandHandler("voiceruslan", voice_ruslan_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_pavel", voice_pavel_command))
    telegram_app.add_handler(CommandHandler("voicepavel", voice_pavel_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_irina", voice_irina_command))
    telegram_app.add_handler(CommandHandler("voiceirina", voice_irina_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_anna", voice_anna_command))
    telegram_app.add_handler(CommandHandler("voiceanna", voice_anna_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_elena", voice_elena_command))
    telegram_app.add_handler(CommandHandler("voiceelena", voice_elena_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_arina", voice_arina_command))
    telegram_app.add_handler(CommandHandler("voicearina", voice_arina_command))  # БЕЗ подчеркивания
    # Yandex SpeechKit голоса
    telegram_app.add_handler(CommandHandler("voice_jane", voice_jane_command))
    telegram_app.add_handler(CommandHandler("voicejane", voice_jane_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_alena", voice_alena_command))
    telegram_app.add_handler(CommandHandler("voicealena", voice_alena_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_filipp", voice_filipp_command))
    telegram_app.add_handler(CommandHandler("voicefilipp", voice_filipp_command))  # БЕЗ подчеркивания

    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, bot.handle_photo))
    telegram_app.add_handler(MessageHandler(filters.VOICE, bot.handle_voice))
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