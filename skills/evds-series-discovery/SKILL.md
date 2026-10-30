---
name: evds-series-discovery
description: TCMB EVDS'de doğru ve güncel seri kodunu bulur, canlı doğrular ve bir sektör feature'ına bağlar (kullanıcının yerel kataloğuna yazar). Haritada "kesif_bekliyor" görünen feature'ları (kredi faizi, mevduat faizi, tüketici güveni, Piyasa Katılımcıları Anketi beklentisi…) doldurur. Durmuş ya da arşive taşınmış kodları (ör. TP.FG.J0 → TP.TUKFIY2025.GENEL) değiştirmek için de kullanılır. Kullanıcı "şu verinin EVDS kodu ne", "X serisini bul", "feature ekle", "panelde eksik feature var", "seri durmuş/400 veriyor", "EVDS'de ara" dediğinde ya da yeni bir makro değişkeni panele eklemek istediğinde kullan. Also: "find EVDS series code".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
---

# EVDS seri keşfi

EVDS'de 680'den fazla veri grubu var ve kodlar zaman içinde değişiyor: arşiv grupları, `.YTL`
takma adları, baz yılı değişiklikleri. Yanlış ya da durmuş bir kod, panele sessizce eski veri
sokar. Bu skill'in işi doğru kodu bulmak ve **canlı doğrulamadan** hiçbir kodu bağlamamaktır.

## Akış

```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
"$MLB" map show --sector <sektör>                       # kesif_bekliyor feature'ları ve ipuçları
"$MLB" catalog search "<anahtar kelimeler>" --limit 15   # veri grupları (Türkçe karakter duyarsız)
"$MLB" catalog series --datagroup <bie_...>             # gruptaki seri kodları, frekans, tarih aralığı
"$MLB" fetch --series <KOD> --start 2024-01             # gerekirse değerlere göz at (birim, ölçek)
"$MLB" catalog set-feature --sector <s> --feature <f> --series <KOD> \
      --name "<açıklama>" --datagroup <bie_...> [--lag <gün>] [--kind level|flow]
"$MLB" build-panel --sector <s> --start 2012-01          # feature'ın panele girdiğini doğrula
```

### 1. Ara
- Feature'ın `discover_hint` alanından ve `references/search-tips.md` dosyasındaki eş anlamlılardan
  2–3 farklı sorgu dene. EVDS adları resmi ve uzundur ("Kredi Faiz Oranları (Akım)").
- **Arşiv grupları** (`[arşiv]` etiketi, eski bitiş tarihi) asla seçilmez. Güncel karşılığını ara.

### 2. Aday seç
Bir kodu seçmeden önce şunları kontrol et:
- **Güncel mi**: bitiş tarihi frekansa göre yakın olmalı. Aylık için son 2–3 ay, haftalık için son
  2–3 hafta.
- **Doğru kavram mı**: akım ya da stok, TL ya da döviz, toplam ya da alt kırılım. Feature adındaki
  niyete uymalı. Örneğin "taşıt kredisi faizi" için TL akım faizi `TP.KTF11` doğrudur, Euro
  versiyonu değildir.
- **Frekans**: haftalık ve günlük seriler aylığa ortalama alınarak indirgenir (seviye için doğru).
  Toplam/akım değişkenleri için `--kind flow` ver.
- Birden fazla makul aday varsa en genel toplamı seç ve diğerlerini kullanıcıya alternatif olarak
  söyle.

### 3. Bağla (canlı doğrulama)
`catalog set-feature` kodu canlı çeker ve şu durumlarda reddeder:
- Son 2 yılda veri yoksa.
- Son gözlem frekansına göre çok eskiyse.

Yayın gecikmesini `references/search-tips.md` dosyasındaki tablodan bul ve `--lag` ile ver. Ay içinde
yayımlanan seriler için (ör. güven endeksleri) bu değer 0'dır. Bilmiyorsan verme: varsayılan
muhafazakârdır (aylık 30 gün → 2 ay kayma) ve look-ahead'i önler, ama feature'ı geç gösterir. Bu
durumda raporda belirt.
Override `~/.mlbooster/catalog/overrides.yaml` dosyasına yazılır, paket dosyaları değişmez.

### Yeni feature eklemek
Kullanıcı sektörde olmayan bir değişken istiyorsa (ör. otomotiv için binek üretimi ya da ithalat
hedef adayı) `--create` ver. Sık seriler için dönüşüm ve agregasyonu da belirt:
`--create --transform yoy_pct --kind flow` (akım: aylık toplam) ya da
`--create --transform level --agg last`. Override'da sektörde olmayan bir feature adı geçerse hata
alınır; sessizce yok sayılmaz.

### 4. Teslim
Her bağlanan feature için şunları raporla: kod, veri grubu, frekans, son gözlem, gecikme ve neden
bu kodun seçildiği (1 cümle). Bulunamayanlar için denenen sorguları ve en yakın adayları yaz.

## Repo katkısı
Kullanıcı `ml_booster_mcp` reposunun içinde çalışıyorsa ve bulunan kodun herkese gitmesini
istiyorsa, override'ı `src/mlbooster/catalog_data/sectors/<sektör>.yaml` (feature `series`) ve
`sources/evds3.yaml` (tohum seri) dosyalarına taşı. Ardından `pytest` ve `pytest -m live` çalıştır.
