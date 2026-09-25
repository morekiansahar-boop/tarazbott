# -*- coding: utf-8 -*-
"""
ربات تلگرامی «تخمین تراز سوابق تحصیلی و کنکور ۱۴۰۴ - رشته تجربی»

منطق این ربات دقیقاً بر اساس فرمول‌های اکسل taraz_estimator است:
- جست‌وجوی خطی (lookup + interpolation) در جدول‌های واقعی کارنامه‌ها
- میانگین وزنی با ضرایب رسمی
- ترکیب نهایی سوابق/کنکور طبق سهم ۶۰٪ / ۴۰٪ اعلامی سال ۱۴۰۴
- بازه‌ی تخمینی «خوش‌بینانه» حول هر عدد نهایی (چون کنکور ۱۴۰۴ سخت بوده و
  خیلی از داوطلب‌ها درصد پایین‌تری نسبت به سال‌های قبل زدن)

هر درس در یک پیام جداگانه پرسیده می‌شه (نه یک‌جا)، و فقط نتیجه‌ی نهایی
(بدون ریز تراز هر درس) نمایش داده می‌شه.

امکانات ادمین:
- کاربرها می‌تونن از دکمه‌ی «ارسال پیام به پشتیبانی» برای ادمین پیام بفرستن.
- ادمین با دستور /broadcast <متن> می‌تونه به همه‌ی کاربرهایی که تا حالا /start
  زدن، یه پیام (مثلاً تبلیغ کانال) بفرسته.
- دستور /myid به هرکسی شناسه‌ی عددی خودش رو نشون می‌ده (برای گرفتن ADMIN_ID).

نکته‌ی بعدی که در دست ساختنه: تخمین رتبه (بر اساس فایل تبدیل تراز-به-رتبه
که قراره جداگانه اضافه بشه) — فعلاً فقط تراز نهایی نمایش داده می‌شه.
"""

import json
import logging
import os
import re
from pathlib import Path

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent / "taraz_data.json"
with open(DATA_PATH, "r", encoding="utf-8") as f:
    DATA = json.load(f)

MAJORS_PATH = Path(__file__).parent / "majors_tajrobi.json"
try:
    with open(MAJORS_PATH, "r", encoding="utf-8") as f:
        MAJORS_DATA = json.load(f)
except FileNotFoundError:
    MAJORS_DATA = []

DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent)
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_PATH = DATA_DIR / "users.json"
CONTACT_MAP_PATH = DATA_DIR / "contact_map.json"


def _parse_admin_id() -> int:
    """ADMIN_ID رو از متغیر محیطی می‌خونه؛ اگه خالی یا نامعتبر بود (مثلاً فاصله
    یا کاراکتر اضافه داشت)، به‌جای کرش کردن کل ربات، صفر برمی‌گردونه و فقط
    قابلیت‌های ادمین (پشتیبانی/Broadcast) غیرفعال می‌مونن."""
    raw = (os.environ.get("ADMIN_ID") or "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        logger.warning("ADMIN_ID نامعتبره (%r)؛ قابلیت‌های ادمین غیرفعال می‌مونن.", raw)
        return 0


ADMIN_ID = _parse_admin_id()

# ---------------------------------------------------------------------------
# تعریف درس‌ها (دقیقاً مطابق دو شیت اکسل)
# هر آیتم: (نام نمایشی, ضریب رسمی, ایندکس ستون در جدول Data_*)
# ---------------------------------------------------------------------------

SAWABEGH_SUBJECTS = [
    ("ادبیات فارسی ۳", 11.09, 1),
    ("عربی، زبان قرآن ۳", 4.64, 2),
    ("دینی ۳", 8.47, 3),
    ("زبان انگلیسی ۳", 6.05, 4),
    ("سلامت و بهداشت", 1.76, 5),
    ("علوم اجتماعی", 1.31, 6),
    ("زیست‌شناسی ۳", 11.45, 7),
    ("ریاضی ۳", 6.55, 8),
    ("فیزیک ۳", 5.90, 9),
    ("شیمی ۳", 9.44, 10),
]

KONKUR_SUBJECTS = [
    ("زیست‌شناسی", 12, 1, True),
    ("شیمی", 9, 2, True),
    ("فیزیک", 7, 3, True),
    ("ریاضی", 7, 4, True),
    ("زمین‌شناسی", 1, 5, False),  # اختیاری
]

SAWABEGH_TABLE = DATA["sawabegh"]  # هر ردیف: [نمره, ادبیات, عربی, ..., شیمی]
KONKUR_TABLE = DATA["konkur"]  # هر ردیف: [درصد, زیست, شیمی, فیزیک, ریاضی, زمین]

# جدول‌های تراز-به-رتبه (اختیاری) — هر ردیف [تراز, رتبه]، صعودی بر اساس تراز
RANK_TABLES = DATA.get("rank_by_region", {})
REGION_LABELS = {
    "region1": "منطقه ۱",
    "region2": "منطقه ۲",
    "region3": "منطقه ۳",
}

SAWABEGH_MIN_GRADE = SAWABEGH_TABLE[0][0]  # کوچیک‌ترین نمره‌ای که جدول پوشش می‌ده

SAWABEGH_RECORDS_SHARE = 0.60  # سهم رسمی سوابق تحصیلی در ۱۴۰۴
KONKUR_SPECIALIZED_SHARE = 0.40  # سهم دروس تخصصی کنکور

# بازه‌ی تخمینی حول هر عدد نهایی — «خوش‌بینانه»: سمت پایین بازه رو کمتر و
# سمت بالا رو بیشتر می‌کشیم، چون کنکور ۱۴۰۴ سخت بوده و درصدهای عمومی پایین‌تر
# از سال‌های قبل بوده.
SAWABEGH_MARGIN_PERCENT = 0.013  # ±۱.۳٪ پایه
KONKUR_MARGIN_PERCENT = 0.018    # ±۱.۸٪ پایه
FINAL_MARGIN_PERCENT = 0.018     # ±۱.۸٪ پایه
OPTIMISM_SKEW = 0.4              # هرچه بیشتر، بازه بیشتر به سمت بالا کشیده می‌شه

# فارسی/عربی -> ارقام لاتین، برای اینکه هرجور کاربر عدد را تایپ کند بخوانیم
DIGIT_MAP = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
)

SKIP_TOKENS = {"-", "_", "x", "X", "ندادم", "رد", "خالی", "skip", "none", ""}

