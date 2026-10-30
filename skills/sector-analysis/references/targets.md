# Sektöre göre önerilen backtest hedefleri

Hedef, `panel.csv` kolonlarından biri olmalıdır. Panelde yoksa tablodaki bir sonraki adaya geç.
Ufuk (horizon), ilgili ML kullanımının karar sıklığına göre seçilir.

| Sektör | Birincil hedef (ufuk) | İkincil hedef (ufuk) | Neden |
|---|---|---|---|
| bankacilik | `aofm` (1) | `tufe_yoy` (3) | Fonlama maliyeti kredi/mevduat fiyatlamasını belirler |
| sigorta | `tufe_yoy` (3) | `usdtry_logdiff` (1) | Hasar enflasyonu ve ithal parça maliyeti |
| telekom | `tufe_yoy` (3) | `usdtry_logdiff` (1) | ARPU fiyatlaması enflasyona endeksli |
| otomotiv | `tasit_kredisi_faizi` (1) | `usdtry_logdiff` (1) | Talep kredi koşullarına ve kura duyarlı |
| akaryakit | `usdtry_logdiff` (1) | `tufe_yoy` (3) | Ürün fiyatı kur geçişkenliğiyle belirlenir |

Notlar:
- `log_diff` hedeflerinde (kur) naive modeli geçmek zordur; geçilemiyorsa bunu raporda dürüstçe yaz.
  Bu, kurun kısa vadede tahmin edilemezliğini gösterir.
- `yoy_pct` hedeflerinde ufuk ≥ 3 ise drift ve ridge genelde naive'e yakındır. Mevsimsel naive
  kötüdür, çünkü YoY serisi zaten mevsimsellikten arındırılmıştır.
