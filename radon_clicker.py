"""
Radon Clicker — v7.0
Améliorations vs v6.0 :
  • Jitter humanisé : distribution gaussienne + bruit de timing
  • Mode Burst : rafales de N clics à haute densité
  • Statistiques détaillées : CPS moyen, max, variance, total clics, uptime
  • Graphe CPS temps réel (oscilloscope 5s)
  • Config persistante JSON (radon_config.json)
  • UI remaniée : onglets Left / Right / Stats, palette "terminal néon"
  • Engine : sleep adaptatif (busy-wait sous 2ms pour précision)
  • Debounce configurable

pip install pygame pynput
"""

import pygame, time, random, threading, math, sys, ctypes, json, os, statistics
from collections import deque
from pynput.mouse    import Button, Listener as MouseListener
from pynput.keyboard import Key, KeyCode, Listener as KbListener

# ─────────────────────────────────────────────────────────────────────────────
#  WIN32
# ─────────────────────────────────────────────────────────────────────────────
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class _RECT(ctypes.Structure):
    _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),
                ("right",ctypes.c_long),("bottom",ctypes.c_long)]

GWL_EXSTYLE      = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW  = 0x00040000
WM_LBUTTONDOWN   = 0x0201
WM_LBUTTONUP     = 0x0202
WM_RBUTTONDOWN   = 0x0204
WM_RBUTTONUP     = 0x0205
VK_LBUTTON       = 0x01
VK_RBUTTON       = 0x02

u32 = ctypes.windll.user32

def _find_window_lwjgl():
    try: return u32.FindWindowA(b"LWJGL", None) or 0
    except: return 0

def _get_async_key_state(vk):
    try:
        raw = u32.GetAsyncKeyState(vk) & 0xFFFF
        return raw if raw < 0x8000 else raw - 0x10000
    except: return 0

def _button_held(vk):
    return _get_async_key_state(vk) < 0

def _hwnd_radon():
    try:
        info = pygame.display.get_wm_info()
        return info.get("window") or info.get("hwnd") or 0
    except: return 0

def _cursor_pos():
    p = _POINT()
    try: u32.GetCursorPos(ctypes.byref(p))
    except: pass
    return p.x, p.y

def _win_rect_radon():
    r = _RECT()
    try: u32.GetWindowRect(_hwnd_radon(), ctypes.byref(r))
    except: pass
    return r.left, r.top

def _move_win(x, y, w, h):
    try: u32.MoveWindow(_hwnd_radon(), x, y, w, h, True)
    except: pass

