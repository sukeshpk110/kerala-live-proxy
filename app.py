
import os
import re
import logging
import base64
import requests
import yt_dlp

from importlib.metadata import version, PackageNotFoundError
from urllib.parse import urljoin, quote, urlparse
from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

# ==========================================
# YouTube Cookies: Load securely from Render
# ==========================================

COOKIES_FILE = "/tmp/youtube_cookies.txt"

cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64", "")

if cookies_b64:
    try:
        cookie_data = base64.b64decode(
            cookies_b64.strip(),
            validate=True
        )

        with open(COOKIES_FILE, "wb") as cookie_file:
            cookie_file.write(cookie_data)

        os.chmod(COOKIES_FILE, 0o600)
        app.logger.info("YouTube cookies loaded successfully")

    except Exception:
        app.logger.exception("Failed to load YouTube cookies")
        raise RuntimeError("YouTube cookie loading failed")

else:
    app.logger.warning("YouTube cookies environment variable is missing")


# ==========================================
# YouTube Live channel IDs
# ==========================================

CHANNELS = {
    "amrita": "0JE1N_HQc1M",
    "kannur": "ldYhjkWYNDE",
    "keralavision": "3446A6FzW3o"
}

POT_SERVER = "http://127.0.0.1:4416"

# YouTube player clients
PLAYER_CLIENTS = [
    x.strip()
    for x in os.environ.get(
        "YOUTUBE_PLAYER_CLIENTS",
        "mweb,tv,web_safari"
    ).split(",")
    if x.strip()
]


# ==========================================
# 1. Home
# ==========================================

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "channels": list(CHANNELS.keys()),
        "diagnostics": "/diagnostics",
        "health": "/health",
        "player_clients": PLAYER_CLIENTS
    })


# ==========================================
# 2. Health check
# ==========================================

@app.route("/health")
@app.route("/healthz")
def health():
    return "OK", 200


# ==========================================
# 3. Diagnostics
# ==========================================

@app.route("/diagnostics")
def diagnostics():

    result = {
        "status": "running",
        "yt_dlp_version": yt_dlp.version.__version__,
        "player_clients": PLAYER_CLIENTS,
        "youtube_cookies": (
            "loaded" if os.path.isfile(COOKIES_FILE)
            else "missing"
        ),
        "pot_plugin": "unknown",
        "pot_server": "unknown"
    }

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


# ==========================================
# 4. YouTube Live extractor
# ==========================================

@app.route("/channel/<name>.m3u8")
@app.route("/<name>.m3u8")
def youtube_channel(name):

    name = name.lower()
    video_id = CHANNELS.get(name)

    if not video_id:
        return "Channel not found", 404

    youtube_url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    ydl_opts = {
        "format": "best[protocol=m3u8_native]/best[protocol=m3u8]/best",
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "noplaylist": True,

        "socket_timeout": 20,
        "retries": 3,
        "extractor_retries": 2,

        "extractor_args": {
            "youtube": {
                "player_client": PLAYER_CLIENTS
            },
            "youtubepot-bgutilhttp": {
                "base_url": POT_SERVER
            }
        }
    }

    # Use the private YouTube cookies
    if os.path.isfile(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    try:

        app.logger.info(
            "Starting extraction: channel=%s clients=%s",
            name,
            PLAYER_CLIENTS
        )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                youtube_url,
                download=False
            )

            if not info:
                return "No video information returned", 502

            stream_url = info.get("url")

            if not stream_url:
                return "Stream URL not found", 404

            app.logger.info(
                "Extraction successful: %s",
                name
            )

            return redirect(stream_url, code=302)

    except Exception as e:

        app.logger.exception(
            "YouTube extraction failed: %s",
            name
        )

        return jsonify({
            "channel": name,
            "video_id": video_id,
            "player_clients": PLAYER_CLIENTS,
            "status": "error",
            "message": str(e)
        }), 500


# ==========================================
# 5. HLS playlist and segment proxy
# ==========================================

@app.route("/proxy")
def proxy_stream():

    target_url = request.args.get("url")

    if not target_url:
        return "Missing url parameter", 400

    parsed = urlparse(target_url)

    if parsed.scheme not in ("http", "https"):
        return "Invalid URL", 400

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.youtube.com/",
        "Origin": "https://www.youtube.com"
    }

    try:

        req = requests.get(
            target_url,
            headers=headers,
            stream=True,
            timeout=(15, 30),
            allow_redirects=True
        )

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

        if is_playlist:

            playlist_url = req.url
            lines = []

            base_proxy = (
                request.host_url.rstrip("/")
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
            content_type=content_type
        )

        response.headers["Cache-Control"] = "no-store"

        return response

    except Exception as e:

        app.logger.exception("Proxy request failed")

        return str(e), 500


# ==========================================
# 6. Local development
# ==========================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
