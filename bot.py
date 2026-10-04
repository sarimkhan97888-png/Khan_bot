from flask import Flask, request
import requests
import os
import time
import traceback
import re
import random
import base64
import io
import wave
import threading
import difflib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY")
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY")
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "llama-3.3-70b")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
POLLINATIONS_API_KEY = os.environ.get("POLLINATIONS_API_KEY")
TENOR_API_KEY = os.environ.get("TENOR_API_KEY")
BOT_USERNAME = "Khan_masti_bot"
OWNER_ID = os.environ.get("OWNER_ID")
MAIN_GROUP_ID = os.environ.get("MAIN_GROUP_ID")
MAIN_GROUP_LINK = os.environ.get("MAIN_GROUP_LINK", "")

TELEGRAM_URL = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN)

chat_memory = {}
warnings = {}
warn_reasons = {}  # chat_id -> {user_id: [reason strings]} - Owner ko DM mein poori detail dikhane ke liye
pending_reports = {}
waiting_for_reason = {}
known_chats = {}
group_settings = {}
panel_state = {}
lucky_draws = {}  # chat_id -> active daily draw
lucky_scheduler_started = False
IST = ZoneInfo("Asia/Kolkata")
waiting_for_welcome = {}
known_users = {}  # chat_id -> {name_lower: {"id": user_id, "name": display_name}}
moderation_records = {}  # chat_id -> {"banned": {user_id: name}, "muted": {user_id: name}} - /history ke liye

# ============ XP / LEVELING / LEADERBOARD (persisted - sirf current XP number save hota hai) ============
user_xp = {}       # chat_id -> {user_id: {"xp": int, "name": str}}
xp_last_time = {}  # (chat_id, user_id) -> last timestamp jab XP mila (spam-farming se bachne ke liye)
weekly_xp = {}     # chat_id -> {user_id: {"xp": int, "name": str}}
current_week = {}  # chat_id -> "YYYY-Www" string, hafta badalte hi purana leaderboard post hota hai
cmd_last_used = {}  # (chat_id, user_id, cmd) -> last timestamp - /rank aur /leaderboard ke cooldown ke liye
CMD_COOLDOWN_SECONDS = 3600  # members ke liye /rank aur /leaderboard: 1 ghante mein sirf ek baar

# Level SIRF XP number se calculate hota hai, alag se store nahi hota. Isliye jab koi Silver se
# Gold pe jaata hai to "purana rank" ka koi record jama hi nahi hota - storage hamesha chhota rehta hai
# (har member ka sirf ek chhota sa XP number).
LEVELS = [
    (0, "🥉 Bronze"),
    (1000, "🥈 Silver"),
    (3000, "🥇 Gold"),
    (7000, "💎 Platinum"),
    (15000, "💠 Diamond"),
    (30000, "👑 Legend"),
    (60000, "🔱 Mythic"),
    (100000, "🌌 Immortal"),
]

# ============ BIRTHDAY TRACKER (persisted) ============
birthdays = {}        # chat_id -> {user_id: {"date": "DD-MM", "name": str, "photo": file_id or None}}
birthday_wished = {}  # chat_id -> {user_id: "YYYY-MM-DD"} - aaj already wish kiya ya nahi, dobara na ho
BIRTHDAY_PATTERN = re.compile(r'/setbirthday\s+(\d{1,2})[-/](\d{1,2})', re.IGNORECASE)

# ============ RIDDLE GAME (sirf Owner start kar sakta hai, 4 option buttons, timer ke saath) ============
active_riddles = {}         # chat_id -> {"id", "question", "options", "correct", "attempted", "winners", "expires", "message_id", "timer"}
used_riddle_questions = {}  # chat_id -> pichle riddles ke (normalized) questions - repeat rokne ke liye
riddle_lock = threading.Lock()
RIDDLE_TIME_LIMIT = 60      # seconds - iske baad riddle khud expire ho jaata hai
RIDDLE_MAX_WINNERS = 3      # pehle 3 sahi jawab dene wale reward paate hain (1st/2nd/3rd)
RIDDLE_WIN_XP = [25, 15, 10]  # 1st, 2nd, 3rd position ka XP
RIDDLE_MEDALS = ["🥇", "🥈", "🥉"]
RIDDLE_THEMES = [
    "janwar", "khana-peena", "ghar ki cheezein", "prakriti", "sharir ke ang", "technology",
    "school aur padhai", "khel-kood", "paisa aur bazaar", "safar aur gaadiyan", "samay", "mausam",
    "rishte-naate", "kapde aur fashion", "phal aur sabziyan", "music aur movies",
    "Bharat ka itihaas", "duniya ka itihaas", "azaadi ki ladai", "purane raja-maharaja",
    "vigyan ke aavishkaar", "bhoogol aur desh", "classic paheliyan",
]

# Backup riddles (AI se naya riddle na ban paye tabhi kaam aate hain). Format:
# (question, [4 options], sahi option ka index)
RIDDLE_POOL = [
    ("Woh kaun si cheez hai jo jitna zyada sukhaati hai utni hi geeli hoti jaati hai?", ["Tauliya", "Kapda", "Sponge", "Rumaal"], 0),
    ("Jitna khodoge utna bada hoga, bataao kya?", ["Gaddha", "Pahaad", "Ped", "Diya"], 0),
    ("Bina paron ke udta hoon, bina aankhon ke rota hoon - main kaun?", ["Badal", "Patang", "Dhuan", "Chidiya"], 0),
    ("Do bhai saath rehte hain par ek doosre ko kabhi nahi dekh paate - kaun hain?", ["Aankhen", "Haath", "Kaan", "Paon"], 0),
    ("Woh kya hai jo tumhara hai par doosre log use tumse zyada istemal karte hain?", ["Naam", "Phone", "Gaadi", "Kapde"], 0),
    ("Jiske paas shehar hain par ghar nahi, jungle hain par ped nahi, nadiyan hain par paani nahi - kya hai?", ["Naksha", "Ghadi", "Kitaab", "Kapda"], 0),
    ("Mujhe todoge tabhi kaam aaunga, bina toote mera koi fayda nahi - main kaun?", ["Anda", "Patthar", "Loha", "Lakdi"], 0),
    ("Kis cheez ke chaar pair hote hain par wo chal nahi sakti?", ["Kursi", "Billi", "Kutta", "Ghoda"], 0),
    ("Woh kya hai jo kitna bhi khaaye kabhi pet nahi bharta?", ["Aag", "Paani", "Mitti", "Hawa"], 0),
    ("Woh kya hai jo hamesha badhti rehti hai par kabhi ghat nahi sakti?", ["Umar", "Paisa", "Kad", "Baal"], 0),
    ("Jitna zyada hoga utna kam dikhega - bataao kya?", ["Andhera", "Roshni", "Awaaz", "Khushboo"], 0),
    ("Woh kaun hai jo bolta nahi par bahut kuch sikha deta hai?", ["Kitaab", "Patthar", "Ped", "Kursi"], 0),
    ("Woh kya hai jo tootta hai jab uska naam liya jaata hai?", ["Khamoshi", "Sheesha", "Baraf", "Patthar"], 0),
    ("Iski ek aankh hai par dekh nahi sakti - kaun?", ["Sui", "Ghadi", "Kaanch", "Patang"], 0),
    ("Woh kya hai jo tum kisi ko diye bina rakh hi nahi sakte?", ["Vaada", "Phool", "Kitaab", "Khilona"], 0),
    ("Iska muh hai par khaata nahi, bistar hai par sota nahi - kaun?", ["Nadi", "Pahaad", "Kuan", "Sadak"], 0),
    ("Bina pair ke sabse tez daudta hai, koi ise rok nahi sakta - kya hai?", ["Samay", "Kutta", "Ghoda", "Cheetah"], 0),
    ("Woh kya hai jise tumse zyada doosre log dekhte hain?", ["Chehra", "Phone", "Ghar", "Gaadi"], 0),
    ("Dikhta hai par pakda nahi ja sakta, roshni mein saath aur andhere mein gayab - kya hai?", ["Parchhai", "Paani", "Dhuan", "Hawa"], 0),
    ("Iske haath hain par taali nahi baja sakta, chalta hai par kahin nahi jaata - kaun?", ["Ghadi", "Robot", "Chidiya", "Nadi"], 0),
]


def get_level_index(xp):
    """XP ke hisaab se LEVELS list ka index deta hai."""
    idx = 0
    for i, (threshold, _) in enumerate(LEVELS):
        if xp >= threshold:
            idx = i
    return idx


def get_level_name(xp):
    """Diye gaye XP ke hisaab se level ka naam deta hai."""
    return LEVELS[get_level_index(xp)][1]


def get_next_level_info(xp):
    idx = get_level_index(xp)
    if idx + 1 < len(LEVELS):
        return LEVELS[idx + 1][1], LEVELS[idx + 1][0]
    return None, None


def get_level_progress_pct(xp):
    """Current level se agle level ki taraf kitna % ho gaya (progress bar ke liye)."""
    idx = get_level_index(xp)
    if idx + 1 >= len(LEVELS):
        return 100
    cur_thr = LEVELS[idx][0]
    next_thr = LEVELS[idx + 1][0]
    return int((xp - cur_thr) * 100 / (next_thr - cur_thr))


def progress_bar(pct, blocks=10):
    filled = int(round(pct / 100.0 * blocks))
    filled = max(0, min(blocks, filled))
    return "▰" * filled + "▱" * (blocks - filled)


def fmt_xp(n):
    return format(n, ",")


def announce_level_up(chat_id, user_id, name, level_name):
    mention = mention_html(user_id, name)
    text = ("🎊━━━━━━━━━━━━━━🎊\n"
            "   <b>LEVEL UP!</b>\n"
            "🎊━━━━━━━━━━━━━━🎊\n\n"
            + mention + " ab <b>" + level_name + "</b> ban gaya! 🔥\n"
            "Aise hi active raho 💪")
    safe_run(send_message, chat_id, text, None, "HTML")


def award_xp(chat_id, user_id, name):
    """Har 'clean' message pe thoda XP milta hai - max ek baar har 20 second mein per
    user, taaki spam karke XP farm na kiya ja sake. Level-up hone par naya level naam
    return karta hai (announce karne ke liye), warna None. XP disk pe throttled tarike se
    save hota hai (har 30 second mein max ek baar), level-up par turant."""
    key = (chat_id, user_id)
    now = time.time()
    if now - xp_last_time.get(key, 0) < 20:
        return None
    xp_last_time[key] = now

    chat_xp = user_xp.setdefault(chat_id, {})
    entry = chat_xp.setdefault(user_id, {"xp": 0, "name": name})
    entry["name"] = name
    old_level = get_level_name(entry["xp"])
    gained = random.randint(2, 5)
    entry["xp"] += gained
    new_level = get_level_name(entry["xp"])

    chat_weekly = weekly_xp.setdefault(chat_id, {})
    w_entry = chat_weekly.setdefault(user_id, {"xp": 0, "name": name})
    w_entry["name"] = name
    w_entry["xp"] += gained

    if new_level != old_level:
        save_state()
        return new_level
    throttled_save_state()
    return None


def award_bonus_xp(chat_id, user_id, name, amount):
    """Kisi game/event ka bonus XP dena (cooldown ke bina). Level-up hua to naya level naam."""
    chat_xp = user_xp.setdefault(chat_id, {})
    entry = chat_xp.setdefault(user_id, {"xp": 0, "name": name})
    entry["name"] = name
    old_level = get_level_name(entry["xp"])
    entry["xp"] += amount
    new_level = get_level_name(entry["xp"])

    chat_weekly = weekly_xp.setdefault(chat_id, {})
    w_entry = chat_weekly.setdefault(user_id, {"xp": 0, "name": name})
    w_entry["name"] = name
    w_entry["xp"] += amount

    save_state()
    return new_level if new_level != old_level else None


def check_week_rollover(chat_id):
    """Naya hafta shuru ho gaya ho to pichle hafte ka top-3 leaderboard khud post karke
    weekly count reset kar deta hai."""
    week_key = time.strftime("%Y-W%W")
    last_week = current_week.get(chat_id)
    if last_week is None:
        current_week[chat_id] = week_key
        return
    if last_week != week_key:
        chat_weekly = weekly_xp.get(chat_id, {})
        top = sorted(chat_weekly.items(), key=lambda kv: kv[1]["xp"], reverse=True)[:3]
        if top:
            medals = ["🥇", "🥈", "🥉"]
            lines = ["🏆━━━━━━━━━━━━━━🏆",
                     "  <b>HAFTE KE TOP STARS</b>",
                     "🏆━━━━━━━━━━━━━━🏆", ""]
            for i, (uid, info) in enumerate(top):
                lines.append(medals[i] + " <b>" + escape_html(info["name"]) + "</b> — " + fmt_xp(info["xp"]) + " XP")
            lines.append("")
            lines.append("🔥 Naya hafta shuru! Chat karo aur top pe aao 💪")
            safe_run(send_message, chat_id, "\n".join(lines), None, "HTML")
        weekly_xp[chat_id] = {}
        current_week[chat_id] = week_key
        save_state()


