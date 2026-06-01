import os
import re
import uuid
import threading
import time
from flask import Flask, request, jsonify, send_file, abort
from yt_dlp import YoutubeDL

app = Flask(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

JOBS = {}
LOCK = threading.Lock()


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\s.\-()\[\]]+", "_", name)
    return name.strip()[:120] or "download"


def cleanup_old_files():
    now = time.time()
    try:
        for entry in os.listdir(DOWNLOAD_DIR):
            full = os.path.join(DOWNLOAD_DIR, entry)
            if os.path.isdir(full) and now - os.path.getmtime(full) > 3600:
                try:
                    import shutil
                    shutil.rmtree(full, ignore_errors=True)
                except Exception:
                    pass
    except Exception:
        pass


def run_download(job_id: str, url: str, fmt: str):
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    def progress_hook(d):
        with LOCK:
            if job_id not in JOBS:
                return
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                pct = int(downloaded * 100 / total) if total else 0
                JOBS[job_id]["progress"] = pct
                JOBS[job_id]["status"] = "downloading"
                JOBS[job_id]["speed"] = d.get("speed") or 0
            elif d["status"] == "finished":
                JOBS[job_id]["progress"] = 100
                JOBS[job_id]["status"] = "processing"

    if fmt == "audio":
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(job_dir, "%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
    else:
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": os.path.join(job_dir, "%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "download")

        files = [f for f in os.listdir(job_dir) if os.path.isfile(os.path.join(job_dir, f))]
        if not files:
            raise RuntimeError("No file produced")
        files.sort(key=lambda x: (
            0 if x.endswith(".mp3") else (0 if x.endswith(".mp4") else 1),
            -os.path.getsize(os.path.join(job_dir, x))
        ))
        final_file = files[0]
        with LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["progress"] = 100
            JOBS[job_id]["title"] = title
            JOBS[job_id]["filename"] = final_file
    except Exception as e:
        with LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)[:300]

    cleanup_old_files()


@app.get("/")
def root():
    return jsonify({
        "name": "Do It Server",
        "status": "ok",
        "endpoints": ["/health", "/api/download", "/api/status/<id>", "/api/file/<id>"]
    })


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/download")
def start_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    fmt = (data.get("format") or "video").strip().lower()
    if not url:
        return jsonify({"error": "url required"}), 400
    if fmt not in ("video", "audio"):
        fmt = "video"

    job_id = uuid.uuid4().hex
    with LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "url": url,
            "format": fmt,
            "title": "",
            "filename": "",
            "error": "",
            "speed": 0,
            "created": time.time(),
        }
    threading.Thread(target=run_download, args=(job_id, url, fmt), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.get("/api/status/<job_id>")
def status(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "job_id": job_id,
            "status": job["status"],
            "progress": job["progress"],
            "title": job["title"],
            "filename": job["filename"],
            "error": job["error"],
            "speed": job.get("speed", 0),
            "format": job["format"],
        })


@app.get("/api/file/<job_id>")
def get_file(job_id):
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        abort(404)
    if job["status"] != "done":
        return jsonify({"error": "not ready", "status": job["status"]}), 409
    fpath = os.path.join(DOWNLOAD_DIR, job_id, job["filename"])
    if not os.path.isfile(fpath):
        abort(404)
    download_name = sanitize_filename(job.get("title") or "download")
    ext = os.path.splitext(job["filename"])[1]
    return send_file(fpath, as_attachment=True, download_name=f"{download_name}{ext}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
