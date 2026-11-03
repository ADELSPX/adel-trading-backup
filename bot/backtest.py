"""اختبار تاريخي v1 — محرك مراكز سيولة الصانع على SPY (أكتوبر 2026).

- المستويات: مستويات القناة الحقيقية مقروءة من شارتات فهد المنشورة (مصدرها في التعليقات).
- البيانات: شموع 5 دقائق من Yahoo (جلسة منتظمة 09:30–16:00 ET فقط).
- الدخول: إشارة المحرك عند إغلاق الشمعة، والتنفيذ عند افتتاح الشمعة التالية (قاعدة المؤشر).
- العقد: أقرب استرايك للسعر (خطوات 1$)، انتهاء الجمعة الواقعة بين 7–14 يومًا (قاعدة القناة: أسبوع–أسبوعين).
- تسعير العقد: Black-Scholes بـ IV = إغلاق VIX لليوم السابق، r=4%، توزيعات SPY ‏1.3%.
- الخروج (الأسبق يفوز): وقف بريميوم ‎−20%‎ | هدف بريميوم ‎+30%‎ (بيع كامل) |
  شمعتان معاكستان متتاليتان (قاعدة البصمات) | خروج المحرك (مركز/هدف/60د/نهاية يوم).
- التكاليف: عمولة IBKR ‏0.65$ للعقد لكل جهة + 0.10$ رسوم ≈ 1.40$ للصفقة الكاملة.
هذا نموذج تقريبي (IV ثابت خلال اليوم، بلا سبريد) — نتائجه للاتجاه العام لا للدقة المطلقة.
"""
from __future__ import annotations
import json, math, sys
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_engine import MakerCentersEngine, Bar

ET = ZoneInfo("America/New_York")
DATA = os.path.expanduser("~/workspace/backtest-data")

# مستويات SPY مقروءة من شارتات القناة: (تاريخ إنشاء الشارت -> الجلسات التي يغطيها)
LEVEL_SETS = {
    # photo_21 (5 Oct 09:39 UTC)
    "A": [774.18, 772.30, 767.13, 764.96, 762.22],
    # photo_187 (8 Oct 02:39 UTC)
    "B": [781.50, 780.00, 778.00, 776.50, 775.17, 773.94, 771.80],
    # photo_446 (10 Oct 08:00 UTC)
    "C": [781.50, 779.00, 776.62, 775.36, 773.57, 771.67],
    # photo_647 (13 Oct 09:04 UTC)
    "D": [781.00, 779.00, 777.00, 775.36, 773.57, 771.01],
    # photo_712 (14 Oct 09:19 UTC)
    "E": [781.70, 779.70, 777.40, 775.96, 773.57, 771.00],
    # photo_1505 (19 Oct 10:31 UTC)
    "F": [776.44, 773.57, 770.00, 767.10, 761.22, 757.90, 754.87],
    # photo_1893 (21 Oct 18:21 UTC — نُشر منتصف جلسة 21، يُطبق من 22 تفاديًا للنظر للأمام)
    "G": [779.00, 776.44, 774.47, 772.30, 770.28, 767.77],
    # photo_2530 (25 Oct 18:07 UTC)
    "H": [767.77, 764.78, 761.22, 758.20, 754.50, 750.95],
    # photo_3251 (30 Oct 10:36 UTC)
    "I": [776.44, 772.63, 767.77, 762.00, 759.16, 752.91],
}
STRONGEST = {"A": 764.96, "B": 775.17, "C": 773.57, "D": 773.57, "E": 773.57,
             "F": 767.10, "G": 772.30, "H": 754.50, "I": 762.00}
DAY_STRONGEST = {}
for d in (5, 6, 7): DAY_STRONGEST[date(2026, 10, d)] = STRONGEST["A"]
for d in (8, 9): DAY_STRONGEST[date(2026, 10, d)] = STRONGEST["B"]
DAY_STRONGEST[date(2026, 10, 12)] = STRONGEST["C"]
DAY_STRONGEST[date(2026, 10, 13)] = STRONGEST["D"]
for d in (14, 15, 16): DAY_STRONGEST[date(2026, 10, d)] = STRONGEST["E"]
for d in (19, 20, 21): DAY_STRONGEST[date(2026, 10, d)] = STRONGEST["F"]
for d in (22, 23): DAY_STRONGEST[date(2026, 10, d)] = STRONGEST["G"]
for d in (26, 27, 28, 29): DAY_STRONGEST[date(2026, 10, d)] = STRONGEST["H"]
DAY_STRONGEST[date(2026, 10, 30)] = STRONGEST["I"]

