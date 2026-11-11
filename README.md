# 🏫 Uy ishlari boti (Telegram + Mini App)

O'quvchi, starosta, o'qituvchi va developer rollari bilan uy vazifalari, eslatmalar, dars jadvali va **Mini App** boshqaruv paneli.

## ✨ Imkoniyatlar

- 🔑 **Telefon raqam bilan kirish** — o'quvchi faqat o'qituvchi kiritgan raqam orqali kira oladi (o'z-o'zidan ro'yxatdan o'tish yo'q)
- 🧑‍🎓 **O'quvchini faqat o'qituvchi qo'shadi** — telefon raqam + ism + familiya bilan bog'lanadi
- 🆕 O'qituvchi qo'shmagan o'quvchi raqam yuborsa, ism-familiya bilan **tasdiqlash so'rovi** o'qituvchiga boradi
- 📚 **Vazifalar matn YOKI rasm ko'rinishida** (ikkalasi birga ham bo'ladi), rasmlar Supabase Storage'ga yuklanadi (ishlamasa lokal `uploads/` papkasida saqlanadi)
- 📤 O'quvchi vazifani **rasm yoki matn** qilib topshiradi — o'qituvchiga darhol yetib boradi
- 🗓 **Dars jadvali eslatmalari**: har kuni 19:00 da darsga 3 kun, 2 kun, 1 kun qolganda eslatiladi
- 🔔 Vazifa eslatmalari: 3/2/1 kun va muddat kuni (08:00), ertangi jadval (19:30), bugungi jadval (07:30)
- 📱 **Mini App** — Telegram ichida to'liq boshqaruv (o'quvchi uchun ham, o'qituvchi uchun ham)
- 👑 **Developer paneli** — rollar va ruxsatlarni boshqarish (kim nima qila olishini developer belgilaydi)

## 🔧 O'rnatish

```
py -m pip install -r requirements.txt
```

Papkada `.env` fayl yarating (git'ga yuklanmaydi):

```
BOT_TOKEN=BotFather_bergan_token
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=supabase_kalit
SUPABASE_BUCKET=uy-ishlari
```

Supabase ixtiyoriy — bo'lmasa rasmlar `uploads/` papkasiga saqlanadi.

```
py bot.py
```

Birinchi bo'lib `/dev` yozib **developer** bo'ling.

## 📱 Mini App'ni ulash

Mini App HTTPS manzil talab qiladi. Lokal kompyuterda test uchun tunnel oching:

```
cloudflared tunnel --url http://localhost:8000
```

yoki ngrok: `ngrok http 8000`. Keyin:

```
set WEBAPP_URL=https://tunnel-manzilingiz
py bot.py
```

Shundan keyin barcha menyularda «📱 Mini ilova» tugmasi paydo bo'ladi. BotFather'da `/newapp` orqali ham havolani bog'lash mumkin.

## 👥 Rollar va ruxsatlar

| Ruxsat | Tavsif |
|---|---|
| Vazifa topshirish | O'quvchi vazifasini rasm/matn bilan topshiradi |
| Uy vazifa qo'shish | Starosta, o'qituvchi, developer |
| Eslatma yuborish | O'qituvchi, developer |
| O'quvchi qo'shish/ko'rish | O'qituvchi, developer |
| Dars jadvalini boshqarish | O'qituvchi, developer |
| Rol berish | O'qituvchi (faqat o'quvchi/starostaga), developer |
| O'chirish | O'qituvchi, developer |
| Statistika | O'qituvchi, developer |
| Ruxsatlarni boshqarish | **Faqat developer** |

Developer panelida (Mini App → 👑 Panel) har bir rol uchun ruxsatlarni yoqib/o'chirib boshqarish mumkin.

## 📌 Muhim

- Rasmlar avval **Supabase Storage**ga yuklanadi (`uy-ishlari` bucket). Agar bucket mavjud bo'lmasa yoki ruxsat etilmasa, avtomatik lokal `uploads/` papkaga saqlanadi — bot baribir ishlaydi. Supabase'da public bucket yaratib qo'ysangiz, bulutga yuklanadi.
- Supabase kalitlari `bot.py` boshida (`SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_BUCKET`).
- Web server port: `WEB_PORT` (standart 8000).
- Ma'lumotlar `uy_ishlari.db` faylida saqlanadi, eski bazadan avtomatik migratsiya qilinadi.
