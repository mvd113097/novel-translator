import os
import io
import re
import uuid
import time
import json
import html
import threading
import zipfile
import urllib.request
import urllib.error

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    send_file,
    session,
)


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "novel-translator-change-this-secret"
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
# GEMINI MODEL
# ============================================================

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()


# ============================================================
# OPENROUTER MODEL
#
# Your old value:
#
# qwen/qwen3-32b:free
#
# is no longer usable.
#
# We automatically replace that old value with the currently
# available free Qwen model.
# ============================================================

OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "qwen/qwen3-coder:free"
).strip()


if OPENROUTER_MODEL == "qwen/qwen3-32b:free":
    OPENROUTER_MODEL = "qwen/qwen3-coder:free"


# ============================================================
# TRANSLATION SETTINGS
# ============================================================

MAX_CHARS_PER_REQUEST = 7000

DOWNLOAD_MIN_WORDS = 30000

REQUEST_DELAY = 3

MAX_RETRIES = 2


# ============================================================
# GEMINI CLIENT
# ============================================================

client = None

try:

    if GEMINI_API_KEY:

        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=GEMINI_API_KEY
        )

        print("Gemini client initialized.")
        print(
            "Gemini model:",
            GEMINI_MODEL
        )

    else:

        print(
            "Gemini API key not configured."
        )

except Exception as e:

    client = None

    print(
        "Gemini client initialization error:",
        repr(e)
    )


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()


# ============================================================
# LOGIN HELPERS
# ============================================================

def password_enabled():

    return bool(SITE_PASSWORD)


def is_logged_in():

    if not password_enabled():
        return True

    return session.get(
        "authenticated",
        False
    ) is True


# ============================================================
# PASSWORD PAGE
# ============================================================

LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Novel Translator - Login</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    margin: 0;
    padding: 20px;
}

.container {
    max-width: 500px;
    margin: 80px auto;
    background: white;
    padding: 25px;
    border-radius: 15px;
    box-shadow: 0 3px 15px rgba(0,0,0,0.1);
}

h1 {
    margin-top: 0;
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

.error {
    margin-top: 15px;
    padding: 12px;
    background: #ffe5e5;
    color: #900;
    border-radius: 8px;
}

</style>

</head>

<body>

<div class="container">

<h1>🔒 Novel Translator</h1>

<p>
Enter the website password to continue.
</p>

<form method="POST"
      action="/login">

<input
    type="password"
    name="password"
    placeholder="Website password"
    required
    autofocus
>

<button type="submit">
    Unlock Website
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
# MAIN PAGE
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
    padding: 20px;
}

.container {
    max-width: 700px;
    margin: auto;
    background: white;
    padding: 25px;
    border-radius: 15px;
    box-shadow: 0 3px 15px rgba(0,0,0,0.1);
}

