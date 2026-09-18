import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET",
    "change-me-in-render",
)


VIDEO_DIR = Path(
    os.environ.get("VIDEO_DIR", "videos")
)

VIDEO_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "change-me",
)

RTMP_URL = os.environ.get(
    "KICK_RTMP_URL",
    "",
).strip()

STREAM_KEY = os.environ.get(
    "KICK_STREAM_KEY",
    "",
).strip()

DEFAULT_VIDEO = os.environ.get(
    "VIDEO_FILE",
    "",
)


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


def build_kick_url():
    """
    كيبني رابط Kick صحيح حتى إلا كانت شي قيمة مكتوبة بشكل ناقص.
    """

    rtmp_url = RTMP_URL
    stream_key = STREAM_KEY

    if not rtmp_url:
        raise RuntimeError(
            "KICK_RTMP_URL ناقص فـ Render"
        )

    if not stream_key:
        raise RuntimeError(
            "KICK_STREAM_KEY ناقص فـ Render"
        )

    # إذا دخل Stream Key كرابط كامل بالغلط
    if stream_key.startswith(
        (
            "rtmps://",
            "rtmp://",
            "https://",
            "http://",
         )
    ):
        parsed = urlparse(stream_key)

        stream_key = parsed.path.strip(
            "/"
        ).split("/")[-1]

        rtmp_url = (
            f"rtmps://{parsed.netloc}/app"
        )

    # تحويل https إلى rtmps
    if rtmp_url.startswith("https://" ):
        rtmp_url = (
            "rtmps://"
            + rtmp_url[len("https://" ):]
        )

    elif rtmp_url.startswith("http://" ):
        rtmp_url = (
            "rtmp://"
            + rtmp_url[len("http://" ):]
        )

    rtmp_url = rtmp_url.rstrip("/")

    # إضافة /app و :443 إذا ناقصين
    if "/app" not in rtmp_url:
        rtmp_url = rtmp_url + ":443/app"

    elif (
        rtmp_url.endswith("/app")
        and ":443" not in rtmp_url
    ):
        rtmp_url = (
            rtmp_url[:-4].rstrip("/")
            + ":443/app"
        )

    if not rtmp_url.startswith(
        (
            "rtmps://",
            "rtmp://",
        )
    ):
        raise RuntimeError(
            "KICK_RTMP_URL خاصو يبدا بـ rtmps://"
        )

    if not stream_key:
        raise RuntimeError(
            "Stream Key خاوي"
        )

    if "/" in stream_key:
        raise RuntimeError(
            "KICK_STREAM_KEY خاصو يكون المفتاح فقط، بلا رابط"
        )

    return f"{rtmp_url}/{stream_key}"


def download_google_drive_video(url):
    filename = (
        f"drive-{uuid.uuid4().hex}.mp4"
    )

    output_file = VIDEO_DIR / filename

    result = subprocess.run(
        [
            "gdown",
            "--fuzzy",
            url,
            "-O",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )

    if (
        result.returncode != 0
        or not output_file.exists()
        or output_file.stat().st_size == 0
    ):
        error_message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Google Drive ما قدرناش نحمّلو"
        )

        raise RuntimeError(
            f"فشل تحميل الفيديو: {error_message[-500:]}"
        )

    return output_file


def start_stream(video_source):
    global process
    global current_video

    output_url = build_kick_url()

    source = video_source.strip()

    if not source:
        raise RuntimeError(
            "خاصك تحط رابط الفيديو"
        )

    is_google_drive = (
        "drive.google.com" in source
        or "drive.usercontent.google.com" in source
    )

    is_web_url = source.startswith(
        (
            "http://",
            "https://",
         )
    )

    if is_google_drive:
        downloaded_file = (
            download_google_drive_video(source)
        )

        input_source = str(downloaded_file)
        current_video = source

    elif is_web_url:
        input_source = source
        current_video = source

    else:
        video_file = Path(source)

        if not video_file.is_absolute():
            video_file = VIDEO_DIR / video_file

        if not video_file.exists():
            raise RuntimeError(
                f"الفيديو ما لقايناهش: {video_file}"
            )

        input_source = str(video_file)
        current_video = video_file.name

    stop_stream()

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

    time.sleep(4)

    if process.poll() is not None:
        error_output = ""

        if process.stderr:
            error_output = (
                process.stderr.read()
                .decode(
                    "utf-8",
                    errors="ignore",
                )
                .strip()
            )

        process = None

        raise RuntimeError(
            f"FFmpeg توقف: {error_output[-1200:]}"
        )


def login_required():
    return session.get(
        "logged_in"
    ) is True


@app.route(
    "/",
    methods=["GET", "POST"],
)
def index():
    if not login_required():
        return redirect(
            url_for("login")
        )

    videos = sorted(
        [
            file.name
            for file in VIDEO_DIR.iterdir()
            if file.is_file()
            and not file.name.startswith("drive-")
        ]
    )

    if request.method == "POST":
        action = request.form.get(
            "action"
        )

        try:
            if action == "start":
                video_url = request.form.get(
                    "video_url",
                    "",
                ).strip()

                selected_video = request.form.get(
                    "video",
                    "",
                ).strip()

                source = (
                    video_url
                    or selected_video
                )

                start_stream(source)

                flash(
                    "البث تخدم بنجاح",
                    "ok",
                )

            elif action == "stop":
                stop_stream()

                flash(
                    "البث توقف",
                    "ok",
                )

            else:
                flash(
                    "أمر غير معروف",
                    "error",
                )

        except Exception as error:
            flash(
                str(error),
                "error",
            )

        return redirect(
            url_for("index")
        )

    return render_template(
        "index.html",
        running=is_running(),
        video=current_video,
        videos=videos,
    )


@app.route(
    "/login",
    methods=["GET", "POST"],
)
def login():
    if request.method == "POST":
        password = request.form.get(
            "password",
            "",
        )

        if password == ADMIN_PASSWORD:
            session["logged_in"] = True

            return redirect(
                url_for("index")
            )

        flash(
            "كلمة السر غير صحيحة",
            "error",
        )

    return render_template(
        "login.html"
    )


@app.route("/logout")
def logout():
    session.clear()

    return redirect(
        url_for("login")
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "streaming": is_running(),
        "video": current_video,
    }


def shutdown(*_):
    stop_stream()


signal.signal(
    signal.SIGTERM,
    shutdown,
)

signal.signal(
    signal.SIGINT,
    shutdown,
)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "10000",
            )
        ),
)
