"""
ReferJobs Bot — Clean Version
- Listens to 2 Telegram source channels
- AI formats job posts (OpenRouter free)
- Smart skip logic (referral asks, promos, conversations)
- Tech / NonTech detection
- Auto posts to ReferJobs channel
- Affiliate system (commented out — enable when ready)
"""

import asyncio
import os
import re
import aiohttp
import json
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
API_ID            = int(os.getenv("TG_API_ID"))
API_HASH          = os.getenv("TG_API_HASH")
PHONE             = os.getenv("TG_PHONE")
SOURCE_CHANNEL_1  = os.getenv("TG_SOURCE_1")   # Tech only source
SOURCE_CHANNEL_2  = os.getenv("TG_SOURCE_2")   # Mixed source
YOUR_CHANNEL      = os.getenv("TG_YOUR_CHANNEL")
USERBOT_SESSION   = os.getenv("TG_USERBOT_SESSION", "")
OPENROUTER_KEY    = os.getenv("OPENROUTER_API_KEY")
# Free model fallback list (tried in order if one is rate-limited)
MODELS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
]

# ── Telegram client ───────────────────────────────────────────────────────────
userbot = TelegramClient(StringSession(USERBOT_SESSION), API_ID, API_HASH)

# ── Daily post counter for affiliate system ───────────────────────────────────
# Tracks how many posts sent today per source
# Used to decide when to send affiliate message
post_counts = {
    "source1": 0,
    "source2": 0,
}
last_reset = datetime.now().date()


def reset_daily_counts():
    """Reset post counts at midnight."""
    global last_reset
    today = datetime.now().date()
    if today != last_reset:
        post_counts["source1"] = 0
        post_counts["source2"] = 0
        last_reset = today
        print(f"[Counts] Reset daily post counts for {today}")


# ── Tech keyword detection ────────────────────────────────────────────────────
TECH_KEYWORDS = [
    "software", "developer", "engineer", "sde", "swe", "frontend", "backend",
    "fullstack", "full stack", "full-stack", "data", "ai", "ml", "machine learning",
    "artificial intelligence", "product", "devops", "cloud", "cyber", "security",
    "blockchain", "android", "ios", "mobile", "web", "ui", "ux", "design",
    "analytics", "analyst", "python", "java", "javascript", "react", "node",
    "database", "sql", "aws", "azure", "gcp", "qa", "testing", "automation",
    "tech", "technology", "it ", "information technology", "computer science",
    "deep learning", "nlp", "computer vision", "research", "intern", "sde",
    "platform", "infrastructure", "site reliability", "sre", "embedded",
    "firmware", "hardware", "chip", "semiconductor", "robotics"
]

def is_tech_job(text: str) -> bool:
    """Returns True if job is tech related (uses word-boundary matching)."""
    text_lower = text.lower()
    return any(re.search(r'\b' + re.escape(keyword.strip()) + r'\b', text_lower) for keyword in TECH_KEYWORDS)


# ── OpenRouter AI call ────────────────────────────────────────────────────────
async def call_ai(prompt: str) -> str:
    """Call OpenRouter free API with automatic model fallback on rate-limit."""
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
                        print(f"  → AI model used: {model}")
                        return data["choices"][0]["message"]["content"].strip()
                    # Rate-limited or error — try next model
                    err = data.get("error", {}).get("message", "unknown error")
                    print(f"  → Model {model} failed ({err[:60]}), trying next...")
                    last_error = err
        except Exception as e:
            print(f"  → Model {model} exception: {e}, trying next...")
            last_error = str(e)
        await asyncio.sleep(1)
    raise RuntimeError(f"All models failed. Last error: {last_error}")


# ── Step 1: Should we post this message? ─────────────────────────────────────
async def should_post(text: str) -> bool:
    """
    Ask AI if this message is a job post worth sharing.
    Returns True = post it, False = skip it.
    """
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


# ── Step 2: Format the job post ───────────────────────────────────────────────
async def format_job(text: str) -> str:
    """Format raw message into ReferJobs style."""
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

🔗 Apply: [link] (write "Not available" if missing)

Use ONLY these hashtags (max 4, at the very end of the full message):
#AI #Product #Remote #Internship #PPO #HighStipend #Tech #Design #Marketing #Finance #Operations #Hybrid #Fulltime #NonTech

Rules:
- #HighStipend only if ₹30K+/month or ₹6LPA+
- Do not add any information not present in the original message
- Keep it concise and clean
- Do not add any intro or outro text — just the formatted job post(s)

Message to format:
{text}"""

    return await call_ai(prompt)


# ── Step 3: Post to channel ───────────────────────────────────────────────────
async def post_to_channel(formatted: str):
    """Send formatted job post to ReferJobs channel."""
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
#     """
#     Send affiliate message after halfway and at end of daily posts.
#     total_today = estimated total posts for today from this source.
#     """
#     count = post_counts[source_key]
#     half  = total_today // 2
#
#     if count == half or count == total_today:
#         await userbot.send_message(YOUR_CHANNEL, AFFILIATE_MESSAGE, parse_mode="markdown")
#         print(f"  → Affiliate message sent (count={count})")


# ── Main message handler ──────────────────────────────────────────────────────
async def handle_message(event, source_key: str):
    """Process a new message from either source channel."""
    reset_daily_counts()

    raw = event.message.text or event.message.caption or ""
    raw = raw.strip()

    if not raw:
        print(f"[{source_key}] Empty message, skipping")
        return

    print(f"\n[{source_key}] New message: {raw[:80]}...")

    # Step 1 — Should we post?
    should = await should_post(raw)
    if not should:
        print(f"  → Skipped (not a job post)")
        return

    # Step 2 — Detect tech or non-tech
    tech = is_tech_job(raw)
    category = "TECH" if tech else "NONTECH"
    print(f"  → Category: {category}")

    # Step 3 — Format
    print(f"  → Formatting...")
    formatted = await format_job(raw)

    # Step 4 — Post to channel
    await post_to_channel(formatted)

    # Step 5 — Update counter
    post_counts[source_key] += 1
    print(f"  → Post count today [{source_key}]: {post_counts[source_key]}")

    # Step 6 — Affiliate system (commented out)
    # Uncomment below and set TOTAL_POSTS_ESTIMATE when you have affiliate deals
    # TOTAL_POSTS_ESTIMATE = 10  # rough estimate of posts per day per source
    # await send_affiliate_if_due(source_key, TOTAL_POSTS_ESTIMATE)

    # Small delay to avoid flood limits
    await asyncio.sleep(2)


# ── Event listeners ───────────────────────────────────────────────────────────
@userbot.on(events.NewMessage(chats=SOURCE_CHANNEL_1))
async def on_source1(event):
    await handle_message(event, "source1")


@userbot.on(events.NewMessage(chats=SOURCE_CHANNEL_2))
async def on_source2(event):
    await handle_message(event, "source2")


# ── Start ─────────────────────────────────────────────────────────────────────
async def main():
    print("🚀 ReferJobs bot starting...")
    await userbot.start()
    me = await userbot.get_me()
    print(f"✅ Connected as: @{me.username}")
    print(f"📡 Source 1: {SOURCE_CHANNEL_1}")
    print(f"📡 Source 2: {SOURCE_CHANNEL_2}")
    print(f"📤 Posting to: {YOUR_CHANNEL}")
    print(f"🤖 Model: {MODEL}")
    print(f"⏳ Waiting for messages...\n")
    await userbot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
