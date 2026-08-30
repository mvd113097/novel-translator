import os
import re
import io
import threading
import time
import gc
import secrets
import requests

from flask import (
    Flask,
    request,
    render_template_string,
    redirect,
    send_file,
    session
)

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

# NEW:
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

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
# MODEL SETTINGS
# =========================================================

# Primary model
GEMINI_MODEL = "gemini-3.6-flash"

# Fallback model
OPENROUTER_MODEL = (
    "qwen/qwen3-235b-a22b-2507:free"
)


# =========================================================
# TRANSLATION CONTROL
# =========================================================

translation_lock = threading.Lock()

current_translation_novel = None


# =========================================================
# BATCH SETTINGS
# =========================================================

# Maximum Chinese characters sent in one translation request.
#
# We deliberately do NOT use the full model context window.
#
# Smaller batches are safer because:
#
# - translated English can be longer
# - prompts add tokens
# - chapter markers add tokens
# - very large responses are more likely to fail
#
# 30,000 Chinese characters is a good starting point.
#
# If testing shows the models handle it comfortably, this can
# later be increased.
BATCH_MAX_CHARS = 30000


# Delay after successful request.
BATCH_DELAY = 1


# =========================================================
# HTTP SETTINGS
# =========================================================

OPENROUTER_TIMEOUT = 600


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

    if not text:
        return 0

    english_words = re.findall(
        r"\b[\w'-]+\b",
        text
    )

    chinese_chars = re.findall(
        r"[\u4e00-\u9fff]",
        text
    )

    if chinese_chars:

        return len(chinese_chars)

    return len(english_words)


# =========================================================
# CHINESE DETECTION
# =========================================================

def contains_chinese(text):

    return bool(
        re.search(
            r"[\u4e00-\u9fff]",
            text or ""
        )
    )


# =========================================================
# CHAPTER HEADING
# =========================================================

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


# =========================================================
# TITLE DETECTION
# =========================================================

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
# BATCH CREATION
# =========================================================

def create_translation_batches(chapters):

    batches = []

    current_batch = []
    current_chars = 0


    for chapter in chapters:

        text = (
            chapter.get(
                "original_text",
                ""
            )
            or ""
        )


        if not text.strip():
            continue


        chapter_chars = len(text)


        # -------------------------------------------------
        # LARGE CHAPTER
        # -------------------------------------------------

        if chapter_chars > BATCH_MAX_CHARS:

            if current_batch:

                batches.append(
                    current_batch
                )

                current_batch = []
                current_chars = 0


            # A large chapter gets its own request.
            batches.append(
                [chapter]
            )

            continue


        # -------------------------------------------------
        # WOULD EXCEED BATCH SIZE
        # -------------------------------------------------

        if (
            current_batch
            and
            current_chars + chapter_chars
            > BATCH_MAX_CHARS
        ):

            batches.append(
                current_batch
            )

            current_batch = []
            current_chars = 0


        current_batch.append(
            chapter
        )

        current_chars += chapter_chars


    if current_batch:

        batches.append(
            current_batch
        )


    return batches


# =========================================================
# ERROR HELPERS
# =========================================================

def error_text(error):

    try:

        return str(error)

    except Exception:

        return repr(error)


def is_quota_error(error):

    text = error_text(
        error
    ).lower()


    patterns = [

        "429",

        "quota exceeded",

        "too many requests",

        "rate limit",

        "rate_limit",

        "resource exhausted",

        "generate_content_free_tier_requests",

        "free-models-per-day",

        "free model rate limit",

        "requests per day",

        "requests per minute"

    ]


    return any(
        pattern in text
        for pattern in patterns
    )


def get_retry_seconds(error):

    text = error_text(
        error
    )


    patterns = [

        r"retry in\s+([0-9]+(?:\.[0-9]+)?)s",

        r"retry-after[\"']?\s*[:=]\s*[\"']?([0-9]+)",

        r"try again in\s+([0-9]+(?:\.[0-9]+)?)s"

    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )


        if match:

            try:

                return max(
                    1,
                    int(
                        float(
                            match.group(1)
                        )
                    ) + 2
                )

            except Exception:

                pass


    return 60


# =========================================================
# TRANSLATION PROMPT
# =========================================================

