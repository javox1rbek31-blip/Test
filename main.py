import os
import json
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.raw.functions.messages import Report
from pyrogram.raw.types import InputReportReasonSpam

# Railway Variables'dan o'qib olish
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

if SESSION_STRING:
    app = Client("secure_packet_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
else:
    app = Client("secure_packet_bot", api_id=API_ID, api_hash=API_HASH)

DB_FILE = "packets.json"
temp_state = {}

def load_packets():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_packets(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

packets_db = load_packets()

# ----------------------------------------------------
# BOSH MENYU VA KNOPKALAR ORQALI BOSHQARUV (!panel)
# ----------------------------------------------------
@app.on_message(filters.me & filters.text & filters.regex(r"^!panel$"))
async def open_panel(client, message):
    await message.delete()
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Yangi paket qo'shish", callback_data="btn_add")],
        [InlineKeyboardButton("📋 Paketlar ro'yxati", callback_data="btn_list")],
        [InlineKeyboardButton("❌ Panelni yopish", callback_data="btn_close")]
    ])
    
    await client.send_message(
        message.chat.id,
        "🎛 **Userbot Boshqaruv Paneli**\n\nKerakli tugmani tanlang:",
        reply_markup=keyboard
    )

@app.on_callback_query()
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if data == "btn_add":
        temp_state[user_id] = {"step": "waiting_name"}
        await callback_query.message.edit_text(
            "✍️ **Yangi paket yaratish:**\n\nIltimos, ushbu paket uchun kalit so'z yozing (masalan: `kom01`):"
        )
    elif data == "btn_list":
        if not packets_db:
            await callback_query.answer("Hozircha paketlar yo'q!", show_alert=True)
            return
        
        text = "📋 **Saqlangan paketlar:**\n\n"
        for name in packets_db:
            text += f"• `{name}`\n"
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="btn_panel")]])
        await callback_query.message.edit_text(text, reply_markup=keyboard)
        
    elif data == "btn_panel":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Yangi paket qo'shish", callback_data="btn_add")],
            [InlineKeyboardButton("📋 Paketlar ro'yxati", callback_data="btn_list")],
            [InlineKeyboardButton("❌ Panelni yopish", callback_data="btn_close")]
        ])
        await callback_query.message.edit_text("🎛 **Userbot Boshqaruv Paneli**", reply_markup=keyboard)
        
    elif data == "btn_close":
        await callback_query.message.delete()

# ----------------------------------------------------
# TUGMALAR ORQALI PAKET YARATISH JARAYONI
# ----------------------------------------------------
@app.on_message(filters.me & filters.private)
async def packet_wizard(client, message):
    user_id = message.from_user.id
    if user_id not in temp_state:
        return

    state = temp_state[user_id].get("step")

    # 1. Paket nomini olish
    if state == "waiting_name":
        p_name = message.text.strip().lower()
        temp_state[user_id] = {
            "name": p_name,
            "text": None,
            "voice": None,
            "video": None,
            "step": "waiting_content"
        }
        await message.reply(
            f"✅ Paket nomi: **{p_name}**\n\n"
            f"Endi ushbu paketga tegishli **Matn**, **Ovozli xabar** va **Videoni** shu chatga ketma-ket yuboring.\n"
            f"Barchasini yuborib bo'lgach, **!save** deb yozing."
        )
        return

    # 2. Kontentlarni qabul qilish va Saqlash
    if state == "waiting_content":
        if message.text and message.text.strip() == "!save":
            p_data = temp_state[user_id]
            p_name = p_data["name"]
            
            packets_db[p_name] = {
                "text": p_data["text"],
                "voice": p_data["voice"],
                "video": p_data["video"]
            }
            save_packets(packets_db)
            del temp_state[user_id]
            
            await message.reply(f"🎉 **Paket `{p_name}` muvaffaqiyatli saqlandi!**")
            return

        if message.text:
            temp_state[user_id]["text"] = message.text
            await message.reply("📝 Matn qabul qilindi. Davom eting yoki `!save` yozing.")
        elif message.voice:
            temp_state[user_id]["voice"] = message.voice.file_id
            await message.reply("🎙 Ovozli xabar qabul qilindi. Davom eting yoki `!save` yozing.")
        elif message.video:
            temp_state[user_id]["video"] = message.video.file_id
            await message.reply("🎥 Video qabul qilindi. Davom eting yoki `!save` yozing.")

# ----------------------------------------------------
# CHATLARDA PAKETNI ISHLATISH VA CHATNI TOZA SAQLASH
# ----------------------------------------------------
@app.on_message(filters.me & filters.text)
async def handle_triggers_and_security(client, message):
    text = message.text.strip().lower()
    chat_id = message.chat.id

    # Paketni yuborish
    if text in packets_db:
        packet = packets_db[text]
        await message.delete()  # Trigger so'zni darhol o'chirib, chatni toza saqlaydi
        
        try:
            if packet.get("text"):
                await client.send_message(chat_id, packet["text"])
                await asyncio.sleep(0.3)
            if packet.get("voice"):
                await client.send_voice(chat_id, voice=packet["voice"])
                await asyncio.sleep(0.3)
            if packet.get("video"):
                await client.send_video(chat_id, video=packet["video"])
        except Exception as e:
            print(f"Xatolik: {e}")
        return

    # 100% HIMOYA: !spam yoki !scam buyrug'i orqali shikoyat qilib bloklash
    if text.startswith("!spam") or text.startswith("!scam"):
        if not message.reply_to_message:
            await message.delete()
            return
            
        user = message.reply_to_message.from_user
        if not user:
            return

        try:
            await message.delete()
            # Telegram serveriga rasmiy shikoyat
            await client.invoke(
                Report(
                    peer=await client.resolve_peer(user.id),
                    id=[message.reply_to_message.id],
                    reason=InputReportReasonSpam(),
                    message="Spam and security protection"
                )
            )
            # Qora ro'yxatga (Block) tiqish
            await client.block_user(user.id)
            
            notif = await client.send_message(chat_id, f"🛡 Foydalanuvchi ({user.first_name}) bloklandi va shikoyat qilindi!")
            await asyncio.sleep(3)
            await notif.delete()
        except Exception as e:
            print(f"Xatolik: {e}")

if __name__ == "__main__":
    print("Xavfsiz va tugmali Userbot ishga tushdi...")
    app.run()
