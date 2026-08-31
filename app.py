import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
import json
import secrets

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    send_file,
    session
)

import requests

from google import genai
from google.genai import types


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    secrets.token_hex(32)
)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY",
    ""
).strip()

OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    ""
).strip()

SITE_PASSWORD = os.environ.get(
    "SITE_PASSWORD",
    ""
).strip()

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    ""
).strip()

SUPABASE_PUBLISHABLE_KEY = os.environ.get(
    "SUPABASE_PUBLISHABLE_KEY",
    ""
).strip()

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# MODEL CONFIGURATION
# ============================================================

# Gemini model comes from Render.
# Your current value can be:
# gemini-3.6-flash
#
# If Gemini is unavailable or exhausted, the app immediately
# switches to OpenRouter.
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()


# IMPORTANT:
#
# We intentionally DO NOT trust OPENROUTER_MODEL.
#
# The old value:
#
# qwen/qwen3-32b:free
#
# became unavailable.
#
# Instead, the app dynamically finds a currently available
# FREE Qwen model from OpenRouter.
#
# If no free Qwen model can be found, it uses:
#
# openrouter/free
#
# which is officially OpenRouter's free router.
OPENROUTER_FREE_ROUTER = "openrouter/free"


# ============================================================
# TRANSLATION SETTINGS
# ============================================================

MAX_CHARS_PER_REQUEST = 6500

DOWNLOAD_MIN_WORDS = 30000

REQUEST_DELAY = 2.5

MAX_RETRIES = 2

OPENROUTER_TIMEOUT = 120

GEMINI_TIMEOUT = 120


# ============================================================
# GEMINI CLIENT
# ============================================================

gemini_client = None

if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )
        print("Gemini client initialized.")
        print("Gemini model:", GEMINI_MODEL)

    except Exception as e:
        print(
            "Gemini client initialization failed:",
            repr(e)
        )


# ============================================================
# IN-MEMORY JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()


# ============================================================
# OPENROUTER STATE
# ============================================================

openrouter_model_cache = {
    "model": None,
    "time": 0
}

OPENROUTER_MODEL_CACHE_SECONDS = 300


# ============================================================
# HTML
# ============================================================

