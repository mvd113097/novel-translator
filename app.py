import os
import io
import re
import uuid
import time
import threading
import zipfile
import html
import json
import secrets
import sqlite3

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    send_file,
    session
)

import requests

from google import genai
from google.genai import types


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "strict_free_novel_translator_render_secure_key_2026"
)

DB_PATH = "translator_storage.db"

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Hardcoded gemini-3.6-flash so Render environment settings cannot break it
GEMINI_MODEL = "gemini-3.6-flash"
OPENROUTER_PREFERRED_FREE = "qwen/qwen3.6-plus:free"
OPENROUTER_FREE_ROUTER = "openrouter/free"

MAX_CHARS_PER_REQUEST = 6000
DOWNLOAD_MIN_WORDS = 30000
REQUEST_DELAY = 3.5  
MAX_RETRIES = 2
OPENROUTER_TIMEOUT = 120
GEMINI_TIMEOUT = 120


# ============================================================
# SQLITE PERSISTENT DATABASE STORAGE
# ============================================================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT,
                chapters TEXT,          
                translations TEXT,      
                translated_chapters INTEGER,
                total_chapters INTEGER,
                words INTEGER,
                percent INTEGER,
                status TEXT,
                error TEXT,
                running INTEGER,
                provider TEXT,
                provider_model TEXT,
                notified_30k INTEGER DEFAULT 0
            )
        """)
        conn.commit()

init_db()

def db_save_job(j):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO jobs 
            (id, filename, chapters, translations, translated_chapters, total_chapters, words, percent, status, error, running, provider, provider_model, notified_30k)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            j["id"], j["filename"], json.dumps(j["chapters"]), json.dumps(j["translations"]),
            j["translated_chapters"], j["total_chapters"], j["words"], j["percent"],
            j["status"], j["error"], 1 if j["running"] else 0, j["provider"], j.get("provider_model", ""),
            j.get("notified_30k", 0)
        ))
        conn.commit()

def db_get_all_jobs():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        jobs_dict = {}
        for r in rows:
            jobs_dict[r["id"]] = {
                "id": r["id"], "filename": r["filename"], 
                "chapters": json.loads(r["chapters"]), "translations": json.loads(r["translations"]),
                "translated_chapters": r["translated_chapters"], "total_chapters": r["total_chapters"],
                "words": r["words"], "percent": r["percent"], "status": r["status"],
                "error": r["error"], "running": True if r["running"] == 1 else False,
                "provider": r["provider"], "provider_model": r["provider_model"],
                "notified_30k": r["notified_30k"]
            }
        return jobs_dict

def db_get_job(job_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not r: return None
        return {
            "id": r["id"], "filename": r["filename"], 
            "chapters": json.loads(r["chapters"]), "translations": json.loads(r["translations"]),
            "translated_chapters": r["translated_chapters"], "total_chapters": r["total_chapters"],
            "words": r["words"], "percent": r["percent"], "status": r["status"],
            "error": r["error"], "running": True if r["running"] == 1 else False,
            "provider": r["provider"], "provider_model": r["provider_model"],
            "notified_30k": r["notified_30k"]
        }

def db_delete_job(job_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()


# ============================================================
# GEMINI SDK CLIENT
# ============================================================

gemini_client = None

if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        print(f"Free Gemini Client Engine mounted on Render using model: {GEMINI_MODEL}")
    except Exception as e:
        print("Gemini API key setup error mapped out:", repr(e))

jobs_lock = threading.Lock()


# ============================================================
# TELEGRAM NOTIFICATION SYSTEM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    try: 
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, 
            timeout=15
        )
    except Exception as e: 
        print("Telegram delivery error tracker failed:", repr(e))


# ============================================================
# HTML INTERFACE TEMPLATE
# ============================================================

PAGE = r"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta charset="UTF-8">
<title>Strictly Free Novel Translator</title>
<style>
* { box-sizing: border-box; }
body { font-family: Arial, sans-serif; background: #f3f4f6; margin: 0; padding: 15px; color: #222; }
.container { max-width: 760px; margin: auto; background: white; padding: 22px; border-radius: 16px; box-shadow: 0 3px 18px rgba(0,0,0,0.10); }
h1 { margin-top: 0; font-size: 25px; color: #111; }
h2 { margin-top: 28px; font-size: 20px; }
input[type=file], input[type=password], select, button { width: 100%; padding: 14px; margin-top: 10px; border-radius: 9px; font-size: 16px; }
input, select { border: 1px solid #ccc; }
button { border: none; background: #222; color: white; cursor: pointer; font-weight: bold; }
button:hover { background: #444; }
button.danger { background: #b00020; }
button.green { background: #177a32; }
button.blue { background: #1759a6; }
.small { color: #666; font-size: 14px; line-height: 1.5; margin-top: 8px; }
.notice { background: #eef5ff; border-left: 5px solid #2774d9; padding: 13px; border-radius: 8px; margin-top: 15px; font-size: 14px; }
.warning { background: #fff4d6; border-left: 5px solid #e5a100; padding: 13px; border-radius: 8px; margin-top: 15px; }
.error { background: #ffe4e4; border-left: 5px solid #c00000; padding: 13px; border-radius: 8px; margin-top: 15px; white-space: pre-wrap; font-size: 14px; }
.job { border: 1px solid #ddd; padding: 16px; margin-top: 16px; border-radius: 12px; background: #fff; }
.job-title { font-weight: bold; font-size: 17px; word-break: break-word; }
.badge { display: inline-block; padding: 5px 9px; border-radius: 20px; font-size: 12px; margin-top: 8px; font-weight: bold; }
.badge.gemini { background: #e7f0ff; color: #1659a6; }
.badge.openrouter { background: #e9f8e9; color: #176b2c; }
.progress { margin-top: 14px; background: #ddd; border-radius: 10px; overflow: hidden; height: 27px; }
.bar { height: 27px; background: #4caf50; text-align: center; color: white; line-height: 27px; min-width: 1%; font-weight: bold; }
.status { margin-top: 13px; padding: 12px; background: #f3f3f3; border-radius: 8px; white-space: pre-wrap; font-family: monospace; font-size: 13px; }
.word-box { margin-top: 13px; padding: 13px; border-radius: 9px; background: #f7f7f7; font-size: 15px; }
.download-box { margin-top: 13px; }
a.button { display: block; text-align: center; padding: 14px; margin-top: 10px; border-radius: 9px; background: #177a32; color: white; text-decoration: none; font-weight: bold; }
a.button:hover { background: #1e5c2b; }
.center { text-align: center; }
</style>
</head>
<body>
<div class="container">
{% if not authenticated %}
    <div class="center">
        <h1>🔒 Novel Translator Portal</h1>
        <p>Password verification required.</p>
        {% if login_error %}
            <div class="error">Authentication failure: Invalid Password.</div>
        {% endif %}
        <form action="/login" method="POST">
            <input type="password" name="password" placeholder="Enter system security password" required autofocus>
            <button type="submit">🔓 Enter Platform</button>
        </form>
    </div>
{% else %}
    <h1>📚 Free Background Novel Translator</h1>
    
    <div class="notice">
        <strong>Render Execution Policies:</strong>
        <p>• <strong>Free Engine:</strong> Powered by Gemini & OpenRouter AI.</p>
        <p>• <strong>Render Heartbeat:</strong> Keep this browser tab open to ensure background translation process doesn't sleep.</p>
        <p>• <strong>Telegram Tracking:</strong> You will get notifications on milestones and job completion.</p>
    </div>

    <form action="/upload" method="POST" enctype="multipart/form-data">
        <h2>Upload New Novel (.txt)</h2>
        <input type="file" name="file" accept=".txt" required>
        <select name="provider">
            <option value="gemini">Google Gemini AI</option>
            <option value="openrouter">OpenRouter Free API</option>
        </select>
        <button type="submit" class="blue">🚀 Start Translation Pipeline</button>
    </form>

    <h2>Active & Completed Translation Jobs</h2>
    {% if not jobs %}
        <p class="small">No active translation jobs. Upload a text file above to get started.</p>
    {% endif %}

    {% for jid, job in jobs.items() %}
    <div class="job">
        <div class="job-title">{{ job.filename }}</div>
        <span class="badge {{ job.provider }}">{{ job.provider | upper }} ({{ job.provider_model }})</span>
        
        <div class="progress">
            <div class="bar" style="width: {{ job.percent }}%;">{{ job.percent }}%</div>
        </div>
        
        <div class="word-box">
            📊 Progress: <strong>{{ job.translated_chapters }}</strong> / {{ job.total_chapters }} chapters | Total Translated Words: <strong>{{ job.words }}</strong>
        </div>

        <div class="status">Status: {{ job.status }}</div>

        {% if job.error %}
            <div class="error">{{ job.error }}</div>
        {% endif %}

        <div class="download-box">
            {% if job.words >= 30000 or job.percent == 100 %}
                <a class="button" href="/download/{{ job.id }}">📥 Download Translated Text File</a>
            {% else %}
                <p class="small center">⏳ File download unlocks at 30,000 translated words (Current: {{ job.words }} words).</p>
            {% endif %}
        </div>

        <form action="/delete/{{ job.id }}" method="POST" style="margin-top: 10px;">
            <button type="submit" class="danger">🗑️ Delete Job</button>
        </form>
    </div>
    {% endfor %}
{% endif %}
</div>
</body>
</html>
"""


