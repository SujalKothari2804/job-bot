"""
ReferJobs Subscription Manager
Runs daily checks:
  - 7 days before expiry → sends warning message to member
  - On expiry day        → removes from channel + sends resubscribe message
"""

import asyncio
from datetime import datetime
from database import (
    get_expiring_soon,
    get_expired,
    mark_warned,
    mark_removed,
    init_db,
)

# ── Messages ──────────────────────────────────────────────────────────────────

def warning_message(full_name: str, expires_at: str) -> str:
    expiry_date = datetime.fromisoformat(expires_at).strftime("%d %b %Y")
    first_name  = full_name.split()[0] if full_name else "there"
    return f"""⚠️ *Subscription Expiring Soon!*

Hey {first_name}! Your ReferJobs Premium access expires on *{expiry_date}* (7 days left).

To keep receiving:
✅ Daily curated job opportunities
✅ Remote & high-stipend roles
✅ Early access before public posting

👉 Resubscribe before it expires to avoid losing access.

📩 Contact us to renew: @ReferJobsAdmin

_Don't miss out — opportunities don't wait!_ 🚀"""


def expired_message(full_name: str) -> str:
    first_name = full_name.split()[0] if full_name else "there"
    return f"""🔒 *Your ReferJobs Premium Access Has Ended*

Hey {first_name}, your 3-month subscription has expired and you've been removed from the premium channel.

We hope you found great opportunities with us! 🙌

To rejoin and get access to:
✅ Daily curated job & internship posts
✅ Remote, hybrid & high-stipend roles
✅ Exclusive opportunities not posted publicly

👉 *Resubscribe here:* [Premium Access Form](https://forms.gle/yourformlink)

📩 Questions? Contact: @ReferJobsAdmin

_Save Time. Find Better. Apply Smarter._ 🚀"""


# ── Core functions ─────────────────────────────────────────────────────────────

async def send_warning_to_expiring(bot, channel_id: int):
    """Send 7-day warning to members expiring soon."""
    members = get_expiring_soon(days_before=7)

    if not members:
        print("[Subscription] No members expiring soon")
        return

    print(f"[Subscription] Sending warnings to {len(members)} member(s)...")

    for m in members:
        try:
            await bot.send_message(
                m["user_id"],
                warning_message(m["full_name"], m["expires_at"]),
                parse_mode="markdown"
            )
            mark_warned(m["user_id"])
            print(f"  → Warned {m['full_name']} (@{m['username']})")
            await asyncio.sleep(1)  # avoid flood limits
        except Exception as e:
            print(f"  → Could not warn {m['user_id']}: {e}")


async def remove_expired_members(bot, userbot, channel_id: int):
    """Remove expired members from channel and send resubscribe message."""
    members = get_expired()

    if not members:
        print("[Subscription] No expired members")
        return

    print(f"[Subscription] Removing {len(members)} expired member(s)...")

    for m in members:
        try:
            # 1. Send resubscribe message BEFORE removing (so they can still receive it)
            await bot.send_message(
                m["user_id"],
                expired_message(m["full_name"]),
                parse_mode="markdown"
            )
            await asyncio.sleep(1)

            # 2. Remove from channel
            await userbot.kick_participant(channel_id, m["user_id"])
            await asyncio.sleep(0.5)

            # 3. Mark as removed in DB
            mark_removed(m["user_id"])

            print(f"  → Removed {m['full_name']} (@{m['username']})")

        except Exception as e:
            print(f"  → Could not remove {m['user_id']}: {e}")


async def run_daily_check(bot, userbot, channel_id: int):
    """
    Main daily check loop.
    Runs once every 24 hours.
    Checks both warnings and expiries.
    """
    print("[Subscription] Daily check starting...")
    init_db()

    while True:
        now = datetime.now()
        print(f"\n[Subscription] Running check at {now.strftime('%d %b %Y %H:%M')}")

        await send_warning_to_expiring(bot, channel_id)
        await remove_expired_members(bot, userbot, channel_id)

        print("[Subscription] Check complete. Next check in 24 hours.")
        await asyncio.sleep(86400)  # 24 hours
