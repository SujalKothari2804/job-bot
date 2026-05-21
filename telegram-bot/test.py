"""
ReferJobs — Function Tester
Run this to verify every part of the system works before going live.

Usage: python test.py
"""

import asyncio
import os
import sys
import smtplib
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── Colors for terminal output ────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg):  print(f"  {RED}❌ {msg}{RESET}")
def warn(msg):  print(f"  {YELLOW}⚠️  {msg}{RESET}")
def info(msg):  print(f"  {BLUE}ℹ️  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}\n" + "─"*50)


# ── Test 1: Environment Variables ─────────────────────────────────────────────
def test_env():
    header("TEST 1 — Environment Variables")

    required = {
        "TG_API_ID":        "Telegram API ID",
        "TG_API_HASH":      "Telegram API Hash",
        "TG_PHONE":         "Your phone number",
        "TG_BOT_TOKEN":     "Review bot token",
        "TG_SOURCE_CHANNEL":"Source channel",
        "TG_YOUR_CHANNEL":  "Your premium channel",
        "TG_REVIEWER_ID":   "Your Telegram user ID",
        "GEMINI_API_KEY":"Claude API key",
        "GMAIL_USER":       "Gmail address",
        "GMAIL_APP_PASSWORD":"Gmail app password",
    }

    all_good = True
    for key, label in required.items():
        val = os.getenv(key)
        if val:
            # Mask sensitive values
            masked = val[:4] + "****" + val[-2:] if len(val) > 6 else "****"
            ok(f"{label}: {masked}")
        else:
            fail(f"{label} — {key} is missing from .env!")
            all_good = False

    return all_good


# ── Test 2: Database ───────────────────────────────────────────────────────────
def test_database():
    header("TEST 2 — Database (SQLite)")

    try:
        sys.path.insert(0, os.path.dirname(__file__))
        from database import init_db, add_member, get_member, get_all_active, renew_member, mark_warned, get_expiring_soon, get_expired

        # Init
        init_db()
        ok("Database initialized")

        # Add test member
        test_id = 999999999
        add_member(test_id, "testuser", "Test User")
        ok("Add member works")

        # Get member
        m = get_member(test_id)
        if m and m["user_id"] == test_id:
            ok(f"Get member works — expires: {m['expires_at'][:10]}")
        else:
            fail("Get member failed")

        # Get all active
        active = get_all_active()
        ok(f"Get all active works — {len(active)} member(s) in DB")

        # Test expiring soon (fake it by setting expires_at to 3 days from now)
        conn = sqlite3.connect("members.db")
        soon = (datetime.now() + timedelta(days=3)).isoformat()
        conn.execute("UPDATE members SET expires_at = ? WHERE user_id = ?", (soon, test_id))
        conn.commit()
        conn.close()

        expiring = get_expiring_soon(days_before=7)
        if any(e["user_id"] == test_id for e in expiring):
            ok("Expiring soon detection works")
        else:
            warn("Expiring soon detection — check logic")

        # Test expired (fake it by setting expires_at to yesterday)
        conn = sqlite3.connect("members.db")
        past = (datetime.now() - timedelta(days=1)).isoformat()
        conn.execute("UPDATE members SET expires_at = ? WHERE user_id = ?", (past, test_id))
        conn.commit()
        conn.close()

        expired = get_expired()
        if any(e["user_id"] == test_id for e in expired):
            ok("Expired detection works")
        else:
            warn("Expired detection — check logic")

        # Renew
        renew_member(test_id)
        m2 = get_member(test_id)
        new_exp = datetime.fromisoformat(m2["expires_at"])
        if new_exp > datetime.now() + timedelta(days=80):
            ok("Renew member works")
        else:
            fail("Renew member — expiry not updated correctly")

        # Cleanup test member
        conn = sqlite3.connect("members.db")
        conn.execute("DELETE FROM members WHERE user_id = ?", (test_id,))
        conn.commit()
        conn.close()
        ok("Test member cleaned up")

        return True

    except ImportError as e:
        fail(f"Could not import database.py — {e}")
        return False
    except Exception as e:
        fail(f"Database error — {e}")
        return False


