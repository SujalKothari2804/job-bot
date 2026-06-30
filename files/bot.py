"""
ReferJobs Bot — Clean Version
- Listens to 2 Telegram source channels
- AI formats job posts (OpenRouter free)
- Smart skip logic (referral asks, promos, conversations)
- Tech / NonTech detection (AI-based, not fragile keyword matching)
- Auto posts to ReferJobs channel
- Affiliate system (commented out — enable when ready)
"""

import asyncio
import os
import aiohttp
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
API_ID            = int(os.getenv("TG_API_ID"))
API_HASH          = os.getenv("TG_API_HASH")
PHONE             = os.getenv("TG_PHONE")
SOURCE_CHANNEL_1  = int(os.getenv("TG_SOURCE_1"))
SOURCE_CHANNEL_2  = int(os.getenv("TG_SOURCE_2"))
YOUR_CHANNEL      = int(os.getenv("TG_YOUR_CHANNEL"))
USERBOT_SESSION   = os.getenv("TG_USERBOT_SESSION", "")
OPENROUTER_KEY    = os.getenv("OPENROUTER_API_KEY")

MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
]

userbot = TelegramClient(StringSession(USERBOT_SESSION), API_ID, API_HASH)

post_counts = {"source1": 0, "source2": 0}
last_reset = datetime.now().date()


def reset_daily_counts():
    global last_reset
    today = datetime.now().date()
    if today != last_reset:
        post_counts["source1"] = 0
        post_counts["source2"] = 0
        last_reset = today
        print(f"[Counts] Reset daily post counts for {today}")


async def call_ai(prompt: str) -> str:
    last_error = None
    for model in MODELS:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://referjobs.in",
                        "X-Title": "ReferJobs",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1024,
                    }
                ) as resp:
                    data = await resp.json()
                    if "choices" in data:
                        return data["choices"][0]["message"]["content"].strip()
                    err = data.get("error", {}).get("message", "unknown error")
                    print(f"  → Model {model} failed ({err[:60]}), trying next...")
                    last_error = err
        except Exception as e:
            print(f"  → Model {model} exception: {e}, trying next...")
            last_error = str(e)
        await asyncio.sleep(1)
    raise RuntimeError(f"All models failed. Last error: {last_error}")


async def should_post(text: str) -> bool:
    prompt = f"""You are a filter for a Telegram job channel called ReferJobs.

Decide if the message below should be POSTED or SKIPPED.

POST if the message:
- Contains a job opportunity, opening, or referral for job seekers to apply
- Has apply links, job forms, role details, company names
- Starts with patterns like 🚨 Referral Alert, 🚨 Hiring, etc.
- Is giving job seekers something to act on

SKIP if the message:
- Is asking the community to share screenshots, emails, or DM someone
- Is asking users to contact a person for referral (e.g. "DM @username for referral")
- Is a conversation, reply, or reaction to another message
- Is a channel promo or announcement unrelated to job applications
- Is asking people to fill a form for the channel owner's benefit (not for applying to a job)

Note: A message CAN have a form link and still be POSTED — if that form is for applying to a job.
Note: "Fill form to apply" = POST. "Fill form and share with us" = SKIP.

Reply with exactly one word: POST or SKIP

Message:
{text}"""
    result = await call_ai(prompt)
    return result.strip().upper().startswith("POST")


async def classify_category(text: str) -> str:
    """
    AI-based classification — avoids the old bug where keyword matching
    flagged "Mumbai" as TECH because it contains the substring "ai".
    """
    prompt = f"""Classify this job posting as either TECH or NONTECH.

TECH means the ROLE itself is a technology/engineering/data/IT role —
examples: Software Engineer, Data Analyst, AI/ML Engineer, DevOps,
Product Manager (tech product), UI/UX Designer, QA/Testing, Mobile Developer,
Cybersecurity, Cloud Engineer.

NONTECH means the role is non-technical — examples: Marketing, Sales,
HR, Finance, Operations, Content Writing, Business Development,
Customer Support, Legal, Consulting, Admin.

IMPORTANT: Base your decision ONLY on the job ROLE/TITLE and core
responsibilities. Do NOT classify based on incidental words like city
names (e.g. "Mumbai" contains "ai" but is NOT related to AI), company
names, or unrelated text in the message.

Reply with exactly one word: TECH or NONTECH

Job posting:
{text}"""
    result = await call_ai(prompt)
    result = result.strip().upper()
    return "TECH" if "NONTECH" not in result and "TECH" in result else "NONTECH"


