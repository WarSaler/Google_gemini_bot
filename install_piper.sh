#!/bin/bash

echo "🔧 Installing Piper TTS..."

# Проверяем необходимые команды
if ! command -v wget &> /dev/null; then
    echo "❌ wget not found, installing..."
    apt-get update && apt-get install -y wget
fi

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
mkdir -p piper_tts/voices

# Проверяем, не установлен ли уже Piper
if [ ! -f "piper_tts/piper/piper" ]; then
    echo "📦 Downloading Piper TTS..."
    
    # Загружаем Piper
    PIPER_URL="https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_linux_${PIPER_ARCH}.tar.gz"
    echo "🔗 URL: $PIPER_URL"
    
    wget -v "$PIPER_URL" -O "piper_linux_${PIPER_ARCH}.tar.gz"
    
    if [ $? -eq 0 ]; then
        echo "📂 Extracting Piper TTS..."
        ls -la "piper_linux_${PIPER_ARCH}.tar.gz"
        tar -xzf "piper_linux_${PIPER_ARCH}.tar.gz" -C piper_tts
        
        echo "📁 Contents of piper_tts after extraction:"
        ls -la piper_tts/
        
        # Найдем исполняемый файл piper
        PIPER_EXEC=$(find piper_tts -name "piper" -type f)
        if [ -n "$PIPER_EXEC" ]; then
            echo "🎯 Found piper executable at: $PIPER_EXEC"
            chmod +x "$PIPER_EXEC"
        else
            echo "❌ Piper executable not found after extraction"
            echo "📁 Full directory structure:"
            find piper_tts -type f
            exit 1
        fi
        
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

# Создаем список голосов для скачивания (мужские и женские)
declare -A VOICES=(
    ["ru_RU-dmitri-medium"]="dmitri/medium"        # Мужской голос
    ["ru_RU-ruslan-medium"]="ruslan/medium"        # Мужской голос  
    ["ru_RU-irina-medium"]="irina/medium"          # Женский голос
    ["ru_RU-anna-medium"]="anna/medium"            # Женский голос
)

for voice_name in "${!VOICES[@]}"; do
    voice_path="${VOICES[$voice_name]}"
    
    if [ ! -f "piper_tts/voices/${voice_name}.onnx" ]; then
        echo "📥 Downloading ${voice_name} voice..."
        
        # URL для голосовой модели
        model_url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/${voice_path}/${voice_name}.onnx"
        config_url="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/${voice_path}/${voice_name}.onnx.json"
        
        # Скачиваем модель и конфиг
        wget_success=true
        wget -q --timeout=60 "$model_url" -O "piper_tts/voices/${voice_name}.onnx" || wget_success=false
        wget -q --timeout=60 "$config_url" -O "piper_tts/voices/${voice_name}.onnx.json" || wget_success=false
        
        if [ "$wget_success" = true ] && [ -f "piper_tts/voices/${voice_name}.onnx" ] && [ -f "piper_tts/voices/${voice_name}.onnx.json" ]; then
            echo "✅ ${voice_name} downloaded successfully"
        else
            echo "❌ Failed to download ${voice_name}"
            # Удаляем частично скачанные файлы
            rm -f "piper_tts/voices/${voice_name}.onnx" "piper_tts/voices/${voice_name}.onnx.json"
        fi
    else
        echo "✅ ${voice_name} already exists"
    fi
done

echo "🧪 Testing Piper TTS..."

# Тестируем Piper
PIPER_EXEC=$(find piper_tts -name "piper" -type f | head -1)
if [ -n "$PIPER_EXEC" ] && [ -f "piper_tts/voices/ru_RU-dmitri-medium.onnx" ]; then
    echo "🧪 Testing Piper TTS with Dmitri voice..."
    echo "Привет! Это тест Piper TTS." | "$PIPER_EXEC" --model piper_tts/voices/ru_RU-dmitri-medium.onnx --output_file test_piper.wav
    
    if [ -f "test_piper.wav" ]; then
        echo "✅ Piper TTS test successful!"
        rm test_piper.wav
    else
        echo "❌ Piper TTS test failed"
    fi
else
    echo "❌ Piper executable or voice models not found"
    echo "📁 Available voices:"
    ls -la piper_tts/voices/ 2>/dev/null || echo "No voices directory"
fi

echo "🎉 Piper TTS setup complete!" 