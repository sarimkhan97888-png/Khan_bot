from flask import Flask, request
import requests
import os
import time
import traceback
import re

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BOT_USERNAME = "Khan_masti_bot"
OWNER_ID = os.environ.get("OWNER_ID")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

chat_memory = {}
warnings = {}
pending_reports = {}
waiting_for_reason = {}
known_chats = {}
group_settings = {}
panel_state = {}  # {owner_id: {"stage":..., "chat_id":..., "target_id":..., "target_name":...}}
waiting_for_welcome = {}

HISTORY_HOURS = 24
MAX_MESSAGES_PER_USER = 40

SYSTEM_PROMPT = """Tum "Khan" ho, ek dost jo Telegram group mein baat karta hai, bilkul ek real insaan ki tarah.

Zaroori niyam:
- Kabhi bhi gyaan mat do, lecture mat do, advice deke bore mat karo. Tum ek masti karne wala dost ho, teacher nahi.
- HAR REPLY MAXIMUM 2 LINES KA HONA CHAHIYE. Kabhi bhi isse zyada lamba mat likho.
- Chhoti baat pe 1 line ka casual reply do.
- Masti wali baat pe thoda taana maaro, witty bano, halka-fulka maza lo — lekin phir bhi 2 line se zyada nahi.
- Sad/pareshan baat pe soft tone rakho, lekin chhota hi reply do.
- Koi insult kare to thoda attitude dikhao, taana maaro — bina gaali ke.
- Hamesha Hinglish, natural, jaise dost chat karte hain — kabhi formal ya robotic mat lagna."""

DEFAULT_WELCOME = "Are wah, {name} aa gaye! 🎉 Group mein swagat hai, masti karo aur rules follow karna bhai!"
LINK_PATTERN = re.compile(r'(https?://|www\.|t\.me/|telegram\.me/)', re.IGNORECASE)

def is_greeting(text):
    words = text.lower().strip().split()
    return any(w in ["hi", "hello", "hii", "hey", "helo", "hlo"] for w in words)

def get_user_history(user_id):
    history = chat_memory.get(user_id, [])
    cutoff = time.time() - (HISTORY_HOURS * 3600)
    fresh_history = [msg for msg in history if msg["time"] > cutoff]
    chat_memory[user_id] = fresh_history
    return fresh_history

def get_settings(chat_id):
    return group_settings.setdefault(chat_id, {"welcome": DEFAULT_WELCOME, "link_filter": True})

def safe_run(func, *args):
    try:
        func(*args)
    except Exception as e:
        print(f"ERROR IN {getattr(func, '__name__', 'func')}: {e}")
        traceback.print_exc()

