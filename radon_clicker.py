"""
Radon Clicker — v4.0
pip install pygame pynput
"""

import pygame, time, random, threading, math, sys, ctypes
from collections import deque
from pynput.mouse    import Button, Listener as MouseListener
from pynput.keyboard import Key, KeyCode, Listener as KbListener

# ─────────────────────────────────────────────────────────────────────────────
#  WIN32
# ─────────────────────────────────────────────────────────────────────────────
GWL_EXSTYLE      = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW  = 0x00040000

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class _RECT(ctypes.Structure):
    _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),
                ("right",ctypes.c_long),("bottom",ctypes.c_long)]

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx",ctypes.c_long),("dy",ctypes.c_long),
                ("mouseData",ctypes.c_ulong),("dwFlags",ctypes.c_ulong),
                ("time",ctypes.c_ulong),("dwExtraInfo",ctypes.POINTER(ctypes.c_ulong))]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]

class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT_UNION)]

MELF_LD = 0x0002; MELF_LU = 0x0004
MELF_RD = 0x0008; MELF_RU = 0x0010

def _send(flags):
    try:
        i = _INPUT(0, _INPUT_UNION(mi=_MOUSEINPUT(0,0,0,flags,0,None)))
        ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(i))
    except: pass

def _hwnd():
    try:
        info = pygame.display.get_wm_info()
        return info.get("window") or info.get("hwnd") or 0
    except: return 0

def _foreground_hwnd():
    try: return ctypes.windll.user32.GetForegroundWindow()
    except: return 0

def _radon_is_focused():
    return _hwnd() == _foreground_hwnd()

def _physical_down(vk):
    """
    État physique uniquement — on utilise GetRawInputDeviceList + RawInput
    serait idéal, mais GetKeyState suffit SI on filtre la fenêtre active.
    On combine GetKeyState (état physique) avec un check fenêtre active.
    """
    try: return bool(ctypes.windll.user32.GetKeyState(vk) & 0x8000)
    except: return False

