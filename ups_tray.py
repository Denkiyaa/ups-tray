#!/usr/bin/env python3
"""UPS Tray — system tray monitor for UPS devices (NUT/upsc or apcupsd/apcaccess, PyQt6).

Battery icon + notifications in the tray, click for a detail window with history chart.
Usage: ups_tray.py [--show] [--ups NAME]
"""
import collections, json, locale, os, shutil, subprocess, sys, time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QPointF
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QMainWindow, QMenu, QPlainTextEdit, QProgressBar, QPushButton,
                             QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QToolTip,
                             QVBoxLayout, QWidget)

APP = "UPS Tray"
APP_ID = "ups-tray"
POLL_SEC = 5
KEEP_SEC = 24 * 3600
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_ID
HIST_FILE = DATA_DIR / "history.jsonl"
EVENTS_FILE = Path("/var/log/apcupsd.events")
LOW_CHARGE = 30  # warn below this % while on battery
SOCKET_NAME = f"{APP_ID}-{os.getuid()}"

GREEN, RED, ORANGE, GRAY, BLUE = (QColor(c) for c in ("#16a34a", "#dc2626", "#d97706", "#6b7280", "#2563eb"))


# ---------------------------------------------------------------- i18n

def _detect_lang():
    lang = os.environ.get("UPS_TRAY_LANG") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") \
        or os.environ.get("LANG") or (locale.getlocale()[0] or "")
    return "tr" if lang.lower().startswith("tr") else "en"


LANG = _detect_lang()

TR = {
    "Online": "Şebekede", "On battery": "Aküde", "Charging": "Şarj oluyor", "Low battery": "Akü düşük",
    "No communication": "Bağlantı yok", "Shutting down": "Kapanıyor", "Overload": "Aşırı yük",
    "Replace battery": "Akü değişmeli", "Calibrating": "Kalibrasyon", "Trimming voltage": "Voltaj düşürülüyor",
    "Boosting voltage": "Voltaj yükseltiliyor", "No battery": "Akü yok",
    "Model": "Model", "Status": "Durum", "Line voltage": "Şebeke voltajı", "Load": "Yük",
    "Battery charge": "Akü şarjı", "Runtime left": "Kalan süre", "Shutdown charge threshold": "Kapanma şarj eşiği",
    "Shutdown runtime threshold": "Kapanma süre eşiği", "Max time on battery": "Aküde azami süre",
    "Sensitivity": "Hassasiyet", "Low transfer voltage": "Alt geçiş voltajı",
    "High transfer voltage": "Üst geçiş voltajı", "Alarm delay": "Alarm gecikmesi", "Battery voltage": "Akü voltajı",
    "Last transfer reason": "Son aküye geçiş sebebi", "Transfers to battery": "Aküye geçiş sayısı",
    "Last on battery": "Son aküye geçiş", "Time on battery (now)": "Şu an aküde geçen süre",
    "Total time on battery": "Toplam aküde süre", "Last back on line": "Son şebekeye dönüş",
    "Self test result": "Self test sonucu", "Serial no": "Seri no", "Battery date": "Akü tarihi",
    "Nominal input V": "Nominal giriş V", "Nominal battery V": "Nominal akü V", "Nominal power": "Nominal güç",
    "Firmware": "Firmware", "Host": "Bilgisayar", "apcupsd version": "apcupsd sürümü", "UPS name": "UPS adı",
    "Cable": "Kablo", "Driver": "Sürücü", "Mode": "Mod", "apcupsd started": "apcupsd başlangıç",
    "Read at": "Okuma zamanı", "Status flags": "Durum bayrakları",
    "Cannot read UPS": "UPS okunamıyor", "apcaccess did not respond": "apcaccess yanıt vermedi",
    "No UPS defined in NUT (upsc -l is empty)": "NUT'ta tanımlı UPS yok (upsc -l boş)",
    "upsc did not respond": "upsc yanıt vermedi",
    "{h} h {m} min": "{h} sa {m} dk", "{m} min {s} s": "{m} dk {s} sn", "{s} s": "{s} sn",
    "No data yet": "Henüz veri yok", "Charge": "Şarj", "Line": "Şebeke",
    "Last outage": "Son kesinti", "History": "Geçmiş", "Overview": "Genel", "Details": "Ayrıntılar",
    "Events": "Olaylar", "Field": "Alan", "Value": "Değer", "Key": "Anahtar", "No entries": "Kayıt yok",
    "Cannot read {f}: {e}": "{f} okunamadı: {e}", "serial": "seri", "last read": "son okuma",
    "on battery for {d}": "aküde {d}", "estimated": "tahmini", "transfer range {lo}–{hi} V": "geçiş aralığı {lo}–{hi} V",
    "nominal {v} V": "nominal {v} V", "{r} · {n} total": "{r} · toplam {n} kez",
    "Open window": "Pencereyi aç", "Quit": "Çıkış",
    "UPS is running on battery": "UPS aküde çalışıyor", "Runtime left ≈ {t}": "Kalan süre ≈ {t}",
    "Lost connection to UPS": "UPS bağlantısı koptu", "UPS connection restored": "UPS bağlantısı geri geldi",
    "Power failure — UPS on battery": "Elektrik kesildi — UPS aküde",
    "Charge {c} %, runtime ≈ {t}": "Şarj {c} %, kalan süre ≈ {t}", "Power restored": "Elektrik geri geldi",
    "Time on battery: {t}": "Aküde geçen süre: {t}", "Battery getting low": "Akü azalıyor",
    "Charge {c} % · Left {t}": "Şarj {c} % · Kalan {t}", "Load {l} %": "Yük {l} %",
    "15 min": "15 dk", "1 h": "1 sa", "6 h": "6 sa", "24 h": "24 sa",
}


