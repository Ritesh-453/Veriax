import smtplib
import os
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime


# ============================================================
# TELEGRAM ALERT (Free, instant, no API key purchase needed)
# ============================================================

def send_telegram_alert(asset_name, similarity, found_url=None):
    """
    Send Telegram alert via Bot API.
    Requires: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
    """
    try:
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')

        if not token or not chat_id:
            print("[Telegram] Credentials not configured — skipping")
            return False

        risk = "🔴 CRITICAL" if similarity >= 90 else "🟠 HIGH"
        found_text = f"\n🔗 Found at: {found_url[:60]}" if found_url else ""

        message = (
            f"🚨 *VERIAX — Violation Detected!*\n\n"
            f"📌 *Asset:* {asset_name}\n"
            f"📊 *Similarity:* {similarity}%\n"
            f"⚠️ *Risk Level:* {risk}\n"
            f"🕒 *Time:* {datetime.now().strftime('%d %b %Y, %H:%M')}\n"
            f"{found_text}\n\n"
            f"👉 Open VERIAX dashboard to take action."
        )

        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            },
            timeout=10
        )

        if response.status_code == 200:
            print(f"[Telegram] Alert sent for {asset_name}")
            return True
        else:
            print(f"[Telegram] Failed: {response.status_code} {response.text}")
            return False

    except Exception as e:
        print(f"[Telegram] Error: {e}")
        return False


# ============================================================
# EMAIL ALERT
# ============================================================

def send_email_alert(asset_name, similarity, found_url=None):
    try:
        sender = os.getenv('MAIL_EMAIL')
        password = os.getenv('MAIL_PASSWORD')
        receiver = os.getenv('ALERT_EMAIL')

        if not all([sender, password, receiver]):
            print("[Email] Credentials not configured — skipping")
            return False

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'🚨 VERIAX Alert: Violation Detected — {asset_name}'
        msg['From'] = sender
        msg['To'] = receiver

        risk_label = "CRITICAL" if similarity >= 90 else "HIGH"
        risk_color = "#ef4444" if similarity >= 90 else "#f97316"

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #0b0f1e; padding: 24px; border-radius: 12px 12px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 22px;">🛡️ VERIAX</h1>
                <p style="color: #6b7fa3; margin: 4px 0 0; font-size: 13px;">Digital Asset Protection</p>
            </div>

            <div style="background: #fff1f2; border: 1px solid #fecdd3; padding: 18px;">
                <h2 style="color: #ef4444; margin: 0 0 6px;">⚠️ Violation Detected</h2>
                <p style="color: #475569; margin: 0; font-size: 14px;">
                    An unauthorized use of your protected asset has been detected.
                </p>
            </div>

            <div style="background: white; border: 1px solid #e2e8f0;
                        padding: 24px; border-radius: 0 0 12px 12px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 11px 0; color: #64748b; font-weight: 600; width: 40%; font-size:14px;">Asset Name</td>
                        <td style="padding: 11px 0; color: #0f172a; font-weight: 700; font-size:14px;">{asset_name}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 11px 0; color: #64748b; font-weight: 600; font-size:14px;">Similarity Score</td>
                        <td style="padding: 11px 0;">
                            <span style="background: #fff1f2; color: #ef4444;
                                         padding: 4px 12px; border-radius: 999px;
                                         font-weight: 700; font-size:13px;">{similarity}% Match</span>
                        </td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 11px 0; color: #64748b; font-weight: 600; font-size:14px;">Risk Level</td>
                        <td style="padding: 11px 0;">
                            <span style="background: {risk_color}; color: white;
                                         padding: 4px 12px; border-radius: 999px;
                                         font-weight: 700; font-size:13px;">{risk_label}</span>
                        </td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 11px 0; color: #64748b; font-weight: 600; font-size:14px;">Detected At</td>
                        <td style="padding: 11px 0; color: #0f172a; font-size:14px;">
                            {datetime.now().strftime("%d %b %Y, %H:%M")}
                        </td>
                    </tr>
                    {"<tr><td style='padding:11px 0;color:#64748b;font-weight:600;font-size:14px;'>Found At</td><td style='padding:11px 0;'><a href='" + found_url + "' style='color:#6366f1;font-size:13px;'>" + found_url[:50] + "...</a></td></tr>" if found_url else ""}
                </table>

                <div style="margin-top: 20px; padding: 14px; background: #f8fafc;
                            border-radius: 8px; border-left: 4px solid #6366f1;">
                    <p style="margin: 0; color: #475569; font-size: 13px;">
                        <strong>Recommended Action:</strong> Review this violation
                        in your VERIAX dashboard and generate a DMCA takedown notice.
                    </p>
                </div>

                <div style="margin-top: 18px; text-align: center;">
                    <a href="http://127.0.0.1:5000/violations"
                       style="background: #6366f1; color: white; padding: 11px 22px;
                              border-radius: 8px; text-decoration: none;
                              font-weight: 600; display: inline-block; font-size:14px;">
                        View in Dashboard →
                    </a>
                </div>

                <p style="margin-top: 20px; color: #94a3b8; font-size: 11px; text-align: center;">
                    VERIAX — Digital Asset Protection System
                </p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())

        print(f"[Email] Alert sent for {asset_name}")
        return True

    except Exception as e:
        print(f"[Email] Error: {e}")
        return False


# ============================================================
# COMBINED ALERT — call this everywhere
# ============================================================

def send_violation_alert(asset_name, similarity, found_url=None):
    """
    Sends both Email + Telegram alerts.
    Safe to call even if credentials missing — just skips.
    """
    send_email_alert(asset_name, similarity, found_url)
    send_telegram_alert(asset_name, similarity, found_url)