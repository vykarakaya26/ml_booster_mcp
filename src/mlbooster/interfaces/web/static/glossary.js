// Glossary: plain-Turkish explanations of the technical terms used in the panel.
// Keys are referenced by the "?" buttons and the glossary drawer.
export const GLOSSARY = {
  feature: {
    term: "Feature (açıklayıcı değişken)",
    text: "Bir modelin tahmin yaparken kullandığı girdi serisi. Örneğin kredi hacmini tahmin eden bir model için politika faizi ve enflasyon birer feature'dır.",
  },
  panel: {
    term: "Panel",
    text: "Seçilen sektörün feature'larının aynı aylık takvimde hizalanmış tablosu. Her satır bir ay, her sütun bir feature'dır; ML modelleri doğrudan bu tabloyla eğitilir.",
  },
  point_in_time: {
    term: "Point-in-time (look-ahead korumalı)",
    text: "Her değer, gerçekte yayımlandığı aya yazılır. Mart enflasyonu Nisan başında açıklandığı için panelde Nisan satırında yer alır. Böylece model, o tarihte henüz bilinmeyen bir bilgiyi kullanamaz.",
  },
  publication_lag: {
    term: "Yayın gecikmesi",
    text: "Bir dönemin verisinin, dönem bittikten kaç gün sonra açıklandığı. Panel bu gecikmeyi dikkate alarak veriyi kaydırır.",
  },
  level: {
    term: "Düzey",
    text: "Serinin kendi değeri, dönüşüm uygulanmadan. Örneğin politika faizi için %37,0.",
  },
  log_diff: {
    term: "Aylık değişim (log)",
    text: "Bir önceki aya göre logaritmik değişim. Küçük değerlerde yüzde değişime çok yakındır: 0,02 yaklaşık %2 artış, -0,03 yaklaşık %3 düşüş demektir. Modellerde fiyat ve kur gibi büyüyen serileri karşılaştırılabilir kılmak için kullanılır.",
  },
  yoy_pct: {
    term: "Yıllık değişim (%)",
    text: "Geçen yılın aynı ayına göre yüzde değişim. Mevsimsel etkileri büyük ölçüde ortadan kaldırır; enflasyon bu yöntemle açıklanır.",
  },
  diff: {
    term: "Aylık fark",
    text: "Bir önceki aya göre değişim, serinin kendi biriminde. Faiz için yüzde puan cinsinden değişimdir.",
  },
  pp: {
    term: "Yüzde ile yüzde puan",
    text: "Faiz %40'tan %45'e çıktığında artış 5 yüzde puandır; oransal olarak ise %12,5'tir. Raporlarda ikisi ayrı tutulur.",
  },
  real: {
    term: "Reel değer",
    text: "TÜFE ile enflasyon etkisi arındırılmış değer. Nominal harcamadaki artışın ne kadarının fiyat artışından geldiğini ayırır.",
  },
  backtest: {
    term: "Backtest (geriye dönük sınama)",
    text: "Modeli geçmişte her ay için yalnızca o güne kadarki veriyle yeniden kurup bir sonraki dönemi tahmin ettirme ve tahmini gerçekleşenle karşılaştırma. Modelin gerçek kullanımdaki başarısını taklit eder.",
  },
  horizon: {
    term: "Ufuk",
    text: "Kaç dönem sonrasının tahmin edildiği. 1 ufuk, bir sonraki ayın tahmini demektir.",
  },
  min_train: {
    term: "Asgari eğitim süresi",
    text: "İlk tahmin yapılmadan önce modelin görmesi gereken en az ay sayısı.",
  },
  anchor: {
    term: "Çapa",
    text: "Hedefin daha güncel bir sürümü. Örneğin aylık ortalama faiz tahmin ediliyorsa ay sonu faizi güçlü bir başlangıç noktasıdır. Seçilirse 'Çapa naive' modeli eklenir.",
  },
  mae: {
    term: "MAE (ortalama mutlak hata)",
    text: "Tahminlerin gerçekleşenden ortalama ne kadar saptığı, hedefin kendi biriminde. Faiz için 0,4 değeri, tahminlerin ortalama 0,4 puan saptığı anlamına gelir. Düşük olan daha iyidir.",
  },
  rmse: {
    term: "RMSE",
    text: "Büyük hataları daha ağır cezalandıran hata ölçüsü. MAE'den belirgin biçimde büyükse model zaman zaman büyük ıskalar yapıyor demektir.",
  },
  mase: {
    term: "MASE",
    text: "Hatanın, en basit yöntem olan 'son değeri tekrar et' (naive) yönteminin hatasına oranı. 1'in altı naive'den iyi, üstü kötü demektir.",
  },
  smape: {
    term: "sMAPE",
    text: "Yüzde cinsinden simetrik hata. Seri sıfıra yakın değerler aldığında yanıltıcı olabilir.",
  },
  coverage: {
    term: "%80 aralık kapsaması",
    text: "Gerçekleşen değerin, modelin verdiği %80 tahmin aralığının içinde kalma oranı. %80'e yakın olması beklenir; çok düşükse aralıklar fazla dar, çok yüksekse gereğinden geniştir.",
  },
  width: {
    term: "Aralık genişliği",
    text: "%80 tahmin aralığının ortalama genişliği. Aynı kapsamayı daha dar aralıkla sağlayan model daha bilgilendiricidir.",
  },
  pinball: {
    term: "Pinball kaybı",
    text: "Tahmin aralığının hem isabetini hem genişliğini tek sayıda ölçen puan. Düşük olan daha iyidir.",
  },
  naive_beat: {
    term: "Naive'e göre iyileşme",
    text: "En iyi modelin hatasının naive yöntemine göre yüzde kaç düşük olduğu. Bir ML modelinin anlamlı sayılması için bu çıtayı geçmesi gerekir.",
  },
  regime: {
    term: "Rejim",
    text: "Türkiye ekonomisinde koşulların belirgin biçimde değiştiği dönemler (2021 sonu kur şoku, 2023 sonrası sıkılaştırma). Sonuçlar her dönem için ayrıca verilir; tek bir döneme bağlı başarı güvenilir sayılmaz.",
  },
  stale: {
    term: "Stale seri",
    text: "Son gözlemi beklenenden eski olan seri. Kaynak seriyi durdurmuş ya da kodunu değiştirmiş olabilir.",
  },
  missing: {
    term: "Eksik dönem",
    text: "Panelde değeri olmayan ayların oranı. Yüksekse model ya bu ayları atar ya da tahmini değerle doldurmak zorunda kalır.",
  },
  outlier: {
    term: "Outlier",
    text: "Serinin olağan dağılımından çok uzak gözlem (medyan ve yayılım ölçüsüne göre). Gerçek bir şok da olabilir, veri hatası da; incelenmesi gerekir.",
  },
  structural_break: {
    term: "Structural break",
    text: "Serinin ortalamasının bir tarihten itibaren kalıcı biçimde değiştiği nokta. Bilinen bir olayla (kur şoku, politika değişikliği) eşleşiyorsa adı gösterilir.",
  },
  collinearity: {
    term: "Aşırı korelasyon",
    text: "İki feature'ın neredeyse aynı bilgiyi taşıması. Modelde ikisini birden kullanmak katsayıları kararsız hale getirebilir; biri çıkarılabilir.",
  },
  stationarity: {
    term: "Durağanlık",
    text: "Serinin ortalama ve yayılımının zaman içinde sabit kalması. Durağan olmayan seriler genellikle fark ya da yıllık değişim alınarak modellenir.",
  },
};