PAGE = r"""
<!DOCTYPE html>
<html>
<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<meta charset="UTF-8">

<title>Novel Translator</title>

<style>

* {
    box-sizing: border-box;
}

body {
    font-family: Arial, sans-serif;
    background: #f3f4f6;
    margin: 0;
    padding: 15px;
    color: #222;
}

.container {
    max-width: 760px;
    margin: auto;
    background: white;
    padding: 22px;
    border-radius: 16px;
    box-shadow: 0 3px 18px rgba(0,0,0,0.10);
}

h1 {
    margin-top: 0;
    font-size: 27px;
}

h2 {
    margin-top: 28px;
}

input[type=file],
input[type=password],
button {
    width: 100%;
    padding: 14px;
    margin-top: 10px;
    border-radius: 9px;
    font-size: 16px;
}

input {
    border: 1px solid #ccc;
}

button {
    border: none;
    background: #222;
    color: white;
    cursor: pointer;
}

button:hover {
    background: #444;
}

button.danger {
    background: #b00020;
}

button.green {
    background: #177a32;
}

button.blue {
    background: #1759a6;
}

.small {
    color: #666;
    font-size: 14px;
    line-height: 1.5;
}

.notice {
    background: #eef5ff;
    border-left: 5px solid #2774d9;
    padding: 13px;
    border-radius: 8px;
    margin-top: 15px;
}

.warning {
    background: #fff4d6;
    border-left: 5px solid #e5a100;
    padding: 13px;
    border-radius: 8px;
    margin-top: 15px;
}

.error {
    background: #ffe4e4;
    border-left: 5px solid #c00000;
    padding: 13px;
    border-radius: 8px;
    margin-top: 15px;
    white-space: pre-wrap;
}

.success {
    background: #e5f8e8;
    border-left: 5px solid #1d8a37;
    padding: 13px;
    border-radius: 8px;
    margin-top: 15px;
}

.job {
    border: 1px solid #ddd;
    padding: 16px;
    margin-top: 16px;
    border-radius: 12px;
    background: #fff;
}

.job-title {
    font-weight: bold;
    font-size: 17px;
    word-break: break-word;
}

.badge {
    display: inline-block;
    padding: 5px 9px;
    border-radius: 20px;
    font-size: 12px;
    margin-top: 8px;
    background: #eee;
}

.badge.gemini {
    background: #e7f0ff;
    color: #1659a6;
}

.badge.qwen {
    background: #e9f8e9;
    color: #176b2c;
}

.badge.router {
    background: #fff2d9;
    color: #875b00;
}

.progress {
    margin-top: 14px;
    background: #ddd;
    border-radius: 10px;
    overflow: hidden;
    height: 27px;
}

.bar {
    height: 27px;
    background: #4caf50;
    text-align: center;
    color: white;
    line-height: 27px;
    min-width: 1%;
}

.status {
    margin-top: 13px;
    padding: 12px;
    background: #f3f3f3;
    border-radius: 8px;
    white-space: pre-wrap;
}

.word-box {
    margin-top: 13px;
    padding: 13px;
    border-radius: 9px;
    background: #f7f7f7;
}

.download-box {
    margin-top: 13px;
}

a.button {
    display: block;
    text-align: center;
    padding: 13px;
    margin-top: 10px;
    border-radius: 9px;
    background: #222;
    color: white;
    text-decoration: none;
}

a.button.green {
    background: #177a32;
}

.lock-box {
    margin-bottom: 20px;
}

.center {
    text-align: center;
}

hr {
    border: none;
    border-top: 1px solid #ddd;
    margin: 25px 0;
}

</style>

</head>

<body>

<div class="container">

{% if not authenticated %}

    <div class="center">

        <h1>🔒 Novel Translator</h1>

        <p>
            This website is password protected.
        </p>

        {% if login_error %}
            <div class="error">
                Incorrect password.
            </div>
        {% endif %}

        <form action="/login" method="POST">

            <input
                type="password"
                name="password"
                placeholder="Enter website password"
                required
                autofocus
            >

            <button type="submit">
                🔓 Enter Website
            </button>

        </form>

    </div>

{% else %}

    <h1>📚 Novel Translator</h1>

    <p>
        Upload a TXT or EPUB novel and translate it to English.
    </p>

    <div class="notice">

        <strong>Translation system</strong>

        <p>
            1. Gemini is tried first.
        </p>

        <p>
            2. If Gemini is exhausted or unavailable,
            OpenRouter automatically takes over.
        </p>

        <p>
            3. OpenRouter uses a FREE Qwen model whenever
            one is currently available.
        </p>

        <p>
            4. If no free Qwen model is available,
            the official OpenRouter FREE router is used.
        </p>

        <p>
            5. Once a novel switches to OpenRouter,
            that novel stays on OpenRouter.
        </p>

        <p>
            6. Translation is cumulative.
        </p>

        <p>
            7. Download becomes available after
            <strong>{{ "{:,}".format(min_words) }}</strong>
            English words.
        </p>

    </div>


    <form
        action="/upload"
        method="POST"
        enctype="multipart/form-data"
    >

        <input
            type="file"
            name="file"
            accept=".txt,.epub"
            required
        >

        <button
            type="submit"
            class="blue"
        >
            📤 Upload Novel
        </button>

    </form>


    <form
        action="/lock"
        method="POST"
    >

        <button type="submit">
            🔒 Lock Website
        </button>

    </form>


    {% if jobs %}

        <h2>Your Novels</h2>

        {% for job_id, job in jobs.items() %}

            <div class="job">

                <div class="job-title">
                    {{ job.filename }}
                </div>


                {% if job.provider == "gemini" %}

                    <span class="badge gemini">
                        🤖 Gemini
                    </span>

                {% elif job.provider == "openrouter_qwen" %}

                    <span class="badge qwen">
                        🤖 OpenRouter Qwen
                    </span>

                {% elif job.provider == "openrouter_free" %}

                    <span class="badge router">
                        🆓 OpenRouter FREE
                    </span>

                {% endif %}


                <p>
                    📖 Chapters:
                    {{ job.translated_chapters }}/{{ job.total_chapters }}
                </p>

                <div class="word-box">

                    📝 English words:

                    <strong>
                        {{ "{:,}".format(job.words) }}
                    </strong>

                </div>


                {% if job.percent > 0 %}

                    <div class="progress">

                        <div
                            class="bar"
                            style="width: {{ job.percent }}%;"
                        >
                            {{ job.percent }}%
                        </div>

                    </div>

                {% endif %}


                <div class="status">

                    Status:
                    {{ job.status }}

                </div>


                {% if job.error %}

                    <div class="error">

                        {{ job.error }}

                    </div>

                {% endif %}


                {% if job.running %}

                    <div class="warning">

                        ⏳ Translation is currently running.

                        <br><br>

                        You can leave this page open.
                        The translation continues in the background.

                    </div>

                    <script>

                    setTimeout(
                        function() {
                            location.reload();
                        },
                        5000
                    );

                    </script>

                {% endif %}


                {% if job.translated_chapters < job.total_chapters
                      and not job.running %}

                    <form
                        action="/translate/{{ job_id }}"
                        method="GET"
                    >

                        <button
                            type="submit"
                            class="green"
                        >
                            ▶ Continue Translation
                        </button>

                    </form>

                {% endif %}


                {% if job.words >= min_words %}

                    <div class="download-box">

                        <a
                            class="button green"
                            href="/download/{{ job_id }}"
                        >
                            📥 Download Current EPUB
                        </a>

                    </div>

                {% else %}

                    <div class="small">
                        🔒 Download unlocks at
                        <strong>
                            {{ "{:,}".format(min_words) }}
                        </strong>
                        words.
                        <br><br>
                        Current:
                        <strong>
                            {{ "{:,}".format(job.words) }}
                        </strong>
                        words.
                    </div>

                {% endif %}


                {% if job.translated_chapters == job.total_chapters
                      and not job.error %}

                    <a
                        class="button green"
                        href="/download/{{ job_id }}"
                    >
                        📚 Download Complete EPUB
                    </a>

                {% endif %}


                <form
                    action="/delete/{{ job_id }}"
                    method="POST"
                    onsubmit="return confirm('Delete this novel?');"
                >

                    <button
                        type="submit"
                        class="danger"
                    >
                        🗑️ Delete Novel
                    </button>

                </form>

            </div>

        {% endfor %}

    {% endif %}

{% endif %}

</div>

</body>
</html>
"""


