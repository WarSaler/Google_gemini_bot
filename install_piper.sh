#!/bin/bash

echo "🔧 Setting up Piper TTS..."

# Проверяем наличие pip
if ! command -v pip &> /dev/null; then
    echo "❌ pip not found"
    exit 1
fi

# Проверяем установлен ли Piper TTS
if python -c "import piper.voice; print('Piper TTS available')" 2>/dev/null; then
    echo "✅ Piper TTS already installed"
else
    echo "📦 Installing piper-tts package..."
    pip install piper-tts==1.2.0 || {
        echo "⚠️ Failed to install piper-tts 1.2.0, trying latest version..."
        pip install piper-tts || {
            echo "❌ Failed to install piper-tts"
            exit 1
        }
    }
fi

# Создаем директорию для голосовых моделей
echo "📁 Creating voice models directory..."
mkdir -p /app/piper_tts/voices

# Проверяем какие модели уже есть
existing_models=$(ls /app/piper_tts/voices/*.onnx 2>/dev/null | wc -l)
echo "📋 Found $existing_models existing voice models"

# Скачиваем русские голосовые модели
echo "🗣️ Downloading Russian voice models..."

# Дмитрий (мужской голос)
if [ ! -f "/app/piper_tts/voices/ru_RU-dmitri-medium.onnx" ]; then
    echo "⬇️ Downloading Dmitri voice model..."
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx" \
        -O "/app/piper_tts/voices/ru_RU-dmitri-medium.onnx" || echo "⚠️ Failed to download Dmitri model"
    
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json" \
        -O "/app/piper_tts/voices/ru_RU-dmitri-medium.onnx.json" || echo "⚠️ Failed to download Dmitri config"
else
    echo "✅ Dmitri model already exists"
fi

# Руслан (мужской голос)
if [ ! -f "/app/piper_tts/voices/ru_RU-ruslan-medium.onnx" ]; then
    echo "⬇️ Downloading Ruslan voice model..."
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx" \
        -O "/app/piper_tts/voices/ru_RU-ruslan-medium.onnx" || echo "⚠️ Failed to download Ruslan model"
    
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json" \
        -O "/app/piper_tts/voices/ru_RU-ruslan-medium.onnx.json" || echo "⚠️ Failed to download Ruslan config"
else
    echo "✅ Ruslan model already exists"
fi

# Ирина (женский голос)
if [ ! -f "/app/piper_tts/voices/ru_RU-irina-medium.onnx" ]; then
    echo "⬇️ Downloading Irina voice model..."
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx" \
        -O "/app/piper_tts/voices/ru_RU-irina-medium.onnx" || echo "⚠️ Failed to download Irina model"
    
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json" \
        -O "/app/piper_tts/voices/ru_RU-irina-medium.onnx.json" || echo "⚠️ Failed to download Irina config"
else
    echo "✅ Irina model already exists"
fi

# Анна (женский голос) 
if [ ! -f "/app/piper_tts/voices/ru_RU-anna-medium.onnx" ]; then
    echo "⬇️ Downloading Anna voice model..."
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/anna/medium/ru_RU-anna-medium.onnx" \
        -O "/app/piper_tts/voices/ru_RU-anna-medium.onnx" || echo "⚠️ Failed to download Anna model"
    
    wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/anna/medium/ru_RU-anna-medium.onnx.json" \
        -O "/app/piper_tts/voices/ru_RU-anna-medium.onnx.json" || echo "⚠️ Failed to download Anna config"
else
    echo "✅ Anna model already exists"
fi

# Проверяем успешность установки Piper TTS
echo "🔍 Checking piper installation..."
if python -c "import piper.voice; print('Piper TTS installed successfully')" 2>/dev/null; then
    echo "✅ Piper TTS installed successfully"
else
    echo "❌ Piper TTS installation verification failed"
    exit 1
fi

# Показываем установленные голоса
echo "📋 Final voice model count:"
voice_count=$(ls -la /app/piper_tts/voices/*.onnx 2>/dev/null | wc -l)
echo "🗣️ Found $voice_count voice models"

if [ "$voice_count" -gt 0 ]; then
    echo "📝 Available models:"
    ls -la /app/piper_tts/voices/*.onnx 2>/dev/null | awk '{print "  - " $9}' | sed 's|.*/||'
fi

echo "🎉 Piper TTS setup complete!" 