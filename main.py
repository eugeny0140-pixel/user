# bot.py
import os
import re
import time
import logging
import requests
import json
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator, MyMemoryTranslator
import schedule
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import html
import traceback
from db_supabase import is_seen, mark_seen

# ================== НАСТРОЙКИ ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@time_n_John")
DATABASE_URL = os.getenv("DATABASE_URL", "")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан")

# Источники RSS
RSS_SOURCES = [
    {"name": "Atlantic Council", "url": "https://www.atlanticcouncil.org/feed/"},
    {"name": "Chatham House", "url": "https://www.chathamhouse.org/rss.xml"},
    {"name": "RAND Corporation", "url": "https://www.rand.org/rss.xml"},
    {"name": "E3G", "url": "https://www.e3g.org/feed/"},
    {"name": "Foreign Affairs", "url": "https://www.foreignaffairs.com/rss.xml"},
    {"name": "CFR", "url": "https://www.cfr.org/rss/"},
    {"name": "The Economist", "url": "https://www.economist.com/latest/rss.xml"},
    {"name": "Bloomberg Politics", "url": "https://www.bloomberg.com/politics/feeds/site.xml"},
]

# HTML-источники
HTML_SOURCES = [
    {"n": "Good Judgment", "u": "https://goodjudgment.com/open-questions/", "s": ".question-title a", "b": "https://goodjudgment.com"},
    {"n": "Johns Hopkins", "u": "https://centerforhealthsecurity.org/news/", "s": "h3.post-title a", "b": "https://centerforhealthsecurity.org"},
    {"n": "Metaculus", "u": "https://www.metaculus.com/news/", "s": "h2.article-title a", "b": "https://www.metaculus.com"},
    {"n": "RAND Pubs", "u": "https://www.rand.org/pubs.html", "s": "h3.pub-title a", "b": "https://www.rand.org"},
    {"n": "WEF", "u": "https://www.weforum.org/agenda/", "s": "h3[data-module='article-title'] a", "b": "https://www.weforum.org"},
    {"n": "CSIS", "u": "https://www.csis.org/analysis", "s": "h3.field--name-title a", "b": "https://www.csis.org"},
    {"n": "Economist", "u": "https://www.economist.com", "s": "h3.teaser__headline a", "b": "https://www.economist.com"},
    {"n": "Bloomberg", "u": "https://www.bloomberg.com", "s": "h3.storyItem__headline a", "b": "https://www.bloomberg.com"},
    {"n": "Carnegie", "u": "https://carnegieendowment.org/publications", "s": "h3.pub-title a", "b": "https://carnegieendowment.org"},
    {"n": "Bruegel", "u": "https://www.bruegel.org/publications", "s": "h3.publication-title a", "b": "https://www.bruegel.org"},
]

# Ключевые слова
KEYWORDS = [
    r"\brussia\b", r"\brussian\b", r"\bputin\b", r"\bukraine\b", r"\bzelensky\b",
    r"\bmoscow\b", r"\bkyiv\b", r"\bsanction[s]?\b", r"\bnato\b", r"\bwar\b",
    r"\bmilitary\b", r"\bkremlin\b", r"\bcrimea\b", r"\bdrone\b", r"\bnuclear\b",
    r"\bgas\b", r"\boil\b", r"\beuropa\b", r"\bgermany\b", r"\bfrance\b",
    r"\busa\b", r"\buk\b", r"\bbritain\b", r"\bpoland\b", r"\bestonia\b",
    r"\blatvia\b", r"\blithuania\b", r"\bchinese\b", r"\bchina\b", r"\bxi\b",
    r"\bepidemic\b", r"\bpandemic\b", r"\bvirus\b", r"\bvaccine\b", r"\bai\b",
    r"\bartificial intelligence\b", r"\bcrypto\b", r"\bbitcoin\b", r"\beth\b",
    r"\bclimate\b", r"\bglobal warming\b", r"\bextremism\b", r"\bterrorism\b"
]

