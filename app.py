
import os
import time
import secrets
from functools import wraps

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Configuration
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "").strip()
SETUP_SECRET = os.environ.get("SETUP_SECRET", "").strip()

TELEGRAM_WEBHOOK_SECRET = os.environ.get(
    "TELEGRAM_WEBHOOK_SECRET", ""
).strip()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY", ""
).strip()

GEMINI_MODEL = "gemini-3.5-flash-lite"

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

MEMORY_URL = f"{SUPABASE_URL}/rest/v1/jarvis_memory"


# Protect setup routes
def require_setup_secret(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        supplied = request.headers.get("X-Setup-Secret", "")

        if (
            not SETUP_SECRET
            or not supplied
            or not secrets.compare_digest(
                supplied, SETUP_SECRET
            )
        ):
            return jsonify({"error": "Unauthorized"}), 401

        return view(*args, **kwargs)

    return wrapped


# Health endpoints
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "Jarvis Assistant",
        "status": "online"
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


# Supabase helpers
def memory_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def get_recent_memory(owner_id, limit=10):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        app.logger.warning("Supabase memory is not configured")
        return []

    try:
        response = requests.get(
            MEMORY_URL,
            headers=memory_headers(),
            params={
                "select": "memory_type,content,created_at",
                "owner_telegram_id": f"eq.{owner_id}",
                "order": "created_at.desc",
                "limit": limit,
            },
            timeout=15,
        )

        if response.status_code != 200:
            app.logger.error(
                "Supabase memory read failed: HTTP %s",
                response.status_code,
            )
            return []

        rows = response.json()
        rows.reverse()
        return rows

    except (requests.RequestException, ValueError):
        app.logger.exception("Could not read Jarvis memory")
        return []


def save_memory(owner_id, memory_type, content):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        app.logger.warning("Supabase memory is not configured")
        return False

    try:
        response = requests.post(
            MEMORY_URL,
            headers={
                **memory_headers(),
                "Prefer": "return=minimal",
            },
            json={
                "owner_telegram_id": str(owner_id),
                "memory_type": memory_type,
                "content": content,
            },
            timeout=15,
        )

        if response.status_code not in (200, 201, 204):
            app.logger.error(
                "Supabase memory save failed: HTTP %s",
                response.status_code,
            )
            return False

        return True

    except requests.RequestException:
        app.logger.exception("Could not save Jarvis memory")
        return False


# Gemini AI
def ask_gemini(user_text, history=None):
    if not GEMINI_API_KEY:
        return (
            "Jarvis is not configured correctly. "
            "Please check server settings."
        )

    contents = []

    for item in history or []:
        kind = item.get("memory_type", "")
        content = item.get("content", "")

        if not content:
            continue

        if kind == "user":
            role = "user"
        elif kind == "assistant":
            role = "model"
        else:
            continue

        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + content
        else:
            contents.append({
                "role": role,
                "parts": [{"text": content}],
            })

    if contents and contents[-1]["role"] == "user":
        contents[-1]["parts"][0]["text"] += (
            "\n" + user_text.strip()
        )
    else:
        contents.append({
            "role": "user",
            "parts": [{"text": user_text.strip()}],
        })

    payload = {
        "system_instruction": {
            "parts": [{
                "text": (
                    "You are Jarvis, a helpful personal AI assistant. "
                    "Respond clearly and naturally. Use the language "
                    "of the user's message unless asked otherwise. "
                    "The conversation history is provided for context."
                )
            }]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048,
        },
    }

    retryable_statuses = {429, 500, 502, 503, 504}

    for attempt in range(3):
        try:
            response = requests.post(
                GEMINI_API,
                headers={
                    "x-goog-api-key": GEMINI_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
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

                return (
                    "I received an empty response. Please try again."
                )

            if response.status_code in retryable_statuses:
                app.logger.warning(
                    "Gemini temporary error: HTTP %s, attempt %s/3",
                    response.status_code,
                    attempt + 1,
                )

                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue

                return (
                    "Gemini is temporarily busy. "
                    "Please try again shortly."
                )

            app.logger.error(
                "Gemini request failed: HTTP %s",
                response.status_code,
            )
            return (
                "I couldn't process that request. "
                "Please try again later."
            )

        except (requests.RequestException, ValueError):
            app.logger.exception("Gemini request error")

            if attempt < 2:
                time.sleep(2 ** attempt)
                continue

            return (
                "I couldn't connect to Gemini. "
                "Please try again shortly."
            )

    return "Something went wrong. Please try again."


# Telegram message sender
def send_message(chat_id, text):
    if not TOKEN:
        app.logger.error("TELEGRAM_BOT_TOKEN is missing")
        return False

    chunks = [
        text[i:i + 4000]
        for i in range(0, len(text), 4000)
    ] or [""]

    all_sent = True

    for chunk in chunks:
        try:
            response = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                },
                timeout=20,
            )

            if not response.ok:
                app.logger.error(
                    "Telegram sendMessage failed: HTTP %s",
                    response.status_code,
                )
                all_sent = False

        except requests.RequestException:
            app.logger.exception("Telegram send error")
            all_sent = False

    return all_sent


# Check Gemini models (protected)
@app.route("/check-models", methods=["GET"])
@require_setup_secret
def check_models():
    if not GEMINI_API_KEY:
        return jsonify({
            "error": "GEMINI_API_KEY is not configured"
        }), 500

    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            timeout=20,
        )

        if response.status_code != 200:
            return jsonify({
                "error": "Gemini model lookup failed"
            }), 502

        models = [
            model["name"]
            for model in response.json().get("models", [])
            if model.get("name")
        ]

        return jsonify({"models": models})

    except (requests.RequestException, ValueError):
        app.logger.exception("Gemini model lookup error")
        return jsonify({
            "error": "Could not check Gemini models"
        }), 502


