#!/bin/bash

echo "🔧 Installing Piper TTS..."

# Определяем архитектуру
ARCH=$(uname -m)
if [[ "$ARCH" == "x86_64" ]]; then
    PIPER_ARCH="amd64"
elif [[ "$ARCH" == "aarch64" ]]; then
    PIPER_ARCH="arm64"
else
    PIPER_ARCH="amd64"  # fallback
fi

echo "📱 Detected architecture: $ARCH -> $PIPER_ARCH"

# Создаем директории
mkdir -p piper_tts
mkdir -p piper_voices

# Проверяем, не установлен ли уже Piper
if [ ! -f "piper_tts/piper/piper" ]; then
    echo "📦 Downloading Piper TTS..."
    
    # Загружаем Piper
    PIPER_URL="https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_${PIPER_ARCH}.tar.gz"
    wget -q "$PIPER_URL" -O "piper_linux_${PIPER_ARCH}.tar.gz"
    
    if [ $? -eq 0 ]; then
        echo "📂 Extracting Piper TTS..."
        tar -xzf "piper_linux_${PIPER_ARCH}.tar.gz" -C piper_tts
        
        # Делаем исполняемым
        chmod +x piper_tts/piper/piper
        
        # Удаляем архив
        rm "piper_linux_${PIPER_ARCH}.tar.gz"
        
        echo "✅ Piper TTS installed successfully!"
    else
        echo "❌ Failed to download Piper TTS"
        exit 1
    fi
else
    echo "✅ Piper TTS already installed"
fi

# Загружаем русские голоса
echo "🎤 Downloading Russian voices..."

# Голос Дмитрий
if [ ! -f "piper_voices/ru_RU-dmitri-medium.onnx" ]; then
    echo "📥 Downloading Dmitri voice..."
    wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx" -O "piper_voices/ru_RU-dmitri-medium.onnx"
    wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json" -O "piper_voices/ru_RU-dmitri-medium.onnx.json"
fi

# Голос Руслан  
if [ ! -f "piper_voices/ru_RU-ruslan-medium.onnx" ]; then
    echo "📥 Downloading Ruslan voice..."
    wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx" -O "piper_voices/ru_RU-ruslan-medium.onnx"
    wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json" -O "piper_voices/ru_RU-ruslan-medium.onnx.json"
fi

echo "🧪 Testing Piper TTS..."

# Тестируем Piper
if [ -f "piper_voices/ru_RU-dmitri-medium.onnx" ]; then
    echo "Привет! Это тест Piper TTS." | ./piper_tts/piper/piper --model piper_voices/ru_RU-dmitri-medium.onnx --output_file test_piper.wav
    
    if [ -f "test_piper.wav" ]; then
        echo "✅ Piper TTS test successful!"
        rm test_piper.wav
    else
        echo "❌ Piper TTS test failed"
    fi
else
    echo "❌ No voice models found"
fi

echo "🎉 Piper TTS setup complete!" 