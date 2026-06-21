import logging
import re
import time
import random
import asyncio
from typing import Optional, List

import requests
import cloudscraper
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== ИМПОРТ TOR ====================
try:
    from tor_native_requests import tor_context
    TOR_AVAILABLE = True
    print("✅ Tor библиотека загружена")
except ImportError:
    TOR_AVAILABLE = False
    print("❌ Tor библиотека НЕ установлена")

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8865091811:AAEmyhomnMKsytMnatDMHQEggEQh1rZNI50"   # ← СЮДА ВСТАВЬТЕ ТОКЕН

USE_TOR = False                     # True = получать ключ через Tor
USE_PROXY_FOR_TELEGRAM = True       # True = использовать прокси для Telegram
TOR_TIMEOUT = 60                    # Таймаут Tor в секундах

# ==== СПИСОК SOCKS5 ПРОКСИ ДЛЯ TELEGRAM ====
TELEGRAM_PROXIES = [
    "socks5://45.155.68.129:1080",
    "socks5://45.79.68.157:1080",
    "socks5://107.152.131.50:1080",
    "socks5://173.249.16.190:1080",
    "socks5://188.166.197.119:1080",
    "socks5://104.248.70.114:1080",
    "socks5://159.89.207.176:1080",
    "socks5://134.209.100.157:1080",
    "socks5://167.172.249.7:1080",
    "socks5://51.38.82.190:1080",
    "socks5://45.33.84.123:1080",
    "socks5://45.33.84.124:1080",
]

# ===================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# CloudScraper для обхода CloudFlare
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def random_ua() -> str:
    """Случайный User-Agent"""
    agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
    ]
    return random.choice(agents)

