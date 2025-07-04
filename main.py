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
    
    # Пытаемся импортировать Piper TTS (но он не нужен как модуль)
    PIPER_AVAILABLE = False  # Будет определяться динамически
    
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
voice_engine_settings: Dict[int, str] = defaultdict(lambda: "gtts")  # По умолчанию gTTS

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
        }
    }

# Инициализируем движки
initialize_voice_engines()

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
        
        # Создаем список доступных движков
        available_engines = []
        for engine_id, engine_info in VOICE_ENGINES.items():
            if engine_info["available"]:
                status = "✅ (текущий)" if engine_id == current_engine else "⚡"
                available_engines.append(f"{status} {engine_info['name']}\n   {engine_info['description']}")
        
        if not available_engines:
            await update.message.reply_text("❌ Голосовые движки недоступны.")
            return
        
        message = "🎤 Доступные голосовые движки:\n\n" + "\n\n".join(available_engines)
        message += "\n\n📝 Чтобы выбрать голос, используйте:\n"
        message += "/voice_gtts - Google TTS\n"
        message += "/voice_gtts_slow - Google TTS (медленный)\n"
        if PIPER_AVAILABLE:
            message += "/voice_dmitri - Piper TTS (Дмитрий, мужской)\n"
            message += "/voice_ruslan - Piper TTS (Руслан, мужской)\n"
            message += "/voice_irina - Piper TTS (Ирина, женский)\n"
            message += "/voice_anna - Piper TTS (Анна, женский)"
        
        await update.message.reply_text(message)

    async def set_voice_engine_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, engine: str):
        """Установка голосового движка"""
        user_id = update.effective_user.id
        
        if engine not in VOICE_ENGINES:
            await update.message.reply_text("❌ Неизвестный голосовой движок.")
            return
        
        engine_info = VOICE_ENGINES[engine]
        if not engine_info["available"]:
            await update.message.reply_text(f"❌ {engine_info['name']} недоступен.")
            return
        
        voice_engine_settings[user_id] = engine
        await update.message.reply_text(
            f"✅ Голос изменен на: {engine_info['name']}\n"
            f"📝 {engine_info['description']}\n\n"
            f"🎵 Отправьте голосовое сообщение для тестирования!"
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
                
            # Ограничение длины текста
            if len(text) > 1000:
                text = text[:1000] + "..."
            
            # Получаем выбранный пользователем движок
            engine = voice_engine_settings.get(user_id, "gtts")
            logger.debug(f"Converting text to speech with {engine}: {len(text)} characters")
            
            if engine == "gtts":
                return await self._gtts_synthesize(text, language, slow=False)
            elif engine == "gtts_slow":
                return await self._gtts_synthesize(text, language, slow=True)
            elif engine.startswith("piper_") and PIPER_AVAILABLE:
                # Определяем голосовую модель из настроек движка
                engine_info = VOICE_ENGINES.get(engine)
                if engine_info and "voice_model" in engine_info:
                    voice_model = engine_info["voice_model"]
                    return await self._piper_synthesize(text, voice_model)
                else:
                    # Fallback к Дмитрию если модель не найдена
                    return await self._piper_synthesize(text, "ru_RU-dmitri-medium")
            else:
                # Fallback к gTTS
                logger.warning(f"Engine {engine} not available, falling back to gTTS")
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

    async def _piper_synthesize(self, text: str, voice_model: str = "ru_RU-dmitri-medium") -> Optional[bytes]:
        """Синтез с помощью Piper TTS (pip version)"""
        try:
            import tempfile
            import os
            import wave
            from piper.voice import PiperVoice
            
            # Определяем модель голоса
            if not voice_model:
                voice_model = "ru_RU-dmitri-medium"
            
            model_path = f"/app/piper_tts/voices/{voice_model}.onnx"
            config_path = f"/app/piper_tts/voices/{voice_model}.onnx.json"
            
            # Проверяем существование файлов модели
            if not os.path.exists(model_path) or not os.path.exists(config_path):
                logger.warning(f"Voice model {voice_model} not found, using fallback")
                # Попробуем найти любую доступную модель
                voices_dir = "/app/piper_tts/voices"
                if os.path.exists(voices_dir):
                    onnx_files = [f for f in os.listdir(voices_dir) if f.endswith('.onnx')]
                    if onnx_files:
                        fallback_model = onnx_files[0].replace('.onnx', '')
                        model_path = f"{voices_dir}/{fallback_model}.onnx"
                        config_path = f"{voices_dir}/{fallback_model}.onnx.json"
                        logger.info(f"Using fallback model: {fallback_model}")
                    else:
                        raise Exception("No voice models found")
                else:
                    raise Exception("Voices directory not found")
            
            logger.info(f"Loading Piper voice model: {model_path}")
            
            # Загружаем голосовую модель
            voice = PiperVoice.load(model_path, config_path)
            logger.info(f"Voice loaded successfully, sample rate: {voice.config.sample_rate}")
            
            # Создаем временный файл для результата
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
            
            logger.info(f"Temporary file created: {temp_path}")
            
            try:
                # Синтезируем речь используя правильный API
                logger.info(f"Starting synthesis for text: {text[:50]}...")
                
                # Синтез через генератор аудио данных
                audio_data = b""
                for audio_chunk in voice.synthesize_stream(text):
                    audio_data += audio_chunk
                
                if not audio_data:
                    raise Exception("No audio data generated")
                
                logger.info(f"Generated raw audio data: {len(audio_data)} bytes")
                
                # Создаем WAV файл с правильными параметрами
                with wave.open(temp_path, 'wb') as wav_file:
                    wav_file.setnchannels(1)  # моно
                    wav_file.setsampwidth(2)   # 16-bit
                    wav_file.setframerate(voice.config.sample_rate)
                    wav_file.writeframes(audio_data)
                
                # Проверяем что файл создан и не пустой
                if not os.path.exists(temp_path):
                    raise Exception("Output file was not created")
                
                file_size = os.path.getsize(temp_path)
                if file_size == 0:
                    raise Exception("Output file is empty")
                
                logger.info(f"Output WAV file size: {file_size} bytes")
                
                # Читаем результат из файла
                with open(temp_path, 'rb') as audio_file:
                    wav_bytes = audio_file.read()
                
                logger.info(f"Piper TTS synthesis success: generated {len(wav_bytes)} bytes")
                return wav_bytes
                
            finally:
                # Очистка временного файла
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                        logger.debug(f"Cleaned up temporary file: {temp_path}")
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup temporary file: {cleanup_error}")
            
        except Exception as e:
            logger.error(f"Piper TTS synthesis error: {e}")
            logger.exception("Full traceback:")
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

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка голосовых сообщений"""
        user_id = update.effective_user.id
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
            
            # Распознавание речи
            await update.message.reply_text("🎤 Распознаю речь...")
            transcribed_text = await self.speech_to_text(bytes(voice_bytes))
            
            if not transcribed_text:
                await update.message.reply_text("❌ Не удалось распознать речь. Попробуйте еще раз или говорите четче.")
                return
            
            logger.info(f"Voice transcribed for user {user_id}: {transcribed_text[:100]}...")
            
            # Отправка уведомления о том, что речь распознана
            await update.message.reply_text(f"✅ Распознано: \"{transcribed_text}\"")
            
            # Проверяем, нужны ли актуальные данные
            if self.needs_current_data(transcribed_text):
                await update.message.reply_text("🔍 Ищу актуальную информацию в интернете...")
                current_info = await self.get_current_data(transcribed_text)
                
                if current_info:
                    # Формируем расширенный запрос с актуальными данными
                    enhanced_message = f"""ВАЖНАЯ ИНФОРМАЦИЯ: Сегодня {datetime.now().strftime('%d.%m.%Y')} год.

Голосовой вопрос пользователя: {transcribed_text}

АКТУАЛЬНАЯ ИНФОРМАЦИЯ ИЗ ИНТЕРНЕТА:
{current_info}

Используй актуальную информацию выше для ответа. Отвечай кратко и по существу на русском языке."""
                    
                    messages = [{"role": "user", "content": enhanced_message}]
                else:
                    messages = [{"role": "user", "content": transcribed_text}]
            else:
                messages = [{"role": "user", "content": transcribed_text}]

            # Уведомление о начале обработки
            await update.message.reply_text("💭 Думаю над ответом...")
            
            logger.info(f"Calling Gemini API for voice message from user {user_id}")
            response = await self.call_gemini_api(messages)
            
            if response:
                logger.info(f"Received response from Gemini API for voice message from user {user_id}: {len(response)} characters")
                
                # Добавление запроса в счетчик
                self.add_request(user_id)
                
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
                    voice_bytes = await self.text_to_speech(clean_response, user_id, tts_language)
                    
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
                            await update.message.reply_text(f"❌ Не удалось отправить голосовой ответ, вот текст:\n\n{response}")
                    else:
                        logger.error(f"Voice synthesis failed for user {user_id}")
                        # Fallback к текстовому ответу
                        await update.message.reply_text(f"❌ Не удалось создать голосовой ответ, вот текст:\n\n{response}")
                else:
                    # Текстовый ответ если голосовые отключены
                    await update.message.reply_text(f"📝 {response}\n\n💡 Включить голосовые ответы: /voice")
            else:
                logger.error(f"No response received from Gemini API for voice message from user {user_id}")
                await update.message.reply_text("❌ Произошла ошибка при обращении к AI. Попробуйте позже.")
                
        except Exception as e:
            logger.error(f"Error handling voice message from user {user_id}: {e}")
            await update.message.reply_text("❌ Произошла ошибка при обработке голосового сообщения.")

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

def setup_piper_if_needed():
    """Устанавливает Piper TTS если не установлен"""
    global PIPER_AVAILABLE
    
    # Проверяем доступность piper через pip
    piper_installed = False
    try:
        import piper.voice
        logger.info("Piper TTS already available")
        piper_installed = True
        PIPER_AVAILABLE = True
    except ImportError:
        logger.info("Piper TTS not found, will install...")
        PIPER_AVAILABLE = False
    
    # Проверяем наличие голосовых моделей
    voices_dir = "/app/piper_tts/voices"
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
    if not piper_installed or not models_exist:
        logger.info("Running Piper TTS installation script...")
        result = subprocess.run(['bash', 'install_piper.sh'], 
                              capture_output=True, text=True, cwd='/app')
        
        if result.returncode == 0:
            logger.info("Piper TTS installation script completed successfully")
            logger.info(f"Installation stdout: {result.stdout}")
            
            # Проверяем установку Piper если он не был установлен
            if not piper_installed:
                try:
                    import piper.voice
                    PIPER_AVAILABLE = True
                    logger.info("Piper TTS installed and imported successfully")
                except ImportError:
                    logger.error("Piper TTS import failed after installation")
                    PIPER_AVAILABLE = False
                    return False
            
            # Проверяем наличие голосовых моделей
            if os.path.exists(voices_dir):
                onnx_files = [f for f in os.listdir(voices_dir) if f.endswith('.onnx')]
                logger.info(f"Voice models after installation: {len(onnx_files)} found")
                if len(onnx_files) > 0:
                    logger.info(f"Available models: {onnx_files}")
                    return True
                else:
                    logger.warning("No voice models found after installation")
                    return False
            else:
                logger.error("Voices directory still not found after installation")
                return False
        else:
            logger.error(f"Piper TTS installation failed: {result.stderr}")
            logger.error(f"Installation stdout: {result.stdout}")
            PIPER_AVAILABLE = False
            return False
    
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
    telegram_app.add_handler(CommandHandler("voice_gtts", lambda u, c: bot.set_voice_engine_command(u, c, "gtts")))
    telegram_app.add_handler(CommandHandler("voice_gtts_slow", lambda u, c: bot.set_voice_engine_command(u, c, "gtts_slow")))
    telegram_app.add_handler(CommandHandler("voice_dmitri", lambda u, c: bot.set_voice_engine_command(u, c, "piper_dmitri")))
    telegram_app.add_handler(CommandHandler("voice_ruslan", lambda u, c: bot.set_voice_engine_command(u, c, "piper_ruslan")))
    telegram_app.add_handler(CommandHandler("voice_irina", lambda u, c: bot.set_voice_engine_command(u, c, "piper_irina")))
    telegram_app.add_handler(CommandHandler("voice_anna", lambda u, c: bot.set_voice_engine_command(u, c, "piper_anna")))
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