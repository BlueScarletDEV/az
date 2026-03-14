"""
Radon Clicker — v5.0
Logique de clic identique à l'original (Runtime_Broker.exe / Radon .NET) :
  - GetAsyncKeyState  → bit 15 = bouton physiquement enfoncé
  - GetForegroundWindow → clique UNIQUEMENT sur la fenêtre active
  - PostMessageA(hwnd, WM_LBUTTONDOWN/UP, ...)  → envoi de message WIN32
  - Debounce 200 ms sur le bind toggle (comme le Sleep(200) original)
  - Thread séparé par side
pip install pygame pynput
"""

import pygame, time, random, threading, math, sys, ctypes, struct
from collections import deque
from pynput.mouse    import Button, Listener as MouseListener
from pynput.keyboard import Key, KeyCode, Listener as KbListener

# ─────────────────────────────────────────────────────────────────────────────
#  WIN32 STRUCTS & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP   = 0x0205
MK_LBUTTON     = 0x0001
MK_RBUTTON     = 0x0002

GWL_EXSTYLE      = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW  = 0x00040000

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top",  ctypes.c_long),
                ("right",ctypes.c_long), ("bottom",ctypes.c_long)]

u32 = ctypes.windll.user32

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _hwnd_radon():
    try:
        info = pygame.display.get_wm_info()
        return info.get("window") or info.get("hwnd") or 0
    except: return 0

def _foreground_hwnd():
    try: return u32.GetForegroundWindow()
    except: return 0

def _radon_focused():
    """La fenêtre Radon est-elle au premier plan ?"""
    return _hwnd_radon() == _foreground_hwnd()

def _physical_down(vk):
    """
    smethod_2 original : (GetKeyState(vk) & 0x8000) != 0
    Bit 15 = appui physique. GetKeyState (pas Async) → ignore SendInput/PostMessage.
    """
    try: return bool(u32.GetKeyState(vk) & 0x8000)
    except: return False

def _async_down(vk):
    """
    GetAsyncKeyState : utilisé dans le thread clicker pour détecter
    si le bouton souris est maintenu (comme dans method_2 original).
    """
    try: return bool(u32.GetAsyncKeyState(vk) & 0x8000)
    except: return False

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

def _post_click(hwnd, down_msg, up_msg, mk_flag):
    """
    PostMessageA(hwnd, WM_xBUTTONDOWN, MK_x, 0)
    PostMessageA(hwnd, WM_xBUTTONUP,   0,    0)
    Exactement comme le original.
    wParam = mk_flag pour DOWN, 0 pour UP.
    lParam = 0 (position relative ignorée par la plupart des jeux).
    """
    try:
        u32.PostMessageA(hwnd, down_msg, mk_flag, 0)
        time.sleep(0.010)
        u32.PostMessageA(hwnd, up_msg,   0,       0)
    except: pass

def _post_click_inv(hwnd, down_msg, up_msg, mk_flag):
    """Inv-click : UP d'abord, puis DOWN (comme original)."""
    try:
        u32.PostMessageA(hwnd, up_msg,   0,       0)
        time.sleep(0.010)
        u32.PostMessageA(hwnd, down_msg, mk_flag, 0)
    except: pass

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02

# ─────────────────────────────────────────────────────────────────────────────
#  CLICKER ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class Side:
    def __init__(self, vk, down_msg, up_msg, mk_flag, default_bind):
        self.vk        = vk
        self.down_msg  = down_msg
        self.up_msg    = up_msg
        self.mk_flag   = mk_flag
        self.bind      = default_bind
        self.enabled   = False
        self.inv       = False
        self.min_cps   = 8.0
        self.max_cps   = 12.0
        self.offset    = 0       # ms jitter max
        self._next     = 0.0
        self._log      = deque(maxlen=100)
        self.cur_ms    = 0.0

    @property
    def avg_cps(self):
        now = time.perf_counter()
        return sum(1 for t in self._log if now - t < 1.0)

    def tick(self):
        if not self.enabled:
            self.cur_ms = 0.0; return

        # ── Règle 1 : récupère la fenêtre au foreground ──────
        # On clique UNIQUEMENT sur cette fenêtre (comme PostMessageA original).
        # Si c'est la fenêtre Radon elle-même → on skip.
        fhwnd = _foreground_hwnd()
        if not fhwnd or fhwnd == _hwnd_radon():
            self.cur_ms = 0.0; return

        # ── Règle 2 : bouton physiquement enfoncé ? ──────────
        # GetAsyncKeyState comme dans method_2 original.
        # On utilise Async ici (pas GetKeyState) parce qu'on est dans
        # un thread séparé qui ne reçoit pas les messages windows.
        if not _async_down(self.vk):
            self.cur_ms = 0.0; return

        # ── Règle 3 : timer de cadence ───────────────────────
        now = time.perf_counter()
        if now < self._next: return

        cps   = random.uniform(self.min_cps, max(self.min_cps, self.max_cps))
        base  = 1.0 / cps
        jit   = random.uniform(-self.offset / 2000.0, self.offset / 2000.0)
        delay = max(0.016, base + jit)
        self.cur_ms = delay * 1000.0
        self._next  = now + delay

        # ── Règle 4 : PostMessageA vers la fenêtre active ────
        if self.inv:
            _post_click_inv(fhwnd, self.down_msg, self.up_msg, self.mk_flag)
        else:
            _post_click(fhwnd, self.down_msg, self.up_msg, self.mk_flag)

        self._log.append(time.perf_counter())


