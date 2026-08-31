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

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip()
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
            f"https://telegram.org{TELEGRAM_BOT_TOKEN}/sendMessage", 
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
input[type=file], input[type=password], button { width: 100%; padding: 14px; margin-top: 10px; border-radius: 9px; font-size: 16px; }
input { border: 1px solid #ccc; }
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
.badge.qwen { background: #e9f8e9; color: #176b2c; }
.badge.router { background: #fff2d9; color: #875b00; }
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
    <h1>📚 Free Background Novel Translator (Render Optimized)</h1>
    
    <div class="notice">
        <strong>Render Execution Policies:</strong>
        <p>• Free Google Gemini endpoints (Targeting: <code>{{ target_model }}</code>).</p>
        <p>• <strong>Render Heartbeat:</strong> Keep this browser tab open to ensure the translation process never rests.</p>
        <p>• <strong>Telegram Tracking:</strong> You'll get messages on milestones (30k words processed), error blocks, and finished pipelines.</p>
    </div>

    <form action="/upload" method="POST" enctype="multipart/form-data">
