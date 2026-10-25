"""A tiny stand-in for Echo360, so the client can be tested offline.

It mimics the shapes the real service returns: a nested syllabus document, a
JSON transcript on the media endpoint for one lesson, a WebVTT file on the
fallback endpoint for another, nothing at all for a third, and a redirect to
an SSO page when the session cookie is missing.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECTION = "11111111-2222-3333-4444-555555555555"
LESSON_JSON = "aaaaaaaa-0000-0000-0000-000000000001"
LESSON_VTT = "aaaaaaaa-0000-0000-0000-000000000002"
LESSON_NONE = "aaaaaaaa-0000-0000-0000-000000000003"
MEDIA_JSON = "bbbbbbbb-0000-0000-0000-000000000001"
MEDIA_VTT = "bbbbbbbb-0000-0000-0000-000000000002"

SYLLABUS = {
    "status": "ok",
    "data": [
        {
            "type": "SyllabusLessonType",
            "lesson": {
                "lesson": {
                    "id": LESSON_VTT,
                    "displayName": "Lecture 2: Thevenin / Norton",
                    "startTimeUTC": "2026-03-09T02:00:00Z",
                },
                "medias": [{"id": MEDIA_VTT, "duration": 3123.0}],
                "hasAvailableVideo": True,
            },
        },
        {
            "type": "SyllabusGroupType",
            "groupInfo": {"name": "Week 1"},
            "lessons": [
                {
                    "type": "SyllabusLessonType",
                    "lesson": {
                        "lesson": {
                            "id": LESSON_JSON,
                            "displayName": "Lecture 1: Circuit basics",
                            "startTimeUTC": "2026-03-02T02:00:00Z",
                        },
                        "medias": [{"id": MEDIA_JSON, "duration": 2890.5}],
                    },
                }
            ],
        },
        {
            "type": "SyllabusLessonType",
            "lesson": {
                "lesson": {
                    "id": LESSON_NONE,
                    "name": "Lab briefing (no captions)",
                    "startTimeUTC": "2026-03-16T02:00:00Z",
                },
                "medias": [],
            },
        },
    ],
}

TRANSCRIPT_JSON = {
    "status": "ok",
    "data": {
        "transcriptId": "cccccccc-0000-0000-0000-000000000001",
        "cues": [
            {"startMs": 0, "endMs": 4200, "content": "Good morning, everyone."},
            {"startMs": 4200, "endMs": 9100,
             "content": "Today we are looking at <i>Kirchhoff's</i> laws."},
            {"startMs": 9100, "endMs": 13000, "content": "Start with the current law."},
        ],
    },
}

TRANSCRIPT_VTT = """WEBVTT

00:00:00.000 --> 00:00:03.500
Welcome back to the second lecture.

00:00:03.500 --> 00:00:08.250
Thevenin equivalents make this much easier.
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep the test output clean
        pass

    # -- helpers
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload) -> None:
        self._send(200, json.dumps(payload).encode(), "application/json")

    def _authenticated(self) -> bool:
        return "ECHO_JWT=" in (self.headers.get("Cookie") or "")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?")[0]
        if not self._authenticated():
            self._send(200, b"<html><body>Please sign in</body></html>", "text/html")
            return
        if re.fullmatch(rf"/section/{SECTION}/syllabus", path):
            self._json(SYLLABUS)
            return
        if path == f"/api/ui/echoplayer/lessons/{LESSON_JSON}/medias/{MEDIA_JSON}/transcript":
            self._json(TRANSCRIPT_JSON)
            return
        if path == f"/lesson/{LESSON_VTT}/transcript-file":
            self._send(200, TRANSCRIPT_VTT.encode(), "text/vtt")
            return
        self._send(404, b'{"status":"not found"}', "application/json")


class MockEcho360:
    """Context manager exposing ``base_url``."""

    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockEcho360":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()