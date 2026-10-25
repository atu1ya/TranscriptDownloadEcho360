"""Command-line interface, for when the GUI is not wanted."""

from __future__ import annotations

import argparse
import sys
import threading

from . import browser, session as session_store
from .core import (
    DEFAULT_HOST,
    DownloadResult,
    Echo360Client,
    Echo360Error,
    download_transcripts,
    parse_cookie_header,
    split_section_url,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m echo360dl",
        description="Batch-download Echo360 lecture transcripts. "
                    "Run with no arguments for the GUI.",
    )
    parser.add_argument("section", help="section URL or UUID of the unit")
    parser.add_argument("-o", "--out", default="transcripts", help="output folder")
    parser.add_argument("-f", "--format", default="txt",
                        help="comma-separated formats: txt,vtt,srt,json")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="Echo360 host, if the section is a bare UUID")
    parser.add_argument("--cookies", help="cookie string (default: saved session)")
    parser.add_argument("--login", action="store_true",
                        help="open a browser to log in, then save the session")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--list", action="store_true",
                        help="list the lectures and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        host, section = split_section_url(args.section, args.host)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cookies: dict[str, str] = {}
    if args.cookies:
        cookies = parse_cookie_header(args.cookies)
    elif args.login:
        try:
            cookies = browser.login(f"{host}/section/{section}/home",
                                    on_status=lambda msg: print(msg))
            session_store.save(cookies, host)
        except Echo360Error as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    else:
        cookies, _, _ = session_store.load()
        if not cookies:
            print("error: no saved session -- pass --login or --cookies",
                  file=sys.stderr)
            return 1

    client = Echo360Client(host=host, cookies=cookies)
    try:
        lessons = client.fetch_lessons(section)
    except Echo360Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{len(lessons)} lecture(s) in {host}/section/{section}")
    if args.list:
        for lesson in lessons:
            print(f"  {lesson.index:02d}  {lesson.display_date or '----------'}  {lesson.title}")
        return 0

    formats = [part.strip() for part in args.format.split(",") if part.strip()]
    failures = 0

    def report(result: DownloadResult) -> None:
        nonlocal failures
        if result.skipped and not result.paths:
            print(f"  {result.lesson.index:02d} skipped")
        elif result.skipped:
            print(f"  {result.lesson.index:02d} already downloaded")
        elif result.ok:
            print(f"  {result.lesson.index:02d} {result.lesson.title}: "
                  f"{len(result.paths)} file(s)")
        else:
            failures += 1
            print(f"  {result.lesson.index:02d} {result.lesson.title}: {result.error}")

    download_transcripts(client, lessons, args.out, formats,
                         overwrite=args.overwrite, workers=args.workers,
                         stop=threading.Event(), on_progress=report)
    print(f"saved to {args.out}" + (f" ({failures} failed)" if failures else ""))
    return 1 if failures else 0