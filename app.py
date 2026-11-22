
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

# --------------------------------------------------
# YouTube cookies
# --------------------------------------------------

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
        app.logger.info("YouTube cookies loaded")

    except Exception:
        app.logger.exception("Cookie loading failed")
        raise RuntimeError("YouTube cookie loading failed")
else:
    app.logger.warning("YouTube cookies variable is missing")


# --------------------------------------------------
# Channel IDs
# --------------------------------------------------

CHANNELS = {
    "amrita": "0JE1N_HQc1M",
    "kannur": "ldYhjkWYNDE",
    "keralavision": "3446A6FzW3o"
}

POT_SERVER = "http://127.0.0.1:4416"

PLAYER_CLIENTS = [
    x.strip()
    for x in os.environ.get(
        "YOUTUBE_PLAYER_CLIENTS",
        "web"
    ).split(",")
    if x.strip()
]


# --------------------------------------------------
# Home
# --------------------------------------------------

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "channels": list(CHANNELS.keys()),
        "diagnostics": "/diagnostics",
        "health": "/healthz",
        "player_clients": PLAYER_CLIENTS
    })


# --------------------------------------------------
# Health
# --------------------------------------------------

@app.route("/health")
@app.route("/healthz")
def health():
    return "OK", 200


# --------------------------------------------------
# Diagnostics
# --------------------------------------------------

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


# --------------------------------------------------
# YouTube channel extraction
# --------------------------------------------------

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

    # BGUtil PO-token provider
    # Local server: 127.0.0.1:4416

    if os.path.isfile(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    try:

        app.logger.info(
            "Starting YouTube extraction: %s",
            name
        )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                youtube_url,
                download=False
            )

        if not info:
            return "No video information returned", 502

        formats = info.get("formats", [])

        live_status = info.get("live_status")
        is_live = info.get("is_live", False)

        app.logger.info(
            "CHANNEL STATUS: %s | live=%s",
            live_status,
            is_live
        )

        # Log available formats

        app.logger.info(
            "AVAILABLE FORMATS: %s",
            [
                (
                    f.get("format_id"),
                    f.get("protocol"),
                    f.get("height"),
                    f.get("ext")
                )
                for f in formats
            ]
        )

        # --------------------------------------------------
        # LIVE BROADCAST CHECK
        # --------------------------------------------------

        if live_status != "is_live" and not is_live:

            app.logger.warning(
                "%s is not currently reported as live",
                name
            )

            return jsonify({
                "channel": name,
                "video_id": video_id,
                "status": "not_live",
                "live_status": live_status,
                "is_live": is_live,
                "message": (
                    "YouTube is not reporting an "
                    "active live broadcast"
                )
            }), 503

        # --------------------------------------------------
        # ACCEPT GENUINE HLS ONLY
        # --------------------------------------------------

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
                "live_status": live_status,
                "message": "No genuine HLS formats found",
                "available_formats": [
                    {
                        "format_id": f.get("format_id"),
                        "protocol": f.get("protocol"),
                        "height": f.get("height"),
                        "ext": f.get("ext")
                    }
                    for f in formats
                ]
            }), 502

        # --------------------------------------------------
        # SELECT HIGHEST QUALITY HLS
        # --------------------------------------------------

        hls_formats.sort(
            key=lambda f: (
                f.get("height") or 0,
                f.get("tbr") or 0
            ),
            reverse=True
        )

        selected = hls_formats[0]
        stream_url = selected["url"]

        app.logger.info(
            "SELECTED LIVE HLS: %s | height=%s | protocol=%s",
            selected.get("format_id"),
            selected.get("height"),
            selected.get("protocol")
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

    host = (parsed.hostname or "").lower()

    allowed_hosts = (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "googlevideo.com"
        or host.endswith(".googlevideo.com")
    )

    if not allowed_hosts:
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

    # Forward byte-range requests if supplied

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

        # --------------------------------------------------
        # REWRITE HLS PLAYLIST
        # --------------------------------------------------

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
                content_type=(
                    "application/vnd.apple.mpegurl"
                )
            )

            response.headers["Cache-Control"] = "no-store"

            return response

        # --------------------------------------------------
        # STREAM VIDEO / AUDIO SEGMENTS
        # --------------------------------------------------

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

        # Preserve relevant upstream headers

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

        app.logger.exception(
            "Proxy request failed"
        )

        return str(e), 500


# --------------------------------------------------
# START FLASK
# --------------------------------------------------

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
