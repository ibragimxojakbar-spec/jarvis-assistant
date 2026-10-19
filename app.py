
import os
import time
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.5-flash-lite"

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


# Check which Gemini models support generateContent.
@app.route("/check-models")
def check_models():
    if not GEMINI_API_KEY:
        return jsonify({"error": "API key missing"}), 500

    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            timeout=20
        )

        if not response.ok:
            app.logger.error(
                "List models failed: HTTP %s: %s",
                response.status_code,
                response.text[:500]
            )
            return jsonify({
                "error": response.status_code,
                "details": response.text[:500]
            }), response.status_code

        data = response.json()

        models = [
            model["name"]
            for model in data.get("models", [])
            if "generateContent" in
            model.get("supportedGenerationMethods", [])
        ]

        return jsonify({"models": models})

    except requests.RequestException:
        app.logger.exception("Could not retrieve Gemini models")
        return jsonify({"error": "Request failed"}), 502


def ask_gemini(user_text):
    payload = {
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
    }

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }

    for attempt in range(3):
        try:
            response = requests.post(
                GEMINI_API,
                headers=headers,
                json=payload,
                timeout=20
            )

            if response.status_code in (
                429, 500, 502, 503, 504
            ):
                app.logger.warning(
                    "Gemini temporary error: HTTP %s, attempt %s/3",
                    response.status_code,
                    attempt + 1
                )

                if attempt < 2:
                    time.sleep(1 + attempt * 2)
                    continue

            response.raise_for_status()
            data = response.json()

            candidates = data.get("candidates", [])

            if not candidates:
                raise ValueError(
                    "Gemini returned no candidates: "
                    + str(data.get("promptFeedback", {}))
                )

            parts = candidates[0].get(
                "content", {}
            ).get("parts", [])

            answer = "".join(
                part.get("text", "") for part in parts
            ).strip()

            if not answer:
                raise ValueError("Gemini returned an empty answer")

            return answer

        except (
            requests.Timeout,
            requests.ConnectionError
        ):
            app.logger.warning(
                "Gemini connection issue, attempt %s/3",
                attempt + 1
            )

            if attempt < 2:
                time.sleep(1 + attempt * 2)
                continue

            raise

    raise RuntimeError("Gemini failed after retries")


def send_message(chat_id, text):
    for start in range(0, len(text), 4000):
        response = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[start:start + 4000]
            },
            timeout=15
        )
        response.raise_for_status()


@app.route("/set-webhook")
def set_webhook():
    if not TOKEN:
        return jsonify({
            "error": "Telegram token is missing"
        }), 500

    url = os.environ.get("RENDER_EXTERNAL_URL")

    if not url:
        return jsonify({
            "error": "Render URL not found"
        }), 500

    try:
        response = requests.post(
            f"{TELEGRAM_API}/setWebhook",
            json={"url": f"{url}/telegram-webhook"},
            timeout=15
        )

        return jsonify(response.json()), response.status_code

    except requests.RequestException:
        app.logger.exception("Failed to set Telegram webhook")
        return jsonify({"error": "Webhook request failed"}), 502


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user_text = (message.get("text") or "").strip()

    if not chat_id or not user_text:
        return jsonify({"ok": True})

    if user_text == "/start":
        send_message(
            chat_id,
            "Hello! I'm Jarvis, connected to Gemini AI. "
            "Ask me anything!"
        )
        return jsonify({"ok": True})

    if not GEMINI_API_KEY:
        send_message(
            chat_id,
            "Gemini API key is not configured."
        )
        return jsonify({"ok": True})

    try:
        answer = ask_gemini(user_text)
        send_message(chat_id, answer)

    except Exception:
        app.logger.exception("Jarvis AI request failed")

        try:
            send_message(
                chat_id,
                "Jarvis is temporarily unable to get an AI response. "
                "Please try again in a minute."
            )
        except Exception:
            app.logger.exception(
                "Failed to send Telegram error message"
            )

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
