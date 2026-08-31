import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
import secrets
import requests

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    send_file,
    session
)

from google import genai
from google.genai import types


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-secret-key"
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
).strip()

# New Supabase secret key.
# We also support the old service_role variable name.
SUPABASE_SECRET_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY",
    ""
).strip()

if not SUPABASE_SECRET_KEY:
    SUPABASE_SECRET_KEY = os.environ.get(
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

OPENROUTER_TIMEOUT = 60
GEMINI_TIMEOUT = 120

SUPABASE_TIMEOUT = 30

TELEGRAM_TIMEOUT = 15


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
# IN-MEMORY JOB CACHE
# ============================================================

jobs = {}
jobs_lock = threading.Lock()

worker_threads = {}
worker_threads_lock = threading.Lock()


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

input,
select {
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

.badge.qwen {
    background: #e8f7e9;
    color: #24702c;
}

.badge.deepseek {
    background: #eee8ff;
    color: #5d3c9c;
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
            🥇 Gemini 2.5 Flash
        </p>

        <p>
            ↓ If unavailable or quota reached
        </p>

        <p>
            🥈 Qwen FREE
        </p>

        <p>
            ↓ If unavailable
        </p>

        <p>
            🥉 DeepSeek FREE
        </p>

        <p>
            ↓ If unavailable
        </p>

        <p>
            🔄 Other OpenRouter $0 models
        </p>

        <p class="small">
            Only free OpenRouter models are used.
        </p>

    </div>

    <form action="/upload" method="POST" enctype="multipart/form-data">

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

            <option value="qwen">
                🧠 Qwen FREE
            </option>

            <option value="deepseek">
                🔬 DeepSeek FREE
            </option>

            <option value="openrouter_free">
                🎲 OpenRouter FREE
                (Automatic)
            </option>

        </select>

        <button
            type="submit"
            class="blue"
        >
            📤 Upload Novel
        </button>

    </form>

    <form action="/lock" method="POST">

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

                {% elif job.provider_model and "qwen" in job.provider_model.lower() %}

                    <span class="badge qwen">
                        🧠 Qwen FREE
                    </span>

                {% elif job.provider_model and "deepseek" in job.provider_model.lower() %}

                    <span class="badge deepseek">
                        🔬 DeepSeek FREE
                    </span>

                {% else %}

                    <span class="badge router">
                        🆓 OpenRouter FREE
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

                        Your progress is saved in Supabase.

                        <br><br>

                        You can leave the page or redeploy the website.
                        Saved chapters will remain available.

                    </div>

                    <script>
                        setTimeout(function() {
                            location.reload();
                        }, 5000);
                    </script>

                {% endif %}

                {% if job.translated_chapters < job.total_chapters and not job.running %}

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
                        🔒 Download unlocks once the first section translates.
                    </div>

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

        if request.endpoint == "index":
            return None

        return redirect(url_for("index"))

    return None


@app.route("/")
def index():

    load_jobs_from_supabase()

    return render_template_string(
        PAGE,
        authenticated=is_authenticated(),
        login_error=False,
        jobs=jobs
    )


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
        jobs={}
    )


@app.route("/lock", methods=["POST"])
def lock():

    session.clear()

    return redirect(
        url_for("index")
    )


# ============================================================
# SUPABASE
# ============================================================

def supabase_enabled():

    return bool(
        SUPABASE_URL
        and SUPABASE_SECRET_KEY
    )


def supabase_headers():

    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json"
    }


def supabase_table_url():

    return (
        SUPABASE_URL.rstrip("/")
        + "/rest/v1/translator_jobs"
    )


def serialize_job(job):

    return {
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
        "provider_model": job["provider_model"]
    }


def save_new_job_to_supabase(job):

    if not supabase_enabled():
        raise RuntimeError(
            "Supabase is not configured. "
            "Set SUPABASE_URL and SUPABASE_SECRET_KEY."
        )

    payload = serialize_job(job)

    response = requests.post(
        supabase_table_url(),
        headers={
            **supabase_headers(),
            "Prefer": "return=minimal"
        },
        json=payload,
        timeout=SUPABASE_TIMEOUT
    )

    if response.status_code not in (200, 201):

        raise RuntimeError(
            "Supabase save failed: "
            f"{response.status_code} "
            f"{response.text}"
        )


def update_job_in_supabase(job):

    if not supabase_enabled():
        return

    job_id = job["id"]

    payload = serialize_job(job)

    response = requests.patch(
        supabase_table_url(),
        headers={
            **supabase_headers(),
            "Prefer": "return=minimal"
        },
        params={
            "id": f"eq.{job_id}"
        },
        json=payload,
        timeout=SUPABASE_TIMEOUT
    )

    if response.status_code not in (200, 204):

        raise RuntimeError(
            "Supabase update failed: "
            f"{response.status_code} "
            f"{response.text}"
        )


def delete_job_from_supabase(job_id):

    if not supabase_enabled():
        return

    response = requests.delete(
        supabase_table_url(),
        headers=supabase_headers(),
        params={
            "id": f"eq.{job_id}"
        },
        timeout=SUPABASE_TIMEOUT
    )

    if response.status_code not in (200, 204):

        print(
            "Supabase delete failed:",
            response.status_code,
            response.text
        )


def load_jobs_from_supabase():

    if not supabase_enabled():
        return

    try:

        response = requests.get(
            supabase_table_url(),
            headers=supabase_headers(),
            params={
                "select": "*",
                "order": "filename.asc"
            },
            timeout=SUPABASE_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "Supabase load failed:",
                response.status_code,
                response.text
            )

            return

        rows = response.json()

        with jobs_lock:

            for row in rows:

                job_id = row.get("id")

                if not job_id:
                    continue

                jobs[job_id] = row

    except Exception as e:

        print(
            "Supabase load error:",
            repr(e)
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

    non_cjk = re.sub(
        r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]',
        '',
        text
    )

    space_word_count = len(
        re.findall(
            r"\b[\w'-]+\b",
            non_cjk
        )
    )

    return (
        cjk_count
        + space_word_count
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
# FILE PARSERS
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
        r"(?im)^(第\s*[0-9一二三四五六七八九十百千万两零]+\s*[章回节]|chapter\s+\d+.*)$"
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
            MAX_CHARS_PER_REQUEST
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
                MAX_CHARS_PER_REQUEST
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
5. Preserve character names consistently.
6. Keep character genders and pronouns consistent with context.
7. Do not randomly change he/she/they.
8. Preserve dialogue formatting.
9. Preserve paragraph breaks.
10. Do not add explanations.
11. Do not add translator notes.
12. Output ONLY the English translation.
13. Do not put the translation inside a code block.
14. Do not mention these instructions.

Chinese text:

{text}
""".strip()


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
                    "429" in err_str
                    or "503" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "quota" in err_str.lower()
                ):

                    sleep_time = (
                        3 * (2 ** attempt)
                    )

                else:

                    sleep_time = (
                        2 ** attempt
                    )

                time.sleep(
                    min(
                        sleep_time,
                        30
                    )
                )

    raise RuntimeError(
        "Gemini unavailable or quota exhausted.\n\n"
        + str(last_error)
    )


# ============================================================
# OPENROUTER FREE MODEL DISCOVERY
# ============================================================

def get_openrouter_models():

    if not OPENROUTER_API_KEY:
        return []

    try:

        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={
                "Authorization":
                    f"Bearer {OPENROUTER_API_KEY}"
            },
            timeout=30
        )

        if response.status_code != 200:

            print(
                "OpenRouter model list failed:",
                response.status_code,
                response.text
            )

            return []

        data = response.json()

        return data.get(
            "data",
            []
        )

    except Exception as e:

        print(
            "OpenRouter model discovery error:",
            repr(e)
        )

        return []


def is_free_openrouter_model(model):

    model_id = str(
        model.get("id", "")
    )

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

    # Explicit :free models are accepted.
    if model_id.endswith(":free"):
        return True

    # Also accept models whose listed prompt and completion
    # prices are exactly zero.
    try:

        return (
            float(prompt_price) == 0
            and float(completion_price) == 0
        )

    except Exception:

        return False


def model_is_translation_candidate(model):

    model_id = str(
        model.get("id", "")
    ).lower()

    name = str(
        model.get("name", "")
    ).lower()

    combined = (
        model_id
        + " "
        + name
    )

    bad_words = [
        "embed",
        "embedding",
        "tts",
        "whisper",
        "audio",
        "image",
        "vision-only",
        "moderation"
    ]

    for word in bad_words:

        if word in combined:
            return False

    return True


def free_model_candidates():

    models = get_openrouter_models()

    free_models = []

    for model in models:

        if not is_free_openrouter_model(
            model
        ):
            continue

        if not model_is_translation_candidate(
            model
        ):
            continue

        model_id = model.get(
            "id",
            ""
        )

        if model_id:
            free_models.append(
                model_id
            )

    # Remove duplicates.
    free_models = list(
        dict.fromkeys(
            free_models
        )
    )

    return free_models


def categorize_model(model_id):

    low = model_id.lower()

    if "qwen" in low:
        return 0

    if "deepseek" in low:
        return 1

    if (
        "llama" in low
        or "gemma" in low
        or "mistral" in low
    ):
        return 2

    return 3


def ordered_free_models():

    models = free_model_candidates()

    models.sort(
        key=lambda x: (
            categorize_model(x),
            x
        )
    )

    return models


# ============================================================
# OPENROUTER TRANSLATION
# ============================================================

def translate_with_openrouter(
    text,
    preferred_category=None
):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY is missing."
        )

    prompt = make_translation_prompt(
        text
    )

    headers = {
        "Authorization":
            f"Bearer {OPENROUTER_API_KEY}",

        "Content-Type":
            "application/json",

        "HTTP-Referer":
            "https://novel-translator-i8wp.onrender.com",

        "X-Title":
            "Free Novel Translator"
    }

    current_models = ordered_free_models()

    if not current_models:

        raise RuntimeError(
            "OpenRouter currently returned no suitable "
            "$0/free models."
        )

    if preferred_category == "qwen":

        current_models.sort(
            key=lambda x: (
                0 if "qwen" in x.lower()
                else 1 if "deepseek" in x.lower()
                else 2,
                x
            )
        )

    elif preferred_category == "deepseek":

        current_models.sort(
            key=lambda x: (
                0 if "deepseek" in x.lower()
                else 1 if "qwen" in x.lower()
                else 2,
                x
            )
        )

    elif preferred_category == "openrouter_free":

        # Keep Qwen first, then DeepSeek, then others.
        current_models.sort(
            key=lambda x: (
                categorize_model(x),
                x
            )
        )

    last_error = None

    for target_model in current_models:

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

            print(
                "Trying OpenRouter FREE model:",
                target_model
            )

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

                        print(
                            "OpenRouter FREE model succeeded:",
                            actual_model
                        )

                        return (
                            translated,
                            actual_model
                        )

            print(
                "OpenRouter model failed:",
                target_model,
                response.status_code,
                response.text[:500]
            )

            last_error = RuntimeError(
                f"{target_model}: "
                f"HTTP {response.status_code}"
            )

        except Exception as e:

            print(
                "OpenRouter model error:",
                target_model,
                repr(e)
            )

            last_error = e

    raise RuntimeError(
        "All currently available OpenRouter "
        "$0/free models failed.\n\n"
        + str(last_error)
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
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage",

            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message
            },

            timeout=TELEGRAM_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "Telegram returned:",
                response.status_code,
                response.text
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


def set_job_value(
    job,
    key,
    value
):

    with jobs_lock:

        job[key] = value


def persist_job(job):

    try:

        update_job_in_supabase(
            job
        )

        return True

    except Exception as e:

        print(
            "Could not save job to Supabase:",
            repr(e)
        )

        return False


# ============================================================
# WORKER MANAGEMENT
# ============================================================

def worker_is_running(job_id):

    with worker_threads_lock:

        thread = worker_threads.get(
            job_id
        )

        return (
            thread is not None
            and thread.is_alive()
        )


def start_worker(job_id):

    if worker_is_running(job_id):
        return False

    thread = threading.Thread(
        target=translation_worker,
        args=(job_id,),
        daemon=True
    )

    with worker_threads_lock:

        worker_threads[job_id] = thread

    thread.start()

    return True


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

        set_job_value(
            job,
            "running",
            True
        )

        set_job_value(
            job,
            "error",
            None
        )

        persist_job(
            job
        )

        send_telegram(
            "📚 Novel Translator\n\n"
            f"{job['filename']}\n\n"
            "🚀 Translation started.\n"
            f"Progress: "
            f"{job['translated_chapters']}/"
            f"{job['total_chapters']} chapters."
        )

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

                set_job_value(
                    job,
                    "status",
                    (
                        f"Translating chapter "
                        f"{index + 1}/{total} "
                        f"(part "
                        f"{piece_number + 1}/"
                        f"{len(pieces)})..."
                    )
                )

                # ====================================================
                # GEMINI PRIMARY
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
                            "Gemini failed. "
                            "Starting FREE OpenRouter fallback:",
                            repr(gemini_error)
                        )

                        if not OPENROUTER_API_KEY:

                            raise gemini_error

                        set_job_value(
                            job,
                            "provider",
                            "openrouter_free"
                        )

                        set_job_value(
                            job,
                            "provider_model",
                            "qwen"
                        )

                        persist_job(
                            job
                        )

                        send_telegram(
                            "🔄 Novel Translator\n\n"
                            f"{job['filename']}\n\n"
                            "Gemini 2.5 Flash is unavailable "
                            "or hit a limit.\n\n"
                            "Switching to FREE OpenRouter models."
                        )

                        translated, actual_model = (
                            translate_with_openrouter(
                                piece,
                                preferred_category="qwen"
                            )
                        )

                        set_job_value(
                            job,
                            "provider_model",
                            actual_model
                        )

                        persist_job(
                            job
                        )

                # ====================================================
                # OPENROUTER FREE
                # ====================================================

                else:

                    preferred = job.get(
                        "provider_model"
                    )

                    if preferred in (
                        None,
                        "",
                        "qwen",
                        "deepseek",
                        "openrouter/free"
                    ):

                        preferred_category = preferred

                    else:

                        preferred_category = "openrouter_free"

                    translated, actual_model = (
                        translate_with_openrouter(
                            piece,
                            preferred_category
                        )
                    )

                    old_model = job.get(
                        "provider_model"
                    )

                    set_job_value(
                        job,
                        "provider_model",
                        actual_model
                    )

                    if (
                        old_model
                        and old_model != actual_model
                        and old_model not in (
                            "qwen",
                            "deepseek",
                            "openrouter/free"
                        )
                    ):

                        send_telegram(
                            "🔄 Novel Translator\n\n"
                            f"{job['filename']}\n\n"
                            f"OpenRouter switched FREE model:\n"
                            f"{old_model}\n→\n{actual_model}"
                        )

                    persist_job(
                        job
                    )

                translated_pieces.append(
                    translated
                )

                time.sleep(
                    REQUEST_DELAY
                )

            # ========================================================
            # COMPLETED CHAPTER
            # ========================================================

            final_translation = (
                "\n\n".join(
                    translated_pieces
                ).strip()
            )

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
                    / total
                )
                * 100
            )

            model_display = job.get(
                "provider_model"
            )

            if job["provider"] == "gemini":

                provider_text = (
                    "Gemini 2.5 Flash"
                )

            else:

                provider_text = (
                    "OpenRouter FREE"
                    f" ({model_display})"
                )

            job["status"] = (
                f"Completed chapter "
                f"{job['translated_chapters']}/"
                f"{total}. "
                f"{job['words']:,} words. "
                f"Active: {provider_text}."
            )

            # IMPORTANT:
            # Save after every completed chapter.
            persist_job(
                job
            )

            # Telegram progress every 10 chapters,
            # plus chapter 1.
            if (
                job["translated_chapters"] == 1
                or job["translated_chapters"] % 10 == 0
            ):

                send_telegram(
                    "📖 Novel Translator\n\n"
                    f"{job['filename']}\n\n"
                    f"Progress: "
                    f"{job['translated_chapters']}/"
                    f"{total} chapters "
                    f"({job['percent']}%).\n\n"
                    f"English words: "
                    f"{job['words']:,}\n\n"
                    f"Model: {provider_text}"
                )

        # ============================================================
        # COMPLETE
        # ============================================================

        job["status"] = (
            "Translation complete!"
        )

        job["running"] = False

        job["percent"] = 100

        persist_job(
            job
        )

        send_telegram(
            "🎉 Novel Translator\n\n"
            f"{job['filename']}\n\n"
            "✅ Translation complete!\n\n"
            f"English words: "
            f"{job['words']:,}\n\n"
            "Your translated EPUB is ready "
            "to download from the website."
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

        persist_job(
            job
        )

        send_telegram(
            "❌ Novel Translator ERROR\n\n"
            f"{job['filename']}\n\n"
            f"{str(e)}\n\n"
            "Your saved chapters remain in Supabase."
        )

    finally:

        with worker_threads_lock:

            worker_threads.pop(
                job_id,
                None
            )


# ============================================================
# AUTOMATIC RESUME
# ============================================================

def resume_saved_jobs():

    time.sleep(5)

    print(
        "Checking Supabase for jobs "
        "that need to resume..."
    )

    load_jobs_from_supabase()

    for job_id, job in list(
        jobs.items()
    ):

        try:

            translated = int(
                job.get(
                    "translated_chapters",
                    0
                )
            )

            total = int(
                job.get(
                    "total_chapters",
                    0
                )
            )

            was_running = bool(
                job.get(
                    "running",
                    False
                )
            )

            if (
                was_running
                and translated < total
            ):

                print(
                    "Resuming job after restart:",
                    job.get("filename"),
                    translated,
                    "/",
                    total
                )

                job["running"] = False

                job["status"] = (
                    "Resuming saved translation..."
                )

                persist_job(
                    job
                )

                start_worker(
                    job_id
                )

        except Exception as e:

            print(
                "Resume check error:",
                repr(e)
            )


# Start automatic resume check when
# the application process starts.
resume_thread = threading.Thread(
    target=resume_saved_jobs,
    daemon=True
)

resume_thread.start()


# ============================================================
# ROUTES
# ============================================================

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

        if chosen_model == "gemini":

            provider = "gemini"
            provider_model = None

        elif chosen_model == "qwen":

            provider = "openrouter_free"
            provider_model = "qwen"

        elif chosen_model == "deepseek":

            provider = "openrouter_free"
            provider_model = "deepseek"

        else:

            provider = "openrouter_free"
            provider_model = "openrouter_free"

        job = {

            "id":
                job_id,

            "filename":
                uploaded.filename,

            "chapters":
                chapters,

            "original_words":
                original_words,

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

            "provider":
                provider,

            "provider_model":
                provider_model
        }

        jobs[job_id] = job

        save_new_job_to_supabase(
            job
        )

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
            "<p>"
            + html.escape(str(e))
            + "</p>"
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

        load_jobs_from_supabase()

        job = get_job(
            job_id
        )

    if not job:

        return redirect(
            url_for("index")
        )

    if job.get(
        "running",
        False
    ):

        return redirect(
            url_for("index")
        )

    if (
        job.get(
            "translated_chapters",
            0
        )
        >= job.get(
            "total_chapters",
            0
        )
    ):

        return redirect(
            url_for("index")
        )

    job["error"] = None

    job["running"] = True

    job["status"] = (
        "Starting translation..."
    )

    persist_job(
        job
    )

    start_worker(
        job_id
    )

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
        or not job.get(
            "translations"
        )
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

            mimetype:
                "application/epub+zip",

            as_attachment:
                True,

            download_name:
                f"{base_name}_translated.epub"
        )

    except Exception as e:

        return (
            "<h2>EPUB creation error</h2>"
            "<p>"
            + html.escape(str(e))
            + "</p>"
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

            body = ""

            for p in paragraphs:

                if p.strip():

                    body += (
                        f"<p>{p.strip()}</p>\n"
                    )

            chapter_html = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml">\n'
                '<head>\n'
                '<meta charset="UTF-8"/>\n'
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
                f'<item '
                f'id="chapter{i + 1}" '
                f'href="{chapter_filename}" '
                f'media-type="application/xhtml+xml"/>'
            )

            spine_items.append(
                f'<itemref '
                f'idref="chapter{i + 1}"/>'
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

            '<item '
            'id="style" '
            'href="style.css" '
            'media-type="text/css"/>'

            f'\n{manifest}\n'

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

    job = get_job(
        job_id
    )

    if job and job.get(
        "running",
        False
    ):

        return (
            "Cannot delete a novel "
            "while it is translating. "
            "Wait until it stops first."
        )

    jobs.pop(
        job_id,
        None
    )

    delete_job_from_supabase(
        job_id
    )

    return redirect(
        url_for("index")
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return {

        "status":
            "ok",

        "gemini_configured":
            bool(GEMINI_API_KEY),

        "gemini_model":
            GEMINI_MODEL,

        "openrouter_configured":
            bool(OPENROUTER_API_KEY),

        "supabase_configured":
            bool(
                SUPABASE_URL
                and SUPABASE_SECRET_KEY
            ),

        "telegram_configured":
            bool(
                TELEGRAM_BOT_TOKEN
                and TELEGRAM_CHAT_ID
            ),

        "automatic_resume":
            True,

        "free_openrouter_only":
            True,

        "translation_priority":
            [
                "Gemini 2.5 Flash",
                "Qwen FREE",
                "DeepSeek FREE",
                "Other OpenRouter $0 models"
            ]
    }


# ============================================================
# MAIN
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