# ── Test 3: AI Formatter ──────────────────────────────────────────────────────
async def test_ai_formatter():
    header("TEST 3 — AI Formatter (Gemini Free API)")

    try:
        import google.generativeai as genai
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            fail("No GEMINI_API_KEY found")
            return False

        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = """You are a job post formatter for ReferJobs.
Convert to this format:
🚀 [Role] at [Company]
Stipend/Salary: ₹[amount]
Location: [location]
Batch: [year(s)]
Why this role stands out:
• [highlight 1]
• [highlight 2]
🔗 Apply: [link]
#tags (max 4, only: #AI #Product #Remote #Internship #PPO #HighStipend #Tech #Design #Marketing #Finance #Operations #Hybrid #Fulltime)
If not a job reply: NOT_A_JOB

Job post:
We're hiring a Product Intern at Swiggy!
Stipend: 25000/month
Location: Bangalore (Hybrid)
Batch: 2025 or 2026 passouts
Work directly with core product team on real features.
PPO based on performance.
Apply: https://swiggy.com/careers/123"""

        info("Sending test job post to Gemini...")
        response = model.generate_content(prompt)
        result   = response.text.strip()

        if result == "NOT_A_JOB":
            fail("Gemini returned NOT_A_JOB for a valid job post")
            return False

        ok("AI formatter works!")
        print(f"\n{BLUE}  Formatted output:{RESET}")
        for line in result.split("\n"):
            print(f"    {line}")

        # Test NOT_A_JOB detection
        info("\n  Testing non-job detection...")
        response2 = model.generate_content(
            "If not a job opportunity reply with exactly: NOT_A_JOB\n\nHappy Diwali everyone! 🎉"
        )
        result2 = response2.text.strip()
        if "NOT_A_JOB" in result2:
            ok("Non-job detection works")
        else:
            warn("Non-job detection may need tuning")

        return True

    except Exception as e:
        fail(f"AI formatter error — {e}")
        return False


# ── Test 4: Telegram Bot Token ────────────────────────────────────────────────
async def test_telegram_bot():
    header("TEST 4 — Telegram Bot Token")

    try:
        import aiohttp
        token = os.getenv("TG_BOT_TOKEN")
        if not token:
            fail("No TG_BOT_TOKEN found")
            return False

        url = f"https://api.telegram.org/bot{token}/getMe"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data.get("ok"):
                    bot = data["result"]
                    ok(f"Bot token valid — @{bot['username']} ({bot['first_name']})")
                    return True
                else:
                    fail(f"Bot token invalid — {data.get('description')}")
                    return False

    except Exception as e:
        fail(f"Telegram bot test error — {e}")
        return False


# ── Test 5: Telegram Invite Link Generation ───────────────────────────────────
async def test_invite_link():
    header("TEST 5 — Invite Link Generation")

    try:
        import aiohttp
        token      = os.getenv("TG_BOT_TOKEN")
        channel    = os.getenv("TG_YOUR_CHANNEL")

        if not token or not channel:
            fail("Missing TG_BOT_TOKEN or TG_YOUR_CHANNEL")
            return False

        expire_date = int((datetime.now() + timedelta(hours=24)).timestamp())
        url = f"https://api.telegram.org/bot{token}/createChatInviteLink"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "chat_id":      channel,
                "member_limit": 1,
                "expire_date":  expire_date,
                "name":         f"Test_{int(datetime.now().timestamp())}",
            }) as resp:
                data = await resp.json()
                if data.get("ok"):
                    link = data["result"]["invite_link"]
                    ok(f"Invite link generated: {link}")
                    info("This is a real link — you can delete it from channel settings")
                    return True
                else:
                    err = data.get("description", "Unknown error")
                    fail(f"Could not generate invite link — {err}")
                    if "not enough rights" in err.lower():
                        warn("Make sure bot is ADMIN of your channel with 'Invite Users' permission")
                    return False

    except Exception as e:
        fail(f"Invite link test error — {e}")
        return False


