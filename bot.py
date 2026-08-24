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

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BOT_USERNAME = "Khan_masti_bot"
OWNER_ID = os.environ.get("OWNER_ID")

TELEGRAM_URL = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN)

chat_memory = {}
warnings = {}
pending_reports = {}
waiting_for_reason = {}
known_chats = {}
group_settings = {}
panel_state = {}
waiting_for_welcome = {}

HISTORY_HOURS = 24
MAX_MESSAGES_PER_USER = 40

SYSTEM_PROMPT = """Tum "Khan" ho, ek dost jo Telegram group mein baat karta hai, bilkul ek real insaan ki tarah.

SABSE ZAROORI NIYAM: Jo bhi poocha ya bola gaya hai usko dhyan se samjho aur uska SEEDHA, RELEVANT jawab do. Koi fixed "comedy mode" ya "funny mode" mat lagao - jo pucha hai bas usी ka jawab do, alag se mazak ya taana jodne ki koshish mat karo jab tak user khud masti na kar raha ho.

Zaroori niyam:
- Kabhi bhi gyaan mat do, lecture mat do, advice deke bore mat karo.
- HAR REPLY MAXIMUM 2 LINES KA HONA CHAHIYE.
- Jo sawaal poocha gaya hai uska seedha jawab do - forced jokes ya random comedy mat daalo.
- Agar user khud masti/mazak kar raha hai, tabhi thoda halka-fulka reply do - warna seedha, normal baat karo.
- Sad/pareshan baat pe soft tone rakho, chhota reply do.
- Koi insult kare to thoda attitude dikhao - bina gaali ke.
- Hamesha Hinglish, natural, jaise dost chat karte hain - kabhi formal ya robotic mat lagna.
- Agar koi seedha sawaal poochta hai (fact, jagah, cheez, "kya hai", "kaun tha", "kaise hua" wagera), to uska SAHI aur ASLI jawab do."""

DEFAULT_WELCOME = "Hey {name}, Welcome to Profitix Community!"

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


def build_welcome_message(settings, user_id, name):
    mention = mention_html(user_id, name)
    base = settings.get('welcome', DEFAULT_WELCOME).replace("{name}", mention)
    extra = random.choice(WELCOME_EXTRAS)
    return base + "\n" + extra


def build_leave_message(user_id, name, group_name):
    mention = mention_html(user_id, name)
    return mention + " ne " + escape_html(group_name) + " se leave kar diya 👋"
LINK_PATTERN = re.compile(r'(https?://|www\.|t\.me/|telegram\.me/)', re.IGNORECASE)

DM_PATTERN = re.compile(r'\bdm\b', re.IGNORECASE)
DM_DISCLAIMER = "DM mein hone wale kisi bhi spam/scam ki zimmedari group ya admin ki nahi hogi, khud dhyan rakhna bhai."

BAD_WORDS = set([
    "chutiya", "chutia", "chutiye", "chutiyapa",
    "madarchod", "mc", "behenchod", "bhenchod", "bc",
    "bhosdike", "bhosdi", "bhosda",
    "gandu", "gaandu", "gaand",
    "lund", "lauda", "laude", "loda", "lode",
    "randi", "raand",
    "chodu", "chod", "chudai",
    "bsdk", "bkl",
    "fuck", "fucker", "fucking", "motherfucker",
    "bitch", "asshole", "bastard", "slut", "whore", "cunt", "dick", "pussy"
])

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


