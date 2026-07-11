"""
ReferJobs Bot — Production Ready (all 9 fixes + branded formatting)

Fix 1  — Source 1: polling every 60s with timestamp-based tracking persisted to disk
Fix 2  — Source 2: persist last seen ID → catch-up on restart + 5-min backup poll
Fix 3  — Single shared asyncio Queue for both sources (sequential, nothing dropped)
Fix 4  — original source: line stripped before AI sees it (LINE_REMOVE_PATTERNS)
Fix 5  — AI formatter removed; regex-based cleaner only (no thinking leaks)
Fix 6  — FloodWaitError caught → exact wait → auto-retry
Fix 7  — All AI models fail → wait 60s → retry once; never silently drops
Fix 8  — State file reads wrapped in try/except; corrupt file → safe defaults + log
Fix 9  — Dedup set capped at 1000 most-recent IDs (no memory leak)
Formatting — Strip Unicode bold + emojis → inject our own emojis + header (plain Source-2 style)
"""

import asyncio
import json
import os
import re
import time
import unicodedata
import aiohttp
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
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

POLL_INTERVAL_S1      = 60    # Source 1 poll interval (seconds)
POLL_INTERVAL_S2      = 300   # Source 2 backup poll interval (seconds)
DEDUP_MAX_SIZE        = 1000  # Fix 9: cap dedup set size
STATE_FILE            = os.path.join(os.path.dirname(__file__), "state.json")

