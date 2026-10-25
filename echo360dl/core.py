"""Core Echo360 (EchoVideo) transcript retrieval.

No GUI, no browser automation -- just an authenticated HTTP session, lesson
discovery and transcript fetching/conversion. Everything here is importable
from scripts and exercised by the tests against a mock server.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Sequence
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

DEFAULT_HOST = "https://echo360.net.au"
AUTH_COOKIE = "ECHO_JWT"
# Echo360 runs on the Play Framework, and the JWT alone does not get you in:
# the server-side session rides in PLAY_SESSION. It is HttpOnly (as is
# ECHO_JWT on most tenants), so document.cookie cannot see it -- it has to
# come from the Cookie: request header or the Application tab.
SESSION_COOKIE = "PLAY_SESSION"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TEXT_KEYS = ("content", "text", "caption", "transcriptText", "cueText", "value")
_START_KEYS = ("startMs", "startTimeMs", "start", "startTime", "beginMs", "from")
_END_KEYS = ("endMs", "endTimeMs", "end", "endTime", "to")
_TIME_KEYS = (
    "startTimeUTC", "startTime", "captureStartTime", "createdAt", "dateTime",
    "lessonStartTime",
)


class Echo360Error(Exception):
    """Base class for errors raised by this package."""


class AuthError(Echo360Error):
    """The session cookies are missing, expired or rejected."""


class NotFoundError(Echo360Error):
    """The requested lesson/section/transcript does not exist."""


class NoTranscriptError(Echo360Error):
    """Echo360 has no transcript for this lesson."""


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------

@dataclass
class Lesson:
    lesson_id: str
    title: str = ""
    media_ids: list[str] = field(default_factory=list)
    starts_at: datetime | None = None
    index: int = 0

    @property
    def display_date(self) -> str:
        return self.starts_at.strftime("%Y-%m-%d") if self.starts_at else ""

    def filename(self, extension: str, template: str = "{index:02d} - {date} - {title}") -> str:
        stem = template.format(
            index=self.index,
            date=self.display_date or "undated",
            title=self.title or self.lesson_id,
            lesson_id=self.lesson_id,
        )
        return f"{safe_filename(stem)}.{extension.lstrip('.')}"


@dataclass
class Cue:
    text: str
    start_ms: int | None = None
    end_ms: int | None = None

    @property
    def timed(self) -> bool:
        return self.start_ms is not None


@dataclass
class Transcript:
    cues: list[Cue]
    source_url: str = ""
    raw_body: str = ""

    @property
    def timed(self) -> bool:
        return any(cue.timed for cue in self.cues)

    def as_text(self, width: int = 0) -> str:
        parts: list[str] = []
        for cue in self.cues:
            line = cue.text.strip()
            if line and (not parts or parts[-1] != line):
                parts.append(line)
        body = " ".join(parts)
        body = re.sub(r"\s+", " ", body).strip()
        if width > 0:
            import textwrap

            body = "\n".join(textwrap.wrap(body, width=width)) if body else body
        return body + "\n" if body else ""

    def as_vtt(self) -> str:
        lines = ["WEBVTT", ""]
        for cue in self._timed_cues():
            lines.append(f"{_stamp(cue.start_ms, '.')} --> {_stamp(cue.end_ms, '.')}")
            lines.append(cue.text.strip())
            lines.append("")
        return "\n".join(lines)

    def as_srt(self) -> str:
        lines: list[str] = []
        for number, cue in enumerate(self._timed_cues(), start=1):
            lines.append(str(number))
            lines.append(f"{_stamp(cue.start_ms, ',')} --> {_stamp(cue.end_ms, ',')}")
            lines.append(cue.text.strip())
            lines.append("")
        return "\n".join(lines)

    def as_json(self) -> str:
        payload = [
            {"start_ms": cue.start_ms, "end_ms": cue.end_ms, "text": cue.text}
            for cue in self.cues
        ]
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    def render(self, fmt: str) -> str:
        fmt = fmt.lower().lstrip(".")
        if fmt == "txt":
            return self.as_text()
        if fmt == "vtt":
            return self.as_vtt()
        if fmt == "srt":
            return self.as_srt()
        if fmt == "json":
            return self.as_json()
        raise ValueError(f"unknown transcript format: {fmt!r}")

    def _timed_cues(self) -> list[Cue]:
        """Cues with usable timings, gap-filling any missing end time."""
        timed = [cue for cue in self.cues if cue.timed]
        for position, cue in enumerate(timed):
            if cue.end_ms is None:
                following = timed[position + 1].start_ms if position + 1 < len(timed) else None
                cue.end_ms = following if following is not None else (cue.start_ms or 0) + 4000
        return timed


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

# Device names Windows refuses to use as a filename, with or without extension.
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$"} | {
    f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in range(1, 10)
}


def safe_filename(name: str, limit: int = 120) -> str:
    """Make ``name`` safe on Windows, macOS and Linux alike."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    cleaned = cleaned[:limit].strip(" .-")
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned or "transcript"


