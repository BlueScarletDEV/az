"""
Radon Clicker - Python/pygame
Reconstruction propre basée sur l'analyse de Radon (Runtime_Broker.exe)
Fonctionnalités : Left Clicker, CPS min/max, offset delay, average CPS,
                  inv-click, toggle, bind customisable, click sounds, UI dark
Zéro réseau. Zéro collecte. 100% local.
Dépendances : pip install pygame pynput
"""

import pygame
import time
import random
import threading
import math
import os
import sys
from collections import deque
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, KeyCode, Listener as KeyListener

# ─────────────────────────────────────────────────────────────
#  CONSTANTS & THEME (Radon dark palette)
# ─────────────────────────────────────────────────────────────
W, H = 340, 490

C = {
    "bg":        (15,  15,  20),
    "panel":     (22,  22,  30),
    "border":    (45,  45,  60),
    "accent":    (100, 140, 255),
    "accent2":   (70,  110, 220),
    "on":        (80,  200, 120),
    "off":       (200, 70,  70),
    "text":      (220, 220, 235),
    "subtext":   (130, 130, 150),
    "handle":    (100, 140, 255),
    "track_bg":  (35,  35,  48),
    "shadow":    (0,   0,   0,  120),
    "hover":     (35,  35,  50),
    "white":     (255, 255, 255),
    "overlay":   (10,  10,  15,  200),
}

# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
def lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i]-a[i])*t) for i in range(3))

def draw_rounded_rect(surf, color, rect, r, alpha=255):
    s = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
    pygame.draw.rect(s, (*color[:3], alpha), (0, 0, rect[2], rect[3]), border_radius=r)
    surf.blit(s, (rect[0], rect[1]))

def draw_border_rect(surf, color, rect, r, width=1):
    pygame.draw.rect(surf, color, rect, width=width, border_radius=r)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

# ─────────────────────────────────────────────────────────────
#  FONTS (loaded once)
# ─────────────────────────────────────────────────────────────
pygame.init()
try:
    FONT_TITLE  = pygame.font.SysFont("Consolas", 15, bold=True)
    FONT_LABEL  = pygame.font.SysFont("Consolas", 12)
    FONT_SMALL  = pygame.font.SysFont("Consolas", 11)
    FONT_BIG    = pygame.font.SysFont("Consolas", 26, bold=True)
    FONT_MED    = pygame.font.SysFont("Consolas", 18, bold=True)
except:
    FONT_TITLE  = pygame.font.Font(None, 16)
    FONT_LABEL  = pygame.font.Font(None, 13)
    FONT_SMALL  = pygame.font.Font(None, 12)
    FONT_BIG    = pygame.font.Font(None, 28)
    FONT_MED    = pygame.font.Font(None, 20)

