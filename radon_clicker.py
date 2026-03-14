"""
Radon Clicker — v3.0
Left Clicker + Right Clicker séparés
Hide bind : cache fenêtre + taskbar, clicker continue en fond
Bind customisable : clavier + touches souris
pip install pygame pynput
"""

import pygame
import time, random, threading, math, sys, os, ctypes
from collections import deque
from pynput.mouse    import Button, Controller as MouseCtrl
from pynput.keyboard import Key, KeyCode, Listener as KbListener

# ═══════════════════════════════════════════════════════════
#  WIN32 helpers — hide from taskbar sans fermer
# ═══════════════════════════════════════════════════════════
GWL_EXSTYLE      = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW  = 0x00040000

def _hwnd():
    try:
        info = pygame.display.get_wm_info()
        return info.get("window") or info.get("hwnd") or 0
    except:
        return 0

def hide_from_taskbar():
    hwnd = _hwnd()
    if not hwnd: return
    try:
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        ctypes.windll.user32.ShowWindow(hwnd, 0)
    except: pass

def show_in_taskbar():
    hwnd = _hwnd()
    if not hwnd: return
    try:
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except: pass

def get_key_state(vk):
    try:    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except: return False

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02

# ═══════════════════════════════════════════════════════════
#  PALETTE
# ═══════════════════════════════════════════════════════════
W, H = 380, 580

BG       = (11,  11,  16 )
PANEL    = (18,  18,  26 )
PANEL2   = (24,  24,  34 )
BORDER   = (40,  40,  58 )
ACCENT_L = (82,  130, 255)
ACCENT_R = (255, 100, 100)
TEXT     = (225, 225, 240)
SUBTEXT  = (110, 110, 140)
ON       = (72,  200, 110)
WHITE    = (255, 255, 255)

def lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i]+(b[i]-a[i])*t) for i in range(len(a)))

def clamp(v, lo, hi): return max(lo, min(hi, v))

# ═══════════════════════════════════════════════════════════
#  FONTS
# ═══════════════════════════════════════════════════════════
pygame.init()
_fonts = {}
def font(size, bold=False):
    k = (size, bold)
    if k not in _fonts:
        try:    _fonts[k] = pygame.font.SysFont("Consolas", size, bold=bold)
        except: _fonts[k] = pygame.font.Font(None, size+4)
    return _fonts[k]

# ═══════════════════════════════════════════════════════════
#  DRAW UTILS
# ═══════════════════════════════════════════════════════════
def rrect(surf, col, rect, r=6, width=0):
    pygame.draw.rect(surf, col, rect, border_radius=r, width=width)

def rrect_a(surf, col, rect, r=6, alpha=255, width=0):
    s = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
    pygame.draw.rect(s, (*col[:3], alpha), (0,0,rect[2],rect[3]), border_radius=r, width=width)
    surf.blit(s, (rect[0], rect[1]))

def txt(surf, text, fnt, col, x, y, anchor="topleft"):
    s = fnt.render(str(text), True, col)
    r = s.get_rect(**{anchor:(x,y)})
    surf.blit(s, r)
    return s.get_width()

