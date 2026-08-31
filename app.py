import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
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
    "super-secret-key-change-this-in-render"
)

app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

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
).strip().rstrip("/")

SUPABASE_PUBLISHABLE_KEY = os.environ.get(
    "SUPABASE_PUBLISHABLE_KEY",
    ""
).strip()

SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
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

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
).strip()


# ============================================================
# TRANSLATION SETTINGS
# ============================================================

MAX_CHARS_PER_REQUEST = 10000
REQUEST_DELAY = 3.0
MAX_RETRIES = 5

OPENROUTER_TIMEOUT = 30
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
# IN-MEMORY CACHE
# ============================================================

jobs = {}
jobs_lock = threading.Lock()


# ============================================================
# OPENROUTER FREE MODELS
# ============================================================

FREE_FALLBACK_MODELS = [
    "deepseek/deepseek-r1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "google/gemma-4-31b:free",
    "openrouter/free"
]


# ============================================================
# HTML
# ============================================================

PAGE = r"""
<!DOCTYPE html>
<html>

<head>

<meta name="viewport" content="width=device-width, initial-scale=1">
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
select,
button {
    width: 100%;
    padding: 14px;
    margin-top: 10px;
    border-radius: 9px;
    font-size: 16px;
}

input, select {
    border: 1px solid #ccc;
    background: #fff;
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
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
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

.center {
    text-align: center;
}

label {
    display: block;
    margin-top: 12px;
    font-weight: bold;
    font-size: 14px;
}

</style>

</head>

<body>

<div class="container">

{% if not authenticated %}

    <div class="center">

        <h1>🔒 Novel Translator</h1>

        <p>This website is password protected.</p>

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

        <strong>Translation System</strong>

        <p>
            1. Choose your preferred translation model below.
        </p>

        <p>
            2. Translation progress is permanently saved to Supabase.
        </p>

        <p>
            3. If Gemini hits a limit, OpenRouter can take over.
        </p>

    </div>

    <form
        action="/upload"
        method="POST"
        enctype="multipart/form-data"
    >

        <label for="file">
            Select File (.txt or .epub):
        </label>

        <input
            type="file"
            id="file"
            name="file"
            accept=".txt,.epub"
            required
        >

        <label for="model_choice">
            Choose Preferred Translation Model:
        </label>

        <select
            name="model_choice"
            id="model_choice"
        >

            <option value="gemini">
                🤖 Gemini 2.5 Flash
                (Recommended)
            </option>

            <option value="deepseek/deepseek-r1:free">
                🧠 DeepSeek R1
                (Free OpenRouter)
            </option>

            <option value="meta-llama/llama-3.3-70b-instruct:free">
                🦙 Meta Llama 3.3 70B
                (Free OpenRouter)
            </option>

            <option value="qwen/qwen-2.5-72b-instruct:free">
                🌐 Qwen 2.5 72B
                (Free OpenRouter)
            </option>

            <option value="openrouter/free">
                🎲 OpenRouter
                (Auto-select Free Model)
            </option>

        </select>

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
                        🤖 Gemini 2.5 Flash
                    </span>

                {% else %}

                    <span class="badge router">

                        🆓 OpenRouter

                        {% if job.provider_model %}
                            ({{ job.provider_model }})
                        {% endif %}

                    </span>

                {% endif %}

                <p>
                    📖 Chapters:
                    {{ job.translated_chapters }}/{{ job.total_chapters }}
                </p>

                <div class="word-box">

                    <div>
                        📄 Original:
                        <strong>
                            {{ "{:,}".format(job.original_words) }}
                        </strong>
                    </div>

                    <div>
                        📝 English:
                        <strong>
                            {{ "{:,}".format(job.words) }}
                        </strong>
                    </div>

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
                    Status: {{ job.status }}
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

                        Progress is automatically saved to Supabase.

                        <br><br>

                        You can leave the page open or come back later.

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

                {% if job.words > 0 %}

                    <div class="download-box">

                        <a
                            class="button green"
                            href="/download/{{ job_id }}"
                        >
                            📥 Download Current EPUB
                            ({{ "{:,}".format(job.words) }} words)
                        </a>

                    </div>

                {% else %}

                    <div class="small">

                        🔒 Download unlocks once the first section
                        translates.

                    </div>

                {% endif %}

                <form
                    action="/delete/{{ job_id }}"
                    method="POST"
                    onsubmit="return confirm('Delete this novel permanently?');"
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

        if request.endpoint == "index":
            return None

        return redirect(url_for("index"))

    return None


# ============================================================
# SUPABASE DATABASE
# ============================================================

def supabase_configured():

    return bool(
        SUPABASE_URL and
        SUPABASE_SERVICE_ROLE_KEY
    )


def supabase_headers():

    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }


def supabase_table_url():

    return (
        f"{SUPABASE_URL}/rest/v1/translator_jobs"
    )


def save_job_to_supabase(job):

    if not supabase_configured():
        raise RuntimeError(
            "Supabase is not configured. "
            "Make sure SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY are set in Render."
        )

    payload = {
        "id": job["id"],
        "filename": job["filename"],
        "chapters": job["chapters"],
        "original_words": job["original_words"],
        "translations": job["translations"],
        "translated_chapters": job["translated_chapters"],
        "total_chapters": job["total_chapters"],
        "words": job["words"],
        "percent": job["percent"],
        "status": job["status"],
        "error": job["error"],
        "running": job["running"],
        "provider": job["provider"],
        "provider_model": job.get("provider_model")
    }

    response = requests.post(
        supabase_table_url(),
        headers=supabase_headers(),
        params={
            "on_conflict": "id"
        },
        json=payload,
        timeout=30
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            "Supabase save failed: "
            f"{response.status_code} "
            f"{response.text[:1000]}"
        )


def update_job_in_supabase(job):

    if not supabase_configured():
        return

    payload = {
        "filename": job["filename"],
        "chapters": job["chapters"],
        "original_words": job["original_words"],
        "translations": job["translations"],
        "translated_chapters": job["translated_chapters"],
        "total_chapters": job["total_chapters"],
        "words": job["words"],
        "percent": job["percent"],
        "status": job["status"],
        "error": job["error"],
        "running": job["running"],
        "provider": job["provider"],
        "provider_model": job.get("provider_model")
    }

    response = requests.patch(
        supabase_table_url(),
        headers=supabase_headers(),
        params={
            "id": f"eq.{job['id']}"
        },
        json=payload,
        timeout=30
    )

    if response.status_code not in (200, 204):
        raise RuntimeError(
            "Supabase update failed: "
            f"{response.status_code} "
            f"{response.text[:1000]}"
        )


def load_jobs_from_supabase():

    if not supabase_configured():

        print(
            "WARNING: Supabase is not configured. "
            "Jobs will only exist in memory."
        )

        return

    try:

        response = requests.get(
            supabase_table_url(),
            headers=supabase_headers(),
            params={
                "select": "*",
                "order": "id.desc"
            },
            timeout=30
        )

        if response.status_code != 200:

            print(
                "Supabase load failed:",
                response.status_code,
                response.text[:1000]
            )

            return

        rows = response.json()

        with jobs_lock:

            jobs.clear()

            for row in rows:

                # A Render restart can happen while a translation
                # thread was running. No thread survives the restart.
                #
                # Therefore any job that was marked running is reset
                # to stopped so the user can safely press Continue.
                if row.get("running"):

                    row["running"] = False

                    if (
                        row.get("translated_chapters", 0)
                        <
                        row.get("total_chapters", 0)
                    ):

                        row["status"] = (
                            "Translation paused because the server "
                            "restarted. Press Continue Translation."
                        )

                jobs[row["id"]] = row

        # Persist the reset running state.
        for row in rows:

            if row.get("running"):

                try:
                    update_job_in_supabase(row)

                except Exception as e:

                    print(
                        "Could not reset running state:",
                        repr(e)
                    )

        print(
            f"Loaded {len(rows)} saved jobs from Supabase."
        )

    except Exception as e:

        print(
            "Could not load jobs from Supabase:",
            repr(e)
        )


def delete_job_from_supabase(job_id):

    if not supabase_configured():
        return

    response = requests.delete(
        supabase_table_url(),
        headers=supabase_headers(),
        params={
            "id": f"eq.{job_id}"
        },
        timeout=30
    )

    if response.status_code not in (200, 204):

        raise RuntimeError(
            "Supabase delete failed: "
            f"{response.status_code} "
            f"{response.text[:1000]}"
        )


# ============================================================
# TEXT UTILITIES
# ============================================================

def clean_text(text):

    text = text.replace(
        "\r\n",
        "\n"
    ).replace(
        "\r",
        "\n"
    ).replace(
        "\x00",
        ""
    )

    return re.sub(
        r"\n{4,}",
        "\n\n\n",
        text
    ).strip()


def count_words(text):

    if not text:
        return 0

    cjk_count = len(
        re.findall(
            r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]',
            text
        )
    )

    space_word_count = len(
        re.findall(
            r"\b[\w'-]+\b",
            re.sub(
                r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]',
                '',
                text
            )
        )
    )

    return cjk_count + space_word_count


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
                chunks.append(current)
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
                chunks.append(current)

            current = paragraph

        else:

            current = candidate

    if current:
        chunks.append(current)

    return chunks


# ============================================================
# PARSERS
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

            text = data.decode(encoding)
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
        r"(?im)^("
        r"第\s*[0-9一二三四五六七八九十百千万两零]+\s*[章回节]"
        r"|chapter\s+\d+.*"
        r")$"
    )

    matches = list(
        pattern.finditer(text)
    )

    chapters = []

    if matches:

        for i, match in enumerate(matches):

            start = match.start()

            end = (
                matches[i + 1].start()
                if i + 1 < len(matches)
                else len(text)
            )

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
            max_chars=MAX_CHARS_PER_REQUEST
        )

        for i, chunk in enumerate(chunks):

            chapters.append(
                f"Chapter {i + 1}\n\n{chunk}"
            )

    if not chapters:

        raise ValueError(
            "No chapters were found."
        )

    return chapters


def parse_epub(data):

    chapters = []

    with zipfile.ZipFile(
        io.BytesIO(data)
    ) as z:

        names = z.namelist()

        html_files = [
            n for n in names
            if n.lower().endswith(
                (".xhtml", ".html", ".htm")
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

            raw = html.unescape(raw)

            text = clean_text(raw)

            if len(text) < 100:
                continue

            parts = split_large_text(
                text,
                max_chars=MAX_CHARS_PER_REQUEST
            )

            chapters.extend(parts)

    if not chapters:

        raise ValueError(
            "No readable chapters were found in the EPUB."
        )

    return chapters


def parse_uploaded_file(
    filename,
    data
):

    lower = filename.lower()

    if lower.endswith(".txt"):
        return parse_txt(data)

    if lower.endswith(".epub"):
        return parse_epub(data)

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
4. Preserve all story details.
5. Preserve character names.
6. Keep character genders and pronouns consistent with the context.
7. Do not randomly change he/she/they for the same character.
8. Keep dialogue formatting.
9. Keep paragraph breaks.
10. Do not add explanations.
11. Do not add translator notes.
12. Output ONLY the English translation.
13. Do not use Markdown code blocks.

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

    prompt = make_translation_prompt(text)

    last_error = None

    for attempt in range(MAX_RETRIES):

        try:

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2
                )
            )

            translated = getattr(
                response,
                "text",
                None
            )

            if translated and translated.strip():

                return translated.strip()

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

                        pt = getattr(
                            part,
                            "text",
                            None
                        )

                        if pt:
                            pieces.append(pt)

                if pieces:

                    return "\n".join(
                        pieces
                    ).strip()

            raise RuntimeError(
                "Gemini returned no translation text."
            )

        except Exception as e:

            last_error = e

            err_str = str(e)

            print(
                f"GEMINI ERROR "
                f"(Attempt {attempt + 1}/{MAX_RETRIES}):",
                repr(e)
            )

            if attempt < MAX_RETRIES - 1:

                if (
                    "503" in err_str
                    or "429" in err_str
                    or "quota" in err_str.lower()
                    or "rate" in err_str.lower()
                ):

                    sleep_time = (
                        (2 ** attempt) * 3
                    )

                else:

                    sleep_time = (
                        2 ** attempt
                    )

                time.sleep(
                    sleep_time
                )

    raise RuntimeError(
        "Gemini unavailable or quota exhausted.\n\n"
        f"{last_error}"
    )


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

    prompt = make_translation_prompt(text)

    headers = {
        "Authorization": (
            f"Bearer {OPENROUTER_API_KEY}"
        ),
        "Content-Type": "application/json",
        "HTTP-Referer": (
            "https://novel-translator.onrender.com"
        ),
        "X-Title": "Free Novel Translator"
    }

    models_to_try = (
        FREE_FALLBACK_MODELS.copy()
    )

    if (
        preferred_model
        and preferred_model not in models_to_try
    ):

        models_to_try.insert(
            0,
            preferred_model
        )

    last_error = None

    for target_model in models_to_try:

        payload = {
            "model": target_model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.2
        }

        try:

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=OPENROUTER_TIMEOUT
            )

            if response.status_code == 200:

                data = response.json()

                choices = data.get(
                    "choices",
                    []
                )

                if choices:

                    translated = (
                        choices[0]
                        .get("message", {})
                        .get("content", "")
                        .strip()
                    )

                    if translated:

                        actual_model = data.get(
                            "model",
                            target_model
                        )

                        return (
                            translated,
                            actual_model
                        )

            else:

                print(
                    f"OpenRouter model "
                    f"{target_model} returned "
                    f"{response.status_code}: "
                    f"{response.text[:500]}"
                )

                last_error = RuntimeError(
                    f"HTTP {response.status_code}"
                )

        except Exception as e:

            print(
                f"OpenRouter model "
                f"{target_model} failed: {e}"
            )

            last_error = e

    raise RuntimeError(
        "All free OpenRouter fallback models "
        "are currently unavailable.\n\n"
        f"{last_error}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        return

    try:

        response = requests.post(
            (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            ),
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=15
        )

        if response.status_code != 200:

            print(
                "Telegram returned:",
                response.status_code,
                response.text[:500]
            )

    except Exception as e:

        print(
            "Telegram error:",
            repr(e)
        )


# ============================================================
# JOB HELPERS
# ============================================================

def get_job(job_id):

    with jobs_lock:
        return jobs.get(job_id)


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

    job = get_job(job_id)

    if not job:
        return

    try:

        job["running"] = True
        job["error"] = None

        update_job_in_supabase(job)

        total = len(
            job["chapters"]
        )

        while (
            job["translated_chapters"]
            <
            total
        ):

            index = (
                job["translated_chapters"]
            )

            original_chapter = (
                job["chapters"][index]
            )

            pieces = split_large_text(
                original_chapter,
                MAX_CHARS_PER_REQUEST
            )

            translated_pieces = []

            for piece_number, piece in enumerate(
                pieces
            ):

                job["status"] = (
                    f"Translating section "
                    f"{index + 1}/{total} "
                    f"(part "
                    f"{piece_number + 1}/"
                    f"{len(pieces)})..."
                )

                # Save current status.
                try:
                    update_job_in_supabase(
                        job
                    )
                except Exception as e:
                    print(
                        "Status save warning:",
                        repr(e)
                    )

                if job["provider"] == "gemini":

                    try:

                        translated = (
                            translate_with_gemini(
                                piece
                            )
                        )

                    except Exception as gemini_error:

                        if OPENROUTER_API_KEY:

                            print(
                                "Gemini limit/error hit. "
                                "Falling back to OpenRouter."
                            )

                            job["provider"] = (
                                "openrouter_free"
                            )

                            job["provider_model"] = (
                                "openrouter/free"
                            )

                            try:

                                update_job_in_supabase(
                                    job
                                )

                            except Exception:
                                pass

                            (
                                translated,
                                actual_model
                            ) = (
                                translate_with_openrouter(
                                    piece
                                )
                            )

                            job["provider_model"] = (
                                actual_model
                            )

                        else:

                            raise gemini_error

                else:

                    (
                        translated,
                        actual_model
                    ) = (
                        translate_with_openrouter(
                            piece,
                            preferred_model=job.get(
                                "provider_model"
                            )
                        )
                    )

                    job["provider_model"] = (
                        actual_model
                    )

                translated_pieces.append(
                    translated
                )

                time.sleep(
                    REQUEST_DELAY
                )

            final_translation = (
                "\n\n".join(
                    translated_pieces
                ).strip()
            )

            # ------------------------------------------------
            # IMPORTANT:
            # Only after the entire chapter/section has
            # translated successfully do we permanently
            # advance translated_chapters.
            # ------------------------------------------------

            job["translations"].append(
                final_translation
            )

            job["translated_chapters"] += 1

            job["words"] = (
                calculate_job_words(
                    job
                )
            )

            job["percent"] = int(
                (
                    job["translated_chapters"]
                    /
                    total
                ) * 100
            )

            model_display = (
                job.get("provider_model")
            )

            if job["provider"] == "gemini":

                provider_text = (
                    "Gemini 2.5 Flash"
                )

            else:

                provider_text = (
                    "OpenRouter FREE"
                )

                if model_display:

                    provider_text += (
                        f" ({model_display})"
                    )

            job["status"] = (
                f"Completed section "
                f"{job['translated_chapters']}/"
                f"{total}. "
                f"{job['words']:,} words. "
                f"Active: {provider_text}."
            )

            # ------------------------------------------------
            # THIS IS THE IMPORTANT SAVE.
            #
            # Every completed chapter is written to Supabase.
            # Therefore a Render restart after this point
            # does not lose that completed chapter.
            # ------------------------------------------------

            update_job_in_supabase(
                job
            )

            print(
                f"Saved progress for "
                f"{job['filename']}: "
                f"{job['translated_chapters']}/{total}"
            )

        job["status"] = (
            "Translation complete!"
        )

        job["running"] = False

        update_job_in_supabase(
            job
        )

        send_telegram(
            "Novel Translator:\n\n"
            f"{job['filename']}\n\n"
            "Translation complete.\n"
            f"{job['words']:,} English words."
        )

    except Exception as e:

        print(
            "TRANSLATION WORKER ERROR:",
            repr(e)
        )

        job["error"] = str(e)

        job["status"] = (
            "Translation stopped."
        )

        job["running"] = False

        try:

            update_job_in_supabase(
                job
            )

        except Exception as save_error:

            print(
                "Could not save error state:",
                repr(save_error)
            )

        send_telegram(
            "Novel Translator ERROR:\n\n"
            f"{job['filename']}\n\n"
            f"{e}"
        )


# ============================================================
# LOAD SAVED JOBS
# ============================================================

load_jobs_from_supabase()


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():

    return render_template_string(
        PAGE,
        authenticated=is_authenticated(),
        login_error=False,
        jobs=jobs
    )


@app.route(
    "/login",
    methods=["POST"]
)
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
        jobs={}
    )


@app.route(
    "/lock",
    methods=["POST"]
)
def lock():

    session.clear()

    return redirect(
        url_for("index")
    )


@app.route(
    "/upload",
    methods=["POST"]
)
def upload():

    uploaded = request.files.get(
        "file"
    )

    chosen_model = request.form.get(
        "model_choice",
        "gemini"
    )

    if (
        not uploaded
        or not uploaded.filename
    ):

        return redirect(
            url_for("index")
        )

    try:

        data = uploaded.read()

        chapters = parse_uploaded_file(
            uploaded.filename,
            data
        )

        original_words = sum(
            count_words(ch)
            for ch in chapters
        )

        job_id = str(
            uuid.uuid4()
        )

        provider = (
            "gemini"
            if chosen_model == "gemini"
            else "openrouter_free"
        )

        provider_model = (
            None
            if chosen_model == "gemini"
            else chosen_model
        )

        job = {
            "id": job_id,
            "filename": uploaded.filename,
            "chapters": chapters,
            "original_words": original_words,
            "translations": [],
            "translated_chapters": 0,
            "total_chapters": len(chapters),
            "words": 0,
            "percent": 0,
            "status": (
                "Uploaded. Ready to translate."
            ),
            "error": None,
            "running": False,
            "provider": provider,
            "provider_model": provider_model
        }

        # Save permanently FIRST.
        save_job_to_supabase(
            job
        )

        # Then put it into memory.
        with jobs_lock:
            jobs[job_id] = job

        return redirect(
            url_for("index")
        )

    except Exception as e:

        print(
            "UPLOAD ERROR:",
            repr(e)
        )

        return (
            "<h2>Upload Error</h2>"
            f"<p>{html.escape(str(e))}</p>"
            "<p><a href='/'>Go back</a></p>"
        )


@app.route(
    "/translate/<job_id>"
)
def translate(job_id):

    job = get_job(
        job_id
    )

    if not job:

        # Try reloading from Supabase in case
        # the in-memory cache does not have it.
        load_jobs_from_supabase()

        job = get_job(
            job_id
        )

    if not job:

        return (
            "<h2>Job not found</h2>"
            "<p><a href='/'>Go back</a></p>"
        )

    if job["running"]:

        return redirect(
            url_for("index")
        )

    if (
        job["translated_chapters"]
        >=
        job["total_chapters"]
    ):

        return redirect(
            url_for("index")
        )

    job["error"] = None
    job["running"] = True
    job["status"] = (
        "Translation starting..."
    )

    try:

        update_job_in_supabase(
            job
        )

    except Exception as e:

        job["running"] = False

        job["error"] = str(e)

        job["status"] = (
            "Could not start translation."
        )

        try:
            update_job_in_supabase(
                job
            )
        except Exception:
            pass

        return redirect(
            url_for("index")
        )

    thread = threading.Thread(
        target=translation_worker,
        args=(job_id,),
        daemon=True
    )

    thread.start()

    return redirect(
        url_for("index")
    )


@app.route(
    "/download/<job_id>"
)
def download(job_id):

    job = get_job(
        job_id
    )

    if not job:

        load_jobs_from_supabase()

        job = get_job(
            job_id
        )

    if (
        not job
        or not job.get("translations")
    ):

        return (
            "Nothing has been translated yet."
        )

    try:

        epub_bytes = create_epub(
            job["filename"],
            job["translations"]
        )

        base_name = os.path.splitext(
            job["filename"]
        )[0]

        return send_file(
            io.BytesIO(
                epub_bytes
            ),
            mimetype="application/epub+zip",
            as_attachment=True,
            download_name=(
                f"{base_name}_translated.epub"
            )
        )

    except Exception as e:

        return (
            "<h2>EPUB creation error</h2>"
            f"<p>{html.escape(str(e))}</p>"
            "<p><a href='/'>Go back</a></p>"
        )


# ============================================================
# EPUB CREATION
# ============================================================

def create_epub(
    filename,
    translations
):

    base_name = os.path.splitext(
        filename
    )[0]

    book_title = (
        f"{base_name} - English Translation"
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
<rootfile full-path="OEBPS/content.opf"
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
                f"chapter{i + 1}.xhtml"
            )

            title = (
                f"Chapter {i + 1}"
            )

            safe_translation = html.escape(
                translation
            )

            paragraphs = (
                safe_translation.split("\n")
            )

            body = "".join(
                f"<p>{p.strip()}</p>\n"
                for p in paragraphs
                if p.strip()
            )

            chapter_html = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml">\n'
                '<head>\n'
                f'<meta charset="UTF-8"/>'
                f'<title>{html.escape(title)}</title>\n'
                '</head>\n'
                '<body>\n'
                f'<h2>{html.escape(title)}</h2>\n'
                f'{body}'
                '</body>\n'
                '</html>'
            )

            epub.writestr(
                f"OEBPS/{chapter_filename}",
                chapter_html
            )

            manifest_items.append(
                f'<item id="chapter{i + 1}" '
                f'href="{chapter_filename}" '
                f'media-type="application/xhtml+xml"/>'
            )

            spine_items.append(
                f'<itemref idref="chapter{i + 1}"/>'
            )

        css = (
            "body { "
            "font-family: serif; "
            "line-height: 1.6; "
            "margin: 5%; "
            "}"
            "h1, h2 { "
            "text-align: center; "
            "}"
            "p { "
            "text-indent: 1.5em; "
            "margin-bottom: 1em; "
            "}"
        )

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
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package version="3.0" '
            'xmlns="http://www.idpf.org/2007/opf" '
            'unique-identifier="BookID">\n'

            '<metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">\n'

            f'<dc:identifier id="BookID">'
            f'{uuid.uuid4()}'
            f'</dc:identifier>\n'

            f'<dc:title>'
            f'{html.escape(book_title)}'
            f'</dc:title>\n'

            '<dc:language>en</dc:language>\n'

            '<dc:creator>'
            'Free Novel Translator'
            '</dc:creator>\n'

            '</metadata>\n'

            '<manifest>\n'

            '<item id="style" '
            'href="style.css" '
            'media-type="text/css"/>\n'

            f'{manifest}\n'

            '</manifest>\n'

            '<spine>\n'
            f'{spine}\n'
            '</spine>\n'

            '</package>'
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

    try:

        delete_job_from_supabase(
            job_id
        )

        with jobs_lock:

            jobs.pop(
                job_id,
                None
            )

        return redirect(
            url_for("index")
        )

    except Exception as e:

        return (
            "<h2>Delete Error</h2>"
            f"<p>{html.escape(str(e))}</p>"
            "<p><a href='/'>Go back</a></p>"
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/health"
)
def health():

    return {
        "status": "ok",

        "gemini_configured": bool(
            GEMINI_API_KEY
        ),

        "openrouter_configured": bool(
            OPENROUTER_API_KEY
        ),

        "supabase_configured": bool(
            SUPABASE_URL
            and SUPABASE_SERVICE_ROLE_KEY
        ),

        "supabase_url_configured": bool(
            SUPABASE_URL
        ),

        "supabase_service_key_configured": bool(
            SUPABASE_SERVICE_ROLE_KEY
        ),

        "password_enabled": bool(
            SITE_PASSWORD
        ),

        "telegram_configured": bool(
            TELEGRAM_BOT_TOKEN
            and TELEGRAM_CHAT_ID
        ),

        "gemini_model": GEMINI_MODEL,

        "saved_jobs_in_memory": len(
            jobs
        ),

        "openrouter_mode": (
            "Multi-model selection enabled"
        )
    }


# ============================================================
# START SERVER
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
