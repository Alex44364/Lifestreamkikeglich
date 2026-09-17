import os
import signal
import subprocess
import threading
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for, flash


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me-in-render")

VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", "videos"))
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")
RTMP_URL = os.environ.get("KICK_RTMP_URL", "")
STREAM_KEY = os.environ.get("KICK_STREAM_KEY", "")
DEFAULT_VIDEO = os.environ.get("VIDEO_FILE", "")

process = None
process_lock = threading.Lock()
current_video = DEFAULT_VIDEO


def is_running():
    return process is not None and process.poll() is None


def stop_stream():
    global process

    with process_lock:
        if process and process.poll() is None:
            process.terminate()

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

        process = None


def start_stream(video_name):
    global process, current_video

    if not RTMP_URL or not STREAM_KEY:
        raise RuntimeError(
            "خاصك تضبط KICK_RTMP_URL و KICK_STREAM_KEY فـ Render"
        )

    source = video_name.strip()

    is_url = source.startswith(("http://", "https://" ))

    video_path = Path(source)

    if not is_url:
        if not video_path.is_absolute():
            video_path = VIDEO_DIR / video_path

        if not video_path.exists():
            raise RuntimeError(
                f"الفيديو ما لقايناهش: {video_path}"
            )

    stop_stream()

    output_url = f"{RTMP_URL.rstrip('/')}/{STREAM_KEY}"

    input_source = source if is_url else str(video_path)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",

        "-re",
        "-stream_loop",
        "-1",
        "-i",
        input_source,

        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",

        "-b:v",
        "4500k",
        "-maxrate",
        "4500k",
        "-bufsize",
        "9000k",

        "-g",
        "60",

        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",

        "-f",
        "flv",
        output_url,
    ]

    with process_lock:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        current_video = source if is_url else video_path.name


def login_required():
    return session.get("logged_in") is True


@app.route("/", methods=["GET", "POST"])
def index():
    if not login_required():
        return redirect(url_for("login"))

    videos = sorted(
        [
            file.name
            for file in VIDEO_DIR.iterdir()
            if file.is_file()
        ]
    )

    if request.method == "POST":
        action = request.form.get("action")

        try:
            if action == "start":
                video_url = request.form.get("video_url", "").strip()
                selected_video = request.form.get("video", "").strip()

                source = video_url or selected_video

                if not source:
                    raise RuntimeError(
                        "اختار فيديو أو حط رابط مباشر للفيديو"
                    )

                start_stream(source)

                flash("البث تخدم بنجاح", "ok")

            elif action == "stop":
                stop_stream()
                flash("البث توقف", "ok")

            else:
                flash("أمر غير معروف", "error")

        except Exception as error:
            flash(str(error), "error")

        return redirect(url_for("index"))

    return render_template(
        "index.html",
        running=is_running(),
        video=current_video,
        videos=videos,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")

        if password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))

        flash("كلمة السر غير صحيحة", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/health")
def health():
    return {
        "ok": True,
        "streaming": is_running(),
        "video": current_video,
    }


def shutdown(*_):
    stop_stream()


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
    )

