# วิธี Deploy เว็บร้านค้าบน Railway

## สิ่งที่ต้องมี
- บัญชี GitHub (สมัครฟรีที่ github.com)
- บัญชี Railway (สมัครฟรีที่ railway.app — ใช้ GitHub login ได้เลย)

---

## ขั้นตอนที่ 1 — อัพโหลดโค้ดขึ้น GitHub

1. ไปที่ **github.com** → กด **New repository**
2. ตั้งชื่อ เช่น `khaosarn-shop` → กด **Create repository**
3. เปิด **Command Prompt** ใน folder `ร้านข้าวสาร` แล้วพิมพ์:

```
git init
git add .
git commit -m "first deploy"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/khaosarn-shop.git
git push -u origin main
```

> แทน `YOUR_USERNAME` ด้วย username GitHub ของคุณ

---

## ขั้นตอนที่ 2 — สร้าง Project บน Railway

1. ไปที่ **railway.app** → กด **New Project**
2. เลือก **Deploy from GitHub repo**
3. เลือก repo `khaosarn-shop`
4. Railway จะ detect Python อัตโนมัติและ deploy ทันที

---

## ขั้นตอนที่ 3 — เพิ่ม Persistent Volume (สำคัญมาก!)

> ถ้าไม่ทำขั้นตอนนี้ ข้อมูลจะหายทุกครั้งที่ deploy ใหม่

1. ใน Railway dashboard → คลิกที่ service ของคุณ
2. ไปที่แท็บ **Volumes**
3. กด **Add Volume**
4. ตั้งค่า:
   - Mount Path: `/data`
5. กด **Add**

---

## ขั้นตอนที่ 4 — ตั้งค่า Environment Variables

ไปที่แท็บ **Variables** แล้วเพิ่ม:

| Key | Value |
|-----|-------|
| `DATA_DIR` | `/data` |
| `ADMIN_PASSWORD` | รหัสผ่าน admin เริ่มต้น (เปลี่ยนได้ทีหลัง) |

---

## ขั้นตอนที่ 5 — รับ URL สาธารณะ

1. ไปที่แท็บ **Settings** → **Networking**
2. กด **Generate Domain**
3. จะได้ URL แบบ `https://khaosarn-shop-xxxx.railway.app`

---

## อัพเดทโค้ดในอนาคต

แก้ไฟล์ในเครื่อง แล้วพิมพ์:
```
git add .
git commit -m "update"
git push
```
Railway จะ deploy ใหม่อัตโนมัติ

---

## ค่าใช้จ่าย

- **Hobby plan**: ~$5/เดือน (รวม volume แล้ว)
- จ่ายตามการใช้งานจริง ไม่มีสัญญา

---

## หมายเหตุ

- รูปภาพที่อัพโหลดและฐานข้อมูลจะถูกเก็บใน `/data` (persistent volume)
- ไม่ต้อง copy shop.db ขึ้น GitHub — จะสร้างใหม่อัตโนมัติ
- Username/password เริ่มต้นคือ `admin` / ค่าจาก `ADMIN_PASSWORD`