def _stamp(ms: int | None, decimal: str) -> str:
    total = max(int(ms or 0), 0)
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal}{millis:03d}"


_IDENTITY_CLAIMS = ("email", "userName", "username", "preferred_username",
                    "userId", "user_id", "personId", "sub", "name")


def decode_jwt(token: str) -> dict:
    """Read a JWT's payload claims. Diagnostics only -- no signature check."""
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        import base64

        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", "replace"))
    except Exception:
        return {}


def describe_jwt(token: str) -> str:
    """One line on whether an ``ECHO_JWT`` is usable, and who it belongs to.

    Echo360 hands out a token before you sign in, so the cookie being
    present says nothing: this reads the token to tell an anonymous or
    expired one from a real login.
    """
    claims = decode_jwt(token)
    if not claims:
        return ("the ECHO_JWT is not a readable token -- it was probably "
                "truncated when copied")

    notes: list[str] = []
    expiry = claims.get("exp")
    if isinstance(expiry, (int, float)):
        when = datetime.fromtimestamp(expiry, tz=timezone.utc)
        minutes = (when - datetime.now(timezone.utc)).total_seconds() / 60
        if minutes <= 0:
            notes.append(f"it expired {abs(minutes):.0f} minute(s) ago "
                         f"({when:%Y-%m-%d %H:%M UTC})")
        else:
            notes.append(f"it is valid for another {minutes:.0f} minute(s)")

    identity = next((str(claims[key]) for key in _IDENTITY_CLAIMS
                     if claims.get(key) not in (None, "", "anonymous")), "")
    if identity:
        notes.append(f"it belongs to {identity}")
    else:
        notes.append("it carries no user -- this is the anonymous token "
                     "Echo360 issues before you sign in, so the cookies were "
                     "copied from a page where you were not logged in")
    return "; ".join(notes)


_COOKIE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def parse_cookie_header(blob: str) -> dict[str, str]:
    """Parse pasted cookies, however the browser handed them over.

    Accepts a ``Cookie:`` request header or ``document.cookie`` output
    (``name=value; name=value``) and DevTools' Application tab, whose rows
    copy as ``name<TAB>value``.
    """
    jar: dict[str, str] = {}
    blob = blob.strip()
    if blob.lower().startswith("cookie:"):
        blob = blob.split(":", 1)[1]
    for chunk in re.split(r"[;\n]", blob):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, separator, value = chunk.partition("=")
        if not (separator and _COOKIE_NAME_RE.match(name.strip())):
            # Not "name=value", so try the table form. Values are base64-ish
            # and never contain whitespace, which makes this unambiguous.
            parts = chunk.split(None, 1)
            if len(parts) != 2 or not _COOKIE_NAME_RE.match(parts[0]):
                continue
            name, value = parts
        name, value = name.strip(), value.strip().strip('"')
        if name:
            jar[name] = value
    return jar


def split_section_url(url_or_uuid: str, default_host: str = DEFAULT_HOST) -> tuple[str, str]:
    """Return ``(host, section_uuid)`` from a section URL or a bare UUID."""
    value = (url_or_uuid or "").strip()
    if not value:
        raise ValueError("no section URL or UUID given")
    if _UUID_RE.match(value):
        return default_host.rstrip("/"), value
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError(f"cannot read a host out of {url_or_uuid!r}")
    host = f"{parsed.scheme}://{parsed.netloc}"
    match = re.search(
        r"/(?:section|courses?)/([0-9a-fA-F-]{36})", parsed.path
    ) or re.search(r"([0-9a-fA-F-]{36})", parsed.path + "?" + (parsed.query or ""))
    if not match:
        raise ValueError(
            f"no section UUID in {url_or_uuid!r} -- open the unit in Echo360 and "
            "copy the address of its home/class-list page"
        )
    return host, match.group(1)