# ============================================================
# AUTHENTICATION
# ============================================================

def password_required():
    return bool(SITE_PASSWORD)


def is_authenticated():
    if not password_required():
        return True

    return session.get("authenticated") is True


@app.before_request
def require_login():

    allowed = {
        "login",
        "health",
        "static"
    }

    if request.endpoint in allowed:
        return None

    if not password_required():
        return None

    if not is_authenticated():
        return redirect(
            url_for(
                "index"
            )
        )

    return None


@app.route("/login", methods=["POST"])
def login():

    password = request.form.get(
        "password",
        ""
    )

    if (
        SITE_PASSWORD
        and secrets.compare_digest(
            password,
            SITE_PASSWORD
        )
    ):
        session["authenticated"] = True

        return redirect(
            url_for("index")
        )

    return render_template_string(
        PAGE,
        authenticated=False,
        login_error=True,
        jobs={},
        min_words=DOWNLOAD_MIN_WORDS
    )


@app.route("/lock", methods=["POST"])
def lock():

    session.clear()

    return redirect(
        url_for("index")
    )


# ============================================================
# TEXT UTILITIES
# ============================================================

def clean_text(text):

    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    text = text.replace(
        "\x00",
        ""
    )

    text = re.sub(
        r"\n{4,}",
        "\n\n\n",
        text
    )

    return text.strip()


def count_words(text):

    return len(
        re.findall(
            r"\b[\w'-]+\b",
            text,
            flags=re.UNICODE
        )
    )


def split_large_text(
    text,
    max_chars=MAX_CHARS_PER_REQUEST
):

    text = clean_text(text)

    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n")

    chunks = []

    current = ""

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        if len(paragraph) > max_chars:

            if current:
                chunks.append(
                    current
                )
                current = ""

            start = 0

            while start < len(paragraph):

                end = min(
                    start + max_chars,
                    len(paragraph)
                )

                chunks.append(
                    paragraph[start:end]
                )

                start = end

            continue

        candidate = (
            current + "\n" + paragraph
            if current
            else paragraph
        )

        if len(candidate) > max_chars:

            if current:
                chunks.append(
                    current
                )

            current = paragraph

        else:

            current = candidate

    if current:
        chunks.append(
            current
        )

    return chunks


# ============================================================
# TXT PARSER
# ============================================================

