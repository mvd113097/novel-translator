import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
import json
import urllib.request
import urllib.error
import urllib.parse
from functools import wraps

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
# CONFIGURATION
# ============================================================

app = Flask(__name__)

# Secret used for the website password session.
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.environ.get(
        "SITE_PASSWORD",
        "change-this-secret"
    )
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
)

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
# AI MODEL SETTINGS
# ============================================================

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()

OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "qwen/qwen3-32b:free"
).strip()


# ============================================================
# TRANSLATION SETTINGS
# ============================================================

# Keep requests reasonably small.
MAX_CHARS_PER_REQUEST = 7000

# User requested cumulative download after 30,000 English words.
DOWNLOAD_MIN_WORDS = 30000

# Delay between AI requests.
REQUEST_DELAY = 3

# Retry attempts for a provider.
MAX_RETRIES = 1

# OpenRouter timeout.
OPENROUTER_TIMEOUT = 180


# ============================================================
# GEMINI CLIENT
# ============================================================

gemini_client = None

if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )

        print(
            "Gemini client initialized."
        )

        print(
            "Gemini model:",
            GEMINI_MODEL
        )

    except Exception as e:

        print(
            "GEMINI CLIENT ERROR:",
            repr(e)
        )


# ============================================================
# IN-MEMORY JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()


# ============================================================
# LOGIN HTML
# ============================================================

LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Novel Translator - Login</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    margin: 0;
    padding: 20px;
}

.container {
    max-width: 430px;
    margin: 70px auto;
    background: white;
    padding: 28px;
    border-radius: 16px;
    box-shadow: 0 3px 18px rgba(0,0,0,0.12);
}

h1 {
    text-align: center;
}

input,
button {
    width: 100%;
    box-sizing: border-box;
    padding: 14px;
    margin-top: 12px;
    border-radius: 9px;
    border: 1px solid #ccc;
    font-size: 16px;
}

button {
    background: #222;
    color: white;
    border: none;
    cursor: pointer;
}

button:hover {
    background: #444;
}

.error {
    background: #ffe5e5;
    color: #990000;
    padding: 12px;
    border-radius: 8px;
    margin-top: 15px;
}

.small {
    text-align: center;
    color: #777;
    font-size: 14px;
}

</style>
</head>

<body>

<div class="container">

<h1>📚 Novel Translator</h1>

<p class="small">
Enter the site password to continue.
</p>

<form method="POST">

<input
    type="password"
    name="password"
    placeholder="Password"
    autocomplete="current-password"
    required
>

<button type="submit">
    🔐 Enter
</button>

</form>

{% if error %}

<div class="error">
{{ error }}
</div>

{% endif %}

</div>

</body>
</html>
"""


# ============================================================
# MAIN HTML
# ============================================================

PAGE = """
<!DOCTYPE html>
<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Novel Translator</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    margin: 0;
    padding: 15px;
}

.container {
    max-width: 720px;
    margin: auto;
    background: white;
    padding: 22px;
    border-radius: 15px;
    box-shadow: 0 3px 15px rgba(0,0,0,0.10);
}

h1 {
    margin-top: 0;
}

h2 {
    margin-bottom: 8px;
}

input,
button {
    width: 100%;
    box-sizing: border-box;
    padding: 14px;
    margin-top: 10px;
    border-radius: 8px;
    border: 1px solid #ccc;
    font-size: 16px;
}

button {
    background: #222;
    color: white;
    cursor: pointer;
    border: none;
}

button:hover {
    background: #444;
}

.logout {
    background: #777;
}

.job {
    border: 1px solid #ddd;
    padding: 15px;
    margin-top: 15px;
    border-radius: 10px;
}

.status {
    margin-top: 12px;
    padding: 12px;
    background: #f0f0f0;
    border-radius: 8px;
    white-space: pre-wrap;
}

.error {
    background: #ffe5e5;
    color: #900;
}

.success {
    background: #e5ffe8;
    color: #176b22;
}

.warning {
    background: #fff5d6;
    color: #735500;
}

.progress {
    margin-top: 15px;
    background: #ddd;
    border-radius: 10px;
    overflow: hidden;
    height: 26px;
}

.bar {
    height: 26px;
    background: #4caf50;
    text-align: center;
    color: white;
    line-height: 26px;
    min-width: 0;
}

.small {
    color: #666;
    font-size: 14px;
}

