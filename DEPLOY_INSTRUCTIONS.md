# 🚀 Инструкция по деплою на Render.com

## ✅ Что уже готово

Проект успешно загружен в GitHub репозиторий:
**https://github.com/WarSaler/Google_gemini_bot**

## 📋 Следующие шаги

### 1. Перейти на Render.com
- Зайдите на [render.com](https://render.com)
- Войдите или зарегистрируйтесь (можно через GitHub)

### 2. Создать новый сервис
- Нажмите **"New +"** → **"Web Service"**
- Выберите **"Build and deploy from a Git repository"**
- Подключите ваш GitHub аккаунт
- Выберите репозиторий `Google_gemini_bot`

### 3. Настроить сервис
Заполните следующие поля:

**Основные настройки:**
- **Name**: `gemini-telegram-bot` (или любое другое имя)
- **Runtime**: `Python`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python main.py`

**Advanced настройки:**
- **Auto-Deploy**: `Yes` (для автоматического обновления при пуше в GitHub)

### 4. Добавить переменные окружения
В разделе **Environment Variables** добавьте:

```
TELEGRAM_TOKEN = 8119871708:AAE-RGXTzdm_oPZzSeDH2W58QDHQAT7Gio8
AI_API_KEY = AIzaSyDtsmdiQ6nY3t8g_VI-CsI5EKzP1ikbtvM
ENVIRONMENT = production
```

### 5. Деплой
- Нажмите **"Create Web Service"**
- Дождитесь завершения сборки (~5-10 минут)
- Сервис автоматически запустится

## 🎉 Готово!

После успешного деплоя:
- Бот будет работать 24/7
- Автоматические обновления при пуше в GitHub
- Keep-alive предотвратит засыпание на бесплатном плане

## 🔧 Проверка работы

1. Найдите вашего бота в Telegram: `@your_bot_name`
2. Отправьте `/start` для проверки
3. Проверьте логи в Render dashboard при необходимости

## 📞 Поддержка

Если что-то не работает:
- Проверьте логи в Render dashboard
- Убедитесь что все переменные окружения добавлены
- Проверьте что бот не заблокирован в Telegram 