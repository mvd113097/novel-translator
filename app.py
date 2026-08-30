import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
import json

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


# ============================================================
# CONFIGURATION
# ============================================================

app = Flask(__name__)

# IMPORTANT:
# Set APP_PASSWORD in Render Environment Variables.
#
# If you don't set it, the temporary default is:
# 1234
#
# CHANGE THIS in Render for real use.
APP_PASSWORD = os.environ.get(
    "APP_PASSWORD",
    "1234"
)

# Flask session secret
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key"
)

app.secret_key = SECRET_KEY

UPLOAD_FOLDER = "uploads"
DATA_FOLDER = "job_data"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)


# ============================================================
# GEMINI CONFIG
# ============================================================

GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY"
)

# Current Gemini model.
# Can be changed from Render environment variables.
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)

# Maximum Chinese characters per Gemini request.
#
# 12000 is deliberately larger than the old 7000 so that
# more novel text can be translated per request.
#
# If you experience output truncation, lower this to 9000.
MAX_CHARS_PER_REQUEST = int(
    os.environ.get(
        "MAX_CHARS_PER_REQUEST",
        "12000"
    )
)

# Minimum English words before download is available.
DOWNLOAD_MIN_WORDS = int(
    os.environ.get(
        "DOWNLOAD_MIN_WORDS",
        "30000"
    )
)

# Delay between successful requests.
REQUEST_DELAY = float(
    os.environ.get(
        "REQUEST_DELAY",
        "3"
    )
)

# IMPORTANT:
# Do NOT retry quota errors.
#
# A retry on a quota error only wastes another request.
MAX_RETRIES = 1


# ============================================================
# GEMINI CLIENT
# ============================================================

client = None

if GEMINI_API_KEY:

    try:

        client = genai.Client(
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

else:

    print(
        "WARNING: GEMINI_API_KEY is not configured."
    )


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()


# ============================================================
# PASSWORD / LOGIN
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
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    margin: 0;
    padding: 20px;
}

.container {
    max-width: 450px;
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

    margin-top: 12px;

    border-radius: 8px;

    border: 1px solid #ccc;

    font-size: 16px;
}

button {

    background: #222;

    color: white;

    border: none;

    cursor: pointer;
}

.error {

    margin-top: 15px;

    padding: 12px;

    border-radius: 8px;

    background: #ffe5e5;

    color: #900;
}

.small {

    color: #666;

    font-size: 14px;

}

</style>

</head>

<body>

<div class="container">

<h1>🔐 Novel Translator</h1>

<p>
Enter the password to access the translator.
</p>

<form method="POST">

<input
    type="password"
    name="password"
    placeholder="Password"
    required
    autofocus
>

<button type="submit">
    Login
</button>

</form>

{% if error %}

<div class="error">
{{ error }}
</div>

{% endif %}

<p class="small">
Private novel translator
</p>

</div>

</body>

</html>
"""


def logged_in():
    return session.get(
        "logged_in",
        False
    )


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

    padding: 20px;

}

.container {

    max-width: 700px;

    margin: auto;

    background: white;

    padding: 25px;

    border-radius: 15px;

    box-shadow:
        0 3px 15px rgba(0,0,0,0.1);

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

.warning {

    background: #fff4cc;

    color: #705500;

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

.logout {

    background: #777;

}

.info {

    padding: 12px;

    background: #eef5ff;

    border-radius: 8px;

    margin-top: 15px;

}

</style>

</head>


<body>

<div class="container">

<div style="text-align:right">

<a
    class="logout"
    href="/logout"
>
    Logout
</a>

</div>


<h1>📚 Novel Translator</h1>


<p>

Upload a TXT or EPUB novel and translate it
to English using Gemini.

</p>


<div class="info">

<b>Current Gemini model:</b>
{{ model }}

<br><br>

<b>Download requirement:</b>
{{ min_words|comma }} English words

</div>


<p class="small">

Translation is cumulative.

You can download the translated portion
once at least {{ min_words|comma }} English
words have been translated.

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


{% if upload_error %}

<div class="status error">

{{ upload_error }}

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
<b>{{ job.words|comma }}</b>

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


{% if job.quota_stopped %}

<div class="status warning">

⏸ Gemini quota/rate limit was reached.

The translated chapters have been saved.

You can press Continue Translation
later after the quota resets.

</div>

{% endif %}


{% if job.words >= min_words %}

<a href="/download/{{ job_id }}">

📥 Download Current EPUB

</a>

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
    5000
);

</script>

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

🗑 Delete

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

        return str(value)


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

        # Handle extremely long paragraphs.
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


    # Chinese chapter headings.
    pattern = re.compile(
        r"(?im)^"
        r"(第\s*[0-9一二三四五六七八九十百千万]+\s*[章回节]"
        r".*|"
        r"chapter\s+\d+.*)"
        r"$"
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

            chapter_text = (
                text[start:end].strip()
            )

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
# GEMINI TRANSLATION
# ============================================================

def translate_with_gemini(text):

    if not client:

        raise RuntimeError(
            "GEMINI_API_KEY is missing. "
            "Add GEMINI_API_KEY to Render Environment Variables."
        )


    prompt = f"""
