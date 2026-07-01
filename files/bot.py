"""
ReferJobs Bot — Production Ready
- Source 1: polling every 60s (handles private/restricted channels)
- Source 2: event-based with queue (handles bulk messages without drops)
- Simplified formatter: clean paraphrase, no hashtags, no promo links
- Smart skip logic: skips non-job posts
- openrouter/free as primary model (auto-selects best available)
"""

import asyncio
import os
import aiohttp
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
API_ID           = int(os.getenv("TG_API_ID"))
API_HASH         = os.getenv("TG_API_HASH")
PHONE            = os.getenv("TG_PHONE")
SOURCE_CHANNEL_1 = int(os.getenv("TG_SOURCE_1"))
SOURCE_CHANNEL_2 = int(os.getenv("TG_SOURCE_2"))
YOUR_CHANNEL     = int(os.getenv("TG_YOUR_CHANNEL"))
USERBOT_SESSION  = os.getenv("TG_USERBOT_SESSION", "")
OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY")
POLL_INTERVAL    = 60  # seconds between Source 1 polls

# openrouter/free auto-selects best available free model — never goes fully down
MODELS = [
    "openrouter/free",
    "qwen/qwen3-8b:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

# ── Telegram client ───────────────────────────────────────────────────────────
userbot = TelegramClient(StringSession(USERBOT_SESSION), API_ID, API_HASH)

# ── State ─────────────────────────────────────────────────────────────────────
last_seen_id = {"source1": 0}
message_queue = asyncio.Queue()


# ── ID normalizer (defined once, used in event handler) ──────────────────────
def normalize_id(cid: int) -> int:
    """
    Normalize Telegram channel ID for comparison.
    Strips the -100 prefix to get the raw channel ID.
    Handles both forms Telethon may return.
    """
    s = str(abs(cid))
    if s.startswith("100"):
        s = s[3:]
    return int(s)


# ── OpenRouter AI call ────────────────────────────────────────────────────────
async def call_ai(prompt: str) -> str:
    """Call OpenRouter with model fallback."""
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
                        "max_tokens": 512,
                        "temperature": 0.1,
                    }
                ) as resp:
                    data = await resp.json()
                    if "choices" in data:
                        result = data["choices"][0]["message"]["content"].strip()
                        print(f"  → Used model: {model}")
                        return result
                    err = data.get("error", {}).get("message", "unknown")
                    print(f"  → {model} failed: {err[:80]}, trying next...")
                    last_error = err
        except Exception as e:
            print(f"  → {model} exception: {e}, trying next...")
            last_error = str(e)
        await asyncio.sleep(1)
    raise RuntimeError(f"All models failed. Last: {last_error}")


# ── Should we post this? ──────────────────────────────────────────────────────
async def should_post(text: str) -> bool:
    prompt = f"""You are a content filter for ReferJobs, a Telegram job channel.

Answer POST or SKIP only. No explanation. No other words.

POST if:
- Message contains a real job opening with role, company, or apply details
- Message is a referral alert or hiring announcement for job seekers
- Message has an apply link, email, or job form link

SKIP if:
- Message asks community to share screenshots or emails
- Message asks users to DM someone for a referral contact
- Message is a conversation or reply between people
- Message is promoting another channel (join us, follow us)
- Message has no actionable job information

Message:
{text}

Answer (POST or SKIP):"""

    result = await call_ai(prompt)
    return result.strip().upper().startswith("POST")


# ── Format the job post ───────────────────────────────────────────────────────
async def format_job(text: str) -> str:
    prompt = f"""You are formatting a job post for ReferJobs Telegram channel.

OUTPUT FORMAT (follow exactly, no deviations):

🚨🔥 FREE REFERRAL ALERT / HIRING ALERT 🔥🚨

🏢 Company: [Company Name]
💼 Role: [Job Title]
📍 Location: [City or "Not mentioned"]
🎓 Batch: [Year(s) or "Open for all"]
💰 Salary/Stipend: [Amount or "Not mentioned"]

About the role:
[Write 2-3 sentences paraphrasing what the job involves, skills needed, and what the candidate will work on. Use your own words. Do not copy paste from the original.]

🔗 Apply: [email address or URL]

STRICT RULES — follow every single one without exception:
1. Output ONLY the formatted post. No thinking, no explanation, no reasoning, no preamble, no notes.
2. ONLY use information that is explicitly present in the original message. Do NOT invent, guess, or assume any detail (company name, salary, location, batch, apply link). If a field is missing, write "Not mentioned".
3. Remove ALL links or mentions of other Telegram channels, WhatsApp groups, or social media pages from the output — these are source channel promotions and must be stripped completely.
4. The apply link must ONLY be the actual job application link or email from the post. Never use a Telegram channel invite link as the apply link.
5. If multiple jobs exist in the message, format each one separately using this full format (including the 🚨🔥 header), divided by: ──────────
6. Do not add hashtags.
7. Do not add any text before or after the formatted post(s).
8. If apply link is missing, write: Not available

Job post to format:
{text}"""

    return await call_ai(prompt)


