import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle, InputTextMessageContent
import sqlite3
import random
import time
import threading
import io
import json
import re
import uuid
from datetime import datetime
from collections import defaultdict

# ================== تنظیمات ==================
BOT_TOKEN = ""
ADMIN_IDS = []

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# ================== دیتابیس ==================
conn = sqlite3.connect('dare_truth.db', check_same_thread=False)
#conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=8000")
conn.commit()

_cursor_local = threading.local()

class _ThreadLocalCursor:
    def _get(self):
        cur = getattr(_cursor_local, 'cur', None)
        if cur is None:
            cur = conn.cursor()
            _cursor_local.cur = cur
        return cur

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def __iter__(self):
        return iter(self._get())

cursor = _ThreadLocalCursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    gender TEXT,
    preferred_gender TEXT,
    is_banned INTEGER DEFAULT 0,
    created_at INTEGER,
    nickname TEXT,
    profile_photo_file_id TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    type TEXT,
    text TEXT,
    status TEXT DEFAULT 'approved',
    suggested_by INTEGER,
    created_at INTEGER
)''')

# پاکسازی سوالات تکراری داخل هر دسته (دسته+نوع+متن یکسان) و نگه‌داشتن قدیمی‌ترین رکورد
# این کوئری idempotent است و هر بار اجرا بشه فقط رکوردهای تکراری واقعی رو حذف می‌کنه
cursor.execute('''
    DELETE FROM questions
    WHERE id NOT IN (
        SELECT MIN(id) FROM questions GROUP BY category, type, text
    )
