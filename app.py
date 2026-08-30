import os
import re
import io
import zipfile
from flask import Flask, request, render_template_string, send_file
from supabase import create_client
from google import genai
from ebooklib import epub
from bs4 import BeautifulSoup

app = Flask(__name__)

# ---------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase = None
gemini = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

if GEMINI_API_KEY:
    gemini = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def count_words(text):
    """Count English-style words."""
    return len(re.findall(r"\b[\w'-]+\b", text))


def split_text_into_chapters(text):
    """
    Attempts to split TXT novels into chapters.
    If no chapter headings are found, the entire file
    becomes one chapter.
    """

    pattern = re.compile(
        r"(?im)^(?:chapter|chap\.|第\s*\d+\s*[章节卷回])"
        r".*$"
    )

    matches = list(pattern.finditer(text))

    if not matches:
        return [
            {
                "number": 1,
                "title": "Chapter 1",
                "text": text.strip()
            }
        ]

    chapters = []

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        block = text[start:end].strip()

        lines = block.splitlines()
        title = lines[0].strip() if lines else f"Chapter {i + 1}"

        chapters.append(
            {
                "number": i + 1,
                "title": title,
                "text": block
            }
        )

    return chapters


def extract_txt(file_bytes):
    """Extract TXT text using common encodings."""

    for encoding in ["utf-8", "utf-8-sig", "gb18030", "gbk"]:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            pass

    return file_bytes.decode("utf-8", errors="ignore")


def extract_epub(file_bytes):
    """Extract readable text from an EPUB."""

    book = epub.read_epub(io.BytesIO(file_bytes))

    sections = []

    for item in book.get_items():

        if item.get_type() == 9:  # XHTML
            soup = BeautifulSoup(
                item.get_content(),
                "html.parser"
            )

            text = soup.get_text("\n", strip=True)

            if text:
                sections.append(text)

    return "\n\n".join(sections)


def translate_text(text):
    """Translate Chinese text to English using Gemini."""

    if not gemini:
        raise RuntimeError(
            "Gemini API key is not configured."
        )

    prompt = f"""
Translate the following Chinese novel text into natural,
fluent English.

IMPORTANT RULES:

- Preserve the meaning and details.
- Do not summarize.
- Do not omit sentences.
- Keep dialogue natural.
- Keep character names consistent.
- Preserve paragraph breaks.
- Do not add explanations.
- Translate only the novel text.

TEXT:

{text}
"""

    response = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    return response.text.strip()


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def create_novel(title, filename, total_words):
    result = supabase.table("novels").insert(
        {
            "title": title,
            "original_filename": filename,
            "total_words": total_words,
            "translated_words": 0,
            "status": "waiting"
        }
    ).execute()

    return result.data[0]


def save_chapter(novel_id, chapter):
    result = supabase.table("chapters").insert(
        {
            "novel_id": novel_id,
            "chapter_number": chapter["number"],
            "title": chapter["title"],
            "original_text": chapter["text"],
            "translated_text": None,
            "original_words": count_words(chapter["text"]),
            "translated_words": 0,
            "status": "waiting"
        }
    ).execute()

    return result.data[0]


def get_chapters(novel_id):
    result = (
        supabase
        .table("chapters")
        .select("*")
        .eq("novel_id", novel_id)
        .order("chapter_number")
        .execute()
    )

    return result.data


# ---------------------------------------------------------
# WEB PAGE
# ---------------------------------------------------------

