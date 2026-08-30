import os
import io
import re
import uuid
import time
import json
import threading
import zipfile
import html
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


# ============================================================
# CONFIGURATION
# ============================================================

app = Flask(__name__)

# ------------------------------------------------------------
# Password / session
# ------------------------------------------------------------

APP_PASSWORD = os.environ.get(
    "APP_PASSWORD",
    "1234"
)

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key"
)

app.secret_key = SECRET_KEY


# ------------------------------------------------------------
# Storage
# ------------------------------------------------------------

UPLOAD_FOLDER = "uploads"
DATA_FOLDER = "data"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)

JOBS_FILE = os.path.join(
    DATA_FOLDER,
    "jobs.json"
)


# ------------------------------------------------------------
# Gemini
# ------------------------------------------------------------

GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY"
)

# Current default based on the model error you received.
# Can still be changed in Render environment variables.
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)


# ------------------------------------------------------------
# OpenRouter / Qwen fallback
# ------------------------------------------------------------

OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY"
)

# Free Qwen model.
#
# Can be changed in Render environment variables:
#
# OPENROUTER_MODEL=qwen/qwen3-32b:free
#
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "qwen/qwen3-32b:free"
)

OPENROUTER_URL = (
    "https://openrouter.ai/api/v1/chat/completions"
)


# ------------------------------------------------------------
# Translation settings
# ------------------------------------------------------------

MAX_CHARS_PER_REQUEST = int(
    os.environ.get(
        "MAX_CHARS_PER_REQUEST",
        "7000"
    )
)

DOWNLOAD_MIN_WORDS = int(
    os.environ.get(
        "DOWNLOAD_MIN_WORDS",
        "30000"
    )
)

REQUEST_DELAY = float(
    os.environ.get(
        "REQUEST_DELAY",
        "3"
    )
)

MAX_RETRIES = int(
    os.environ.get(
        "MAX_RETRIES",
        "2"
    )
)


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

    except Exception as e:

        print(
            "GEMINI CLIENT ERROR:",
            repr(e)
        )

else:

    print(
        "GEMINI_API_KEY is not configured."
    )


# ============================================================
# RUNTIME AI STATE
# ============================================================

# IMPORTANT:
#
# If Gemini returns a quota, 404, unavailable-model,
# permission, or similar permanent failure, we disable Gemini
# for the rest of the running process.
#
# This prevents the application from wasting requests
# repeatedly hitting an exhausted Gemini quota.
#
# Qwen then becomes the active translator.
# ============================================================

gemini_disabled = False

gemini_disable_reason = ""

ai_state_lock = threading.Lock()


def disable_gemini(reason):

    global gemini_disabled
    global gemini_disable_reason

    with ai_state_lock:

        gemini_disabled = True
        gemini_disable_reason = str(reason)

    print(
        "=================================================="
    )

    print(
        "GEMINI DISABLED"
    )

    print(
        str(reason)
    )

    print(
        "QWEN / OPENROUTER WILL NOW BE USED."
    )

    print(
        "=================================================="
    )


def is_gemini_disabled():

    with ai_state_lock:

        return gemini_disabled


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()


# ============================================================
# LOAD SAVED JOBS
# ============================================================

def load_jobs():

    global jobs

    if not os.path.exists(JOBS_FILE):

        jobs = {}
        return

    try:

        with open(
            JOBS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            loaded = json.load(f)

        if isinstance(loaded, dict):

            jobs = loaded

        else:

            jobs = {}

        print(
            f"Loaded {len(jobs)} saved jobs."
        )

    except Exception as e:

        print(
            "JOB LOAD ERROR:",
            repr(e)
        )

        jobs = {}


# ============================================================
# SAVE JOBS
# ============================================================

def save_all_jobs():

    temp_file = JOBS_FILE + ".tmp"

    try:

        with jobs_lock:

            data = jobs.copy()

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False
            )

        os.replace(
            temp_file,
            JOBS_FILE
        )

    except Exception as e:

        print(
            "JOB SAVE ERROR:",
            repr(e)
        )


