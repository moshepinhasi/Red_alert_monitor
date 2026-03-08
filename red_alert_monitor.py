"""
Red Alert Monitor — מוניטור אזעקות צבע אדום בזמן אמת
======================================================
Requirements:
    pip install customtkinter httpx

Usage:
    python red_alert_monitor.py        <- עם מסוף
    לחיצה כפולה על הקובץ              <- ללא מסוף (אוטומטי)
"""

# ── No-console trick: relaunch with pythonw on double-click ──────────────────
import os
import subprocess
import sys

if sys.platform == "win32" and os.path.basename(sys.executable).lower() == "python.exe":
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if os.path.exists(pythonw):
        subprocess.Popen([pythonw, os.path.abspath(__file__)] + sys.argv[1:])
        sys.exit(0)

# ─── Imports ──────────────────────────────────────────────────────────────────
import json
import logging
import platform
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import customtkinter as ctk
import httpx

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.NullHandler()],
)
logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
OREF_API_URL     = "https://www.oref.org.il/WarningMessages/alert/alerts.json"
OREF_HISTORY_URL = "https://www.oref.org.il/WarningMessages/History/AlertsHistory.json"
POLL_INTERVAL_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 3.0

WINDOW_WIDTH = 430
WINDOW_HEIGHT_IDLE = 130
WINDOW_HEIGHT_ALERT = 320

ALERT_COLOR       = "#CC0000"
ALERT_COLOR_LIGHT = "#FF2222"
IDLE_BG           = "#1a1a2e"
TEXT_PRIMARY      = "#FFFFFF"
FLASH_MS          = 300

HEADERS = {
    "Referer": "https://www.oref.org.il/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
}

APP_NAME    = "RedAlertMonitor"
STARTUP_REG = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _get_exe_path() -> str:
    """Returns the path to use for startup registration."""
    if getattr(sys, "frozen", False):
        return sys.executable
    vbs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "הפעל_אזעקות.vbs")
    if os.path.exists(vbs):
        return f'wscript.exe "{vbs}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def is_startup_enabled() -> bool:
    """Returns True if the app is registered to run on Windows startup."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def set_startup(enabled: bool) -> None:
    """Adds or removes the app from Windows startup registry."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _get_exe_path())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    except Exception as exc:
        logger.error("Startup registry error: %s", exc)


# ─── Data Model ───────────────────────────────────────────────────────────────
@dataclass
class AlertData:
    """Represents a single active red alert from Pikud HaOref."""

    alert_id: str
    title: str
    areas: list[str]
    timestamp: datetime = field(default_factory=datetime.now)
    category: int = 1

    @property
    def areas_text(self) -> str:
        """Formatted area list (max 8 shown)."""
        if not self.areas:
            return "אזור לא ידוע"
        lines = " | ".join(self.areas[:8])
        if len(self.areas) > 8:
            lines += f"\n+{len(self.areas) - 8} נוספות"
        return lines

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")


# ─── Alert Poller ─────────────────────────────────────────────────────────────
class AlertPoller:
    """Background thread that polls the Oref API every POLL_INTERVAL_SECONDS."""

    def __init__(
        self,
        on_alert: Callable[[AlertData], None],
        on_clear: Callable[[], None],
        on_connection_change: Callable[[bool], None],
    ) -> None:
        self._on_alert = on_alert
        self._on_clear = on_clear
        self._on_connection_change = on_connection_change
        self._last_id: Optional[str] = None
        self._running = False
        self._connected = False

    def start(self) -> None:
        """Starts polling in a daemon thread."""
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="AlertPoller").start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                alert = self._fetch()
                if not self._connected:
                    self._connected = True
                    self._on_connection_change(True)

                if alert:
                    if alert.alert_id != self._last_id:
                        self._last_id = alert.alert_id
                        self._on_alert(alert)
                else:
                    if self._last_id is not None:
                        self._last_id = None
                        self._on_clear()

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.warning("Connection issue: %s", exc)
                self._mark_disconnected()
            except Exception as exc:
                logger.error("Poller error: %s", exc, exc_info=True)
                self._mark_disconnected()

            time.sleep(POLL_INTERVAL_SECONDS)

    def _fetch(self) -> Optional[AlertData]:
        """GETs the Oref alerts endpoint and parses the response."""
        import ssl
        # Oref server requires lenient SSL — create permissive context
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        with httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers=HEADERS,
            verify=False,          # bypass SSL verify (oref TLS quirks)
            follow_redirects=True,
        ) as client:
            resp = client.get(OREF_API_URL)

        # Strip BOM + whitespace — oref sometimes sends UTF-8 BOM
        content = resp.content.decode("utf-8-sig").strip()

        if not content or content in ("{}", "null", "[]", ""):
            return None

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.debug("Non-JSON response: %r", content[:80])
            return None

        if not isinstance(data, dict):
            return None

        areas = data.get("data", [])
        if not areas:
            return None

        return AlertData(
            alert_id=str(data.get("id", time.time())),
            title=data.get("title", "אזעקת צבע אדום"),
            areas=areas if isinstance(areas, list) else [str(areas)],
            category=data.get("cat", 1),
        )

    def _mark_disconnected(self) -> None:
        if self._connected:
            self._connected = False
            self._on_connection_change(False)


