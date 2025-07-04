#!/usr/bin/env python3
"""
Скрипт для автоматической установки и настройки Piper TTS
"""

import os
import sys
import subprocess
import urllib.request
import tarfile
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PIPER_VERSION = "1.2.0"
PIPER_DIR = "piper_tts"
VOICES_DIR = "piper_voices"

def download_file(url, filename):
    """Загрузка файла"""
    logger.info(f"Загрузка {url} -> {filename}")
    urllib.request.urlretrieve(url, filename)

def setup_piper():
    """Установка Piper TTS"""
    try:
        # Создаем директории
        os.makedirs(PIPER_DIR, exist_ok=True)
        os.makedirs(VOICES_DIR, exist_ok=True)
        
        # Определяем архитектуру
        machine = os.uname().machine.lower()
        if machine in ['x86_64', 'amd64']:
            arch = "amd64"
        elif machine in ['aarch64', 'arm64']:
            arch = "arm64"
        else:
            arch = "amd64"  # fallback
            
        logger.info(f"Detected architecture: {machine} -> {arch}")
        
        # URL для загрузки Piper
        piper_url = f"https://github.com/rhasspy/piper/releases/download/v{PIPER_VERSION}/piper_linux_{arch}.tar.gz"
        piper_archive = f"piper_linux_{arch}.tar.gz"
        
        # Загружаем и распаковываем Piper
        if not os.path.exists(f"{PIPER_DIR}/piper"):
            logger.info("Загрузка Piper TTS...")
            download_file(piper_url, piper_archive)
            
            logger.info("Распаковка Piper TTS...")
            with tarfile.open(piper_archive, 'r:gz') as tar:
                tar.extractall(PIPER_DIR)
            
            # Делаем файл исполняемым
            piper_path = f"{PIPER_DIR}/piper/piper"
            if os.path.exists(piper_path):
                os.chmod(piper_path, 0o755)
                logger.info(f"Piper установлен: {piper_path}")
            
            # Удаляем архив
            os.remove(piper_archive)
        else:
            logger.info("Piper уже установлен")
        
        # Загружаем русские голоса
        voices_to_download = [
            {
                "name": "ru_RU-dmitri-medium",
                "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx",
                "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json"
            },
            {
                "name": "ru_RU-ruslan-medium", 
                "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx",
                "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json"
            }
        ]
        
        for voice in voices_to_download:
            model_path = f"{VOICES_DIR}/{voice['name']}.onnx"
            config_path = f"{VOICES_DIR}/{voice['name']}.onnx.json"
            
            if not os.path.exists(model_path):
                logger.info(f"Загрузка голоса {voice['name']}...")
                download_file(voice['url'], model_path)
                download_file(voice['config_url'], config_path)
            else:
                logger.info(f"Голос {voice['name']} уже загружен")
        
        logger.info("Установка Piper TTS завершена!")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка установки Piper TTS: {e}")
        return False

def test_piper():
    """Тестирование Piper TTS"""
    try:
        piper_path = f"{PIPER_DIR}/piper/piper"
        if not os.path.exists(piper_path):
            logger.error("Piper не найден")
            return False
            
        # Проверяем доступные голоса
        voices = [f for f in os.listdir(VOICES_DIR) if f.endswith('.onnx')]
        if not voices:
            logger.error("Голоса не найдены")
            return False
            
        # Тестируем первый голос
        test_voice = f"{VOICES_DIR}/{voices[0]}"
        test_text = "Привет! Это тест голосового синтеза Piper."
        output_file = "test_piper.wav"
        
        cmd = f'echo "{test_text}" | {piper_path} --model {test_voice} --output_file {output_file}'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(output_file):
            logger.info("Тест Piper TTS прошел успешно!")
            os.remove(output_file)  # Удаляем тестовый файл
            return True
        else:
            logger.error(f"Тест Piper TTS не удался: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Ошибка тестирования Piper TTS: {e}")
        return False

if __name__ == "__main__":
    if setup_piper():
        test_piper()
    else:
        sys.exit(1) 