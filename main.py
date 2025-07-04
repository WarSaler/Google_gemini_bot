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

# Проверка доступности функций
try:
    from gtts import gTTS
    import tempfile
    import speech_recognition as sr
    from pydub import AudioSegment
    
    VOICE_FEATURES_AVAILABLE = True
    logger.info("Voice features available")
except ImportError as e:
    VOICE_FEATURES_AVAILABLE = False
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

# Голосовые настройки - будут инициализированы в initialize_voice_engines()
voice_engine_settings: Dict[int, str] = defaultdict(str)  # Будет установлен позже
VOICE_ENGINES: Dict[str, dict] = {}  # Будет заполнен в initialize_voice_engines()
DEFAULT_VOICE_ENGINE = "azure_dmitri"  # Будет установлен в initialize_voice_engines()

# Хранилище служебных сообщений для автоудаления
user_service_messages: Dict[int, List[int]] = defaultdict(list)  # user_id -> [message_id, ...]

# Хранилище обработанных сообщений для предотвращения дублирования
processed_messages: Dict[str, bool] = {}  # message_id -> processed

def initialize_voice_engines():
    """Инициализация голосовых движков"""
    global VOICE_ENGINES
    VOICE_ENGINES = {
        "gtts": {
            "name": "Google TTS",
            "description": "Стандартный качественный голос Google",
            "available": VOICE_FEATURES_AVAILABLE
        },
        # Azure Speech Services - мужские голоса
        "azure_dmitri": {
            "name": "Azure Speech - Дмитрий",
            "description": "Реалистичный мужской голос высокого качества",
            "available": VOICE_FEATURES_AVAILABLE,
            "azure_voice": "ru-RU-DmitryNeural"
        },
        "azure_artem": {
            "name": "Azure Speech - Артём",
            "description": "Естественный мужской голос",
            "available": VOICE_FEATURES_AVAILABLE,
            "azure_voice": "ru-RU-ArtemNeural"
        },
        # Azure Speech Services - женские голоса  
        "azure_svetlana": {
            "name": "Azure Speech - Светлана",
            "description": "Реалистичный женский голос высокого качества",
            "available": VOICE_FEATURES_AVAILABLE,
            "azure_voice": "ru-RU-SvetlanaNeural"
        },
        "azure_darya": {
            "name": "Azure Speech - Дарья",
            "description": "Естественный женский голос",
            "available": VOICE_FEATURES_AVAILABLE,
            "azure_voice": "ru-RU-DaryaNeural"
        },
        "azure_polina": {
            "name": "Azure Speech - Полина",
            "description": "Мягкий женский голос",
            "available": VOICE_FEATURES_AVAILABLE,
            "azure_voice": "ru-RU-PolinaNeural"
        }
    }
    
    # Обновляем дефолтные настройки голоса для новых пользователей
    global voice_engine_settings, DEFAULT_VOICE_ENGINE
    default_engine = "azure_dmitri"  # Azure Дмитрий по умолчанию
    DEFAULT_VOICE_ENGINE = default_engine
    voice_engine_settings = defaultdict(lambda: default_engine)
    
    logger.info(f"Voice engines initialized.")
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
        """Команда выбора голосового движка"""
        user_id = update.effective_user.id
        
        # Текущий выбранный движок
        current_engine = voice_engine_settings.get(user_id, DEFAULT_VOICE_ENGINE)
        current_name = VOICE_ENGINES.get(current_engine, {}).get('name', 'Неизвестный')
        
        # Проверяем доступность Azure
        azure_api_key = os.getenv('AZURE_SPEECH_KEY')
        azure_status = "✅ Настроен" if azure_api_key else "❌ Не настроен"
        
        voice_list = f"""🎵 Доступные голосовые движки:

ТЕКУЩИЙ: {current_name}

🔸 GOOGLE TTS:
/voicegtts - Google TTS (всегда доступен, быстрый)

🔸 AZURE SPEECH SERVICES ({azure_status}):
/voicedmitri - Дмитрий (мужской)
/voiceartem - Артём (мужской) 
/voicesvetlana - Светлана (женский)
/voicedarya - Дарья (женский)
/voicepolina - Полина (женский)

ℹ️ Команды также работают с подчёркиваниями:
/voice_gtts, /voice_dmitri и т.д."""

        if not azure_api_key:
            voice_list += "\n\n⚠️ Azure движки требуют настройки API ключа AZURE_SPEECH_KEY"
        
        await update.message.reply_text(voice_list)

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
        """Синтез речи из текста с поддержкой Google TTS и Azure Speech Services"""
        if not VOICE_FEATURES_AVAILABLE:
            return None
            
        try:
            # Проверка на минимальную длину текста
            if not text or len(text.strip()) < 3:
                logger.warning("Text too short for TTS")
                return None
                
            # Синтезируем полностью весь текст
            logger.info(f"Synthesizing text of {len(text)} characters")
            
            # Получаем выбранный пользователем движок
            engine = voice_engine_settings.get(user_id, DEFAULT_VOICE_ENGINE)
            logger.info(f"User {user_id} selected engine: {engine}")
            logger.debug(f"Converting text to speech with {engine}: {len(text)} characters")
            
            # Проверяем доступность движка
            engine_info = VOICE_ENGINES.get(engine)
            if engine_info:
                logger.info(f"Engine info for {engine}: name='{engine_info['name']}', available={engine_info['available']}")
            else:
                logger.warning(f"No engine info found for {engine}")
            
            if engine == "gtts":
                logger.info("Using Google TTS")
                return await self._gtts_synthesize(text, language)
            elif engine.startswith("azure_"):
                logger.info(f"Using Azure Speech Services with engine: {engine}")
                # Azure Speech Services TTS
                engine_info = VOICE_ENGINES.get(engine)
                if engine_info and "azure_voice" in engine_info:
                    azure_voice = engine_info["azure_voice"]
                    logger.info(f"Using Azure voice: {azure_voice}")
                    
                    # Проверяем API ключ Azure
                    azure_api_key = os.getenv('AZURE_SPEECH_KEY')
                    if not azure_api_key:
                        logger.warning("Azure Speech API key not configured, falling back to Google TTS")
                        return await self._gtts_synthesize(text, language)
                    
                    # Пытаемся Azure
                    azure_result = await self._azure_synthesize(text, azure_voice)
                    if azure_result:
                        return azure_result
                    else:
                        # Fallback к gTTS при ошибке Azure
                        logger.warning(f"Azure synthesis failed for {engine}, falling back to gTTS")
                        return await self._gtts_synthesize(text, language)
                else:
                    # Fallback к gTTS
                    logger.warning(f"Azure voice not configured for {engine}, falling back to gTTS")
                    return await self._gtts_synthesize(text, language)
            else:
                # Fallback к gTTS
                logger.warning(f"Engine {engine} not available or not supported, falling back to gTTS")
                logger.warning(f"Available engines: {list(VOICE_ENGINES.keys())}")
                return await self._gtts_synthesize(text, language)
                    
        except Exception as e:
            logger.error(f"Error in text-to-speech: {e}")
            # В случае любой ошибки, пытаемся gTTS
            try:
                logger.info("Attempting fallback to gTTS due to error")
                return await self._gtts_synthesize(text, language)
            except:
                return None

    async def _gtts_synthesize(self, text: str, language: str) -> Optional[bytes]:
        """Оптимизированный синтез с помощью Google TTS"""
        try:
            # Создание временного файла для аудио
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                temp_path = temp_file.name
            
            try:
                # Создание TTS объекта с оптимизацией скорости
                # slow=False делает речь быстрее
                tts = gTTS(text=text, lang=language, slow=False)
                
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

    async def _azure_synthesize(self, text: str, voice: str = "ru-RU-SvetlanaNeural") -> Optional[bytes]:
        """Синтез с помощью Azure Speech Services"""
        try:
            # Проверяем наличие API ключа Azure
            azure_api_key = os.getenv('AZURE_SPEECH_KEY')
            azure_region = os.getenv('AZURE_SPEECH_REGION', 'eastus')
            
            if not azure_api_key:
                logger.error("Azure Speech API key not found")
                return None
            
            # Определяем пол голоса по конкретному имени голоса
            # Для русских голосов Azure
            male_voices = ["ru-RU-DmitryNeural", "ru-RU-ArtemNeural"]
            female_voices = ["ru-RU-SvetlanaNeural", "ru-RU-DaryaNeural", "ru-RU-PolinaNeural"]
            
            gender = 'Male' if voice in male_voices else 'Female'
            logger.info(f"Using Azure voice {voice} with gender {gender}")
            
            # Создаем стандартный SSML для Azure Speech
            # ВАЖНО: Используем строгий формат SSML без лишних атрибутов и с правильными пространствами имен
            # Для корректной работы всех голосов (Дмитрий, Артём, Светлана, Дарья, Полина)
            ssml = f'<speak xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="http://www.w3.org/2001/mstts" version="1.0" xml:lang="ru-RU"><voice name="{voice}">{text}</voice></speak>'
            
            headers = {
                'Ocp-Apim-Subscription-Key': azure_api_key,
                'Content-Type': 'application/ssml+xml',
                'X-Microsoft-OutputFormat': 'audio-24khz-48kbitrate-mono-mp3'
            }
            
            url = f"https://{azure_region}.tts.speech.microsoft.com/cognitiveservices/v1"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=ssml.encode('utf-8'), timeout=30) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.info(f"✅ Azure Speech synthesis successful: {len(audio_data)} bytes")
                        return audio_data
                    else:
                        logger.error(f"Azure Speech API error: {response.status}")
                        error_text = await response.text()
                        logger.error(f"Error details: {error_text}")
                        return None
        
        except Exception as e:
            logger.error(f"Error in Azure Speech synthesis: {e}")
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
        """Безопасная отправка сообщений с учетом лимитов Telegram"""
        max_length = 4096  # Максимальный лимит Telegram для текстовых сообщений
        
        if len(response) <= max_length:
            # Короткое сообщение - отправляем целиком
            await update.message.reply_text(response)
        else:
            # Длинное сообщение - разбиваем на части
            parts = []
            current_part = ""
            
            # Разбиваем по предложениям
            sentences = re.split(r'(?<=[.!?])\s+', response)
            
            for sentence in sentences:
                # Если добавление предложения не превышает лимит
                if len(current_part + sentence) <= max_length:
                    current_part += sentence + " "
                else:
                    # Сохраняем текущую часть и начинаем новую
                    if current_part:
                        parts.append(current_part.strip())
                    
                    # Если само предложение очень длинное - принудительно разбиваем
                    if len(sentence) > max_length:
                        for i in range(0, len(sentence), max_length):
                            parts.append(sentence[i:i + max_length])
                        current_part = ""
                    else:
                        current_part = sentence + " "
            
            # Добавляем последнюю часть
            if current_part:
                parts.append(current_part.strip())
            
            # Отправляем части
            for i, part in enumerate(parts):
                if i == 0:
                    await update.message.reply_text(part)
                else:
                    await update.message.reply_text(f"(продолжение {i+1}/{len(parts)})\n\n{part}")
                
                # Небольшая задержка между сообщениями
                if i < len(parts) - 1:
                    await asyncio.sleep(0.5)

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
                    
                    # ДЛЯ ГОЛОСОВЫХ СООБЩЕНИЙ: весь ответ в одном файле, без разделения
                    logger.info(f"Synthesizing complete voice response: {len(clean_response)} characters")
                    voice_data = await self.text_to_speech(clean_response, user_id)
                    
                    if voice_data:
                        await self.cleanup_service_messages(update, context, user_id)
                        await update.message.reply_voice(
                            voice=BytesIO(voice_data),
                            caption=f"🎤 Голосовой ответ\n\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
                        )
                        logger.info(f"Successfully sent complete voice response to user {user_id}")
                        user_sessions[user_id].append({"role": "assistant", "content": response})
                    else:
                        # Fallback к тексту
                        await self.cleanup_service_messages(update, context, user_id)
                        await update.message.reply_text(
                            f"💬 {response}\n\n⚠️ Не удалось создать голосовой ответ\n📊 Осталось запросов: {remaining_minute}/{MINUTE_LIMIT} в минуту, {remaining_day}/{DAILY_LIMIT} сегодня"
                        )
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

    async def add_service_message(self, user_id: int, message_id: int):
        """Добавление служебного сообщения для отслеживания"""
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
    return app

