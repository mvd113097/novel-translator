import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
from flask import Flask, request, redirect, url_for, render_template_string, send_file

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Gemini API key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Change this in Render environment variables if desired.
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

# Maximum Chinese characters sent in ONE Gemini request.
# Smaller batches are safer and easier on quotas.
MAX_CHARS_PER_REQUEST = 7000

# Minimum translated English words required before download.
DOWNLOAD_MIN_WORDS = 30000

# Small delay between requests.
REQUEST_DELAY = 3

# Maximum automatic retries.
MAX_RETRIES = 2


# ============================================================
# GEMINI CLIENT
# ============================================================

client = None

if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("GEMINI CLIENT ERROR:", repr(e))


# ============================================================
# IN-MEMORY JOB STORAGE
# ============================================================

jobs = {}


# ============================================================
# HTML
# ============================================================

PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
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

        input, button {
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
    </style>
</head>

<body>

<div class="container">

    <h1>📚 Novel Translator</h1>

    <p>
        Upload a TXT or EPUB novel and translate it to English using Gemini.
    </p>

    <p class="small">
        Translation is cumulative. Once enough English text has been translated,
        you can download the current EPUB.
    </p>

    <form action="/upload" method="POST" enctype="multipart/form-data">

        <input
            type="file"
            name="file"
            accept=".txt,.epub"
            required
        >

        <button type="submit">
            Upload Novel
        </button>

    </form>


    {% if jobs %}

        <h2>Your Novels</h2>

        {% for job_id, job in jobs.items() %}

            <div class="job">

                <strong>{{ job.filename }}</strong>

                <p>
                    Chapters: {{ job.translated_chapters }}/{{ job.total_chapters }}
                </p>

                <p>
                    English words: {{ job.words }}
                </p>

                <p>
                    Status: {{ job.status }}
                </p>

                {% if job.error %}

                    <div class="status error">
                        {{ job.error }}
                    </div>

                {% endif %}

                {% if job.translated_chapters < job.total_chapters and not job.running %}

                    <form action="/translate/{{ job_id }}" method="GET">
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
                        setTimeout(function() {
                            location.reload();
                        }, 4000);
                    </script>

                {% endif %}


                {% if job.words >= min_words %}

                    <a href="/download/{{ job_id }}">
                        📥 Download Current EPUB
                    </a>

                {% endif %}


                {% if job.translated_chapters == job.total_chapters and not job.error %}

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
                        Delete
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
# UTILITY FUNCTIONS
# ============================================================

def clean_text(text):
    """
    Clean excessive whitespace while keeping paragraphs.
    """

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Remove weird null characters
    text = text.replace("\x00", "")

    # Normalize excessive blank lines
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    return text.strip()


def count_words(text):
    """
    Count English words.
    """

    return len(re.findall(r"\b[\w'-]+\b", text))


def split_large_text(text, max_chars=MAX_CHARS_PER_REQUEST):
    """
    Split text into manageable pieces.

    Tries to split at paragraph boundaries.
    """

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

        # Very long paragraph
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

    # Try to detect chapter headings.
    pattern = re.compile(
        r"(?im)^(第\s*[0-9一二三四五六七八九十百千万]+\s*[章回节]|"
        r"chapter\s+\d+.*)$"
    )

    matches = list(pattern.finditer(text))

    chapters = []

    if matches:

        for i, match in enumerate(matches):

            start = match.start()

            end = (
                matches[i + 1].start()
                if i + 1 < len(matches)
                else len(text)
            )

            chapter_text = text[start:end].strip()

            if chapter_text:
                chapters.append(chapter_text)

    else:

        # No chapter headings detected.
        # Split the novel into large sections.
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

    with zipfile.ZipFile(io.BytesIO(data)) as z:

        names = z.namelist()

        html_files = [
            n for n in names
            if n.lower().endswith((
                ".xhtml",
                ".html",
                ".htm"
            ))
        ]

        for filename in html_files:

            try:
                raw = z.read(filename).decode(
                    "utf-8",
                    errors="ignore"
                )

            except Exception:
                continue

            # Remove scripts/styles
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

            # Convert common paragraph tags
            raw = re.sub(
                r"</(p|div|br|h1|h2|h3|li)>",
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

            raw = html.unescape(raw)

            text = clean_text(raw)

            # Ignore tiny files such as navigation pages
            if len(text) < 100:
                continue

            # Split oversized XHTML files
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


# ============================================================
# FILE PARSER
# ============================================================

def parse_uploaded_file(filename, data):

    lower = filename.lower()

    if lower.endswith(".txt"):
        return parse_txt(data)

    if lower.endswith(".epub"):
        return parse_epub(data)

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
            "Add GEMINI_API_KEY to your Render environment variables."
        )

    prompt = f"""
You are a professional Chinese-to-English web novel translator.

Translate the Chinese text below into natural, fluent English.

IMPORTANT RULES:

1. Translate everything.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Preserve the meaning and details.
5. Keep character names consistent.
6. Keep character gender/pronouns consistent based on context.
7. Preserve dialogue.
8. Preserve paragraph breaks when possible.
9. Do not add explanations.
10. Output ONLY the English translation.
11. Do not include phrases such as "Here is the translation".
12. Do not put the translation inside Markdown code blocks.

Chinese text:

{text}
"""

    last_error = None

    for attempt in range(MAX_RETRIES + 1):

        try:

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2
                )
            )

            # ==================================================
            # IMPORTANT:
            # DO NOT USE response["text"]
            #
            # The google-genai SDK returns a response object.
            # The normal way is response.text.
            # ==================================================

            translated = getattr(
                response,
                "text",
                None
            )

            if translated:

                translated = translated.strip()

                if translated:
                    return translated

            # Sometimes text can be found through candidates.
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

            error_string = str(e)

            print(
                "GEMINI ERROR:",
                repr(e)
            )

            # Quota / rate limit errors should NOT
            # be repeatedly hammered.
            quota_words = [
                "quota",
                "429",
                "resource exhausted",
                "rate limit",
                "too many requests"
            ]

            if any(
                word in error_string.lower()
                for word in quota_words
            ):

                raise RuntimeError(
                    "Gemini quota/rate limit reached.\n\n"
                    "Gemini rejected the request because "
                    "the API quota has been exceeded.\n\n"
                    f"Original error: {error_string}"
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
# TRANSLATION WORKER
# ============================================================

def translation_worker(job_id):

    job = jobs.get(job_id)

    if not job:
        return

    try:

        job["running"] = True
        job["error"] = None

        total = len(job["chapters"])

        while job["translated_chapters"] < total:

            index = job["translated_chapters"]

            original_chapter = job["chapters"][index]

            # Split chapter if necessary.
            pieces = split_large_text(
                original_chapter,
                MAX_CHARS_PER_REQUEST
            )

            translated_pieces = []

            for piece_number, piece in enumerate(pieces):

                job["status"] = (
                    f"Translating chapter {index + 1}/{total} "
                    f"(part {piece_number + 1}/{len(pieces)})..."
                )

                translated = translate_with_gemini(
                    piece
                )

                translated_pieces.append(
                    translated
                )

                # Give API a little breathing room.
                time.sleep(
                    REQUEST_DELAY
                )

            final_translation = "\n\n".join(
                translated_pieces
            ).strip()

            job["translations"].append(
                final_translation
            )

            job["translated_chapters"] += 1

            job["words"] = count_words(
                "\n\n".join(
                    job["translations"]
                )
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
                f"{job['translated_chapters']}/{total}. "
                f"{job['words']:,} English words translated."
            )

            save_job(job_id)

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
# SIMPLE JOB PERSISTENCE
# ============================================================

def save_job(job_id):

    """
    Keep everything in memory for now.

    Render's filesystem is temporary, so this is mainly
    to keep the current process safe.

    The translated chapters remain available while the
    service is running.
    """

    return


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    return render_template_string(
        PAGE,
        jobs=jobs,
        min_words=DOWNLOAD_MIN_WORDS
    )


# ============================================================
# UPLOAD
# ============================================================

@app.route("/upload", methods=["POST"])
def upload():

    uploaded = request.files.get("file")

    if not uploaded:
        return redirect(url_for("index"))

    if not uploaded.filename:
        return redirect(url_for("index"))

    try:

        data = uploaded.read()

        chapters = parse_uploaded_file(
            uploaded.filename,
            data
        )

        job_id = str(uuid.uuid4())

        jobs[job_id] = {
            "id": job_id,
            "filename": uploaded.filename,
            "chapters": chapters,
            "translations": [],
            "translated_chapters": 0,
            "total_chapters": len(chapters),
            "words": 0,
            "percent": 0,
            "status": "Uploaded. Ready to translate.",
            "error": None,
            "running": False
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
# START TRANSLATION
# ============================================================

@app.route("/translate/<job_id>")
def translate(job_id):

    job = jobs.get(job_id)

    if not job:
        return redirect(
            url_for("index")
        )

    if job["running"]:
        return redirect(
            url_for("index")
        )

    if job["translated_chapters"] >= job["total_chapters"]:
        return redirect(
            url_for("index")
        )

    # Reset only the error.
    # DO NOT erase previous translations.
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
# DOWNLOAD EPUB
# ============================================================

@app.route("/download/<job_id>")
def download(job_id):

    job = jobs.get(job_id)

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
            + "_translated.epub"
        )

        return send_file(
            io.BytesIO(epub_bytes),
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

def create_epub(filename, translations):

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

        # ---------------------------------------------
        # mimetype MUST be first and uncompressed.
        # ---------------------------------------------

        epub.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED
        )

        # ---------------------------------------------
        # container.xml
        # ---------------------------------------------

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

        # ---------------------------------------------
        # Chapters
        # ---------------------------------------------

        manifest_items = []
        spine_items = []
        chapter_files = []

        for i, translation in enumerate(translations):

            chapter_filename = (
                f"chapter{i + 1}.xhtml"
            )

            chapter_files.append(
                chapter_filename
            )

            title = f"Chapter {i + 1}"

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
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="UTF-8"/>
<title>{title}</title>
</head>
<body>
<h2>{title}</h2>
{body}
</body>
</html>
"""

            epub.writestr(
                "OEBPS/" + chapter_filename,
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

        # ---------------------------------------------
        # CSS
        # ---------------------------------------------

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

        # ---------------------------------------------
        # OPF
        # ---------------------------------------------

        manifest = "\n".join(
            manifest_items
        )

        spine = "\n".join(
            spine_items
        )

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
        "status": "ok",
        "gemini_configured": bool(
            GEMINI_API_KEY
        ),
        "model": GEMINI_MODEL
    }


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

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