def parse_key(html: str) -> Optional[str]:
    """Ищет ключ в HTML разными способами"""
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text()

    patterns = [
        r'(?:key|ключ)[:\s]+([A-Za-z0-9\-_]{10,})',
        r'(?:token|токен)[:\s]+([A-Za-z0-9\-_]{10,})',
        r'([A-Za-z0-9\-_]{20,})',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m and len(m.group(1)) >= 10:
            return m.group(1)

    for script in soup.find_all('script'):
        if script.string:
            matches = re.findall(r'["\']([A-Za-z0-9\-_]{20,})["\']', script.string)
            if matches:
                return matches[0]

    for tag in soup.find_all(attrs={"data-key": True}):
        return tag.get("data-key")
    for tag in soup.find_all(attrs={"data-token": True}):
        return tag.get("data-token")

    for meta in soup.find_all('meta'):
        content = meta.get('content', '')
        if content and len(content) >= 20 and not content.startswith('http'):
            return content

    for inp in soup.find_all('input', {'type': 'hidden'}):
        val = inp.get('value', '')
        if val and len(val) >= 20 and not val.startswith('http'):
            return val

    for a in soup.find_all('a', href=True):
        if 'key=' in a['href']:
            key = a['href'].split('key=')[1].split('&')[0]
            if key and len(key) >= 10:
                return key

    return None

async def test_proxy(proxy_url: str) -> bool:
    """Проверяет, работает ли прокси"""
    try:
        from telegram.request import HTTPXRequest
        # Пытаемся создать клиент с прокси
        request = HTTPXRequest(proxy=proxy_url, connection_pool_size=1)
        # Проверяем через короткий запрос к Telegram API
        import httpx
        async with httpx.AsyncClient(proxy=proxy_url, timeout=5) as client:
            resp = await client.get("https://api.telegram.org")
            return resp.status_code == 200
    except:
        return False

def find_working_proxy() -> Optional[str]:
    """Находит первый рабочий прокси из списка"""
    logger.info("🔍 Поиск рабочего прокси для Telegram...")
    for proxy in TELEGRAM_PROXIES:
        try:
            logger.info(f"🔄 Проверка: {proxy}")
            # Синхронная проверка (проще)
            import httpx
            with httpx.Client(proxy=proxy, timeout=5) as client:
                resp = client.get("https://api.telegram.org")
                if resp.status_code == 200:
                    logger.info(f"✅ Прокси работает: {proxy}")
                    return proxy
        except Exception as e:
            logger.info(f"❌ Прокси не работает: {proxy} - {str(e)[:50]}")
            continue
    logger.warning("⚠️ Ни один прокси не работает!")
    return None

async def get_key_via_tor(link: str):
    """Получение ключа через Tor"""
    if not TOR_AVAILABLE or not USE_TOR:
        return await get_key_direct(link)

    logger.info("🔄 Запрос через Tor...")
    try:
        headers = {
            'User-Agent': random_ua(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

        with tor_context():
            resp = requests.get(link, headers=headers, timeout=TOR_TIMEOUT, verify=False)

        if resp.status_code == 200:
            key = parse_key(resp.text)
            if key:
                logger.info(f"✅ Ключ через Tor: {key[:10]}...")
                return key, "Tor"
            if 'location' in resp.headers and 'key=' in resp.headers['location']:
                key = resp.headers['location'].split('key=')[1].split('&')[0]
                if key:
                    return key, "Tor (редирект)"
            try:
                data = resp.json()
                if 'key' in data:
                    return data['key'], "Tor (JSON)"
                if 'token' in data:
                    return data['token'], "Tor (JSON)"
            except:
                pass
            return None, "Ключ не найден (Tor)"

        elif resp.status_code == 403:
            return None, "Ошибка 403 (Tor)"
        elif resp.status_code == 404:
            return None, "Ссылка недействительна (Tor)"
        else:
            return None, f"Ошибка {resp.status_code} (Tor)"

    except requests.exceptions.Timeout:
        return None, "Таймаут Tor"
    except requests.exceptions.ConnectionError:
        return None, "Ошибка подключения Tor"
    except Exception as e:
        return None, f"Ошибка Tor: {str(e)[:80]}"

async def get_key_direct(link: str):
    """Получение ключа напрямую (без Tor)"""
    logger.info("🔄 Прямой запрос...")
    try:
        headers = {
            'User-Agent': random_ua(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

        resp = scraper.get(link, headers=headers, timeout=30, verify=False)

        if resp.status_code == 200:
            key = parse_key(resp.text)
            if key:
                logger.info(f"✅ Ключ напрямую: {key[:10]}...")
                return key, "Прямое"
            if 'location' in resp.headers and 'key=' in resp.headers['location']:
                key = resp.headers['location'].split('key=')[1].split('&')[0]
                if key:
                    return key, "Прямое (редирект)"
            try:
                data = resp.json()
                if 'key' in data:
                    return data['key'], "Прямое (JSON)"
                if 'token' in data:
                    return data['token'], "Прямое (JSON)"
            except:
                pass
            return None, "Ключ не найден"

        elif resp.status_code == 403:
            return None, "Ошибка 403"
        elif resp.status_code == 404:
            return None, "Ссылка недействительна"
        else:
            return None, f"Ошибка {resp.status_code}"

    except requests.exceptions.Timeout:
        return None, "Таймаут"
    except requests.exceptions.ConnectionError:
        return None, "Ошибка подключения"
    except Exception as e:
        return None, f"Ошибка: {str(e)[:80]}"

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    status = "✅ Доступен" if (USE_TOR and TOR_AVAILABLE) else "❌ Отключен"
    await update.message.reply_text(
        f"🤖 *Delta Key Bot*\n\n"
        f"📌 Отправьте ссылку вида:\n"
        f"`https://auth.platorelay.com/a?d=...`\n\n"
        f"🛡️ Tor: {status}\n"
        f"⚙️ Бот сам обойдет блокировки",
        parse_mode='Markdown'
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 *Помощь*\n\n"
        "1. В Delta Injector нажмите 'Получить ключ'\n"
        "2. Скопируйте ссылку из браузера\n"
        "3. Отправьте ссылку сюда\n"
        "4. Получите ключ!",
        parse_mode='Markdown'
    )

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tor_status = "🟢 Доступен" if (USE_TOR and TOR_AVAILABLE) else "🔴 Отключен"
    proxy_status = "🟢 Включен" if USE_PROXY_FOR_TELEGRAM else "🔴 Отключен"
    await update.message.reply_text(
        f"📊 *Статус*\n\n"
        f"🛡️ Tor: {tor_status}\n"
        f"🌐 Режим получения ключа: {'Tor' if USE_TOR else 'Прямой'}\n"
        f"🔌 Прокси для Telegram: {proxy_status}\n"
        f"⏱️ Таймаут Tor: {TOR_TIMEOUT} сек",
        parse_mode='Markdown'
    )

async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()

    if not re.match(r'https?://auth\.platorelay\.com/', link):
        await update.message.reply_text(
            "❌ Неверная ссылка. Должна начинаться с:\n"
            "`https://auth.platorelay.com/`",
            parse_mode='Markdown'
        )
        return

    msg = await update.message.reply_text(
        "⏳ *Обработка...*" + ("\n🛡️ Использую Tor" if USE_TOR and TOR_AVAILABLE else ""),
        parse_mode='Markdown'
    )

    if USE_TOR and TOR_AVAILABLE:
        key, method = await get_key_via_tor(link)
        if key:
            await msg.edit_text(
                f"✅ *Ключ получен!*\n\n"
                f"`{key}`\n\n"
                f"🔑 Действителен 24 часа\n"
                f"🔄 {method}",
                parse_mode='Markdown'
            )
            return

        await msg.edit_text("⚠️ Tor не сработал, пробую напрямую...")
        key, method = await get_key_direct(link)

        if key:
            await msg.edit_text(
                f"✅ *Ключ получен!*\n\n"
                f"`{key}`\n\n"
                f"🔑 Действителен 24 часа\n"
                f"🔄 {method} (запасной)",
                parse_mode='Markdown'
            )
            return

    else:
        key, method = await get_key_direct(link)
        if key:
            await msg.edit_text(
                f"✅ *Ключ получен!*\n\n"
                f"`{key}`\n\n"
                f"🔑 Действителен 24 часа\n"
                f"🔄 {method}",
                parse_mode='Markdown'
            )
            return

    await msg.edit_text(
        "❌ *Не удалось получить ключ*\n\n"
        "💡 Проверьте:\n"
        "• Ссылка активна (сгенерируйте новую)\n"
        "• Ссылка должна быть свежей (не старше 5 минут)\n"
        "• Попробуйте через 1-2 минуты",
        parse_mode='Markdown'
    )

async def error_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {ctx.error}")
    if update and update.message:
        await update.message.reply_text("⚠️ Ошибка. Попробуйте позже.")

# ==================== ЗАПУСК ====================

def main():
    if USE_TOR and TOR_AVAILABLE:
        logger.info("🛡️ Бот использует Tor для получения ключей")
    else:
        logger.info("🌐 Бот работает напрямую (без Tor)")

    # Находим рабочий прокси для Telegram
    working_proxy = None
    if USE_PROXY_FOR_TELEGRAM:
        working_proxy = find_working_proxy()

    if working_proxy:
        try:
            from telegram.request import HTTPXRequest
            request = HTTPXRequest(proxy=working_proxy, connection_pool_size=8)
            app = Application.builder().token(BOT_TOKEN).request(request).build()
            logger.info(f"🔄 Используется прокси для Telegram: {working_proxy}")
        except Exception as e:
            logger.error(f"❌ Ошибка настройки прокси: {e}")
            logger.info("🌐 Пробую подключиться напрямую...")
            app = Application.builder().token(BOT_TOKEN).build()
    else:
        app = Application.builder().token(BOT_TOKEN).build()
        logger.info("🌐 Telegram подключение напрямую (без прокси)")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_error_handler(error_handler)

    logger.info("🚀 Бот запущен! Ожидаю сообщения...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