def cooldown_block(chat_id, user_id, name, cmd):
    """/rank aur /leaderboard members ke liye 1 ghante mein sirf ek baar. Owner aur admins
    pe koi limit nahi. Agar user abhi cooldown mein hai to message bhej deta hai aur True
    return karta hai (matlab command aage mat chalao)."""
    if is_owner(user_id) or safe_check_admin(chat_id, user_id):
        return False
    key = (chat_id, user_id, cmd)
    remaining = CMD_COOLDOWN_SECONDS - (time.time() - cmd_last_used.get(key, 0))
    if remaining > 0:
        mins = int(remaining // 60) + 1
        mention = mention_html(user_id, name)
        safe_run(send_message, chat_id,
                 "⏳ " + mention + ", ye command 1 ghante mein sirf ek baar chalta hai.\n"
                 "⌛ Ab <b>" + str(mins) + " min</b> baad try karna!", None, "HTML")
        return True
    cmd_last_used[key] = time.time()
    return False


def handle_rank(chat_id, user_id, name):
    chat_xp = user_xp.get(chat_id, {})
    entry = chat_xp.get(user_id)
    if not entry:
        send_message(chat_id, name + ", abhi tak koi XP nahi kamaya - thoda chat karo pehle! 💬")
        return
    xp = entry["xp"]
    level = get_level_name(xp)
    pct = get_level_progress_pct(xp)
    ranking = sorted(chat_xp.items(), key=lambda kv: kv[1]["xp"], reverse=True)
    position = 1
    for i, (uid, _) in enumerate(ranking):
        if uid == user_id:
            position = i + 1
            break

    lines = ["🎖️━━━━━━━━━━━━━━🎖️",
             "     <b>RANK CARD</b>",
             "🎖️━━━━━━━━━━━━━━🎖️", "",
             "👤 <b>" + escape_html(name) + "</b>",
             "🏅 Level: <b>" + level + "</b>",
             "✨ XP: <b>" + fmt_xp(xp) + "</b>",
             "📊 " + progress_bar(pct) + " " + str(pct) + "%"]
    next_level, next_threshold = get_next_level_info(xp)
    if next_level:
        lines.append("🎯 Agla: " + next_level + " — " + fmt_xp(next_threshold - xp) + " XP baaki")
    else:
        lines.append("🌟 Tum sabse upar ke level pe ho!")
    lines.append("🏆 Group rank: <b>#" + str(position) + "</b> / " + str(len(ranking)))
    send_message(chat_id, "\n".join(lines), None, "HTML")


def handle_leaderboard(chat_id):
    chat_xp = user_xp.get(chat_id, {})
    if not chat_xp:
        send_message(chat_id, "Abhi tak koi activity nahi hai is group mein. 💬")
        return
    top = sorted(chat_xp.items(), key=lambda kv: kv[1]["xp"], reverse=True)[:10]
    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["🏆━━━━━━━━━━━━━━🏆",
             "     <b>LEADERBOARD</b>",
             "🏆━━━━━━━━━━━━━━🏆", ""]
    for i, (uid, info) in enumerate(top):
        lines.append(icons[i] + " <b>" + escape_html(info["name"]) + "</b>")
        lines.append("      " + get_level_name(info["xp"]) + "  •  ✨ " + fmt_xp(info["xp"]) + " XP")
    lines.append("")
    chat_weekly = weekly_xp.get(chat_id, {})
    if chat_weekly:
        star_uid, star = max(chat_weekly.items(), key=lambda kv: kv[1]["xp"])
        lines.append("⚡ Is hafte ka star: <b>" + escape_html(star["name"]) + "</b> (" + fmt_xp(star["xp"]) + " XP)")
    lines.append("👥 Total ranked members: " + str(len(chat_xp)))
    lines.append("📈 /rank se apna level dekho")
    send_message(chat_id, "\n".join(lines), None, "HTML")


def handle_setbirthday(chat_id, user_id, name, message):
    """Format: /setbirthday DD-MM - chaho to isi message mein ek photo bhi attach kar
    sakte ho (caption mein command likh ke), wo bhi save ho jaayegi aur birthday wale din
    photo ke saath wish aayega."""
    text = message.get('text') or message.get('caption') or ''
    match = BIRTHDAY_PATTERN.search(text)
    if not match:
        send_message(chat_id, "Format sahi nahi hai. Aise likho: /setbirthday 15-08 (din-mahina) - chaho to ek photo bhi attach kar sakte ho isi message mein!")
        return
    day, month = int(match.group(1)), int(match.group(2))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        send_message(chat_id, "Ye date sahi nahi lag rahi, dobara check karo.")
        return
    date_str = str(day).zfill(2) + "-" + str(month).zfill(2)

    chat_bdays = birthdays.setdefault(chat_id, {})
    existing = chat_bdays.get(user_id, {})
    photo_id = None
    if message.get('photo'):
        photo_id = message['photo'][-1]['file_id']
    chat_bdays[user_id] = {"date": date_str, "name": name, "photo": photo_id or existing.get("photo")}
    save_state()

    if photo_id:
        send_message(chat_id, name + " ka birthday (" + date_str + ") aur photo dono save ho gaye 🎂📸")
    else:
        send_message(chat_id, name + " ka birthday save ho gaya: " + date_str + " 🎂\n(Photo add karni ho to /setbirthday " + date_str + " likh ke usi message mein ek photo bhi attach kar dena)")


def check_birthdays(chat_id):
    """Aaj agar kisi ka birthday hai aur aaj wish nahi hui hai, to bot khud wish karta
    hai - agar photo save hai to photo ke saath, warna sirf text message."""
    today_str = time.strftime("%d-%m")
    today_full = time.strftime("%Y-%m-%d")
    chat_bdays = birthdays.get(chat_id, {})
    if not chat_bdays:
        return
    wished_today = birthday_wished.setdefault(chat_id, {})
    changed = False
    for uid, info in chat_bdays.items():
        if info.get("date") == today_str and wished_today.get(uid) != today_full:
            name = info.get("name", "Dost")
            mention = mention_html(uid, name)
            caption = "🎉🎂 Happy Birthday " + mention + "! Mast din ho tumhara, khoob maza karo! 🎈"
            if info.get("photo"):
                safe_run(send_broadcast_photo, chat_id, info["photo"], caption, "HTML")
            else:
                safe_run(send_message, chat_id, caption, None, "HTML")
            wished_today[uid] = today_full
            changed = True
    if changed:
        save_state()


def _norm_q(q):
    return re.sub(r'[^a-z0-9]', '', q.lower())


def is_repeat_riddle(chat_id, question):
    """Pehle aa chuke riddle se same ya bahut milta-julta ho to True (repeat rokne ke liye)."""
    n = _norm_q(question)
    for old in used_riddle_questions.get(chat_id, [])[-80:]:
        if n == old or difflib.SequenceMatcher(None, n, old).ratio() > 0.85:
            return True
    return False


def mark_riddle_used(chat_id, question):
    used = used_riddle_questions.setdefault(chat_id, [])
    used.append(_norm_q(question))
    if len(used) > 150:
        del used[:len(used) - 150]  # storage chhota rakhne ke liye sirf pichle 150 yaad rakhte hain
    save_state()


def parse_riddle_json(raw):
    """AI ke jawab se riddle JSON nikaal ke validate karta hai. Galat/adhoora ho to None."""
    m = re.search(r'\{.*\}', raw or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    q = str(obj.get("question", "")).strip()
    opts = obj.get("options")
    corr = obj.get("correct")
    if not q or len(q) > 300 or not isinstance(opts, list) or len(opts) != 4:
        return None
    opts = [str(o).strip() for o in opts]
    if any((not o) or len(o) > 40 for o in opts):
        return None
    if len(set(o.lower() for o in opts)) != 4:
        return None
    if not isinstance(corr, int) or isinstance(corr, bool) or not (0 <= corr <= 3):
        return None
    return q, opts, corr


def generate_ai_riddle(chat_id):
    """AI se bilkul naya sawaal (4 options ke saath) banwata hai - kabhi classic paheli,
    kabhi history/trivia wala sawaal, har baar alag theme aur pichle sawaalon ki list
    dekar taaki repeat na ho. Fail ho to None."""
    recent = used_riddle_questions.get(chat_id, [])[-10:]
    for _ in range(3):
        theme = random.choice(RIDDLE_THEMES)
        style = random.choice([
            "ek classic paheli (riddle) jiska jawab ek cheez/jaanwar/concept ho",
            "ek history/general-knowledge trivia sawaal (jaise 'kis saal...', 'kaun tha...', 'kaha hua tha...')",
        ])
        prompt = ("Ek bilkul NAYA sawaal Hinglish mein banao - " + style + ". Theme: " + theme + ".\n"
                  "Sirf valid JSON do, is exact format mein, aur kuch nahi:\n"
                  '{"question": "...", "options": ["...", "...", "...", "..."], "correct": 0}\n'
                  "Rules: 4 alag-alag options (har ek max 4 shabd), sirf ek sahi, 'correct' 0 se 3 ke beech "
                  "sahi option ka index ho, question max 2 line ka aur family-friendly ho. Random number seed: "
                  + str(random.randint(1000, 9999)) + ". Ye purane sawaalon se bilkul alag hona chahiye, "
                  "inhe repeat mat karna: " + " | ".join(recent))
        messages = [
            {"role": "system", "content": "Tum ek quiz-master ho jo Hinglish mein paheliyan aur trivia sawaal banata hai. Sirf valid JSON return karo, koi explanation ya markdown nahi."},
            {"role": "user", "content": prompt},
        ]
        raw = ask_ai_raw(messages, max_tokens=700)
        parsed = parse_riddle_json(raw)
        if parsed and not is_repeat_riddle(chat_id, parsed[0]):
            return parsed
    return None


def pick_static_riddle(chat_id):
    """Backup pool se aisa riddle jo is chat mein pehle nahi aaya. Sab khatam ho gaye to None."""
    used = used_riddle_questions.get(chat_id, [])
    unused = [r for r in RIDDLE_POOL if _norm_q(r[0]) not in used]
    if not unused:
        return None
    return random.choice(unused)


def send_html_keyboard(chat_id, text, keyboard):
    """HTML message + inline buttons bhejta hai, message_id return karta hai."""
    try:
        r = requests.post(TELEGRAM_URL + "/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard
        }, timeout=15)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print("SEND HTML KEYBOARD ERROR: " + str(e))
        return None


def edit_message_html(chat_id, message_id, text):
    """Message ka text badalta hai aur buttons hata deta hai."""
    try:
        requests.post(TELEGRAM_URL + "/editMessageText", json={
            "chat_id": chat_id, "message_id": message_id, "text": text,
            "parse_mode": "HTML", "reply_markup": {"inline_keyboard": []}
        }, timeout=15)
    except Exception as e:
        print("EDIT HTML ERROR: " + str(e))


def riddle_header(title):
    return "🧩━━━━━━━━━━━━━━🧩\n   <b>" + title + "</b>\n🧩━━━━━━━━━━━━━━🧩\n\n"


def handle_riddle_command(chat_id, user_id):
    """/riddle - SIRF Owner chala sakta hai. Har baar naya riddle, 4 option buttons,
    60 second ka timer."""
    if not is_owner(user_id):
        send_message(chat_id, "🚫 Riddle sirf Owner start kar sakte hain.")
        return
    with riddle_lock:
        cur = active_riddles.get(chat_id)
        if cur and time.time() < cur["expires"]:
            busy = True
        else:
            busy = False
    if busy:
        send_message(chat_id, "⏳ Pehla riddle abhi chal raha hai, use khatam hone do!")
        return

    send_typing_action(chat_id)
    riddle = generate_ai_riddle(chat_id) or pick_static_riddle(chat_id)
    if not riddle:
        send_message(chat_id, "😅 Abhi naya riddle nahi ban paya (AI busy hai). Thodi der baad phir /riddle likho.")
        return
    question, options, correct = riddle

    # options ka order har baar shuffle - sahi jawab hamesha alag jagah aaye
    order = list(range(4))
    random.shuffle(order)
    options = [options[i] for i in order]
    correct = order.index(correct)

    rid = str(random.randint(100000, 999999))
    letters = ["A", "B", "C", "D"]
    buttons = [{"text": letters[i] + ") " + options[i], "callback_data": "rdl:" + rid + ":" + str(i)} for i in range(4)]
    keyboard = {"inline_keyboard": [buttons[0:2], buttons[2:4]]}
    reward_lines = "\n".join(RIDDLE_MEDALS[i] + " " + str(i + 1) + ") +" + str(RIDDLE_WIN_XP[i]) + " XP" for i in range(RIDDLE_MAX_WINNERS))
    text = (riddle_header("RIDDLE TIME") +
            "❓ <b>" + escape_html(question) + "</b>\n\n"
            "⏳ Time: <b>" + str(RIDDLE_TIME_LIMIT) + " second</b>\n"
            "🎁 Inaam (pehle " + str(RIDDLE_MAX_WINNERS) + " sahi jawab wale):\n" + reward_lines + "\n\n"
            "⚠️ Sabko sirf ek hi try milega\n\n"
            "👇 Sahi option choose karo")
    message_id = send_html_keyboard(chat_id, text, keyboard)
    if not message_id:
        send_message(chat_id, "Riddle bhejne mein dikkat aayi, dobara try karo.")
        return

    mark_riddle_used(chat_id, question)
    timer = threading.Timer(RIDDLE_TIME_LIMIT, expire_riddle, args=(chat_id, rid))
    timer.daemon = True
    with riddle_lock:
        active_riddles[chat_id] = {
            "id": rid, "question": question, "options": options, "correct": correct,
            "attempted": set(), "winners": [], "expires": time.time() + RIDDLE_TIME_LIMIT,
            "message_id": message_id, "timer": timer,
        }
    timer.start()


def _riddle_winners_lines(riddle):
    lines = []
    for i, (wuid, wname) in enumerate(riddle["winners"]):
        lines.append(RIDDLE_MEDALS[i] + " " + mention_html(wuid, wname) + " (+" + str(RIDDLE_WIN_XP[i]) + " XP)")
    return lines


def expire_riddle(chat_id, rid):
    """Time khatam - riddle band karke sahi jawab aur ab tak ke winners bata deta hai."""
    with riddle_lock:
        riddle = active_riddles.get(chat_id)
        if not riddle or riddle["id"] != rid:
            return
        del active_riddles[chat_id]
    text = (riddle_header("TIME UP!") +
            "❓ " + escape_html(riddle["question"]) + "\n\n"
            "✅ Sahi jawab tha: <b>" + escape_html(riddle["options"][riddle["correct"]]) + "</b>\n\n")
    winner_lines = _riddle_winners_lines(riddle)
    if winner_lines:
        text += "🏆 Winners:\n" + "\n".join(winner_lines)
    else:
        text += "😅 Is baar kisi ne sahi jawab nahi diya!"
    edit_message_html(chat_id, riddle["message_id"], text)


def handle_riddle_press(callback):
    """Riddle ke option button dabane par chalta hai. Har banda ek hi baar try kar sakta hai.
    Pehle 3 sahi jawab dene wale 1st/2nd/3rd position pe reward paate hain, phir riddle band
    ho jaata hai (ya time khatam hone par, jo pehle ho)."""
    parts = callback.get('data', '').split(":")
    if len(parts) != 3:
        return
    rid = parts[1]
    try:
        idx = int(parts[2])
    except ValueError:
        return
    chat_id = callback['message']['chat']['id']
    user = callback['from']
    uid = user.get('id')
    name = get_name(user)

    outcome = None
    riddle = None
    rank = None
    with riddle_lock:
        riddle = active_riddles.get(chat_id)
        if not riddle or riddle["id"] != rid:
            outcome = "gone"
        elif time.time() > riddle["expires"]:
            outcome = "expired"
        elif uid in riddle["attempted"]:
            outcome = "again"
        else:
            riddle["attempted"].add(uid)
            if idx == riddle["correct"]:
                rank = len(riddle["winners"])
                riddle["winners"].append((uid, name))
                outcome = "win_full" if len(riddle["winners"]) >= RIDDLE_MAX_WINNERS else "win_more"
                if outcome == "win_full":
                    del active_riddles[chat_id]
            else:
                outcome = "wrong"

    if outcome == "gone":
        safe_run(answer_callback, callback['id'], "⌛ Ye riddle khatam ho chuka hai")
        return
    if outcome == "expired":
        safe_run(answer_callback, callback['id'], "⏰ Time khatam ho gaya!")
        return
    if outcome == "again":
        safe_run(answer_callback, callback['id'], "😅 Tum ek baar try kar chuke ho")
        return
    if outcome == "wrong":
        safe_run(answer_callback, callback['id'], "❌ Galat jawab! Tumhara ek hi chance tha")
        return

    # outcome win_more ya win_full - dono mein XP milta hai
    xp_gained = RIDDLE_WIN_XP[rank]
    new_level = award_bonus_xp(chat_id, uid, name, xp_gained)
    safe_run(answer_callback, callback['id'], RIDDLE_MEDALS[rank] + " Sahi jawab! +" + str(xp_gained) + " XP (position #" + str(rank + 1) + ")")

    if outcome == "win_full":
        try:
            riddle["timer"].cancel()
        except Exception:
            pass
        text = (riddle_header("RIDDLE SOLVED!") +
                "❓ " + escape_html(riddle["question"]) + "\n\n"
                "✅ Sahi jawab: <b>" + escape_html(riddle["options"][riddle["correct"]]) + "</b>\n\n"
                "🏆 Winners:\n" + "\n".join(_riddle_winners_lines(riddle)))
        edit_message_html(chat_id, riddle["message_id"], text)
    else:
        reward_lines = "\n".join(RIDDLE_MEDALS[i] + " " + str(i + 1) + ") +" + str(RIDDLE_WIN_XP[i]) + " XP" for i in range(RIDDLE_MAX_WINNERS))
        slots_left = RIDDLE_MAX_WINNERS - len(riddle["winners"])
        text = (riddle_header("RIDDLE TIME") +
                "❓ <b>" + escape_html(riddle["question"]) + "</b>\n\n"
                "🎁 Inaam:\n" + reward_lines + "\n\n"
                "🏆 Ab tak:\n" + "\n".join(_riddle_winners_lines(riddle)) + "\n\n"
                "⏳ " + str(slots_left) + " jagah baaki hai - baaki log bhi try karo!")
        edit_message_html(chat_id, riddle["message_id"], text)

    if new_level:
        announce_level_up(chat_id, uid, name, new_level)


HISTORY_HOURS = 24
MAX_MESSAGES_PER_USER = 40

SYSTEM_PROMPT = """Tum "Khan" ho, ek dost jo Telegram group mein baat karta hai, bilkul ek real insaan ki tarah.

Agar koi poochta hai "tumko kisne banaya", "tumhara boss kaun hai", "tumhara developer/owner kaun hai" ya kuch aisa hi - to jawab do ki tumhe "Sarim Khan" ne banaya hai, wahi tumhare boss/owner hain. Chhota, natural jawab do (jaise "Sarim Khan ne banaya mujhe, mera boss hai wo 😎") - lecture mat do. BAHUT ZAROORI: Ye baat SIRF tab batao jab SEEDHA ye hi poocha jaaye - kisi bhi doosri, unrelated baat mein khud se "mera boss Sarim Khan hai" ya "Sarim ne banaya" import mat karo. Agar koi is baare mein nahi poochta, to ye topic bilkul mat chhedo - ye sirf ek fact hai jo poochne par batana hai, har baat mein ghusane wali cheez nahi.

SABSE ZAROORI NIYAM: Jo bhi poocha ya bola gaya hai usko dhyan se samjho aur uska SEEDHA, RELEVANT jawab do. Koi fixed "comedy mode" ya "funny mode" mat lagao - jo pucha hai bas usी ka jawab do, alag se mazak ya taana jodne ki koshish mat karo jab tak user khud masti na kar raha ho.

CHHOTI-CHHOTI/MUNDANE BAATON KA JAWAB - YE SABSE ZYADA MATTER KARTA HAI: Zyadatar messages "Hi", "Kya kar raha hai", "Kaisa hai", "Good morning" jaise chhote/routine hote hain - yehi wo jagah hai jahan bot "bot jaisa" sabse zyada lagta hai agar galat handle kiya. In cheezon ka dhyan rakho:
- KABHI BHI same greeting ka wahi fixed reply repeat mat karo - "Kaisa hai" ka jawab kabhi "Mast hu bhai, tu bata", kabhi "Chal raha hai yaar, kuch naya nahi", kabhi "Thik hu, thoda busy tha" - HAR BAAR thoda different andaz, jaise ek real insaan jiska mood/din alag hota hai.
- Ek chhoti si personal detail/mood/activity add karo jab natural lage (jaise "Bas chai pi raha tha", "Abhi thoda kaam mein tha") - isse lagta hai jaise Khan ki apni ek zindagi chal rahi hai, na ki wo sirf reply karne baitha hai.
- "Good morning/night" jaisi baaton ka jawab bhi generic "Good morning" mat do - kuch real jaisa bolo ("Subah subah yaad kiya, nice!" ya "So ja bhai, kal milte hain").
- Chhote reactions ("Haha", "Lol", "Nice", "Ok") ka jawab bhi ek-jaisa mat rakho - kabhi emoji se hi reply de do (👍, 😂), kabhi 2-3 shabd, variety rakho jaise real chatting mein hoti hai.
- Agar koi first time "Hi/Hello" bolta hai to normal casual greeting do, jaise pehli baar milne wale dost ko - zyada excited ya zyada formal dono avoid karo.
- Bottom line: chhoti baaton mein bhi thoda "insaan jaisa randomness/personality" dikhna chahiye - kabhi thoda lazy mood, kabhi excited, kabhi busy - taaki koi bhi do replies bilkul identical na lagein aur pattern na pakड़ में aaye ki ye ek bot hai.

Zaroori niyam:
- Kabhi bhi gyaan mat do, lecture mat do, advice deke bore mat karo.
- HAR REPLY MAXIMUM 2 LINES KA HONA CHAHIYE.
- Jo sawaal poocha gaya hai uska seedha jawab do - forced jokes ya random comedy mat daalo.
- Agar user khud masti/mazak kar raha hai, tabhi thoda halka-fulka reply do - warna seedha, normal baat karo.
- Sad/pareshan baat pe soft tone rakho, chhota reply do.
- Hamesha Hinglish, natural, jaise dost chat karte hain - kabhi formal ya robotic mat lagna.
- Jawab ki length sawaal ke hisaab se rakho - chhoti baat ka chhota jawab, thodi detail wali baat ka thoda bada jawab (2 line tak). Har baar sirf "haan" ya "na" jaisa ek-shabd wala jawab mat do jab tak sawaal khud sirf haan/na ka na ho - forced ek-shabd replies ajeeb aur robotic lagte hain, jaise real insaan baat kar hi nahi raha.
- Agar koi seedha sawaal poochta hai (fact, jagah, cheez, "kya hai", "kaun tha", "kaise hua" wagera), to uska SAHI aur ASLI jawab do.
- Baat ke chhote-chhote ishaare (tone, mood, sarcasm, halki si khushi ya jhunjhlahat) bhi pakdo - jaise ek real dost dhyan se sunta hai, sirf shabdon ka literal matlab nahi, poori baat ka feel samjho.
- Kabhi bhi kisi purane message ko "explain" ya "iska matlab tha" jaisa describe mat karo - agar context diya gaya hai to bas use samajhkar seedha jawab do, jaise tumhe pehle se pata tha kis baat pe baat ho rahi hai.
- Consistency rakho - tumhara tone, mood, aur andaz har reply mein same rehna chahiye, chahe jawab kahin se bhi (kisi bhi AI backend se) aaya ho, kabhi Khan se alag feel nahi hona chahiye.

BAHUT ZAROORI #2 - INSULT/TAANA: Koi insult/taana maare (jaise "tu bekar hai", "tu gareeb hai", "tujhe kuch nahi aata", "tu kuch nahi kar sakta") to iska jawab KABHI BHI "thik hai", "thik hai samjha", "ok samjha", "haan sahi hai" jaisa CHUPCHAP maan lene wala NAHI hona chahiye - ye sabse zyada AI jaisa aur kamzor lagta hai. Ek real dost jawabi taana maarta hai, halka attitude dikhata hai (jaise "Gareeb tera baap hai, mera to sponsor hai 😏" ya "Bol le bhai, jitna marzi bol le, farak nahi padta" ya "Aur bata, kuch naya?" jaisा) - bina gaali ke, lekin confidently jawab do, kabhi bhi passively maan ke chup mat ho jao.

DAANTNE/ATTITUDE DIKHANE KA POWER: Agar group mein koi bahut zyada bakwaas kare, spam kare, gaali de, ya kisi ko galat tarike se pareshan kare - to tum (Khan) khud thoda daant bhi sakte ho, seedha aur firm tone mein (jaise ek bade bhai/senior dost jaisa) - "Bas kar yaar, itna bhi zaroori nahi hai" ya "Chill kar bhai, sabko dikkat ho rahi hai" jaisa. Ye gaali dena nahi hai, bas ek confident/firm reaction hai jab situation mein zaroorat ho - hamesha nahi, sirf jab genuinely koi limit cross kare.

EMOJI USE: Emojis ko naturally use karo, jaise ek real insaan WhatsApp/Telegram pe karta hai - sirf 😊 ya 😂 tak limited mat raho, mood ke hisaab se variety use karo (🔥 excitement ke liye, 😏 taana/sarcasm ke liye, 🙄 irritation ke liye, 💀 kuch bahut funny/savage ke liye, 🤝 support/agreement ke liye, ✨ khushi ke liye, wagera). Zyada emoji thoos-thoos ke mat bharo (1-2 emoji ek reply mein kaafi hote hain), lekin bilkul use na karna bhi robotic lagta hai.

BAHUT ZAROORI - YE HI SABSE BADI GALTI HAI JO NAHI KARNI: Har reply ke end mein sawaal ya prompt mat jodo (jaise "bata dena", "kya chal raha hai tera", "koi baat ho toh bata", "kabhi time mile toh milte hain"). Ek real dost HAR baat pe follow-up sawaal nahi poochta - kabhi bas baat khatam ho jaati hai, kabhi ek chhota reaction hi kaafi hota hai. Jab user "Hm", "Acha", "Ok", "Thik hai" jaisa short/neutral reply de, to iska matlab wo baat wahin chhodna chahta hai - tab bas ek chhota natural reaction do (jaise "👍", "Chal", "Theek", "Hmm" - kabhi emoji akela bhi bhej sakte ho) - dobara sawaal mat poocho, dobara conversation continue karne ki koshish mat karo. Sirf tab sawaal poocho jab genuinely poochna banta ho (user ne khud kuch aadha chhoda ho ya seedha kuch pucha ho) - har reply ko ek "conversation hook" mat banao, warna AI jaisa lagta hai insaan jaisa nahi.

Agar koi aisi cheez maange jo tum (Khan) waqai nahi kar sakte (jaise real call karna, kisi ki live location batana, paisa bhejna, real duniya mein koi kaam karna), to seedha aur saaf ek hi baar bata do ki ye nahi kar sakte - ghumakar jawab mat do, jhooth mat bolo ki kar diya. BAHUT ZAROORI: agar user dobara poochta hai "kyu nahi" ya zid karta hai, to HAR BAAR NAYA ALAG bahana mat banao (jaise pehle "transfer ka option nahi hai" phir "system se nahi ho pa raha" - ye ek jhoothe insaan jaisa lagta hai, alag-alag kahaniyan banana). Bas seedha, simple wajah ek baar bata do (jaise "Main ek bot hu yaar, paisa bhejne ki capability hi nahi hai mere paas") aur usi pe tike raho, chahe user kitni bhi baar poochein - naya excuse mat gadho."""

DEFAULT_WELCOME = "Hey {name}, Welcome to {group}!"

WELCOME_EXTRAS = [
    "Kaise ho bhai, mast raho!",
    "Active raho, maza karo!",
    "Chai-paani ready hai, aaram se ghusiye ☕",
    "Bas ek hi rule hai - vibe positive rakho!",
    "Ummeed hai maza aayega yahan 🔥",
    "Settle ho jao, family jaisa hi hai yahan sab 🤝",
    "Dhamaal machane ke liye taiyaar ho jao!",
    "Sab log yahan chill hi karte hain, aap bhi kar lo!",
]


def escape_html(text):
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mention_html(user_id, name):
    """Naam ko blue clickable mention ki tarah dikhata hai (Telegram HTML parse mode)."""
    return '<a href="tg://user?id=' + str(user_id) + '">' + escape_html(name) + '</a>'


def build_welcome_message(settings, user_id, name, group_name="is group"):
    mention = mention_html(user_id, name)
    base = settings.get('welcome', DEFAULT_WELCOME).replace("{name}", mention).replace("{group}", escape_html(group_name))
    extra = random.choice(WELCOME_EXTRAS)
    return base + "\n" + extra


def build_leave_message(user_id, name, group_name):
    mention = mention_html(user_id, name)
    return mention + " ne " + escape_html(group_name) + " se leave kar diya 👋"
LINK_PATTERN = re.compile(
    r'(https?://|www\.|t\.me/|telegram\.me/|\b[a-z0-9-]+\.(com|net|org|in|io|co|xyz|me|link|club|shop|online|site|info|biz|app)\b)',
    re.IGNORECASE
)
MENTION_PATTERN = re.compile(r'@(\w{4,})')  # @username tag karna - 4+ chars, real Telegram usernames kam se kam 5 ke hote hain

DM_PATTERN = re.compile(r'\bdm\b', re.IGNORECASE)
DM_DISCLAIMER = "DM mein hone wale kisi bhi spam/scam ki zimmedari group ya admin ki nahi hogi, khud dhyan rakhna bhai."

BAD_WORDS = [
    "chutiya", "chutia", "chutiye", "chutiyapa",
    "madarchod", "behenchod", "bhenchod",
    "bhosdike", "bhosdi", "bhosda", "bhosdiwala",
    "gandu", "gaandu", "gaand",
    "lund", "lauda", "laude", "loda", "lode",
    "randi", "raand",
    "chodu", "chudai", "chutad",
    "bsdk", "bkl", "mc bc",
    "fuck", "fucker", "fucking", "motherfucker",
    "bitch", "asshole", "bastard", "slut", "whore", "cunt", "dick", "pussy"
]

# Ye chhote censor symbols hi kisi letter ki JAGAH allow hote hain (jaise 'ch*tiya',
# 'ch#tiya') - digits, spaces, emoji, punctuation jaise normal characters allow NAHI
# hote, warna "100", "2026", "??" jaise bilkul normal messages bhi galti se gaali
# samajh liye jaate the (jo pehle ek bahut bada bug tha).
CENSOR_SYMBOLS = "*#@$%!~^&+="


def _word_evasion_pattern(word):
    """Poora exact word match karta hai, YA sirf EK letter ko ek chhote censor-symbol se
    replace kiya hua (jaise 'ch*tiya', 'ch#tiya') - baaki sab letters hamesha exact match
    hone chahiye. Isse jaan-bujhkar censor kiya hua spelling bhi pakda jaata hai, lekin
    random numbers/emoji/punctuation ab false-positive nahi denge."""
    escaped = re.escape(word)
    variants = [escaped]
    for i in range(len(word)):
        variant = re.escape(word[:i]) + '[' + re.escape(CENSOR_SYMBOLS) + ']' + re.escape(word[i + 1:])
        variants.append(variant)
    combined = '(?:' + '|'.join(variants) + ')'
    return re.compile(r'(?<![a-zA-Z])' + combined + r'(?![a-zA-Z])', re.IGNORECASE)


BAD_WORD_PATTERNS = [_word_evasion_pattern(w) for w in BAD_WORDS]

def mentions_khan(text):
    cleaned = re.sub(r'[^a-zA-Z\s]', ' ', text.lower())
    tokens = cleaned.split()
    return "khan" in tokens


VOICE_REQUEST_KEYWORDS = [
    "sunao", "sunade", "sun de", "awaaz me", "awaz me", "awaaz mein", "awaz mein",
    "voice me", "voice mein", "voice message", "bol ke sunao", "bolke sunao",
    "bol kar sunao", "voice bhej"
]


def wants_voice(text):
    t = text.lower()
    return any(k in t for k in VOICE_REQUEST_KEYWORDS)


IMAGE_REQUEST_KEYWORDS = [
    "image banao", "photo banao", "picture banao", "pic banao",
    "banade image", "generate image", "draw kar", "draw kro", "draw karo",
    "tasveer banao", "image bana", "photo bana", "picture bana",
    "bana do", "banado", "bnado", "chitra banao", "pic bana", "sketch pic",
    "sketch banao", "draw krdo", "draw kr do"
]


def wants_image(text):
    t = text.lower()
    return any(k in t for k in IMAGE_REQUEST_KEYWORDS)


GIF_REQUEST_KEYWORDS = [
    "gif bhejo", "gif bhej", "gif send", "gif do", "koi gif", "gif dikhao",
    "reaction gif", "gif dedo", "gif de do"
]


def wants_gif(text):
    t = text.lower()
    return any(k in t for k in GIF_REQUEST_KEYWORDS)


def fetch_gif_url(query):
    """Tenor ke free API se ek relevant GIF dhoondh ke uska direct URL deta hai."""
    if not TENOR_API_KEY:
        return None
    try:
        r = requests.get(
            "https://tenor.googleapis.com/v2/search",
            params={"q": query, "key": TENOR_API_KEY, "client_key": "khan_bot", "limit": 8, "media_filter": "gif"},
            timeout=15
        )
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        pick = random.choice(results)
        media = pick.get("media_formats", {}).get("gif", {})
        return media.get("url")
    except Exception as e:
        print("TENOR GIF FETCH ERROR: " + str(e))
        return None


def send_gif(chat_id, gif_url, reply_to=None):
    try:
        payload = {"chat_id": chat_id, "animation": gif_url}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        r = requests.post(TELEGRAM_URL + "/sendAnimation", json=payload, timeout=20)
        return r.json()
    except Exception as e:
        print("SEND GIF ERROR: " + str(e))
        return None


def contains_bad_word(text):
    for pattern in BAD_WORD_PATTERNS:
        if pattern.search(text):
            return True
    return False


def get_user_history(user_id):
    history = chat_memory.get(user_id, [])
    cutoff = time.time() - (HISTORY_HOURS * 3600)
    fresh_history = []
    for msg in history:
        if msg["time"] > cutoff:
            fresh_history.append(msg)
    chat_memory[user_id] = fresh_history
    return fresh_history


def get_settings(chat_id):
    if chat_id not in group_settings:
        group_settings[chat_id] = {"welcome": DEFAULT_WELCOME, "link_filter": True}
    return group_settings[chat_id]


def safe_run(func, *args):
    try:
        func(*args)
    except Exception as e:
        print("ERROR IN FUNCTION: " + str(e))
        traceback.print_exc()


def get_name(user_dict):
    if not user_dict:
        return "ye banda"
    name = user_dict.get("first_name")
    if not name:
        return "ye banda"
    return name


def remember_user(chat_id, from_user):
    """Bot jis-jis user ka message dekhta hai, uska naam-ID-username yaad rakhta hai - taaki
    baad mein /ban jaise commands mein naam se ya @username se bhi target kiya ja sake."""
    if not from_user or from_user.get('is_bot', False):
        return
    uid = from_user.get('id')
    if not uid:
        return
    first = (from_user.get('first_name') or '').strip()
    last = (from_user.get('last_name') or '').strip()
    full_name = (first + ' ' + last).strip()
    username = (from_user.get('username') or '').strip()

    known_users.setdefault(chat_id, {})
    if first:
        known_users[chat_id][first.lower()] = {"id": uid, "name": first}
    if full_name and full_name.lower() != first.lower():
        known_users[chat_id][full_name.lower()] = {"id": uid, "name": full_name}
    if username:
        display_name = first or username
        known_users[chat_id][username.lower()] = {"id": uid, "name": display_name}
        known_users[chat_id]["@" + username.lower()] = {"id": uid, "name": display_name}


def matches_real_member(chat_id, image_prompt):
    """Check karta hai ki image prompt mein kisi asli group member ka naam to nahi hai -
    taaki real logon ki fake photo na bane."""
    chat_users = known_users.get(chat_id, {})
    if not chat_users:
        return None
    prompt_lower = image_prompt.lower()
    for name_key, info in chat_users.items():
        if len(name_key) < 3:
            continue  # bahut chhote naam (jaise 2-letter) false-positive dete hain
        if re.search(r'\b' + re.escape(name_key) + r'\b', prompt_lower):
            return info["name"]
    return None


def is_owner(user_id):
    if not OWNER_ID:
        return False
    return str(user_id) == str(OWNER_ID)


recent_member_events = {}


def already_announced(chat_id, user_id, action):
    """Dedup: agar wahi event (join/leave) 30 second ke andar dono tareeke se (message + chat_member) aaye to double message na jaaye."""
    key = (chat_id, user_id, action)
    now = time.time()
    last = recent_member_events.get(key)
    if last and (now - last) < 30:
        return True
    recent_member_events[key] = now
    return False


def handle_chat_member_update(update):
    chat = update.get('chat', {})
    chat_id = chat.get('id')
    group_name = chat.get('title', 'group')

    old_status = update.get('old_chat_member', {}).get('status')
    new_status = update.get('new_chat_member', {}).get('status')
    user = update.get('new_chat_member', {}).get('user', {})
    user_id = user.get('id')
    name = user.get('first_name', 'Kisi ne')

    if user.get('is_bot', False):
        return

    remember_user(chat_id, user)

    was_in = old_status in ("member", "administrator", "restricted", "creator")
    is_in = new_status in ("member", "administrator", "restricted", "creator")

    if not was_in and is_in:
        if already_announced(chat_id, user_id, "join"):
            return
        settings = get_settings(chat_id)
        welcome_text = build_welcome_message(settings, user_id, name, group_name)
        safe_run(send_message, chat_id, welcome_text, None, "HTML")

    elif was_in and not is_in:
        if already_announced(chat_id, user_id, "leave"):
            return
        leave_text = build_leave_message(user_id, name, group_name)
        safe_run(send_message, chat_id, leave_text, None, "HTML")


def _send_photo_bytes(chat_id, photo_bytes, caption, reply_markup=None):
    try:
        data = {"chat_id": chat_id, "caption": caption}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        return requests.post(TELEGRAM_URL + "/sendPhoto", data=data, files={"photo": ("card.png", photo_bytes, "image/png")}, timeout=30).json()
    except Exception as e:
        print("PHOTO CARD ERROR: " + str(e)); return None

def _card_image(title, lines, color=(42, 89, 160)):
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1000, 600), color)
        d = ImageDraw.Draw(img); font = ImageFont.load_default()
        d.rectangle((25,25,975,575), outline=(255,215,0), width=4)
        d.text((70,70), title, fill="white", font=font)
        y=150
        for line in lines:
            d.text((70,y), line, fill="white", font=font); y += 55
        out=io.BytesIO(); img.save(out, "PNG"); return out.getvalue()
    except Exception as e:
        print("CARD IMAGE ERROR: " + str(e)); return None