# ==================== WEBHOOK ====================

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return {"ok": True}
        print(f"UPDATE AAYA: {data}")

        if 'callback_query' in data:
            safe_run(handle_callback, data['callback_query'])
            return {"ok": True}
        if 'message' not in data:
            return {"ok": True}

        handle_message(data['message'])
        return {"ok": True}
    except Exception as e:
        print(f"WEBHOOK CRASH BACHAYA: {e}")
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
            safe_run(send_message, chat_id, f"Connected! Tumhara ID: {user_id}\n/panel likho group control karne ke liye.")
            return

        if str(user_id) != str(OWNER_ID):
            return  # sirf owner hi DM se panel use kar sakta hai

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
                safe_run(send_message, target_chat, text)
                safe_run(send_message, chat_id, "Broadcast bhej diya gaya group mein ✅")
                panel_state.pop(user_id, None)
                return
            if stage == 'await_welcome':
                target_chat = state['chat_id']
                get_settings(target_chat)['welcome'] = text
                safe_run(send_message, chat_id, "Naya welcome message set ho gaya ✅")
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
        safe_run(send_message, chat_id, "Naya welcome message set ho gaya! ✅")
        return

    if settings.get('link_filter', True) and LINK_PATTERN.search(text):
        if not safe_check_admin(chat_id, user_id):
            try:
                requests.post(f"{TELEGRAM_URL}/deleteMessage", json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
                name = message.get('from', {}).get('first_name', 'Bhai')
                safe_run(send_message, chat_id, f"{name}, yahan link allowed nahi hai bhai 🚫")
            except Exception as e:
                print(f"LINK DELETE ERROR: {e}")
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
                safe_run(send_message, chat_id, "Ye sawaal tumhara nahi hai bhai 😅", message_id)
                return

    cmd = text.strip().split()[0].lower() if text.strip() else ""

    if cmd == '/help':
        safe_run(send_message, chat_id, HELP_TEXT())
        return
    if cmd == '/ban':
        safe_run(handle_ban, chat_id, message); return
    if cmd == '/kick':
        safe_run(handle_kick, chat_id, message); return
    if cmd == '/unban':
        safe_run(handle_unban, chat_id, message); return
    if cmd == '/mute':
        safe_run(handle_mute, chat_id, message); return
    if cmd == '/unmute':
        safe_run(handle_unmute, chat_id, message); return
    if cmd == '/warn':
        safe_run(handle_warn, chat_id, message); return
    if cmd == '/pin':
        safe_run(handle_pin, chat_id, message); return
    if cmd == '/report':
        safe_run(start_report, chat_id, message); return
    if cmd == '/setwelcome':
        waiting_for_welcome[user_id] = chat_id
        safe_run(send_message, chat_id, "Ab agla message bhejo jo naya welcome text hoga. {name} likhoge wahan naam aayega.", message_id)
        return
    if cmd == '/linkson':
        settings['link_filter'] = True
        safe_run(send_message, chat_id, "Link filter ON ✅"); return
    if cmd == '/linksoff':
        settings['link_filter'] = False
        safe_run(send_message, chat_id, "Link filter OFF"); return

    is_reply_to_bot = (reply_to or {}).get('from', {}).get('username') == BOT_USERNAME
    is_mentioned = f"@{BOT_USERNAME}" in text
    greeting = is_greeting(text)
    should_reply = is_mentioned or is_reply_to_bot or greeting

    if should_reply:
        user_text = text.replace(f"@{BOT_USERNAME}", "").strip()
        reply = get_ai_reply(user_id, user_text)
        safe_run(send_message, chat_id, reply, message_id)

def HELP_TEXT():
    return """🤖 Khan Bot Commands

Chat: mujhe reply karo ya tag karo

Admin (reply karke):
/ban /kick /unban /mute /unmute /warn /pin
/report - shikayat bhejo

Settings:
/setwelcome /linkson /linksoff

/help - ye list"""

def safe_check_admin(chat_id, user_id):
    try:
        r = requests.get(f"{TELEGRAM_URL}/getChatMember", params={"chat_id": chat_id, "user_id": user_id}, timeout=10)
        result = r.json()
        return result.get('result', {}).get('status', '') in ('administrator', 'creator')
    except Exception as e:
        print(f"ADMIN CHECK ERROR: {e}")
        return False

# ==================== REPORT ====================

def start_report(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /report likho!")
        return
    reporter = message['from']
    reporter_id = reporter['id']
    question_text = f"Theek hai, {target.get('first_name','is bande')} ki report darj karni hai. Isi message ko REPLY karke batao — kyun report karna hai?"
    sent = send_message(chat_id, question_text, reply_to=message['message_id'])
    question_msg_id = sent['result']['message_id'] if sent and sent.get('ok') else None
    waiting_for_reason[reporter_id] = {
        "chat_id": chat_id, "target_id": target['id'],
        "target_name": target.get('first_name', 'is bande'),
        "reporter_name": reporter.get('first_name', 'Kisi ne'),
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
        send_message(chat_id, "⚠️ Owner ID set nahi hai.")
        return

    report_id = str(int(time.time() * 1000))
    pending_reports[report_id] = {
        "chat_id": chat_id, "target_id": report_data["target_id"],
        "target_name": target_name, "reporter_name": reporter_name
    }
    text_to_owner = f"🚨 Nayi Report\n\nReport kiya: {reporter_name}\nReport hua: {target_name}\nReason: {reason_text}"
    keyboard = {"inline_keyboard": [[
        {"text": "🚫 Ban", "callback_data": f"ban:{report_id}"},
        {"text": "👢 Kick", "callback_data": f"kick:{report_id}"}
    ], [
        {"text": "🔇 Mute", "callback_data": f"mute:{report_id}"},
        {"text": "✅ Free Chhod Do", "callback_data": f"free:{report_id}"}
    ]]}
    try:
        requests.post(f"{TELEGRAM_URL}/sendMessage", json={"chat_id": OWNER_ID, "text": text_to_owner, "reply_markup": keyboard}, timeout=10)
    except Exception as e:
        print(f"OWNER DM ERROR: {e}")
    send_message(chat_id, "Report bhej di gayi hai. 👍")

# ==================== OWNER CONTROL PANEL ====================

def show_panel_groups(chat_id):
    if not known_chats:
        send_message(chat_id, "Abhi koi group activity nahi mili. Group mein pehle koi message aane do.")
        return
    buttons = [[{"text": title, "callback_data": f"panelgrp:{gid}"}] for gid, title in known_chats.items()]
    send_message_with_keyboard(chat_id, "Konsa group control karna hai?", {"inline_keyboard": buttons})

def show_group_menu(chat_id, gid):
    """Ye woh full menu hai jisme sab group commands + broadcast + settings hain"""
    title = known_chats.get(gid, "Group")
    settings = get_settings(gid)
    link_status = "ON ✅" if settings.get('link_filter', True) else "OFF ❌"

    buttons = [
        [{"text": "📢 Broadcast Message", "callback_data": f"panelmenu:broadcast:{gid}"}],
        [{"text": "👤 User Action (Ban/Kick/Mute)", "callback_data": f"panelmenu:userselect:{gid}"}],
        [{"text": "✏️ Set Welcome Message", "callback_data": f"panelmenu:welcome:{gid}"}],
        [{"text": f"🔗 Link Filter: {link_status}", "callback_data": f"panelmenu:togglelinks:{gid}"}],
    ]
    send_message_with_keyboard(chat_id, f"*{title}* — kya karna hai?", {"inline_keyboard": buttons})

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
        target_name = fwd.get('first_name', 'User')
    else:
        text = message.get('text', '').strip().lstrip('@')
        try:
            r = requests.get(f"{TELEGRAM_URL}/getChat", params={"chat_id": f"@{text}"}, timeout=10)
            result = r.json()
            if result.get('ok'):
                target_id = result['result']['id']
                target_name = result['result'].get('first_name', text)
        except Exception as e:
            print(f"GETCHAT ERROR: {e}")

    if not target_id:
        send_message(reply_chat, "User nahi mila. Username bhejo (@ ke bina) ya uska message forward karo.")
        return

    panel_state[owner_id] = {"stage": "done", "chat_id": chat_id, "target_id": target_id, "target_name": target_name}

    buttons = [[
        {"text": "🚫 Ban", "callback_data": f"panelact:ban:{chat_id}:{target_id}"},
        {"text": "👢 Kick", "callback_data": f"panelact:kick:{chat_id}:{target_id}"}
    ], [
        {"text": "🔇 Mute", "callback_data": f"panelact:mute:{chat_id}:{target_id}"},
        {"text": "🔊 Unmute", "callback_data": f"panelact:unmute:{chat_id}:{target_id}"}
    ], [
        {"text": "⚠️ Warn", "callback_data": f"panelact:warn:{chat_id}:{target_id}"}
    ]]
    send_message_with_keyboard(reply_chat, f"{target_name} pe kya action lena hai?", {"inline_keyboard": buttons})

# ==================== CALLBACKS ====================

def handle_callback(callback):
    data_str = callback.get('data', '')
    owner_dm_chat_id = callback['message']['chat']['id']
    owner_id = callback['from']['id']

    if data_str.startswith("panelgrp:"):
        gid = int(data_str.split(":")[1])
        safe_run(show_group_menu, owner_dm_chat_id, gid)
        safe_run(answer_callback, callback['id'], "Ok")
        return

    if data_str.startswith("panelmenu:"):
        _, action, gid = data_str.split(":")
        gid = int(gid)
        if action == "broadcast":
            panel_state[owner_id] = {"stage": "await_broadcast", "chat_id": gid}
            safe_run(send_message, owner_dm_chat_id, "Theek hai, ab jo message bhejoge wahi group mein broadcast ho jaayega. Likho:")
        elif action == "userselect":
            panel_state[owner_id] = {"stage": "await_user", "chat_id": gid}
            safe_run(send_message, owner_dm_chat_id, "Us member ka @username bhejo (bina @) ya uska koi message forward karo.")
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
        _, action, gid, target_id = data_str.split(":")
        gid = int(gid)
        target_id = int(target_id)
        result_msg = do_moderation_action(action, gid, target_id)
        safe_run(send_message, owner_dm_chat_id, result_msg)
        safe_run(answer_callback, callback['id'], "Done ✅")
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
    group_msg = do_moderation_action(action, chat_id, target_id, target_name)

    safe_run(send_message, chat_id, group_msg)
    safe_run(answer_callback, callback['id'], "Action ho gaya ✅")
    try:
        requests.post(f"{TELEGRAM_URL}/editMessageText", json={
            "chat_id": owner_dm_chat_id, "message_id": callback['message']['message_id'],
            "text": f"✅ Handled: {group_msg}"
        }, timeout=10)
    except Exception as e:
        print(f"EDIT ERROR: {e}")
    del pending_reports[report_id]

def do_moderation_action(action, chat_id, target_id, target_name=None):
    if not target_name:
        target_name = "ye banda"
    if action == "ban":
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        return f"🚫 {target_name} ko ban kar diya gaya."
    elif action == "kick":
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        return f"👢 {target_name} ko nikaal diya gaya."
    elif action == "mute":
        requests.post(f"{TELEGRAM_URL}/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id, "permissions": {"can_send_messages": False}
        }, timeout=10)
        return f"🔇 {target_name} ko mute kar diya gaya."
    elif action == "unmute":
        requests.post(f"{TELEGRAM_URL}/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id,
            "permissions": {"can_send_messages": True, "can_send_media_messages": True,
                             "can_send_other_messages": True, "can_add_web_page_previews": True}
        }, timeout=10)
        return f"🔊 {target_name} wapas bol sakta hai."
    elif action == "warn":
        chat_warns = warnings.setdefault(chat_id, {})
        count = chat_warns.get(target_id, 0) + 1
        chat_warns[target_id] = count
        if count >= 3:
            requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
            chat_warns[target_id] = 0
            return f"⚠️ {target_name} ki 3 warning ho gayi, ban kar diya 🚫"
        return f"⚠️ {target_name} ko warning di gayi ({count}/3)"
    else:
        return f"✅ {target_name} pe koi action nahi liya gaya."

def answer_callback(callback_id, text):
    requests.post(f"{TELEGRAM_URL}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": text}, timeout=10)

# ==================== ADMIN COMMANDS (GROUP SE) ====================

def get_target_user(message):
    reply_msg = message.get('reply_to_message')
    if not reply_msg:
        return None
    return reply_msg.get('from')

def handle_ban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /ban likho!"); return
    send_message(chat_id, do_moderation_action("ban", chat_id, target['id'], target.get('first_name')))

def handle_kick(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /kick likho!"); return
    send_message(chat_id, do_moderation_action("kick", chat_id, target['id'], target.get('first_name')))

def handle_unban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Kisi ke message pe reply karke /unban likho!"); return
    requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target['id'], "only_if_banned": True}, timeout=10)
    send_message(chat_id, f"{target.get('first_name','ye
