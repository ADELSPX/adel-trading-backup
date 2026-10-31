"""حاسبة مراكز سيولة الصانع من قاما الخيارات — منهجية قناة GAMMA FAHAD VIP.

المنهجية كما وثقناها من الحلقات والبصمات (STRATEGY.md):
- قراءة قاما أسبوعية (افتتاح الاثنين) لكل استرايك.
- «برج القاما»: قمة بارزة في ملف القاما عند استرايك خلال أسبوع.
- «مركز القاما»: برج يتكرر بنفس الاسترايك عبر الأسابيع — نافذة شهر متدحرج (~4 قراءات).
- «مركز سيولة الصانع»: أقوى مركز (ننشر السلم كاملًا كما تفعل القناة ونعلّم الأقوى بنجمة).
- أفق الصلاحية أسبوعان؛ المركز لا يزاح إلا إذا ظهر برج جارٍّ أقوى وأكثر استمرارية.
- انحياز القاما الأسبوعي (أبراج يمين/يسار) يستخدم لترجيح الدخول بين المراكز فقط.

هذه الحاسبة تعمل على لقطات أسبوعية جاهزة (gamma بحسب الاسترايك)، وتوجد دالة
from_chain_rows لبناء لقطة من صفوف سلسلة خيارات (قاما × عقود مفتوحة) عند توفر بيانات IBKR.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

WINDOW_WEEKS = 4          # شهر متدحرج: آخر 4 قراءات أسبوعية
MIN_REPEATS = 2           # برج شوهد في أسبوعين على الأقل داخل النافذة = مركز
PROMINENCE_FRAC = 0.10    # بروز القمة الأدنى نسبةً لأعلى |قاما| في الأسبوع
CLUSTER_TOL_PCT = 0.25    # تسامح تجميع نفس المركز عبر الأسابيع (% من السعر)


@dataclass
class Tower:
    strike: float
    strength: float       # |gamma| عند القمة
    signed: float         # gamma الموقعة (يمين/موجب أو يسار/سالب)
    week: int             # فهرس الأسبوع داخل النافذة (0 = الأقدم)


@dataclass
class Center:
    price: float                  # متوسط الاسترايكات المرجح بالقوة
    score: float                  # متوسط القوة الأسبوعية × عدد التكرارات
    repeats: int
    side_balance: float           # ‎+‎ يغلب عليه الموجب / ‎-‎ يغلب عليه السالب


@dataclass
class WeeklySnapshot:
    gamma_by_strike: dict         # strike -> net gamma (موقعة)
    spot: float


def detect_towers(snap: WeeklySnapshot, week_idx: int) -> list[Tower]:
    """قمم |القاما| المحلية ذات البروز الكافي في لقطة أسبوع واحد."""
    items = sorted(snap.gamma_by_strike.items())
    if len(items) < 3:
        return []
    strikes = [k for k, _ in items]
    vals = [abs(v) for _, v in items]
    peak = max(vals) if vals else 0.0
    floor = peak * PROMINENCE_FRAC
    towers = []
    for i in range(len(items)):
        if vals[i] < floor:
            continue
        left = vals[i - 1] if i > 0 else -1
        right = vals[i + 1] if i < len(items) - 1 else -1
        if vals[i] >= left and vals[i] >= right and (vals[i] > left or vals[i] > right):
            towers.append(Tower(strikes[i], vals[i], items[i][1], week_idx))
    return towers


def compute_centers(snapshots: list[WeeklySnapshot]) -> tuple[list[Center], Optional[Center], str]:
    """من آخر WINDOW_WEEKS لقطات -> (سلم المراكز تنازليًا, الأقوى, انحياز القاما).

    الانحياز: مقارنة مجموع قوى الأبراج الموجبة بالسالبة في أحدث لقطة:
    'CALL' يمين أثقل | 'PUT' يسار أثقل | 'NEUTRAL'.
    """
    snaps = snapshots[-WINDOW_WEEKS:]
    if not snaps:
        return [], None, "NEUTRAL"
    spot = snaps[-1].spot
    tol = max(spot * CLUSTER_TOL_PCT / 100.0, 1e-9)

    all_towers: list[Tower] = []
    for wi, snap in enumerate(snaps):
        all_towers.extend(detect_towers(snap, wi))

    # تجميع الأبراج المتقاربة عبر الأسابيع
    clusters: list[list[Tower]] = []
    for tw in sorted(all_towers, key=lambda t: t.strike):
        placed = False
        for cl in clusters:
            ref = sum(t.strike * t.strength for t in cl) / max(sum(t.strength for t in cl), 1e-9)
            if abs(tw.strike - ref) <= tol:
                cl.append(tw)
                placed = True
                break
        if not placed:
            clusters.append([tw])

    centers: list[Center] = []
    for cl in clusters:
        weeks_present = len({t.week for t in cl})
        if weeks_present < MIN_REPEATS:
            continue
        wsum = sum(t.strength for t in cl)
        price = sum(t.strike * t.strength for t in cl) / wsum
        mean_strength = wsum / len(snaps)
        score = mean_strength * weeks_present
        side = sum(t.signed for t in cl) / max(wsum, 1e-9)
        centers.append(Center(round(price, 2), score, weeks_present, side))

    centers.sort(key=lambda c: -c.price)
    strongest = max(centers, key=lambda c: c.score) if centers else None

    latest_towers = detect_towers(snaps[-1], len(snaps) - 1)
    pos = sum(t.strength for t in latest_towers if t.signed > 0)
    neg = sum(t.strength for t in latest_towers if t.signed < 0)
    bias = "NEUTRAL"
    if pos + neg > 0:
        if pos > neg * 1.5:
            bias = "CALL"
        elif neg > pos * 1.5:
            bias = "PUT"
    return centers, strongest, bias


def format_levels_line(symbol: str, centers: list[Center], strongest: Optional[Center]) -> str:
    """صيغة سطر المؤشر: 'SPY: 776.44, 773.57, 767.10*, ...' تنازليًا، الأقوى بنجمة."""
    parts = []
    for c in centers:
        s = f"{c.price:.2f}".rstrip("0").rstrip(".") if c.price % 1 else f"{c.price:.0f}"
        if strongest is not None and c.price == strongest.price:
            s += "*"
        parts.append(s)
    return f"{symbol}: " + ", ".join(parts)


def from_chain_rows(rows, spot: float) -> WeeklySnapshot:
    """بناء لقطة أسبوعية من صفوف سلسلة خيارات.

    rows: قواميس فيها strike, right ('C'/'P'), gamma, open_interest.
    الاتفاقية: قاما الكول موجبة وقاما البوت سالبة (اتفاقية تعرّض الصانع الشائعة في أدوات GEX)،
    والقيمة = gamma × OI × 100 لكل استرايك.
    """
    g: dict[float, float] = {}
    for r in rows:
        v = float(r["gamma"]) * float(r["open_interest"]) * 100.0
        if str(r["right"]).upper().startswith("P"):
            v = -v
        k = float(r["strike"])
        g[k] = g.get(k, 0.0) + v
    return WeeklySnapshot(gamma_by_strike=g, spot=spot)