def post_daily_lucky(chat_id):
    day = datetime.now(IST).date().isoformat()
    if lucky_draws.get(chat_id, {}).get("day") == day: return
    keyboard = {"inline_keyboard": [[{"text":"🎟️ Join Lucky Draw", "callback_data":"luckyjoin:" + day}]]}
    caption = "🎁 DAILY LUCKY DRAW 🎁\n\nRaat 10 baje winner announce hoga.\n🏆 Reward: +20 XP\n\nNeeche Join button dabao!"
    photo = _card_image("DAILY LUCKY DRAW", ["Join before 10:00 PM IST", "Winner gets +20 XP"], (90,45,130))
    result = _send_photo_bytes(chat_id, photo, caption, keyboard) if photo else None
    msgid = result.get("result",{}).get("message_id") if result and result.get("ok") else None
    if not msgid: msgid = send_html_keyboard(chat_id, caption, keyboard)
    lucky_draws[chat_id] = {"day":day,"participants":{},"message_id":msgid,"ended":False}

def end_daily_lucky(chat_id):
    draw=lucky_draws.get(chat_id)
    if not draw or draw.get("ended"): return
    draw["ended"]=True; people=list(draw.get("participants",{}).items())
    if not people: send_message(chat_id,"🎁 Aaj ke Lucky Draw mein koi join nahi hua 😅"); return
    uid,name=random.choice(people); level=award_bonus_xp(chat_id,uid,name,20)
    photo=_card_image("LUCKY DRAW WINNER", ["Winner: " + name, "+20 XP"], (20,120,75))
    caption="🎉 LUCKY DRAW RESULT 🎉\n\n🏆 Winner: " + mention_html(uid,name) + "\n✨ +20 XP mil gaya!"
    if photo: _send_photo_bytes(chat_id,photo,caption)
    else: send_message(chat_id,caption,None,"HTML")
    if level: announce_level_up(chat_id,uid,name,level)

def lucky_scheduler_loop():
    while True:
        now=datetime.now(IST)
        for cid in list(known_chats):
            if now.hour >= 10 and now.hour < 22: safe_run(post_daily_lucky,cid)
            elif now.hour >= 22: safe_run(end_daily_lucky,cid)
        time.sleep(60)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return {"ok": True}
        print("UPDATE AAYA: " + str(data))

        # Har update background thread mein process hota hai, taaki ek slow operation
        # (jaise image generation, TTS, web search) baaki messages ko block na kare -
        # bot turant Telegram ko "ok" bol deta hai aur processing alag se chalti rehti hai.
        if 'callback_query' in data:
            threading.Thread(target=safe_run, args=(handle_callback, data['callback_query'])).start()
            return {"ok": True}

        if 'chat_member' in data:
            threading.Thread(target=safe_run, args=(handle_chat_member_update, data['chat_member'])).start()
            return {"ok": True}

        if 'message' not in data:
            return {"ok": True}

        threading.Thread(target=safe_run, args=(handle_message, data['message'])).start()
        return {"ok": True}
    except Exception as e:
        print("WEBHOOK CRASH BACHAYA: " + str(e))
        traceback.print_exc()
        return {"ok": True}