def parse_txt(data):

    encodings = [
        "utf-8",
        "utf-8-sig",
        "gb18030",
        "gbk",
        "big5"
    ]

    text = None

    for encoding in encodings:

        try:

            text = data.decode(
                encoding
            )

            break

        except UnicodeDecodeError:
            continue

    if text is None:

        raise ValueError(
            "Could not decode TXT file. "
            "Please save it as UTF-8."
        )

    text = clean_text(text)

    pattern = re.compile(
        r"(?im)^(第\s*[0-9一二三四五六七八九十百千万两零]+\s*[章回节]|"
        r"chapter\s+\d+.*)$"
    )

    matches = list(
        pattern.finditer(text)
    )

    chapters = []

    if matches:

        for i, match in enumerate(matches):

            start = match.start()

            if i + 1 < len(matches):

                end = matches[
                    i + 1
                ].start()

            else:

                end = len(text)

            chapter_text = text[
                start:end
            ].strip()

            if chapter_text:

                chapters.append(
                    chapter_text
                )

    else:

        chunks = split_large_text(
            text,
            max_chars=12000
        )

        for i, chunk in enumerate(chunks):

            chapters.append(
                "Chapter "
                + str(i + 1)
                + "\n\n"
                + chunk
            )

    if not chapters:

        raise ValueError(
            "No chapters were found."
        )

    return chapters


# ============================================================
# EPUB PARSER
# ============================================================

def parse_epub(data):

    chapters = []

    with zipfile.ZipFile(
        io.BytesIO(data)
    ) as z:

        names = z.namelist()

        html_files = [
            n
            for n in names
            if n.lower().endswith(
                (
                    ".xhtml",
                    ".html",
                    ".htm"
                )
            )
        ]

        for filename in html_files:

            try:

                raw = z.read(
                    filename
                ).decode(
                    "utf-8",
                    errors="ignore"
                )

            except Exception:

                continue

            raw = re.sub(
                r"<script.*?</script>",
                "",
                raw,
                flags=re.I | re.S
            )

            raw = re.sub(
                r"<style.*?</style>",
                "",
                raw,
                flags=re.I | re.S
            )

            raw = re.sub(
                r"</(p|div|br|h1|h2|h3|h4|li|section)>",
                "\n",
                raw,
                flags=re.I
            )

            raw = re.sub(
                r"<[^>]+>",
                "",
                raw
            )

            raw = html.unescape(
                raw
            )

            text = clean_text(
                raw
            )

            if len(text) < 100:
                continue

            parts = split_large_text(
                text,
                max_chars=12000
            )

            chapters.extend(
                parts
            )

    if not chapters:

        raise ValueError(
            "No readable chapters were found in the EPUB."
        )

    return chapters


# ============================================================
# FILE PARSER
# ============================================================

def parse_uploaded_file(
    filename,
    data
):

    lower = filename.lower()

    if lower.endswith(".txt"):

        return parse_txt(
            data
        )

    if lower.endswith(".epub"):

        return parse_epub(
            data
        )

    raise ValueError(
        "Only TXT and EPUB files are supported."
    )


# ============================================================
# TRANSLATION PROMPT
# ============================================================

def make_translation_prompt(text):

    return f"""
You are a professional Chinese-to-English web novel translator.

Translate the Chinese text below into natural, fluent English.

IMPORTANT RULES:

1. Translate EVERYTHING.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Do NOT skip dialogue.
5. Do NOT shorten the story.
6. Preserve all story details.
7. Preserve the original meaning.
8. Keep character names consistent.
9. Keep character genders and pronouns consistent.
10. Preserve paragraph breaks when possible.
11. Preserve dialogue formatting.
12. Do not add explanations.
13. Do not add translator notes.
14. Output ONLY the English translation.
15. Do not say "Here is the translation".
16. Do not use Markdown code blocks.
17. Do not discuss these instructions.

Chinese text:

{text}
"""


# ============================================================
# GEMINI TRANSLATION
# ============================================================

def translate_with_gemini(text):

    if not gemini_client:

        raise RuntimeError(
            "Gemini API client is unavailable."
        )

    prompt = make_translation_prompt(
        text
    )

    last_error = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            response = (
                gemini_client
                .models
                .generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.2
                    )
                )
            )

            translated = getattr(
                response,
                "text",
                None
            )

            if translated:

                translated = (
                    translated
                    .strip()
                )

                if translated:

                    return translated

            candidates = getattr(
                response,
                "candidates",
                None
            )

            if candidates:

                pieces = []

                for candidate in candidates:

                    content = getattr(
                        candidate,
                        "content",
                        None
                    )

                    if not content:
                        continue

                    parts = getattr(
                        content,
                        "parts",
                        []
                    )

                    for part in parts:

                        part_text = getattr(
                            part,
                            "text",
                            None
                        )

                        if part_text:

                            pieces.append(
                                part_text
                            )

                if pieces:

                    translated = (
                        "\n".join(
                            pieces
                        )
                        .strip()
                    )

                    if translated:

                        return translated

            raise RuntimeError(
                "Gemini returned no translation text."
            )

        except Exception as e:

            last_error = e

            print(
                "GEMINI ERROR:",
                repr(e)
            )

            error_string = str(e)

            # IMPORTANT:
            #
            # Any Gemini failure is considered a reason
            # to fall back to OpenRouter.
            #
            # We do NOT keep hammering an exhausted Gemini
            # quota.

            if attempt < MAX_RETRIES:

                time.sleep(
                    2 ** attempt
                )

    raise RuntimeError(
        "Gemini unavailable or quota exhausted.\n\n"
        + str(last_error)
    )


