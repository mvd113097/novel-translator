import os
import re
import io

from flask import Flask, request, render_template_string
from supabase import create_client
from google import genai
from ebooklib import epub
from bs4 import BeautifulSoup


# =========================================================
# APP SETUP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


supabase = None
gemini = None


if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )


if GEMINI_API_KEY:
    gemini = genai.Client(
        api_key=GEMINI_API_KEY
    )


# =========================================================
# WORD COUNT
# =========================================================

def count_words(text):
    """
    Counts words for progress tracking.

    Chinese text does not use spaces between words,
    so this also provides a character-based fallback.
    """

    english_words = re.findall(
        r"\b[\w'-]+\b",
        text
    )

    chinese_chars = re.findall(
        r"[\u4e00-\u9fff]",
        text
    )

    if english_words:
        return len(english_words)

    return len(chinese_chars)


# =========================================================
# TXT EXTRACTION
# =========================================================

def extract_txt(file_bytes):

    encodings = [
        "utf-8",
        "utf-8-sig",
        "gb18030",
        "gbk"
    ]

    for encoding in encodings:

        try:
            return file_bytes.decode(encoding)

        except UnicodeDecodeError:
            continue

    return file_bytes.decode(
        "utf-8",
        errors="ignore"
    )


# =========================================================
# EPUB EXTRACTION
# =========================================================

def extract_epub(file_bytes):

    book = epub.read_epub(
        io.BytesIO(file_bytes)
    )

    sections = []

    for item in book.get_items():

        if item.get_type() == 9:

            soup = BeautifulSoup(
                item.get_content(),
                "html.parser"
            )

            text = soup.get_text(
                "\n",
                strip=True
            )

            if text:
                sections.append(text)

    return "\n\n".join(sections)


# =========================================================
# CHAPTER SPLITTING
# =========================================================

def split_text_into_chapters(text):

    patterns = [
        r"(?im)^(第\s*\d+\s*[章节卷回].*)$",
        r"(?im)^(chapter\s+\d+.*)$",
        r"(?im)^(chap\.\s*\d+.*)$"
    ]

    matches = []

    for pattern in patterns:

        found = list(
            re.finditer(
                pattern,
                text
            )
        )

        if len(found) > len(matches):
            matches = found

    # No chapter headings found
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

        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(text)

        block = text[start:end].strip()

        lines = block.splitlines()

        if lines:
            title = lines[0].strip()
        else:
            title = f"Chapter {i + 1}"

        chapters.append(
            {
                "number": i + 1,
                "title": title,
                "text": block
            }
        )

    return chapters


# =========================================================
# GEMINI TRANSLATION
# =========================================================

def translate_text(text):

    if not gemini:

        raise RuntimeError(
            "Gemini API key is not configured."
        )


    prompt = f"""
You are a professional Chinese-to-English web-novel translator.

Translate the following Chinese novel chapter into natural,
fluent English.

IMPORTANT RULES:

1. Translate everything.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Preserve the original meaning.
5. Preserve paragraph breaks.
6. Keep character names consistent.
7. Keep gender and pronouns consistent.
8. Keep dialogue natural.
9. Do not add explanations or translator notes.
10. Do not describe what you are doing.
11. Output ONLY the English translation.

CHAPTER:

{text}
"""


    interaction = gemini.interactions.create(
        model="gemini-3.6-flash",
        input=prompt
    )


    translated = interaction.output_text

    if not translated:
        raise RuntimeError(
            "Gemini returned an empty translation."
        )

    return translated.strip()


# =========================================================
# SUPABASE DATABASE FUNCTIONS
# =========================================================

def create_novel(
    title,
    filename,
    total_words
):

    result = (
        supabase
        .table("novels")
        .insert(
            {
                "title": title,
                "original_filename": filename,
                "total_words": total_words,
                "translated_words": 0,
                "status": "waiting"
            }
        )
        .execute()
    )

    return result.data[0]


def save_chapter(
    novel_id,
    chapter
):

    result = (
        supabase
        .table("chapters")
        .insert(
            {
                "novel_id": novel_id,
                "chapter_number": chapter["number"],
                "title": chapter["title"],
                "original_text": chapter["text"],
                "translated_text": None,
                "original_words": count_words(
                    chapter["text"]
                ),
                "translated_words": 0,
                "status": "waiting"
            }
        )
        .execute()
    )

    return result.data[0]


def get_chapters(novel_id):

    result = (
        supabase
        .table("chapters")
        .select("*")
        .eq(
            "novel_id",
            novel_id
        )
        .order(
            "chapter_number"
        )
        .execute()
    )

    return result.data


