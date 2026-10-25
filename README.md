# Echo360 Transcript Downloader

Batch-downloads lecture transcripts from Echo360 (EchoVideo) as `.txt`, `.vtt`,
`.srt` or `.json`. Point it at a unit, tick the lectures you want, get a folder
of text files.

It never asks for your password. You stay signed in to Echo360 in your own
browser and hand the app the session your browser already has.

---

## 1. Download it

Grab the latest build for your system from the
[**Releases**](../../releases) page, then unzip it anywhere — Desktop or
Documents is fine.

**Keep the whole folder together.** The `.exe` needs the files beside it; moving
it out on its own will not work.

Nothing to install. No Python, no setup.

---

## 2. First launch: getting past the warnings

The app is not code-signed (certificates cost hundreds a year), so Windows and
macOS will warn you about it. This is a warning about the *publisher being
unknown*, not about anything found in the file.

**Windows — "Windows protected your PC":**
Click **More info**, then **Run anyway**.

**Windows — "contains a virus or potentially unwanted software":**
Defender occasionally flags bundled Python apps as a false positive. If this
happens, the simplest fix is to download the folder build rather than the
single-file one. If you still want to run it, add the folder to Defender's
exclusions: **Windows Security → Virus & threat protection → Manage settings →
Exclusions → Add an exclusion → Folder**.

Only do that if you trust where you got this from. Never add exclusions for
files you did not expect.

**macOS — "cannot be opened because it is from an unidentified developer":**
Right-click the app → **Open** → **Open**. Only needed the first time.

---

## 3. Log in

Echo360 needs two cookies: `ECHO_JWT` and `PLAY_SESSION`. Both are **HttpOnly**,
which means the browser console cannot read them. Copy them from the request
headers instead:

1. Sign in to Echo360 in your normal browser and open the unit, so you can see
   its class list.
2. Press **F12** (macOS: **Option+Command+I**) to open DevTools.
3. Click the **Network** tab.
4. **Reload the page.** The request list fills up.
5. Click the **first request** in the list — the page itself.
6. Click the **Headers** tab and scroll to **Request Headers**.
7. Right-click the **`Cookie`** row → **Copy value**.
8. In the app, click **Paste cookies…**, paste into the box, click
   **Use these cookies**.

The app checks the cookies before accepting them. If it says the token is
anonymous or expired, you were not actually signed in on the page you copied
from — sign in, reload, copy again.

Your session is saved and reused, so this is a once-in-a-while job, not every
time. The cookies expire when you fully close your browser.

> There is a "Log in with browser" button in the window. Ignore it — the cookie
> method above is the supported one.

---

## 4. Download transcripts

1. Open the unit in Echo360 and copy its address from the address bar, e.g.
   `https://echo360.net.au/section/0e2e7ae7-75b5-4ea2-877e-750e2da38830/home`
2. Paste it into **Section URL or UUID** and click **Fetch lessons**.
3. Tick the lectures you want. **Select all**, **Invert** and the **Filter** box
   help with a full semester.
4. Choose your formats, pick a folder, click **Download transcripts**.

Files come out named like
`01 - 2026-07-21 - CITS2211 - 21 Jul 2026 - Lecture.txt`.

| Format | What you get |
|---|---|
| `.txt` | Readable prose — deduplicated, whitespace tidied. **Start here.** |
| `.vtt` / `.srt` | Subtitles with timings. Only written when the transcript has timings; otherwise skipped, with a note in the log. |
| `.json` | Raw cues with start/end times, for scripting. |

Already-downloaded files are skipped unless you tick **Overwrite existing
files**, so you can grab each week's new lectures without refetching everything.

---

## If something goes wrong

paste the url of this repo into claude as well as the error, it can probably figure it out

Read the **Log** box at the bottom of the window — it explains most failures in
plain English, and its first line shows which version you are running.

| Message | What it means |
|---|---|
| `PLAY_SESSION was not sent` | Only part of the cookie line was copied. Redo step 3 using the **Network** tab, not the console. |
| `it carries no user — this is the anonymous token` | You were not signed in on the page you copied from. |
| `it expired N minutes ago` | Reload Echo360 in your browser and copy the cookies again. |
| `no lessons were recognised` | Open an issue and include the error text — it sketches what Echo360 returned. |
| `data list is empty` | Wrong unit address. Copy the section URL again from the unit's own page. |
| `no transcript available` | That lecture has no transcript. The error lists everything it tried. |

Reporting a problem? Include the **whole Log box** — version line included.

---

## Notes

- Transcripts are Echo360's machine-generated captions. Expect errors on names,
  jargon and equations.
- You can only download lectures you can already open in Echo360. This uses your
  own session and grants no access you do not already have.

---



</details>
