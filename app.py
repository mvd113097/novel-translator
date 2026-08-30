import os
import re
import io
import threading
import time
import gc
import secrets
import requests
from flask import Flask, request, render_template_string, redirect, send_file, session
from supabase import create_client
from google import genai
from ebooklib import epub
from bs4 import BeautifulSoup


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    secrets.token_hex(32)
)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SITE_PASSWORD = os.environ.get("SITE_PASSWORD")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


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
# TRANSLATION CONTROL
# =========================================================

translation_lock = threading.Lock()

current_translation_novel = None

last_translation_activity = 0

# IMPORTANT:
# Translation is automatically considered stopped after
# 2 minutes without activity.
STOP_TIMEOUT = 2 * 60


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram bot token not configured.")
        return False

    if not TELEGRAM_CHAT_ID:
        print("Telegram chat ID not configured.")
        return False

    try:

        url = (
            "https://api.telegram.org/bot"
            + TELEGRAM_BOT_TOKEN
            + "/sendMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=15
        )

        if response.ok:
            return True

        print(
            "Telegram error:",
            response.text
        )

        return False

    except Exception as error:

        print(
            "Telegram notification error:",
            str(error)
        )

        return False


# =========================================================
# LOGIN
# =========================================================

def is_logged_in():

    return session.get(
        "logged_in",
        False
    )


def login_required():

    if not is_logged_in():

        return render_template_string(
            LOGIN_HTML,
            error=None
        )

    return None


LOGIN_HTML = """
<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Novel Translator Login</title>

<style>

body {
    font-family: Arial, sans-serif;
    max-width: 500px;
    margin: auto;
    padding: 20px;
    background: #f5f5f5;
}

.box {
    background: white;
    padding: 25px;
    border-radius: 12px;
    margin-top: 60px;
}

input {
    width: 100%;
    padding: 14px;
    margin: 12px 0;
    box-sizing: border-box;
    border: 1px solid #ccc;
    border-radius: 8px;
}

button {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 8px;
    background: #333;
    color: white;
    font-size: 16px;
}

.error {
    background: #f8d7da;
    padding: 12px;
    border-radius: 8px;
    margin-bottom: 10px;
}

</style>

</head>

<body>

<div class="box">

<h1>🔐 Novel Translator</h1>

<p>Enter your password to continue.</p>

{% if error %}

<div class="error">
{{ error }}
</div>

{% endif %}

<form method="POST" action="/login">

<input
    type="password"
    name="password"
    placeholder="Password"
    required
>

<button type="submit">
Login
</button>

</form>

</div>

</body>

</html>
"""


