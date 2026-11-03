#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
فحص اتصال IB Gateway — قراءة فقط، لا يضع أي أوامر ولا يطلب بيانات سوق.

الغرض: التأكد قبل أي تشغيل أن:
  1) الـGateway تستمع على المنفذ المتوقع (الورقي 4002 افتراضيًا).
  2) الدخول بوضع Paper فعليًا: رقم الحساب الذي يعيده الاتصال يجب أن
     يبدأ بـ DU (حسابات IBKR الورقية). أي حساب آخر = توقف فوري.

التشغيل على نفس جهاز الـGateway (A6):
    pip install ibapi
    python bot/connection_check.py            # ورقي 4002
    python bot/connection_check.py --port 4001  # (للفحص فقط إن لزم)

رموز الخروج: 0 = ورقي مؤكد | 2 = لا اتصال | 3 = الحساب ليس ورقيًا
"""
import argparse
import sys
import threading

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
except ImportError:
    print("مكتبة ibapi غير مثبتة — ثبّتها:  pip install ibapi")
    sys.exit(2)

PAPER_PREFIX = "DU"          # معرف الحساب الورقي الموثق للمشروع يبدأ بـ DU
EXPECTED_PAPER_PORT = 4002


class CheckApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.accounts = []
        self.done = threading.Event()

    def managedAccounts(self, accountsList):
        self.accounts = [a.strip() for a in accountsList.split(",") if a.strip()]
        self.done.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # 2104/2106/2158 رسائل حالة مزارع البيانات — ليست أخطاء اتصال
        if errorCode in (2104, 2106, 2158):
            return
        print(f"[gateway] code={errorCode} {errorString}")
        if errorCode in (502, 504, 1100):
            self.done.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=EXPECTED_PAPER_PORT)
    ap.add_argument("--client-id", type=int, default=901)
    args = ap.parse_args()

    app = CheckApp()
    print(f"الاتصال بـ {args.host}:{args.port} ...")
    try:
        app.connect(args.host, args.port, args.client_id)
    except Exception as e:
        print(f"فشل إنشاء الاتصال: {e}")
        return 2
    t = threading.Thread(target=app.run, daemon=True)
    t.start()

    if not app.done.wait(timeout=10):
        print("انتهت المهلة (10 ثوانٍ) بلا رد — تحقق أن الـGateway شغالة")
        print("وأن خيار Enable ActiveX and Socket Clients مفعّل في إعدادات API.")
        app.disconnect()
        return 2

    if not app.accounts:
        print("اتصلنا لكن لم يصل أي رقم حساب — أعد المحاولة بعد ثوانٍ.")
        app.disconnect()
        return 2

    print(f"الحسابات المرتبطة بهذا الدخول: {app.accounts}")
    app.disconnect()

    if all(a.startswith(PAPER_PREFIX) for a in app.accounts):
        print("✅ مؤكد: الدخول بوضع PAPER — آمن لمتابعة اختبارات الربط.")
        return 0
    print("⛔ توقف: الحساب لا يبدأ بـ DU — هذا ليس دخولًا ورقيًا.")
    print("لا تتابع أي خطوة ربط قبل إعادة الدخول بوضع Paper.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