def _(s, /, **kw):
    s = TR.get(s, s) if LANG == "tr" else s
    return s.format(**kw) if kw else s


STATUS_NAMES = {"ONLINE": "Online", "ONBATT": "On battery", "CHARGING": "Charging", "LOWBATT": "Low battery",
                "COMMLOST": "No communication", "SHUTTING": "Shutting down", "OVERLOAD": "Overload",
                "REPLACEBATT": "Replace battery", "CAL": "Calibrating", "TRIM": "Trimming voltage",
                "BOOST": "Boosting voltage", "NOBATT": "No battery"}

LABELS = {
    "MODEL": "Model", "STATUS": "Status", "LINEV": "Line voltage", "LOADPCT": "Load", "BCHARGE": "Battery charge",
    "TIMELEFT": "Runtime left", "MBATTCHG": "Shutdown charge threshold", "MINTIMEL": "Shutdown runtime threshold",
    "MAXTIME": "Max time on battery", "SENSE": "Sensitivity", "LOTRANS": "Low transfer voltage",
    "HITRANS": "High transfer voltage", "ALARMDEL": "Alarm delay", "BATTV": "Battery voltage",
    "LASTXFER": "Last transfer reason", "NUMXFERS": "Transfers to battery", "XONBATT": "Last on battery",
    "TONBATT": "Time on battery (now)", "CUMONBATT": "Total time on battery", "XOFFBATT": "Last back on line",
    "SELFTEST": "Self test result", "SERIALNO": "Serial no", "BATTDATE": "Battery date", "NOMINV": "Nominal input V",
    "NOMBATTV": "Nominal battery V", "NOMPOWER": "Nominal power", "FIRMWARE": "Firmware", "HOSTNAME": "Host",
    "VERSION": "apcupsd version", "UPSNAME": "UPS name", "CABLE": "Cable", "DRIVER": "Driver",
    "UPSMODE": "Mode", "STARTTIME": "apcupsd started", "DATE": "Read at", "STATFLAG": "Status flags",
}


# ---------------------------------------------------------------- data

def read_apcupsd():
    try:
        p = subprocess.run(["apcaccess", "status"], capture_output=True, text=True, timeout=5)
    except Exception as e:
        return {"_error": str(e), "_backend": "apcupsd"}
    d = {}
    for line in p.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    if "STATUS" not in d:
        d["_error"] = (p.stderr or p.stdout).strip() or _("apcaccess did not respond")
    d["_backend"] = "apcupsd"
    return d


