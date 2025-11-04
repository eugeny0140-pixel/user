import os
import re
import time
import logging
import json
import threading
from urllib.parse import urlparse

import feedparser
import schedule
import telegram
from flask import Flask
from deep_translator import GoogleTranslator, MyMemoryTranslator
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client, Client # <<< НОВОЕ: импорт Supabase

# --- 1. Конфигурация ---
load_dotenv()

# Учетные данные Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_IDS = [cid for cid in [os.getenv("CHANNEL_ID1"), os.getenv("CHANNEL_ID2")] if cid]

# Учетные данные Supabase (будут в Render Environment)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Порт
PORT = int(os.getenv("PORT", 10000))

# --- 2. Настройка логирования ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logging.getLogger("schedule").setLevel(logging.WARN)
logging.getLogger("urllib3").setLevel(logging.WARN)
logging.getLogger("httpx").setLevel(logging.WARN) # <<< НОВОЕ: убираем лишний лог от Supabase

# --- 3. Константы: Источники и Ключевые слова ---
# (Оставляем KEYWORDS_RAW и SOURCES как в прошлый раз)
# ... (здесь ваш ОГРОМНЫЙ список KEYWORDS_RAW) ...
KEYWORDS_RAW = [
    r"\brussia\b", r"\brussian\b", r"\bputin\b", r"\bmoscow\b", r"\bkremlin\b",
    r"\bukraine\b", r"\bukrainian\b", r"\bzelensky\b", r"\bkyiv\b", r"\bkiev\b",
    r"\bcrimea\b", r"\bdonbas\b", r"\bsanction[s]?\b", r"\bgazprom\b",
    r"\bnord\s?stream\b", r"\bwagner\b", r"\blavrov\b", r"\bshoigu\b",
    r"\bmedvedev\b", r"\bpeskov\b", r"\bnato\b", r"\beuropa\b", r"\busa\b",
    r"\bsoviet\b", r"\bussr\b", r"\bpost\W?soviet\b",
    # === СВО и Война ===
    r"\bsvo\b", r"\bспецоперация\b", r"\bspecial military operation\b",
    r"\bвойна\b", r"\bwar\b", r"\bconflict\b", r"\bконфликт\b",
    r"\bнаступление\b", r"\boffensive\b", r"\bатака\b", r"\battack\b",
    r"\bудар\b", r"\bstrike\b", r"\bобстрел\b", r"\bshelling\b",
    r"\bдрон\b", r"\bdrone\b", r"\bmissile\b", r"\bракета\b",
    r"\bэскалация\b", r"\bescalation\b", r"\bмобилизация\b", r"\bmobilization\b",
    r"\bфронт\b", r"\bfrontline\b", r"\bзахват\b", r"\bcapture\b",
    r"\bосвобождение\b", r"\bliberation\b", r"\bбой\b", r"\bbattle\b",
    r"\bпотери\b", r"\bcasualties\b", r"\bпогиб\b", r"\bkilled\b",
    r"\bранен\b", r"\binjured\b", r"\bпленный\b", r"\bprisoner of war\b",
    r"\bпереговоры\b", r"\btalks\b", r"\bперемирие\b", r"\bceasefire\b",
    r"\bсанкции\b", r"\bsanctions\b", r"\bоружие\b", r"\bweapons\b",
    r"\bпоставки\b", r"\bsupplies\b", r"\bhimars\b", r"\batacms\b",
    r"\bhour ago\b", r"\bчас назад\b", r"\bminutos atrás\b", r"\b小时前\b",
    # === Криптовалюта (топ-20 + CBDC, DeFi, регуляция) ===
    r"\bbitcoin\b", r"\bbtc\b", r"\bбиткоин\b", r"\b比特币\b",
    r"\bethereum\b", r"\beth\b", r"\bэфир\b", r"\b以太坊\b",
    r"\bbinance coin\b", r"\bbnb\b", r"\busdt\b", r"\btether\b",
    r"\bxrp\b", r"\bripple\b", r"\bcardano\b", r"\bada\b",
    r"\bsolana\b", r"\bsol\b", r"\bdoge\b", r"\bdogecoin\b",
    r"\bavalanche\b", r"\bavax\b", r"\bpolkadot\b", r"\bdot\b",
    r"\bchainlink\b", r"\blink\b", r"\btron\b", r"\btrx\b",
    r"\bcbdc\b", r"\bcentral bank digital currency\b", r"\bцифровой рубль\b",
    r"\bdigital yuan\b", r"\beuro digital\b", r"\bdefi\b", r"\bдецентрализованные финансы\b",
    r"\bnft\b", r"\bnon-fungible token\b", r"\bsec\b", r"\bцб рф\b",
    r"\bрегуляция\b", r"\bregulation\b", r"\bзапрет\b", r"\bban\b",
    r"\bмайнинг\b", r"\bmining\b", r"\bhalving\b", r"\bхалвинг\b",
    r"\bволатильность\b", r"\bvolatility\b", r"\bcrash\b", r"\bкрах\b",
    r"\b刚刚\b", r"\bدقائق مضت\b",
    # === Пандемия и болезни (включая биобезопасность) ===
    r"\bpandemic\b", r"\bпандемия\b", r"\b疫情\b", r"\bجائحة\b",
    r"\boutbreak\b", r"\bвспышка\b", r"\bэпидемия\b", r"\bepidemic\b",
    r"\bvirus\b", r"\bвирус\b", r"\bвирусы\b", r"\b变异株\b",
    r"\bvaccine\b", r"\bвакцина\b", r"\b疫苗\b", r"\bلقاح\b",
    r"\bbooster\b", r"\bбустер\b", r"\bревакцинация\b",
    r"\bquarantine\b", r"\bкарантин\b", r"\b隔离\b", r"\bحجر صحي\b",
    r"\blockdown\b", r"\bлокдаун\b", r"\b封锁\b",
    r"\bmutation\b", r"\bмутация\b", r"\b变异\b",
    r"\bstrain\b", r"\bштамм\b", r"\bomicron\b", r"\bdelta\b",
    r"\bbiosafety\b", r"\bбиобезопасность\b", r"\b生物安全\b",
    r"\blab leak\b", r"\bлабораторная утечка\b", r"\b实验室泄漏\b",
    r"\bgain of function\b", r"\bусиление функции\b",
    r"\bwho\b", r"\bвоз\b", r"\bcdc\b", r"\bроспотребнадзор\b",
    r"\binfection rate\b", r"\bзаразность\b", r"\b死亡率\b",
    r"\bhospitalization\b", r"\bгоспитализация\b",
    r"\bقبل ساعات\b", r"\b刚刚报告\b"
]
KEYWORDS = [re.compile(kw, re.IGNORECASE) for kw in KEYWORDS_RAW]
SOURCES = {
    "E3G": "https://www.e3g.org/feed/",
    "Foreign Affairs": "https://www.foreignaffairs.com/rss/topics/russia-and-former-soviet-republics/feed",
    "Reuters Inst": "https://reutersinstitute.politics.ox.ac.uk/rss.xml",
    "Bruegel": "https://www.bruegel.org/feed",
    "Chatham House": "https://www.chathamhouse.org/topics/russia-and-eurasia/rss",
    "CSIS": "https://www.csis.org/regions/russia-and-eurasia/rss.xml",
    "Atlantic Council": "https://www.atlanticcouncil.org/region/eurasia/feed/",
    "RAND": "https://www.rand.org/pubs.rss",
    "CFR": "https://www.cfr.org/rss/publication/by_region/russia-and-eurasia/rss.xml",
    "Carnegie": "https://carnegieendowment.org/rss/topic/R23",
    "The Economist": "https://www.economist.com/europe/rss.xml",
    "Bloomberg": "https://feeds.bloomberg.com/politics/rss.xml",
    "J. Hopkins CHS": "https://www.centerforhealthsecurity.org/news/rss/",
    "Metaculus": "https://www.metaculus.com/news/rss/",
    "WEF": "https://www.weforum.org/feed",
    "BBC Future": "https://www.bbc.com/feed/future",
    "Future Timeline": "https://www.futuretimeline.net/blog/rss.xml",
}


