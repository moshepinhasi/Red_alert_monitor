# Red Alert Monitor — צבע אדום

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

> **EN:** A real-time desktop alert application that notifies you of Red Alert (Tzeva Adom) emergency warnings issued by Israel's Home Front Command (Pikud HaOref).
>
> **HE:** תוכנה שמתחברת ל-API של פיקוד העורף ומציגה התראות **צבע אדום** בזמן אמת על שולחן העבודה שלך — עם צליל, הבהוב חזותי ואינטגרציה מלאה עם מגש המערכת.

---

## Features / תכונות

| Feature | Description |
|---------|-------------|
| Real-time alerts | Polls the Oref API every 1.5 seconds |
| Visual flash | Red flashing window when an alert is active |
| Audio alerts | Platform-native sounds (beep / Sosumi / freedesktop) |
| System tray | Minimize to tray; window pops up automatically on alert |
| Startup on boot | One-click toggle to run on Windows startup (registry) |
| Alert history | View the last 50 alerts from the Oref history API |
| Opacity control | Adjustable window transparency (0.2 – 1.0) |
| Always on top | Window stays above all other applications |
| Simulation mode | 5-second test alert to verify everything is working |
| Cross-platform | Windows, macOS, and Linux support |
| Hebrew UI | Bilingual interface (Hebrew / English) |

---

## Screenshots

> _Alert state — the window flashes red and lists the affected areas:_

```
┌─────────────────────────────────────────┐
│  ● Red Alert Monitor          _    ✕   │
├─────────────────────────────────────────┤
│                                         │
│         🚨 אזעקת צבע אדום 🚨            │
│                                         │
│  • תל אביב - מרכז העיר                  │
│  • רמת גן                               │
│  • גבעתיים                              │
│                                         │
│  13:45:22                 סה"כ: 3      │
└─────────────────────────────────────────┘
```

---

## Requirements

- Python 3.8 or higher
- The packages listed in `requirements.txt`:

```
customtkinter
httpx
Pillow       (optional — for tray icon)
pystray      (optional — for system tray)
```

---

## להורדה והתקנה מהירה /  Quick download and installation

👇🏻👇🏻
https://github.com/moshepinhasi/Red_alert_monitor/releases/download/red_alert_monitor/RedAlertMonitor.exe
👆🏻👆🏻



## Installation


```bash
# 1. Clone the repository
git clone https://github.com/moshepinhasi/Red_alert_monitor.git
cd Red_alert_monitor

# 2. (Recommended) Create a virtual environment
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the application
python red_alert_monitor.py
```

On **Windows**, you can also double-click `red_alert_monitor.py` — it will automatically relaunch without a console window using `pythonw.exe`.

---

## Usage

| Button | Action |
|--------|--------|
| ⚡ | Toggle "run on Windows startup" |
| 🔔 | Toggle system tray mode |
| 🧪 | Trigger a 5-second test alert |
| 📋 | Open alert history window |
| Opacity slider | Adjust window transparency |

- When an alert is active the window expands, flashes red, and plays a sound.
- Up to 8 affected areas are shown; if more exist, a count is displayed.
- The connection status dot is **green** when online and **orange** when disconnected.

---

## Platform Support

| OS | Audio | System Tray | Startup |
|----|-------|-------------|---------|
| Windows | `winsound` beeps | `pystray` | Registry |
| macOS | `afplay` (Sosumi) | `pystray` | — |
| Linux | `paplay` (alarm) | `pystray` | — |

---

## Contributing

Contributions are welcome!

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m "Add my feature"`
4. Push the branch: `git push origin feature/my-feature`
5. Open a Pull Request

Please keep code style consistent with the existing file and test your changes before submitting.

---

## License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE) for details.

---

## Disclaimer

This application is a third-party tool and is **not affiliated with or endorsed by Pikud HaOref** (Israel's Home Front Command). Always follow official instructions during an emergency.
