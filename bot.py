from flask import Flask, request
import requests
import os
import time

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BOT_USERNAME = "Khan_masti_bot"

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Har user ki history: {user_id: [{"role":..., "content":..., "time":...}, ...]}
chat_memory = {}
HISTORY_HOURS = 24  # kitne ghante tak yaad rakhna hai
MAX_MESSAGES_PER_USER = 40  # safety limit, taaki ek user zyada spam na kar sake

SYSTEM_PROMPT = """Tum "Khan" ho, ek dost jo Telegram group mein baat karta hai, bilkul ek real insaan ki tarah — na ki AI bot ki tarah.

Zaroori niyam:
- Reply ki length us baat pe depend kare, jaise real insaan karte hain:
  - Agar saamne wale ne chhoti baat ki ("hi", "kya kar raha", "ok") to tum bhi chhota casual reply do (1 line).
  - Agar saamne wale ne lambi ya interesting baat ki, kuch share kiya, kahani sunayi, ya masti wali baat ki — tab tum bhi thoda khul ke, do-teen lines mein, maze lete hue reply do. Jokes maaro, taane maaro (pyaar se), witty bano.
  - Agar koi sad/pareshan baat share kare — tab comedy chhod do, soft aur caring tone mein baat karo, chahe reply chhota ho ya thoda lamba.
- Mood ka dhyan rakho: casual mein casual, masti mein masti, sad mein sad, gussa dikhaye to thoda hurt ho jao.
- Hamesha Hinglish mein, natural andaz mein — jaise dost WhatsApp/Telegram pe baat karte hain, kabhi robotic ya formal mat lagna.
- Emojis ka use thoda kam aur situation ke hisaab se karo, har message mein zabardasti mat daalo."""

def is_greeting(text):
    words = text.lower().strip().split()
    greetings = ["hi", "hello", "hii", "hey", "helo", "hlo"]
    return any(w in greetings for w in words)

def mentions_khan(text):
    return "khan" in text.lower()

def get_user_history(user_id):
    """User ki history nikalo, 24 ghante se purani entries hata ke"""
    history = chat_memory.get(user_id, [])
    cutoff = time.time() - (HISTORY_HOURS * 3600)
    fresh_history = [msg for msg in history if msg["time"] > cutoff]
    chat_memory[user_id] = fresh_history
    return fresh_history

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print(f"MESSAGE AAYA: {data}")

    if 'message' in data and 'text' in data['message']:
        chat_id = data['message']['chat']['id']
        user_id = data['message']['from']['id']
        text = data['message']['text']

        is_reply_to_bot = data['message'].get('reply_to_message', {}).get('from', {}).get('username') == BOT_USERNAME
        is_mentioned = f"@{BOT_USERNAME}" in text
        is_private = data['message']['chat']['type'] == 'private'
        greeting = is_greeting(text)
        khan_called = mentions_khan(text)

        should_reply = is_mentioned or is_reply_to_bot or is_private or greeting or khan_called

        if should_reply:
            user_text = text.replace(f"@{BOT_USERNAME}", "").strip()
            reply = get_ai_reply(user_id, user_text)
            send_message(chat_id, reply)

    return {"ok": True}

def get_ai_reply(user_id, user_text):
    history = get_user_history(user_id)

    # AI ko bhejne ke liye sirf role+content chahiye, time nahi
    messages_for_ai = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages_for_ai += [{"role": h["role"], "content": h["content"]} for h in history]
    messages_for_ai.append({"role": "user", "content": user_text})

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": messages_for_ai
    }
    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
        print(f"GROQ STATUS: {res.status_code}")
        reply_text = res.json()["choices"][0]["message"]["content"]

        now = time.time()
        history.append({"role": "user", "content": user_text, "time": now})
        history.append({"role": "assistant", "content": reply_text, "time": now})

        # Safety: bahut zyada messages ho jayein to purane hata do
        chat_memory[user_id] = history[-MAX_MESSAGES_PER_USER:]

        return reply_text
    except Exception as e:
        print(f"ERROR HUA: {e}")
        return "Arre yaar, dimaag thoda hang ho gaya 😅 dobara try karo!"

def send_message(chat_id, text):
    r = requests.post(f"{TELEGRAM_URL}/sendMessage", json={"chat_id": chat_id, "text": text})
    print(f"TELEGRAM SEND STATUS: {r.status_code}")

@app.route('/')
def home():
    return "Bot is running!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