MODELS = [
    "openrouter/free",
    "qwen/qwen3-8b:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

# ── Telegram client ───────────────────────────────────────────────────────────
userbot = TelegramClient(StringSession(USERBOT_SESSION), API_ID, API_HASH)

# ── Shared queue (Fix 3) ──────────────────────────────────────────────────────
message_queue: asyncio.Queue = asyncio.Queue()

# ── Dedup set — capped at DEDUP_MAX_SIZE (Fix 9) ─────────────────────────────
# Stored as list to maintain insertion order for eviction of oldest
processed_ids: list = []
processed_ids_set: set = set()


def dedup_add(msg_id: int) -> bool:
    """
    Add msg_id to dedup tracker.
    Returns True if it's new (not seen before), False if duplicate.
    Evicts oldest when cap is reached (Fix 9).
    """
    if msg_id in processed_ids_set:
        return False
    if len(processed_ids) >= DEDUP_MAX_SIZE:
        oldest = processed_ids.pop(0)
        processed_ids_set.discard(oldest)
    processed_ids.append(msg_id)
    processed_ids_set.add(msg_id)
    return True


# ── Persistent state (Fix 1, Fix 2, Fix 8) ───────────────────────────────────
def load_state() -> dict:
    """
    Load persisted state from disk.
    Fix 8: wrap in try/except — corrupt file falls back to safe defaults.
    """
    defaults = {
        "source1_last_timestamp": 0.0,
        "source2_last_id": 0,
    }
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Validate expected keys exist
            for key in defaults:
                if key not in data:
                    data[key] = defaults[key]
            print(f"[State] Loaded: source1_ts={data['source1_last_timestamp']}, source2_id={data['source2_last_id']}")
            return data
    except Exception as e:
        print(f"[State] ⚠️  State file corrupt or unreadable ({e}). Using safe defaults.")
    return dict(defaults)


def save_state(state: dict):
    """
    Atomically write state to disk using a temp file + rename (Fix 8).
    Prevents corruption if power is lost mid-write.
    """
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[State] ⚠️  Could not save state: {e}")


# Mutable state dict shared across coroutines
state: dict = {}


# ── OpenRouter AI call (Fix 7) ────────────────────────────────────────────────
async def call_ai(prompt: str) -> str:
    """
    Call OpenRouter with model fallback.
    Fix 7: if ALL models fail → wait 60s → retry once.
    """
    timeout = aiohttp.ClientTimeout(total=30)

    async def _try_all_models() -> str | None:
        last_error = None
        for model in MODELS:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
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
        return None  # all failed

    # First attempt
    result = await _try_all_models()
    if result is not None:
        return result

    # Fix 7: all failed on first pass → wait 60s → retry once
    print("  → [AI] All models failed. Waiting 60s before retry...")
    await asyncio.sleep(60)
    result = await _try_all_models()
    if result is not None:
        return result

    # Both passes failed
    raise RuntimeError("All AI models failed on both attempts.")


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


# ── Regex-based message cleaner (Fix 4, Fix 5) ───────────────────────────────
# Patterns matched LINE BY LINE — any line matching gets dropped entirely
LINE_REMOVE_PATTERNS = [
    # ── Link patterns ─────────────────────────────────────────────────────────
    re.compile(r'https?://t\.me/\S+', re.IGNORECASE),                  # t.me links
    re.compile(r'\bt\.me/\S+', re.IGNORECASE),                          # t.me without http
    re.compile(r'https?://wa\.me/\S+', re.IGNORECASE),                  # WhatsApp wa.me
    re.compile(r'https?://chat\.whatsapp\.com/\S+', re.IGNORECASE),     # WhatsApp group invite
    re.compile(r'https?://whatsapp\.com/\S+', re.IGNORECASE),           # WhatsApp channel links
    re.compile(r'https?://whatsapp\.com$', re.IGNORECASE),              # bare whatsapp.com
    # ── Promo / CTA patterns ──────────────────────────────────────────────────
    re.compile(r'\bjoin\b.{0,60}\b(jobs?|internship|referral|update|channel|group|telegram|whatsapp)\b', re.IGNORECASE),  # "Join JobDrop for daily jobs"
    re.compile(r'join (our|the|us|this)\b', re.IGNORECASE),             # "join our channel"
    re.compile(r'follow (us|our|me)\b', re.IGNORECASE),                 # "follow us on"
    re.compile(r'subscribe (to|our|now)\b', re.IGNORECASE),             # "subscribe to"
    re.compile(r'for (more|daily|latest|free|regular)\b.{0,30}\b(jobs?|updates?|opportunities?|alerts?|referrals?)', re.IGNORECASE),  # "for daily fresher jobs"
    re.compile(r'(click|tap) (here|the link|below|above)', re.IGNORECASE),
    re.compile(r'forward (this|to your)', re.IGNORECASE),
    re.compile(r'share (this|with your|in your)', re.IGNORECASE),
    re.compile(r'powered by\b', re.IGNORECASE),
    re.compile(r'brought to you by\b', re.IGNORECASE),
    re.compile(r'get (free|daily|latest)\b.{0,30}\b(jobs?|updates?|referrals?|alerts?)', re.IGNORECASE),  # "get daily job updates"
    # ── Handle / attribution patterns ─────────────────────────────────────────
    re.compile(r'^\s*@[A-Za-z0-9_]+\s*$'),                              # line is just a @handle
    re.compile(r'(channel|group|community)\s*[:\-]\s*@[A-Za-z0-9_]+', re.IGNORECASE),
    re.compile(r'source\s*[:\-]\s*@[A-Za-z0-9_]+', re.IGNORECASE),
    re.compile(r'via\s+@[A-Za-z0-9_]+', re.IGNORECASE),
    re.compile(r'original\s+source\s*[:\-].*', re.IGNORECASE),         # Fix 4
    # ── Separator / divider lines ─────────────────────────────────────────────
    re.compile(r'^[\s━─═\-_~*=\.]{5,}$'),                              # ━━━ / --- / === etc.
]

# Inline patterns — strip the match but keep the rest of the line
INLINE_REMOVE_PATTERNS = [
    re.compile(r'https?://t\.me/\S+', re.IGNORECASE),
    re.compile(r'\bt\.me/\S+', re.IGNORECASE),
    re.compile(r'https?://wa\.me/\S+', re.IGNORECASE),
    re.compile(r'https?://chat\.whatsapp\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://whatsapp\.com/\S+', re.IGNORECASE),           # WhatsApp channel links
    re.compile(r'https?://instagram\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://twitter\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://x\.com/\S+', re.IGNORECASE),
    re.compile(r'https?://linkedin\.com/company/\S+', re.IGNORECASE),
]

# ── ReferJobs brand formatting ───────────────────────────────────────────────
OUR_HEADER = "🚨 𝐅𝐑𝐄𝐄 𝐑𝐄𝐅𝐄𝐑𝐑𝐀𝐋 𝐀𝐋𝐄𝐑𝐓 🚨"

# Matches emoji characters across all common Unicode emoji ranges
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"   # emoticons
    "\U0001F300-\U0001F5FF"   # symbols & pictographs
    "\U0001F680-\U0001F6FF"   # transport & map
    "\U0001F700-\U0001F77F"   # alchemical
    "\U0001F780-\U0001F7FF"   # geometric shapes extended
    "\U0001F800-\U0001F8FF"   # supplemental arrows-c
    "\U0001F900-\U0001F9FF"   # supplemental symbols
    "\U0001FA00-\U0001FA6F"   # chess symbols
    "\U0001FA70-\U0001FAFF"   # symbols extended-a
    "\U00002702-\U000027B0"   # dingbats
    "\U000024C2-\U0001F251"   # misc
    "\U00002300-\U000023FF"   # miscellaneous technical (⏳⏰⌛ etc.)
    "\U00002600-\U000026FF"   # miscellaneous symbols (☀️⭐ etc.)
    "]+",
    flags=re.UNICODE,
)

