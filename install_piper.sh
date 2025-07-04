#!/bin/bash

echo "🔧 Installing Piper TTS via pip..."

# Проверяем наличие pip
if ! command -v pip &> /dev/null; then
    echo "❌ pip not found"
    exit 1
fi

# Устанавливаем Piper TTS через pip
echo "📦 Installing piper-tts package..."
pip install piper-tts==1.2.0 || {
    echo "⚠️ Failed to install piper-tts 1.2.0, trying latest version..."
    pip install piper-tts || {
        echo "❌ Failed to install piper-tts"
        exit 1
    }
}

# Создаем директорию для голосовых моделей
echo "📁 Creating voice models directory..."
mkdir -p /app/piper_tts/voices

# Скачиваем русские голосовые модели
echo "🗣️ Downloading Russian voice models..."

# Дмитрий (мужской голос)
echo "⬇️ Downloading Dmitri voice model..."
wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx" \
    -O "/app/piper_tts/voices/ru_RU-dmitri-medium.onnx" || echo "⚠️ Failed to download Dmitri model"

wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json" \
    -O "/app/piper_tts/voices/ru_RU-dmitri-medium.onnx.json" || echo "⚠️ Failed to download Dmitri config"

# Руслан (мужской голос)
echo "⬇️ Downloading Ruslan voice model..."
wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx" \
    -O "/app/piper_tts/voices/ru_RU-ruslan-medium.onnx" || echo "⚠️ Failed to download Ruslan model"

wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json" \
    -O "/app/piper_tts/voices/ru_RU-ruslan-medium.onnx.json" || echo "⚠️ Failed to download Ruslan config"

# Ирина (женский голос)
echo "⬇️ Downloading Irina voice model..."
wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx" \
    -O "/app/piper_tts/voices/ru_RU-irina-medium.onnx" || echo "⚠️ Failed to download Irina model"

wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json" \
    -O "/app/piper_tts/voices/ru_RU-irina-medium.onnx.json" || echo "⚠️ Failed to download Irina config"

# Анна (женский голос) 
echo "⬇️ Downloading Anna voice model..."
wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/anna/medium/ru_RU-anna-medium.onnx" \
    -O "/app/piper_tts/voices/ru_RU-anna-medium.onnx" || echo "⚠️ Failed to download Anna model"

wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/anna/medium/ru_RU-anna-medium.onnx.json" \
    -O "/app/piper_tts/voices/ru_RU-anna-medium.onnx.json" || echo "⚠️ Failed to download Anna config"

# Проверяем успешность установки
echo "🔍 Checking piper installation..."
if python -c "import piper.voice; print('Piper TTS installed successfully')" 2>/dev/null; then
    echo "✅ Piper TTS installed successfully"
else
    echo "❌ Piper TTS installation verification failed"
    exit 1
fi

# Показываем установленные голоса
echo "📋 Installed voice models:"
ls -la /app/piper_tts/voices/*.onnx 2>/dev/null | wc -l | awk '{print "🗣️ Found " $1 " voice models"}'

echo "🎉 Piper TTS installation complete!" 