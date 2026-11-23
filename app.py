import os
import re
import logging
import base64
import hashlib
import requests
import yt_dlp
import subprocess
import shutil

from importlib.metadata import version, PackageNotFoundError
from urllib.parse import urljoin, quote, urlparse
from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

# --------------------------------------------------
# YOUTUBE COOKIES
# --------------------------------------------------

COOKIES_FILE = "/tmp/youtube_cookies.txt"

cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64", "")

if cookies_b64:
    try:
        cookie_data = base64.b64decode(
            cookies_b64.strip(),
            validate=True
        )

        with open(COOKIES_FILE, "wb") as f:
            f.write(cookie_data)

        os.chmod(COOKIES_FILE, 0o600)

        app.logger.info(
            "COOKIE CHECK: bytes=%s sha256=%s",
            len(cookie_data),
            hashlib.sha256(cookie_data).hexdigest()
        )

        app.logger.info("YouTube cookies loaded")

    except Exception:
        app.logger.exception("Cookie loading failed")
        raise RuntimeError("YouTube cookie loading failed")

else:
    app.logger.warning("YouTube cookies variable is missing")


# --------------------------------------------------
# CHANNELS
# --------------------------------------------------

CHANNELS = {
    "amrita": "https://www.youtube.com/@AmritaNews/live",
    "kannur": "ldYhjkWYNDE",
    "keralavision": "3446A6FzW3o"
}

POT_SERVER = "http://127.0.0.1:4416"

STREAMLINK_BIN = shutil.which("streamlink") or "streamlink"

PLAYER_CLIENTS = [
    x.strip()
    for x in os.environ.get(
        "YOUTUBE_PLAYER_CLIENTS",
        "web"
    ).split(",")
    if x.strip()
]


# --------------------------------------------------
# STREAMLINK VERSION
# --------------------------------------------------

def get_streamlink_version():

    try:
        return version("streamlink")

    except PackageNotFoundError:
        return "NOT INSTALLED"


# --------------------------------------------------
# STREAMLINK EXTRACTION - AMRITA NEWS
# --------------------------------------------------

def extract_amrita_stream():

    if not os.path.isfile(COOKIES_FILE):
        raise RuntimeError("YouTube cookies are missing")

    youtube_url = CHANNELS["amrita"]

    command = [
        STREAMLINK_BIN,
        "--loglevel", "error",
        "--http-timeout", "30",
        "--http-cookies-file", COOKIES_FILE,
        "--stream-url",
        youtube_url,
        "best"
    ]

    app.logger.info(
        "Starting Streamlink extraction for Amrita News"
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90,
        check=False
    )

    if result.returncode != 0:

        error_text = (result.stderr or result.stdout or "")[-3000:]

        app.logger.error(
            "Streamlink extraction failed: %s",
            error_text
        )

        raise RuntimeError(
            "Streamlink extraction failed: " + error_text
        )

    # Find extracted URL
    output_lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("https://")
    ]

    if not output_lines:
        raise RuntimeError(
            "Streamlink did not return a stream URL"
        )

    stream_url = output_lines[-1]

    parsed = urlparse(stream_url)

    if not (
        parsed.hostname
        and (
            parsed.hostname == "googlevideo.com"
            or parsed.hostname.endswith(".googlevideo.com")
        )
    ):
        raise RuntimeError(
            "Unexpected Streamlink URL host"
        )

    if ".m3u8" not in parsed.path.lower():
        raise RuntimeError(
            "Extracted URL is not an HLS playlist"
        )

    app.logger.info(
        "Amrita News HLS successfully extracted"
    )

    return stream_url


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.route("/")
def home():

    return jsonify({
        "status": "running",
        "channels": list(CHANNELS.keys()),
        "amrita_extractor": "Streamlink",
        "other_extractors": "yt-dlp",
        "streamlink_version": get_streamlink_version(),
        "diagnostics": "/diagnostics",
        "health": "/healthz",
        "player_clients": PLAYER_CLIENTS
    })


# --------------------------------------------------
# HEALTH
# --------------------------------------------------

@app.route("/health")
@app.route("/healthz")
def health():

    return "OK", 200


