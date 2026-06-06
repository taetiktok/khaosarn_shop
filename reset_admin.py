"""
reset_admin.py  —  กู้คืนรหัสผ่าน superadmin ฉุกเฉิน
วิธีใช้: เปิด Command Prompt ใน folder ร้านข้าวสาร แล้วพิมพ์
         python reset_admin.py
"""
import sqlite3
import hashlib
import secrets
import os
import sys
import getpass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")

def hash_pw(password: str) -> str:
    salt = secrets.token_bytes(16)
    key  = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 260_000)
    return salt.hex() + ':' + key.hex()

def main():
    print("=" * 40)
    print("  รีเซ็ตรหัสผ่าน Admin — ร้านข้าวสาร")
    print("=" * 40)

    if not os.path.exists(DB_PATH):
        print(f"\n❌ ไม่พบฐานข้อมูล: {DB_PATH}")
        print("   ตรวจสอบว่าไฟล์นี้อยู่ใน folder เดียวกับ shop_server.py")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    users = conn.execute(
        "SELECT id, username, role FROM users ORDER BY role DESC, id"
    ).fetchall()

    if not users:
        print("\n❌ ไม่พบผู้ใช้ในระบบ")
        sys.exit(1)

    print("\n👤 รายชื่อผู้ใช้ทั้งหมด:")
    for u in users:
        badge = "⭐ Super Admin" if u['role'] == 'superadmin' else "   Editor"
        print(f"  [{u['id']}] {u['username']:20s}  {badge}")

    print()
    uid_str = input("ใส่ ID ของ user ที่ต้องการรีเซ็ตรหัส: ").strip()
    try:
        uid = int(uid_str)
    except ValueError:
        print("❌ ID ไม่ถูกต้อง"); sys.exit(1)

    target = next((u for u in users if u['id'] == uid), None)
    if not target:
        print(f"❌ ไม่พบ user ID {uid}"); sys.exit(1)

    print(f"\n🔐 กำลังรีเซ็ตรหัสผ่านของ: {target['username']} ({target['role']})")

    try:
        new_pw = getpass.getpass("รหัสผ่านใหม่ (ไม่แสดงบนหน้าจอ): ")
        if len(new_pw) < 6:
            print("❌ รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร"); sys.exit(1)
        confirm = getpass.getpass("ยืนยันรหัสผ่านใหม่อีกครั้ง: ")
    except KeyboardInterrupt:
        print("\n\nยกเลิก"); sys.exit(0)

    if new_pw != confirm:
        print("❌ รหัสผ่านทั้งสองครั้งไม่ตรงกัน"); sys.exit(1)

    hashed = hash_pw(new_pw)
    conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (hashed, uid))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()

    print(f"\n✅ รีเซ็ตรหัสผ่านของ '{target['username']}' สำเร็จ!")
    print("   (session เดิมทั้งหมดถูกยกเลิกแล้ว)")
    print("\n💡 เริ่มเซิร์ฟเวอร์ใหม่แล้ว login ด้วยรหัสใหม่ได้เลยครับ")

if __name__ == "__main__":
    main()
