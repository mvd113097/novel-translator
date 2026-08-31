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

============================================================

CONFIGURATION

============================================================

app = Flask(name)

Secret used by Flask sessions.

Render automatically supplies a random value if SECRET_KEY

is not set, but setting one yourself is preferable.

app.secret_key = os.environ.get(
"SECRET_KEY",
"novel-translator-session-secret-change-me"
)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

============================================================

ENVIRONMENT VARIABLES

============================================================

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

Gemini model comes from Render.

GEMINI_MODEL = os.environ.get(
"GEMINI_MODEL",
"gemini-3.6-flash"
).strip()

============================================================

STRICTLY FREE OPENROUTER QWEN MODELS

============================================================

IMPORTANT:

Every model here ends with :free.

The application will NEVER intentionally use:

qwen/qwen3-32b

qwen/qwen3-32b:free

qwen/qwen3-coder

or any paid model.

We try the strongest free Qwen first and then smaller

free Qwen models if an endpoint is unavailable.

FREE_QWEN_MODELS = [
"qwen/qwen3-235b-a22b-2507:free",
"qwen/qwen3-235b-a22b-instruct-2507:free",
"qwen/qwen3-14b:free",
"qwen/qwen3-4b:free",
]

============================================================

TRANSLATION SETTINGS

============================================================

Larger chunks reduce the number of API requests.

The free OpenRouter tier is rate-limited, so we don't want

to make hundreds of tiny requests.

MAX_CHARS_PER_REQUEST = 12000

Minimum English words required before download.

DOWNLOAD_MIN_WORDS = 30000

Delay between API requests.

REQUEST_DELAY = 3

Gemini retry count.

GEMINI_RETRIES = 1

OpenRouter retry count for the same model.

OPENROUTER_RETRIES = 1

============================================================

GEMINI CLIENT

============================================================

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
        "GEMINI CLIENT ERROR:",
        repr(e)
    )

else:

print(
    "GEMINI_API_KEY not configured."
)

============================================================

IN-MEMORY JOB STORAGE

============================================================

Render's free instance can restart.

This storage therefore represents the current running

instance. The translation itself remains cumulative while

the instance is alive.

jobs = {}

============================================================

HTML

============================================================

