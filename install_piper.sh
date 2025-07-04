#!/bin/bash

echo "🔧 Setting up Piper TTS..."

# Проверяем наличие pip
if ! command -v pip &> /dev/null; then
    echo "❌ pip not found"
    exit 1
fi

# Проверяем установлен ли Piper TTS через pip
if python -c "import piper.voice; print('Piper TTS available')" 2>/dev/null; then
    echo "✅ Piper TTS Python package already installed"
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

# Скачиваем исполняемый файл Piper TTS
echo "📥 Downloading Piper TTS executable..."

# Определяем архитектуру
ARCH=$(uname -m)
case $ARCH in
    x86_64)
        PIPER_ARCH="amd64"
        ;;
    aarch64)
        PIPER_ARCH="arm64"
        ;;
    armv7l)
        PIPER_ARCH="armv7"
        ;;
    *)
        echo "❌ Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

# URL для скачивания последней версии Piper
PIPER_VERSION="2023.11.14-2"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_${PIPER_ARCH}.tar.gz"

echo "🔗 Downloading from: $PIPER_URL"

# Создаем директорию для исполняемого файла
mkdir -p /app/piper_bin

# Скачиваем и извлекаем Piper
cd /app/piper_bin
if wget -T 30 -q --show-progress "$PIPER_URL" -O piper.tar.gz; then
    echo "✅ Downloaded Piper executable"
    if tar -xzf piper.tar.gz; then
        echo "✅ Extracted Piper executable"
        # Найдем исполняемый файл piper
        find . -name "piper" -type f -exec chmod +x {} \;
        find . -name "piper" -type f -exec cp {} /usr/local/bin/piper \;
        echo "✅ Piper executable installed to /usr/local/bin/piper"
        rm -f piper.tar.gz
    else
        echo "❌ Failed to extract Piper executable"
    fi
else
    echo "⚠️ Failed to download Piper executable, will try to use pip version"
fi

cd /app

# Создаем директорию для голосовых моделей
echo "📁 Creating voice models directory..."
mkdir -p /app/piper_tts/voices

# Проверяем какие модели уже есть
existing_models=$(ls /app/piper_tts/voices/*.onnx 2>/dev/null | wc -l)
echo "📋 Found $existing_models existing voice models"

# Скачиваем русские голосовые модели
echo "🗣️ Downloading Russian voice models..."

# Массив с именами моделей
declare -a models=("ru_RU-dmitri-medium" "ru_RU-ruslan-medium" "ru_RU-irina-medium" "ru_RU-anna-medium")

for model in "${models[@]}"
do
    onnx_file="/app/piper_tts/voices/${model}.onnx"
    json_file="/app/piper_tts/voices/${model}.onnx.json"
    
    if [[ ! -f "$onnx_file" ]]; then
        echo "⬇️ Downloading $model voice model..."
        wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/${model}/${model}.onnx" -O "$onnx_file" || {
            echo "⚠️ Failed to download $model model"
        }
    else
        echo "✅ $model.onnx already exists"
    fi
    
    if [[ ! -f "$json_file" ]]; then
        echo "⬇️ Downloading $model config..."
        wget -T 30 -q --show-progress "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/${model}/${model}.onnx.json" -O "$json_file" || {
            echo "⚠️ Failed to download $model config"
        }
    else
        echo "✅ $model.onnx.json already exists"
    fi
done

# Проверяем установку
echo "🔍 Checking installation..."

# Проверяем исполняемый файл piper
if command -v piper &> /dev/null; then
    echo "✅ Piper executable available"
    piper --help | head -n 3 || echo "⚠️ Piper executable found but may have issues"
else
    echo "⚠️ Piper executable not found in PATH"
fi

# Проверяем Python пакет
if python -c "import piper.voice; print('Piper Python package available')" 2>/dev/null; then
    echo "✅ Piper Python package available"
else
    echo "⚠️ Piper Python package not available"
fi

echo "✅ Piper TTS installed successfully"

# Подсчитываем финальное количество моделей
final_models=$(ls /app/piper_tts/voices/*.onnx 2>/dev/null | wc -l)
echo "📋 Final voice model count:"
echo "🗣️ Found $final_models voice models"

if [ "$final_models" -gt 0 ]; then
    echo "📝 Available models:"
    ls /app/piper_tts/voices/*.onnx 2>/dev/null | xargs -n 1 basename
fi

echo "🎉 Piper TTS setup complete!" 