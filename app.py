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

SUPABASE_URL = os.environ.get("SUPABASE_URL")

# Normal database key
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")

# Optional service role key.
# Put this ONLY in Render Environment Variables.
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY"
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


supabase = None
supabase_admin = None
gemini = None


# =========================================================
# SUPABASE
# =========================================================

if SUPABASE_URL and SUPABASE_KEY:

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )


# If service role key exists, use it for database operations
# that need to bypass RLS, such as deleting a novel.
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:

    supabase_admin = create_client(
        SUPABASE_URL,
        SUPABASE_SERVICE_ROLE_KEY
    )


# =========================================================
# GEMINI
# =========================================================

if GEMINI_API_KEY:

    gemini = genai.Client(
        api_key=GEMINI_API_KEY
    )


# Only one translation worker at a time.
translation_lock = threading.Lock()


# =========================================================
# DOWNLOAD LIMIT
# =========================================================

# IMPORTANT:
# Downloads unlock after 30,000 translated words.

DOWNLOAD_LIMIT = 30000


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
# AUTOMATIC TITLE DETECTION
# =========================================================

def detect_chinese_title(text, filename):

    """
    Automatically detects a Chinese novel title
    if one appears before Chapter 1.

    Example:

    穿到史前就爱种田

    第1章 初到异世界

    ...

    It detects:
        穿到史前就爱种田

    If there is no Chinese title before Chapter 1,
    it returns an empty string.
    """

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:

        return ""


    # Look only at the beginning of the novel.
    # Usually the title is within the first few lines.
    for line in lines[:20]:

        # Ignore obvious chapter headings.
        if re.match(
            r"(?i)^(第\s*\d+\s*[章节卷回]|chapter\s+\d+|chap\.\s*\d+)",
            line
        ):

            break


        # Remove common title labels.
        cleaned = re.sub(
            r"^(书名|小说名|作品名|标题)\s*[:：]\s*",
            "",
            line
        ).strip()


        # Must contain Chinese characters.
        chinese_chars = re.findall(
            r"[\u4e00-\u9fff]",
            cleaned
        )


        if not chinese_chars:

            continue


        # A title should normally contain more than
        # just one Chinese character.
        if len(chinese_chars) >= 2:

            # Avoid treating a sentence as the title.
            # Titles generally don't end in Chinese punctuation.
            if cleaned[-1:] in "。！？；，,!?;":

                continue

            return cleaned


    return ""


# =========================================================
# REMOVE TITLE FROM NOVEL BODY
# =========================================================

def remove_detected_title(text, chinese_title):

    if not chinese_title:

        return text


    lines = text.splitlines()

    result = []

    removed = False

    for line in lines:

        stripped = line.strip()

        if not removed and stripped == chinese_title:

            removed = True

            continue

        result.append(line)


    return "\n".join(result).strip()


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
# GEMINI TRANSLATION
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


                # Save translation immediately.
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


                # Update novel progress.
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
# DOWNLOAD CHECK
# =========================================================

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
# DOWNLOAD TITLE
# =========================================================