# Map NUT variables to apcupsd names so the window and history work with a single format
NUT_MAP = {"device.model": "MODEL", "ups.model": "MODEL", "battery.charge": "BCHARGE", "ups.load": "LOADPCT",
           "input.voltage": "LINEV", "battery.voltage": "BATTV", "battery.voltage.nominal": "NOMBATTV",
           "ups.realpower.nominal": "NOMPOWER", "ups.serial": "SERIALNO", "device.serial": "SERIALNO",
           "input.transfer.low": "LOTRANS", "input.transfer.high": "HITRANS", "input.transfer.reason": "LASTXFER",
           "input.voltage.nominal": "NOMINV", "ups.firmware": "FIRMWARE", "battery.charge.low": "MBATTCHG",
           "input.sensitivity": "SENSE", "battery.date": "BATTDATE", "ups.test.result": "SELFTEST",
           "driver.name": "DRIVER"}
NUT_STATUS = {"OL": "ONLINE", "OB": "ONBATT", "LB": "LOWBATT", "CHRG": "CHARGING", "RB": "REPLACEBATT",
              "OVER": "OVERLOAD", "TRIM": "TRIM", "BOOST": "BOOST", "CAL": "CAL", "FSD": "SHUTTING"}
_nut = {"ups": None, "onbatt_since": None}


def read_nut():
    try:
        if not _nut["ups"]:
            names = subprocess.run(["upsc", "-l"], capture_output=True, text=True, timeout=5).stdout.split()
            if not names:
                return {"_error": _("No UPS defined in NUT (upsc -l is empty)"), "_backend": "nut"}
            _nut["ups"] = names[0]
        p = subprocess.run(["upsc", _nut["ups"]], capture_output=True, text=True, timeout=5)
    except Exception as e:
        return {"_error": str(e), "_backend": "nut"}
    raw = {}
    for line in p.stdout.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            raw[k.strip()] = v.strip()
    if "ups.status" not in raw:
        if "@" not in (_nut["ups"] or "") and not os.environ.get("UPS_TRAY_UPS"):
            _nut["ups"] = None
        return {"_error": (p.stderr or _("upsc did not respond")).strip(), "_backend": "nut"}
    d = {"UPSNAME": _nut["ups"], "DRIVER": "NUT"}
    for k, v in raw.items():
        if k in NUT_MAP:
            d[NUT_MAP[k]] = v
    # Some APC models (e.g. Back-UPS BGM) report "OL DISCHRG" while on mains; trust OL/OB, ignore DISCHRG
    words = [NUT_STATUS.get(w, w) for w in raw["ups.status"].split() if w != "DISCHRG"]
    d["STATUS"] = " ".join(words)
    if "battery.runtime" in raw:
        d["TIMELEFT"] = f"{num(raw['battery.runtime']) / 60:.1f} Minutes"
    if "battery.runtime.low" in raw:
        d["MINTIMEL"] = f"{num(raw['battery.runtime.low']) / 60:.0f} Minutes"
    if "NOMPOWER" in d:
        d["NOMPOWER"] += " Watts"
    if "ONBATT" in words:
        if _nut["onbatt_since"] is None:
            _nut["onbatt_since"] = time.time()
        d["XONBATT"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_nut["onbatt_since"]))
        d["TONBATT"] = f"{time.time() - _nut['onbatt_since']:.0f} Seconds"
    else:
        _nut["onbatt_since"] = None
    for k, v in raw.items():  # unmapped variables still show up in details
        d.setdefault(k, v)
    d["_backend"] = "nut"
    return d


def read_ups():
    return read_nut() if shutil.which("upsc") else read_apcupsd()


def num(v):
    try:
        return float(str(v).split()[0])
    except Exception:
        return None


def status_words(d):
    return d.get("STATUS", "").split()


def status_color(d):
    if d.get("_error"):
        return GRAY
    w = status_words(d)
    if any(x in w for x in ("ONBATT", "LOWBATT", "OVERLOAD", "COMMLOST", "SHUTTING", "REPLACEBATT")):
        return RED
    if w == ["ONLINE"]:
        return GREEN
    return ORANGE


def status_text(d):
    if d.get("_error"):
        return _("Cannot read UPS")
    return ", ".join(_(STATUS_NAMES.get(x, x)) for x in status_words(d)) or "?"