def save_job(job_id):

    save_all_jobs()


load_jobs()


# ============================================================
# HTML
# ============================================================

PAGE = """
<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Novel Translator</title>

<style>

* {
    box-sizing: border-box;
}

body {

    font-family:
        Arial,
        sans-serif;

    background:
        #f5f5f5;

    margin: 0;

    padding: 15px;
}

.container {

    max-width: 750px;

    margin:
        auto;

    background:
        white;

    padding:
        22px;

    border-radius:
        15px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,0.10);
}

h1 {

    margin-top:
        0;
}

input,
button {

    width:
        100%;

    padding:
        14px;

    margin-top:
        10px;

    border-radius:
        8px;

    border:
        1px solid #ccc;

    font-size:
        16px;
}

button {

    background:
        #222;

    color:
        white;

    cursor:
        pointer;

    border:
        none;
}

button:hover {

    background:
        #444;
}

.logout {

    background:
        #777;

    margin-bottom:
        15px;
}

.progress {

    margin-top:
        15px;

    background:
        #ddd;

    border-radius:
        10px;

    overflow:
        hidden;

    height:
        26px;
}

.bar {

    height:
        26px;

    background:
        #4caf50;

    width:
        0%;

    text-align:
        center;

    color:
        white;

    line-height:
        26px;

    transition:
        width 0.3s;
}

.status {

    margin-top:
        12px;

    padding:
        12px;

    background:
        #f0f0f0;

    border-radius:
        8px;

    white-space:
        pre-wrap;
}

.error {

    background:
        #ffe5e5;

    color:
        #900;
}

.success {

    background:
        #e5ffe8;

    color:
        #176b22;
}

.warning {

    background:
        #fff4d6;

    color:
        #805500;
}

.job {

    border:
        1px solid #ddd;

    padding:
        15px;

    margin-top:
        15px;

    border-radius:
        10px;
}

.small {

    color:
        #666;

    font-size:
        14px;
}

.ai {

    padding:
        8px 10px;

    border-radius:
        7px;

    background:
        #eeeeee;

    display:
        inline-block;

    margin-top:
        5px;

    font-size:
        14px;
}

.ai-gemini {

    background:
        #e5f0ff;

    color:
        #1453a6;
}

.ai-qwen {

    background:
        #e9ffe8;

    color:
        #176b22;
}

a.download {

    display:
        block;

    margin-top:
        10px;

    padding:
        12px;

    background:
        #222;

    color:
        white;

    text-decoration:
        none;

    text-align:
        center;

    border-radius:
        8px;
}

hr {

    border:
        0;

    border-top:
        1px solid #ddd;

    margin:
        20px 0;
}

</style>

</head>

<body>

<div class="container">

<h1>📚 Novel Translator</h1>

<form
    action="/logout"
    method="POST"
>

<button
    class="logout"
    type="submit"
>
    🔒 Lock / Logout
</button>

</form>

<p>
Upload a TXT or EPUB novel and translate it to English.
</p>

<p class="small">

<strong>Translation system:</strong>

Gemini is tried first.

If Gemini is exhausted, unavailable,
or returns a model/access error,
the app automatically switches to
Qwen through OpenRouter.

</p>

<p class="small">

Translation is cumulative.
Completed chapters are saved so you can
continue after an error or restart.

Download becomes available after
{{ min_words|comma }} English words.

</p>


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

<button type="submit">

📤 Upload Novel

</button>

</form>


{% if gemini_disabled %}

<div class="status warning">

⚠️ Gemini is currently disabled.

Reason:
{{ gemini_reason }}

<strong>
Qwen through OpenRouter is being used.
</strong>

</div>

{% endif %}


{% if not openrouter_configured %}

<div class="status warning">

⚠️ OPENROUTER_API_KEY is not configured.

Qwen fallback will not work until you add
OPENROUTER_API_KEY to Render.

</div>

{% endif %}


{% if jobs %}

<h2>Your Novels</h2>


{% for job_id, job in jobs.items() %}

<div class="job">

<strong>
{{ job.filename }}
</strong>


<p>

Chapters:
{{ job.translated_chapters }}/{{ job.total_chapters }}

</p>


<p>

English words:
{{ "{:,}".format(job.words|int) }}

</p>


<p>

Current AI:

{% if job.provider == "Gemini" %}

<span class="ai ai-gemini">
🟦 Gemini
</span>

{% elif job.provider == "Qwen" %}

<span class="ai ai-qwen">
🟩 Qwen / OpenRouter
</span>

{% else %}

<span class="ai">
Not started
</span>

{% endif %}

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

<div
    class="bar"
    style="width: {{ job.percent }}%;"
>

{{ job.percent }}%

</div>

</div>


<div class="status">

{{ job.status }}

</div>


<script>

setTimeout(
    function() {
        location.reload();
    },
    4000
);

</script>

{% endif %}


{% if
    job.translated_chapters < job.total_chapters
    and not job.running
%}

<form
    action="/translate/{{ job_id }}"
    method="GET"
>

<button type="submit">

▶ Continue Translation

</button>

</form>

{% endif %}


{% if job.words >= min_words %}

<a
    class="download"
    href="/download/{{ job_id }}"
>

📥 Download Current EPUB

</a>

{% endif %}


{% if
    job.translated_chapters == job.total_chapters
    and not job.error
%}

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

🗑️ Delete

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
# LOGIN HTML
# ============================================================

LOGIN_PAGE = """
<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Novel Translator Login</title>