# ─────────────────────────────────────────────────────────────
#  UI COMPONENTS
# ─────────────────────────────────────────────────────────────
class SkeetSlider:
    """Slider style Radon avec label valeur + suffix"""
    def __init__(self, x, y, w, h, min_v, max_v, value, label, suffix="", is_float=True):
        self.rect   = pygame.Rect(x, y, w, h)
        self.min_v  = min_v
        self.max_v  = max_v
        self.value  = value
        self.label  = label
        self.suffix = suffix
        self.is_float = is_float
        self.dragging = False
        self._hover   = False
        self._anim    = 0.0

    @property
    def norm(self):
        return (self.value - self.min_v) / (self.max_v - self.min_v)

    def handle_event(self, event):
        mx, my = pygame.mouse.get_pos()
        self._hover = self.rect.collidepoint(mx, my)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.dragging = True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        if event.type == pygame.MOUSEMOTION and self.dragging:
            t = clamp((event.pos[0] - self.rect.x) / self.rect.w, 0, 1)
            raw = self.min_v + t * (self.max_v - self.min_v)
            self.value = raw if self.is_float else int(round(raw))

    def update(self, dt):
        target = 1.0 if (self._hover or self.dragging) else 0.0
        self._anim += (target - self._anim) * min(1, dt * 10)

    def draw(self, surf):
        x, y, w, h = self.rect
        track_h = 4
        ty = y + h//2 - track_h//2

        # track background
        draw_rounded_rect(surf, C["track_bg"], (x, ty, w, track_h), 2)

        # track fill
        fw = int(w * self.norm)
        if fw > 0:
            col = lerp_color(C["accent2"], C["accent"], self._anim)
            draw_rounded_rect(surf, col, (x, ty, fw, track_h), 2)

        # handle
        hx = x + int(w * self.norm)
        hr = int(7 + self._anim * 2)
        hcol = lerp_color(C["accent2"], C["accent"], self._anim)
        pygame.draw.circle(surf, C["bg"], (hx, ty + track_h//2), hr + 2)
        pygame.draw.circle(surf, hcol,   (hx, ty + track_h//2), hr)

        # label (left) + value (right)
        lbl  = FONT_SMALL.render(self.label, True, C["subtext"])
        surf.blit(lbl, (x, y))
        val_str = (f"{self.value:.1f}" if self.is_float else str(int(self.value))) + self.suffix
        val_surf = FONT_SMALL.render(val_str, True, C["text"])
        surf.blit(val_surf, (x + w - val_surf.get_width(), y))


class SkeetCheckbox:
    """Toggle checkbox style Radon"""
    def __init__(self, x, y, label, value=False):
        self.rect  = pygame.Rect(x, y, 16, 16)
        self.label = label
        self.value = value
        self._anim = 1.0 if value else 0.0

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            lbl_w = FONT_LABEL.size(self.label)[0]
            hit = pygame.Rect(self.rect.x, self.rect.y, 16 + 6 + lbl_w, 16)
            if hit.collidepoint(event.pos):
                self.value = not self.value

    def update(self, dt):
        target = 1.0 if self.value else 0.0
        self._anim += (target - self._anim) * min(1, dt * 12)

    def draw(self, surf):
        x, y = self.rect.x, self.rect.y
        col = lerp_color(C["border"], C["accent"], self._anim)
        draw_rounded_rect(surf, col, (x, y, 16, 16), 4)
        draw_border_rect(surf, lerp_color(C["border"], C["accent"], self._anim), (x, y, 16, 16), 4)
        if self._anim > 0.05:
            # checkmark
            a = self._anim
            p1 = (x+3, y+8)
            p2 = (x+7, y+12)
            p3 = (x+13, y+4)
            alpha_col = (*C["white"], int(255*a))
            s = pygame.Surface((16,16), pygame.SRCALPHA)
            pygame.draw.lines(s, (255,255,255,int(255*a)), False, [(3,8),(7,12),(13,4)], 2)
            surf.blit(s, (x, y))
        lbl = FONT_LABEL.render(self.label, True,
                                lerp_color(C["subtext"], C["text"], self._anim))
        surf.blit(lbl, (x + 22, y + 1))


class SkeetButton:
    """Bouton gradient style Radon"""
    def __init__(self, x, y, w, h, label, color=None, text_color=None):
        self.rect  = pygame.Rect(x, y, w, h)
        self.label = label
        self.color = color or C["accent"]
        self.tcol  = text_color or C["white"]
        self._hover = False
        self._press = False
        self._anim  = 0.0
        self.clicked = False

    def handle_event(self, event):
        self.clicked = False
        mx, my = pygame.mouse.get_pos()
        self._hover = self.rect.collidepoint(mx, my)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self._press = True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._press and self.rect.collidepoint(event.pos):
                self.clicked = True
            self._press = False

    def update(self, dt):
        target = 1.0 if (self._hover or self._press) else 0.0
        self._anim += (target - self._anim) * min(1, dt * 12)

    def draw(self, surf):
        x, y, w, h = self.rect
        base = lerp_color(C["panel"], self.color, 0.4 + self._anim*0.3)
        draw_rounded_rect(surf, base, (x, y, w, h), 6)
        draw_border_rect(surf, lerp_color(C["border"], self.color, self._anim),
                         (x, y, w, h), 6, 1)
        lbl = FONT_LABEL.render(self.label, True, self.tcol)
        surf.blit(lbl, (x + w//2 - lbl.get_width()//2, y + h//2 - lbl.get_height()//2))


# ─────────────────────────────────────────────────────────────
#  BIND OVERLAY — waiting for a key press
# ─────────────────────────────────────────────────────────────
class BindOverlay:
    def __init__(self):
        self.active = False
        self.target = None  # "click" | "hide"

    def open(self, target):
        self.active = True
        self.target = target

    def close(self):
        self.active = False
        self.target = None

    def draw(self, surf):
        if not self.active:
            return
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((10, 10, 15, 210))
        surf.blit(overlay, (0, 0))
        t1 = FONT_MED.render("Press a key…", True, C["text"])
        t2 = FONT_SMALL.render("Esc to cancel", True, C["subtext"])
        surf.blit(t1, (W//2 - t1.get_width()//2, H//2 - 20))
        surf.blit(t2, (W//2 - t2.get_width()//2, H//2 + 10))


# ─────────────────────────────────────────────────────────────
#  CLICKER ENGINE
# ─────────────────────────────────────────────────────────────
class ClickerEngine:
    def __init__(self):
        self.mouse     = MouseController()
        self.enabled   = False
        self.inv_click = False
        self.min_cps   = 8.0
        self.max_cps   = 12.0
        self.offset    = 0
        self.click_bind  = Key.f6          # bind hotkey
        self.hide_bind   = Key.f7
        self.sound_path  = None
        self.sound_obj   = None
        self._thread   = None
        self._stop     = threading.Event()
        self._lock     = threading.Lock()
        self._cps_log  = deque(maxlen=60)  # timestamps for avg CPS
        self.current_delay = 0.0           # ms displayed
        self.click_sound_enabled = False

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def average_cps(self):
        now = time.perf_counter()
        recent = [t for t in self._cps_log if now - t < 1.0]
        return len(recent)

    def _click(self):
        if self.inv_click:
            self.mouse.release(Button.left)
            time.sleep(0.01)
            self.mouse.press(Button.left)
        else:
            self.mouse.press(Button.left)
            time.sleep(0.01)
            self.mouse.release(Button.left)
        self._cps_log.append(time.perf_counter())
        if self.click_sound_enabled and self.sound_obj:
            try:
                self.sound_obj.play()
            except:
                pass

    def _loop(self):
        from pynput.mouse import Button as Btn
        import ctypes
        while not self._stop.is_set():
            if self.enabled:
                # Check left mouse button held (GetAsyncKeyState style)
                try:
                    state = ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000
                except:
                    state = 0
                if state:
                    cps = random.uniform(self.min_cps, self.max_cps)
                    base_delay = 1.0 / cps
                    # offset jitter (delay offset slider)
                    jitter = random.uniform(-self.offset/1000, self.offset/1000)
                    delay = max(0.01, base_delay + jitter)
                    self.current_delay = delay * 1000
                    self._click()
                    time.sleep(delay)
                else:
                    self.current_delay = 0
                    time.sleep(0.005)
            else:
                self.current_delay = 0
                time.sleep(0.02)

    def load_sound(self, path):
        try:
            self.sound_obj  = pygame.mixer.Sound(path)
            self.sound_path = path
            return True
        except:
            return False


# ─────────────────────────────────────────────────────────────
#  MAIN APP
# ─────────────────────────────────────────────────────────────
class RadonApp:
    def __init__(self):
        self.screen = pygame.display.set_mode((W, H), pygame.NOFRAME)
        pygame.display.set_caption("Radon")
        pygame.mixer.init()
        self.clock  = pygame.time.Clock()

        self.engine = ClickerEngine()
        self.engine.start()

        # UI state
        self.dragging_window = False
        self.drag_offset     = (0, 0)
        self.bind_overlay    = BindOverlay()
        self.waiting_bind    = None   # "click" | "hide"
        self.status_msg      = ""
        self.status_timer    = 0.0
        self.hidden          = False

        # ── Sliders ──────────────────────────────────────────
        SX, SW = 24, W - 48
        self.sl_min = SkeetSlider(SX, 130, SW, 22, 1.0, 30.0, 8.0,  "Min CPS",    " cps", is_float=True)
        self.sl_max = SkeetSlider(SX, 168, SW, 22, 1.0, 30.0, 12.0, "Max CPS",    " cps", is_float=True)
        self.sl_off = SkeetSlider(SX, 206, SW, 22, 0,   50,   0,    "Delay Offset"," ms", is_float=False)
        self.sliders = [self.sl_min, self.sl_max, self.sl_off]

        # ── Checkboxes ────────────────────────────────────────
        self.cb_toggle = SkeetCheckbox(SX, 260, "Enable Clicker", value=False)
        self.cb_inv    = SkeetCheckbox(SX, 284, "Inv-Click (release → press)", value=False)
        self.cb_sound  = SkeetCheckbox(SX, 308, "Click Sounds", value=False)
        self.checkboxes = [self.cb_toggle, self.cb_inv, self.cb_sound]

        # ── Buttons ───────────────────────────────────────────
        BW = (SW - 8) // 2
        self.btn_bind_click = SkeetButton(SX,           342, BW, 26, "Click Bind: F6")
        self.btn_bind_hide  = SkeetButton(SX + BW + 8, 342, BW, 26, "Hide Bind: F7")
        self.btn_load_sound = SkeetButton(SX,           376, SW, 26, "Load Click Sound…",
                                          color=C["track_bg"])
        self.buttons = [self.btn_bind_click, self.btn_bind_hide, self.btn_load_sound]

        # ── Keyboard listener ─────────────────────────────────
        self._key_listener = KeyListener(on_press=self._on_key)
        self._key_listener.start()

        # pulse anim for CPS dot
        self._pulse = 0.0
        self._pulse_dir = 1

    # ──────────────────────────────────────────────────────────
    #  KEY LISTENER (global hotkeys)
    # ──────────────────────────────────────────────────────────
    def _on_key(self, key):
        if self.waiting_bind:
            # ESC cancels
            if key == Key.esc:
                self.waiting_bind = None
                self.bind_overlay.close()
                return
            # assign bind
            if self.waiting_bind == "click":
                self.engine.click_bind = key
                name = getattr(key, 'char', None) or str(key).replace('Key.','').upper()
                self.btn_bind_click.label = f"Click Bind: {name}"
            else:
                self.engine.hide_bind = key
                name = getattr(key, 'char', None) or str(key).replace('Key.','').upper()
                self.btn_bind_hide.label = f"Hide Bind: {name}"
            self.waiting_bind = None
            self.bind_overlay.close()
            return

        if key == self.engine.click_bind:
            self.engine.enabled = not self.engine.enabled
            self.cb_toggle.value = self.engine.enabled

        if key == self.engine.hide_bind:
            self.hidden = not self.hidden

    # ──────────────────────────────────────────────────────────
    #  SYNC engine ← UI
    # ──────────────────────────────────────────────────────────
    def _sync(self):
        self.engine.min_cps   = self.sl_min.value
        self.engine.max_cps   = max(self.sl_min.value, self.sl_max.value)
        self.engine.offset    = self.sl_off.value
        self.engine.enabled   = self.cb_toggle.value
        self.engine.inv_click = self.cb_inv.value
        self.engine.click_sound_enabled = self.cb_sound.value

    # ──────────────────────────────────────────────────────────
    #  DRAW HELPERS
    # ──────────────────────────────────────────────────────────
    def _draw_bg(self):
        self.screen.fill(C["bg"])
        # top accent bar
        draw_rounded_rect(self.screen, C["panel"], (0, 0, W, H), 10)
        draw_border_rect(self.screen, C["border"], (0, 0, W, H), 10, 1)

    def _draw_header(self):
        # gradient title bar
        bar = pygame.Surface((W, 52), pygame.SRCALPHA)
        for i in range(52):
            t = i / 52
            col = lerp_color(C["accent2"], C["panel"], t)
            pygame.draw.line(bar, col, (0, i), (W, i))
        self.screen.blit(bar, (0, 0))
        draw_border_rect(self.screen, C["border"], (0, 0, W, H), 10, 1)

        # title
        t = FONT_TITLE.render("RADON", True, C["white"])
        self.screen.blit(t, (16, 14))
        sub = FONT_SMALL.render("Left Clicker  •  v2.0", True, (180,180,200))
        self.screen.blit(sub, (16, 32))

        # close button
        pygame.draw.circle(self.screen, (200,60,60), (W-20, 18), 8)
        cl = FONT_SMALL.render("×", True, C["white"])
        self.screen.blit(cl, (W-20 - cl.get_width()//2, 18 - cl.get_height()//2))

    def _draw_cps_panel(self, dt):
        # panel
        px, py, pw, ph = 24, 62, W-48, 56
        draw_rounded_rect(self.screen, C["track_bg"], (px, py, pw, ph), 8)
        draw_border_rect(self.screen, C["border"], (px, py, pw, ph), 8, 1)

        avg = self.engine.average_cps
        cur_delay = self.engine.current_delay
        active = self.engine.enabled

        # pulse dot
        self._pulse += dt * (4 if active else 1)
        dot_r = int(5 + 2 * math.sin(self._pulse))
        dot_col = C["on"] if active else C["off"]
        pygame.draw.circle(self.screen, dot_col, (px+15, py+ph//2), dot_r)

        # avg CPS big number
        cps_str = f"{avg}"
        big = FONT_BIG.render(cps_str, True, C["white"] if active else C["subtext"])
        self.screen.blit(big, (px + 36, py + 6))
        unit = FONT_SMALL.render("avg cps", True, C["subtext"])
        self.screen.blit(unit, (px + 36, py + 36))

        # current delay
        delay_str = f"{cur_delay:.1f} ms" if cur_delay else "—"
        dl = FONT_SMALL.render(f"delay: {delay_str}", True, C["subtext"])
        self.screen.blit(dl, (px + pw - dl.get_width() - 10, py + 10))

        # status
        state_str = "ACTIVE" if active else "IDLE"
        sc = C["on"] if active else C["subtext"]
        sl = FONT_SMALL.render(state_str, True, sc)
        self.screen.blit(sl, (px + pw - sl.get_width() - 10, py + 30))

    def _draw_section(self, label, y):
        lbl = FONT_SMALL.render(label.upper(), True, C["subtext"])
        self.screen.blit(lbl, (24, y))
        pygame.draw.line(self.screen, C["border"],
                         (24 + lbl.get_width() + 6, y + 5),
                         (W - 24, y + 5), 1)

    def _draw_status(self):
        if self.status_timer > 0:
            alpha = min(255, int(self.status_timer * 510))
            s = FONT_SMALL.render(self.status_msg, True, C["on"])
            s.set_alpha(alpha)
            self.screen.blit(s, (W//2 - s.get_width()//2, H - 22))

    def _draw_footer(self):
        f = FONT_SMALL.render("Zero network  •  100% local", True, C["border"])
        self.screen.blit(f, (W//2 - f.get_width()//2, H - 18))

    # ──────────────────────────────────────────────────────────
    #  MAIN LOOP
    # ──────────────────────────────────────────────────────────
    def run(self):
        running = True
        prev_t  = time.perf_counter()

        while running:
            now = time.perf_counter()
            dt  = now - prev_t
            prev_t = now

            if self.hidden:
                time.sleep(0.05)
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                continue

            # ── Events ───────────────────────────────────────
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

                # window drag
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = event.pos
                    if my < 52 and mx < W - 30:
                        self.dragging_window = True
                        wx, wy = pygame.display.get_surface().get_abs_offset() if hasattr(pygame.display, 'get_surface') else (0,0)
                        try:
                            import ctypes
                            cx, cy = ctypes.windll.user32.GetCursorPos.__func__() if False else (0,0)
                        except:
                            pass
                        self.drag_offset = event.pos

                    # close button
                    if mx >= W-28 and my <= 28:
                        running = False

                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self.dragging_window = False

                if event.type == pygame.MOUSEMOTION and self.dragging_window:
                    pass  # window move handled below

                # sliders / checkboxes / buttons
                if not self.bind_overlay.active:
                    for sl in self.sliders:
                        sl.handle_event(event)
                    for cb in self.checkboxes:
                        cb.handle_event(event)
                    for btn in self.buttons:
                        btn.handle_event(event)

                # button actions
                if self.btn_bind_click.clicked:
                    self.waiting_bind = "click"
                    self.bind_overlay.open("click")

                if self.btn_bind_hide.clicked:
                    self.waiting_bind = "hide"
                    self.bind_overlay.open("hide")

                if self.btn_load_sound.clicked:
                    self._open_sound_dialog()

            # ── Sync & Update ────────────────────────────────
            self._sync()

            for sl in self.sliders:  sl.update(dt)
            for cb in self.checkboxes: cb.update(dt)
            for btn in self.buttons:   btn.update(dt)

            if self.status_timer > 0:
                self.status_timer = max(0, self.status_timer - dt)

            # ── Draw ─────────────────────────────────────────
            self._draw_bg()
            self._draw_header()
            self._draw_cps_panel(dt)

            self._draw_section("CPS Settings", 118)
            for sl in self.sliders:
                sl.draw(self.screen)

            self._draw_section("Options", 248)
            for cb in self.checkboxes:
                cb.draw(self.screen)

            self._draw_section("Binds", 330)
            for btn in self.buttons:
                btn.draw(self.screen)

            self._draw_footer()
            self._draw_status()
            self.bind_overlay.draw(self.screen)

            pygame.display.flip()
            self.clock.tick(60)

        self.engine.stop()
        self._key_listener.stop()
        pygame.quit()
        sys.exit()

    def _open_sound_dialog(self):
        """Open a file dialog to pick a .wav sound"""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            path = filedialog.askopenfilename(
                title="Select click sound",
                filetypes=[("WAV Files", "*.wav"), ("All files", "*.*")]
            )
            root.destroy()
            if path:
                ok = self.engine.load_sound(path)
                if ok:
                    self.status_msg   = f"Sound loaded: {os.path.basename(path)}"
                    self.status_timer = 2.5
                    self.cb_sound.value = True
                else:
                    self.status_msg   = "Failed to load sound (WAV only)"
                    self.status_timer = 2.5
        except Exception as e:
            self.status_msg   = "Tkinter unavailable for dialog"
            self.status_timer = 2.5


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = RadonApp()
    app.run()
