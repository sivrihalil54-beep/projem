# Projeyi yerelde çalıştırma ve durdurma

Bu dosya, projeyi hiç bilmeyen biri için yazıldı. Komutları **Terminal** (Linux’ta genelde Ctrl+Alt+T) içinde çalıştırın. Önce proje klasörüne gidin:

```bash
cd /tam/yol/projem
```

*(Kendi bilgisayarınızdaki `projem` klasörünün yolunu yazın.)*

---

## İlk kurulum (bir kez)

1. **Python sanal ortamı ve kütüphaneler**

   ```bash
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

2. **Panel (React) bağımlılıkları**

   ```bash
   cd web
   npm install
   cd ..
   ```

3. **Kökteki tek-komut script’i için `concurrently`**

   ```bash
   npm install
   ```

---

## Hepsini tek yerden başlatmak (API + web panel)

Proje kökünde (`projem` içinde, `web` değil) şunu yazın:

```bash
npm run dev:all
```

- **API (FastAPI):** tarayıcıdan doğrudan denemek için örnek adres: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Panel (Vite):** [http://localhost:5173](http://localhost:5173)

İlk kez kullanıyorsanız önce yukarıdaki **İlk kurulum** adımlarını yapın; aksi halde `npm run dev:all` hata verebilir.

---

## Hepsini durdurmak

`npm run dev:all` komutunu çalıştırdığınız **aynı terminal penceresinde**:

- **Ctrl+C** tuşlarına bir kez basın.

Bu, hem API hem de panel süreçlerini durdurur (arka planda ekstra bir şey açmadıysanız başka işlem yapmanız gerekmez).

---

## İsterseniz iki ayrı terminal (alternatif)

Bazen hataları ayırmak için iki pencere kullanmak kolaydır.

**Terminal 1 — API:**

```bash
./venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — Panel:**

```bash
cd web
npm run dev
```

**Durdurmak:** Her iki terminalde sırayla **Ctrl+C**.

---

## Bot giriş adımını çalıştırmak (ayrı komut)

Panelden en az bir **aktif profil** kaydettikten sonra, proje kökünde:

```bash
./venv/bin/python run_login_step.py
```

*(Playwright tarayıcısı açılır; iş bitince terminalde Enter ile çıkabilirsiniz.)*

**Panelden başlatma:** `npm run dev:all` çalışırken, tarayıcıda listede ilgili profilin yanındaki **Botu baslat** düğmesine tıklayın. Seçtiğiniz profil için `run_login_step.py` arka planda çalışır; çıktı `backend/data/bot_run.log` dosyasına yazılır. Aynı anda yalnızca bir bot çalışması kısıtlıdır.

---

## Sık sorulan kısa notlar

| Sorun | Ne yapmalı |
|--------|------------|
| `venv/bin/python: No such file` | Önce `python3 -m venv venv` ile sanal ortamı oluşturun. |
| `npm: command not found` | Node.js kurun ([https://nodejs.org](https://nodejs.org)). |
| Panel API’ye bağlanamıyor | Önce `npm run dev:all` ile API’nin de çalıştığından emin olun; panel `/api` isteklerini 8000 portuna yönlendirir. |

---

## Özet

| Ne istiyorsunuz? | Komut (proje kökünde) | Durdurma |
|------------------|----------------------|----------|
| API + panel birlikte | `npm run dev:all` | Aynı terminalde **Ctrl+C** |