# ============================================================
# OPENROUTER MODEL DISCOVERY
# ============================================================

def get_free_qwen_model():

    if not OPENROUTER_API_KEY:

        return None

    now = time.time()

    cached_model = openrouter_model_cache.get(
        "model"
    )

    cached_time = openrouter_model_cache.get(
        "time",
        0
    )

    if (
        cached_model
        and now - cached_time
        < OPENROUTER_MODEL_CACHE_SECONDS
    ):

        return cached_model

    headers = {
        "Authorization":
            "Bearer "
            + OPENROUTER_API_KEY,

        "Content-Type":
            "application/json"
    }

    try:

        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
            timeout=30
        )

        response.raise_for_status()

        payload = response.json()

        models = payload.get(
            "data",
            []
        )

        free_qwen = []

        for model in models:

            model_id = str(
                model.get(
                    "id",
                    ""
                )
            )

            name = str(
                model.get(
                    "name",
                    ""
                )
            )

            model_lower = model_id.lower()
            name_lower = name.lower()

            is_qwen = (
                "qwen" in model_lower
                or "qwen" in name_lower
            )

            if not is_qwen:
                continue

            pricing = model.get(
                "pricing",
                {}
            )

            prompt_price = str(
                pricing.get(
                    "prompt",
                    ""
                )
            )

            completion_price = str(
                pricing.get(
                    "completion",
                    ""
                )
            )

            # Zero price is required.
            if (
                prompt_price != "0"
                or completion_price != "0"
            ):
                continue

            free_qwen.append(
                model
            )

        # Prefer models whose ID explicitly says :free.
        free_qwen.sort(
            key=lambda m: (
                ":free" not in str(
                    m.get(
                        "id",
                        ""
                    )
                ).lower(),
                str(
                    m.get(
                        "id",
                        ""
                    )
                )
            )
        )

        if free_qwen:

            selected = str(
                free_qwen[0].get(
                    "id"
                )
            )

            openrouter_model_cache[
                "model"
            ] = selected

            openrouter_model_cache[
                "time"
            ] = now

            print(
                "Selected FREE Qwen model:",
                selected
            )

            return selected

        print(
            "No currently available FREE Qwen model found."
        )

        return None

    except Exception as e:

        print(
            "OpenRouter model discovery error:",
            repr(e)
        )

        return None


# ============================================================
# OPENROUTER TRANSLATION
# ============================================================

def translate_with_openrouter(
    text,
    preferred_model=None
):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY is missing."
        )

    prompt = make_translation_prompt(
        text
    )

    # First try a currently FREE Qwen model.
    selected_model = (
        preferred_model
        or get_free_qwen_model()
    )

    # If Qwen is not currently free,
    # use OpenRouter's official FREE router.
    if not selected_model:

        selected_model = OPENROUTER_FREE_ROUTER

    headers = {
        "Authorization":
            "Bearer "
            + OPENROUTER_API_KEY,

        "Content-Type":
            "application/json",

        "HTTP-Referer":
            "https://novel-translator.onrender.com",

        "X-Title":
            "Free Novel Translator"
    }

    payload = {
        "model": selected_model,

        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.2
    }

    last_error = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=OPENROUTER_TIMEOUT
            )

            if response.status_code != 200:

                error_text = response.text

                # If the selected Qwen model became
                # unavailable, use the official FREE router.
                if (
                    selected_model
                    != OPENROUTER_FREE_ROUTER
                    and response.status_code
                    in (400, 404)
                ):

                    print(
                        "Selected FREE Qwen model unavailable."
                    )

                    print(
                        "Switching to OpenRouter FREE router."
                    )

                    selected_model = (
                        OPENROUTER_FREE_ROUTER
                    )

                    payload["model"] = (
                        selected_model
                    )

                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=OPENROUTER_TIMEOUT
                    )

                    if response.status_code != 200:

                        error_text = response.text

                if response.status_code != 200:

                    raise RuntimeError(
                        "OpenRouter HTTP "
                        + str(
                            response.status_code
                        )
                        + ": "
                        + error_text
                    )

            data = response.json()

            choices = data.get(
                "choices",
                []
            )

            if not choices:

                raise RuntimeError(
                    "OpenRouter returned no choices."
                )

            message = choices[0].get(
                "message",
                {}
            )

            translated = message.get(
                "content"
            )

            if not translated:

                raise RuntimeError(
                    "OpenRouter returned no translation text."
                )

            if isinstance(
                translated,
                list
            ):

                parts = []

                for item in translated:

                    if isinstance(
                        item,
                        dict
                    ):

                        value = item.get(
                            "text"
                        )

                        if value:
                            parts.append(
                                value
                            )

                translated = "\n".join(
                    parts
                )

            translated = str(
                translated
            ).strip()

            if not translated:

                raise RuntimeError(
                    "OpenRouter returned an empty translation."
                )

            actual_model = data.get(
                "model",
                selected_model
            )

            return (
                translated,
                actual_model
            )

        except Exception as e:

            last_error = e

            print(
                "OPENROUTER ERROR:",
                repr(e)
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    2 ** attempt
                )

    raise RuntimeError(
        "OpenRouter translation failed.\n\n"
        + str(last_error)
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        return

    if not TELEGRAM_CHAT_ID:
        return

    try:

        requests.post(
            "https://api.telegram.org/bot"
            + TELEGRAM_BOT_TOKEN
            + "/sendMessage",

            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message
            },

            timeout=15
        )

    except Exception as e:

        print(
            "Telegram notification error:",
            repr(e)
        )