MAX_PER_RUN = 10
CHECK_INTERVAL_MINUTES = 14

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def clean_text(text):
    """Очистка текста от лишних пробелов и специальных символов"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()

def translate_text(text):
    """Перевод текста с fallback на разные сервисы"""
    if not text or len(text.strip()) < 5:
        return text
        
    try:
        # Используем Google Translator
        translated = GoogleTranslator(source='auto', target='ru').translate(text[:2000])
        return translated
    except Exception as e1:
        log.warning(f"Google Translate failed: {e1}")
        try:
            # Fallback на MyMemory Translator
            translated = MyMemoryTranslator(source='en', target='ru').translate(text[:2000])
            return translated
        except Exception as e2:
            log.warning(f"MyMemoryTranslator failed: {e2}")
            # Если оба переводчика не работают, возвращаем оригинальный текст
            return text

def get_source_prefix(name):
    """Получение короткого названия источника"""
    prefixes = {
        "Atlantic Council": "ATLANTICCOUNCIL",
        "Chatham House": "CHATHAMHOUSE",
        "RAND Corporation": "RAND",
        "RAND Pubs": "RAND",
        "E3G": "E3G",
        "Foreign Affairs": "FOREIGNAFFAIRS",
        "CFR": "CFR",
        "The Economist": "ECONOMIST",
        "Bloomberg": "BLOOMBERG",
        "Bloomberg Politics": "BLOOMBERG",
        "WEF": "WEF",
        "CSIS": "CSIS",
        "Good Judgment": "GOODJUDGMENT",
        "Johns Hopkins": "JHUCSSE",
        "Metaculus": "METACULUS",
        "Carnegie": "CARNEGIE",
        "Bruegel": "BRUEGEL",
        "Economist": "ECONOMIST"
    }
    return prefixes.get(name, name.upper())

def parse_rss_feed(url):
    """Парсинг RSS-ленты через BeautifulSoup"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            log.error(f"❌ Ошибка загрузки RSS {url}: статус {response.status_code}")
            return []
        
        # Парсим как XML
        soup = BeautifulSoup(response.content, "xml")
        
        # Ищем элементы <item> или <entry>
        items = soup.find_all("item") or soup.find_all("entry")
        return items
    except Exception as e:
        log.error(f"❌ Ошибка парсинга RSS {url}: {e}")
        return []

def extract_lead_from_html(html_content):
    """Извлечение лид-текста из HTML"""
    if not html_content:
        return ""
    
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Удаляем ненужные элементы
        for elem in soup(["script", "style", "nav", "footer", "header", "aside"]):
            elem.decompose()
        
        # Ищем первый абзац
        first_p = soup.find("p")
        if first_p:
            return clean_text(first_p.get_text())
        
        # Если нет абзацев, берем весь текст и делим на предложения
        text = clean_text(soup.get_text())
        if not text:
            return ""
        
        # Делим на предложения и берем первые два
        sentences = re.split(r'(?<=[.!?])\s+', text)
        lead = " ".join(sentences[:2]).strip()
        return lead + "..." if len(sentences) > 2 else lead
    except Exception as e:
        log.error(f"❌ Ошибка извлечения лид-текста: {e}")
        return ""

