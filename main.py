import os
import sys
import json
import asyncio
import logging
from collections import deque

from pyrogram import Client, filters, idle, utils
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, RPCError
from pyrogram.raw.functions.messages import Report, SaveDraft
from pyrogram.raw.types import (
    InputReportReasonSpam,
    InputReportReasonOther,
    UpdateDraftMessage,
    DraftMessage,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("userbot")

# ----------------------------------------------------
# SOZLAMALAR (Railway Variables)
# ----------------------------------------------------
API_ID = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

missing = [n for n, v in (("API_ID", API_ID), ("API_HASH", API_HASH), ("SESSION_STRING", SESSION_STRING)) if not v]
if missing:
    log.error("Railway Variables'da quyidagilar yo'q: %s", ", ".join(missing))
    sys.exit(1)

try:
    API_ID = int(API_ID)
except ValueError:
    log.error("API_ID faqat raqamlardan iborat bo'lishi kerak")
    sys.exit(1)

# Railway Volume /data ga ulangan bo'lsa, paketlar o'sha yerda saqlanadi (deploy'da o'chmaydi)
DATA_DIR = os.getenv("DATA_DIR") or ("/data" if os.path.isdir("/data") else ".")
DB_FILE = os.path.join(DATA_DIR, "packets.json")

app = Client(
    "userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)

MY_ID = None
DRAFT_DELAY = 2  # qoralama o'zgarmay shuncha soniya tursa, paket yuboriladi
MEDIA_TYPES = ("photo", "voice", "video", "video_note", "animation", "audio", "document")
LABELS = {
    "text": "matn", "photo": "rasm", "voice": "ovozli", "video": "video",
    "video_note": "krujok", "animation": "GIF", "audio": "audio", "document": "fayl",
}


# ----------------------------------------------------
# PAKETLAR BAZASI
# ----------------------------------------------------
def _normalize(packet):
    """Eski formatdagi ({"text":..,"voice":..,"video":..}) paketlarni yangi formatga o'tkazadi."""
    if isinstance(packet, list):
        return packet
    items = []
    if isinstance(packet, dict):
        if packet.get("text"):
            items.append({"type": "text", "text": packet["text"]})
        if packet.get("voice"):
            items.append({"type": "voice", "file_id": packet["voice"]})
        if packet.get("video"):
            items.append({"type": "video", "file_id": packet["video"]})
    return items


def load_packets():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("packets.json o'qilmadi: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): _normalize(v) for k, v in data.items()}


def save_packets():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(packets, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB_FILE)


packets = load_packets()
wizard = None                # {"name": str, "items": list, "status_id": int}
sent_ids = deque(maxlen=300)  # bot o'zi yuborgan xabarlar (qayta ishlanmasligi uchun)
last_drafts = {}              # chat_id -> oxirgi qoralama matni


# ----------------------------------------------------
# YORDAMCHI FUNKSIYALAR
# ----------------------------------------------------
def remember(msg):
    if msg:
        sent_ids.append((msg.chat.id, msg.id))
    return msg


async def safe(call):
    """FloodWait bo'lsa kutib, bir marta qayta urinadi."""
    try:
        return await call()
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        return await call()


async def notice(message, text, delete_after=None):
    """Buyruq xabarining o'zini javobga aylantiradi (chatda ortiqcha xabar qolmaydi)."""
    try:
        await message.edit_text(text)
    except RPCError:
        return
    if delete_after:
        async def _later():
            await asyncio.sleep(delete_after)
            try:
                await message.delete()
            except RPCError:
                pass
        asyncio.create_task(_later())


def is_saved_messages(message):
    return message.chat.id == MY_ID


def panel_text():
    lines = ["🎛 **Userbot paneli**", "", f"📦 Paketlar: **{len(packets)}** ta"]
    if wizard:
        lines.append(f"✍️ Yaratilmoqda: `{wizard['name']}` ({len(wizard['items'])} ta element)")
    lines += [
        "",
        "**Paketlar** (Saqlangan xabarlarda):",
        "`!add nom` — yangi paket boshlash",
        "`!save` — paketni saqlash",
        "`!cancel` — bekor qilish",
        "`!list` — paketlar ro'yxati",
        "`!del nom` — paketni o'chirish",
        "",
        "**Ishlatish (tavsiya):** lid chatida paket nomini yozing, lekin **yubormang** — "
        "chatlar ro'yxatiga chiqing. Nom lidga umuman bormaydi, bot paketni o'zi yuboradi.",
        "Yuborib yuborsangiz ham ishlaydi, lekin nom bir lahzaga lidga boradi.",
        "",
        "**Himoya** (xabarga reply qilib):",
        "`!spam` — spam deb shikoyat + blok",
        "`!scam` — firibgarlik deb shikoyat + blok",
    ]
    return "\n".join(lines)


def wizard_status():
    w = wizard
    kinds = ", ".join(LABELS.get(i["type"], i["type"]) for i in w["items"]) or "hali yo'q"
    text = (
        f"✍️ **Paket:** `{w['name']}`\n"
        f"📎 Elementlar ({len(w['items'])}): {kinds}\n\n"
        "Shu chatga matn, rasm, ovozli, video, krujok yoki fayl yuboring — "
        "paket shu tartibda yuboriladi.\n"
        "Tugatish: `!save` · Bekor qilish: `!cancel`"
    )
    if w["name"] in packets:
        text += "\n\n⚠️ Bu nomli paket allaqachon bor — saqlasangiz almashtiriladi."
    return text


async def refresh_wizard_status(client):
    if not wizard:
        return
    try:
        await client.edit_message_text(MY_ID, wizard["status_id"], wizard_status())
    except RPCError:
        pass


def extract_item(message):
    if message.text:
        return {"type": "text", "text": message.text.html}
    for t in MEDIA_TYPES:
        media = getattr(message, t, None)
        if media:
            item = {"type": t, "file_id": media.file_id}
            if message.caption:
                item["caption"] = message.caption.html
            return item
    return None


async def send_item(client, chat_id, item, reply_to=None):
    t = item["type"]
    if t == "text":
        return await client.send_message(
            chat_id, item["text"], parse_mode=ParseMode.HTML, reply_to_message_id=reply_to
        )
    method = getattr(client, f"send_{t}")
    kwargs = {t: item["file_id"], "reply_to_message_id": reply_to}
    if item.get("caption") and t != "video_note":
        kwargs["caption"] = item["caption"]
        kwargs["parse_mode"] = ParseMode.HTML
    return await method(chat_id, **kwargs)


# ----------------------------------------------------
# BUYRUQLAR
# ----------------------------------------------------
async def cmd_panel(client, message, arg):
    # Mijoz chatida panel 15 soniyadan keyin o'chadi
    await notice(message, panel_text(), None if is_saved_messages(message) else 15)


async def cmd_add(client, message, arg):
    global wizard
    if not is_saved_messages(message):
        await notice(message, "⚠️ Paketni **Saqlangan xabarlar** (Saved Messages) chatida yarating.", 5)
        return
    name = arg.strip().lower()
    if not name or " " in name or name.startswith("!"):
        await notice(message, "⚠️ Format: `!add nom` — nom bitta so'z va `!` belgisisiz bo'lsin.", 8)
        return
    wizard = {"name": name, "items": [], "status_id": message.id}
    await notice(message, wizard_status())


async def cmd_save(client, message, arg):
    global wizard
    if not wizard:
        await notice(message, "ℹ️ Faol paket yo'q. Boshlash: `!add nom`", 5)
        return
    if not wizard["items"]:
        await notice(message, "⚠️ Paket bo'sh — avval biror narsa yuboring.", 5)
        return
    name, items = wizard["name"], wizard["items"]
    old = packets.get(name)
    packets[name] = items
    try:
        save_packets()
    except OSError as e:
        if old is None:
            packets.pop(name, None)
        else:
            packets[name] = old
        log.error("Saqlashda xato: %s", e)
        await notice(message, f"❌ Saqlab bo'lmadi: `{e}`")
        return
    status_id = wizard["status_id"]
    wizard = None
    try:
        await client.edit_message_text(MY_ID, status_id, f"✅ Paket `{name}` tayyor.")
    except RPCError:
        pass
    await notice(message, f"🎉 Paket `{name}` saqlandi ({len(items)} ta element).", 10)


async def cmd_cancel(client, message, arg):
    global wizard
    if not wizard:
        await notice(message, "ℹ️ Bekor qiladigan narsa yo'q.", 5)
        return
    name = wizard["name"]
    wizard = None
    await notice(message, f"🚫 `{name}` paketini yaratish bekor qilindi.", 5)


async def cmd_list(client, message, arg):
    if not packets:
        text = "📭 Hozircha paketlar yo'q. Yaratish: `!add nom`"
    else:
        rows = []
        for name, items in sorted(packets.items()):
            kinds = ", ".join(LABELS.get(i["type"], i["type"]) for i in items)
            rows.append(f"• `{name}` — {kinds}")
        text = "📋 **Saqlangan paketlar:**\n\n" + "\n".join(rows)
    await notice(message, text, None if is_saved_messages(message) else 15)


async def cmd_del(client, message, arg):
    name = arg.strip().lower()
    if not name:
        await notice(message, "⚠️ Format: `!del nom`", 5)
        return
    if name not in packets:
        await notice(message, f"⚠️ `{name}` nomli paket topilmadi.", 5)
        return
    removed = packets.pop(name)
    try:
        save_packets()
    except OSError as e:
        packets[name] = removed
        await notice(message, f"❌ O'chirib bo'lmadi: `{e}`")
        return
    await notice(message, f"🗑 Paket `{name}` o'chirildi.", 5)


async def cmd_report(client, message, scam):
    target = message.reply_to_message
    if not target:
        await notice(message, "⚠️ Spammer xabariga **reply** qilib yozing.", 4)
        return
    user = target.from_user
    if not user or user.is_self:
        await notice(message, "⚠️ Bu xabar egasini aniqlab bo'lmadi.", 4)
        return

    reason = InputReportReasonOther() if scam else InputReportReasonSpam()
    comment = "Scam / fraud" if scam else "Spam"
    results = []

    try:
        await client.invoke(
            Report(
                peer=await client.resolve_peer(message.chat.id),
                id=[target.id],
                reason=reason,
                message=comment,
            )
        )
        results.append("shikoyat yuborildi")
    except RPCError as e:
        log.warning("Shikoyat xatosi: %s", e)
        results.append("shikoyat yuborilmadi")

    try:
        await client.block_user(user.id)
        results.append("bloklandi")
    except RPCError as e:
        log.warning("Bloklash xatosi: %s", e)
        results.append("bloklanmadi")

    icon = "🚨" if scam else "🛡"
    await notice(message, f"{icon} {user.first_name or 'Foydalanuvchi'}: " + ", ".join(results), 3)


COMMANDS = {
    "!panel": cmd_panel,
    "!add": cmd_add,
    "!save": cmd_save,
    "!cancel": cmd_cancel,
    "!list": cmd_list,
    "!del": cmd_del,
    "!spam": lambda c, m, a: cmd_report(c, m, scam=False),
    "!scam": lambda c, m, a: cmd_report(c, m, scam=True),
}


# ----------------------------------------------------
# PAKET YUBORISH
# ----------------------------------------------------
async def deliver_packet(client, chat_id, key, reply_to=None):
    for index, item in enumerate(packets.get(key, [])):
        try:
            sent = await safe(lambda: send_item(client, chat_id, item, reply_to if index == 0 else None))
            remember(sent)
        except RPCError as e:
            log.warning("Paket '%s' elementi yuborilmadi: %s", key, e)
        await asyncio.sleep(0.4)


async def send_packet(client, message, key):
    """Paket nomi yuborilgan holat: nomni o'chirib, paketni yuboradi."""
    chat_id = message.chat.id
    reply_to = message.reply_to_message_id
    try:
        await message.delete()
    except RPCError:
        pass
    await deliver_packet(client, chat_id, key, reply_to)


# ----------------------------------------------------
# QORALAMA ORQALI PAKET (nom lidga umuman yuborilmaydi)
# ----------------------------------------------------
async def handle_draft(client, update, chat_id, text):
    await asyncio.sleep(DRAFT_DELAY)
    if last_drafts.get(chat_id) != text:
        return  # hali yozilyapti yoki qoralama o'zgardi
    last_drafts[chat_id] = ""

    try:
        await client.invoke(SaveDraft(peer=await client.resolve_peer(chat_id), message=""))
    except RPCError as e:
        log.warning("Qoralamani tozalab bo'lmadi: %s", e)

    log.info("Qoralama orqali paket '%s' -> %s", text, chat_id)
    await deliver_packet(client, chat_id, text, getattr(update, "top_msg_id", None))


@app.on_raw_update(group=1)
async def on_raw(client, update, users, chats):
    if not isinstance(update, UpdateDraftMessage):
        return
    try:
        chat_id = utils.get_peer_id(update.peer)
    except Exception:
        return

    draft = update.draft
    text = ""
    if isinstance(draft, DraftMessage) and draft.message:
        text = draft.message.strip().lower()
    last_drafts[chat_id] = text

    if text and text in packets and chat_id != MY_ID:
        asyncio.create_task(handle_draft(client, update, chat_id, text))


# ----------------------------------------------------
# ASOSIY HANDLER (bitta handler — xabarlar bir-birini "yutib" yubormaydi)
# ----------------------------------------------------
@app.on_message(filters.me & ~filters.service)
async def on_my_message(client, message):
    if message.edit_date or (message.chat.id, message.id) in sent_ids:
        return

    text = (message.text or "").strip()

    # 1. Buyruqlar
    if text.startswith("!"):
        cmd, _, arg = text.partition(" ")
        handler = COMMANDS.get(cmd.lower())
        if handler:
            try:
                await handler(client, message, arg)
            except Exception as e:
                log.exception("Buyruq xatosi (%s): %s", cmd, e)
            return

    # 2. Paket yaratish rejimi (faqat Saqlangan xabarlarda)
    if wizard and is_saved_messages(message) and message.id != wizard["status_id"]:
        item = extract_item(message)
        if item:
            wizard["items"].append(item)
            await refresh_wizard_status(client)
        return

    # 3. Paket nomi yozilsa — paketni yuborish
    key = text.lower()
    if key and key in packets:
        await send_packet(client, message, key)


# ----------------------------------------------------
# ISHGA TUSHIRISH
# ----------------------------------------------------
async def main():
    global MY_ID
    await app.start()
    me = await app.get_me()
    MY_ID = me.id
    log.info("Userbot ishga tushdi: %s (id=%s)", me.first_name, me.id)
    log.info("Paketlar: %d ta, fayl: %s", len(packets), DB_FILE)
    if DATA_DIR == ".":
        log.warning("Volume ulanmagan — paketlar har deploy'da o'chib ketadi. Railway'da /data ga Volume ulang.")
    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
