"""
ReferJobs — Telegram Review Bot (Full Version)
Handles both:
  1. Direct Telegram source channel messages (via userbot)
  2. WhatsApp posts queued via shared queue server (polls every 10s)
  3. Member subscription tracking (3 month expiry, warnings, auto-remove)
"""

import asyncio
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonRow, KeyboardButtonCallback
import google.generativeai as genai
from dotenv import load_dotenv
from database import init_db, add_member, get_member, renew_member, get_all_active
from subscription import run_daily_check

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
API_ID           = int(os.getenv("TG_API_ID"))
API_HASH         = os.getenv("TG_API_HASH")
PHONE            = os.getenv("TG_PHONE")
BOT_TOKEN        = os.getenv("TG_BOT_TOKEN")
SOURCE_CHANNEL   = os.getenv("TG_SOURCE_CHANNEL")
YOUR_CHANNEL     = os.getenv("TG_YOUR_CHANNEL")
REVIEWER_CHAT_ID = int(os.getenv("TG_REVIEWER_ID"))
GEMINI_KEY        = os.getenv("GEMINI_API_KEY")
GMAIL_USER        = os.getenv("GMAIL_USER", "referjobsco@gmail.com")
GMAIL_PASSWORD    = os.getenv("GMAIL_APP_PASSWORD")
USERBOT_SESSION   = os.getenv("TG_USERBOT_SESSION", "")
BOT_SESSION       = os.getenv("TG_BOT_SESSION", "")

# ── Gemini setup ──────────────────────────────────────────────────────────────
genai.configure(api_key=GEMINI_KEY)
gemini = genai.GenerativeModel("gemini-2.5-flash")

# ── Telegram clients (uses session strings — no OTP needed after first setup) ─
userbot = TelegramClient(StringSession(USERBOT_SESSION), API_ID, API_HASH)
bot     = TelegramClient(StringSession(BOT_SESSION), API_ID, API_HASH)

# ── In-memory pending  { queue_id: formatted_text } ──────────────────────────
pending = {}

SYSTEM_PROMPT = """You are a job post formatter for ReferJobs, a premium job channel.

Convert any job opportunity into this exact format:

🚀 [Role] at [Company]

Stipend/Salary: ₹[amount]
Location: [location]
Batch: [year(s)]

Why this role stands out:
• [highlight 1]
• [highlight 2]
• [highlight 3]

🔗 Apply: [link or "Not available"]

#[tag1] #[tag2] #[tag3]

Rules:
- Use ONLY these hashtags when relevant: #AI #Product #Remote #Internship #PPO #HighStipend #Tech #Design #Marketing #Finance #Operations #Hybrid #Fulltime
- Max 4 hashtags
- #HighStipend only if ₹30K+/month or ₹6LPA+
- If no apply link exists, write "🔗 Apply: Not available — referral only"
- If post is NOT a job opportunity, reply with exactly: NOT_A_JOB
- Keep bullet points concise and factual
- Do not add information not in the original post"""


async def format_with_ai(raw_text: str) -> str | None:
    """Format job post using Gemini free API."""
    prompt   = f"{SYSTEM_PROMPT}\n\nJob post to format:\n{raw_text}"
    response = gemini.generate_content(prompt)
    result   = response.text.strip()
    return None if result == "NOT_A_JOB" else result


async def send_review_message(queue_id: str, formatted: str):
    """Send a formatted post to reviewer with approve/edit/reject buttons."""
    preview      = formatted[:3500]
    pending[queue_id] = formatted

    markup = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✅ Approve", data=f"approve:{queue_id}".encode()),
            KeyboardButtonCallback(text="✏️ Edit",   data=f"edit:{queue_id}".encode()),
            KeyboardButtonCallback(text="❌ Reject",  data=f"reject:{queue_id}".encode()),
        ])
    ])

    await bot.send_message(
        REVIEWER_CHAT_ID,
        f"📢 *New Job Post*\n\n{preview}",
        parse_mode="markdown",
        buttons=markup
    )