.provider {
    display: inline-block;
    padding: 5px 9px;
    border-radius: 7px;
    background: #eee;
    font-size: 13px;
    margin-top: 5px;
}

a.button {
    display: block;
    margin-top: 10px;
    padding: 13px;
    background: #222;
    color: white;
    text-decoration: none;
    text-align: center;
    border-radius: 8px;
}

a.button:hover {
    background: #444;
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

<h1>📚 Novel Translator</h1>

<p>
Upload a TXT or EPUB novel and translate it to English.
</p>

<div class="status">

<b>Translation system</b>

<br><br>

1. Gemini is tried first.

<br>

2. If Gemini is exhausted or unavailable,
OpenRouter Qwen automatically takes over.

<br>

3. Once Qwen takes over,
the job stays on Qwen.

<br>

4. Translation is cumulative.

<br>

5. Download becomes available after
{{ "{:,}".format(min_words) }} English words.

</div>


<form action="/upload"
      method="POST"
      enctype="multipart/form-data">

<input
    type="file"
    name="file"
    accept=".txt,.epub"
    required
>

<button type="submit">
    📤 Upload Novel
</button>

</form>


<form action="/logout"
      method="POST">

<button class="logout"
        type="submit">
    🔒 Lock Website
</button>

</form>


{% if jobs %}

<hr>

<h2>Your Novels</h2>


{% for job_id, job in jobs.items() %}

<div class="job">

<strong>
{{ job.filename }}
</strong>


<p>
📖 Chapters:
{{ job.translated_chapters }}/{{ job.total_chapters }}
</p>


<p>
📝 English words:
{{ "{:,}".format(job.words) }}
</p>


<p>
{% if job.provider == "qwen" %}

<span class="provider">
🤖 OpenRouter Qwen
</span>

{% elif job.provider == "gemini" %}

<span class="provider">
✨ Gemini
</span>

{% else %}

<span class="provider">
⏳ Not started
</span>

{% endif %}
</p>


<p>
<b>Status:</b>
{{ job.status }}
</p>


{% if job.running %}

<div class="progress">

<div class="bar"
     style="width: {{ job.percent }}%;">

{{ job.percent }}%

</div>

</div>

{% endif %}


{% if job.error %}

<div class="status error">

{{ job.error }}

</div>

{% endif %}


{% if job.fallback_message %}

<div class="status warning">

{{ job.fallback_message }}

</div>

{% endif %}


{% if job.running %}

<div class="status">

Translation is running.

Keep this page open or refresh it later.

</div>

<script>

setTimeout(function() {
    location.reload();
}, 5000);

</script>

{% endif %}


{% if not job.running and
      job.translated_chapters < job.total_chapters %}

<form action="/translate/{{ job_id }}"
      method="GET">

<button type="submit">

▶ Continue Translation

</button>

</form>

{% endif %}


{% if job.words >= min_words %}

<a class="button"
   href="/download/{{ job_id }}">

📥 Download Current EPUB
({{ "{:,}".format(job.words) }} words)

</a>

{% else %}

<div class="status">

🔒 Download unlocks at
{{ "{:,}".format(min_words) }}
English words.

<br><br>

Current:
{{ "{:,}".format(job.words) }}
words.

</div>

{% endif %}


{% if job.translated_chapters == job.total_chapters
      and not job.error %}

<a class="button"
   href="/download/{{ job_id }}">

📚 Download Complete EPUB

</a>

{% endif %}


<form action="/delete/{{ job_id }}"
      method="POST"
      onsubmit="return confirm('Delete this novel?');">

<button type="submit">

🗑️ Delete Novel

</button>

</form>


</div>

{% endfor %}

{% endif %}

</div>

</body>

</html>
"""


# ============================================================
# LOGIN REQUIRED DECORATOR
# ============================================================

def login_required(function):

    @wraps(function)
    def wrapped(*args, **kwargs):

        if not SITE_PASSWORD:

            # If no password was configured,
            # allow access instead of locking the site.
            return function(
                *args,
                **kwargs
            )

        if session.get("authenticated"):

            return function(
                *args,
                **kwargs
            )

        return redirect(
            url_for("login")
        )

    return wrapped


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if not SITE_PASSWORD:

        session["authenticated"] = True

        return redirect(
            url_for("index")
        )

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if password == SITE_PASSWORD:

            session["authenticated"] = True

            return redirect(
                url_for("index")
            )

        return render_template_string(
            LOGIN_PAGE,
            error="Incorrect password."
        )

    return render_template_string(
        LOGIN_PAGE,
        error=None
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route(
    "/logout",
    methods=["POST"]
)
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# TEXT CLEANING
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


# ============================================================
# WORD COUNT
# ============================================================

def count_words(text):

    return len(
        re.findall(
            r"\b[\w'-]+\b",
            text
        )
    )


# ============================================================
# SPLIT TEXT
# ============================================================

def split_large_text(
    text,
    max_chars=MAX_CHARS_PER_REQUEST
):

    text = clean_text(text)

    if len(text) <= max_chars:

        return [text]

    paragraphs = text.split(
        "\n"
    )

    chunks = []

    current = ""

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        # Handle an individual paragraph
        # that is itself too large.
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

        if current:

            candidate = (
                current
                + "\n"
                + paragraph
            )

        else:

            candidate = paragraph

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

    text = clean_text(
        text
    )

    pattern = re.compile(
        r"(?im)^(第\s*[0-9一二三四五六七八九十百千万]+\s*[章回节]|"
        r"chapter\s+\d+.*)$"
    )

    matches = list(
        pattern.finditer(
            text
        )
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
            "No readable chapters found."
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
            name
            for name in names
            if name.lower().endswith(
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
                r"</(p|div|br|h1|h2|h3|li)>",
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
            "No readable chapters were found "
            "in the EPUB."
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

    if lower.endswith(
        ".txt"
    ):

        return parse_txt(
            data
        )

    if lower.endswith(
        ".epub"
    ):

        return parse_epub(
            data
        )

    raise ValueError(
        "Only TXT and EPUB files are supported."
    )


# ============================================================
# TELEGRAM NOTIFICATION
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        return

    if not TELEGRAM_CHAT_ID:
        return

    try:

        url = (
            "https://api.telegram.org/bot"
            + TELEGRAM_BOT_TOKEN
            + "/sendMessage"
        )

        payload = urllib.parse.urlencode(
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            }
        ).encode(
            "utf-8"
        )

        request = urllib.request.Request(
            url,
            data=payload,
            method="POST"
        )

        request.add_header(
            "Content-Type",
            "application/x-www-form-urlencoded"
        )

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            response.read()

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            repr(e)
        )


# ============================================================
# GEMINI QUOTA / FAILURE DETECTION
# ============================================================

def should_fallback_to_qwen(error):

    text = str(error).lower()

    fallback_terms = [
        "quota",
        "429",
        "resource exhausted",
        "rate limit",
        "too many requests",
        "not found",
        "404",
        "not_found",
        "no longer available",
        "unavailable",
        "permission denied",
        "forbidden",
        "api key",
        "invalid argument"
    ]

    return any(
        term in text
        for term in fallback_terms
    )


# ============================================================
# GEMINI TRANSLATION
# ============================================================

def translate_with_gemini(
    text
):

    if not gemini_client:

        raise RuntimeError(
            "Gemini client is not available."
        )

    prompt = f"""
You are a professional Chinese-to-English web novel translator.

Translate the Chinese text below into natural, fluent English.

IMPORTANT RULES:

1. Translate EVERYTHING.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Do NOT skip dialogue.
5. Preserve all story details.
6. Preserve paragraph breaks when possible.
7. Keep character names consistent.
8. Keep gender and pronouns consistent.
9. Translate names naturally but consistently.
10. Preserve the tone of a web novel.
11. Do not add explanations.
12. Do not discuss the translation.
13. Output ONLY the English translation.
14. Do not use Markdown code blocks.
15. Do not say "Here is the translation".

Chinese text:

{text}
"""

    last_error = None

    for attempt in range(
        MAX_RETRIES + 1
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

            if translated:

                translated = translated.strip()

                if translated:

                    return translated

            # Extra compatibility handling.
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

                    result = "\n".join(
                        pieces
                    ).strip()

                    if result:

                        return result

            raise RuntimeError(
                "Gemini returned no translation text."
            )

        except Exception as e:

            last_error = e

            print(
                "GEMINI ERROR:",
                repr(e)
            )

            if should_fallback_to_qwen(
                e
            ):

                raise RuntimeError(
                    "GEMINI_FALLBACK_REQUIRED: "
                    + str(e)
                )

            if attempt < MAX_RETRIES:

                time.sleep(
                    2
                )

    raise RuntimeError(
        "Gemini translation failed: "
        + str(last_error)
    )


# ============================================================
# OPENROUTER QWEN TRANSLATION
# ============================================================

def translate_with_qwen(
    text
):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY is missing."
        )

    prompt = f"""
You are a professional Chinese-to-English web novel translator.

Translate the Chinese text below into natural, fluent English.

IMPORTANT RULES:

1. Translate EVERYTHING.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Do NOT skip dialogue.
5. Preserve all story details.
6. Preserve paragraph breaks when possible.
7. Keep character names consistent.
8. Keep gender and pronouns consistent.
9. Translate names naturally but consistently.
10. Preserve the tone of a web novel.
11. Do not add explanations.
12. Do not discuss the translation.
13. Output ONLY the English translation.
14. Do not use Markdown code blocks.
15. Do not say "Here is the translation".

Chinese text:

{text}
"""

    payload = {
        "model": OPENROUTER_MODEL,

        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert "
                    "Chinese-to-English "
                    "web novel translator."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.2,

        "stream": False
    }

    body = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        method="POST"
    )

    request.add_header(
        "Authorization",
        "Bearer "
        + OPENROUTER_API_KEY
    )

    request.add_header(
        "Content-Type",
        "application/json"
    )

    request.add_header(
        "HTTP-Referer",
        "https://novel-translator-i8wp.onrender.com"
    )

    request.add_header(
        "X-Title",
        "Novel Translator"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=OPENROUTER_TIMEOUT
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

        data = json.loads(
            raw
        )

    except urllib.error.HTTPError as e:

        try:

            error_body = e.read().decode(
                "utf-8",
                errors="replace"
            )

        except Exception:

            error_body = str(e)

        raise RuntimeError(
            "OpenRouter HTTP "
            + str(e.code)
            + ": "
            + error_body
        )

    except Exception as e:

        raise RuntimeError(
            "OpenRouter request failed: "
            + str(e)
        )

    try:

        choices = data.get(
            "choices",
            []
        )

        if not choices:

            raise RuntimeError(
                "OpenRouter returned no choices: "
                + json.dumps(
                    data
                )[:2000]
            )

        message = choices[0].get(
            "message",
            {}
        )

        translated = message.get(
            "content"
        )

        if isinstance(
            translated,
            list
        ):

            pieces = []

            for item in translated:

                if isinstance(
                    item,
                    dict
                ):

                    if item.get(
                        "type"
                    ) == "text":

                        pieces.append(
                            item.get(
                                "text",
                                ""
                            )
                        )

            translated = "\n".join(
                pieces
            )

        if not translated:

            raise RuntimeError(
                "OpenRouter returned empty translation."
            )

        return str(
            translated
        ).strip()

    except Exception as e:

        raise RuntimeError(
            "Could not read OpenRouter response: "
            + str(e)
        )


# ============================================================
# UNIFIED TRANSLATION
# ============================================================

def translate_text(
    job,
    text
):

    # ========================================================
    # IMPORTANT BEHAVIOR:
    #
    # If the job has already switched to Qwen,
    # NEVER try Gemini again for that job.
    # ========================================================

    if job.get(
        "provider"
    ) == "qwen":

        print(
            "Using OpenRouter Qwen."
        )

        return translate_with_qwen(
            text
        )

    # ========================================================
    # First attempt Gemini.
    # ========================================================

    try:

        job["provider"] = "gemini"

        print(
            "Trying Gemini..."
        )

        result = translate_with_gemini(
            text
        )

        return result

    except Exception as gemini_error:

        print(
            "Gemini failed:",
            repr(gemini_error)
        )

        # ====================================================
        # Switch to Qwen.
        # ====================================================

        job["provider"] = "qwen"

        job["fallback_message"] = (
            "Gemini is unavailable or its quota is exhausted. "
            "Automatically switched to OpenRouter Qwen."
        )

        job["status"] = (
            "Gemini unavailable. "
            "Switching to OpenRouter Qwen..."
        )

        print(
            "GEMINI UNAVAILABLE."
        )

        print(
            "SWITCHING TO OPENROUTER QWEN."
        )

        return translate_with_qwen(
            text
        )


# ============================================================
# TRANSLATION WORKER
# ============================================================

def translation_worker(
    job_id
):

    job = jobs.get(
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

        if job.get(
            "provider"
        ) not in (
            "gemini",
            "qwen"
        ):

            job["provider"] = None

        send_telegram(
            "📚 Novel translation started\n\n"
            + job["filename"]
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

                provider_name = (
                    "OpenRouter Qwen"
                    if job.get(
                        "provider"
                    ) == "qwen"
                    else "Gemini"
                )

                job["status"] = (
                    "Translating chapter "
                    + str(index + 1)
                    + "/"
                    + str(total)
                    + " - part "
                    + str(piece_number + 1)
                    + "/"
                    + str(len(pieces))
                    + " using "
                    + provider_name
                    + "..."
                )

                translated = translate_text(
                    job,
                    piece
                )

                translated_pieces.append(
                    translated
                )

                # Small delay between API requests.
                time.sleep(
                    REQUEST_DELAY
                )

            final_translation = (
                "\n\n".join(
                    translated_pieces
                ).strip()
            )

            job["translations"].append(
                final_translation
            )

            job["translated_chapters"] += 1

            all_translations = "\n\n".join(
                job["translations"]
            )

            job["words"] = count_words(
                all_translations
            )

            job["percent"] = int(
                (
                    job["translated_chapters"]
                    /
                    total
                ) * 100
            )

            provider_name = (
                "OpenRouter Qwen"
                if job.get(
                    "provider"
                ) == "qwen"
                else "Gemini"
            )

            job["status"] = (
                "Completed chapter "
                + str(
                    job["translated_chapters"]
                )
                + "/"
                + str(total)
                + ". "
                + str(
                    job["words"]
                )
                + " English words translated "
                + "using "
                + provider_name
                + "."
            )

        job["percent"] = 100

        job["status"] = (
            "Translation complete! "
            + str(
                job["words"]
            )
            + " English words."
        )

        send_telegram(
            "✅ Translation complete\n\n"
            + job["filename"]
            + "\n\n"
            + str(
                job["words"]
            )
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

        send_telegram(
            "❌ Translation error\n\n"
            + job["filename"]
            + "\n\n"
            + str(e)
        )

    finally:

        job["running"] = False


# ============================================================
# HOME
# ============================================================

@app.route("/")
@login_required
def index():

    return render_template_string(
        PAGE,
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
@login_required
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

            "id": job_id,

            "filename": uploaded.filename,

            "chapters": chapters,

            "translations": [],

            "translated_chapters": 0,

            "total_chapters": len(
                chapters
            ),

            "words": 0,

            "percent": 0,

            "status": (
                "Uploaded. Ready to translate."
            ),

            "error": None,

            "fallback_message": None,

            "running": False,

            "provider": None
        }

        print(
            "Uploaded "
            + uploaded.filename
            + ": "
            + str(
                len(chapters)
            )
            + " chapters"
        )

        send_telegram(
            "📤 Novel uploaded\n\n"
            + uploaded.filename
            + "\n\n"
            + str(
                len(chapters)
            )
            + " chapters"
        )

        return redirect(
            url_for("index")
        )

    except Exception as e:

        return """
        <h2>Upload Error</h2>
        <p>
        """
        + html.escape(
            str(e)
        )
        + """
        </p>
        <p>
        <a href="/">Go back</a>
        </p>
        """


# ============================================================
# START / CONTINUE TRANSLATION
# ============================================================

@app.route(
    "/translate/<job_id>"
)
@login_required
def translate(
    job_id
):

    job = jobs.get(
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

    # IMPORTANT:
    #
    # If this job already switched to Qwen,
    # it stays on Qwen.
    #
    # If it has never started, Gemini is tried first.

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
# DOWNLOAD EPUB
# ============================================================

@app.route(
    "/download/<job_id>"
)
@login_required
def download(
    job_id
):

    job = jobs.get(
        job_id
    )

    if not job:

        return redirect(
            url_for("index")
        )

    if not job["translations"]:

        return (
            "Nothing translated yet.",
            400
        )

    if job["words"] < DOWNLOAD_MIN_WORDS:

        return (
            "Download unlocks after "
            + str(
                DOWNLOAD_MIN_WORDS
            )
            + " English words. "
            + "Current: "
            + str(
                job["words"]
            ),
            403
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
            mimetype=(
                "application/epub+zip"
            ),
            as_attachment=True,
            download_name=output_name
        )

    except Exception as e:

        return """
        <h2>EPUB creation error</h2>
        <p>
        """
        + html.escape(
            str(e)
        )
        + """
        </p>
        <p>
        <a href="/">Go back</a>
        </p>
        """


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

        # ====================================================
        # EPUB MIME TYPE
        # ====================================================

        epub.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED
        )

        # ====================================================
        # CONTAINER
        # ====================================================

        container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container
version="1.0"
xmlns="urn:oasis:names:tc:opendocument:xmlns:container">

<rootfiles>

<rootfile
full-path="OEBPS/content.opf"
media-type="application/oebps-package+xml"/>

</rootfiles>

</container>
"""

        epub.writestr(
            "META-INF/container.xml",
            container_xml
        )

        # ====================================================
        # CHAPTERS
        # ====================================================

        manifest_items = []
        spine_items = []

        for i, translation in enumerate(
            translations
        ):

            chapter_number = i + 1

            chapter_filename = (
                "chapter"
                + str(
                    chapter_number
                )
                + ".xhtml"
            )

            title = (
                "Chapter "
                + str(
                    chapter_number
                )
            )

            safe_translation = html.escape(
                translation
            )

            paragraphs = safe_translation.split(
                "\n"
            )

            body_parts = []

            for paragraph in paragraphs:

                paragraph = paragraph.strip()

                if paragraph:

                    body_parts.append(
                        "<p>"
                        + paragraph
                        + "</p>"
                    )

            body = "\n".join(
                body_parts
            )

            chapter_html = """<?xml version="1.0" encoding="UTF-8"?>

<!DOCTYPE html>

<html
xmlns="http://www.w3.org/1999/xhtml">

<head>

<meta charset="UTF-8"/>

<title>
"""
            chapter_html += html.escape(
                title
            )

            chapter_html += """
</title>

<link
rel="stylesheet"
type="text/css"
href="style.css"/>

</head>

<body>

<h2>
"""

            chapter_html += html.escape(
                title
            )

            chapter_html += """
</h2>

"""

            chapter_html += body

            chapter_html += """

</body>

</html>
"""

            epub.writestr(
                "OEBPS/"
                + chapter_filename,
                chapter_html
            )

            manifest_items.append(
                '<item id="chapter'
                + str(
                    chapter_number
                )
                + '" href="'
                + chapter_filename
                + '" media-type="application/xhtml+xml"/>'
            )

            spine_items.append(
                '<itemref idref="chapter'
                + str(
                    chapter_number
                )
                + '"/>'
            )

        # ====================================================
        # CSS
        # ====================================================

        css = """
body {
    font-family: serif;
    line-height: 1.6;
    margin: 5%;
}

h1,
h2 {
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

        # ====================================================
        # OPF
        # ====================================================

        manifest = "\n".join(
            manifest_items
        )

        spine = "\n".join(
            spine_items
        )

        identifier = str(
            uuid.uuid4()
        )

        opf = """<?xml version="1.0" encoding="UTF-8"?>

<package
version="3.0"
xmlns="http://www.idpf.org/2007/opf"
unique-identifier="BookID">

<metadata
xmlns:dc="http://purl.org/dc/elements/1.1/">

<dc:identifier id="BookID">
"""

        opf += identifier

        opf += """
</dc:identifier>

<dc:title>
"""

        opf += html.escape(
            book_title
        )

        opf += """
</dc:title>

<dc:language>
en
</dc:language>

<dc:creator>
Novel Translator
</dc:creator>

</metadata>

<manifest>

<item
id="style"
href="style.css"
media-type="text/css"/>

"""

        opf += manifest

        opf += """

</manifest>

<spine>

"""

        opf += spine

        opf += """

</spine>

</package>
"""

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
@login_required
def delete(
    job_id
):

    job = jobs.get(
        job_id
    )

    if job:

        del jobs[
            job_id
        ]

        send_telegram(
            "🗑️ Novel deleted\n\n"
            + job["filename"]
        )

    return redirect(
        url_for("index")
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

        "gemini_model": GEMINI_MODEL,

        "openrouter_configured": bool(
            OPENROUTER_API_KEY
        ),

        "openrouter_model": OPENROUTER_MODEL,

        "password_configured": bool(
            SITE_PASSWORD
        ),

        "supabase_configured": bool(
            SUPABASE_URL
            and SUPABASE_PUBLISHABLE_KEY
        ),

        "telegram_configured": bool(
            TELEGRAM_BOT_TOKEN
            and TELEGRAM_CHAT_ID
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