# ============================================================
# HELPER FUNCTIONS & TRANSLATION ENGINE
# ============================================================

def split_text_into_chapters(text):
    patterns = [
        r"(?i)^\s*(chapter\s+\d+.*)$",
        r"^\s*(第[0-9一二三四五六七八九十百千万]+[章|节].*)$"
    ]
    lines = text.splitlines()
    chapters = []
    current_title = "Chapter 1"
    current_content = []

    for line in lines:
        is_heading = False
        for p in patterns:
            if re.match(p, line):
                is_heading = True
                break
        if is_heading:
            if current_content:
                chapters.append({"title": current_title, "content": "\n".join(current_content)})
                current_content = []
            current_title = line.strip()
        else:
            current_content.append(line)

    if current_content:
        chapters.append({"title": current_title, "content": "\n".join(current_content)})

    return chapters if chapters else [{"title": "Full Novel", "content": text}]


def translate_chunk_gemini(text):
    if not gemini_client:
        raise Exception("Gemini client is not initialized. Check GEMINI_API_KEY environment variable.")
    
    prompt = f"Translate the following novel text into English clearly and naturally. Maintain narrative tone and readability:\n\n{text}"
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )
    return response.text


def translate_chunk_openrouter(text):
    if not OPENROUTER_API_KEY:
        raise Exception("OpenRouter API Key is missing.")
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": OPENROUTER_PREFERRED_FREE,
        "messages": [
            {"role": "system", "content": "You are a professional literary translator specializing in web novels."},
            {"role": "user", "content": f"Translate this novel text into fluent English:\n\n{text}"}
        ]
    }
    resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=OPENROUTER_TIMEOUT)
    if resp.status_code != 200:
        raise Exception(f"OpenRouter API Error: {resp.status_code} - {resp.text}")
    
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def process_translation_job(job_id):
    job = db_get_job(job_id)
    if not job: return

    job["running"] = True
    job["status"] = "Translating background queue running..."
    db_save_job(job)

    try:
        total = len(job["chapters"])
        for idx in range(job["translated_chapters"], total):
            ch = job["chapters"][idx]
            text = ch["content"]

            if len(text) > MAX_CHARS_PER_REQUEST:
                chunks = [text[i:i+MAX_CHARS_PER_REQUEST] for i in range(0, len(text), MAX_CHARS_PER_REQUEST)]
            else:
                chunks = [text]

            translated_chunks = []
            for chunk in chunks:
                if job["provider"] == "gemini":
                    translated = translate_chunk_gemini(chunk)
                else:
                    translated = translate_chunk_openrouter(chunk)
                
                translated_chunks.append(translated)
                time.sleep(REQUEST_DELAY)

            full_translated_chapter = "\n\n".join(translated_chunks)
            job["translations"].append({
                "title": ch["title"],
                "content": full_translated_chapter
            })

            job["translated_chapters"] += 1
            job["words"] += len(full_translated_chapter.split())
            job["percent"] = int((job["translated_chapters"] / total) * 100)
            db_save_job(job)

            if job["words"] >= DOWNLOAD_MIN_WORDS and not job.get("notified_30k"):
                send_telegram(f"🎉 Job '{job['filename']}' passed {DOWNLOAD_MIN_WORDS} translated words threshold!")
                job["notified_30k"] = 1
                db_save_job(job)

        job["running"] = False
        job["status"] = "Completed translation successfully!"
        db_save_job(job)
        send_telegram(f"✅ Novel Translation Complete: '{job['filename']}' - Total Words: {job['words']}")

    except Exception as e:
        job["running"] = False
        job["status"] = "Execution halted on failure."
        job["error"] = str(e)
        db_save_job(job)
        send_telegram(f"❌ Translation Failed on '{job['filename']}': {str(e)}")


