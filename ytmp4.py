"""Download a YouTube video as an MP4, or just its audio as an MP3.

Usage:
    ytmp4 URL [URL ...] [-o OUTPUT_DIR] [-q HEIGHT] [-a] [-i ITEMS]
    ytmp4                (no arguments: opens the window)
"""
import argparse
import gc
import json
import os
import subprocess
import sys

import imageio_ffmpeg
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

QUALITIES = ["Best", "2160", "1440", "1080", "720", "480", "360"]
CANCELLED = 130
NATIVE_LIGHT_THEMES = ("vista", "xpnative", "winnative", "aqua")  # ttk themes good enough to use as they are


def data_dir():
    """Where the queue and settings are kept: the per-user config folder of each platform."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base or os.path.expanduser("~"), "ytmp4")


DATA_DIR = data_dir()
QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


def system_prefers_dark():
    """Whether the system is set to dark mode (Windows and macOS; False where it can't be told)."""
    try:
        if sys.platform == "darwin":
            style = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                   capture_output=True, text=True, timeout=5).stdout
            return style.strip() == "Dark"
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except (ImportError, OSError, subprocess.SubprocessError):
        return False


def resource(name):
    """Path to a file shipped with the program (next to the script, or unpacked from the executable)."""
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), name)


def read_json(path):
    """The parsed file, or None if it is missing or damaged."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(path + ".tmp", path)
    except OSError:
        pass  # the window still works, it just won't remember this next time


def bundled_deno():
    """Path to the Deno binary shipped inside the executable (or installed in the venv), if there is one."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    path = os.path.join(base, "deno.exe" if os.name == "nt" else "deno")
    return path if os.path.isfile(path) else None


# YouTube needs a JS runtime to expose all formats; use the bundled Deno, else Deno/Node on PATH.
JS_RUNTIMES = {"deno": {"path": bundled_deno()}, "node": {}}
UNAVAILABLE = ("[Private video]", "[Deleted video]")


class Quiet:
    """A yt-dlp logger that discards everything."""

    def debug(self, msg):
        pass

    info = warning = error = debug


def list_playlist(url):
    """(title, videos) for a playlist URL, or None if it is a single video.

    Each video is a dict with url, title and duration (seconds, may be None). Raises DownloadError.
    """
    options = {"extract_flat": "in_playlist", "skip_download": True, "logger": Quiet(), "js_runtimes": JS_RUNTIMES}
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info or info.get("_type") != "playlist":
        return None
    videos = [dict(url=entry["url"], title=entry.get("title") or entry.get("id") or entry["url"],
                   duration=entry.get("duration"))
              for entry in info.get("entries") or [] if entry and entry.get("url")]
    return info.get("title") or "Playlist", videos


