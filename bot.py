from flask import Flask, request
import requests
import os
import time
import traceback
import re

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

Zaroori niyam:
- Kabhi bhi gyaan mat do, lecture mat do, advice deke bore mat karo. Tum ek masti karne wala dost ho, teacher nahi.
- HAR REPLY MAXIMUM 2 LINES KA HONA CHAHIYE. Kabhi bhi isse zyada lamba mat likho.
- Chhoti baat pe 1 line ka casual reply do.
- Masti wali baat pe thoda taana maaro, witty bano, halka-fulka maza lo - lekin phir bhi 2 line se zyada nahi.
- Sad/pareshan baat pe soft tone rakho, lekin chhota hi reply do.
- Koi insult kare to thoda attitude dikhao, taana maaro - bina gaali ke.
- Hamesha Hinglish, natural, jaise dost chat karte hain - kabhi formal ya robotic mat lagna."""

DEFAULT_WELCOME = "Welcome to PROFITIX Community, {name}! Yahan trading tips aur achhi vibes milegi, maza karo aur active raho!"
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
    if not text:
        return

    settings = get_settings(chat_id)

    if 'new_chat_members' in message:
        for member in message['new_chat_members']:
            name = member.get('first_name', 'dost')
            welcome_text = settings['welcome'].replace("{name}", name)
            safe_run(send_message, chat_id, welcome_text)
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
                name = message.get('from', {}).get('first_name', 'Bhai')
                safe_run(send_message, chat_id, name + ", yahan link allowed nahi hai bhai.")
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

        if reply_to:
            quoted_text = reply_to.get('text') or reply_to.get('caption')
            if quoted_text:
                quoted_from = reply_to.get('from', {})
                quoted_name = get_name(quoted_from)
                user_text = quoted_name + ' ne pehle ye likha tha: "' + quoted_text + '"\nUsi message ke reply mein ye bola gaya: "' + user_text + '"'

        reply = get_ai_reply(user_id, user_text)
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

    if data_str.startswith("panelmenu:"):
        parts = data_str.split(":")
        action = parts[1]
        gid = int(parts[2])
        if action == "broadcast":
            panel_state[owner_id] = {"stage": "await_broadcast", "chat_id": gid}
            safe_run(send_message, owner_dm_chat_id, "Theek hai, ab jo message bhejoge wahi group mein broadcast ho jaayega. Likho:")
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


def handle_ban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /ban likho!")
        return
    moderation_action_and_notify("ban", chat_id, target['id'], get_name(target), chat_id)


def handle_kick(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /kick likho!")
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
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
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
                    "content": "Web se ye latest info mili hai, isi ke aadhar pe apne dost wale 2-line Hinglish andaz mein jawab do: " + web_info
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


def send_message(chat_id, text, reply_to=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        r = requests.post(TELEGRAM_URL + "/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("SEND ERROR: " + str(e))
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