PAGE = r"""

<!DOCTYPE html><html><head><meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Novel Translator</title><style>

* {
    box-sizing: border-box;
}

body {
    font-family: Arial, sans-serif;
    background: #f4f6f8;
    margin: 0;
    padding: 18px;
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
    font-size: 28px;
}

h2 {
    margin-top: 28px;
}

input,
button {
    width: 100%;
    padding: 14px;
    margin-top: 10px;
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

.lock-button {
    background: #7b1e1e;
}

.upload-button {
    background: #1d6f42;
}

.job {
    border: 1px solid #ddd;
    padding: 16px;
    margin-top: 16px;
    border-radius: 12px;
    background: #fafafa;
}

.job-title {
    font-size: 18px;
    font-weight: bold;
    word-break: break-word;
}

.provider {
    display: inline-block;
    padding: 6px 9px;
    border-radius: 7px;
    background: #eee;
    margin-top: 8px;
    font-size: 13px;
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
    color: white;
    text-align: center;
    line-height: 26px;
    font-size: 13px;
}

.status {
    margin-top: 13px;
    padding: 12px;
    background: #f0f2f4;
    border-radius: 9px;
    white-space: pre-wrap;
    word-break: break-word;
}

.error {
    background: #ffe5e5;
    color: #8b0000;
}

.success {
    background: #e5ffe8;
    color: #176b22;
}

.warning {
    background: #fff5d6;
    color: #795500;
}

.info {
    background: #e5f1ff;
    color: #174a7e;
}

a.download {
    display: block;
    margin-top: 10px;
    padding: 13px;
    background: #222;
    color: white;
    text-decoration: none;
    text-align: center;
    border-radius: 9px;
}

.small {
    color: #666;
    font-size: 14px;
}

.center {
    text-align: center;
}

.password-box {
    max-width: 420px;
    margin: 70px auto;
    background: white;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 3px 18px rgba(0,0,0,0.10);
}

.locked-message {
    padding: 12px;
    background: #fff5d6;
    border-radius: 9px;
    margin-bottom: 15px;
}

.stat {
    margin-top: 7px;
}

</style></head><body>{% if locked %}

<div class="password-box"><h1>🔒 Novel Translator</h1>

<p>
    This website is password protected.
</p>

{% if password_error %}

    <div class="status error">
        {{ password_error }}
    </div>

{% endif %}

<form action="/login" method="POST">

    <input
        type="password"
        name="password"
        placeholder="Website password"
        autocomplete="current-password"
        required
    >

    <button type="submit">
        🔓 Enter Website
    </button>

</form>

</div>{% else %}

<div class="container"><h1>📚 Novel Translator</h1>

<p>
    Upload a TXT or EPUB novel and translate it to English.
</p>

<div class="status info">

    <strong>Translation system</strong>

    <br><br>

    <strong>1.</strong>
    Gemini is tried first.

    <br><br>

    <strong>2.</strong>
    If Gemini is exhausted or unavailable,
    OpenRouter free Qwen automatically takes over.

    <br><br>

    <strong>3.</strong>
    Once a novel switches to Qwen,
    that novel stays on Qwen.

    <br><br>

    <strong>4.</strong>
    Translation is cumulative.

    <br><br>

    <strong>5.</strong>
    Download becomes available after
    <strong>{{ min_words|comma }}</strong>
    English words.

    <br><br>

    <strong>🆓 Qwen fallback uses free endpoints only.</strong>

</div>

{% if upload_error %}

    <div class="status error">
        {{ upload_error }}
    </div>

{% endif %}

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
        class="upload-button"
        type="submit"
    >
        📤 Upload Novel
    </button>

</form>

<form
    action="/lock"
    method="POST"
>

    <button
        class="lock-button"
        type="submit"
    >
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

        <div class="provider">

            {% if job.provider == "gemini" %}
                🤖 Gemini
            {% elif job.provider == "qwen" %}
                🧠 OpenRouter Qwen
            {% else %}
                ⏳ Not started
            {% endif %}

        </div>

        <div class="stat">
            📖 Chapters:
            {{ job.translated_chapters }}/{{ job.total_chapters }}
        </div>

        <div class="stat">
            📝 English words:
            {{ job.words|comma }}
        </div>

        <div class="stat">
            📊 Progress:
            {{ job.percent }}%
        </div>

        <div class="status">
            Status: {{ job.status }}
        </div>

        {% if job.error %}

            <div class="status error">
                {{ job.error }}
            </div>

        {% endif %}

        {% if job.provider == "qwen" and job.qwen_model %}

            <div class="status info">

                Current free Qwen model:

                <strong>
                    {{ job.qwen_model }}
                </strong>

            </div>

        {% endif %}

        {% if job.switched_to_qwen %}

            <div class="status warning">

                ⚡ Gemini was unavailable or exhausted.

                <br><br>

                This novel has automatically switched
                to <strong>free OpenRouter Qwen</strong>.

                <br><br>

                It will remain on Qwen for this job.

            </div>

        {% endif %}


        {% if job.running %}

            <div class="progress">

                <div
                    class="bar"
                    style="width: {{ job.percent }}%;"
                >
                    {{ job.percent }}%
                </div>

            </div>

            <script>

            setTimeout(function() {
                location.reload();
            }, 5000);

            </script>

        {% endif %}


        {% if job.words >= min_words %}

            <a
                class="download"
                href="/download/{{ job_id }}"
            >
                📥 Download Current EPUB
            </a>

        {% else %}

            <div class="status">

                🔒 Download unlocks at

                <strong>
                    {{ min_words|comma }}
                </strong>

                English words.

                <br><br>

                Current:

                <strong>
                    {{ job.words|comma }}
                </strong>

                words.

            </div>

        {% endif %}


        {% if job.translated_chapters < job.total_chapters
              and not job.running %}

            <form
                action="/translate/{{ job_id }}"
                method="GET"
            >

                <button type="submit">
                    ▶ Continue Translation
                </button>

            </form>

        {% endif %}


        {% if job.translated_chapters == job.total_chapters
              and not job.error %}

            <a
                class="download"
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

            <button type="submit">
                🗑️ Delete Novel
            </button>

        </form>

    </div>

    {% endfor %}

{% endif %}

</div>{% endif %}

</body>
</html>
"""============================================================