HTML = """
<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Novel Translator</title>

<style>

body {
    font-family: Arial, sans-serif;
    max-width: 800px;
    margin: auto;
    padding: 20px;
    background: #f5f5f5;
}

.box {
    background: white;
    padding: 20px;
    border-radius: 12px;
    margin-bottom: 20px;
}

button {
    padding: 12px 20px;
    border: none;
    border-radius: 8px;
    background: #333;
    color: white;
    font-size: 16px;
}

input {
    width: 100%;
    padding: 12px;
    margin: 10px 0;
    box-sizing: border-box;
}

.progress {
    background: #ddd;
    border-radius: 10px;
    overflow: hidden;
}

.bar {
    background: #333;
    height: 20px;
    width: {{ progress }}%;
}

</style>

</head>

<body>

<h1>📚 Novel Translator</h1>

<div class="box">

<h2>Upload Novel</h2>

<form method="POST"
      action="/upload"
      enctype="multipart/form-data">

<input type="file"
       name="novel"
       accept=".txt,.epub"
       required>

<button type="submit">
Upload
</button>

</form>

</div>

{% if message %}

<div class="box">

{{ message }}

</div>

{% endif %}

{% if novel %}

<div class="box">

<h2>{{ novel.title }}</h2>

<p>
Original words:
{{ novel.total_words }}
</p>

<p>
Translated words:
{{ novel.translated_words }}
</p>

<div class="progress">
<div class="bar"></div>
</div>

<br>

<a href="/translate/{{ novel.id }}">
<button>
Continue Translation
</button>
</a>

</div>

{% endif %}

</body>

</html>
"""


# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route("/", methods=["GET"])
def home():

    novel = None
    message = None

    if supabase:

        result = (
            supabase
            .table("novels")
            .select("*")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if result.data:
            novel = result.data[0]

    progress = 0

    if novel and novel["total_words"]:

        progress = (
            novel["translated_words"]
            / novel["total_words"]
        ) * 100

    return render_template_string(
        HTML,
        novel=novel,
        message=message,
        progress=min(progress, 100)
    )


@app.route("/upload", methods=["POST"])
def upload():

    if not supabase:
        return render_template_string(
            HTML,
            message="Supabase is not configured.",
            novel=None,
            progress=0
        )

    file = request.files.get("novel")

    if not file:
        return render_template_string(
            HTML,
            message="Please choose a TXT or EPUB file.",
            novel=None,
            progress=0
        )

    filename = file.filename
    data = file.read()

    if filename.lower().endswith(".txt"):

        text = extract_txt(data)

    elif filename.lower().endswith(".epub"):

        text = extract_epub(data)

    else:

        return render_template_string(
            HTML,
            message="Only TXT and EPUB files are supported.",
            novel=None,
            progress=0
        )

    chapters = split_text_into_chapters(text)

    total_words = sum(
        count_words(chapter["text"])
        for chapter in chapters
    )

    title = os.path.splitext(filename)[0]

    novel = create_novel(
        title,
        filename,
        total_words
    )

    for chapter in chapters:
        save_chapter(
            novel["id"],
            chapter
        )

    return render_template_string(
        HTML,
        novel=novel,
        message=f"Uploaded {len(chapters)} chapters.",
        progress=0
    )


@app.route("/translate/<novel_id>")
def translate(novel_id):

    if not supabase:
        return "Supabase is not configured."

    chapters = get_chapters(novel_id)

    translated_words = 0

    for chapter in chapters:

        if chapter["status"] == "translated":
            translated_words += chapter["translated_words"]
            continue

        try:

            translated = translate_text(
                chapter["original_text"]
            )

            words = count_words(translated)

            (
                supabase
                .table("chapters")
                .update(
                    {
                        "translated_text": translated,
                        "translated_words": words,
                        "status": "translated"
                    }
                )
                .eq("id", chapter["id"])
                .execute()
            )

            translated_words += words

            (
                supabase
                .table("novels")
                .update(
                    {
                        "translated_words": translated_words,
                        "status": "translating"
                    }
                )
                .eq("id", novel_id)
                .execute()
            )

        except Exception as e:

            return f"""
            Translation stopped.

            Error:
            {str(e)}

            Already translated:
            {translated_words} words
            """

    (
        supabase
        .table("novels")
        .update(
            {
                "translated_words": translated_words,
                "status": "completed"
            }
        )
        .eq("id", novel_id)
        .execute()
    )

    return f"""
    <h1>Translation complete!</h1>

    <p>
    Translated approximately
    {translated_words:,} words.
    </p>

    <p>
    The next step is adding the
    30,000-word EPUB download system.
    </p>

    <a href="/">Back</a>
    """


# ---------------------------------------------------------
# START
# ---------------------------------------------------------

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
      )