h1 {
    margin-top: 0;
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

.progress {
    margin-top: 20px;
    background: #ddd;
    border-radius: 10px;
    overflow: hidden;
    height: 25px;
}

.bar {
    height: 25px;
    background: #4caf50;
    width: 0%;
    text-align: center;
    color: white;
    line-height: 25px;
}

.status {
    margin-top: 15px;
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

.job {
    border: 1px solid #ddd;
    padding: 15px;
    margin-top: 15px;
    border-radius: 10px;
}

.small {
    color: #666;
    font-size: 14px;
}

.mode {
    display: inline-block;
    padding: 6px 10px;
    border-radius: 20px;
    background: #eee;
    font-size: 13px;
    margin-top: 5px;
}

.qwen {
    background: #fff1d6;
}

.gemini {
    background: #e6f0ff;
}

a {
    display: block;
    margin-top: 10px;
    padding: 12px;
    background: #222;
    color: white;
    text-decoration: none;
    text-align: center;
    border-radius: 8px;
}

.lock {
    background: #555;
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

<strong>Translation system</strong>

<br><br>

1. Gemini is tried first.

<br><br>

2. If Gemini is exhausted or unavailable,
OpenRouter Qwen automatically takes over.

<br><br>

3. Once Qwen takes over,
that job stays on Qwen.

<br><br>

4. Translation is cumulative.

<br><br>

5. Download becomes available after
{{ min_words | comma }} English words.

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


{% if password_enabled %}

<a class="lock"
   href="/logout">

🔒 Lock Website

</a>

{% endif %}


{% if jobs %}

<h2>Your Novels</h2>


{% for job_id, job in jobs.items() %}

<div class="job">

<strong>
{{ job.filename }}
</strong>


<div class="mode
{% if job.provider == 'qwen' %}
qwen
{% else %}
gemini
{% endif %}
">

{% if job.provider == 'qwen' %}

🤖 OpenRouter Qwen

{% else %}

✨ Gemini

{% endif %}

</div>


<p>

📖 Chapters:
{{ job.translated_chapters }}/{{ job.total_chapters }}

</p>


<p>

📝 English words:
{{ "{:,}".format(job.words) }}

</p>


<p>

Status:
{{ job.status }}

</p>


{% if job.error %}

<div class="status error">

{{ job.error }}

</div>

{% endif %}


{% if job.running %}

<div class="progress">

<div class="bar"
     style="width: {{ job.percent }}%;">

{{ job.percent }}%

</div>

</div>

<div class="status">

{{ job.status }}

</div>


<script>

setTimeout(function() {

    location.reload();

}, 5000);

</script>

{% endif %}


{% if job.translated_chapters < job.total_chapters
      and not job.running %}

<form action="/translate/{{ job_id }}"
      method="GET">

<button type="submit">

▶ Continue Translation

</button>

</form>

{% endif %}


{% if job.words >= min_words %}

<a href="/download/{{ job_id }}">

📥 Download Current EPUB

</a>

{% else %}

<div class="status">

🔒 Download unlocks at
<strong>
{{ min_words | comma }}
</strong>
English words.

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

<a href="/download/{{ job_id }}">

📚 Download Complete EPUB

</a>

{% endif %}


<form
    action="/delete/{{ job_id }}"
    method="POST"
    onsubmit="return confirm('Delete this novel?');"
>

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
# JINJA FILTER
# ============================================================

@app.template_filter("comma")
def comma_filter(value):

    try:

        return f"{int(value):,}"

    except Exception:

        return value


# ============================================================
# AUTHENTICATION
# ============================================================

@app.before_request
def require_login():

    allowed = {
        "login",
        "health",
        "static"
    }

    if request.endpoint in allowed:
        return None

    if not password_enabled():
        return None

    if is_logged_in():
        return None

    return redirect(
        url_for("login")
    )


@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if not password_enabled():

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


@app.route("/logout")
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
        r"(?im)^(第\s*[0-9一二三四五六七八九十百千万]+\s*[章回节]|"
        r"chapter\s+\d+.*)$"
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
            max_chars=12000
        )

        for i, chunk in enumerate(chunks):

            chapters.append(
                f"Chapter {i + 1}\n\n{chunk}"
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

        return parse_txt(data)

    if lower.endswith(".epub"):

        return parse_epub(data)

    raise ValueError(
        "Only TXT and EPUB files are supported."
    )


# ============================================================
# TRANSLATION PROMPT
# ============================================================

def translation_prompt(text):

    return f"""
You are a professional Chinese-to-English web novel translator.

Translate the Chinese text below into natural, fluent English.

IMPORTANT RULES:

1. Translate EVERYTHING.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Do NOT skip descriptions.
5. Preserve the meaning and details.
6. Keep character names consistent.
7. Keep character gender and pronouns consistent.
8. Preserve dialogue.
9. Preserve paragraph breaks when possible.
10. Do not add explanations.
11. Output ONLY the English translation.
12. Do not say "Here is the translation".
13. Do not use Markdown code blocks.
14. Do not discuss the translation process.
15. Do not shorten the text deliberately.

Chinese text:

{text}
"""


# ============================================================
# GEMINI TRANSLATION
# ============================================================

def translate_with_gemini(text):

    if not client:

        raise RuntimeError(
            "Gemini client is unavailable."
        )

    prompt = translation_prompt(
        text
    )

    last_error = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            response = client.models.generate_content(

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

                    translated = "\n".join(
                        pieces
                    ).strip()

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

            error_string = str(e).lower()

            quota_words = [
                "quota",
                "429",
                "resource exhausted",
                "rate limit",
                "too many requests",
                "not found",
                "404",
                "unavailable",
                "permission",
                "403",
                "failed precondition",
                "503",
                "500"
            ]

            if any(
                word in error_string
                for word in quota_words
            ):

                raise RuntimeError(
                    "Gemini unavailable or quota exhausted."
                )

            if attempt < MAX_RETRIES:

                time.sleep(
                    2 ** attempt
                )

    raise RuntimeError(
        "Gemini translation failed: "
        + str(last_error)
    )


# ============================================================
# OPENROUTER TRANSLATION
# ============================================================

def translate_with_openrouter(text):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY is missing."
        )

    prompt = translation_prompt(
        text
    )

    payload = {

        "model": OPENROUTER_MODEL,

        "messages": [

            {
                "role": "user",
                "content": prompt
            }

        ],

        "temperature": 0.2,

        "max_tokens": 12000

    }

    data = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    request_obj = urllib.request.Request(

        "https://openrouter.ai/api/v1/chat/completions",

        data=data,

        headers={
            "Authorization":
                "Bearer " + OPENROUTER_API_KEY,

            "Content-Type":
                "application/json",

            "HTTP-Referer":
                "https://novel-translator-i8wp.onrender.com",

            "X-Title":
                "Novel Translator"
        },

        method="POST"
    )

    last_error = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            with urllib.request.urlopen(
                request_obj,
                timeout=180
            ) as response:

                raw = response.read().decode(
                    "utf-8",
                    errors="replace"
                )

                result = json.loads(
                    raw
                )

            if "error" in result:

                error = result["error"]

                raise RuntimeError(
                    "OpenRouter error: "
                    + json.dumps(
                        error,
                        ensure_ascii=False
                    )
                )

            choices = result.get(
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

                        if item.get("type") == "text":

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

            translated = str(
                translated
            ).strip()

            if translated:

                return translated

            raise RuntimeError(
                "OpenRouter returned empty text."
            )

        except urllib.error.HTTPError as e:

            try:

                error_body = e.read().decode(
                    "utf-8",
                    errors="replace"
                )

            except Exception:

                error_body = str(e)

            last_error = (
                f"OpenRouter HTTP {e.code}: "
                f"{error_body}"
            )

            print(
                last_error
            )

            if e.code in (
                401,
                403,
                404
            ):

                raise RuntimeError(
                    last_error
                )

            if e.code in (
                429,
                500,
                502,
                503,
                504
            ):

                if attempt < MAX_RETRIES:

                    time.sleep(
                        2 ** attempt
                    )

                    continue

                raise RuntimeError(
                    last_error
                )

            raise RuntimeError(
                last_error
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

            else:

                raise RuntimeError(
                    "OpenRouter translation failed: "
                    + str(last_error)
                )

    raise RuntimeError(
        "OpenRouter translation failed: "
        + str(last_error)
    )


# ============================================================
# SMART TRANSLATION
#
# THIS IS THE IMPORTANT PART.
#
# provider == "gemini":
#     Try Gemini.
#
# Gemini fails:
#     Immediately switch job to Qwen.
#
# provider == "qwen":
#     NEVER try Gemini again for that job.
# ============================================================

def translate_piece(
    job,
    text
):

    provider = job.get(
        "provider",
        "gemini"
    )

    if provider == "qwen":

        job["status"] = (
            "🤖 Translating with "
            "OpenRouter Qwen..."
        )

        return translate_with_openrouter(
            text
        )

    try:

        job["status"] = (
            "✨ Translating with Gemini..."
        )

        return translate_with_gemini(
            text
        )

    except Exception as gemini_error:

        print(
            "Gemini failed."
        )

        print(
            "Switching job permanently to Qwen."
        )

        print(
            "Gemini reason:",
            repr(gemini_error)
        )

        job["provider"] = "qwen"

        job["provider_message"] = (
            "Gemini is unavailable or its quota "
            "is exhausted. Automatically switched "
            "to OpenRouter Qwen."
        )

        job["status"] = (
            "Gemini unavailable. "
            "Automatically switched to "
            "OpenRouter Qwen."
        )

        return translate_with_openrouter(
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

        while (
            job["translated_chapters"]
            <
            total
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

                current_provider = job.get(
                    "provider",
                    "gemini"
                )

                provider_name = (
                    "OpenRouter Qwen"
                    if current_provider == "qwen"
                    else "Gemini"
                )

                job["status"] = (

                    f"Translating chapter "
                    f"{index + 1}/{total} "

                    f"(part "
                    f"{piece_number + 1}/"
                    f"{len(pieces)}) "

                    f"with {provider_name}..."
                )

                translated = translate_piece(
                    job,
                    piece
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

            job["translations"].append(
                final_translation
            )

            job["translated_chapters"] += 1

            all_translation = (
                "\n\n".join(
                    job["translations"]
                )
            )

            job["words"] = count_words(
                all_translation
            )

            job["percent"] = int(
                (
                    job[
                        "translated_chapters"
                    ]
                    /
                    total
                ) * 100
            )

            provider_name = (
                "OpenRouter Qwen"
                if job.get("provider") == "qwen"
                else "Gemini"
            )

            job["status"] = (

                f"Completed chapter "
                f"{job['translated_chapters']}/"
                f"{total}. "

                f"{job['words']:,} English words "
                f"translated. "

                f"Provider: {provider_name}."
            )

        job["status"] = (
            "Translation complete!"
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

    finally:

        job["running"] = False


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    return render_template_string(

        PAGE,

        jobs=jobs,

        min_words=DOWNLOAD_MIN_WORDS,

        password_enabled=password_enabled()

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

            # IMPORTANT:
            # Every NEW job starts with Gemini.
            "provider":
                "gemini",

            "provider_message":
                None

        }

        print(
            f"Uploaded {uploaded.filename}: "
            f"{len(chapters)} chapters"
        )

        return redirect(
            url_for("index")
        )

    except Exception as e:

        return f"""
        <h2>Upload Error</h2>
        <p>{html.escape(str(e))}</p>
        <p><a href="/">Go back</a></p>
        """


# ============================================================
# START / CONTINUE TRANSLATION
# ============================================================

@app.route(
    "/translate/<job_id>"
)
def translate(job_id):

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
        >=
        job["total_chapters"]
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

    job = jobs.get(
        job_id
    )

    if not job:

        return redirect(
            url_for("index")
        )

    if not job["translations"]:

        return "Nothing translated yet."

    if job["words"] < DOWNLOAD_MIN_WORDS:

        return (
            "Download unlocks after "
            f"{DOWNLOAD_MIN_WORDS:,} "
            "English words."
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

            mimetype="application/epub+zip",

            as_attachment=True,

            download_name=output_name

        )

    except Exception as e:

        return f"""
        <h2>EPUB creation error</h2>
        <p>{html.escape(str(e))}</p>
        <p><a href="/">Go back</a></p>
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
        # REQUIRED EPUB MIMETYPE
        # ====================================================

        epub.writestr(

            "mimetype",

            "application/epub+zip",

            compress_type=zipfile.ZIP_STORED

        )

        # ====================================================
        # CONTAINER
        # ====================================================

        epub.writestr(

            "META-INF/container.xml",

            """<?xml version="1.0" encoding="UTF-8"?>

<container
version="1.0"
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
                safe_translation.split(
                    "\n"
                )
            )

            body = ""

            for paragraph in paragraphs:

                paragraph = paragraph.strip()

                if paragraph:

                    body += (
                        "<p>"
                        + paragraph
                        + "</p>\n"
                    )

            chapter_html = f"""<?xml version="1.0" encoding="UTF-8"?>

<!DOCTYPE html>

<html
xmlns="http://www.w3.org/1999/xhtml">

<head>

<meta charset="UTF-8"/>

<title>{title}</title>

<link
rel="stylesheet"
type="text/css"
href="style.css"/>

</head>

<body>

<h2>{title}</h2>

{body}

</body>

</html>
"""

            epub.writestr(

                "OEBPS/"
                + chapter_filename,

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

        manifest = "\n".join(
            manifest_items
        )

        spine = "\n".join(
            spine_items
        )

        # ====================================================
        # OPF
        # ====================================================

        opf = f"""<?xml version="1.0" encoding="UTF-8"?>

<package
version="3.0"
xmlns="http://www.idpf.org/2007/opf"
unique-identifier="BookID">

<metadata
xmlns:dc="http://purl.org/dc/elements/1.1/">

<dc:identifier id="BookID">
{uuid.uuid4()}
</dc:identifier>

<dc:title>
{html.escape(book_title)}
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

{manifest}

</manifest>

<spine>

{spine}

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
            bool(GEMINI_API_KEY),

        "openrouter_configured":
            bool(OPENROUTER_API_KEY),

        "gemini_model":
            GEMINI_MODEL,

        "openrouter_model":
            OPENROUTER_MODEL,

        "password_enabled":
            password_enabled()

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