class Engine:
    def __init__(self):
        self.L = Side(VK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, Key.f6)
        self.R = Side(VK_RBUTTON, WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON, Key.f8)
        self.hide_bind   = Key.f7
        self._stop       = threading.Event()
        # debounce state (200ms comme Sleep(200) original)
        self._bind_next  = {self.L: 0.0, self.R: 0.0}
        self._hide_next  = 0.0

    def start(self):
        threading.Thread(target=self._run, args=(self.L,), daemon=True).start()
        threading.Thread(target=self._run, args=(self.R,), daemon=True).start()

    def _run(self, side):
        while not self._stop.is_set():
            side.tick()
            time.sleep(0.001)

    def stop(self): self._stop.set()

    def toggle_side(self, side):
        """Toggle avec debounce 200ms (= Sleep(200) du original)."""
        now = time.perf_counter()
        if now < self._bind_next[side]: return
        self._bind_next[side] = now + 0.200
        side.enabled = not side.enabled

    def toggle_hide(self, app):
        now = time.perf_counter()
        if now < self._hide_next: return
        self._hide_next = now + 0.200
        if app.hidden: app._show()
        else:          app._hide()

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
CL    = ( 68, 122, 255)
CR    = (238,  72,  72)
CG    = ( 56, 192, 100)
CY    = (255, 188,  48)

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
    s = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
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
        return M.get(k, str(k).replace("Key.", "").upper())
    return str(k)