def fetch_news():
    """Сбор новостей из RSS и HTML источников"""
    result = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    # === 1. Обработка RSS-источников ===
    for src in RSS_SOURCES:
        if len(result) >= MAX_PER_RUN:
            break
            
        try:
            log.info(f"📡 RSS {src['name']}: {src['url']}")
            items = parse_rss_feed(src["url"].strip())
            
            for item in items[:10]:
                if len(result) >= MAX_PER_RUN:
                    break
                
                # Извлекаем заголовок и ссылку
                title = clean_text(item.title.get_text() if item.title else "")
                link = clean_text(item.link.get_text() if item.link else (item.guid.get_text() if item.guid else ""))
                
                if not title or not link:
                    continue
                
                # Проверяем на дубликаты
                if is_seen(link, title):
                    continue
                
                # Фильтрация по ключевым словам
                if not any(re.search(kw, title, re.IGNORECASE) for kw in KEYWORDS):
                    continue
                
                # Извлекаем лид-текст
                desc = clean_text(item.description.get_text() if item.description else "")
                content = clean_text(item.content.get_text() if hasattr(item, "content") and item.content else "")
                lead = desc or content or title
                
                # Ограничиваем лид двумя предложениями
                sentences = re.split(r'(?<=[.!?])\s+', lead)
                if len(sentences) > 2:
                    lead = ' '.join(sentences[:2]).rstrip() + "…"
                else:
                    lead = lead.rstrip()
                
                # Переводим на русский
                ru_title = translate_text(title)
                ru_lead = translate_text(lead)
                
                # Формируем сообщение
                prefix = get_source_prefix(src["name"])
                safe_prefix = html.escape(prefix)
                safe_title = html.escape(ru_title)
                safe_lead = html.escape(ru_lead)
                safe_link = html.escape(link)
                
                msg = f"<b>{safe_prefix}</b>: {safe_title}\n\n{safe_lead}\n\nИсточник: {safe_link}"
                result.append({"msg": msg, "link": link, "title": title})
                log.info(f"✅ Найдена релевантная новость из RSS {src['name']}: {title[:50]}...")
                
        except Exception as e:
            log.error(f"❌ Ошибка обработки RSS {src['name']}: {e}")
            log.error(traceback.format_exc())
    
    # === 2. Обработка HTML-источников ===
    for src in HTML_SOURCES:
        if len(result) >= MAX_PER_RUN:
            break
            
        try:
            base_url = src["b"].rstrip("/")
            page_url = src["u"].strip()
            selector = src["s"]
            log.info(f"🌐 HTML {src['n']}: {page_url}")
            
            response = requests.get(page_url, headers=headers, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            items = soup.select(selector)
            
            for item in items[:10]:
                if len(result) >= MAX_PER_RUN:
                    break
                
                a_tag = item if item.name == 'a' else item.find('a')
                if not a_tag or not a_tag.get_text(strip=True):
                    continue
                
                link = a_tag.get('href', '').strip()
                title = clean_text(a_tag.get_text())
                
                # Нормализуем URL
                if link.startswith('/'):
                    link = base_url + link
                elif not link.startswith('http'):
                    continue
                
                if not title:
                    continue
                
                # Проверяем на дубликаты
                if is_seen(link, title):
                    continue
                
                # Фильтрация по ключевым словам
                if not any(re.search(kw, title, re.IGNORECASE) for kw in KEYWORDS):
                    continue
                
                # Для HTML-источников используем заголовок как лид
                ru_title = translate_text(title)
                ru_lead = ru_title
                
                # Формируем сообщение
                safe_prefix = html.escape(src["n"])
                safe_title = html.escape(ru_title)
                safe_lead = html.escape(ru_lead)
                safe_link = html.escape(link)
                
                msg = f"<b>{safe_prefix}</b>: {safe_title}\n\n{safe_lead}\n\nИсточник: {safe_link}"
                result.append({"msg": msg, "link": link, "title": title})
                log.info(f"✅ Найдена релевантная новость из HTML {src['n']}: {title[:50]}...")
                
        except Exception as e:
            log.error(f"❌ Ошибка обработки HTML {src['n']}: {e}")
            log.error(traceback.format_exc())
    
    return result

def send_to_telegram(text):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    
    try:
        log.info("📤 Отправка сообщения в Telegram...")
        response = requests.post(url, data=payload, timeout=15)
        
        if response.status_code == 200:
            log.info("✅ Сообщение успешно отправлено")
            return True
        else:
            log.error(f"❌ Ошибка Telegram ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        log.error(f"❌ Исключение при отправке в Telegram: {e}")
        return False

def job_main():
    """Основная задача проверки новостей"""
    try:
        log.info("🔄 Запуск основной проверки новостей...")
        news = fetch_news()
        
        if not news:
            log.info("📭 Нет релевантных новостей для отправки")
            return
        
        log.info(f"📬 Найдено {len(news)} релевантных новостей")
        
        for i, item in enumerate(news, 1):
            log.info(f"📨 Отправка новости {i}/{len(news)}: {item['title'][:50]}...")
            if send_to_telegram(item["msg"]):
                mark_seen(item["link"], item["title"])
                log.info(f"✅ Новость успешно отправлена и сохранена")
            time.sleep(2)  # Задержка между отправками
            
    except Exception as e:
        log.error("🚨 Критическая ошибка в job_main")
        log.error(traceback.format_exc())

def job_keepalive():
    """Фоновая задача для keep-alive"""
    log.info("💤 Keep-alive check")

# ================== HTTP сервер для Render ==================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK - Bot is alive")
    
    def log_message(self, format, *args):
        pass

def start_http_server():
    """Запуск HTTP сервера для health checks"""
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info(f"🌐 HTTP сервер запущен на порту {port}")
    server.serve_forever()

# ================== MAIN ==================
if __name__ == "__main__":
    log.info("🚀 Запуск бота")
    
    # Запускаем HTTP сервер в фоновом режиме
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    
    # Первый запуск немедленно
    job_main()
    
    # Настройка расписания
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(job_main)
    schedule.every(10).minutes.do(job_keepalive)
    
    log.info(f"⏰ Расписание настроено: проверка каждые {CHECK_INTERVAL_MINUTES} минут")
    
    # Основной цикл
    while True:
        schedule.run_pending()
        time.sleep(1)