def _iter_dicts(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item)


def _looks_like_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value.strip()))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for pattern in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _collect_media_ids(node: Any) -> list[str]:
    found: list[str] = []
    for candidate in _iter_dicts(node):
        for key in ("mediaId", "id"):
            value = candidate.get(key)
            if key == "id" and candidate.get("_type") not in (None, "Media", "EchoMedia"):
                continue
            if _looks_like_id(value) and value not in found:
                if key == "mediaId" or "media" in json.dumps(
                    sorted(candidate.keys())
                ).lower() or "duration" in candidate:
                    found.append(value)
    return found


def parse_syllabus(payload: Any) -> list[Lesson]:
    """Pull the lesson list out of a ``/section/<uuid>/syllabus`` response.

    Echo360 wraps lessons differently between tenants and releases, so this
    walks the whole document looking for lesson-shaped objects rather than
    trusting one fixed path.
    """
    lessons: list[Lesson] = []
    seen: set[str] = set()

    for node in _iter_dicts(payload):
        wrapper = node.get("lesson")
        if not isinstance(wrapper, dict):
            continue
        inner = wrapper.get("lesson")
        core = inner if isinstance(inner, dict) else wrapper
        lesson_id = core.get("id") or wrapper.get("id")
        if not _looks_like_id(lesson_id) or lesson_id in seen:
            continue
        seen.add(lesson_id)

        title = ""
        for key in ("displayName", "name", "title", "lessonName"):
            value = core.get(key) or wrapper.get(key)
            if isinstance(value, str) and value.strip():
                title = value.strip()
                break

        starts_at = None
        for key in _TIME_KEYS:
            starts_at = _parse_time(core.get(key) or wrapper.get(key))
            if starts_at:
                break

        media_ids: list[str] = []
        for source in (wrapper.get("medias"), core.get("medias"),
                       wrapper.get("video"), core.get("video"),
                       wrapper.get("media"), core.get("media")):
            for media_id in _collect_media_ids(source):
                if media_id not in media_ids:
                    media_ids.append(media_id)

        lessons.append(Lesson(lesson_id=lesson_id, title=title,
                              media_ids=media_ids, starts_at=starts_at))

    lessons.sort(key=lambda item: (item.starts_at is None,
                                   item.starts_at or datetime.min.replace(tzinfo=timezone.utc)))
    for position, lesson in enumerate(lessons, start=1):
        lesson.index = position
        if not lesson.title:
            lesson.title = f"Lesson {position}"
    return lessons


# --------------------------------------------------------------------------
# transcript parsing
# --------------------------------------------------------------------------