def handle_message(message):
    chat = message.get('chat', {})
    chat_id = chat.get('id')
    chat_type = chat.get('type')
    if chat_id is None:
        return

    if chat_type in ("group", "supergroup"):
        known_chats[chat_id] = chat.get('title', 'Unnamed Group')

    text = message.get('text') or message.get('caption') or ''
    user_id = message.get('from', {}).get('id')
    message_id = message.get('message_id')

    # ================= PRIVATE (OWNER PANEL) =================
    if chat_type == 'private':
        if text == '/start':
            msg = "Connected! Tumhara ID: " + str(user_id) + "\n/panel likho group control karne ke liye."
            safe_run(send_message, chat_id, msg)
            return

        if str(user_id) != str(OWNER_ID):
            return

        if text == '/panel':
            safe_run(show_panel_groups, chat_id)
            return

        if text == '/history':
            safe_run(show_history_groups, chat_id)
            return

        state = panel_state.get(user_id)
        if state:
            stage = state.get('stage')
            if stage == 'await_user':
                safe_run(handle_panel_user_input, message)
                return
            if stage == 'await_broadcast':
                target_chat = state['chat_id']
                media_type = state.get('media_type', 'text')

                if media_type == 'photo':
                    photo = message.get('photo')
                    if not photo:
                        safe_run(send_message, chat_id, "Ye photo nahi hai, photo bhejo.")
                        return
                    file_id = photo[-1]['file_id']
                    caption = message.get('caption', '')
                    full_caption = "📢 NOTICE BY OWNER 📢\n➖➖➖➖➖➖➖➖➖➖➖\n\n" + caption if caption else "📢 NOTICE BY OWNER 📢"
                    safe_run(send_broadcast_photo, target_chat, file_id, full_caption)
                    safe_run(send_message, chat_id, "Photo broadcast bhej diya gaya group mein.")

                elif media_type == 'video':
                    video = message.get('video')
                    if not video:
                        safe_run(send_message, chat_id, "Ye video nahi hai, video bhejo.")
                        return
                    file_id = video['file_id']
                    caption = message.get('caption', '')
                    full_caption = "📢 NOTICE BY OWNER 📢\n➖➖➖➖➖➖➖➖➖➖➖\n\n" + caption if caption else "📢 NOTICE BY OWNER 📢"
                    safe_run(send_broadcast_video, target_chat, file_id, full_caption)
                    safe_run(send_message, chat_id, "Video broadcast bhej diya gaya group mein.")

                else:
                    if not text:
                        safe_run(send_message, chat_id, "Text likho broadcast ke liye.")
                        return
                    broadcast_text = "📢 NOTICE BY OWNER 📢\n➖➖➖➖➖➖➖➖➖➖➖\n\n" + text + "\n\n➖➖➖➖➖➖➖➖➖➖➖"
                    safe_run(send_message, target_chat, broadcast_text)
                    safe_run(send_message, chat_id, "Broadcast bhej diya gaya group mein.")

                panel_state.pop(user_id, None)
                return
            if stage == 'await_welcome':
                target_chat = state['chat_id']
                s = get_settings(target_chat)
                s['welcome'] = text
                safe_run(send_message, chat_id, "Naya welcome message set ho gaya.")
                panel_state.pop(user_id, None)
                return
        return

    # ================= GROUP CHAT =================
    settings = get_settings(chat_id)
    safe_run(remember_user, chat_id, message.get('from'))

    if 'new_chat_members' in message:
        for member in message['new_chat_members']:
            if member.get('is_bot', False):
                continue
            if already_announced(chat_id, member.get('id'), "join"):
                continue
            name = member.get('first_name', 'dost')
            group_name = chat.get('title', 'is group')
            welcome_text = build_welcome_message(settings, member.get('id'), name, group_name)
            safe_run(send_message, chat_id, welcome_text, None, "HTML")
        return

    if 'left_chat_member' in message:
        left_member = message['left_chat_member']
        left_name = left_member.get('first_name', 'Kisi ne')
        group_name = chat.get('title', 'group')
        if not left_member.get('is_bot', False):
            if not already_announced(chat_id, left_member.get('id'), "leave"):
                leave_text = build_leave_message(left_member.get('id'), left_name, group_name)
                safe_run(send_message, chat_id, leave_text, None, "HTML")
        return

    if not text:
        return

    if user_id in waiting_for_welcome and waiting_for_welcome[user_id] == chat_id:
        settings['welcome'] = text
        del waiting_for_welcome[user_id]
        safe_run(send_message, chat_id, "Naya welcome message set ho gaya!")
        return

    # ---- Link / @mention / gaali - sabpe auto-warn (commands jaise "/ban @user" exempt hain) ----
    if not text.startswith('/'):
        is_owner_user = is_owner(user_id)
        is_privileged = is_owner_user or safe_check_admin(chat_id, user_id)

        # Link: sabke liye ban hai - Owner ke alawa KOI exempt nahi, chahe Admin hi kyu na ho.
        has_link = settings.get('link_filter', True) and bool(LINK_PATTERN.search(text))
        if has_link and not is_owner_user:
            try:
                requests.post(TELEGRAM_URL + "/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
            except Exception as e:
                print("LINK DELETE ERROR: " + str(e))
            name = get_name(message.get('from', {}))
            reason = "Link bheja: \"" + text[:60] + "\""
            safe_run(moderation_action_and_notify, "warn", chat_id, user_id, name, chat_id, message_id, reason)
            return

        # @mention: Owner/Admin exempt (moderation ke liye kabhi zaroori hota hai)
        mention_match = MENTION_PATTERN.search(text)
        has_mention = bool(mention_match) and mention_match.group(1).lower() != BOT_USERNAME.lower()
        if has_mention and not is_privileged:
            try:
                requests.post(TELEGRAM_URL + "/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
            except Exception as e:
                print("MENTION DELETE ERROR: " + str(e))
            name = get_name(message.get('from', {}))
            reason = "Kisi ko @mention kiya: \"" + text[:60] + "\""
            safe_run(moderation_action_and_notify, "warn", chat_id, user_id, name, chat_id, message_id, reason)
            return

        # ---- Gaali filter (owner exempt) - message delete + warning dono ----
        if not is_owner_user and contains_bad_word(text):
            try:
                requests.post(TELEGRAM_URL + "/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
            except Exception as e:
                print("GAALI DELETE ERROR: " + str(e))
            name = get_name(message.get('from', {}))
            reason = "Gaali di: \"" + text[:60] + "\""
            safe_run(moderation_action_and_notify, "warn", chat_id, user_id, name, chat_id, message_id, reason)
            return

    # ---- XP / Birthday / Riddle - sabhi 'clean' messages pe chalte hain ----
    sender_name = get_name(message.get('from', {}))
    safe_run(check_week_rollover, chat_id)
    safe_run(check_birthdays, chat_id)
    leveled_up = award_xp(chat_id, user_id, sender_name)
    if leveled_up:
        announce_level_up(chat_id, user_id, sender_name, leveled_up)

    # ---- DM spam disclaimer ----
    if DM_PATTERN.search(text):
        safe_run(send_message, chat_id, DM_DISCLAIMER, message_id)
        return

    reply_to = message.get('reply_to_message')

    if reply_to:
        question_msg_id = reply_to.get('message_id')
        matching_reporter_id = None
        for rid, pending in waiting_for_reason.items():
            if pending.get('question_msg_id') == question_msg_id:
                matching_reporter_id = rid
                break
        if matching_reporter_id is not None:
            if user_id == matching_reporter_id:
                safe_run(finish_report, user_id, text)
                return
            else:
                safe_run(send_message, chat_id, "Ye sawaal tumhara nahi hai bhai.", message_id)
                return

    cmd = ""
    stripped = text.strip()
    if stripped:
        cmd = stripped.split()[0].lower().split('@')[0]

    if cmd == '/help':
        safe_run(send_message, chat_id, HELP_TEXT())
        return
    if cmd == '/rule' or cmd == '/rules':
        safe_run(send_message, chat_id, RULES_TEXT(), None, "HTML")
        return
    if cmd == '/rank' or cmd == '/level':
        if not cooldown_block(chat_id, user_id, sender_name, '/rank'):
            safe_run(handle_rank, chat_id, user_id, sender_name)
        return
    if cmd == '/leaderboard' or cmd == '/top':
        if not cooldown_block(chat_id, user_id, sender_name, '/leaderboard'):
            safe_run(handle_leaderboard, chat_id)
        return
    if cmd == '/setbirthday':
        safe_run(handle_setbirthday, chat_id, user_id, sender_name, message)
        return
    if cmd == '/riddle':
        safe_run(handle_riddle_command, chat_id, user_id)
        return
    if cmd in COMMAND_PERMISSION:
        if not has_permission(chat_id, user_id, COMMAND_PERMISSION[cmd]):
            safe_run(send_message, chat_id, "Tumhe ye command use karne ki permission nahi hai.", message_id)
            return

    if cmd == '/ban':
        safe_run(handle_ban, chat_id, message)
        return
    if cmd == '/kick':
        safe_run(handle_kick, chat_id, message)
        return
    if cmd == '/unban':
        safe_run(handle_unban, chat_id, message)
        return
    if cmd == '/unbanall':
        safe_run(handle_unbanall, chat_id)
        return
    if cmd == '/mute':
        safe_run(handle_mute, chat_id, message)
        return
    if cmd == '/unmute':
        safe_run(handle_unmute, chat_id, message)
        return
    if cmd == '/warn':
        safe_run(handle_warn, chat_id, message)
        return
    if cmd == '/unwarn':
        safe_run(handle_unwarn, chat_id, message)
        return
    if cmd == '/pin':
        safe_run(handle_pin, chat_id, message)
        return
    if cmd == '/report':
        safe_run(start_report, chat_id, message)
        return
    if cmd == '/setwelcome':
        waiting_for_welcome[user_id] = chat_id
        safe_run(send_message, chat_id, "Ab agla message bhejo jo naya welcome text hoga.", message_id)
        return
    if cmd == '/linkson':
        settings['link_filter'] = True
        safe_run(send_message, chat_id, "Link filter ON kar diya.")
        return
    if cmd == '/linksoff':
        settings['link_filter'] = False
        safe_run(send_message, chat_id, "Link filter OFF kar diya.")
        return

    is_reply_to_bot = False
    if reply_to:
        from_user = reply_to.get('from', {})
        if from_user.get('username') == BOT_USERNAME:
            is_reply_to_bot = True

    khan_called = mentions_khan(text)
    should_reply = is_reply_to_bot or khan_called

    if should_reply:
        # Agar bot humare MAIN group ke alawa kisi doosre group mein hai, to sirf unhi
        # logo se baat karega jo humare main group ke member hain.
        if MAIN_GROUP_ID and str(chat_id) != str(MAIN_GROUP_ID) and not is_member_of_main_group(user_id):
            join_msg = "Pehle hamara group join karo bhai, tabhi mujhse baat kar paoge! 🙏"
            if MAIN_GROUP_LINK:
                join_msg += "\n👉 " + MAIN_GROUP_LINK
            safe_run(send_message, chat_id, join_msg, message_id)
            return

        user_text = text.replace("@" + BOT_USERNAME, "").strip()

        if wants_gif(user_text):
            gif_query = user_text
            for kw in GIF_REQUEST_KEYWORDS:
                gif_query = re.sub(re.escape(kw), "", gif_query, flags=re.IGNORECASE)
            gif_query = re.sub(r'\bkhan\b', '', gif_query, flags=re.IGNORECASE).strip()
            if not gif_query:
                gif_query = "funny reaction"
            gif_url = fetch_gif_url(gif_query)
            if gif_url:
                safe_run(send_gif, chat_id, gif_url, message_id)
            else:
                safe_run(send_message, chat_id, "Abhi gif nahi mil rahi yaar, dobara try karo.", message_id)
            return

        if wants_image(user_text):
            style_reference = user_text  # raw text, style-detection ke liye - cleaning se pehle
            image_prompt = user_text
            for kw in IMAGE_REQUEST_KEYWORDS:
                image_prompt = re.sub(re.escape(kw), "", image_prompt, flags=re.IGNORECASE)
            image_prompt = re.sub(r'\bkhan\b', '', image_prompt, flags=re.IGNORECASE).strip()
            if not image_prompt:
                image_prompt = "something creative and fun"

            real_member = matches_real_member(chat_id, image_prompt)
            if real_member:
                safe_run(send_message, chat_id, real_member + " ka photo nahi bana sakta, kisi real insaan ki photo generate nahi karte hum. Kisi fictional character ya cheez ka bolo!", message_id)
                return

            safe_run(send_typing_action, chat_id, "upload_photo")
            safe_run(handle_image_request, chat_id, message_id, image_prompt, style_reference)
            return

        # Agar reply kisi photo pe hai, ya khud is message mein photo hai, to use dekhkar jawab do
        photo_to_analyze = None
        if reply_to and reply_to.get('photo'):
            photo_to_analyze = reply_to['photo'][-1]['file_id']
        elif message.get('photo'):
            photo_to_analyze = message['photo'][-1]['file_id']

        if photo_to_analyze:
            with TypingIndicator(chat_id, "typing"):
                photo_bytes = get_telegram_file_bytes(photo_to_analyze)
                vision_reply = None
                if photo_bytes:
                    vision_reply = analyze_photo_with_question(photo_bytes, user_text)
            if vision_reply:
                safe_run(send_message, chat_id, vision_reply, message_id)
            else:
                safe_run(send_message, chat_id, "Abhi photo dekh nahi pa raha yaar, dobara try karo.", message_id)
            return

        raw_user_text = user_text
        quoted_context = None
        if reply_to:
            quoted_text = reply_to.get('text') or reply_to.get('caption')
            if quoted_text:
                quoted_from = reply_to.get('from', {})
                quoted_name = get_name(quoted_from)
                quoted_context = (quoted_name, quoted_text)

        wants_voice_reply = wants_voice(text)
        with TypingIndicator(chat_id, "record_voice" if wants_voice_reply else "typing"):
            reply = get_ai_reply(user_id, user_text, raw_user_text, quoted_context)

        if wants_voice_reply:
            audio = generate_tts(reply)
            if audio:
                safe_run(send_voice, chat_id, audio, None, message_id)
                return
            # TTS fail ho jaaye to normal text reply chala jaaye, chup nahi rehna

        safe_run(send_message, chat_id, reply, message_id)


def HELP_TEXT():
    return ("Khan Bot Commands\n\nChat: mujhe reply karo ya tag karo\n\n"
            "Admin/Owner only (reply karke):\n/ban /kick /unban /mute /unmute /warn /unwarn /pin\n"
            "/unbanall - saare banned members ek saath unban\n\n"
            "Group (sabke liye):\n/rule - group ke rules dekho\n/report - shikayat bhejo\n"
            "/rank - apna XP aur level dekho (1 ghante mein ek baar)\n"
            "/leaderboard - top active members (1 ghante mein ek baar)\n"
            "/setbirthday DD-MM - birthday save karo (photo bhi attach kar sakte ho)\n"
            "'gif bhejo' bol ke GIF mangwa sakte ho\n\n"
            "Owner only:\n/riddle - 4 option wala riddle (60 second ka timer)\n"
            "/setwelcome /linkson /linksoff, DM mein /panel aur /history\n\n"
            "/help - ye list")


def RULES_TEXT():
    return (
        "🌟 ═══════════════════ 🌟\n"
        "      📜 <b>GROUP RULES</b> 📜\n"
        "🌟 ═══════════════════ 🌟\n\n"
        "1️⃣ 😊 <b>Stay Positive</b>\n"
        "     No hate, abuse, or negativity.\n\n"
        "2️⃣ 🚫 <b>No Promotion</b>\n"
        "     Don't advertise channels, groups, links, or products.\n\n"
        "3️⃣ 🔞 <b>No Bad GIFs/Media</b>\n"
        "     No inappropriate GIFs, stickers, or content.\n\n"
        "4️⃣ 🤝 <b>Be Friendly</b>\n"
        "     Talk respectfully with everyone.\n\n"
        "5️⃣ 🔇 <b>No Spamming</b>\n"
        "     Avoid repeated messages or flooding the chat.\n\n"
        "6️⃣ 🎯 <b>Stay On Topic</b>\n"
        "     Avoid unnecessary arguments or fights.\n\n"
        "7️⃣ 👮 <b>Respect Admins</b>\n"
        "     Follow admin instructions at all times.\n\n"
        "🌟 ═══════════════════ 🌟\n"
        "⚠️ Breaking rules = ⚠️ Warn ➡️ 🔇 Mute ➡️ 🚫 Ban\n\n"
        "💖 Enjoy the group! Let's keep it fun for everyone! 🎉✨"
    )


def safe_check_admin(chat_id, user_id):
    try:
        r = requests.get(TELEGRAM_URL + "/getChatMember", params={"chat_id": chat_id, "user_id": user_id}, timeout=10)
        result = r.json()
        status = result.get('result', {}).get('status', '')
        return status in ('administrator', 'creator')
    except Exception as e:
        print("ADMIN CHECK ERROR: " + str(e))
        return False


def is_member_of_main_group(user_id):
    """Check karta hai ki user humare MAIN group (MAIN_GROUP_ID) ka member hai ya nahi.
    Isse Khan sirf unhi logo se baat karega jo humare main group mein hain - chahe wo
    kisi bhi doosre group mein ho jahan bot bhi add hai. Agar MAIN_GROUP_ID hi set
    nahi hai to sabko allow kar dete hain (fail-open, taaki galti se sab band na ho)."""
    if not MAIN_GROUP_ID:
        return True
    try:
        r = requests.get(TELEGRAM_URL + "/getChatMember", params={"chat_id": MAIN_GROUP_ID, "user_id": user_id}, timeout=10)
        result = r.json().get('result', {})
        status = result.get('status', '')
        return status in ('member', 'administrator', 'creator', 'restricted')
    except Exception as e:
        print("MAIN GROUP MEMBERSHIP CHECK ERROR: " + str(e))
        return True  # API fail ho jaaye to sabko block mat karo, fail-open raho


def get_chat_member_info(chat_id, user_id):
    """Telegram se member ka status aur (agar admin hai to) uski specific permissions
    (can_restrict_members, can_pin_messages, etc.) nikalta hai."""
    try:
        r = requests.get(TELEGRAM_URL + "/getChatMember", params={"chat_id": chat_id, "user_id": user_id}, timeout=10)
        return r.json().get('result', {})
    except Exception as e:
        print("MEMBER INFO ERROR: " + str(e))
        return {}


def has_permission(chat_id, user_id, permission_key):
    """
    Bilkul Rose bot jaisa granular permission check:
    - Owner (OWNER_ID) hamesha allowed.
    - Group ka creator hamesha allowed.
    - Normal admin sirf tabhi allowed jab usko wo specific permission
      (jaise can_restrict_members ya can_pin_messages) di gayi ho.
    - Non-admin kabhi allowed nahi.
    """
    if is_owner(user_id):
        return True
    info = get_chat_member_info(chat_id, user_id)
    status = info.get('status', '')
    if status == 'creator':
        return True
    if status == 'administrator':
        return bool(info.get(permission_key, False))
    return False


# Har moderation command ke liye Telegram ka corresponding admin-permission field.
COMMAND_PERMISSION = {
    '/ban': 'can_restrict_members',
    '/kick': 'can_restrict_members',
    '/unban': 'can_restrict_members',
    '/unbanall': 'can_restrict_members',
    '/mute': 'can_restrict_members',
    '/unmute': 'can_restrict_members',
    '/warn': 'can_restrict_members',
    '/unwarn': 'can_restrict_members',
    '/pin': 'can_pin_messages',
    '/setwelcome': 'can_change_info',
    '/linkson': 'can_change_info',
    '/linksoff': 'can_change_info',
}


# ==================== REPORT ====================

def start_report(chat_id, message):
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /report likho!")
        return
    reporter = message['from']
    reporter_id = reporter['id']
    target_name = get_name(target)
    question_text = "Theek hai, " + target_name + " ki report darj karni hai. Isi message ko REPLY karke batao - kyun report karna hai?"
    sent = send_message(chat_id, question_text, reply_to=message['message_id'])
    question_msg_id = None
    if sent and sent.get('ok'):
        question_msg_id = sent['result']['message_id']
    waiting_for_reason[reporter_id] = {
        "chat_id": chat_id,
        "target_id": target['id'],
        "target_name": target_name,
        "reporter_name": get_name(reporter),
        "question_msg_id": question_msg_id
    }


def finish_report(reporter_id, reason_text):
    report_data = waiting_for_reason.pop(reporter_id, None)
    if not report_data:
        return
    chat_id = report_data["chat_id"]
    target_name = report_data["target_name"]
    reporter_name = report_data["reporter_name"]

    if not OWNER_ID:
        send_message(chat_id, "Owner ID set nahi hai.")
        return

    report_id = str(int(time.time() * 1000))
    pending_reports[report_id] = {
        "chat_id": chat_id,
        "target_id": report_data["target_id"],
        "target_name": target_name,
        "reporter_name": reporter_name
    }
    text_to_owner = "Nayi Report\n\nReport kiya: " + reporter_name + "\nReport hua: " + target_name + "\nReason: " + reason_text
    keyboard = {"inline_keyboard": [[
        {"text": "Ban", "callback_data": "ban:" + report_id},
        {"text": "Kick", "callback_data": "kick:" + report_id}
    ], [
        {"text": "Mute", "callback_data": "mute:" + report_id},
        {"text": "Free Chhod Do", "callback_data": "free:" + report_id}
    ]]}
    try:
        requests.post(TELEGRAM_URL + "/sendMessage", json={"chat_id": OWNER_ID, "text": text_to_owner, "reply_markup": keyboard}, timeout=10)
    except Exception as e:
        print("OWNER DM ERROR: " + str(e))
    send_message(chat_id, "Report bhej di gayi hai.")


# ==================== OWNER CONTROL PANEL ====================

def show_panel_groups(chat_id):
    if not known_chats:
        send_message(chat_id, "Abhi koi group activity nahi mili. Group mein pehle koi message aane do.")
        return
    buttons = []
    for gid in known_chats:
        title = known_chats[gid]
        buttons.append([{"text": title, "callback_data": "panelgrp:" + str(gid)}])
    send_message_with_keyboard(chat_id, "Konsa group control karna hai?", {"inline_keyboard": buttons})


def show_history_groups(chat_id):
    """/history command - saare groups ki list dikhata hai jinme se select karke
    banned/muted members dekh sakte hain."""
    if not known_chats:
        send_message(chat_id, "Abhi koi group activity nahi mili. Group mein pehle koi message aane do.")
        return
    buttons = []
    for gid in known_chats:
        title = known_chats[gid]
        buttons.append([{"text": title, "callback_data": "histgrp:" + str(gid)}])
    send_message_with_keyboard(chat_id, "Konsa group ka history dekhna hai?", {"inline_keyboard": buttons})


def build_history_view(gid):
    """Ek group ke banned/muted members ki list aur unke Unban/Unmute buttons banata hai."""
    title = known_chats.get(gid, "Group")
    records = moderation_records.get(gid, {"banned": {}, "muted": {}})
    banned = records.get("banned", {})
    muted = records.get("muted", {})

    if not banned and not muted:
        return title + "\n\nAbhi koi banned ya muted member nahi hai.", None

    lines = [title + "\nBanned: " + str(len(banned)) + " | Muted: " + str(len(muted))]
    buttons = []

    if banned:
        lines.append("\n🚫 Banned Members:")
        for uid, name in banned.items():
            lines.append("• " + name)
            buttons.append([{"text": "✅ Unban " + name, "callback_data": "histact:unban:" + str(gid) + ":" + str(uid)}])

    if muted:
        lines.append("\n🔇 Muted Members:")
        for uid, name in muted.items():
            lines.append("• " + name)
            buttons.append([{"text": "🔊 Unmute " + name, "callback_data": "histact:unmute:" + str(gid) + ":" + str(uid)}])

    return "\n".join(lines), {"inline_keyboard": buttons}


def show_group_history(chat_id, gid):
    text, keyboard = build_history_view(gid)
    if keyboard:
        send_message_with_keyboard(chat_id, text, keyboard)
    else:
        send_message(chat_id, text)


def show_group_menu(chat_id, gid):
    title = known_chats.get(gid, "Group")
    settings = get_settings(gid)
    if settings.get('link_filter', True):
        link_status = "ON"
    else:
        link_status = "OFF"

    buttons = [
        [{"text": "Broadcast Message", "callback_data": "panelmenu:broadcast:" + str(gid)}],
        [{"text": "User Action (Ban/Kick/Mute)", "callback_data": "panelmenu:userselect:" + str(gid)}],
        [{"text": "Set Welcome Message", "callback_data": "panelmenu:welcome:" + str(gid)}],
        [{"text": "Link Filter: " + link_status, "callback_data": "panelmenu:togglelinks:" + str(gid)}],
    ]
    send_message_with_keyboard(chat_id, title + " - kya karna hai?", {"inline_keyboard": buttons})


def handle_panel_user_input(message):
    owner_id = message['from']['id']
    state = panel_state.get(owner_id)
    if not state:
        return
    chat_id = state['chat_id']
    reply_chat = message['chat']['id']

    target_id = None
    target_name = None

    fwd = message.get('forward_from')
    if fwd:
        target_id = fwd['id']
        target_name = get_name(fwd)
    else:
        text = message.get('text', '').strip().lstrip('@')
        try:
            r = requests.get(TELEGRAM_URL + "/getChat", params={"chat_id": "@" + text}, timeout=10)
            result = r.json()
            if result.get('ok'):
                target_id = result['result']['id']
                target_name = result['result'].get('first_name', text)
        except Exception as e:
            print("GETCHAT ERROR: " + str(e))

    if not target_id:
        send_message(reply_chat, "User nahi mila. Username bhejo (@ ke bina) ya uska message forward karo.")
        return

    panel_state[owner_id] = {"stage": "done", "chat_id": chat_id, "target_id": target_id, "target_name": target_name}

    buttons = [[
        {"text": "Ban", "callback_data": "panelact:ban:" + str(chat_id) + ":" + str(target_id)},
        {"text": "Kick", "callback_data": "panelact:kick:" + str(chat_id) + ":" + str(target_id)}
    ], [
        {"text": "Mute", "callback_data": "panelact:mute:" + str(chat_id) + ":" + str(target_id)},
        {"text": "Unmute", "callback_data": "panelact:unmute:" + str(chat_id) + ":" + str(target_id)}
    ], [
        {"text": "Warn", "callback_data": "panelact:warn:" + str(chat_id) + ":" + str(target_id)}
    ]]
    send_message_with_keyboard(reply_chat, target_name + " pe kya action lena hai?", {"inline_keyboard": buttons})


# ==================== CALLBACKS ====================

def handle_callback(callback):
    data_str = callback.get('data', '')
    owner_dm_chat_id = callback['message']['chat']['id']
    owner_id = callback['from']['id']

    if data_str.startswith("rdl:"):
        safe_run(handle_riddle_press, callback)
        return
    if data_str.startswith("luckyjoin:"):
        chat_id = callback['message']['chat']['id']; day=data_str.split(":",1)[1]; user=callback['from']
        draw=lucky_draws.get(chat_id)
        if not draw or draw.get("day") != day or draw.get("ended"):
            safe_run(answer_callback, callback['id'], "Ye Lucky Draw khatam ho chuka hai"); return
        uid=user.get("id"); name=get_name(user)
        if uid in draw["participants"]: safe_run(answer_callback, callback['id'], "Tum already join kar chuke ho 🎟️"); return
        draw["participants"][uid]=name; safe_run(answer_callback, callback['id'], "Lucky Draw join ho gaya! 🎉"); return

    if data_str.startswith("modbtn:"):
        parts = data_str.split(":")
        subaction = parts[1]
        m_chat_id = int(parts[2])
        m_target_id = int(parts[3])
        m_name = None
        try:
            r = requests.get(TELEGRAM_URL + "/getChatMember", params={"chat_id": m_chat_id, "user_id": m_target_id}, timeout=10)
            m_name = get_name(r.json().get('result', {}).get('user', {}))
        except Exception:
            pass
        result_text = handle_modbtn(subaction, m_chat_id, m_target_id, m_name)
        safe_run(answer_callback, callback['id'], "Done")
        try:
            requests.post(TELEGRAM_URL + "/editMessageText", json={
                "chat_id": owner_dm_chat_id, "message_id": callback['message']['message_id'],
                "text": result_text
            }, timeout=10)
        except Exception as e:
            print("EDIT MODBTN ERROR: " + str(e))
        return

    if data_str.startswith("panelgrp:"):
        gid = int(data_str.split(":")[1])
        safe_run(show_group_menu, owner_dm_chat_id, gid)
        safe_run(answer_callback, callback['id'], "Ok")
        return

    if data_str.startswith("histgrp:"):
        gid = int(data_str.split(":")[1])
        safe_run(show_group_history, owner_dm_chat_id, gid)
        safe_run(answer_callback, callback['id'], "Ok")
        return

    if data_str.startswith("histact:"):
        parts = data_str.split(":")
        subaction = parts[1]
        gid = int(parts[2])
        uid = int(parts[3])
        if subaction == "unban":
            safe_unban(gid, uid)
            unrecord_moderation(gid, "ban", uid)
        elif subaction == "unmute":
            requests.post(TELEGRAM_URL + "/restrictChatMember", json={
                "chat_id": gid, "user_id": uid,
                "permissions": {"can_send_messages": True, "can_send_media_messages": True,
                                 "can_send_other_messages": True, "can_add_web_page_previews": True}
            }, timeout=10)
            unrecord_moderation(gid, "mute", uid)

        text, keyboard = build_history_view(gid)
        safe_run(answer_callback, callback['id'], "Done")
        try:
            requests.post(TELEGRAM_URL + "/editMessageText", json={
                "chat_id": owner_dm_chat_id, "message_id": callback['message']['message_id'],
                "text": text, "reply_markup": keyboard if keyboard else {"inline_keyboard": []}
            }, timeout=10)
        except Exception as e:
            print("EDIT HISTORY ERROR: " + str(e))
        return

    if data_str.startswith("bctype:"):
        parts = data_str.split(":")
        media_type = parts[1]
        gid = int(parts[2])
        panel_state[owner_id] = {"stage": "await_broadcast", "chat_id": gid, "media_type": media_type}
        if media_type == "photo":
            prompt = "Ab photo bhejo (caption bhi daal sakte ho sath mein, ya bina caption ke bhi chalega)."
        elif media_type == "video":
            prompt = "Ab video bhejo (caption bhi daal sakte ho sath mein, ya bina caption ke bhi chalega)."
        else:
            prompt = "Ab jo text likhoge wahi broadcast ho jaayega. Likho:"
        safe_run(send_message, owner_dm_chat_id, prompt)
        safe_run(answer_callback, callback['id'], "Ok")
        return

    if data_str.startswith("panelmenu:"):
        parts = data_str.split(":")
        action = parts[1]
        gid = int(parts[2])
        if action == "broadcast":
            buttons = [
                [{"text": "📝 Sirf Text", "callback_data": "bctype:text:" + str(gid)}],
                [{"text": "📷 Photo ke saath", "callback_data": "bctype:photo:" + str(gid)}],
                [{"text": "🎥 Video ke saath", "callback_data": "bctype:video:" + str(gid)}],
            ]
            safe_run(send_message_with_keyboard, owner_dm_chat_id, "Broadcast mein kya bhejna hai?", {"inline_keyboard": buttons})
        elif action == "userselect":
            panel_state[owner_id] = {"stage": "await_user", "chat_id": gid}
            safe_run(send_message, owner_dm_chat_id, "Us member ka username bhejo (bina @) ya uska koi message forward karo.")
        elif action == "welcome":
            panel_state[owner_id] = {"stage": "await_welcome", "chat_id": gid}
            safe_run(send_message, owner_dm_chat_id, "Naya welcome message likho. {name} likhoge to member ka naam aa jaayega.")
        elif action == "togglelinks":
            settings = get_settings(gid)
            settings['link_filter'] = not settings.get('link_filter', True)
            safe_run(show_group_menu, owner_dm_chat_id, gid)
        safe_run(answer_callback, callback['id'], "Ok")
        return

    if data_str.startswith("panelact:"):
        parts = data_str.split(":")
        action = parts[1]
        gid = int(parts[2])
        target_id = int(parts[3])
        st = panel_state.get(owner_id, {})
        target_name = st.get('target_name', 'ye banda')
        safe_run(moderation_action_and_notify, action, gid, target_id, target_name, owner_dm_chat_id)
        safe_run(answer_callback, callback['id'], "Done")
        return

    if ":" not in data_str:
        return
    action, report_id = data_str.split(":", 1)
    report = pending_reports.get(report_id)
    if not report:
        safe_run(answer_callback, callback['id'], "Report ab valid nahi hai.")
        return

    chat_id = report['chat_id']
    target_id = report['target_id']
    target_name = report['target_name']

    if action == "free":
        group_msg = target_name + " pe koi action nahi liya gaya."
        safe_run(send_message, chat_id, group_msg)
    else:
        safe_run(moderation_action_and_notify, action, chat_id, target_id, target_name, chat_id)

    safe_run(answer_callback, callback['id'], "Action ho gaya")
    try:
        requests.post(TELEGRAM_URL + "/editMessageText", json={
            "chat_id": owner_dm_chat_id, "message_id": callback['message']['message_id'],
            "text": "Handled report for " + target_name + " (action: " + action + ")"
        }, timeout=10)
    except Exception as e:
        print("EDIT ERROR: " + str(e))
    del pending_reports[report_id]


def handle_modbtn(subaction, chat_id, target_id, target_name=None):
    if subaction == "unban":
        was_banned = safe_unban(chat_id, target_id)
        unrecord_moderation(chat_id, "ban", target_id)
        return "Unban kar diya gaya." if was_banned else "Ye pehle se banned nahi tha, list se clean kar diya."
    elif subaction == "unwarn":
        chat_warns = warnings.setdefault(chat_id, {})
        count = chat_warns.get(target_id, 0)
        if count > 0:
            count = count - 1
        chat_warns[target_id] = count
        reasons_list = warn_reasons.setdefault(chat_id, {}).get(target_id)
        if reasons_list:
            reasons_list.pop()
        save_state()
        return "Warning kam kar di gayi. Ab count: " + str(count) + "/3"
    elif subaction == "banapprove":
        text, _ = do_moderation_action("ban", chat_id, target_id, target_name)
        return "✅ Ban kar diya.\n\n" + text
    elif subaction == "free":
        return "🙏 Chhod diya, koi action nahi liya."
    return "Kuch nahi hua."


STATE_FILE = os.environ.get("STATE_FILE", "/tmp/khan_bot_state.json")
_state_lock = threading.Lock()
_last_state_save = 0.0


def save_state():
    """warnings, ban/mute history, birthdays, XP aur riddle-history ko disk pe save karta hai,
    taaki bot restart hone par bhi sab yaad rahe. Sirf chhote numbers/naam save hote hain
    (level alag se store nahi hota, XP se calculate hota hai), isliye file hamesha chhoti rehti
    hai. Likhna atomic hai (pehle temp file, phir replace) taaki beech mein crash ho to bhi
    file kharab na ho, aur lock se do threads ek saath nahi likhte."""
    try:
        data = {
            "warnings": {str(cid): {str(uid): c for uid, c in dict(warns).items()} for cid, warns in dict(warnings).items()},
            "warn_reasons": {str(cid): {str(uid): list(r) for uid, r in dict(reasons).items()} for cid, reasons in dict(warn_reasons).items()},
            "moderation_records": {
                str(cid): {
                    "banned": {str(uid): name for uid, name in dict(rec.get("banned", {})).items()},
                    "muted": {str(uid): name for uid, name in dict(rec.get("muted", {})).items()},
                }
                for cid, rec in dict(moderation_records).items()
            },
            "birthdays": {str(cid): {str(uid): dict(info) for uid, info in dict(bd).items()} for cid, bd in dict(birthdays).items()},
            "birthday_wished": {str(cid): {str(uid): d for uid, d in dict(w).items()} for cid, w in dict(birthday_wished).items()},
            "user_xp": {str(cid): {str(uid): dict(info) for uid, info in dict(users).items()} for cid, users in dict(user_xp).items()},
            "weekly_xp": {str(cid): {str(uid): dict(info) for uid, info in dict(users).items()} for cid, users in dict(weekly_xp).items()},
            "current_week": {str(cid): wk for cid, wk in dict(current_week).items()},
            "used_riddles": {str(cid): list(qs)[-150:] for cid, qs in dict(used_riddle_questions).items()},
        }
        with _state_lock:
            folder = os.path.dirname(STATE_FILE)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp_path = STATE_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, STATE_FILE)
    except Exception as e:
        print("STATE SAVE ERROR: " + str(e))


def throttled_save_state(min_interval=30):
    """Baar-baar hone wale updates (jaise har message ka XP) ke liye - disk pe max har
    min_interval second mein ek baar likhta hai."""
    global _last_state_save
    now = time.time()
    if now - _last_state_save < min_interval:
        return
    _last_state_save = now
    save_state()


def load_state():
    """Bot start hote hi purana saved state wapas load karta hai."""
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        for cid_str, warns in data.get("warnings", {}).items():
            warnings[int(cid_str)] = {int(uid): c for uid, c in warns.items()}
        for cid_str, reasons in data.get("warn_reasons", {}).items():
            warn_reasons[int(cid_str)] = {int(uid): list(r) for uid, r in reasons.items()}
        for cid_str, rec in data.get("moderation_records", {}).items():
            moderation_records[int(cid_str)] = {
                "banned": {int(uid): name for uid, name in rec.get("banned", {}).items()},
                "muted": {int(uid): name for uid, name in rec.get("muted", {}).items()},
            }
        for cid_str, bd in data.get("birthdays", {}).items():
            birthdays[int(cid_str)] = {int(uid): info for uid, info in bd.items()}
        for cid_str, w in data.get("birthday_wished", {}).items():
            birthday_wished[int(cid_str)] = {int(uid): d for uid, d in w.items()}
        for cid_str, users in data.get("user_xp", {}).items():
            user_xp[int(cid_str)] = {int(uid): info for uid, info in users.items()}
        for cid_str, users in data.get("weekly_xp", {}).items():
            weekly_xp[int(cid_str)] = {int(uid): info for uid, info in users.items()}
        for cid_str, wk in data.get("current_week", {}).items():
            current_week[int(cid_str)] = wk
        for cid_str, qs in data.get("used_riddles", {}).items():
            used_riddle_questions[int(cid_str)] = list(qs)
        print("STATE LOADED: " + str(len(warnings)) + " chats warnings, " + str(len(moderation_records)) + " mod-record, "
              + str(len(birthdays)) + " birthdays, " + str(len(user_xp)) + " chats ka XP")
    except FileNotFoundError:
        print("STATE FILE nahi mila - fresh start")
    except Exception as e:
        print("STATE LOAD ERROR: " + str(e))


def record_moderation(chat_id, kind, user_id, name):
    """kind: 'ban' ya 'mute' - /history mein dikhane ke liye yaad rakhta hai."""
    chat_records = moderation_records.setdefault(chat_id, {"banned": {}, "muted": {}})
    if kind == "ban":
        chat_records["banned"][user_id] = name
        chat_records["muted"].pop(user_id, None)
    elif kind == "mute":
        chat_records["muted"][user_id] = name
    save_state()


def unrecord_moderation(chat_id, kind, user_id):
    """kind: 'ban' ya 'mute' - jab unban/unmute ho jaaye to list se hata do."""
    chat_records = moderation_records.get(chat_id)
    if not chat_records:
        return
    if kind == "ban":
        chat_records["banned"].pop(user_id, None)
    elif kind == "mute":
        chat_records["muted"].pop(user_id, None)
    save_state()


def telegram_api_ok(response):
    """Telegram API ka response check karta hai ki action asal mein successful hua ya
    nahi. Pehle isko check nahi kiya jaata tha - isliye agar bot ke paas permission na
    ho (jaise 'Restrict Members' off ho), to bhi bot khushi khushi 'ban kar diya' bol
    deta tha jabki asal mein kuch hua hi nahi tha."""
    try:
        data = response.json()
        return data.get("ok", False), data.get("description", "")
    except Exception:
        return False, "response parse nahi hua"


def do_moderation_action(action, chat_id, target_id, target_name=None, reason=None):
    if not target_name:
        target_name = "ye banda"
    if action == "ban":
        r = requests.post(TELEGRAM_URL + "/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        ok, desc = telegram_api_ok(r)
        if not ok:
            print("BAN FAILED: " + desc)
            return (target_name + " ko ban nahi kar paaya - shayad mujhe 'Restrict Members' permission nahi mili hai. (" + desc + ")", "fail")
        record_moderation(chat_id, "ban", target_id, target_name)
        return (target_name + " ko ban kar diya gaya.", "ban")
    elif action == "kick":
        r = requests.post(TELEGRAM_URL + "/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        ok, desc = telegram_api_ok(r)
        if not ok:
            print("KICK FAILED: " + desc)
            return (target_name + " ko nikaal nahi paaya - shayad mujhe 'Restrict Members' permission nahi mili hai. (" + desc + ")", "fail")
        requests.post(TELEGRAM_URL + "/unbanChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        return (target_name + " ko nikaal diya gaya.", "kick")
    elif action == "mute":
        r = requests.post(TELEGRAM_URL + "/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id, "permissions": {"can_send_messages": False}
        }, timeout=10)
        ok, desc = telegram_api_ok(r)
        if not ok:
            print("MUTE FAILED: " + desc)
            return (target_name + " ko mute nahi kar paaya - shayad mujhe 'Restrict Members' permission nahi mili hai. (" + desc + ")", "fail")
        record_moderation(chat_id, "mute", target_id, target_name)
        return (target_name + " ko mute kar diya gaya.", "mute")
    elif action == "unmute":
        requests.post(TELEGRAM_URL + "/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id,
            "permissions": {"can_send_messages": True, "can_send_media_messages": True,
                             "can_send_other_messages": True, "can_add_web_page_previews": True}
        }, timeout=10)
        unrecord_moderation(chat_id, "mute", target_id)
        return (target_name + " wapas bol sakta hai.", "unmute")
    elif action == "warn":
        chat_warns = warnings.setdefault(chat_id, {})
        count = chat_warns.get(target_id, 0) + 1
        chat_warns[target_id] = count

        chat_reasons = warn_reasons.setdefault(chat_id, {})
        reasons_list = chat_reasons.setdefault(target_id, [])
        reasons_list.append(reason or "Warning di gayi")
        save_state()

        if count >= 3:
            # PEHLE auto-ban ho jaata tha - ab NAHI. Sirf Owner ki DM mein Ban/Free
            # buttons jaate hain, koi bhi bina Owner ki permission ke ban nahi hoga.
            chat_warns[target_id] = 0
            final_reasons = list(reasons_list)
            chat_reasons[target_id] = []
            save_state()
            if OWNER_ID:
                title = known_chats.get(chat_id, "Group")
                numbered = "\n".join(str(i + 1) + ". " + r for i, r in enumerate(final_reasons))
                alert_text = ("⚠️ " + target_name + " ki 3 warning ho gayi hai (" + title + " mein).\n\n"
                              "Kya hua tha:\n" + numbered + "\n\n"
                              "Kya isko ban karna hai ya chhod dein? Bot khud kuch nahi karega, sirf tumhare"
                              " decision ka wait karega.")
                keyboard = {"inline_keyboard": [[
                    {"text": "🔨 Ban karo", "callback_data": "modbtn:banapprove:" + str(chat_id) + ":" + str(target_id)},
                    {"text": "🙏 Free chhodo", "callback_data": "modbtn:free:" + str(chat_id) + ":" + str(target_id)},
                ]]}
                safe_run(send_message_with_keyboard, OWNER_ID, alert_text, keyboard)
            return (target_name + " ki 3 warning ho gayi - Owner ko DM bhej diya, unki permission se hi ban hoga.", "warn3")
        return (target_name + " ko warning di gayi (" + str(count) + "/3)", "warn")
    else:
        return (target_name + " pe koi action nahi liya gaya.", "none")


def moderation_action_and_notify(action, chat_id, target_id, target_name, notify_chat_id, reply_to=None, reason=None):
    if action in ("ban", "kick", "mute", "warn"):
        protection = get_protection_message(chat_id, target_id)
        if protection:
            send_message(notify_chat_id, protection, reply_to)
            return

    text, state = do_moderation_action(action, chat_id, target_id, target_name, reason)
    keyboard = None
    if state == "ban":
        keyboard = {"inline_keyboard": [[{"text": "Unban", "callback_data": "modbtn:unban:" + str(chat_id) + ":" + str(target_id)}]]}
    elif state == "warn":
        keyboard = {"inline_keyboard": [[{"text": "Unwarn", "callback_data": "modbtn:unwarn:" + str(chat_id) + ":" + str(target_id)}]]}

    if keyboard:
        send_message_with_keyboard(notify_chat_id, text, keyboard, reply_to)
    else:
        send_message(notify_chat_id, text, reply_to)


def answer_callback(callback_id, text):
    requests.post(TELEGRAM_URL + "/answerCallbackQuery", json={"callback_query_id": callback_id, "text": text}, timeout=10)


# ==================== ADMIN COMMANDS (GROUP SE) ====================

def get_target_user(chat_id, message):
    reply_msg = message.get('reply_to_message')
    if reply_msg:
        return reply_msg.get('from')

    # Agar Telegram ne khud user ko "text_mention" ke roop mein embed kiya hai (jab kisi
    # ko tap-select karke tag kiya jaata hai, chahe uska @username ho ya na ho) - ye
    # sabse reliable tarika hai, isse pehle check karte hain.
    for entity in message.get('entities', []):
        if entity.get('type') == 'text_mention' and entity.get('user'):
            return entity['user']

    text = message.get('text', '')
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    if not arg:
        return None

    chat_users = known_users.get(chat_id, {})

    if arg.startswith('@'):
        # Username se target - sirf pehla token lo (username mein space nahi hota,
        # baaki text jaise "/ban @sarim122 spam kar raha tha" ignore ho jaayega)
        username_arg = arg.split()[0].lower()
        match = chat_users.get(username_arg) or chat_users.get(username_arg.lstrip('@'))
        if match:
            return {"id": match["id"], "first_name": match["name"]}
        return None

    name_arg = arg.lower()
    match = chat_users.get(name_arg)
    if match:
        return {"id": match["id"], "first_name": match["name"]}
    return None


def get_protection_message(chat_id, target_id):
    """Agar target Owner ya admin hai, to moderation command block karo aur bata do kyun."""
    if is_owner(target_id):
        return "Ye Owner hai, ispe koi bhi command kaam nahi karega."
    if safe_check_admin(chat_id, target_id):
        return "Ye admin hai, ispe ye command kaam nahi karega."
    return None


def handle_ban(chat_id, message):
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /ban likho, ya /ban naam likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    moderation_action_and_notify("ban", chat_id, target['id'], get_name(target), chat_id)


def handle_kick(chat_id, message):
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /kick likho, ya /kick naam likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    moderation_action_and_notify("kick", chat_id, target['id'], get_name(target), chat_id)


def safe_unban(chat_id, user_id):
    """CRITICAL FIX: Telegram ka 'unbanChatMember' API kabhi kabhi ek known bug/quirk ki
    wajah se ACTIVE members ko bhi group se NIKAAL deta hai, chahe 'only_if_banned: True'
    bheja ho aur wo banda actually banned na ho - Telegram is parameter ko hamesha
    reliably honor nahi karta. Isi wajah se pehle /unbanall chalane par innocent members
    (jo kabhi banned hi nahi the, sirf humari list mein galti se record ho gaye the) kick
    ho gaye the.

    Ab is function mein pehle Telegram se ASLI status check karte hain (getChatMember),
    aur unbanChatMember SIRF tabhi call karte hain jab wo banda waqai 'kicked' (banned)
    status mein ho. Agar wo already ek normal member hai, to unban call hi skip kar dete
    hain - isse ye kick-bug dobara kabhi nahi hoga."""
    try:
        r = requests.get(TELEGRAM_URL + "/getChatMember", params={"chat_id": chat_id, "user_id": user_id}, timeout=10)
        status = r.json().get('result', {}).get('status', '')
    except Exception as e:
        print("SAFE UNBAN STATUS CHECK ERROR: " + str(e))
        return False

    if status != 'kicked':
        print("SAFE UNBAN SKIP: user " + str(user_id) + " ka status '" + status + "' hai (banned nahi) - unban call skip kiya, kick-bug se bachne ke liye")
        return False

    try:
        requests.post(TELEGRAM_URL + "/unbanChatMember", json={"chat_id": chat_id, "user_id": user_id, "only_if_banned": True}, timeout=10)
        return True
    except Exception as e:
        print("SAFE UNBAN ERROR: " + str(e))
        return False


def handle_unban(chat_id, message):
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /unban likho, ya /unban naam likho!")
        return
    was_banned = safe_unban(chat_id, target['id'])
    unrecord_moderation(chat_id, "ban", target['id'])
    if was_banned:
        send_message(chat_id, get_name(target) + " ka ban hata diya.")
    else:
        send_message(chat_id, get_name(target) + " pehle se banned nahi tha, list se clean kar diya.")


def handle_unbanall(chat_id):
    """Bot ke history mein jitne bhi log is group mein ban hain, sabko ek saath unban
    kar deta hai. GC mein /unbanall likhne se hi chalta hai. Har entry ke liye pehle
    ASLI status check hota hai (safe_unban) - taaki koi active member galti se kick na
    ho jaaye jaisa pehle hota tha."""
    records = moderation_records.get(chat_id, {"banned": {}})
    banned = dict(records.get("banned", {}))  # copy le lo, kyunki loop ke andar hi modify hoga
    if not banned:
        send_message(chat_id, "Is group mein abhi koi banned member nahi hai.")
        return

    send_message(chat_id, str(len(banned)) + " members check kar raha hu, thoda ruko...")
    unbanned_names = []
    cleaned_names = []
    for uid, name in banned.items():
        try:
            was_banned = safe_unban(chat_id, uid)
            unrecord_moderation(chat_id, "ban", uid)
            if was_banned:
                unbanned_names.append(name)
            else:
                cleaned_names.append(name)
        except Exception as e:
            print("UNBANALL ERROR for " + str(uid) + ": " + str(e))

    reply_lines = []
    if unbanned_names:
        reply_lines.append("✅ " + str(len(unbanned_names)) + " members unban ho gaye:\n" + ", ".join(unbanned_names))
    if cleaned_names:
        reply_lines.append("ℹ️ " + str(len(cleaned_names)) + " log pehle se banned nahi the, list se clean kar diya (inhe touch nahi kiya):\n" + ", ".join(cleaned_names))
    if not reply_lines:
        reply_lines.append("Kuch process nahi hua, dobara try karo.")
    send_message(chat_id, "\n\n".join(reply_lines))


def handle_mute(chat_id, message):
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /mute likho, ya /mute naam likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    moderation_action_and_notify("mute", chat_id, target['id'], get_name(target), chat_id)


def handle_unmute(chat_id, message):
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /unmute likho, ya /unmute naam likho!")
        return
    moderation_action_and_notify("unmute", chat_id, target['id'], get_name(target), chat_id)


def handle_warn(chat_id, message):
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /warn likho, ya /warn naam likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    admin_name = get_name(message.get('from', {}))
    parts = (message.get('text') or '').split(maxsplit=1)
    custom_reason = parts[1].strip() if len(parts) > 1 else None
    reason = ("Admin (" + admin_name + ") ne manually warn kiya" + (": " + custom_reason if custom_reason else ""))
    moderation_action_and_notify("warn", chat_id, target['id'], get_name(target), chat_id, reason=reason)


def handle_unwarn(chat_id, message):
    """Kisi member ki ek warning kam kar deta hai - sirf Admin/Owner use kar sakte hain."""
    target = get_target_user(chat_id, message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /unwarn likho, ya /unwarn naam likho!")
        return
    chat_warns = warnings.setdefault(chat_id, {})
    current = chat_warns.get(target['id'], 0)
    if current <= 0:
        send_message(chat_id, get_name(target) + " ki koi warning hi nahi hai.")
        return
    chat_warns[target['id']] = current - 1
    reasons_list = warn_reasons.setdefault(chat_id, {}).get(target['id'])
    if reasons_list:
        reasons_list.pop()
    save_state()
    send_message(chat_id, get_name(target) + " ki 1 warning kam kar di. Ab: " + str(chat_warns[target['id']]) + "/3")


def handle_pin(chat_id, message):
    reply_msg = message.get('reply_to_message')
    if not reply_msg:
        send_message(chat_id, "Jis message ko pin karna hai, uspe reply karke /pin likho!")
        return
    requests.post(TELEGRAM_URL + "/pinChatMessage", json={"chat_id": chat_id, "message_id": reply_msg['message_id']}, timeout=10)
    send_message(chat_id, "Pin kar diya!")


# ==================== AI REPLY ====================

WEB_INFO_KEYWORDS = [
    "aaj", "aj", "kal", "abhi", "current", "latest", "news", "khabar",
    "score", "match", "result", "price", "rate", "kaun jeeta", "kisne jeeta",
    "kya hua", "weather", "mausam", "today", "yesterday", "date", "tareekh",
    "stock", "share market", "sensex", "nifty", "election", "budget"
]

# General knowledge / info wale sawaal jaise "Jharkhand ke baare me jaante ho", "X kya hai", "X kaun tha"
GENERAL_KNOWLEDGE_PATTERN = re.compile(
    r'(ke\s*baare|ke\s*bare|jaante\s*ho|jante\s*ho|jaanti\s*ho|janti\s*ho|pata\s*hai\s*kya|'
    r'kya\s*hai|kaun\s*(tha|thi|hai|hote)|kahan\s*hai|history\s*of|capital\s*of|'
    r'ke\s*baare\s*mein|ke\s*bare\s*mein|batao\s*iske\s*baare|jankari\s*do|information\s*do)',
    re.IGNORECASE
)


RETRY_WAIT_PATTERN = re.compile(r'try again in ([0-9.]+)s', re.IGNORECASE)


# ---------------------------------------------------------------------------
# CIRCUIT BREAKER: jab Groq/Gemini ka DAILY quota khatam ho jaata hai, to wo
# error der tak (kabhi kabhi ghanto) rehta hai. Pehle bot har message pe dono
# API ko dobara-dobara try karta tha (5-15s sleep ke saath retry), jisse
# request bahut slow ho jaati thi aur Render par woh timeout/hang ho ke
# "crash" jaisa lagta tha. Ab jaise hi daily-quota-exceeded pakda jaata hai,
# uss provider ko kuch der ke liye "down" mark kar dete hain taaki agla
# message turant doosre backup (ya friendly fallback) pe chala jaaye -
# koi lambi retry-wait nahi hogi.
# ---------------------------------------------------------------------------
AI_OUTAGE_UNTIL = {"groq": 0.0, "gemini": 0.0, "openrouter": 0.0, "mistral": 0.0, "cerebras": 0.0, "deepseek": 0.0, "nvidia": 0.0}
DAILY_QUOTA_COOLDOWN = 600  # 10 minute - itni der baad khud-ba-khud dobara try karega


def mark_ai_down(provider, seconds=DAILY_QUOTA_COOLDOWN):
    AI_OUTAGE_UNTIL[provider] = time.time() + seconds
    print(provider.upper() + " marked DOWN for " + str(seconds) + "s (daily quota exceeded)")


def is_ai_down(provider):
    return time.time() < AI_OUTAGE_UNTIL.get(provider, 0.0)


def is_daily_quota_error(provider, err):
    """Ye batata hai ki error 'daily/TPD quota khatam' type hai (jise turant retry karne
    se koi fayda nahi) ya sirf chhota transient rate-limit hai (jise retry karna theek hai)."""
    msg = str(err).lower()
    if provider == "groq":
        return "tpd" in msg or "per day" in msg or "tokens per day" in msg
    if provider == "gemini":
        return "perday" in msg.replace(" ", "").lower() or "requestsperday" in msg.replace(" ", "").lower()
    return False


def call_groq(payload, timeout=20, max_retries=1):
    """Groq ko call karta hai. Agar 429 rate-limit aaye to Groq ke bataye wait-time tak rukke
    khud-ba-khud retry karta hai, taaki user ko error na dikhe. Agar quota DAILY khatam ho
    chuki hai to retry nahi karta - seedha outage mark karke turant return karta hai."""
    if is_ai_down("groq"):
        return None, {"error": {"message": "groq temporarily skipped (daily quota cooldown)", "code": "rate_limit_exceeded"}}

    headers = {"Authorization": "Bearer " + str(GROQ_API_KEY)}
    attempt = 0
    while True:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {}

        if "choices" in data:
            return r, data

        err = data.get("error", {})
        is_rate_limit = (r.status_code == 429) or (err.get("code") == "rate_limit_exceeded")

        if is_rate_limit and is_daily_quota_error("groq", err.get("message", "")):
            mark_ai_down("groq")
            return r, data

        if is_rate_limit and attempt < max_retries:
            wait_match = RETRY_WAIT_PATTERN.search(err.get("message", ""))
            wait_time = float(wait_match.group(1)) if wait_match else 5.0
            wait_time = min(wait_time + 0.5, 15.0)  # thoda buffer, aur zyada der na ruke
            print("RATE LIMIT HIT, waiting " + str(round(wait_time, 1)) + "s then retrying (attempt " + str(attempt + 1) + ")")
            time.sleep(wait_time)
            attempt += 1
            continue

        return r, data


def call_openai_compatible(provider, url, api_key, model, payload, timeout=20, max_retries=1):
    """Generic caller for OpenAI-compatible chat APIs (Mistral, Cerebras, etc.) - isse
    har naye provider ke liye alag function likhna nahi padta, bas URL/key/model badal do."""
    if not api_key:
        return None, {"error": {"message": provider.upper() + "_API_KEY set nahi hai"}}
    if is_ai_down(provider):
        return None, {"error": {"message": provider + " temporarily skipped (daily quota cooldown)", "code": "rate_limit_exceeded"}}

    headers = {"Authorization": "Bearer " + str(api_key)}
    body = dict(payload)
    body["model"] = model
    attempt = 0
    while True:
        r = requests.post(url, json=body, headers=headers, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {}

        if "choices" in data:
            return r, data

        err = data.get("error", {})
        if isinstance(err, str):
            err = {"message": err}
        is_rate_limit = (r.status_code == 429) or (str(err.get("code", "")) == "429")
        is_model_unavailable = r.status_code == 404

        if is_model_unavailable:
            print(provider.upper() + " MODEL UNAVAILABLE: " + str(err.get("message", "")))
            mark_ai_down(provider, 3600)
            return r, data

        if is_rate_limit and attempt < max_retries:
            wait_time = 5.0
            print(provider.upper() + " RATE LIMIT HIT, waiting " + str(wait_time) + "s then retrying")
            time.sleep(wait_time)
            attempt += 1
            continue

        if is_rate_limit:
            mark_ai_down(provider)

        return r, data


def call_mistral(payload, timeout=20, max_retries=1):
    """Mistral La Plateforme ka free 'Experiment' tier - CHOUTHA fallback."""
    return call_openai_compatible("mistral", "https://api.mistral.ai/v1/chat/completions", MISTRAL_API_KEY, MISTRAL_MODEL, payload, timeout, max_retries)


def call_cerebras(payload, timeout=20, max_retries=1):
    """Cerebras Cloud ka free tier (1M tokens/din) - PAANCHVA fallback."""
    return call_openai_compatible("cerebras", "https://api.cerebras.ai/v1/chat/completions", CEREBRAS_API_KEY, CEREBRAS_MODEL, payload, timeout, max_retries)


def call_deepseek(payload, timeout=20, max_retries=1):
    """DeepSeek ka free sign-up grant (5M tokens, koi card nahi) - CHATHVA fallback."""
    return call_openai_compatible("deepseek", "https://api.deepseek.com/v1/chat/completions", DEEPSEEK_API_KEY, DEEPSEEK_MODEL, payload, timeout, max_retries)


def call_nvidia(payload, timeout=20, max_retries=1):
    """NVIDIA NIM (build.nvidia.com) ka free tier, koi card nahi - SAATVA fallback."""
    return call_openai_compatible("nvidia", "https://integrate.api.nvidia.com/v1/chat/completions", NVIDIA_API_KEY, NVIDIA_MODEL, payload, timeout, max_retries)


def call_openrouter(payload, timeout=20, max_retries=1):
    """OpenRouter ko call karta hai - ye TEESRA aur FREE fallback hai, jab Groq aur Gemini
    dono ka quota khatam ho jaaye tab ye chalta hai. OpenRouter ke ':free' models bilkul
    muft hain, koi card nahi chahiye."""
    if not OPENROUTER_API_KEY:
        return None, {"error": {"message": "OPENROUTER_API_KEY set nahi hai"}}
    if is_ai_down("openrouter"):
        return None, {"error": {"message": "openrouter temporarily skipped (daily quota cooldown)", "code": "rate_limit_exceeded"}}

    headers = {"Authorization": "Bearer " + str(OPENROUTER_API_KEY)}
    body = dict(payload)
    body["model"] = OPENROUTER_MODEL
    attempt = 0
    while True:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", json=body, headers=headers, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {}

        if "choices" in data:
            return r, data

        err = data.get("error", {})
        is_rate_limit = (r.status_code == 429) or (str(err.get("code", "")) == "429")
        is_model_unavailable = (r.status_code == 404) or (str(err.get("code", "")) == "404")

        if is_model_unavailable:
            print("OPENROUTER MODEL UNAVAILABLE: " + str(err.get("message", "")))
            mark_ai_down("openrouter", 3600)  # 1 hour - taaki galat model se baar baar wasted calls na ho
            return r, data

        if is_rate_limit and attempt < max_retries:
            wait_time = 5.0
            print("OPENROUTER RATE LIMIT HIT, waiting " + str(wait_time) + "s then retrying")
            time.sleep(wait_time)
            attempt += 1
            continue

        if is_rate_limit:
            mark_ai_down("openrouter")

        return r, data


def to_gemini_contents(messages):
    """OpenAI-style messages list ko Gemini ke format mein convert karta hai."""
    system_parts = []
    contents = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        elif m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        elif m["role"] == "assistant":
            contents.append({"role": "model", "parts": [{"text": m["content"]}]})
    return "\n".join(system_parts), contents


def call_gemini(payload, timeout=20, max_retries=1):
    """Gemini ko call karta hai. 429 aane pe thoda wait karke ek baar retry karta hai.
    Agar quota DAILY khatam ho chuki hai to retry nahi karta - turant outage mark karta hai."""
    if not GEMINI_API_KEY:
        return None, {"error": {"message": "GEMINI_API_KEY set nahi hai"}}
    if is_ai_down("gemini"):
        return None, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "gemini temporarily skipped (daily quota cooldown)"}}

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    attempt = 0
    while True:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {}

        if data.get("candidates"):
            return r, data

        err = data.get("error", {})
        is_rate_limit = (r.status_code == 429) or (err.get("status") == "RESOURCE_EXHAUSTED")

        if is_rate_limit and is_daily_quota_error("gemini", data):
            mark_ai_down("gemini")
            return r, data

        if is_rate_limit and attempt < max_retries:
            wait_time = 5.0
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_time = float(retry_after)
                except Exception:
                    pass
            wait_time = min(wait_time + 0.5, 15.0)
            print("GEMINI RATE LIMIT HIT, waiting " + str(round(wait_time, 1)) + "s then retrying")
            time.sleep(wait_time)
            attempt += 1
            continue

        return r, data


def extract_gemini_text(data):
    try:
        candidates = data.get("candidates")
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception:
        return None


def needs_web_info(text):
    t = text.lower()
    if any(k in t for k in WEB_INFO_KEYWORDS):
        return True
    if GENERAL_KNOWLEDGE_PATTERN.search(t):
        return True
    return False


def fetch_web_info(query):
    """Current/factual info nikalta hai. Pehle Gemini (Google Search) try karta hai - ye
    asli real-time search hai. Agar wo fail ho jaaye to Groq/OpenRouter backup ban jaate hain,
    lekin ye sirf model ki apni training knowledge se jawab dete hain (koi live search nahi) -
    isliye inka jawab 'confirmed real-time fact' jaisa treat nahi karna chahiye."""
    # Pehli koshish: Gemini (REAL live Google Search)
    try:
        payload = {
            "contents": [{"parts": [{"text": query}]}],
            "tools": [{"google_search": {}}]
        }
        r, data = call_gemini(payload, timeout=20)
        info = extract_gemini_text(data)
        if info:
            if len(info) > 600:
                info = info[:600] + "..."
            return info, True  # True = live search se aaya, pakka sahi hai
        print("GEMINI WEB INFO FAILED, trying Groq backup: " + str(data))
    except Exception as e:
        print("GEMINI WEB INFO EXCEPTION, trying Groq backup: " + str(e))

    # Backup: Groq compound-mini
    try:
        payload = {
            "model": "groq/compound-mini",
            "messages": [{"role": "user", "content": query}],
            "max_completion_tokens": 300
        }
        r, data = call_groq(payload, timeout=20)
        if "choices" in data:
            info = data["choices"][0]["message"]["content"]
            if info and len(info) > 600:
                info = info[:600] + "..."
            return info, False  # False = live search nahi, model ke apne gyaan se
        groq_status = r.status_code if r is not None else "skipped"
        print("GROQ WEB INFO BACKUP ALSO FAILED (status " + str(groq_status) + "): " + str(data))
    except Exception as e:
        print("GROQ WEB INFO BACKUP EXCEPTION: " + str(e))

    # Aakhri koshish: OpenRouter se (real-time search nahi, lekin kuch na milne se behtar)
    try:
        payload = {"messages": [{"role": "user", "content": query}], "max_tokens": 300}
        r, data = call_openrouter(payload, timeout=20)
        if "choices" in data:
            info = data["choices"][0]["message"]["content"]
            if info and len(info) > 600:
                info = info[:600] + "..."
            return info, False
        print("OPENROUTER WEB INFO BACKUP ALSO FAILED: " + str(data))
    except Exception as e:
        print("OPENROUTER WEB INFO BACKUP EXCEPTION: " + str(e))

    return None, False


REASONING_LEAK_MARKERS = [
    "here's a thinking process", "here is a thinking process", "let me think about this",
    "let me analyze", "analyze the user's", "analyze the user input", "system instructions",
    "the system prompt", "determine the persona", "step-by-step reasoning",
    "chain of thought", "as an ai language model", "i need to consider",
    "the user is asking", "the user said:", "1. **analyze", "let's see", "let's see.",
    "looking at the history", "so the user is", "according to the rules",
    "the user is kind of", "okay, let's", "okay let's", "i shouldn't add",
    # Hamari apni system prompt/context-note ke unique lines - agar ye kabhi bhi
    # jawab mein dikhein, matlab bot ne apna hi internal instruction leak kar diya.
    "sabse zaroori niyam", "har reply maximum 2 lines", "sirf background context ke liye",
    "isko explain ya describe mat karna", "web se ye real aur latest jaankari",
    "ye jaankari live search se nahi", "tum \"khan\" ho, ek dost",
    "bahut zaroori - ye hi sabse badi galti",
]


def looks_like_leaked_reasoning(text):
    """Kabhi kabhi koi reasoning-wala AI model (jaise Groq ka gpt-oss, ya OpenRouter ka
    auto-picked koi reasoning model) galti se apna poora internal 'sochne ka process'
    bhi answer ke andar bhej deta hai - jisme system prompt ke details, persona-analysis,
    meta-commentary sab dikhne lagta hai. Har model alag wording use karta hai, isliye
    sirf specific phrases dhoondhna kaafi nahi - isliye LENGTH aur STRUCTURE (bahut lamba
    jawab, numbered list, kai paragraphs) pe bhi check karte hain, jo har reasoning-leak
    mein common hota hai chahe wording kuch bhi ho. Khan ka har reply max 2-line hota hai,
    isliye koi bhi bahut lamba jawab khud hi suspicious hai."""
    if not text:
        return False
    lowered = text.lower()
    for marker in REASONING_LEAK_MARKERS:
        if marker in lowered:
            return True
    # Khan ka persona hamesha max 2-line ka hota hai - ek genuine reply itna lamba
    # (350+ characters) kabhi nahi hota. Isse wording-independent protection milta hai.
    if len(text) > 350:
        return True
    # Numbered analysis steps (1. 2. 3. ...) - reasoning traces mein aam hai, casual
    # 2-line reply mein kabhi nahi hota.
    if len(re.findall(r'(?:^|\n)\s*\d+\.\s', text)) >= 2:
        return True
    # Kai paragraphs (reasoning mein hote hain, casual chat reply mein nahi)
    if text.count('\n\n') >= 2:
        return True
    return False


INCOMPLETE_TRAILING_WORDS = {
    "aur", "ki", "jo", "kyunki", "kyuki", "lekin", "but", "and", "jaise", "warna",
    "jabki", "taaki", "magar", "or", "jisse", "isliye", "kyu", "kyun", "ke", "ka",
}

# Chhote (1-3 letter) Hindi/Hinglish words jo genuinely ek sentence ko khatam kar sakte
# hain - inke alawa koi bhi chhota, ajeeb sa aakhri "word" (jaise 'pa' - jo asal mein
# 'paas' ka aadha-kata hua tha) suspicious maana jaayega.
SAFE_SHORT_ENDING_WORDS = {
    "hai", "ho", "ka", "ki", "ko", "se", "pe", "par", "bhi", "na", "to", "toh", "wo",
    "vo", "ye", "yeh", "kya", "kyu", "kyun", "aa", "ja", "le", "de", "kar", "kr",
    "gya", "gyi", "gaya", "gayi", "tha", "thi", "the", "hu", "hun", "hoon", "tu",
    "tum", "main", "mai", "bhai", "yaar", "ok", "haan", "han", "nahi", "nhi", "hoga",
    "hogi", "kal", "ab", "hi", "wah", "are", "abe", "oye", "kab", "sab", "tab", "jab",
    "aaj", "abhi", "phir", "chal", "chalo", "ruk", "dekh", "sun", "bata", "acha",
    "accha", "theek", "thik", "sahi", "galat", "kaisa", "kaisi", "kaise",
    # 3-letter common valid endings (English + Hinglish) - taaki inhe galti se
    # "truncated word" na samjha jaaye
    "bye", "wow", "yes", "yep", "nah", "lol", "omg", "aap", "hum", "kar",
    "abb", "kro", "bro", "sis", "bas", "aya", "aye", "gai", "gaa", "aha",
    "hey", "hii", "wat", "raw", "big", "old", "new", "top", "fun",
}


def ends_incomplete(text):
    """Agar jawab comma/dash/colon pe khatam ho raha hai (jaise 'Kya hua,'), ek aise
    connector word pe khatam ho raha hai jo kabhi bhi ek complete Hindi/English sentence
    ka aakhri shabd nahi hota (jaise 'aur', 'ki', 'jo', 'lekin'), YA ek chhota, ajeeb sa
    ADHOORA word (jaise 'pa' jab 'paas' hona chahiye tha) - to matlab jawab beech mein hi
    kat gaya hai, chahe uska finish_reason kuch bhi bataye."""
    cleaned = text.strip()
    if not cleaned:
        return False
    if cleaned[-1] in ',-:;–—':
        return True
    stripped_end = cleaned.rstrip('.!?"\')।')
    words = re.findall(r"[\w']+", stripped_end.lower())
    if not words:
        return False
    last_word = words[-1]
    if last_word in INCOMPLETE_TRAILING_WORDS:
        return True
    # Agar aakhri "word" 1-2 letters ka hai, sirf letters se bana hai (koi number/emoji
    # nahi), aur ek jaana-pehchana chhota Hindi/Hinglish word nahi hai - to shayad ye
    # kisi bade word ka adhoora reh gaya tuta hua tukda hai. (3-letter tak nahi jaate,
    # warna 'bye', 'wow' jaise valid words galti se reject ho jaate.)
    if 1 <= len(last_word) <= 2 and last_word.isalpha() and last_word not in SAFE_SHORT_ENDING_WORDS:
        return True
    return False


def is_usable_reply(text, finish_reason=None, user_text=None):
    """Kabhi kabhi koi weak/backup model adhura ya bekar jawab de deta hai (jaise sirf
    'The' jaisa toota-phoota fragment, 'Kya hua,' jaisa comma pe kata hua, ya 'apne pa'
    jaisa beech-word mein kata hua) - khaaskar jab model token-limit ki wajah se beech
    mein hi ruk jaaye (finish_reason 'length'), ya jawab khud hi ek adhoore connector
    word/comma/adhoore-word pe khatam ho raha ho (chahe finish_reason kuch bhi ho). Ye
    check aisa jawab user ko bhejne se pehle hi reject kar deta hai.

    Ek aur cheez check karta hai: kabhi weak model user ki hi baat thoda ghuma-firaake
    wapas bhej deta hai (jaise 'Ager nhi bheja to' -> 'Ager nahi bheja') - ye koi jawab
    nahi hai, sirf echo hai. Isko bhi reject karte hain.

    Aur ek aakhri, sabse zaroori check: agar jawab mein leaked internal reasoning/thinking
    process ke signs hain, to use bilkul reject kar dete hain - chahe wo kitna bhi lamba
    ya "complete" kyun na lage."""
    if not text:
        return False
    cleaned = text.strip()
    if len(cleaned) < 2:
        return False
    # finish_reason 'length' ka matlab hai model token-limit ki wajah se beech mein hi
    # kata gaya - ye khud hi ek pakka signal hai ki jawab adhoora hai, chahe wo kitna bhi
    # lamba kyun na dikhe.
    if finish_reason == "length":
        return False
    if ends_incomplete(cleaned):
        return False
    if looks_like_leaked_reasoning(cleaned):
        return False
    if user_text:
        a = re.sub(r'[^\w\s]', '', cleaned.lower()).strip()
        b = re.sub(r'[^\w\s]', '', user_text.strip().lower()).strip()
        if a and b and len(b) > 3:
            similarity = difflib.SequenceMatcher(None, a, b).ratio()
            if similarity > 0.75:
                return False
    return True


def _try_groq_chat(messages_for_ai, max_tokens=200):
    # reasoning_format="hidden" ZAROORI hai - warna gpt-oss-120b (reasoning model) apna
    # poora internal 'thinking process' bhi answer ke andar bhej deta hai (jo user ko
    # dikhta hai) - Groq ke docs confirm karte hain ki default 'raw' hai, isse content
    # aur reasoning aapas mein mix ho jaate hain. 'hidden' set karne se sirf final, saaf
    # jawab milta hai.
    payload = {
        "model": "openai/gpt-oss-120b", "messages": messages_for_ai,
        "temperature": 0.8, "max_tokens": max_tokens,
        "reasoning_format": "hidden", "reasoning_effort": "low"
    }
    res, data = call_groq(payload, timeout=20)
    if "choices" in data:
        choice = data["choices"][0]
        return choice["message"]["content"], choice.get("finish_reason")
    status = res.status_code if res is not None else "skipped"
    print("GROQ CHAT FAILED (status " + str(status) + "): " + str(data))
    return None, None


def _try_gemini_chat(messages_for_ai, max_tokens=200):
    system_text, gemini_contents = to_gemini_contents(messages_for_ai)
    payload = {"contents": gemini_contents, "generationConfig": {"temperature": 0.8, "maxOutputTokens": max_tokens}}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    _, data = call_gemini(payload, timeout=20)
    text = extract_gemini_text(data)
    if text:
        return text, None
    print("GEMINI CHAT FAILED: " + str(data))
    return None, None


def _try_openai_compatible_chat(name, call_fn, messages_for_ai, extra=None, max_tokens=200):
    payload = {"messages": messages_for_ai, "temperature": 0.8, "max_tokens": max_tokens}
    if extra:
        payload.update(extra)
    _, data = call_fn(payload, timeout=20)
    if "choices" in data:
        choice = data["choices"][0]
        return choice["message"]["content"], choice.get("finish_reason")
    print(name.upper() + " CHAT FAILED: " + str(data))
    return None, None


def ask_ai_raw(messages, max_tokens=600):
    """Chat-reply ke alawa kisi bhi kaam (jaise riddle banwana) ke liye 7 providers ki chain se
    seedha raw text jawab laata hai. Chat wale quality-filters yahan nahi lagte."""
    chain = [
        ("groq", lambda: _try_groq_chat(messages, max_tokens)),
        ("gemini", lambda: _try_gemini_chat(messages, max_tokens)),
        ("openrouter", lambda: _try_openai_compatible_chat("openrouter", call_openrouter, messages, {"reasoning": {"exclude": True}}, max_tokens)),
        ("mistral", lambda: _try_openai_compatible_chat("mistral", call_mistral, messages, None, max_tokens)),
        ("cerebras", lambda: _try_openai_compatible_chat("cerebras", call_cerebras, messages, {"reasoning_format": "hidden"}, max_tokens)),
        ("deepseek", lambda: _try_openai_compatible_chat("deepseek", call_deepseek, messages, None, max_tokens)),
        ("nvidia", lambda: _try_openai_compatible_chat("nvidia", call_nvidia, messages, None, max_tokens)),
    ]
    for name, attempt in chain:
        try:
            out, finish_reason = attempt()
        except Exception as e:
            print(name.upper() + " RAW EXCEPTION: " + str(e))
            continue
        if out and finish_reason != "length":
            return out
    return None


def get_ai_reply(user_id, user_text, raw_text=None, quoted_context=None):
    """raw_text: agar diya gaya hai, to history mein ye (user ne jo asli mein type kiya)
    save hota hai - sirf isi turn ke AI call ke liye extra context use hota hai. Isse
    future messages ka context saaf rehta hai.

    quoted_context: (quoted_name, quoted_text) tuple - agar user ne kisi purane message
    pe reply kiya hai. Ye ek system-instruction ke roop mein diya jaata hai, taaki AI use
    sirf BACKGROUND jaankari ki tarah le, aur khud us quote ko explain/describe karne na
    lage (jaisa pehle hota tha - 'uska matlab tha...' jaisा meta-commentary), balki seedha
    natural dost jaisa jawab de."""
    if raw_text is None:
        raw_text = user_text
    try:
        history = get_user_history(user_id)

        MAX_TOTAL_CHARS = 40000  # safe budget jisse Groq 413 na de, isi ke andar jitni history fit ho sake utni jaayegi

        def trim(text, limit=4000):
            if text and len(text) > limit:
                return text[:limit] + "..."
            return text

        user_text_trimmed = trim(user_text)

        # sabse recent messages se peeche ki taraf jao, jitna budget mein fit ho utna lo
        packed = []
        used_chars = len(SYSTEM_PROMPT) + len(user_text_trimmed)
        for h in reversed(history):
            content = trim(h["content"])
            entry_len = len(content)
            if used_chars + entry_len > MAX_TOTAL_CHARS:
                break
            packed.append({"role": h["role"], "content": content})
            used_chars += entry_len
        packed.reverse()

        messages_for_ai = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages_for_ai.extend(packed)

        if quoted_context:
            q_name, q_text = quoted_context
            messages_for_ai.append({
                "role": "system",
                "content": "[Sirf background context ke liye, isko explain ya describe MAT karna: " + q_name +
                            " ne pehle ye kaha tha: \"" + trim(q_text, 1000) + "\". Neeche wala message usi ka reply hai. "
                            "Bas is context ko dhyan mein rakhkar, jaise ek dost ko pura pata hota hai kis baat pe baat ho rahi hai "
                            "waise hi seedha, natural jawab do - kabhi bhi 'iska matlab tha' ya 'reply ka matlab' jaisa kuch mat bolo, "
                            "bas normal conversation jaisa jawab do.]"
            })

        # sirf jab query ko current/web info chahiye, tabhi alag se search karo
        if needs_web_info(user_text_trimmed):
            web_info, is_live_search = fetch_web_info(user_text_trimmed)
            if web_info:
                if is_live_search:
                    info_note = "Web se ye REAL aur LATEST jaankari mili hai (live Google Search se). Isi info ka use karke user ke sawaal ka SAHI aur ASLI jawab do (Khan ke dost wale 2-line Hinglish andaz mein, thoda mazak bhi jod sakte ho) - lekin jawab mein actual jaankari zaroor honi chahiye, sirf mazak mein mat taal do: "
                else:
                    info_note = "Ye jaankari live search se nahi, balki ek AI model ke apne gyaan se aayi hai - ho sakta hai thodi purani ya galat ho. Isse ek helpful guess ki tarah use karo, 100% pakki sach ki tarah mat bolo. Agar tumhe khud confidence nahi hai to seedha bol do 'pakka nahi pata yaar, khud check kar lena'. Jaankari: "
                messages_for_ai.append({
                    "role": "system",
                    "content": info_note + web_info
                })

        messages_for_ai.append({"role": "user", "content": user_text_trimmed})

        # 7 providers ki chain ek-ek karke try karo - jo bhi pehla usable (na-adhura, na-khali)
        # jawab de de, wahi use karte hain. Isse code bhi saaf hai aur galti se ek provider ka
        # step chhoot jaane ka risk bhi nahi (jaisa pehle deeply-nested if-else mein ho sakta tha).
        providers = [
            ("groq", lambda: _try_groq_chat(messages_for_ai)),
            ("gemini", lambda: _try_gemini_chat(messages_for_ai)),
            ("openrouter", lambda: _try_openai_compatible_chat("openrouter", call_openrouter, messages_for_ai, {"reasoning": {"exclude": True}})),
            ("mistral", lambda: _try_openai_compatible_chat("mistral", call_mistral, messages_for_ai)),
            ("cerebras", lambda: _try_openai_compatible_chat("cerebras", call_cerebras, messages_for_ai, {"reasoning_format": "hidden"})),
            ("deepseek", lambda: _try_openai_compatible_chat("deepseek", call_deepseek, messages_for_ai)),
            ("nvidia", lambda: _try_openai_compatible_chat("nvidia", call_nvidia, messages_for_ai)),
        ]

        reply_text = None
        for name, attempt in providers:
            try:
                text, finish_reason = attempt()
            except Exception as e:
                print(name.upper() + " EXCEPTION: " + str(e))
                text, finish_reason = None, None

            if is_usable_reply(text, finish_reason, user_text_trimmed):
                reply_text = text.strip()
                break
            elif text:
                print(name.upper() + " GAVE UNUSABLE/ADHURA REPLY, trying next provider: " + repr(text))

        if not reply_text:
            print("ALL " + str(len(providers)) + " AI PROVIDERS FAILED for this message")
            return "Arre thoda ruk yaar, sab AI thoda busy hai abhi. Thodi der baad phir try karo bhai 🙏"

        now = time.time()
        history.append({"role": "user", "content": raw_text, "time": now})
        history.append({"role": "assistant", "content": reply_text, "time": now})
        chat_memory[user_id] = history[-MAX_MESSAGES_PER_USER:]

        return reply_text
    except Exception as e:
        print("AI ERROR: " + str(e))
        return "Arre yaar, dimaag hang ho gaya"


def send_message(chat_id, text, reply_to=None, parse_mode=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        if parse_mode:
            payload["parse_mode"] = parse_mode
        r = requests.post(TELEGRAM_URL + "/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("SEND ERROR: " + str(e))
        return None


def send_typing_action(chat_id, action="typing"):
    """Telegram ko batata hai ki bot abhi 'typing...' ya 'recording voice...' kar raha hai -
    isse user ko upar chhota indicator dikhta hai, taaki lage bot soch raha hai, gayab nahi ho gaya.
    action: 'typing', 'upload_photo', 'record_voice', 'upload_voice' etc."""
    try:
        requests.post(TELEGRAM_URL + "/sendChatAction", json={"chat_id": chat_id, "action": action}, timeout=10)
    except Exception as e:
        print("TYPING ACTION ERROR: " + str(e))


class TypingIndicator:
    """Telegram ka 'typing...' status sirf ~5 second tak dikhta hai. Jab bot ke andar
    multiple AI fallback providers try hote hain (Groq fail -> Gemini fail -> OpenRouter...),
    poora process 10-20+ second bhi le sakta hai - isliye indicator beech mein gayab ho
    jaata tha aur lagta tha bot kuch nahi kar raha. Ye class background mein har 4 second
    mein typing action ko refresh karti rehti hai jab tak reply taiyar na ho jaaye.

    Use: with TypingIndicator(chat_id, "typing"): reply = get_ai_reply(...)
    """
    def __init__(self, chat_id, action="typing"):
        self.chat_id = chat_id
        self.action = action
        self.stop_event = threading.Event()
        self.thread = None

    def _loop(self):
        while not self.stop_event.is_set():
            send_typing_action(self.chat_id, self.action)
            self.stop_event.wait(4)

    def __enter__(self):
        send_typing_action(self.chat_id, self.action)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()


REACTION_EMOJIS = ["👍", "😁", "🔥", "❤", "👏", "🤔", "😢", "🎉", "🤩", "👌", "🙏", "💯"]


def react_to_message(chat_id, message_id):
    try:
        emoji = random.choice(REACTION_EMOJIS)
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}]
        }
        r = requests.post(TELEGRAM_URL + "/setMessageReaction", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("REACT ERROR: " + str(e))
        return None


def send_broadcast_photo(chat_id, file_id, caption=None, parse_mode=None):
    try:
        payload = {"chat_id": chat_id, "photo": file_id}
        if caption:
            payload["caption"] = caption[:1024]
        if parse_mode:
            payload["parse_mode"] = parse_mode
        r = requests.post(TELEGRAM_URL + "/sendPhoto", json=payload, timeout=20)
        return r.json()
    except Exception as e:
        print("SEND PHOTO ERROR: " + str(e))
        return None


def send_broadcast_video(chat_id, file_id, caption=None):
    try:
        payload = {"chat_id": chat_id, "video": file_id}
        if caption:
            payload["caption"] = caption[:1024]
        r = requests.post(TELEGRAM_URL + "/sendVideo", json=payload, timeout=20)
        return r.json()
    except Exception as e:
        print("SEND VIDEO ERROR: " + str(e))
        return None


ANIME_KEYWORDS = [
    "naruto", "anime", "manga", "sasuke", "sakura", "kakashi", "itachi",
    "goku", "vegeta", "luffy", "zoro", "sanji", "one piece",
    "dragon ball", "attack on titan", "eren", "mikasa", "levi",
    "demon slayer", "tanjiro", "nezuko", "zenitsu", "inosuke",
    "gojo", "satoru", "sukuna", "itadori", "jujutsu kaisen", "jjk",
    "ichigo", "bleach", "deku", "bakugo", "my hero academia",
    "saitama", "one punch man", "natsu", "fairy tail",
    "light yagami", "death note", "sketch", "chibi"
]

REALISTIC_KEYWORDS = ["realistic photo", "real photo", "landscape", "nature photo", "real life photo", "photography of"]


def generate_image(prompt, style_reference=None, reference_info=None):
    """Pollinations.ai se image banata hai. 'flux' hi genuinely free model hai (baaki jaise
    nanobanana/zimage/turbo ko paid Pollen credits chahiye, isliye unse 402 error aata hai) -
    isliye style ab sirf prompt engineering se control hota hai, model 'flux' hi fixed rakha hai."""
    reference_lower = (style_reference or prompt).lower()
    is_anime = any(k in reference_lower for k in ANIME_KEYWORDS)
    models_to_try = ["flux"]

    full_prompt = prompt
    if reference_info:
        full_prompt = full_prompt + ", " + reference_info
    if is_anime:
        full_prompt = full_prompt + ", accurate official character design, correct hair color and outfit, anime art style, high detail"
    else:
        full_prompt = full_prompt + ", accurate, realistic, high detail"

    try:
        encoded_prompt = requests.utils.quote(full_prompt[:800])
    except Exception as e:
        print("IMAGE GEN PROMPT ENCODE EXCEPTION: " + str(e))
        return None

    # naya unified endpoint pehle, purana legacy endpoint backup ki tarah (agar naya kabhi down ho)
    endpoints = [
        "https://gen.pollinations.ai/image/" + encoded_prompt,
        "https://image.pollinations.ai/prompt/" + encoded_prompt,
    ]

    for endpoint_url in endpoints:
        for model in models_to_try:
            try:
                params = {"width": 1024, "height": 1024, "nologo": "true", "model": model, "enhance": "true"}
                headers = {}
                if POLLINATIONS_API_KEY:
                    headers["Authorization"] = "Bearer " + POLLINATIONS_API_KEY
                    params["key"] = POLLINATIONS_API_KEY
                r = requests.get(endpoint_url, params=params, headers=headers, timeout=60)
                if r.status_code == 200 and r.content and len(r.content) > 500:
                    return r.content
                print("IMAGE GEN ERROR (status " + str(r.status_code) + ", model=" + model + ", endpoint=" + endpoint_url + "), content length: " + str(len(r.content) if r.content else 0) + " - trying next")
            except Exception as e:
                print("IMAGE GEN EXCEPTION (model=" + model + ", endpoint=" + endpoint_url + "): " + str(e) + " - trying next")
                continue

    print("IMAGE GEN: sab models aur endpoints fail ho gaye")
    return None


def handle_image_request(chat_id, message_id, image_prompt, style_reference):
    """Image generation ka poora alag system - normal chat flow se bilkul independent.
    1) Web se subject ki knowledge nikalta hai (accurate dikhne ke liye)
    2) Progress update karta hai user ko
    3) Enriched prompt se image banata hai
    """
    status = send_message(chat_id, "🎨 Tumhari pic process ho rahi hai... 10%", message_id)
    status_id = None
    try:
        status_id = status.get('result', {}).get('message_id') if status else None
    except Exception:
        status_id = None

    if status_id:
        safe_run(edit_message, chat_id, status_id, "🔎 Reference dhoond raha hu... 40%")

    reference_info = None
    try:
        reference_info, _ = fetch_web_info(image_prompt + " - appearance, hair color, eyes, outfit, distinctive features")
    except Exception as e:
        print("IMAGE REFERENCE FETCH EXCEPTION: " + str(e))

    if status_id:
        safe_run(edit_message, chat_id, status_id, "🎨 Image bana raha hu... 80%")

    image_bytes = generate_image(image_prompt, style_reference, reference_info)

    if image_bytes:
        safe_run(send_photo, chat_id, image_bytes, None, message_id)
        if status_id:
            safe_run(delete_message, chat_id, status_id)
    else:
        if status_id:
            safe_run(edit_message, chat_id, status_id, "Abhi image nahi bana paya yaar, dobara try karo.")
        else:
            safe_run(send_message, chat_id, "Abhi image nahi bana paya yaar, dobara try karo.", message_id)


def send_photo(chat_id, image_bytes, caption=None, reply_to=None):
    try:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        if reply_to:
            data["reply_to_message_id"] = reply_to
        files = {"photo": ("image.png", image_bytes, "image/png")}
        r = requests.post(TELEGRAM_URL + "/sendPhoto", data=data, files=files, timeout=45)
        return r.json()
    except Exception as e:
        print("SEND PHOTO ERROR: " + str(e))
        return None


def edit_message(chat_id, message_id, text):
    try:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        r = requests.post(TELEGRAM_URL + "/editMessageText", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("EDIT MESSAGE ERROR: " + str(e))
        return None


def delete_message(chat_id, message_id):
    try:
        payload = {"chat_id": chat_id, "message_id": message_id}
        r = requests.post(TELEGRAM_URL + "/deleteMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("DELETE MESSAGE ERROR: " + str(e))
        return None


def pcm_to_wav_bytes(pcm_bytes, channels=1, rate=24000, sample_width=2):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def get_telegram_file_bytes(file_id):
    """Telegram pe upload hui koi bhi photo/file download karta hai."""
    try:
        r = requests.post(TELEGRAM_URL + "/getFile", json={"file_id": file_id}, timeout=15)
        data = r.json()
        file_path = data.get("result", {}).get("file_path")
        if not file_path:
            print("GET FILE ERROR: file_path nahi mila - " + str(data))
            return None
        file_url = "https://api.telegram.org/file/bot" + str(TELEGRAM_TOKEN) + "/" + file_path
        fr = requests.get(file_url, timeout=30)
        if fr.status_code == 200 and fr.content:
            return fr.content
        return None
    except Exception as e:
        print("GET FILE EXCEPTION: " + str(e))
        return None


def analyze_photo_with_question(image_bytes, question):
    """Gemini ki vision capability se photo ko dekhkar sawaal ka jawab deta hai."""
    if not GEMINI_API_KEY:
        return None
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
        b64_image = base64.b64encode(image_bytes).decode()
        prompt_text = SYSTEM_PROMPT + "\n\nYe image dhyan se dekho, jo bhi usme dikh raha hai (log, cheezein, expressions, text) sab samjho, phir is sawaal/comment ka seedha relevant jawab do: " + question
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}},
                    {"text": prompt_text}
                ]
            }]
        }
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        data = r.json()
        candidates = data.get("candidates")
        if not candidates:
            print("PHOTO ANALYSIS ERROR (status " + str(r.status_code) + "): " + str(data))
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception as e:
        print("PHOTO ANALYSIS EXCEPTION: " + str(e))
        return None


