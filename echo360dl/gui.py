"""Tkinter front end for batch-downloading Echo360 lecture transcripts."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from typing import Any

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ImportError as exc:  # pragma: no cover - platform dependent
    raise SystemExit(
        "This tool needs Tkinter, which is missing from your Python install.\n"
        "  Windows/macOS: reinstall Python from python.org (Tkinter is included)\n"
        "  Debian/Ubuntu: sudo apt install python3-tk\n"
        "  Fedora:        sudo dnf install python3-tkinter\n"
        f"({exc})"
    )

from . import bootstrap, browser, session as session_store
from .core import (
    DEFAULT_HOST,
    AuthError,
    DownloadResult,
    Echo360Client,
    Echo360Error,
    Lesson,
    describe_jwt,
    parse_cookie_header,
    split_section_url,
)
from .core import download_transcripts, AUTH_COOKIE

CHECKED, UNCHECKED = "☑", "☐"
FORMATS = ("txt", "vtt", "srt", "json")


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.master.title("Echo360 Transcript Downloader")
        self.master.minsize(880, 620)
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.client = Echo360Client()
        self.client.on_cookies_changed = lambda jar: self._post("refresh", jar)
        self.lessons: list[Lesson] = []
        self.checked: set[str] = set()
        self.stop_flag = threading.Event()
        self.busy = False

        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.section_var = tk.StringVar()
        self.outdir_var = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "Echo360Transcripts")
        )
        self.filter_var = tk.StringVar()
        self.session_var = tk.StringVar(value="Not logged in")
        self.status_var = tk.StringVar(value="Ready.")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.workers_var = tk.IntVar(value=3)
        self.format_vars = {
            name: tk.BooleanVar(value=(name == "txt")) for name in FORMATS
        }

        self._build()
        self._restore_session()
        self.after(100, self._drain_events)

    # ---------------------------------------------------------------- build
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=3)
        self.rowconfigure(4, weight=1)

        # --- session ---
        session = ttk.LabelFrame(self, text="1. Echo360 session", padding=8)
        session.grid(row=0, column=0, sticky="ew")
        session.columnconfigure(1, weight=1)
        ttk.Label(session, textvariable=self.session_var, width=34).grid(
            row=0, column=0, sticky="w"
        )
        buttons = ttk.Frame(session)
        buttons.grid(row=0, column=1, sticky="e")
        ttk.Button(buttons, text="Log in with browser",
                   command=self.on_browser_login).grid(row=0, column=0, padx=2)
        ttk.Button(buttons, text="Paste cookies…",
                   command=self.on_paste_cookies).grid(row=0, column=1, padx=2)
        ttk.Button(buttons, text="Forget session",
                   command=self.on_forget_session).grid(row=0, column=2, padx=2)

        # --- course ---
        course = ttk.LabelFrame(self, text="2. Unit", padding=8)
        course.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        course.columnconfigure(1, weight=1)
        ttk.Label(course, text="Section URL or UUID:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(course, textvariable=self.section_var)
        entry.grid(row=0, column=1, sticky="ew", padx=6)
        entry.bind("<Return>", lambda _event: self.on_fetch())
        self.fetch_button = ttk.Button(course, text="Fetch lessons", command=self.on_fetch)
        self.fetch_button.grid(row=0, column=2)
        ttk.Label(
            course,
            text="e.g. https://echo360.net.au/section/0f1e2d3c-…/home  "
                 "(open the unit in Echo360 and copy the address)",
            foreground="#666",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(course, text="Echo360 host:").grid(row=2, column=0, sticky="w",
                                                     pady=(6, 0))
        ttk.Combobox(
            course, textvariable=self.host_var,
            values=["https://echo360.net.au", "https://echo360.org.au",
                    "https://echo360.org", "https://echo360.ca",
                    "https://echo360.org.uk"],
        ).grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(course, text="(only used when the box above is a bare UUID)",
                  foreground="#666").grid(row=2, column=2, sticky="w", pady=(6, 0))

        # --- lessons ---
        lessons = ttk.LabelFrame(self, text="3. Lectures", padding=8)
        lessons.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        lessons.columnconfigure(0, weight=1)
        lessons.rowconfigure(1, weight=1)

        bar = ttk.Frame(lessons)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(4, weight=1)
        ttk.Button(bar, text="Select all", command=lambda: self._set_all(True)).grid(row=0, column=0)
        ttk.Button(bar, text="Select none", command=lambda: self._set_all(False)).grid(row=0, column=1, padx=4)
        ttk.Button(bar, text="Invert", command=self._invert).grid(row=0, column=2)
        ttk.Label(bar, text="Filter:").grid(row=0, column=3, padx=(12, 4))
        filter_entry = ttk.Entry(bar, textvariable=self.filter_var, width=24)
        filter_entry.grid(row=0, column=4, sticky="w")
        self.filter_var.trace_add("write", lambda *_: self._refresh_tree())

        self.tree = ttk.Treeview(
            lessons, columns=("sel", "num", "date", "title"), show="headings",
            selectmode="none",
        )
        for column, heading, width, anchor in (
            ("sel", "", 34, "center"),
            ("num", "#", 44, "center"),
            ("date", "Date", 100, "center"),
            ("title", "Lecture", 520, "w"),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor=anchor,
                             stretch=(column == "title"))
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(lessons, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", lambda _e: None)

        # --- output ---
        output = ttk.LabelFrame(self, text="4. Download", padding=8)
        output.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="Save to:").grid(row=0, column=0, sticky="w")
        ttk.Entry(output, textvariable=self.outdir_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(output, text="Browse…", command=self.on_browse).grid(row=0, column=2)

        options = ttk.Frame(output)
        options.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(options, text="Formats:").grid(row=0, column=0, sticky="w")
        for position, name in enumerate(FORMATS):
            ttk.Checkbutton(options, text=f".{name}",
                            variable=self.format_vars[name]).grid(
                row=0, column=1 + position, padx=3)
        ttk.Checkbutton(options, text="Overwrite existing files",
                        variable=self.overwrite_var).grid(row=0, column=6, padx=(16, 4))
        ttk.Label(options, text="Parallel:").grid(row=0, column=7, padx=(16, 2))
        ttk.Spinbox(options, from_=1, to=8, width=4,
                    textvariable=self.workers_var).grid(row=0, column=8)

        actions = ttk.Frame(output)
        actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        actions.columnconfigure(2, weight=1)
        self.download_button = ttk.Button(actions, text="Download transcripts",
                                          command=self.on_download)
        self.download_button.grid(row=0, column=0)
        self.stop_button = ttk.Button(actions, text="Stop", command=self.on_stop,
                                      state="disabled")
        self.stop_button.grid(row=0, column=1, padx=6)
        self.progress = ttk.Progressbar(actions, mode="determinate")
        self.progress.grid(row=0, column=2, sticky="ew", padx=6)
        ttk.Button(actions, text="Open folder", command=self.on_open_folder).grid(row=0, column=3)

        # --- log ---
        log_frame = ttk.LabelFrame(self, text="Log", padding=4)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_box = scrolledtext.ScrolledText(log_frame, height=8, wrap="word",
                                                 state="disabled")
        self.log_box.grid(row=0, column=0, sticky="nsew")
        ttk.Label(self, textvariable=self.status_var, foreground="#444").grid(
            row=5, column=0, sticky="w", pady=(6, 0))

    # ------------------------------------------------------------- plumbing
    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message.rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _post(self, kind: str, payload: Any = None) -> None:
        self.events.put((kind, payload))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "log":
            self.log(str(payload))
        elif kind == "status":
            self.status_var.set(str(payload))
        elif kind == "session":
            self._apply_cookies(payload, source="browser")
        elif kind == "refresh":
            # Echo360 rotated a cookie mid-session; keep the fresh one so the
            # saved session does not go stale while the app is open.
            try:
                session_store.save(payload, self.client.host)
            except OSError:
                pass
        elif kind == "lessons":
            self.lessons = payload
            self.checked = {lesson.lesson_id for lesson in payload}
            self._refresh_tree()
            self.log(f"Found {len(payload)} lecture(s).")
        elif kind == "progress":
            done, total = payload
            self.progress.configure(maximum=max(total, 1), value=done)
        elif kind == "error":
            self.log(f"ERROR: {payload}")
            messagebox.showerror("Echo360 Transcript Downloader", str(payload))
        elif kind == "fallback":
            self.status_var.set("Browser login unavailable -- use pasted cookies.")
            messagebox.showwarning(
                "Browser login could not be set up",
                f"{payload}\n\nNo problem -- you can log in by pasting "
                "cookies instead. That window will open now.",
            )
            self.on_paste_cookies()
        elif kind == "idle":
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.fetch_button.configure(state=state)
        self.download_button.configure(state=state)
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _run(self, target, *args) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.stop_flag.clear()

        def wrapper() -> None:
            try:
                target(*args)
            except AuthError as exc:
                self._post("error", f"{exc}")
                self._post("status", "Session expired -- log in again.")
            except Echo360Error as exc:
                self._post("error", str(exc))
            except Exception as exc:  # last resort, keep the GUI alive
                self._post("error", f"{type(exc).__name__}: {exc}")
            finally:
                self._post("idle")

        threading.Thread(target=wrapper, daemon=True).start()

    # -------------------------------------------------------------- session
    def _restore_session(self) -> None:
        cookies, host, saved_at = session_store.load()
        if cookies:
            self.client.set_cookies(cookies)
            if host:
                self.host_var.set(host)
                self.client.host = host
            self.session_var.set(f"Logged in (saved {saved_at[:10] or '?'})")
            self.log("Reusing the saved Echo360 session.")
        else:
            self.session_var.set("Not logged in")

    def _apply_cookies(self, cookies: dict[str, str], source: str) -> None:
        if not cookies.get(AUTH_COOKIE):
            messagebox.showwarning(
                "Echo360 Transcript Downloader",
                f"No {AUTH_COOKIE} cookie found, so Echo360 will refuse these "
                "requests. Copy the cookies again while logged in to Echo360.",
            )
            return
        verdict = describe_jwt(cookies[AUTH_COOKIE])
        if "expired" in verdict or "anonymous" in verdict or "truncated" in verdict:
            # Say so now rather than after the first failed fetch.
            if not messagebox.askyesno(
                "That login does not look usable",
                f"The {AUTH_COOKIE} that was pasted: {verdict}.\n\n"
                "Echo360 will almost certainly refuse it. Sign in to Echo360, "
                "open the unit, and copy the cookies again.\n\n"
                "Use it anyway?",
            ):
                return
        self.client.set_cookies(cookies)
        self.session_var.set(f"Logged in (via {source})")
        self.status_var.set("Session ready.")
        try:
            path = session_store.save(cookies, self.client.host)
            self.log(f"Session saved to {path}")
        except OSError as exc:
            self.log(f"Could not save the session: {exc}")

    def on_browser_login(self) -> None:
        state = bootstrap.describe()
        if state == bootstrap.FROZEN:
            messagebox.showinfo("Browser login", bootstrap.frozen_text())
            return
        if state != bootstrap.READY:
            if not messagebox.askyesno("One-time setup",
                                       bootstrap.prompt_text(state)):
                return

        start = self.section_var.get().strip() or self.host_var.get().strip() or DEFAULT_HOST
        if "://" not in start:
            start = "https://" + start

        def work() -> None:
            if state != bootstrap.READY:
                self._post("status", "Setting up browser login (one time only)…")
                try:
                    bootstrap.ensure(lambda msg: self._post("log", msg))
                except Echo360Error as exc:
                    # Never dead-end: offer the login route that always works.
                    self._post("log", f"Setup did not finish: {exc}")
                    self._post("fallback", str(exc))
                    return
                self._post("log", "Browser login is ready.")
            cookies = browser.login(start, on_status=lambda msg: self._post("log", msg))
            self._post("session", cookies)

        self._run(work)

    def on_paste_cookies(self) -> None:
        """Guided cookie entry, for people who have never opened DevTools."""
        window = tk.Toplevel(self)
        window.title("Paste Echo360 cookies")
        window.transient(self.master)
        window.grab_set()
        window.columnconfigure(0, weight=1)

        steps = ttk.Frame(window, padding=12)
        steps.grid(row=0, column=0, sticky="ew")
        steps.columnconfigure(0, weight=1)
        ttk.Label(
            steps,
            justify="left",
            text=(
                "This copies the login you already have in your browser.\n"
                "Nothing is typed here except what your browser prints.\n\n"
                "Quick way:\n"
                "1.  Click the button below to open Echo360, and sign in.\n"
                "2.  Press F12 (macOS: Option+Command+I) to open DevTools.\n"
                "3.  Click the \"Console\" tab.\n"
                "4.  Click \"Copy the command\" below, paste it into the\n"
                "     console and press Enter. If the browser refuses to\n"
                "     paste, type  allow pasting  first, press Enter, then\n"
                "     paste again.\n"
                "5.  Copy the whole line it prints out (without the quote\n"
                "     marks is fine too) and paste it into the box below.\n\n"
                "If Echo360 still says the session was rejected, the console\n"
                "did not show every cookie (some are HttpOnly). Complete way:\n"
                "1.  In DevTools, open the \"Network\" tab and reload the page.\n"
                "2.  Click the first request in the list (the page itself).\n"
                "3.  Under \"Request Headers\", right-click the \"Cookie\" row\n"
                "     and copy its value -- that one holds all of them.\n\n"
                "Either way, copy the cookies from the same Echo360 address\n"
                "as the unit you are downloading."
            ),
        ).grid(row=0, column=0, sticky="w")

        helpers = ttk.Frame(steps)
        helpers.grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Button(helpers, text="Open Echo360 in my browser",
                   command=self._open_echo360).grid(row=0, column=0)
        copy_button = ttk.Button(helpers, text="Copy the command")
        copy_button.grid(row=0, column=1, padx=6)

        def copy_command() -> None:
            self.clipboard_clear()
            self.clipboard_append("document.cookie")
            copy_button.configure(text="Copied ✓")
            window.after(1500, lambda: copy_button.configure(text="Copy the command"))

        copy_button.configure(command=copy_command)

        box = scrolledtext.ScrolledText(window, width=78, height=7, wrap="word")
        box.grid(row=1, column=0, padx=12, sticky="ew")
        box.focus_set()
        hint = ttk.Label(window, text=f"The line must contain {AUTH_COOKIE}=…",
                         foreground="#666")
        hint.grid(row=2, column=0, sticky="w", padx=12, pady=(4, 0))

        def accept() -> None:
            cookies = parse_cookie_header(box.get("1.0", "end"))
            if not cookies.get(AUTH_COOKIE):
                hint.configure(
                    text=f"That text has no {AUTH_COOKIE} in it. Make sure you are "
                         "signed in to Echo360, then copy the line again.",
                    foreground="#b00",
                )
                return
            window.destroy()
            self._apply_cookies(cookies, source="pasted cookies")

        row = ttk.Frame(window, padding=12)
        row.grid(row=3, column=0, sticky="e")
        ttk.Button(row, text="Use these cookies", command=accept).grid(row=0, column=0)
        ttk.Button(row, text="Cancel", command=window.destroy).grid(row=0, column=1, padx=6)
        window.bind("<Escape>", lambda _event: window.destroy())

    def _open_echo360(self) -> None:
        target = self.section_var.get().strip() or self.host_var.get().strip() or DEFAULT_HOST
        if "://" not in target:
            target = "https://" + target
        webbrowser.open(target)

    def on_forget_session(self) -> None:
        session_store.clear()
        self.client.set_cookies({})
        self.session_var.set("Not logged in")
        self.log("Saved session cleared.")

    # -------------------------------------------------------------- lessons
    def on_fetch(self) -> None:
        raw = self.section_var.get().strip()
        if not raw:
            messagebox.showinfo("Echo360 Transcript Downloader",
                                "Paste the Echo360 unit's section URL first.")
            return
        try:
            host, section = split_section_url(raw, self.host_var.get().strip() or DEFAULT_HOST)
        except ValueError as exc:
            messagebox.showerror("Echo360 Transcript Downloader", str(exc))
            return
        if not self.client.authenticated:
            messagebox.showinfo("Echo360 Transcript Downloader",
                                "Log in first (Log in with browser, or paste cookies).")
            return
        self.client.host = host
        self.host_var.set(host)

        def work() -> None:
            self._post("status", f"Fetching the class list for {section}…")
            lessons = self.client.fetch_lessons(section)
            self._post("lessons", lessons)
            self._post("status", f"{len(lessons)} lecture(s) found.")

        self._run(work)

    def _visible_lessons(self) -> list[Lesson]:
        needle = self.filter_var.get().strip().lower()
        if not needle:
            return self.lessons
        return [
            lesson for lesson in self.lessons
            if needle in lesson.title.lower() or needle in lesson.display_date
        ]

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for lesson in self._visible_lessons():
            mark = CHECKED if lesson.lesson_id in self.checked else UNCHECKED
            self.tree.insert("", "end", iid=lesson.lesson_id,
                             values=(mark, lesson.index, lesson.display_date or "-",
                                     lesson.title))

    def _on_tree_click(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self._toggle(row)

    def _toggle(self, lesson_id: str) -> None:
        if lesson_id in self.checked:
            self.checked.discard(lesson_id)
            mark = UNCHECKED
        else:
            self.checked.add(lesson_id)
            mark = CHECKED
        self.tree.set(lesson_id, "sel", mark)

    def _set_all(self, value: bool) -> None:
        for lesson in self._visible_lessons():
            if value:
                self.checked.add(lesson.lesson_id)
            else:
                self.checked.discard(lesson.lesson_id)
        self._refresh_tree()

    def _invert(self) -> None:
        for lesson in self._visible_lessons():
            if lesson.lesson_id in self.checked:
                self.checked.discard(lesson.lesson_id)
            else:
                self.checked.add(lesson.lesson_id)
        self._refresh_tree()

    # ------------------------------------------------------------- download
    def on_browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.outdir_var.get() or os.getcwd())
        if chosen:
            self.outdir_var.set(chosen)

    def on_open_folder(self) -> None:
        path = self.outdir_var.get().strip()
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        path = os.path.abspath(path)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except (OSError, AttributeError):
            webbrowser.open("file://" + path)

    def on_stop(self) -> None:
        self.stop_flag.set()
        self.status_var.set("Stopping after the current lectures…")

    def on_download(self) -> None:
        selected = [lesson for lesson in self.lessons if lesson.lesson_id in self.checked]
        if not selected:
            messagebox.showinfo("Echo360 Transcript Downloader",
                                "Tick at least one lecture.")
            return
        formats = [name for name, var in self.format_vars.items() if var.get()]
        if not formats:
            messagebox.showinfo("Echo360 Transcript Downloader",
                                "Pick at least one output format.")
            return
        out_dir = self.outdir_var.get().strip()
        if not out_dir:
            messagebox.showinfo("Echo360 Transcript Downloader",
                                "Choose a folder to save into.")
            return

        total = len(selected)
        self.progress.configure(maximum=total, value=0)
        counter = {"done": 0, "ok": 0, "skipped": 0, "failed": 0}

        def on_progress(result: DownloadResult) -> None:
            counter["done"] += 1
            if result.skipped and not result.paths:
                counter["skipped"] += 1
                label = "cancelled"
            elif result.skipped:
                counter["skipped"] += 1
                label = "already downloaded"
            elif result.ok:
                counter["ok"] += 1
                label = ", ".join(os.path.basename(path) for path in result.paths)
            else:
                counter["failed"] += 1
                label = result.error
            self._post("log", f"[{counter['done']}/{total}] "
                              f"{result.lesson.index:02d} {result.lesson.title}: {label}")
            self._post("progress", (counter["done"], total))

        def work() -> None:
            self._post("status", f"Downloading {total} transcript(s)…")
            download_transcripts(
                self.client, selected, out_dir, formats,
                overwrite=self.overwrite_var.get(),
                workers=self.workers_var.get(),
                stop=self.stop_flag,
                on_progress=on_progress,
            )
            self._post("status",
                       f"Done: {counter['ok']} saved, {counter['skipped']} skipped, "
                       f"{counter['failed']} failed → {out_dir}")

        self._run(work)


def main() -> None:
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()