# ── Telegram source listener (userbot) ───────────────────────────────────────
@userbot.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def on_tg_message(event):
    raw = event.message.text or event.message.caption or ""
    if not raw.strip():
        return
    print(f"[TG] {raw[:80]}...")
    formatted = await format_with_ai(raw)
    if not formatted:
        print("  → Not a job, skipped")
        return
    queue_id = f"tg-{event.message.id}"
    await send_review_message(queue_id, formatted)
    print(f"  → Sent for review ({queue_id})")


# ── Review bot: button callbacks ──────────────────────────────────────────────
@bot.on(events.CallbackQuery())
async def on_button(event):
    data   = event.data.decode()
    action, queue_id = data.split(":", 1)

    if action == "approve":
        text = pending.pop(queue_id, None)
        if text:
            await userbot.send_message(YOUR_CHANNEL, text)
            await event.edit("✅ *Posted to channel!*", parse_mode="markdown", buttons=None)
            print(f"  → Approved & posted ({queue_id})")
        else:
            await event.answer("⚠️ Post not found.")

    elif action == "reject":
        pending.pop(queue_id, None)
        await event.edit("❌ *Rejected.*", parse_mode="markdown", buttons=None)
        print(f"  → Rejected ({queue_id})")

    elif action == "edit":
        text = pending.get(queue_id, "")
        await event.answer("Send edited version with /submit")
        await bot.send_message(
            REVIEWER_CHAT_ID,
            f"✏️ *Edit Mode*\n\nCurrent draft:\n\n{text}\n\n"
            f"Reply with:\n`/submit {queue_id}`\n`<your edited text>`",
            parse_mode="markdown"
        )


@bot.on(events.NewMessage(
    pattern=r"/submit (.+)",
    from_users=REVIEWER_CHAT_ID
))
async def on_submit(event):
    full  = event.raw_text
    lines = full.split("\n", 2)
    if len(lines) < 3:
        await event.reply("❌ Format: `/submit <id>`\n`<edited text>`")
        return
    queue_id    = lines[1].replace("/submit ", "").strip()
    edited_text = "\n".join(lines[2:]).strip()
    if queue_id in pending:
        pending[queue_id] = edited_text
        await event.reply("✅ *Draft updated!* Now tap Approve.", parse_mode="markdown")
    else:
        await event.reply("⚠️ Post not found. May have already been actioned.")


# ── Invite Link Generator ─────────────────────────────────────────────────────
async def generate_invite_link(channel_id: int) -> str | None:
    expire_date = int((datetime.now() + timedelta(hours=24)).timestamp())
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createChatInviteLink"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={
            "chat_id":      channel_id,
            "member_limit": 1,
            "expire_date":  expire_date,
            "name":         f"Invite_{int(datetime.now().timestamp())}",
        }) as resp:
            data = await resp.json()
            if data.get("ok"):
                return data["result"]["invite_link"]
            print(f"[Invite Error] {data}")
            return None