def build_translation_prompt(batch):

    sections = []


    for index, chapter in enumerate(batch):

        marker = (
            f"<<<CHAPTER_{index + 1}_START>>>"
        )

        end_marker = (
            f"<<<CHAPTER_{index + 1}_END>>>"
        )


        sections.append(

            marker
            + "\n"
            + chapter["text"]
            + "\n"
            + end_marker

        )


    combined_text = "\n\n".join(
        sections
    )


    prompt = f"""
You are a professional Chinese-to-English web-novel translator.

Translate EVERY chapter below into natural, fluent English.

This is a literary translation, not a summary.

IMPORTANT RULES:

1. Translate everything.
2. Do NOT summarize.
3. Do NOT omit sentences.
4. Do NOT skip paragraphs.
5. Preserve the original meaning.
6. Preserve paragraph breaks.
7. Keep character names consistent.
8. Keep genders and pronouns consistent.
9. Keep dialogue natural.
10. Preserve humor and sarcasm.
11. Translate Chinese internet slang naturally into English.
12. Preserve jokes and wordplay as naturally as possible.
13. Do not add explanations.
14. Do not add translator notes.
15. Do not combine chapters.
16. Do not change chapter order.
17. Output ONLY the translations.
18. Preserve every START and END marker exactly.
19. Do not translate or modify the markers.
20. Do not invent content.
21. Do not shorten the story.
22. Internal thoughts enclosed in [ ] should remain clearly recognizable as thoughts.
23. Keep the same paragraph structure whenever practical.

The markers are used by the program to separate the chapters after translation.

Return EXACTLY this structure:

<<<CHAPTER_1_START>>>
English translation
<<<CHAPTER_1_END>>>

<<<CHAPTER_2_START>>>
English translation
<<<CHAPTER_2_END>>>

Continue this way for every chapter.

TEXT TO TRANSLATE:

{combined_text}
"""


    return prompt


# =========================================================
# GEMINI TRANSLATION
# =========================================================

def translate_with_gemini(prompt):

    if not gemini:

        raise RuntimeError(
            "Gemini API key is not configured."
        )


    print(
        "Using Gemini:",
        GEMINI_MODEL
    )


    interaction = gemini.interactions.create(
        model=GEMINI_MODEL,
        input=prompt
    )


    result = interaction.output_text


    if not result:

        raise RuntimeError(
            "Gemini returned an empty translation."
        )


    return result.strip()


# =========================================================
# OPENROUTER TRANSLATION
# =========================================================