# ============================================================
# JOB HELPERS
# ============================================================

def get_job(job_id):

    with jobs_lock:

        return jobs.get(
            job_id
        )


def calculate_job_words(job):

    combined = "\n\n".join(
        job.get(
            "translations",
            []
        )
    )

    return count_words(
        combined
    )


# ============================================================
# TRANSLATION WORKER
# ============================================================

def translation_worker(job_id):

    job = get_job(
        job_id
    )

    if not job:
        return

    try:

        job["running"] = True
        job["error"] = None

        total = len(
            job["chapters"]
        )

        while (
            job["translated_chapters"]
            < total
        ):

            index = job[
                "translated_chapters"
            ]

            original_chapter = job[
                "chapters"
            ][index]

            pieces = split_large_text(
                original_chapter,
                MAX_CHARS_PER_REQUEST
            )

            translated_pieces = []

            for piece_number, piece in enumerate(
                pieces
            ):

                job["status"] = (
                    "Translating chapter "
                    + str(index + 1)
                    + "/"
                    + str(total)
                    + " - part "
                    + str(piece_number + 1)
                    + "/"
                    + str(len(pieces))
                    + "..."
                )

                # ====================================================
                # GEMINI FIRST
                # ====================================================

                if job["provider"] == "gemini":

                    try:

                        translated = (
                            translate_with_gemini(
                                piece
                            )
                        )

                    except Exception as gemini_error:

                        print(
                            "Gemini failed."
                        )

                        print(
                            "Switching job to OpenRouter."
                        )

                        job["provider"] = (
                            "openrouter_qwen"
                        )

                        job["provider_model"] = None

                        job["status"] = (
                            "Gemini is unavailable or "
                            "its quota is exhausted. "
                            "Automatically switched to "
                            "FREE OpenRouter."
                        )

                        send_telegram(
                            "Novel Translator:\n\n"
                            + job["filename"]
                            + "\n\n"
                            "Gemini failed/exhausted. "
                            "Switched to FREE OpenRouter."
                        )

                        translated, actual_model = (
                            translate_with_openrouter(
                                piece
                            )
                        )

                        job[
                            "provider_model"
                        ] = actual_model

                else:

                    # =================================================
                    # OPENROUTER STAYS OPENROUTER
                    # =================================================

                    translated, actual_model = (
                        translate_with_openrouter(
                            piece,
                            preferred_model=job.get(
                                "provider_model"
                            )
                        )
                    )

                    job[
                        "provider_model"
                    ] = actual_model

                translated_pieces.append(
                    translated
                )

                time.sleep(
                    REQUEST_DELAY
                )

            final_translation = (
                "\n\n".join(
                    translated_pieces
                )
                .strip()
            )

            job[
                "translations"
            ].append(
                final_translation
            )

            job[
                "translated_chapters"
            ] += 1

            job[
                "words"
            ] = calculate_job_words(
                job
            )

            job[
                "percent"
            ] = int(
                (
                    job[
                        "translated_chapters"
                    ]
                    /
                    total
                )
                * 100
            )

            model_display = job.get(
                "provider_model"
            )

            if job["provider"] == "gemini":

                provider_text = (
                    "Gemini"
                )

            else:

                provider_text = (
                    "OpenRouter FREE"
                )

                if model_display:
                    provider_text += (
                        " ("
                        + model_display
                        + ")"
                    )

            job["status"] = (
                "Completed chapter "
                + str(
                    job[
                        "translated_chapters"
                    ]
                )
                + "/"
                + str(total)
                + ". "
                + str(
                    job[
                        "words"
                    ]
                )
                + " English words. "
                + provider_text
                + "."
            )

        job["status"] = (
            "Translation complete!"
        )

        job["running"] = False

        send_telegram(
            "Novel Translator:\n\n"
            + job["filename"]
            + "\n\n"
            "Translation complete.\n"
            + str(job["words"])
            + " English words."
        )

    except Exception as e:

        print(
            "TRANSLATION WORKER ERROR:",
            repr(e)
        )

        job["error"] = str(
            e
        )

        job["status"] = (
            "Translation stopped."
        )

        job["running"] = False

        send_telegram(
            "Novel Translator ERROR:\n\n"
            + job["filename"]
            + "\n\n"
            + str(e)
        )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    authenticated = is_authenticated()

    return render_template_string(
        PAGE,

        authenticated=authenticated,

        login_error=False,

        jobs=jobs,

        min_words=DOWNLOAD_MIN_WORDS
    )