def fmt_dur(sec):
    if sec is None:
        return "–"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return _("{h} h {m} min", h=h, m=m) if h else (_("{m} min {s} s", m=m, s=s) if m else _("{s} s", s=s))


def watts(d):
    ld, nom = num(d.get("LOADPCT")), num(d.get("NOMPOWER"))
    return None if ld is None or nom is None else nom * ld / 100


class History:
    """Samples from the last 24 h; appended to a jsonl file and reloaded on start."""

    def __init__(self):
        self.items = collections.deque()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - KEEP_SEC
        try:
            for line in HIST_FILE.read_text().splitlines():
                p = json.loads(line)
                if p["t"] >= cutoff:
                    self.items.append(p)
        except (OSError, ValueError, KeyError):
            pass
        self._rewrite()

    def _rewrite(self):
        try:
            HIST_FILE.write_text("".join(json.dumps(p) + "\n" for p in self.items))
        except OSError:
            pass
        self._writes = 0

    def add(self, d):
        p = {"t": int(time.time()), "charge": num(d.get("BCHARGE")), "load": num(d.get("LOADPCT")),
             "linev": num(d.get("LINEV")), "onbatt": "ONBATT" in status_words(d)}
        self.items.append(p)
        cutoff = time.time() - KEEP_SEC
        while self.items and self.items[0]["t"] < cutoff:
            self.items.popleft()
        self._writes += 1
        if self._writes > 720:  # trim the file roughly once an hour
            self._rewrite()
        else:
            try:
                with HIST_FILE.open("a") as f:
                    f.write(json.dumps(p) + "\n")
            except OSError:
                pass


class Poller(QThread):
    got = pyqtSignal(dict)

    def run(self):
        while not self.isInterruptionRequested():
            self.got.emit(read_ups())
            for _i in range(POLL_SEC * 10):
                if self.isInterruptionRequested():
                    return
                self.msleep(100)


# ---------------------------------------------------------------- tray icon

