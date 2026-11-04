// Glossary: one Turkish sentence per term used in the panel; English technical terms stay in English.
// Keys are referenced by the "?" buttons; the drawer lists them sorted by term.
export const GLOSSARY = {
  collinearity: {
    term: "Collinearity (yüksek korelasyon)",
    text: "İki feature'ın neredeyse aynı bilgiyi taşıması; ikisi birlikte kullanıldığında regresyon katsayıları istikrarsızlaşır, biri çıkarılabilir.",
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
    text: "Bir dönemin satırında o tarihte henüz yayımlanmamış bilginin bulunması; bu veriyle eğitilen bir modelin başarısı olduğundan yüksek görünür.",
  },
  market_share: {
    term: "Pazar payı",
    text: "Bir oyuncunun satış, prim ya da abone gibi değerinin pazar toplamına oranı; toplam TOPLAM (ya da TOTAL) satırlarıyla verilmezse listelenen oyuncuların toplamı kullanılır.",
  },
  missing: {
    term: "Missing value",
    text: "Panelde değeri olmayan dönemler; oranı yüksekse model bu dönemleri atmak ya da imputation ile doldurmak zorunda kalır.",
  },
  outlier: {
    term: "Outlier",
    text: "Medyan ve MAD'e göre serinin olağan dağılımından çok uzak kalan gözlem; gerçek bir şok da veri hatası da olabilir, incelenmesi gerekir.",
  },
  panel: {
    term: "Panel",
    text: "Her satırı bir dönem, her sütunu bir feature olan ve ML modellerinin doğrudan eğitildiği ortak takvimli tablo.",
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
  share_trend: {
    term: "Pay trendi",
    text: "Seçilen penceredeki dönemlik paylara uydurulan doğrusal trend; eğim en az 0,2 puan/yıl ve |t| ≥ 2 ise oyuncu anlamlı biçimde pay kazanıyor ya da kaybediyor sayılır.",
  },
  shift_share: {
    term: "Shift-share",
    text: "Odak oyuncunun toplam pay değişimini segment içi pay etkisi ve segment karması etkisi olarak ikiye ayırır; iki etkinin toplamı değişime eşittir.",
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
  yoy_pct: {
    term: "Yıllık değişim (%)",
    text: "Geçen yılın aynı ayına göre yüzde değişim; mevsimsel etkileri büyük ölçüde giderir.",
  },
};
