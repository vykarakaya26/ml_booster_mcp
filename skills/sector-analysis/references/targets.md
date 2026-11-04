# Sektöre göre önerilen hedefler

Hedef, `panel.csv` kolonlarından biri olmalıdır. Panelde yoksa tablodaki bir sonraki adaya geç.
Horizon, ilgili ML kullanımının karar sıklığına göre seçilir ve `feature-engineering` taramasında kullanılır.

| Sektör | Birincil hedef (horizon) | İkincil hedef (horizon) | Neden |
|---|---|---|---|
| bankacilik | `aofm` (1) | `tufe_yoy` (3) | Fonlama maliyeti kredi ve mevduat fiyatlamasını belirler |
| sigorta | `tufe_yoy` (3) | `usdtry_logdiff` (1) | Hasar enflasyonu ve ithal parça maliyeti |
| telekom | `tufe_yoy` (3) | `usdtry_logdiff` (1) | ARPU fiyatlaması enflasyona endeksli |
| otomotiv | `tasit_kredisi_faizi` (1); bağlı değilse `aofm` (1) | `usdtry_logdiff` (1) | Talep kredi koşullarına ve kura duyarlı |
| akaryakit | `usdtry_logdiff` (1) | `tufe_yoy` (3) | Ürün fiyatı kur geçişkenliğiyle belirlenir |

Notlar:
- Aylık ortalama hedeflerde (aofm, haftalık faizler) aynı serinin `_son` kolonu ay sonu değerini taşır. Bu kolon
  hedefle neredeyse aynı bilgiyi içerdiği için yorumlarken bunu belirt.
- Yıllık değişim (`yoy_pct`) serileri mevsimsellikten büyük ölçüde arınmıştır; bu hedeflerde mevsimsel feature'ların
  katkısı sınırlıdır.
