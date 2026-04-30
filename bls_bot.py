import cloudscraper
import time
from playwright.sync_api import sync_playwright

# --- AYARLAR ---
# Hangi şehirdeysen o linki koy (Örn: İstanbul)
URL_GIRIS = "https://turkey.blsspainvisa.com/istanbul/index.php"
URL_RANDEVU = "https://turkey.blsspainvisa.com/istanbul/book_appointment.php"

def tarayiciyi_firlat():
    """Slot bulunduğu an gerçek Chrome'u açar"""
    with sync_playwright() as p:
        print("🔥 SLOT BULUNDU! Tarayıcı başlatılıyor...")
        # Bilgisayarındaki yüklü Chrome'u da kullanabiliriz ama şimdilik standart:
        browser = p.chromium.launch(headless=False) 
        page = browser.new_page()
        page.goto(URL_GIRIS)
        
        # Burada senin manuel müdahalen veya otomatik giriş kodların olacak
        print("⚠️  Tarayıcı açık! Lütfen formu doldurmaya başla.")
        
        # Tarayıcının hemen kapanmaması için uzun süreli bekleme
        time.sleep(3000)

def kontrol_dongusu():
    """Arka planda sessizce sorgu atar"""
    # Cloudflare ve bot korumalarını aşmak için özel tarayıcı taklitçisi
    scraper = cloudscraper.create_scraper()
    
    print("🚀 BLS İspanya Botu Dinlemede...")
    
    while True:
        try:
            response = scraper.get(URL_RANDEVU)
            
            # BLS'de slot yoksa genelde 'No Appointment' veya 'Not Available' geçer
            if "No Appointment" in response.text or "Not Available" in response.text:
                print(f"[{time.strftime('%H:%M:%S')}] Slot yok, tekrar denenecek...")
            else:
                # Sayfa içeriği değiştiyse slot gelmiş olabilir!
                tarayiciyi_firlat()
                break
            
            # Çok hızlı sorgu IP ban yedirir, 60 saniye güvenlidir
            time.sleep(60) 
            
        except Exception as e:
            print(f"Bağlantı hatası: {e}")
            time.sleep(10)

if __name__ == "__main__":
    kontrol_dongusu()
    import cloudscraper
import time
import random # Rastgele bekleme için

def kontrol_dongusu():
    # 1. Kimlik Tanımlama
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    
    # 2. Proxy Tanımlama (Şimdilik boş, gerekince doldururuz)
    # proxies = {"http": "http://kullanici:sifre@proxy_ip:port", "https": "http://kullanici:sifre@proxy_ip:port"}
    
    scraper = cloudscraper.create_scraper()
    print("🚀 BLS İspanya Botu Dinlemede...")
    
    while True:
        try:
            # İsteği atıyoruz
            response = scraper.get(URL_RANDEVU, headers=headers, timeout=20)
            
            # BLS'nin hata sayfasını veya 'No Appointment' yazısını kontrol et
            if "No Appointment" in response.text or "Not Available" in response.text:
                bekleme = random.randint(45, 75) # 45 ile 75 saniye arası rastgele bekle
                print(f"[{time.strftime('%H:%M:%S')}] Slot yok. {bekleme} sn sonra tekrar denenecek...")
                time.sleep(bekleme)
            else:
                # Ekranda beklediğimiz olumsuz yazılar yoksa slot gelmiş olabilir!
                print("🔥 HAREKETLİLİK SEZİLDİ! Sayfa içeriği değişti.")
                tarayiciyi_firlat()
                break
                
        except Exception as e:
            print(f"❌ Bağlantı hatası (Muhtemelen IP engeli): {e}")
            time.sleep(30) # Hata alınca biraz daha uzun bekle
            