JINJA FILTER

============================================================

def comma_filter(value):

try:
    return f"{int(value):,}"
except Exception:
    return value

Register custom filter.

app.jinja_env.filters["comma"] = comma_filter

============================================================

PASSWORD PROTECTION

============================================================

def password_enabled():

return bool(SITE_PASSWORD)

def is_authenticated():

if not password_enabled():
    return True

return session.get(
    "authenticated",
    False
)

@app.before_request
def protect_website():

# These routes must remain accessible.
allowed_paths = {
    "/login",
    "/health",
    "/favicon.ico"
}

if request.path in allowed_paths:
    return None

if not password_enabled():
    return None

if not is_authenticated():
    return render_template_string(
        PAGE,
        locked=True,
        password_error=None,
        jobs={},
        min_words=DOWNLOAD_MIN_WORDS,
        upload_error=None
    )

return None

============================================================

TEXT UTILITIES

============================================================

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
        text
    )
)

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

============================================================

TXT PARSER

============================================================

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

============================================================

EPUB PARSER

============================================================

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

        raw = html.unescape(raw)

        text = clean_text(raw)

        if len(text) < 100:
            continue

        parts = split_large_text(
            text,
            max_chars=12000
        )

        chapters.extend(parts)

if not chapters:

    raise ValueError(
        "No readable chapters were found in the EPUB."
    )

return chapters

============================================================

FILE PARSER

============================================================

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

============================================================

ERROR CLASSIFICATION

============================================================

def error_text(error):

try:
    return str(error)
except Exception:
    return repr(error)

def is_gemini_unavailable(error):

text = error_text(
    error
).lower()

indicators = [
    "quota",
    "429",
    "resource exhausted",
    "rate limit",
    "too many requests",
    "not found",
    "404",
    "model is no longer available",
    "unavailable",
    "permission denied",
    "403",
    "401",
    "api key",
    "invalid argument",
    "deadline exceeded",
    "service unavailable",
    "503"
]

return any(
    item in text
    for item in indicators
)

def is_openrouter_unavailable(error):

text = error_text(
    error
).lower()

indicators = [
    "404",
    "not found",
    "unavailable",
    "rate limit",
    "429",
    "too many requests",
    "free",
    "provider",
    "temporarily",
    "503",
    "502",
    "timeout"
]

return any(
    item in text
    for item in indicators
)

============================================================

TRANSLATION PROMPT

============================================================

def make_translation_prompt(
text
):

return f"""

You are a professional Chinese-to-English web novel translator.

Translate the Chinese text below into natural, fluent,
readable English suitable for an English-language web novel.

IMPORTANT:

1. Translate EVERYTHING.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Do NOT shorten the text.
5. Preserve all details.
6. Preserve the original meaning.
7. Keep character names consistent.
8. Keep genders and pronouns consistent.
9. Use context to determine correct pronouns.
10. Preserve dialogue.
11. Preserve paragraph breaks whenever possible.
12. Preserve emotional tone.
13. Preserve names, titles, relationships, and forms of address.
14. Do not explain the translation.
15. Do not add commentary.
16. Do not say "Here is the translation".
17. Do not use Markdown code fences.
18. Output ONLY the English translation.

Chinese text:

{text}
"""

============================================================

GEMINI TRANSLATION

============================================================

def translate_with_gemini(
text
):

if not gemini_client:

    raise RuntimeError(
        "Gemini client is not configured."
    )

prompt = make_translation_prompt(
    text
)

last_error = None