# --------------------------------------------------
# DIAGNOSTICS
# --------------------------------------------------

@app.route("/diagnostics")
def diagnostics():

    result = {
        "status": "running",
        "yt_dlp_version": yt_dlp.version.__version__,
        "streamlink_version": get_streamlink_version(),
        "streamlink_executable": STREAMLINK_BIN,
        "player_clients": PLAYER_CLIENTS,
        "youtube_cookies": (
            "loaded" if os.path.isfile(COOKIES_FILE)
            else "missing"
        ),
        "cookie_bytes": 0,
        "cookie_sha256": None,
        "pot_plugin": "unknown",
        "pot_server": "unknown"
    }

    if os.path.isfile(COOKIES_FILE):

        with open(COOKIES_FILE, "rb") as f:
            cookie_data = f.read()

        result["cookie_bytes"] = len(cookie_data)

        result["cookie_sha256"] = hashlib.sha256(
            cookie_data
        ).hexdigest()

    try:

        result["pot_plugin"] = version(
            "bgutil-ytdlp-pot-provider"
        )

    except PackageNotFoundError:

        result["pot_plugin"] = "NOT INSTALLED"

    try:

        r = requests.get(
            POT_SERVER + "/ping",
            timeout=5
        )

        try:
            server_info = r.json()
        except ValueError:
            server_info = r.text[:500]

        result["pot_server"] = {
            "http_status": r.status_code,
            "response": server_info
        }

    except Exception as e:

        result["pot_server"] = {
            "error": str(e)
        }

    return jsonify(result)


# --------------------------------------------------
# AMRITA NEWS - STREAMLINK
# --------------------------------------------------

@app.route("/amrita.m3u8")
@app.route("/channel/amrita.m3u8")
def amrita_channel():

    try:

        stream_url = extract_amrita_stream()

        # Send playlist through our HLS proxy
        proxy_url = (
            request.url_root.rstrip("/")
            + "/proxy?url="
            + quote(stream_url, safe="")
        )

        return redirect(proxy_url, code=302)

    except subprocess.TimeoutExpired:

        app.logger.exception(
            "Amrita Streamlink extraction timed out"
        )

        return jsonify({
            "channel": "amrita",
            "status": "error",
            "message": "Streamlink extraction timed out"
        }), 504

    except Exception as e:

        app.logger.exception(
            "Amrita News extraction failed"
        )

        return jsonify({
            "channel": "amrita",
            "extractor": "Streamlink",
            "status": "error",
            "message": str(e)
        }), 502


# --------------------------------------------------
# OTHER CHANNELS - YT-DLP
# --------------------------------------------------

@app.route("/channel/<name>.m3u8")
@app.route("/<name>.m3u8")
def youtube_channel(name):

    name = name.lower()

    if name == "amrita":
        return amrita_channel()

    channel_source = CHANNELS.get(name)

    if not channel_source:
        return "Channel not found", 404

    if channel_source.startswith("http"):
        youtube_url = channel_source
    else:
        youtube_url = (
            "https://www.youtube.com/watch?v="
            + channel_source
        )

    ydl_opts = {
        "format": "all",
        "js_runtimes": {
            "node": {}
        },
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_retries": 2,
        "extractor_args": {
            "youtube": {
                "player_client": PLAYER_CLIENTS
            }
        }
    }

    if os.path.isfile(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    try:

        app.logger.info(
            "Starting YouTube extraction: %s | %s",
            name,
            youtube_url
        )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                youtube_url,
                download=False
            )

        if not info:
            return "No video information returned", 502

        formats = info.get("formats", [])
        video_id = info.get("id")
        live_status = info.get("live_status")
        is_live = info.get("is_live", False)

        if live_status != "is_live" and not is_live:

            return jsonify({
                "channel": name,
                "video_id": video_id,
                "status": "not_live",
                "live_status": live_status
            }), 503

        hls_formats = [
            f for f in formats
            if f.get("protocol") in (
                "m3u8",
                "m3u8_native"
            )
            and f.get("url")
        ]

        if not hls_formats:

            return jsonify({
                "channel": name,
                "video_id": video_id,
                "status": "error",
                "message": "No genuine HLS formats found"
            }), 502

        hls_formats.sort(
            key=lambda f: (
                f.get("height") or 0,
                f.get("tbr") or 0
            ),
            reverse=True
        )

        selected = hls_formats[0]
        stream_url = selected["url"]

        proxy_url = (
            request.url_root.rstrip("/")
            + "/proxy?url="
            + quote(stream_url, safe="")
        )

        return redirect(proxy_url, code=302)

    except Exception as e:

        app.logger.exception(
            "YouTube extraction failed: %s",
            name
        )

        return jsonify({
            "channel": name,
            "status": "error",
            "message": str(e)
        }), 500


