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

## ☁️ Vercel'ga joylash (asosiy usul)

Vercel'da bot **webhook** rejimida ishlaydi: kirish nuqtasi `api/index.py`, ma'lumotlar Postgres'da.

1. **Postgres oling.** Supabase → Project Settings → Database → *Connection string* → **Transaction pooler** (port `6543`) manzilini nusxalang.
2. [vercel.com/new](https://vercel.com/new) → shu GitHub repo'ni import qiling. Framework: **Other**.
3. **Environment Variables**:

```
BOT_TOKEN        = BotFather bergan token
DATABASE_URL     = postgresql://postgres.xxxx:PAROL@aws-0-....pooler.supabase.com:6543/postgres
SUPABASE_URL     = https://xxxx.supabase.co
SUPABASE_KEY     = supabase kalit
SUPABASE_BUCKET  = uy-ishlari
CRON_SECRET      = istalgan-uzun-maxfiy-satr   (eslatmalar uchun shart)
```

4. **Deploy** bosing, so'ng brauzerda bir marta oching: `https://LOYIHANGIZ.vercel.app/api/setup`.
   Javobda `"ok": true` chiqsa, webhook ulandi. Botga `/start` yozing.
5. Birinchi bo'lib `/dev` yozib **developer** bo'ling.

Mini App manzili avtomatik Vercel domeni bo'ladi (`WEBAPP_URL` shart emas).

**Eslatmalar (Vercel Cron):** `/api/cron/morning` (07:30 jadval + 08:00 vazifa eslatmalari) va
`/api/cron/evening` (19:00 dars eslatmasi + 19:30 ertangi jadval). Bepul (Hobby) tarifda cron
belgilangan soat ichida istalgan daqiqada ishlaydi.

⚠️ Vercel ishlayotganda kompyuterda `py bot.py` ni **ishga tushirmang** — u webhook'ni o'chiradi.
Shunday bo'lsa, `/api/setup` ni qayta oching.

## 💻 Lokal ishga tushirish (test uchun)

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

`DATABASE_URL` bo'lmasa ma'lumotlar `uy_ishlari.db` (SQLite) faylida saqlanadi. Supabase ixtiyoriy — bo'lmasa rasmlar `uploads/` papkasiga saqlanadi.

```
py bot.py
```

Mini App lokalda HTTPS tunnel talab qiladi (`cloudflared tunnel --url http://localhost:8000`), keyin `.env` ga `WEBAPP_URL=https://tunnel-manzilingiz` qo'shing.

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
- Lokal web server porti: `WEB_PORT` (standart 8000).
- Ma'lumotlar Vercel'da Postgres'da (`DATABASE_URL`), lokalda `uy_ishlari.db` faylida.
