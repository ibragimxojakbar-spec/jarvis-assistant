
import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.8-flash"

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)


@app.route("/")
def home():
    return jsonify({
        "assistant": "Jarvis",
        "status": "online",
        "ai": "Gemini"
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


def ask_gemini(user_text):
    response = requests.post(
        GEMINI_API,
        headers={
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "system_instruction": {
                "parts": [{
                    "text": (
                        "You are Jarvis, a helpful personal AI assistant. "
                        "Be friendly, clear, and practical. "
                        "Help with web development, business, and daily tasks."
                    )
                }]
            },
            "contents": [{
                "parts": [{"text": user_text}]
            }],
            "generationConfig": {
                "maxOutputTokens": 700
            }
        },
        timeout=40
    )
    response.raise_for_status()
    data = response.json()

    return data["candidates"][0]["content"]["parts"][0]["text"]


def send_message(chat_id, text):
    # Telegram message limit is 4096 characters.
    for start in range(0, len(text), 4000):
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[start:start + 4000]
            },
            timeout=15
        ).raise_for_status()


@app.route("/set-webhook")
def set_webhook():
    if not TOKEN:
        return jsonify({"error": "Telegram token is missing"}), 500

    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return jsonify({"error": "Render URL not found"}), 500

    response = requests.post(
        f"{TELEGRAM_API}/setWebhook",
        json={"url": f"{url}/telegram-webhook"},
        timeout=15
    )
    return jsonify(response.json())


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user_text = message.get("text", "").strip()

    if not chat_id or not user_text:
        return jsonify({"ok": True})

    if user_text == "/start":
        send_message(
            chat_id,
            "Hello! I'm Jarvis, now connected to Gemini AI. "
            "Ask me anything!"
        )
        return jsonify({"ok": True})

    if not GEMINI_API_KEY:
        send_message(chat_id, "Gemini API key is not configured.")
        return jsonify({"ok": True})

    try:
        answer = ask_gemini(user_text)
        send_message(chat_id, answer)
    except Exception:
        app.logger.exception("Gemini request failed")
        send_message(
            chat_id,
            "Sorry, I couldn't get an AI response. "
            "Please try again shortly."
        )

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