# =========================================================
# LOGIN ROUTE
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if (
            SITE_PASSWORD
            and password == SITE_PASSWORD
        ):

            session["logged_in"] = True

            return redirect("/")

        return render_template_string(
            LOGIN_HTML,
            error="Incorrect password."
        )

    return render_template_string(
        LOGIN_HTML,
        error=None
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


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
# TITLE DETECTION
# =========================================================

def contains_chinese(text):

    return bool(
        re.search(
            r"[\u4e00-\u9fff]",
            text or ""
        )
    )


def is_chapter_heading(text):

    if not text:
        return False

    patterns = [
        r"^第\s*\d+\s*[章节卷回]",
        r"^chapter\s+\d+",
        r"^chap\.\s*\d+"
    ]

    for pattern in patterns:

        if re.search(
            pattern,
            text.strip(),
            re.IGNORECASE
        ):
            return True

    return False


def detect_titles(text, filename):

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    chinese_title = ""
    english_title = ""

    filename_title = os.path.splitext(
        filename
    )[0].strip()


    for line in lines[:15]:

        if is_chapter_heading(line):
            continue

        if len(line) > 100:
            continue

        if contains_chinese(line):

            chinese_title = line

            break

        english_title = line

        break


    if not chinese_title and not english_title:

        if contains_chinese(filename_title):

            chinese_title = filename_title

        else:

            english_title = filename_title


    if (
        not chinese_title
        and contains_chinese(filename_title)
    ):

        chinese_title = filename_title


    if (
        chinese_title
        and not english_title
        and not contains_chinese(filename_title)
    ):

        english_title = filename_title


    return (
        chinese_title,
        english_title
    )


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


    return "\n\n".join(sections)


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

    global last_translation_activity


    if not gemini:

        raise RuntimeError(
            "Gemini API key is not configured."
        )


    last_translation_activity = time.time()


    prompt = f"""
You are a professional Chinese-to-English web-novel translator.

Translate the following Chinese novel text into natural,
fluent English.

RULES:

- Translate everything.
- Do not summarize.
- Do not omit sentences.
- Preserve the original meaning.
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


    last_translation_activity = time.time()


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
# TRANSLATION WORKER
# =========================================================

def translation_worker(novel_id):

    global current_translation_novel
    global last_translation_activity


    if not translation_lock.acquire(
        blocking=False
    ):

        return


    current_translation_novel = novel_id

    last_translation_activity = time.time()


    try:

        chapters = get_chapters(
            novel_id
        )


        translated_words = 0


        # Count chapters already completed.
        for chapter in chapters:

            if chapter.get("status") == "translated":

                translated_words += (
                    chapter.get(
                        "translated_words",
                        0
                    )
                    or 0
                )


        (
            supabase
            .table("novels")
            .update(
                {
                    "status": "translating",
                    "translated_words": translated_words
                }
            )
            .eq(
                "id",
                novel_id
            )
            .execute()
        )


        # =================================================
        # TRANSLATE ONE CHAPTER AT A TIME
        # =================================================

        for chapter in chapters:

            # IMPORTANT:
            # Already completed chapters are skipped.
            if chapter.get("status") == "translated":
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


            last_translation_activity = time.time()


            try:

                translated = translate_text(
                    original
                )


                last_translation_activity = time.time()


                words = count_words(
                    translated
                )


                # SAVE THE COMPLETED CHAPTER IMMEDIATELY.
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
                    .eq(
                        "id",
                        chapter["id"]
                    )
                    .execute()
                )


                translated_words += words


                # SAVE TOTAL PROGRESS IMMEDIATELY.
                (
                    supabase
                    .table("novels")
                    .update(
                        {
                            "translated_words": translated_words,
                            "status": "translating"
                        }
                    )
                    .eq(
                        "id",
                        novel_id
                    )
                    .execute()
                )


                print(
                    "Completed chapter",
                    chapter["chapter_number"],
                    "-",
                    words,
                    "translated words."
                )


                del translated
                del original

                gc.collect()


                last_translation_activity = time.time()


                # Small pause so the API is not hammered.
                time.sleep(1)


            except Exception as error:

                print(
                    "TRANSLATION ERROR:",
                    str(error)
                )


                try:

                    (
                        supabase
                        .table("novels")
                        .update(
                            {
                                "status": "paused",
                                "translated_words": translated_words
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


                send_telegram(
                    "🔴 TRANSLATION STOPPED\n\n"
                    "Gemini returned an error while translating.\n\n"
                    "Translated words: "
                    + f"{translated_words:,}"
                    + "\n\n"
                    "The completed chapters were saved.\n"
                    "Press \"Resume Translation\" to continue."
                )


                return


        # =================================================
        # ALL CHAPTERS FINISHED
        # =================================================

        (
            supabase
            .table("novels")
            .update(
                {
                    "translated_words": translated_words,
                    "status": "completed"
                }
            )
            .eq(
                "id",
                novel_id
            )
            .execute()
        )


        send_telegram(
            "✅ TRANSLATION COMPLETE!\n\n"
            "The novel has finished translating.\n\n"
            "Translated words: "
            + f"{translated_words:,}"
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
                        "status": "paused"
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


        send_telegram(
            "🔴 TRANSLATION STOPPED\n\n"
            "The translation worker stopped unexpectedly.\n\n"
            "Completed chapters were saved.\n\n"
            "Press \"Resume Translation\" to continue."
        )


    finally:

        current_translation_novel = None

        last_translation_activity = 0

        gc.collect()

        translation_lock.release()


# =========================================================
# HEARTBEAT MONITOR
# =========================================================

def heartbeat_monitor():

    global current_translation_novel
    global last_translation_activity


    while True:

        try:

            if (
                current_translation_novel
                and last_translation_activity
            ):

                elapsed = (
                    time.time()
                    - last_translation_activity
                )


                # =================================================
                # 2-MINUTE STOP DETECTION
                # =================================================

                if elapsed > STOP_TIMEOUT:

                    novel_id = (
                        current_translation_novel
                    )


                    try:

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


                        if (
                            novel
                            and novel.get("status")
                            == "translating"
                        ):

                            (
                                supabase
                                .table("novels")
                                .update(
                                    {
                                        "status": "paused"
                                    }
                                )
                                .eq(
                                    "id",
                                    novel_id
                                )
                                .execute()
                            )


                            send_telegram(
                                "🔴 TRANSLATION PAUSED\n\n"
                                "No translation activity has been detected "
                                "for more than 2 minutes.\n\n"
                                "The translator may have stopped or crashed.\n\n"
                                "Completed chapters were saved.\n"
                                "Press \"Resume Translation\" to continue."
                            )


                    except Exception as error:

                        print(
                            "Heartbeat error:",
                            str(error)
                        )


                    current_translation_novel = None

                    last_translation_activity = 0


        except Exception as error:

            print(
                "Heartbeat monitor error:",
                str(error)
            )


        # Check every 20 seconds.
        time.sleep(20)


# =========================================================
# START HEARTBEAT THREAD
# =========================================================

heartbeat_thread = threading.Thread(
    target=heartbeat_monitor,
    daemon=True
)

heartbeat_thread.start()


# =========================================================
# DOWNLOAD
# =========================================================

# IMPORTANT:
# Downloads are unlocked as soon as there is at least
# ONE completely translated chapter.
#
# The novel does NOT need to reach 30,000 words anymore.

def download_allowed(novel_id):

    try:

        chapters = get_chapters(
            novel_id
        )

        for chapter in chapters:

            translated = (
                chapter.get(
                    "translated_text",
                    ""
                )
                or ""
            )

            if (
                chapter.get("status")
                == "translated"
                and translated.strip()
            ):

                return True

    except Exception as error:

        print(
            "Download permission error:",
            str(error)
        )


    return False


# =========================================================
# CHINESE TITLE
# =========================================================

def get_chinese_title(novel):

    filename = novel.get(
        "original_filename",
        ""
    )


    filename_title = os.path.splitext(
        filename
    )[0]


    if contains_chinese(
        filename_title
    ):

        return filename_title


    try:

        chapters = get_chapters(
            novel["id"]
        )


        if chapters:

            first_text = (
                chapters[0].get(
                    "original_text",
                    ""
                )
                or ""
            )


            lines = [
                line.strip()
                for line in first_text.splitlines()
                if line.strip()
            ]


            for line in lines[:5]:

                if (
                    contains_chinese(line)
                    and not is_chapter_heading(line)
                ):

                    return line


    except Exception:

        pass


    return ""


# =========================================================
# DOWNLOAD TXT
# =========================================================

@app.route("/download/txt/<novel_id>")
def download_txt(novel_id):

    protection = login_required()

    if protection:
        return protection


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


    if not novel:

        return "Novel not found.", 404


    if not download_allowed(
        novel_id
    ):

        return (
            "There are no completely translated chapters "
            "available for download yet."
        ), 403


    chapters = get_chapters(
        novel_id
    )


    output = []


    chinese_title = get_chinese_title(
        novel
    )


    if chinese_title:

        output.append(
            chinese_title
        )

        output.append("")


    output.append(
        "English Title: "
        + novel["title"]
    )

    output.append("")


    # =================================================
    # ONLY COMPLETED CHAPTERS ARE INCLUDED
    # =================================================

    for chapter in chapters:

        translated = (
            chapter.get(
                "translated_text",
                ""
            )
            or ""
        )


        if (
            chapter.get("status")
            != "translated"
        ):

            continue


        if not translated.strip():
            continue


        output.append(
            chapter.get(
                "title",
                f"Chapter {chapter['chapter_number']}"
            )
        )

        output.append(
            translated
        )

        output.append("")


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
# DOWNLOAD EPUB
# =========================================================

@app.route("/download/epub/<novel_id>")
def download_epub(novel_id):

    protection = login_required()

    if protection:
        return protection


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


    if not novel:

        return "Novel not found.", 404


    if not download_allowed(
        novel_id
    ):

        return (
            "There are no completely translated chapters "
            "available for download yet."
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


    # =================================================
    # TITLE PAGE
    # =================================================

    title_page = epub.EpubHtml(
        title="Title",
        file_name="title.xhtml",
        lang="en"
    )


    chinese_title = get_chinese_title(
        novel
    )


    title_html = ""


    if chinese_title:

        safe_chinese = (

            chinese_title
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")

        )


        title_html += (
            "<h1>"
            + safe_chinese
            + "</h1>"
        )


    safe_english = (

        novel["title"]
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")

    )


    title_html += (
        "<h2>English Title: "
        + safe_english
        + "</h2>"
    )


    title_page.content = f"""
    <html>

    <head>

    <title>{safe_english}</title>

    </head>

    <body>

    {title_html}

    </body>

    </html>
    """


    book.add_item(
        title_page
    )


    epub_chapters.append(
        title_page
    )


    spine.append(
        title_page
    )


    # =================================================
    # COMPLETED CHAPTERS ONLY
    # =================================================

    for chapter in chapters:

        translated = (
            chapter.get(
                "translated_text",
                ""
            )
            or ""
        )


        # IMPORTANT:
        # Unfinished chapters are skipped.
        if (
            chapter.get("status")
            != "translated"
        ):
            continue


        if not translated.strip():
            continue


        chapter_number = chapter[
            "chapter_number"
        ]


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


            if not paragraph:
                continue


            safe_paragraph = (

                paragraph
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")

            )


            html += (
                "<p>"
                + safe_paragraph
                + "</p>"
            )


        safe_title = (

            title
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")

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

    protection = login_required()

    if protection:
        return protection


    try:

        global current_translation_novel


        # Forget active worker.
        if current_translation_novel == novel_id:

            current_translation_novel = None


        # Delete chapters.
        (
            supabase
            .table("chapters")
            .delete()
            .eq(
                "novel_id",
                novel_id
            )
            .execute()
        )


        # Delete translation batches if table exists.
        try:

            (
                supabase
                .table("translation_batches")
                .delete()
                .eq(
                    "novel_id",
                    novel_id
                )
                .execute()
            )

        except Exception:

            pass


        # Delete novel.
        (
            supabase
            .table("novels")
            .delete()
            .eq(
                "id",
                novel_id
            )
            .execute()
        )


        # IMPORTANT:
        # NO TELEGRAM MESSAGE IS SENT HERE.
        #
        # This means manually deleting a novel
        # will NOT send a "Novel Deleted" Telegram notification.


        return redirect("/")


    except Exception as error:

        return (
            "Delete error: "
            + str(error)
        )


# =========================================================
# MAIN HTML
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

.success {
    background: #d4edda;
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

.delete {
    margin-top: 25px;
    padding-top: 20px;
    border-top: 1px solid #ddd;
}

.delete button {
    background: #b00020;
    width: 100%;
}

.logout {
    text-align: right;
    margin-bottom: 15px;
}

.logout a {
    color: #555;
}

.info {
    background: #e7f1ff;
    padding: 12px;
    border-radius: 8px;
}

</style>

</head>

<body>

<div class="logout">

<a href="/logout">🔒 Logout</a>

</div>

<h1>📚 Novel Translator</h1>

<div class="box">

<h2>Upload Novel</h2>

<p>

The title is detected automatically from the file.

If a Chinese title is found, it will appear before
the English title in the downloaded TXT and EPUB.

</p>

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


{% if chinese_title %}

<p>

Chinese Title:

<strong>

{{ chinese_title }}

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

▶️ Resume Translation

{% else %}

▶️ Start Translation

{% endif %}

</button>

</a>


{% elif novel.status == "translating" %}

<div class="warning">

⏳ Translation is running.

<br><br>

Completed chapters are saved immediately.

<br><br>

You can download completed chapters while
the rest of the novel continues translating.

<br><br>

If no translation activity is detected for
2 minutes, the translator will automatically
be marked as paused and Telegram will notify you.

</div>


{% elif novel.status == "completed" %}

<div class="success">

✅ Translation complete!

</div>

{% endif %}


<hr>


<h3>📥 Downloads</h3>


<div class="info">

Downloads contain only chapters that have been
completely translated.

<br><br>

Unfinished chapters are automatically skipped.

<br><br>

You can download the completed chapters
<strong>even while the rest of the novel is still translating.</strong>

<br><br>

Downloading does not delete or interrupt
the saved translations.

</div>


{% if has_completed_chapters %}


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

🔒 No completed chapters are available yet.

<br><br>

Once the first chapter finishes translating,
the TXT and EPUB download buttons will appear.

</div>

{% endif %}


<div class="delete">

<form
    method="POST"
    action="/delete/{{ novel.id }}"
    onsubmit="return confirm('Delete this novel and all its translations? This cannot be undone.')"
>

<button type="submit">

🗑️ Delete Novel

</button>

</form>

</div>

</div>

{% endif %}


<script>

/*
Instead of a full page reload every 10 seconds,
this page refreshes every 10 seconds only while
translation is running.

This does NOT affect the background translation worker.
*/

</script>

</body>

</html>
"""


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    protection = login_required()

    if protection:
        return protection


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

    chinese_title = ""

    has_completed_chapters = False


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


        chinese_title = get_chinese_title(
            novel
        )


        # Check whether at least one chapter
        # is completely translated.

        try:

            chapters = get_chapters(
                novel["id"]
            )


            for chapter in chapters:

                if (
                    chapter.get("status")
                    == "translated"
                    and (
                        chapter.get(
                            "translated_text",
                            ""
                        )
                        or ""
                    ).strip()
                ):

                    has_completed_chapters = True

                    break

        except Exception:

            has_completed_chapters = False


    # =====================================================
    # AUTOMATIC 10-SECOND PAGE REFRESH
    # =====================================================

    # We add this only when translation is running.
    # The refresh DOES NOT restart translation.
    # It only updates the displayed progress.

    if novel and novel.get("status") == "translating":

        refresh_script = """
        <script>
        setTimeout(function() {
            window.location.reload();
        }, 10000);
        </script>
        """

    else:

        refresh_script = ""


    page = render_template_string(

        HTML,

        novel=novel,

        message=message,

        chinese_title=chinese_title,

        has_completed_chapters=has_completed_chapters,

        progress=min(
            progress,
            100
        )

    )


    # Insert refresh script before </body>.
    page = page.replace(
        "</body>",
        refresh_script + "</body>"
    )


    return page


# =========================================================
# UPLOAD
# =========================================================

@app.route(
    "/upload",
    methods=["POST"]
)
def upload():

    protection = login_required()

    if protection:
        return protection


    if not supabase:

        return render_template_string(
            HTML,
            novel=None,
            message="Supabase is not configured.",
            progress=0,
            chinese_title="",
            has_completed_chapters=False
        )


    uploaded_file = request.files.get(
        "novel"
    )


    if not uploaded_file:

        return render_template_string(
            HTML,
            novel=None,
            message="Please choose a TXT or EPUB file.",
            progress=0,
            chinese_title="",
            has_completed_chapters=False
        )


    filename = (
        uploaded_file.filename
        or "novel"
    )


    try:

        file_bytes = uploaded_file.read()


        if filename.lower().endswith(".txt"):

            text = extract_txt(
                file_bytes
            )

        elif filename.lower().endswith(".epub"):

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
        # TITLE
        # =================================================

        chinese_title, english_title = detect_titles(
            text,
            filename
        )


        if english_title:

            title = english_title

        elif chinese_title:

            title = chinese_title

        else:

            title = os.path.splitext(
                filename
            )[0]


        # =================================================
        # CHAPTERS
        # =================================================

        chapters = split_text_into_chapters(
            text
        )


        total_words = sum(

            count_words(
                chapter["text"]
            )

            for chapter in chapters

        )


        # =================================================
        # CREATE NOVEL
        # =================================================

        novel_result = (

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
                        "novel_id": novel["id"],
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


        del file_bytes

        del text

        gc.collect()


        # =================================================
        # UPLOAD TELEGRAM
        # =================================================

        send_telegram(
            "📚 NOVEL UPLOADED\n\n"
            + title
            + "\n\n"
            + "Original words: "
            + f"{total_words:,}"
            + "\n\n"
            + "Press Start Translation on the website."
        )


        return redirect("/")


    except Exception as error:

        return render_template_string(
            HTML,
            novel=None,
            message="Upload error: " + str(error),
            progress=0,
            chinese_title="",
            has_completed_chapters=False
        )


# =========================================================
# START / RESUME TRANSLATION
# =========================================================

@app.route(
    "/translate/<novel_id>"
)
def start_translation(novel_id):

    protection = login_required()

    if protection:
        return protection


    if not supabase:

        return "Supabase is not configured."


    try:

        chapters = get_chapters(
            novel_id
        )


        remaining = [

            chapter

            for chapter in chapters

            if chapter.get("status")
            != "translated"

        ]


        if not remaining:

            (
                supabase
                .table("novels")
                .update(
                    {
                        "status": "completed"
                    }
                )
                .eq(
                    "id",
                    novel_id
                )
                .execute()
            )


            return redirect("/")


        # =================================================
        # DO NOT START TWO WORKERS
        # =================================================

        if translation_lock.locked():

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
# HEALTH CHECK
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