# ============================================================
# UPLOAD
# ============================================================

@app.route(
    "/upload",
    methods=["POST"]
)
def upload():

    uploaded = request.files.get(
        "file"
    )

    if not uploaded:

        return redirect(
            url_for("index")
        )

    if not uploaded.filename:

        return redirect(
            url_for("index")
        )

    try:

        data = uploaded.read()

        chapters = parse_uploaded_file(
            uploaded.filename,
            data
        )

        job_id = str(
            uuid.uuid4()
        )

        jobs[job_id] = {

            "id":
                job_id,

            "filename":
                uploaded.filename,

            "chapters":
                chapters,

            "translations":
                [],

            "translated_chapters":
                0,

            "total_chapters":
                len(chapters),

            "words":
                0,

            "percent":
                0,

            "status":
                "Uploaded. Ready to translate.",

            "error":
                None,

            "running":
                False,

            # Gemini is ALWAYS tried first.
            "provider":
                "gemini",

            # Once fallback happens, this gets filled.
            "provider_model":
                None
        }

        print(
            "Uploaded "
            + uploaded.filename
            + ": "
            + str(len(chapters))
            + " chapters"
        )

        return redirect(
            url_for("index")
        )

    except Exception as e:

        return (
            "<h2>Upload Error</h2>"
            "<p>"
            + html.escape(
                str(e)
            )
            + "</p>"
            "<p><a href='/'>Go back</a></p>"
        )


# ============================================================
# START / CONTINUE TRANSLATION
# ============================================================

@app.route(
    "/translate/<job_id>"
)
def translate(job_id):

    job = get_job(
        job_id
    )

    if not job:

        return redirect(
            url_for("index")
        )

    if job["running"]:

        return redirect(
            url_for("index")
        )

    if (
        job["translated_chapters"]
        >= job["total_chapters"]
    ):

        return redirect(
            url_for("index")
        )

    job["error"] = None

    thread = threading.Thread(
        target=translation_worker,
        args=(job_id,),
        daemon=True
    )

    thread.start()

    return redirect(
        url_for("index")
    )


# ============================================================
# DOWNLOAD
# ============================================================

@app.route(
    "/download/<job_id>"
)
def download(job_id):

    job = get_job(
        job_id
    )

    if not job:

        return redirect(
            url_for("index")
        )

    if not job["translations"]:

        return (
            "Nothing has been translated yet."
        )

    # Enforce 30,000-word requirement unless
    # the entire novel is complete.
    if (
        job["words"] < DOWNLOAD_MIN_WORDS
        and job["translated_chapters"]
        < job["total_chapters"]
    ):

        return (
            "Download unlocks after "
            + str(
                DOWNLOAD_MIN_WORDS
            )
            + " English words."
        )

    try:

        epub_bytes = create_epub(
            job["filename"],
            job["translations"]
        )

        base_name = os.path.splitext(
            job["filename"]
        )[0]

        output_name = (
            base_name
            + "_translated.epub"
        )

        return send_file(
            io.BytesIO(
                epub_bytes
            ),

            mimetype=
                "application/epub+zip",

            as_attachment=True,

            download_name=
                output_name
        )

    except Exception as e:

        return (
            "<h2>EPUB creation error</h2>"
            "<p>"
            + html.escape(
                str(e)
            )
            + "</p>"
            "<p><a href='/'>Go back</a></p>"
        )


