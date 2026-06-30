"""
ReferJobs — Quick Tester
Tests all connections before going live.
Run: python test.py
"""

import asyncio
import os
import re
import aiohttp
from dotenv import load_dotenv

load_dotenv()

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):     print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg):   print(f"  {RED}❌ {msg}{RESET}")
def warn(msg):   print(f"  {YELLOW}⚠️  {msg}{RESET}")
def info(msg):   print(f"  {BLUE}ℹ️  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}\n" + "─"*50)


# ── Test 1: Environment Variables ─────────────────────────────────────────────
def test_env():
    header("TEST 1 — Environment Variables")
    required = {
        "TG_API_ID":          "Telegram API ID",
        "TG_API_HASH":        "Telegram API Hash",
        "TG_PHONE":           "Phone number",
        "TG_SOURCE_1":        "Source channel 1",
        "TG_SOURCE_2":        "Source channel 2",
        "TG_YOUR_CHANNEL":    "Your ReferJobs channel",
        "TG_USERBOT_SESSION": "Userbot session string",
        "OPENROUTER_API_KEY": "OpenRouter API key",
    }
    all_good = True
    for key, label in required.items():
        val = os.getenv(key)
        if val:
            masked = val[:6] + "****" if len(val) > 6 else "****"
            ok(f"{label}: {masked}")
        else:
            fail(f"{label} — {key} missing from .env!")
            all_good = False
    return all_good


# ── Test 2: OpenRouter AI ─────────────────────────────────────────────────────
async def test_ai():
    header("TEST 2 — OpenRouter AI (Free)")

    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        fail("No OPENROUTER_API_KEY found")
        return False

    # Try models in order until one works
    MODELS = [
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3-nano-30b-a3b:free",
    ]

    async def ai_call(session, prompt, max_tokens=10):
        """Try each model until one responds."""
        for model in MODELS:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://referjobs.in",
                    "X-Title": "ReferJobs",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                }
            ) as resp:
                data = await resp.json()
                if "choices" in data:
                    return model, data["choices"][0]["message"]["content"].strip()
                await asyncio.sleep(1)
        return None, None

    # Test 1 — should post detection
    info("Testing skip logic...")
    skip_prompt = """You are a filter for ReferJobs channel.
Reply with exactly: POST or SKIP

Message: "Those who got mail from salesforce please share screenshot at @Developer_coder1"
"""

    post_prompt = """You are a filter for ReferJobs channel.
Reply with exactly: POST or SKIP

Message: "🚨 Referral Alert 🚨 Google is hiring SDE Intern | Stipend: ₹80,000/month | Apply: careers.google.com/jobs/123"
"""

    try:
        async with aiohttp.ClientSession() as session:
            # Test skip
            model, result = await ai_call(session, skip_prompt)
            if result is None:
                fail("All models failed for skip test")
                return False
            result = result.strip().upper()
            if "SKIP" in result:
                ok(f"Skip logic works — correctly skipped non-job message [{model.split('/')[1]}]")
            else:
                warn(f"Skip logic returned: {result} (expected SKIP)")

            await asyncio.sleep(1)

            # Test post
            model, result = await ai_call(session, post_prompt)
            if result is None:
                fail("All models failed for post test")
                return False
            result = result.strip().upper()
            if "POST" in result:
                ok(f"Post logic works — correctly identified job post [{model.split('/')[1]}]")
            else:
                warn(f"Post logic returned: {result} (expected POST)")

        # Test formatting
        info("Testing job formatter...")
        format_prompt = """Format this job post for ReferJobs channel:

🚨 Referral Alert 🚨
Company: Swiggy
Role: Product Intern
Stipend: ₹25,000/month
Location: Bangalore (Hybrid)
Batch: 2025/2026
PPO opportunity available
Apply: https://swiggy.com/careers/123

Format:
🚀 [Role] at [Company]
Stipend/Salary: ₹[amount]
Location: [location]
Batch: [year]
Why this role stands out:
• point 1
• point 2
🔗 Apply: [link]
#tags"""

        async with aiohttp.ClientSession() as session:
            model, result = await ai_call(session, format_prompt, max_tokens=512)
            if result is None:
                fail("All models failed for formatter test")
                return False
            ok(f"Formatter works! [{model.split('/')[1]}]")
            print(f"\n{BLUE}  Sample output:{RESET}")
            for line in result.split("\n"):
                print(f"    {line}")

        return True

    except Exception as e:
        fail(f"OpenRouter error — {e}")
        return False