def battery_icon(d):
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = status_color(d)
    charge = num(d.get("BCHARGE")) or 0
    body = QRectF(4, 14, 50, 36)
    p.setPen(QPen(QColor("#e5e7eb"), 4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(body, 6, 6)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#e5e7eb"))
    p.drawRoundedRect(QRectF(55, 25, 6, 14), 2, 2)
    p.setBrush(col)
    inner = body.adjusted(5, 5, -5, -5)
    p.drawRoundedRect(QRectF(inner.left(), inner.top(), inner.width() * charge / 100, inner.height()), 3, 3)
    if "ONBATT" in status_words(d) or d.get("_error"):
        p.setPen(QPen(QColor("white"), 1))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(30)
        p.setFont(f)
        p.drawText(body, Qt.AlignmentFlag.AlignCenter, "!" if not d.get("_error") else "?")
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- chart

class Chart(QWidget):
    RANGES = [("15 min", 900), ("1 h", 3600), ("6 h", 6 * 3600), ("24 h", KEEP_SEC)]

    def __init__(self, history):
        super().__init__()
        self.history = history
        self.span = 3600
        self.setMinimumHeight(200)
        self.setMouseTracking(True)

    def points(self):
        t0 = time.time() - self.span
        return [p for p in self.history.items if p["t"] >= t0]

    def geom(self):
        r = QRectF(self.rect()).adjusted(36, 8, -10, -22)
        now = time.time()
        X = lambda t: r.left() + (t - (now - self.span)) / self.span * r.width()
        Y = lambda v: r.bottom() - v / 100 * r.height()
        return r, X, Y

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        mut = pal.color(pal.ColorRole.PlaceholderText)
        grid = QColor(mut)
        grid.setAlpha(60)
        r, X, Y = self.geom()
        pts = self.points()
        # shade periods on battery
        onb = QColor(RED)
        onb.setAlpha(45)
        for a, b in zip(pts, pts[1:]):
            if a["onbatt"] and b["t"] - a["t"] < POLL_SEC * 4:
                p.fillRect(QRectF(X(a["t"]), r.top(), X(b["t"]) - X(a["t"]) + 1, r.height()), onb)
        # grid
        p.setFont(QFont(self.font().family(), 8))
        for v in (0, 25, 50, 75, 100):
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(r.left(), Y(v)), QPointF(r.right(), Y(v)))
            p.setPen(mut)
            p.drawText(QRectF(0, Y(v) - 8, r.left() - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v}%")
        now = time.time()
        for i in range(5):
            t = now - self.span + self.span * i / 4
            p.drawText(QRectF(X(t) - 40, r.bottom() + 4, 80, 16), Qt.AlignmentFlag.AlignCenter,
                       time.strftime("%H:%M", time.localtime(t)))
        # lines (broken at data gaps)
        for key, col in (("charge", GREEN), ("load", BLUE)):
            path, prev = QPainterPath(), None
            for pt in pts:
                if pt[key] is None:
                    prev = None
                    continue
                q = QPointF(X(pt["t"]), Y(pt[key]))
                if prev is None or pt["t"] - prev > POLL_SEC * 4:
                    path.moveTo(q)
                else:
                    path.lineTo(q)
                prev = pt["t"]
            p.setPen(QPen(col, 2))
            p.drawPath(path)
        if not pts:
            p.setPen(mut)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, _("No data yet"))

    def mouseMoveEvent(self, e):
        pts = self.points()
        if not pts:
            return
        r, X, _y = self.geom()
        x = e.position().x()
        pt = min(pts, key=lambda p: abs(X(p["t"]) - x))
        f = lambda v, u: "–" if v is None else f"{v:.0f}{u}"
        QToolTip.showText(e.globalPosition().toPoint(),
                          f"{time.strftime('%H:%M:%S', time.localtime(pt['t']))}\n"
                          f"{_('Charge')} {f(pt['charge'], ' %')} · {_('Load')} {f(pt['load'], ' %')}\n"
                          f"{_('Line')} {f(pt['linev'], ' V')}" + (f"\n{_('On battery')}" if pt["onbatt"] else ""),
                          self)


# ---------------------------------------------------------------- window

class Card(QFrame):
    def __init__(self, title, bar=False):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        t = QLabel(title.upper())
        t.setStyleSheet("color: palette(placeholder-text); font-size: 11px; letter-spacing: 1px;")
        self.value = QLabel("–")
        self.value.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.sub = QLabel("")
        self.sub.setStyleSheet("color: palette(placeholder-text); font-size: 12px;")
        lay.addWidget(t)
        lay.addWidget(self.value)
        self.bar = None
        if bar:
            self.bar = QProgressBar()
            self.bar.setTextVisible(False)
            self.bar.setFixedHeight(8)
            lay.addWidget(self.bar)
        lay.addWidget(self.sub)

    def set(self, value, sub="", pct=None, color=None):
        self.value.setText(value)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        if self.bar is not None:
            self.bar.setValue(int(pct or 0))
            if color:
                self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color.name()}; border-radius: 3px; }}")