# --- 4. Вспомогательные функции ---

# УДАЛЕНЫ: load_processed_guids() и save_processed_guids()

def load_processed_guids_from_db(supabase: Client):
    """ <<< НОВОЕ: Загружает ID из Supabase. """
    try:
        response = supabase.table('processed_articles').select('guid').execute()
        return set([row['guid'] for row in response.data])
    except Exception as e:
        logging.error(f"Could not load GUIDs from Supabase: {e}")
        return set() # Начинаем с пустым набором в случае ошибки

def save_guid_to_db(supabase: Client, guid: str):
    """ <<< НОВОЕ: Сохраняет один ID в Supabase. """
    try:
        # upsert=True - на случай, если мы пытаемся добавить то, что уже есть
        supabase.table('processed_articles').insert({"guid": guid}, upsert=True).execute()
    except Exception as e:
        # Мы логируем ошибку, но не останавливаемся.
        # Primary Key constraint в базе данных всё равно не даст вставить дубликат.
        logging.error(f"Failed to save GUID {guid} to Supabase: {e}")

# (escape_markdown_v2, translate_text, get_real_lead, check_keywords, send_to_telegram
# остаются БЕЗ ИЗМЕНЕНИЙ, как в прошлом файле)

def escape_markdown_v2(text):
    if not text:
        return ""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    text = re.sub(r'\\', r'\\\\', text)
    for char in escape_chars:
        text = text.replace(char, f"\\{char}")
    return text

def translate_text(text, target_lang='ru', source_lang='en'):
    if not text:
        return None
    try:
        translated = GoogleTranslator(source=source_lang, target=target_lang).translate(text)
        return translated
    except Exception as e_google:
        logging.warning(f"Google Translate failed: {e_google}. Trying MyMemory.")
        try:
            translated = MyMemoryTranslator(source=source_lang, target=target_lang).translate(text)
            return translated
        except Exception as e_memory:
            logging.error(f"All translators failed for text: {text}. Error: {e_memory}")
            return None