export const MODEL_INFO = {
  naive: ["Naive", "Bir sonraki dönem için son gözlenen değeri tahmin eder. Tüm modellerin karşılaştırıldığı en basit çıtadır."],
  seasonal_naive: ["Mevsimsel naive", "Geçen yılın aynı ayını tahmin eder. Mevsimsellik güçlü serilerde anlamlıdır."],
  drift: ["Drift", "Son değere, serinin tüm geçmişteki ortalama eğilimini ekler."],
  drift_recent: ["Yakın dönem drift", "Son değere, son 6 ayın ortalama eğilimini ekler."],
  mean12: ["12 ay ortalaması", "Son 12 ayın ortalamasını tahmin eder."],
  median12: ["12 ay medyanı", "Son 12 ayın medyanını tahmin eder; uç değerlerden daha az etkilenir."],
  ridge: ["Ridge regresyon", "Hedefin ve diğer feature'ların gecikmeli değerleriyle kurulan, aşırı uyuma karşı düzenlileştirilmiş doğrusal model."],
  qr: ["Kantil regresyon", "Medyanı ve %10–%90 aralığını doğrudan tahmin eden regresyon; belirsizliği de verir."],
  combo_mean: ["Kombinasyon (ortalama)", "Üye modellerin tahminlerinin ortalaması."],
  combo_median: ["Kombinasyon (medyan)", "Üye modellerin tahminlerinin medyanı; tek bir modelin büyük hatasından az etkilenir."],
  auto_robust: ["Otomatik dayanıklı", "Modeller arasında görüş ayrılığı büyükse medyana, küçükse ortalamaya geçen kombinasyon."],
  anchor_naive: ["Çapa naive", "Seçilen çapa serisinin son değerini tahmin eder (ör. ay sonu faizi ile aylık ortalama faizi tahmin etmek)."],
};