class Window(QMainWindow):
    def __init__(self, history):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(820, 620)
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # Overview
        page = QWidget()
        v = QVBoxLayout(page)
        head = QHBoxLayout()
        self.title = QLabel(APP)
        self.title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.badge = QLabel("–")
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(self.badge)
        v.addLayout(head)
        self.sub = QLabel("")
        self.sub.setStyleSheet("color: palette(placeholder-text);")
        v.addWidget(self.sub)

        g = QGridLayout()
        self.c_charge = Card(_("Battery charge"), bar=True)
        self.c_load = Card(_("Load"), bar=True)
        self.c_time = Card(_("Runtime left"))
        self.c_line = Card(_("Line"))
        self.c_batt = Card(_("Battery voltage"))
        self.c_xfer = Card(_("Last outage"))
        for i, c in enumerate((self.c_charge, self.c_load, self.c_time, self.c_line, self.c_batt, self.c_xfer)):
            g.addWidget(c, i // 3, i % 3)
        v.addLayout(g)

        rng = QHBoxLayout()
        rng.addWidget(QLabel(_("History")))
        rng.addStretch()
        legend = QLabel(f'<span style="color:{GREEN.name()}">■</span> {_("Charge")} &nbsp; '
                        f'<span style="color:{BLUE.name()}">■</span> {_("Load")} &nbsp; '
                        f'<span style="color:{RED.name()}">■</span> {_("On battery")}')
        rng.addWidget(legend)
        rng.addSpacing(12)
        self.chart = Chart(history)
        grp = QButtonGroup(self)
        for label, span in Chart.RANGES:
            b = QPushButton(_(label))
            b.setCheckable(True)
            b.setChecked(span == self.chart.span)
            b.clicked.connect(lambda _c, s=span: (setattr(self.chart, "span", s), self.chart.update()))
            grp.addButton(b)
            rng.addWidget(b)
        v.addLayout(rng)
        v.addWidget(self.chart, 1)
        tabs.addTab(page, _("Overview"))

        # Details
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_("Field"), _("Value"), _("Key")])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        tabs.addTab(self.table, _("Details"))

        # Events
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setFont(QFont("monospace"))
        tabs.addTab(self.events, _("Events"))
        tabs.currentChanged.connect(lambda i: i == 2 and self.load_events())

    def load_events(self):
        if shutil.which("upsc"):
            p = subprocess.run(["journalctl", "-u", "nut-monitor", "-u", "nut-driver*", "-u", "nut-server",
                                "-n", "300", "--no-pager", "-o", "short-iso", "-r"],
                               capture_output=True, text=True, timeout=5)
            self.events.setPlainText(p.stdout.strip() or p.stderr.strip() or _("No entries"))
            return
        try:
            lines = EVENTS_FILE.read_text(errors="replace").splitlines()
            self.events.setPlainText("\n".join(reversed(lines)) or _("No entries"))
        except OSError as e:
            self.events.setPlainText(_("Cannot read {f}: {e}", f=EVENTS_FILE, e=e))

    def update_data(self, d):
        col = status_color(d)
        self.badge.setText(f"  {status_text(d)}  ")
        self.badge.setStyleSheet(f"background: {col.name()}; color: white; border-radius: 10px; "
                                 "padding: 4px; font-weight: 600;")
        if d.get("_error"):
            self.sub.setText(d["_error"])
            self.chart.update()
            return
        self.title.setText(d.get("MODEL", APP))
        self.sub.setText(f"{d.get('NOMPOWER', '')} · {_('serial')} {d.get('SERIALNO', '?')} · "
                         f"{d['_backend']} · {_('last read')} {time.strftime('%H:%M:%S')}")

        ch, ld, w = num(d.get("BCHARGE")), num(d.get("LOADPCT")), watts(d)
        self.c_charge.set("–" if ch is None else f"{ch:.0f} %", "" if num(d.get("BATTV")) is None else f"{num(d.get('BATTV')):.1f} V",
                          ch, GREEN if (ch or 0) >= LOW_CHARGE else RED)
        self.c_load.set("–" if ld is None else f"{ld:.0f} %", "" if w is None else f"≈ {w:.0f} W",
                        ld, BLUE if (ld or 0) < 80 else RED)
        tl = num(d.get("TIMELEFT"))
        self.c_time.set("–" if tl is None else fmt_dur(tl * 60),
                        _("on battery for {d}", d=fmt_dur(num(d.get("TONBATT"))))
                        if "ONBATT" in status_words(d) else _("estimated"))
        lv, lo, hi = num(d.get("LINEV")), num(d.get("LOTRANS")), num(d.get("HITRANS"))
        self.c_line.set("–" if lv is None else f"{lv:.0f} V",
                        _("transfer range {lo}–{hi} V", lo=f"{lo:.0f}", hi=f"{hi:.0f}") if lo and hi else "")
        bv, nbv = num(d.get("BATTV")), num(d.get("NOMBATTV"))
        self.c_batt.set("–" if bv is None else f"{bv:.1f} V", _("nominal {v} V", v=f"{nbv:.0f}") if nbv else "")
        xon = d.get("XONBATT", "N/A")
        xfer = d.get("LASTXFER", "")
        self.c_xfer.set(xon[11:16] if len(xon) >= 16 else "–",
                        _("{r} · {n} total", r=xfer, n=d["NUMXFERS"]) if "NUMXFERS" in d else xfer)

        self.table.setRowCount(0)
        for k, val in d.items():
            if k.startswith("_") or k in ("APC", "END APC"):
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, txt in enumerate((_(LABELS[k]) if k in LABELS else k, val, k)):
                self.table.setItem(r, c, QTableWidgetItem(txt))
        self.chart.update()
        if self.centralWidget().currentIndex() == 2:
            self.load_events()

    def closeEvent(self, e):  # closing hides to tray
        e.ignore()
        self.hide()


