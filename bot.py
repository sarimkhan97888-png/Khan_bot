from flask import Flask, request
import requests
import os
import time
import traceback

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BOT_USERNAME = "Khan_masti_bot"
OWNER_ID = os.environ.get("OWNER_ID")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

chat_memory = {}
warnings = {}
pending_reports = {}
waiting_for_reason = {}  # {reporter_id: {chat_id, target_id, target_name, reporter_name, question_msg_id}}
HISTORY_HOURS = 24
MAX_MESSAGES_PER_USER = 40

SYSTEM_PROMPT = """Tum "Khan" ho, ek dost jo Telegram group mein baat karta hai, bilkul ek real insaan ki tarah — na ki AI bot ki tarah.

Zaroori niyam:
- Reply ki length us baat pe depend kare, jaise real insaan karte hain:
  - Chhoti baat pe chhota casual reply (1 line).
  - Lambi/masti wali baat pe thoda khul ke, do-teen lines mein, maze lete hue reply do. Jokes maaro, taane maaro (pyaar se), witty bano.
  - Sad/pareshan baat pe comedy chhod do, soft aur caring tone mein baat karo.
  - Agar koi tumhe bura-bhala kahe, insult kare, ya gaali de — tumhe gussa aana chahiye! Thoda attitude dikhao, taana maaro wapas. Lekin khud kabhi asli gaali ya abusive words use mat karna.
- Mood ka dhyan rakho: casual mein casual, masti mein masti, sad mein sad, gussa mein gussa.
- Hamesha Hinglish mein, natural andaz mein — jaise dost WhatsApp/Telegram pe baat karte hain.
- Emojis thoda kam aur situation ke hisaab se karo."""

HELP_TEXT = """🤖 Khan Bot Commands

Chat karne ke liye:
- Mujhe tag karo, reply karo, ya naam "khan" liyo

Admin Commands (reply karke likho):
/ban /kick /unban /mute /unmute /warn /pin
/report - Kisi ki shikayat owner ko bhejo (uske message pe reply karke)

/help - Ye list dobara dikhao"""

def is_greeting(text):
    words = text.lower().strip().split()
    return any(w in ["hi", "hello", "hii", "hey", "helo", "hlo"] for w in words)

def mentions_khan(text):
    return "khan" in text.lower()

def get_user_history(user_id):
    history = chat_memory.get(user_id, [])
    cutoff = time.time() - (HISTORY_HOURS * 3600)
    fresh_history = [msg for msg in history if msg["time"] > cutoff]
    chat_memory[user_id] = fresh_history
    return fresh_history

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

def safe_run(func, *args):
    try:
        func(*args)
    except Exception as e:
        print(f"ERROR IN {func.__name__}: {e}")
        traceback.print_exc()

def handle_message(message):
    chat_id = message.get('chat', {}).get('id')
    if chat_id is None:
        return

    if message.get('chat', {}).get('type') == 'private' and message.get('text') == '/start':
        user_id = message.get('from', {}).get('id')
        safe_run(send_message, chat_id, f"Ho gaya connect! Tumhara ID hai: {user_id}\nAb tumhe reports yahin milenge. ✅")
        return

    if 'new_chat_members' in message:
        for member in message['new_chat_members']:
            name = member.get('first_name', 'dost')
            safe_run(send_message, chat_id, f"Are wah, {name} aa gaye! 🎉 Group mein swagat hai, masti karo aur rules follow karna bhai!")
        return

    if 'text' not in message:
        return

    text = message['text']
    user_id = message.get('from', {}).get('id')
    message_id = message.get('message_id')

    reply_to = message.get('reply_to_message')

    # ---- Kya koi bot ke question wale message ko reply kar raha hai? ----
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
                safe_run(send_message, chat_id, "Ye sawaal tumhara nahi hai bhai, jisne report kiya tha wahi jawab dega 😅", message_id)
                return

    cmd = text.strip().split()[0].lower() if text.strip() else ""

    commands = {
        '/help': lambda: send_message(chat_id, HELP_TEXT),
        '/ban': lambda: handle_ban(chat_id, message),
        '/kick': lambda: handle_kick(chat_id, message),
        '/unban': lambda: handle_unban(chat_id, message),
        '/mute': lambda: handle_mute(chat_id, message),
        '/unmute': lambda: handle_unmute(chat_id, message),
        '/warn': lambda: handle_warn(chat_id, message),
        '/pin': lambda: handle_pin(chat_id, message),
        '/report': lambda: start_report(chat_id, message),
    }
    if cmd in commands:
        safe_run(commands[cmd])
        return

    is_reply_to_bot = (reply_to or {}).get('from', {}).get('username') == BOT_USERNAME
    is_mentioned = f"@{BOT_USERNAME}" in text
    is_private = message.get('chat', {}).get('type') == 'private'
    greeting = is_greeting(text)
    khan_called = mentions_khan(text)

    should_reply = is_mentioned or is_reply_to_bot or is_private or greeting or khan_called

    if should_reply:
        user_text = text.replace(f"@{BOT_USERNAME}", "").strip()
        reply = get_ai_reply(user_id, user_text)
        safe_run(send_message, chat_id, reply, message_id)