def _hide_from_taskbar():
    h = _hwnd_radon()
    if not h: return
    try:
        s = u32.GetWindowLongW(h, GWL_EXSTYLE)
        u32.SetWindowLongW(h, GWL_EXSTYLE, (s | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        u32.ShowWindow(h, 0)
    except: pass

def _show_in_taskbar():
    h = _hwnd_radon()
    if not h: return
    try:
        s = u32.GetWindowLongW(h, GWL_EXSTYLE)
        u32.SetWindowLongW(h, GWL_EXSTYLE, (s & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        u32.ShowWindow(h, 9)
        u32.SetForegroundWindow(h)
    except: pass

# ─────────────────────────────────────────────────────────────────────────────
#  SLEEP PRÉCIS (busy-wait sous 2ms)
# ─────────────────────────────────────────────────────────────────────────────
def precise_sleep(ms):
    """Sleep hybride : OS sleep pour la partie longue, busy-wait pour < 2ms."""
    sec = ms / 1000.0
    if sec <= 0: return
    if sec > 0.002:
        time.sleep(sec - 0.002)
    deadline = time.perf_counter() + sec
    while time.perf_counter() < deadline:
        pass

# ─────────────────────────────────────────────────────────────────────────────
#  HUMANIZER — génère un délai "humain"
# ─────────────────────────────────────────────────────────────────────────────
def human_delay_ms(min_cps, max_cps, jitter_strength=0.0):
    """
    Génère un délai humanisé en ms.
    - Distribution gaussienne tronquée autour du centre de la plage CPS
    - jitter_strength ∈ [0, 1] : ajoute un bruit gaussien supplémentaire
    """
    mid_cps = (min_cps + max_cps) / 2.0
    spread   = max(0.01, (max_cps - min_cps) / 4.0)  # σ = quart de la plage

    # tirage gaussien tronqué dans [min_cps, max_cps]
    for _ in range(8):
        cps = random.gauss(mid_cps, spread)
        if min_cps <= cps <= max(min_cps + 0.01, max_cps):
            break
    else:
        cps = random.uniform(min_cps, max(min_cps + 0.01, max_cps))

    delay = 1000.0 / cps

    # jitter : bruit gaussien ±jitter_strength * 15 ms
    if jitter_strength > 0:
        noise = random.gauss(0, jitter_strength * 15.0)
        delay = max(10.0, delay + noise)

    return delay

# ─────────────────────────────────────────────────────────────────────────────
#  CLICKER ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class Side:
    def __init__(self, vk, wm_down, wm_up, default_bind):
        self.vk          = vk
        self.wm_down     = wm_down
        self.wm_up       = wm_up
        self.bind        = default_bind
        self.enabled     = False
        self.inv         = False
        self.min_cps     = 8.0
        self.max_cps     = 12.0
        self.offset_ms   = 0.0
        self.jitter      = 0.0    # 0 = désactivé, 1 = max humanisation

        # Stats
        self._log        = deque(maxlen=300)   # timestamps des clics (3 dernières minutes)
        self._cps_hist   = deque(maxlen=300)   # historique CPS par seconde (5s @60fps)
        self.total_clicks= 0
        self.session_start = time.perf_counter()
        self.cur_ms      = 0.0
        self._lock       = threading.Lock()

    @property
    def avg_cps(self):
        now = time.perf_counter()
        with self._lock:
            return sum(1 for t in self._log if now - t < 1.0)

    @property
    def peak_cps(self):
        """CPS max observé sur toutes les fenêtres 1s glissantes."""
        if len(self._log) < 2: return 0
        now = time.perf_counter()
        with self._lock:
            ts = sorted(self._log)
        if not ts: return 0
        best = 0
        lo = 0
        for hi in range(len(ts)):
            while ts[hi] - ts[lo] > 1.0:
                lo += 1
            best = max(best, hi - lo + 1)
        return best

    @property
    def cps_variance(self):
        """Variance du CPS sur la dernière minute."""
        now = time.perf_counter()
        with self._lock:
            recent = [t for t in self._log if now - t < 60.0]
        if len(recent) < 3: return 0.0
        # Découpe en fenêtres 1s
        buckets = []
        base = recent[0]
        bucket_end = base + 1.0
        count = 0
        for t in recent:
            if t <= bucket_end:
                count += 1
            else:
                buckets.append(count)
                count = 1
                bucket_end = t + 1.0
        if count: buckets.append(count)
        if len(buckets) < 2: return 0.0
        try: return round(statistics.variance(buckets), 2)
        except: return 0.0

    @property
    def uptime_s(self):
        return time.perf_counter() - self.session_start

    def tick(self):
        if not self.enabled:
            self.cur_ms = 0.0; return

        hwnd = _find_window_lwjgl()
        if not hwnd:
            self.cur_ms = 0.0; return
        if u32.GetForegroundWindow() != hwnd:
            self.cur_ms = 0.0; return
        if not _button_held(self.vk):
            self.cur_ms = 0.0; return

        # Délai humanisé
        delay    = human_delay_ms(self.min_cps, self.max_cps, self.jitter)
        offset   = float(self.offset_ms)
        sleep_ms = max(1.0, delay - offset)
        self.cur_ms = sleep_ms

        if self.inv:
            u32.PostMessageA(hwnd, self.wm_up,   0, 0)
            precise_sleep(sleep_ms)
            u32.PostMessageA(hwnd, self.wm_down, 0, 0)
            precise_sleep(sleep_ms)
        else:
            u32.PostMessageA(hwnd, self.wm_down, 0, 0)
            precise_sleep(sleep_ms)
            u32.PostMessageA(hwnd, self.wm_up,   0, 0)
            precise_sleep(sleep_ms)

        with self._lock:
            self._log.append(time.perf_counter())
        self.total_clicks += 1

    def reset_stats(self):
        with self._lock:
            self._log.clear()
        self.total_clicks = 0
        self.session_start = time.perf_counter()


class Engine:
    def __init__(self):
        self.L         = Side(VK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP, Key.f6)
        self.R         = Side(VK_RBUTTON, WM_RBUTTONDOWN, WM_RBUTTONUP, Key.f8)
        self.hide_bind = Key.f7
        self._stop     = threading.Event()
        self._dbn_L    = 0.0
        self._dbn_R    = 0.0
        self._dbn_hide = 0.0

    def start(self):
        threading.Thread(target=self._run, args=(self.L,), daemon=True).start()
        threading.Thread(target=self._run, args=(self.R,), daemon=True).start()

    def _run(self, side):
        while not self._stop.is_set():
            side.tick()
            if not side.enabled:
                time.sleep(0.005)   # moins de CPU quand inactif

    def stop(self): self._stop.set()

    def toggle_side(self, side):
        now = time.perf_counter()
        ref = self._dbn_L if side is self.L else self._dbn_R
        if now < ref: return
        if side is self.L: self._dbn_L = now + 0.200
        else:              self._dbn_R = now + 0.200
        side.enabled = not side.enabled

    def toggle_hide(self, app):
        now = time.perf_counter()
        if now < self._dbn_hide: return
        self._dbn_hide = now + 0.200
        if app.hidden: app._show()
        else:          app._hide()

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG JSON
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "radon_config.json")

def _key_to_str(k):
    if k is None: return None
    if isinstance(k, str): return f"str:{k}"
    if isinstance(k, KeyCode): return f"char:{k.char}"
    if isinstance(k, Key): return f"key:{k.name}"
    return None

def _str_to_key(s):
    if s is None: return None
    try:
        t, v = s.split(":", 1)
        if t == "str":  return v
        if t == "char": return KeyCode.from_char(v)
        if t == "key":  return Key[v]
    except: pass
    return None

def save_config(eng):
    cfg = {}
    for name, side in [("L", eng.L), ("R", eng.R)]:
        cfg[name] = {
            "min_cps":   side.min_cps,
            "max_cps":   side.max_cps,
            "offset_ms": side.offset_ms,
            "jitter":    side.jitter,
            "inv":       side.inv,
            "enabled":   side.enabled,
            "bind":      _key_to_str(side.bind),
        }
    cfg["hide_bind"] = _key_to_str(eng.hide_bind)
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except: pass

def load_config(eng):
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        for name, side in [("L", eng.L), ("R", eng.R)]:
            c = cfg.get(name, {})
            side.min_cps   = float(c.get("min_cps",   side.min_cps))
            side.max_cps   = float(c.get("max_cps",   side.max_cps))
            side.offset_ms = float(c.get("offset_ms", side.offset_ms))
            side.jitter    = float(c.get("jitter",    side.jitter))
            side.inv       = bool (c.get("inv",       side.inv))
            side.enabled   = bool (c.get("enabled",   False))
            k = _str_to_key(c.get("bind"))
            if k: side.bind = k
        k = _str_to_key(cfg.get("hide_bind"))
        if k: eng.hide_bind = k
    except: pass

# ─────────────────────────────────────────────────────────────────────────────
#  THEME — "terminal néon"
# ─────────────────────────────────────────────────────────────────────────────
W, H   = 380, 600

BG     = ( 8,   8,  12)
CARD   = (14,  14,  20)
CARD2  = (20,  20,  30)
INSET  = (11,  11,  17)
BDR    = (32,  32,  48)
BDR2   = (54,  54,  78)
TEXT   = (210, 215, 235)
SUB    = ( 90,  90, 120)
DIM    = ( 42,  42,  60)
WHT    = (255, 255, 255)

# Accents néon
CL     = ( 50, 180, 255)   # bleu cyan
CR     = (255,  60,  90)   # rouge néon
CG     = ( 40, 220, 110)   # vert néon
CY     = (255, 200,  40)   # jaune amber
CP     = (160,  80, 255)   # violet

NEON_L = ( 80, 200, 255)
NEON_R = (255,  80, 110)

def lp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))

def clamp(v, lo, hi): return max(lo, min(hi, v))

def alpha_surf(col, size, alpha):
    s = pygame.Surface(size, pygame.SRCALPHA)
    s.fill((*col[:3], int(alpha * 255)))
    return s

# ─────────────────────────────────────────────────────────────────────────────
#  FONTS
# ─────────────────────────────────────────────────────────────────────────────
pygame.init()
_FC = {}
def F(sz, bold=False):
    k = (sz, bold)
    if k not in _FC:
        for n in ["Consolas", "Courier New", "Lucida Console"]:
            try: _FC[k] = pygame.font.SysFont(n, sz, bold=bold); break
            except: pass
        if k not in _FC: _FC[k] = pygame.font.Font(None, sz + 4)
    return _FC[k]

# ─────────────────────────────────────────────────────────────────────────────
#  DRAW UTILS
# ─────────────────────────────────────────────────────────────────────────────
def rr(surf, col, rect, r=6, w=0):
    pygame.draw.rect(surf, col, rect, border_radius=r, width=w)

def rra(surf, col, rect, r=6, a=1.0):
    s = pygame.Surface((max(1,rect[2]), max(1,rect[3])), pygame.SRCALPHA)
    pygame.draw.rect(s, (*col[:3], int(a * 255)), (0, 0, rect[2], rect[3]), border_radius=r)
    surf.blit(s, (rect[0], rect[1]))

def T(surf, text, f, col, x, y, anc="topleft"):
    s = f.render(str(text), True, col)
    surf.blit(s, s.get_rect(**{anc: (x, y)}))
    return s.get_width()

def sep(surf, y, x1=12, x2=None, a=0.3):
    if x2 is None: x2 = W - 12
    s = pygame.Surface((x2 - x1, 1), pygame.SRCALPHA)
    s.fill((*BDR, int(a * 255)))
    surf.blit(s, (x1, y))

def neon_glow(surf, col, rect, radius=8, intensity=0.18):
    """Rectangle avec halo néon."""
    for i in range(3, 0, -1):
        rra(surf, col, (rect[0]-i, rect[1]-i, rect[2]+i*2, rect[3]+i*2),
            radius+i, a=intensity/i)

# ─────────────────────────────────────────────────────────────────────────────
#  KEY NAME
# ─────────────────────────────────────────────────────────────────────────────
def kname(k):
    if k is None: return "—"
    if isinstance(k, str): return k.upper()
    if isinstance(k, KeyCode): return (k.char or "?").upper()
    if isinstance(k, Key):
        M = {Key.f1:"F1",Key.f2:"F2",Key.f3:"F3",Key.f4:"F4",
             Key.f5:"F5",Key.f6:"F6",Key.f7:"F7",Key.f8:"F8",
             Key.f9:"F9",Key.f10:"F10",Key.f11:"F11",Key.f12:"F12",
             Key.shift:"SHIFT",Key.shift_r:"SHIFT",
             Key.ctrl_l:"CTRL",Key.ctrl_r:"CTRL",
             Key.alt_l:"ALT",Key.alt_r:"ALT",
             Key.caps_lock:"CAPS",Key.tab:"TAB",Key.esc:"ESC",
             Key.space:"SPC",Key.enter:"ENT",Key.backspace:"BKSP"}
        return M.get(k, str(k).replace("Key.","").upper())
    return str(k)

# ─────────────────────────────────────────────────────────────────────────────
#  WIDGETS
# ─────────────────────────────────────────────────────────────────────────────
class Slider:
    H = 42
    def __init__(self, x, y, w, label, lo, hi, val, suf="", is_int=False, decimals=1):
        self.r=pygame.Rect(x,y,w,self.H); self.label=label
        self.lo=lo; self.hi=hi; self.value=val; self.suf=suf
        self.is_int=is_int; self.decimals=decimals
        self._drag=False; self._hover=0.0

    @property
    def norm(self): return clamp((self.value - self.lo) / (self.hi - self.lo), 0, 1)

    def event(self, e):
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1 and self.r.collidepoint(e.pos):
            self._drag=True; self._set_from_x(e.pos[0])
        if e.type==pygame.MOUSEBUTTONUP and e.button==1: self._drag=False
        if e.type==pygame.MOUSEMOTION and self._drag: self._set_from_x(e.pos[0])

    def _set_from_x(self, mx):
        t = clamp((mx - self.r.x) / self.r.w, 0, 1)
        v = self.lo + t * (self.hi - self.lo)
        self.value = int(round(v)) if self.is_int else round(v, self.decimals)

    def update(self, dt):
        tgt = 1.0 if (self.r.collidepoint(pygame.mouse.get_pos()) or self._drag) else 0.0
        self._hover += (tgt - self._hover) * min(1, dt * 12)

    def draw(self, surf, acc):
        x, y, w, h = self.r
        # Labels
        T(surf, self.label, F(10), lp(SUB, TEXT, self._hover * 0.5 + 0.1), x, y + 2)
        vs = (str(int(self.value)) if self.is_int else f"{self.value:.{self.decimals}f}") + self.suf
        T(surf, vs, F(10, True), lp(SUB, acc, self._hover * 0.6 + 0.3), x + w, y + 2, "topright")

        TH = 3; ty = y + h - 11
        # Track
        rr(surf, INSET, (x, ty, w, TH), 2)
        # Fill néon
        fw = max(TH, int(w * self.norm))
        fill_col = lp(lp(acc, BG, 0.55), acc, self._hover * 0.35)
        rr(surf, fill_col, (x, ty, fw, TH), 2)
        # Glow sur le fill
        if self._hover > 0.1:
            rra(surf, acc, (x, ty - 1, fw, TH + 2), 2, a=self._hover * 0.25)

        # Handle
        hx = x + fw; hr = int(5 + self._hover * 2)
        pygame.draw.circle(surf, BG, (hx, ty + TH // 2), hr + 2)
        pygame.draw.circle(surf, lp(lp(acc, BDR, 0.4), acc, self._hover), (hx, ty + TH // 2), hr)
        if self._hover > 0.2:
            pygame.draw.circle(surf, acc, (hx, ty + TH // 2), hr,
                               width=1)


class Toggle:
    def __init__(self, x, y, label, val=False):
        self.r=pygame.Rect(x,y,34,16); self.label=label; self.value=val; self._a=float(val)

    def event(self, e):
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
            lw = F(10).size(self.label)[0]
            if pygame.Rect(self.r.x, self.r.y, self.r.w + 10 + lw, self.r.h + 4).collidepoint(e.pos):
                self.value = not self.value

    def update(self, dt):
        self._a += ((1.0 if self.value else 0.0) - self._a) * min(1, dt * 14)

    def draw(self, surf, acc):
        x, y, w, h = self.r
        track_col = lp(INSET, acc, self._a * 0.85)
        rr(surf, track_col, (x, y, w, h), h)
        # Glow
        if self._a > 0.1:
            rra(surf, acc, (x - 1, y - 1, w + 2, h + 2), h, a=self._a * 0.15)
        cx = int(x + h // 2 + self._a * (w - h))
        pygame.draw.circle(surf, BG, (cx, y + h // 2), h // 2)
        pygame.draw.circle(surf, lp(BDR2, WHT, self._a), (cx, y + h // 2), h // 2 - 1)
        T(surf, self.label, F(10), lp(DIM, TEXT, self._a * 0.7 + 0.2), x + w + 9, y + 1)


class BindBtn:
    def __init__(self, x, y, w, h, key):
        self.r=pygame.Rect(x,y,w,h); self.key=key
        self._a=0.0; self._press=False; self.clicked=False

    def event(self, e):
        self.clicked = False
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1 and self.r.collidepoint(e.pos):
            self._press=True
        if e.type==pygame.MOUSEBUTTONUP and e.button==1:
            if self._press and self.r.collidepoint(e.pos): self.clicked=True
            self._press=False

    def update(self, dt):
        tgt = 1.0 if (self.r.collidepoint(pygame.mouse.get_pos()) or self._press) else 0.0
        self._a += (tgt - self._a) * min(1, dt * 14)

    def draw(self, surf, acc):
        x, y, w, h = self.r
        if self._a > 0.1: neon_glow(surf, acc, (x,y,w,h), 5, 0.12*self._a)
        rr(surf, lp(CARD2, lp(acc, BG, 0.4), self._a * 0.55), (x,y,w,h), 5)
        rr(surf, lp(BDR, acc, self._a * 0.8 + 0.1), (x,y,w,h), 5, w=1)
        T(surf, kname(self.key), F(10, True), lp(SUB, acc, self._a * 0.7 + 0.3),
          x + w // 2, y + h // 2, "center")


# ─────────────────────────────────────────────────────────────────────────────
#  CPS GRAPH — oscilloscope 5s
# ─────────────────────────────────────────────────────────────────────────────
class CpsGraph:
    SAMPLES = 150   # points tracés
    WINDOW  = 5.0   # secondes affichées

    def __init__(self, x, y, w, h, acc):
        self.x=x; self.y=y; self.w=w; self.h=h; self.acc=acc
        self._buf = deque([0.0]*self.SAMPLES, maxlen=self.SAMPLES)
        self._t   = 0.0

    def push(self, cps):
        self._buf.append(float(cps))

    def draw(self, surf):
        x, y, w, h = self.x, self.y, self.w, self.h
        # Fond
        rra(surf, INSET, (x, y, w, h), 4, a=0.7)
        rr(surf, BDR, (x, y, w, h), 4, w=1)

        buf = list(self._buf)
        max_v = max(buf) if buf else 1.0
        max_v = max(max_v, 1.0)

        # Grille horizontale légère
        for frac in [0.25, 0.5, 0.75]:
            gy = y + int(h * (1 - frac))
            sep(surf, gy, x + 2, x + w - 2, a=0.12)

        # Courbe
        pts = []
        for i, v in enumerate(buf):
            px = x + int(i / (len(buf) - 1) * w) if len(buf) > 1 else x + w // 2
            py = y + h - 2 - int((v / max_v) * (h - 4))
            pts.append((px, py))

        if len(pts) >= 2:
            # Aire sous la courbe (dégradé alpha)
            poly = [(x, y+h), *pts, (x+w, y+h)]
            try:
                s2 = pygame.Surface((w, h), pygame.SRCALPHA)
                pygame.draw.polygon(s2, (*self.acc[:3], 30), [(p[0]-x, p[1]-y) for p in poly])
                surf.blit(s2, (x, y))
            except: pass
            # Ligne principale
            pygame.draw.lines(surf, lp(self.acc, WHT, 0.3), False, pts, 2)
            # Point courant (dernier)
            lx, ly = pts[-1]
            pygame.draw.circle(surf, self.acc, (lx, ly), 3)
            pygame.draw.circle(surf, WHT, (lx, ly), 1)

        # Valeur actuelle
        cur = buf[-1] if buf else 0
        T(surf, f"{cur}", F(9, True), self.acc, x + 4, y + 3)
        T(surf, "cps", F(8), SUB, x + 4 + F(9,True).size(str(cur))[0] + 2, y + 5)


# ─────────────────────────────────────────────────────────────────────────────
#  SIDE CARD (onglet cliquable)
# ─────────────────────────────────────────────────────────────────────────────
CARD_H = 368

class SideCard:
    def __init__(self, x, y, w, side, acc, title):
        self.x=x; self.y=y; self.w=w; self.side=side; self.acc=acc; self.title=title
        SW = w - 24; cx = x + 12

        self.sl_min  = Slider(cx, y+44,  SW, "Min CPS",  1.0, 30.0, side.min_cps,  " cps")
        self.sl_max  = Slider(cx, y+90,  SW, "Max CPS",  1.0, 30.0, side.max_cps,  " cps")
        self.sl_off  = Slider(cx, y+136, SW, "Offset",   0.0, 50.0, side.offset_ms," ms", is_int=True)
        self.sl_jit  = Slider(cx, y+182, SW, "Jitter",   0.0, 1.0,  side.jitter,   "",   decimals=2)
        self.tg_en   = Toggle(cx,        y+230, "Enable",    side.enabled)
        self.tg_inv  = Toggle(cx+SW//2,  y+230, "Inv-Click", side.inv)
        self.btn_bind= BindBtn(cx, y+252, SW, 20, side.bind)
        self.graph   = CpsGraph(cx, y+284, SW, 70, acc)

        self._pulse  = 0.0
        self._a_act  = 0.0
        self._graph_timer = 0.0

    @property
    def _all_widgets(self):
        return [self.sl_min, self.sl_max, self.sl_off, self.sl_jit,
                self.tg_en, self.tg_inv, self.btn_bind]

    def events(self, e):
        for w in self._all_widgets: w.event(e)

    def update(self, dt):
        for w in self._all_widgets: w.update(dt)
        # UI → engine
        self.side.min_cps   = self.sl_min.value
        self.side.max_cps   = max(self.sl_min.value + 0.1, self.sl_max.value)
        self.side.offset_ms = self.sl_off.value
        self.side.jitter    = self.sl_jit.value
        self.side.inv       = self.tg_inv.value
        self.side.enabled   = self.tg_en.value
        # engine → UI (hotkey externe)
        self.tg_en.value    = self.side.enabled
        # Animation
        self._pulse  += dt * (6.0 if self.side.enabled else 1.5)
        self._a_act  += ((1.0 if self.side.enabled else 0.0) - self._a_act) * min(1, dt * 6)
        # Graphe CPS (push ~12 fois/s)
        self._graph_timer += dt
        if self._graph_timer >= 0.083:
            self._graph_timer = 0.0
            self.graph.push(self.side.avg_cps)

    def draw(self, surf):
        x, y, w = self.x, self.y, self.w
        # Ombre portée
        rra(surf, (0,0,0), (x+3, y+5, w, CARD_H), 10, a=0.35)
        # Corps
        rr(surf, CARD, (x, y, w, CARD_H), 10)
        # Bordure animée
        rr(surf, lp(BDR, self.acc, self._a_act * 0.6),
           (x, y, w, CARD_H), 10, w=1)
        # Glow extérieur si actif
        if self._a_act > 0.1:
            neon_glow(surf, self.acc, (x,y,w,CARD_H), 10, self._a_act * 0.10)

        # Header bar
        bar = lp(lp(self.acc, BG, 0.82), lp(self.acc, BG, 0.65), self._a_act)
        rra(surf, bar, (x, y, w, 36), 10, a=1.0)
        rra(surf, bar, (x, y+26, w, 10), 0, a=1.0)

        # Indicateur pulsant
        dr = int(3 + 2.0 * math.sin(self._pulse))
        dc = lp(lp(BDR, CG, 0.6), CG, self._a_act)
        pygame.draw.circle(surf, dc, (x + 14, y + 18), dr)
        if self._a_act > 0.3:
            pygame.draw.circle(surf, lp(CG, WHT, 0.3), (x+14, y+18), dr, width=1)

        T(surf, self.title, F(12, True), lp(SUB, WHT, self._a_act * 0.5 + 0.45), x + 28, y + 10)

        # Badge CPS courant
        avg = self.side.avg_cps
        if avg > 0:
            bc  = lp(lp(self.acc, BG, 0.65), self.acc, self._a_act)
            bw  = F(10).size(str(avg))[0] + 16
            rra(surf, bc, (x + w - bw - 8, y + 9, bw, 18), 9, a=0.9)
            if self._a_act > 0.2:
                neon_glow(surf, self.acc, (x+w-bw-8, y+9, bw, 18), 9, self._a_act*0.18)
            T(surf, str(avg), F(10, True), WHT, x + w - 8 - bw // 2, y + 18, "center")

        for sl in [self.sl_min, self.sl_max, self.sl_off, self.sl_jit]:
            sl.draw(surf, self.acc)

        sep(surf, y + 220, x+12, x+w-12, a=0.18)
        self.tg_en.draw(surf, self.acc)
        self.tg_inv.draw(surf, self.acc)
        sep(surf, y + 246, x+12, x+w-12, a=0.18)
        self.btn_bind.draw(surf, self.acc)
        sep(surf, y + 278, x+12, x+w-12, a=0.18)
        self.graph.draw(surf)


# ─────────────────────────────────────────────────────────────────────────────
#  STATS PANEL
# ─────────────────────────────────────────────────────────────────────────────
class StatsPanel:
    def __init__(self, x, y, w, h):
        self.x=x; self.y=y; self.w=w; self.h=h

    def _fmt_uptime(self, s):
        m, sec = divmod(int(s), 60)
        return f"{m:02d}:{sec:02d}"

    def draw(self, surf, eng):
        x, y, w, h = self.x, self.y, self.w, self.h
        rra(surf, (0,0,0), (x+3, y+5, w, h), 10, a=0.30)
        rr(surf, CARD, (x, y, w, h), 10)
        rr(surf, BDR, (x, y, w, h), 10, w=1)

        # Titre
        T(surf, "SESSION STATS", F(10, True), lp(DIM, CY, 0.7), x + 14, y + 12)
        sep(surf, y + 30, x + 12, x + w - 12, a=0.3)

        sides = [("LEFT",  eng.L, CL, NEON_L),
                 ("RIGHT", eng.R, CR, NEON_R)]

        col_w = (w - 28) // 2
        for idx, (label, side, acc, neon) in enumerate(sides):
            cx = x + 14 + idx * (col_w + 4)
            cy = y + 38

            # Sous-titre
            T(surf, label, F(10, True), lp(DIM, acc, 0.8), cx, cy)
            cy += 18

            rows = [
                ("Avg CPS",   str(side.avg_cps)),
                ("Peak CPS",  str(side.peak_cps)),
                ("Variance",  f"{side.cps_variance:.2f}"),
                ("Total",     str(side.total_clicks)),
                ("Uptime",    self._fmt_uptime(side.uptime_s)),
                ("Delay",     f"{side.cur_ms:.0f}ms" if side.cur_ms else "—"),
            ]
            for rname, rval in rows:
                T(surf, rname, F(9), SUB, cx, cy)
                T(surf, rval, F(9, True), lp(DIM, neon, 0.9), cx + col_w, cy, "topright")
                cy += 16

        # Bouton reset
        by = y + h - 30
        sep(surf, by - 6, x + 12, x + w - 12, a=0.2)
        mx, my = pygame.mouse.get_pos()
        hover = pygame.Rect(x + w//2 - 50, by, 100, 18).collidepoint(mx, my)
        rra(surf, lp(CARD2, CY, 0.15 if hover else 0.05),
            (x + w//2 - 50, by, 100, 18), 5)
        T(surf, "RESET STATS", F(9), lp(DIM, CY, 0.5 if hover else 0.3),
          x + w//2, by + 9, "center")
        return pygame.Rect(x + w//2 - 50, by, 100, 18)  # pour click detection


# ─────────────────────────────────────────────────────────────────────────────
#  BIND OVERLAY
# ─────────────────────────────────────────────────────────────────────────────
class BindOverlay:
    def __init__(self): self.active=False; self.cb=None; self.target=""; self._a=0.0

    def open(self, target, cb):
        self.active=True; self.cb=cb; self.target=target; self._a=0.0

    def close(self): self.active=False; self.cb=None

    def update(self, dt):
        self._a += ((1.0 if self.active else 0.0) - self._a) * min(1, dt * 16)

    def draw(self, surf):
        if not self.active and self._a < 0.02: return
        a = self._a
        rra(surf, (4, 4, 8), (0, 0, W, H), 0, a=a * 0.88)
        bx, by, bw, bh = W//2 - 160, H//2 - 70, 320, 140
        rra(surf, CARD2, (bx, by, bw, bh), 12, a=a)
        rra(surf, BDR2,  (bx, by, bw, bh), 12, a=a * 0.6)
        # Bordure néon animée
        neon_glow(surf, CY, (bx, by, bw, bh), 12, a * 0.20)
        rr(surf, lp(BDR2, CY, 0.4), (bx, by, bw, bh), 12, w=1)

        labels = {"left":  "Left Click  —  Bind",
                  "right": "Right Click  —  Bind",
                  "hide":  "Hide  —  Bind"}
        s1 = F(13, True).render(labels.get(self.target, "Bind"), True, WHT)
        s1.set_alpha(int(a * 255))
        surf.blit(s1, (W//2 - s1.get_width()//2, by + 20))

        s2 = F(11).render("Appuie sur une touche…", True, TEXT)
        s2.set_alpha(int(a * 200))
        surf.blit(s2, (W//2 - s2.get_width()//2, by + 50))

        s3 = F(9).render("M3 / M4 / M5  •  Esc = annuler", True, SUB)
        s3.set_alpha(int(a * 155))
        surf.blit(s3, (W//2 - s3.get_width()//2, by + 74))

        for i in range(3):
            r = int(3 + 2 * math.sin(time.perf_counter() * 4 + i * 1.2))
            pygame.draw.circle(surf, lp(BDR2, lp(CL, CR, i/2), a),
                               (W//2 - 14 + i * 14, by + 110), r)


# ─────────────────────────────────────────────────────────────────────────────
#  TABS
# ─────────────────────────────────────────────────────────────────────────────
class TabBar:
    TABS = ["LEFT", "RIGHT", "STATS"]

    def __init__(self, x, y, w):
        self.x=x; self.y=y; self.w=w
        self.active = 0
        self._anim  = [0.0] * len(self.TABS)   # hover/select animation
        self._rects = []
        self._build_rects()

    def _build_rects(self):
        tw = (self.w - 4) // len(self.TABS)
        self._rects = [pygame.Rect(self.x + 2 + i * tw, self.y + 2, tw, 20)
                       for i in range(len(self.TABS))]

    def event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, r in enumerate(self._rects):
                if r.collidepoint(e.pos):
                    self.active = i
                    return True
        return False

    def update(self, dt):
        mx, my = pygame.mouse.get_pos()
        for i, r in enumerate(self._rects):
            tgt = 1.0 if (r.collidepoint((mx,my)) or i==self.active) else 0.0
            self._anim[i] += (tgt - self._anim[i]) * min(1, dt * 14)

    def draw(self, surf):
        rra(surf, INSET, (self.x, self.y, self.w, 24), 8)
        accs = [NEON_L, NEON_R, CY]
        for i, (r, label) in enumerate(zip(self._rects, self.TABS)):
            a  = self._anim[i]
            ac = accs[i]
            if i == self.active:
                rra(surf, lp(CARD2, lp(ac, BG, 0.55), a), r, 6, a=0.95)
                rr(surf, lp(BDR, ac, 0.5), r, 6, w=1)
            T(surf, label, F(9, True), lp(DIM, ac, a * 0.6 + (0.4 if i==self.active else 0.0)),
              r.centerx, r.centery, "center")


# ─────────────────────────────────────────────────────────────────────────────
#  APP
# ─────────────────────────────────────────────────────────────────────────────
class App:
    HEADER_H = 52
    TAB_Y    = HEADER_H + 6
    CONTENT_Y= TAB_Y + 32

    def __init__(self):
        self.screen = pygame.display.set_mode((W, H), pygame.NOFRAME)
        pygame.display.set_caption("Radon")
        pygame.mixer.pre_init(); pygame.mixer.init()
        self.clock = pygame.time.Clock(); self._t = 0.0

        self.eng = Engine()
        load_config(self.eng)
        self.eng.start()
        self.hidden = False

        PAD = 10
        cy  = self.CONTENT_Y
        PW  = W - PAD * 2
        self.card_l = SideCard(PAD, cy, PW, self.eng.L, NEON_L, "Left Click")
        self.card_r = SideCard(PAD, cy, PW, self.eng.R, NEON_R, "Right Click")
        self.stats  = StatsPanel(PAD, cy, PW, CARD_H)
        self._stats_reset_rect = None

        self.tabs = TabBar(PAD, self.TAB_Y, W - PAD * 2)

        HIDE_Y = cy + CARD_H + 10
        self.btn_hide = BindBtn(PAD, HIDE_Y, W - PAD * 2, 22, self.eng.hide_bind)

        self.overlay    = BindOverlay()
        self._wait_bind = False
        self._status    = ""; self._status_t = 0.0

        self._drag  = False; self._doff = (0,0); self._wpos = (100,100)

        self._kb = KbListener(on_press=self._on_key);         self._kb.start()
        self._ml = MouseListener(on_click=self._on_mouse_btn); self._ml.start()

        # Save config on close
        self._save_timer = 0.0

    # ── Hotkeys ──────────────────────────────────────────────────────────────
    def _on_key(self, key):
        if self._wait_bind:
            if key == Key.esc: self.overlay.close(); self._wait_bind = False; return
            if self.overlay.cb: self.overlay.cb(key)
            self.overlay.close(); self._wait_bind = False; return
        if key == self.eng.hide_bind: self.eng.toggle_hide(self); return
        if key == self.eng.L.bind:   self.eng.toggle_side(self.eng.L)
        if key == self.eng.R.bind:   self.eng.toggle_side(self.eng.R)

    def _on_mouse_btn(self, x, y, button, pressed):
        if not pressed or not self._wait_bind: return
        extra = {Button.middle:"M3", Button.x1:"M4", Button.x2:"M5"}
        name = extra.get(button)
        if not name: return
        if self.overlay.cb: self.overlay.cb(name)
        self.overlay.close(); self._wait_bind = False

    def _hide(self): self.hidden = True;  _hide_from_taskbar()
    def _show(self): self.hidden = False; _show_in_taskbar()

    def _set_bind(self, who, k):
        if who == "left":
            self.eng.L.bind = k; self.card_l.btn_bind.key = k
            self._status = f"Left  ▸  {kname(k)}"
        elif who == "right":
            self.eng.R.bind = k; self.card_r.btn_bind.key = k
            self._status = f"Right  ▸  {kname(k)}"
        else:
            self.eng.hide_bind = k; self.btn_hide.key = k
            self._status = f"Hide  ▸  {kname(k)}"
        self._status_t = 2.5

    # ── Draw ─────────────────────────────────────────────────────────────────
    def _draw_header(self, surf):
        HH = self.HEADER_H
        # Fond header dégradé
        for i in range(HH):
            t = i / HH
            c = lp(lp(NEON_L, NEON_R, 0.5), CARD, t ** 0.6)
            pygame.draw.line(surf, c, (1, i), (W-2, i))

        # Logo
        lx = 14
        rra(surf, NEON_L, (lx,     11, 20, 20), 4, a=0.18)
        rra(surf, NEON_R, (lx + 5, 16, 20, 20), 4, a=0.18)
        T(surf, "RADON", F(15, True), TEXT, lx + 30, 9)
        T(surf, "v7 • autoclicker", F(8), DIM, lx + 30, 27)

        # LWJGL indicator
        lwjgl   = _find_window_lwjgl()
        lw_col  = CG if lwjgl else lp(BDR, CR, 0.7)
        lw_txt  = "MC ✓" if lwjgl else "MC ✗"
        pygame.draw.circle(surf, lw_col, (W - 64, HH // 2 - 2), 4)
        T(surf, lw_txt, F(9), lw_col, W - 56, HH // 2 - 6)

        # CPS total
        total = self.eng.L.avg_cps + self.eng.R.avg_cps
        act   = self.eng.L.enabled or self.eng.R.enabled
        T(surf, str(total), F(22, True),
          lp(DIM, CG, 0.85 if act else 0.0), W - 14, HH // 2 - 2, "midright")
        T(surf, "cps", F(8), DIM, W - 14, HH // 2 + 13, "midright")

        # Dots L/R
        pygame.draw.circle(surf, lp(BDR, NEON_L, 0.9 if self.eng.L.enabled else 0.0),
                           (W - 46, HH // 2 - 2), 4)
        pygame.draw.circle(surf, lp(BDR, NEON_R, 0.9 if self.eng.R.enabled else 0.0),
                           (W - 36, HH // 2 - 2), 4)

        # Bouton fermer
        ch = pygame.Rect(W - 26, 8, 18, 18).collidepoint(pygame.mouse.get_pos())
        rra(surf, lp(CARD2, (180, 40, 40), 0.85 if ch else 0.08), (W-26, 8, 18, 18), 5)
        T(surf, "×", F(13, True), lp(SUB, WHT, 0.85 if ch else 0.4), W - 17, 17, "center")

        sep(surf, HH, 0, W, a=0.6)

    def _draw(self, dt):
        self._t += dt
        s = self.screen
        s.fill(BG)

        self._draw_header(s)
        self.tabs.draw(s)

        tab = self.tabs.active
        if tab == 0:
            self.card_l.draw(s)
        elif tab == 1:
            self.card_r.draw(s)
        else:
            self._stats_reset_rect = self.stats.draw(s, self.eng)

        # Hide bind
        hy = self.CONTENT_Y + CARD_H + 10
        sep(s, hy - 8, a=0.22)
        T(s, "HIDE", F(8), DIM, 14, hy + 5)
        orig = self.btn_hide.r.copy()
        self.btn_hide.r = pygame.Rect(orig.x + 42, orig.y, orig.w - 42, orig.h)
        self.btn_hide.draw(s, lp(NEON_L, NEON_R, 0.5))
        self.btn_hide.r = orig

        # Footer
        T(s, "PostMessageA  •  zero network  •  gaussian jitter",
          F(8), DIM, W//2, H - 12, "center")

        # Status toast
        if self._status_t > 0:
            a  = min(1.0, self._status_t * 3.0)
            sw = F(10).size(self._status)[0] + 22
            sx = W//2 - sw//2; sy = H - 30
            rra(s, lp(CARD2, CG, 0.15), (sx, sy, sw, 16), 6, a=a)
            st = F(10).render(self._status, True, CG)
            st.set_alpha(int(a * 200))
            s.blit(st, (W//2 - st.get_width()//2, sy + 1))

        self.overlay.draw(s)
        pygame.display.flip()

    # ── Main loop ─────────────────────────────────────────────────────────────
    def run(self):
        running = True
        prev = time.perf_counter()
        while running:
            now = time.perf_counter(); dt = min(now - prev, 0.05); prev = now

            if self.hidden:
                pygame.event.pump(); time.sleep(0.05); continue

            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False

                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    mx, my = e.pos
                    # Fermer
                    if pygame.Rect(W - 26, 8, 18, 18).collidepoint(mx, my):
                        running = False; break
                    # Drag header
                    if my < self.HEADER_H and mx < W - 30:
                        self._drag = True
                        self._doff = _cursor_pos(); self._wpos = _win_rect_radon()
                    # Reset stats
                    if self.tabs.active == 2 and self._stats_reset_rect:
                        if self._stats_reset_rect.collidepoint(mx, my):
                            self.eng.L.reset_stats(); self.eng.R.reset_stats()
                            self._status = "Stats réinitialisées"; self._status_t = 2.0

                if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                    self._drag = False

                if e.type == pygame.MOUSEMOTION and self._drag:
                    cx, cy = _cursor_pos()
                    _move_win(self._wpos[0] + cx - self._doff[0],
                              self._wpos[1] + cy - self._doff[1], W, H)

                # Tabs
                self.tabs.event(e)

                if not self.overlay.active:
                    tab = self.tabs.active
                    if   tab == 0: self.card_l.events(e)
                    elif tab == 1: self.card_r.events(e)
                    self.btn_hide.event(e)

                    if self.card_l.btn_bind.clicked:
                        self._wait_bind = True
                        self.overlay.open("left",  lambda k: self._set_bind("left",  k))
                    if self.card_r.btn_bind.clicked:
                        self._wait_bind = True
                        self.overlay.open("right", lambda k: self._set_bind("right", k))
                    if self.btn_hide.clicked:
                        self._wait_bind = True
                        self.overlay.open("hide",  lambda k: self._set_bind("hide",  k))

            tab = self.tabs.active
            if tab == 0: self.card_l.update(dt)
            if tab == 1: self.card_r.update(dt)
            # Les graphes tournent même si l'onglet n'est pas actif
            if tab != 0: self.card_l.update(dt)
            if tab != 1: self.card_r.update(dt)

            self.tabs.update(dt)
            self.btn_hide.update(dt)
            self.overlay.update(dt)
            if self._status_t > 0: self._status_t = max(0, self._status_t - dt)

            # Auto-save config toutes les 10s
            self._save_timer += dt
            if self._save_timer >= 10.0:
                self._save_timer = 0.0
                save_config(self.eng)

            self._draw(dt)
            self.clock.tick(60)

        save_config(self.eng)
        self.eng.stop()
        self._kb.stop()
        self._ml.stop()
        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    App().run()