async def format_job(text: str) -> str:
    prompt = f"""You are a job post formatter for ReferJobs, a free Telegram job channel.

Convert the message below into ReferJobs format.

If the message contains MULTIPLE job openings, format ALL of them together in one message.
Separate each job with a blank line and a divider ──────────

Format for each job:
🚀 [Role] at [Company]

Stipend/Salary: ₹[amount] (write "Not mentioned" if missing)
Location: [location] (write "Not mentioned" if missing)
Batch: [year(s)] (write "Any" if not mentioned)

Why this role stands out:
• [highlight 1]
• [highlight 2]
• [highlight 3 if available]

🔗 Apply: [link or email] (write "Not available" if missing)

Use ONLY these hashtags (max 4, at the very end of the full message):
#AI #Product #Remote #Internship #PPO #HighStipend #Tech #Design #Marketing #Finance #Operations #Hybrid #Fulltime #NonTech

Rules:
- #HighStipend only if ₹30K+/month or ₹6LPA+
- Do not add any information not present in the original message
- Keep it concise and clean
- Do not add any intro or outro text — just the formatted job post(s)
- IGNORE and DO NOT include any self-promotion from the source channel such as:
  "Join our channel", "Follow us on Telegram/WhatsApp", channel invite links,
  "for daily updates join...", or any links to the source channel itself
- Apply contact can be an email address OR a link — both are valid, include whichever is present
- Only include information directly related to the job opportunity itself

Message to format:
{text}"""
    return await call_ai(prompt)


async def post_to_channel(formatted: str):
    await userbot.send_message(YOUR_CHANNEL, formatted)
    print(f"  → Posted to channel ✅")


# ── Affiliate message sender (COMMENTED OUT — enable when ready) ──────────────
# AFFILIATE_MESSAGE = """
# 🤝 *Sponsored Resource*
#
# Struggling to get shortlisted? Your resume might be the issue.
# Try [Tool Name] — trusted by 10,000+ job seekers.
#
# 🔗 Check it out: [link here]
#
# #CareerTip #Resource
# """
#
# async def send_affiliate_if_due(source_key: str, total_today: int):
#     count = post_counts[source_key]
#     half  = total_today // 2
#     if count == half or count == total_today:
#         await userbot.send_message(YOUR_CHANNEL, AFFILIATE_MESSAGE, parse_mode="markdown")
#         print(f"  → Affiliate message sent (count={count})")


async def handle_message(event, source_key: str):
    reset_daily_counts()

    raw = event.message.text or event.message.caption or ""
    raw = raw.strip()

    if not raw:
        print(f"[{source_key}] Empty message, skipping")
        return

    print(f"\n[{source_key}] New message: {raw[:80]}...")

    should = await should_post(raw)
    if not should:
        print(f"  → Skipped (not a job post)")
        return

    category = await classify_category(raw)
    print(f"  → Category: {category}")

    print(f"  → Formatting...")
    formatted = await format_job(raw)

    await post_to_channel(formatted)

    post_counts[source_key] += 1
    print(f"  → Post count today [{source_key}]: {post_counts[source_key]}")

    # await send_affiliate_if_due(source_key, TOTAL_POSTS_ESTIMATE)

    await asyncio.sleep(2)


@userbot.on(events.NewMessage(chats=SOURCE_CHANNEL_1))
async def on_source1(event):
    await handle_message(event, "source1")


@userbot.on(events.NewMessage(chats=SOURCE_CHANNEL_2))
async def on_source2(event):
    await handle_message(event, "source2")


async def main():
    print("🚀 ReferJobs bot starting...")
    await userbot.start()
    me = await userbot.get_me()
    print(f"✅ Connected as: {me.first_name} (@{me.username})")

    # Pre-resolve entities so Telethon caches them before event handlers fire
    try:
        await userbot.get_entity(SOURCE_CHANNEL_1)
        print(f"📡 Source 1 resolved: {SOURCE_CHANNEL_1}")
    except Exception as e:
        print(f"⚠️  Could not resolve Source 1 ({SOURCE_CHANNEL_1}): {e}")

    try:
        await userbot.get_entity(SOURCE_CHANNEL_2)
        print(f"📡 Source 2 resolved: {SOURCE_CHANNEL_2}")
    except Exception as e:
        print(f"⚠️  Could not resolve Source 2 ({SOURCE_CHANNEL_2}): {e}")

    try:
        await userbot.get_entity(YOUR_CHANNEL)
        print(f"📤 Destination resolved: {YOUR_CHANNEL}")
    except Exception as e:
        print(f"⚠️  Could not resolve destination ({YOUR_CHANNEL}): {e}")

    print(f"🤖 Models (fallback order): {', '.join(MODELS)}")
    print(f"⏳ Waiting for messages...\n")
    await userbot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