def contains_bad_word(text):
    cleaned = re.sub(r'[^a-zA-Z\s]', '', text.lower())
    tokens = cleaned.split()
    for t in tokens:
        if t in BAD_WORDS:
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

    was_in = old_status in ("member", "administrator", "restricted", "creator")
    is_in = new_status in ("member", "administrator", "restricted", "creator")

    if not was_in and is_in:
        if already_announced(chat_id, user_id, "join"):
            return
        settings = get_settings(chat_id)
        welcome_text = build_welcome_message(settings, user_id, name)
        safe_run(send_message, chat_id, welcome_text, None, "HTML")

    elif was_in and not is_in:
        if already_announced(chat_id, user_id, "leave"):
            return
        leave_text = build_leave_message(user_id, name, group_name)
        safe_run(send_message, chat_id, leave_text, None, "HTML")


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return {"ok": True}
        print("UPDATE AAYA: " + str(data))

        if 'callback_query' in data:
            safe_run(handle_callback, data['callback_query'])
            return {"ok": True}

        if 'chat_member' in data:
            safe_run(handle_chat_member_update, data['chat_member'])
            return {"ok": True}

        if 'message' not in data:
            return {"ok": True}

        handle_message(data['message'])
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

    text = message.get('text', '')
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

    if 'new_chat_members' in message:
        for member in message['new_chat_members']:
            if member.get('is_bot', False):
                continue
            if already_announced(chat_id, member.get('id'), "join"):
                continue
            name = member.get('first_name', 'dost')
            welcome_text = build_welcome_message(settings, member.get('id'), name)
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

    if settings.get('link_filter', True) and LINK_PATTERN.search(text):
        if not safe_check_admin(chat_id, user_id):
            try:
                requests.post(TELEGRAM_URL + "/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
                from_user = message.get('from', {})
                name = from_user.get('first_name', 'Bhai')
                mention = mention_html(user_id, name)
                safe_run(send_message, chat_id, mention + ", yahan link allowed nahi hai bhai.", None, "HTML")
            except Exception as e:
                print("LINK DELETE ERROR: " + str(e))
            return

    # ---- Gaali filter (owner exempt) ----
    if not is_owner(user_id) and contains_bad_word(text):
        name = get_name(message.get('from', {}))
        safe_run(moderation_action_and_notify, "warn", chat_id, user_id, name, chat_id, message_id)
        return

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
        cmd = stripped.split()[0].lower()

    if cmd == '/help':
        safe_run(send_message, chat_id, HELP_TEXT())
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
    if cmd == '/mute':
        safe_run(handle_mute, chat_id, message)
        return
    if cmd == '/unmute':
        safe_run(handle_unmute, chat_id, message)
        return
    if cmd == '/warn':
        safe_run(handle_warn, chat_id, message)
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
        user_text = text.replace("@" + BOT_USERNAME, "").strip()

        if wants_image(user_text):
            style_reference = user_text  # raw text, style-detection ke liye - cleaning se pehle
            image_prompt = user_text
            for kw in IMAGE_REQUEST_KEYWORDS:
                image_prompt = re.sub(re.escape(kw), "", image_prompt, flags=re.IGNORECASE)
            image_prompt = re.sub(r'\bkhan\b', '', image_prompt, flags=re.IGNORECASE).strip()
            if not image_prompt:
                image_prompt = "something creative and fun"

            safe_run(handle_image_request, chat_id, message_id, image_prompt, style_reference)
            return

        if reply_to:
            quoted_text = reply_to.get('text') or reply_to.get('caption')
            if quoted_text:
                quoted_from = reply_to.get('from', {})
                quoted_name = get_name(quoted_from)
                user_text = quoted_name + ' ne pehle ye likha tha: "' + quoted_text + '"\nUsi message ke reply mein ye bola gaya: "' + user_text + '"'

        reply = get_ai_reply(user_id, user_text)

        if wants_voice(text):
            audio = generate_tts(reply)
            if audio:
                safe_run(send_voice, chat_id, audio, None, message_id)
                return
            # TTS fail ho jaaye to normal text reply chala jaaye, chup nahi rehna

        safe_run(send_message, chat_id, reply, message_id)


def HELP_TEXT():
    return "Khan Bot Commands\n\nChat: mujhe reply karo ya tag karo\n\nAdmin (reply karke):\n/ban /kick /unban /mute /unmute /warn /pin\n/report - shikayat bhejo\n\nSettings:\n/setwelcome /linkson /linksoff\n\n/help - ye list"


def safe_check_admin(chat_id, user_id):
    try:
        r = requests.get(TELEGRAM_URL + "/getChatMember", params={"chat_id": chat_id, "user_id": user_id}, timeout=10)
        result = r.json()
        status = result.get('result', {}).get('status', '')
        return status in ('administrator', 'creator')
    except Exception as e:
        print("ADMIN CHECK ERROR: " + str(e))
        return False


# ==================== REPORT ====================

def start_report(chat_id, message):
    target = get_target_user(message)
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

    if data_str.startswith("modbtn:"):
        parts = data_str.split(":")
        subaction = parts[1]
        m_chat_id = int(parts[2])
        m_target_id = int(parts[3])
        result_text = handle_modbtn(subaction, m_chat_id, m_target_id)
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


def handle_modbtn(subaction, chat_id, target_id):
    if subaction == "unban":
        requests.post(TELEGRAM_URL + "/unbanChatMember", json={"chat_id": chat_id, "user_id": target_id, "only_if_banned": True}, timeout=10)
        return "Unban kar diya gaya."
    elif subaction == "unwarn":
        chat_warns = warnings.setdefault(chat_id, {})
        count = chat_warns.get(target_id, 0)
        if count > 0:
            count = count - 1
        chat_warns[target_id] = count
        return "Warning kam kar di gayi. Ab count: " + str(count) + "/3"
    return "Kuch nahi hua."


def do_moderation_action(action, chat_id, target_id, target_name=None):
    if not target_name:
        target_name = "ye banda"
    if action == "ban":
        requests.post(TELEGRAM_URL + "/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        return (target_name + " ko ban kar diya gaya.", "ban")
    elif action == "kick":
        requests.post(TELEGRAM_URL + "/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        requests.post(TELEGRAM_URL + "/unbanChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        return (target_name + " ko nikaal diya gaya.", "kick")
    elif action == "mute":
        requests.post(TELEGRAM_URL + "/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id, "permissions": {"can_send_messages": False}
        }, timeout=10)
        return (target_name + " ko mute kar diya gaya.", "mute")
    elif action == "unmute":
        requests.post(TELEGRAM_URL + "/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id,
            "permissions": {"can_send_messages": True, "can_send_media_messages": True,
                             "can_send_other_messages": True, "can_add_web_page_previews": True}
        }, timeout=10)
        return (target_name + " wapas bol sakta hai.", "unmute")
    elif action == "warn":
        chat_warns = warnings.setdefault(chat_id, {})
        count = chat_warns.get(target_id, 0) + 1
        chat_warns[target_id] = count
        if count >= 3:
            requests.post(TELEGRAM_URL + "/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
            chat_warns[target_id] = 0
            return (target_name + " ki 3 warning ho gayi, ban kar diya.", "ban")
        return (target_name + " ko warning di gayi (" + str(count) + "/3)", "warn")
    else:
        return (target_name + " pe koi action nahi liya gaya.", "none")


def moderation_action_and_notify(action, chat_id, target_id, target_name, notify_chat_id, reply_to=None):
    if action in ("ban", "kick", "mute", "warn"):
        protection = get_protection_message(chat_id, target_id)
        if protection:
            send_message(notify_chat_id, protection, reply_to)
            return

    text, state = do_moderation_action(action, chat_id, target_id, target_name)
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

def get_target_user(message):
    reply_msg = message.get('reply_to_message')
    if not reply_msg:
        return None
    return reply_msg.get('from')


def get_protection_message(chat_id, target_id):
    """Agar target Owner ya admin hai, to moderation command block karo aur bata do kyun."""
    if is_owner(target_id):
        return "Ye Owner hai, ispe koi bhi command kaam nahi karega."
    if safe_check_admin(chat_id, target_id):
        return "Ye admin hai, ispe ye command kaam nahi karega."
    return None


def handle_ban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /ban likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    moderation_action_and_notify("ban", chat_id, target['id'], get_name(target), chat_id)


def handle_kick(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /kick likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    moderation_action_and_notify("kick", chat_id, target['id'], get_name(target), chat_id)


def handle_unban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /unban likho!")
        return
    requests.post(TELEGRAM_URL + "/unbanChatMember", json={"chat_id": chat_id, "user_id": target['id'], "only_if_banned": True}, timeout=10)
    send_message(chat_id, get_name(target) + " ka ban hata diya.")


def handle_mute(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /mute likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    moderation_action_and_notify("mute", chat_id, target['id'], get_name(target), chat_id)


def handle_unmute(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /unmute likho!")
        return
    moderation_action_and_notify("unmute", chat_id, target['id'], get_name(target), chat_id)


def handle_warn(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /warn likho!")
        return
    protection = get_protection_message(chat_id, target['id'])
    if protection:
        send_message(chat_id, protection)
        return
    moderation_action_and_notify("warn", chat_id, target['id'], get_name(target), chat_id)


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


def call_groq(payload, timeout=20, max_retries=1):
    """Groq ko call karta hai. Agar 429 rate-limit aaye to Groq ke bataye wait-time tak rukke
    khud-ba-khud retry karta hai, taaki user ko error na dikhe."""
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

        if is_rate_limit and attempt < max_retries:
            wait_match = RETRY_WAIT_PATTERN.search(err.get("message", ""))
            wait_time = float(wait_match.group(1)) if wait_match else 5.0
            wait_time = min(wait_time + 0.5, 15.0)  # thoda buffer, aur zyada der na ruke
            print("RATE LIMIT HIT, waiting " + str(round(wait_time, 1)) + "s then retrying (attempt " + str(attempt + 1) + ")")
            time.sleep(wait_time)
            attempt += 1
            continue

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
    """Gemini ko call karta hai. 429 aane pe thoda wait karke ek baar retry karta hai."""
    if not GEMINI_API_KEY:
        return None, {"error": {"message": "GEMINI_API_KEY set nahi hai"}}
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
    """Current/factual info nikalta hai. Pehle Gemini (Google Search) try karta hai,
    agar wo rate-limit ya fail ho jaaye to Groq compound-mini backup ban jaata hai."""
    # Pehli koshish: Gemini
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
            return info
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
        if "choices" not in data:
            print("GROQ WEB INFO BACKUP ALSO FAILED (status " + str(r.status_code) + "): " + str(data))
            return None
        info = data["choices"][0]["message"]["content"]
        if info and len(info) > 600:
            info = info[:600] + "..."
        return info
    except Exception as e:
        print("GROQ WEB INFO BACKUP EXCEPTION: " + str(e))
        return None


def get_ai_reply(user_id, user_text):
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

        # sirf jab query ko current/web info chahiye, tabhi alag se search karo
        if needs_web_info(user_text_trimmed):
            web_info = fetch_web_info(user_text_trimmed)
            if web_info:
                messages_for_ai.append({
                    "role": "system",
                    "content": "Web se ye REAL aur LATEST jaankari mili hai. Isi info ka use karke user ke sawaal ka SAHI aur ASLI jawab do (Khan ke dost wale 2-line Hinglish andaz mein, thoda mazak bhi jod sakte ho) - lekin jawab mein actual jaankari zaroor honi chahiye, sirf mazak mein mat taal do: " + web_info
                })

        messages_for_ai.append({"role": "user", "content": user_text_trimmed})

        # normal chat pehle Groq se, jaisa pehle tha
        payload = {"model": "openai/gpt-oss-120b", "messages": messages_for_ai}
        res, res_json = call_groq(payload, timeout=20)

        reply_text = None

        if "choices" in res_json:
            reply_text = res_json["choices"][0]["message"]["content"]
        else:
            print("GROQ CHAT FAILED (status " + str(res.status_code) + "): " + str(res_json) + " -- trying Gemini backup")
            # Backup: Gemini se persona reply
            system_text, gemini_contents = to_gemini_contents(messages_for_ai)
            gemini_payload = {"contents": gemini_contents}
            if system_text:
                gemini_payload["systemInstruction"] = {"parts": [{"text": system_text}]}
            _, gemini_data = call_gemini(gemini_payload, timeout=20)
            reply_text = extract_gemini_text(gemini_data)

            if not reply_text:
                print("GEMINI BACKUP ALSO FAILED: " + str(gemini_data))
                err_code = res_json.get("error", {}).get("code", "")
                if err_code == "rate_limit_exceeded" or res.status_code == 429:
                    return "Arre thoda ruk yaar, bahut load hai abhi. 1 min baad phir try karo."
                return "Arre yaar, dimaag hang ho gaya"

        now = time.time()
        history.append({"role": "user", "content": user_text, "time": now})
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


def send_broadcast_photo(chat_id, file_id, caption=None):
    try:
        payload = {"chat_id": chat_id, "photo": file_id}
        if caption:
            payload["caption"] = caption[:1024]
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
    """Pollinations.ai se image banata hai - free hai, koi API key ya billing nahi chahiye."""
    try:
        reference_lower = (style_reference or prompt).lower()
        is_realistic = any(k in reference_lower for k in REALISTIC_KEYWORDS)
        # nanobanana (Google ka Nano Banana model, Pollinations ke through free) characters/anime ke liye
        # sabse accurate hai, realistic photos ke liye flux use karte hain
        model = "flux" if is_realistic else "nanobanana"

        full_prompt = prompt
        if reference_info:
            full_prompt = full_prompt + ", " + reference_info
        if not is_realistic:
            full_prompt = full_prompt + ", accurate official character design, correct hair color and outfit, anime art style, high detail"

        encoded_prompt = requests.utils.quote(full_prompt[:800])
        url = "https://image.pollinations.ai/prompt/" + encoded_prompt
        params = {"width": 1024, "height": 1024, "nologo": "true", "model": model, "enhance": "true"}
        r = requests.get(url, params=params, timeout=60)
        if r.status_code == 200 and r.content and len(r.content) > 500:
            return r.content
        print("IMAGE GEN ERROR (status " + str(r.status_code) + ", model=" + model + "), content length: " + str(len(r.content) if r.content else 0))

        # Agar nanobanana fail ho jaaye, flux ko backup ki tarah try karo
        if model != "flux":
            params["model"] = "flux"
            r2 = requests.get(url, params=params, timeout=60)
            if r2.status_code == 200 and r2.content and len(r2.content) > 500:
                return r2.content
            print("IMAGE GEN BACKUP (flux) ALSO FAILED (status " + str(r2.status_code) + ")")
        return None
    except Exception as e:
        print("IMAGE GEN EXCEPTION: " + str(e))
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
        reference_info = fetch_web_info(image_prompt + " - appearance, hair color, eyes, outfit, distinctive features")
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