def parse_vtt_or_srt(body: str) -> list[Cue]:
    cues: list[Cue] = []
    arrow = re.compile(
        r"(\d{1,2}:)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
        r"(\d{1,2}:)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
    )
    for block in re.split(r"\r?\n\s*\r?\n", body.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_at = next((i for i, line in enumerate(lines) if arrow.search(line)), None)
        if timing_at is None:
            continue
        match = arrow.search(lines[timing_at])
        assert match is not None
        start = _hms_to_ms(match.group(1), match.group(2), match.group(3), match.group(4))
        end = _hms_to_ms(match.group(5), match.group(6), match.group(7), match.group(8))
        text = " ".join(line.strip() for line in lines[timing_at + 1:]).strip()
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            cues.append(Cue(text=text, start_ms=start, end_ms=end))
    return cues


def _hms_to_ms(hours: str | None, minutes: str, seconds: str, fraction: str) -> int:
    millis = int(fraction.ljust(3, "0")[:3])
    hour_value = int((hours or "0").rstrip(":") or 0)
    return hour_value * 3_600_000 + int(minutes) * 60_000 + int(seconds) * 1000 + millis


def _cue_from_dict(node: dict) -> Cue | None:
    text = next((node[key] for key in _TEXT_KEYS
                 if isinstance(node.get(key), str) and node[key].strip()), None)
    if text is None:
        return None
    start = next((node[key] for key in _START_KEYS
                  if isinstance(node.get(key), (int, float))), None)
    end = next((node[key] for key in _END_KEYS
                if isinstance(node.get(key), (int, float))), None)
    if start is None:
        for key in _START_KEYS:
            parsed = _seconds_like(node.get(key))
            if parsed is not None:
                start = parsed
                break
    return Cue(
        text=re.sub(r"<[^>]+>", "", str(text)).strip(),
        start_ms=_to_ms(start),
        end_ms=_to_ms(end),
    )


def _seconds_like(value: Any) -> float | None:
    if isinstance(value, str):
        match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?([.,]\d+)?$", value.strip())
        if match:
            a, b, c, frac = match.groups()
            if c is None:
                total = int(a) * 60 + int(b)
            else:
                total = int(a) * 3600 + int(b) * 60 + int(c)
            return total + (float(frac.replace(",", ".")) if frac else 0.0)
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_ms(value: Any) -> int | None:
    if value is None:
        return None
    number = float(value)
    # Sub-second floats are seconds; large integers are already milliseconds.
    if isinstance(value, float) and number < 10_000:
        return int(round(number * 1000))
    if isinstance(value, int) and number < 10_000 and number != int(number):
        return int(round(number * 1000))
    return int(round(number))


def parse_transcript_payload(payload: Any) -> list[Cue]:
    """Find the cue list inside a transcript JSON document."""
    best: list[Cue] = []
    for node in _iter_dicts(payload):
        for value in node.values():
            if not isinstance(value, list) or not value:
                continue
            cues = [cue for cue in (_cue_from_dict(item) for item in value
                                    if isinstance(item, dict)) if cue and cue.text]
            if len(cues) > len(best):
                best = cues
    if best:
        return best
    # Some tenants return the whole transcript as one blob of text.
    for node in _iter_dicts(payload):
        for key in _TEXT_KEYS + ("transcript", "body"):
            value = node.get(key)
            if isinstance(value, str) and len(value.strip()) > 40:
                if value.lstrip().upper().startswith("WEBVTT"):
                    return parse_vtt_or_srt(value)
                return [Cue(text=value.strip())]
    return []


def parse_transcript_body(body: str, content_type: str = "") -> list[Cue]:
    body = (body or "").strip()
    if not body:
        return []
    looks_json = "json" in content_type.lower() or body[0] in "{["
    if looks_json:
        try:
            return parse_transcript_payload(json.loads(body))
        except json.JSONDecodeError:
            pass
    if body.upper().startswith("WEBVTT") or "-->" in body:
        return parse_vtt_or_srt(body)
    return [Cue(text=line.strip()) for line in body.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

class Echo360Client:
    """Authenticated read-only client for one Echo360 tenant."""

    def __init__(self, host: str = DEFAULT_HOST, cookies: dict[str, str] | None = None,
                 timeout: float = 30.0, retries: int = 3, delay: float = 0.3):
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.cookies = dict(cookies or {})
        self.timeout = timeout
        self.retries = max(1, retries)
        self.delay = delay
        self._local = threading.local()
        self._cookie_lock = threading.Lock()
        self._cookie_version = 1
        # Called when Echo360 hands back a refreshed cookie, so callers can
        # re-save the session.
        self.on_cookies_changed: Callable[[dict[str, str]], None] | None = None

    # -- plumbing ---------------------------------------------------------
    @property
    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            })
            self._local.session = session
            self._local.cookie_version = 0
        with self._cookie_lock:
            version, cookies = self._cookie_version, dict(self.cookies)
        # Only rewrite the jar when the cookies actually changed: Echo360
        # rotates its JWT and we must keep the fresh one, not stamp the
        # original back over it before every request.
        if getattr(self._local, "cookie_version", 0) != version:
            session.cookies.clear()
            for name, value in cookies.items():
                session.cookies.set(name, value)
            self._local.cookie_version = version
        return session

    def _absorb_cookies(self, session: requests.Session) -> None:
        """Pick up any cookie Echo360 refreshed during the request."""
        fresh = session.cookies.get_dict()
        if not fresh:
            return
        with self._cookie_lock:
            changed = {name: value for name, value in fresh.items()
                       if self.cookies.get(name) != value}
            if not changed:
                return
            self.cookies.update(changed)
            self._cookie_version += 1
            self._local.cookie_version = self._cookie_version
            snapshot = dict(self.cookies)
        if self.on_cookies_changed:
            self.on_cookies_changed(snapshot)

    @property
    def authenticated(self) -> bool:
        return AUTH_COOKIE in self.cookies and bool(self.cookies[AUTH_COOKIE])

    def set_cookies(self, cookies: dict[str, str]) -> None:
        with self._cookie_lock:
            self.cookies = dict(cookies or {})
            self._cookie_version += 1

    def _get(self, path: str, *, referer: str | None = None) -> requests.Response:
        url = path if path.startswith("http") else f"{self.host}{path}"
        headers = {"Referer": referer} if referer else {}
        last: Exception | None = None
        for attempt in range(self.retries):
            session = self.session
            try:
                response = session.get(url, headers=headers, timeout=self.timeout,
                                       allow_redirects=True)
            except requests.RequestException as exc:
                last = exc
                time.sleep(self.delay * (2 ** attempt))
                continue
            if response.status_code in (401, 403) or _is_login_page(response):
                raise AuthError(self._auth_error(url, response))
            self._absorb_cookies(session)
            if response.status_code == 404:
                raise NotFoundError(f"404 from {url}")
            if response.status_code >= 500:
                last = Echo360Error(f"{response.status_code} from {url}")
                time.sleep(self.delay * (2 ** attempt))
                continue
            response.raise_for_status()
            if self.delay:
                time.sleep(self.delay)
            return response
        raise Echo360Error(f"giving up on {url}: {last}")

    def _auth_error(self, url: str, response: requests.Response) -> str:
        """Say *why* Echo360 said no, since 'log in again' often is not it."""
        sent = sorted(self.cookies)
        lines = [
            "Echo360 rejected the session "
            f"(HTTP {response.status_code} from {url}).",
        ]
        token = self.cookies.get(AUTH_COOKIE)
        if token:
            lines += ["", f"About the {AUTH_COOKIE} that was sent: "
                          f"{describe_jwt(token)}."]

        causes: list[str] = []
        if SESSION_COOKIE not in self.cookies:
            causes.append(
                f"{SESSION_COOKIE} was not sent, and Echo360 needs it -- the "
                "JWT on its own is not a login. It is HttpOnly, so "
                "document.cookie cannot see it: copy the Cookie: request "
                "header from the Network tab, which carries every cookie."
            )
        causes += [
            "The cookies are for a different Echo360 host. This unit lives "
            f"on {self.host} -- copy the cookies from that address.",
            "The browser was not actually signed in when the cookies were "
            "copied, so Echo360 handed it an anonymous token.",
            f"Only part of the cookie line was copied. {AUTH_COOKIE} and "
            f"{SESSION_COOKIE} values are long; check nothing was cut off.",
        ]
        lines += ["", "The usual causes, in order:"]
        lines += [f"  * {cause}" for cause in causes]
        lines += ["", f"Cookies sent: {', '.join(sent) or 'none'}"]
        if response.url != url:
            lines.append(f"Ended up at: {response.url}")
        return "\n".join(lines)

    # -- api --------------------------------------------------------------
    def fetch_lessons(self, section_uuid: str) -> list[Lesson]:
        response = self._get(
            f"/section/{section_uuid}/syllabus",
            referer=f"{self.host}/section/{section_uuid}/home",
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise Echo360Error(
                "the syllabus endpoint returned HTML rather than JSON -- the "
                "session is probably not valid for this tenant"
            ) from exc
        lessons = parse_syllabus(payload)
        if not lessons:
            raise Echo360Error(
                "no lessons found in the syllabus response (is this the right "
                "section, and can you see classes in the browser?)"
            )
        return lessons

    def transcript_urls(self, lesson: Lesson) -> list[str]:
        """Candidate transcript URLs, most specific first.

        ``format=text`` is what the Echo360 player itself requests; vtt is
        tried first only because it carries timings, which text does not.
        """
        urls: list[str] = []
        for media_id in lesson.media_ids:
            base = f"/api/ui/echoplayer/lessons/{lesson.lesson_id}/medias/{media_id}"
            urls.append(f"{base}/transcript-file?format=vtt")
            urls.append(f"{base}/transcript-file?format=text")
            urls.append(f"{base}/transcript")
        player = f"/api/ui/echoplayer/lessons/{lesson.lesson_id}"
        urls.append(f"{player}/transcript-file?format=vtt")
        urls.append(f"{player}/transcript-file?format=text")
        urls.append(f"{player}/transcript")
        urls.append(f"/lesson/{lesson.lesson_id}/transcript-file?format=vtt")
        urls.append(f"/lesson/{lesson.lesson_id}/transcript-file?format=text")
        urls.append(f"/lesson/{lesson.lesson_id}/transcript")
        return urls

    def fetch_transcript(self, lesson: Lesson) -> Transcript:
        problems: list[str] = []
        for path in self.transcript_urls(lesson):
            try:
                response = self._get(
                    path, referer=f"{self.host}/lesson/{lesson.lesson_id}/classroom"
                )
            except NotFoundError:
                problems.append(f"{path}: 404")
                continue
            except AuthError:
                raise
            except Echo360Error as exc:
                problems.append(f"{path}: {exc}")
                continue
            cues = parse_transcript_body(
                response.text, response.headers.get("Content-Type", "")
            )
            if cues:
                return Transcript(cues=cues, source_url=response.url,
                                  raw_body=response.text)
            problems.append(f"{path}: no cues in response")
        raise NoTranscriptError(
            "no transcript available for this lesson (" + "; ".join(problems) + ")"
        )


def _is_login_page(response: requests.Response) -> bool:
    if response.status_code != 200:
        return False
    final = response.url.lower()
    if any(marker in final for marker in ("/login", "/sso", "saml", "okta", "adfs")):
        return True
    body = response.text[:2000].lower()
    return "<html" in body and ("sign in" in body or "login" in body) and \
        "json" not in response.headers.get("Content-Type", "").lower()


# --------------------------------------------------------------------------
# batch download
# --------------------------------------------------------------------------

@dataclass
class DownloadResult:
    lesson: Lesson
    paths: list[str] = field(default_factory=list)
    skipped: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def download_transcripts(
    client: Echo360Client,
    lessons: Sequence[Lesson],
    out_dir: str,
    formats: Iterable[str] = ("txt",),
    *,
    overwrite: bool = False,
    workers: int = 3,
    stop: threading.Event | None = None,
    on_progress: Callable[[DownloadResult], None] | None = None,
) -> list[DownloadResult]:
    """Fetch and write transcripts for ``lessons``. Returns one result each."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    formats = [fmt.lower().lstrip(".") for fmt in formats] or ["txt"]
    os.makedirs(out_dir, exist_ok=True)
    stop = stop or threading.Event()

    def one(lesson: Lesson) -> DownloadResult:
        result = DownloadResult(lesson=lesson)
        if stop.is_set():
            result.skipped = True
            result.error = "cancelled"
            return result
        targets = {fmt: os.path.join(out_dir, lesson.filename(fmt)) for fmt in formats}
        if not overwrite and all(os.path.exists(path) for path in targets.values()):
            result.skipped = True
            result.paths = list(targets.values())
            return result
        try:
            transcript = client.fetch_transcript(lesson)
        except Echo360Error as exc:
            result.error = str(exc)
            return result
        for fmt, path in targets.items():
            if (fmt in ("vtt", "srt")) and not transcript.timed:
                result.error = (result.error or "") + \
                    f"no timings available, skipped .{fmt}; "
                continue
            body = transcript.render(fmt)
            if not body.strip():
                continue
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(body)
            result.paths.append(path)
        if not result.paths and not result.error:
            result.error = "transcript was empty"
        return result

    results: list[DownloadResult] = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for result in pool.map(one, lessons):
            results.append(result)
            if on_progress:
                on_progress(result)
    return results