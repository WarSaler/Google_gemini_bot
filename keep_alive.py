import asyncio
import os
from aiohttp import web, ClientSession
import logging

logger = logging.getLogger(__name__)

# Render использует переменную PORT, fallback на 8000 для локальной разработки
PORT = int(os.getenv('PORT', 8000))
logger.info(f"Keep-alive server will use port: {PORT}")

async def health_check(request):
    """Health check endpoint для Render"""
    return web.Response(text="Bot is running!")

async def self_ping():
    """Функция для пинга самого себя каждые 14 минут"""
    while True:
        try:
            await asyncio.sleep(840)  # 14 минут
            async with ClientSession() as session:
                try:
                    async with session.get(f'http://localhost:{PORT}/health') as response:
                        if response.status == 200:
                            logger.info("Self-ping successful")
                except Exception as e:
                    logger.warning(f"Self-ping failed: {e}")
        except Exception as e:
            logger.error(f"Error in self_ping: {e}")

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
    
    # Запуск задачи self-ping
    asyncio.create_task(self_ping())

if __name__ == '__main__':
    asyncio.run(start_server()) 