# ═══════════════════════════════════════════════════════════
#  KEY NAME
# ═══════════════════════════════════════════════════════════
def key_name(k):
    if k is None:         return "—"
    if k == "mouse3":     return "M3"
    if k == "mouse4":     return "M4"
    if k == "mouse5":     return "M5"
    if isinstance(k, KeyCode):
        return (k.char or "?").upper()
    if isinstance(k, Key):
        names = {
            Key.f1:"F1",Key.f2:"F2",Key.f3:"F3",Key.f4:"F4",
            Key.f5:"F5",Key.f6:"F6",Key.f7:"F7",Key.f8:"F8",
            Key.f9:"F9",Key.f10:"F10",Key.f11:"F11",Key.f12:"F12",
            Key.shift:"SHIFT",Key.shift_r:"SHIFT",
            Key.ctrl_l:"CTRL",Key.ctrl_r:"CTRL",
            Key.alt_l:"ALT",Key.alt_r:"ALT",
            Key.caps_lock:"CAPS",Key.tab:"TAB",Key.esc:"ESC",
            Key.space:"SPACE",Key.enter:"ENTER",Key.backspace:"BKSP",
            Key.delete:"DEL",Key.insert:"INS",Key.home:"HOME",
            Key.end:"END",Key.page_up:"PGUP",Key.page_down:"PGDN",
            Key.up:"UP",Key.down:"DOWN",Key.left:"LEFT",Key.right:"RIGHT",
            Key.num_lock:"NUMLOCK",Key.scroll_lock:"SCRLOCK",
            Key.print_screen:"PRTSC",Key.pause:"PAUSE",
        }
        return names.get(k, str(k).replace("Key.","").upper())
    return str(k)

# ═══════════════════════════════════════════════════════════
#  CLICKER SIDE
# ═══════════════════════════════════════════════════════════
class ClickerSide:
    def __init__(self, button, vk, default_bind):
        self.button  = button
        self.vk      = vk
        self.enabled = False
        self.inv     = False
        self.min_cps = 8.0
        self.max_cps = 12.0
        self.offset  = 0
        self.bind    = default_bind
        self._log    = deque(maxlen=80)
        self.cur_delay = 0.0
        self._next   = 0.0

    @property
    def avg_cps(self):
        now = time.perf_counter()
        return sum(1 for t in self._log if now-t < 1.0)

    def tick(self, mouse):
        if not self.enabled:
            self.cur_delay = 0; return
        if not get_key_state(self.vk):
            self.cur_delay = 0; return
        now = time.perf_counter()
        if now < self._next: return
        cps   = random.uniform(self.min_cps, max(self.min_cps, self.max_cps))
        delay = 1.0/cps + random.uniform(-self.offset/2000, self.offset/2000)
        delay = max(0.008, delay)
        self.cur_delay = delay * 1000
        self._next = now + delay
        if self.inv:
            mouse.release(self.button); time.sleep(0.008); mouse.press(self.button)
        else:
            mouse.press(self.button);   time.sleep(0.008); mouse.release(self.button)
        self._log.append(time.perf_counter())

class Engine:
    def __init__(self):
        self.mouse     = MouseCtrl()
        self.left      = ClickerSide(Button.left,  VK_LBUTTON, Key.f6)
        self.right     = ClickerSide(Button.right, VK_RBUTTON, Key.f8)
        self.hide_bind = Key.f7
        self._stop     = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            self.left.tick(self.mouse)
            self.right.tick(self.mouse)
            time.sleep(0.001)

    def stop(self): self._stop.set()

