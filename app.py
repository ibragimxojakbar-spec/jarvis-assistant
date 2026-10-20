import os
import time
import secrets
from functools import wraps

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# ==================================================
# CONFIGURATION
# ==================================================

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "").strip()
SETUP_SECRET = os.environ.get("SETUP_SECRET", "").strip()

GEMINI_MODEL = "gemini-3.5-flash-lite"

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

# ==================================================
# SECURITY FOR SETUP ROUTES
# ==================================================

def require_setup_secret(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        supplied = request.headers.get("X-Setup-Secret", "")

        if (
            not SETUP_SECRET
            or not supplied
            or not secrets.compare_digest(supplied, SETUP_SECRET)
        ):
            return jsonify({"error": "Unauthorized"}), 401

        return view(*args, **kwargs)

    return wrapped


# ==================================================
# HOME AND HEALTH
# ==================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "Jarvis Assistant",
        "status": "online"
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy"
    })


# ==================================================
# CHECK AVAILABLE GEMINI MODELS
# Requires X-Setup-Secret header
# ==================================================

@app.route("/check-models", methods=["GET"])
@require_setup_secret
def check_models():
    if not GEMINI_API_KEY:
        return jsonify({
            "error": "GEMINI_API_KEY is not configured"
        }), 500

    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models"
    )

    try:
        response = requests.get(
            url,
            headers={
                "x-goog-api-key": GEMINI_API_KEY
            },
            timeout=20
        )

        try:
            data = response.json()
        except ValueError:
            data = {"error": "Invalid response from Gemini"}

        if response.status_code != 200:
            return jsonify({
                "error": "Gemini model lookup failed",
                "status_code": response.status_code
            }), 502

        models = [
            model.get("name")
            for model in data.get("models", [])
            if model.get("name")
        ]

        return jsonify({
            "models": models
        })

    except requests.RequestException:
        app.logger.exception("Gemini model lookup failed")

        return jsonify({
            "error": "Could not connect to Gemini"
        }), 502


# ==================================================
# ASK GEMINI
# ==================================================

def ask_gemini(user_text):
    if not GEMINI_API_KEY:
        app.logger.error("GEMINI_API_KEY is missing")
        return "Jarvis is not configured correctly. Please check the server settings."

    if not user_text or not user_text.strip():
        return "Please send me a message."

    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": (
                        "You are Jarvis, a helpful personal AI assistant. "
                        "Respond clearly and naturally. "
                        "Use the language of the user's message unless "
                        "the user requests another language."
                    )
                }
            ]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": user_text.strip()
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    retryable_statuses = {429, 500, 502, 503, 504}
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            response = requests.post(
                GEMINI_API,
                headers={
                    "x-goog-api-key": GEMINI_API_KEY,
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=60
            )

            if response.status_code == 200:
                data = response.json()

                candidates = data.get("candidates", [])

                if candidates:
                    parts = (
                        candidates[0]
                        .get("content", {})
                        .get("parts", [])
                    )

                    answer = "".join(
                        part.get("text", "")
                        for part in parts
                        if part.get("text")
                    ).strip()

                    if answer:
                        return answer

                app.logger.warning(
                    "Gemini returned no usable text"
                )
                return "I received an empty response. Please try again."

            if response.status_code in retryable_statuses:
                app.logger.warning(
                    "Gemini temporary error: HTTP %s; attempt %s/%s",
                    response.status_code,
                    attempt + 1,
                    max_attempts
                )

                if attempt < max_attempts - 1:
                    time.sleep(2 ** attempt)
                    continue

                return (
                    "Gemini is temporarily unavailable or busy. "
                    "Please try again in a little while."
                )

            app.logger.error(
                "Gemini request failed with HTTP %s",
                response.status_code
            )

            return (
                "I couldn't process that request. "
                "Please try again later."
            )

        except requests.RequestException:
            app.logger.exception(
                "Gemini connection error; attempt %s/%s",
                attempt + 1,
                max_attempts
            )

            if attempt < max_attempts - 1:
                time.sleep(2 ** attempt)
                continue

            return (
                "I couldn't connect to Gemini. "
                "Please try again in a little while."
            )

        except ValueError:
            app.logger.exception("Invalid Gemini response")
            return "I received an invalid response. Please try again."

    return "Something went wrong. Please try again."


# ==================================================
# SEND TELEGRAM MESSAGE
# ==================================================

def send_message(chat_id, text):
    if not TOKEN:
        app.logger.error("TELEGRAM_BOT_TOKEN is missing")
        return False

    # Telegram messages have a length limit.
    max_length = 4000

    chunks = [
        text[i:i + max_length]
        for i in range(0, len(text), max_length)
    ] or [""]

    all_sent = True

    for chunk in chunks:
        try:
            response = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk
                },
                timeout=20
            )

            if not response.ok:
                app.logger.error(
                    "Telegram sendMessage failed: HTTP %s",
                    response.status_code
                )
                all_sent = False

        except requests.RequestException:
            app.logger.exception(
                "Could not send Telegram message"
            )
            all_sent = False

    return all_sent


# ==================================================
# SET TELEGRAM WEBHOOK
# Requires X-Setup-Secret header
# ==================================================

@app.route("/set-webhook", methods=["GET"])
@require_setup_secret
def set_webhook():
    if not TOKEN:
        return jsonify({
            "error": "TELEGRAM_BOT_TOKEN is not configured"
        }), 500

    render_url = os.environ.get(
        "RENDER_EXTERNAL_URL", ""
    ).strip().rstrip("/")

    if not render_url:
        return jsonify({
            "error": "RENDER_EXTERNAL_URL is not configured"
        }), 500

    webhook_url = f"{render_url}/telegram-webhook"

    try:
        response = requests.post(
            f"{TELEGRAM_API}/setWebhook",
            json={
                "url": webhook_url
            },
            timeout=20
        )

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.ok and data.get("ok"):
            return jsonify({
                "ok": True,
                "message": "Webhook configured successfully"
            })

        app.logger.error(
            "Telegram webhook setup failed: HTTP %s",
            response.status_code
        )

        return jsonify({
            "error": "Telegram webhook setup failed"
        }), 502

    except requests.RequestException:
        app.logger.exception("Webhook setup connection error")

        return jsonify({
            "error": "Could not connect to Telegram"
        }), 502


# ==================================================
# TELEGRAM WEBHOOK
# OWNER-ONLY PRIVATE ACCESS
# ==================================================

@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    message = update.get("message", {})

    # Ignore updates without a normal message.
    if not message:
        return jsonify({"ok": True})

    chat = message.get("chat", {})
    sender = message.get("from", {})

    chat_id = chat.get("id")
    user_id = str(sender.get("id", ""))
    user_text = message.get("text", "")

    # Allow only the configured owner in a private chat.
    if (
        chat.get("type") != "private"
        or not OWNER_TELEGRAM_ID
        or user_id != OWNER_TELEGRAM_ID
    ):
        app.logger.warning(
            "Ignored unauthorized Telegram message"
        )
        return jsonify({"ok": True})

    if not chat_id or not user_text:
        return jsonify({"ok": True})

    # Start command.
    if user_text.strip().split()[0].split("@")[0] == "/start":
        send_message(
            chat_id,
            (
                "Hello! I'm Jarvis, your personal AI assistant. "
                "How can I help you?"
            )
        )
        return jsonify({"ok": True})

    # Ask Gemini.
    answer = ask_gemini(user_text)
    send_message(chat_id, answer)

    return jsonify({"ok": True})


# ==================================================
# START SERVER
# ==================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port
    )
