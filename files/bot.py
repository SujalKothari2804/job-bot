"""
ReferJobs Bot — Production Ready
- Source 1: polling every 60s (handles private/restricted channels)
- Source 2: event-based with queue (handles bulk messages without drops)
- No AI formatter: posts original text, strips branding/promo via regex
- AI used ONLY for SKIP/POST filter
- openrouter/free as primary model (auto-selects best available)
"""

import asyncio
import re
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
- Message has a direct apply link, email, or job application form link

SKIP if:
- Message asks users to fill a form and they will GET a referral in return (this is not a job post, it is a referral service)
- Message asks users to DM someone to get a referral (e.g. "DM me for referral", "comment your resume")
- Message asks the community to share screenshots, resumes, or emails with the poster
- Message is promoting another Telegram channel, WhatsApp group, or social media page
- Message is a conversation, reply, or reaction between people
- Message has no actionable job information

Message:
{text}

Answer (POST or SKIP):"""

    result = await call_ai(prompt)
    return result.strip().upper().startswith("POST")


# ── Regex-based message cleaner (no AI) ──────────────────────────────────────
# Patterns matched LINE BY LINE — any line matching gets dropped entirely
LINE_REMOVE_PATTERNS = [
    re.compile(r'https?://t\.me/\S+', re.IGNORECASE),           # t.me links
    re.compile(r'\bt\.me/\S+', re.IGNORECASE),                   # t.me without http
    re.compile(r'https?://wa\.me/\S+', re.IGNORECASE),           # WhatsApp wa.me
    re.compile(r'https?://chat\.whatsapp\.com/\S+', re.IGNORECASE),  # WhatsApp groups
    re.compile(r'join (our|the|us|this)\b', re.IGNORECASE),      # "join our channel"
    re.compile(r'follow (us|our|me)\b', re.IGNORECASE),          # "follow us on"
    re.compile(r'subscribe (to|our|now)\b', re.IGNORECASE),      # "subscribe to"
    re.compile(r'for (more|daily|latest|free|regular) (jobs?|updates?|opportunities?|alerts?|referrals?)', re.IGNORECASE),
    re.compile(r'(click|tap) (here|the link|below|above)', re.IGNORECASE),
    re.compile(r'forward (this|to your)', re.IGNORECASE),
    re.compile(r'share (this|with your|in your)', re.IGNORECASE),
    re.compile(r'powered by\b', re.IGNORECASE),
    re.compile(r'brought to you by\b', re.IGNORECASE),
    re.compile(r'^\s*@[A-Za-z0-9_]+\s*$'),                       # line is just a @handle
    re.compile(r'(channel|group|community)\s*[:\-]\s*@[A-Za-z0-9_]+', re.IGNORECASE),
    re.compile(r'source\s*[:\-]\s*@[A-Za-z0-9_]+', re.IGNORECASE),
    re.compile(r'via\s+@[A-Za-z0-9_]+', re.IGNORECASE),
]

# Inline patterns — strip the match but keep the rest of the line
INLINE_REMOVE_PATTERNS = [
    re.compile(r'https?://t\.me/\S+', re.IGNORECASE),
    re.compile(r'\bt\.me/\S+', re.IGNORECASE),
    re.compile(r'https?://wa\.me/\S+', re.IGNORECASE),
    re.compile(r'https?://chat\.whatsapp\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://instagram\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://twitter\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://x\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://linkedin\.com/company/\S+', re.IGNORECASE),
]

HEADER = "ReferJobs - Find Refer Grow"


def clean_message(text: str) -> str:
    """
    Clean a job post by removing channel branding and promo content.
    No AI involved — pure regex. Fast and deterministic.
    """
    lines = text.split('\n')
    cleaned = []

    for line in lines:
        # Drop the whole line if it matches a promo pattern
        if any(p.search(line) for p in LINE_REMOVE_PATTERNS):
            continue
        # Strip inline promo links from lines that have other content too
        for p in INLINE_REMOVE_PATTERNS:
            line = p.sub('', line)
        cleaned.append(line)

    result = '\n'.join(cleaned)
    # Collapse 3+ consecutive blank lines into 2
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


# ── Post to ReferJobs channel ─────────────────────────────────────────────────
async def post_to_channel(formatted: str):
    await userbot.send_message(YOUR_CHANNEL, formatted)
    print(f"  → ✅ Posted to ReferJobs channel")


# ── Process a single message ──────────────────────────────────────────────────
async def process_message(text: str, source: str):
    """Full pipeline: filter → clean → post. No AI formatting."""
    text = text.strip()
    if not text:
        return

    print(f"\n[{source}] New message: {text[:80]}...")

    # Step 1 — AI filter (POST or SKIP)
    should = await should_post(text)
    if not should:
        print(f"  → Skipped (not a job post)")
        return

    # Step 2 — Regex clean (strip branding, promo links)
    cleaned = clean_message(text)
    if not cleaned:
        print(f"  → Skipped (nothing left after cleaning)")
        return

    # Step 3 — Add header and post
    final = f"{HEADER}\n\n{cleaned}"
    await post_to_channel(final)

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
