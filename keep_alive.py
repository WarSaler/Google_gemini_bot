import asyncio
import os
from aiohttp import web, ClientSession
import logging

logger = logging.getLogger(__name__)

# Render использует переменную PORT, fallback на 10000 для соответствия render.yaml
PORT = int(os.getenv('PORT', 10000))
logger.info(f"Keep-alive server will use port: {PORT}")

async def health_check(request):
    """Health check endpoint для Render"""
    return web.Response(text="Bot is running! Status: Active")

async def self_ping():
    """Функция для пинга самого себя каждые 5 минут"""
    while True:
        try:
            await asyncio.sleep(300)  # 5 минут вместо 14
            async with ClientSession() as session:
                try:
                    # Пинг localhost
                    async with session.get(f'http://localhost:{PORT}/health', timeout=10) as response:
                        if response.status == 200:
                            logger.info("Self-ping successful")
                        else:
                            logger.warning(f"Self-ping returned status: {response.status}")
                except Exception as e:
                    logger.warning(f"Self-ping failed: {e}")
                    
                try:
                    # Дополнительный внешний пинг для предотвращения засыпания
                    service_url = os.getenv('RENDER_SERVICE_URL', 'https://google-gemini-bot.onrender.com')
                    async with session.get(f'{service_url}/health', timeout=15) as response:
                        if response.status == 200:
                            logger.info("External ping successful")
                        else:
                            logger.warning(f"External ping returned status: {response.status}")
                except Exception as e:
                    logger.warning(f"External ping failed: {e}")
                    
        except Exception as e:
            logger.error(f"Error in self_ping: {e}")

async def aggressive_keep_alive():
    """Дополнительная функция для более агрессивного предотвращения засыпания"""
    while True:
        try:
            await asyncio.sleep(120)  # Каждые 2 минуты
            logger.debug("Keep-alive heartbeat")
        except Exception as e:
            logger.error(f"Error in aggressive_keep_alive: {e}")

async def start_server():
    """Запуск HTTP сервера"""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"HTTP server started on port {PORT}")
    
    # Запуск задач keep-alive
    asyncio.create_task(self_ping())
    asyncio.create_task(aggressive_keep_alive())
    logger.info("Keep-alive mechanisms started (5-min ping + 2-min heartbeat)")

if __name__ == '__main__':
    asyncio.run(start_server()) 