def get_real_lead(html_description):
    if not html_description:
        return None
    soup = BeautifulSoup(html_description, 'html.parser')
    first_p = soup.find('p')
    if first_p:
        lead = first_p.get_text(strip=True)
    else:
        lead = soup.get_text(strip=True)
        if '.' in lead:
            lead = lead.split('.')[0] + '.'
    if not lead or len(lead) < 20:
        return None
    lower_lead = lead.lower()
    if "appeared first on" in lower_lead or \
       "read more" in lower_lead or \
       "©" in lower_lead or \
       "click here" in lower_lead:
        logging.info(f"Skipping article, template lead found: {lead[:50]}...")
        return None
    return lead

def check_keywords(text):
    if not text:
        return False
    for kw_regex in KEYWORDS:
        if kw_regex.search(text):
            return True
    return False

def send_to_telegram(message_text, bot_instance, target_channels):
    for channel_id in target_channels:
        try:
            bot_instance.send_message(
                chat_id=channel_id,
                text=message_text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True
            )
            logging.info(f"Successfully sent message to {channel_id}")
            time.sleep(1) 
        except Exception as e:
            logging.error(f"Failed to send message to {channel_id}: {e}")
            logging.error(f"Message content (raw): {message_text}")


# --- 5. Основная логика парсинга (обновлена) ---

def process_feeds(bot_instance: telegram.Bot, supabase: Client):
    """ <<< ИЗМЕНЕНО: Принимает 'supabase' клиент. """
    logging.info("--- Starting new feed processing cycle ---")
    
    # <<< ИЗМЕНЕНО: Загружаем из Supabase
    processed_guids = load_processed_guids_from_db(supabase)
    
    new_articles_found = 0
    
    for prefix, url in SOURCES.items():
        logging.info(f"Parsing source: {prefix} ({url})")
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logging.error(f"Could not parse feed {prefix}: {e}")
            continue

        for entry in feed.entries:
            guid = entry.get('id', entry.link)
            
            if not guid or guid in processed_guids:
                continue

            title = entry.get('title', '')
            link = entry.get('link', '')
            description = entry.get('description', entry.get('summary', ''))
            
            text_to_check = title + " " + description
            if not check_keywords(text_to_check):
                continue
                
            logging.info(f"Found keyword match in '{title[:50]}...'")

            lead = get_real_lead(description)
            if not lead:
                save_guid_to_db(supabase, guid) # Сохраняем "мусорный" guid, чтобы не проверять
                continue

            translated_title = translate_text(title)
            translated_lead = translate_text(lead)

            if not translated_title or not translated_lead:
                logging.warning(f"Translation failed for '{title}'. Skipping.")
                save_guid_to_db(supabase, guid) # Сохраняем, чтобы не пытаться перевести снова
                continue

            parsed_link = urlparse(link)
            clean_link = f"{parsed_link.scheme}://{parsed_link.netloc}{parsed_link.path}"

            esc_prefix = escape_markdown_v2(prefix)
            esc_title = escape_markdown_v2(translated_title)
            esc_lead = escape_markdown_v2(translated_lead)
            esc_link = escape_markdown_v2(clean_link)

            message = f"*{esc_prefix}*: {esc_title}\n\n{esc_lead}\n\n:Источник:({esc_link})"

            send_to_telegram(message, bot_instance, CHANNEL_IDS)
            
            # <<< ИЗМЕНЕНО: Сохраняем в Supabase
            save_guid_to_db(supabase, guid)
            processed_guids.add(guid) # Добавляем в локальный набор, чтобы не дублировать в этом же цикле
            new_articles_found += 1
            
            # <<< УДАЛЕНО: save_processed_guids(processed_guids)

    logging.info(f"--- Feed processing cycle finished. Found {new_articles_found} new articles. ---")

# --- 6. Настройка Веб-сервера (Flask) и Планировщика (Schedule) ---

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running...", 200

def run_scheduler():
    logging.info("Scheduler started.")
    
    # --- Инициализация Бота и Supabase ---
    if not TELEGRAM_BOT_TOKEN or not CHANNEL_IDS:
        logging.critical("Telegram TOKEN or CHANNEL_IDs not found!")
        return
        
    if not SUPABASE_URL or not SUPABASE_KEY:
        logging.critical("Supabase URL or KEY not found!")
        return
        
    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        bot_info = bot.get_me()
        logging.info(f"Telegram Bot initialized: {bot_info.username}")
    except Exception as e:
        logging.critical(f"Failed to initialize Telegram Bot: {e}")
        return

    # <<< НОВОЕ: Инициализируем Supabase клиент
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logging.info("Supabase client initialized.")
    except Exception as e:
        logging.critical(f"Failed to initialize Supabase client: {e}")
        return
    
    # <<< ИЗМЕНЕНО: Привязываем 'bot' и 'supabase'
    def job():
        process_feeds(bot, supabase)
        
    logging.info("Running initial job...")
    job()
    
    schedule.every(10).minutes.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

def run_server():
    logging.info(f"Starting Flask server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT)

# --- 7. Запуск ---
if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    run_scheduler()
