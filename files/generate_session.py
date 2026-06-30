"""
ReferJobs — Session Generator
Run this ONCE on your PC.
Paste the output into your .env as TG_USERBOT_SESSION.
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


async def main():
    print("\n" + "="*50)
    print("ReferJobs — Session Generator")
    print("="*50)
    print("You will receive an OTP on Telegram.")
    print("This only happens ONCE.\n")

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(phone=PHONE)

    session_string = client.session.save()
    me = await client.get_me()

    print(f"\n✅ Logged in as: {me.first_name} (@{me.username})")
    print("\n" + "="*50)
    print("COPY THIS FULLY → paste as TG_USERBOT_SESSION in .env")
    print("="*50)
    print(session_string)
    print("="*50 + "\n")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