# ---------------------------------------------------------------------------
# حالت‌های مکالمه
# ---------------------------------------------------------------------------
MAIN_MENU, SAWABEGH_Q, KONKUR_Q, CONTACT_INPUT, REGION_Q, MAJORS_Q, RANK_TOOL_REGION_Q, RANK_TOOL_RANK_Q, TARAZ_TOOL_REGION_Q, TARAZ_TOOL_TARAZ_Q, SEARCH_MAJOR_Q, FILTER_COURSE_Q, FILTER_CITY_Q = range(13)

MENU_SAWABEGH = "📚 تراز سوابق تحصیلی"
MENU_KONKUR = "📝 تراز کنکور (اختصاصی)"
MENU_BOTH = "🎯 هردو + تراز نهایی"
MENU_CONTACT = "✉️ ارسال پیام به پشتیبانی"
MENU_RANK_TOOL = "🎓 انتخاب رشته (رتبه‌مو می‌دونم)"
MENU_TARAZ_TOOL = "🏆 انتخاب رشته (ترازمو می‌دونم)"
MENU_SEARCH_MAJOR = "🔍 جستجوی رتبه‌ی قبولی یه رشته"
MENU_CANCEL = "لغو"
SKIP_TEXT = "رد شدن (امتحان نداده‌ام)"

MENU_REGION_1 = "🟢 منطقه ۱"
MENU_REGION_2 = "🟡 منطقه ۲"
MENU_REGION_3 = "🔵 منطقه ۳"
REGION_BUTTON_MAP = {
    MENU_REGION_1: "region1",
    MENU_REGION_2: "region2",
    MENU_REGION_3: "region3",
}

MENU_MAJORS_YES = "✅ بله، نشونم بده"
MENU_MAJORS_NO = "❌ نه، لازم نیست"
MAJORS_MAX_PER_CATEGORY = 8  # سقف تعداد رشته‌ی نمایش‌داده‌شده در هر دسته
RANK_TOOL_MARGIN_PERCENT = 0.05  # ±۵٪ حول رتبه‌ی مرزی هر رشته، برای ابزار رتبه‌ی دقیق

# --- فیلتر نوع دوره (اضافه‌شده به همه‌ی ابزارهای انتخاب رشته) ---
MENU_COURSE_DAILY = "🏛️ فقط روزانه (رایگان)"
MENU_COURSE_GOV = "🏫 دولتی (روزانه+شبانه+پردیس و مشابه)"
MENU_COURSE_ALL = "🌐 همه‌ی دوره‌ها (شامل غیرانتفاعی/پیام‌نور/آزاد)"
COURSE_PRESETS = {
    MENU_COURSE_DAILY: {"روزانه"},
    MENU_COURSE_GOV: {"روزانه", "شبانه", "پردیس", "ظرفیت مازاد", "محروم", "تعهدی"},
    MENU_COURSE_ALL: None,  # None یعنی فیلتری اعمال نشه (همه‌ی نوع دوره‌ها)
}

# --- فیلتر شهر/دانشگاه (اختیاری) ---
MENU_CITY_SKIP = "🌍 نه، همه‌جا رو نشون بده"


# ---------------------------------------------------------------------------
# ذخیره‌ی ساده‌ی کاربرها (برای Broadcast)
# توجه: این فایل روی دیسک همون کانتینره؛ با هر Redeploy ممکنه پاک بشه مگر
# اینکه یه Volume روی Railway بهش وصل کنی.
# ---------------------------------------------------------------------------

def load_users() -> set:
    if not USERS_PATH.exists():
        return set()
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_users(users: set):
    try:
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(users), f)
    except OSError as e:
        logger.warning("Could not save users.json: %s", e)


def register_user(update: Update):
    if not update.effective_user:
        return
    users = load_users()
    uid = update.effective_user.id
    if uid not in users:
        users.add(uid)
        save_users(users)


