from flask import Flask, request
import requests
import os
import time
import json

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BOT_USERNAME = "Khan_masti_bot"
OWNER_ID = os.environ.get("OWNER_ID")  # tumhara personal Telegram user ID

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

chat_memory = {}
warnings = {}
pending_reports = {}  # {report_id: {chat_id, target_id, target_name, reporter_name}}
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

HELP_TEXT = """🤖 *Khan Bot Commands*

*Chat karne ke liye:*
- Mujhe tag karo, reply karo, ya naam "khan" liyo

*Admin Commands (reply karke likho):*
/ban /kick /unban /mute /unmute /warn /pin
/report - Kisi ki shikayat owner ko bhejo

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
    data = request.get_json()
    print(f"UPDATE AAYA: {data}")

    # ---- Button dabane wala event ----
    if 'callback_query' in data:
        handle_callback(data['callback_query'])
        return {"ok": True}

    if 'message' not in data:
        return {"ok": True}

    message = data['message']
    chat_id = message['chat']['id']

    if message['chat']['type'] == 'private' and message.get('text') == '/start':
        send_message(chat_id, "Ho gaya connect! Ab tumhe reports aur alerts yahin milenge. ✅")
        return {"ok": True}

    if 'new_chat_members' in message:
        for member in message['new_chat_members']:
            name = member.get('first_name', 'dost')
            send_message(chat_id, f"Are wah, {name} aa gaye! 🎉 Group mein swagat hai, masti karo aur rules follow karna bhai!")
        return {"ok": True}

    if 'text' not in message:
        return {"ok": True}

    text = message['text']
    user_id = message['from']['id']
    message_id = message['message_id']
    cmd = text.strip().split()[0].lower()

    commands = {
        '/help': lambda: send_message(chat_id, HELP_TEXT),
        '/ban': lambda: handle_ban(chat_id, message),
        '/kick': lambda: handle_kick(chat_id, message),
        '/unban': lambda: handle_unban(chat_id, message),
        '/mute': lambda: handle_mute(chat_id, message),
        '/unmute': lambda: handle_unmute(chat_id, message),
        '/warn': lambda: handle_warn(chat_id, message),
        '/pin': lambda: handle_pin(chat_id, message),
        '/report': lambda: handle_report(chat_id, message),
    }
    if cmd in commands:
        commands[cmd]()
        return {"ok": True}

    is_reply_to_bot = message.get('reply_to_message', {}).get('from', {}).get('username') == BOT_USERNAME
    is_mentioned = f"@{BOT_USERNAME}" in text
    is_private = message['chat']['type'] == 'private'
    greeting = is_greeting(text)
    khan_called = mentions_khan(text)

    should_reply = is_mentioned or is_reply_to_bot or is_private or greeting or khan_called

    if should_reply:
        user_text = text.replace(f"@{BOT_USERNAME}", "").strip()
        reply = get_ai_reply(user_id, user_text)
        send_message(chat_id, reply, reply_to=message_id)

    return {"ok": True}

# ---------------- REPORT SYSTEM ----------------

def handle_report(chat_id, message):
    if not OWNER_ID:
        send_message(chat_id, "Owner ID set nahi hai abhi, thodi der mein try karo.")
        return

    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai jisko report karna hai, uske message pe reply karke /report likho!")
        return

    reporter = message['from']
    reporter_name = reporter.get('first_name', 'Kisi ne')
    target_name = target.get('first_name', 'is bande')

    report_id = str(int(time.time() * 1000))
    pending_reports[report_id] = {
        "chat_id": chat_id,
        "target_id": target['id'],
        "target_name": target_name,
        "reporter_name": reporter_name
    }

    text_to_owner = f"🚨 *Nayi Report*\n\n{reporter_name} ne {target_name} ko report kiya hai.\n\nKya karna hai?"

    keyboard = {
        "inline_keyboard": [[
            {"text": "🚫 Ban", "callback_data": f"ban:{report_id}"},
            {"text": "👢 Kick", "callback_data": f"kick:{report_id}"}
        ], [
            {"text": "🔇 Mute", "callback_data": f"mute:{report_id}"},
            {"text": "✅ Free Chhod Do", "callback_data": f"free:{report_id}"}
        ]]
    }

    requests.post(f"{TELEGRAM_URL}/sendMessage", json={
        "chat_id": OWNER_ID,
        "text": text_to_owner,
        "parse_mode": "Markdown",
        "reply_markup": keyboard
    })

    send_message(chat_id, "Report bhej di gayi hai, owner dekh lenge. 👍")

def handle_callback(callback):
    data_str = callback['data']  # jaise "ban:12345"
    action, report_id = data_str.split(":")

    report = pending_reports.get(report_id)
    if not report:
        answer_callback(callback['id'], "Ye report ab valid nahi hai.")
        return

    chat_id = report['chat_id']
    target_id = report['target_id']
    target_name = report['target_name']

    if action == "ban":
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target_id})
        group_msg = f"🚫 Report ke baad {target_name} ko ban kar diya gaya hai."
    elif action == "kick":
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target_id})
        requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target_id})
        group_msg = f"👢 Report ke baad {target_name} ko nikaal diya gaya hai."
    elif action == "mute":
        requests.post(f"{TELEGRAM_URL}/restrictChatMember", json={
            "chat_id": chat_id, "user_id": target_id,
            "permissions": {"can_send_messages": False}
        })
        group_msg = f"🔇 Report ke baad {target_name} ko mute kar diya gaya hai."
    else:  # free
        group_msg = f"✅ Report check ki gayi, {target_name} pe koi action nahi liya gaya."

    send_message(chat_id, group_msg)
    answer_callback(callback['id'], "Action ho gaya ✅")

    # Owner ke message ko update karo taaki dubara button na dabe
    requests.post(f"{TELEGRAM_URL}/editMessageText", json={
        "chat_id": callback['message']['chat']['id'],
        "message_id": callback['message']['message_id'],
        "text": f"✅ Handled: {group_msg}"
    })

    del pending_reports[report_id]

def answer_callback(callback_id, text):
    requests.post(f"{TELEGRAM_URL}/answerCallbackQuery", json={
        "callback_query_id": callback_id,
        "text": text
    })

# ---------------- ADMIN COMMANDS ----------------

def get_target_user(message):
    reply_msg = message.get('reply_to_message')
    if not reply_msg:
        return None
    return reply_msg['from']

def handle_ban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /ban likho!")
        return
    requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target['id']})
    send_message(chat_id, f"{target.get('first_name','ye banda')} ko bahar ka rasta dikha diya gaya hai 🚪👋")

def handle_kick(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /kick likho!")
        return
    requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target['id']})
    requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target['id']})
    send_message(chat_id, f"{target.get('first_name','ye banda')} ko nikaal diya, wapas aa sakta hai join karke 👋")

def handle_unban(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /unban likho!")
        return
    requests.post(f"{TELEGRAM_URL}/unbanChatMember", json={"chat_id": chat_id, "user_id": target['id'], "only_if_banned": True})
    send_message(chat_id, f"{target.get('first_name','ye banda')} ka ban hata diya ✅")

def handle_mute(chat_id, message):
    target = get_target_user(message)
    if not target:
        send_message(chat_id, "Bhai kisi ke message pe reply karke /mute likho!")
        return
    requests.post(f"{TELEGRAM_URL}/restrictChatMember", json={
        "chat_id": chat_id, "user_id": target['id'],
        "permissions": {"can_send_messages": False}
    })
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
    })
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
        requests.post(f"{TELEGRAM_URL}/banChatMember", json={"chat_id": chat_id, "user_id": target['id']})
        send_message(chat_id, f"{name} ko 3 warning mil chuki thi, ab ban ho gaya 🚫")
        chat_warns[target['id']] = 0
    else:
        send_message(chat_id, f"⚠️ {name} ko warning mili ({count}/3)")

def handle_pin(chat_id, message):
    reply_msg = message.get('reply_to_message')
    if not reply_msg:
        send_message(chat_id, "Bhai jis message ko pin karna hai, uspe reply karke /pin likho!")
        return
    requests.post(f"{TELEGRAM_URL}/pinChatMessage", json={"chat_id": chat_id, "message_id": reply_msg['message_id']})
    send_message(chat_id, "📌 Pin kar diya!")

# ---------------- AI REPLY ----------------

def get_ai_reply(user_id, user_text):
    history = get_user_history(user_id)
    messages_for_ai = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages_for_ai += [{"role": h["role"], "content": h["content"]} for h in history]
    messages_for_ai.append({"role": "user", "content": user_text})

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {"model": "openai/gpt-oss-120b", "messages": messages_for_ai}
    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
        reply_text = res.json()["choices"][0]["message"]["content"]

        now = time.time()
        history.append({"role": "user", "content": user_text, "time": now})
        history.append({"role": "assistant", "content": reply_text, "time": now})
        chat_memory[user_id] = history[-MAX_MESSAGES_PER_USER:]

        return reply_text
    except Exception as e:
        print(f"ERROR HUA: {e}")
        return "Arre yaar, dimaag thoda hang ho gaya 😅 dobara try karo!"

def send_message(chat_id, text, reply_to=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    r = requests.post(f"{TELEGRAM_URL}/sendMessage", json=payload)
    print(f"TELEGRAM SEND STATUS: {r.status_code}")

@app.route('/')
def home():
    return "Bot is running!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