# Detect a non-field "alert/header" first line (safe to replace with our header)
ALERT_HEADER_PATTERN = re.compile(
    r'\b(free|new|urgent|hiring|job alert|internship alert|referral alert|'
    r'opening|opportunity|alert|announcement)\b',
    re.IGNORECASE,
)

# Detect lines that ARE job field labels (do NOT replace these as headers)
FIELD_START_PATTERN = re.compile(
    r'^(company|role|position|location|stipend|salary|apply|email|batch|eligibility)\s*[:\-]',
    re.IGNORECASE,
)

# Ordered list of (field-label regex, our emoji)
# Matched against the START of a stripped line
FIELD_EMOJI_MAP = [
    (re.compile(r'^company\s*[:\-]', re.IGNORECASE),                                                        '🏢'),
    (re.compile(r'^(role|position|title|designation|job title)\s*[:\-]', re.IGNORECASE),                    '💼'),
    (re.compile(r'^(batch|year|graduation year|passing year)\s*[:\-]', re.IGNORECASE),                      '🎓'),
    (re.compile(r'^(eligibility|qualification|education)\b', re.IGNORECASE),                                '🎓'),
    (re.compile(r'^(stipend|salary|ctc|package|compensation)\s*[:\-]', re.IGNORECASE),                      '💰'),
    (re.compile(r'^(location|place|city|work location)\s*[:\-]', re.IGNORECASE),                           '📍'),
    (re.compile(r'^(duration|internship duration|program duration)\s*[:\-]', re.IGNORECASE),                '⏳'),
    (re.compile(r'^(responsibilities|requirements|skills required|your role|what you.ll do|who can apply)\b', re.IGNORECASE), '🛠'),
    (re.compile(r'^(what you.ll work on|what you will work on|key responsibilities)\b', re.IGNORECASE),      '🚀'),
    (re.compile(r'^(key highlights?|highlights?)\s*[:\-]?', re.IGNORECASE),                                 '📌'),
    (re.compile(r'^(internship details?|job details?|offer details?|about the (role|position|internship))\b', re.IGNORECASE), '📌'),
    (re.compile(r'^(apply|how to apply|application link|apply here|apply now)\s*[:\-]?', re.IGNORECASE),    '📩'),
    (re.compile(r'^(contact|email|reach us)\s*[:\-]', re.IGNORECASE),                                      '📩'),
    (re.compile(r'^(experience|exp required|years of exp)\s*[:\-]', re.IGNORECASE),                        '⚡'),
    (re.compile(r'^(skills?)\s*[:\-]', re.IGNORECASE),                                                     '⚡'),
    (re.compile(r'^(deadline|last date|last day|apply by|closing date)\s*[:\-]', re.IGNORECASE),            '⏰'),
    (re.compile(r'^(work type|work mode|mode|type|job type|employment type)\s*[:\-]', re.IGNORECASE),       '🖥'),
    (re.compile(r'^(perks?|benefits?|what we offer)\b', re.IGNORECASE),                                     '🎁'),
]