# ── Email Sender ──────────────────────────────────────────────────────────────
def send_invite_email(to_email: str, full_name: str, invite_link: str):
    first_name = full_name.split()[0] if full_name else "there"
    subject    = "✅ Payment Verified — Your ReferJobs Premium Access"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">

      <div style="background:#1a1a2e;padding:30px;text-align:center;border-radius:10px 10px 0 0;">
        <h1 style="color:#ffffff;margin:0;font-size:26px;">🚀 ReferJobs Premium</h1>
        <p style="color:#a0a0b0;margin:10px 0 0;font-size:14px;">Save Time. Find Better. Apply Smarter.</p>
      </div>

      <div style="background:#ffffff;padding:35px;border:1px solid #e0e0e0;">

        <h2 style="color:#1a1a2e;margin-top:0;">Hey {first_name}! 👋</h2>

        <p style="color:#444;line-height:1.7;">
          Your payment has been verified. You now have access to
          <strong>ReferJobs Premium</strong> — follow the steps below to join right away.
        </p>

        <hr style="border:none;border-top:1px solid #eee;margin:25px 0;">

        <h3 style="color:#1a1a2e;margin-bottom:5px;">How to Join</h3>

        <table style="width:100%;border-collapse:collapse;">
          <tr>
            <td style="padding:12px 0;vertical-align:top;width:36px;">
              <span style="background:#1a1a2e;color:white;border-radius:50%;
                           padding:4px 10px;font-weight:bold;font-size:14px;">1</span>
            </td>
            <td style="padding:12px 0;color:#444;line-height:1.6;">
              Make sure you have <strong>Telegram installed</strong> on your phone or desktop.
            </td>
          </tr>
          <tr>
            <td style="padding:12px 0;vertical-align:top;">
              <span style="background:#1a1a2e;color:white;border-radius:50%;
                           padding:4px 10px;font-weight:bold;font-size:14px;">2</span>
            </td>
            <td style="padding:12px 0;color:#444;line-height:1.6;">
              Tap the <strong>Join ReferJobs Premium</strong> button below.
              It will open Telegram and add you to the channel instantly.
            </td>
          </tr>
          <tr>
            <td style="padding:12px 0;vertical-align:top;">
              <span style="background:#1a1a2e;color:white;border-radius:50%;
                           padding:4px 10px;font-weight:bold;font-size:14px;">3</span>
            </td>
            <td style="padding:12px 0;color:#444;line-height:1.6;">
              Once inside, <strong>turn on notifications</strong> so you never miss
              an opportunity.
            </td>
          </tr>
          <tr>
            <td style="padding:12px 0;vertical-align:top;">
              <span style="background:#1a1a2e;color:white;border-radius:50%;
                           padding:4px 10px;font-weight:bold;font-size:14px;">4</span>
            </td>
            <td style="padding:12px 0;color:#444;line-height:1.6;">
              Use <strong>hashtags</strong> to quickly find roles that match your interests —
              <code>#AI</code>, <code>#Remote</code>, <code>#Internship</code>,
              <code>#PPO</code>, <code>#HighStipend</code>, <code>#Product</code> and more.
            </td>
          </tr>
        </table>

        <div style="text-align:center;margin:30px 0;">
          <a href="{invite_link}"
             style="background:#1a1a2e;color:white;padding:16px 40px;
                    text-decoration:none;border-radius:8px;font-size:16px;
                    font-weight:bold;display:inline-block;letter-spacing:0.3px;">
            Join ReferJobs Premium →
          </a>
        </div>

        <div style="background:#fff3cd;border:1px solid #ffc107;
                    border-radius:8px;padding:16px;margin:10px 0 25px;">
          <p style="margin:0;color:#856404;font-size:14px;line-height:1.7;">
            ⚠️ <strong>Please read before clicking:</strong><br>
            This link is generated exclusively for you. It is valid for
            <strong>24 hours only</strong> and can be used <strong>just once.</strong>
            If someone else uses your link, your access will be blocked permanently.
            Do not forward or share this email with anyone.
          </p>
        </div>

        <p style="color:#444;line-height:1.7;">
          Your subscription is active for <strong>3 months</strong> from today.
          We will send you a reminder 7 days before it expires so you have
          enough time to renew without any interruption.
        </p>

        <hr style="border:none;border-top:1px solid #eee;margin:25px 0;">

        <p style="color:#888;font-size:13px;line-height:1.6;margin:0;">
          If you face any issues joining, reply to this email or reach out to us on
          Telegram at
          <a href="https://t.me/ReferJobsAdmin" style="color:#1a1a2e;font-weight:bold;">
            @ReferJobsAdmin
          </a>.
          We typically respond within a few hours.
        </p>

      </div>

      <div style="background:#f8f8f8;padding:15px;text-align:center;
                  border-radius:0 0 10px 10px;border:1px solid #e0e0e0;border-top:none;">
        <p style="color:#aaa;font-size:12px;margin:0;">
          © ReferJobs &nbsp;·&nbsp; referjobsco@gmail.com
        </p>
      </div>

    </div>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"ReferJobs <{GMAIL_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())
    print(f"[Email] Sent to {to_email}")


