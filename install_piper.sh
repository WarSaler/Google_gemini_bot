#!/bin/bash

# Скрипт установки Piper TTS
echo "🔧 Настройка Piper TTS..."

# Не устанавливаем Python пакет piper-tts - он несовместим
# Используем только исполняемый файл из релизов GitHub

# Создаем директории
mkdir -p piper_tts/bin
mkdir -p piper_tts/voices

echo "📥 Скачивание исполняемого файла Piper TTS..."

# Правильный URL для последней версии исполняемого файла Linux amd64
PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"

echo "🔗 Скачивание с: $PIPER_URL"
if wget --timeout=30 --tries=3 -O piper_linux_x86_64.tar.gz "$PIPER_URL"; then
    echo "✅ Исполняемый файл Piper скачан успешно"
    
    # Распаковываем
    if tar -xzf piper_linux_x86_64.tar.gz -C piper_tts/bin/; then
        echo "✅ Архив распакован"
        # Делаем исполняемым
        chmod +x piper_tts/bin/piper/piper
        echo "✅ Права доступа установлены"
    else
        echo "⚠️ Ошибка распаковки архива"
    fi
    
    # Удаляем архив
    rm -f piper_linux_x86_64.tar.gz
else
    echo "⚠️ Не удалось скачать исполняемый файл Piper"
fi

echo "📁 Создание директории для голосовых моделей..."
mkdir -p piper_tts/voices

# Подсчитываем существующие модели
EXISTING_MODELS=$(find piper_tts/voices -name "*.onnx" 2>/dev/null | wc -l)
echo "📋 Найдено $EXISTING_MODELS существующих голосовых моделей"

echo "🗣️ Скачивание русских голосовых моделей с Hugging Face..."

# Массив для моделей (имя_модели)
declare -a MODELS=(
    "ru_RU-dmitri-medium"
    "ru_RU-ruslan-medium" 
    "ru_RU-irina-medium"
    "ru_RU-anna-medium"
)

# Базовый URL для моделей на Hugging Face (версия v1.0.0)
BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

# Функция скачивания модели
download_model() {
    local model_name="$1"
    local voice_path
    local onnx_url
    local json_url
    
    # Определяем пути для разных моделей
    case "$model_name" in
        "ru_RU-dmitri-medium")
            voice_path="ru/ru_RU/dmitri/medium"
            ;;
        "ru_RU-ruslan-medium")
            voice_path="ru/ru_RU/ruslan/medium"
            ;;
        "ru_RU-irina-medium")
            voice_path="ru/ru_RU/irina/medium"
            ;;
        "ru_RU-anna-medium")
            voice_path="ru/ru_RU/anna/medium"
            ;;
        *)
            echo "⚠️ Неизвестная модель: $model_name"
            return 1
            ;;
    esac
    
    onnx_url="${BASE_URL}/${voice_path}/${model_name}.onnx"
    json_url="${BASE_URL}/${voice_path}/${model_name}.onnx.json"
    
    echo "⬇️ Скачивание модели $model_name..."
    
    # Скачиваем .onnx файл
    if wget --timeout=60 --tries=2 -O "piper_tts/voices/${model_name}.onnx" "$onnx_url"; then
        echo "✅ Модель ${model_name}.onnx скачана"
    else
        echo "⚠️ Не удалось скачать ${model_name}.onnx"
        return 1
    fi
    
    # Скачиваем .onnx.json файл конфигурации  
    if wget --timeout=60 --tries=2 -O "piper_tts/voices/${model_name}.onnx.json" "$json_url"; then
        echo "✅ Конфигурация ${model_name}.onnx.json скачана"
    else
        echo "⚠️ Не удалось скачать ${model_name}.onnx.json"
        # Удаляем .onnx файл если не удалось скачать конфигурацию
        rm -f "piper_tts/voices/${model_name}.onnx"
        return 1
    fi
    
    return 0
}

# Скачиваем все модели
for model in "${MODELS[@]}"; do
    download_model "$model"
    sleep 1  # Небольшая пауза между запросами
done

echo "🔍 Проверка установки..."

# Проверяем исполняемый файл
if [ -x "piper_tts/bin/piper/piper" ]; then
    echo "✅ Исполняемый файл Piper найден"
else
    echo "⚠️ Исполняемый файл Piper не найден"
fi

# Подсчитываем финальное количество моделей
FINAL_MODELS=$(find piper_tts/voices -name "*.onnx" 2>/dev/null | wc -l)
echo "📋 Итоговое количество голосовых моделей: $FINAL_MODELS"

echo "📝 Доступные модели:"
find piper_tts/voices -name "*.onnx" -exec basename {} \; 2>/dev/null | sort

echo "🎉 Установка Piper TTS завершена!" 