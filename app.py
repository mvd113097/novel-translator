import os
import re
import io
import threading
import time
import gc

from flask import (
    Flask,
    request,
    render_template_string,
    redirect,
    send_file
)

from supabase import create_client
from google import genai

from ebooklib import epub
from bs4 import BeautifulSoup


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL"
)

SUPABASE_KEY = os.environ.get(
    "SUPABASE_PUBLISHABLE_KEY"
)

GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY"
)


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


# Only one translation worker at a time.
translation_lock = threading.Lock()


# =========================================================
# WORD COUNT
# =========================================================

def count_words(text):

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
# TXT
# =========================================================

def extract_txt(file_bytes):

    for encoding in [
        "utf-8",
        "utf-8-sig",
        "gb18030",
        "gbk"
    ]:

        try:

            return file_bytes.decode(
                encoding
            )

        except UnicodeDecodeError:

            pass


    return file_bytes.decode(
        "utf-8",
        errors="ignore"
    )


# =========================================================
# EPUB
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


    return "\n\n".join(
        sections
    )


# =========================================================
# CHAPTER SPLITTER
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


    # If no chapter headings exist.
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

            end = matches[
                i + 1
            ].start()

        else:

            end = len(text)


        block = text[
            start:end
        ].strip()


        lines = block.splitlines()


        if lines:

            title = lines[0].strip()

        else:

            title = (
                f"Chapter {i + 1}"
            )


        chapters.append(
            {
                "number": i + 1,
                "title": title,
                "text": block
            }
        )


    return chapters


# =========================================================
# GEMINI
# =========================================================

def translate_text(text):

    if not gemini:

        raise RuntimeError(
            "Gemini API key is not configured."
        )


    prompt = f"""
You are a professional Chinese-to-English web-novel translator.

Translate the following Chinese novel text into natural,
fluent English.

RULES:

- Translate everything.
- Do not summarize.
- Do not omit sentences.
- Preserve the meaning.
- Preserve paragraph breaks.
- Keep character names consistent.
- Keep gender and pronouns consistent.
- Keep dialogue natural.
- Do not add explanations.
- Do not add translator notes.
- Output ONLY the English translation.

TEXT:

{text}
"""


    interaction = gemini.interactions.create(

        model="gemini-3.6-flash",

        input=prompt

    )


    result = interaction.output_text


    if not result:

        raise RuntimeError(
            "Gemini returned an empty translation."
        )


    return result.strip()


# =========================================================
# DATABASE HELPERS
# =========================================================

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


# =========================================================
# BACKGROUND TRANSLATION
# =========================================================