for attempt in range(
    GEMINI_RETRIES + 1
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

        if attempt < GEMINI_RETRIES:

            time.sleep(
                2 ** attempt
            )

raise RuntimeError(
    "Gemini translation failed: "
    + error_text(last_error)
)

============================================================

OPENROUTER HTTP

============================================================

def openrouter_request(
model,
prompt
):

if not OPENROUTER_API_KEY:

    raise RuntimeError(
        "OPENROUTER_API_KEY is missing."
    )

# Safety check:
# We intentionally refuse to send a model that isn't
# explicitly marked as free.
if not model.endswith(":free"):

    raise RuntimeError(
        "SAFETY STOP: OpenRouter model is not marked "
        "as free. Refusing to use a paid model: "
        + model
    )

url = (
    "https://openrouter.ai/api/v1/chat/completions"
)

payload = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": prompt
        }
    ],
    "temperature": 0.2,
    "max_tokens": 12000
}

body = json.dumps(
    payload
).encode(
    "utf-8"
)

headers = {
    "Authorization":
        "Bearer " + OPENROUTER_API_KEY,

    "Content-Type":
        "application/json",

    "HTTP-Referer":
        "https://novel-translator-i8wp.onrender.com",

    "X-Title":
        "Free Novel Translator"
}

req = urllib.request.Request(
    url,
    data=body,
    headers=headers,
    method="POST"
)

try:

    with urllib.request.urlopen(
        req,
        timeout=180
    ) as response:

        raw = response.read().decode(
            "utf-8",
            errors="replace"
        )

        status = response.status

except urllib.error.HTTPError as e:

    raw = e.read().decode(
        "utf-8",
        errors="replace"
    )

    raise RuntimeError(
        f"OpenRouter HTTP {e.code}: {raw}"
    )

except Exception as e:

    raise RuntimeError(
        "OpenRouter connection error: "
        + error_text(e)
    )

if status < 200 or status >= 300:

    raise RuntimeError(
        f"OpenRouter HTTP {status}: {raw}"
    )

try:

    result = json.loads(
        raw
    )

except Exception:

    raise RuntimeError(
        "OpenRouter returned invalid JSON:\n"
        + raw
    )

if "error" in result:

    error = result.get(
        "error"
    )

    raise RuntimeError(
        "OpenRouter error: "
        + json.dumps(
            error,
            ensure_ascii=False
        )
    )

choices = result.get(
    "choices"
)

if not choices:

    raise RuntimeError(
        "OpenRouter returned no choices."
    )

message = choices[0].get(
    "message",
    {}
)

content = message.get(
    "content"
)

if not content:

    raise RuntimeError(
        "OpenRouter returned no translation text."
    )

# Some providers can return a list of content parts.
if isinstance(
    content,
    list
):

    pieces = []

    for item in content:

        if isinstance(
            item,
            dict
        ):

            if item.get("text"):
                pieces.append(
                    item["text"]
                )

    content = "\n".join(
        pieces
    )

content = str(
    content
).strip()

if not content:

    raise RuntimeError(
        "OpenRouter returned empty translation."
    )

return content

============================================================

QWEN TRANSLATION

============================================================

def translate_with_qwen(
text,
job
):

prompt = make_translation_prompt(
    text
)

last_error = None

# If this job already selected a Qwen model,
# try that model first.
models_to_try = []

current_model = job.get(
    "qwen_model"
)

if current_model:

    models_to_try.append(
        current_model
    )

for model in FREE_QWEN_MODELS:

    if model not in models_to_try:

        models_to_try.append(
            model
        )

for model in models_to_try:

    for attempt in range(
        OPENROUTER_RETRIES + 1
    ):

        try:

            job["qwen_model"] = model

            translated = openrouter_request(
                model,
                prompt
            )

            return translated

        except Exception as e:

            last_error = e

            print(
                "OPENROUTER QWEN ERROR:",
                model,
                repr(e)
            )

            if attempt < OPENROUTER_RETRIES:

                time.sleep(
                    2 ** attempt
                )

                continue

            # This endpoint failed.
            # Try the next FREE Qwen endpoint.
            break

raise RuntimeError(
    "All configured FREE Qwen endpoints "
    "are currently unavailable.\n\n"
    + error_text(last_error)
)

============================================================

TRANSLATE ONE PIECE

============================================================