# ═══════════════════════════════════════════════════════════
#  WIDGETS
# ═══════════════════════════════════════════════════════════
class Slider:
    def __init__(self, x, y, w, label, lo, hi, val, suffix="", is_int=False):
        self.r      = pygame.Rect(x, y, w, 26)
        self.label  = label; self.lo = lo; self.hi = hi
        self.value  = val;   self.suffix = suffix; self.is_int = is_int
        self._drag  = False; self._hover = False; self._anim = 0.0

    @property
    def norm(self): return (self.value-self.lo)/(self.hi-self.lo)

    def event(self, e):
        mx,my = pygame.mouse.get_pos()
        self._hover = self.r.collidepoint(mx,my)
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1 and self.r.collidepoint(e.pos): self._drag=True
        if e.type==pygame.MOUSEBUTTONUP   and e.button==1: self._drag=False
        if e.type==pygame.MOUSEMOTION and self._drag:
            t = clamp((e.pos[0]-self.r.x)/self.r.w, 0, 1)
            v = self.lo + t*(self.hi-self.lo)
            self.value = int(round(v)) if self.is_int else round(v,1)

    def update(self, dt):
        tgt = 1.0 if (self._hover or self._drag) else 0.0
        self._anim += (tgt-self._anim)*min(1,dt*14)

    def draw(self, surf, accent):
        x,y,w,h = self.r
        TH = 3; ty = y+h-6
        rrect(surf, PANEL2, (x,ty,w,TH), 2)
        fw = int(w*self.norm)
        if fw>0:
            rrect(surf, lerp(lerp(accent,(30,30,50),0.5), accent, self._anim), (x,ty,fw,TH), 2)
        hx = x+fw
        hr = int(5+self._anim*2)
        pygame.draw.circle(surf, BG,   (hx, ty+TH//2), hr+2)
        pygame.draw.circle(surf, lerp(lerp(accent,(50,50,80),0.5), accent, self._anim), (hx, ty+TH//2), hr)
        txt(surf, self.label, font(11), SUBTEXT, x, y)
        val_s = (str(int(self.value)) if self.is_int else f"{self.value:.1f}") + self.suffix
        txt(surf, val_s, font(11), TEXT, x+w, y, anchor="topright")


class Toggle:
    def __init__(self, x, y, label, val=False):
        self.r=pygame.Rect(x,y,34,16); self.label=label; self.value=val; self._anim=float(val)

    def event(self, e):
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
            lw = font(11).size(self.label)[0]
            if pygame.Rect(self.r.x, self.r.y, self.r.w+8+lw, self.r.h).collidepoint(e.pos):
                self.value = not self.value

    def update(self, dt):
        self._anim += ((1.0 if self.value else 0.0)-self._anim)*min(1,dt*14)

    def draw(self, surf, accent):
        x,y,w,h = self.r
        rrect(surf, lerp(BORDER, accent, self._anim), (x,y,w,h), 8)
        cx = int(x+h//2 + self._anim*(w-h))
        pygame.draw.circle(surf, BG,    (cx,y+h//2), h//2)
        pygame.draw.circle(surf, WHITE, (cx,y+h//2), h//2-2)
        txt(surf, self.label, font(11), lerp(SUBTEXT,TEXT,self._anim), x+w+8, y+1)


class BtnSmall:
    def __init__(self, x, y, w, h, label):
        self.r=pygame.Rect(x,y,w,h); self.label=label
        self._hover=False; self._press=False; self.clicked=False; self._anim=0.0

    def event(self, e):
        self.clicked=False
        self._hover=self.r.collidepoint(pygame.mouse.get_pos())
        if e.type==pygame.MOUSEBUTTONDOWN and e.button==1 and self.r.collidepoint(e.pos): self._press=True
        if e.type==pygame.MOUSEBUTTONUP   and e.button==1:
            if self._press and self.r.collidepoint(e.pos): self.clicked=True
            self._press=False

    def update(self, dt):
        tgt=1.0 if (self._hover or self._press) else 0.0
        self._anim+=(tgt-self._anim)*min(1,dt*14)

    def draw(self, surf, accent):
        x,y,w,h=self.r
        rrect(surf, lerp(PANEL2, lerp(accent,(15,15,25),0.55), self._anim), (x,y,w,h), 5)
        rrect(surf, lerp(BORDER, accent, self._anim), (x,y,w,h), 5, width=1)
        txt(surf, self.label, font(11), lerp(SUBTEXT,TEXT,self._anim), x+w//2, y+h//2, anchor="center")

# ═══════════════════════════════════════════════════════════
#  SIDE PANEL
# ═══════════════════════════════════════════════════════════
PH = 238   # panel height

class SidePanel:
    def __init__(self, x, y, w, side, accent, title):
        self.x=x; self.y=y; self.w=w; self.side=side; self.accent=accent; self.title=title
        SW=w-24
        self.sl_min = Slider(x+12, y+46,  SW, "Min CPS", 1,  30, side.min_cps, " cps")
        self.sl_max = Slider(x+12, y+80,  SW, "Max CPS", 1,  30, side.max_cps, " cps")
        self.sl_off = Slider(x+12, y+114, SW, "Offset",  0,  50, 0, " ms", is_int=True)
        self.tg_en  = Toggle(x+12, y+148, "Enable",    side.enabled)
        self.tg_inv = Toggle(x+12, y+168, "Inv-Click", side.inv)
        self.btn    = BtnSmall(x+12, y+196, SW, 22, f"Bind: {key_name(side.bind)}")
        self._pulse = 0.0

    def events(self, e):
        for w in [self.sl_min,self.sl_max,self.sl_off,self.tg_en,self.tg_inv,self.btn]:
            w.event(e)

    def update(self, dt):
        for w in [self.sl_min,self.sl_max,self.sl_off,self.tg_en,self.tg_inv,self.btn]:
            w.update(dt)
        self.side.min_cps = self.sl_min.value
        self.side.max_cps = max(self.sl_min.value, self.sl_max.value)
        self.side.offset  = self.sl_off.value
        self.side.enabled = self.tg_en.value
        self.side.inv     = self.tg_inv.value
        self._pulse += dt*(5 if self.side.enabled else 1.2)

    def draw(self, surf):
        x,y,w = self.x,self.y,self.w
        # card
        rrect(surf, PANEL,  (x,y,w,PH), 8)
        rrect(surf, BORDER, (x,y,w,PH), 8, width=1)
        # top accent strip
        rrect_a(surf, lerp(self.accent,(8,8,14),0.72), (x,y,w,34), 8, alpha=255)
        rrect(surf,   lerp(self.accent,(8,8,14),0.72), (x,y+26,w,8), 0)
        # pulse dot
        dr = int(4+1.5*math.sin(self._pulse))
        dc = ON if self.side.enabled else lerp(BORDER,ACCENT_R,0.6)
        pygame.draw.circle(surf, dc, (x+13,y+16), dr)
        # title
        txt(surf, self.title, font(12,bold=True), WHITE, x+24, y+8)
        # avg cps
        avg = str(self.side.avg_cps)
        txt(surf, avg+" cps", font(10), lerp(SUBTEXT,self.accent,0.9 if self.side.enabled else 0.2), x+w-10, y+10, anchor="topright")
        # widgets
        for wid in [self.sl_min,self.sl_max,self.sl_off]:
            wid.draw(surf, self.accent)
        self.tg_en.draw(surf,  self.accent)
        self.tg_inv.draw(surf, self.accent)
        self.btn.draw(surf, self.accent)

# ═══════════════════════════════════════════════════════════
#  BIND OVERLAY
# ═══════════════════════════════════════════════════════════
class BindOverlay:
    def __init__(self): self.active=False; self.target=None; self.cb=None

    def open(self, target, cb):
        self.active=True; self.target=target; self.cb=cb

    def close(self): self.active=False; self.target=None; self.cb=None

    def draw(self, surf):
        if not self.active: return
        rrect_a(surf, (6,6,12), (0,0,W,H), 0, alpha=215)
        bx,by,bw,bh = W//2-140, H//2-55, 280, 110
        rrect(surf, PANEL2, (bx,by,bw,bh), 10)
        rrect(surf, BORDER, (bx,by,bw,bh), 10, width=1)
        labels = {"left_click":"Left Click Bind","right_click":"Right Click Bind","hide":"Hide Bind"}
        txt(surf, labels.get(self.target,"Bind"), font(13,bold=True), WHITE,   W//2, by+16,  anchor="center")
        txt(surf, "Appuie sur une touche…",        font(11),          TEXT,    W//2, by+38,  anchor="center")
        txt(surf, "M3 / M4 / M5 acceptés  •  Esc = annuler", font(10), SUBTEXT, W//2, by+58, anchor="center")

# ═══════════════════════════════════════════════════════════
#  APP
# ═══════════════════════════════════════════════════════════
class App:
    def __init__(self):
        self.screen = pygame.display.set_mode((W,H), pygame.NOFRAME)
        pygame.display.set_caption("Radon")
        pygame.mixer.init()
        self.clock  = pygame.time.Clock()

        self.engine  = Engine()
        self.engine.start()
        self.hidden  = False
        self.overlay = BindOverlay()
        self._waiting_bind = False
        self._drag_win = False
        self._drag_off = (0,0)
        self._win_pos  = [100,100]
        self._status   = ""
        self._status_t = 0.0

        PAD = 10
        PW  = (W - PAD*3) // 2
        PY  = 56
        self.panel_l = SidePanel(PAD,       PY, PW, self.engine.left,  ACCENT_L, "Left Click")
        self.panel_r = SidePanel(PAD*2+PW,  PY, PW, self.engine.right, ACCENT_R, "Right Click")
        self.btn_hide = BtnSmall(PAD, PY+PH+8, W-PAD*2, 24,
                                  f"Hide Bind: {key_name(self.engine.hide_bind)}")

        self._kb = KbListener(on_press=self._on_key)
        self._kb.start()
        from pynput.mouse import Listener as ML
        self._ml = ML(on_click=self._on_mouse_extra)
        self._ml.start()

    # ───────────────────────────────────────────────────────
    def _on_key(self, key):
        if self._waiting_bind:
            if key == Key.esc:
                self.overlay.close(); self._waiting_bind=False; return
            if self.overlay.cb: self.overlay.cb(key)
            self.overlay.close(); self._waiting_bind=False; return
        if key == self.engine.hide_bind:
            if self.hidden: self._show()
            else:           self._hide()
            return
        if key == self.engine.left.bind:
            self.engine.left.enabled  = not self.engine.left.enabled
            self.panel_l.tg_en.value  = self.engine.left.enabled
        if key == self.engine.right.bind:
            self.engine.right.enabled = not self.engine.right.enabled
            self.panel_r.tg_en.value  = self.engine.right.enabled

    def _on_mouse_extra(self, x, y, button, pressed):
        if not pressed: return
        extra = {Button.middle:"mouse3", Button.x1:"mouse4", Button.x2:"mouse5"}
        bname = extra.get(button)
        if not bname or not self._waiting_bind: return
        if self.overlay.cb: self.overlay.cb(bname)
        self.overlay.close(); self._waiting_bind=False

    def _hide(self):
        self.hidden=True; hide_from_taskbar()

    def _show(self):
        self.hidden=False; show_in_taskbar()

    def _set_bind(self, side_str, k):
        if side_str=="left":
            self.engine.left.bind=k; self.panel_l.btn.label=f"Bind: {key_name(k)}"
            self._status=f"Left bind → {key_name(k)}"
        elif side_str=="right":
            self.engine.right.bind=k; self.panel_r.btn.label=f"Bind: {key_name(k)}"
            self._status=f"Right bind → {key_name(k)}"
        else:
            self.engine.hide_bind=k; self.btn_hide.label=f"Hide Bind: {key_name(k)}"
            self._status=f"Hide bind → {key_name(k)}"
        self._status_t=2.5

    # ───────────────────────────────────────────────────────
    def _draw(self, dt):
        s = self.screen
        s.fill(BG)

        # ── Header gradient ──────────────────────────────────
        for i in range(50):
            t = i/50
            c = lerp(lerp(ACCENT_L,ACCENT_R,0.5), PANEL, t**0.5)
            pygame.draw.line(s, c, (1,i),(W-2,i))

        rrect(s, BORDER, (0,0,W,H), 10, width=1)

        txt(s, "RADON",            font(15,bold=True), WHITE,   16, 10)
        txt(s, "left & right clicker", font(9),        (170,170,195), 16,28)

        total = self.engine.left.avg_cps + self.engine.right.avg_cps
        txt(s, f"{total} cps", font(10), lerp(SUBTEXT,WHITE,0.5), W-14, 20, anchor="topright")

        # close
        cr = pygame.Rect(W-26,8,18,18)
        hov = cr.collidepoint(pygame.mouse.get_pos())
        pygame.draw.circle(s, (210,55,55) if hov else (130,35,35), (W-17,17), 8)
        txt(s, "×", font(13,bold=True), WHITE, W-17,17, anchor="center")

        pygame.draw.line(s, BORDER, (0,50),(W,50), 1)

        # ── Panels + hide btn ────────────────────────────────
        self.panel_l.draw(s)
        self.panel_r.draw(s)
        self.btn_hide.draw(s, lerp(ACCENT_L,ACCENT_R,0.5))

        # ── Footer ───────────────────────────────────────────
        txt(s, "zero network  •  100% local", font(9), lerp(BORDER,SUBTEXT,0.4), W//2, H-12, anchor="center")

        # ── Status ───────────────────────────────────────────
        if self._status_t>0:
            a = min(255, int(self._status_t*350))
            ss = font(10).render(self._status, True, ON)
            ss.set_alpha(a)
            s.blit(ss, (W//2-ss.get_width()//2, H-26))

        self.overlay.draw(s)
        pygame.display.flip()

    # ───────────────────────────────────────────────────────
    def run(self):
        running = True
        prev    = time.perf_counter()
        while running:
            now=time.perf_counter(); dt=now-prev; prev=now

            if self.hidden:
                pygame.event.pump(); time.sleep(0.05); continue

            for e in pygame.event.get():
                if e.type==pygame.QUIT: running=False

                # window drag
                if e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
                    mx,my=e.pos
                    if pygame.Rect(W-26,8,18,18).collidepoint(mx,my):
                        running=False; break
                    if my<50 and mx<W-30:
                        self._drag_win=True
                        try:
                            import ctypes as _c
                            pt=_c.wintypes.POINT()
                            _c.windll.user32.GetCursorPos(_c.byref(pt))
                            self._drag_off=(pt.x,pt.y)
                            r=_c.wintypes.RECT()
                            _c.windll.user32.GetWindowRect(_hwnd(),_c.byref(r))
                            self._win_pos=[r.left,r.top]
                        except: pass

                if e.type==pygame.MOUSEBUTTONUP   and e.button==1: self._drag_win=False

                if e.type==pygame.MOUSEMOTION and self._drag_win:
                    try:
                        import ctypes as _c
                        pt=_c.wintypes.POINT()
                        _c.windll.user32.GetCursorPos(_c.byref(pt))
                        dx=pt.x-self._drag_off[0]; dy=pt.y-self._drag_off[1]
                        _c.windll.user32.MoveWindow(_hwnd(),
                            self._win_pos[0]+dx, self._win_pos[1]+dy, W, H, True)
                    except: pass

                if not self.overlay.active:
                    self.panel_l.events(e)
                    self.panel_r.events(e)
                    self.btn_hide.event(e)

                if self.panel_l.btn.clicked and not self.overlay.active:
                    self._waiting_bind=True
                    self.overlay.open("left_click", lambda k: self._set_bind("left",k))
                if self.panel_r.btn.clicked and not self.overlay.active:
                    self._waiting_bind=True
                    self.overlay.open("right_click", lambda k: self._set_bind("right",k))
                if self.btn_hide.clicked and not self.overlay.active:
                    self._waiting_bind=True
                    self.overlay.open("hide", lambda k: self._set_bind("hide",k))

            self.panel_l.update(dt)
            self.panel_r.update(dt)
            self.btn_hide.update(dt)
            if self._status_t>0: self._status_t=max(0,self._status_t-dt)

            self._draw(dt)
            self.clock.tick(60)

        self.engine.stop()
        self._kb.stop()
        self._ml.stop()
        pygame.quit()
        sys.exit()

if __name__=="__main__":
    App().run()