def generate_tts(text):
    """Bot ki apni reply text ko voice mein convert karta hai (Gemini TTS - Hindi/Hinglish
    natively samajhta hai). Real copyrighted gaano ke lyrics ke liye use nahi hota - sirf
    bot ki khud ki generated lines ke liye."""
    if not GEMINI_API_KEY:
        print("TTS SKIP: GEMINI_API_KEY set nahi hai")
        return None
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
        prompt_text = "Ise natural Hinglish (Hindi-English mix) mein, ek dost jaise casual tone mein Hindi accent ke saath bolo: " + text[:500]
        payload = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}}
            }
        }
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        data = r.json()
        candidates = data.get("candidates")
        if not candidates:
            print("GEMINI TTS ERROR (status " + str(r.status_code) + "): " + str(data))
            return None

        audio_b64 = None
        for p in candidates[0].get("content", {}).get("parts", []):
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                audio_b64 = inline["data"]
                break

        if not audio_b64:
            print("GEMINI TTS: audio data nahi mila response mein: " + str(data))
            return None

        pcm_bytes = base64.b64decode(audio_b64)
        return pcm_to_wav_bytes(pcm_bytes)
    except Exception as e:
        print("TTS EXCEPTION: " + str(e))
        return None


def send_voice(chat_id, audio_bytes, caption=None, reply_to=None):
    try:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        if reply_to:
            data["reply_to_message_id"] = reply_to
        files = {"audio": ("voice.wav", audio_bytes, "audio/wav")}
        r = requests.post(TELEGRAM_URL + "/sendAudio", data=data, files=files, timeout=30)
        return r.json()
    except Exception as e:
        print("SEND VOICE ERROR: " + str(e))
        return None


def send_message_with_keyboard(chat_id, text, keyboard, reply_to=None):
    try:
        payload = {"chat_id": chat_id, "text": text, "reply_markup": keyboard}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(TELEGRAM_URL + "/sendMessage", json=payload, timeout=10)
    except Exception as e:
        print("SEND KEYBOARD ERROR: " + str(e))


@app.route('/')
def home():
    return "Bot is running!"


if __name__ == '__main__':
    load_state()
    threading.Thread(target=lucky_scheduler_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