def translate_piece(
text,
job
):

# --------------------------------------------------------
# Once a job switches to Qwen, NEVER go back to Gemini.
# --------------------------------------------------------

if job.get("provider") == "qwen":

    return translate_with_qwen(
        text,
        job
    )

# --------------------------------------------------------
# Gemini is first.
# --------------------------------------------------------

try:

    job["provider"] = "gemini"

    return translate_with_gemini(
        text
    )

except Exception as gemini_error:

    print(
        "Gemini unavailable. "
        "Switching job to FREE Qwen."
    )

    print(
        "Gemini reason:",
        repr(gemini_error)
    )

    # ----------------------------------------------------
    # Permanently switch this job to Qwen.
    # ----------------------------------------------------

    job["provider"] = "qwen"
    job["switched_to_qwen"] = True
    job["qwen_model"] = None

    job["status"] = (
        "Gemini is unavailable or its quota is "
        "exhausted. Automatically switched to "
        "FREE OpenRouter Qwen."
    )

    # Now translate this SAME piece with Qwen.
    return translate_with_qwen(
        text,
        job
    )

============================================================

TRANSLATION WORKER

============================================================

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

            provider_name = (
                "Gemini"
                if job.get("provider") == "gemini"
                else "FREE Qwen"
                if job.get("provider") == "qwen"
                else "Gemini"
            )

            job["status"] = (
                f"Translating chapter "
                f"{index + 1}/{total} "
                f"(part "
                f"{piece_number + 1}/"
                f"{len(pieces)}) "
                f"using {provider_name}..."
            )

            translated = translate_piece(
                piece,
                job
            )

            translated_pieces.append(
                translated
            )

            # Delay after each API request.
            time.sleep(
                REQUEST_DELAY
            )

        final_translation = (
            "\n\n".join(
                translated_pieces
            ).strip()
        )

        if not final_translation:

            raise RuntimeError(
                "Translation returned empty text."
            )

        job["translations"].append(
            final_translation
        )

        job["translated_chapters"] += 1

        combined = "\n\n".join(
            job["translations"]
        )

        job["words"] = count_words(
            combined
        )

        job["percent"] = int(
            (
                job["translated_chapters"]
                /
                total
            ) * 100
        )

        if job.get("provider") == "qwen":

            job["status"] = (
                f"Completed chapter "
                f"{job['translated_chapters']}/"
                f"{total}. "
                f"{job['words']:,} English words. "
                f"Using FREE Qwen."
            )

        else:

            job["status"] = (
                f"Completed chapter "
                f"{job['translated_chapters']}/"
                f"{total}. "
                f"{job['words']:,} English words. "
                f"Using Gemini."
            )

    job["status"] = (
        "Translation complete!"
    )

except Exception as e:

    print(
        "TRANSLATION WORKER ERROR:",
        repr(e)
    )

    job["error"] = error_text(
        e
    )

    job["status"] = (
        "Translation stopped."
    )

finally:

    job["running"] = False

============================================================

TELEGRAM

============================================================

def send_telegram(
message
):

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
            "chat_id":
                TELEGRAM_CHAT_ID,

            "text":
                message
        }
    ).encode(
        "utf-8"
    )

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST"
    )

    urllib.request.urlopen(
        req,
        timeout=20
    )

except Exception as e:

    print(
        "TELEGRAM ERROR:",
        repr(e)
    )

============================================================

HOME

============================================================

@app.route("/")
def index():

return render_template_string(
    PAGE,
    locked=False,
    password_error=None,
    jobs=jobs,
    min_words=DOWNLOAD_MIN_WORDS,
    upload_error=None
)

============================================================

LOGIN

============================================================

@app.route(
"/login",
methods=["POST"]
)
def login():

if not password_enabled():

    return redirect(
        url_for("index")
    )

supplied = request.form.get(
    "password",
    ""
)

if supplied == SITE_PASSWORD:

    session["authenticated"] = True

    return redirect(
        url_for("index")
    )

return render_template_string(
    PAGE,
    locked=True,
    password_error="Incorrect password.",
    jobs={},
    min_words=DOWNLOAD_MIN_WORDS,
    upload_error=None
)