# ============================================================
# EPUB CREATOR
# ============================================================

def create_epub(
    filename,
    translations
):

    base_name = os.path.splitext(
        filename
    )[0]

    book_title = (
        base_name
        + " - English Translation"
    )

    buf = io.BytesIO()

    with zipfile.ZipFile(
        buf,
        "w",
        zipfile.ZIP_DEFLATED
    ) as epub:

        epub.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED
        )

        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0"
xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles>
<rootfile
full-path="OEBPS/content.opf"
media-type="application/oebps-package+xml"/>
</rootfiles>
</container>"""
        )

        manifest_items = []

        spine_items = []

        for i, translation in enumerate(
            translations
        ):

            chapter_filename = (
                "chapter"
                + str(i + 1)
                + ".xhtml"
            )

            title = (
                "Chapter "
                + str(i + 1)
            )

            safe_translation = html.escape(
                translation
            )

            paragraphs = (
                safe_translation.split(
                    "\n"
                )
            )

            body = ""

            for paragraph in paragraphs:

                paragraph = (
                    paragraph.strip()
                )

                if paragraph:

                    body += (
                        "<p>"
                        + paragraph
                        + "</p>\n"
                    )

            chapter_html = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "\n"
                '<!DOCTYPE html>'
                "\n"
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                "\n"
                "<head>"
                "\n"
                '<meta charset="UTF-8"/>'
                "\n"
                "<title>"
                + html.escape(title)
                + "</title>"
                "\n"
                "</head>"
                "\n"
                "<body>"
                "\n"
                "<h2>"
                + html.escape(title)
                + "</h2>"
                "\n"
                + body
                + "</body>"
                "\n"
                "</html>"
            )

            epub.writestr(
                "OEBPS/"
                + chapter_filename,
                chapter_html
            )

            manifest_items.append(
                '<item id="chapter'
                + str(i + 1)
                + '" href="'
                + chapter_filename
                + '" media-type="application/xhtml+xml"/>'
            )

            spine_items.append(
                '<itemref idref="chapter'
                + str(i + 1)
                + '"/>'
            )

        css = """
body {
    font-family: serif;
    line-height: 1.6;
    margin: 5%;
}

h1, h2 {
    text-align: center;
}

p {
    text-indent: 1.5em;
    margin-bottom: 1em;
}
"""

        epub.writestr(
            "OEBPS/style.css",
            css
        )

        manifest = "\n".join(
            manifest_items
        )

        spine = "\n".join(
            spine_items
        )

        opf = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "\n"
            '<package version="3.0" '
            'xmlns="http://www.idpf.org/2007/opf" '
            'unique-identifier="BookID">'
            "\n"
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "\n"
            '<dc:identifier id="BookID">'
            + str(uuid.uuid4())
            + "</dc:identifier>"
            "\n"
            "<dc:title>"
            + html.escape(book_title)
            + "</dc:title>"
            "\n"
            "<dc:language>en</dc:language>"
            "\n"
            "<dc:creator>Free Novel Translator</dc:creator>"
            "\n"
            "</metadata>"
            "\n"
            "<manifest>"
            "\n"
            '<item id="style" href="style.css" media-type="text/css"/>'
            "\n"
            + manifest
            + "\n"
            "</manifest>"
            "\n"
            "<spine>"
            "\n"
            + spine
            + "\n"
            "</spine>"
            "\n"
            "</package>"
        )

        epub.writestr(
            "OEBPS/content.opf",
            opf
        )

    return buf.getvalue()


# ============================================================
# DELETE
# ============================================================

@app.route(
    "/delete/<job_id>",
    methods=["POST"]
)
def delete(job_id):

    if job_id in jobs:

        del jobs[job_id]

    return redirect(
        url_for("index")
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return {

        "status":
            "ok",

        "gemini_configured":
            bool(
                GEMINI_API_KEY
            ),

        "openrouter_configured":
            bool(
                OPENROUTER_API_KEY
            ),

        "password_enabled":
            bool(
                SITE_PASSWORD
            ),

        "gemini_model":
            GEMINI_MODEL,

        "openrouter_mode":
            "FREE Qwen when available, otherwise openrouter/free"
    }


# ============================================================
# SERVER
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
