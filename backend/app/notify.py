"""Notifications: always logged in-app; email via SMTP and SMS via Twilio when configured in .env."""
import os
import smtplib
from email.message import EmailMessage

import requests
from sqlalchemy import text

from .db import engine


def _log(aoi, channel, recipient, message, status):
    with engine.begin() as con:
        con.execute(text("INSERT INTO notifications(aoi, channel, recipient, message, status) "
                         "VALUES (:a, :c, :r, :m, :s)"), dict(a=aoi, c=channel, r=recipient, m=message, s=status))


def notify(aoi: str, message: str):
    _log(aoi, "in-app", "SDMA dashboard", message, "delivered")
    to = os.getenv("ALERT_EMAIL_TO")
    if os.getenv("SMTP_HOST") and to:
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = "SURAKSHA alert", os.getenv("SMTP_USER"), to
            msg.set_content(message)
            with smtplib.SMTP_SSL(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", 465))) as s:
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
                s.send_message(msg)
            _log(aoi, "email", to, message, "sent")
        except Exception as e:  # never let a mail failure break the refresh loop
            _log(aoi, "email", to, message, f"failed: {e}")
    sid, sms_to = os.getenv("TWILIO_SID"), os.getenv("ALERT_SMS_TO")
    if sid and sms_to:
        try:
            r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", timeout=15,
                              auth=(sid, os.getenv("TWILIO_TOKEN")),
                              data={"From": os.getenv("TWILIO_FROM"), "To": sms_to, "Body": message[:300]})
            _log(aoi, "sms", sms_to, message, "sent" if r.ok else f"failed: {r.status_code}")
        except Exception as e:
            _log(aoi, "sms", sms_to, message, f"failed: {e}")
