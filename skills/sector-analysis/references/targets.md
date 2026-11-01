# Sektöre göre önerilen backtest hedefleri

Hedef, `panel.csv` kolonlarından biri olmalıdır. Panelde yoksa tablodaki bir sonraki adaya geç.
Ufuk (horizon), ilgili ML kullanımının karar sıklığına göre seçilir.

| Sektör | Birincil hedef (ufuk) | İkincil hedef (ufuk) | Neden |
|---|---|---|---|
| bankacilik | `aofm` (1, `--anchor aofm_son`) | `tufe_yoy` (3) | Fonlama maliyeti kredi ve mevduat fiyatlamasını belirler |
| sigorta | `tufe_yoy` (3) | `usdtry_logdiff` (1) | Hasar enflasyonu ve ithal parça maliyeti |
| telekom | `tufe_yoy` (3) | `usdtry_logdiff` (1) | ARPU fiyatlaması enflasyona endeksli |
| otomotiv | `tasit_kredisi_faizi` (1); bağlı değilse `aofm` (1, `--anchor aofm_son`) | `usdtry_logdiff` (1) | Talep kredi koşullarına ve kura duyarlı |
| akaryakit | `usdtry_logdiff` (1) | `tufe_yoy` (3) | Ürün fiyatı kur geçişkenliğiyle belirlenir |

Notlar:
- `log_diff` hedeflerinde (kur) naive'i geçmek zordur; geçilemiyorsa bunu raporda açıkça belirt. Bu sonuç,
  kurun kısa vadede tahmin edilemediğini gösterir.
- `yoy_pct` hedeflerinde ufuk ≥ 3 ise drift ve ridge genellikle naive'e yakın kalır. Seasonal naive zayıftır,
  çünkü YoY serisi mevsimsellikten zaten arınmıştır.
- Aylık ortalama hedeflerde (aofm, haftalık faizler) panelde `_son` kolonu varsa `--anchor` ver. Aksi halde
  düz naive benchmark'ı yanıltıcı derecede düşük kalır.