''')
# جلوگیری از ثبت تکراری در آینده: هر ترکیب دسته+نوع+متن فقط یک بار مجاز است
cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_unique ON questions(category, type, text)')
conn.commit()

cursor.execute('''CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS forced_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_username TEXT UNIQUE,
    join_url TEXT,
    is_active INTEGER DEFAULT 1,
    created_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS admin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT,
    details TEXT,
    created_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS help_buttons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    button_text TEXT NOT NULL,
    button_callback TEXT UNIQUE NOT NULL,
    content TEXT NOT NULL,
    button_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at INTEGER,
    updated_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS anon_queue (
    user_id INTEGER PRIMARY KEY,
    gender TEXT,
    preferred_gender TEXT,
    join_time INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS anon_matches (
    match_id TEXT PRIMARY KEY,
    player1 INTEGER,
    player2 INTEGER,
    current_turn INTEGER,
    current_question_type TEXT,
    current_question_text TEXT,
    status TEXT,
    created_at INTEGER,
    last_activity INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS anon_chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT,
    sender_id INTEGER,
    message TEXT,
    created_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS group_games_db (
    game_id TEXT PRIMARY KEY,
    host_id INTEGER,
    chat_id INTEGER,
    players TEXT,
    current_player INTEGER,
    stage TEXT,
    temp_category TEXT,
    current_question_type TEXT,
    current_question_text TEXT,
    status TEXT,
    created_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    liked_by INTEGER,
    match_id TEXT,
    created_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS blocked_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    blocked_user INTEGER,
    match_id TEXT,
    created_at INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    favorite_user_id INTEGER,
    created_at INTEGER,
    UNIQUE(user_id, favorite_user_id)
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawal_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    created_at INTEGER,
    processed_at INTEGER,
    processed_by INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS game_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user INTEGER,
    to_user INTEGER,
    status TEXT DEFAULT 'pending',
    created_at INTEGER
)''')

conn.commit()

def _add_column_if_missing(table, column, coltype):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = [row[1] for row in cursor.fetchall()]
    if column not in existing:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            conn.commit()
        except Exception as e:
            print(f"[WARN] add column {column} failed: {e}")

_add_column_if_missing('users', 'age', 'INTEGER')
_add_column_if_missing('users', 'latitude', 'REAL')
_add_column_if_missing('users', 'longitude', 'REAL')
_add_column_if_missing('users', 'wallet_balance', 'INTEGER DEFAULT 0')
_add_column_if_missing('users', 'referred_by', 'INTEGER')
_add_column_if_missing('users', 'referral_paid', 'INTEGER DEFAULT 0')
_add_column_if_missing('users', 'province', 'TEXT')
_add_column_if_missing('users', 'city', 'TEXT')
_add_column_if_missing('anon_queue', 'location_scope', "TEXT DEFAULT 'any'")

cursor.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('store_text', ?)",
               ("🛍 <b>فروشگاه ما</b>\n\nبه زودی محصولات ما اینجا نمایش داده می‌شود.\nجهت خرید وی‌پی‌ان با ادمین در ارتباط باشید. 📩",))
conn.commit()

REFERRAL_REWARD = 2000
MIN_WITHDRAW_AMOUNT = 30000
MIN_WITHDRAW_REFERRALS = 10

VALID_CATEGORIES = ('boy_normal', 'girl_normal', 'boy_18', 'girl_18')
cursor.execute("UPDATE questions SET category = 'boy_normal' WHERE category = 'boy'")
cursor.execute("UPDATE questions SET category = 'girl_normal' WHERE category = 'girl'")
conn.commit()

IRAN_LOCATIONS = {
    "آذربایجان شرقی": ["تبریز", "مراغه", "میانه", "اهر", "مرند", "بناب", "سراب", "ملکان", "شبستر", "عجب‌شیر", "هریس", "ورزقان", "خداآفرین", "چاراویماق", "هشترود", "کلیبر", "جلفا", "اسکو", "ایلخچی", "بستان‌آباد", "سیس", "سهند", "ترکمانچای", "تیمورلو", "خاروانا", "دوزدوزان", "زرنق", "زنجان", "سردرود", "شربیان", "صوفیان", "قره‌آغاج", "کشکسرای", "ممقان", "هادیشهر", "ورزقان"],
    "آذربایجان غربی": ["ارومیه", "خوی", "مهاباد", "بوکان", "میاندوآب", "سلماس", "نقده", "پیرانشهر", "شاهین‌دژ", "تکاب", "چالدران", "ماکو", "اشنویه", "سردشت", "ربط", "سیمینه", "میرآباد", "نازک علیا", "سیلوانا", "فیرورق", "سرو", "تازه‌شهر", "نوشین‌شهر", "قطور", "باروق", "سلماس", "آواجیق", "زرآباد", "کشاورز", "دیزج", "حاجیلار"],
    "اردبیل": ["اردبیل", "مشگین‌شهر", "پارس‌آباد", "خلخال", "گرمی", "نمین", "بیله‌سوار", "کوثر", "سرعین", "عنبران", "فخرآباد", "اصلاندوز", "گیوی", "نیر", "هیر", "لاهرود", "کلور", "اهر", "تازه‌کند", "رضی"],
    "اصفهان": ["اصفهان", "کاشان", "نجف‌آباد", "خمینی‌شهر", "شاهین‌شهر", "نطنز", "آران و بیدگل", "مبارکه", "فلاورجان", "لنجان", "برخوار", "گلپایگان", "خوانسار", "فریدن", "سمیرم", "شهرضا", "دهاقان", "تیران و کرون", "چادگان", "بوئین و میاندشت", "فریدون‌شهر", "خور", "بیابانک", "اردستان", "ورزنه", "کوهپایه", "زیار", "گلشهر", "کلیشاد", "سجزی", "باغ بهادران", "میمه", "وزوان", "قائم‌شهر", "جاوان", "هرند", "حبیب‌آباد", "نائین", "تودشک", "زواره", "سده لنجان", "فولادشهر", "دانگاه", "ورزنه"],
    "البرز": ["کرج", "فردیس", "نظرآباد", "اشتهارد", "هشتگرد", "ساوجبلاغ", "طالقان", "محمدشهر", "کمال‌شهر", "گرمدره", "ماهدشت", "چهارباغ", "تنکمان", "آسارا"],
    "ایلام": ["ایلام", "دهلران", "آبدانان", "ایوان", "مهران", "دره‌شهر", "شیروان و چرداول", "بدره", "ملکشاهی", "سیروان", "پشتکوه", "چوار", "موسیان", "ارکوازی", "دلگشا", "ماژین", "هفت‌چشمه"],
    "بوشهر": ["بوشهر", "برازجان", "گناوه", "کنگان", "دیر", "دیلم", "جم", "تنگستان", "دشتی", "دشستان", "عسلویه", "بندر ریگ", "اهرم", "خارک", "نخل تقی", "جزیره شیف", "آب‌پخش", "بندر دیر", "بندر کنگان", "چغادک", "خورموج", "بادوله", "بوشکان"],
    "تهران": ["تهران", "اسلامشهر", "شهریار", "ورامین", "پاکدشت", "دماوند", "پردیس", "رباط‌کریم", "قرچک", "ملارد", "ری", "شمیرانات", "بهارستان", "قدس", "فیروزکوه", "رودهن", "لواسان", "بومهن", "دماوند", "آبسرد", "فشم", "کهریزک", "باقرآباد", "صالح‌آباد", "جوادآباد", "سعیدآباد", "نسیم‌شهر", "شاهدشهر", "گلستان", "احمدآباد مستوفی", "غیاث‌آباد", "صفادشت", "رباط‌کریم", "کهنک", "باغستان", "اکبرآباد", "سیمین‌دشت"],
    "چهارمحال و بختیاری": ["شهرکرد", "بروجن", "فارسان", "لردگان", "اردل", "کیار", "کوهرنگ", "سامان", "بنی", "نافچ", "گوجان", "ناغان", "طالقان", "سردشت", "هارونی", "منج", "فرخشهر", "گوشه", "سورک", "هفشجان"],
    "خراسان جنوبی": ["بیرجند", "قائنات", "طبس", "نهبندان", "سربیشه", "درمیان", "زیرکوه", "بشرویه", "سرایان", "فردوس", "گرم‌چشمه", "خوسف", "مود", "نیگنان", "حاجی‌آباد", "شوسف", "شاهرخت", "نیمبلوک", "سه‌قلعه", "دستگردان"],
    "خراسان رضوی": ["مشهد", "نیشابور", "سبزوار", "تربت‌حیدریه", "قوچان", "کاشمر", "گناباد", "تربت جام", "خواف", "تایباد", "بردسکن", "چناران", "درگز", "فریمان", "کلات", "رشتخوار", "باخرز", "زاوه", "جغتای", "جوین", "خلیل‌آباد", "مه‌ولات", "ششتمد", "بینالود", "قدمگاه", "طرقبه", "شاندیز", "گلبهار", "نوخندان", "لطف‌آباد", "نصرآباد", "فیروزه", "سلطان‌آباد", "سنگ‌سفید", "جنت‌آباد", "میان‌جلگه", "یونسی", "کاخک", "سده", "شادمهر", "کدکن", "بجستان", "انابد", "جنگل", "سرخس", "مرزداران", "باجگیران"],
    "خراسان شمالی": ["بجنورد", "شیروان", "اسفراین", "مانه و سملقان", "گرمه", "جاجرم", "راز و جرگلان", "فاروج", "چناران‌شهر", "پیش‌قلعه", "آشخانه", "سنخواست", "صفی‌آباد", "حصار گرم‌خان", "درق", "پارچ", "گرماب"],
    "خوزستان": ["اهواز", "آبادان", "خرمشهر", "دزفول", "بندرماهشهر", "شوشتر", "اندیمشک", "بهبهان", "ایذه", "رامهرمز", "شوش", "مسجدسلیمان", "هویزه", "رامشیر", "امیدیه", "گتوند", "لالی", "باغملک", "هفتکل", "اندیکا", "کارون", "حمیدیه", "دشت آزادگان", "مینوشهر", "الوان", "اروندکنار", "ترک‌آباد", "شادگان", "دارخوین", "ویسی", "صالح‌شهر", "زهره", "میداوود", "سردشت", "آبژدان", "تلخاب", "چغامیش", "دیدگان", "رامشیر", "قلعه تل", "شهیون", "گوریه", "ملاثانی", "بندر امام خمینی", "بندر ماهشهر"],
    "زنجان": ["زنجان", "ابهر", "خدابنده", "خرمدره", "ماه‌نشان", "طارم", "سلطانیه", "سجاس", "زرین‌رود", "زرین‌آباد", "نوربهار", "هیدج", "قیدار", "استار", "ارمغان‌خانه", "چورزق", "نیاز", "دندی"],
    "سمنان": ["سمنان", "شاهرود", "دامغان", "گرمسار", "میامی", "آرادان", "مهدی‌شهر", "کلاته خیج", "بسطام", "دیباج", "درجزین", "کهن‌آباد", "مجن", "اسحاق‌آباد", "مومن‌آباد", "سلطان‌آباد"],
    "سیستان و بلوچستان": ["زاهدان", "زابل", "چابهار", "ایرانشهر", "خاش", "سراوان", "کنارک", "نیک‌شهر", "دلگان", "سرباز", "بمپور", "زهک", "هیرمند", "نیمروز", "میرجاوه", "قصرقند", "فنوج", "مهرستان", "تفتان", "سیب و سوران", "نوک‌آباد", "بزمان", "سوران", "راسک", "پیشین", "زرآباد", "چاهان", "جالق", "کوشک‌نصر", "ادیمی", "نگور", "باهوکلات", "بنت", "بیرک", "جکیگور", "دوست‌محمد", "رامشیر", "سیرکان", "علی‌اکبر", "محمدآباد", "نصرت‌آباد", "کهیر", "گشت"],
    "فارس": ["شیراز", "مرودشت", "جهرم", "کازرون", "فسا", "لارستان", "داراب", "سپیدان", "ممسنی", "نی‌ریز", "استهبان", "اقلید", "پاسارگاد", "خرم‌بید", "فراشبند", "زرین‌دشت", "لامرد", "سرچهان", "رستم", "بیضا", "جویم", "خرامه", "بوانات", "ارسنجان", "قیر و کارزین", "کوار", "زرقان", "سروستان", "حاجی‌آباد", "نورآباد", "شش‌ده", "دوزه", "مهر", "خنج", "گراش", "بیدشهر", "کوهنجان", "داریان", "صدرا", "مبارک‌آباد", "قایمیه", "باب‌انار", "دشتک", "سده", "کمهر", "پرنده", "بندرمهر", "خوزی", "وراوی", "افزر", "چم‌انجیر", "گویم", "نودان", "برم", "شیراز", "سعادت‌شهر", "رونیز", "بالاده", "یزدان‌شهر", "میمند", "خسویه", "مشکان"],
    "قزوین": ["قزوین", "تاکستان", "البرز", "بوئین‌زهرا", "آوج", "آبیک", "نرجه", "رازمیان", "خاکعلی", "شریف‌آباد", "ارمغان‌خانه", "محمودآباد نمونه", "سیردان", "دشتابی", "الوند", "اقبالیه", "معلم‌کلایه", "اسفرورین", "دانسفهان"],
    "قم": ["قم", "جعفرآباد", "دستجرد", "سلفچگان", "کهک", "قنوات", "خلج‌آباد", "پردیسان", "قمصر", "نیزار"],
    "کردستان": ["سنندج", "سقز", "مریوان", "بانه", "قروه", "بیجار", "دیواندره", "کامیاران", "دهگلان", "سروآباد", "پیرتاج", "بلبان‌آباد", "یاسوکند", "برده‌رشه", "چناره", "هزارکانیان", "سریش‌آباد", "کانی‌دینار", "ترجان", "موچش", "بولی", "ننور", "زیویه", "توپ‌آقاج", "پیرتاج", "بلبان‌آباد"],
    "کرمان": ["کرمان", "رفسنجان", "سیرجان", "جیرفت", "بم", "زرند", "راور", "شهربابک", "کهنوج", "عنبرآباد", "مهرستان", "بافت", "نرماشیر", "منوجان", "رودبار جنوب", "ریگان", "فاریاب", "قلعه‌گنج", "کوهبنان", "انار", "بابک", "خنامان", "مس سرچشمه", "جوپار", "جوازان", "حسین‌آباد", "زنگی‌آباد", "گزک", "ماهان", "نجف‌شهر", "کشکوییه", "خواجو", "دهج", "امیرآباد", "بلورد", "خورسند", "دشت‌کار", "کیانشهر", "نوق", "شهداد", "اندوهجرد", "باغین", "بروات", "بهرمان", "چترود", "حیدرآباد", "ریحان‌شهر", "سالار", "سیریز", "فهرج", "قلعه‌نو", "گلباف", "محمدآباد", "مشیز", "موگویی", "نظام‌شهر", "هرند", "یزدان‌شهر"],
    "کرمانشاه": ["کرمانشاه", "اسلام‌آباد غرب", "سنقر", "پاوه", "هرسین", "کنگاور", "جوانرود", "صحنه", "سرپل ذهاب", "گل‌دشت", "قصرشیرین", "دالاهو", "روانسر", "ثلاث باباجانی", "کرند غرب", "سرمست", "شاهو", "بی‌ستون", "میان‌راهان", "کرج", "نودشه", "نوسود", "باینگان", "گهواره", "حومه", "تازه‌آباد", "کوزران", "رباط", "حمیل"],
    "کهگیلویه و بویراحمد": ["یاسوج", "دوگنبدان", "دهدشت", "سی‌سخت", "چرام", "بهمئی", "باشت", "لنده", "مارگون", "پاتاوه", "دیشموک", "قلعه‌رئیسی", "سرفاریاب", "چیتاب", "مادوان", "نرماشیر", "سوق", "کریک", "انارستان", "بلیان", "برج", "سردار", "شهنیا", "کوشک", "گچساران"],
    "گلستان": ["گرگان", "گنبدکاووس", "علی‌آباد کتول", "بندرترکمن", "آق‌قلا", "کردکوی", "مینودشت", "گالیکش", "مراوه‌تپه", "رامیان", "کلاه‌قاضی", "فراغی", "نگین‌شهر", "سیمین‌شهر", "تاتار", "خان‌ببین", "دوزین", "فاضل‌آباد", "تازه‌آباد", "سیاه‌مرز", "تنگراه", "چناران", "دامغان"],
    "گیلان": ["رشت", "بندرانزلی", "لاهیجان", "آستارا", "لنگرود", "رودسر", "رودبار", "فومن", "صومعه‌سرا", "رضوانشهر", "آستانه اشرفیه", "سیاهکل", "املش", "تالش", "ماسال", "شفعت", "خمام", "شفت", "سنگر", "دیلمان", "پره‌سر", "چابکسر", "چاف و چمخاله", "خشک‌بیجار", "واجارگاه", "رامجین", "ماکلوان", "کلشتر", "کوچصفه‌ان", "بازار جمعه", "لشکرنشا", "خشک‌بیجار", "کومله", "حویق", "بازی‌رود", "ماسوله"],
    "لرستان": ["خرم‌آباد", "بروجرد", "دورود", "الیگودرز", "کوهدشت", "ازنا", "پلدختر", "دلفان", "سلسله", "دوره", "رومشکان", "چالانچولان", "سراب دوره", "ویسیان", "هفت‌چشمه", "کوه‌دشت", "چم‌گلک", "دمه", "گراب", "شاه‌آباد", "برخوردار", "نورآباد", "سپیددشت", "چهاربرج", "مینو", "کاکاوند", "تنگه‌راه", "هرو", "خرم‌آباد", "ساران", "زاغه", "قلعه‌تیمور", "چغلوندی"],
    "مازندران": ["ساری", "بابل", "آمل", "قائم‌شهر", "بابلسر", "چالوس", "نوشهر", "تنکابن", "نکا", "بهشهر", "رامسر", "جویبار", "فریدونکنار", "سوادکوه", "سوادکوه شمالی", "نور", "محمودآباد", "سیمرغ", "میاندرود", "کلاردشت", "عباس‌آباد", "گلوگاه", "امیرکلا", "خلیل‌شهر", "زرگرمحله", "شیرگاه", "پل سفید", "مرزن‌آباد", "کجور", "نظام‌آباد", "کتالم و سادات‌شهر", "رویان", "نارنج‌باغ", "گزنک", "دابودشت", "چمستان", "بایکلا", "پایین‌هولار", "کیاکلا", "سورک", "فریم", "خوش‌رودپی", "بندپی", "دودانگه", "چالدره", "گتاب", "لاریم", "سوادکوه", "آلاشت", "ریگ‌چشمه", "نکا", "بهنمیر", "نفت‌چال", "گلوگاه"],
    "مرکزی": ["اراک", "ساوه", "خمین", "محلات", "دلیجان", "تفرش", "فراهان", "شازند", "زرندیه", "کمیجان", "خنداب", "آشتیان", "مهاجران", "نوبران", "قورچی‌باشی", "خشکرود", "شهباز", "امیرآباد", "جاورسیان", "غرق‌آباد", "نیم‌ور", "توره", "داودآباد", "ساروق", "هزاوه", "آستانه", "خوشه‌باف", "کارچان", "چهارچشمه", "کیازان", "آهنگران", "اناج", "حسن‌آباد", "فرمهین"],
    "هرمزگان": ["بندرعباس", "میناب", "بندرلنگه", "قشم", "کیش", "حاجی‌آباد", "جاسک", "رودان", "بستک", "خمیر", "پارسیان", "سیریک", "بشاگرد", "فین", "تازیان", "سردشت", "کوخرد", "هشتبندی", "بندر کنگ", "لمزان", "هور", "سوزا", "تنگ", "دولاب", "جناح", "دهبارز", "بیرم", "کهورستان", "درگهان", "رودخانه", "فارغان", "تخت", "فین", "گرگ"],
    "همدان": ["همدان", "ملایر", "نهاوند", "تویسرکان", "اسدآباد", "بهار", "کبودرآهنگ", "فامنین", "رزن", "درگزین", "قلعه‌قشلاق", "جورقان", "مریانج", "ازندریان", "آجین", "جوکار", "سرکان", "زنگنه", "سامن", "برزول", "گیان", "فیروزان", "شیرین‌سو", "خضرآباد", "صالح‌آباد", "لالجین", "جیحون‌آباد", "دمق", "قهاوند", "تویسرکان", "خزل", "حسن‌آباد", "قروه درجزین"],
    "یزد": ["یزد", "میبد", "اردکان", "بافق", "مهریز", "تفت", "ابرکوه", "خاتم", "اشکذر", "بهاباد", "زرتشتیان", "نیر", "شاهدیه", "حمیدیا", "نصرآباد", "دستگرد", "خیرآباد", "بزنجان", "مروست", "سریزد", "هرات", "مروست", "چاه‌میر", "روستاهای یزد", "ندوشن", "ابرکوه", "خضرآباد", "احمدآباد", "دهج", "ترک‌آباد", "منشاد", "بخشی"]
}
IRAN_PROVINCES = list(IRAN_LOCATIONS.keys())

group_games = {}
temp_data = {}
user_last_action = defaultdict(float)
anon_search_timers = {}
anon_location_context = {}

def init_questions():
    default_questions = [
        ('boy_normal', 'جرعت', 'یک کار خنده‌دار انجام بده'),
        ('boy_normal', 'جرعت', 'به یک نفر تصادفی پیام بده'),
        ('boy_normal', 'جرعت', '۱۰ تا اسکوات برو'),
        ('boy_normal', 'حقیقت', 'آخرین باری که دروغ گفتی کی بود؟'),
        ('boy_normal', 'حقیقت', 'از چه چیزی بیشتر می‌ترسی؟'),
        ('girl_normal', 'جرعت', 'یک عکس بدون فیلتر بفرست'),
        ('girl_normal', 'جرعت', 'یک رقص بکن'),
        ('girl_normal', 'حقیقت', 'آیا تا حالا عاشق شده‌ای؟'),
        ('girl_normal', 'حقیقت', 'بزرگترین آرزوت چیه؟'),
        ('boy_18', 'جرعت', 'یک راز بگو'),
        ('boy_18', 'حقیقت', 'بزرگترین اشتباه عشقی‌ات؟'),
        ('girl_18', 'جرعت', 'یک چالش انجام بده'),
        ('girl_18', 'حقیقت', 'اولین عشق تو کی بود؟'),
    ]
    for cat, q_type, text in default_questions:
        cursor.execute("INSERT OR IGNORE INTO questions (category, type, text, status, created_at) VALUES (?, ?, ?, 'approved', ?)",
                       (cat, q_type, text, int(time.time())))
    conn.commit()

def init_help_buttons():
    default_buttons = [
        ('👥 آموزش بازی گروهی', 'help_group', 'برای شروع بازی گروهی:\n1️⃣ روی دکمه «بازی گروهی» کلیک کنید\n2️⃣ چت مورد نظر را انتخاب کنید\n3️⃣ بازی در آن چت ایجاد می‌شود', 0),
        ('🎭 آموزش بازی ناشناس', 'help_anonymous', '🎮 <b>بازی ناشناس</b>\n1️⃣ روی دکمه «بازی ناشناس» کلیک کنید\n2️⃣ جنسیت خود را انتخاب کنید\n3️⃣ وارد صف انتظار می‌شوید', 1),
        ('📝 ارسال سوال', 'help_question', '1️⃣ روی دکمه «ارسال سوال» کلیک کنید\n2️⃣ دسته و نوع سوال را انتخاب کنید\n3️⃣ متن سوال را ارسال کنید', 2),
        ('🛟 پشتیبانی', 'help_support', 'برای ارتباط با پشتیبانی به آیدی @brosbio پیام دهید.', 3),
        ('🏆 لیدربرد', 'help_leaderboard', '🏆 <b>لیدربرد</b>\n\nاین بخش شامل دو قسمت است:\n\n1️⃣ <b>نفرات برتر لایک:</b>\nکاربرانی که بیشترین لایک را در بازی‌های ناشناس دریافت کرده‌اند.\n\n2️⃣ <b>برترین دعوت‌کنندگان:</b>\nکاربرانی که بیشترین تعداد کاربر را با لینک دعوت خود وارد ربات کرده‌اند.\n\n💡 برای دیدن لیست کامل، روی دکمه «لیدربرد» در منوی اصلی کلیک کنید.', 4),
        ('💰 درآمد از ربات', 'help_income', '💰 <b>درآمد از ربات</b>\n\nبه ازای هر کاربری که با لینک دعوت شما وارد ربات شود، مبلغ {REFERRAL_REWARD} تومن به کیف پول شما اضافه می‌شود.\n\n📌 شرایط برداشت:\n• حداقل موجودی: {MIN_WITHDRAW_AMOUNT:,} تومن\n• حداقل تعداد دعوتی: {MIN_WITHDRAW_REFERRALS} نفر\n\n🔗 برای دریافت لینک دعوت خود، به بخش «درآمد از ربات» بروید.', 5),
        ('🔧 تنظیمات پروفایل', 'help_profile', '👤 <b>تنظیمات پروفایل</b>\n\nدر این بخش می‌توانید:\n• تغییر نام مستعار\n• تغییر سن\n• تغییر جنسیت\n• تغییر ترجیح بازی\n• تغییر عکس پروفایل\n• ثبت لوکیشن\n• مشاهده لایک‌ها و بلاک‌ها\n• مدیریت افراد دلخواه', 6),
        ('🎲 نحوه بازی', 'help_game', '🎲 <b>نحوه بازی جرعت و حقیقت</b>\n\nاین ربات دو حالت بازی دارد:\n\n<b>۱. بازی ناشناس:</b>\n• با یک کاربر تصادفی همبازی می‌شوید\n• هویت شما مخفی است\n• می‌توانید به همبازی خود لایک یا بلاک کنید\n\n<b>۲. بازی گروهی:</b>\n• در گروه‌ها و چت‌های خصوصی قابل اجراست\n• میزبان بازی را شروع می‌کند\n• نوبت‌ها به صورت چرخشی می‌باشند\n\n<b>نکته:</b> سوالات به دو دسته «عادی» و «+۱۸» تقسیم می‌شوند.', 7),
    ]
    for btn_text, callback, content, order in default_buttons:
        content = content.replace('{REFERRAL_REWARD}', str(REFERRAL_REWARD))
        content = content.replace('{MIN_WITHDRAW_AMOUNT:,}', f"{MIN_WITHDRAW_AMOUNT:,}")
        content = content.replace('{MIN_WITHDRAW_REFERRALS}', str(MIN_WITHDRAW_REFERRALS))
        cursor.execute('''INSERT OR IGNORE INTO help_buttons 
                          (button_text, button_callback, content, button_order, is_active, created_at, updated_at)
                          VALUES (?, ?, ?, ?, 1, ?, ?)''',
                       (btn_text, callback, content, order, int(time.time()), int(time.time())))
    conn.commit()

init_questions()
init_help_buttons()

def bold(text):
    return f"<b>{text}</b>"

def get_user_name(user_id):
    user = get_user(user_id)
    if user:
        if user[7]:
            return user[7]
        elif user[2]:
            return user[2]
        elif user[1]:
            return f"@{user[1]}"
    try:
        chat = bot.get_chat(user_id)
        if chat.first_name:
            return chat.first_name
        elif chat.username:
            return f"@{chat.username}"
    except:
        pass
    return f"کاربر {user_id}"

def get_user_mention(user_id):
    user = get_user(user_id)
    if user and user[1]:
        return f"@{user[1]}"
    name = get_user_name(user_id)
    return f'<a href="tg://user?id={user_id}">{name}</a>'

def check_rate_limit(user_id, cooldown=1):
    now = time.time()
    if now - user_last_action[user_id] < cooldown:
        return False
    user_last_action[user_id] = now
    return True

def get_user_id(identifier):
    identifier = str(identifier)
    if identifier.isdigit():
        return int(identifier)
    else:
        username = identifier.replace('@', '').strip()
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return row[0] if row else None

def create_user(user_id, username=None, first_name=None):
    cursor.execute("INSERT OR IGNORE INTO users (id, username, first_name, gender, preferred_gender, created_at, nickname, profile_photo_file_id) VALUES (?, ?, ?, NULL, 'both', ?, NULL, NULL)",
                   (user_id, username, first_name, int(time.time())))
    conn.commit()

def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()

def get_user_profile(user_id):
    cursor.execute("SELECT id, username, first_name, gender, preferred_gender, nickname, profile_photo_file_id FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()

def update_user_gender(user_id, gender):
    cursor.execute("UPDATE users SET gender = ? WHERE id = ?", (gender, user_id))
    conn.commit()

def update_user_preferred_gender(user_id, preferred_gender):
    cursor.execute("UPDATE users SET preferred_gender = ? WHERE id = ?", (preferred_gender, user_id))
    conn.commit()

def update_user_nickname(user_id, nickname):
    cursor.execute("UPDATE users SET nickname = ? WHERE id = ?", (nickname, user_id))
    conn.commit()

def update_user_profile_photo(user_id, file_id):
    cursor.execute("UPDATE users SET profile_photo_file_id = ? WHERE id = ?", (file_id, user_id))
    conn.commit()

def is_banned(user_id):
    cursor.execute("SELECT is_banned FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    return row and row[0] == 1

def is_admin(user_id):
    if user_id in ADMIN_IDS:
        return True
    conn_temp = sqlite3.connect('dare_truth.db')
    cursor_temp = conn_temp.cursor()
    cursor_temp.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    result = cursor_temp.fetchone() is not None
    conn_temp.close()
    return result

def log_admin_action(admin_id, action, details=None):
    cursor.execute("INSERT INTO admin_logs (admin_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                   (admin_id, action, details, int(time.time())))
    conn.commit()

def get_random_question(category, q_type):
    cursor.execute("SELECT text FROM questions WHERE category = ? AND type = ? AND status = 'approved' ORDER BY RANDOM() LIMIT 1", (category, q_type))
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute("SELECT text FROM questions WHERE category = 'boy_normal' AND type = ? AND status = 'approved' ORDER BY RANDOM() LIMIT 1", (q_type,))
    row = cursor.fetchone()
    return row[0] if row else "سوالی یافت نشد!"

def add_question(category, q_type, text, suggested_by=None):
    cleaned_text = re.sub(r'^\d+[\.\-\)]\s*', '', text.strip())
    cleaned_text = re.sub(r'^[۰-۹]+[\.\-\)]\s*', '', cleaned_text)
    cursor.execute("SELECT id FROM questions WHERE category = ? AND type = ? AND text = ?", 
                   (category, q_type, cleaned_text))
    if cursor.fetchone():
        return None
    cursor.execute("INSERT INTO questions (category, type, text, suggested_by, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                   (category, q_type, cleaned_text, suggested_by, int(time.time())))
    conn.commit()
    return cursor.lastrowid

def get_questions_count():
    cursor.execute("SELECT COUNT(*) FROM questions WHERE status = 'approved'")
    return cursor.fetchone()[0]

def get_user_stats():
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE gender = 'male' AND is_banned = 0")
    male = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE gender = 'female' AND is_banned = 0")
    female = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
    banned = cursor.fetchone()[0]
    return total, male, female, banned

def get_help_content(callback):
    cursor.execute("SELECT content FROM help_buttons WHERE button_callback = ? AND is_active = 1", (callback,))
    row = cursor.fetchone()
    return row[0] if row else None

def get_help_keyboard():
    cursor.execute('''SELECT button_text, button_callback, button_order 
                      FROM help_buttons 
                      WHERE is_active = 1 
                      ORDER BY button_order''')
    buttons = cursor.fetchall()
    kb = InlineKeyboardMarkup(row_width=1)
    for btn_text, callback, order in buttons:
        kb.add(InlineKeyboardButton(btn_text, callback_data=callback))
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def get_all_help_buttons():
    cursor.execute('''SELECT id, button_text, button_callback, content, button_order, is_active 
                      FROM help_buttons 
                      ORDER BY button_order''')
    return cursor.fetchall()

def add_help_button(button_text, button_callback, content, button_order):
    try:
        cursor.execute('''INSERT INTO help_buttons 
                          (button_text, button_callback, content, button_order, is_active, created_at, updated_at)
                          VALUES (?, ?, ?, ?, 1, ?, ?)''',
                       (button_text, button_callback, content, button_order, int(time.time()), int(time.time())))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def update_help_button(button_id, button_text=None, button_callback=None, content=None, button_order=None, is_active=None):
    updates = []
    params = []
    if button_text is not None:
        updates.append("button_text = ?")
        params.append(button_text)
    if button_callback is not None:
        updates.append("button_callback = ?")
        params.append(button_callback)
    if content is not None:
        updates.append("content = ?")
        params.append(content)
    if button_order is not None:
        updates.append("button_order = ?")
        params.append(button_order)
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(is_active)
    if updates:
        updates.append("updated_at = ?")
        params.append(int(time.time()))
        params.append(button_id)
        cursor.execute(f"UPDATE help_buttons SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    return True

def delete_help_button(button_id):
    cursor.execute("DELETE FROM help_buttons WHERE id = ?", (button_id,))
    conn.commit()
    return True

def add_forced_channel(channel_username, join_url):
    channel_username = channel_username.replace('@', '').strip()
    try:
        cursor.execute("INSERT INTO forced_channels (channel_username, join_url, created_at) VALUES (?, ?, ?)",
                       (channel_username, join_url, int(time.time())))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def remove_forced_channel(identifier):
    identifier = str(identifier).replace('@', '')
    cursor.execute("DELETE FROM forced_channels WHERE channel_username = ?", (identifier,))
    conn.commit()
    return cursor.rowcount > 0

def get_forced_channels():
    cursor.execute("SELECT channel_username, join_url FROM forced_channels WHERE is_active = 1 ORDER BY created_at")
    return cursor.fetchall()

def is_force_join_enabled():
    return len(get_forced_channels()) > 0

def check_user_joined_channels(user_id):
    channels = get_forced_channels()
    if not channels:
        return True, []
    not_joined = []
    for channel_username, join_url in channels:
        if not channel_username:
            continue
        try:
            chat_id = '@' + channel_username
            member = bot.get_chat_member(chat_id, user_id)
            if member.status in ['left', 'kicked']:
                not_joined.append({'username': channel_username, 'url': join_url})
        except:
            not_joined.append({'username': channel_username, 'url': join_url})
    return len(not_joined) == 0, not_joined

def get_force_join_keyboard(not_joined, callback_prefix="check_join"):
    kb = InlineKeyboardMarkup(row_width=1)
    for ch in not_joined:
        kb.add(InlineKeyboardButton(f"📢 عضویت در @{ch['username']}", url=ch['url'], style="primary"))
    kb.add(InlineKeyboardButton("✅ بررسی مجدد", callback_data=callback_prefix, style="success"))
    return kb

def send_media_message(chat_id, message, caption=None, reply_markup=None):
    try:
        if message.photo:
            return bot.send_photo(chat_id, message.photo[-1].file_id, caption=caption or message.caption, reply_markup=reply_markup)
        elif message.video:
            return bot.send_video(chat_id, message.video.file_id, caption=caption or message.caption, reply_markup=reply_markup)
        elif message.animation:
            return bot.send_animation(chat_id, message.animation.file_id, caption=caption or message.caption, reply_markup=reply_markup)
        elif message.sticker:
            return bot.send_sticker(chat_id, message.sticker.file_id, reply_markup=reply_markup)
        elif message.voice:
            return bot.send_voice(chat_id, message.voice.file_id, caption=caption or message.caption, reply_markup=reply_markup)
        elif message.audio:
            return bot.send_audio(chat_id, message.audio.file_id, caption=caption or message.caption, reply_markup=reply_markup)
        elif message.document:
            return bot.send_document(chat_id, message.document.file_id, caption=caption or message.caption, reply_markup=reply_markup)
        elif message.video_note:
            return bot.send_video_note(chat_id, message.video_note.file_id, reply_markup=reply_markup)
        elif message.text:
            return bot.send_message(chat_id, caption or message.text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            return bot.copy_message(chat_id, message.chat.id, message.message_id, reply_markup=reply_markup)
    except Exception as e:
        print(f"Error sending media: {e}")
        return None

def forward_media_message(chat_id, from_chat_id, message_id):
    try:
        return bot.forward_message(chat_id, from_chat_id, message_id)
    except Exception as e:
        print(f"Error forwarding media: {e}")
        return None

def save_group_game(game_id):
    if game_id not in group_games:
        return False
    game = group_games[game_id]
    cursor.execute("INSERT OR REPLACE INTO group_games_db (game_id, host_id, chat_id, players, current_player, stage, temp_category, current_question_type, current_question_text, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (game_id, game['host_id'], game.get('chat_id'), json.dumps(game['players']), game['current_player'], game.get('stage', 'idle'), game.get('temp_category'), game.get('current_question_type'), game.get('current_question_text'), game['status'], game['created_at']))
    conn.commit()
    return True

def load_group_games():
    cursor.execute("SELECT * FROM group_games_db")
    rows = cursor.fetchall()
    for row in rows:
        group_games[row[0]] = {
            'host_id': row[1],
            'chat_id': row[2],
            'players': json.loads(row[3]),
            'current_player': row[4],
            'stage': row[5],
            'temp_category': row[6],
            'current_question_type': row[7],
            'current_question_text': row[8],
            'status': row[9],
            'created_at': row[10]
        }

def get_or_load_game(game_id):
    if game_id in group_games:
        return group_games[game_id]
    cursor.execute("SELECT * FROM group_games_db WHERE game_id = ?", (game_id,))
    row = cursor.fetchone()
    if row:
        game = {
            'game_id': row[0],
            'host_id': row[1],
            'chat_id': row[2],
            'players': json.loads(row[3]),
            'current_player': row[4],
            'stage': row[5],
            'temp_category': row[6],
            'current_question_type': row[7],
            'current_question_text': row[8],
            'status': row[9],
            'created_at': row[10]
        }
        group_games[game_id] = game
        return game
    return None

def get_host_of_game(game_id):
    game = get_or_load_game(game_id)
    if game:
        return game['host_id']
    return None

def is_host_of_game(game_id, user_id):
    host = get_host_of_game(game_id)
    return host is not None and host == user_id

def get_category_name(cat):
    names = {
        'boy_normal': '👨 پسر عادی',
        'girl_normal': '👩 دختر عادی',
        'boy_18': '🔞 پسر +۱۸',
        'girl_18': '🔞 دختر +۱۸'
    }
    return names.get(cat, cat)

def edit_or_send_message(call_or_msg, text, reply_markup=None, parse_mode='HTML'):
    try:
        if hasattr(call_or_msg, 'inline_message_id') and call_or_msg.inline_message_id:
            return bot.edit_message_text(
                text, 
                inline_message_id=call_or_msg.inline_message_id,
                reply_markup=reply_markup, 
                parse_mode=parse_mode
            )
        elif hasattr(call_or_msg, 'message') and call_or_msg.message:
            return bot.edit_message_text(
                text, 
                call_or_msg.message.chat.id, 
                call_or_msg.message.message_id,
                reply_markup=reply_markup, 
                parse_mode=parse_mode
            )
        elif isinstance(call_or_msg, tuple) and len(call_or_msg) == 2:
            return bot.edit_message_text(
                text, 
                call_or_msg[0], 
                call_or_msg[1],
                reply_markup=reply_markup, 
                parse_mode=parse_mode
            )
        else:
            print(f"[ERROR] Invalid target for edit_or_send_message: {call_or_msg}")
            return None
    except Exception as e:
        if "there is no text in the message to edit" in str(e):
            try:
                if hasattr(call_or_msg, 'message') and call_or_msg.message:
                    return bot.send_message(call_or_msg.message.chat.id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            except:
                pass
        print(f"[ERROR] edit_or_send_message failed: {e}")
        return None

def send_or_reply(chat_id, text, reply_markup=None, parse_mode='HTML'):
    try:
        return bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        print(f"[ERROR] send_or_reply failed: {e}")
        return None

def group_category_keyboard(game_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👩 دختر عادی", callback_data=f"group_cat|{game_id}|girl_normal"),
        InlineKeyboardButton("👨 پسر عادی", callback_data=f"group_cat|{game_id}|boy_normal")
    )
    kb.add(
        InlineKeyboardButton("👩 دختر +۱۸ 🔞", callback_data=f"group_cat|{game_id}|girl_18")
    )
    kb.add(
        InlineKeyboardButton("👨 پسر +۱۸ 🔞", callback_data=f"group_cat|{game_id}|boy_18"),
        InlineKeyboardButton("🎲 شانسی", callback_data=f"group_cat|{game_id}|random")
    )
    return kb

def group_type_keyboard(game_id, category):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎲 جرعت", callback_data=f"group_type|{game_id}|{category}|dare"),
        InlineKeyboardButton("📖 حقیقت", callback_data=f"group_type|{game_id}|{category}|truth")
    )
    kb.add(
        InlineKeyboardButton("🎲 شانسی", callback_data=f"group_type|{game_id}|{category}|random")
    )
    kb.add(
        InlineKeyboardButton("🔙 بازگشت", callback_data=f"group_back_cat|{game_id}", style="primary")
    )
    return kb

def group_question_keyboard(game_id, question_type, is_host=False):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ جواب دادم", callback_data=f"group_answered|{game_id}", style="success"),
        InlineKeyboardButton("🔄 رد کردن", callback_data=f"group_skip|{game_id}", style="danger")
    )
    kb.add(
        InlineKeyboardButton("🎲 تعویض سوال", callback_data=f"group_new_question|{game_id}|{question_type}", style="primary")
    )
    if is_host:
        kb.add(
            InlineKeyboardButton("❌ حذف بازیکن", callback_data=f"group_kick_menu|{game_id}", style="danger"),
            InlineKeyboardButton("🔚 پایان بازی", callback_data=f"group_end|{game_id}", style="danger")
        )
    else:
        kb.add(
            InlineKeyboardButton("🔚 پایان بازی", callback_data=f"group_end|{game_id}", style="danger")
        )
    return kb

def group_host_controls_keyboard(game_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("❌ حذف بازیکن", callback_data=f"group_kick_menu|{game_id}", style="danger"),
        InlineKeyboardButton("🔚 پایان بازی", callback_data=f"group_end|{game_id}", style="danger")
    )
    return kb

def group_kick_player_keyboard(game_id, players, host_id):
    kb = InlineKeyboardMarkup(row_width=1)
    for p in players:
        if p != host_id:
            kb.add(InlineKeyboardButton(f"❌ حذف {get_user_name(p)}", callback_data=f"group_kick|{game_id}|{p}", style="danger"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data=f"group_back_menu|{game_id}", style="primary"))
    return kb

def group_waiting_keyboard(game_id, host_id, user_id, player_count):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("✅ حاضرم", callback_data=f"group_ready|{game_id}", style="success"))
    if user_id == host_id:
        kb.add(InlineKeyboardButton("🚀 شروع بازی", callback_data=f"group_start|{game_id}", style="primary"))
    return kb

def group_waiting_keyboard_for_edit(game_id, host_id, player_count):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("✅ حاضرم", callback_data=f"group_ready|{game_id}", style="success"))
    kb.add(InlineKeyboardButton("🚀 شروع بازی", callback_data=f"group_start|{game_id}", style="primary"))
    return kb

def get_waiting_message_text(game):
    players_list = "\n".join([f"• {get_user_name(p)}" for p in game['players']])
    text = f"""
🎳 <b>بازی جرئت و حقیقت</b>

✅ برای شرکت در بازی ابتدا در کانال‌های ربات عضو شوید.

➖➖➖➖➖➖➖➖➖
<b>👑 میزبان بازی</b>
{get_user_name(game['host_id'])}

<b>👥 شرکت‌کنندگان</b> ({len(game['players'])} نفر)
{players_list if players_list else '• هنوز کسی نیومده...'}
➖➖➖➖➖➖➖➖➖

❗️ <b>تنها میزبان بازی می‌تواند بازی را شروع کند.</b>

✅ روی دکمه «حاضرم» کلیک کنید تا به بازی اضافه شوید.
"""
    return text

def get_start_game_message(game):
    text = f"""
🎮 <b>بازی جرئت و حقیقت شروع شد!</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}

📌 لطفاً دسته سوال را انتخاب کنید:
➖➖➖➖➖➖➖➖➖
"""
    return text

def main_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎮 بازی", callback_data="games_menu", style="primary")
    )
    kb.add(
        InlineKeyboardButton("📝 ارسال سوال", callback_data="new_question", style="primary"),
        InlineKeyboardButton("📖 راهنما", callback_data="help_menu", style="primary")
    )
    kb.add(
        InlineKeyboardButton("💰 درآمد از ربات", callback_data="income_menu", style="success")
    )
    kb.add(
        InlineKeyboardButton("👤 پروفایل من", callback_data="my_profile", style="primary"),
        InlineKeyboardButton("🛍 فروشگاه ما", callback_data="store_menu", style="primary")
    )
    kb.add(
        InlineKeyboardButton("🏆 لیدربرد", callback_data="leaderboard_menu", style="primary"),
        InlineKeyboardButton("🛟 پشتیبانی", callback_data="support_menu", style="primary")
    )
    return kb

def games_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎭 بازی ناشناس", callback_data="start_anonymous", style="primary"),
        InlineKeyboardButton("👥 بازی گروهی", switch_inline_query="بازی", style="success")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def leaderboard_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("❤️ نفرات برتر لایک", callback_data="top_likes", style="success"),
        InlineKeyboardButton("👥 برترین دعوت‌کنندگان", callback_data="top_referrers", style="primary")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def profile_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👤 تغییر نام مستعار", callback_data="profile_change_nickname", style="primary"),
        InlineKeyboardButton("🎂 تغییر سن", callback_data="profile_change_age", style="primary"),
        InlineKeyboardButton("🔄 تغییر جنسیت", callback_data="profile_change_gender", style="primary"),
        InlineKeyboardButton("🎯 تغییر ترجیح بازی", callback_data="profile_change_preference", style="primary"),
        InlineKeyboardButton("📸 تغییر عکس پروفایل", callback_data="profile_change_photo", style="primary"),
        InlineKeyboardButton("📍 ثبت لوکیشن", callback_data="register_location", style="success")
    )
    kb.add(
        InlineKeyboardButton("❤️ لایک شده‌ها", callback_data="profile_likes", style="primary"),
        InlineKeyboardButton("🚫 بلاک شده‌ها", callback_data="profile_blocks", style="danger")
    )
    kb.add(InlineKeyboardButton("⭐️ افراد دلخواه من", callback_data="my_favorites", style="primary"))
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def store_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

# ================== کیبوردهای ۲×۲ برای لیدربرد ==================
def top_likes_keyboard(rows, viewer_id):
    kb = InlineKeyboardMarkup(row_width=2)  # ۲ ستون
    for uid, cnt in rows:
        if uid == viewer_id:
            continue
        name = get_user_name(uid)
        # دکمه با اسم کاربر - با کلیک پروفایل نمایش داده میشه
        kb.add(InlineKeyboardButton(f"👤 {name}", callback_data=f"view_profile|{uid}", style="primary"))
    kb.add(InlineKeyboardButton("🔙 بازگشت به لیدربرد", callback_data="leaderboard_menu", style="primary"))
    return kb

def top_referrers_keyboard(rows, viewer_id):
    kb = InlineKeyboardMarkup(row_width=2)  # ۲ ستون
    for uid, cnt in rows:
        if uid == viewer_id:
            continue
        name = get_user_name(uid)
        kb.add(InlineKeyboardButton(f"👤 {name}", callback_data=f"view_profile|{uid}", style="primary"))
    kb.add(InlineKeyboardButton("🔙 بازگشت به لیدربرد", callback_data="leaderboard_menu", style="primary"))
    return kb

def favorites_keyboard(favorite_ids, viewer_id):
    kb = InlineKeyboardMarkup(row_width=1)
    for uid in favorite_ids:
        name = get_user_name(uid)
        kb.add(InlineKeyboardButton(f"👤 {name}", callback_data=f"view_profile|{uid}", style="primary"))
        kb.add(InlineKeyboardButton(f"❌ حذف {name} از لیست", callback_data=f"fav_remove|{uid}", style="danger"))
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def public_profile_keyboard(target_id, viewer_id):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🎮 درخواست بازی", callback_data=f"req_game|{target_id}", style="primary"))
    if is_favorite(viewer_id, target_id):
        kb.add(InlineKeyboardButton("⭐️ در لیست دلخواه (حذف)", callback_data=f"fav_remove|{target_id}", style="danger"))
    else:
        kb.add(InlineKeyboardButton("➕ افزودن به افراد دلخواه", callback_data=f"fav_add|{target_id}", style="success"))
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def income_keyboard(can_withdraw):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔗 دریافت لینک دعوت", callback_data="get_referral_link", style="primary"))
    if can_withdraw:
        kb.add(InlineKeyboardButton("💸 درخواست تسویه", callback_data="request_withdraw", style="success"))
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def province_keyboard(back_callback="my_profile"):
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(p, callback_data=f"loc_prov|{i}|{back_callback}") for i, p in enumerate(IRAN_PROVINCES)]
    kb.add(*buttons)
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data=back_callback, style="primary"))
    return kb

def city_keyboard(prov_idx, back_callback="my_profile"):
    kb = InlineKeyboardMarkup(row_width=2)
    province = IRAN_PROVINCES[prov_idx]
    cities = IRAN_LOCATIONS[province]
    buttons = [InlineKeyboardButton(c, callback_data=f"loc_city|{prov_idx}|{i}|{back_callback}") for i, c in enumerate(cities)]
    kb.add(*buttons)
    kb.add(InlineKeyboardButton("🔙 بازگشت به لیست استان‌ها", callback_data=f"register_location|{back_callback}", style="primary"))
    return kb

def anon_location_scope_keyboard(user_id):
    user = get_user(user_id)
    has_city = bool(user and len(user) > 16 and user[16])
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🌍 هرکسی (بدون محدودیت مکانی)", callback_data="anon_loc_any", style="primary"))
    if has_city:
        kb.add(InlineKeyboardButton("🏙 فقط هم‌شهری", callback_data="anon_loc_city"))
        kb.add(InlineKeyboardButton("🗺 فقط هم‌استانی", callback_data="anon_loc_province"))
    else:
        kb.add(InlineKeyboardButton("📍 برای فیلتر هم‌شهری/هم‌استانی، اول لوکیشن ثبت کن", callback_data="register_location|anon_location"))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_start", style="primary"))
    return kb

def admin_main_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("📊 آمار", callback_data="admin_stats", style="primary"),
        InlineKeyboardButton("👤 اطلاعات کاربر", callback_data="admin_user_info", style="primary"),
        InlineKeyboardButton("🚫 بن/آنبن", callback_data="admin_ban_user", style="danger")
    )
    kb.add(
        InlineKeyboardButton("📨 پیام به کاربر", callback_data="admin_msg_user", style="primary"),
        InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin_broadcast", style="primary"),
        InlineKeyboardButton("🔄 فوروارد", callback_data="admin_forward", style="primary")
    )
    kb.add(
        InlineKeyboardButton("➕ افزودن سوال", callback_data="admin_add_question", style="success"),
        InlineKeyboardButton("❌ حذف سوال", callback_data="admin_del_question", style="danger")
    )
    kb.add(
        InlineKeyboardButton("👁 همه سوالات", callback_data="admin_view_questions", style="primary"),
        InlineKeyboardButton("📄 گزارش کامل سوالات", callback_data="admin_export_questions", style="primary"),
        InlineKeyboardButton("⏳ در انتظار تایید", callback_data="admin_pending_questions", style="primary")
    )
    kb.add(
        InlineKeyboardButton("🔗 جوین اجباری", callback_data="admin_force_join_menu", style="primary"),
        InlineKeyboardButton("👑 مدیریت ادمین", callback_data="admin_admins_menu", style="primary"),
        InlineKeyboardButton("💾 بک آپ", callback_data="admin_backup", style="primary")
    )
    kb.add(
        InlineKeyboardButton("✅ تایید همه سوالات", callback_data="admin_approve_all", style="success"),
        InlineKeyboardButton("📚 مدیریت راهنما", callback_data="admin_help_menu", style="primary"),
        InlineKeyboardButton("🛍 متن فروشگاه", callback_data="admin_edit_store", style="primary")
    )
    kb.add(
        InlineKeyboardButton("💸 درخواست‌های تسویه", callback_data="admin_withdrawals", style="primary")
    )
    return kb

def admin_help_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📋 لیست دکمه‌ها", callback_data="admin_help_list", style="primary"),
        InlineKeyboardButton("➕ افزودن دکمه جدید", callback_data="admin_help_add", style="success"),
        InlineKeyboardButton("✏️ ویرایش دکمه", callback_data="admin_help_edit"),
        InlineKeyboardButton("❌ حذف دکمه", callback_data="admin_help_delete", style="danger"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")
    )
    return kb

def admin_help_buttons_keyboard(buttons, action):
    kb = InlineKeyboardMarkup(row_width=1)
    for btn in buttons:
        btn_id, btn_text, btn_callback, content, order, is_active = btn
        status = "✅" if is_active else "❌"
        row_style = "success" if is_active else "danger"
        kb.add(InlineKeyboardButton(f"{status} {btn_text[:30]}", callback_data=f"admin_help_{action}_{btn_id}", style=row_style))
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="admin_help_menu", style="primary"))
    return kb

def admin_back_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔙 بازگشت به پنل ادمین", callback_data="admin_back", style="primary"))
    return kb

def admin_force_join_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ افزودن کانال", callback_data="admin_add_channel", style="success"),
        InlineKeyboardButton("❌ حذف کانال", callback_data="admin_remove_channel", style="danger"),
        InlineKeyboardButton("📋 لیست کانال‌ها", callback_data="admin_list_channels", style="primary"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")
    )
    return kb

def admin_admins_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_add_admin", style="success"),
        InlineKeyboardButton("❌ حذف ادمین", callback_data="admin_remove_admin", style="danger"),
        InlineKeyboardButton("📋 لیست ادمین‌ها", callback_data="admin_list_admins", style="primary"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")
    )
    return kb

def admin_add_method_keyboard(category, q_type):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ افزودن تکی", callback_data=f"admin_single_{category}_{q_type}", style="success"),
        InlineKeyboardButton("📚 افزودن دسته‌جمعی", callback_data=f"admin_batch_{category}_{q_type}", style="success")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="admin_add_question", style="primary"))
    return kb

def category_keyboard(prefix="cat"):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👨 پسر عادی", callback_data=f"{prefix}_boy_normal"),
        InlineKeyboardButton("👩 دختر عادی", callback_data=f"{prefix}_girl_normal"),
        InlineKeyboardButton("🔞 پسر ۱۸+", callback_data=f"{prefix}_boy_18"),
        InlineKeyboardButton("🔞 دختر ۱۸+", callback_data=f"{prefix}_girl_18")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def question_type_keyboard(category, prefix="q"):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎲 جرعت", callback_data=f"{prefix}_dare_{category}"),
        InlineKeyboardButton("📖 حقیقت", callback_data=f"{prefix}_truth_{category}")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="new_question", style="primary"))
    return kb

def anon_gender_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👨 مرد", callback_data="anon_gender_male"),
        InlineKeyboardButton("👩 زن", callback_data="anon_gender_female")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_start", style="primary"))
    return kb

def anon_preferred_gender_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👨 فقط مرد", callback_data="anon_pref_male"),
        InlineKeyboardButton("👩 فقط زن", callback_data="anon_pref_female"),
        InlineKeyboardButton("👥 فرقی نمی‌کند", callback_data="anon_pref_both")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_start", style="primary"))
    return kb

def anon_category_keyboard(match_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👨 پسر عادی", callback_data=f"anon_cat|{match_id}|boy_normal"),
        InlineKeyboardButton("👩 دختر عادی", callback_data=f"anon_cat|{match_id}|girl_normal"),
        InlineKeyboardButton("🔞 پسر ۱۸+", callback_data=f"anon_cat|{match_id}|boy_18"),
        InlineKeyboardButton("🔞 دختر ۱۸+", callback_data=f"anon_cat|{match_id}|girl_18"),
        InlineKeyboardButton("🎲 رندوم", callback_data=f"anon_cat|{match_id}|random")
    )
    kb.add(InlineKeyboardButton("🔚 پایان بازی", callback_data=f"anon_end|{match_id}", style="danger"))
    return kb

def anon_question_type_keyboard(match_id, category):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🎲 جرعت", callback_data=f"anon_question|{match_id}|{category}|dare"),
        InlineKeyboardButton("📖 حقیقت", callback_data=f"anon_question|{match_id}|{category}|truth")
    )
    kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data=f"anon_back_to_cat|{match_id}", style="primary"))
    kb.add(InlineKeyboardButton("🔚 پایان بازی", callback_data=f"anon_end|{match_id}", style="danger"))
    return kb

def anon_game_keyboard(match_id, is_my_turn):
    kb = InlineKeyboardMarkup(row_width=2)
    if is_my_turn:
        kb.add(InlineKeyboardButton("🎲 انتخاب سوال", callback_data=f"anon_pick_question|{match_id}", style="primary"))
    else:
        kb.add(InlineKeyboardButton("⏳ نوبت شما نیست", callback_data="anon_no_turn"))
    
    kb.add(
        InlineKeyboardButton("📸 پروفایل همبازی", callback_data=f"anon_show_profile|{match_id}", style="primary"),
        InlineKeyboardButton("❤️ لایک", callback_data=f"anon_like|{match_id}", style="success"),
        InlineKeyboardButton("🚫 بلاک", callback_data=f"anon_block|{match_id}", style="danger")
    )
    kb.add(InlineKeyboardButton("🔚 پایان بازی", callback_data=f"anon_end|{match_id}", style="danger"))
    return kb

def anon_question_keyboard(match_id, question_type):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ جواب دادم", callback_data=f"anon_answered|{match_id}", style="success"),
        InlineKeyboardButton("🔄 رد کردن", callback_data=f"anon_skip|{match_id}", style="danger")
    )
    kb.add(InlineKeyboardButton("🎲 سوال جدید", callback_data=f"anon_change|{match_id}|{question_type}", style="primary"))
    kb.add(InlineKeyboardButton("🔚 پایان بازی", callback_data=f"anon_end|{match_id}", style="danger"))
    return kb

def force_join_info_keyboard():
    channels = get_forced_channels()
    kb = InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        kb.add(InlineKeyboardButton(f"📢 عضویت در @{ch[0]}", url=ch[1], style="primary"))
    kb.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary"))
    return kb

def add_to_queue(user_id, gender, preferred_gender, location_scope='any'):
    cursor.execute("INSERT OR REPLACE INTO anon_queue (user_id, gender, preferred_gender, join_time, location_scope) VALUES (?, ?, ?, ?, ?)",
                   (user_id, gender, preferred_gender, int(time.time()), location_scope))
    conn.commit()

def remove_from_queue(user_id):
    cursor.execute("DELETE FROM anon_queue WHERE user_id = ?", (user_id,))
    conn.commit()

def find_match(user_id, gender, preferred_gender, location_scope='any'):
    my_province, my_city = get_user_location(user_id)
    params = [user_id]
    if preferred_gender == 'both':
        base = '''SELECT q.user_id FROM anon_queue q JOIN users u ON u.id = q.user_id
                  WHERE q.user_id != ? AND (q.preferred_gender = ? OR q.preferred_gender = 'both')'''
        params.append(gender)
    else:
        base = '''SELECT q.user_id FROM anon_queue q JOIN users u ON u.id = q.user_id
                  WHERE q.user_id != ? AND q.preferred_gender IN (?, 'both') AND q.gender = ?'''
        params.append(gender)
        params.append(preferred_gender)

    if location_scope == 'city' and my_city:
        base += " AND u.city = ?"
        params.append(my_city)
    elif location_scope == 'province' and my_province:
        base += " AND u.province = ?"
        params.append(my_province)

    base += " ORDER BY q.join_time ASC LIMIT 1"
    cursor.execute(base, tuple(params))
    row = cursor.fetchone()
    return row[0] if row else None

def create_anonymous_match(player1, player2):
    match_id = f"anon_{uuid.uuid4().hex[:12]}"
    cursor.execute("INSERT INTO anon_matches (match_id, player1, player2, current_turn, status, created_at, last_activity) VALUES (?, ?, ?, ?, 'playing', ?, ?)",
                   (match_id, player1, player2, player1, int(time.time()), int(time.time())))
    conn.commit()
    return match_id

def get_anonymous_match(match_id):
    cursor.execute("SELECT * FROM anon_matches WHERE match_id = ?", (match_id,))
    row = cursor.fetchone()
    if row:
        return {
            'match_id': row[0],
            'player1': row[1],
            'player2': row[2],
            'current_turn': row[3],
            'current_question_type': row[4],
            'current_question_text': row[5],
            'status': row[6],
            'created_at': row[7],
            'last_activity': row[8]
        }
    return None

def update_anonymous_match(match_id, data):
    fields = []
    params = []
    if 'current_turn' in data:
        fields.append("current_turn = ?")
        params.append(data['current_turn'])
    if 'current_question_type' in data:
        fields.append("current_question_type = ?")
        params.append(data['current_question_type'])
    if 'current_question_text' in data:
        fields.append("current_question_text = ?")
        params.append(data['current_question_text'])
    if 'status' in data:
        fields.append("status = ?")
        params.append(data['status'])
    fields.append("last_activity = ?")
    params.append(int(time.time()))
    params.append(match_id)
    cursor.execute(f"UPDATE anon_matches SET {', '.join(fields)} WHERE match_id = ?", params)
    conn.commit()

def delete_anonymous_match(match_id):
    cursor.execute("DELETE FROM anon_matches WHERE match_id = ?", (match_id,))
    cursor.execute("DELETE FROM anon_chat_history WHERE match_id = ?", (match_id,))
    conn.commit()

def get_other_player_in_match(match_id, user_id):
    match = get_anonymous_match(match_id)
    if not match:
        return None
    if match['player1'] == user_id:
        return match['player2']
    elif match['player2'] == user_id:
        return match['player1']
    return None

def save_chat_message(match_id, sender_id, message_text, message_type='text', file_id=None):
    data = json.dumps({'type': message_type, 'text': message_text, 'file_id': file_id})
    cursor.execute("INSERT INTO anon_chat_history (match_id, sender_id, message, created_at) VALUES (?, ?, ?, ?)",
                   (match_id, sender_id, data, int(time.time())))
    conn.commit()

def get_chat_history(match_id, limit=50):
    cursor.execute("SELECT sender_id, message, created_at FROM anon_chat_history WHERE match_id = ? ORDER BY created_at DESC LIMIT ?", (match_id, limit))
    rows = cursor.fetchall()
    return list(reversed(rows))

def start_anon_search_timer(user_id, gender, preferred_gender, location_scope='any'):
    def timer_func():
        time.sleep(30)
        cursor.execute("SELECT user_id FROM anon_queue WHERE user_id = ?", (user_id,))
        if cursor.fetchone():
            other = find_match(user_id, gender, preferred_gender, location_scope)
            if not other and location_scope == 'any':
                cursor.execute("SELECT user_id FROM anon_queue WHERE user_id != ? ORDER BY RANDOM() LIMIT 1", (user_id,))
                row = cursor.fetchone()
                other = row[0] if row else None
            if other:
                remove_from_queue(user_id)
                remove_from_queue(other)
                match_id = create_anonymous_match(user_id, other)
                match = get_anonymous_match(match_id)
                if match:
                    for pid in [user_id, other]:
                        is_my_turn = (match['current_turn'] == pid)
                        turn_text = "نوبت شماست." if is_my_turn else "نوبت همبازی شماست."
                        try:
                            bot.send_message(pid, f"🎭 <b>همبازی پیدا شد!</b>\n\nبازی ناشناس شروع شد.\n\n{turn_text}\n\nبرای شروع روی دکمه «انتخاب سوال» کلیک کنید.",
                                            reply_markup=anon_game_keyboard(match_id, is_my_turn))
                        except:
                            pass
                return
        time.sleep(30)
        cursor.execute("SELECT user_id FROM anon_queue WHERE user_id = ?", (user_id,))
        if cursor.fetchone():
            remove_from_queue(user_id)
            try:
                bot.send_message(user_id, f"⏰ <b>زمان جستجو به پایان رسید!</b>\n\nبازیکنی پیدا نشد.\nلطفاً دوباره تلاش کنید.",
                                reply_markup=main_menu_keyboard())
            except:
                pass
    timer = threading.Thread(target=timer_func, daemon=True)
    timer.start()
    anon_search_timers[user_id] = timer

def get_start_message():
    return f"""
✨ <b>به ربات جرعت و حقیقت خوش اومدی!</b> ✨

🔮 اینجا می‌تونی به دو صورت بازی کنی:
🎭 <b>بازی ناشناس</b> با یک همبازی تصادفی
👥 <b>بازی گروهی</b> در گروه‌ها و چت‌های خصوصی

👇 برای شروع روی دکمه‌های زیر کلیک کن:
"""

def get_user_likes(user_id):
    cursor.execute("""
        SELECT l.liked_by, u.nickname, u.first_name, u.username 
        FROM likes l 
        JOIN users u ON l.liked_by = u.id 
        WHERE l.user_id = ? 
        ORDER BY l.created_at DESC
    """, (user_id,))
    return cursor.fetchall()

def get_user_blocks(user_id):
    cursor.execute("""
        SELECT b.blocked_user, u.nickname, u.first_name, u.username 
        FROM blocked_users b 
        JOIN users u ON b.blocked_user = u.id 
        WHERE b.user_id = ? 
        ORDER BY b.created_at DESC
    """, (user_id,))
    return cursor.fetchall()

def save_like(user_id, liked_by, match_id):
    cursor.execute("INSERT INTO likes (user_id, liked_by, match_id, created_at) VALUES (?, ?, ?, ?)",
                   (user_id, liked_by, match_id, int(time.time())))
    conn.commit()

def save_block(user_id, blocked_user, match_id):
    cursor.execute("INSERT INTO blocked_users (user_id, blocked_user, match_id, created_at) VALUES (?, ?, ?, ?)",
                   (user_id, blocked_user, match_id, int(time.time())))
    conn.commit()

def get_bot_setting(key, default=None):
    cursor.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default

def set_bot_setting(key, value):
    cursor.execute("INSERT INTO bot_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))
    conn.commit()

def get_referral_count(user_id):
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    return cursor.fetchone()[0]

def get_wallet_balance(user_id):
    cursor.execute("SELECT wallet_balance FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row and row[0] is not None else 0

def register_referral(new_user_id, referrer_id):
    if referrer_id == new_user_id:
        return False
    cursor.execute("SELECT referred_by FROM users WHERE id = ?", (new_user_id,))
    row = cursor.fetchone()
    if not row or row[0] is not None:
        return False
    cursor.execute("SELECT id FROM users WHERE id = ?", (referrer_id,))
    if not cursor.fetchone():
        return False
    cursor.execute("UPDATE users SET referred_by = ? WHERE id = ?", (referrer_id, new_user_id))
    cursor.execute("UPDATE users SET wallet_balance = COALESCE(wallet_balance, 0) + ? WHERE id = ?",
                   (REFERRAL_REWARD, referrer_id))
    conn.commit()
    return True

def get_top_referrers(limit=10):
    cursor.execute('''
        SELECT referred_by, COUNT(*) as cnt
        FROM users
        WHERE referred_by IS NOT NULL
        GROUP BY referred_by
        ORDER BY cnt DESC
        LIMIT ?
    ''', (limit,))
    return cursor.fetchall()

def get_top_liked_users(limit=10, exclude_user=None):
    cursor.execute('''
        SELECT user_id, COUNT(*) as cnt
        FROM likes
        GROUP BY user_id
        ORDER BY cnt DESC
        LIMIT ?
    ''', (limit + 1,))
    rows = cursor.fetchall()
    if exclude_user is not None:
        rows = [r for r in rows if r[0] != exclude_user]
    return rows[:limit]

def create_withdrawal_request(user_id, amount):
    cursor.execute("INSERT INTO withdrawal_requests (user_id, amount, status, created_at) VALUES (?, ?, 'pending', ?)",
                   (user_id, amount, int(time.time())))
    conn.commit()
    return cursor.lastrowid

def get_withdrawal_request(req_id):
    cursor.execute("SELECT id, user_id, amount, status FROM withdrawal_requests WHERE id = ?", (req_id,))
    return cursor.fetchone()

def mark_withdrawal_paid(req_id, admin_id):
    req = get_withdrawal_request(req_id)
    if not req or req[3] != 'pending':
        return False
    _, uid, amount, _ = req
    cursor.execute("UPDATE withdrawal_requests SET status='paid', processed_at=?, processed_by=? WHERE id=?",
                   (int(time.time()), admin_id, req_id))
    cursor.execute("UPDATE users SET wallet_balance = MAX(COALESCE(wallet_balance,0) - ?, 0) WHERE id = ?", (amount, uid))
    conn.commit()
    return True

def update_user_location(user_id, lat, lon):
    cursor.execute("UPDATE users SET latitude = ?, longitude = ? WHERE id = ?", (lat, lon, user_id))
    conn.commit()

def update_user_location_city(user_id, province, city):
    cursor.execute("UPDATE users SET province = ?, city = ? WHERE id = ?", (province, city, user_id))
    conn.commit()

def get_user_location(user_id):
    cursor.execute("SELECT province, city FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)

def update_user_age(user_id, age):
    cursor.execute("UPDATE users SET age = ? WHERE id = ?", (age, user_id))
    conn.commit()

def add_favorite(user_id, favorite_user_id):
    if user_id == favorite_user_id:
        return False
    cursor.execute("INSERT OR IGNORE INTO favorites (user_id, favorite_user_id, created_at) VALUES (?, ?, ?)",
                   (user_id, favorite_user_id, int(time.time())))
    conn.commit()
    return True

def remove_favorite(user_id, favorite_user_id):
    cursor.execute("DELETE FROM favorites WHERE user_id = ? AND favorite_user_id = ?", (user_id, favorite_user_id))
    conn.commit()
    return True

def get_favorites(user_id):
    cursor.execute("SELECT favorite_user_id FROM favorites WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    return [row[0] for row in cursor.fetchall()]

def is_favorite(user_id, favorite_user_id):
    cursor.execute("SELECT 1 FROM favorites WHERE user_id = ? AND favorite_user_id = ?", (user_id, favorite_user_id))
    return cursor.fetchone() is not None

def create_game_request(from_user, to_user):
    cursor.execute("INSERT INTO game_requests (from_user, to_user, status, created_at) VALUES (?, ?, 'pending', ?)",
                   (from_user, to_user, int(time.time())))
    conn.commit()
    return cursor.lastrowid

def get_game_request(req_id):
    cursor.execute("SELECT id, from_user, to_user, status FROM game_requests WHERE id = ?", (req_id,))
    return cursor.fetchone()

def update_game_request_status(req_id, status):
    cursor.execute("UPDATE game_requests SET status = ? WHERE id = ?", (status, req_id))
    conn.commit()

def export_questions_to_text():
    cursor.execute("""
        SELECT id, category, type, text, status, created_at 
        FROM questions 
        ORDER BY category, type, id
    """)
    questions = cursor.fetchall()
    
    cat_names = {
        'boy_normal': 'پسر عادی',
        'girl_normal': 'دختر عادی', 
        'boy_18': 'پسر +۱۸',
        'girl_18': 'دختر +۱۸'
    }
    
    lines = []
    lines.append("=" * 60)
    lines.append("📋 گزارش کامل سوالات ربات جرعت و حقیقت")
    lines.append(f"📅 تاریخ: {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}")
    lines.append(f"📊 تعداد کل سوالات: {len(questions)}")
    lines.append("=" * 60)
    lines.append("")
    
    total_by_category = {}
    total_by_type = {'جرعت': 0, 'حقیقت': 0}
    
    for cat in ['boy_normal', 'girl_normal', 'boy_18', 'girl_18']:
        total_by_category[cat] = 0
    
    current_cat = None
    for q in questions:
        q_id, category, q_type, text, status, created_at = q
        cat_name = cat_names.get(category, category)
        
        if current_cat != category:
            current_cat = category
            lines.append("")
            lines.append(f"📂 {cat_name}")
            lines.append("-" * 40)
        
        status_text = "✅ تایید شده" if status == 'approved' else "⏳ در انتظار"
        lines.append(f"#{q_id} | {q_type} | {text[:50]}..." if len(text) > 50 else f"#{q_id} | {q_type} | {text}")
        lines.append(f"   وضعیت: {status_text}")
        
        total_by_category[category] = total_by_category.get(category, 0) + 1
        if q_type == 'جرعت':
            total_by_type['جرعت'] += 1
        else:
            total_by_type['حقیقت'] += 1
    
    lines.append("")
    lines.append("=" * 60)
    lines.append("📊 خلاصه آمار:")
    lines.append("-" * 40)
    for cat, name in cat_names.items():
        count = total_by_category.get(cat, 0)
        lines.append(f"{name}: {count} سوال")
    lines.append("")
    lines.append(f"🎲 جرعت: {total_by_type['جرعت']} سوال")
    lines.append(f"📖 حقیقت: {total_by_type['حقیقت']} سوال")
    lines.append("=" * 60)
    
    return "\n".join(lines)

@bot.message_handler(commands=['start'])
def start_command(message):
    user = message.from_user
    if is_banned(user.id):
        bot.reply_to(message, "🚫 شما توسط ادمین مسدود شده‌اید!")
        return

    is_new_user = get_user(user.id) is None
    create_user(user.id, user.username, user.first_name)

    parts = message.text.split(maxsplit=1) if message.text else []
    if is_new_user and len(parts) > 1:
        payload = parts[1].strip()
        ref_match = re.match(r'ref[_-]?(\d+)', payload)
        if ref_match:
            referrer_id = int(ref_match.group(1))
            try:
                if register_referral(user.id, referrer_id):
                    try:
                        bot.send_message(referrer_id, f"🎉 یک کاربر جدید با لینک دعوت شما وارد ربات شد!\n💰 مبلغ {REFERRAL_REWARD} تومن به کیف پول شما اضافه شد.")
                    except:
                        pass
            except Exception as e:
                print(f"[ERROR] referral registration failed: {e}")

    if is_force_join_enabled():
        joined, not_joined = check_user_joined_channels(user.id)
        if not joined:
            kb = get_force_join_keyboard(not_joined, "check_join_start")
            bot.reply_to(message, f"🔒 <b>برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شوید:</b>", 
                        reply_markup=kb)
            return
    bot.reply_to(message, get_start_message(), parse_mode='HTML', reply_markup=main_menu_keyboard())

@bot.message_handler(content_types=['location'])
def handle_location_message(message):
    user_id = message.from_user.id
    if not message.location:
        return
    update_user_location(user_id, message.location.latitude, message.location.longitude)
    from telebot.types import ReplyKeyboardRemove
    bot.send_message(user_id, "✅ لوکیشن شما با موفقیت ثبت شد.", reply_markup=ReplyKeyboardRemove())
    bot.send_message(user_id, get_start_message(), parse_mode='HTML', reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ شما دسترسی به این بخش ندارید!")
        return
    bot.reply_to(message, f"🔐 <b>پنل مدیریت ربات</b>\n\n✨ از اینجا می‌تونی ربات رو مدیریت کنی:", 
                 reply_markup=admin_main_menu_keyboard())

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'animation', 'sticker', 'voice', 'audio', 'document', 'video_note'])
def handle_anon_chat_message(message):
    user_id = message.from_user.id
    cursor.execute("SELECT match_id FROM anon_matches WHERE (player1 = ? OR player2 = ?) AND status = 'playing'", (user_id, user_id))
    row = cursor.fetchone()
    if row:
        match_id = row[0]
        match = get_anonymous_match(match_id)
        if match and match['status'] == 'playing':
            other = get_other_player_in_match(match_id, user_id)
            if other:
                if message.text:
                    save_chat_message(match_id, user_id, message.text, 'text')
                elif message.photo:
                    save_chat_message(match_id, user_id, "عکس", 'photo', message.photo[-1].file_id)
                elif message.video:
                    save_chat_message(match_id, user_id, "ویدئو", 'video', message.video.file_id)
                elif message.animation:
                    save_chat_message(match_id, user_id, "گیف", 'animation', message.animation.file_id)
                elif message.sticker:
                    save_chat_message(match_id, user_id, "استیکر", 'sticker', message.sticker.file_id)
                elif message.voice:
                    save_chat_message(match_id, user_id, "ویس", 'voice', message.voice.file_id)
                elif message.audio:
                    save_chat_message(match_id, user_id, "آهنگ", 'audio', message.audio.file_id)
                elif message.document:
                    save_chat_message(match_id, user_id, "فایل", 'document', message.document.file_id)
                elif message.video_note:
                    save_chat_message(match_id, user_id, "ویدئو نوت", 'video_note', message.video_note.file_id)
                
                try:
                    send_media_message(other, message, caption=message.caption)
                except:
                    pass
                return

@bot.inline_handler(func=lambda q: True)
def inline_query_handler(inline_query):
    user_id = inline_query.from_user.id
    create_user(user_id, inline_query.from_user.username, inline_query.from_user.first_name)
    
    if is_force_join_enabled():
        joined, not_joined = check_user_joined_channels(user_id)
        if not joined:
            text = "🔒 لطفاً ابتدا در کانال‌های زیر عضو شوید:\n\n"
            for ch in not_joined:
                text += f"• @{ch['username']}\n{ch['url']}\n\n"
            results = [InlineQueryResultArticle(
                id="error",
                title="🔒 عضویت در کانال الزامی است",
                description="لطفاً ابتدا در کانال عضو شوید",
                input_message_content=InputTextMessageContent(text, parse_mode='HTML'),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ بررسی مجدد", callback_data="check_inline", style="success")
                ]])
            )]
            bot.answer_inline_query(inline_query.id, results, cache_time=0)
            return
    
    query_text = inline_query.query.lower().strip()
    
    if 'بازی' in query_text:
        game_id = f"group_{user_id}_{int(time.time())}"
        group_games[game_id] = {
            'host_id': user_id,
            'chat_id': None,
            'players': [user_id],
            'current_player': user_id,
            'stage': 'waiting',
            'status': 'waiting',
            'created_at': int(time.time())
        }
        save_group_game(game_id)
        
        text = get_waiting_message_text(group_games[game_id])
        
        results = [InlineQueryResultArticle(
            id=game_id,
            title="🎮 شروع بازی گروهی جرعت و حقیقت",
            description="یک بازی گروهی راه اندازی کن",
            input_message_content=InputTextMessageContent(text, parse_mode='HTML'),
            reply_markup=group_waiting_keyboard(game_id, user_id, user_id, 1)
        )]
        bot.answer_inline_query(inline_query.id, results, cache_time=0)
        return
    
    results = [
        InlineQueryResultArticle(
            id=f"dare_{int(time.time())}",
            title="🎲 جرعت تصادفی",
            description="یک چالش دریافت کن",
            input_message_content=InputTextMessageContent(f"🎲 <b>جرعت تصادفی</b>", parse_mode='HTML'),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎲 دریافت جرعت", callback_data="inline_dare")
            ]])
        ),
        InlineQueryResultArticle(
            id=f"truth_{int(time.time())}",
            title="📖 حقیقت تصادفی",
            description="یک سوال دریافت کن",
            input_message_content=InputTextMessageContent(f"📖 <b>حقیقت تصادفی</b>", parse_mode='HTML'),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📖 دریافت حقیقت", callback_data="inline_truth")
            ]])
        )
    ]
    bot.answer_inline_query(inline_query.id, results, cache_time=0)

# ================== هندلر کالبک اصلی ==================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data
    
    print(f"[DEBUG] Callback received: {data} from user {get_user_name(user_id)} (id:{user_id})")
    
    if not check_rate_limit(user_id):
        bot.answer_callback_query(call.id, "⏳ لطفاً کمی صبر کنید!", show_alert=True)
        return
    
    if is_banned(user_id):
        bot.answer_callback_query(call.id, "🚫 شما مسدود شده‌اید!", show_alert=True)
        return
    
    # ========== منوی بازی ==========
    if data == "games_menu":
        text = "🎮 <b>انتخاب نوع بازی</b>\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
        edit_or_send_message(call, text, reply_markup=games_menu_keyboard())
        return
    
    # ========== بررسی جوین اجباری بعد از استارت ==========
    if data.startswith("check_join_start"):
        joined, not_joined = check_user_joined_channels(user_id)
        if not joined:
            kb = get_force_join_keyboard(not_joined, "check_join_start")
            try:
                bot.edit_message_text(
                    f"🔒 <b>برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شوید:</b>",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=kb,
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"[ERROR] check_join_start edit failed: {e}")
        else:
            try:
                bot.edit_message_text(
                    get_start_message(),
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=main_menu_keyboard(),
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"[ERROR] check_join_start edit to main menu failed: {e}")
        return
    
    # ========== بررسی جوین اجباری در اینلاین ==========
    if data == "check_inline":
        joined, not_joined = check_user_joined_channels(user_id)
        if not joined:
            text = "🔒 لطفاً ابتدا در کانال‌های زیر عضو شوید:\n\n"
            for ch in not_joined:
                text += f"• @{ch['username']}\n{ch['url']}\n\n"
            kb = InlineKeyboardMarkup()
            for ch in not_joined:
                kb.add(InlineKeyboardButton(f"📢 عضویت در @{ch['username']}", url=ch['url'], style="primary"))
            kb.add(InlineKeyboardButton("✅ بررسی مجدد", callback_data="check_inline", style="success"))
            try:
                bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=kb, parse_mode='HTML')
            except:
                pass
        else:
            try:
                bot.edit_message_text(get_start_message(), inline_message_id=call.inline_message_id, 
                                     reply_markup=main_menu_keyboard(), parse_mode='HTML')
            except:
                pass
        return
    
    # ========== group_check_join ==========
    if data.startswith("group_check_join|"):
        game_id = data.split("|")[1]
        joined, not_joined = check_user_joined_channels(user_id)
        
        if not joined:
            text = "🔒 <b>لطفاً ابتدا در کانال‌های زیر عضو شوید:</b>\n\n"
            for ch in not_joined:
                text += f"📢 @{ch['username']}\n🔗 {ch['url']}\n\n"
            kb = InlineKeyboardMarkup(row_width=1)
            for ch in not_joined:
                kb.add(InlineKeyboardButton(f"📢 عضویت در @{ch['username']}", url=ch['url'], style="primary"))
            kb.add(InlineKeyboardButton("✅ بررسی مجدد", callback_data=f"group_check_join|{game_id}", style="success"))
            
            if call.inline_message_id:
                try:
                    bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=kb, parse_mode='HTML')
                except:
                    pass
            else:
                try:
                    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='HTML')
                except:
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except:
                        pass
                    bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode='HTML')
            
            bot.answer_callback_query(call.id, "⚠️ لطفاً ابتدا عضو شوید!", show_alert=True)
            return
        
        game = get_or_load_game(game_id)
        if not game:
            bot.answer_callback_query(call.id, "❌ بازی یافت نشد!", show_alert=True)
            return
        
        if user_id not in game['players']:
            game['players'].append(user_id)
            save_group_game(game_id)
        
        text = get_waiting_message_text(game)
        new_kb = group_waiting_keyboard(game_id, game['host_id'], game['host_id'], len(game['players']))
        
        if call.inline_message_id:
            try:
                bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=new_kb, parse_mode='HTML')
            except:
                pass
        else:
            try:
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=new_kb, parse_mode='HTML')
            except:
                try:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass
                bot.send_message(call.message.chat.id, text, reply_markup=new_kb, parse_mode='HTML')
        
        bot.answer_callback_query(
            call.id, 
            f"✅ عضویت تأیید شد! شما به بازی اضافه شدید! (تعداد بازیکنان: {len(game['players'])} نفر)", 
            show_alert=True
        )
        return
    
    if data == "back_to_start":
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, get_start_message(), 
                                   parse_mode='HTML', reply_markup=main_menu_keyboard())
                else:
                    bot.edit_message_text(get_start_message(), call.message.chat.id, call.message.message_id, 
                                         parse_mode='HTML', reply_markup=main_menu_keyboard())
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(get_start_message(), inline_message_id=call.inline_message_id,
                                     parse_mode='HTML', reply_markup=main_menu_keyboard())
            except:
                pass
        return
    
    # ========== لیدربرد ==========
    if data == "leaderboard_menu":
        text = "🏆 <b>لیدربرد</b>\n\nدر این بخش می‌توانید بهترین‌های ربات را مشاهده کنید:\n\n❤️ نفرات برتر لایک\n👥 برترین دعوت‌کنندگان\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید:"
        edit_or_send_message(call, text, reply_markup=leaderboard_keyboard())
        return
    
    if data == "help_menu":
        if call.message:
            try:
                bot.edit_message_text(f"📖 <b>راهنمای ربات</b>\n\nبرای مشاهده هر بخش روی دکمه کلیک کنید:", 
                                     call.message.chat.id, call.message.message_id, 
                                     reply_markup=get_help_keyboard())
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(f"📖 <b>راهنمای ربات</b>\n\nبرای مشاهده هر بخش روی دکمه کلیک کنید:", 
                                     inline_message_id=call.inline_message_id,
                                     reply_markup=get_help_keyboard())
            except:
                pass
        return
    
    if data.startswith("help_"):
        content = get_help_content(data)
        if content:
            if call.message:
                try:
                    bot.edit_message_text(f"📖 <b>راهنما</b>\n\n{content}", 
                                         call.message.chat.id, call.message.message_id,
                                         reply_markup=InlineKeyboardMarkup().add(
                                             InlineKeyboardButton("🔙 بازگشت به راهنما", callback_data="help_menu", style="primary")
                                         ))
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"📖 <b>راهنما</b>\n\n{content}", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=InlineKeyboardMarkup().add(
                                             InlineKeyboardButton("🔙 بازگشت به راهنما", callback_data="help_menu", style="primary")
                                         ))
                except:
                    pass
        return
    
    if data == "support_menu":
        support_text = f"🛟 <b>پشتیبانی</b>\n\n✨ برای ارتباط با ادمین ربات، به آیدی زیر پیام دهید:\n\n📧 @brosbio"
        if call.message:
            try:
                bot.edit_message_text(support_text, call.message.chat.id, call.message.message_id, 
                                     reply_markup=InlineKeyboardMarkup().add(
                                         InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary")
                                     ))
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(support_text, inline_message_id=call.inline_message_id,
                                     reply_markup=InlineKeyboardMarkup().add(
                                         InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_to_start", style="primary")
                                     ))
            except:
                pass
        return
    
    # ========== ثبت لوکیشن ==========
    if data.startswith("register_location|"):
        back_callback = data.split("|")[1] if "|" in data else "my_profile"
        text = "📍 <b>ثبت لوکیشن</b>\n\nابتدا استان خودت رو انتخاب کن:"
        edit_or_send_message(call, text, reply_markup=province_keyboard(back_callback))
        return
    
    if data == "register_location":
        text = "📍 <b>ثبت لوکیشن</b>\n\nابتدا استان خودت رو انتخاب کن:"
        edit_or_send_message(call, text, reply_markup=province_keyboard("my_profile"))
        return

    if data.startswith("loc_prov|"):
        parts = data.split("|")
        prov_idx = int(parts[1])
        back_callback = parts[2] if len(parts) > 2 else "my_profile"
        province = IRAN_PROVINCES[prov_idx]
        text = f"📍 <b>استان انتخابی:</b> {province}\n\nحالا شهرت رو انتخاب کن:"
        edit_or_send_message(call, text, reply_markup=city_keyboard(prov_idx, back_callback))
        return

    if data.startswith("loc_city|"):
        parts = data.split("|")
        prov_idx, city_idx = int(parts[1]), int(parts[2])
        back_callback = parts[3] if len(parts) > 3 else "my_profile"
        province = IRAN_PROVINCES[prov_idx]
        city = IRAN_LOCATIONS[province][city_idx]
        update_user_location_city(user_id, province, city)
        bot.answer_callback_query(call.id, f"✅ لوکیشن شما ثبت شد: {city}، {province}", show_alert=True)
        
        if back_callback == "anon_location":
            pref = temp_data.get(user_id, {}).get('anon_preferred_gender')
            if pref:
                proceed_to_anon_queue(call, user_id, pref, 'any')
            else:
                call.data = "back_to_start"
                return callback_handler(call)
        else:
            call.data = "my_profile"
            return callback_handler(call)

    # ========== فروشگاه ما ==========
    if data == "store_menu":
        text = get_bot_setting('store_text', "🛍 <b>فروشگاه ما</b>\n\nبه زودی...")
        edit_or_send_message(call, text, reply_markup=store_keyboard())
        return

    # ========== لیدربرد: نفرات برتر لایک ==========
    if data == "top_likes":
        rows = get_top_liked_users(10, exclude_user=user_id)
        if not rows:
            text = "🏆 <b>نفرات برتر لایک</b>\n\nهنوز کسی لایک دریافت نکرده!"
        else:
            text = "🏆 <b>نفرات برتر لایک</b>\n\n"
            for i, (uid, cnt) in enumerate(rows, 1):
                text += f"{i}. {get_user_name(uid)} — ❤️ {cnt}\n"
            text += "\n👇 برای مشاهده پروفایل و درخواست بازی روی اسم فرد بزنید:"
        edit_or_send_message(call, text, reply_markup=top_likes_keyboard(rows, user_id))
        return

    # ========== لیدربرد: برترین دعوت‌کنندگان ==========
    if data == "top_referrers":
        rows = get_top_referrers(10)
        if not rows:
            text = "📢 <b>برترین دعوت‌کنندگان</b>\n\nهنوز کسی کاربر دعوت نکرده!"
        else:
            text = "📢 <b>برترین دعوت‌کنندگان</b>\n\n"
            for i, (uid, cnt) in enumerate(rows, 1):
                text += f"{i}. {get_user_name(uid)} — 👥 {cnt} نفر\n"
            text += "\n👇 برای مشاهده پروفایل روی اسم فرد بزنید:"
        edit_or_send_message(call, text, reply_markup=top_referrers_keyboard(rows, user_id))
        return

    # ========== مشاهده پروفایل عمومی کاربر (با عکس پروفایل) ==========
    if data.startswith("view_profile|"):
        target_id = int(data.split("|")[1])
        target = get_user(target_id)
        if not target:
            bot.answer_callback_query(call.id, "❌ کاربر یافت نشد!", show_alert=True)
            return
        
        name = get_user_name(target_id)
        gender_text = {'male': 'مرد', 'female': 'زن'}.get(target[3], 'ثبت نشده')
        age_text = target[9] if len(target) > 9 and target[9] else "ثبت نشده"
        province_val = target[15] if len(target) > 15 else None
        city_val = target[16] if len(target) > 16 else None
        location_text = f"{city_val}، {province_val}" if city_val and province_val else "ثبت نشده"
        
        cursor.execute("SELECT COUNT(*) FROM likes WHERE user_id = ?", (target_id,))
        likes_count = cursor.fetchone()[0]
        
        photo_id = target[8] if len(target) > 8 else None
        
        text = f"""👤 <b>پروفایل {name}</b>

🎂 سن: {age_text}
⚧ جنسیت: {gender_text}
📍 لوکیشن: {location_text}
❤️ لایک دریافت شده: {likes_count}
"""
        
        kb = public_profile_keyboard(target_id, user_id)
        
        if call.message:
            try:
                if photo_id:
                    bot.send_photo(
                        call.message.chat.id,
                        photo_id,
                        caption=text,
                        reply_markup=kb,
                        parse_mode='HTML'
                    )
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except:
                        pass
                else:
                    bot.edit_message_text(
                        text,
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=kb,
                        parse_mode='HTML'
                    )
            except Exception as e:
                print(f"[ERROR] view_profile: {e}")
                bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode='HTML')
        elif call.inline_message_id:
            try:
                bot.edit_message_text(
                    text,
                    inline_message_id=call.inline_message_id,
                    reply_markup=kb,
                    parse_mode='HTML'
                )
            except:
                pass
        return

    # ========== افراد دلخواه من ==========
    if data == "my_favorites":
        favs = get_favorites(user_id)
        if not favs:
            text = "⭐️ <b>افراد دلخواه من</b>\n\nلیست شما خالیه!\nبعد از هر بازی می‌تونی همبازیت رو به این لیست اضافه کنی."
        else:
            text = "⭐️ <b>افراد دلخواه من</b>\n\nاین افراد رو برای بازی بعدی ذخیره کردی:"
        edit_or_send_message(call, text, reply_markup=favorites_keyboard(favs, user_id))
        return

    if data.startswith("fav_remove|"):
        target = int(data.split("|")[1])
        remove_favorite(user_id, target)
        bot.answer_callback_query(call.id, "❌ از لیست دلخواه حذف شد.")
        favs = get_favorites(user_id)
        text = "⭐️ <b>افراد دلخواه من</b>\n\n" + ("لیست شما خالیه!" if not favs else "این افراد رو برای بازی بعدی ذخیره کردی:")
        edit_or_send_message(call, text, reply_markup=favorites_keyboard(favs, user_id))
        return

    if data.startswith("fav_add|"):
        target = int(data.split("|")[1])
        if add_favorite(user_id, target):
            bot.answer_callback_query(call.id, "⭐️ به لیست افراد دلخواه اضافه شد!", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "قبلاً اضافه شده بود.", show_alert=True)
        return

    # ========== درخواست بازی مستقیم ==========
    if data.startswith("req_game|"):
        target = int(data.split("|")[1])
        if target == user_id:
            bot.answer_callback_query(call.id, "❌ نمی‌تونی به خودت درخواست بدی!", show_alert=True)
            return
        target_user = get_user(target)
        if not target_user:
            bot.answer_callback_query(call.id, "❌ کاربر یافت نشد!", show_alert=True)
            return
        req_id = create_game_request(user_id, target)
        req_kb = InlineKeyboardMarkup(row_width=2)
        req_kb.add(
            InlineKeyboardButton("✅ قبول", callback_data=f"req_accept|{req_id}", style="success"),
            InlineKeyboardButton("❌ رد", callback_data=f"req_decline|{req_id}", style="danger")
        )
        try:
            bot.send_message(target, f"🎮 <b>درخواست بازی جدید!</b>\n\n{get_user_name(user_id)} می‌خواد باهات بازی کنه.",
                              reply_markup=req_kb)
            bot.answer_callback_query(call.id, "✅ درخواست بازی ارسال شد!", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, "❌ ارسال درخواست ناموفق بود (شاید کاربر ربات را بلاک کرده).", show_alert=True)
        return

    if data.startswith("req_accept|") or data.startswith("req_decline|"):
        req_id = int(data.split("|")[1])
        req = get_game_request(req_id)
        if not req or req[3] != 'pending':
            bot.answer_callback_query(call.id, "❌ این درخواست دیگر معتبر نیست!", show_alert=True)
            return
        _, from_user, to_user, _ = req
        if user_id != to_user:
            bot.answer_callback_query(call.id, "❌ این درخواست برای شما نیست!", show_alert=True)
            return
        if data.startswith("req_accept|"):
            update_game_request_status(req_id, 'accepted')
            match_id = create_anonymous_match(from_user, to_user)
            match = get_anonymous_match(match_id)
            for pid, opid in [(from_user, to_user), (to_user, from_user)]:
                try:
                    is_turn = (match['current_turn'] == pid)
                    bot.send_message(pid, f"🎮 <b>درخواست بازی قبول شد!</b>\n\nهمبازی شما: {get_user_name(opid)}\n\n" +
                                      ("نوبت شماست." if is_turn else "نوبت همبازی شماست."),
                                      reply_markup=anon_game_keyboard(match_id, is_turn))
                except:
                    pass
            edit_or_send_message(call, "✅ بازی شروع شد!")
        else:
            update_game_request_status(req_id, 'declined')
            try:
                bot.send_message(from_user, f"❌ {get_user_name(to_user)} درخواست بازی شما را رد کرد.")
            except:
                pass
            edit_or_send_message(call, "❌ درخواست رد شد.")
        return

    # ========== درآمد از ربات ==========
    if data == "income_menu":
        balance = get_wallet_balance(user_id)
        ref_count = get_referral_count(user_id)
        can_withdraw = balance >= MIN_WITHDRAW_AMOUNT and ref_count >= MIN_WITHDRAW_REFERRALS
        text = f"""💰 <b>درآمد از ربات</b>

به ازای هر کاربری که با لینک اختصاصی شما وارد ربات بشه، <b>{REFERRAL_REWARD} تومن</b> به کیف پولت اضافه میشه.

👥 تعداد دعوتی‌های شما: <b>{ref_count}</b> نفر
💵 موجودی کیف پول: <b>{balance:,}</b> تومن

📌 شرایط برداشت:
• حداقل موجودی: {MIN_WITHDRAW_AMOUNT:,} تومن
• حداقل تعداد دعوتی: {MIN_WITHDRAW_REFERRALS} نفر
"""
        edit_or_send_message(call, text, reply_markup=income_keyboard(can_withdraw))
        return

    if data == "get_referral_link":
        try:
            bot_username = bot.get_me().username
            link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        except:
            link = f"لینک دعوت شما: کد {user_id}"
        bot.answer_callback_query(call.id)
        chat_id = call.message.chat.id if call.message else user_id
        bot.send_message(chat_id, f"🔗 <b>لینک دعوت اختصاصی شما:</b>\n\n<code>{link}</code>\n\nاین لینک رو برای دوستانت بفرست!",
                          reply_markup=InlineKeyboardMarkup().add(
                              InlineKeyboardButton("🔙 بازگشت", callback_data="income_menu", style="primary")
                          ))
        return

    if data == "request_withdraw":
        balance = get_wallet_balance(user_id)
        ref_count = get_referral_count(user_id)
        if balance < MIN_WITHDRAW_AMOUNT or ref_count < MIN_WITHDRAW_REFERRALS:
            bot.answer_callback_query(call.id, f"❌ حداقل {MIN_WITHDRAW_AMOUNT:,} تومن موجودی و {MIN_WITHDRAW_REFERRALS} نفر دعوتی لازمه!", show_alert=True)
            return
        req_id = create_withdrawal_request(user_id, balance)
        bot.answer_callback_query(call.id, "✅ درخواست تسویه شما ثبت شد و برای ادمین ارسال شد.", show_alert=True)
        pay_kb = InlineKeyboardMarkup()
        pay_kb.add(InlineKeyboardButton("✅ پرداخت شد", callback_data=f"admin_pay_withdraw|{req_id}", style="success"))
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, f"💸 <b>درخواست تسویه جدید</b>\n\nکاربر: {get_user_name(user_id)} (<code>{user_id}</code>)\nمبلغ: {balance:,} تومن\nتعداد دعوتی: {ref_count} نفر",
                                  reply_markup=pay_kb)
            except:
                pass
        return

    if data == "profile_change_age":
        chat_id = call.message.chat.id if call.message else user_id
        msg = bot.send_message(chat_id, "🎂 <b>سن خود را وارد کنید (فقط عدد):</b>")
        bot.register_next_step_handler(msg, set_user_age)
        bot.answer_callback_query(call.id)
        return

    # ========== پنل ادمین ==========
    if data == "admin_edit_store":
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔ دسترسی ندارید!", show_alert=True)
            return
        chat_id = call.message.chat.id if call.message else user_id
        msg = bot.send_message(chat_id, "🛍 متن جدید فروشگاه را ارسال کنید (HTML مجاز است):")
        bot.register_next_step_handler(msg, admin_set_store_text)
        bot.answer_callback_query(call.id)
        return

    if data == "admin_withdrawals":
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔ دسترسی ندارید!", show_alert=True)
            return
        cursor.execute("SELECT id, user_id, amount FROM withdrawal_requests WHERE status='pending' ORDER BY created_at ASC LIMIT 15")
        rows = cursor.fetchall()
        if not rows:
            text = "💸 <b>درخواست‌های تسویه</b>\n\nدرخواست در انتظاری وجود ندارد."
        else:
            text = "💸 <b>درخواست‌های تسویه در انتظار</b>\n\n"
            for rid, uid, amount in rows:
                text += f"#{rid} — {get_user_name(uid)} — {amount:,} تومن\n"
        edit_or_send_message(call, text, reply_markup=admin_back_keyboard())
        return

    if data.startswith("admin_pay_withdraw|"):
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔ دسترسی ندارید!", show_alert=True)
            return
        req_id = int(data.split("|")[1])
        req = get_withdrawal_request(req_id)
        if not req:
            bot.answer_callback_query(call.id, "❌ درخواست یافت نشد!", show_alert=True)
            return
        if req[3] != 'pending':
            bot.answer_callback_query(call.id, "✅ این درخواست قبلاً پردازش شده.", show_alert=True)
            return
        mark_withdrawal_paid(req_id, user_id)
        bot.answer_callback_query(call.id, "✅ به عنوان پرداخت‌شده علامت خورد.", show_alert=True)
        try:
            bot.send_message(req[1], f"✅ درخواست تسویه شما به مبلغ {req[2]:,} تومن پرداخت شد.")
        except:
            pass
        if call.message:
            try:
                bot.edit_message_text(f"✅ پرداخت شد — {req[2]:,} تومن به {get_user_name(req[1])}",
                                       call.message.chat.id, call.message.message_id)
            except:
                pass
        return

    if data.startswith("group_"):
        handle_group_callback(call, user_id, data)
        return
    
    # ========== هندلر پروفایل ==========
    if data == "my_profile":
        profile = get_user_profile(user_id)
        if not profile:
            bot.answer_callback_query(call.id, "❌ خطا در دریافت اطلاعات!", show_alert=True)
            return
        
        user_id_db, username, first_name, gender, preferred_gender, nickname, photo_id = profile
        
        gender_text = {'male': 'مرد', 'female': 'زن'}.get(gender, 'ثبت نشده')
        pref_text = {'male': 'مرد', 'female': 'زن', 'both': 'هر دو'}.get(preferred_gender, 'ثبت نشده')
        display_name = nickname or first_name or f"کاربر {user_id_db}"
        
        cursor.execute("SELECT COUNT(*) FROM likes WHERE user_id = ?", (user_id_db,))
        likes_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM likes WHERE liked_by = ?", (user_id_db,))
        likes_given_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM blocked_users WHERE user_id = ?", (user_id_db,))
        blocks_count = cursor.fetchone()[0]
        full_user = get_user(user_id_db)
        age_val = full_user[9] if full_user and len(full_user) > 9 else None
        age_text = age_val if age_val else "ثبت نشده"
        province_val = full_user[15] if full_user and len(full_user) > 15 else None
        city_val = full_user[16] if full_user and len(full_user) > 16 else None
        location_text = f"{city_val}، {province_val}" if city_val and province_val else "❌ ثبت نشده"
        favorites_count = len(get_favorites(user_id_db))
        
        text = f"""
👤 <b>پروفایل من</b>

📛 نام مستعار: {display_name}
🎂 سن: {age_text}
⚧ جنسیت: {gender_text}
🎯 ترجیح بازی: {pref_text}
📍 لوکیشن: {location_text}
🆔 آیدی: {user_id_db}

❤️ لایک دریافت شده: {likes_count}
👍 لایک داده شده: {likes_given_count}
🚫 بلاک شده‌ها: {blocks_count}
⭐️ افراد دلخواه: {favorites_count} نفر
"""
        
        kb = profile_keyboard()
        
        if call.message:
            try:
                if call.message.photo and photo_id:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_photo(call.message.chat.id, photo_id, caption=text, reply_markup=kb, parse_mode='HTML')
                elif photo_id:
                    bot.send_photo(call.message.chat.id, photo_id, caption=text, reply_markup=kb, parse_mode='HTML')
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except:
                        pass
                else:
                    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='HTML')
            except Exception as e:
                print(f"[ERROR] my_profile: {e}")
                bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode='HTML')
        return
    
    if data == "profile_change_nickname":
        chat_id = call.message.chat.id if call.message else user_id
        msg = bot.send_message(chat_id, f"✏️ <b>نام مستعار جدید خود را وارد کنید:</b>")
        bot.register_next_step_handler(msg, set_user_nickname)
        try:
            if call.message:
                bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        return
    
    if data == "profile_change_gender":
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("👨 مرد", callback_data="profile_set_gender_male"),
            InlineKeyboardButton("👩 زن", callback_data="profile_set_gender_female")
        )
        kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="my_profile", style="primary"))
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, "⚧ <b>جنسیت خود را انتخاب کنید:</b>", reply_markup=kb, parse_mode='HTML')
                else:
                    bot.edit_message_text("⚧ <b>جنسیت خود را انتخاب کنید:</b>", 
                                         call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='HTML')
            except:
                pass
        return
    
    if data == "profile_change_preference":
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("👨 فقط مرد", callback_data="profile_set_pref_male"),
            InlineKeyboardButton("👩 فقط زن", callback_data="profile_set_pref_female"),
            InlineKeyboardButton("👥 فرقی نمی‌کند", callback_data="profile_set_pref_both")
        )
        kb.add(InlineKeyboardButton("🔙 بازگشت", callback_data="my_profile", style="primary"))
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, "🎯 <b>ترجیح بازی خود را انتخاب کنید:</b>", reply_markup=kb, parse_mode='HTML')
                else:
                    bot.edit_message_text("🎯 <b>ترجیح بازی خود را انتخاب کنید:</b>", 
                                         call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='HTML')
            except:
                pass
        return
    
    if data == "profile_change_photo":
        chat_id = call.message.chat.id if call.message else user_id
        msg = bot.send_message(chat_id, f"📸 <b>لطفاً عکس جدید خود را ارسال کنید:</b>")
        bot.register_next_step_handler(msg, set_user_profile_photo)
        try:
            if call.message:
                bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        return
    
    if data == "profile_likes":
        likes = get_user_likes(user_id)
        if not likes:
            text = "❤️ <b>لایک شده‌ها</b>\n\nهیچ کسی به شما لایک نداده است!"
        else:
            text = "❤️ <b>لایک شده‌ها</b>\n\n"
            for like in likes:
                liked_by, nickname, first_name, username = like
                display_name = nickname or first_name or username or f"کاربر {liked_by}"
                text += f"• {display_name}\n"
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 بازگشت به پروفایل", callback_data="my_profile", style="primary"))
        
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode='HTML')
                else:
                    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                                        reply_markup=kb, parse_mode='HTML')
            except Exception as e:
                print(f"[ERROR] profile_likes: {e}")
                bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode='HTML')
        elif call.inline_message_id:
            try:
                bot.edit_message_text(text, inline_message_id=call.inline_message_id,
                                    reply_markup=kb, parse_mode='HTML')
            except:
                pass
        return
    
    if data == "profile_blocks":
        blocks = get_user_blocks(user_id)
        if not blocks:
            text = "🚫 <b>بلاک شده‌ها</b>\n\nشما هیچ کسی را بلاک نکرده‌اید!"
        else:
            text = "🚫 <b>بلاک شده‌ها</b>\n\n"
            for block in blocks:
                blocked_user, nickname, first_name, username = block
                display_name = nickname or first_name or username or f"کاربر {blocked_user}"
                text += f"• {display_name}\n"
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 بازگشت به پروفایل", callback_data="my_profile", style="primary"))
        
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode='HTML')
                else:
                    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                                        reply_markup=kb, parse_mode='HTML')
            except Exception as e:
                print(f"[ERROR] profile_blocks: {e}")
                bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode='HTML')
        elif call.inline_message_id:
            try:
                bot.edit_message_text(text, inline_message_id=call.inline_message_id,
                                    reply_markup=kb, parse_mode='HTML')
            except:
                pass
        return
    
    if data.startswith("profile_set_gender_"):
        gender = data.split("_")[-1]
        update_user_gender(user_id, gender)
        gender_text = {'male': 'مرد', 'female': 'زن'}.get(gender, 'نامشخص')
        bot.answer_callback_query(call.id, f"✅ جنسیت شما به {gender_text} تغییر یافت!", show_alert=True)
        call.data = "my_profile"
        return callback_handler(call)
    
    if data.startswith("profile_set_pref_"):
        pref = data.split("_")[-1]
        update_user_preferred_gender(user_id, pref)
        pref_text = {'male': 'مرد', 'female': 'زن', 'both': 'هر دو'}.get(pref, 'نامشخص')
        bot.answer_callback_query(call.id, f"✅ ترجیح بازی به {pref_text} تغییر یافت!", show_alert=True)
        call.data = "my_profile"
        return callback_handler(call)
    
    # ========== هندلر بازی ناشناس ==========
    if data == "start_anonymous":
        user = get_user(user_id)
        if user and user[3]:
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"🎭 <b>بازی ناشناس</b>\n\nلطفاً جنسیت مورد نظر خود را انتخاب کنید:", 
                                       reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nلطفاً جنسیت مورد نظر خود را انتخاب کنید:", 
                                             call.message.chat.id, call.message.message_id,
                                             reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nلطفاً جنسیت مورد نظر خود را انتخاب کنید:", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
                except:
                    pass
        else:
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"🎭 <b>بازی ناشناس</b>\n\nلطفاً ابتدا جنسیت خود را انتخاب کنید:", 
                                       reply_markup=anon_gender_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nلطفاً ابتدا جنسیت خود را انتخاب کنید:", 
                                             call.message.chat.id, call.message.message_id,
                                             reply_markup=anon_gender_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nلطفاً ابتدا جنسیت خود را انتخاب کنید:", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=anon_gender_keyboard(), parse_mode='HTML')
                except:
                    pass
        return
    
    if data == "anon_gender_male":
        update_user_gender(user_id, 'male')
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, f"🎭 <b>بازی ناشناس</b>\n\nجنسیت شما: <b>مرد</b>\n\nحالا جنسیت مورد نظر برای بازی را انتخاب کنید:", 
                                   reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
                else:
                    bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nجنسیت شما: <b>مرد</b>\n\nحالا جنسیت مورد نظر برای بازی را انتخاب کنید:", 
                                         call.message.chat.id, call.message.message_id,
                                         reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nجنسیت شما: <b>مرد</b>\n\nحالا جنسیت مورد نظر برای بازی را انتخاب کنید:", 
                                     inline_message_id=call.inline_message_id,
                                     reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
            except:
                pass
        return
    
    if data == "anon_gender_female":
        update_user_gender(user_id, 'female')
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, f"🎭 <b>بازی ناشناس</b>\n\nجنسیت شما: <b>زن</b>\n\nحالا جنسیت مورد نظر برای بازی را انتخاب کنید:", 
                                   reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
                else:
                    bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nجنسیت شما: <b>زن</b>\n\nحالا جنسیت مورد نظر برای بازی را انتخاب کنید:", 
                                         call.message.chat.id, call.message.message_id,
                                         reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(f"🎭 <b>بازی ناشناس</b>\n\nجنسیت شما: <b>زن</b>\n\nحالا جنسیت مورد نظر برای بازی را انتخاب کنید:", 
                                     inline_message_id=call.inline_message_id,
                                     reply_markup=anon_preferred_gender_keyboard(), parse_mode='HTML')
            except:
                pass
        return
    
    if data == "anon_pref_male":
        temp_data[user_id] = {'anon_preferred_gender': 'male'}
        edit_or_send_message(call, "📍 <b>حالا مشخص کن با چه کسانی می‌خوای بازی کنی:</b>", reply_markup=anon_location_scope_keyboard(user_id))
        return
    elif data == "anon_pref_female":
        temp_data[user_id] = {'anon_preferred_gender': 'female'}
        edit_or_send_message(call, "📍 <b>حالا مشخص کن با چه کسانی می‌خوای بازی کنی:</b>", reply_markup=anon_location_scope_keyboard(user_id))
        return
    elif data == "anon_pref_both":
        temp_data[user_id] = {'anon_preferred_gender': 'both'}
        edit_or_send_message(call, "📍 <b>حالا مشخص کن با چه کسانی می‌خوای بازی کنی:</b>", reply_markup=anon_location_scope_keyboard(user_id))
        return

    if data in ("anon_loc_any", "anon_loc_city", "anon_loc_province"):
        scope = {'anon_loc_any': 'any', 'anon_loc_city': 'city', 'anon_loc_province': 'province'}[data]
        pref = temp_data.get(user_id, {}).get('anon_preferred_gender')
        if not pref:
            bot.answer_callback_query(call.id, "❌ لطفاً دوباره از منوی اصلی شروع کنید!", show_alert=True)
            return
        if scope in ('city', 'province'):
            _, city_check = get_user_location(user_id)
            if not city_check:
                bot.answer_callback_query(call.id, "❌ ابتدا باید لوکیشن خودت رو ثبت کنی!", show_alert=True)
                return
        proceed_to_anon_queue(call, user_id, pref, scope)
        return
    
    if data == "anon_cancel_search":
        remove_from_queue(user_id)
        if user_id in anon_search_timers:
            try:
                del anon_search_timers[user_id]
            except:
                pass
        if call.message:
            try:
                bot.edit_message_text(f"❌ <b>جستجو لغو شد.</b>\n\nبه منوی اصلی بازگشتید.",
                                     call.message.chat.id, call.message.message_id,
                                     reply_markup=main_menu_keyboard())
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(f"❌ <b>جستجو لغو شد.</b>\n\nبه منوی اصلی بازگشتید.",
                                     inline_message_id=call.inline_message_id,
                                     reply_markup=main_menu_keyboard())
            except:
                pass
        return
    
    if data.startswith("anon_"):
        handle_anon_game_callback(call, user_id, data)
        return
    
    if data == "new_question":
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, f"📝 <b>ارسال سوال جدید</b>\n\nلطفاً دسته سوال را انتخاب کنید:", 
                                   reply_markup=category_keyboard("cat"), parse_mode='HTML')
                else:
                    bot.edit_message_text(f"📝 <b>ارسال سوال جدید</b>\n\nلطفاً دسته سوال را انتخاب کنید:", 
                                         call.message.chat.id, call.message.message_id, reply_markup=category_keyboard("cat"), parse_mode='HTML')
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(f"📝 <b>ارسال سوال جدید</b>\n\nلطفاً دسته سوال را انتخاب کنید:", 
                                     inline_message_id=call.inline_message_id, reply_markup=category_keyboard("cat"), parse_mode='HTML')
            except:
                pass
        return
    
    if data.startswith("cat_"):
        category = data[len("cat_"):]
        temp_data[user_id] = {'category': category}
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, f"📝 <b>نوع سوال را انتخاب کنید:</b>", 
                                   reply_markup=question_type_keyboard(category, "q"), parse_mode='HTML')
                else:
                    bot.edit_message_text(f"📝 <b>نوع سوال را انتخاب کنید:</b>", 
                                         call.message.chat.id, call.message.message_id, 
                                         reply_markup=question_type_keyboard(category, "q"), parse_mode='HTML')
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(f"📝 <b>نوع سوال را انتخاب کنید:</b>", 
                                     inline_message_id=call.inline_message_id,
                                     reply_markup=question_type_keyboard(category, "q"), parse_mode='HTML')
            except:
                pass
        return
    
    if data.startswith("q_dare_"):
        category = data[len("q_dare_"):]
        chat_id = call.message.chat.id if call.message else user_id
        msg = bot.send_message(chat_id, f"📝 <b>متن جرعت خود را ارسال کنید:</b>", parse_mode='HTML')
        bot.register_next_step_handler(msg, lambda m: save_question(m, category, 'جرعت'))
        try:
            if call.message:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            elif call.inline_message_id:
                pass
        except:
            pass
        return
    
    if data.startswith("q_truth_"):
        category = data[len("q_truth_"):]
        chat_id = call.message.chat.id if call.message else user_id
        msg = bot.send_message(chat_id, f"📝 <b>متن حقیقت خود را ارسال کنید:</b>", parse_mode='HTML')
        bot.register_next_step_handler(msg, lambda m: save_question(m, category, 'حقیقت'))
        try:
            if call.message:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            elif call.inline_message_id:
                pass
        except:
            pass
        return
    
    if data in ["inline_dare", "inline_truth"]:
        q_type = 'جرعت' if data == "inline_dare" else 'حقیقت'
        user = get_user(user_id)
        gender = user[3] if user and user[3] else 'boy'
        category = f"{gender}_normal"
        question = get_random_question(category, q_type)
        if not question:
            question = get_random_question('boy_normal', q_type)
        if not question:
            question = "سوالی یافت نشد!"
        if call.message:
            try:
                bot.edit_message_text(f"<b>{q_type}</b>:\n\n{question}", 
                                     call.message.chat.id, call.message.message_id, parse_mode='HTML')
            except:
                pass
        elif call.inline_message_id:
            try:
                bot.edit_message_text(f"<b>{q_type}</b>:\n\n{question}", 
                                     inline_message_id=call.inline_message_id, parse_mode='HTML')
            except:
                pass
        return
    
    # ========== بخش ادمین ==========
    if data.startswith("admin_"):
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔ شما ادمین نیستید!", show_alert=True)
            return
        
        if data == "admin_export_questions":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "⛔ دسترسی ندارید!", show_alert=True)
                return
            
            try:
                text_content = export_questions_to_text()
                file_obj = io.BytesIO(text_content.encode('utf-8'))
                file_obj.name = f"questions_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                
                chat_id = call.message.chat.id if call.message else user_id
                bot.send_document(chat_id, file_obj, caption="📄 <b>گزارش کامل سوالات</b>\n\nتمام سوالات به همراه دسته‌بندی و وضعیت در این فایل موجود است.")
                bot.answer_callback_query(call.id, "✅ فایل گزارش ارسال شد!")
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)
            return
        
        if data == "admin_help_menu":
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"📚 <b>مدیریت راهنما</b>", 
                                       reply_markup=admin_help_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"📚 <b>مدیریت راهنما</b>", call.message.chat.id, call.message.message_id, 
                                             reply_markup=admin_help_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"📚 <b>مدیریت راهنما</b>", inline_message_id=call.inline_message_id,
                                         reply_markup=admin_help_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_help_list":
            buttons = get_all_help_buttons()
            if not buttons:
                bot.answer_callback_query(call.id, "هیچ دکمه‌ای یافت نشد!", show_alert=True)
                return
            text = f"📋 <b>لیست دکمه‌های راهنما:</b>\n\n"
            for btn in buttons:
                btn_id, btn_text, btn_callback, content, order, is_active = btn
                status = "✅ فعال" if is_active else "❌ غیرفعال"
                text += f"• {btn_text}\n  🔑 کالبک: {btn_callback}\n\n"
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, text, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                                             reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(text, inline_message_id=call.inline_message_id,
                                         reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_help_add":
            temp_data[user_id] = {'help_action': 'add'}
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, 
                f"➕ <b>افزودن دکمه جدید</b>\n\nفرمت:\n<code>متن دکمه | کالبک | متن نمایشی | ترتیب</code>", 
                parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_help_add_button)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_help_edit":
            buttons = get_all_help_buttons()
            if not buttons:
                bot.answer_callback_query(call.id, "هیچ دکمه‌ای یافت نشد!", show_alert=True)
                return
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"✏️ <b>انتخاب دکمه برای ویرایش:</b>", 
                                       reply_markup=admin_help_buttons_keyboard(buttons, "edit"), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"✏️ <b>انتخاب دکمه برای ویرایش:</b>", 
                                             call.message.chat.id, call.message.message_id, 
                                             reply_markup=admin_help_buttons_keyboard(buttons, "edit"), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"✏️ <b>انتخاب دکمه برای ویرایش:</b>", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=admin_help_buttons_keyboard(buttons, "edit"), parse_mode='HTML')
                except:
                    pass
            return
        
        if data.startswith("admin_help_edit_"):
            btn_id = int(data.split("_")[-1])
            temp_data[user_id] = {'help_action': 'edit', 'help_button_id': btn_id}
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, 
                f"✏️ <b>ویرایش دکمه</b>\n\nفرمت:\n<code>متن جدید | کالبک جدید | متن نمایشی جدید | ترتیب جدید | فعال(1)/غیرفعال(0)</code>", 
                parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_help_edit_button)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_help_delete":
            buttons = get_all_help_buttons()
            if not buttons:
                bot.answer_callback_query(call.id, "هیچ دکمه‌ای یافت نشد!", show_alert=True)
                return
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"❌ <b>انتخاب دکمه برای حذف:</b>", 
                                       reply_markup=admin_help_buttons_keyboard(buttons, "delete"), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"❌ <b>انتخاب دکمه برای حذف:</b>", 
                                             call.message.chat.id, call.message.message_id, 
                                             reply_markup=admin_help_buttons_keyboard(buttons, "delete"), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"❌ <b>انتخاب دکمه برای حذف:</b>", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=admin_help_buttons_keyboard(buttons, "delete"), parse_mode='HTML')
                except:
                    pass
            return
        
        if data.startswith("admin_help_delete_"):
            btn_id = int(data.split("_")[-1])
            if delete_help_button(btn_id):
                bot.answer_callback_query(call.id, "✅ دکمه حذف شد!", show_alert=True)
                log_admin_action(user_id, "delete_help_button", f"deleted button #{btn_id}")
            else:
                bot.answer_callback_query(call.id, "❌ خطا!", show_alert=True)
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"✅ <b>دکمه حذف شد!</b>", 
                                       reply_markup=admin_help_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"✅ <b>دکمه حذف شد!</b>", 
                                             call.message.chat.id, call.message.message_id, 
                                             reply_markup=admin_help_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"✅ <b>دکمه حذف شد!</b>", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=admin_help_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_back":
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"🔐 <b>پنل مدیریت ربات</b>\n\n✨ از اینجا می‌تونی ربات رو مدیریت کنی:", 
                                       reply_markup=admin_main_menu_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"🔐 <b>پنل مدیریت ربات</b>\n\n✨ از اینجا می‌تونی ربات رو مدیریت کنی:", 
                                             call.message.chat.id, call.message.message_id,
                                             reply_markup=admin_main_menu_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"🔐 <b>پنل مدیریت ربات</b>\n\n✨ از اینجا می‌تونی ربات رو مدیریت کنی:", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=admin_main_menu_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_stats":
            total, male, female, banned = get_user_stats()
            questions_count = get_questions_count()
            channels = get_forced_channels()
            cursor.execute("SELECT COUNT(*) FROM anon_matches WHERE status = 'playing'")
            active_anon = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM anon_queue")
            queue_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM group_games_db WHERE status = 'playing'")
            active_group = cursor.fetchone()[0]
            text = f"""
📊 <b>آمار ربات</b>

<b>👥 کاربران:</b>
• کل فعال: {total}
• 👨 مرد: {male} | 👩 زن: {female}
• 🚫 بن شده: {banned}

<b>🎮 بازی‌ها:</b>
• ناشناس فعال: {active_anon}
• صف انتظار ناشناس: {queue_count}
• گروهی فعال: {active_group}

<b>📝 سوالات:</b>
• تایید شده: {questions_count}

<b>🔗 جوین اجباری:</b>
• تعداد کانال‌ها: {len(channels)}
"""
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, text, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_user_info":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"🔍 <b>لطفاً آیدی یا یوزرنیم کاربر را وارد کنید:</b>", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_get_user_info)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_ban_user":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"🚫 <b>لطفاً آیدی یا یوزرنیم کاربر را وارد کنید:</b>", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_ban_unban_user)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_msg_user":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"📨 <b>لطفاً آیدی یا یوزرنیم کاربر را وارد کنید:</b>", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_msg_user_id)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_broadcast":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"📢 <b>لطفاً پیام همگانی را ارسال کنید:</b>", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_broadcast_msg)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_forward":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"🔄 <b>لطفاً پیام برای فوروارد را ارسال کنید:</b>", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_forward_msg)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_add_question":
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"📝 <b>افزودن سوال جدید</b>\n\nلطفاً دسته سوال را انتخاب کنید:", 
                                       reply_markup=category_keyboard("admin_cat"), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"📝 <b>افزودن سوال جدید</b>\n\nلطفاً دسته سوال را انتخاب کنید:", 
                                             call.message.chat.id, call.message.message_id, reply_markup=category_keyboard("admin_cat"), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"📝 <b>افزودن سوال جدید</b>\n\nلطفاً دسته سوال را انتخاب کنید:", 
                                         inline_message_id=call.inline_message_id, reply_markup=category_keyboard("admin_cat"), parse_mode='HTML')
                except:
                    pass
            return
        
        if data.startswith("admin_cat_"):
            category = data[len("admin_cat_"):]
            temp_data[user_id] = {'admin_category': category}
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"📝 <b>نوع سوال را انتخاب کنید:</b>", 
                                       reply_markup=question_type_keyboard(category, "admin_q"), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"📝 <b>نوع سوال را انتخاب کنید:</b>", 
                                             call.message.chat.id, call.message.message_id, 
                                             reply_markup=question_type_keyboard(category, "admin_q"), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"📝 <b>نوع سوال را انتخاب کنید:</b>", 
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=question_type_keyboard(category, "admin_q"), parse_mode='HTML')
                except:
                    pass
            return
        
        if data.startswith("admin_q_dare_"):
            category = data[len("admin_q_dare_"):]
            temp_data[user_id] = {'admin_category': category, 'admin_question_type': 'جرعت'}
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"📝 <b>روش افزودن سوال را انتخاب کنید:</b>",
                                       reply_markup=admin_add_method_keyboard(category, 'جرعت'), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"📝 <b>روش افزودن سوال را انتخاب کنید:</b>",
                                             call.message.chat.id, call.message.message_id,
                                             reply_markup=admin_add_method_keyboard(category, 'جرعت'), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"📝 <b>روش افزودن سوال را انتخاب کنید:</b>",
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=admin_add_method_keyboard(category, 'جرعت'), parse_mode='HTML')
                except:
                    pass
            return
        
        if data.startswith("admin_q_truth_"):
            category = data[len("admin_q_truth_"):]
            temp_data[user_id] = {'admin_category': category, 'admin_question_type': 'حقیقت'}
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"📝 <b>روش افزودن سوال را انتخاب کنید:</b>",
                                       reply_markup=admin_add_method_keyboard(category, 'حقیقت'), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"📝 <b>روش افزودن سوال را انتخاب کنید:</b>",
                                             call.message.chat.id, call.message.message_id,
                                             reply_markup=admin_add_method_keyboard(category, 'حقیقت'), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"📝 <b>روش افزودن سوال را انتخاب کنید:</b>",
                                         inline_message_id=call.inline_message_id,
                                         reply_markup=admin_add_method_keyboard(category, 'حقیقت'), parse_mode='HTML')
                except:
                    pass
            return
        
        if data.startswith("admin_single_"):
            remainder = data[len("admin_single_"):]
            category, q_type = remainder.rsplit("_", 1)
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"📝 <b>متن سوال را ارسال کنید:</b>", parse_mode='HTML')
            bot.register_next_step_handler(msg, lambda m: admin_save_question(m, category, q_type))
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data.startswith("admin_batch_"):
            remainder = data[len("admin_batch_"):]
            category, q_type = remainder.rsplit("_", 1)
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id,
                f"📚 <b>افزودن دسته‌جمعی سوالات</b>\n\nلطفاً سوالات را به صورت یک خط یک سوال ارسال کنید.", parse_mode='HTML')
            bot.register_next_step_handler(msg, lambda m: admin_batch_questions(m, category, q_type))
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_del_question":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"❌ <b>لطفاً آیدی سوال را وارد کنید:</b>", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_delete_question)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_view_questions":
            cursor.execute("SELECT id, category, type, text FROM questions WHERE status='approved' ORDER BY id LIMIT 50")
            questions = cursor.fetchall()
            if not questions:
                bot.answer_callback_query(call.id, "📭 هیچ سوالی یافت نشد!", show_alert=True)
                return
            text = f"📋 <b>لیست سوالات:</b>\n\n"
            cat_names = {'boy_normal': '👨 پسر عادی', 'girl_normal': '👩 دختر عادی', 'boy_18': '🔞 پسر۱۸+', 'girl_18': '🔞 دختر۱۸+'}
            for q in questions:
                text += f"#{q[0]} | {cat_names.get(q[1], q[1])} | {q[2]}\n{q[3][:35]}...\n\n"
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, text, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_pending_questions":
            cursor.execute("SELECT id, category, type, text FROM questions WHERE status='pending' ORDER BY created_at DESC")
            questions = cursor.fetchall()
            if not questions:
                bot.answer_callback_query(call.id, "✅ هیچ سوال در انتظاری وجود ندارد!", show_alert=True)
                return
            cat_names = {'boy_normal': '👨 پسر عادی', 'girl_normal': '👩 دختر عادی', 'boy_18': '🔞 پسر۱۸+', 'girl_18': '🔞 دختر۱۸+'}
            chat_id = call.message.chat.id if call.message else user_id
            for q in questions:
                q_id, category, q_type, text = q
                kb = InlineKeyboardMarkup()
                kb.add(
                    InlineKeyboardButton("✅ تایید", callback_data=f"approve_{q_id}", style="success"),
                    InlineKeyboardButton("❌ رد", callback_data=f"reject_{q_id}", style="danger")
                )
                msg_text = f"📝 <b>سوال #{q_id}</b>\n\n📂 {cat_names.get(category, category)}\n🎲 {q_type}\n📄 {text}"
                bot.send_message(chat_id, msg_text, reply_markup=kb, parse_mode='HTML')
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_force_join_menu":
            channels = get_forced_channels()
            status = "فعال ✅" if is_force_join_enabled() else "غیرفعال ❌"
            channels_text = "\n".join([f"• @{ch[0]}" for ch in channels]) if channels else "هیچ کانالی اضافه نشده"
            text = f"🔗 <b>مدیریت جوین اجباری</b>\n\n<b>وضعیت:</b> {status}\n\n<b>کانال‌ها:</b>\n{channels_text}"
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, text, reply_markup=admin_force_join_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_force_join_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=admin_force_join_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_add_channel":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"➕ <b>افزودن کانال</b>\n\nفرمت:\n<code>یوزرنیم | لینک دعوت</code>", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_add_channel)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_remove_channel":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"❌ <b>حذف کانال</b>\n\nلطفاً یوزرنیم کانال را وارد کنید:", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_remove_channel)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_list_channels":
            channels = get_forced_channels()
            if not channels:
                bot.answer_callback_query(call.id, "📭 هیچ کانالی وجود ندارد!", show_alert=True)
                return
            text = f"📋 <b>لیست کانال‌ها:</b>\n\n"
            for ch in channels:
                text += f"🔖 @{ch[0]}\n🔗 {ch[1]}\n\n"
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, text, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_admins_menu":
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"👑 <b>مدیریت ادمین‌ها</b>", 
                                       reply_markup=admin_admins_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"👑 <b>مدیریت ادمین‌ها</b>", call.message.chat.id, call.message.message_id, 
                                             reply_markup=admin_admins_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"👑 <b>مدیریت ادمین‌ها</b>", inline_message_id=call.inline_message_id,
                                         reply_markup=admin_admins_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_add_admin":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"➕ <b>افزودن ادمین</b>\n\nلطفاً یوزرنیم ادمین جدید را وارد کنید:", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_add_admin)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_remove_admin":
            chat_id = call.message.chat.id if call.message else user_id
            msg = bot.send_message(chat_id, f"❌ <b>حذف ادمین</b>\n\nلطفاً یوزرنیم ادمین را وارد کنید:", parse_mode='HTML')
            bot.register_next_step_handler(msg, admin_remove_admin)
            try:
                if call.message:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                elif call.inline_message_id:
                    pass
            except:
                pass
            return
        
        if data == "admin_list_admins":
            cursor.execute("SELECT user_id FROM admins")
            admins = [row[0] for row in cursor.fetchall()]
            text = f"👑 <b>لیست ادمین‌ها:</b>\n\n"
            for a in admins:
                user = get_user(a)
                name = user[2] if user else str(a)
                text += f"👤 {name} (ID: {a})\n"
            text += f"\n⭐ ادمین اصلی: {ADMIN_IDS}"
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, text, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                    else:
                        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(text, inline_message_id=call.inline_message_id, reply_markup=admin_back_keyboard(), parse_mode='HTML')
                except:
                    pass
            return
        
        if data == "admin_backup":
            if call.message:
                try:
                    if call.message.photo:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        bot.send_message(call.message.chat.id, f"⏳ <b>در حال تهیه بک آپ...</b>", parse_mode='HTML')
                    else:
                        bot.edit_message_text(f"⏳ <b>در حال تهیه بک آپ...</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
                except:
                    pass
            elif call.inline_message_id:
                try:
                    bot.edit_message_text(f"⏳ <b>در حال تهیه بک آپ...</b>", inline_message_id=call.inline_message_id, parse_mode='HTML')
                except:
                    pass
            try:
                backup_data = create_backup()
                backup_file = io.BytesIO(json.dumps(backup_data, ensure_ascii=False, indent=2).encode('utf-8'))
                backup_file.name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                chat_id = call.message.chat.id if call.message else user_id
                bot.send_document(chat_id, backup_file, caption=f"💾 بک آپ کامل دیتابیس\n📅 {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}")
                if call.message:
                    bot.edit_message_text(f"✅ <b>بک آپ با موفقیت ارسال شد!</b>", call.message.chat.id, call.message.message_id, 
                                         reply_markup=admin_back_keyboard(), parse_mode='HTML')
                elif call.inline_message_id:
                    bot.edit_message_text(f"✅ <b>بک آپ با موفقیت ارسال شد!</b>", inline_message_id=call.inline_message_id,
                                         reply_markup=admin_back_keyboard(), parse_mode='HTML')
            except Exception as e:
                if call.message:
                    bot.edit_message_text(f"❌ خطا: {str(e)}", call.message.chat.id, call.message.message_id, 
                                         reply_markup=admin_back_keyboard(), parse_mode='HTML')
                elif call.inline_message_id:
                    bot.edit_message_text(f"❌ خطا: {str(e)}", inline_message_id=call.inline_message_id,
                                         reply_markup=admin_back_keyboard(), parse_mode='HTML')
            return
    
    if data == "admin_approve_all":
        cursor.execute("SELECT COUNT(*) FROM questions WHERE status = 'pending'")
        count = cursor.fetchone()[0]
        if count == 0:
            bot.answer_callback_query(call.id, "✅ هیچ سوال در انتظاری وجود ندارد!", show_alert=True)
            return
        cursor.execute("UPDATE questions SET status = 'approved' WHERE status = 'pending'")
        conn.commit()
        log_admin_action(user_id, "approve_all", f"approved {count} questions")
        bot.answer_callback_query(call.id, f"✅ {count} سوال با موفقیت تایید شدند!", show_alert=True)
        try:
            bot.edit_message_text(f"🔐 <b>پنل مدیریت ربات</b>\n\n✨ {count} سوال تایید شد.", 
                             call.message.chat.id, call.message.message_id,
                             reply_markup=admin_main_menu_keyboard(), parse_mode='HTML')
        except:
            pass
        return

    if data.startswith("approve_"):
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔ شما ادمین نیستید!", show_alert=True)
            return
        q_id = int(data.split("_")[1])
        cursor.execute("UPDATE questions SET status = 'approved' WHERE id = ?", (q_id,))
        conn.commit()
        log_admin_action(user_id, "approve_question", f"approved question #{q_id}")
        bot.answer_callback_query(call.id, "✅ سوال تایید شد!", show_alert=True)
        try:
            if call.message:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            elif call.inline_message_id:
                pass
        except:
            pass
        return
    
    if data.startswith("reject_"):
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔ شما ادمین نیستید!", show_alert=True)
            return
        q_id = int(data.split("_")[1])
        cursor.execute("DELETE FROM questions WHERE id = ?", (q_id,))
        conn.commit()
        log_admin_action(user_id, "reject_question", f"rejected question #{q_id}")
        bot.answer_callback_query(call.id, "❌ سوال رد شد!", show_alert=True)
        try:
            if call.message:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            elif call.inline_message_id:
                pass
        except:
            pass
        return

# ================== توابع بازی گروهی ==================
def handle_group_callback(call, user_id, data):
    print(f"[DEBUG] Group callback: {data} from user {get_user_name(user_id)}")
    
    parts = data.split("|")
    action = parts[0]
    game_id = parts[1] if len(parts) > 1 else None
    
    if not game_id:
        bot.answer_callback_query(call.id, "❌ خطا در شناسایی بازی!", show_alert=True)
        return
    
    game = get_or_load_game(game_id)
    
    if not game:
        bot.answer_callback_query(call.id, "❌ بازی یافت نشد!", show_alert=True)
        return
    
    if game.get('chat_id') is None and call.message and call.message.chat:
        game['chat_id'] = call.message.chat.id
        save_group_game(game_id)
    
    if action not in ["group_check_join", "group_ready", "group_start"]:
        if is_force_join_enabled():
            joined, not_joined = check_user_joined_channels(user_id)
            if not joined:
                kb = get_force_join_keyboard(not_joined, f"group_check_join|{game_id}")
                edit_or_send_message(call, f"🔒 <b>لطفاً ابتدا در کانال‌های زیر عضو شوید:</b>", reply_markup=kb)
                bot.answer_callback_query(call.id, "لطفاً ابتدا عضو شوید!", show_alert=True)
                return
    
    if action == "group_ready":
        if user_id in game['players']:
            bot.answer_callback_query(call.id, "✅ شما قبلاً ثبت نام کرده‌اید!", show_alert=True)
            return
        
        if is_force_join_enabled():
            joined, not_joined = check_user_joined_channels(user_id)
            if not joined:
                kb = get_force_join_keyboard(not_joined, f"group_check_join|{game_id}")
                edit_or_send_message(call, f"🔒 <b>لطفاً ابتدا در کانال‌های زیر عضو شوید:</b>", reply_markup=kb)
                bot.answer_callback_query(call.id, "لطفاً ابتدا عضو شوید!", show_alert=True)
                return
        
        game['players'].append(user_id)
        save_group_game(game_id)
        bot.answer_callback_query(call.id, f"✅ شما به بازی اضافه شدید! تعداد بازیکنان: {len(game['players'])}")
        
        text = get_waiting_message_text(game)
        new_kb = group_waiting_keyboard(game_id, game['host_id'], game['host_id'], len(game['players']))
        
        edit_or_send_message(call, text, reply_markup=new_kb)
        return
    
    if action == "group_start":
        if user_id != game['host_id']:
            bot.answer_callback_query(call.id, "❌ فقط میزبان می‌تواند بازی را شروع کند!", show_alert=True)
            return
        
        if is_force_join_enabled():
            all_joined = True
            not_joined_list = []
            for player in game['players']:
                joined, not_joined = check_user_joined_channels(player)
                if not joined:
                    all_joined = False
                    not_joined_list.append({"player": player, "channels": not_joined})
            
            if not all_joined:
                text = "❌ <b>شروع بازی امکان‌پذیر نیست!</b>\n\nبازیکنان زیر در کانال‌های اجباری عضو نیستند:\n\n"
                for item in not_joined_list:
                    player_name = get_user_name(item["player"])
                    text += f"👤 {player_name}:\n"
                    for ch in item["channels"]:
                        text += f"   • @{ch['username']}\n"
                    text += "\n"
                text += "لطفاً ابتدا در کانال‌ها عضو شوید."
                bot.answer_callback_query(call.id, "بعضی بازیکنان در کانال‌ها عضو نیستند!", show_alert=True)
                edit_or_send_message(call, text, reply_markup=None)
                return
        
        if len(game['players']) < 2:
            bot.answer_callback_query(call.id, f"❌ حداقل ۲ بازیکن نیاز است! (تعداد فعلی: {len(game['players'])} نفر)", show_alert=True)
            text = get_waiting_message_text(game)
            new_kb = group_waiting_keyboard_for_edit(game_id, game['host_id'], len(game['players']))
            edit_or_send_message(call, text, reply_markup=new_kb)
            return
        
        game['status'] = 'playing'
        game['current_player'] = game['players'][0]
        game['stage'] = 'selecting_category'
        save_group_game(game_id)
        bot.answer_callback_query(call.id, "🚀 بازی شروع شد!")
        
        start_text = get_start_game_message(game)
        
        try:
            if hasattr(call, 'inline_message_id') and call.inline_message_id:
                bot.edit_message_text(
                    start_text,
                    inline_message_id=call.inline_message_id,
                    reply_markup=group_category_keyboard(game_id),
                    parse_mode='HTML'
                )
                print(f"[DEBUG] Game started - edited inline message with ID: {call.inline_message_id}")
            elif call.message:
                bot.edit_message_text(
                    start_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=group_category_keyboard(game_id),
                    parse_mode='HTML'
                )
                print(f"[DEBUG] Game started - edited regular message in chat {call.message.chat.id}")
            else:
                print(f"[ERROR] No message or inline_message_id in call")
                if game.get('chat_id'):
                    bot.send_message(
                        game['chat_id'],
                        start_text,
                        reply_markup=group_category_keyboard(game_id),
                        parse_mode='HTML'
                    )
        except Exception as e:
            print(f"[ERROR] Failed to edit message: {e}")
            try:
                if game.get('chat_id'):
                    bot.send_message(
                        game['chat_id'],
                        start_text,
                        reply_markup=group_category_keyboard(game_id),
                        parse_mode='HTML'
                    )
            except Exception as e2:
                print(f"[ERROR] Also failed to send new message: {e2}")
        
        return
    
    if game['status'] != 'playing':
        bot.answer_callback_query(call.id, "❌ بازی در حال اجرا نیست!", show_alert=True)
        return
    
    if action == "group_kick_menu":
        if user_id != game['host_id']:
            bot.answer_callback_query(call.id, "❌ فقط میزبان می‌تواند بازیکن حذف کند!", show_alert=True)
            return
        if len(game['players']) <= 1:
            bot.answer_callback_query(call.id, "❌ فقط میزبان در بازی است!", show_alert=True)
            return
        edit_or_send_message(call, f"❌ <b>حذف بازیکن</b>\n\nبازیکن مورد نظر را انتخاب کنید:",
                            reply_markup=group_kick_player_keyboard(game_id, game['players'], game['host_id']))
        return
    
    if action == "group_kick":
        if user_id != game['host_id']:
            bot.answer_callback_query(call.id, "❌ فقط میزبان!", show_alert=True)
            return
        target_id = int(parts[2])
        if target_id not in game['players']:
            bot.answer_callback_query(call.id, "❌ بازیکن یافت نشد!", show_alert=True)
            return
        game['players'].remove(target_id)
        if len(game['players']) < 1:
            if game_id in group_games:
                del group_games[game_id]
            cursor.execute("DELETE FROM group_games_db WHERE game_id = ?", (game_id,))
            edit_or_send_message(call, f"❌ <b>بازی به دلیل نبود بازیکن پایان یافت.</b>", reply_markup=main_menu_keyboard())
            return
        if game['current_player'] == target_id:
            idx = game['players'].index(game['current_player']) if game['current_player'] in game['players'] else 0
            game['current_player'] = game['players'][idx % len(game['players'])]
            chat_id = game.get('chat_id')
            if chat_id:
                turn_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}

📌 لطفاً دسته سوال را انتخاب کنید:
➖➖➖➖➖➖➖➖➖
"""
                if hasattr(call, 'inline_message_id') and call.inline_message_id:
                    bot.edit_message_text(
                        turn_text,
                        inline_message_id=call.inline_message_id,
                        reply_markup=group_category_keyboard(game_id),
                        parse_mode='HTML'
                    )
                elif call.message:
                    bot.edit_message_text(
                        turn_text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=group_category_keyboard(game_id),
                        parse_mode='HTML'
                    )
        save_group_game(game_id)
        bot.answer_callback_query(call.id, f"✅ بازیکن {get_user_name(target_id)} حذف شد!")
        edit_or_send_message(call, f"🔄 <b>بازیکن حذف شد.</b>", reply_markup=group_host_controls_keyboard(game_id))
        return
    
    if action == "group_skip_turn":
        if user_id != game['host_id']:
            bot.answer_callback_query(call.id, "❌ فقط میزبان!", show_alert=True)
            return
        current = game['current_player']
        idx = game['players'].index(current)
        next_idx = (idx + 1) % len(game['players'])
        game['current_player'] = game['players'][next_idx]
        game['stage'] = 'selecting_category'
        game['temp_category'] = None
        game['current_question_type'] = None
        game['current_question_text'] = None
        save_group_game(game_id)
        bot.answer_callback_query(call.id, f"✅ نوبت {get_user_name(current)} رد شد. نوبت {get_user_name(game['current_player'])} است.")
        chat_id = game.get('chat_id')
        if chat_id:
            turn_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}

📌 لطفاً دسته سوال را انتخاب کنید:
➖➖➖➖➖➖➖➖➖
"""
            if hasattr(call, 'inline_message_id') and call.inline_message_id:
                bot.edit_message_text(
                    turn_text,
                    inline_message_id=call.inline_message_id,
                    reply_markup=group_category_keyboard(game_id),
                    parse_mode='HTML'
                )
            elif call.message:
                bot.edit_message_text(
                    turn_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=group_category_keyboard(game_id),
                    parse_mode='HTML'
                )
        edit_or_send_message(call, f"✅ <b>نوبت رد شد.</b>", reply_markup=group_host_controls_keyboard(game_id))
        return
    
    if action == "group_end":
        if user_id != game['host_id']:
            bot.answer_callback_query(call.id, "❌ فقط میزبان!", show_alert=True)
            return
        for p in game['players']:
            try:
                bot.send_message(p, f"🔚 <b>بازی گروهی توسط میزبان پایان یافت.</b>", reply_markup=main_menu_keyboard())
            except:
                pass
        if game_id in group_games:
            del group_games[game_id]
        cursor.execute("DELETE FROM group_games_db WHERE game_id = ?", (game_id,))
        edit_or_send_message(call, f"🔚 <b>بازی به پایان رسید.</b>", reply_markup=main_menu_keyboard())
        return
    
    if game['current_player'] != user_id:
        bot.answer_callback_query(call.id, "❌ نوبت شما نیست!", show_alert=True)
        return
    
    if action == "group_cat":
        category = parts[2]
        if category == 'random':
            cats = ['boy_normal', 'girl_normal', 'boy_18', 'girl_18']
            category = random.choice(cats)
        game['temp_category'] = category
        game['stage'] = 'selecting_type'
        save_group_game(game_id)
        
        cat_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}
📂 <b>دسته انتخاب شد:</b> {get_category_name(category)}

📌 اکنون نوع سوال را انتخاب کنید:
➖➖➖➖➖➖➖➖➖
"""
        if hasattr(call, 'inline_message_id') and call.inline_message_id:
            bot.edit_message_text(
                cat_text,
                inline_message_id=call.inline_message_id,
                reply_markup=group_type_keyboard(game_id, category),
                parse_mode='HTML'
            )
        elif call.message:
            bot.edit_message_text(
                cat_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=group_type_keyboard(game_id, category),
                parse_mode='HTML'
            )
        return
    
    if action == "group_back_cat":
        game['stage'] = 'selecting_category'
        save_group_game(game_id)
        
        back_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}

📌 لطفاً دسته سوال را انتخاب کنید:
➖➖➖➖➖➖➖➖➖
"""
        if hasattr(call, 'inline_message_id') and call.inline_message_id:
            bot.edit_message_text(
                back_text,
                inline_message_id=call.inline_message_id,
                reply_markup=group_category_keyboard(game_id),
                parse_mode='HTML'
            )
        elif call.message:
            bot.edit_message_text(
                back_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=group_category_keyboard(game_id),
                parse_mode='HTML'
            )
        return
    
    if action == "group_type":
        category = parts[2]
        q_type_raw = parts[3]
        
        if q_type_raw == 'random':
            q_type_raw = random.choice(['dare', 'truth'])
        
        q_type = 'جرعت' if q_type_raw == 'dare' else 'حقیقت'
        question = get_random_question(category, q_type)
        if not question:
            question = get_random_question('boy_normal', q_type)
        if not question:
            question = "سوالی یافت نشد!"
        game['current_question_type'] = q_type
        game['current_question_text'] = question
        game['stage'] = 'showing_question'
        save_group_game(game_id)
        
        is_host = (user_id == game['host_id'])
        
        question_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}
🎲 <b>{q_type}</b>:

{question}
➖➖➖➖➖➖➖➖➖
"""
        if hasattr(call, 'inline_message_id') and call.inline_message_id:
            bot.edit_message_text(
                question_text,
                inline_message_id=call.inline_message_id,
                reply_markup=group_question_keyboard(game_id, q_type, is_host),
                parse_mode='HTML'
            )
        elif call.message:
            bot.edit_message_text(
                question_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=group_question_keyboard(game_id, q_type, is_host),
                parse_mode='HTML'
            )
        
        for p in game['players']:
            if p != user_id:
                try:
                    bot.send_message(p, f"🎲 <b>سوال جدید مطرح شد!</b>\n\n{get_user_mention(user_id)} در نوبت خود سوال دریافت کرد.\nمنتظر پاسخ ایشان باشید...")
                except:
                    pass
        return
    
    if action == "group_answered":
        idx = game['players'].index(user_id)
        next_idx = (idx + 1) % len(game['players'])
        new_player = game['players'][next_idx]
        game['current_player'] = new_player
        game['stage'] = 'selecting_category'
        game['temp_category'] = None
        game['current_question_type'] = None
        game['current_question_text'] = None
        save_group_game(game_id)
        bot.answer_callback_query(call.id, "✅ جواب شما ثبت شد!")
        
        turn_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}

📌 لطفاً دسته سوال را انتخاب کنید:
➖➖➖➖➖➖➖➖➖
"""
        if hasattr(call, 'inline_message_id') and call.inline_message_id:
            bot.edit_message_text(
                turn_text,
                inline_message_id=call.inline_message_id,
                reply_markup=group_category_keyboard(game_id),
                parse_mode='HTML'
            )
        elif call.message:
            bot.edit_message_text(
                turn_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=group_category_keyboard(game_id),
                parse_mode='HTML'
            )
        
        try:
            bot.send_message(
                new_player,
                f"🎉 <b>نوبت شماست!</b>\n\nبازیکن قبلی به سوال پاسخ داد.\nحالا نوبت شماست که دسته سوال را انتخاب کنید.\n\nبرای ادامه به گروه مراجعه کنید.",
                reply_markup=main_menu_keyboard()
            )
        except:
            pass
        
        return
    
    if action == "group_skip":
        idx = game['players'].index(user_id)
        next_idx = (idx + 1) % len(game['players'])
        new_player = game['players'][next_idx]
        game['current_player'] = new_player
        game['stage'] = 'selecting_category'
        game['temp_category'] = None
        game['current_question_type'] = None
        game['current_question_text'] = None
        save_group_game(game_id)
        bot.answer_callback_query(call.id, "🔄 سوال رد شد! نوبت به نفر بعدی رسید.")
        
        turn_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}

📌 لطفاً دسته سوال را انتخاب کنید:
➖➖➖➖➖➖➖➖➖
"""
        if hasattr(call, 'inline_message_id') and call.inline_message_id:
            bot.edit_message_text(
                turn_text,
                inline_message_id=call.inline_message_id,
                reply_markup=group_category_keyboard(game_id),
                parse_mode='HTML'
            )
        elif call.message:
            bot.edit_message_text(
                turn_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=group_category_keyboard(game_id),
                parse_mode='HTML'
            )
        
        try:
            bot.send_message(
                new_player,
                f"🔄 <b>نوبت شماست!</b>\n\nبازیکن قبلی سوال را رد کرد.\nحالا نوبت شماست.\n\nبرای ادامه به گروه مراجعه کنید.",
                reply_markup=main_menu_keyboard()
            )
        except:
            pass
        
        return
    
    if action == "group_new_question":
        q_type = parts[2]
        category = game.get('temp_category')
        if not category:
            category = game.get('current_category', 'boy_normal')
        if q_type == 'جرعت':
            q_type_db = 'جرعت'
        elif q_type == 'حقیقت':
            q_type_db = 'حقیقت'
        else:
            q_type_db = q_type
            
        question = get_random_question(category, q_type_db)
        if not question:
            question = get_random_question(category, q_type_db)
        if not question:
            question = "سوالی یافت نشد!"
        game['current_question_text'] = question
        game['current_category'] = category
        save_group_game(game_id)
        bot.answer_callback_query(call.id, "🎲 سوال جدید دریافت شد!")
        is_host = (user_id == game['host_id'])
        
        question_text = f"""
🎮 <b>بازی جرئت و حقیقت</b>

➖➖➖➖➖➖➖➖➖
🎤 <b>نوبت:</b> {get_user_mention(game['current_player'])}
📂 <b>دسته:</b> {get_category_name(category)}
🎲 <b>{q_type_db}</b> (سوال جدید):

{question}
➖➖➖➖➖➖➖➖➖
"""
        if hasattr(call, 'inline_message_id') and call.inline_message_id:
            bot.edit_message_text(
                question_text,
                inline_message_id=call.inline_message_id,
                reply_markup=group_question_keyboard(game_id, q_type_db, is_host),
                parse_mode='HTML'
            )
        elif call.message:
            bot.edit_message_text(
                question_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=group_question_keyboard(game_id, q_type_db, is_host),
                parse_mode='HTML'
            )
        return
    
    if action == "group_back_menu":
        if user_id == game['host_id']:
            edit_or_send_message(call, f"⚙️ <b>کنترل میزبان</b>\n\nبازی در حال اجراست.",
                                reply_markup=group_host_controls_keyboard(game_id))
        else:
            edit_or_send_message(call, f"🎮 <b>وضعیت بازی</b>\n\nنوبت {get_user_mention(game['current_player'])} است.\nمنتظر بمانید...",
                                reply_markup=None)
        return

def save_question(message, category, q_type):
    result = add_question(category, q_type, message.text, message.from_user.id)
    if result is None:
        bot.reply_to(message, f"⚠️ <b>این سوال قبلاً ثبت شده است!</b>", 
                     reply_markup=main_menu_keyboard())
    else:
        bot.reply_to(message, f"✅ <b>سوال شما ارسال شد!</b>\n\nپس از تایید ادمین اضافه می‌شود.", 
                     reply_markup=main_menu_keyboard())

def admin_save_question(message, category, q_type):
    result = add_question(category, q_type, message.text, message.from_user.id)
    if result is None:
        bot.reply_to(message, f"⚠️ <b>این سوال قبلاً ثبت شده است!</b>", 
                     reply_markup=admin_back_keyboard())
    else:
        bot.reply_to(message, f"✅ <b>سوال با موفقیت اضافه شد!</b>", 
                     reply_markup=admin_back_keyboard())
        log_admin_action(message.from_user.id, "add_question", f"added {q_type} in {category}")

def admin_batch_questions(message, category, q_type):
    user_id = message.from_user.id
    text = message.text.strip()
    if not text:
        bot.reply_to(message, "❌ متنی ارسال نشد!", reply_markup=admin_back_keyboard())
        return
    lines = text.split('\n')
    added = 0
    duplicate = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r'^\d+[\.\-\)]\s*', '', line)
        cleaned = re.sub(r'^[۰-۹]+[\.\-\)]\s*', '', cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        result = add_question(category, q_type, cleaned, user_id)
        if result is None:
            duplicate += 1
        elif result is not False:
            added += 1
    report = f"✅ <b>نتیجه افزودن دسته‌جمعی</b>\n\n📂 دسته‌بندی: {category}\n🎲 نوع: {q_type}\n✅ اضافه شد: {added}\n⚠️ تکراری: {duplicate}"
    bot.reply_to(message, report, reply_markup=admin_back_keyboard())
    log_admin_action(user_id, "batch_add_questions", f"added {added} {q_type} in {category}")

def set_user_nickname(message):
    user_id = message.from_user.id
    nickname = message.text.strip()
    if len(nickname) > 50:
        bot.reply_to(message, "❌ نام مستعار نباید بیشتر از ۵۰ کاراکتر باشد!", reply_markup=main_menu_keyboard())
        return
    update_user_nickname(user_id, nickname)
    bot.reply_to(message, f"✅ نام مستعار شما به <b>{nickname}</b> تغییر یافت!", 
                 reply_markup=main_menu_keyboard())

def set_user_profile_photo(message):
    user_id = message.from_user.id
    if message.photo:
        file_id = message.photo[-1].file_id
        update_user_profile_photo(user_id, file_id)
        bot.reply_to(message, "✅ عکس پروفایل شما با موفقیت تغییر یافت!", 
                     reply_markup=main_menu_keyboard())
    else:
        bot.reply_to(message, "❌ لطفاً یک عکس معتبر ارسال کنید!", 
                     reply_markup=main_menu_keyboard())

def set_user_age(message):
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""
    if not text.isdigit() or not (10 <= int(text) <= 100):
        bot.reply_to(message, "❌ لطفاً یک سن معتبر (بین ۱۰ تا ۱۰۰) وارد کنید!", reply_markup=main_menu_keyboard())
        return
    update_user_age(user_id, int(text))
    bot.reply_to(message, f"✅ سن شما به <b>{text}</b> تغییر یافت!", reply_markup=main_menu_keyboard())

def admin_set_store_text(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        bot.reply_to(message, "⛔ شما ادمین نیستید!")
        return
    if not message.text:
        bot.reply_to(message, "❌ لطفاً یک متن معتبر ارسال کنید!", reply_markup=admin_back_keyboard())
        return
    set_bot_setting('store_text', message.text)
    log_admin_action(admin_id, "edit_store_text", message.text[:200])
    bot.reply_to(message, "✅ متن فروشگاه با موفقیت به‌روزرسانی شد!", reply_markup=admin_back_keyboard())

def admin_get_user_info(message):
    identifier = message.text.strip()
    user_id = get_user_id(identifier)
    if not user_id:
        bot.reply_to(message, "❌ کاربر یافت نشد!")
        return
    user = get_user(user_id)
    if not user:
        bot.reply_to(message, "❌ کاربر یافت نشد!")
        return
    gender_text = {'male': 'مرد', 'female': 'زن'}.get(user[3], 'ثبت نشده')
    pref_text = {'male': 'مرد', 'female': 'زن', 'both': 'هر دو'}.get(user[4], 'ثبت نشده')
    status_text = '🚫 بن شده' if user[5] else '✅ فعال'
    text = f"""
👤 <b>مشخصات کاربر</b>

🆔 آیدی: {user[0]}
👤 یوزرنیم: @{user[1] if user[1] else 'ندارد'}
📛 نام: {user[2] if user[2] else 'ندارد'}
⚧ جنسیت: {gender_text}
🎮 ترجیح: {pref_text}
🚫 وضعیت: {status_text}
"""
    bot.reply_to(message, text, reply_markup=admin_back_keyboard())

def admin_ban_unban_user(message):
    identifier = message.text.strip()
    user_id = get_user_id(identifier)
    if not user_id:
        bot.reply_to(message, "❌ کاربر یافت نشد!")
        return
    user = get_user(user_id)
    if user and user[5] == 1:
        cursor.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
        conn.commit()
        log_admin_action(message.from_user.id, "unban_user", f"unbanned user: {identifier}")
        bot.reply_to(message, f"✅ بن کاربر {identifier} برداشته شد.", reply_markup=admin_back_keyboard())
    else:
        cursor.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
        conn.commit()
        log_admin_action(message.from_user.id, "ban_user", f"banned user: {identifier}")
        bot.reply_to(message, f"✅ کاربر {identifier} بن شد.", reply_markup=admin_back_keyboard())

def admin_msg_user_id(message):
    identifier = message.text.strip()
    user_id = get_user_id(identifier)
    if not user_id:
        bot.reply_to(message, "❌ کاربر یافت نشد!")
        return
    msg = bot.send_message(message.chat.id, f"📨 متن پیام برای {identifier} را ارسال کنید:")
    bot.register_next_step_handler(msg, lambda m: admin_send_msg(m, user_id))

def admin_send_msg(message, user_id):
    try:
        result = send_media_message(user_id, message, reply_markup=None)
        if result:
            bot.reply_to(message, f"✅ پیام با موفقیت ارسال شد.", reply_markup=admin_back_keyboard())
            log_admin_action(message.from_user.id, "send_message", f"sent media to user {user_id}")
        else:
            bot.reply_to(message, f"❌ خطا در ارسال پیام!", reply_markup=admin_back_keyboard())
    except Exception as e:
        bot.reply_to(message, f"❌ خطا: {str(e)}", reply_markup=admin_back_keyboard())

def admin_broadcast_msg(message):
    cursor.execute("SELECT id FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    success = 0
    fail = 0
    status_msg = bot.reply_to(message, f"⏳ <b>در حال ارسال همگانی...</b>")
    for user in users:
        try:
            send_media_message(user[0], message)
            success += 1
        except:
            fail += 1
        time.sleep(0.05)
    bot.edit_message_text(f"✅ <b>نتیجه ارسال همگانی</b>\n\n✅ موفق: {success}\n❌ ناموفق: {fail}\n\n📊 مجموع کاربران: {len(users)}", 
                         message.chat.id, status_msg.message_id, reply_markup=admin_back_keyboard())
    log_admin_action(message.from_user.id, "broadcast", f"sent to {success} users, failed {fail}")

def admin_forward_msg(message):
    cursor.execute("SELECT id FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    success = 0
    fail = 0
    status_msg = bot.reply_to(message, f"⏳ <b>در حال فوروارد...</b>")
    for user in users:
        try:
            forward_media_message(user[0], message.chat.id, message.message_id)
            success += 1
        except:
            fail += 1
        time.sleep(0.05)
    bot.edit_message_text(f"✅ <b>نتیجه فوروارد</b>\n\n✅ موفق: {success}\n❌ ناموفق: {fail}\n\n📊 مجموع کاربران: {len(users)}", 
                         message.chat.id, status_msg.message_id, reply_markup=admin_back_keyboard())
    log_admin_action(message.from_user.id, "forward", f"forwarded to {success} users, failed {fail}")

def admin_delete_question(message):
    try:
        q_id = int(message.text.strip())
        cursor.execute("DELETE FROM questions WHERE id = ?", (q_id,))
        conn.commit()
        log_admin_action(message.from_user.id, "delete_question", f"deleted question #{q_id}")
        bot.reply_to(message, f"✅ سوال #{q_id} حذف شد.", reply_markup=admin_back_keyboard())
    except:
        bot.reply_to(message, "❌ آیدی نامعتبر!")

def admin_add_channel(message):
    try:
        parts = message.text.split('|')
        if len(parts) != 2:
            bot.reply_to(message, "❌ فرمت نامعتبر!\nمثال: mychannel | https://t.me/mychannel")
            return
        username = parts[0].strip().replace('@', '')
        join_url = parts[1].strip()
        if add_forced_channel(username, join_url):
            bot.reply_to(message, f"✅ کانال @{username} اضافه شد.", reply_markup=admin_back_keyboard())
            log_admin_action(message.from_user.id, "add_forced_channel", f"added @{username}")
        else:
            bot.reply_to(message, f"⚠️ کانال @{username} قبلاً اضافه شده است!", reply_markup=admin_back_keyboard())
    except:
        bot.reply_to(message, "❌ خطا!")

def admin_remove_channel(message):
    identifier = message.text.strip().replace('@', '')
    if remove_forced_channel(identifier):
        bot.reply_to(message, f"✅ کانال @{identifier} حذف شد.", reply_markup=admin_back_keyboard())
        log_admin_action(message.from_user.id, "remove_forced_channel", f"removed @{identifier}")
    else:
        bot.reply_to(message, f"❌ کانال @{identifier} یافت نشد!")

def admin_add_admin(message):
    identifier = message.text.strip()
    user_id = get_user_id(identifier)
    if user_id and user_id not in ADMIN_IDS:
        cursor.execute("INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
                       (user_id, message.from_user.id, int(time.time())))
        conn.commit()
        log_admin_action(message.from_user.id, "add_admin", f"added admin: {identifier}")
        bot.reply_to(message, f"✅ ادمین {identifier} اضافه شد.", reply_markup=admin_back_keyboard())
    else:
        bot.reply_to(message, f"❌ کاربر {identifier} یافت نشد!")

def admin_remove_admin(message):
    identifier = message.text.strip()
    user_id = get_user_id(identifier)
    if user_id and user_id not in ADMIN_IDS:
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
        log_admin_action(message.from_user.id, "remove_admin", f"removed admin: {identifier}")
        bot.reply_to(message, f"✅ ادمین {identifier} حذف شد.", reply_markup=admin_back_keyboard())
    else:
        bot.reply_to(message, f"❌ ادمین {identifier} یافت نشد!")

def admin_help_add_button(message):
    user_id = message.from_user.id
    try:
        parts = message.text.split('|')
        if len(parts) != 4:
            bot.reply_to(message, "❌ فرمت نامعتبر!")
            return
        button_text = parts[0].strip()
        button_callback = parts[1].strip()
        content = parts[2].strip()
        button_order = int(parts[3].strip())
        if not button_callback.startswith("help_"):
            button_callback = "help_" + button_callback
        if add_help_button(button_text, button_callback, content, button_order):
            bot.reply_to(message, f"✅ دکمه {button_text} اضافه شد!", reply_markup=admin_back_keyboard())
            log_admin_action(user_id, "add_help_button", f"added button: {button_callback}")
        else:
            bot.reply_to(message, "❌ دکمه تکراری!", reply_markup=admin_back_keyboard())
    except:
        bot.reply_to(message, "❌ خطا!", reply_markup=admin_back_keyboard())

def admin_help_edit_button(message):
    user_id = message.from_user.id
    btn_id = temp_data.get(user_id, {}).get('help_button_id')
    if not btn_id:
        bot.reply_to(message, "❌ خطا!", reply_markup=admin_back_keyboard())
        return
    try:
        parts = message.text.split('|')
        if len(parts) != 5:
            bot.reply_to(message, "❌ فرمت نامعتبر!")
            return
        button_text = parts[0].strip() if parts[0].strip() != '*' else None
        button_callback = parts[1].strip() if parts[1].strip() != '*' else None
        content = parts[2].strip() if parts[2].strip() != '*' else None
        button_order = int(parts[3].strip()) if parts[3].strip() != '*' else None
        is_active = int(parts[4].strip()) if parts[4].strip() != '*' else None
        if button_callback and not button_callback.startswith("help_"):
            button_callback = "help_" + button_callback
        if update_help_button(btn_id, button_text=button_text, button_callback=button_callback, 
                               content=content, button_order=button_order, is_active=is_active):
            bot.reply_to(message, f"✅ دکمه ویرایش شد!", reply_markup=admin_back_keyboard())
            log_admin_action(user_id, "edit_help_button", f"edited button #{btn_id}")
        else:
            bot.reply_to(message, "❌ خطا!", reply_markup=admin_back_keyboard())
    except:
        bot.reply_to(message, "❌ خطا!", reply_markup=admin_back_keyboard())

def proceed_to_anon_queue(call, user_id, preferred_gender, location_scope='any'):
    user = get_user(user_id)
    if not user or not user[3]:
        bot.answer_callback_query(call.id, "❌ ابتدا جنسیت خود را انتخاب کنید!", show_alert=True)
        return
    
    gender = user[3]
    update_user_preferred_gender(user_id, preferred_gender)
    
    match_user = find_match(user_id, gender, preferred_gender, location_scope)
    
    if match_user:
        remove_from_queue(user_id)
        remove_from_queue(match_user)
        match_id = create_anonymous_match(user_id, match_user)
        match = get_anonymous_match(match_id)
        if match:
            for pid in [user_id, match_user]:
                is_my_turn = (match['current_turn'] == pid)
                turn_text = "نوبت شماست." if is_my_turn else "نوبت همبازی شماست."
                try:
                    bot.send_message(pid, f"🎭 <b>همبازی پیدا شد!</b>\n\nبازی ناشناس شروع شد.\n\n{turn_text}\n\nبرای شروع روی دکمه «انتخاب سوال» کلیک کنید.",
                                    reply_markup=anon_game_keyboard(match_id, is_my_turn))
                except:
                    pass
            edit_or_send_message(call, f"✅ <b>همبازی پیدا شد!</b>\n\nدر حال شروع بازی...", reply_markup=None)
    else:
        add_to_queue(user_id, gender, preferred_gender, location_scope)
        start_anon_search_timer(user_id, gender, preferred_gender, location_scope)
        
        gender_text = 'مرد' if gender == 'male' else 'زن'
        pref_text = {'male': 'مرد', 'female': 'زن', 'both': 'فرقی نمی‌کند'}.get(preferred_gender, 'نامشخص')
        scope_text = {'any': 'هرکسی', 'city': 'فقط هم‌شهری', 'province': 'فقط هم‌استانی'}.get(location_scope, 'هرکسی')
        
        text = f"🔍 <b>در حال جستجوی همبازی...</b>\n\n" \
               f"جنسیت شما: {gender_text}\n" \
               f"جنسیت مورد نظر: {pref_text}\n" \
               f"محدوده مکانی: {scope_text}\n\n" \
               f"⏱️ اگر تا ۱ دقیقه همبازی پیدا نشود، جستجو لغو می‌شود."
        
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ لغو جستجو", callback_data="anon_cancel_search", style="danger"))
        
        edit_or_send_message(call, text, reply_markup=kb)

def handle_anon_game_callback(call, user_id, data):
    print(f"[DEBUG] Anon callback received: {data} from user {get_user_name(user_id)}")

    if '|' not in data:
        print(f"[WARN] Received old format callback (ignored): {data}")
        return

    parts = data.split('|')
    action = parts[0]

    match_id = None
    category = None
    question_type = None

    try:
        if action in ['anon_pick_question', 'anon_back_to_cat', 'anon_answered', 'anon_skip', 'anon_end', 'anon_show_profile', 'anon_back_to_game', 'anon_like', 'anon_block']:
            match_id = parts[1] if len(parts) > 1 else None
        elif action == 'anon_cat':
            match_id = parts[1] if len(parts) > 1 else None
            category = parts[2] if len(parts) > 2 else None
        elif action == 'anon_question':
            match_id = parts[1] if len(parts) > 1 else None
            category = parts[2] if len(parts) > 2 else None
            question_type = parts[3] if len(parts) > 3 else None
        elif action == 'anon_change':
            match_id = parts[1] if len(parts) > 1 else None
            question_type = parts[2] if len(parts) > 2 else None
        else:
            print(f"[WARN] Unknown action: {action}")
            return
    except IndexError:
        print(f"[ERROR] Invalid callback format: {data}")
        bot.answer_callback_query(call.id, "❌ خطا در شناسایی بازی!", show_alert=True)
        return

    if not match_id:
        bot.answer_callback_query(call.id, "❌ خطا در شناسایی بازی!", show_alert=True)
        return

    match = get_anonymous_match(match_id)
    if not match:
        print(f"[ERROR] Match not found for match_id: {match_id}")
        bot.answer_callback_query(call.id, "❌ بازی یافت نشد! لطفاً دوباره تلاش کنید.", show_alert=True)
        return

    if match['status'] != 'playing':
        bot.answer_callback_query(call.id, "❌ بازی به پایان رسیده است!", show_alert=True)
        return

    turn_required_actions = ['anon_pick_question', 'anon_cat', 'anon_question', 'anon_back_to_cat', 'anon_answered', 'anon_skip', 'anon_change']
    if action in turn_required_actions and match['current_turn'] != user_id:
        bot.answer_callback_query(call.id, "❌ نوبت شما نیست!", show_alert=True)
        return

    if action == 'anon_pick_question':
        edit_or_send_message(call, f"🎲 <b>انتخاب دسته سوال</b>\n\nلطفاً دسته سوال مورد نظر خود را انتخاب کنید:",
                            reply_markup=anon_category_keyboard(match_id))
        return

    if action == 'anon_cat':
        if category == 'random':
            categories = ['boy_normal', 'girl_normal', 'boy_18', 'girl_18']
            category = random.choice(categories)

        temp_data[user_id] = {'anon_category': category, 'anon_match_id': match_id}

        edit_or_send_message(call, f"🎲 <b>انتخاب نوع سوال</b>\n\nلطفاً جرعت یا حقیقت را انتخاب کنید:",
                            reply_markup=anon_question_type_keyboard(match_id, category))
        return

    if action == 'anon_back_to_cat':
        edit_or_send_message(call, f"🎲 <b>انتخاب دسته سوال</b>\n\nلطفاً دسته سوال مورد نظر خود را انتخاب کنید:",
                            reply_markup=anon_category_keyboard(match_id))
        return

    if action == 'anon_question':
        if not category or not question_type:
            bot.answer_callback_query(call.id, "❌ خطا در شناسایی سوال!", show_alert=True)
            return

        q_type = 'جرعت' if question_type == 'dare' else 'حقیقت'
        question = get_random_question(category, q_type)
        if not question:
            question = get_random_question('boy_normal', q_type)
        if not question:
            question = "سوالی یافت نشد! لطفاً بعداً تلاش کنید."

        update_anonymous_match(match_id, {
            'current_question_type': q_type,
            'current_question_text': question
        })

        other = get_other_player_in_match(match_id, user_id)

        edit_or_send_message(call, f"🎲 <b>{q_type}</b>\n\n{question}",
                            reply_markup=anon_question_keyboard(match_id, q_type))

        if other:
            try:
                bot.send_message(other,
                    f"🎲 <b>سوال جدیدی مطرح شد!</b>\n\n{q_type}:\n\n{question}\n\n⏳ منتظر بمانید تا همبازی پاسخ دهد.",
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("🔚 پایان بازی", callback_data=f"anon_end|{match_id}", style="danger")
                    ))
            except:
                pass
        return

    if action == 'anon_answered':
        other = get_other_player_in_match(match_id, user_id)
        update_anonymous_match(match_id, {
            'current_turn': other,
            'current_question_type': None,
            'current_question_text': None
        })

        bot.answer_callback_query(call.id, "✅ جواب شما ثبت شد! نوبت به همبازی رسید.")

        try:
            bot.send_message(other, f"🎉 <b>نوبت شماست!</b>\n\nهمبازی به سوال پاسخ داد.\nحالا نوبت شماست.",
                            reply_markup=anon_game_keyboard(match_id, True))
        except:
            pass

        edit_or_send_message(call, f"✅ <b>جواب شما ثبت شد!</b>\n\nنوبت به همبازی رسید.",
                            reply_markup=anon_game_keyboard(match_id, False))
        return

    if action == 'anon_skip':
        other = get_other_player_in_match(match_id, user_id)
        update_anonymous_match(match_id, {
            'current_turn': other,
            'current_question_type': None,
            'current_question_text': None
        })

        bot.answer_callback_query(call.id, "🔄 سوال رد شد! نوبت به همبازی رسید.")

        try:
            bot.send_message(other, f"🔄 <b>سوال رد شد!</b>\n\nهمبازی سوال را رد کرد.\nحالا نوبت شماست.",
                            reply_markup=anon_game_keyboard(match_id, True))
        except:
            pass

        edit_or_send_message(call, f"🔄 <b>سوال رد شد!</b>\n\nنوبت به همبازی رسید.",
                            reply_markup=anon_game_keyboard(match_id, False))
        return

    if action == 'anon_change':
        if not question_type:
            bot.answer_callback_query(call.id, "❌ خطا در شناسایی نوع سوال!", show_alert=True)
            return

        user = get_user(user_id)
        gender = user[3] if user and user[3] else 'boy'
        category = f"{gender}_normal"

        question = get_random_question(category, question_type)
        if not question:
            question = get_random_question('boy_normal', question_type)
        if not question:
            question = "سوالی یافت نشد!"

        update_anonymous_match(match_id, {'current_question_text': question})

        other = get_other_player_in_match(match_id, user_id)

        bot.answer_callback_query(call.id, "🎲 سوال جدید دریافت شد!")
        edit_or_send_message(call, f"🎲 <b>{question_type}</b> (سوال جدید)\n\n{question}",
                            reply_markup=anon_question_keyboard(match_id, question_type))

        if other:
            try:
                bot.send_message(other,
                    f"🎲 <b>سوال جدیدی مطرح شد!</b>\n\n{question_type}:\n\n{question}\n\n⏳ منتظر بمانید تا همبازی پاسخ دهد.",
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("🔚 پایان بازی", callback_data=f"anon_end|{match_id}", style="danger")
                    ))
            except:
                pass
        return

    if action == 'anon_show_profile':
        other = get_other_player_in_match(match_id, user_id)
        if not other:
            bot.answer_callback_query(call.id, "❌ همبازی یافت نشد!", show_alert=True)
            return
        
        profile = get_user_profile(other)
        if not profile:
            bot.answer_callback_query(call.id, "❌ خطا در دریافت اطلاعات!", show_alert=True)
            return
        
        user_id_db, username, first_name, gender, preferred_gender, nickname, photo_id = profile
        
        gender_text = {'male': 'مرد', 'female': 'زن'}.get(gender, 'ثبت نشده')
        pref_text = {'male': 'مرد', 'female': 'زن', 'both': 'هر دو'}.get(preferred_gender, 'ثبت نشده')
        display_name = nickname or first_name or f"کاربر {user_id_db}"
        
        text = f"""
📸 <b>پروفایل همبازی</b>

📛 نام مستعار: {display_name}
⚧ جنسیت: {gender_text}
🎯 ترجیح بازی: {pref_text}
🆔 آیدی: {user_id_db}
"""
        
        kb = InlineKeyboardMarkup(row_width=1)
        if is_favorite(user_id, user_id_db):
            kb.add(InlineKeyboardButton("⭐️ در لیست دلخواه شماست (حذف)", callback_data=f"fav_remove|{user_id_db}", style="danger"))
        else:
            kb.add(InlineKeyboardButton("➕ افزودن به افراد دلخواه", callback_data=f"fav_add|{user_id_db}", style="success"))
        kb.add(InlineKeyboardButton("🔙 بازگشت به بازی", callback_data=f"anon_back_to_game|{match_id}", style="primary"))
        
        if call.message:
            try:
                if photo_id:
                    bot.send_photo(call.message.chat.id, photo_id, caption=text, reply_markup=kb)
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                else:
                    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='HTML')
            except:
                pass
        return

    if action == 'anon_back_to_game':
        match = get_anonymous_match(match_id)
        if not match:
            bot.answer_callback_query(call.id, "❌ بازی یافت نشد!", show_alert=True)
            return
        
        is_my_turn = (match['current_turn'] == user_id)
        turn_text = "نوبت شماست." if is_my_turn else "نوبت همبازی شماست."
        
        text = f"🎭 <b>بازی ناشناس</b>\n\n{turn_text}\n\nبرای شروع روی دکمه «انتخاب سوال» کلیک کنید."
        
        if call.message:
            try:
                if call.message.photo:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(call.message.chat.id, text, reply_markup=anon_game_keyboard(match_id, is_my_turn))
                else:
                    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                                        reply_markup=anon_game_keyboard(match_id, is_my_turn), parse_mode='HTML')
            except:
                pass
        return

    if action == 'anon_like':
        other = get_other_player_in_match(match_id, user_id)
        if not other:
            bot.answer_callback_query(call.id, "❌ همبازی یافت نشد!", show_alert=True)
            return
        
        try:
            save_like(other, user_id, match_id)
            bot.answer_callback_query(call.id, "❤️ به همبازی خود لایک دادید!", show_alert=True)
            
            try:
                other_name = get_user_name(user_id)
                bot.send_message(other, f"❤️ کاربر {other_name} به شما لایک داد!")
            except:
                pass
        except Exception as e:
            print(f"[ERROR] Failed to save like: {e}")
            bot.answer_callback_query(call.id, f"❌ خطا در ثبت لایک!", show_alert=True)
        return

    if action == 'anon_block':
        other = get_other_player_in_match(match_id, user_id)
        if not other:
            bot.answer_callback_query(call.id, "❌ همبازی یافت نشد!", show_alert=True)
            return
        
        try:
            save_block(user_id, other, match_id)
        except:
            pass
        
        try:
            bot.send_message(other, f"🚫 همبازی شما بازی را پایان داد و شما را بلاک کرد!")
        except:
            pass
        
        delete_anonymous_match(match_id)
        edit_or_send_message(call, f"🚫 <b>همبازی بلاک شد!</b>\n\nبازی به پایان رسید.", 
                            reply_markup=main_menu_keyboard())
        return

    if action == 'anon_end':
        other = get_other_player_in_match(match_id, user_id)
        if other:
            try:
                bot.send_message(other, f"🔚 <b>همبازی بازی را پایان داد.</b>\n\nبازی ناشناس به پایان رسید.",
                                reply_markup=main_menu_keyboard())
            except:
                pass
        delete_anonymous_match(match_id)
        edit_or_send_message(call, f"🔚 <b>بازی ناشناس به پایان رسید.</b>\n\nبه منوی اصلی بازگشتید.",
                            reply_markup=main_menu_keyboard())
        return

    print(f"[WARN] Unknown action: {action}")
    bot.answer_callback_query(call.id, "❌ خطا! دوباره تلاش کنید.", show_alert=True)

def create_backup():
    backup_data = {
        'backup_time': datetime.now().isoformat(),
        'users': [],
        'questions': [],
        'forced_channels': [],
        'admins': [],
        'help_buttons': [],
        'anon_matches': [],
        'anon_queue': [],
        'group_games': [],
        'likes': [],
        'blocked_users': []
    }
    cursor.execute("SELECT id, username, first_name, gender, preferred_gender, is_banned, created_at, nickname, profile_photo_file_id FROM users")
    backup_data['users'] = cursor.fetchall()
    cursor.execute("SELECT category, type, text, status, suggested_by FROM questions")
    backup_data['questions'] = cursor.fetchall()
    cursor.execute("SELECT channel_username, join_url FROM forced_channels")
    backup_data['forced_channels'] = cursor.fetchall()
    cursor.execute("SELECT user_id FROM admins")
    backup_data['admins'] = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT button_text, button_callback, content, button_order FROM help_buttons WHERE is_active = 1")
    backup_data['help_buttons'] = cursor.fetchall()
    cursor.execute("SELECT match_id, player1, player2, current_turn, current_question_type, current_question_text, status, created_at, last_activity FROM anon_matches")
    backup_data['anon_matches'] = cursor.fetchall()
    cursor.execute("SELECT user_id, gender, preferred_gender, join_time FROM anon_queue")
    backup_data['anon_queue'] = cursor.fetchall()
    cursor.execute("SELECT game_id, host_id, chat_id, players, current_player, stage, temp_category, current_question_type, current_question_text, status, created_at FROM group_games_db")
    backup_data['group_games'] = cursor.fetchall()
    cursor.execute("SELECT user_id, liked_by, match_id, created_at FROM likes")
    backup_data['likes'] = cursor.fetchall()
    cursor.execute("SELECT user_id, blocked_user, match_id, created_at FROM blocked_users")
    backup_data['blocked_users'] = cursor.fetchall()
    return backup_data

def cleanup_old_anon_games():
    while True:
        time.sleep(300)
        cursor.execute("DELETE FROM anon_matches WHERE last_activity < ? AND status = 'playing'", (int(time.time()) - 3600,))
        conn.commit()
        cursor.execute("DELETE FROM anon_queue WHERE join_time < ?", (int(time.time()) - 300,))
        conn.commit()
        cursor.execute("DELETE FROM group_games_db WHERE created_at < ? AND status != 'playing'", (int(time.time()) - 7200,))
        conn.commit()
        for gid in list(group_games.keys()):
            try:
                created_at = group_games[gid].get('created_at')
                if created_at is not None:
                    if isinstance(created_at, str):
                        created_at = int(created_at) if created_at.isdigit() else 0
                    if created_at < time.time() - 7200 and group_games[gid].get('status') != 'playing':
                        del group_games[gid]
            except (KeyError, ValueError, TypeError):
                pass

threading.Thread(target=cleanup_old_anon_games, daemon=True).start()

@bot.message_handler(commands=['approveall'])
def approve_all_questions(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ شما ادمین نیستید!")
        return
    cursor.execute("SELECT COUNT(*) FROM questions WHERE status = 'pending'")
    count = cursor.fetchone()[0]
    if count == 0:
        bot.reply_to(message, "✅ هیچ سوال در انتظار تاییدی وجود ندارد!")
        return
    cursor.execute("UPDATE questions SET status = 'approved' WHERE status = 'pending'")
    conn.commit()
    log_admin_action(message.from_user.id, "approve_all", f"approved {count} questions")
    bot.reply_to(message, f"✅ {count} سوال با موفقیت تایید شدند!")

if __name__ == "__main__":
    load_group_games()
    print("bot runned")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)
