// Glossary: one Turkish sentence per term used in the panel; English technical terms stay in English.
// Keys are referenced by the "?" buttons; the drawer lists GLOSSARY and MODEL_INFO together, sorted by term.
export const GLOSSARY = {
  anchor: {
    term: "Anchor",
    text: "Hedefin daha erken bilinen ya da daha güncel bir sürümü (ör. aylık ortalama faiz için ay sonu faizi); seçilirse Anchor naive modeli de backtest'e eklenir.",
  },
  backtest: {
    term: "Backtest",
    text: "Modelin geçmişteki her origin'de yalnızca o tarihe kadar yayımlanmış veriyle yeniden kurulup sonraki dönemi tahmin etmesi ve tahminin gerçekleşenle karşılaştırılması.",
  },
  baseline: {
    term: "Baseline",
    text: "Bir ML modelinin katkı sağladığından söz edebilmek için geçmesi gereken basit yöntemin hata düzeyi; bu ekranda backtest'te en düşük MAE'yi veren model.",
  },
  collinearity: {
    term: "Collinearity (yüksek korelasyon)",
    text: "İki feature'ın neredeyse aynı bilgiyi taşıması; ikisi birlikte kullanıldığında regresyon katsayıları istikrarsızlaşır, biri çıkarılabilir.",
  },
  coverage: {
    term: "Coverage (tahmin aralığı)",
    text: "Gerçekleşen değerin %80 tahmin aralığının içinde kalma oranı; %80'e yakın olmalıdır, çok düşükse aralıklar dar, çok yüksekse gereğinden geniştir.",
  },
  coverage_share: {
    term: "Coverage (pazar payı)",
    text: "Tabloda listelenen oyuncuların toplamının pazar toplamına oranı; %80'in altındaysa paylar pazarın yalnızca bir bölümünü temsil eder.",
  },
  cr_k: {
    term: "CR3 / CR5",
    text: "En büyük 3 ya da 5 oyuncunun toplam pazar payı; HHI tüm payların dağılımına, CR-k yalnızca en büyük oyunculara bakar.",
  },
  diff: {
    term: "Aylık fark",
    text: "Bir önceki aya göre değişim, serinin kendi biriminde; faiz serilerinde yüzde puan cinsindendir.",
  },
  equivalent_players: {
    term: "Eşdeğer oyuncu sayısı",
    text: "10.000 / HHI, yani aynı yoğunlaşmayı üretecek eşit paylı oyuncu sayısı; HHI 2.500 ise pazar dört eşit oyunculu bir pazar kadar yoğundur.",
  },
  feature: {
    term: "Feature",
    text: "Modelin tahmin yaparken kullandığı girdi serisi; örneğin kredi hacmi modelinde politika faizi ve enflasyon birer feature'dır.",
  },
  heavy_tail: {
    term: "Heavy tail",
    text: "Dağılımın uçlarında normal dağılıma göre daha fazla gözlem bulunması; şok dönemlerinin sık yaşandığını gösterir.",
  },
  hhi: {
    term: "HHI",
    text: "Herfindahl–Hirschman endeksi: yüzde payların karelerinin toplamı (0–10.000); AB Yatay Birleşme Rehberi'ne göre 1.000'in altı düşük, 1.000–2.000 orta, 2.000'in üstü yüksek yoğunlaşmadır.",
  },
  horizon: {
    term: "Horizon (tahmin ufku)",
    text: "Kaç dönem ilerisinin tahmin edildiği; 1, bir sonraki ayın tahmini demektir.",
  },
  lag: {
    term: "Lag",
    text: "Bir serinin geçmiş dönemdeki değeri; örneğin lag 3, üç dönem önceki değerdir.",
  },
  level: {
    term: "Düzey",
    text: "Serinin dönüşüm uygulanmamış kendi değeri; örneğin politika faizi için %37,0.",
  },
  log_diff: {
    term: "Aylık değişim (log)",
    text: "Bir önceki aya göre logaritmik değişim; küçük değerlerde yüzde değişime yakındır (0,02 yaklaşık %2 artış, −0,03 yaklaşık %3 düşüş demektir).",
  },
  look_ahead: {
    term: "Look-ahead bias",
    text: "Modelin, tahmin tarihinde henüz yayımlanmamış bir bilgiyle eğitilmesi ya da sınanması; backtest başarısını olduğundan yüksek gösterir.",
  },
  mae: {
    term: "MAE",
    text: "Mean absolute error: tahminlerin gerçekleşenden ortalama mutlak sapması, hedefin biriminde (faiz için 0,4, ortalama 0,4 puan sapma demektir); düşük olan daha iyidir.",
  },
  market_share: {
    term: "Pazar payı",
    text: "Bir oyuncunun satış, prim ya da abone gibi değerinin pazar toplamına oranı; toplam TOPLAM (ya da TOTAL) satırlarıyla verilmezse listelenen oyuncuların toplamı kullanılır.",
  },
  mase: {
    term: "MASE",
    text: "Mean absolute scaled error: modelin MAE'sinin naive forecast'ın MAE'sine oranı; 1'in altı naive'den iyi demektir.",
  },
  min_train: {
    term: "Minimum eğitim dönemi",
    text: "İlk tahminden önce modelin eğitildiği en az dönem sayısı.",
  },
  missing: {
    term: "Missing value",
    text: "Panelde değeri olmayan dönemler; oranı yüksekse model bu dönemleri atmak ya da imputation ile doldurmak zorunda kalır.",
  },
  naive_beat: {
    term: "Naive'e göre iyileşme",
    text: "Baseline modelin MAE'sinin naive forecast'a göre yüzde kaç düşük olduğu; bir ML modelinin bu düzeyi geçmesi beklenir.",
  },
  origin: {
    term: "Origin",
    text: "Backtest'te modelin kurulduğu ve tahminin yapıldığı tarih; her origin'de yalnızca o tarihe kadar yayımlanmış veri kullanılır.",
  },
  outlier: {
    term: "Outlier",
    text: "Medyan ve MAD'e göre serinin olağan dağılımından çok uzak kalan gözlem; gerçek bir şok da veri hatası da olabilir, incelenmesi gerekir.",
  },
  panel: {
    term: "Panel",
    text: "Her satırı bir dönem, her sütunu bir feature olan ve ML modellerinin doğrudan eğitildiği ortak takvimli tablo.",
  },
  pinball: {
    term: "Pinball loss",
    text: "Quantile tahminlerinin isabetini ve aralık genişliğini birlikte ölçen kayıp; düşük olan daha iyidir.",
  },
  point_in_time: {
    term: "Point-in-time",
    text: "Her değerin gerçekte yayımlandığı aya yazılması; Mart enflasyonu Nisan başında açıklandığı için panelde Nisan satırında yer alır ve look-ahead bias oluşmaz.",
  },
  pp: {
    term: "Yüzde ve yüzde puan",
    text: "Faiz %40'tan %45'e çıktığında artış 5 yüzde puan, oransal olarak ise %12,5'tir; raporlarda ikisi ayrı tutulur.",
  },
  publication_lag: {
    term: "Yayın gecikmesi (publication lag)",
    text: "Bir dönemin verisinin, dönem bittikten kaç gün sonra açıklandığı; panel değerleri bu gecikme kadar ileri kaydırır.",
  },
  real: {
    term: "Reel değer",
    text: "TÜFE ile enflasyondan arındırılmış değer; nominal artışın ne kadarının fiyat artışından geldiğini ayırır.",
  },
  regime: {
    term: "Rejim",
    text: "Türkiye ekonomisinde koşulların belirgin biçimde değiştiği dönemler (2021 sonu kur şoku, 2023 sonrası sıkılaştırma); backtest sonuçları her rejim için ayrıca verilir.",
  },
  rmse: {
    term: "RMSE",
    text: "Root mean squared error: büyük hataları daha ağır cezalandırır; MAE'den belirgin biçimde büyükse model zaman zaman büyük hata yapıyordur.",
  },
  share_trend: {
    term: "Pay trendi",
    text: "Seçilen penceredeki dönemlik paylara uydurulan doğrusal trend; eğim en az 0,2 puan/yıl ve |t| ≥ 2 ise oyuncu anlamlı biçimde pay kazanıyor ya da kaybediyor sayılır.",
  },
  shift_share: {
    term: "Shift-share",
    text: "Odak oyuncunun toplam pay değişimini segment içi pay etkisi ve segment karması etkisi olarak ikiye ayırır; iki etkinin toplamı değişime eşittir.",
  },
  smape: {
    term: "sMAPE",
    text: "Symmetric mean absolute percentage error: yüzde cinsinden simetrik hata; seri sıfıra yakın değerler aldığında yanıltıcı olabilir.",
  },
  stale: {
    term: "Stale seri",
    text: "Son gözlemi beklenenden eski olan seri; kaynak yayımı durdurmuş ya da seri kodunu değiştirmiş olabilir.",
  },
  stationarity: {
    term: "Stationarity",
    text: "Serinin ortalama ve varyansının zaman içinde sabit kalması; non-stationary seriler genellikle fark ya da yıllık değişim alınarak modellenir.",
  },
  structural_break: {
    term: "Structural break",
    text: "Serinin ortalamasının bir tarihten itibaren kalıcı biçimde değiştiği nokta; bilinen bir olayla (kur şoku, politika değişikliği) eşleşiyorsa olayın adı gösterilir.",
  },
  substitution: {
    term: "Substitution correlation",
    text: "Odak oyuncunun dönemlik pay değişimleriyle bir rakibinkinin korelasyonu; güçlü negatif değer kazanılan payın o rakipten geldiğine işaret eder (en az 12 gözlem ve |r| ≥ 2/√n koşuluyla; nedensellik kanıtı değildir).",
  },
  ttm: {
    term: "TTM (son 12 ay toplamı)",
    text: "Akım verilerde payın son 12 ayın toplamı üzerinden hesaplanması; mevsimselliği giderir ve en az iki yıllık aylık ya da çeyreklik akım verisinde otomatik seçilir.",
  },
  width: {
    term: "Aralık genişliği",
    text: "%80 tahmin aralığının ortalama genişliği; aynı coverage'ı daha dar aralıkla sağlayan model daha bilgilendiricidir.",
  },
  yoy_pct: {
    term: "Yıllık değişim (%)",
    text: "Geçen yılın aynı ayına göre yüzde değişim; mevsimsel etkileri büyük ölçüde giderir.",
  },
};