def get_display_title(novel):

    chinese_title = (
        novel.get(
            "chinese_title",
            ""
        )
        or ""
    ).strip()


    english_title = (
        novel.get(
            "title",
            ""
        )
        or ""
    ).strip()


    if chinese_title:

        if english_title:

            return (
                chinese_title
                + "\n"
                + english_title
            )

        return chinese_title


    return english_title


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


    # Put title at beginning.
    chinese_title = (
        novel.get(
            "chinese_title",
            ""
        )
        or ""
    ).strip()


    english_title = (
        novel.get(
            "title",
            ""
        )
        or ""
    ).strip()


    if chinese_title:

        output.append(
            chinese_title
        )

        if english_title:

            output.append(
                english_title
            )

        output.append("")
        output.append("")


    elif english_title:

        output.append(
            "English Title: "
            + english_title
        )

        output.append("")
        output.append("")


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


    # =====================================================
    # TITLE PAGE
    # =====================================================

    chinese_title = (
        novel.get(
            "chinese_title",
            ""
        )
        or ""
    ).strip()


    english_title = (
        novel.get(
            "title",
            ""
        )
        or ""
    ).strip()


    title_page = epub.EpubHtml(

        title="Title",

        file_name="title.xhtml",

        lang="en"

    )


    title_html = ""


    if chinese_title:

        title_html += (
            "<h1>"
            + (
                chinese_title
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            + "</h1>"
        )


        if english_title:

            title_html += (
                "<h2>"
                + (
                    english_title
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                + "</h2>"
            )

    else:

        title_html += (
            "<h1>"
            + (
                english_title
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            + "</h1>"
        )


    title_page.content = f"""
    <html>

    <head>
    <title>Title</title>
    </head>

    <body>

    {title_html}

    </body>

    </html>
    """


    book.add_item(
        title_page
    )


    spine.append(
        title_page
    )


    # =====================================================
    # CHAPTERS
    # =====================================================

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

                safe_paragraph = (

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


                html += (
                    "<p>"
                    + safe_paragraph
                    + "</p>"
                )


        safe_title = (

            title

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


        c.content = f"""
        <html>

        <head>

        <title>{safe_title}</title>

        </head>

        <body>

        <h1>{safe_title}</h1>

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
# DELETE NOVEL
# =========================================================

@app.route(
    "/delete/<novel_id>",
    methods=["POST"]
)

def delete_novel(novel_id):

    try:

        # Prefer service-role client if configured.
        db = supabase_admin or supabase


        if not db:

            return (
                "Supabase is not configured."
            ), 500


        # Delete chapters first.
        #
        # This prevents problems if chapters.novel_id
        # has a foreign-key relationship to novels.id.

        db.table("chapters").delete().eq(
            "novel_id",
            novel_id
        ).execute()


        # Then delete the novel.
        db.table("novels").delete().eq(
            "id",
            novel_id
        ).execute()


        return redirect("/")


    except Exception as error:

        print(
            "DELETE ERROR:",
            str(error)
        )


        return (
            "Unable to delete novel: "
            + str(error)
        ), 500


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


label {

    display: block;

    margin-top: 12px;

    font-weight: bold;

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

    text-decoration: none;

}


.download button {

    width: 100%;

}


.delete-box {

    margin-top: 25px;

    padding-top: 20px;

    border-top: 1px solid #ddd;

}


.delete-button {

    background: #c62828;

    width: 100%;

}


.small {

    font-size: 13px;

    color: #666;

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


<label>Novel File</label>


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


<p class="small">

The Chinese title is detected automatically when
one appears at the beginning of the novel.

</p>


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


{% if novel.chinese_title %}

<p>

Chinese title:

<strong>

{{ novel.chinese_title }}

</strong>

</p>

{% endif %}


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

This page checks progress automatically.

<br><br>

You can close the browser.

The translation runs on the server while the
Render service remains running.

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


<!-- DELETE -->

<div class="delete-box">


<h3>🗑️ Delete Novel</h3>


<p class="small">

After you have downloaded the TXT or EPUB,
you can permanently remove this novel and its
translations from the website.

Your downloaded file on your phone will NOT be deleted.

</p>


<form
    method="POST"
    action="/delete/{{ novel.id }}"
    onsubmit="return confirm(
        'Are you sure you want to permanently delete this novel and all of its translations? This cannot be undone.'
    );"
>


<button
    type="submit"
    class="delete-button"
>

🗑️ Delete Novel

</button>


</form>


</div>


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


        # =================================================
        # EXTRACT TEXT
        # =================================================

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


        # =================================================
        # DETECT CHINESE TITLE
        # =================================================

        chinese_title = detect_chinese_title(
            text,
            filename
        )


        # Remove the Chinese title from the
        # chapter text if it was detected.
        if chinese_title:

            text = remove_detected_title(
                text,
                chinese_title
            )


        # =================================================
        # CHAPTERS
        # =================================================

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


        # =================================================
        # ENGLISH TITLE
        # =================================================

        english_title = os.path.splitext(
            filename
        )[0]


        # =================================================
        # CREATE NOVEL
        # =================================================

        novel_data = {

            "title":
                english_title,

            "original_filename":
                filename,

            "total_words":
                total_words,

            "translated_words":
                0,

            "status":
                "waiting"
        }


        # Add Chinese title only if it was detected.
        #
        # This requires the novels table to have
        # a chinese_title column.

        if chinese_title:

            novel_data[
                "chinese_title"
            ] = chinese_title


        novel_result = (

            supabase

            .table("novels")

            .insert(
                novel_data
            )

            .execute()

        )


        novel = novel_result.data[0]


        # =================================================
        # SAVE CHAPTERS
        # =================================================

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