def translation_worker(novel_id):

    if not translation_lock.acquire(
        blocking=False
    ):

        return


    try:

        chapters = get_chapters(
            novel_id
        )


        translated_words = 0


        # Count already completed chapters.
        for chapter in chapters:

            if (
                chapter["status"]
                == "translated"
            ):

                translated_words += (

                    chapter.get(
                        "translated_words",
                        0
                    )

                    or 0

                )


        # Mark as translating.
        (

            supabase

            .table("novels")

            .update(
                {
                    "status":
                        "translating",

                    "translated_words":
                        translated_words
                }
            )

            .eq(
                "id",
                novel_id
            )

            .execute()

        )


        # =================================================
        # CHAPTER BY CHAPTER
        # =================================================

        for chapter in chapters:

            if (
                chapter["status"]
                == "translated"
            ):

                continue


            original = (

                chapter.get(
                    "original_text",
                    ""
                )

                or ""

            )


            if not original.strip():

                continue


            try:

                translated = translate_text(
                    original
                )


                words = count_words(
                    translated
                )


                # Save immediately.
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


                # Update total progress.
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


                del translated
                del original

                gc.collect()


                time.sleep(1)


            except Exception as error:

                print(
                    "TRANSLATION ERROR:",
                    str(error)
                )


                (

                    supabase

                    .table("novels")

                    .update(
                        {
                            "status":
                                "paused",

                            "translated_words":
                                translated_words
                        }
                    )

                    .eq(
                        "id",
                        novel_id
                    )

                    .execute()

                )


                return


        # =================================================
        # COMPLETE
        # =================================================

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


    except Exception as error:

        print(
            "WORKER ERROR:",
            str(error)
        )


        try:

            (

                supabase

                .table("novels")

                .update(
                    {
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

        except Exception:

            pass


    finally:

        gc.collect()

        translation_lock.release()


# =========================================================
# DOWNLOAD HELPERS
# =========================================================

DOWNLOAD_LIMIT = 30000


def download_allowed(novel):

    translated_words = (

        novel.get(
            "translated_words",
            0
        )

        or 0

    )


    return translated_words >= DOWNLOAD_LIMIT


# =========================================================
# TXT DOWNLOAD
# =========================================================

@app.route(
    "/download/txt/<novel_id>"
)

def download_txt(novel_id):

    novel_result = (

        supabase

        .table("novels")

        .select("*")

        .eq(
            "id",
            novel_id
        )

        .single()

        .execute()

    )


    novel = novel_result.data


    if not download_allowed(
        novel
    ):

        return (
            "Download requires at least "
            "30,000 translated words."
        ), 403


    chapters = get_chapters(
        novel_id
    )


    output = []


    for chapter in chapters:

        translated = (

            chapter.get(
                "translated_text",
                ""
            )

            or ""

        )


        if translated.strip():

            output.append(
                chapter.get(
                    "title",
                    f"Chapter {chapter['chapter_number']}"
                )
            )

            output.append(
                translated
            )

            output.append(
                "\n"
            )


    content = "\n".join(
        output
    )


    data = io.BytesIO(
        content.encode(
            "utf-8"
        )
    )


    filename = (
        novel["title"]
        + "_translated.txt"
    )


    return send_file(

        data,

        mimetype="text/plain",

        as_attachment=True,

        download_name=filename

    )


# =========================================================
# EPUB DOWNLOAD
# =========================================================

@app.route(
    "/download/epub/<novel_id>"
)

def download_epub(novel_id):

    novel_result = (

        supabase

        .table("novels")

        .select("*")

        .eq(
            "id",
            novel_id
        )

        .single()

        .execute()

    )


    novel = novel_result.data


    if not download_allowed(
        novel
    ):

        return (
            "Download requires at least "
            "30,000 translated words."
        ), 403


    chapters = get_chapters(
        novel_id
    )


    book = epub.EpubBook()


    book.set_identifier(
        str(novel_id)
    )


    book.set_title(
        novel["title"]
    )


    book.set_language(
        "en"
    )


    spine = [
        "nav"
    ]


    epub_chapters = []


    for chapter in chapters:

        translated = (

            chapter.get(
                "translated_text",
                ""
            )

            or ""

        )


        if not translated.strip():

            continue


        chapter_number = (
            chapter[
                "chapter_number"
            ]
        )


        title = (

            chapter.get(
                "title",
                f"Chapter {chapter_number}"
            )

        )


        safe_title = re.sub(
            r"[^a-zA-Z0-9_-]",
            "_",
            title
        )


        c = epub.EpubHtml(

            title=title,

            file_name=(
                f"chapter_{chapter_number}.xhtml"
            ),

            lang="en"

        )


        paragraphs = translated.split(
            "\n"
        )


        html = ""


        for paragraph in paragraphs:

            paragraph = paragraph.strip()


            if paragraph:

                html += (
                    "<p>"
                    + (
                        paragraph
                        .replace(
                            "&",
                            "&amp;"
                        )
                        .replace(
                            "<",
                            "&lt;"
                        )
                        .replace(
                            ">",
                            "&gt;"
                        )
                    )
                    + "</p>"
                )


        c.content = f"""
        <html>
        <head>
        <title>{title}</title>
        </head>

        <body>

        <h1>{title}</h1>

        {html}

        </body>
        </html>
        """


        book.add_item(c)

        epub_chapters.append(c)

        spine.append(c)


    book.toc = tuple(
        epub_chapters
    )


    book.add_item(
        epub.EpubNcx()
    )


    book.add_item(
        epub.EpubNav()
    )


    book.spine = spine


    output = io.BytesIO()


    epub.write_epub(
        output,
        book
    )


    output.seek(0)


    filename = (
        novel["title"]
        + "_translated.epub"
    )


    return send_file(

        output,

        mimetype="application/epub+zip",

        as_attachment=True,

        download_name=filename

    )


# =========================================================
# HTML
# =========================================================

HTML = """

<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<meta http-equiv="refresh"
      content="10">

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

    height: 24px;

}


.bar {

    background: #333;

    height: 24px;

}


.status {

    padding: 12px;

    background: #eee;

    border-radius: 8px;

}


.warning {

    background: #fff3cd;

    padding: 12px;

    border-radius: 8px;

}


.download {

    display: block;

    margin-top: 12px;

}


.download button {

    width: 100%;

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

{{ "{:,}".format(
    novel.total_words or 0
) }}

</strong>

</p>


<p>

Translated words:

<strong>

{{ "{:,}".format(
    novel.translated_words or 0
) }}

</strong>

</p>


{% if novel.total_words %}

<div class="progress">

<div
    class="bar"
    style="width: {{ progress }}%;"
></div>

</div>

{% endif %}


<p>

Status:

<strong>

{{ novel.status }}

</strong>

</p>


{% if novel.status == "waiting"
   or novel.status == "paused" %}


<a href="/translate/{{ novel.id }}">

<button>

{% if novel.status == "paused" %}

Resume Translation

{% else %}

Start Translation

{% endif %}

</button>

</a>


{% elif novel.status == "translating" %}


<div class="warning">

⏳ Translation is running.

<br><br>

This page checks progress
automatically.

</div>


{% elif novel.status == "completed" %}


<div class="status">

✅ Translation complete!

</div>


{% endif %}


{% if novel.translated_words >= 30000 %}

<hr>


<h3>📥 Downloads</h3>


<a
    class="download"
    href="/download/txt/{{ novel.id }}"
>

<button>

📄 Download TXT

</button>

</a>


<a
    class="download"
    href="/download/epub/{{ novel.id }}"
>

<button>

📖 Download EPUB

</button>

</a>


{% else %}


<div class="warning">

🔒 Downloads unlock at
<strong>30,000 translated words</strong>.

<br><br>

Current translated words:

<strong>

{{ "{:,}".format(
    novel.translated_words or 0
) }}

</strong>

</div>


{% endif %}


</div>

{% endif %}


</body>

</html>

"""


# =========================================================
# HOME
# =========================================================

@app.route("/")
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

        except Exception as error:

            message = (
                "Database error: "
                + str(error)
            )


    progress = 0


    if novel:

        total = (
            novel.get(
                "total_words",
                0
            )

            or 0
        )


        translated = (
            novel.get(
                "translated_words",
                0
            )

            or 0
        )


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


    try:

        file_bytes = uploaded_file.read()


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


        # Create novel.
        novel_result = (

            supabase

            .table("novels")

            .insert(
                {
                    "title":
                        title,

                    "original_filename":
                        filename,

                    "total_words":
                        total_words,

                    "translated_words":
                        0,

                    "status":
                        "waiting"
                }
            )

            .execute()

        )


        novel = novel_result.data[0]


        # Save chapters.
        for chapter in chapters:

            (

                supabase

                .table("chapters")

                .insert(
                    {
                        "novel_id":
                            novel["id"],

                        "chapter_number":
                            chapter["number"],

                        "title":
                            chapter["title"],

                        "original_text":
                            chapter["text"],

                        "translated_text":
                            None,

                        "original_words":
                            count_words(
                                chapter["text"]
                            ),

                        "translated_words":
                            0,

                        "status":
                            "waiting"
                    }
                )

                .execute()

            )


        del file_bytes
        del text

        gc.collect()


        return redirect("/")


    except Exception as error:

        return render_template_string(

            HTML,

            novel=None,

            message=(
                "Upload error: "
                + str(error)
            ),

            progress=0

        )


# =========================================================
# START TRANSLATION
# =========================================================

@app.route(
    "/translate/<novel_id>"
)

def start_translation(novel_id):

    if not supabase:

        return (
            "Supabase is not configured."
        )


    try:

        chapters = get_chapters(
            novel_id
        )


        remaining = [

            chapter

            for chapter in chapters

            if chapter["status"]
            != "translated"

        ]


        if not remaining:

            (

                supabase

                .table("novels")

                .update(
                    {
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

            return redirect("/")


        thread = threading.Thread(

            target=translation_worker,

            args=(novel_id,),

            daemon=True

        )


        thread.start()


        return redirect("/")


    except Exception as error:

        return (

            "Unable to start translation: "

            + str(error)

        )


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return "OK"


# =========================================================
# RUN
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