# ─── Sound ────────────────────────────────────────────────────────────────────
def play_alert_sound() -> None:
    """Plays a beep sound in a non-blocking thread."""

    def _play() -> None:
        try:
            if platform.system() == "Windows":
                import winsound
                for _ in range(3):
                    winsound.Beep(1200, 200)
                    time.sleep(0.1)
                    winsound.Beep(800, 300)
                    time.sleep(0.05)
            elif platform.system() == "Darwin":
                subprocess.run(["afplay", "/System/Library/Sounds/Sosumi.aiff"], check=False)
            else:
                subprocess.run(
                    ["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                    check=False, stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            logger.debug("Sound error: %s", exc)

    threading.Thread(target=_play, daemon=True).start()


# ─── Main Window ──────────────────────────────────────────────────────────────
class RedAlertApp(ctk.CTk):
    """
    Frameless always-on-top window.
    Idle: compact bar.  Alert: expands with red flashing panel.
    """

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")

        self._alert_active = False
        self._flash_state = False
        self._flash_job: Optional[str] = None
        self._alert_count = 0
        self._minimized = False
        self._drag_x = self._drag_y = 0
        self._tray_mode = False          # hide to tray, pop only on alert
        self._tray_icon: Optional[object] = None

        self._setup_window()
        self._build_ui()
        self._start_poller()

    # ── Window setup ──────────────────────────────────────────────────────────
    def _setup_window(self) -> None:
        self.title("Red Alert Monitor")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT_IDLE}+60+60")
        self.resizable(False, False)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.95)
        self.configure(fg_color=IDLE_BG)

        # Set window icon (1.ico must be in same folder as the script)
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "1.ico")
            self.iconbitmap(icon_path)
        except Exception:
            pass

        self.bind("<ButtonPress-1>", lambda e: (setattr(self, "_drag_x", e.x), setattr(self, "_drag_y", e.y)))
        self.bind("<B1-Motion>", self._on_drag)

    def _on_drag(self, e: tk.Event) -> None:
        self.geometry(f"+{self.winfo_x() + e.x - self._drag_x}+{self.winfo_y() + e.y - self._drag_y}")

    # ── UI Build ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:

        # ── Top bar (44px) ────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="#12122a", corner_radius=0, height=44)
        top.pack(fill="x")
        top.pack_propagate(False)

        self._status_dot = ctk.CTkLabel(
            top, text="●", font=("Arial", 14), text_color="#00FF88", width=22,
        )
        self._status_dot.pack(side="left", padx=(8, 2), pady=8)

        ctk.CTkLabel(
            top,
            text="🛡  מוניטור אזעקות | פיקוד העורף",
            font=ctk.CTkFont("Arial", 12, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            top, text="✕", width=28, height=28, corner_radius=6,
            fg_color="#3a3a5c", hover_color="#CC2200",
            font=ctk.CTkFont(size=11), command=self._on_close,
        ).pack(side="right", padx=(4, 8), pady=8)

        ctk.CTkButton(
            top, text="—", width=28, height=28, corner_radius=6,
            fg_color="#3a3a5c", hover_color="#555580",
            font=ctk.CTkFont(size=11), command=self._toggle_minimize,
        ).pack(side="right", padx=2, pady=8)

        # Tray button
        self._tray_btn = ctk.CTkButton(
            top, text="🔔", width=28, height=28, corner_radius=6,
            fg_color="#3a3a5c", hover_color="#336633",
            font=ctk.CTkFont(size=11),
            command=self._toggle_tray_mode,
        )
        self._tray_btn.pack(side="right", padx=2, pady=8)

        # Startup button
        self._startup_btn = ctk.CTkButton(
            top, text="⚡", width=28, height=28, corner_radius=6,
            fg_color="#553300" if not is_startup_enabled() else "#335500",
            hover_color="#664400",
            font=ctk.CTkFont(size=11),
            command=self._toggle_startup,
        )
        self._startup_btn.pack(side="right", padx=2, pady=8)

        # ── Status bar (36px) ─────────────────────────────────────────────────
        self._status_frame = ctk.CTkFrame(self, fg_color="#0d1117", corner_radius=8, height=36)
        self._status_frame.pack(fill="x", padx=10, pady=(6, 4))
        self._status_frame.pack_propagate(False)

        self._status_label = ctk.CTkLabel(
            self._status_frame,
            text="✅  אין אזעקות פעילות  |  מחובר לפיקוד העורף",
            font=ctk.CTkFont("Arial", 11),
            text_color="#66FF99",
        )
        self._status_label.pack(expand=True, fill="both", padx=8)

        # ── Alert panel (hidden until alert fires) ────────────────────────────
        self._alert_panel = ctk.CTkFrame(self, fg_color=ALERT_COLOR, corner_radius=10)

        ctk.CTkLabel(self._alert_panel, text="🚨", font=ctk.CTkFont(size=44)).pack(pady=(14, 2))

        self._alert_title_lbl = ctk.CTkLabel(
            self._alert_panel, text="אזעקת צבע אדום",
            font=ctk.CTkFont("Arial", 18, "bold"), text_color=TEXT_PRIMARY,
        )
        self._alert_title_lbl.pack()

        self._alert_areas_lbl = ctk.CTkLabel(
            self._alert_panel, text="",
            font=ctk.CTkFont("Arial", 12), text_color="#FFE0E0",
            wraplength=390, justify="center",
        )
        self._alert_areas_lbl.pack(pady=(6, 2))

        self._alert_time_lbl = ctk.CTkLabel(
            self._alert_panel, text="",
            font=ctk.CTkFont("Arial", 10), text_color="#FFCCCC",
        )
        self._alert_time_lbl.pack(pady=(0, 12))

        # ── Footer (36px) ─────────────────────────────────────────────────────
        self._footer_frame = ctk.CTkFrame(self, fg_color="transparent", height=36)
        self._footer_frame.pack(fill="x", padx=10, pady=(2, 6))
        self._footer_frame.pack_propagate(False)

        self._counter_lbl = ctk.CTkLabel(
            self._footer_frame,
            text="סה״כ אזעקות: 0",
            font=ctk.CTkFont("Arial", 9),
            text_color="#555577",
        )
        self._counter_lbl.pack(side="left", pady=5)

        # © Copyright label
        ctk.CTkLabel(
            self._footer_frame,
            text="© כל הזכויות שמורות למשה פנחסי",
            font=ctk.CTkFont("Arial", 8),
            text_color="#333355",
        ).pack(side="left", padx=(10, 0), pady=5)

        # 🔆 Opacity slider
        self._opacity_slider = ctk.CTkSlider(
            self._footer_frame,
            from_=0.2, to=1.0,
            width=80, height=16,
            number_of_steps=16,
            button_color="#7777aa",
            button_hover_color="#9999cc",
            progress_color="#4444aa",
            fg_color="#222233",
            command=self._on_opacity_change,
        )
        self._opacity_slider.set(0.95)
        self._opacity_slider.pack(side="left", padx=(8, 0), pady=5)

        # 🧪 Simulation button
        self._sim_btn = ctk.CTkButton(
            self._footer_frame,
            text="🧪 סימולציה",
            width=90, height=26,
            corner_radius=6,
            font=ctk.CTkFont("Arial", 10),
            fg_color="#2a2a4a",
            hover_color="#4a4a7a",
            command=self._run_simulation,
        )
        self._sim_btn.pack(side="right", pady=5)

        # 📋 History button
        self._hist_btn = ctk.CTkButton(
            self._footer_frame,
            text="📋 היסטוריה",
            width=90, height=26,
            corner_radius=6,
            font=ctk.CTkFont("Arial", 10),
            fg_color="#1a3a2a",
            hover_color="#2a5a3a",
            command=self._open_history,
        )
        self._hist_btn.pack(side="right", pady=5, padx=(0, 6))

    # ── Poller wiring ─────────────────────────────────────────────────────────
    def _start_poller(self) -> None:
        self._poller = AlertPoller(
            on_alert=lambda a: self.after(0, self._show_alert, a),
            on_clear=lambda: self.after(0, self._clear_alert),
            on_connection_change=lambda c: self.after(0, self._set_connected, c),
        )
        self._poller.start()

    # ── Alert state ───────────────────────────────────────────────────────────
    def _show_alert(self, alert: AlertData) -> None:
        self._alert_active = True
        self._alert_count += 1
        self._alert_title_lbl.configure(text=alert.title or "אזעקת צבע אדום")
        self._alert_areas_lbl.configure(text=alert.areas_text)
        self._alert_time_lbl.configure(text=f"⏰  {alert.time_str}")
        self._counter_lbl.configure(text=f"סה״כ אזעקות: {self._alert_count}")
        self._status_label.configure(text="🚨  אזעקת צבע אדום פעילה!", text_color="#FF4444")
        self._alert_panel.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT_ALERT}")
        if self._flash_job:
            self.after_cancel(self._flash_job)
        self._flash_loop()
        play_alert_sound()
        # If hiding in tray — pop window to front + show balloon
        if self._tray_mode:
            self.deiconify()
            self._tray_notify(alert)
        self.attributes("-topmost", True)
        self.lift()

    def _clear_alert(self) -> None:
        self._alert_active = False
        if self._flash_job:
            self.after_cancel(self._flash_job)
            self._flash_job = None
        self._alert_panel.pack_forget()
        self.configure(fg_color=IDLE_BG)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT_IDLE}")
        self._status_label.configure(
            text="✅  אין אזעקות פעילות  |  מחובר לפיקוד העורף",
            text_color="#66FF99",
        )

    def _flash_loop(self) -> None:
        if not self._alert_active:
            return
        col = ALERT_COLOR_LIGHT if self._flash_state else ALERT_COLOR
        self._alert_panel.configure(fg_color=col)
        self.configure(fg_color=col if self._flash_state else "#1a0000")
        self._flash_state = not self._flash_state
        self._flash_job = self.after(FLASH_MS, self._flash_loop)

    # ── Connection indicator ──────────────────────────────────────────────────
    def _set_connected(self, connected: bool) -> None:
        if connected:
            self._status_dot.configure(text_color="#00FF88")
            if not self._alert_active:
                self._status_label.configure(
                    text="✅  אין אזעקות פעילות  |  מחובר לפיקוד העורף",
                    text_color="#66FF99",
                )
        else:
            self._status_dot.configure(text_color="#FF8800")
            self._status_label.configure(
                text="⚠️  מנסה להתחבר לשרת פיקוד העורף...",
                text_color="#FF8800",
            )

    # ── Controls ──────────────────────────────────────────────────────────────
    def _toggle_minimize(self) -> None:
        if self._minimized:
            self._status_frame.pack(fill="x", padx=10, pady=(6, 4))
            self._footer_frame.pack(fill="x", padx=10, pady=(2, 6))
            if self._alert_active:
                self._alert_panel.pack(fill="both", expand=True, padx=10, pady=(0, 4))
            h = WINDOW_HEIGHT_ALERT if self._alert_active else WINDOW_HEIGHT_IDLE
            self.geometry(f"{WINDOW_WIDTH}x{h}")
            self._minimized = False
        else:
            self._status_frame.pack_forget()
            self._footer_frame.pack_forget()
            self._alert_panel.pack_forget()
            self.geometry(f"{WINDOW_WIDTH}x44")
            self._minimized = True

    def _run_simulation(self) -> None:
        """Fires a fake 5-second alert for testing."""
        self._show_alert(AlertData(
            alert_id=f"sim-{time.time()}",
            title="אזעקת צבע אדום [סימולציה]",
            areas=["תל אביב - מרכז העיר", "רמת גן", "גבעתיים", "בני ברק"],
        ))
        self.after(5000, self._clear_alert)

    def _on_opacity_change(self, value: float) -> None:
        """Updates window transparency from slider."""
        self.attributes("-alpha", value)

    def _open_history(self) -> None:
        """Opens a separate history window and fetches alert history in background."""
        # If window already open, just bring it to front
        if hasattr(self, "_history_win") and self._history_win.winfo_exists():
            self._history_win.lift()
            return

        win = ctk.CTkToplevel(self)
        win.title("היסטוריית אזעקות")
        win.geometry("560x480+120+120")
        win.configure(fg_color="#0d1117")
        win.attributes("-topmost", True)
        win.resizable(True, True)
        self._history_win = win

        # ── Header ────────────────────────────────────────────────────────────
        ctk.CTkLabel(
            win, text="📋  היסטוריית אזעקות אחרונות",
            font=ctk.CTkFont("Arial", 14, "bold"), text_color="#FFFFFF",
        ).pack(pady=(14, 4))

        # ── Status label ──────────────────────────────────────────────────────
        self._hist_status = ctk.CTkLabel(
            win, text="⏳  טוען נתונים...",
            font=ctk.CTkFont("Arial", 11), text_color="#AAAAAA",
        )
        self._hist_status.pack(pady=(0, 6))

        # ── Scrollable list ───────────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(win, fg_color="#0d1117", corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self._hist_scroll = scroll

        # ── Refresh button ────────────────────────────────────────────────────
        ctk.CTkButton(
            win, text="🔄 רענן", width=100, height=30,
            font=ctk.CTkFont("Arial", 11),
            fg_color="#1a3a2a", hover_color="#2a5a3a",
            command=lambda: threading.Thread(target=self._fetch_history, daemon=True).start(),
        ).pack(pady=(0, 12))

        # Fetch in background
        threading.Thread(target=self._fetch_history, daemon=True).start()

    def _fetch_history(self) -> None:
        """Fetches alert history from Oref API and populates the history window."""
        urls_to_try = [
            OREF_HISTORY_URL,
            "https://www.oref.org.il/WarningMessages/History/AlertsHistory.json",
            "https://alerts-history.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=he&mode=1",
        ]
        last_error = ""
        for url in urls_to_try:
            try:
                self.after(0, self._hist_status.configure,
                           {"text": f"⏳  מנסה {url.split('/')[-1]}...", "text_color": "#AAAAAA"})
                with httpx.Client(timeout=10.0, headers=HEADERS, follow_redirects=True) as client:
                    resp = client.get(url)

                logger.info("History response: status=%d, len=%d, preview=%r",
                            resp.status_code, len(resp.text), resp.text[:120])

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    continue

                content = resp.text.strip()
                if not content or content.startswith("<"):
                    last_error = "שרת החזיר HTML במקום JSON (גישה נדחתה)"
                    continue

                data = json.loads(content)
                if not isinstance(data, list) or not data:
                    last_error = "תגובה ריקה מהשרת"
                    continue

                # Log first record so we can see the real field names/format
                logger.info("History first record: %s", json.dumps(data[0], ensure_ascii=False))

                self.after(0, self._populate_history, data[:50])
                return

            except httpx.TimeoutException:
                last_error = "timeout — השרת לא הגיב תוך 10 שניות"
            except httpx.ConnectError as exc:
                last_error = f"שגיאת חיבור: {exc}"
            except json.JSONDecodeError as exc:
                last_error = f"JSON לא תקין: {exc}"
            except Exception as exc:
                last_error = str(exc)
                logger.error("History fetch error: %s", exc, exc_info=True)

        self.after(0, self._hist_status.configure,
                   {"text": f"❌  {last_error}", "text_color": "#FF6666"})

    def _populate_history(self, records: list) -> None:
        """Renders history records into the scrollable frame."""
        # Clear previous rows
        for widget in self._hist_scroll.winfo_children():
            widget.destroy()

        self._hist_status.configure(
            text=f"✅  נמצאו {len(records)} אזעקות אחרונות", text_color="#66FF99"
        )

        # Column headers
        header = ctk.CTkFrame(self._hist_scroll, fg_color="#1a1a3a", corner_radius=6, height=28)
        header.pack(fill="x", pady=(0, 4))
        header.pack_propagate(False)
        for txt, w in [("תאריך", 100), ("סוג", 190), ("אזורים", 220)]:
            ctk.CTkLabel(
                header, text=txt, width=w,
                font=ctk.CTkFont("Arial", 10, "bold"), text_color="#AAAAFF",
                anchor="center",
            ).pack(side="right", padx=4)

        # Rows
        for i, rec in enumerate(records):
            row_color = "#111122" if i % 2 == 0 else "#0d1020"
            row = ctk.CTkFrame(self._hist_scroll, fg_color=row_color, corner_radius=4, height=26)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            date_str = str(rec.get("alertDate", rec.get("date", rec.get("time", "")))).strip()

            # Try to parse multiple possible formats:
            # "DD/MM/YYYY HH:MM:SS", "YYYY-MM-DDTHH:MM:SS", "DD/MM/YYYY HH:MM"
            date_part, time_part = "—", "—"
            for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(date_str, fmt)
                    date_part = dt.strftime("%d/%m/%Y")
                    time_part = dt.strftime("%H:%M")
                    break
                except ValueError:
                    continue

            # Fallback: split on space or T
            if date_part == "—" and date_str:
                sep = "T" if "T" in date_str else " "
                parts = date_str.split(sep, 1)
                date_part = parts[0] if parts else "—"
                time_part = parts[1][:5] if len(parts) > 1 else "—"

            title = str(rec.get("title", "אזעקה")).strip() or "אזעקה"
            city  = str(rec.get("data", "")).strip() or "—"

            for txt, w, color in [
                (date_part, 100, "#CCCCCC"),
                (title,     190, "#FF9999"),
                (city,      220, "#FFDDDD"),
            ]:
                ctk.CTkLabel(
                    row, text=txt, width=w,
                    font=ctk.CTkFont("Arial", 9), text_color=color,
                    anchor="center",
                ).pack(side="right", padx=4)

    def _toggle_tray_mode(self) -> None:
        """Toggles tray mode — hides window, shows tray icon, pops on alert only."""
        if not TRAY_AVAILABLE:
            tk.messagebox.showinfo(
                "חסר מודול",
                "להפעלת מגש יש להתקין:\npip install pystray Pillow",
            )
            return

        self._tray_mode = not self._tray_mode

        if self._tray_mode:
            self._tray_btn.configure(fg_color="#226622", text="🔕")
            self._start_tray_icon()
            self.withdraw()           # hide main window
        else:
            self._tray_btn.configure(fg_color="#3a3a5c", text="🔔")
            self._stop_tray_icon()
            self.deiconify()          # show main window

    def _make_tray_image(self) -> "Image.Image":
        """Creates a simple red shield icon for the tray."""
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(180, 0, 0, 255))
        draw.text((20, 18), "🛡", fill=(255, 255, 255, 255))
        return img

    def _start_tray_icon(self) -> None:
        """Creates and starts the system tray icon in a background thread."""
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "1.ico")
            if os.path.exists(icon_path):
                img = Image.open(icon_path)
            else:
                img = self._make_tray_image()
        except Exception:
            img = self._make_tray_image()

        menu = pystray.Menu(
            pystray.MenuItem("פתח חלון", self._tray_show_window, default=True),
            pystray.MenuItem("סגור", self._on_close),
        )
        self._tray_icon = pystray.Icon(APP_NAME, img, "Red Alert Monitor", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _stop_tray_icon(self) -> None:
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    def _tray_show_window(self) -> None:
        """Called from tray menu — shows the main window."""
        self.after(0, self.deiconify)
        self.after(0, self.lift)

    def _tray_notify(self, alert: AlertData) -> None:
        """Shows a tray notification balloon when in tray mode."""
        if self._tray_icon and TRAY_AVAILABLE:
            try:
                self._tray_icon.notify(
                    title="🚨 אזעקת צבע אדום",
                    message=alert.areas_text[:200],
                )
            except Exception:
                pass

    def _toggle_startup(self) -> None:
        """Toggles Windows startup registration."""
        current = is_startup_enabled()
        set_startup(not current)
        if not current:
            self._startup_btn.configure(fg_color="#335500")
            tk.messagebox.showinfo("הפעלה עם Windows",
                                   "✅ התוכנה תעלה אוטומטית עם הפעלת המחשב.")
        else:
            self._startup_btn.configure(fg_color="#553300")
            tk.messagebox.showinfo("הפעלה עם Windows",
                                   "❌ התוכנה הוסרה מהפעלה אוטומטית.")

    def _on_close(self) -> None:
        self._stop_tray_icon()
        self._poller.stop()
        self.destroy()


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = RedAlertApp()
    app.mainloop()
