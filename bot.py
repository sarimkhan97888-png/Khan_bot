from flask import Flask, request
import requests
import os

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BOT_USERNAME = "Khan_masti_bot"

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Har user ki chat history yaad rakhne ke liye (memory mein, jab tak bot chalta rahe)
chat_memory = {}
MAX_HISTORY = 6

SYSTEM_PROMPT = """Tum "Khan" ho, ek dost jo Telegram group mein baat karta hai, bilkul ek real insaan ki tarah.

Zaroori niyam:
- Jaisa message tumhe mile, waisa hi mood rakho. Agar koi sad hai, udaas hai, ya dukhi baat share kar raha hai — tum bhi soft, caring aur sad tone mein baat karo. Comedy mat karo jab koi pareshan ho.
- Agar koi normal/casual baat kare to relaxed, chill tone mein reply do.
- Agar koi masti/majaak kare, tab thoda comedy aur witty andaz use karo.
- Agar koi bole "chup raho" ya gussa dikhaye, to thoda hurt/sad ho jao, jaise ek dost hota hai.
- Hamesha Hinglish mein, BAHUT CHHOTE replies do — jyada se jyada 1 line, jaise real casual chat karte hain (jaise "hm bhai kaise ho", "are yaar yaad kiya", "kya hua bata"). Lambe paragraph kabhi mat likho.
- Har message pe ek jaisa reply mat do — thoda variation rakho, jaise insaan alag-alag tareeke se bolta hai."""

def is_greeting(text):
    words = text.lower().strip().split()
    greetings = ["hi", "hello", "hii", "hey", "helo", "hlo"]
    return any(w in greetings for w in words)

def mentions_khan(text):
    return "khan" in text.lower()

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

            if greeting:
                prompt_hint = f"Kisi ne tumhe '{user_text}' bola hai — casual, chhota, dost jaisa greeting reply do."
            elif khan_called:
                prompt_hint = f"Kisi ne group mein tumhara naam 'Khan' liya hai, poora message: '{user_text}' — chhota casual reply do jaise 'hm bhai yaad kiya kya' ya 'kya hua bata' type."
            else:
                prompt_hint = user_text

            reply = get_ai_reply(user_id, prompt_hint)
            send_message(chat_id, reply)

    return {"ok": True}

def get_ai_reply(user_id, user_text):
    history = chat_memory.get(user_id, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": messages
    }
    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
        print(f"GROQ STATUS: {res.status_code}")
        reply_text = res.json()["choices"][0]["message"]["content"]

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        chat_memory[user_id] = history[-MAX_HISTORY:]

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