# ─────────────────────────────────────────────────────────────────────────────
#  WIDGETS
# ─────────────────────────────────────────────────────────────────────────────
class Slider:
    H = 38
    def __init__(self, x, y, w, label, lo, hi, val, suf="", is_int=False):
        self.r     = pygame.Rect(x, y, w, self.H)
        self.label = label; self.lo = lo; self.hi = hi
        self.value = val;   self.suf = suf; self.is_int = is_int
        self._drag = False; self._a = 0.0

    @property
    def norm(self): return (self.value - self.lo) / (self.hi - self.lo)

    def event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.r.collidepoint(e.pos):
            self._drag = True
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
        T(surf, self.label, F(11), lp(SUB, TEXT, self._a * 0.6), x, y + 2)
        val_s = (str(int(self.value)) if self.is_int else f"{self.value:.1f}") + self.suf
        T(surf, val_s, F(11, bold=True), lp(SUB, acc, self._a * 0.7 + 0.2), x + w, y + 2, "topright")
        TH = 4; ty = y + h - 10
        rr(surf, INSET, (x, ty, w, TH), 2)
        fw = max(TH, int(w * self.norm))
        rr(surf, lp(lp(acc, BG, 0.6), acc, self._a * 0.4), (x, ty, fw, TH), 2)
        hx = x + fw
        hr = int(6 + self._a * 2)
        pygame.draw.circle(surf, BG, (hx, ty + TH // 2), hr + 2)
        pygame.draw.circle(surf, lp(lp(acc, BDR, 0.5), acc, self._a), (hx, ty + TH // 2), hr)


class Toggle:
    def __init__(self, x, y, label, val=False):
        self.r = pygame.Rect(x, y, 32, 16)
        self.label = label; self.value = val; self._a = float(val)

    def event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            lw = F(11).size(self.label)[0]
            if pygame.Rect(self.r.x, self.r.y, self.r.w + 8 + lw, self.r.h).collidepoint(e.pos):
                self.value = not self.value

    def update(self, dt):
        self._a += ((1.0 if self.value else 0.0) - self._a) * min(1, dt * 14)

    def draw(self, surf, acc):
        x, y, w, h = self.r
        rr(surf, lp(INSET, acc, self._a * 0.9), (x, y, w, h), h)
        cx = int(x + h // 2 + self._a * (w - h))
        pygame.draw.circle(surf, lp(BDR2, WHT, self._a), (cx, y + h // 2), h // 2 - 1)
        T(surf, self.label, F(11), lp(SUB, TEXT, self._a * 0.8 + 0.1), x + w + 9, y + 1)


class BindBtn:
    def __init__(self, x, y, w, h, key):
        self.r = pygame.Rect(x, y, w, h)
        self.key = key
        self._a = 0.0; self._press = False; self.clicked = False

    def event(self, e):
        self.clicked = False
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
        rr(surf, lp(CARD2, lp(acc, BG, 0.45), self._a * 0.6), (x, y, w, h), 5)
        rr(surf, lp(BDR, acc, self._a), (x, y, w, h), 5, w=1)
        T(surf, kname(self.key), F(11, bold=True),
          lp(SUB, acc, self._a * 0.7 + 0.3), x + w // 2, y + h // 2, "center")

# ─────────────────────────────────────────────────────────────────────────────
#  SIDE CARD
# ─────────────────────────────────────────────────────────────────────────────
CARD_H = 234

class SideCard:
    def __init__(self, x, y, w, side: Side, acc, title):
        self.x = x; self.y = y; self.w = w
        self.side = side; self.acc = acc; self.title = title
        SW = w - 24; cx = x + 12
        self.sl_min   = Slider(cx, y + 44,  SW, "Min CPS", 1,  30, side.min_cps, " cps")
        self.sl_max   = Slider(cx, y + 86,  SW, "Max CPS", 1,  30, side.max_cps, " cps")
        self.sl_off   = Slider(cx, y + 128, SW, "Jitter",  0,  50, 0,            " ms", is_int=True)
        self.tg_en    = Toggle(cx, y + 170, "Enable",     side.enabled)
        self.tg_inv   = Toggle(cx, y + 191, "Inv-Click",  side.inv)
        self.btn_bind = BindBtn(cx, y + 212, SW, 20, side.bind)
        self._pulse   = 0.0
        self._a_act   = 0.0

    @property
    def _all(self): return [self.sl_min, self.sl_max, self.sl_off,
                            self.tg_en, self.tg_inv, self.btn_bind]

    def events(self, e):
        for w in self._all: w.event(e)

    def update(self, dt):
        for w in self._all: w.update(dt)
        # sync engine ← UI
        self.side.min_cps = self.sl_min.value
        self.side.max_cps = max(self.sl_min.value, self.sl_max.value)
        self.side.offset  = self.sl_off.value
        self.side.inv     = self.tg_inv.value
        # sync UI ← engine (le hotkey peut changer enabled)
        self.tg_en.value  = self.side.enabled
        self._pulse += dt * (5.0 if self.side.enabled else 1.0)
        self._a_act += ((1.0 if self.side.enabled else 0.0) - self._a_act) * min(1, dt * 6)
        # toggle depuis UI
        self.side.enabled = self.tg_en.value

    def draw(self, surf):
        x, y, w = self.x, self.y, self.w
        rra(surf, (0, 0, 0), (x + 2, y + 3, w, CARD_H), 8, a=0.35)
        rr(surf, CARD, (x, y, w, CARD_H), 8)
        rr(surf, lp(BDR, self.acc, self._a_act * 0.55), (x, y, w, CARD_H), 8, w=1)
        bar = lp(lp(self.acc, BG, 0.78), lp(self.acc, BG, 0.60), self._a_act)
        rra(surf, bar, (x, y, w, 32), 8, a=1.0)
        rra(surf, bar, (x, y + 24, w, 8), 0, a=1.0)
        # dot
        dr = int(4 + 1.8 * math.sin(self._pulse))
        dc = lp(lp(BDR, CG, 0.7), CG, self._a_act)
        pygame.draw.circle(surf, dc, (x + 13, y + 16), dr)
        # title
        T(surf, self.title, F(12, bold=True),
          lp(SUB, WHT, self._a_act * 0.6 + 0.4), x + 26, y + 8)
        # avg cps badge
        avg = self.side.avg_cps
        if avg > 0:
            bc = lp(lp(self.acc, BG, 0.7), self.acc, self._a_act)
            bw = F(10).size(str(avg))[0] + 14
            rr(surf, bc, (x + w - bw - 8, y + 8, bw, 16), 8)
            T(surf, str(avg), F(10, bold=True), WHT, x + w - 8 - bw // 2, y + 16, "center")
        # widgets
        for sl in [self.sl_min, self.sl_max, self.sl_off]: sl.draw(surf, self.acc)
        sep(surf, y + 162, x + 12, x + w - 12, a=0.22)
        self.tg_en.draw(surf,  self.acc)
        self.tg_inv.draw(surf, self.acc)
        sep(surf, y + 207, x + 12, x + w - 12, a=0.22)
        self.btn_bind.draw(surf, self.acc)

# ─────────────────────────────────────────────────────────────────────────────
#  BIND OVERLAY
# ─────────────────────────────────────────────────────────────────────────────
class BindOverlay:
    def __init__(self):
        self.active = False; self.cb = None; self.target = ""; self._a = 0.0

    def open(self, target, cb):
        self.active = True; self.cb = cb; self.target = target; self._a = 0.0

    def close(self): self.active = False; self.cb = None

    def update(self, dt):
        tgt = 1.0 if self.active else 0.0
        self._a += (tgt - self._a) * min(1, dt * 16)

    def draw(self, surf):
        if not self.active and self._a < 0.02: return
        a = self._a
        rra(surf, (6, 6, 12), (0, 0, W, H), 0, a=a * 0.82)
        bx, by, bw, bh = W//2 - 150, H//2 - 64, 300, 128
        rra(surf, CARD2, (bx, by, bw, bh), 10, a=a)
        rra(surf, BDR2,  (bx, by, bw, bh), 10, a=a * 0.7)
        labels = {"left":"Left Click  —  Bind","right":"Right Click  —  Bind","hide":"Hide  —  Bind"}
        s1 = F(13, bold=True).render(labels.get(self.target, "Bind"), True, WHT)
        s1.set_alpha(int(a * 255)); surf.blit(s1, (W//2 - s1.get_width()//2, by + 18))
        s2 = F(11).render("Appuie sur une touche…", True, TEXT)
        s2.set_alpha(int(a * 200)); surf.blit(s2, (W//2 - s2.get_width()//2, by + 46))
        s3 = F(10).render("M3 / M4 / M5 supportés   •   Esc = annuler", True, SUB)
        s3.set_alpha(int(a * 155)); surf.blit(s3, (W//2 - s3.get_width()//2, by + 68))
        for i in range(3):
            r = int(3 + 1.5 * math.sin(time.perf_counter() * 4 + i * 1.2))
            c = lp(BDR2, lp(CL, CR, i / 2), a)
            pygame.draw.circle(surf, c, (W//2 - 16 + i*16, by + 100), r)

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

        self.eng  = Engine()
        self.eng.start()

        self.hidden   = False
        self._drag    = False
        self._doff    = (0, 0)
        self._wpos    = (100, 100)

        PAD = 10
        PW  = (W - PAD * 3) // 2
        PY  = 62

        self.card_l  = SideCard(PAD,        PY, PW, self.eng.L, CL, "Left Click")
        self.card_r  = SideCard(PAD*2 + PW, PY, PW, self.eng.R, CR, "Right Click")

        HIDE_Y = PY + CARD_H + 14
        self.btn_hide = BindBtn(PAD, HIDE_Y, W - PAD * 2, 26, self.eng.hide_bind)

        self.overlay    = BindOverlay()
        self._wait_bind = False
        self._status    = ""
        self._status_t  = 0.0

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
            self.eng.toggle_hide(self); return

        if key == self.eng.L.bind:
            self.eng.toggle_side(self.eng.L)
        if key == self.eng.R.bind:
            self.eng.toggle_side(self.eng.R)

    def _on_mouse_btn(self, x, y, button, pressed):
        if not pressed or not self._wait_bind: return
        extra = {Button.middle: "M3", Button.x1: "M4", Button.x2: "M5"}
        name = extra.get(button)
        if name is None: return
        if self.overlay.cb: self.overlay.cb(name)
        self.overlay.close(); self._wait_bind = False

    def _hide(self): self.hidden = True;  _hide_from_taskbar()
    def _show(self): self.hidden = False; _show_in_taskbar()

    def _set_bind(self, who, k):
        if who == "left":
            self.eng.L.bind = k;            self.card_l.btn_bind.key = k
            self._status = f"Left  ▸  {kname(k)}"
        elif who == "right":
            self.eng.R.bind = k;            self.card_r.btn_bind.key = k
            self._status = f"Right  ▸  {kname(k)}"
        else:
            self.eng.hide_bind = k;         self.btn_hide.key = k
            self._status = f"Hide  ▸  {kname(k)}"
        self._status_t = 2.5

    # ── Draw ──────────────────────────────────────────────────
    def _draw(self, dt):
        self._t += dt
        s = self.screen
        s.fill(BG)

        # Header gradient
        HH = 54
        for i in range(HH):
            t = i / HH
            c = lp(lp(CL, CR, 0.5), CARD, t ** 0.5)
            pygame.draw.line(s, c, (1, i), (W - 2, i))
        rr(s, BDR, (0, 0, W, H), 10, w=1)

        # Logo
        lx = 14
        rra(s, CL, (lx, 13, 20, 20), 4, a=0.22)
        rra(s, CR, (lx + 4, 17, 20, 20), 4, a=0.22)
        T(s, "RADON", F(15, bold=True), TEXT, lx + 28, 10)
        T(s, "autoclicker", F(9), DIM, lx + 28, 28)

        # Total CPS
        total = self.eng.L.avg_cps + self.eng.R.avg_cps
        act   = self.eng.L.enabled or self.eng.R.enabled
        T(s, str(total), F(20, bold=True), lp(DIM, CG, 0.8 if act else 0.0), W - 14, HH // 2, "midright")
        T(s, "cps", F(9), DIM, W - 14, HH // 2 + 12, "midright")

        # L/R dots
        pygame.draw.circle(s, lp(BDR, CL, 0.9 if self.eng.L.enabled else 0.0), (W-46, HH//2-4), 3)
        pygame.draw.circle(s, lp(BDR, CR, 0.9 if self.eng.R.enabled else 0.0), (W-36, HH//2-4), 3)

        # Close btn
        ch = pygame.Rect(W-26, 8, 18, 18).collidepoint(pygame.mouse.get_pos())
        rra(s, lp(CARD2, (200, 50, 50), 0.9 if ch else 0.1), (W-26, 8, 18, 18), 5)
        T(s, "×", F(12, bold=True), lp(SUB, WHT, 0.9 if ch else 0.5), W-17, 17, "center")

        sep(s, HH, 0, W, a=0.55)

        # Cards
        self.card_l.draw(s)
        self.card_r.draw(s)

        # Hide bind row
        hy = self.btn_hide.r.y
        sep(s, hy - 10, a=0.28)
        T(s, "HIDE BIND", F(9), DIM, 14, hy + 7)
        orig_r = self.btn_hide.r.copy()
        self.btn_hide.r = pygame.Rect(orig_r.x + 80, orig_r.y, orig_r.w - 80, orig_r.h)
        self.btn_hide.draw(s, lp(CL, CR, 0.5))
        self.btn_hide.r = orig_r

        # Footer
        T(s, "PostMessageA  •  zero network  •  100% local", F(9), DIM, W // 2, H - 14, "center")

        # Status toast
        if self._status_t > 0:
            a  = min(1.0, self._status_t * 2.5)
            sw = F(11).size(self._status)[0] + 20
            sx = W // 2 - sw // 2; sy = H - 34
            rra(s, lp(CARD2, CG, 0.18), (sx, sy, sw, 18), 6, a=a)
            ss = F(11).render(self._status, True, CG)
            ss.set_alpha(int(a * 210))
            s.blit(ss, (W // 2 - ss.get_width() // 2, sy + 1))

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

                # Window drag
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    mx, my = e.pos
                    if pygame.Rect(W-26, 8, 18, 18).collidepoint(mx, my):
                        running = False; break
                    if my < 54 and mx < W - 30:
                        self._drag = True
                        self._doff = _cursor_pos()
                        self._wpos = _win_rect_radon()

                if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                    self._drag = False

                if e.type == pygame.MOUSEMOTION and self._drag:
                    cx, cy = _cursor_pos()
                    _move_win(self._wpos[0] + cx - self._doff[0],
                              self._wpos[1] + cy - self._doff[1], W, H)

                if not self.overlay.active:
                    self.card_l.events(e)
                    self.card_r.events(e)
                    self.btn_hide.event(e)

                if not self.overlay.active:
                    if self.card_l.btn_bind.clicked:
                        self._wait_bind = True
                        self.overlay.open("left",  lambda k: self._set_bind("left",  k))
                    if self.card_r.btn_bind.clicked:
                        self._wait_bind = True
                        self.overlay.open("right", lambda k: self._set_bind("right", k))
                    if self.btn_hide.clicked:
                        self._wait_bind = True
                        self.overlay.open("hide",  lambda k: self._set_bind("hide",  k))

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
