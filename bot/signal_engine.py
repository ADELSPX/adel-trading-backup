"""محرك إشارات «مراكز سيولة الصانع» — ترجمة حرفية لمنطق مؤشر القناة (Pine Script v6).

القواعد مطابقة لكود المؤشر الرسمي في قناة GAMMA FAHAD VIP:
- NEAR_PCT=15: القرب من مركز = 15% من المسافة للمركز التالي
- شمعة نظيفة واحدة (N_CANDLES=1) بجانب المركز تعطي الإشارة
- الوقف = المركز نفسه، الهدف = المركز التالي، أقصى مدة 60 دقيقة
- إغلاق نهاية اليوم دائمًا، صفقة/حالة واحدة في كل مرة
الدخول يُنفَّذ مع افتتاح الشمعة التالية للإشارة (كما ينص المؤشر).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

NEAR_PCT = 15.0
PRICE_PCT = 0.1
N_CANDLES = 1
MAX_MIN = 60
SW_LEN = 4
SW_MIN = 3
SW_THICK = 10.0


@dataclass
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float


@dataclass
class Event:
    kind: str            # CALL | PUT | EXIT
    t: datetime
    price_ref: float     # سعر الإشارة (إغلاق شمعة الإشارة) أو سعر الخروج المرجعي
    center: Optional[float] = None
    target: Optional[float] = None
    stop: Optional[float] = None
    reason: str = ""     # سبب الخروج
    sideways: bool = False


def _below(levels, x):
    r = None
    for v in levels:
        if v < x:
            r = v if r is None else max(r, v)
    return r


def _above(levels, x):
    r = None
    for v in levels:
        if v > x:
            r = v if r is None else min(r, v)
    return r


def _touched(levels, h, l, ref):
    r = None
    for v in levels:
        if l <= v <= h:
            if r is None or abs(v - ref) < abs(r - ref):
                r = v
    return r


def _zone_tol(levels, c, direction, close):
    """مسافة القرب لمركز: 15% من المسافة للمركز التالي باتجاه الصفقة."""
    if c is None:
        return None
    other = _above(levels, c) if direction == 1 else _below(levels, c)
    if other is None:
        return close * PRICE_PCT / 100.0
    return abs(other - c) * NEAR_PCT / 100.0


class MakerCentersEngine:
    def __init__(self, timeframe_minutes: int = 5):
        self.tf = timeframe_minutes
        self.bars_max = max(1, round(MAX_MIN * 60 / (timeframe_minutes * 60)))
        self.reset_day(None, [])

    def reset_day(self, day: Optional[date], levels):
        self.day = day
        self.levels = sorted(levels)
        self.st_dir = 0            # 1 كول / -1 بوت / 0 لا حالة
        self.st_ctr = None
        self.st_tgt = None
        self.st_tol = None
        self.st_bar_idx = None
        self.last_ctr = None
        self.g_run = 0
        self.r_run = 0
        self.pos = 0
        self.stop = None
        self.entry_idx = None
        self.bar_idx = -1
        self.history: list[Bar] = []

    def on_bar(self, bar: Bar) -> list[Event]:
        """يُستدعى عند إغلاق كل شمعة. يعيد أحداث هذا الشمعة (دخول/خروج)."""
        events: list[Event] = []
        if self.day is None or bar.t.date() != self.day:
            # يوم جديد: إغلاق أي صفقة على الشمعة السابقة، وتصفير الحالة
            if self.pos != 0 and self.history:
                prev = self.history[-1]
                events.append(Event("EXIT", prev.t, prev.c, reason="نهاية اليوم"))
            self.reset_day(bar.t.date(), self.levels)
        self.bar_idx += 1
        self.history.append(bar)
        lv = self.levels
        if not lv:
            return events

        is_green = bar.c > bar.o
        is_red = bar.c < bar.o
        tc = _touched(lv, bar.h, bar.l, bar.c)     # مركز لمسته الشمعة
        cb = _below(lv, bar.l)                      # المركز تحت الشمعة
        ca = _above(lv, bar.h)                      # المركز فوق الشمعة
        sideways = self._touch_count(tc) >= SW_MIN if tc is not None else False

        sig_exit = False

        # 1) خروج الصفقة
        if self.pos != 0 and self.bar_idx > self.entry_idx:
            why = ""
            if self.pos == 1:
                if bar.l <= self.stop:
                    why = "لمس المركز / الوقف"
                elif self.st_tgt is not None and bar.h >= self.st_tgt - self.st_tol:
                    why = "المركز التالي"
            else:
                if bar.h >= self.stop:
                    why = "لمس المركز / الوقف"
                elif self.st_tgt is not None and bar.l <= self.st_tgt + self.st_tol:
                    why = "المركز التالي"
            if not why and self.bar_idx - self.entry_idx >= self.bars_max:
                why = f"{MAX_MIN} دقيقة"
            if why:
                events.append(Event("EXIT", bar.t, bar.c, reason=why, sideways=sideways))
                self.pos = 0
                sig_exit = True

        # 2) نهاية الحالة: لمس مركزها أو بلوغ هدفها
        if self.st_dir != 0 and self.bar_idx > self.st_bar_idx:
            touched_ctr = bar.l <= self.st_ctr <= bar.h
            reached = False
            if self.st_tgt is not None:
                reached = (bar.h >= self.st_tgt - self.st_tol) if self.st_dir == 1 \
                    else (bar.l <= self.st_tgt + self.st_tol)
            if touched_ctr or reached:
                self.st_dir = 0

        # 3) آخر مركز كان السعر عنده (لمس أو اقتراب)
        if tc is not None:
            self.last_ctr = tc
        else:
            near_b = cb if (cb is not None and bar.l - cb <= _zone_tol(lv, cb, 1, bar.c)) else None
            near_a = ca if (ca is not None and ca - bar.h <= _zone_tol(lv, ca, -1, bar.c)) else None
            if near_b is not None and (near_a is None or bar.l - near_b <= near_a - bar.h):
                self.last_ctr = near_b
            elif near_a is not None:
                self.last_ctr = near_a

        # 4) الشموع النظيفة بجانب المركز
        g_side = is_green and tc is None and self.last_ctr is not None and cb is not None and cb == self.last_ctr
        r_side = is_red and tc is None and self.last_ctr is not None and ca is not None and ca == self.last_ctr
        self.g_run = self.g_run + 1 if g_side else 0
        self.r_run = self.r_run + 1 if r_side else 0

        # 5) الإشارة
        if self.st_dir == 0 and self.pos == 0 and not sig_exit:
            if self.g_run == N_CANDLES:
                tgt = _above(lv, self.last_ctr)
                tol = None if tgt is None else (tgt - self.last_ctr) * NEAR_PCT / 100.0
                self.pos = 1
                self.st_dir = 1
                self.st_ctr = self.last_ctr
                self.st_tgt = tgt
                self.st_tol = tol
                self.stop = self.last_ctr
                self.entry_idx = self.bar_idx
                self.st_bar_idx = self.bar_idx
                events.append(Event("CALL", bar.t, bar.c, center=self.st_ctr,
                                    target=tgt, stop=self.stop, sideways=sideways))
            elif self.r_run == N_CANDLES:
                tgt = _below(lv, self.last_ctr)
                tol = None if tgt is None else (self.last_ctr - tgt) * NEAR_PCT / 100.0
                self.pos = -1
                self.st_dir = -1
                self.st_ctr = self.last_ctr
                self.st_tgt = tgt
                self.st_tol = tol
                self.stop = self.last_ctr
                self.entry_idx = self.bar_idx
                self.st_bar_idx = self.bar_idx
                events.append(Event("PUT", bar.t, bar.c, center=self.st_ctr,
                                    target=tgt, stop=self.stop, sideways=sideways))
        return events

    def _touch_count(self, c) -> int:
        if c is None:
            return 0
        n = 0
        for b in self.history[-SW_LEN:]:
            if b.l <= c <= b.h:
                n += 1
        return n


def parse_levels_line(line: str):
    """'SPY: 774.97, 773.57, 769.35*, 764.32' -> ('SPY', [levels], strongest)"""
    if ":" not in line:
        return None
    sym, rest = line.split(":", 1)
    levels, strongest = [], None
    for item in rest.replace("،", ",").split(","):
        item = item.strip()
        if not item:
            continue
        strong = "*" in item
        try:
            v = float(item.replace("*", ""))
        except ValueError:
            continue
        levels.append(v)
        if strong:
            strongest = v
    return sym.strip().upper(), levels, strongest
