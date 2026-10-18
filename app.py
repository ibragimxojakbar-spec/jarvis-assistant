
import os
import requests
from flask import Flask, jsonify

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"


@app.route("/")
def home():
    return jsonify({
        "assistant": "Jarvis",
        "status": "online",
        "message": "Jarvis is ready!"
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


def send_message(chat_id, text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15
    )


@app.route("/set-webhook")
def set_webhook():
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
    from flask import request

    update = request.get_json(silent=True) or {}
    message = update.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "").strip()

    if chat_id and text:
        if text == "/start":
            reply = (
                "Hello! I am Jarvis, your personal assistant. "
                "Send me a message to get started."
            )
        else:
            reply = (
                f"I received your message: {text}\n\n"
                "My basic Telegram connection works! "
                "AI intelligence will be added next."
            )

        send_message(chat_id, reply)

    return jsonify({"ok": True})