def load_contact_map() -> dict:
    """نگاشت شناسه‌ی پیامی که برای ادمین فوروارد شده -> شناسه‌ی چت کاربر اصلی.
    این باعث می‌شه وقتی ادمین روی پیام فوروارد‌شده Reply می‌زنه، بدونیم جوابش
    باید برای کدوم کاربر برگرده."""
    if not CONTACT_MAP_PATH.exists():
        return {}
    try:
        with open(CONTACT_MAP_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_contact_map(mapping: dict):
    try:
        with open(CONTACT_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)
    except OSError as e:
        logger.warning("Could not save contact_map.json: %s", e)


def parse_number(token: str):
    """عدد فارسی/انگلیسی را به float تبدیل می‌کند، یا None برمی‌گرداند."""
    cleaned = token.strip().translate(DIGIT_MAP)
    cleaned = cleaned.replace(",", ".").replace("٫", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def interpolate(table, col_index: int, x: float) -> float:
    """
    دقیقاً معادل فرمول اکسل:
    IF(x<=first_key, first_value,
       IF(x>=last_key, last_value,
          interpolation خطی بین دو نقطه‌ی همسایه))
    """
    keys = [row[0] for row in table]
    if x <= keys[0]:
        return table[0][col_index]
    if x >= keys[-1]:
        return table[-1][col_index]
    for i in range(len(keys) - 1):
        x0, x1 = keys[i], keys[i + 1]
        if x0 <= x <= x1:
            y0, y1 = table[i][col_index], table[i + 1][col_index]
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][col_index]


def rank_to_taraz(region_key: str, rank: float):
    """برعکس تابع بالا: از رو یه رتبه، تراز تقریبی همون منطقه رو درمیاره
    (با معکوس کردن جدول تراز->رتبه و درون‌یابی رو رتبه به‌جای تراز)."""
    table = RANK_TABLES.get(region_key)
    if not table:
        return None
    inv = sorted(table, key=lambda r: r[1])
    inv_table = [[r[1], r[0]] for r in inv]
    return interpolate(inv_table, 1, rank)


def taraz_range_values(value: int, percent: float):
    """
    بازه‌ی تخمینی «خوش‌بینانه» حول یک عدد نهایی: سمت پایین کوچیک‌تر، سمت بالا
    بزرگ‌تر (چون کنکور امسال سخت بوده و کف تراز واقعی معمولاً بالاتر از
    محاسبه‌ی خام درمیاد). خروجی: (پایین, بالا)
    """
    margin = value * percent
    low = round(value - margin * (1 - OPTIMISM_SKEW))
    high = round(value + margin * (1 + OPTIMISM_SKEW))
    return low, high


def format_range(low: int, high: int) -> str:
    return f"{low:,} تا {high:,}".replace(",", "٬")


def range_text(value: int, percent: float) -> str:
    low, high = taraz_range_values(value, percent)
    return format_range(low, high)


def region_keyboard():
    return ReplyKeyboardMarkup(
        [[MENU_REGION_1], [MENU_REGION_2], [MENU_REGION_3], [MENU_CANCEL]],
        resize_keyboard=True,
    )


def majors_yesno_keyboard():
    return ReplyKeyboardMarkup(
        [[MENU_MAJORS_YES], [MENU_MAJORS_NO]], resize_keyboard=True
    )


def course_filter_keyboard():
    return ReplyKeyboardMarkup(
        [[MENU_COURSE_DAILY], [MENU_COURSE_GOV], [MENU_COURSE_ALL], [MENU_CANCEL]],
        resize_keyboard=True,
    )


def city_filter_keyboard():
    return ReplyKeyboardMarkup(
        [[MENU_CITY_SKIP], [MENU_CANCEL]], resize_keyboard=True
    )


def main_menu_keyboard():
    rows = [[MENU_SAWABEGH], [MENU_KONKUR], [MENU_BOTH]]
    if MAJORS_DATA:
        rows.append([MENU_RANK_TOOL])
    if MAJORS_DATA and RANK_TABLES:
        rows.append([MENU_TARAZ_TOOL])
    if MAJORS_DATA:
        rows.append([MENU_SEARCH_MAJOR])
    rows.append([MENU_CONTACT])
    rows.append([MENU_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def cancel_keyboard(extra_skip: bool = False):
    rows = [[MENU_CANCEL]]
    if extra_skip:
        rows.insert(0, [SKIP_TEXT])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# ---------------------------------------------------------------------------
# شروع / منو
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    register_user(update)
    context.user_data.clear()
    await update.message.reply_text(
        "سلام 👋\n"
        "این ربات تراز تخمینی سوابق تحصیلی و کنکور ۱۴۰۵ (رشته تجربی) رو بر اساس "
        "جدول واقعی کارنامه‌های ۱۴۰۴ حساب می‌کنه.\n\n"
        "یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_SAWABEGH:
        return await start_sawabegh(update, context, then_konkur=False)
    if text == MENU_KONKUR:
        return await start_konkur(update, context, combine=False)
    if text == MENU_BOTH:
        return await start_sawabegh(update, context, then_konkur=True)
    if text == MENU_CONTACT:
        return await prompt_contact(update, context)
    if text == MENU_RANK_TOOL and MAJORS_DATA:
        return await start_rank_tool(update, context)
    if text == MENU_TARAZ_TOOL and MAJORS_DATA and RANK_TABLES:
        return await start_taraz_tool(update, context)
    if text == MENU_SEARCH_MAJOR and MAJORS_DATA:
        return await start_search_major(update, context)
    if text == MENU_CANCEL:
        return await cancel(update, context)
    await update.message.reply_text(
        "لطفاً یکی از گزینه‌های روی صفحه‌کلید رو انتخاب کن.",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


# ---------------------------------------------------------------------------
# مسیر سوابق تحصیلی — هر درس در یک پیام جداگانه
# ---------------------------------------------------------------------------
async def start_sawabegh(update: Update, context: ContextTypes.DEFAULT_TYPE, then_konkur: bool) -> int:
    context.user_data["saw_index"] = 0
    context.user_data["saw_scores"] = {}
    context.user_data["then_konkur"] = then_konkur
    await update.message.reply_text(
        f"نمره‌ی نهایی هر درس رو بین {SAWABEGH_MIN_GRADE:g} تا ۲۰ وارد کن.",
        reply_markup=cancel_keyboard(),
    )
    await ask_next_sawabegh(update, context)
    return SAWABEGH_Q


async def ask_next_sawabegh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data["saw_index"]
    name, coeff, _col = SAWABEGH_SUBJECTS[idx]
    await update.message.reply_text(f"نمره‌ی «{name}» (ضریب {coeff}) چنده؟")


async def handle_sawabegh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    idx = context.user_data["saw_index"]
    name, coeff, col = SAWABEGH_SUBJECTS[idx]
    value = parse_number(text)
    if value is None:
        await update.message.reply_text("یه عدد معتبر بفرست، مثلاً 17.5")
        return SAWABEGH_Q
    if value < 0 or value > 20:
        await update.message.reply_text("نمره باید بین ۰ تا ۲۰ باشه. دوباره وارد کن:")
        return SAWABEGH_Q
    if value < SAWABEGH_MIN_GRADE:
        await update.message.reply_text(
            f"⚠️ توجه: جدول منبع فقط از نمره‌ی {SAWABEGH_MIN_GRADE:g} به بالا رو پوشش می‌ده، "
            "برای این نمره دقت مدل کمتره."
        )

    context.user_data["saw_scores"][idx] = value
    context.user_data["saw_index"] += 1

    if context.user_data["saw_index"] < len(SAWABEGH_SUBJECTS):
        await ask_next_sawabegh(update, context)
        return SAWABEGH_Q

    result = compute_sawabegh(context.user_data["saw_scores"])
    context.user_data["saw_result"] = result
    await update.message.reply_text(
        f"✅ تراز تخمینی سوابق تحصیلی: {result['final']:.0f}\n"
        f"📊 بازه‌ی تخمینی: {range_text(result['final'], SAWABEGH_MARGIN_PERCENT)}"
    )

    if context.user_data.get("then_konkur"):
        return await start_konkur(update, context, combine=True)

    await update.message.reply_text(
        "برای شروع دوباره /start رو بزن.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def compute_sawabegh(scores: dict) -> dict:
    per_subject = []
    for i, (name, coeff, col) in enumerate(SAWABEGH_SUBJECTS):
        val = interpolate(SAWABEGH_TABLE, col, scores[i])
        per_subject.append(val)
    total_coeff = sum(c for _n, c, _c in SAWABEGH_SUBJECTS)
    weighted = sum(c * v for (_n, c, _c), v in zip(SAWABEGH_SUBJECTS, per_subject))
    final = round(weighted / total_coeff)
    return {"per_subject": per_subject, "final": final}


# ---------------------------------------------------------------------------
# مسیر کنکور — هر درس در یک پیام جداگانه
# ---------------------------------------------------------------------------
async def start_konkur(update: Update, context: ContextTypes.DEFAULT_TYPE, combine: bool) -> int:
    context.user_data["kon_index"] = 0
    context.user_data["kon_scores"] = {}
    context.user_data["combine"] = combine
    await update.message.reply_text(
        "درصد تراز خودت رو تو هر درس تخصصی وارد کن (از منفی ۵ تا ۱۰۰).",
        reply_markup=cancel_keyboard(),
    )
    await ask_next_konkur(update, context)
    return KONKUR_Q


async def ask_next_konkur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data["kon_index"]
    name, coeff, _col, required = KONKUR_SUBJECTS[idx]
    optional_note = "" if required else " (اگه امتحان ندادی رد شدن رو بزن)"
    await update.message.reply_text(
        f"درصد «{name}» (ضریب {coeff}) چنده؟{optional_note}",
        reply_markup=cancel_keyboard(extra_skip=not required),
    )


async def handle_konkur(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    idx = context.user_data["kon_index"]
    name, coeff, col, required = KONKUR_SUBJECTS[idx]

    if text == SKIP_TEXT and not required:
        context.user_data["kon_scores"][idx] = None
    else:
        value = parse_number(text)
        if value is None:
            await update.message.reply_text("یه عدد معتبر بفرست، مثلاً 45")
            return KONKUR_Q
        if value < -5 or value > 100:
            await update.message.reply_text("درصد باید بین ۵- تا ۱۰۰ باشه. دوباره وارد کن:")
            return KONKUR_Q
        context.user_data["kon_scores"][idx] = value

    context.user_data["kon_index"] += 1

    if context.user_data["kon_index"] < len(KONKUR_SUBJECTS):
        await ask_next_konkur(update, context)
        return KONKUR_Q

    filled = sum(1 for v in context.user_data["kon_scores"].values() if v is not None)
    if filled < 4:
        await update.message.reply_text(
            "حداقل باید ۴ درس اصلی رو وارد کنی. بیا از اول این بخش شروع کنیم."
        )
        return await start_konkur(update, context, combine=context.user_data.get("combine", False))

    result = compute_konkur(context.user_data["kon_scores"])
    context.user_data["kon_result"] = result
    await update.message.reply_text(
        f"✅ تراز تخمینی کنکور (اختصاصی): {result['final']:.0f}\n"
        f"📊 بازه‌ی تخمینی: {range_text(result['final'], KONKUR_MARGIN_PERCENT)}"
    )

    if context.user_data.get("combine") and "saw_result" in context.user_data:
        next_state = await send_final_combination(update, context)
        if next_state == REGION_Q:
            return REGION_Q

    await update.message.reply_text(
        "برای شروع دوباره /start رو بزن.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def compute_konkur(scores: dict) -> dict:
    per_subject = []
    weighted = 0.0
    coeff_used = 0.0
    for i, (name, coeff, col, _req) in enumerate(KONKUR_SUBJECTS):
        v = scores[i]
        if v is None:
            per_subject.append(None)
            continue
        val = interpolate(KONKUR_TABLE, col, v)
        per_subject.append(val)
        weighted += coeff * val
        coeff_used += coeff
    final = round(weighted / coeff_used) if coeff_used else 0
    return {"per_subject": per_subject, "final": final}


async def send_final_combination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نتیجه‌ی نهایی رو می‌فرسته؛ اگه داده‌ی رتبه موجود باشه، REGION_Q برمی‌گردونه
    تا از کاربر منطقه‌ی کنکورش رو بپرسه، وگرنه None."""
    saw_final = context.user_data["saw_result"]["final"]
    kon_final = context.user_data["kon_result"]["final"]
    combined = round(
        SAWABEGH_RECORDS_SHARE * saw_final + KONKUR_SPECIALIZED_SHARE * kon_final
    )
    low, high = taraz_range_values(combined, FINAL_MARGIN_PERCENT)
    context.user_data["final_taraz_range"] = (low, high)

    await update.message.reply_text(
        "———————————\n"
        f"🏁 تراز نهایی تخمینی: {combined}\n"
        f"📊 بازه‌ی تخمینی: {format_range(low, high)}\n\n"
        "⚠️ این عدد فقط یه تخمینه؛ سهم دروس عمومی کنکور در این محاسبه لحاظ نشده "
        "چون طبق تغییرات ۱۴۰۴ حذف شدن."
    )

    if RANK_TABLES:
        await update.message.reply_text(
            "برای گرفتن بازه‌ی رتبه‌ی تخمینی، منطقه‌ی کنکورت رو انتخاب کن:",
            reply_markup=region_keyboard(),
        )
        return REGION_Q
    return None


async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    region_key = REGION_BUTTON_MAP.get(text)
    if not region_key:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های منطقه رو از روی صفحه‌کلید انتخاب کن.",
            reply_markup=region_keyboard(),
        )
        return REGION_Q

    low, high = context.user_data.get("final_taraz_range", (None, None))
    table = RANK_TABLES.get(region_key)
    if low is None or not table:
        await update.message.reply_text(
            "⚠️ داده‌ی رتبه برای این حالت موجود نیست.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await update.message.reply_text("برای شروع دوباره /start رو بزن.")
        return ConversationHandler.END

    # چون تراز بالاتر یعنی رتبه‌ی بهتر (عدد کوچیک‌تر)، بازه‌ی تراز رو
    # به بازه‌ی رتبه معکوس می‌کنیم.
    best_rank = round(interpolate(table, 1, high))
    worst_rank = round(interpolate(table, 1, low))
    if best_rank > worst_rank:
        best_rank, worst_rank = worst_rank, best_rank

    context.user_data["region_key"] = region_key
    context.user_data["rank_range"] = (best_rank, worst_rank)

    await update.message.reply_text(
        f"🏅 رتبه‌ی تخمینی در {REGION_LABELS.get(region_key, region_key)}: "
        f"{format_range(best_rank, worst_rank)}\n\n"
        "⚠️ این بازه هم بر اساس کارنامه‌های واقعی همون تراز به‌دست اومده و صرفاً یک تخمینه."
    )

    if MAJORS_DATA:
        await update.message.reply_text(
            "می‌خوای رشته‌های احتمالی قبولیت (فقط دوره‌ی روزانه، رشته‌ی تجربی) رو هم ببینی؟",
            reply_markup=majors_yesno_keyboard(),
        )
        return MAJORS_Q

    await update.message.reply_text(
        "برای شروع دوباره /start رو بزن.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def normalize_fa(text: str) -> str:
    """نرمال‌سازی ساده‌ی حروف عربی/فارسی رایج، برای مقایسه‌ی متن‌ها."""
    return (text or "").replace("ي", "ی").replace("ك", "ک")


def major_matches_filters(rec: dict, course_types, city_filter: str) -> bool:
    """چک می‌کنه یه رکورد رشته، فیلترهای نوع دوره و شهر/دانشگاه رو رعایت می‌کنه یا نه."""
    if course_types is not None and rec.get("course_type") not in course_types:
        return False
    if city_filter:
        haystack = normalize_fa((rec.get("university") or "") + " " + (rec.get("city") or ""))
        if normalize_fa(city_filter) not in haystack:
            return False
    return True


def classify_majors_by_range(region_key: str, best_rank: float, worst_rank: float, course_types=None, city_filter: str = None):
    """چهار دسته (قطعی/محتمل/ریسکی/بعید) بر اساس یه بازه‌ی رتبه می‌سازه."""
    range_width = worst_rank - best_rank
    if range_width <= 0:
        range_width = max(1, round(best_rank * 0.05))  # حالت خیلی نادر که بازه صفر بشه
    mid_rank = (best_rank + worst_rank) / 2
    unlikely_floor = best_rank - 0.5 * range_width

    definite, likely, risky, unlikely = [], [], [], []
    for rec in MAJORS_DATA:
        if not major_matches_filters(rec, course_types, city_filter):
            continue
        cutoff = rec.get(region_key)
        if cutoff is None:
            continue
        if cutoff >= worst_rank:
            definite.append((cutoff, rec))
        elif cutoff >= mid_rank:
            likely.append((cutoff, rec))
        elif cutoff >= best_rank:
            risky.append((cutoff, rec))
        elif cutoff >= unlikely_floor:
            unlikely.append((cutoff, rec))
    return definite, likely, risky, unlikely


def classify_majors_by_exact_rank(region_key: str, user_rank: int, course_types=None, city_filter: str = None):
    """چهار دسته بر اساس یه رتبه‌ی دقیق (که خود کاربر می‌دونتش) می‌سازه.
    چون رتبه‌ی کاربر دقیقه، عدم‌قطعیت این‌جا از نوسان رتبه‌ی مرزی هر رشته
    نسبت به سال قبله؛ برای همین بازه رو دور رتبه‌ی مرزی هر رشته می‌سازیم."""
    definite, likely, risky, unlikely = [], [], [], []
    for rec in MAJORS_DATA:
        if not major_matches_filters(rec, course_types, city_filter):
            continue
        cutoff = rec.get(region_key)
        if cutoff is None:
            continue
        step = max(1, round(cutoff * RANK_TOOL_MARGIN_PERCENT))
        if user_rank <= cutoff - step:
            definite.append((cutoff, rec))
        elif user_rank <= cutoff:
            likely.append((cutoff, rec))
        elif user_rank <= cutoff + step:
            risky.append((cutoff, rec))
        elif user_rank <= cutoff + 2 * step:
            unlikely.append((cutoff, rec))
    return definite, likely, risky, unlikely


def fmt_majors_list(items):
    lines = []
    for cutoff, rec in items[:MAJORS_MAX_PER_CATEGORY]:
        uni = rec.get("university") or ""
        city = rec.get("city") or ""
        place = f"{uni} - {city}" if city else uni
        lines.append(f"• {rec.get('major')} ({place}) — آخرین رتبه‌ی قبولی: {cutoff:,}".replace(",", "٬"))
    remaining = len(items) - MAJORS_MAX_PER_CATEGORY
    if remaining > 0:
        lines.append(f"…و {remaining} مورد دیگر")
    return "\n".join(lines) if lines else "موردی پیدا نشد."


async def send_majors_results(update: Update, definite, likely, risky, unlikely, course_types=None, city_filter: str = None):
    """درون هر دسته: دسته‌های امن (قطعی/محتمل) پرستیژی‌ترین گزینه‌ها اول؛
    دسته‌های پرریسک (ریسکی/بعید) امن‌ترین گزینه‌ی همون دسته اول."""
    definite.sort(key=lambda x: x[0])
    likely.sort(key=lambda x: x[0])
    risky.sort(key=lambda x: x[0], reverse=True)
    unlikely.sort(key=lambda x: x[0], reverse=True)

    parts = []
    if definite:
        parts.append("🟢 قطعی (خیلی محتمل):\n" + fmt_majors_list(definite))
    if likely:
        parts.append("🔵 محتمل:\n" + fmt_majors_list(likely))
    if risky:
        parts.append("🟡 ریسکی:\n" + fmt_majors_list(risky))
    if unlikely:
        parts.append("🟠 بعید (شانس کمی داره):\n" + fmt_majors_list(unlikely))
    if not parts:
        parts.append("متأسفانه با این فیلترها، رشته‌ای پیدا نشد. سعی کن فیلتر شهر رو بردار یا نوع دوره رو گسترده‌تر کن.")

    if course_types is None:
        course_note = "همه‌ی نوع دوره‌ها (روزانه، شبانه، پردیس، غیرانتفاعی، پیام‌نور، آزاد و...)"
    else:
        course_note = "، ".join(sorted(course_types))
    city_note = f" | فیلتر شهر/دانشگاه: «{city_filter}»" if city_filter else ""
    warning = (
        f"⚠️ این لیست بر اساس فیلتر انتخابی توئه — نوع دوره: {course_note}{city_note}. "
        "رتبه‌ها از کارنامه‌های واقعی سال قبله، صرفاً یک تخمینه."
    )

    if not (definite or likely or risky or unlikely):
        await update.message.reply_text(parts[0] + "\n\n" + warning, reply_markup=ReplyKeyboardRemove())
    else:
        # برای اینکه پیام از سقف تلگرام رد نشه، تو دو پیام جدا می‌فرستیم.
        first_half = [p for p in parts if p.startswith(("🟢", "🔵"))]
        second_half = [p for p in parts if p.startswith(("🟡", "🟠"))]
        if first_half:
            await update.message.reply_text("\n\n".join(first_half), reply_markup=ReplyKeyboardRemove())
        if second_half:
            await update.message.reply_text("\n\n".join(second_half))
        await update.message.reply_text(warning)


async def handle_majors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    if text == MENU_MAJORS_NO:
        await update.message.reply_text(
            "باشه. برای شروع دوباره /start رو بزن.", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    if text != MENU_MAJORS_YES:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های روی صفحه‌کلید رو انتخاب کن.",
            reply_markup=majors_yesno_keyboard(),
        )
        return MAJORS_Q

    region_key = context.user_data.get("region_key")
    best_rank, worst_rank = context.user_data.get("rank_range", (None, None))
    if not region_key or best_rank is None:
        await update.message.reply_text(
            "⚠️ مشکلی پیش اومد، دوباره از /start شروع کن.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    context.user_data["pending_majors"] = {
        "mode": "range",
        "region_key": region_key,
        "best_rank": best_rank,
        "worst_rank": worst_rank,
    }
    await update.message.reply_text(
        "کدوم نوع دوره‌ها برات مهمه؟", reply_markup=course_filter_keyboard()
    )
    return FILTER_COURSE_Q


# ---------------------------------------------------------------------------
# ابزار انتخاب رشته با رتبه‌ی دقیق (وقتی خود کاربر رتبه‌شو می‌دونه)
# ---------------------------------------------------------------------------
async def start_rank_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "منطقه‌ی کنکورت رو انتخاب کن:", reply_markup=region_keyboard()
    )
    return RANK_TOOL_REGION_Q


async def handle_rank_tool_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    region_key = REGION_BUTTON_MAP.get(text)
    if not region_key:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های منطقه رو از روی صفحه‌کلید انتخاب کن.",
            reply_markup=region_keyboard(),
        )
        return RANK_TOOL_REGION_Q

    context.user_data["rank_tool_region"] = region_key
    await update.message.reply_text(
        "رتبه‌ت رو بنویس (فقط عدد):", reply_markup=cancel_keyboard()
    )
    return RANK_TOOL_RANK_Q


async def handle_rank_tool_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    value = parse_number(text)
    if value is None or value <= 0:
        await update.message.reply_text("یه رتبه‌ی معتبر (عدد مثبت) بفرست، مثلاً 8500")
        return RANK_TOOL_RANK_Q

    user_rank = round(value)
    region_key = context.user_data.get("rank_tool_region")
    if not region_key:
        await update.message.reply_text(
            "⚠️ مشکلی پیش اومد، دوباره از /start شروع کن.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    context.user_data["pending_majors"] = {
        "mode": "exact",
        "region_key": region_key,
        "rank": user_rank,
    }
    await update.message.reply_text(
        "کدوم نوع دوره‌ها برات مهمه؟", reply_markup=course_filter_keyboard()
    )
    return FILTER_COURSE_Q


# ---------------------------------------------------------------------------
# ابزار انتخاب رشته با تراز (وقتی خود کاربر یا از رو یه کارنامه‌ی دیگه ترازشو می‌دونه)
# ---------------------------------------------------------------------------
async def start_taraz_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "منطقه‌ی کنکورت رو انتخاب کن:", reply_markup=region_keyboard()
    )
    return TARAZ_TOOL_REGION_Q


async def handle_taraz_tool_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    region_key = REGION_BUTTON_MAP.get(text)
    if not region_key:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های منطقه رو از روی صفحه‌کلید انتخاب کن.",
            reply_markup=region_keyboard(),
        )
        return TARAZ_TOOL_REGION_Q

    context.user_data["taraz_tool_region"] = region_key
    await update.message.reply_text(
        "تراز کلت رو بنویس (فقط عدد):", reply_markup=cancel_keyboard()
    )
    return TARAZ_TOOL_TARAZ_Q


async def handle_taraz_tool_taraz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    value = parse_number(text)
    if value is None or value <= 0:
        await update.message.reply_text("یه تراز معتبر (عدد مثبت) بفرست، مثلاً 8500")
        return TARAZ_TOOL_TARAZ_Q

    user_taraz = value
    region_key = context.user_data.get("taraz_tool_region")
    if not region_key:
        await update.message.reply_text(
            "⚠️ مشکلی پیش اومد، دوباره از /start شروع کن.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    table = RANK_TABLES.get(region_key)
    if not table:
        await update.message.reply_text(
            "⚠️ داده‌ی رتبه برای این منطقه موجود نیست.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    # همون جدول تراز->رتبه رو مستقیم استفاده می‌کنیم (دقیقاً مثل جایی که تراز
    # نهایی ترکیبی رو به رتبه تبدیل می‌کردیم)، بعد با رتبه‌ی به‌دست‌اومده،
    # همون منطق «رتبه‌ی دقیق» رو برای پیدا کردن رشته‌ها به کار می‌بریم.
    estimated_rank = round(interpolate(table, 1, user_taraz))
    await update.message.reply_text(
        f"📊 با تراز {user_taraz:g} تو {REGION_LABELS.get(region_key, region_key)}, "
        f"رتبه‌ی تخمینی حدود {estimated_rank:,} می‌شه.".replace(",", "٬")
    )

    context.user_data["pending_majors"] = {
        "mode": "exact",
        "region_key": region_key,
        "rank": estimated_rank,
    }
    await update.message.reply_text(
        "کدوم نوع دوره‌ها برات مهمه؟", reply_markup=course_filter_keyboard()
    )
    return FILTER_COURSE_Q


# ---------------------------------------------------------------------------
# جستجوی رتبه‌ی قبولی یه رشته با اسمش (برعکسِ ابزارهای بالا: این‌جا از رشته
# می‌رسیم به رتبه/تراز، نه برعکس)
# ---------------------------------------------------------------------------
SEARCH_MAX_RESULTS = 20  # سقف تعداد نتیجه‌ای که نشون داده می‌شه


async def start_search_major(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["pending_majors"] = {"mode": "search-init"}
    await update.message.reply_text(
        "اول بگو کدوم نوع دوره‌ها برات مهمه؟", reply_markup=course_filter_keyboard()
    )
    return FILTER_COURSE_Q


async def handle_search_major(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    query = normalize_fa(text)
    if len(query) < 2:
        await update.message.reply_text("لطفاً حداقل ۲ حرف از اسم رشته رو بنویس.")
        return SEARCH_MAJOR_Q

    course_types = context.user_data.get("search_course_types")
    city_filter = context.user_data.get("search_city_filter")

    matches = []
    for rec in MAJORS_DATA:
        if not major_matches_filters(rec, course_types, city_filter):
            continue
        major = normalize_fa(rec.get("major") or "")
        if query in major:
            matches.append(rec)

    if not matches:
        await update.message.reply_text(
            "چیزی با این اسم (و فیلترهای انتخابی‌ت) پیدا نشد. اسم رو کامل‌تر/متفاوت بنویس، "
            "یا برای تغییر فیلترها دوباره از /start شروع کن.",
            reply_markup=cancel_keyboard(),
        )
        return SEARCH_MAJOR_Q

    # مرتب‌سازی بر اساس رتبه‌ی مرزی منطقه‌ی ۳ (اگه نبود، منطقه‌ی ۱ یا ۲)
    def sort_key(rec):
        return rec.get("region3") or rec.get("region2") or rec.get("region1") or 10**9

    matches.sort(key=sort_key)

    lines = [f"🔎 {len(matches)} نتیجه برای «{text}» پیدا شد:\n"]
    for rec in matches[:SEARCH_MAX_RESULTS]:
        uni = rec.get("university") or ""
        city = rec.get("city") or ""
        place = f"{uni} - {city}" if city else uni
        course = rec.get("course_type") or ""
        rank_parts = []
        for i, rk in enumerate(["region1", "region2", "region3"], start=1):
            rank = rec.get(rk)
            if rank is None:
                continue
            taraz = rank_to_taraz(rk, rank)
            taraz_txt = f"≈تراز{taraz:.0f}" if taraz is not None else ""
            rank_parts.append(f"م{i}: رتبه {rank:,}".replace(",", "٬") + f" ({taraz_txt})")
        lines.append(f"• {rec.get('major')} — {place} [{course}]\n   " + " | ".join(rank_parts))

    remaining = len(matches) - SEARCH_MAX_RESULTS
    if remaining > 0:
        lines.append(f"\n…و {remaining} نتیجه‌ی دیگه (برای دقیق‌تر شدن نتایج، اسم کامل‌تری بنویس)")

    lines.append(
        "\n⚠️ این رتبه‌ها مال کارنامه‌های واقعی سال قبله و تراز کنارشون هم یه تخمینه، نه قطعی."
    )

    # چون ممکنه لیست طولانی بشه، در چند پیام جدا می‌فرستیم.
    full_text = "\n".join(lines)
    chunk = ""
    for line in full_text.split("\n"):
        if len(chunk) + len(line) + 1 > 3500:
            await update.message.reply_text(chunk, reply_markup=ReplyKeyboardRemove())
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await update.message.reply_text(chunk, reply_markup=ReplyKeyboardRemove())

    await update.message.reply_text(
        "می‌خوای رشته‌ی دیگه‌ای هم جستجو کنی؟ اسمشو بنویس، یا برای شروع دوباره /start بزن.",
        reply_markup=cancel_keyboard(),
    )
    return SEARCH_MAJOR_Q


# ---------------------------------------------------------------------------
# فیلترهای مشترک (نوع دوره + شهر/دانشگاه) — بین همه‌ی ابزارهای انتخاب رشته
# مشترکه: بعد از محاسبه‌ی کامل، ابزار رتبه، ابزار تراز، و جستجوی نام رشته.
# ---------------------------------------------------------------------------
async def handle_filter_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    if text not in COURSE_PRESETS:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های روی صفحه‌کلید رو انتخاب کن.",
            reply_markup=course_filter_keyboard(),
        )
        return FILTER_COURSE_Q

    context.user_data["chosen_course_types"] = COURSE_PRESETS[text]
    await update.message.reply_text(
        "می‌خوای فقط یه شهر/دانشگاه خاص رو ببینی؟ اسمشو بنویس (مثلاً «تهران»)، "
        "یا از دکمه‌ی پایین برای دیدن همه‌جا استفاده کن.",
        reply_markup=city_filter_keyboard(),
    )
    return FILTER_CITY_Q


async def handle_filter_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == MENU_CANCEL:
        return await cancel(update, context)

    normalized = normalize_fa(text).strip()
    if text == MENU_CITY_SKIP or normalized in {"نه", "خیر", "همه", "همه‌جا", "skip", "no"}:
        city_filter = None
    else:
        city_filter = text

    course_types = context.user_data.get("chosen_course_types")
    pending = context.user_data.get("pending_majors") or {}
    mode = pending.get("mode")

    if mode == "search-init":
        context.user_data["search_course_types"] = course_types
        context.user_data["search_city_filter"] = city_filter
        await update.message.reply_text(
            "اسم رشته رو بنویس (کامل یا بخشی ازش کافیه)، مثلاً «پرستاری» یا «مدیریت»:",
            reply_markup=cancel_keyboard(),
        )
        return SEARCH_MAJOR_Q

    if mode == "range":
        definite, likely, risky, unlikely = classify_majors_by_range(
            pending["region_key"], pending["best_rank"], pending["worst_rank"],
            course_types, city_filter,
        )
    elif mode == "exact":
        definite, likely, risky, unlikely = classify_majors_by_exact_rank(
            pending["region_key"], pending["rank"], course_types, city_filter,
        )
    else:
        await update.message.reply_text(
            "⚠️ مشکلی پیش اومد، دوباره از /start شروع کن.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    await send_majors_results(update, definite, likely, risky, unlikely, course_types, city_filter)
    await update.message.reply_text("برای شروع دوباره /start رو بزن.")
    return ConversationHandler.END




# ---------------------------------------------------------------------------
# پیام به پشتیبانی (فوروارد برای ادمین)
# ---------------------------------------------------------------------------
async def prompt_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "پیامتو بنویس یا عکس بفرست (می‌تونی کپشن هم بذاری)، مستقیم برای پشتیبانی ارسال می‌شه.",
        reply_markup=cancel_keyboard(),
    )
    return CONTACT_INPUT


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if message.text and message.text.strip() == MENU_CANCEL:
        return await cancel(update, context)

    if not ADMIN_ID:
        await message.reply_text(
            "⚠️ شناسه‌ی ادمین هنوز تنظیم نشده، پیامت الان قابل ارسال نیست.",
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU

    if not message.text and not message.photo:
        await message.reply_text("فقط می‌تونی متن یا عکس بفرستی. دوباره امتحان کن.")
        return CONTACT_INPUT

    user = update.effective_user
    username = f"@{user.username}" if user.username else "(بدون یوزرنیم)"
    header = (
        f"✉️ پیام جدید از کاربر:\n"
        f"نام: {user.full_name}\n"
        f"یوزرنیم: {username}\n"
        f"شناسه: {user.id}"
    )

    try:
        if message.photo:
            caption = message.caption or ""
            full_caption = header + ("\n\nمتن پیام:\n" + caption if caption else "")
            # کپشن تلگرام حداکثر ۱۰۲۴ کاراکتره
            sent = await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=message.photo[-1].file_id,
                caption=full_caption[:1024],
            )
        else:
            sent = await context.bot.send_message(
                chat_id=ADMIN_ID, text=f"{header}\n\nمتن پیام:\n{message.text}"
            )
        # این نگاشت رو ذخیره می‌کنیم تا اگه ادمین روی همین پیام Reply بزنه،
        # بدونیم جوابش باید برای همین کاربر برگرده.
        mapping = load_contact_map()
        mapping[str(sent.message_id)] = user.id
        save_contact_map(mapping)
        await message.reply_text(
            "✅ پیامت برای پشتیبانی ارسال شد.", reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.warning("Could not forward message to admin: %s", e)
        await message.reply_text(
            "⚠️ مشکلی تو ارسال پیش اومد، بعداً دوباره امتحان کن.",
            reply_markup=main_menu_keyboard(),
        )
    return MAIN_MENU


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وقتی ادمین روی پیام فوروارد‌شده‌ی یه کاربر Reply می‌زنه، جوابش رو مستقیم
    برای همون کاربر می‌فرستیم. اگه پیام مربوط به این قابلیت نبود، کاری نمی‌کنیم
    و می‌ذاریم بقیه‌ی handler ها طبق روال عادی پیام رو پردازش کنن."""
    if not ADMIN_ID or not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    if not update.message or not update.message.reply_to_message:
        return

    mapping = load_contact_map()
    target_user_id = mapping.get(str(update.message.reply_to_message.message_id))
    if target_user_id is None:
        return  # این یه ریپلای معمولیه، نه جواب به کاربر — کاری نمی‌کنیم

    reply_text = update.message.text or ""
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"📩 پاسخ پشتیبانی:\n\n{reply_text}",
        )
        await update.message.reply_text("✅ جوابت برای کاربر ارسال شد.")
    except Exception as e:
        logger.warning("Could not deliver admin reply to user: %s", e)
        await update.message.reply_text(
            "⚠️ نشد جواب رو بفرستم (شاید کاربر ربات رو بلاک کرده)."
        )

    # این پیام کاملاً پردازش شد؛ نذار وارد فلوی عادی مکالمه هم بشه.
    raise ApplicationHandlerStop


# ---------------------------------------------------------------------------
# Broadcast — فقط برای ادمین
# ---------------------------------------------------------------------------
async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)
    await update.message.reply_text(f"شناسه‌ی عددی تو تلگرام: {update.effective_user.id}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        return  # کاربر عادی، سکوت می‌کنیم
    users = load_users()
    await update.message.reply_text(
        f"📊 تعداد کل کاربرهایی که تا الان /start زدن: {len(users):,}".replace(",", "٬")
    )


BROADCAST_CONTENT_Q = 9001  # حالت مکالمه‌ی جدا برای برادکست؛ عمداً عددی دوره


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END  # کاربر عادی، سکوت می‌کنیم

    # حالت قدیمی هنوز کار می‌کنه: /broadcast متن... همه‌چی رو یه‌جا می‌فرسته
    match = re.match(r"^/broadcast(?:@\S+)?\s+([\s\S]+)$", update.message.text or "", re.DOTALL)
    if match:
        await do_broadcast(update, context, text=match.group(1).strip())
        return ConversationHandler.END

    await update.message.reply_text(
        "چی می‌خوای برای همه‌ی کاربرا بفرستی؟ می‌تونی متن، عکس یا ویدیو "
        "(با یا بدون کپشن) بفرستی."
    )
    return BROADCAST_CONTENT_Q


async def broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    message = update.message
    if message.video:
        await do_broadcast(update, context, video_file_id=message.video.file_id, caption=message.caption)
    elif message.photo:
        await do_broadcast(update, context, photo_file_id=message.photo[-1].file_id, caption=message.caption)
    elif message.text:
        await do_broadcast(update, context, text=message.text)
    else:
        await update.message.reply_text("فقط متن، عکس یا ویدیو رو می‌تونم برادکست کنم.")
        return BROADCAST_CONTENT_Q
    return ConversationHandler.END


async def do_broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str = None,
    photo_file_id: str = None,
    video_file_id: str = None,
    caption: str = None,
):
    users = load_users()
    if not users:
        await update.message.reply_text("هنوز هیچ کاربری ثبت نشده.")
        return

    sent, failed = 0, 0
    for uid in users:
        try:
            if video_file_id:
                await context.bot.send_video(chat_id=uid, video=video_file_id, caption=caption)
            elif photo_file_id:
                await context.bot.send_photo(chat_id=uid, photo=photo_file_id, caption=caption)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ ارسال شد به {sent} کاربر" + (f" (ناموفق: {failed})" if failed else "")
    )


# ---------------------------------------------------------------------------
# لغو / خطا
# ---------------------------------------------------------------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "لغو شد. هر وقت خواستی دوباره /start رو بزن.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("برای شروع، دستور /start رو بفرست.")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "متغیر محیطی BOT_TOKEN تنظیم نشده. توکن ربات رو از BotFather بگیر و در "
            "Environment Variables تنظیم کن."
        )

    application = Application.builder().token(token).build()

    # این باید قبل از ConversationHandler چک بشه تا اگه ادمین داشت به یه پیام
    # کاربر Reply می‌زد، مستقیم پردازش بشه و وارد فلوی منو نشه.
    application.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY, handle_admin_reply), group=-1
    )

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_choice)],
            SAWABEGH_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sawabegh)],
            KONKUR_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_konkur)],
            CONTACT_INPUT: [
                MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_contact)
            ],
            REGION_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_region)],
            MAJORS_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_majors)],
            RANK_TOOL_REGION_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rank_tool_region)],
            RANK_TOOL_RANK_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rank_tool_rank)],
            TARAZ_TOOL_REGION_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_taraz_tool_region)],
            TARAZ_TOOL_TARAZ_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_taraz_tool_taraz)],
            SEARCH_MAJOR_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_major)],
            FILTER_COURSE_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_course)],
            FILTER_CITY_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_city)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_CONTENT_Q: [
                MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, broadcast_content)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv)
    application.add_handler(broadcast_conv)
    application.add_handler(CommandHandler("myid", myid_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info("Bot starting (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