def translate_with_openrouter(prompt):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured."
        )


    print(
        "Using OpenRouter:",
        OPENROUTER_MODEL
    )


    response = requests.post(

        "https://openrouter.ai/api/v1/chat/completions",

        headers={
            "Authorization":
                "Bearer "
                + OPENROUTER_API_KEY,

            "Content-Type":
                "application/json",

            "HTTP-Referer":
                os.environ.get(
                    "APP_URL",
                    "https://localhost"
                ),

            "X-Title":
                "Novel Translator"
        },

        json={

            "model":
                OPENROUTER_MODEL,

            "messages": [

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }

            ],

            "temperature":
                0.2,

            "max_tokens":
                65536

        },

        timeout=OPENROUTER_TIMEOUT

    )


    if response.status_code != 200:

        raise RuntimeError(
            "OpenRouter HTTP "
            + str(response.status_code)
            + ": "
            + response.text[:3000]
        )


    try:

        data = response.json()

    except Exception:

        raise RuntimeError(
            "OpenRouter returned invalid JSON."
        )


    choices = data.get(
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


    result = message.get(
        "content",
        ""
    )


    if isinstance(
        result,
        list
    ):

        result = "".join(

            part.get(
                "text",
                ""
            )

            if isinstance(
                part,
                dict
            )

            else str(part)

            for part in result

        )


    if not result:

        raise RuntimeError(
            "OpenRouter returned an empty translation."
        )


    return result.strip()


# =========================================================
# BATCH TRANSLATION WITH FALLBACK
# =========================================================

def translate_batch(batch):

    prompt = build_translation_prompt(
        batch
    )


    # =====================================================
    # TRY GEMINI FIRST
    # =====================================================

    if gemini:

        try:

            result = translate_with_gemini(
                prompt
            )

            return result, "Gemini"


        except Exception as gemini_error:

            print(
                "Gemini error:",
                error_text(
                    gemini_error
                )
            )


            # ---------------------------------------------
            # ONLY FALL BACK TO QWEN FOR QUOTA / RATE LIMIT
            # ---------------------------------------------

            if not is_quota_error(
                gemini_error
            ):

                # A normal Gemini error should still try
                # Qwen once, because Qwen can potentially
                # complete the request successfully.
                print(
                    "Gemini failed for a non-quota reason."
                )

            else:

                print(
                    "Gemini quota/rate limit detected."
                )


            if OPENROUTER_API_KEY:

                try:

                    result = translate_with_openrouter(
                        prompt
                    )

                    return result, "Qwen"


                except Exception as qwen_error:

                    print(
                        "Qwen error:",
                        error_text(
                            qwen_error
                        )
                    )

                    raise RuntimeError(

                        "Gemini failed:\n"
                        + error_text(
                            gemini_error
                        )
                        + "\n\n"
                        + "Qwen failed:\n"
                        + error_text(
                            qwen_error
                        )

                    )

            raise


    # =====================================================
    # NO GEMINI → USE QWEN
    # =====================================================

    if OPENROUTER_API_KEY:

        result = translate_with_openrouter(
            prompt
        )

        return result, "Qwen"


    raise RuntimeError(
        "Neither Gemini nor OpenRouter is configured."
    )


# =========================================================
# PARSE BATCH TRANSLATION
# =========================================================

def parse_batch_translation(
    translated_text,
    batch
):

    translations = []


    for index, chapter in enumerate(batch):

        number = index + 1


        start_marker = (
            f"<<<CHAPTER_{number}_START>>>"
        )

        end_marker = (
            f"<<<CHAPTER_{number}_END>>>"
        )


        start_position = translated_text.find(
            start_marker
        )


        if start_position == -1:

            raise RuntimeError(
                "Translation did not contain expected "
                + f"start marker for chapter {number}."
            )


        content_start = (
            start_position
            + len(start_marker)
        )


        end_position = translated_text.find(
            end_marker,
            content_start
        )


        if end_position == -1:

            raise RuntimeError(
                "Translation did not contain expected "
                + f"end marker for chapter {number}."
            )


        content = translated_text[
            content_start:end_position
        ].strip()


        if not content:

            raise RuntimeError(
                "Empty translation for chapter "
                + str(number)
            )


        translations.append(
            content
        )


    if len(translations) != len(batch):

        raise RuntimeError(
            "Translated chapter count does not "
            "match batch chapter count."
        )


    return translations


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


    if not translation_lock.acquire(
        blocking=False
    ):

        print(
            "Another translation is already running."
        )

        return


    current_translation_novel = novel_id


    try:

        chapters = get_chapters(
            novel_id
        )


        # -------------------------------------------------
        # COUNT ALREADY COMPLETED CHAPTERS
        # -------------------------------------------------

        translated_words = 0


        for chapter in chapters:

            if (
                chapter.get("status")
                == "translated"
            ):

                translated_words += (

                    chapter.get(
                        "translated_words",
                        0
                    )
                    or 0

                )


        # -------------------------------------------------
        # MARK TRANSLATING
        # -------------------------------------------------

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


        # -------------------------------------------------
        # ONLY UNFINISHED CHAPTERS
        # -------------------------------------------------

        remaining = []


        for chapter in chapters:

            if (
                chapter.get("status")
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


            remaining.append(
                chapter
            )


        # -------------------------------------------------
        # NOTHING LEFT
        # -------------------------------------------------

        if not remaining:

            (
                supabase
                .table("novels")
                .update(
                    {
                        "status":
                            "completed",

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


        # -------------------------------------------------
        # CREATE BATCHES
        # -------------------------------------------------

        batches = create_translation_batches(
            remaining
        )


        print(
            "Remaining chapters:",
            len(remaining)
        )

        print(
            "Translation batches:",
            len(batches)
        )


        # =================================================
        # PROCESS BATCHES
        # =================================================

        for batch_number, batch in enumerate(
            batches,
            start=1
        ):


            batch_chapters = len(
                batch
            )


            batch_chars = sum(

                len(
                    chapter.get(
                        "original_text",
                        ""
                    )
                    or ""
                )

                for chapter in batch

            )


            print(
                f"Starting batch {batch_number}/"
                f"{len(batches)}"
                f" - {batch_chapters} chapters"
                f" - {batch_chars:,} Chinese characters"
            )


            try:

                # -------------------------------------------------
                # TRANSLATE
                # -------------------------------------------------

                translated_batch, model_used = (
                    translate_batch(
                        batch
                    )
                )


                print(
                    "Batch translated using:",
                    model_used
                )


                # -------------------------------------------------
                # PARSE
                # -------------------------------------------------

                translations = parse_batch_translation(
                    translated_batch,
                    batch
                )


                # -------------------------------------------------
                # IMPORTANT:
                #
                # We only start saving AFTER the entire batch
                # has successfully returned AND parsed.
                #
                # Therefore a malformed/partial response does
                # not mark chapters as translated.
                # -------------------------------------------------

                for chapter, translated in zip(
                    batch,
                    translations
                ):

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


                    # -------------------------------------------------
                    # SAVE TOTAL AFTER EVERY CHAPTER
                    # -------------------------------------------------

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


                    print(
                        "Completed chapter",
                        chapter["chapter_number"],
                        "-",
                        words,
                        "translated words",
                        "- model:",
                        model_used
                    )


                print(
                    f"Completed batch {batch_number}/"
                    f"{len(batches)}"
                    f" using {model_used}"
                )


                del translated_batch
                del translations

                gc.collect()


                time.sleep(
                    BATCH_DELAY
                )


            except Exception as error:

                error_message = error_text(
                    error
                )


                print(
                    "TRANSLATION ERROR:",
                    error_message
                )


                # =================================================
                # QUOTA / RATE LIMIT
                # =================================================

                if is_quota_error(error):

                    retry_seconds = get_retry_seconds(
                        error
                    )


                    try:

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

                    except Exception:

                        pass


                    send_telegram(

                        "⏸️ TRANSLATION PAUSED\n\n"

                        "Both available translation paths "
                        "are currently rate-limited or unavailable.\n\n"

                        "Completed chapters were saved safely.\n\n"

                        f"Suggested retry time: about "
                        f"{retry_seconds} seconds.\n\n"

                        "Press Resume Translation later. "
                        "Already completed chapters will "
                        "NOT be translated again.\n\n"

                        f"Translated words: "
                        f"{translated_words:,}"

                    )


                    return


                # =================================================
                # OTHER ERROR
                # =================================================

                try:

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

                except Exception:

                    pass


                send_telegram(

                    "🔴 TRANSLATION STOPPED\n\n"

                    "An unexpected translation error occurred.\n\n"

                    f"Error:\n"
                    f"{error_message[:1500]}\n\n"

                    f"Translated words: "
                    f"{translated_words:,}\n\n"

                    "Completed chapters were saved.\n"

                    "Press Resume Translation to continue."

                )


                return


        # =================================================
        # ALL FINISHED
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


        send_telegram(

            "✅ TRANSLATION COMPLETE!\n\n"

            "The novel has finished translating.\n\n"

            "Translated words: "
            + f"{translated_words:,}"

        )


    except Exception as error:

        error_message = error_text(
            error
        )


        print(
            "WORKER ERROR:",
            error_message
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


        send_telegram(

            "🔴 TRANSLATION WORKER STOPPED\n\n"

            "The translation worker stopped unexpectedly.\n\n"

            f"Error:\n"
            f"{error_message[:1500]}\n\n"

            "Completed chapters were saved.\n"

            "Press Resume Translation to continue."

        )


    finally:

        current_translation_novel = None

        gc.collect()

        translation_lock.release()


# =========================================================
# DOWNLOAD PERMISSION
# =========================================================

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


    # =====================================================
    # TITLE PAGE
    # =====================================================

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


    # =====================================================
    # COMPLETED CHAPTERS
    # =====================================================

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


        if current_translation_novel == novel_id:

            current_translation_novel = None


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

.error {
    background: #f8d7da;
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

.model {
    background: #f0f0f0;
    padding: 10px;
    border-radius: 8px;
    margin-top: 10px;
    font-size: 14px;
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


{% if novel.status == "paused" %}

<div class="warning">

⏸️ Translation is paused.

<br><br>

The translator has stopped making requests
instead of repeatedly hitting the API limits.

<br><br>

Completed chapters are already saved safely.

<br><br>

Press Resume Translation to continue.

</div>

{% endif %}


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

Gemini is used first.

If Gemini reaches a quota/rate limit,
the app automatically tries Qwen through OpenRouter.

<br><br>

Multiple small chapters are combined into larger
requests to reduce the number of API requests.

<br><br>

Completed chapters are saved immediately.

<br><br>

You can close your browser while translation continues.

<br><br>

You can also download completed chapters while
the remaining chapters continue translating.

</div>


<div class="model">

Primary: Gemini

<br>

Fallback: Qwen3 235B A22B Instruct 2507

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

You do <strong>NOT</strong> need 20,000 translated
words before downloading.

<br><br>

As soon as the first chapter from a successfully
translated batch is saved, the download buttons
become available.

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

The download buttons will appear immediately
after the first chapter is successfully saved.

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

{% if novel and novel.status == "translating" %}

setTimeout(function() {
    window.location.reload();
}, 10000);

{% endif %}

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


    return render_template_string(

        HTML,

        novel=novel,

        message=message,

        chinese_title=chinese_title,

        has_completed_chapters=(
            has_completed_chapters
        ),

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

    protection = login_required()

    if protection:
        return protection


    if not supabase:

        return render_template_string(

            HTML,

            novel=None,

            message=(
                "Supabase is not configured."
            ),

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

            message=(
                "Please choose a TXT or EPUB file."
            ),

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


        chapters = split_text_into_chapters(
            text
        )


        total_words = sum(

            count_words(
                chapter["text"]
            )

            for chapter in chapters

        )


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


        send_telegram(

            "📚 NOVEL UPLOADED\n\n"
            + title
            + "\n\n"
            + "Original words: "
            + f"{total_words:,}"
            + "\n\n"
            + "Gemini → Qwen fallback enabled."
            + "\n\n"
            + "Press Start Translation on the website."

        )


        return redirect("/")


    except Exception as error:

        return render_template_string(

            HTML,

            novel=None,

            message=(
                "Upload error: "
                + str(error)
            ),

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