<style>

body {

    font-family:
        Arial,
        sans-serif;

    background:
        #f5f5f5;

    margin: 0;

    padding: 20px;
}

.container {

    max-width:
        450px;

    margin:
        80px auto;

    background:
        white;

    padding:
        25px;

    border-radius:
        15px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,0.1);
}

input,
button {

    width:
        100%;

    box-sizing:
        border-box;

    padding:
        14px;

    margin-top:
        10px;

    border-radius:
        8px;

    border:
        1px solid #ccc;

    font-size:
        16px;
}

button {

    background:
        #222;

    color:
        white;

    border:
        none;
}

.error {

    margin-top:
        15px;

    padding:
        12px;

    background:
        #ffe5e5;

    color:
        #900;

    border-radius:
        8px;
}

</style>

</head>

<body>

<div class="container">

<h1>🔐 Novel Translator</h1>

<p>
Enter the password to continue.
</p>

<form
    action="/login"
    method="POST"
>

<input
    type="password"
    name="password"
    placeholder="Password"
    autocomplete="current-password"
    required
>

<button type="submit">

Enter

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
# TEMPLATE FILTER
# ============================================================

@app.template_filter("comma")
def comma_filter(value):

    try:

        return f"{int(value):,}"

    except Exception:

        return str(value)


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if password == APP_PASSWORD:

            session["logged_in"] = True

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
# LOGIN PROTECTION
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

    if not session.get("logged_in"):

        return redirect(
            url_for("login")
        )

    return None