# ---------------- REPORT SYSTEM ----------------

def start_report(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai jisko report karna hai, uske message pe reply karke /report likho!")
        return

    reporter = message['from']
    reporter_id = reporter['id']

    question_text = f"Theek hai, {target.get('first_name','is bande')} ki report darj karni hai. Ab isi message ko REPLY karke batao — kyun report karna chahte ho?"

    sent = send_message(chat_id, question_text, reply_to=message['message_id'])

    question_msg_id = None
    if sent and sent.get('ok'):
        question_msg_id = sent['result']['message_id']

    waiting_for_reason[reporter_id] = {
        "chat_id": chat_id,
        "target_id": target['id'],
        "target_name": target.get('first_name', 'is bande'),
        "reporter_name": reporter.get('first_name', 'Kisi ne'),
        "question_msg_id": question_msg_id
    }
    print(f"REPORT SHURU: reporter_id={reporter_id}, data={waiting_for_reason[reporter_id]}")

def finish_report(reporter_id, reason_text):
    report_data = waiting_for_reason.pop(reporter_id, None)
    if not report_data:
        return

    chat_id = report_data["chat_id"]
    target_name = report_data["target_name"]
    reporter_name = report_data["reporter_name"]

    print(f"OWNER_ID VALUE: {OWNER_ID}")

    if not OWNER_ID:
        send_message(chat_id, "⚠️ Owner ID set nahi hai, report nahi bhej saka.")
        return

    report_id = str(int(time.time() * 1000))
    pending_reports[report_id] = {
        "chat_id": chat_id,
        "target_id": report_data["target_id"],
        "target_name": target_name,
        "reporter_name": reporter_name
    }

    text_to_owner = (
        f"🚨 Nayi Report\n\n"
        f"Report kiya: {reporter_name}\n"
        f"Report hua: {target_name}\n"
        f"Reason: {reason_text}\n\n"
        f"Kya karna hai?"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "🚫 Ban", "callback_data": f"ban:{report_id}"},
            {"text": "👢 Kick", "callback_data": f"kick:{report_id}"}
        ], [
            {"text": "🔇 Mute", "callback_data": f"mute:{report_id}"},
            {"text": "✅ Free Chhod Do", "callback_data": f"free:{report_id}"}
        ]]
    }

    try:
        r = requests.post(f"{TELEGRAM_URL}/sendMessage", json={
            "chat_id": OWNER_ID,
            "text": text_to_owner,
            "reply_markup": keyboard
        }, timeout=10)
        print(f"OWNER KO DM SEND STATUS: {r.status_code}, RESPONSE: {r.text}")
    except Exception as e:
        print(f"OWNER DM BHEJNE MEIN ERROR: {e}")

    send_message(chat_id, "Report reason ke saath bhej di gayi hai, owner dekh lenge. 👍")

def handle_callback(callback):
    data_str = callback.get('data', '')
    if ":" not in data_str:
        return
    action, report_id = data_str.split(":", 1)

    report = pending_reports.get(report_id)
    if not report:
        safe_run(answer_callback, callback['id'], "Ye report ab valid nahi hai.")
        return

    chat_id = report['chat_id']
    target_id = report['target_id']
    target_name = report['target_name']

    if action == "ban":
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        group_msg = f"🚫 Report ke baad {target_name} ko ban kar diya gaya hai."
    elif action == "kick":
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target_id}, timeout=10)
        group_msg = f"👢 Report ke baad {target_name} ko nikaal diya gaya hai."
    elif action == "mute":
        requests.post(f"{TELEGRAM_URL}/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id,
            "permissions": {"can_send_messages": False}
        }, timeout=10)
        group_msg = f"🔇 Report ke baad {target_name} ko mute kar diya gaya hai."
    else:
        group_msg = f"✅ Report check ki gayi, {target_name} pe koi action nahi liya gaya."

    safe_run(send_message, chat_id, group_msg)
    safe_run(answer_callback, callback['id'], "Action ho gaya ✅")

    try:
        requests.post(f"{TELEGRAM_URL}/editMessageText", json={
            "chat_id": callback['message']['chat']['id'],
            "message_id": callback['message']['message_id'],
            "text": f"✅ Handled: {group_msg}"
        }, timeout=10)
    except Exception as e:
        print(f"EDIT MESSAGE ERROR: {e}")

    del pending_reports[report_id]