# Configure Telegram webhook (protected)
@app.route("/set-webhook", methods=["GET"])
@require_setup_secret
def set_webhook():
    render_url = os.environ.get(
        "RENDER_EXTERNAL_URL", ""
    ).strip().rstrip("/")

    if not TOKEN or not render_url:
        return jsonify({
            "error": "Bot token or Render URL is missing"
        }), 500

    if not TELEGRAM_WEBHOOK_SECRET:
        return jsonify({
            "error": "TELEGRAM_WEBHOOK_SECRET is not configured"
        }), 500

    try:
        response = requests.post(
            f"{TELEGRAM_API}/setWebhook",
            json={
                "url": f"{render_url}/telegram-webhook",
                "secret_token": TELEGRAM_WEBHOOK_SECRET,
            },
            timeout=20,
        )

        data = response.json()

        if response.ok and data.get("ok"):
            return jsonify({
                "ok": True,
                "message": "Webhook configured successfully",
            })

        app.logger.error(
            "Telegram webhook setup failed: HTTP %s",
            response.status_code,
        )
        return jsonify({
            "error": "Telegram webhook setup failed"
        }), 502

    except (requests.RequestException, ValueError):
        app.logger.exception("Webhook setup error")
        return jsonify({
            "error": "Could not configure webhook"
        }), 502


# Telegram webhook: validate secret and allow owner only
@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    supplied_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if (
        not TELEGRAM_WEBHOOK_SECRET
        or not supplied_secret
        or not secrets.compare_digest(
            supplied_secret, TELEGRAM_WEBHOOK_SECRET
        )
    ):
        app.logger.warning(
            "Rejected webhook request with invalid secret"
        )
        return jsonify({"error": "Unauthorized"}), 401

    update = request.get_json(silent=True) or {}
    message = update.get("message", {})

    if not message:
        return jsonify({"ok": True})

    chat = message.get("chat", {})
    sender = message.get("from", {})
    chat_id = chat.get("id")
    user_id = str(sender.get("id", ""))
    user_text = message.get("text", "")

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

    command = user_text.strip().split()[0].split("@")[0]

    if command == "/start":
        send_message(
            chat_id,
            "Hello! I'm Jarvis, your personal AI assistant. "
            "How can I help?",
        )
        return jsonify({"ok": True})

    history = get_recent_memory(OWNER_TELEGRAM_ID, limit=10)
    answer = ask_gemini(user_text, history)

    save_memory(OWNER_TELEGRAM_ID, "user", user_text.strip())
    save_memory(OWNER_TELEGRAM_ID, "assistant", answer)

    send_message(chat_id, answer)

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