async def main():
    """Основная функция"""
    global telegram_app
    
    logger.info("Starting Gemini Telegram Bot...")
    logger.info(f"TELEGRAM_TOKEN: {'✓' if TELEGRAM_TOKEN else '✗'}")
    logger.info(f"AI_API_KEY: {'✓' if AI_API_KEY else '✗'}")
    logger.info(f"NEWS_API_KEY: {'✓' if NEWS_API_KEY else '✗'}")
    logger.info(f"AZURE_SPEECH_KEY: {'✓' if os.getenv('AZURE_SPEECH_KEY') else '✗'}")
    logger.info(f"PORT: {PORT}")
    logger.info(f"RENDER environment: {'✓' if os.environ.get('RENDER') else '✗'}")
    
    if not TELEGRAM_TOKEN or not AI_API_KEY:
        logger.error("Missing required environment variables")
        return
        
    # Инициализируем голосовые движки
    initialize_voice_engines()
    logger.info("Voice engines initialized")
    
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
    # Azure Speech Services команды
    async def voice_dmitri_command(u, c): await bot.set_voice_engine_command(u, c, "azure_dmitri")
    async def voice_artem_command(u, c): await bot.set_voice_engine_command(u, c, "azure_artem")
    async def voice_svetlana_command(u, c): await bot.set_voice_engine_command(u, c, "azure_svetlana")
    async def voice_darya_command(u, c): await bot.set_voice_engine_command(u, c, "azure_darya")
    async def voice_polina_command(u, c): await bot.set_voice_engine_command(u, c, "azure_polina")
    
    # ДОБАВЛЯЕМ ОБРАБОТЧИКИ ДЛЯ КОМАНД С ПОДЧЕРКИВАНИЕМ И БЕЗ
    # Google TTS
    telegram_app.add_handler(CommandHandler("voice_gtts", voice_gtts_command))
    telegram_app.add_handler(CommandHandler("voicegtts", voice_gtts_command))  # БЕЗ подчеркивания
    
    # Azure Speech Services голоса
    # Мужские голоса
    telegram_app.add_handler(CommandHandler("voice_dmitri", voice_dmitri_command))
    telegram_app.add_handler(CommandHandler("voicedmitri", voice_dmitri_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_artem", voice_artem_command))
    telegram_app.add_handler(CommandHandler("voiceartem", voice_artem_command))  # БЕЗ подчеркивания
    
    # Женские голоса
    telegram_app.add_handler(CommandHandler("voice_svetlana", voice_svetlana_command))
    telegram_app.add_handler(CommandHandler("voicesvetlana", voice_svetlana_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_darya", voice_darya_command))
    telegram_app.add_handler(CommandHandler("voicedarya", voice_darya_command))  # БЕЗ подчеркивания
    telegram_app.add_handler(CommandHandler("voice_polina", voice_polina_command))
    telegram_app.add_handler(CommandHandler("voicepolina", voice_polina_command))  # БЕЗ подчеркивания

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
    web_server = await start_web_server()
    
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
    return web_server

if __name__ == '__main__':
    asyncio.run(main()) 