"""مدير التداول الورقي (دون اتصال) — نواة منفّذ البوت قبل ربط IBKR.

يستخرج قواعد إدارة الصفقة المثبتة في STRATEGY.md والمجرّبة في backtest.py
إلى مكوّن مستقل قابل للاختبار والربط لاحقًا بأي مصدر أسعار (IBKR ورقي أولًا):

- بوابات الدخول: أقوى مركز فقط، حجب العرضي، تهدئة 60 دقيقة بعد وقف عند
  المركز نفسه (قواعد الحلقات + فلتر الباك-تست الأفضل).
- الخروج (الأسبق يفوز): وقف بريميوم −20% | هدف بريميوم +30% (بيع كامل) |
  شمعتان معاكستان متتاليتان | خروج المحرك (مركز/هدف/60د) | إغلاق نهاية اليوم.
- التكاليف: عمولة IBKR 0.65$ للعقد لكل جهة + 0.10$ رسوم ≈ 1.40$ للصفقة
  الكاملة، ومضاعف العقد 100 (كما في backtest.py).

لا يتصل بأي وسيط ولا ينفّذ تداولًا حقيقيًا: المتصل هو من يزوّده بسعر
البريميوم (حيًا من IBKR لاحقًا، أو محاكاةً الآن) وبإشارة الشمعة المعاكسة
من شمعة الإدارة، وهو من يقرر مصدر الأسعار. لا مبيت أبدًا: close_day().
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

STOP_PCT = 0.20
TARGET_PCT = 0.30
COMMISSION_RT = 1.40
MULTIPLIER = 100
COOLDOWN_MINUTES = 60


@dataclass
class Position:
    direction: str            # CALL | PUT
    center: Optional[float]
    entry_t: datetime
    entry_premium: float
    adverse_run: int = 0


@dataclass
class Trade:
    direction: str
    center: Optional[float]
    entry_t: datetime
    entry_premium: float
    exit_t: datetime
    exit_premium: float
    reason: str
    pnl: float = field(init=False)

    def __post_init__(self):
        self.pnl = (self.exit_premium - self.entry_premium) * MULTIPLIER - COMMISSION_RT


class PaperTradeManager:
    """صفقة واحدة في كل مرة (قاعدة المؤشر)، وبوابات دخول قبل أي فتح."""

    def __init__(self, strongest_only: bool = True, block_sideways: bool = True,
                 cooldown_minutes: int = COOLDOWN_MINUTES):
        self.strongest_only = strongest_only
        self.block_sideways = block_sideways
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self.last_stop: dict[float, datetime] = {}

    def can_enter(self, center: Optional[float], strongest_center: Optional[float] = None,
                  sideways: bool = False, now: Optional[datetime] = None):
        """(مسموح؟، السبب). تُستدعى قبل open() عند كل إشارة من المحرك."""
        if self.position is not None:
            return False, "صفقة مفتوحة"
        if self.block_sideways and sideways:
            return False, "عرضي — لا دخول"
        if self.strongest_only and strongest_center is not None \
                and center is not None and center != strongest_center:
            return False, "ليس أقوى مركز"
        if center is not None and now is not None and center in self.last_stop:
            if now - self.last_stop[center] < self.cooldown:
                return False, "تهدئة بعد وقف عند المركز نفسه"
        return True, ""

    def open(self, direction: str, premium: float, t: datetime,
             center: Optional[float] = None) -> Position:
        if self.position is not None:
            raise ValueError("صفقة مفتوحة بالفعل")
        if direction not in ("CALL", "PUT"):
            raise ValueError("الاتجاه CALL أو PUT فقط")
        self.position = Position(direction, center, t, premium)
        return self.position

    def on_premium(self, premium: float, t: datetime, adverse: bool = False):
        """تغذية بسعر بريميوم جديد (وشمعة معاكسة؟). تعيد الصفقة المغلقة أو None."""
        pos = self.position
        if pos is None:
            return None
        if premium <= pos.entry_premium * (1 - STOP_PCT):
            return self._close(pos.entry_premium * (1 - STOP_PCT), t, "وقف بريميوم −20%", stop=True)
        if premium >= pos.entry_premium * (1 + TARGET_PCT):
            return self._close(pos.entry_premium * (1 + TARGET_PCT), t, "هدف بريميوم +30%")
        pos.adverse_run = pos.adverse_run + 1 if adverse else 0
        if pos.adverse_run >= 2:
            return self._close(premium, t, "شمعتان معاكستان")
        return None

    def on_engine_exit(self, premium: float, t: datetime, reason: str):
        """خروج من محرك الإشارات (لمس مركز/هدف/60 دقيقة)."""
        if self.position is None:
            return None
        stop = "الوقف" in reason or "لمس المركز" in reason
        return self._close(premium, t, f"محرك: {reason}", stop=stop)

    def close_day(self, premium: float, t: datetime):
        if self.position is None:
            return None
        return self._close(premium, t, "نهاية اليوم")

    def _close(self, premium: float, t: datetime, reason: str, stop: bool = False):
        pos = self.position
        self.position = None
        if stop and pos.center is not None:
            self.last_stop[pos.center] = t
        trade = Trade(pos.direction, pos.center, pos.entry_t, pos.entry_premium, t, premium, reason)
        self.trades.append(trade)
        return trade

    @property
    def total_pnl(self) -> float:
        return round(sum(tr.pnl for tr in self.trades), 2)


if __name__ == "__main__":
    # اختبار دخان: القواعد الخمس + البوابات، بلا بيانات خارجية.
    m = PaperTradeManager()
    t0 = datetime(2026, 11, 2, 17, 30)
    ok, why = m.can_enter(770.0, strongest_center=769.0)
    assert not ok and why == "ليس أقوى مركز"
    ok, why = m.can_enter(769.0, strongest_center=769.0, sideways=True)
    assert not ok and why == "عرضي — لا دخول"
    m.open("CALL", 1.20, t0, center=769.0)
    tr = m.on_premium(1.56, t0)
    assert tr is not None and tr.reason == "هدف بريميوم +30%" and abs(tr.pnl - 34.60) < 1e-9, tr
    m.open("PUT", 1.50, t0, center=769.0)
    tr = m.on_premium(1.20, t0)
    assert tr is not None and tr.reason == "وقف بريميوم −20%" and abs(tr.pnl + 31.40) < 1e-9, tr
    ok, _ = m.can_enter(769.0, strongest_center=769.0, now=t0)
    assert not ok  # تهدئة 60 دقيقة بعد الوقف عند المركز نفسه
    ok, _ = m.can_enter(769.0, strongest_center=769.0, now=t0 + timedelta(minutes=61))
    assert ok
    m.open("CALL", 1.00, t0, center=769.0)
    assert m.on_premium(1.05, t0, adverse=True) is None
    tr = m.on_premium(1.02, t0, adverse=True)
    assert tr is not None and tr.reason == "شمعتان معاكستان", tr
    m.open("PUT", 1.00, t0, center=769.0)
    tr = m.close_day(1.10, t0)
    assert tr is not None and tr.reason == "نهاية اليوم" and abs(tr.pnl - 8.60) < 1e-9, tr
    assert m.total_pnl == 12.40, m.total_pnl
    print(f"paper_manager smoke test OK: {len(m.trades)} trades, total_pnl={m.total_pnl}$")