def download(urls, output="downloads", quality=None, audio=False, items=None,
             progress_hook=None, logger=None, cancel=None):
    """Download urls; returns 0 on success, CANCELLED if the cancel event was set, else an error code.

    items picks videos out of a playlist by position, e.g. "1,3,5-7".
    """
    if audio:
        mode = {
            "format": "ba/b",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}],
        }
    else:
        height = f"[height<={quality}]" if quality else ""
        mode = {
            # Prefer H.264 + AAC so the MP4 plays everywhere; fall back to whatever is best.
            "format": f"bv*{height}[vcodec^=avc1]+ba[ext=m4a]/bv*{height}+ba/b{height}/b",
            "merge_output_format": "mp4",
        }
    options = {
        **mode,
        "outtmpl": os.path.join(output, "%(title)s [%(id)s].%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "js_runtimes": JS_RUNTIMES,
        "windowsfilenames": True,
        "ignoreerrors": "only_download",
    }
    if items:
        options["playlist_items"] = items
    if logger:
        options["logger"] = logger

    partial = set()  # files of the download in flight, removed if it is cancelled

    def on_progress(d):
        partial.update(f for f in (d.get("tmpfilename"), d.get("filename")) if f)
        stop_if_cancelled(d)
        if progress_hook:
            progress_hook(d)

    # ffmpeg steps can't be interrupted, so a cancel during one takes effect before the next.
    def stop_if_cancelled(d):
        if cancel and cancel.is_set():
            raise DownloadCancelled("Cancelled")

    options["progress_hooks"] = [on_progress]
    options["postprocessor_hooks"] = [stop_if_cancelled]

    try:
        with YoutubeDL(options) as ydl:
            return ydl.download(urls)
    except DownloadCancelled:
        gc.collect()  # release the handle on the half-written file so Windows lets us delete it
        for path in partial:
            for leftover in (path, path + ".ytdl"):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
        return CANCELLED
    except DownloadError as e:
        if logger:
            logger.error(str(e))
        else:
            print(e, file=sys.stderr)
        return 1


class PlaylistPicker:
    """A window listing a playlist's videos with a tick against each one to download."""

    TICK = "✓"

    def __init__(self, app, title, videos, on_done):
        import tkinter as tk
        from tkinter import ttk

        self.app, self.videos, self.on_done = app, videos, on_done
        self.window = window = tk.Toplevel(app.root)
        window.title("Choose videos")
        window.configure(background=ttk.Style(app.root).lookup("TFrame", "background"))
        window.resizable(False, False)
        window.transient(app.root)
        window.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(window, padding=14)
        frame.grid()

        ttk.Label(frame, text=title, wraplength=600, justify="left").grid(row=0, column=0, sticky="w")

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=1, column=0, pady=(10, 0))
        self.tree = ttk.Treeview(list_frame, columns=("pick", "number", "title", "length"), show="headings",
                                 height=14, selectmode="none")
        for column, text, width, anchor in (("pick", "", 34, "center"), ("number", "#", 44, "e"),
                                            ("title", "Video", 440, "w"), ("length", "Length", 76, "e")):
            self.tree.heading(column, text=text, anchor=anchor)
            self.tree.column(column, width=width, anchor=anchor, stretch=False)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left")
        scroll.pack(side="left", fill="y")
        for number, video in enumerate(videos, 1):
            picked = video["title"] not in UNAVAILABLE
            self.tree.insert("", "end", iid=str(number - 1), values=(
                self.TICK if picked else "", number, video["title"], self.length(video["duration"])))
        self.tree.bind("<Button-1>", self.on_click)

        self.count = tk.StringVar()
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="Select all", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(buttons, text="Select none", command=lambda: self.set_all(False)).pack(side="left", padx=(8, 0))
        ttk.Label(buttons, textvariable=self.count).pack(side="left", padx=(12, 0))
        self.add_button = ttk.Button(buttons, text="Add selected", command=self.confirm, default="active")
        self.add_button.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side="right", padx=(0, 8))
        window.bind("<Return>", lambda e: self.confirm())
        window.bind("<Escape>", lambda e: self.close())

        self.update_count()
        app.set_title_bar(window)
        app.picker = self
        window.grab_set()
        window.focus_set()

    @staticmethod
    def length(seconds):
        if not seconds:
            return ""
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"

    def picked(self):
        return [video for row, video in enumerate(self.videos) if self.tree.set(str(row), "pick")]

    def on_click(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.set(row, "pick", "" if self.tree.set(row, "pick") else self.TICK)
            self.update_count()

    def set_all(self, picked):
        for row in self.tree.get_children():
            self.tree.set(row, "pick", self.TICK if picked else "")
        self.update_count()

    def update_count(self):
        count = len(self.picked())
        self.count.set(f"{count} of {len(self.videos)} selected")
        self.add_button.configure(state="normal" if count else "disabled")

    def confirm(self):
        videos = self.picked()
        if videos:
            self.close(videos)

    def close(self, videos=()):
        self.app.picker = None
        self.window.destroy()
        self.on_done(list(videos))


class App:
    """The window shown when ytmp4 is started with no arguments (e.g. double-clicked)."""

    def __init__(self):
        import queue
        import threading
        import tkinter as tk
        from tkinter import ttk

        try:  # crisp text on high-DPI displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.events = queue.Queue()
        self.last_error = None
        self.cancel_event = threading.Event()
        self.items = {}      # queue row id -> {"url", "audio", "quality", "state", "error"}
        self.current = None  # row being downloaded, if any
        self.fetching = 0    # playlist links still being looked up
        self.picker = None   # the open "choose videos" window, if any

        self.root = root = tk.Tk()
        root.title("YouTube Downloader")
        try:
            if os.name == "nt":
                root.iconbitmap(default=resource("icon.ico"))
            else:
                self.icon = tk.PhotoImage(file=resource("icon.png"))
                root.iconphoto(True, self.icon)
        except tk.TclError:
            pass  # icon file missing: keep the default
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=14)
        frame.grid()

        self.url = tk.StringVar()
        self.audio = tk.BooleanVar(value=False)
        self.quality = tk.StringVar(value=QUALITIES[0])
        self.dark = tk.BooleanVar(value=system_prefers_dark())
        self.native_theme = ttk.Style(root).theme_use()
        self.folder =tk.StringVar(value=os.path.abspath("downloads"))
        self.status = tk.StringVar(value="Paste one or more YouTube links, then press Download.")

        ttk.Label(frame, text="URL").grid(row=0, column=0, sticky="w")
        url_entry = ttk.Entry(frame, textvariable=self.url, width=58)
        url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))
        url_entry.bind("<Return>", lambda e: self.add())
        url_entry.focus()
        ttk.Button(frame, text="Add", command=self.add).grid(row=0, column=3, padx=(8, 0))

        ttk.Label(frame, text="Format").grid(row=1, column=0, sticky="w", pady=(10, 0))
        formats = ttk.Frame(frame)
        formats.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Checkbutton(frame, text="Dark", variable=self.dark, command=self.apply_theme).grid(
            row=1, column=3, sticky="e", pady=(10, 0))
        ttk.Radiobutton(formats, text="Video (MP4)", variable=self.audio, value=False,
                        command=self.sync_quality).pack(side="left")
        ttk.Radiobutton(formats, text="Audio (MP3)", variable=self.audio, value=True,
                        command=self.sync_quality).pack(side="left", padx=(12, 0))
        ttk.Label(formats, text="Quality").pack(side="left", padx=(24, 6))
        self.quality_box = ttk.Combobox(formats, textvariable=self.quality, values=QUALITIES,
                                        state="readonly", width=7)
        self.quality_box.pack(side="left")

        ttk.Label(frame, text="Save to").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.folder).grid(row=2, column=1, columnspan=2, sticky="ew",
                                                        padx=(8, 0), pady=(10, 0))
        ttk.Button(frame, text="Browse...", command=self.browse).grid(row=2, column=3, padx=(8, 0), pady=(10, 0))

        queue_frame = ttk.Frame(frame)
        queue_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        self.tree = ttk.Treeview(queue_frame, columns=("title", "format", "status"), show="headings", height=7)
        for column, text, width in (("title", "Video", 330), ("format", "Format", 90), ("status", "Status", 130)):
            self.tree.heading(column, text=text, anchor="w")
            self.tree.column(column, width=width, anchor="w", stretch=column == "title")
        scroll = ttk.Scrollbar(queue_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Delete>", lambda e: self.remove())
        self.tree.bind("<<TreeviewSelect>>", self.show_error)

        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Label(frame, textvariable=self.status, wraplength=560, justify="left").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="Remove", command=self.remove).pack(side="left")
        ttk.Button(buttons, text="Clear done", command=self.clear_done).pack(side="left", padx=(8, 0))
        self.button = ttk.Button(buttons, text="Download", command=self.start, default="active")
        self.button.pack(side="right")
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Open folder", command=self.open_folder).pack(side="right", padx=(0, 8))
        frame.columnconfigure(1, weight=1)

        self.load_settings()
        self.apply_theme()
        for setting in (self.folder, self.audio, self.quality, self.dark):
            setting.trace_add("write", self.save_settings)
        self.load()
        root.after(100, self.poll)

    def sync_quality(self):
        self.quality_box.configure(state="disabled" if self.audio.get() else "readonly")

    def browse(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(initialdir=self.folder.get() or ".")
        if folder:
            self.folder.set(os.path.normpath(folder))

    def open_folder(self):
        folder = self.folder.get()
        os.makedirs(folder, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def add(self, then_start=False):
        """Queue every URL in the box (separated by spaces or new lines) with the current format.

        Playlist links are looked up first so the videos to download can be picked.
        """
        import threading
        urls = self.url.get().split()
        audio = self.audio.get()
        quality = int(self.quality.get()) if self.quality.get().isdigit() else None
        for url in urls:
            if "list=" in url:
                self.fetching += 1
                threading.Thread(target=self.fetch_playlist, args=(url, audio, quality, then_start),
                                 daemon=True).start()
            else:
                self.insert(dict(url=url, audio=audio, state="queued", error=None, title=None, quality=quality))
        self.url.set("")
        self.save()
        if urls and not self.current:
            self.summarize()

    # Runs on its own thread: only touch the queue, never the widgets.
    def fetch_playlist(self, url, audio, quality, then_start):
        try:
            found = list_playlist(url)
        except Exception:
            found = None  # queue the link as it is; downloading it will report what is wrong
        self.events.put(("playlist", (url, audio, quality, then_start, found)))

    def on_playlist(self, url, audio, quality, then_start, found):
        self.fetching -= 1

        def queue_videos(videos):
            for video in videos:
                self.insert(dict(url=video["url"], audio=audio, state="queued", error=None,
                                 title=video.get("title"), quality=quality))
            self.save()
            if self.current:
                return  # already running; these are picked up in turn
            if videos and then_start:
                self.start()
            else:
                self.summarize()

        if found and found[1]:
            PlaylistPicker(self, found[0], found[1], queue_videos)
        else:
            queue_videos([dict(url=url)])

    def set_title_bar(self, window):
        """Make a window's title bar match the theme (Windows 10 20H1 and later)."""
        try:
            import ctypes
            window.update_idletasks()
            handle = ctypes.windll.user32.GetParent(window.winfo_id())
            flag = ctypes.c_int(1 if self.dark.get() else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 20, ctypes.byref(flag), ctypes.sizeof(flag))
        except Exception:
            pass

    def insert(self, item):
        label = "MP3" if item["audio"] else f"MP4 {item['quality'] or 'Best'}"
        row = self.tree.insert("", "end", values=(item["title"] or item["url"], label, item["state"].capitalize()))
        self.items[row] = item

    def save(self):
        """Write the unfinished part of the queue to disk so it survives closing the window."""
        pending = [self.items[row] for row in self.tree.get_children() if self.items[row]["state"] != "done"]
        write_json(QUEUE_FILE, pending)

    def save_settings(self, *_):
        write_json(SETTINGS_FILE, dict(folder=self.folder.get(), audio=self.audio.get(), quality=self.quality.get(),
                                       dark=self.dark.get()))

    def load_settings(self):
        saved = read_json(SETTINGS_FILE)
        if not isinstance(saved, dict):
            return
        if isinstance(saved.get("folder"), str) and saved["folder"].strip():
            self.folder.set(saved["folder"])
        if saved.get("quality") in QUALITIES:
            self.quality.set(saved["quality"])
        self.audio.set(saved.get("audio") is True)
        self.sync_quality()
        if isinstance(saved.get("dark"), bool):  # otherwise keep following the Windows setting
            self.dark.set(saved["dark"])

    def apply_theme(self):
        from tkinter import ttk
        dark = self.dark.get()
        style = ttk.Style(self.root)
        if not dark and self.native_theme in NATIVE_LIGHT_THEMES:
            style.theme_use(self.native_theme)
            bg = style.lookup("TFrame", "background")
            listbox = dict(background="SystemWindow", foreground="SystemWindowText",
                           selectbackground="SystemHighlight", selectforeground="SystemHighlightText")
            if os.name != "nt":
                listbox = dict(background="#ffffff", foreground="#000000",
                               selectbackground="#0a64d8", selectforeground="#ffffff")
        else:
            # Drawn with the recolourable "clam" theme: always for dark, and for light where there is
            # no good-looking native theme (Linux).
            if dark:
                bg, field, raised, hover = "#1e1e1e", "#2a2a2a", "#333333", "#404040"
                text, dim, border, select, on_select = "#e8e8e8", "#7d7d7d", "#484848", "#3d5a80", "#e8e8e8"
            else:
                bg, field, raised, hover = "#f0f0f0", "#ffffff", "#e1e1e1", "#d2d2d2"
                text, dim, border, select, on_select = "#1a1a1a", "#9a9a9a", "#adadad", "#0a64d8", "#ffffff"
            accent = "#e22d2d"
            style.theme_use("clam")
            style.configure(".", background=bg, foreground=text, fieldbackground=field, bordercolor=border,
                            darkcolor=bg, lightcolor=bg, troughcolor=field, insertcolor=text,
                            selectbackground=select, selectforeground=on_select, focuscolor=border)
            style.configure("TButton", background=raised, darkcolor=raised, lightcolor=raised, padding=(10, 3))
            style.map("TButton", background=[("disabled", bg), ("pressed", border), ("active", hover)],
                      darkcolor=[("disabled", bg), ("active", hover)], lightcolor=[("disabled", bg), ("active", hover)],
                      foreground=[("disabled", dim)])
            style.configure("TEntry", padding=3)
            style.map("TEntry", bordercolor=[("focus", accent)], lightcolor=[("focus", accent)])
            style.configure("TCombobox", background=raised, arrowcolor=text, padding=2)
            style.map("TCombobox", fieldbackground=[("disabled", bg), ("readonly", field)],
                      foreground=[("disabled", dim)], arrowcolor=[("disabled", dim)],
                      selectbackground=[("readonly", field)], selectforeground=[("readonly", text)])
            for toggle in ("TRadiobutton", "TCheckbutton"):
                style.configure(toggle, indicatorbackground=field, indicatorforeground="#ffffff", indicatorsize=14,
                                upperbordercolor=dim, lowerbordercolor=dim)
                style.map(toggle, background=[("active", bg)], foreground=[("disabled", dim)],
                          indicatorbackground=[("selected", accent)],
                          upperbordercolor=[("selected", accent)], lowerbordercolor=[("selected", accent)])
            style.configure("Treeview", background=field, fieldbackground=field, foreground=text, borderwidth=0)
            style.map("Treeview", background=[("selected", select)], foreground=[("selected", on_select)])
            style.configure("Treeview.Heading", background=raised, foreground=text, relief="flat")
            style.map("Treeview.Heading", background=[("active", hover)])
            style.configure("TScrollbar", background=raised, arrowcolor=text, troughcolor=field)
            style.map("TScrollbar", background=[("disabled", field), ("active", hover)])
            style.configure("TProgressbar", background=accent, darkcolor=accent, lightcolor=accent,
                            bordercolor=border, troughcolor=field)
            listbox = dict(background=field, foreground=text, selectbackground=select, selectforeground=on_select)
        self.root.configure(background=bg)
        # The quality dropdown's list is a classic Tk widget, outside ttk styling.
        popdown = self.root.tk.call("ttk::combobox::PopdownWindow", self.quality_box)
        for option, value in listbox.items():
            self.root.tk.call(f"{popdown}.f.l", "configure", f"-{option}", value)
        self.set_title_bar(self.root)

    def load(self):
        saved = read_json(QUEUE_FILE)
        for entry in saved if isinstance(saved, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                continue
            quality = entry.get("quality")
            # An item that was mid-download when the window closed goes back to queued.
            state = entry.get("state") if entry.get("state") in ("failed", "cancelled") else "queued"
            self.insert(dict(url=entry["url"], audio=bool(entry.get("audio")), state=state,
                             error=entry.get("error") if state == "failed" else None,
                             title=entry.get("title"), quality=quality if isinstance(quality, int) else None))
        if self.items:
            self.summarize()

    def remove(self):
        for row in self.tree.selection():
            if row != self.current:
                self.tree.delete(row)
                del self.items[row]
        self.save()
        if not self.current:
            self.summarize()

    def clear_done(self):
        for row, item in list(self.items.items()):
            if item["state"] == "done":
                self.tree.delete(row)
                del self.items[row]
        if not self.current:
            self.summarize()

    def show_error(self, event=None):
        selected = self.tree.selection()
        if len(selected) == 1 and self.items[selected[0]]["error"]:
            self.status.set(f"Failed: {self.items[selected[0]]['error']}")

    def set_state(self, row, state, text):
        self.items[row]["state"] = state
        self.tree.set(row, "status", text)
        self.save()

    def summarize(self):
        if self.fetching:
            self.status.set("Reading playlist...")
            return
        states = [item["state"] for item in self.items.values()]
        counts = [f"{states.count(state)} {state}" for state in ("done", "failed", "cancelled", "queued")
                  if state in states]
        hint = " Select a failed row to see why." if "failed" in states else ""
        self.status.set(", ".join(counts).capitalize() + "." + hint if counts else "Queue is empty.")

    def start(self):
        """Download everything in the queue that isn't done, one after another."""
        self.add(then_start=True)
        if self.current:
            return  # already running; anything just added is picked up in turn
        for row, item in self.items.items():
            if item["state"] in ("failed", "cancelled"):
                item["error"] = None
                self.set_state(row, "queued", "Queued")
        self.cancel_event.clear()
        self.run_next()

    def run_next(self):
        import threading
        row = next((r for r in self.tree.get_children() if self.items[r]["state"] == "queued"), None)
        self.current = row
        if row is None:
            self.cancel_button.configure(state="disabled")
            self.summarize()
            return
        item = self.items[row]
        job = dict(urls=[item["url"]], output=self.folder.get(), audio=item["audio"], quality=item["quality"],
                   progress_hook=self.on_progress, logger=self, cancel=self.cancel_event)
        self.last_error = None
        self.cancel_button.configure(state="normal")
        self.bar.configure(value=0)
        self.set_state(row, "running", "Starting...")
        self.tree.see(row)
        position = self.tree.index(row) + 1
        self.status.set(f"Downloading {position} of {len(self.items)}...")
        threading.Thread(target=lambda: self.events.put(("done", download(**job))), daemon=True).start()

    def cancel(self):
        """Stop the current download; the rest of the queue stays queued."""
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.tree.set(self.current, "status", "Cancelling...")

    # Called from the download thread: only touch the queue, never the widgets.
    def on_progress(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            percent = d.get("downloaded_bytes", 0) * 100 / total if total else None
            info = d.get("info_dict") or {}
            self.events.put(("progress", (percent, info.get("playlist_title") or info.get("title"))))
        elif d["status"] == "finished":
            self.events.put(("processing", None))

    # yt-dlp logger interface
    def debug(self, msg):
        pass

    info = warning = debug

    def error(self, msg):
        self.last_error = msg.replace("ERROR: ", "", 1)

    def poll(self):
        import queue
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "playlist":
                    self.on_playlist(*value)
                    continue
                row = self.current
                if kind != "done" and self.cancel_event.is_set():
                    continue  # keep showing "Cancelling..."
                if kind == "progress":
                    percent, title = value
                    if title and title != self.items[row]["title"]:
                        self.items[row]["title"] = title
                        self.tree.set(row, "title", title)
                        self.save()
                    if percent is None:
                        self.tree.set(row, "status", "Downloading...")
                    else:
                        self.bar.configure(value=percent)
                        self.tree.set(row, "status", f"Downloading {percent:.0f}%")
                elif kind == "processing":
                    self.tree.set(row, "status", "Processing...")
                elif kind == "done":
                    self.bar.configure(value=100 if value == 0 else 0)
                    if value == 0:
                        self.set_state(row, "done", "Done")
                    elif value == CANCELLED:
                        self.set_state(row, "cancelled", "Cancelled")
                    else:
                        self.items[row]["error"] = self.last_error or "unknown error"
                        self.set_state(row, "failed", "Failed")
                    if self.cancel_event.is_set():
                        self.current = None
                        self.cancel_button.configure(state="disabled")
                        self.summarize()
                    else:
                        self.run_next()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def run(self):
        self.root.mainloop()
        return 0


def main():
    if len(sys.argv) == 1:
        return App().run()

    parser = argparse.ArgumentParser(prog="ytmp4", description="Download YouTube videos as MP4, or audio as MP3.")
    parser.add_argument("urls", nargs="+", help="video or playlist URL(s)")
    parser.add_argument("-o", "--output", default="downloads", help="output folder (default: downloads)")
    parser.add_argument("-q", "--quality", type=int, metavar="HEIGHT",
                        help="max video height, e.g. 1080 or 720 (default: best available)")
    parser.add_argument("-a", "--audio", action="store_true", help="download just the audio as MP3")
    parser.add_argument("-i", "--items", metavar="LIST",
                        help="which videos of a playlist to download, by position, e.g. 1,3,5-7 (default: all)")
    args = parser.parse_args()
    return download(args.urls, args.output, args.quality, args.audio, args.items)


if __name__ == "__main__":
    sys.exit(main())