# ── Test 3: Telegram Userbot ──────────────────────────────────────────────────
async def test_telegram():
    header("TEST 3 — Telegram Userbot")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_id      = os.getenv("TG_API_ID")
        api_hash    = os.getenv("TG_API_HASH")
        session_str = os.getenv("TG_USERBOT_SESSION")

        if not session_str:
            warn("No session string — run generate_session.py first")
            return False

        client = TelegramClient(StringSession(session_str), int(api_id), api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            fail("Session string invalid or expired")
            return False

        me = await client.get_me()
        ok(f"Connected as: {me.first_name} (@{me.username})")

        # Check source channels
        for key, label in [("TG_SOURCE_1", "Source 1"), ("TG_SOURCE_2", "Source 2"), ("TG_YOUR_CHANNEL", "Your channel")]:
            ch = os.getenv(key)
            if ch:
                try:
                    entity = await client.get_entity(int(ch))
                    ok(f"{label} accessible: {entity.title}")
                except Exception as e:
                    fail(f"{label} not accessible: {e}")

        await client.disconnect()
        return True

    except Exception as e:
        fail(f"Telegram error — {e}")
        return False


# ── Tech Detection Test ───────────────────────────────────────────────────────
def test_tech_detection():
    header("TEST 4 — Tech/NonTech Detection")

    TECH_KEYWORDS = [
        "software", "developer", "engineer", "sde", "swe", "frontend", "backend",
        "fullstack", "data", "ai", "ml", "machine learning", "product", "devops",
        "cloud", "cyber", "android", "ios", "mobile", "web", "ui", "ux", "design",
        "analytics", "analyst", "python", "java", "javascript", "react", "tech"
    ]

    def is_tech(text):
        return any(re.search(r'\b' + re.escape(k.strip()) + r'\b', text.lower()) for k in TECH_KEYWORDS)

    tests = [
        ("SDE Intern at Google | Python | Remote", True),
        ("Marketing Manager at Zomato | Mumbai", False),
        ("Data Analyst at Flipkart | Bangalore", True),
        ("HR Executive at Startup | Delhi", False),
        ("AI Research Intern at Microsoft", True),
        ("Sales Associate at Amazon | Any Batch", False),
    ]

    all_good = True
    for text, expected in tests:
        result = is_tech(text)
        label  = "TECH" if result else "NONTECH"
        exp_label = "TECH" if expected else "NONTECH"
        if result == expected:
            ok(f"{label} — {text[:50]}")
        else:
            fail(f"Got {label}, expected {exp_label} — {text[:50]}")
            all_good = False

    return all_good


# ── Summary ───────────────────────────────────────────────────────────────────
def print_summary(results):
    header("SUMMARY")
    passed = sum(1 for v in results.values() if v)
    total  = len(results)

    for test, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"  [{status}] {test}")

    print(f"\n{BOLD}  {passed}/{total} tests passed{RESET}")
    if passed == total:
        print(f"\n{GREEN}{BOLD}  🎉 All good! Ready to deploy.{RESET}\n")
    else:
        print(f"\n{YELLOW}{BOLD}  ⚠️  Fix failing tests before deploying.{RESET}\n")


async def main():
    print(f"\n{BOLD}{'='*50}")
    print("  ReferJobs System Tester")
    print(f"{'='*50}{RESET}")

    results = {}
    results["Environment Variables"] = test_env()
    results["Tech Detection"]        = test_tech_detection()
    results["OpenRouter AI"]         = await test_ai()
    results["Telegram Userbot"]      = await test_telegram()

    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