# ── /invite command ───────────────────────────────────────────────────────────
@bot.on(events.NewMessage(
    pattern=r"/invite (.+)",
    from_users=REVIEWER_CHAT_ID
))
async def cmd_invite(event):
    """Usage: /invite email@gmail.com Full Name"""
    args = event.pattern_match.group(1).strip().split(" ", 1)
    if len(args) < 2:
        await event.reply(
            "❌ *Wrong format!*\n\n"
            "Usage:\n`/invite email@gmail.com Full Name`\n\n"
            "Example:\n`/invite john@gmail.com John Doe`",
            parse_mode="markdown"
        )
        return

    email     = args[0].strip()
    full_name = args[1].strip()

    if "@" not in email or "." not in email:
        await event.reply("❌ Invalid email address. Please check and try again.")
        return

    await event.reply(f"⏳ Generating invite for *{full_name}*...", parse_mode="markdown")

    try:
        channel_entity = await userbot.get_entity(YOUR_CHANNEL)
        invite_link    = await generate_invite_link(channel_entity.id)

        if not invite_link:
            await event.reply("❌ Failed to generate link. Is bot admin of channel?")
            return

        send_invite_email(email, full_name, invite_link)

        await event.reply(
            f"✅ *Invite Sent!*\n\n"
            f"👤 Name: {full_name}\n"
            f"📧 Email: {email}\n"
            f"🔗 Link: `{invite_link}`\n\n"
            f"⏰ Expires 24hrs · 1 use only",
            parse_mode="markdown"
        )
        print(f"[Invite] Sent to {email} ({full_name})")

    except smtplib.SMTPAuthenticationError:
        await event.reply(
            "❌ Gmail auth failed.\n"
            "Check GMAIL_APP_PASSWORD in .env\n"
            "Generate at: myaccount.google.com/apppasswords"
        )
    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")


# ── Renewal screenshot handler ────────────────────────────────────────────────
@bot.on(events.NewMessage(func=lambda e: e.photo and e.chat_id != REVIEWER_CHAT_ID))
async def on_renewal_screenshot(event):
    """When a member sends a payment screenshot to the bot for renewal."""
    try:
        sender    = await event.get_sender()
        user_id   = sender.id
        full_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
        username  = sender.username or "no username"
        member    = get_member(user_id)

        # Download screenshot and forward to you
        photo = await event.download_media(bytes)

        caption = (
            f"💳 *Renewal Payment Screenshot*\n\n"
            f"👤 Name: {full_name}\n"
            f"📱 Username: @{username}\n"
            f"🆔 User ID: `{user_id}`\n"
        )

        if member:
            expiry = datetime.fromisoformat(member["expires_at"]).strftime("%d %b %Y")
            caption += f"📅 Current expiry: {expiry}\n"
            caption += f"Status: {member['status']}\n"
        else:
            caption += "⚠️ Not found in members DB\n"

        caption += f"\n✅ To renew: `/renew {user_id}`"

        await bot.send_file(
            REVIEWER_CHAT_ID,
            photo,
            caption=caption,
            parse_mode="markdown"
        )

        # Acknowledge to member
        await event.reply(
            "✅ *Screenshot received!*\n\n"
            "We'll verify your payment and renew your access within a few hours.\n\n"
            "Thank you for renewing with ReferJobs! 🚀",
            parse_mode="markdown"
        )

    except Exception as e:
        print(f"[Renewal Screenshot Error] {e}")