# ============================================================
# UTILITY FUNCTIONS
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

        # ----------------------------------------------------
        # Extremely long paragraph
        # ----------------------------------------------------

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

    text = clean_text(
        text
    )

    # --------------------------------------------------------
    # Chinese and English chapter headings
    # --------------------------------------------------------

    pattern = re.compile(
        r"(?im)^("
        r"第\s*[0-9一二三四五六七八九十百千万两]+\s*[章回节].*"
        r"|chapter\s+\d+.*"
        r")$"
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

        for i, chunk in enumerate(
            chunks
        ):

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

            # Remove scripts
            raw = re.sub(
                r"<script.*?</script>",
                "",
                raw,
                flags=re.I | re.S
            )

            # Remove styles
            raw = re.sub(
                r"<style.*?</style>",
                "",
                raw,
                flags=re.I | re.S
            )

            # Convert paragraph endings to newlines
            raw = re.sub(
                r"</(p|div|br|h1|h2|h3|h4|li)>",
                "\n",
                raw,
                flags=re.I
            )

            # Remove tags
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

            # Ignore tiny navigation files
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
4. Do NOT shorten the story.
5. Preserve all events and details.
6. Preserve the original meaning.
7. Keep character names consistent.
8. Keep character genders and pronouns consistent.
9. Use context to determine correct pronouns.
10. Preserve dialogue.
11. Preserve paragraph breaks when possible.
12. Translate narration naturally.
13. Do not add explanations.
14. Do not add translator notes.
15. Output ONLY the English translation.
16. Do not say "Here is the translation".
17. Do not use Markdown code blocks.
18. Do not describe what you are doing.
19. Do not summarize before or after the translation.

Chinese text:

{text}
"""


# ============================================================
# DETERMINE WHETHER GEMINI ERROR IS PERMANENT / QUOTA
# ============================================================

def should_switch_to_qwen(error):

    text = str(error).lower()

    permanent_patterns = [

        "quota",

        "429",

        "resource exhausted",

        "resource_exhausted",

        "rate limit",

        "rate_limit",

        "too many requests",

        "404 not_found",

        "404",

        "not found",

        "model is no longer available",

        "no longer available",

        "permission denied",

        "permission_denied",

        "unauthorized",

        "401",

        "403",

        "api key",

        "invalid api key",

        "invalid_argument",

        "model not found",

        "access denied",

        "failed_precondition"
    ]

    for pattern in permanent_patterns:

        if pattern in text:

            return True

    return False


# ============================================================
# EXTRACT GEMINI TEXT
# ============================================================

def extract_gemini_text(
    response
):

    # --------------------------------------------------------
    # Normal google-genai response
    # --------------------------------------------------------

    text = getattr(
        response,
        "text",
        None
    )

    if text:

        text = str(
            text
        ).strip()

        if text:

            return text

    # --------------------------------------------------------
    # Fallback through candidates
    # --------------------------------------------------------

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
                        str(part_text)
                    )

        if pieces:

            result = "\n".join(
                pieces
            ).strip()

            if result:

                return result

    return None


# ============================================================
# GEMINI TRANSLATION
# ============================================================

def translate_with_gemini(
    text,
    job=None
):

    global gemini_client

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    if not gemini_client:

        raise RuntimeError(
            "Gemini client is unavailable."
        )

    if is_gemini_disabled():

        raise RuntimeError(
            "Gemini has already been disabled. "
            "Use Qwen."
        )

    prompt = make_translation_prompt(
        text
    )

    last_error = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            if job:

                job["provider"] = "Gemini"

                job["status"] = (
                    "🟦 Gemini is translating..."
                )

                save_job(
                    job["id"]
                )

            print(
                "GEMINI REQUEST:",
                GEMINI_MODEL,
                "attempt",
                attempt + 1
            )

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

            translated = extract_gemini_text(
                response
            )

            if translated:

                print(
                    "GEMINI SUCCESS"
                )

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

            # ------------------------------------------------
            # QUOTA / MODEL / ACCESS FAILURE
            #
            # Immediately switch to Qwen.
            # ------------------------------------------------

            if should_switch_to_qwen(
                e
            ):

                disable_gemini(
                    str(e)
                )

                raise RuntimeError(
                    "GEMINI_FALLBACK_REQUIRED: "
                    + str(e)
                )

            # ------------------------------------------------
            # Temporary error
            # ------------------------------------------------

            if attempt < MAX_RETRIES:

                wait_time = (
                    2 ** attempt
                )

                print(
                    "Temporary Gemini error."
                    f" Retrying in {wait_time}s..."
                )

                time.sleep(
                    wait_time
                )

    raise RuntimeError(
        "Gemini translation failed:\n"
        + str(last_error)
    )


# ============================================================
# OPENROUTER RESPONSE EXTRACTION
# ============================================================

def extract_openrouter_text(
    data
):

    try:

        choices = data.get(
            "choices",
            []
        )

        if not choices:

            return None

        message = choices[0].get(
            "message",
            {}
        )

        content = message.get(
            "content"
        )

        if isinstance(
            content,
            str
        ):

            return content.strip()

        # Some APIs may return content arrays.
        if isinstance(
            content,
            list
        ):

            pieces = []

            for item in content:

                if not isinstance(
                    item,
                    dict
                ):

                    continue

                if item.get(
                    "type"
                ) == "text":

                    value = item.get(
                        "text"
                    )

                    if value:

                        pieces.append(
                            value
                        )

            if pieces:

                return "\n".join(
                    pieces
                ).strip()

    except Exception as e:

        print(
            "OPENROUTER RESPONSE PARSE ERROR:",
            repr(e)
        )

    return None


# ============================================================
# QWEN / OPENROUTER TRANSLATION
# ============================================================

def translate_with_qwen(
    text,
    job=None
):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY is missing.\n\n"
            "Add OPENROUTER_API_KEY to your Render "
            "environment variables."
        )

    prompt = make_translation_prompt(
        text
    )

    payload = {

        "model":
            OPENROUTER_MODEL,

        "messages": [

            {
                "role":
                    "system",

                "content":
                    "You are a professional "
                    "Chinese-to-English web novel translator."
            },

            {
                "role":
                    "user",

                "content":
                    prompt
            }

        ],

        "temperature":
            0.2,

        "stream":
            False
    }

    body = json.dumps(
        payload,
        ensure_ascii=False
    ).encode(
        "utf-8"
    )

    headers = {

        "Authorization":
            "Bearer "
            + OPENROUTER_API_KEY,

        "Content-Type":
            "application/json",

        "HTTP-Referer":
            "https://novel-translator-i8wp.onrender.com",

        "X-Title":
            "Novel Translator"
    }

    last_error = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            if job:

                job["provider"] = "Qwen"

                job["status"] = (
                    "🟩 Qwen / OpenRouter is translating..."
                )

                save_job(
                    job["id"]
                )

            print(
                "QWEN REQUEST:",
                OPENROUTER_MODEL,
                "attempt",
                attempt + 1
            )

            req = urllib.request.Request(
                OPENROUTER_URL,
                data=body,
                headers=headers,
                method="POST"
            )

            with urllib.request.urlopen(
                req,
                timeout=180
            ) as response:

                raw = response.read()

                status_code = response.getcode()

            decoded = raw.decode(
                "utf-8",
                errors="replace"
            )

            try:

                data = json.loads(
                    decoded
                )

            except Exception:

                raise RuntimeError(
                    "OpenRouter returned invalid JSON:\n"
                    + decoded[:2000]
                )

            if status_code < 200 or status_code >= 300:

                error_message = (
                    data.get(
                        "error",
                        {}
                    )
                )

                if isinstance(
                    error_message,
                    dict
                ):

                    error_message = (
                        error_message.get(
                            "message",
                            str(error_message)
                        )
                    )

                raise RuntimeError(
                    f"OpenRouter HTTP {status_code}: "
                    f"{error_message}"
                )

            translated = extract_openrouter_text(
                data
            )

            if translated:

                print(
                    "QWEN SUCCESS"
                )

                return translated

            raise RuntimeError(
                "OpenRouter returned no translation text."
            )

        except urllib.error.HTTPError as e:

            try:

                raw_error = e.read().decode(
                    "utf-8",
                    errors="replace"
                )

            except Exception:

                raw_error = str(e)

            last_error = RuntimeError(
                f"OpenRouter HTTP {e.code}: "
                f"{raw_error}"
            )

            print(
                "OPENROUTER ERROR:",
                repr(last_error)
            )

            # 401 / 403 means key/access problem.
            if e.code in (
                401,
                403
            ):

                raise last_error

            # 429 means Qwen itself is rate limited.
            if e.code == 429:

                if attempt < MAX_RETRIES:

                    wait_time = (
                        5 * (attempt + 1)
                    )

                    print(
                        "Qwen rate limited."
                        f" Waiting {wait_time}s..."
                    )

                    time.sleep(
                        wait_time
                    )

                    continue

                raise last_error

            if attempt < MAX_RETRIES:

                wait_time = (
                    2 ** attempt
                )

                time.sleep(
                    wait_time
                )

                continue

        except Exception as e:

            last_error = e

            print(
                "OPENROUTER ERROR:",
                repr(e)
            )

            if attempt < MAX_RETRIES:

                wait_time = (
                    2 ** attempt
                )

                time.sleep(
                    wait_time
                )

                continue

    raise RuntimeError(
        "Qwen / OpenRouter translation failed:\n"
        + str(last_error)
    )


# ============================================================
# SMART TRANSLATION
# ============================================================

def translate_text(
    text,
    job
):

    # --------------------------------------------------------
    # If Gemini has already failed earlier in this run,
    # don't even attempt Gemini again.
    # --------------------------------------------------------

    if not is_gemini_disabled():

        try:

            return translate_with_gemini(
                text,
                job
            )

        except Exception as e:

            error_text = str(
                e
            )

            print(
                "GEMINI FAILED:"
            )

            print(
                error_text
            )

            # ------------------------------------------------
            # Gemini quota/model/access failure.
            # Immediately move to Qwen.
            # ------------------------------------------------

            if (
                "GEMINI_FALLBACK_REQUIRED"
                in error_text
            ):

                print(
                    "SWITCHING TO QWEN..."
                )

            else:

                # Any Gemini failure that reaches here
                # also causes fallback so the novel doesn't
                # get stuck.
                disable_gemini(
                    error_text
                )

    # --------------------------------------------------------
    # QWEN FALLBACK
    # --------------------------------------------------------

    print(
        "USING QWEN / OPENROUTER"
    )

    return translate_with_qwen(
        text,
        job
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

        save_job(
            job_id
        )

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

                # ------------------------------------------------
                # Display which AI is about to be used.
                # ------------------------------------------------

                if is_gemini_disabled():

                    provider_text = (
                        "Qwen / OpenRouter"
                    )

                    job["provider"] = "Qwen"

                else:

                    provider_text = "Gemini"

                    job["provider"] = "Gemini"

                job["status"] = (
                    f"{provider_text}: "
                    f"Translating chapter "
                    f"{index + 1}/{total} "
                    f"(part "
                    f"{piece_number + 1}/"
                    f"{len(pieces)})..."
                )

                save_job(
                    job_id
                )

                translated = translate_text(
                    piece,
                    job
                )

                translated_pieces.append(
                    translated
                )

                # ------------------------------------------------
                # Delay between API requests.
                # ------------------------------------------------

                if (
                    piece_number
                    <
                    len(pieces) - 1
                ):

                    time.sleep(
                        REQUEST_DELAY
                    )

            final_translation = (
                "\n\n".join(
                    translated_pieces
                ).strip()
            )

            # ----------------------------------------------------
            # IMPORTANT:
            #
            # Only mark the chapter completed AFTER the entire
            # chapter has successfully translated.
            #
            # This prevents partial chapters from being counted
            # as completed.
            # ----------------------------------------------------

            job["translations"].append(
                final_translation
            )

            job["translated_chapters"] += 1

            all_translated = (
                "\n\n".join(
                    job["translations"]
                )
            )

            job["words"] = count_words(
                all_translated
            )

            job["percent"] = int(
                (
                    job["translated_chapters"]
                    /
                    total
                ) * 100
            )

            job["status"] = (
                f"Completed chapter "
                f"{job['translated_chapters']}/"
                f"{total}. "
                f"{job['words']:,} English words translated."
            )

            save_job(
                job_id
            )

        job["status"] = (
            "Translation complete!"
        )

        job["percent"] = 100

        save_job(
            job_id
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
            "Translation stopped. "
            "Completed chapters were saved."
        )

        save_job(
            job_id
        )

    finally:

        job["running"] = False

        save_job(
            job_id
        )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    return render_template_string(
        PAGE,
        jobs=jobs,
        min_words=DOWNLOAD_MIN_WORDS,
        gemini_disabled=is_gemini_disabled(),
        gemini_reason=gemini_disable_reason,
        openrouter_configured=bool(
            OPENROUTER_API_KEY
        )
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

            "provider":
                None

        }

        save_job(
            job_id
        )

        print(
            f"Uploaded "
            f"{uploaded.filename}: "
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

    if job.get(
        "running",
        False
    ):

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

    # --------------------------------------------------------
    # Preserve previous translations.
    # --------------------------------------------------------

    job["error"] = None

    job["status"] = (
        "Starting translation..."
    )

    save_job(
        job_id
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
# DOWNLOAD EPUB
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

    if not job.get(
        "translations"
    ):

        return "Nothing translated yet."

    # --------------------------------------------------------
    # Require 30,000 words unless entire novel is finished.
    # --------------------------------------------------------

    complete = (
        job["translated_chapters"]
        >=
        job["total_chapters"]
    )

    if (
        job["words"]
        <
        DOWNLOAD_MIN_WORDS
        and
        not complete
    ):

        return (
            "Download becomes available after "
            f"{DOWNLOAD_MIN_WORDS:,} English words."
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

            mimetype:
                "application/epub+zip",

            as_attachment:
                True,

            download_name:
                output_name
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

        # ----------------------------------------------------
        # EPUB mimetype MUST be first and uncompressed.
        # ----------------------------------------------------

        epub.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED
        )

        # ----------------------------------------------------
        # Container
        # ----------------------------------------------------

        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>

<container
    version="1.0"
    xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
>

<rootfiles>

<rootfile
    full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"
/>

</rootfiles>

</container>
"""
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

            paragraphs = (
                safe_translation
                .split("\n")
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

            chapter_html = f"""
<?xml version="1.0" encoding="UTF-8"?>

<!DOCTYPE html>

<html
    xmlns="http://www.w3.org/1999/xhtml"
>

<head>

<meta charset="UTF-8"/>

<title>
{html.escape(title)}
</title>

<link
    rel="stylesheet"
    type="text/css"
    href="style.css"
/>

</head>

<body>

<h2>
{html.escape(title)}
</h2>

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

        # ----------------------------------------------------
        # CSS
        # ----------------------------------------------------

        css = """
body {

    font-family:
        serif;

    line-height:
        1.6;

    margin:
        5%;
}

h1,
h2 {

    text-align:
        center;
}

p {

    text-indent:
        1.5em;

    margin-bottom:
        1em;
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

        opf = f"""
<?xml version="1.0" encoding="UTF-8"?>

<package
    version="3.0"
    xmlns="http://www.idpf.org/2007/opf"
    unique-identifier="BookID"
>

<metadata
    xmlns:dc="http://purl.org/dc/elements/1.1/"
>

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
    media-type="text/css"
/>

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

        save_all_jobs()

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

        "status":
            "ok",

        "gemini_configured":
            bool(GEMINI_API_KEY),

        "gemini_model":
            GEMINI_MODEL,

        "gemini_disabled":
            is_gemini_disabled(),

        "gemini_disable_reason":
            gemini_disable_reason,

        "openrouter_configured":
            bool(OPENROUTER_API_KEY),

        "openrouter_model":
            OPENROUTER_MODEL,

        "download_min_words":
            DOWNLOAD_MIN_WORDS
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
