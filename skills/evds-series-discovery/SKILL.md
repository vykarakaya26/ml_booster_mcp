---
name: evds-series-discovery
description: TCMB EVDS'de doğru ve güncel seri kodunu bulur, canlı doğrular ve bir sektör feature'ına bağlar (kullanıcının yerel kataloğuna override olarak yazar). Haritada "pending_discovery" durumunda görünen feature'ları (kredi faizi, mevduat faizi, tüketici güveni, Piyasa Katılımcıları Anketi beklentisi…) doldurur. Yayımı durdurulmuş ya da arşive taşınmış kodları (ör. TP.FG.J0 → TP.TUKFIY2025.GENEL) değiştirmek için de kullanılır. Kullanıcı "şu verinin EVDS kodu ne", "X serisini bul", "feature ekle", "panelde eksik feature var", "seri durmuş/400 veriyor", "EVDS'de ara" dediğinde ya da panele yeni bir makro değişken eklemek istediğinde kullan. Also: "find EVDS series code".
allowed-tools:
  - Bash(${CLAUDE_SKILL_DIR}/../_shared/bin/mlb *)
---

# EVDS seri keşfi

EVDS'de 680'den fazla veri grubu var ve kodlar zamanla değişiyor: arşiv grupları, `.YTL` takma adları, baz
yılı değişiklikleri. Yanlış ya da stale bir kod hiçbir hata üretmeden panele eski veri taşır. Bu skill'in işi
doğru kodu bulmak ve **canlı doğrulamadan** hiçbir kodu bağlamamaktır.

## Akış

```bash
MLB="${CLAUDE_SKILL_DIR}/../_shared/bin/mlb"
"$MLB" map show --sector <sektör>                       # pending_discovery feature'lar ve ipuçları
"$MLB" catalog search "<anahtar kelimeler>" --limit 15   # veri grupları (Türkçe karakter duyarsız)
"$MLB" catalog series --datagroup <bie_...>             # gruptaki seri kodları, frekans, tarih aralığı
"$MLB" fetch --series <KOD> --start 2024-01             # gerekirse değerleri incele (birim, ölçek)
"$MLB" catalog set-feature --sector <s> --feature <f> --series <KOD> \
      --name "<açıklama>" --datagroup <bie_...> [--lag <gün>] [--kind level|flow]
"$MLB" build-panel --sector <s> --start 2012-01          # feature'ın panele girdiğini doğrula
```

### 1. Ara
- Feature'ın `discover_hint` alanından ve `references/search-tips.md` dosyasındaki eş anlamlılardan 2–3
  farklı sorgu dene. EVDS adları resmi ve uzundur ("Kredi Faiz Oranları (Akım)").
- **Arşiv grupları** (`[arşiv]` etiketi, eski bitiş tarihi) asla seçilmez. Güncel karşılığını ara.

### 2. Aday seç
Bir kodu seçmeden önce şunları kontrol et:
- **Güncel mi**: bitiş tarihi frekansa göre yakın olmalı. Aylık seride son 2–3 ay, haftalık seride son
  2–3 hafta.
- **Doğru kavram mı**: akım ya da stok, TL ya da döviz, toplam ya da alt kırılım. Feature adındaki niyetle
  örtüşmeli. Örneğin "taşıt kredisi faizi" için doğru seri TL akım faizi `TP.KTF11`'dir, Euro versiyonu değil.
- **Frekans**: haftalık ve günlük seriler ortalama alınarak aylığa indirgenir (seviye için doğru yöntem).
  Toplam/akım değişkenlerinde `--kind flow` ver.
- Birden fazla makul aday varsa en genel toplamı seç, diğerlerini kullanıcıya alternatif olarak sun.

### 3. Bağla (canlı doğrulama)
`catalog set-feature` kodu canlı çeker ve şu durumlarda reddeder:
- Son 2 yılda veri yoksa.
- Son gözlem frekansa göre beklenenden eskiyse.

Publication lag'i `references/search-tips.md` dosyasındaki tablodan bul ve `--lag` ile ver. Aynı ay içinde
yayımlanan serilerde (ör. güven endeksleri) bu değer 0'dır. Lag bilinmiyorsa verme: varsayılan değer
ihtiyatlıdır (aylık 30 gün → 2 ay kayma) ve look-ahead'i önler, ancak feature'ı gerektiğinden geç gösterir.
Bu durumu raporda belirt.
Override `~/.mlbooster/catalog/overrides.yaml` dosyasına yazılır; paket dosyaları değişmez.

### Yeni feature eklemek
Kullanıcı sektörde olmayan bir değişken istiyorsa (ör. otomotiv için binek üretimi ya da ithalat gibi bir
hedef adayı) `--create` ver. Yüksek frekanslı serilerde dönüşümü ve agregasyonu da belirt:
`--create --transform yoy_pct --kind flow` (akım: aylık toplam) ya da `--create --transform level --agg last`.
Override'da sektörde olmayan bir feature adı geçerse hata verilir; kayıt sessizce yok sayılmaz.

### 4. Teslim
Bağlanan her feature için şunları raporla: kod, veri grubu, frekans, son gözlem, lag ve bu kodun neden
seçildiği (1 cümle). Bulunamayanlar için denenen sorguları ve en yakın adayları yaz.
Yanıtı akıcı, nesnel ve kurumsal Türkçeyle yaz. Teknik terimleri İngilizce bırak ve eki kesme işaretiyle
ekle ("feature'ı", "override'a", "look-ahead'i"); terimlere Türkçe karşılık uydurma. EVDS seri ve grup
adlarını olduğu gibi aktar.

## Repo katkısı
Kullanıcı `ml_booster_mcp` reposunun içinde çalışıyorsa ve bulunan kodun herkese ulaşmasını istiyorsa,
override'ı `src/mlbooster/catalog_data/sectors/<sektör>.yaml` (feature `series`) ve `sources/evds3.yaml`
(seed seri) dosyalarına taşı. Ardından `pytest` ve `pytest -m live` çalıştır.

## Bütünsel akıştaki yeri
Bağlanan seriler sektör tariflerinin (ör. kredi − mevduat faizi farkı, reel taşıt kredisi faizi) girdisi
olabilir; bağlantıdan sonra `feature-engineering` bu tarifleri otomatik hesaplar. Akış:
`${CLAUDE_SKILL_DIR}/../_shared/references/workflow.md`.