# ---------------------------------------------------------------- app

class TrayApp:
    def __init__(self, app, show):
        self.app = app
        self.history = History()
        self.win = Window(self.history)
        self.last = None
        self.warned_low = False

        self.tray = QSystemTrayIcon(battery_icon({"_error": "x"}))
        self.tray.setToolTip(APP)
        menu = QMenu()
        a_show = QAction(_("Open window"), menu)
        a_show.triggered.connect(self.show_window)
        a_quit = QAction(_("Quit"), menu)
        a_quit.triggered.connect(self.quit)
        menu.addAction(a_show)
        menu.addSeparator()
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_activate)
        self.tray.show()

        self.poller = Poller()
        self.poller.got.connect(self.on_data)
        self.poller.start()
        if show or not QSystemTrayIcon.isSystemTrayAvailable():
            self.show_window()

    def show_window(self):
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.win.isVisible() and self.win.isActiveWindow():
                self.win.hide()
            else:
                self.show_window()

    def notify(self, title, msg, critical=False):
        icon = QSystemTrayIcon.MessageIcon.Critical if critical else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, msg, icon, 10000)

    def on_data(self, d):
        if not d.get("_error"):
            self.history.add(d)
        self.win.update_data(d)
        self.tray.setIcon(battery_icon(d))

        ch, tl = num(d.get("BCHARGE")), num(d.get("TIMELEFT"))
        left = fmt_dur((tl or 0) * 60)
        chs = "–" if ch is None else f"{ch:.0f}"
        if d.get("_error"):
            self.tray.setToolTip(f"{APP}\n{d['_error']}")
        else:
            w = watts(d)
            self.tray.setToolTip(f"{d.get('MODEL', APP)}\n{status_text(d)}\n"
                                 f"{_('Charge {c} % · Left {t}', c=chs, t=left)}\n"
                                 f"{_('Load {l} %', l=d.get('LOADPCT', '–').split()[0])}"
                                 + (f" (≈{w:.0f} W)" if w else ""))

        # notifications on state changes
        prev = self.last
        self.last = d
        if prev is None:
            if "ONBATT" in status_words(d):
                self.notify(_("UPS is running on battery"), _("Runtime left ≈ {t}", t=left), True)
            return
        was_err, is_err = bool(prev.get("_error")), bool(d.get("_error"))
        if is_err and not was_err:
            self.notify(_("Lost connection to UPS"), d["_error"], True)
        elif was_err and not is_err:
            self.notify(_("UPS connection restored"), status_text(d))
        was_b, is_b = "ONBATT" in status_words(prev), "ONBATT" in status_words(d)
        if is_b and not was_b:
            self.notify(_("Power failure — UPS on battery"), _("Charge {c} %, runtime ≈ {t}", c=chs, t=left), True)
        elif was_b and not is_b and not is_err:
            self.notify(_("Power restored"), _("Time on battery: {t}", t=fmt_dur(num(prev.get("TONBATT")))))
        if is_b and ch is not None and ch < LOW_CHARGE and not self.warned_low:
            self.warned_low = True
            self.notify(_("Battery getting low"), _("Charge {c} %, runtime ≈ {t}", c=chs, t=left), True)
        if not is_b:
            self.warned_low = False

    def quit(self):
        self.poller.requestInterruption()
        self.poller.wait(2000)
        self.tray.hide()
        self.app.quit()


def main():
    if "--ups" in sys.argv[:-1]:
        os.environ["UPS_TRAY_UPS"] = sys.argv[sys.argv.index("--ups") + 1]
    if os.environ.get("UPS_TRAY_UPS"):
        _nut["ups"] = os.environ["UPS_TRAY_UPS"]
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon.fromTheme(APP_ID))
    app.setQuitOnLastWindowClosed(False)
    # single instance: if already running, ask it to show its window and exit
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer()
    server.listen(SOCKET_NAME)
    tray = TrayApp(app, "--show" in sys.argv)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), tray.show_window()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