# ── Member join tracking ──────────────────────────────────────────────────────
@userbot.on(events.ChatAction(chats=YOUR_CHANNEL))
async def on_member_join(event):
    if event.user_joined or event.user_added:
        try:
            user      = await event.get_user()
            full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
            add_member(user_id=user.id, username=user.username or "", full_name=full_name)
            print(f"[Member] New join: {full_name} (@{user.username})")
            await bot.send_message(
                user.id,
                f"👋 *Welcome to ReferJobs Premium, {full_name.split()[0]}!*\n\n"
                f"Your 3-month subscription starts today.\n\n"
                f"📌 Use hashtags to find roles:\n"
                f"#AI · #Product · #Remote · #Internship · #PPO · #HighStipend\n\n"
                f"🔔 Keep notifications ON for faster applications.\n\n"
                f"_Save Time. Find Better. Apply Smarter._ 🚀",
                parse_mode="markdown"
            )
        except Exception as e:
            print(f"[Member Join Error] {e}")


# ── Admin commands ────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r"/members", from_users=REVIEWER_CHAT_ID))
async def cmd_members(event):
    members = get_all_active()
    if not members:
        await event.reply("No active members yet.")
        return
    lines = [f"👥 *Active Members ({len(members)})*\n"]
    for m in members:
        expiry = datetime.fromisoformat(m["expires_at"]).strftime("%d %b %Y")
        warned = "⚠️" if m["warned"] else ""
        name   = m["full_name"] or m["username"] or str(m["user_id"])
        lines.append(f"{warned} {name} — expires *{expiry}*")
    await event.reply("\n".join(lines), parse_mode="markdown")


@bot.on(events.NewMessage(pattern=r"/renew (\d+)", from_users=REVIEWER_CHAT_ID))
async def cmd_renew(event):
    user_id = int(event.pattern_match.group(1))
    member  = get_member(user_id)
    if not member:
        await event.reply("⚠️ Member not found.")
        return
    renew_member(user_id)
    new_expiry = (datetime.now() + timedelta(days=90)).strftime("%d %b %Y")
    await event.reply(
        f"✅ Renewed *{member['full_name']}* — new expiry: *{new_expiry}*",
        parse_mode="markdown"
    )
    await bot.send_message(
        user_id,
        f"🎉 *Your ReferJobs Premium has been renewed!*\n\n"
        f"New expiry: *{new_expiry}*\n\n"
        f"Keep notifications ON and keep applying! 🚀",
        parse_mode="markdown"
    )


@bot.on(events.NewMessage(pattern=r"/help", from_users=REVIEWER_CHAT_ID))
async def cmd_help(event):
    await event.reply(
        "🤖 *ReferJobs Bot Commands*\n\n"
        "*Inviting New Members:*\n"
        "`/invite email@gmail.com Full Name`\n\n"
        "*Managing Members:*\n"
        "`/members` — View all active members\n"
        "`/renew 123456789` — Renew by user ID\n\n"
        "*Renewal:*\n"
        "Member sends payment screenshot → bot forwards to you\n"
        "→ Verify → `/renew <user_id>`\n\n"
        "*Job Posts:*\n"
        "Tap ✅ Approve · ✏️ Edit · ❌ Reject\n\n"
        "*Edit a queued post:*\n"
        "`/submit <id>`\n`<edited text>`",
        parse_mode="markdown"
    )


# ── Start everything ──────────────────────────────────────────────────────────
async def main():
    print("🚀 ReferJobs bot pipeline starting...")
    init_db()

    # Session strings handle auth — no OTP or phone needed
    await userbot.start()
    await bot.start()

    me = await userbot.get_me()
    print(f"✅ Userbot: @{me.username}")
    print(f"📡 Source: {SOURCE_CHANNEL} → {YOUR_CHANNEL}")
    print(f"👤 Reviewer ID: {REVIEWER_CHAT_ID}")
    print(f"🤖 AI: Gemini 1.5 Flash (free tier)")

    channel_entity = await userbot.get_entity(YOUR_CHANNEL)

    await asyncio.gather(
        userbot.run_until_disconnected(),
        bot.run_until_disconnected(),
        run_daily_check(bot, userbot, channel_entity.id),
    )


if __name__ == "__main__":
    asyncio.run(main())