# --------------------------------------------------
# HLS PROXY
# --------------------------------------------------

@app.route("/proxy")
def proxy_stream():

    target_url = request.args.get("url")

    if not target_url:
        return "Missing url parameter", 400

    parsed = urlparse(target_url)

    if parsed.scheme not in ("http", "https"):
        return "Invalid URL", 400

    def allowed_host(host):

        host = (host or "").lower()

        return (
            host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "googlevideo.com"
            or host.endswith(".googlevideo.com")
        )

    if not allowed_host(parsed.hostname):
        return "Host not allowed", 403

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 "
            "Safari/537.36"
        ),
        "Referer": "https://www.youtube.com/",
        "Origin": "https://www.youtube.com"
    }

    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]

    try:

        req = requests.get(
            target_url,
            headers=headers,
            stream=True,
            timeout=(15, 30),
            allow_redirects=True
        )

        # Validate final redirect host too
        if not allowed_host(urlparse(req.url).hostname):
            req.close()
            return "Redirect host not allowed", 403

        if req.status_code >= 400:

            status = req.status_code
            req.close()

            return "Upstream error", status

        content_type = req.headers.get(
            "content-type", ""
        )

        is_playlist = (
            ".m3u8" in urlparse(req.url).path.lower()
            or ".m3u8" in parsed.path.lower()
            or "mpegurl" in content_type.lower()
        )

        # ------------------------------------------
        # REWRITE HLS PLAYLIST
        # ------------------------------------------

        if is_playlist:

            playlist_url = req.url

            lines = []

            forwarded_proto = request.headers.get(
                "X-Forwarded-Proto",
                request.scheme
            ).split(",")[0].strip()

            base_proxy = (
                forwarded_proto
                + "://"
                + request.host
                + "/proxy?url="
            )

            for line in req.text.splitlines():

                stripped = line.strip()

                if not stripped:
                    lines.append(line)
                    continue

                if stripped.startswith("#"):

                    def replace_uri(match):

                        original = match.group(1)

                        full_url = urljoin(
                            playlist_url,
                            original
                        )

                        return (
                            'URI="'
                            + base_proxy
                            + quote(full_url, safe="")
                            + '"'
                        )

                    line = re.sub(
                        r'URI="([^"]+)"',
                        replace_uri,
                        line
                    )

                    lines.append(line)
                    continue

                full_url = urljoin(
                    playlist_url,
                    stripped
                )

                lines.append(
                    base_proxy
                    + quote(full_url, safe="")
                )

            req.close()

            response = Response(
                "\n".join(lines) + "\n",
                content_type="application/vnd.apple.mpegurl"
            )

            response.headers["Cache-Control"] = "no-store"

            return response

        # ------------------------------------------
        # VIDEO / AUDIO SEGMENTS
        # ------------------------------------------

        def generate():

            try:

                for chunk in req.iter_content(
                    chunk_size=16384
                ):

                    if chunk:
                        yield chunk

            finally:
                req.close()

        response = Response(
            generate(),
            status=req.status_code,
            content_type=content_type
        )

        for header in (
            "Content-Range",
            "Accept-Ranges",
            "Content-Length"
        ):

            if header in req.headers:
                response.headers[header] = req.headers[header]

        response.headers["Cache-Control"] = "no-store"

        return response

    except Exception as e:

        app.logger.exception("Proxy request failed")

        return str(e), 500


# --------------------------------------------------
# START FLASK
# --------------------------------------------------

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