# ── Post to ReferJobs channel ─────────────────────────────────────────────────
async def post_to_channel(formatted: str):
    await userbot.send_message(YOUR_CHANNEL, formatted)
    print(f"  → ✅ Posted to ReferJobs channel")


# ── Process a single message ──────────────────────────────────────────────────
async def process_message(text: str, source: str):
    """Full pipeline: filter → format → post."""
    text = text.strip()
    if not text:
        return

    print(f"\n[{source}] New message: {text[:80]}...")

    # Step 1 — Filter
    should = await should_post(text)
    if not should:
        print(f"  → Skipped (not a job post)")
        return

    # Step 2 — Format
    print(f"  → Formatting...")
    formatted = await format_job(text)

    # Step 3 — Post
    await post_to_channel(formatted)

    # Brief delay to respect Telegram flood limits
    await asyncio.sleep(3)


# ── Queue worker ──────────────────────────────────────────────────────────────
async def queue_worker():
    """Processes messages sequentially — handles bulk without drops."""
    print("[Queue] Worker started")
    while True:
        text, source = await message_queue.get()
        try:
            await process_message(text, source)
        except Exception as e:
            print(f"  → [Error] {source}: {e}")
        finally:
            message_queue.task_done()


# ── Source 1: Polling ─────────────────────────────────────────────────────────
async def poll_source1():
    """
    Poll Source 1 every 60 seconds via get_messages().
    Works for private/restricted channels where events may not fire.
    """
    print(f"[Source1] Polling started (every {POLL_INTERVAL}s)")

    # Capture current latest ID so we only process NEW messages going forward
    try:
        messages = await userbot.get_messages(SOURCE_CHANNEL_1, limit=1)
        if messages:
            last_seen_id["source1"] = messages[0].id
            print(f"[Source1] Starting from message ID: {last_seen_id['source1']}")
    except Exception as e:
        print(f"[Source1] Could not get initial message ID: {e}")

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            messages = await userbot.get_messages(
                SOURCE_CHANNEL_1,
                limit=50,
                min_id=last_seen_id["source1"]
            )

            if not messages:
                print(f"[Source1] No new messages")
                continue

            # Process oldest → newest
            for msg in reversed(messages):
                text = msg.text or msg.caption or ""
                if text.strip():
                    await message_queue.put((text, "source1"))
                last_seen_id["source1"] = max(last_seen_id["source1"], msg.id)

            print(f"[Source1] Queued {len(messages)} new message(s)")

        except Exception as e:
            print(f"[Source1] Poll error: {e}")


# ── Source 2: Event-based ─────────────────────────────────────────────────────
@userbot.on(events.NewMessage())
async def on_new_message(event):
    """Catch-all handler — routes Source 2 messages to queue."""
    try:
        if normalize_id(event.chat_id) == normalize_id(SOURCE_CHANNEL_2):
            text = event.message.text or event.message.caption or ""
            if text.strip():
                await message_queue.put((text, "source2"))
    except Exception as e:
        print(f"[Source2] Event error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print("🚀 ReferJobs bot starting...")
    await userbot.start()
    me = await userbot.get_me()
    print(f"✅ Connected as: {me.first_name} (@{me.username})")

    # Populate entity cache so send_message(YOUR_CHANNEL) works reliably
    print("🔄 Loading dialogs...")
    await userbot.get_dialogs()
    print("✅ Dialogs loaded")

    print(f"📡 Source 1 (polling): {SOURCE_CHANNEL_1}")
    print(f"📡 Source 2 (events):  {SOURCE_CHANNEL_2}")
    print(f"📤 Posting to: {YOUR_CHANNEL}")
    print(f"🤖 Models: {', '.join(MODELS)}")
    print(f"⏳ Waiting for messages...\n")

    await asyncio.gather(
        userbot.run_until_disconnected(),
        poll_source1(),
        queue_worker(),
    )


if __name__ == "__main__":
    asyncio.run(main())