def hide_win():
    h = _hwnd()
    if not h: return
    try:
        s = ctypes.windll.user32.GetWindowLongW(h, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(h, GWL_EXSTYLE,
            (s | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        ctypes.windll.user32.ShowWindow(h, 0)
    except: pass

def show_win():
    h = _hwnd()
    if not h: return
    try:
        s = ctypes.windll.user32.GetWindowLongW(h, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(h, GWL_EXSTYLE,
            (s & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        ctypes.windll.user32.ShowWindow(h, 9)
        ctypes.windll.user32.SetForegroundWindow(h)
    except: pass

def move_win(x, y, w, h):
    try: ctypes.windll.user32.MoveWindow(_hwnd(), x, y, w, h, True)
    except: pass

def get_win_rect():
    r = _RECT()
    try: ctypes.windll.user32.GetWindowRect(_hwnd(), ctypes.byref(r))
    except: pass
    return r.left, r.top

def get_cursor_pos():
    p = _POINT()
    try: ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    except: pass
    return p.x, p.y

# ─────────────────────────────────────────────────────────────────────────────
#  CLICKER ENGINE
# ─────────────────────────────────────────────────────────────────────────────
VK_LB = 0x01
VK_RB = 0x02

class Side:
    def __init__(self, vk, dn, up, default_bind):
        self.vk      = vk
        self._dn     = dn
        self._up     = up
        self.bind    = default_bind
        self.enabled = False
        self.inv     = False
        self.min_cps = 8.0
        self.max_cps = 12.0
        self.offset  = 0        # ms jitter max
        self._next   = 0.0
        self._log    = deque(maxlen=100)
        self.cur_ms  = 0.0

    @property
    def avg_cps(self):
        now = time.perf_counter()
        return sum(1 for t in self._log if now - t < 1.0)

    def tick(self):
        if not self.enabled:
            self.cur_ms = 0.0; return

        # ── Clé : ne PAS cliquer si la fenêtre Radon est au premier plan
        # (évite les clics en boucle quand on interagit avec l'UI)
        if _radon_is_focused():
            self.cur_ms = 0.0; return

        if not _physical_down(self.vk):
            self.cur_ms = 0.0; return

        now = time.perf_counter()
        if now < self._next: return

        cps   = random.uniform(self.min_cps, max(self.min_cps, self.max_cps))
        base  = 1.0 / cps
        jit   = random.uniform(-self.offset / 2000.0, self.offset / 2000.0)
        delay = max(0.016, base + jit)
        self.cur_ms = delay * 1000.0
        self._next  = now + delay

        if self.inv:
            _send(self._up);  time.sleep(0.010); _send(self._dn)
        else:
            _send(self._dn);  time.sleep(0.010); _send(self._up)
        self._log.append(time.perf_counter())


class Engine:
    def __init__(self):
        self.L = Side(VK_LB, MELF_LD, MELF_LU, Key.f6)
        self.R = Side(VK_RB, MELF_RD, MELF_RU, Key.f8)
        self.hide_bind = Key.f7
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, args=(self.L,), daemon=True).start()
        threading.Thread(target=self._run, args=(self.R,), daemon=True).start()

    def _run(self, side):
        while not self._stop.is_set():
            side.tick()
            time.sleep(0.001)

    def stop(self): self._stop.set()

# ─────────────────────────────────────────────────────────────────────────────
#  THEME
# ─────────────────────────────────────────────────────────────────────────────
W, H = 360, 560

BG    = (10,  10,  14 )
CARD  = (17,  17,  24 )
CARD2 = (22,  22,  32 )
INSET = (13,  13,  19 )
BDR   = (36,  36,  52 )
BDR2  = (58,  58,  82 )
TEXT  = (218, 220, 236)
SUB   = ( 98,  98, 126)
DIM   = ( 48,  48,  66)
WHT   = (255, 255, 255)

CL = ( 68, 122, 255)   # blue  — left
CR = (238,  72,  72)   # red   — right
CG = ( 56, 192, 100)   # green — active
CY = (255, 188,  48)   # yellow— bind

def lp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i]-a[i])*t) for i in range(len(a)))

def clamp(v, lo, hi): return max(lo, min(hi, v))

# ─────────────────────────────────────────────────────────────────────────────
#  FONTS
# ─────────────────────────────────────────────────────────────────────────────
pygame.init()
_FC = {}
def F(sz, bold=False):
    k = (sz, bold)
    if k not in _FC:
        for n in ["Consolas","Courier New","Lucida Console"]:
            try: _FC[k] = pygame.font.SysFont(n, sz, bold=bold); break
            except: pass
        if k not in _FC: _FC[k] = pygame.font.Font(None, sz+4)
    return _FC[k]

# ─────────────────────────────────────────────────────────────────────────────
#  DRAW PRIMITIVES
# ─────────────────────────────────────────────────────────────────────────────
def rr(surf, col, rect, r=6, w=0):
    pygame.draw.rect(surf, col, rect, border_radius=r, width=w)

def rra(surf, col, rect, r=6, a=1.0):
    s = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
    pygame.draw.rect(s, (*col[:3], int(a*255)), (0,0,rect[2],rect[3]), border_radius=r)
    surf.blit(s, (rect[0], rect[1]))

def T(surf, text, f, col, x, y, anc="topleft"):
    s = f.render(str(text), True, col)
    surf.blit(s, s.get_rect(**{anc:(x,y)}))
    return s.get_width()

def sep(surf, y, x1=12, x2=None, a=0.35):
    if x2 is None: x2 = W-12
    s = pygame.Surface((x2-x1, 1), pygame.SRCALPHA)
    s.fill((*BDR, int(a*255)))
    surf.blit(s, (x1, y))

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
             Key.space:"SPACE",Key.enter:"ENTER",Key.backspace:"BKSP",
             Key.delete:"DEL",Key.insert:"INS",Key.home:"HOME",
             Key.end:"END",Key.page_up:"PGUP",Key.page_down:"PGDN",
             Key.up:"UP",Key.down:"DN",Key.left:"LT",Key.right:"RT",
             Key.num_lock:"NUML",Key.scroll_lock:"SCRL",
             Key.print_screen:"PRT",Key.pause:"PAUSE"}
        return M.get(k, str(k).replace("Key.","").upper())
    return str(k)