============================================================

LOCK

============================================================

@app.route(
"/lock",
methods=["POST"]
)
def lock():

session.clear()

return redirect(
    url_for("index")
)

============================================================

UPLOAD

============================================================

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

    if not data:

        raise ValueError(
            "The uploaded file is empty."
        )

    chapters = parse_uploaded_file(
        uploaded.filename,
        data
    )

    if not chapters:

        raise ValueError(
            "No chapters were found."
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

        # Gemini starts first.
        "provider":
            None,

        # Becomes True permanently after
        # Gemini fails.
        "switched_to_qwen":
            False,

        "qwen_model":
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

    return render_template_string(
        PAGE,
        locked=False,
        password_error=None,
        jobs=jobs,
        min_words=DOWNLOAD_MIN_WORDS,
        upload_error=error_text(e)
    )

============================================================

START / CONTINUE TRANSLATION

============================================================

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

============================================================

DOWNLOAD

============================================================

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

# --------------------------------------------------------
# Enforce 30,000-word unlock.
# --------------------------------------------------------

if job["words"] < DOWNLOAD_MIN_WORDS:

    return (
        "Download is locked until "
        f"{DOWNLOAD_MIN_WORDS:,} English words "
        "have been translated."
    )

if not job["translations"]:

    return "Nothing translated yet."

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

    return (
        "<h2>EPUB creation error</h2>"
        "<p>"
        + html.escape(
            error_text(e)
        )
        + "</p>"
        "<p><a href='/'>Go back</a></p>"
    )

============================================================

EPUB CREATOR

============================================================

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

    # ----------------------------------------------------
    # EPUB mimetype MUST be first and uncompressed.
    # ----------------------------------------------------

    epub.writestr(
        "mimetype",
        "application/epub+zip",
        compress_type=zipfile.ZIP_STORED
    )

    # ----------------------------------------------------
    # container.xml
    # ----------------------------------------------------

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>

<container
version="1.0"
xmlns="urn:oasis:names:tc:opendocument:xmlns:container">

<rootfiles><rootfile
full-path="OEBPS/content.opf"
media-type="application/oebps-package+xml"/>

</rootfiles></container>
"""    epub.writestr(
        "META-INF/container.xml",
        container_xml
    )

    manifest_items = []
    spine_items = []

    # ----------------------------------------------------
    # Chapters
    # ----------------------------------------------------

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

        paragraphs = safe_translation.split(
            "\n"
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

<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="UTF-8"/><title>{html.escape(title)}</title><link
rel="stylesheet"
type="text/css"
href="style.css"/></head><body><h2>{html.escape(title)}</h2>{body}

</body></html>
"""        epub.writestr(
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

    # ----------------------------------------------------
    # CSS
    # ----------------------------------------------------

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

    # ----------------------------------------------------
    # OPF
    # ----------------------------------------------------

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

"dc:title" (dc:title)
{html.escape(book_title)}
</dc:title>

"dc:language" (dc:language)
en
</dc:language>

"dc:creator" (dc:creator)
Free AI Novel Translator
</dc:creator>

</metadata><manifest><item
id="style"
href="style.css"
media-type="text/css"/>

{manifest}

</manifest><spine>{spine}

</spine></package>
"""    epub.writestr(
        "OEBPS/content.opf",
        opf
    )

return buf.getvalue()

============================================================

DELETE

============================================================

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

============================================================

HEALTH CHECK

============================================================

@app.route(
"/health"
)
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

    "strictly_free_qwen":
        True,

    "free_qwen_models":
        FREE_QWEN_MODELS,

    "password_protection":
        password_enabled(),

    "download_threshold_words":
        DOWNLOAD_MIN_WORDS
}

============================================================

FAVICON

============================================================

@app.route(
"/favicon.ico"
)
def favicon():

return (
    "",
    204
)

============================================================

SERVER

============================================================

if name == "main":

port = int(
    os.environ.get(
        "PORT",
        10000
    )
)

app.run(
    host="0.0.0.0",
    port=port,
    debug=False
)
