import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
import secrets
import json

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
# APP CONFIG
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-secret-key"
)

app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

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

# New Supabase secret key
SUPABASE_SECRET_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY",
    ""
).strip()

# Also support the old service_role variable if you used it
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
).strip()

# Keep support for your existing variable
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
# SUPABASE KEY
# ============================================================

SUPABASE_KEY = (
    SUPABASE_SECRET_KEY
    or SUPABASE_SERVICE_ROLE_KEY
    or SUPABASE_PUBLISHABLE_KEY
)


# ============================================================
# MODEL CONFIG
# ============================================================

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
).strip()


# ============================================================
# TRANSLATION SETTINGS
# ============================================================

MAX_CHARS_PER_REQUEST = 9000
REQUEST_DELAY = 2.5

MAX_RETRIES = 5

OPENROUTER_TIMEOUT = 60
GEMINI_TIMEOUT = 180

SUPABASE_TIMEOUT = 30


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
# LOCAL JOB LOCKS
# ============================================================

job_locks = {}
job_locks_lock = threading.Lock()


def get_job_lock(job_id):
    with job_locks_lock:
        if job_id not in job_locks:
            job_locks[job_id] = threading.Lock()

        return job_locks[job_id]


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

.success {
    background: #e5f8e9;
    border-left: 5px solid #1d8a3a;
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

.badge.router {
    background: #fff2d9;
    color: #875b00;
}

.badge.complete {
    background: #e3f7e8;
    color: #16702e;
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
<div class="error">Incorrect password.</div>
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
🤖 Gemini 2.5 Flash → 🟢 Qwen FREE → 🔵 DeepSeek FREE → 🆓 Other OpenRouter FREE models
</p>

<p>
Your progress is saved to Supabase after every completed chapter,
so completed chapters survive Render restarts and redeployments.
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
Preferred Starting Model:
</label>

<select name="model_choice" id="model_choice">

<option value="gemini">
🤖 Gemini 2.5 Flash — Primary / Recommended
</option>

<option value="qwen">
🟢 Qwen FREE — OpenRouter fallback
</option>

<option value="deepseek">
🔵 DeepSeek FREE — OpenRouter fallback
</option>

<option value="openrouter">
🆓 OpenRouter FREE — automatic free-model selection
</option>

</select>

<button type="submit" class="blue">
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


{% for job in jobs %}

<div class="job">

<div class="job-title">
{{ job.filename }}
</div>


{% if job.provider == "gemini" %}

<span class="badge gemini">
🤖 Gemini 2.5 Flash
</span>

{% elif job.provider == "openrouter" %}

<span class="badge router">

🆓 OpenRouter

{% if job.provider_model %}
({{ job.provider_model }})
{% endif %}

</span>

{% endif %}


{% if job.percent >= 100 %}

<span class="badge complete">
✅ COMPLETE
</span>

{% endif %}


<p>
📖 Chapters:
<strong>
{{ job.translated_chapters }}/{{ job.total_chapters }}
</strong>
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

<strong>Status:</strong>

{{ job.status }}

{% if job.provider_model %}

<br>
<strong>Active model:</strong>
{{ job.provider_model }}

{% endif %}

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

You may leave the page.

</div>

<script>
setTimeout(function() {
    location.reload();
}, 5000);
</script>

{% endif %}


{% if not job.running and job.translated_chapters < job.total_chapters %}

<form action="/translate/{{ job.id }}" method="GET">

<button type="submit" class="green">

▶ Continue Translation

</button>

</form>

{% endif %}


{% if job.translations and job.words > 0 %}

<div class="download-box">

<a
class="button green"
href="/download/{{ job.id }}"
>

📥 Download Current EPUB
({{ "{:,}".format(job.words) }} words)

</a>

</div>

{% else %}

<div class="small">

🔒 Download unlocks once the first chapter is translated.

</div>

{% endif %}


<form
action="/delete/{{ job.id }}"
method="POST"
onsubmit="return confirm('Delete this novel permanently?');"
>

<button type="submit" class="danger">

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
        "health"
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

    return render_template_string(
        PAGE,
        authenticated=is_authenticated(),
        login_error=False,
        jobs=get_all_jobs()
    )


@app.route("/login", methods=["POST"])
def login():

    password = request.form.get(
        "password",
        ""
    )

    if SITE_PASSWORD and secrets.compare_digest(
        password,
        SITE_PASSWORD
    ):

        session["authenticated"] = True

        return redirect(
            url_for("index")
        )

    return render_template_string(
        PAGE,
        authenticated=False,
        login_error=True,
        jobs=[]
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

    normal_words = len(
        re.findall(
            r"\b[\w'-]+\b",
            non_cjk
        )
    )

    return cjk_count + normal_words


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
        r"(?im)^(第\s*[0-9一二三四五六七八九十百千万两零]+\s*[章回节]"
        r"|chapter\s+\d+.*)$"
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
            text
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
                text
            )

            chapters.extend(
                parts
            )

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
6. Preserve pronouns consistently.
7. Do not randomly change he/she/they.
8. Keep dialogue formatting.
9. Keep paragraph breaks.
10. Do not add explanations.
11. Do not add translator notes.
12. Do not output Chinese unless a proper name absolutely requires it.
13. Output ONLY the English translation.

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

    for attempt in range(
        MAX_RETRIES
    ):

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

            print(
                "GEMINI ERROR:",
                repr(e)
            )

            error_text = str(e).lower()

            if attempt < MAX_RETRIES - 1:

                if (
                    "429" in error_text
                    or "quota" in error_text
                    or "resource exhausted" in error_text
                    or "503" in error_text
                    or "unavailable" in error_text
                ):

                    sleep_time = (
                        5 * (attempt + 1)
                    )

                else:

                    sleep_time = (
                        2 * (attempt + 1)
                    )

                time.sleep(
                    sleep_time
                )

    raise RuntimeError(
        f"Gemini unavailable or quota exhausted: {last_error}"
    )


# ============================================================
# OPENROUTER MODEL DISCOVERY
# ============================================================

def get_openrouter_models():

    if not OPENROUTER_API_KEY:
        return []

    headers = {
        "Authorization":
            f"Bearer {OPENROUTER_API_KEY}"
    }

    try:

        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:

            print(
                "OpenRouter model list error:",
                response.status_code,
                response.text[:500]
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


def is_free_text_model(model):

    try:

        pricing = model.get(
            "pricing",
            {}
        )

        prompt_price = float(
            pricing.get(
                "prompt",
                1
            )
        )

        completion_price = float(
            pricing.get(
                "completion",
                1
            )
        )

        if prompt_price != 0:
            return False

        if completion_price != 0:
            return False

        architecture = model.get(
            "architecture",
            {}
        )

        input_modalities = architecture.get(
            "input_modalities",
            ["text"]
        )

        output_modalities = architecture.get(
            "output_modalities",
            ["text"]
        )

        if "text" not in input_modalities:
            return False

        if "text" not in output_modalities:
            return False

        return True

    except Exception:

        return False


def choose_free_models():

    models = get_openrouter_models()

    free_models = [
        m
        for m in models
        if is_free_text_model(m)
    ]

    qwen = []
    deepseek = []
    other = []

    for model in free_models:

        model_id = (
            model.get("id", "")
            .lower()
        )

        if (
            "qwen" in model_id
            and "coder" not in model_id
            and "rerank" not in model_id
            and "embed" not in model_id
        ):

            qwen.append(
                model.get("id")
            )

        elif "deepseek" in model_id:

            deepseek.append(
                model.get("id")
            )

        else:

            other.append(
                model.get("id")
            )

    # Remove duplicates while keeping order
    result = []

    for model_id in (
        qwen + deepseek + other
    ):

        if model_id and model_id not in result:

            result.append(
                model_id
            )

    return result


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

    discovered = choose_free_models()

    qwen_models = [
        m
        for m in discovered
        if "qwen" in m.lower()
    ]

    deepseek_models = [
        m
        for m in discovered
        if "deepseek" in m.lower()
    ]

    other_models = [
        m
        for m in discovered
        if (
            "qwen" not in m.lower()
            and "deepseek" not in m.lower()
        )
    ]

    if preferred_category == "qwen":

        ordered = (
            qwen_models
            + deepseek_models
            + other_models
        )

    elif preferred_category == "deepseek":

        ordered = (
            deepseek_models
            + qwen_models
            + other_models
        )

    else:

        ordered = (
            qwen_models
            + deepseek_models
            + other_models
        )

    # Always keep OpenRouter's own free router last.
    if "openrouter/free" not in ordered:

        ordered.append(
            "openrouter/free"
        )

    last_error = None

    for model_id in ordered:

        if not model_id:
            continue

        payload = {
            "model": model_id,

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
                "Trying OpenRouter model:",
                model_id
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

                    message = choices[0].get(
                        "message",
                        {}
                    )

                    translated = message.get(
                        "content",
                        ""
                    )

                    if translated and translated.strip():

                        actual_model = data.get(
                            "model",
                            model_id
                        )

                        return (
                            translated.strip(),
                            actual_model
                        )

            else:

                print(
                    "OpenRouter",
                    model_id,
                    "returned",
                    response.status_code,
                    response.text[:500]
                )

                last_error = RuntimeError(
                    f"OpenRouter {model_id}: "
                    f"{response.status_code}"
                )

        except Exception as e:

            print(
                "OpenRouter model failed:",
                model_id,
                repr(e)
            )

            last_error = e

    raise RuntimeError(
        "All currently available free OpenRouter "
        f"models failed.\n\n{last_error}"
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

        response = requests.post(
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage",

            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message
            },

            timeout=15
        )

        if response.status_code != 200:

            print(
                "Telegram error:",
                response.status_code,
                response.text[:500]
            )

    except Exception as e:

        print(
            "Telegram error:",
            repr(e)
        )


# ============================================================
# SUPABASE
# ============================================================

def supabase_ready():

    return bool(
        SUPABASE_URL
        and SUPABASE_KEY
    )


def supabase_headers():

    return {
        "apikey":
            SUPABASE_KEY,

        "Authorization":
            f"Bearer {SUPABASE_KEY}",

        "Content-Type":
            "application/json",

        "Prefer":
            "return=representation"
    }


def supabase_table_url():

    return (
        SUPABASE_URL.rstrip("/")
        + "/rest/v1/translator_jobs"
    )


def supabase_get_jobs():

    if not supabase_ready():

        raise RuntimeError(
            "Supabase is not configured."
        )

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

        raise RuntimeError(
            "Supabase load failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    return response.json()


def supabase_get_job(job_id):

    if not supabase_ready():

        raise RuntimeError(
            "Supabase is not configured."
        )

    response = requests.get(
        supabase_table_url(),

        headers=supabase_headers(),

        params={
            "select": "*",
            "id": f"eq.{job_id}"
        },

        timeout=SUPABASE_TIMEOUT
    )

    if response.status_code != 200:

        raise RuntimeError(
            "Supabase job load failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    rows = response.json()

    if not rows:
        return None

    return rows[0]


def supabase_insert_job(job):

    if not supabase_ready():

        raise RuntimeError(
            "Supabase is not configured."
        )

    response = requests.post(
        supabase_table_url(),

        headers=supabase_headers(),

        json=job,

        timeout=SUPABASE_TIMEOUT
    )

    if response.status_code not in (
        200,
        201
    ):

        raise RuntimeError(
            "Supabase save failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    rows = response.json()

    if rows:
        return rows[0]

    return job


def supabase_update_job(
    job_id,
    updates
):

    if not supabase_ready():

        raise RuntimeError(
            "Supabase is not configured."
        )

    response = requests.patch(
        supabase_table_url(),

        headers=supabase_headers(),

        params={
            "id":
                f"eq.{job_id}"
        },

        json=updates,

        timeout=SUPABASE_TIMEOUT
    )

    if response.status_code not in (
        200,
        204
    ):

        raise RuntimeError(
            "Supabase update failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    rows = []

    try:
        rows = response.json()
    except Exception:
        pass

    if rows:
        return rows[0]

    return None


def supabase_delete_job(job_id):

    if not supabase_ready():

        raise RuntimeError(
            "Supabase is not configured."
        )

    response = requests.delete(
        supabase_table_url(),

        headers=supabase_headers(),

        params={
            "id":
                f"eq.{job_id}"
        },

        timeout=SUPABASE_TIMEOUT
    )

    if response.status_code not in (
        200,
        204
    ):

        raise RuntimeError(
            "Supabase delete failed: "
            f"{response.status_code} "
            f"{response.text}"
        )


# ============================================================
# JOB HELPERS
# ============================================================

def get_all_jobs():

    try:

        return supabase_get_jobs()

    except Exception as e:

        print(
            "Could not load Supabase jobs:",
            repr(e)
        )

        return []


def calculate_words_from_translations(
    translations
):

    if not translations:
        return 0

    combined = "\n\n".join(
        translations
    )

    return count_words(
        combined
    )


# ============================================================
# TRANSLATION WORKER
# ============================================================

def translation_worker(
    job_id
):

    lock = get_job_lock(
        job_id
    )

    if not lock.acquire(
        blocking=False
    ):

        print(
            "Job already running:",
            job_id
        )

        return

    try:

        job = supabase_get_job(
            job_id
        )

        if not job:

            print(
                "Job not found:",
                job_id
            )

            return

        # ----------------------------------------------------
        # Mark running
        # ----------------------------------------------------

        supabase_update_job(
            job_id,
            {
                "running": True,
                "error": None,
                "status":
                    "Translation worker started."
            }
        )

        filename = job.get(
            "filename",
            "Novel"
        )

        chapters = job.get(
            "chapters",
            []
        )

        translations = job.get(
            "translations",
            []
        ) or []

        translated_chapters = int(
            job.get(
                "translated_chapters",
                0
            ) or 0
        )

        total_chapters = int(
            job.get(
                "total_chapters",
                len(chapters)
            ) or len(chapters)
        )

        provider = job.get(
            "provider",
            "gemini"
        )

        provider_model = job.get(
            "provider_model"
        )

        # ----------------------------------------------------
        # Safety correction
        # ----------------------------------------------------

        if translated_chapters > len(
            translations
        ):

            translated_chapters = len(
                translations
            )

        # ----------------------------------------------------
        # Main loop
        # ----------------------------------------------------

        while translated_chapters < total_chapters:

            # Re-read from Supabase before each chapter.
            # This makes progress persistent.
            fresh = supabase_get_job(
                job_id
            )

            if fresh:

                chapters = fresh.get(
                    "chapters",
                    chapters
                )

                translations = fresh.get(
                    "translations",
                    translations
                ) or []

                translated_chapters = int(
                    fresh.get(
                        "translated_chapters",
                        translated_chapters
                    ) or translated_chapters
                )

                provider = fresh.get(
                    "provider",
                    provider
                )

                provider_model = fresh.get(
                    "provider_model",
                    provider_model
                )

            if translated_chapters >= len(
                chapters
            ):
                break

            index = translated_chapters

            original_chapter = chapters[
                index
            ]

            pieces = split_large_text(
                original_chapter
            )

            translated_pieces = []

            # ------------------------------------------------
            # Translate each chapter piece
            # ------------------------------------------------

            for piece_number, piece in enumerate(
                pieces
            ):

                status = (
                    f"Translating chapter "
                    f"{index + 1}/{total_chapters} "
                    f"(part "
                    f"{piece_number + 1}/"
                    f"{len(pieces)})..."
                )

                supabase_update_job(
                    job_id,
                    {
                        "running": True,
                        "status": status,
                        "error": None
                    }
                )

                # --------------------------------------------
                # Gemini primary
                # --------------------------------------------

                if provider == "gemini":

                    try:

                        translated = (
                            translate_with_gemini(
                                piece
                            )
                        )

                        active_provider = (
                            "gemini"
                        )

                        active_model = (
                            GEMINI_MODEL
                        )

                    except Exception as gemini_error:

                        print(
                            "Gemini failed. "
                            "Falling back to "
                            "OpenRouter:",
                            repr(gemini_error)
                        )

                        send_telegram(
                            "⚠️ Novel Translator\n\n"
                            f"{filename}\n\n"
                            "Gemini 2.5 Flash is unavailable "
                            "or its quota was reached.\n\n"
                            "Switching to OpenRouter FREE."
                        )

                        if not OPENROUTER_API_KEY:

                            raise RuntimeError(
                                "Gemini failed and "
                                "OPENROUTER_API_KEY is missing.\n\n"
                                f"{gemini_error}"
                            )

                        translated, actual_model = (
                            translate_with_openrouter(
                                piece,
                                preferred_category="qwen"
                            )
                        )

                        active_provider = (
                            "openrouter"
                        )

                        active_model = (
                            actual_model
                        )

                        provider = (
                            active_provider
                        )

                        provider_model = (
                            active_model
                        )

                # --------------------------------------------
                # OpenRouter
                # --------------------------------------------

                else:

                    preferred_category = (
                        "qwen"
                    )

                    if provider_model and (
                        "deepseek"
                        in provider_model.lower()
                    ):

                        preferred_category = (
                            "deepseek"
                        )

                    translated, actual_model = (
                        translate_with_openrouter(
                            piece,
                            preferred_category
                            =preferred_category
                        )
                    )

                    active_provider = (
                        "openrouter"
                    )

                    active_model = (
                        actual_model
                    )

                    provider = (
                        active_provider
                    )

                    provider_model = (
                        active_model
                    )

                translated_pieces.append(
                    translated
                )

                # Save active provider/model.
                supabase_update_job(
                    job_id,
                    {
                        "provider":
                            active_provider,

                        "provider_model":
                            active_model,

                        "running":
                            True,

                        "status":
                            status
                            + "\nActive: "
                            + active_model
                    }
                )

                time.sleep(
                    REQUEST_DELAY
                )

            # ------------------------------------------------
            # Chapter completed
            # ------------------------------------------------

            final_translation = (
                "\n\n".join(
                    translated_pieces
                ).strip()
            )

            translations.append(
                final_translation
            )

            translated_chapters += 1

            words = (
                calculate_words_from_translations(
                    translations
                )
            )

            percent = int(
                (
                    translated_chapters
                    / total_chapters
                ) * 100
            )

            if percent > 100:
                percent = 100

            model_display = (
                provider_model
                or GEMINI_MODEL
            )

            status = (
                f"Completed chapter "
                f"{translated_chapters}/"
                f"{total_chapters}. "
                f"{words:,} English words. "
                f"Active: {model_display}"
            )

            # ------------------------------------------------
            # THIS IS THE IMPORTANT SAVE
            # ------------------------------------------------
            #
            # Completed chapter is now permanently stored
            # in Supabase.
            #

            supabase_update_job(
                job_id,
                {
                    "translations":
                        translations,

                    "translated_chapters":
                        translated_chapters,

                    "total_chapters":
                        total_chapters,

                    "words":
                        words,

                    "percent":
                        percent,

                    "status":
                        status,

                    "error":
                        None,

                    "running":
                        True,

                    "provider":
                        provider,

                    "provider_model":
                        provider_model
                }
            )

            # Telegram chapter progress
            if (
                translated_chapters == 1
                or translated_chapters == total_chapters
                or translated_chapters % 10 == 0
            ):

                send_telegram(
                    "📚 Novel Translator\n\n"
                    f"{filename}\n\n"
                    f"Progress: "
                    f"{translated_chapters}/"
                    f"{total_chapters} chapters\n"
                    f"English words: "
                    f"{words:,}\n"
                    f"Model: {model_display}"
                )

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        final_job = supabase_get_job(
            job_id
        )

        final_words = int(
            (
                final_job or {}
            ).get(
                "words",
                0
            ) or 0
        )

        supabase_update_job(
            job_id,
            {
                "running":
                    False,

                "percent":
                    100,

                "translated_chapters":
                    total_chapters,

                "status":
                    "Translation complete!",

                "error":
                    None,

                "words":
                    final_words
            }
        )

        send_telegram(
            "✅ Novel Translation Complete!\n\n"
            f"{filename}\n\n"
            f"Chapters: {total_chapters}\n"
            f"English words: {final_words:,}\n\n"
            "Your translated EPUB is ready to download."
        )

        print(
            "Translation complete:",
            filename
        )

    except Exception as e:

        print(
            "TRANSLATION WORKER ERROR:",
            repr(e)
        )

        try:

            job = supabase_get_job(
                job_id
            )

            filename = (
                job.get(
                    "filename",
                    "Novel"
                )
                if job
                else "Novel"
            )

            # Keep running FALSE so the website
            # shows Continue Translation.
            supabase_update_job(
                job_id,
                {
                    "running":
                        False,

                    "error":
                        str(e),

                    "status":
                        "Translation stopped."
                }
            )

            send_telegram(
                "❌ Novel Translator ERROR\n\n"
                f"{filename}\n\n"
                f"{e}"
            )

        except Exception as save_error:

            print(
                "Could not save worker error:",
                repr(save_error)
            )

    finally:

        lock.release()


# ============================================================
# STARTUP RESUME
# ============================================================

def resume_interrupted_jobs():

    if not supabase_ready():

        print(
            "Supabase not configured. "
            "Cannot resume jobs."
        )

        return

    try:

        jobs = supabase_get_jobs()

        for job in jobs:

            if not job.get(
                "running",
                False
            ):

                continue

            job_id = job.get(
                "id"
            )

            if not job_id:
                continue

            print(
                "Resuming interrupted job:",
                job.get(
                    "filename"
                ),
                job_id
            )

            # Don't start all jobs simultaneously.
            # Start each in its own daemon thread.
            thread = threading.Thread(
                target=translation_worker,
                args=(job_id,),
                daemon=True
            )

            thread.start()

    except Exception as e:

        print(
            "Startup resume error:",
            repr(e)
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

    chosen_model = request.form.get(
        "model_choice",
        "gemini"
    )

    if not uploaded or not uploaded.filename:

        return redirect(
            url_for("index")
        )

    try:

        if not supabase_ready():

            raise RuntimeError(
                "Supabase is not configured. "
                "Check SUPABASE_URL and your "
                "Supabase secret/service key."
            )

        data = uploaded.read()

        chapters = parse_uploaded_file(
            uploaded.filename,
            data
        )

        original_words = sum(
            count_words(chapter)
            for chapter in chapters
        )

        job_id = str(
            uuid.uuid4()
        )

        if chosen_model == "gemini":

            provider = "gemini"

            provider_model = (
                GEMINI_MODEL
            )

        elif chosen_model == "deepseek":

            provider = "openrouter"

            provider_model = (
                "deepseek"
            )

        else:

            provider = "openrouter"

            provider_model = (
                "qwen"
                if chosen_model == "qwen"
                else "openrouter/free"
            )

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

        supabase_insert_job(
            job
        )

        send_telegram(
            "📤 Novel Uploaded\n\n"
            f"{uploaded.filename}\n\n"
            f"Chapters: {len(chapters)}\n"
            f"Original words: {original_words:,}\n\n"
            "Ready to translate."
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


# ============================================================
# START TRANSLATION
# ============================================================

@app.route(
    "/translate/<job_id>"
)
def translate(job_id):

    job = supabase_get_job(
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

    translated = int(
        job.get(
            "translated_chapters",
            0
        ) or 0
    )

    total = int(
        job.get(
            "total_chapters",
            0
        ) or 0
    )

    if translated >= total:

        return redirect(
            url_for("index")
        )

    # Mark running BEFORE starting thread.
    supabase_update_job(
        job_id,
        {
            "running":
                True,

            "error":
                None,

            "status":
                "Starting translation..."
        }
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


# ============================================================
# EPUB DOWNLOAD
# ============================================================

@app.route(
    "/download/<job_id>"
)
def download(job_id):

    job = supabase_get_job(
        job_id
    )

    if not job:

        return (
            "<h2>Novel not found</h2>"
        )

    translations = job.get(
        "translations",
        []
    ) or []

    if not translations:

        return (
            "<h2>Nothing has been translated yet.</h2>"
            "<p><a href='/'>Go back</a></p>"
        )

    try:

        filename = job.get(
            "filename",
            "translated_novel.epub"
        )

        epub_bytes = create_epub(
            filename,
            translations
        )

        base_name = os.path.splitext(
            filename
        )[0]

        download_name = (
            f"{base_name}_translated.epub"
        )

        return send_file(
            io.BytesIO(epub_bytes),

            mimetype="application/epub+zip",

            as_attachment=True,

            download_name=download_name
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
# CREATE EPUB
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

        # EPUB requires mimetype to be the first
        # uncompressed file.
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

            body = "".join(
                f"<p>{p.strip()}</p>\n"
                for p in paragraphs
                if p.strip()
            )

            chapter_html = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html '
                'xmlns="http://www.w3.org/1999/xhtml">\n'
                '<head>\n'
                '<meta charset="UTF-8"/>\n'
                f'<title>{html.escape(title)}</title>\n'
                '<link rel="stylesheet" '
                'type="text/css" '
                'href="style.css"/>\n'
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
            "\n"
            "h1, h2 { "
            "text-align: center; "
            "}"
            "\n"
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

        book_id = str(
            uuid.uuid4()
        )

        opf = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package version="3.0" '
            'xmlns="http://www.idpf.org/2007/opf" '
            'unique-identifier="BookID">\n'

            '<metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">\n'

            f'<dc:identifier id="BookID">'
            f'{book_id}'
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

        job = supabase_get_job(
            job_id
        )

        if job:

            send_telegram(
                "🗑️ Novel Deleted\n\n"
                f"{job.get('filename', 'Novel')}"
            )

        supabase_delete_job(
            job_id
        )

    except Exception as e:

        print(
            "Delete error:",
            repr(e)
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

        "openrouter_configured":
            bool(OPENROUTER_API_KEY),

        "supabase_configured":
            bool(supabase_ready()),

        "telegram_configured":
            bool(
                TELEGRAM_BOT_TOKEN
                and TELEGRAM_CHAT_ID
            ),

        "password_enabled":
            bool(SITE_PASSWORD),

        "gemini_model":
            GEMINI_MODEL,

        "translation_order":
            [
                "Gemini 2.5 Flash",
                "Qwen FREE",
                "DeepSeek FREE",
                "Other OpenRouter FREE",
                "OpenRouter FREE Router"
            ]
    }


# ============================================================
# STARTUP
# ============================================================

def startup():

    print("")
    print("========================================")
    print("FREE NOVEL TRANSLATOR")
    print("========================================")
    print(
        "Gemini configured:",
        bool(GEMINI_API_KEY)
    )
    print(
        "OpenRouter configured:",
        bool(OPENROUTER_API_KEY)
    )
    print(
        "Supabase configured:",
        bool(supabase_ready())
    )
    print(
        "Telegram configured:",
        bool(
            TELEGRAM_BOT_TOKEN
            and TELEGRAM_CHAT_ID
        )
    )
    print(
        "Gemini model:",
        GEMINI_MODEL
    )
    print("========================================")
    print("")

    # Resume jobs after Render restart.
    resume_interrupted_jobs()


# Run startup once when Gunicorn imports app.py.
try:

    startup()

except Exception as startup_error:

    print(
        "Startup initialization error:",
        repr(startup_error)
    )


# ============================================================
# LOCAL DEVELOPMENT
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