You are a professional Chinese-to-English web novel translator.

Translate the following Chinese web novel text into natural,
fluent English.

This is a CONTINUOUS NOVEL TRANSLATION.

IMPORTANT RULES:

1. Translate EVERYTHING.
2. Do NOT summarize.
3. Do NOT shorten the story.
4. Do NOT omit sentences.
5. Do NOT invent events.
6. Preserve all details.
7. Preserve paragraph breaks whenever possible.
8. Preserve dialogue.
9. Keep character names consistent.
10. Keep gender and pronouns consistent using context.
11. Keep titles, relationships, and forms of address consistent.
12. Translate Chinese idioms naturally while preserving meaning.
13. Do not explain your translation.
14. Do not add notes.
15. Do not say "Here is the translation".
16. Output ONLY the English translation.
17. Do not use Markdown code blocks.
18. Do not include the original Chinese.
19. Do not summarize at the end.
20. Treat this as a serious published web-novel translation.

Chinese text begins below:

{text}

Chinese text ends above.

Return ONLY the English translation.
"""


    last_error = None


    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            # =================================================
            # NEW GEMINI INTERACTIONS API
            # =================================================

            interaction = client.interactions.create(

                model=GEMINI_MODEL,

                input=prompt

            )


            translated = getattr(
                interaction,
                "output_text",
                None
            )


            if translated:

                translated = (
                    translated
                    .strip()
                )

                if translated:

                    return translated


            # Fallback for SDK response formats.
            steps = getattr(
                interaction,
                "steps",
                None
            )

            if steps:

                pieces = []

                for step in steps:

                    step_type = getattr(
                        step,
                        "type",
                        None
                    )

                    if step_type != "model_output":

                        continue

                    content = getattr(
                        step,
                        "content",
                        None
                    )

                    if not content:

                        continue

                    for block in content:

                        block_text = getattr(
                            block,
                            "text",
                            None
                        )

                        if block_text:

                            pieces.append(
                                block_text
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

            error_string = str(e)

            lower_error = (
                error_string.lower()
            )


            print(
                "GEMINI ERROR:",
                repr(e)
            )


            # =================================================
            # QUOTA / RATE LIMIT
            # =================================================

            quota_words = [

                "quota",

                "429",

                "resource exhausted",

                "rate limit",

                "too many requests",

                "exceeded"

            ]


            if any(
                word in lower_error
                for word in quota_words
            ):

                raise RuntimeError(
                    "GEMINI_QUOTA\n\n"
                    "Gemini quota or rate limit "
                    "was reached.\n\n"
                    "Already translated chapters "
                    "have been saved.\n\n"
                    "Please continue later when "
                    "your Gemini quota resets.\n\n"
                    + error_string
                )


            # =================================================
            # INVALID / UNAVAILABLE MODEL
            # =================================================

            if (
                "404" in lower_error
                and (
                    "model" in lower_error
                    or "not_found" in lower_error
                )
            ):

                raise RuntimeError(
                    "GEMINI_MODEL_ERROR\n\n"
                    f"The configured Gemini model "
                    f"'{GEMINI_MODEL}' is unavailable.\n\n"
                    "Set GEMINI_MODEL to:\n"
                    "gemini-3.6-flash"
                )


            if attempt < MAX_RETRIES:

                time.sleep(
                    2 ** attempt
                )


    raise RuntimeError(
        "Gemini translation failed.\n\n"
        + str(last_error)
    )


# ============================================================
# JOB PERSISTENCE
# ============================================================

def job_file(job_id):

    return os.path.join(
        DATA_FOLDER,
        job_id + ".json"
    )


def save_job(job_id):

    job = jobs.get(
        job_id
    )

    if not job:

        return


    # Don't save runtime-only lock/thread info.
    safe_job = {}

    for key, value in job.items():

        if key in (
            "running",
        ):

            continue

        safe_job[key] = value


    try:

        with open(
            job_file(job_id),
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                safe_job,
                f,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            "SAVE JOB ERROR:",
            repr(e)
        )


def load_jobs():

    if not os.path.exists(
        DATA_FOLDER
    ):

        return


    for filename in os.listdir(
        DATA_FOLDER
    ):

        if not filename.endswith(
            ".json"
        ):

            continue


        path = os.path.join(
            DATA_FOLDER,
            filename
        )


        try:

            with open(
                path,
                "r",
                encoding="utf-8"
            ) as f:

                job = json.load(f)


            job_id = job.get(
                "id"
            )

            if not job_id:

                continue


            job["running"] = False

            jobs[job_id] = job


        except Exception as e:

            print(
                "LOAD JOB ERROR:",
                repr(e)
            )


# Load saved jobs when application starts.
load_jobs()


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

        job["quota_stopped"] = False


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

                job["status"] = (

                    f"Translating chapter "
                    f"{index + 1}/{total} "

                    f"(part "
                    f"{piece_number + 1}/"
                    f"{len(pieces)})..."

                )


                translated = (
                    translate_with_gemini(
                        piece
                    )
                )


                translated_pieces.append(
                    translated
                )


                # Small delay after successful request.
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
                    job[
                        "translated_chapters"
                    ]
                    /
                    total
                )
                *
                100

            )


            job["status"] = (

                f"Completed chapter "
                f"{job['translated_chapters']}/"
                f"{total}. "

                f"{job['words']:,} English "
                f"words translated."

            )


            # SAVE AFTER EVERY COMPLETED CHAPTER.
            save_job(
                job_id
            )


        job["status"] = (
            "Translation complete!"
        )

        job["quota_stopped"] = False

        save_job(
            job_id
        )


    except Exception as e:

        print(
            "TRANSLATION WORKER ERROR:",
            repr(e)
        )


        error_string = str(e)


        if (
            "GEMINI_QUOTA"
            in error_string
        ):

            job["quota_stopped"] = True

            job["error"] = (
                "Gemini quota reached. "
                "Your completed translations "
                "are saved."
            )

            job["status"] = (
                "Paused because Gemini quota "
                "was reached."
            )


        else:

            job["quota_stopped"] = False

            job["error"] = (
                error_string
            )

            job["status"] = (
                "Translation stopped."
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
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=[
        "GET",
        "POST"
    ]
)
def login():

    if logged_in():

        return redirect(
            url_for("index")
        )


    error = None


    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )


        if password == APP_PASSWORD:

            session[
                "logged_in"
            ] = True

            return redirect(
                url_for("index")
            )


        error = (
            "Incorrect password."
        )


    return render_template_string(
        LOGIN_PAGE,
        error=error
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    if not logged_in():

        return redirect(
            url_for("login")
        )


    return render_template_string(

        PAGE,

        jobs=jobs,

        min_words=DOWNLOAD_MIN_WORDS,

        model=GEMINI_MODEL,

        upload_error=None

    )


# ============================================================
# UPLOAD
# ============================================================

@app.route(
    "/upload",
    methods=["POST"]
)
def upload():

    if not logged_in():

        return redirect(
            url_for("login")
        )


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

            "id": job_id,

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

            "quota_stopped":
                False,

            "running":
                False

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

        return render_template_string(

            PAGE,

            jobs=jobs,

            min_words=DOWNLOAD_MIN_WORDS,

            model=GEMINI_MODEL,

            upload_error=str(e)

        )


# ============================================================
# START / CONTINUE TRANSLATION
# ============================================================

@app.route(
    "/translate/<job_id>"
)
def translate(job_id):

    if not logged_in():

        return redirect(
            url_for("login")
        )


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

    job["quota_stopped"] = False


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

    if not logged_in():

        return redirect(
            url_for("login")
        )


    job = jobs.get(
        job_id
    )


    if not job:

        return redirect(
            url_for("index")
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
            +
            "_translated.epub"

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

        return f"""

        <h2>EPUB creation error</h2>

        <p>
        {html.escape(str(e))}
        </p>

        <p>
        <a href="/">
        Go back
        </a>
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
        +
        " - English Translation"

    )


    buf = io.BytesIO()


    with zipfile.ZipFile(

        buf,

        "w",

        zipfile.ZIP_DEFLATED

    ) as epub:


        # =====================================================
        # MIME TYPE
        # =====================================================

        epub.writestr(

            "mimetype",

            "application/epub+zip",

            compress_type=
                zipfile.ZIP_STORED

        )


        # =====================================================
        # CONTAINER
        # =====================================================

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


        # =====================================================
        # CHAPTERS
        # =====================================================

        for i, translation in enumerate(
            translations
        ):


            chapter_filename = (

                f"chapter{i + 1}.xhtml"

            )


            title = (

                f"Chapter {i + 1}"

            )


            safe_translation = (
                html.escape(
                    translation
                )
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
                        +
                        paragraph
                        +
                        "</p>\n"

                    )


            chapter_html = f"""

<?xml version="1.0"
encoding="UTF-8"?>

<!DOCTYPE html>

<html
xmlns="http://www.w3.org/1999/xhtml">

<head>

<meta charset="UTF-8"/>

<title>
{title}
</title>

<link
rel="stylesheet"
type="text/css"
href="style.css"/>

</head>

<body>

<h2>
{title}
</h2>

{body}

</body>

</html>

"""


            epub.writestr(

                "OEBPS/"
                +
                chapter_filename,

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


        # =====================================================
        # CSS
        # =====================================================

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


        # =====================================================
        # OPF
        # =====================================================

        manifest = "\n".join(
            manifest_items
        )

        spine = "\n".join(
            spine_items
        )


        opf = f"""

<?xml version="1.0"
encoding="UTF-8"?>

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

Gemini Translation

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

    if not logged_in():

        return redirect(
            url_for("login")
        )


    if job_id in jobs:

        del jobs[job_id]


    try:

        path = job_file(
            job_id
        )

        if os.path.exists(path):

            os.remove(path)

    except Exception:

        pass


    return redirect(
        url_for("index")
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return {

        "status": "ok",

        "gemini_configured":
            bool(GEMINI_API_KEY),

        "model":
            GEMINI_MODEL,

        "login_enabled":
            True

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