# ── Test 6: Gmail ─────────────────────────────────────────────────────────────
def test_gmail():
    header("TEST 6 — Gmail Email Sender")

    try:
        gmail_user = os.getenv("GMAIL_USER")
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD")

        if not gmail_user or not gmail_pass:
            fail("Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env")
            return False

        info(f"Connecting to Gmail as {gmail_user}...")

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            ok("Gmail login successful!")

        info("Sending test email to yourself...")
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "✅ ReferJobs Gmail Test"
        msg["From"]    = f"ReferJobs <{gmail_user}>"
        msg["To"]      = gmail_user  # send to yourself

        html = """
        <div style="font-family:Arial,sans-serif;padding:20px;">
          <h2>✅ Gmail is working!</h2>
          <p>Your ReferJobs email sender is configured correctly.</p>
          <p>Users will receive invite links at this email address.</p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, gmail_user, msg.as_string())

        ok(f"Test email sent to {gmail_user} — check your inbox!")
        return True

    except smtplib.SMTPAuthenticationError:
        fail("Gmail authentication failed!")
        warn("Steps to fix:")
        warn("1. Enable 2FA at myaccount.google.com/security")
        warn("2. Go to myaccount.google.com/apppasswords")
        warn("3. Create App Password for 'Mail'")
        warn("4. Paste 16-digit password in .env as GMAIL_APP_PASSWORD")
        return False
    except Exception as e:
        fail(f"Gmail error — {e}")
        return False


# ── Test 7: Userbot Connection ────────────────────────────────────────────────
async def test_userbot():
    header("TEST 7 — Telegram Userbot (Your Account)")

    try:
        from telethon import TelegramClient

        api_id   = os.getenv("TG_API_ID")
        api_hash = os.getenv("TG_API_HASH")
        phone    = os.getenv("TG_PHONE")

        if not all([api_id, api_hash, phone]):
            fail("Missing TG_API_ID, TG_API_HASH or TG_PHONE")
            return False

        info("Connecting userbot (your personal account)...")
        info("If first time: you'll be asked for OTP")

        client = TelegramClient("test_session", int(api_id), api_hash)
        await client.start(phone=phone)
        me = await client.get_me()
        ok(f"Userbot connected — @{me.username} ({me.first_name})")

        # Check source channel access
        source = os.getenv("TG_SOURCE_CHANNEL")
        info(f"Checking access to source channel: {source}...")
        try:
            entity = await client.get_entity(source)
            ok(f"Source channel accessible — {entity.title}")
        except Exception:
            fail(f"Cannot access source channel: {source}")
            warn("Make sure your personal account is a member of that channel")

        # Check your premium channel access
        your_channel = os.getenv("TG_YOUR_CHANNEL")
        info(f"Checking access to your channel: {your_channel}...")
        try:
            entity2 = await client.get_entity(your_channel)
            ok(f"Your premium channel accessible — {entity2.title}")
        except Exception:
            fail(f"Cannot access your channel: {your_channel}")

        await client.disconnect()

        # Clean up test session
        if os.path.exists("test_session.session"):
            os.remove("test_session.session")

        return True

    except Exception as e:
        fail(f"Userbot error — {e}")
        return False


# ── Summary ───────────────────────────────────────────────────────────────────
def print_summary(results: dict):
    header("TEST SUMMARY")
    passed = sum(1 for v in results.values() if v)
    total  = len(results)

    for test, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"  [{status}] {test}")

    print(f"\n{BOLD}  {passed}/{total} tests passed{RESET}")

    if passed == total:
        print(f"\n{GREEN}{BOLD}  🎉 All systems go! Ready to deploy.{RESET}\n")
    else:
        print(f"\n{YELLOW}{BOLD}  ⚠️  Fix failing tests before deploying.{RESET}\n")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print(f"\n{BOLD}{'='*50}")
    print("  ReferJobs System Tester")
    print(f"{'='*50}{RESET}")

    results = {}

    # Run all tests
    results["Environment Variables"] = test_env()
    results["Database"]              = test_database()
    results["AI Formatter"]          = await test_ai_formatter()
    results["Telegram Bot Token"]    = await test_telegram_bot()
    results["Invite Link Generator"] = await test_invite_link()
    results["Gmail Sender"]          = test_gmail()
    results["Userbot Connection"]    = await test_userbot()

    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