def format_for_referjobs(text: str) -> str:
    """
    Apply ReferJobs branded formatting to a cleaned job post:
    1. Normalize Unicode math-bold/italic → plain ASCII  (fixes font inconsistency)
    2. Strip all emojis from the body
    3. Detect and replace/inject our standard header
    4. Inject our fixed emoji set on recognised field labels
    Result: plain Source-2 style text, consistently branded regardless of source.
    """
    # Step 1 — Unicode normalisation (NFKC decomposes math-bold 𝐀→A, etc.)
    text = unicodedata.normalize('NFKC', text)

    # Step 2 — Strip all emojis
    text = EMOJI_PATTERN.sub('', text)

    # Clean up any double-spaces left after emoji removal, and trim lines
    lines = [line.rstrip() for line in text.split('\n')]

    # Step 3 — Handle header: find first non-empty line
    header_injected = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_field = bool(FIELD_START_PATTERN.match(stripped))
        is_alert = bool(ALERT_HEADER_PATTERN.search(stripped))
        if is_alert and not is_field:
            # Replace their alert line with ours
            lines[i] = OUR_HEADER
        else:
            # Content starts immediately — inject our header above it
            lines.insert(i, '')
            lines.insert(i, OUR_HEADER)
        header_injected = True
        break

    if not header_injected:
        lines = [OUR_HEADER, ''] + lines

    # Step 4 — Inject our emojis on recognised field label lines
    result_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip blank lines and our header (already has its emoji)
        if not stripped or stripped == OUR_HEADER:
            result_lines.append(line)
            continue
        injected = False
        for pattern, emoji in FIELD_EMOJI_MAP:
            if pattern.match(stripped):
                result_lines.append(f"{emoji} {stripped}")
                injected = True
                break
        if not injected:
            result_lines.append(line)

    result = '\n'.join(result_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def clean_message(text: str) -> str:
    """
    Clean a job post by removing channel branding and promo content.
    Fix 5: No AI involved — pure regex. Fast and deterministic.
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


# ── Post to ReferJobs channel (Fix 6) ────────────────────────────────────────
async def post_to_channel(text: str):
    """
    Send a message to YOUR_CHANNEL.
    Fix 6: Catch FloodWaitError → sleep exact wait → retry automatically.
    """
    while True:
        try:
            await userbot.send_message(YOUR_CHANNEL, text)
            print("  → ✅ Posted to ReferJobs channel")
            return
        except FloodWaitError as e:
            print(f"  → ⏳ FloodWaitError: Telegram says wait {e.seconds}s. Waiting...")
            await asyncio.sleep(e.seconds + 5)  # +5s buffer
            # Loop back and retry
        except Exception as e:
            print(f"  → ❌ Post failed: {e}")
            raise


# ── Process a single message ──────────────────────────────────────────────────
async def process_message(text: str, source: str):
    """Full pipeline: clean → AI filter → post. No AI formatting (Fix 5)."""
    text = text.strip()
    if not text:
        return

    print(f"\n[{source}] Processing: {text[:80]}...")

    # Step 1 — Regex clean (strip branding, promo links BEFORE AI sees it)
    cleaned = clean_message(text)
    if not cleaned:
        print(f"  → Skipped (nothing left after cleaning)")
        return

    # Step 2 — AI filter on clean text only (POST or SKIP)
    # Fix 7: call_ai handles retry internally
    try:
        should = await should_post(cleaned)
    except RuntimeError as e:
        print(f"  → ❌ AI filter failed permanently: {e}. Skipping message.")
        return

    if not should:
        print(f"  → Skipped (not a job post)")
        return

    # Step 3 — Apply ReferJobs brand formatting, then post
    # (Fix 6: FloodWaitError handled inside post_to_channel)
    final = format_for_referjobs(cleaned)
    await post_to_channel(final)

    # Brief delay to respect Telegram rate limits
    await asyncio.sleep(3)


# ── Queue worker (Fix 3) ──────────────────────────────────────────────────────
async def queue_worker():
    """Processes messages strictly one at a time — handles bulk without drops."""
    print("[Queue] Worker started")
    while True:
        text, source = await message_queue.get()
        try:
            await process_message(text, source)
        except Exception as e:
            print(f"  → [Error] {source}: {e}")
        finally:
            message_queue.task_done()


# ── Source 1: Polling with persistent timestamp (Fix 1, Fix 8) ───────────────
async def poll_source1():
    """
    Poll Source 1 every 60s via get_messages().
    Fix 1: Timestamp-based tracking persisted to disk.
    Works for private/restricted channels where events don't reliably fire.
    """
    print(f"[Source1] Polling started (every {POLL_INTERVAL_S1}s)")

    # Bootstrap: if no saved timestamp, capture current time so we don't reprocess history
    if state["source1_last_timestamp"] == 0.0:
        try:
            msgs = await userbot.get_messages(SOURCE_CHANNEL_1, limit=1)
            if msgs:
                state["source1_last_timestamp"] = msgs[0].date.timestamp()
                save_state(state)
                print(f"[Source1] Bootstrapped timestamp: {msgs[0].date}")
        except Exception as e:
            print(f"[Source1] Could not bootstrap initial timestamp: {e}")

    while True:
        await asyncio.sleep(POLL_INTERVAL_S1)
        try:
            messages = await userbot.get_messages(SOURCE_CHANNEL_1, limit=50)

            if not messages:
                continue

            last_ts = state["source1_last_timestamp"]
            new_messages = [
                m for m in messages
                if m.date.timestamp() > last_ts and (m.text or m.caption or "").strip()
            ]

            if not new_messages:
                print(f"[Source1] No new messages since last poll")
                continue

            # Process oldest → newest
            for msg in sorted(new_messages, key=lambda m: m.date):
                text = msg.text or msg.caption or ""
                if dedup_add(msg.id):
                    await message_queue.put((text, "source1"))

                # Advance timestamp
                ts = msg.date.timestamp()
                if ts > state["source1_last_timestamp"]:
                    state["source1_last_timestamp"] = ts

            save_state(state)
            print(f"[Source1] Queued {len(new_messages)} new message(s)")

        except Exception as e:
            print(f"[Source1] Poll error: {e}")


# ── Source 2: Event-based + catch-up + backup poll (Fix 2, Fix 9) ────────────
@userbot.on(events.NewMessage(chats=[SOURCE_CHANNEL_2], incoming=True))
async def on_new_message_source2(event):
    """
    Source 2 primary handler — routes incoming messages to queue instantly.
    Fix 9: dedup_add() prevents double posting if backup poll catches same message.
    """
    try:
        text = event.message.text or event.message.caption or ""
        if text.strip():
            msg_id = event.message.id
            print(f"[Source2] 📥 Event received (id={msg_id}): {text[:60]}...")
            if dedup_add(msg_id):
                await message_queue.put((text, "source2"))
                # Persist new highest ID (Fix 2)
                if msg_id > state["source2_last_id"]:
                    state["source2_last_id"] = msg_id
                    save_state(state)
            else:
                print(f"[Source2] ⏭ Duplicate skipped (id={msg_id})")
        else:
            print(f"[Source2] ⏭ Skipped (media-only, id={event.message.id})")
    except Exception as e:
        print(f"[Source2] ❌ Event error (id={event.message.id}): {e}")


async def catchup_source2():
    """
    Fix 2: On startup, fetch all messages posted to Source 2 while bot was offline.
    Reads last seen ID from persistent state, fetches everything newer.
    """
    last_id = state["source2_last_id"]
    if last_id == 0:
        # First run — bootstrap from current latest so we don't reprocess history
        try:
            msgs = await userbot.get_messages(SOURCE_CHANNEL_2, limit=1)
            if msgs:
                state["source2_last_id"] = msgs[0].id
                save_state(state)
                print(f"[Source2] Bootstrapped last ID: {msgs[0].id}")
        except Exception as e:
            print(f"[Source2] Could not bootstrap last ID: {e}")
        return

    print(f"[Source2] Catching up from message ID {last_id}...")
    try:
        missed = await userbot.get_messages(SOURCE_CHANNEL_2, limit=100, min_id=last_id)
        if not missed:
            print("[Source2] No missed messages.")
            return

        count = 0
        for msg in sorted(missed, key=lambda m: m.id):
            text = msg.text or msg.caption or ""
            if text.strip() and dedup_add(msg.id):
                await message_queue.put((text, "source2-catchup"))
                count += 1
                if msg.id > state["source2_last_id"]:
                    state["source2_last_id"] = msg.id

        save_state(state)
        print(f"[Source2] Queued {count} missed message(s) from catch-up")
    except Exception as e:
        print(f"[Source2] Catch-up error: {e}")


async def backup_poll_source2():
    """
    Fix 2: Poll Source 2 every 5 minutes as safety net for rare event drops.
    Dedup ensures no double-posting if event + poll both catch same message.
    """
    print(f"[Source2-Backup] Backup polling started (every {POLL_INTERVAL_S2}s)")
    while True:
        await asyncio.sleep(POLL_INTERVAL_S2)
        try:
            last_id = state["source2_last_id"]
            messages = await userbot.get_messages(SOURCE_CHANNEL_2, limit=20, min_id=last_id)
            if not messages:
                continue

            count = 0
            for msg in sorted(messages, key=lambda m: m.id):
                text = msg.text or msg.caption or ""
                if text.strip() and dedup_add(msg.id):
                    await message_queue.put((text, "source2-backup"))
                    count += 1
                    if msg.id > state["source2_last_id"]:
                        state["source2_last_id"] = msg.id

            if count:
                save_state(state)
                print(f"[Source2-Backup] Queued {count} message(s) from backup poll")

        except Exception as e:
            print(f"[Source2-Backup] Poll error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    global state

    print("🚀 ReferJobs bot starting...")

    # Fix 8: Load persistent state with corruption safety
    state = load_state()

    await userbot.start()
    me = await userbot.get_me()
    print(f"✅ Connected as: {me.first_name} (@{me.username})")

    # Populate entity cache so send_message(YOUR_CHANNEL) works reliably
    print("🔄 Loading dialogs...")
    await userbot.get_dialogs()
    print("✅ Dialogs loaded")

    print(f"📡 Source 1 (polling every {POLL_INTERVAL_S1}s): {SOURCE_CHANNEL_1}")
    print(f"📡 Source 2 (events + backup poll every {POLL_INTERVAL_S2}s): {SOURCE_CHANNEL_2}")
    print(f"📤 Posting to: {YOUR_CHANNEL}")
    print(f"🤖 Models: {', '.join(MODELS)}")

    # Fix 2: Catch up on missed Source 2 messages before starting normal operations
    await catchup_source2()

    print("⏳ Waiting for messages...\n")

    await asyncio.gather(
        userbot.run_until_disconnected(),
        poll_source1(),
        backup_poll_source2(),
        queue_worker(),
    )


if __name__ == "__main__":
    asyncio.run(main())
