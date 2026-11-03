#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
لقطة سلسلة خيارات SPY من IB Gateway — قراءة فقط، لا أوامر إطلاقًا.

يجلب لانتهاءٍ واحد (الأقرب ضمن 5–14 يومًا) صفوف (strike, right, gamma,
open_interest) ويبني منها لقطة أسبوعية عبر centers.from_chain_rows ثم
يطبع أبراج هذا الأسبوع المكتشفة (لقطة واحدة لا تصنع «مراكز» — المراكز
تحتاج تكرارًا عبر الأسابيع، وهذا هو الغرض: بدء بناء اللقطات الأسبوعية
ومقارنة أبراج أول لقطة حية بما تنشره القناة).

التشغيل وقت السوق المفتوح حصرًا (الإثنين–الجمعة 17:30–00:00 الرياض):
    pip install ibapi
    python bot/chain_snapshot.py                 # عبر نفق SSH: 127.0.0.1:4002
    python bot/chain_snapshot.py --range-pct 3   # استرايكات ضمن ±3% من السعر

يخرج أيضًا CSV بنفس اسم التاريخ في مجلد اللقطات إن مررت --out.
رموز الخروج: 0 نجاح | 2 لا اتصال/لا بيانات | 3 الحساب ليس ورقيًا
"""
import argparse
import csv
import sys
import threading
import time
from datetime import datetime, timezone

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
except ImportError:
    print("مكتبة ibapi غير مثبتة — ثبّتها:  pip install ibapi")
    sys.exit(2)

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from centers import from_chain_rows, detect_towers, WeeklySnapshot  # noqa: E402

GENERIC_TICKS = "100,101,106"   # 100=حجم الخيار، 101=العقود المفتوحة، 106=IV


class ChainApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.accounts = []
        self.accounts_done = threading.Event()
        self.spot = None
        self.spot_done = threading.Event()
        self.opt_params = []
        self.params_done = threading.Event()
        self.details = {}          # (strike, right) -> {'gamma':..,'oi':..}
        self.snap_done = {}        # reqId -> Event
        self.req_contract = {}
        self.errors = []

    # ---------- callbacks ----------
    def managedAccounts(self, accountsList):
        self.accounts = [a.strip() for a in accountsList.split(",") if a.strip()]
        self.accounts_done.set()

    def securityDefinitionOptionParameter(self, reqId, exchange, underlyingConId,
                                          tradingClass, multiplier, expirations, strikes):
        self.opt_params.append((sorted(expirations), sorted(strikes)))

    def securityDefinitionOptionParameterEnd(self, reqId):
        self.params_done.set()

    def tickPrice(self, reqId, tickType, price, attrib):
        if reqId == 1 and tickType in (4, 9, 68, 75) and price and price > 0:
            self.spot = price            # LAST / CLOSE / delayed variants
            self.spot_done.set()
        c = self.req_contract.get(reqId)
        if c:
            d = self.details.setdefault(c, {})
            if tickType in (1, 66):
                d["bid"] = price
            elif tickType in (2, 67):
                d["ask"] = price
            elif tickType in (4, 68):
                d["last"] = price

    def tickOptionComputation(self, reqId, tickType, tickAttrib, impliedVol,
                              delta, optPrice, pvDividend, gamma, vega, theta, undPrice):
        c = self.req_contract.get(reqId)
        if c and gamma is not None and gamma != -2:
            self.details.setdefault(c, {})["gamma"] = gamma

    def tickSize(self, reqId, tickType, size):
        c = self.req_contract.get(reqId)
        if c:
            if tickType == 27:
                self.details.setdefault(c, {})["oi"] = size   # Call OI
            elif tickType == 28:
                self.details.setdefault(c, {})["oi"] = size   # Put OI

    def tickSnapshotEnd(self, reqId):
        ev = self.snap_done.get(reqId)
        if ev:
            ev.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2158, 2107, 2119):
            return
        self.errors.append((errorCode, errorString))
        if reqId == 1 and errorCode in (200, 354, 10090):
            self.spot_done.set()


def pick_expiry(expirations):
    """أقرب انتهاء يبعد 5–14 يومًا من اليوم (قاعدة المشروع: أسبوع–أسبوعان)."""
    today = datetime.now(timezone.utc).date()
    parsed = []
    for e in expirations:
        try:
            d = datetime.strptime(e, "%Y%m%d").date()
        except ValueError:
            continue
        parsed.append((d, e))
    parsed.sort()
    for d, e in parsed:
        if 5 <= (d - today).days <= 14:
            return e
    return parsed[0][1] if parsed else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4002)
    ap.add_argument("--client-id", type=int, default=902)
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--range-pct", type=float, default=3.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    app = ChainApp()
    try:
        app.connect(args.host, args.port, args.client_id)
    except Exception as e:
        print(f"فشل الاتصال: {e}")
        return 2
    threading.Thread(target=app.run, daemon=True).start()

    if not app.accounts_done.wait(timeout=10) or not app.accounts:
        print("لم يصل رقم الحساب — أعد المحاولة.")
        app.disconnect()
        return 2
    if not all(a.startswith("DU") for a in app.accounts):
        print(f"⛔ الحساب {app.accounts} ليس ورقيًا (لا يبدأ بـ DU) — توقف.")
        app.disconnect()
        return 3
    print(f"الحساب الورقي مؤكد: {app.accounts}")

    # سعر الأساس (SPY) — لقطة
    und = Contract()
    und.symbol = args.symbol
    und.secType = "STK"
    und.exchange = "SMART"
    und.currency = "USD"
    app.reqMktData(1, und, "", True, False, [])
    if not app.spot_done.wait(timeout=15) or not app.spot:
        print(f"تعذر قراءة سعر {args.symbol}. أخطاء: {app.errors[-3:]}")
        app.disconnect()
        return 2
    spot = app.spot
    print(f"سعر {args.symbol} المرجعي: {spot}")

    # معلمات الخيارات (الانتهاءات والاسترايكات)
    app.reqSecDefOptParams(2, args.symbol, "", "STK", 756118)  # conId احتياطي يُتجاهل إن اختلف
    if not app.params_done.wait(timeout=20) or not app.opt_params:
        print(f"لم تصل معلمات الخيارات. أخطاء: {app.errors[-3:]}")
        app.disconnect()
        return 2
    exps, strikes_all = app.opt_params[0]
    expiry = pick_expiry(exps)
    lo, hi = spot * (1 - args.range_pct / 100), spot * (1 + args.range_pct / 100)
    strikes = [s for s in strikes_all if lo <= s <= hi]
    print(f"الانتهاء المختار: {expiry} | استرايكات ضمن ±{args.range_pct}%: {len(strikes)}")

    # لقطات العقود (كول + بوت لكل استرايك) — تسلسليًا احترامًا لحدود IBKR
    rows = []
    req = 100
    for k in strikes:
        for right in ("C", "P"):
            req += 1
            c = Contract()
            c.symbol = args.symbol
            c.secType = "OPT"
            c.exchange = "SMART"
            c.currency = "USD"
            c.lastTradeDateOrContractMonth = expiry
            c.strike = k
            c.right = right
            c.multiplier = "100"
            key = (k, right)
            app.req_contract[req] = key
            ev = threading.Event()
            app.snap_done[req] = ev
            app.reqMktData(req, c, GENERIC_TICKS, True, False, [])
            ev.wait(timeout=6)
            app.cancelMktData(req)
            d = app.details.get(key, {})
            if d.get("gamma") is not None and d.get("oi") is not None:
                rows.append({"strike": k, "right": right,
                             "gamma": d["gamma"], "open_interest": d["oi"],
                             "bid": d.get("bid"), "ask": d.get("ask"), "last": d.get("last")})
            time.sleep(0.15)

    app.disconnect()
    if not rows:
        print("لم تكتمل أي صفوف (قاما+OI) — غالبًا السوق مغلق أو البيانات لم تصل بعد.")
        print(f"أخطاء أخيرة: {app.errors[-5:]}")
        return 2

    snap = from_chain_rows(rows, spot)
    towers = detect_towers(snap, 0)
    towers.sort(key=lambda t: -t.strength)
    print(f"\nاكتملت {len(rows)} صفًا. أبراج قاما هذه اللقطة (|قاما| تنازليًا):")
    for t in towers:
        side = "يمين/كول" if t.signed > 0 else "يسار/بوت"
        print(f"  استرايك {t.strike}: |قاما×OI| = {t.strength:,.0f} ({side})")
    print("\nتذكير: لقطة واحدة = أبراج فقط. «المراكز» تُبنى بتكرار البرج")
    print("في لقطات أسبوعية متتالية — قارن هذه الأبراج بما تنشره القناة اليوم.")

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["strike", "right", "gamma",
                                              "open_interest", "bid", "ask", "last"])
            w.writeheader()
            w.writerows(rows)
        print(f"حُفظ CSV في: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