# ─────────────────────────────────────────────────────────────────────────────
#  WIDGETS
# ─────────────────────────────────────────────────────────────────────────────

class Slider:
    H = 38
    def __init__(self, x, y, w, label, lo, hi, val, suf="", is_int=False):
        self.r  = pygame.Rect(x, y, w, self.H)
        self.label = label; self.lo = lo; self.hi = hi
        self.value = val; self.suf = suf; self.is_int = is_int
        self._drag = False; self._a = 0.0

    @property
    def norm(self): return (self.value - self.lo) / (self.hi - self.lo)

    def event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.r.collidepoint(e.pos): self._drag = True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._drag = False
        if e.type == pygame.MOUSEMOTION and self._drag:
            t = clamp((e.pos[0] - self.r.x) / self.r.w, 0, 1)
            v = self.lo + t * (self.hi - self.lo)
            self.value = int(round(v)) if self.is_int else round(v, 1)

    def update(self, dt):
        hover = self.r.collidepoint(pygame.mouse.get_pos())
        tgt = 1.0 if (hover or self._drag) else 0.0
        self._a += (tgt - self._a) * min(1, dt * 12)

    def draw(self, surf, acc):
        x, y, w, h = self.r
        # label + value
        T(surf, self.label, F(11), lp(SUB, TEXT, self._a*0.7), x, y+2)
        val_s = (str(int(self.value)) if self.is_int else f"{self.value:.1f}") + self.suf
        T(surf, val_s, F(11, bold=True), lp(SUB, acc, self._a*0.8+0.2), x+w, y+2, "topright")

        # track
        TH = 4; ty = y + h - 10
        rr(surf, INSET, (x, ty, w, TH), 2)
        fw = max(TH, int(w * self.norm))
        # filled portion with glow effect
        fill_col = lp(lp(acc, BG, 0.6), acc, self._a * 0.4)
        rr(surf, fill_col, (x, ty, fw, TH), 2)

        # handle
        hx = x + fw
        hr = int(6 + self._a * 2)
        pygame.draw.circle(surf, BG,             (hx, ty + TH//2), hr + 2)
        pygame.draw.circle(surf, lp(lp(acc, BDR, 0.5), acc, self._a), (hx, ty + TH//2), hr)


class Toggle:
    def __init__(self, x, y, label, val=False):
        self.r = pygame.Rect(x, y, 32, 16)
        self.label = label; self.value = val; self._a = float(val)

    def event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            lw = F(11).size(self.label)[0]
            hit = pygame.Rect(self.r.x, self.r.y, self.r.w + 8 + lw, self.r.h)
            if hit.collidepoint(e.pos): self.value = not self.value

    def update(self, dt):
        self._a += ((1.0 if self.value else 0.0) - self._a) * min(1, dt * 14)

    def draw(self, surf, acc):
        x, y, w, h = self.r
        # track
        rr(surf, lp(INSET, acc, self._a * 0.9), (x, y, w, h), h)
        # thumb
        cx = int(x + h//2 + self._a * (w - h))
        pygame.draw.circle(surf, lp(BDR2, WHT, self._a), (cx, y + h//2), h//2 - 1)
        # label
        T(surf, self.label, F(11), lp(SUB, TEXT, self._a * 0.8 + 0.1), x + w + 9, y + 1)


class BindBtn:
    def __init__(self, x, y, w, h, key):
        self.r = pygame.Rect(x, y, w, h)
        self.key = key
        self._a = 0.0; self._press = False; self.clicked = False

    def event(self, e):
        self.clicked = False
        hover = self.r.collidepoint(pygame.mouse.get_pos())
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.r.collidepoint(e.pos):
            self._press = True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            if self._press and self.r.collidepoint(e.pos): self.clicked = True
            self._press = False

    def update(self, dt):
        hover = self.r.collidepoint(pygame.mouse.get_pos())
        tgt = 1.0 if (hover or self._press) else 0.0
        self._a += (tgt - self._a) * min(1, dt * 14)

    def draw(self, surf, acc):
        x, y, w, h = self.r
        bg  = lp(CARD2, lp(acc, BG, 0.45), self._a * 0.6)
        bdr = lp(BDR, acc, self._a)
        rr(surf, bg,  (x, y, w, h), 5)
        rr(surf, bdr, (x, y, w, h), 5, w=1)
        label = kname(self.key)
        T(surf, label, F(11, bold=True), lp(SUB, acc, self._a * 0.7 + 0.3), x + w//2, y + h//2, "center")

# ─────────────────────────────────────────────────────────────────────────────
#  SIDE CARD  (Left / Right)
# ─────────────────────────────────────────────────────────────────────────────
CARD_H = 234

class SideCard:
    def __init__(self, x, y, w, side: Side, acc, title):
        self.x = x; self.y = y; self.w = w
        self.side = side; self.acc = acc; self.title = title
        SW = w - 24; cx = x + 12
        self.sl_min = Slider(cx, y+44,  SW, "Min CPS", 1, 30, side.min_cps, " cps")
        self.sl_max = Slider(cx, y+86,  SW, "Max CPS", 1, 30, side.max_cps, " cps")
        self.sl_off = Slider(cx, y+128, SW, "Jitter",  0, 50, 0,            " ms",  is_int=True)
        self.tg_en  = Toggle(cx, y+170, "Enable",    side.enabled)
        self.tg_inv = Toggle(cx, y+191, "Inv-Click", side.inv)
        BW = (SW - 6) // 2
        self.btn_bind = BindBtn(cx, y+212, BW, 20, side.bind)
        self._pulse = 0.0; self._a_active = 0.0

    @property
    def _all(self): return [self.sl_min, self.sl_max, self.sl_off,
                            self.tg_en, self.tg_inv, self.btn_bind]

    def events(self, e):
        for w in self._all: w.event(e)

    def update(self, dt):
        for w in self._all: w.update(dt)
        self.side.min_cps = self.sl_min.value
        self.side.max_cps = max(self.sl_min.value, self.sl_max.value)
        self.side.offset  = self.sl_off.value
        self.side.enabled = self.tg_en.value
        self.side.inv     = self.tg_inv.value
        self._pulse += dt * (5.0 if self.side.enabled else 1.0)
        tgt_a = 1.0 if self.side.enabled else 0.0
        self._a_active += (tgt_a - self._a_active) * min(1, dt * 6)

    def draw(self, surf):
        x, y, w = self.x, self.y, self.w
        # card shadow
        rra(surf, (0,0,0), (x+2, y+3, w, CARD_H), 8, a=0.35)
        # card bg
        rr(surf, CARD, (x, y, w, CARD_H), 8)
        # active glow border
        bdr_col = lp(BDR, self.acc, self._a_active * 0.6)
        rr(surf, bdr_col, (x, y, w, CARD_H), 8, w=1)
        # top accent bar
        bar_col = lp(lp(self.acc, BG, 0.78), lp(self.acc, BG, 0.6), self._a_active)
        rra(surf, bar_col, (x, y, w, 32), 8, a=1.0)
        rra(surf, bar_col, (x, y+24, w, 8), 0, a=1.0)

        # pulse dot
        dr = int(4 + 1.8 * math.sin(self._pulse))
        dc = lp(lp(BDR, CG, 0.7), CG, self._a_active)
        pygame.draw.circle(surf, dc, (x + 13, y + 16), dr)

        # title
        T(surf, self.title, F(12, bold=True), lp(SUB, WHT, self._a_active * 0.6 + 0.4), x + 26, y + 8)
        # avg cps badge
        avg = self.side.avg_cps
        if avg > 0:
            badge_col = lp(lp(self.acc, BG, 0.7), self.acc, self._a_active)
            bw = F(10).size(str(avg))[0] + 14
            rr(surf, badge_col, (x + w - bw - 8, y + 8, bw, 16), 8)
            T(surf, str(avg), F(10, bold=True), WHT, x + w - 8 - bw//2, y + 16, "center")

        # sliders & toggles
        self.sl_min.draw(surf, self.acc)
        self.sl_max.draw(surf, self.acc)
        self.sl_off.draw(surf, self.acc)
        sep(surf, y + 162, x+12, x+w-12, a=0.25)
        self.tg_en.draw(surf,  self.acc)
        self.tg_inv.draw(surf, self.acc)
        sep(surf, y + 207, x+12, x+w-12, a=0.25)
        self.btn_bind.draw(surf, self.acc)

# ─────────────────────────────────────────────────────────────────────────────
#  BIND OVERLAY
# ─────────────────────────────────────────────────────────────────────────────
class BindOverlay:
    def __init__(self):
        self.active = False; self.cb = None; self.target = ""
        self._a = 0.0

    def open(self, target, cb):
        self.active = True; self.cb = cb; self.target = target; self._a = 0.0

    def close(self):
        self.active = False; self.cb = None

    def update(self, dt):
        tgt = 1.0 if self.active else 0.0
        self._a += (tgt - self._a) * min(1, dt * 16)

    def draw(self, surf):
        if not self.active and self._a < 0.02: return
        a = self._a
        # backdrop
        rra(surf, (6, 6, 12), (0, 0, W, H), 0, a=a * 0.82)
        # dialog card
        bx, by, bw, bh = W//2 - 150, H//2 - 64, 300, 128
        rra(surf, CARD2, (bx, by, bw, bh), 10, a=a)
        rra(surf, BDR2,  (bx, by, bw, bh), 10, a=a * 0.8)

        labels = {
            "left":  "Left Click  —  Bind",
            "right": "Right Click  —  Bind",
            "hide":  "Hide  —  Bind",
        }
        title = labels.get(self.target, "Bind")

        s1 = F(13, bold=True).render(title, True, WHT)
        s1.set_alpha(int(a * 255))
        surf.blit(s1, (W//2 - s1.get_width()//2, by + 18))

        s2 = F(11).render("Appuie sur une touche…", True, TEXT)
        s2.set_alpha(int(a * 200))
        surf.blit(s2, (W//2 - s2.get_width()//2, by + 46))

        s3 = F(10).render("M3 / M4 / M5 supportés   •   Esc = annuler", True, SUB)
        s3.set_alpha(int(a * 160))
        surf.blit(s3, (W//2 - s3.get_width()//2, by + 70))

        # animated dots
        for i in range(3):
            ox = W//2 - 16 + i * 16
            oy = by + 100
            r = int(3 + 1.5 * math.sin(time.perf_counter()*4 + i*1.2))
            c = lp(BDR2, lp(CL, CR, i/2), a)
            pygame.draw.circle(surf, c, (ox, oy), r)

# ─────────────────────────────────────────────────────────────────────────────
#  APP
# ─────────────────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.screen = pygame.display.set_mode((W, H), pygame.NOFRAME)
        pygame.display.set_caption("Radon")
        pygame.mixer.pre_init(); pygame.mixer.init()
        self.clock = pygame.time.Clock()
        self._t    = 0.0

        self.eng = Engine()
        self.eng.start()

        self.hidden  = False
        self._drag   = False
        self._doff   = (0, 0)
        self._wpos   = (100, 100)

        PAD = 10
        PW  = (W - PAD * 3) // 2
        PY  = 62

        self.card_l  = SideCard(PAD,         PY, PW, self.eng.L, CL, "Left Click")
        self.card_r  = SideCard(PAD*2 + PW,  PY, PW, self.eng.R, CR, "Right Click")

        HIDE_Y = PY + CARD_H + 14
        self.btn_hide = BindBtn(PAD, HIDE_Y, W - PAD*2, 26, self.eng.hide_bind)

        self.overlay       = BindOverlay()
        self._wait_bind    = False
        self._status       = ""
        self._status_t     = 0.0

        self._kb = KbListener(on_press=self._on_key)
        self._kb.start()
        self._ml = MouseListener(on_click=self._on_mouse_btn)
        self._ml.start()

    # ── Global hotkeys ────────────────────────────────────────
    def _on_key(self, key):
        if self._wait_bind:
            if key == Key.esc:
                self.overlay.close(); self._wait_bind = False; return
            if self.overlay.cb: self.overlay.cb(key)
            self.overlay.close(); self._wait_bind = False; return

        if key == self.eng.hide_bind:
            if self.hidden: self._show()
            else:           self._hide()
            return

        if key == self.eng.L.bind:
            self.eng.L.enabled   = not self.eng.L.enabled
            self.card_l.tg_en.value = self.eng.L.enabled
        if key == self.eng.R.bind:
            self.eng.R.enabled   = not self.eng.R.enabled
            self.card_r.tg_en.value = self.eng.R.enabled

    def _on_mouse_btn(self, x, y, button, pressed):
        if not pressed or not self._wait_bind: return
        extra = {Button.middle: "M3", Button.x1: "M4", Button.x2: "M5"}
        name = extra.get(button)
        if name is None: return
        if self.overlay.cb: self.overlay.cb(name)
        self.overlay.close(); self._wait_bind = False

    def _hide(self): self.hidden = True;  hide_win()
    def _show(self): self.hidden = False; show_win()

    def _set_bind(self, who, k):
        if who == "left":
            self.eng.L.bind = k
            self.card_l.btn_bind.key = k
            self._status = f"Left  ▸  {kname(k)}"
        elif who == "right":
            self.eng.R.bind = k
            self.card_r.btn_bind.key = k
            self._status = f"Right  ▸  {kname(k)}"
        else:
            self.eng.hide_bind = k
            self.btn_hide.key  = k
            self._status = f"Hide  ▸  {kname(k)}"
        self._status_t = 2.5

    # ── Draw ──────────────────────────────────────────────────
    def _draw(self, dt):
        self._t += dt
        s = self.screen
        s.fill(BG)

        # ── Header ──────────────────────────────────────────
        HH = 54
        rr(s, CARD, (0, 0, W, HH), 0)

        # Logo block
        lx = 14
        rra(s, CL, (lx, 13, 20, 20), 4, a=0.25)
        rra(s, CR, (lx + 4, 17, 20, 20), 4, a=0.25)
        T(s, "RADON", F(15, bold=True), TEXT, lx + 28, 10)
        T(s, "autoclicker", F(9), DIM, lx + 28, 28)

        # total cps
        total = self.eng.L.avg_cps + self.eng.R.avg_cps
        active_any = self.eng.L.enabled or self.eng.R.enabled
        col_cps = lp(DIM, CG, (0.8 if active_any else 0.0))
        T(s, str(total), F(20, bold=True), col_cps, W - 14, HH//2, "midright")
        T(s, "cps", F(9), DIM, W - 14, HH//2 + 12, "midright")

        # L / R indicator dots
        pygame.draw.circle(s, lp(BDR, CL, 0.9 if self.eng.L.enabled else 0.0),
                           (W - 46, HH//2 - 4), 3)
        pygame.draw.circle(s, lp(BDR, CR, 0.9 if self.eng.R.enabled else 0.0),
                           (W - 36, HH//2 - 4), 3)

        # close button
        close_r = pygame.Rect(W - 26, 8, 18, 18)
        ch = close_r.collidepoint(pygame.mouse.get_pos())
        rra(s, lp(CARD2, (200,50,50), 0.9 if ch else 0.1), (W-26, 8, 18, 18), 5)
        T(s, "×", F(12, bold=True), lp(SUB, WHT, 0.9 if ch else 0.5), W-17, 17, "center")

        # separator
        sep(s, HH, 0, W, a=0.6)

        # ── Cards ────────────────────────────────────────────
        self.card_l.draw(s)
        self.card_r.draw(s)

        # ── Hide bind row ────────────────────────────────────
        hy = self.btn_hide.r.y
        sep(s, hy - 10, a=0.3)
        T(s, "HIDE BIND", F(9), DIM, 14, hy + 7)
        # btn occupies right part
        old_r = self.btn_hide.r
        self.btn_hide.r = pygame.Rect(old_r.x + 76, old_r.y, old_r.w - 76, old_r.h)
        self.btn_hide.draw(s, lp(CL, CR, 0.5))
        self.btn_hide.r = old_r

        # ── Footer ───────────────────────────────────────────
        T(s, "zero network  •  100% local", F(9), DIM, W//2, H - 14, "center")

        # ── Status toast ─────────────────────────────────────
        if self._status_t > 0:
            a = min(1.0, self._status_t * 2.5)
            sw = F(11).size(self._status)[0] + 20
            sx = W//2 - sw//2; sy = H - 34
            rra(s, lp(CARD2, CG, 0.18), (sx, sy, sw, 18), 6, a=a)
            ss = F(11).render(self._status, True, CG)
            ss.set_alpha(int(a * 210))
            s.blit(ss, (W//2 - ss.get_width()//2, sy + 1))

        # ── Overlay ──────────────────────────────────────────
        self.overlay.draw(s)
        pygame.display.flip()

    # ── Main loop ─────────────────────────────────────────────
    def run(self):
        running = True
        prev = time.perf_counter()

        while running:
            now = time.perf_counter(); dt = min(now - prev, 0.05); prev = now

            if self.hidden:
                pygame.event.pump(); time.sleep(0.05); continue

            for e in pygame.event.get():
                if e.type == pygame.QUIT: running = False

                # window drag
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    mx, my = e.pos
                    if pygame.Rect(W-26, 8, 18, 18).collidepoint(mx, my):
                        running = False; break
                    if my < 54 and mx < W - 30:
                        self._drag = True
                        self._doff = get_cursor_pos()
                        self._wpos = get_win_rect()

                if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                    self._drag = False

                if e.type == pygame.MOUSEMOTION and self._drag:
                    cx, cy = get_cursor_pos()
                    move_win(self._wpos[0] + cx - self._doff[0],
                             self._wpos[1] + cy - self._doff[1], W, H)

                if not self.overlay.active:
                    self.card_l.events(e)
                    self.card_r.events(e)
                    bh_r_actual = pygame.Rect(
                        self.btn_hide.r.x + 76, self.btn_hide.r.y,
                        self.btn_hide.r.w - 76, self.btn_hide.r.h)
                    # manual event for shifted btn_hide
                    self.btn_hide.event(e)

                # bind button clicks
                if not self.overlay.active:
                    if self.card_l.btn_bind.clicked:
                        self._wait_bind = True
                        self.overlay.open("left",  lambda k: self._set_bind("left", k))
                    if self.card_r.btn_bind.clicked:
                        self._wait_bind = True
                        self.overlay.open("right", lambda k: self._set_bind("right", k))
                    if self.btn_hide.clicked:
                        self._wait_bind = True
                        self.overlay.open("hide",  lambda k: self._set_bind("hide", k))

            self.card_l.update(dt)
            self.card_r.update(dt)
            self.btn_hide.update(dt)
            self.overlay.update(dt)
            if self._status_t > 0: self._status_t = max(0, self._status_t - dt)

            self._draw(dt)
            self.clock.tick(60)

        self.eng.stop()
        self._kb.stop()
        self._ml.stop()
        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    App().run()
