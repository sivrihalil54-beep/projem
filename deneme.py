import requests
print("--- Bot Testi Başladı ---")
try:
    r = requests.get("https://www.google.com", timeout=5)
    print(f"Bağlantı Başarılı! Durum: {r.status_code}")
except Exception as e:
    print(f"Hata: {e}")
