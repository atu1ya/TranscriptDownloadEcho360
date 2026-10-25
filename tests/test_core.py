"""Offline tests: parsing helpers plus a full download against a mock server."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from echo360dl.core import (  # noqa: E402
    AuthError,
    Echo360Client,
    Lesson,
    Transcript,
    download_transcripts,
    parse_cookie_header,
    parse_syllabus,
    parse_transcript_body,
    safe_filename,
    split_section_url,
)
from tests.mock_echo360 import (  # noqa: E402
    LESSON_JSON,
    LESSON_NONE,
    LESSON_VTT,
    SECTION,
    MockEcho360,
)

COOKIES = {"ECHO_JWT": "test-token"}


def read(directory: str, name: str) -> str:
    with open(os.path.join(directory, name), encoding="utf-8") as handle:
        return handle.read()


class HelperTests(unittest.TestCase):
    def test_section_url_forms(self):
        host, section = split_section_url(
            "https://echo360.net.au/section/11111111-2222-3333-4444-555555555555/home"
        )
        self.assertEqual(host, "https://echo360.net.au")
        self.assertEqual(section, "11111111-2222-3333-4444-555555555555")

        host, section = split_section_url(
            "11111111-2222-3333-4444-555555555555", "https://echo360.org.au"
        )
        self.assertEqual(host, "https://echo360.org.au")

        with self.assertRaises(ValueError):
            split_section_url("https://echo360.net.au/home")

    def test_cookie_header(self):
        jar = parse_cookie_header('Cookie: ECHO_JWT=abc.def; other="1"; junk')
        self.assertEqual(jar["ECHO_JWT"], "abc.def")
        self.assertEqual(jar["other"], "1")
        self.assertNotIn("junk", jar)

    def test_live_syllabus_shape(self):
        """A trimmed capture of a real echo360.net.au syllabus response.

        A scheduled capture's lesson id is not a UUID but a composite
        ``G_<capture>_<section>_<start>_<end>``; requiring a UUID here used
        to drop every timetabled lecture and find nothing at all.
        """
        section = "0e2e7ae7-75b5-4ea2-877e-750e2da38830"
        scheduled = (f"G_ee0be9bd-53e8-4146-a2da-b46b8efef1f3_{section}"
                     "_2026-07-21T15:58:00.000_2026-07-21T17:52:00.000")
        payload = {"status": "ok", "message": "", "data": [
            {"type": "SyllabusLessonType", "lesson": {
                "lesson": {
                    "id": scheduled,
                    "sectionId": section,
                    "name": "CITS2211 - 21 Jul 2026, 04:00 - Lecture",
                    "displayName": "CITS2211 - 21 Jul 2026, 04:00 - Lecture",
                    "timing": {"start": "2026-07-21T15:58:00.000",
                               "end": "2026-07-21T17:52:00.000"},
                },
                "medias": [{"id": "928ad1ca-f347-4c27-b309-dc70e3ff52bb",
                            "mediaType": "Video", "isAvailable": True,
                            "title": "CITS2211 - 21 Jul 2026 - Lecture"}],
                "hasVideo": True,
                "startTimeUTC": "2026-07-21T07:58:00.000Z",
                "endTimeUTC": "2026-07-21T09:52:00.000Z",
            }},
            {"type": "SyllabusLessonType", "lesson": {
                "lesson": {
                    "id": "2201a071-043d-4714-a4f1-46f18dc7eb1b",
                    "sectionId": section,
                    "name": "CITS2211 - Lecture 2 from 2025 - With Audio",
                    "displayName": "CITS2211 - Lecture 2 from 2025 - With Audio",
                },
                "medias": [{"id": "325c022d-11e0-4213-bfde-3527bbcc5123",
                            "mediaType": "Video", "isAvailable": True}],
                "hasVideo": True,
                "startTimeUTC": "2026-07-28T07:00:00.000Z",
            }},
        ]}

        lessons = parse_syllabus(payload)
        self.assertEqual(len(lessons), 2)
        first, second = lessons
        self.assertEqual(first.lesson_id, scheduled)
        self.assertEqual(first.title, "CITS2211 - 21 Jul 2026, 04:00 - Lecture")
        self.assertEqual(first.display_date, "2026-07-21")
        self.assertEqual(first.media_ids, ["928ad1ca-f347-4c27-b309-dc70e3ff52bb"])
        self.assertEqual(second.lesson_id, "2201a071-043d-4714-a4f1-46f18dc7eb1b")
        self.assertEqual([lesson.index for lesson in lessons], [1, 2])

    def test_safe_filename(self):
        self.assertEqual(safe_filename('L1: a/b?c*  "x"'), "L1- a-b-c- -x")
        self.assertEqual(safe_filename("   ..."), "transcript")

    def test_windows_reserved_names_get_a_prefix(self):
        # "AUX.txt" cannot be created on Windows at all.
        self.assertEqual(safe_filename("AUX"), "_AUX")
        self.assertEqual(safe_filename("con.txt"), "_con.txt")
        self.assertEqual(safe_filename("Aux Lecture"), "Aux Lecture")

    def test_vtt_roundtrip(self):
        cues = parse_transcript_body(
            "WEBVTT\n\n00:01:02.500 --> 00:01:05.000\nHello <b>there</b>\n", "text/vtt"
        )
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "Hello there")
        self.assertEqual(cues[0].start_ms, 62_500)
        srt = Transcript(cues=cues).as_srt()
        self.assertIn("00:01:02,500 --> 00:01:05,000", srt)

    def test_untimed_transcript_has_no_vtt_cues(self):
        transcript = Transcript(cues=parse_transcript_body("just a wall of text"))
        self.assertFalse(transcript.timed)
        self.assertEqual(transcript.as_text().strip(), "just a wall of text")

    def test_filename_template(self):
        lesson = Lesson(lesson_id="x", title="Lecture 1: Intro", index=3)
        self.assertEqual(lesson.filename("txt"), "03 - undated - Lecture 1- Intro.txt")


class LiveishTests(unittest.TestCase):
    """Exercises the client against the mock Echo360 server."""

    def setUp(self):
        self.server = MockEcho360().__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = Echo360Client(host=self.server.base_url, cookies=COOKIES, delay=0)

    def test_rejects_unauthenticated_session(self):
        anonymous = Echo360Client(host=self.server.base_url, cookies={}, delay=0)
        with self.assertRaises(AuthError):
            anonymous.fetch_lessons(SECTION)

    def test_lessons_are_found_and_ordered_by_date(self):
        lessons = self.client.fetch_lessons(SECTION)
        self.assertEqual([lesson.lesson_id for lesson in lessons],
                         [LESSON_JSON, LESSON_VTT, LESSON_NONE])
        self.assertEqual([lesson.index for lesson in lessons], [1, 2, 3])
        self.assertEqual(lessons[0].display_date, "2026-03-02")
        self.assertTrue(lessons[0].media_ids)

    def test_download_all_formats(self):
        lessons = self.client.fetch_lessons(SECTION)
        with tempfile.TemporaryDirectory() as out:
            results = download_transcripts(
                self.client, lessons, out, ("txt", "vtt", "srt", "json"), workers=2
            )
            self.assertEqual(len(results), 3)

            # Lesson 1: JSON cue list on the media endpoint.
            first = results[0]
            self.assertTrue(first.ok, first.error)
            self.assertEqual(len(first.paths), 4)
            text = read(out, lessons[0].filename("txt"))
            self.assertIn("Kirchhoff's laws", text)
            self.assertNotIn("<i>", text)
            vtt = read(out, lessons[0].filename("vtt"))
            self.assertTrue(vtt.startswith("WEBVTT"))
            self.assertIn("00:00:04.200 --> 00:00:09.100", vtt)

            # Lesson 2: only the transcript-file fallback answers.
            second = results[1]
            self.assertTrue(second.ok, second.error)
            self.assertIn("Thevenin", read(out, lessons[1].filename("txt")))

            # Lesson 3: no transcript anywhere -> reported, not crashed.
            third = results[2]
            self.assertFalse(third.ok)
            self.assertIn("no transcript", third.error)

            # Re-running skips what is already on disk.
            again = download_transcripts(self.client, lessons[:1], out, ("txt",))
            self.assertTrue(again[0].skipped)

    def test_stop_event_cancels(self):
        import threading

        lessons = self.client.fetch_lessons(SECTION)
        stop = threading.Event()
        stop.set()
        with tempfile.TemporaryDirectory() as out:
            results = download_transcripts(self.client, lessons, out, ("txt",), stop=stop)
        self.assertTrue(all(result.skipped for result in results))
        self.assertTrue(all(not result.paths for result in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)