DAY_LEVELS = {}
for d in (5, 6, 7): DAY_LEVELS[date(2026, 10, d)] = LEVEL_SETS["A"]
for d in (8, 9): DAY_LEVELS[date(2026, 10, d)] = LEVEL_SETS["B"]
DAY_LEVELS[date(2026, 10, 12)] = LEVEL_SETS["C"]
DAY_LEVELS[date(2026, 10, 13)] = LEVEL_SETS["D"]
for d in (14, 15, 16): DAY_LEVELS[date(2026, 10, d)] = LEVEL_SETS["E"]
for d in (19, 20, 21): DAY_LEVELS[date(2026, 10, d)] = LEVEL_SETS["F"]
for d in (22, 23): DAY_LEVELS[date(2026, 10, d)] = LEVEL_SETS["G"]
for d in (26, 27, 28, 29): DAY_LEVELS[date(2026, 10, d)] = LEVEL_SETS["H"]
DAY_LEVELS[date(2026, 10, 30)] = LEVEL_SETS["I"]

STOP_PCT = 0.20
TARGET_PCT = 0.30
COMMISSION_RT = 1.40
R_FREE = 0.04
DIV_YIELD = 0.013


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot, strike, t_years, iv, is_call):
    if t_years <= 0:
        return max(0.0, (spot - strike) if is_call else (strike - spot))
    d1 = (math.log(spot / strike) + (R_FREE - DIV_YIELD + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)
    if is_call:
        return spot * math.exp(-DIV_YIELD * t_years) * norm_cdf(d1) - strike * math.exp(-R_FREE * t_years) * norm_cdf(d2)
    return strike * math.exp(-R_FREE * t_years) * norm_cdf(-d2) - spot * math.exp(-DIV_YIELD * t_years) * norm_cdf(-d1)


def load_bars(symbol):
    with open(os.path.join(DATA, f"{symbol}.json")) as f:
        d = json.load(f)
    r = d["chart"]["result"][0]
    ts = r["timestamp"]; q = r["indicators"]["quote"][0]
    days = {}
    for t, o, h, l, c in zip(ts, q["open"], q["high"], q["low"], q["close"]):
        if None in (o, h, l, c):
            continue
        dt = datetime.fromtimestamp(t, ET)
        if dt.weekday() >= 5:
            continue
        if not (time(9, 30) <= dt.time() < time(16, 0)):
            continue
        days.setdefault(dt.date(), []).append(Bar(dt.replace(tzinfo=None), o, h, l, c))
    for v in days.values():
        v.sort(key=lambda b: b.t)
    return days


def load_vix():
    try:
        with open(os.path.join(DATA, "VIXd.json")) as f:
            d = json.load(f)
        r = d["chart"]["result"][0]
        out = {}
        for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
            if c:
                out[datetime.fromtimestamp(t, ET).date()] = c
        return out
    except Exception:
        return {}


def friday_expiry(entry_day: date, lo=7, hi=14) -> datetime:
    """الجمعة الواقعة بين lo وhi يومًا بعد يوم الدخول، الساعة 16:00 ET.
    إن لم توجد جمعة داخل النافذة، أقرب جمعة بعد lo."""
    fallback = None
    for add in range(lo, lo + 15):
        cand = entry_day + timedelta(days=add)
        if cand.weekday() == 4:
            if add <= hi:
                return datetime.combine(cand, time(16, 0))
            if fallback is None:
                fallback = cand
    if fallback is not None:
        return datetime.combine(fallback, time(16, 0))
    cand = entry_day + timedelta(days=11)
    return datetime.combine(cand, time(16, 0))


def bs_delta(spot, strike, t_years, iv, is_call):
    if t_years <= 0:
        return 1.0 if (is_call and spot > strike) or (not is_call and spot < strike) else 0.0
    d1 = (math.log(spot / strike) + (R_FREE - DIV_YIELD + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    return math.exp(-DIV_YIELD * t_years) * norm_cdf(d1) if is_call else \
        -math.exp(-DIV_YIELD * t_years) * norm_cdf(-d1)


def strike_for_delta(spot, t_years, iv, is_call, target_delta):
    """أقرب استرايك (خطوات 1$) لدلتا مستهدفة موجبة القيمة."""
    best_k, best_err = round(spot), 9e9
    k = round(spot) - 45
    while k <= round(spot) + 45:
        d = abs(bs_delta(spot, k, t_years, iv, is_call))
        err = abs(d - target_delta)
        if err < best_err:
            best_err, best_k = err, k
        k += 1
    return best_k


def run(block_sideways=False, cooldown_same_center=False, strongest_only=False,
        dte_lo=7, dte_hi=14, target_delta=None, block_two_stops=False):
    days = load_bars("SPY")
    vix = load_vix()
    vix_dates = sorted(vix)
    trades = []

    for day in sorted(DAY_LEVELS):
        bars = days.get(day)
        if not bars:
            print(f"-- {day}: لا بيانات")
            continue
        stopped_centers = {}  # مركز -> وقت آخر وقف عنده (لتهدئة إعادة الدخول)
        stop_streak = {}      # مركز -> عدد الوقفات المتتالية لصفقاته (لحظر المركز بعد وقفين)
        blocked_centers = set()  # مراكز محظورة بقية الجلسة (تحسين 2026-11-02)

        def note_close(center, is_stop):
            """تحديث سلسلة الوقفات عند إغلاق صفقة (متسق مع paper_manager.py)."""
            if center is None:
                return
            if is_stop:
                stop_streak[center] = stop_streak.get(center, 0) + 1
                if stop_streak[center] >= 2:
                    blocked_centers.add(center)
            else:
                stop_streak[center] = 0
        iv = 0.18
        prev = [d for d in vix_dates if d < day]
        if prev:
            iv = vix[prev[-1]] / 100.0
        eng = MakerCentersEngine(5)
        eng.reset_day(day, DAY_LEVELS[day])
        open_trade = None  # قاموس الصفقة المفتوحة في المحاكاة
        adv_run = 0

        for i, bar in enumerate(bars):
            events = eng.on_bar(bar)
            for ev in events:
                if ev.kind in ("CALL", "PUT") and open_trade is None:
                    if i + 1 >= len(bars):
                        continue
                    if block_sideways and ev.sideways:
                        continue
                    if strongest_only and ev.center != DAY_STRONGEST.get(day):
                        continue
                    if cooldown_same_center and ev.center in stopped_centers:
                        if (bar.t - stopped_centers[ev.center]) < timedelta(minutes=60):
                            continue
                    if block_two_stops and ev.center in blocked_centers:
                        continue
                    fill = bars[i + 1]
                    spot0 = fill.o
                    is_call = ev.kind == "CALL"
                    expiry = friday_expiry(day, dte_lo, dte_hi)
                    t0 = (expiry - fill.t).total_seconds() / (365.0 * 24 * 3600)
                    strike = strike_for_delta(spot0, t0, iv, is_call, target_delta) \
                        if target_delta else round(spot0)
                    prem0 = bs_price(spot0, strike, t0, iv, is_call)
                    open_trade = {
                        "date": day, "dir": ev.kind, "is_call": is_call,
                        "entry_t": fill.t, "entry_spot": spot0,
                        "strike": strike, "expiry": expiry, "iv": iv,
                        "prem_in": prem0, "center": ev.center, "target_ctr": ev.target,
                        "fill_idx": i + 1,
                    }
                    adv_run = 0
                elif ev.kind == "EXIT" and open_trade is not None:
                    tr = open_trade
                    tt = (tr["expiry"] - bar.t).total_seconds() / (365.0 * 24 * 3600)
                    prem_out = bs_price(bar.c, tr["strike"], tt, iv, tr["is_call"])
                    close_trade(trades, tr, bar.t, bar.c, prem_out, "محرك: " + ev.reason)
                    if "لمس المركز" in ev.reason and tr.get("center") is not None:
                        stopped_centers[tr["center"]] = bar.t
                    note_close(tr.get("center"), "لمس المركز" in ev.reason)
                    open_trade = None

            if open_trade is not None and i > open_trade["fill_idx"]:
                tr = open_trade
                tt = (tr["expiry"] - bar.t).total_seconds() / (365.0 * 24 * 3600)
                is_call = tr["is_call"]
                # حدود البريميوم داخل الشمعة عبر تطرفات السعر
                worst_spot = bar.l if is_call else bar.h
                best_spot = bar.h if is_call else bar.l
                prem_worst = bs_price(worst_spot, tr["strike"], tt, iv, is_call)
                prem_best = bs_price(best_spot, tr["strike"], tt, iv, is_call)
                stop_lvl = tr["prem_in"] * (1 - STOP_PCT)
                tgt_lvl = tr["prem_in"] * (1 + TARGET_PCT)
                # شمعة معاكسة؟
                adverse = (bar.c < bar.o) if is_call else (bar.c > bar.o)
                adv_run = adv_run + 1 if adverse else 0
                if prem_worst <= stop_lvl:
                    close_trade(trades, tr, bar.t, bar.c, stop_lvl, "وقف بريميوم ‎-20%")
                    note_close(tr.get("center"), True)
                    open_trade = None
                elif prem_best >= tgt_lvl:
                    close_trade(trades, tr, bar.t, bar.c, tgt_lvl, "هدف بريميوم ‎+30%")
                    note_close(tr.get("center"), False)
                    open_trade = None
                elif adv_run >= 2:
                    prem_out = bs_price(bar.c, tr["strike"], tt, iv, is_call)
                    close_trade(trades, tr, bar.t, bar.c, prem_out, "شمعتان معاكستان")
                    note_close(tr.get("center"), False)
                    open_trade = None
            # نهاية اليوم: إغلاق قسري
            if open_trade is not None and i == len(bars) - 1:
                tr = open_trade
                tt = (tr["expiry"] - bar.t).total_seconds() / (365.0 * 24 * 3600)
                prem_out = bs_price(bar.c, tr["strike"], tt, iv, tr["is_call"])
                close_trade(trades, tr, bar.t, bar.c, prem_out, "نهاية اليوم (محاكاة)")
                note_close(tr.get("center"), False)
                open_trade = None
    return trades


def close_trade(trades, tr, exit_t, exit_spot, prem_out, reason):
    pnl = (prem_out - tr["prem_in"]) * 100 - COMMISSION_RT
    pts = (exit_spot - tr["entry_spot"]) * (1 if tr["is_call"] else -1)
    trades.append({**tr, "exit_t": exit_t, "exit_spot": exit_spot,
                   "prem_out": prem_out, "reason": reason, "pnl": pnl, "pts": pts})


def summarize(trades):
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    tot = sum(t["pnl"] for t in trades)
    cum, peak, mdd = 0.0, 0.0, 0.0
    for t in trades:
        cum += t["pnl"]; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    print(f"\n===== ملخص الاختبار v1 — SPY أكتوبر 2026 ({n} صفقة) =====")
    print(f"صافي الربح (عقد واحد/إشارة، بعد العمولة): {tot:+.2f}$")
    print(f"نسبة الفوز: {len(wins)}/{n} = {100*len(wins)/max(n,1):.0f}%")
    if wins:
        print(f"متوسط الرابحة: {sum(t['pnl'] for t in wins)/len(wins):+.2f}$ | "
              f"متوسط الخاسرة: {sum(t['pnl'] for t in trades if t['pnl']<=0)/max(n-len(wins),1):+.2f}$")
    print(f"أقصى تراجع تراكمي: {mdd:.2f}$")
    reasons = {}
    for t in trades:
        r = reasons.setdefault(t["reason"], [0, 0.0])
        r[0] += 1; r[1] += t["pnl"]
    print("بحسب سبب الخروج:")
    for k, (c, p) in sorted(reasons.items(), key=lambda x: x[1][1]):
        print(f"  {k}: {c} صفقات، {p:+.2f}$")
    dirs = {}
    for t in trades:
        r = dirs.setdefault(t["dir"], [0, 0.0])
        r[0] += 1; r[1] += t["pnl"]
    print("بحسب الاتجاه:", {k: f"{c} صفقة، {p:+.0f}$" for k, (c, p) in dirs.items()})


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--block-sideways", action="store_true")
    ap.add_argument("--block-two-stops", action="store_true",
                    help="حظر المركز بقية الجلسة بعد وقفين متتاليين عنده (تحسين 2026-11-02)")
    ap.add_argument("--cooldown", action="store_true")
    ap.add_argument("--strongest-only", action="store_true")
    ap.add_argument("--dte-lo", type=int, default=7)
    ap.add_argument("--dte-hi", type=int, default=14)
    ap.add_argument("--target-delta", type=float, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    trades = run(block_sideways=args.block_sideways, cooldown_same_center=args.cooldown,
                 strongest_only=args.strongest_only, dte_lo=args.dte_lo, dte_hi=args.dte_hi,
                 target_delta=args.target_delta, block_two_stops=args.block_two_stops)
    if args.quiet:
        n = len(trades); wins = [t for t in trades if t["pnl"] > 0]
        reasons = {}
        for t in trades:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        print(f"trades={n} net={sum(t['pnl'] for t in trades):+.2f}$ winrate={100*len(wins)/max(n,1):.0f}% reasons={reasons}")
        sys.exit(0)
    for t in trades:
        print(f"{t['date']} {t['dir']:4} دخول {t['entry_t'].strftime('%H:%M')} @{t['entry_spot']:.2f} "
              f"K={t['strike']} بريميوم {t['prem_in']:.2f}->{t['prem_out']:.2f} | {t['reason']} | {t['pnl']:+.2f}$ ({t['pts']:+.2f} نقطة)")
    summarize(trades)
    import csv
    with open(os.path.join(DATA, "trades_v1.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "dir", "entry", "exit", "entry_spot", "exit_spot", "strike", "prem_in", "prem_out", "reason", "pnl", "pts"])
        for t in trades:
            w.writerow([t["date"], t["dir"], t["entry_t"], t["exit_t"], f"{t['entry_spot']:.2f}", f"{t['exit_spot']:.2f}",
                        t["strike"], f"{t['prem_in']:.2f}", f"{t['prem_out']:.2f}", t["reason"], f"{t['pnl']:.2f}", f"{t['pts']:.2f}"])