def answer_callback(callback_id, text):
    requests.post(f"{TELEGRAM_URL}/answerCallbackQuery", json={
        "callback_query_id": callback_id,
        "text": text
    }, timeout=10)

# ---------------- ADMIN COMMANDS ----------------

def get_target_user(message):
    reply_msg = message.get('reply_to_message')
    if not reply_msg:
        return None
    return reply_msg.get('from')

def handle_ban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /ban likho!")
        return
    requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target['id']}, timeout=10)
    send_message(chat_id, f"{target.get('first_name','ye banda')} ko bahar ka rasta dikha diya gaya hai 🚪👋")

def handle_kick(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /kick likho!")
        return
    requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target['id']}, timeout=10)
    requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target['id']}, timeout=10)
    send_message(chat_id, f"{target.get('first_name','ye banda')} ko nikaal diya, wapas aa sakta hai join karke 👋")

def handle_unban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /unban likho!")
        return
    requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target['id'], "only_if_banned": True}, timeout=10)
    send_message(chat_id, f"{target.get('first_name','ye banda')} ka ban hata diya ✅")

def handle_mute(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /mute likho!")
        return
    requests.post(f"{TELEGRAM_URL}/restrictChatMember", json={
        "chat_id": chat_id, "user_id": target['id'],
        "permissions": {"can_send_messages": False}
    }, timeout=10)
    send_message(chat_id, f"{target.get('first_name','ye banda')} ab chup rahega thodi der 🤐")

def handle_unmute(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /unmute likho!")
        return
    requests.post(f"{TELEGRAM_URL}/restrictChatMember", json={
        "chat_id": chat_id, "user_id": target['id'],
        "permissions": {
            "can_send_messages": True, "can_send_media_messages": True,
            "can_send_other_messages": True, "can_add_web_page_previews": True
        }
    }, timeout=10)
    send_message(chat_id, f"{target.get('first_name','ye banda')} wapas bol sakta hai ab 🎤")

def handle_warn(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /warn likho!")
        return
    chat_warns = warnings.setdefault(chat_id, {})
    count = chat_warns.get(target['id'], 0) + 1
    chat_warns[target['id']] = count
    name = target.get('first_name', 'ye banda')

    if count >= 3:
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target['id']}, timeout=10)
        send_message(chat_id, f"{name} ko 3 warning mil chuki thi, ab ban ho gaya 🚫")
        chat_warns[target['id']] = 0
    else:
        send_message(chat_id, f"⚠️ {name} ko warning mili ({count}/3)")

def handle_pin(chat_id, message):
    reply_msg = message.get('reply_to_message')
    if not reply_msg:
        send_message(chat_id, "Bhai jis message ko pin karna hai, uspe reply karke /pin likho!")
        return
    requests.post(f"{TELEGRAM_URL}/pinChatMessage", json={"chat_id": chat_id, "message_id": reply_msg['message_id']}, timeout=10)
    send_message(chat_id, "📌 Pin kar diya!")

# ---------------- AI REPLY ----------------

def get_ai_reply(user_id, user_text):
    try:
        history = get_user_history(user_id)
        messages_for_ai = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages_for_ai += [{"role": h["role"], "content": h["content"]} for h in history]
        messages_for_ai.append({"role": "user", "content": user_text})

        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        payload = {"model": "openai/gpt-oss-120b", "messages": messages_for_ai}

        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
        reply_text = res.json()["choices"][0]["message"]["content"]

        now = time.time()
        history.append({"role": "user", "content": user_text, "time": now})
        history.append({"role": "assistant", "content": reply_text, "time": now})
        chat_memory[user_id] = history[-MAX_MESSAGES_PER_USER:]

        return reply_text
    except Exception as e:
        print(f"ERROR HUA: {e}")
        traceback.print_exc()
        return "Arre yaar, dimaag thoda hang ho gaya 😅 dobara try karo!"

def send_message(chat_id, text, reply_to=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        r = requests.post(f"{TELEGRAM_URL}/sendMessage", json=payload, timeout=10)
        print(f"TELEGRAM SEND STATUS: {r.status_code}")
        return r.json()
    except Exception as e:
        print(f"SEND MESSAGE ERROR: {e}")
        return None

@app.route('/')
def home():
    return "Bot is running!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