export const MODEL_INFO = {
  naive: ["Naive", "Bir sonraki dönem için son gözlenen değeri tahmin eder; diğer modellerin karşılaştırıldığı en basit baseline'dır."],
  seasonal_naive: ["Seasonal naive", "Geçen yılın aynı ayının değerini tahmin eder; mevsimselliği güçlü serilerde anlamlıdır."],
  drift: ["Drift", "Son değere, serinin tüm geçmişindeki ortalama dönemlik değişimi ekler."],
  drift_recent: ["Drift (son 6 ay)", "Son değere, son 6 ayın ortalama dönemlik değişimini ekler."],
  mean12: ["12 ay ortalaması", "Son 12 ayın ortalamasını tahmin eder."],
  median12: ["12 ay medyanı", "Son 12 ayın medyanını tahmin eder; outlier'lardan daha az etkilenir."],
  ridge: ["Ridge regression", "Hedefin ve diğer feature'ların lag'leriyle kurulan, overfitting'e karşı regularization uygulanmış doğrusal model."],
  qr: ["Quantile regression", "Medyanı ve %10–%90 aralığını doğrudan tahmin eder; belirsizliği de ölçer."],
  combo_mean: ["Kombinasyon (ortalama)", "Üye modellerin tahminlerinin ortalaması."],
  combo_median: ["Kombinasyon (medyan)", "Üye modellerin tahminlerinin medyanı; tek bir modelin büyük hatasından az etkilenir."],
  auto_robust: ["Robust kombinasyon", "Geçmiş değişimlerde excess kurtosis ya da outlier payı yüksekse medyan, değilse ortalama kombinasyonunu kullanır."],
  anchor_naive: ["Anchor naive", "Seçilen anchor serisinin son değerini tahmin eder (ör. aylık ortalama faizi ay sonu faiziyle tahmin etmek)."],
};
