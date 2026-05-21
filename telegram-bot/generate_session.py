"""
ReferJobs — Session String Generator
Run this ONCE on your PC to generate session strings.
Paste the output into Render environment variables.
Never run this again after that.
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv
import os

load_dotenv()

API_ID   = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
PHONE    = os.getenv("TG_PHONE")
BOT_TOKEN = os.getenv("TG_BOT_TOKEN")


async def generate_userbot_session():
    print("\n" + "="*50)
    print("STEP 1 — Generating USERBOT session")
    print("(This is YOUR personal Telegram account)")
    print("="*50)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(phone=PHONE)
    session_string = client.session.save()
    me = await client.get_me()
    print(f"\n✅ Logged in as: {me.first_name} (@{me.username})")
    print("\n" + "="*50)
    print("USERBOT SESSION STRING (copy this fully):")
    print("="*50)
    print(session_string)
    print("="*50)
    print("\n→ Save this as: TG_USERBOT_SESSION in Render environment variables\n")
    await client.disconnect()
    return session_string


async def generate_bot_session():
    print("\n" + "="*50)
    print("STEP 2 — Generating REVIEW BOT session")
    print("(This is your @ReferJobsReviewBot)")
    print("="*50)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    session_string = client.session.save()
    me = await client.get_me()
    print(f"\n✅ Bot connected: {me.first_name} (@{me.username})")
    print("\n" + "="*50)
    print("REVIEW BOT SESSION STRING (copy this fully):")
    print("="*50)
    print(session_string)
    print("="*50)
    print("\n→ Save this as: TG_BOT_SESSION in Render environment variables\n")
    await client.disconnect()
    return session_string


async def main():
    print("\n🔐 ReferJobs Session Generator")
    print("This runs ONCE. Copy the strings and paste into Render.\n")

    await generate_userbot_session()
    await generate_bot_session()

    print("\n✅ Both sessions generated!")
    print("\nNext steps:")
    print("1. Copy TG_USERBOT_SESSION string → paste in Render env vars")
    print("2. Copy TG_BOT_SESSION string → paste in Render env vars")
    print("3. Deploy to Render")
    print("4. Never need to enter OTP again ✅\n")


if __name__ == "__main__":
    asyncio.run(main())
