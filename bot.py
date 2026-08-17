from flask import Flask, request
import requests
import os

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BOT_USERNAME = "Khan_masti_bot"

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print(f"MESSAGE AAYA: {data}")

    if 'message' in data and 'text' in data['message']:
        chat_id = data['message']['chat']['id']
        text = data['message']['text']
        text_lower = text.lower().strip()

        is_reply_to_bot = data['message'].get('reply_to_message', {}).get('from', {}).get('username') == BOT_USERNAME
        is_mentioned = f"@{BOT_USERNAME}" in text
        is_greeting = text_lower in ["hi", "hello", "hii", "hey", "helo"]
        is_private = data['message']['chat']['type'] == 'private'

        if is_greeting:
            reply = get_ai_reply("Kisi ne tumhe hi/hello bola hai, pyaar se aur mazedaar tareeke se greet karo unhe")
            send_message(chat_id, reply)
        elif is_mentioned or is_reply_to_bot or is_private:
            user_text = text.replace(f"@{BOT_USERNAME}", "").strip()
            reply = get_ai_reply(user_text)
            send_message(chat_id, reply)

    return {"ok": True}

def get_ai_reply(user_text):
    print(f"GROQ KEY EXISTS: {bool(GROQ_API_KEY)}")
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Tum ek majedar, comedy karne wale dost ho. Hinglish mein, thoda masti aur witty andaz mein short jawab do (2-3 lines se zyada nahi)."},
            {"role": "user", "content": user_text}
        ]
    }
    try:
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
        print(f"GROQ STATUS: {res.status_code}")
        print(f"GROQ RESPONSE: {res.text}")
        return res.json()["choices"][0]["message"]["content"]
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