def get_latest_novel():

    result = (
        supabase
        .table("novels")
        .select("*")
        .order(
            "created_at",
            desc=True
        )
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


# =========================================================
# HTML
# =========================================================

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


h1 {

    margin-top: 0;
}


button {

    padding: 12px 20px;

    border: none;

    border-radius: 8px;

    background: #333;

    color: white;

    font-size: 16px;

    cursor: pointer;
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

    height: 22px;
}


.bar {

    background: #333;

    height: 22px;

}


.status {

    padding: 10px;

    background: #eee;

    border-radius: 8px;

}


a {

    text-decoration: none;

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


<input
    type="file"
    name="novel"
    accept=".txt,.epub"
    required
>


<button type="submit">

Upload Novel

</button>


</form>

</div>


{% if message %}

<div class="box">

<div class="status">

{{ message }}

</div>

</div>

{% endif %}


{% if novel %}

<div class="box">


<h2>

{{ novel.title }}

</h2>


<p>

Original words:
<strong>

{{ "{:,}".format(novel.total_words or 0) }}

</strong>

</p>


<p>

Translated words:
<strong>

{{ "{:,}".format(novel.translated_words or 0) }}

</strong>

</p>


<div class="progress">

<div
    class="bar"
    style="width: {{ progress }}%;"
></div>

</div>


<br>


<p>

Status:

<strong>

{{ novel.status }}

</strong>

</p>


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


# =========================================================
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)

def home():

    novel = None

    message = None


    if not supabase:

        message = (
            "Supabase is not configured."
        )

    else:

        try:

            novel = get_latest_novel()

        except Exception as e:

            message = (
                "Database error: "
                + str(e)
            )


    progress = 0


    if novel:

        total = novel.get(
            "total_words",
            0
        ) or 0

        translated = novel.get(
            "translated_words",
            0
        ) or 0


        if total > 0:

            progress = (
                translated / total
            ) * 100


    return render_template_string(
        HTML,
        novel=novel,
        message=message,
        progress=min(
            progress,
            100
        )
    )


# =========================================================
# UPLOAD
# =========================================================

@app.route(
    "/upload",
    methods=["POST"]
)

def upload():

    if not supabase:

        return render_template_string(
            HTML,
            novel=None,
            message=(
                "Supabase is not configured."
            ),
            progress=0
        )


    uploaded_file = request.files.get(
        "novel"
    )


    if not uploaded_file:

        return render_template_string(
            HTML,
            novel=None,
            message=(
                "Please choose a TXT or EPUB file."
            ),
            progress=0
        )


    filename = (
        uploaded_file.filename
        or "novel"
    )


    file_bytes = uploaded_file.read()


    try:

        if filename.lower().endswith(
            ".txt"
        ):

            text = extract_txt(
                file_bytes
            )


        elif filename.lower().endswith(
            ".epub"
        ):

            text = extract_epub(
                file_bytes
            )


        else:

            raise RuntimeError(
                "Only TXT and EPUB files are supported."
            )


        if not text.strip():

            raise RuntimeError(
                "The uploaded file is empty."
            )


        chapters = (
            split_text_into_chapters(
                text
            )
        )


        total_words = sum(
            count_words(
                chapter["text"]
            )
            for chapter in chapters
        )


        title = os.path.splitext(
            filename
        )[0]


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


        message = (
            f"Uploaded {len(chapters)} "
            f"chapters successfully."
        )


        return render_template_string(
            HTML,
            novel=novel,
            message=message,
            progress=0
        )


    except Exception as e:

        return render_template_string(
            HTML,
            novel=None,
            message=(
                "Upload error: "
                + str(e)
            ),
            progress=0
        )


# =========================================================
# TRANSLATE
# =========================================================

@app.route(
    "/translate/<novel_id>"
)

def translate(novel_id):

    if not supabase:

        return (
            "Supabase is not configured."
        )


    try:

        chapters = get_chapters(
            novel_id
        )


        if not chapters:

            return (
                "<h2>No chapters found.</h2>"
                "<a href='/'>Back</a>"
            )


        translated_words = 0


        # Count chapters that were already
        # translated before this request.

        for chapter in chapters:

            if (
                chapter["status"]
                == "translated"
            ):

                translated_words += (
                    chapter["translated_words"]
                    or 0
                )


        # Translate remaining chapters.

        for chapter in chapters:


            if (
                chapter["status"]
                == "translated"
            ):

                continue


            try:

                translated = translate_text(
                    chapter["original_text"]
                )


                words = count_words(
                    translated
                )


                (
                    supabase
                    .table("chapters")
                    .update(
                        {
                            "translated_text":
                                translated,

                            "translated_words":
                                words,

                            "status":
                                "translated"
                        }
                    )
                    .eq(
                        "id",
                        chapter["id"]
                    )
                    .execute()
                )


                translated_words += words


                (
                    supabase
                    .table("novels")
                    .update(
                        {
                            "translated_words":
                                translated_words,

                            "status":
                                "translating"
                        }
                    )
                    .eq(
                        "id",
                        novel_id
                    )
                    .execute()
                )


            except Exception as e:


                (
                    supabase
                    .table("novels")
                    .update(
                        {
                            "translated_words":
                                translated_words,

                            "status":
                                "paused"
                        }
                    )
                    .eq(
                        "id",
                        novel_id
                    )
                    .execute()
                )


                return f"""

                <html>

                <body style="font-family:Arial;padding:20px">

                <h2>⚠️ Translation stopped</h2>

                <p>

                Error:

                </p>

                <pre>{str(e)}</pre>

                <p>

                Already translated:

                <strong>

                {translated_words:,}

                </strong>

                words.

                </p>

                <p>

                Your completed chapters were saved.

                </p>

                <a href="/">

                Back to translator

                </a>

                </body>

                </html>

                """


        (
            supabase
            .table("novels")
            .update(
                {
                    "translated_words":
                        translated_words,

                    "status":
                        "completed"
                }
            )
            .eq(
                "id",
                novel_id
            )
            .execute()
        )


        return f"""

        <html>

        <body style="font-family:Arial;padding:20px">

        <h1>✅ Translation complete!</h1>

        <p>

        Translated approximately

        <strong>

        {translated_words:,}

        </strong>

        words.

        </p>

        <p>

        The translated chapters are safely
        stored in Supabase.

        </p>

        <p>

        Next we will add the cumulative
        30,000-word EPUB download system.

        </p>

        <a href="/">

        Back to translator

        </a>

        </body>

        </html>

        """


    except Exception as e:

        return f"""

        <h2>Translation error</h2>

        <pre>{str(e)}</pre>

        <a href="/">

        Back

        </a>

        """


# =========================================================
# START SERVER
# =========================================================

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