# ============================================================
# FLASK HTTP ROUTES
# ============================================================

@app.route("/")
def index():
    authenticated = session.get("authenticated", False)
    if SITE_PASSWORD and not authenticated:
        return render_template_string(PAGE, authenticated=False, login_error=False)
    
    jobs = db_get_all_jobs()
    return render_template_string(PAGE, authenticated=True, jobs=jobs, target_model=GEMINI_MODEL)


@app.route("/login", methods=["POST"])
def login():
    password = request.form.get("password", "")
    if SITE_PASSWORD and password == SITE_PASSWORD:
        session["authenticated"] = True
        return redirect(url_for("index"))
    elif not SITE_PASSWORD:
        session["authenticated"] = True
        return redirect(url_for("index"))
    
    return render_template_string(PAGE, authenticated=False, login_error=True)


@app.route("/upload", methods=["POST"])
def upload():
    if SITE_PASSWORD and not session.get("authenticated"):
        return redirect(url_for("index"))

    file = request.files.get("file")
    provider = request.form.get("provider", "gemini")

    if not file or file.filename == "":
        return redirect(url_for("index"))

    content = file.read().decode("utf-8", errors="ignore")
    chapters = split_text_into_chapters(content)

    job_id = str(uuid.uuid4())[:8]
    job_data = {
        "id": job_id,
        "filename": file.filename,
        "chapters": chapters,
        "translations": [],
        "translated_chapters": 0,
        "total_chapters": len(chapters),
        "words": 0,
        "percent": 0,
        "status": "Queued",
        "error": "",
        "running": False,
        "provider": provider,
        "provider_model": GEMINI_MODEL if provider == "gemini" else OPENROUTER_PREFERRED_FREE,
        "notified_30k": 0
    }

    db_save_job(job_data)

    t = threading.Thread(target=process_translation_job, args=(job_id,))
    t.daemon = True
    t.start()

    return redirect(url_for("index"))


@app.route("/download/<job_id>")
def download(job_id):
    job = db_get_job(job_id)
    if not job:
        return "Job not found", 404

    output_lines = []
    for t in job["translations"]:
        output_lines.append(f"=== {t['title']} ===\n\n{t['content']}\n\n")

    full_output = "\n".join(output_lines)
    buf = io.BytesIO()
    buf.write(full_output.encode("utf-8"))
    buf.seek(0)

    download_name = f"translated_{job['filename']}"
    return send_file(buf, as_attachment=True, download_name=download_name, mimetype="text/plain")


@app.route("/delete/<job_id>", methods=["POST"])
def delete_job(job_id):
    db_delete_job(job_id)
    return redirect(url_for("index"))


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
