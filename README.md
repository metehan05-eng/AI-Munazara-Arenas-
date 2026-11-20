# AI Münazara Arenası

Terminalde çalışan bir Textual uygulaması. İki yapay zeka ajanı, kendi yerel MCP araçları üzerinden kendi bilgi dosyalarını sorgulayarak münazara yapar. Ajan A kiracı tarafını, Ajan B ev sahibi ve ekonomist tarafını savunur. Siz araya moderatör olarak girip her an yeni bilgi veya yönerge ekleyebilirsiniz.

> **Uyarı:** Bu uygulama bir münazara simülasyonudur. Ürettiği metinler bağlayıcı hukuki tavsiye değildir. Bilgi dosyalarındaki mevzuat özetleri başlangıç taslağıdır ve kullanmadan önce resmi kaynaklarla teyit edilmelidir.

---

## Mimari

```
tui_app.py            Textual arayüzü (python tui_app.py)
   │  olaylar (callback)
orchestrator.py       Tur döngüsü, moderatör notları, token sayımı, LLM çağrıları
   │                       │
   │ stdio (alt süreç)     │ AsyncOpenAI → BASE_URL
   ▼                       ▼
mcp_server.py --role tenant      → query_tenant_knowledge          → data/ajan_a_verileri.json
mcp_server.py --role landlord    → query_landlord_econ_knowledge   → data/ajan_b_verileri.json
```

Her ajan kendi MCP sunucu sürecine bağlanır ve yalnızca kendi aracını görür. Orchestrator de isteğe yalnızca o ajanın aracını ekler. Böylece Ajan A, Ajan B'nin veri dosyasına erişemez.

Dosya yapısı:

```
ai-munazara-arenasi/
├── tui_app.py            # Ana giriş noktası
├── orchestrator.py       # Münazara çekirdeği ve MCP istemcileri
├── mcp_server.py         # Yerel MCP sunucusu (rol başına tek araç)
├── requirements.txt
├── .env.example          # .env şablonu
├── data/
│   ├── ajan_a_verileri.json   # Kiracı tarafı bilgi tabanı
│   └── ajan_b_verileri.json   # Ev sahibi ve ekonomi bilgi tabanı
└── logs/                 # Çalışma sırasında oluşur (MCP ve uygulama logları)
```

---

## Gereksinimler

- Python 3.11 veya üzeri
- OpenAI uyumlu bir uç nokta: Ollama, llama.cpp, NVIDIA NIM veya OpenRouter
- **Araç çağrısı (function calling) destekleyen modeller.** Ajanlar kanıtı MCP aracıyla topladığı için bu zorunludur.
- Terminal boyutu en az 110 × 32 olmalıdır.

---

## Kurulum

```bash
cd ai-munazara-arenasi
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

### Yerel model örneği (Ollama)

```bash
ollama pull qwen2.5:7b
ollama pull llama3.1:8b
ollama serve
```

`.env` dosyasında `BASE_URL=http://localhost:11434/v1` olduğundan emin olun. Ajanlara farklı modeller de verebilirsiniz; aynı modeli iki ajana da verebilirsiniz.

### .env değişkenleri

| Değişken | Açıklama | Örnek |
|---|---|---|
| `BASE_URL` | OpenAI uyumlu uç nokta | `http://localhost:11434/v1` |
| `API_KEY` | Sağlayıcı anahtarı; yerel sunucularda boş bırakılabilir | `sk-...` |
| `AGENT_A_MODEL` | Ajan A (kiracı) modeli | `qwen2.5:7b` |
| `AGENT_B_MODEL` | Ajan B (ev sahibi) modeli | `llama3.1:8b` |
| `TEMPERATURE` | Yaratıcılık düzeyi | `0.6` |
| `MAX_RESPONSE_TOKENS` | Bir konuşmanın üst token sınırı | `900` |
| `MAX_TOOL_ROUNDS` | Bir konuşma başına en fazla araç çağrısı turu | `3` |

Diğer sağlayıcılar için `BASE_URL` örnekleri:

- llama.cpp: `http://localhost:8080/v1`
- NVIDIA NIM: `https://integrate.api.nvidia.com/v1`
- OpenRouter: `https://openrouter.ai/api/v1`

---

## Çalıştırma

```bash
python tui_app.py
```

Uygulama açıldığında konu alanı hazırdır. Konuyu ve tur sayısını girip `[S]` tuşuna basın. Her tur, Ajan A'nın ardından Ajan B'nin konuşmasından oluşur.

### Ekran düzeni

- **Üst başlık:** Tur sayacı (`Tur: 2/6`) ve durum göstergesi (`Ajan A Düşünüyor...`, `MCP Sorgulanıyor...`).
- **Sol panel:** Ajan A'nın rolü, modeli, harcanan token ve son MCP sorgusu.
- **Orta akış:** Ajan A mavi çerçeveli ve solda, Ajan B kehribar çerçeveli ve sağda görünür. Bir ajan araç çağırdığında konuşma balonunun üstünde `[MCP Tool -> query_tenant_knowledge("TBK 344")]` kutusu belirir. Moderatör notları yeşil kesikli çerçeveyle gösterilir.
- **Sağ panel:** Ajan B'nin rolü, modeli, harcanan token ve son MCP sorgusu.
- **Alt panel:** Konu girişi, tur sayısı, moderatör girişi ve kontrol düğmeleri.

### Klavye kısayolları

| Tuş | İşlev |
|---|---|
| `S` | Münazarayı başlatır (konu ve tur sayısı alanlarından okunur) |
| `P` | Duraklatır veya devam ettirir. Duraklatma, yarım kalan konuşmayı değil, sıradaki konuşmayı bekletir. |
| `R` | Münazarayı durdurur ve ekranı temizler |
| `Q` veya `Ctrl+Q` | Çıkış |
| `Esc` | Metin giriş alanından çıkar; harf tuşları tekrar kısayol olur |

Metin kutularına yazarken harf tuşları metne eklenir. Kısayolları kullanmak için önce `Esc` ile giriş alanından çıkın. Düğmelere fareyle de tıklayabilirsiniz.

### Moderatör müdahalesi

Münazara çalışırken alttaki moderatör kutusuna yazıp `Enter` ile gönderin. Not, iki ajanın da sonraki konuşmasının bağlamına eklenir ve tartışma boyunca geçerli kalır. Ajanlar bu notları `[YENİ]` olarak görür; bir kez uyguladıktan sonra bu etiket kalkar.

Örnek müdahaleler:

- `Yeni gelişme: Ev sahibi taşınmazı üçüncü bir kişiye sattı.`
- `Argümanları 3 cümleyle sınırlandırın.`
- `Ajan B, enflasyon rakamlarını yalnızca data/ajan_b_verileri.json içinden kullansın.`

---

## Bilgi dosyalarını kendi verinizle güncelleme

Uygulama bilgi dosyalarını her sorguda yeniden okur. Dosyayı düzenledikten sonra uygulamayı yeniden başlatmanız gerekmez.

### Kayıt şeması

Her iki dosya da aynı yapıdadır:

```json
{
  "meta": { "ajan": "A", "rol": "...", "uyari": "..." },
  "kayitlar": [
    {
      "id": "TBK-344",
      "kategori": "mevzuat",
      "madde_no": "TBK 344",
      "baslik": "Konut kiralarında kira bedeli artışı",
      "anahtar_kelimeler": ["kira artışı", "TÜFE", "enflasyon"],
      "icerik": "Maddenin özeti veya paraphrase edilmiş metni.",
      "savunma_notu": "Bu kaynağın argümanda nasıl kullanılacağı.",
      "kaynak": "6098 sayılı Türk Borçlar Kanunu",
      "dogrulama_durumu": "TASLAK"
    }
  ]
}
```

| Alan | Açıklama |
|---|---|
| `id` | Benzersiz kimlik. Ajanlar argümanlarında `[ID]` olarak atıf yapar. |
| `kategori` | `mevzuat`, `anayasa`, `emsal_karar`, `usul`, `argüman`, `ekonomi`, `rayic`, `karsilastirmali` gibi serbest bir etiket. |
| `madde_no` | Madde veya karar numarası. Arama bu alana en yüksek ağırlığı verir. |
| `baslik` | Kısa başlık. |
| `anahtar_kelimeler` | Arama için anahtar kelimeler. Türkçe karakter ve büyük/küçük harf farkı gözetilmez. |
| `icerik` | Asıl bilgi. Araç sonucunda ilk 700 karakter gösterilir. |
| `savunma_notu` | Ajanın argümanda bu kaydı nasıl kullanacağına dair yönlendirme. |
| `kaynak` | Kaynağın adı ve tarihi. |
| `dogrulama_durumu` | Aşağıdaki etiketlerden biri. |

Doğrulama etiketleri:

- `DOĞRULANDI`: Resmi kaynakla teyit edilmiştir.
- `TASLAK`: Özet niteliğindedir; resmi metinle karşılaştırılmalıdır.
- `DOĞRULANMADI`: Yer tutucudur. Doldurulmadan kullanılmamalıdır.
- `GEREKÇE`: Hukuki kaynak değil, savunma çerçevesidir.
- `HESAPLAMA`: Matematiksel çerçevedir; girdiler ayrıca doğrulanmalıdır.

### Yargıtay kararları hakkında

Başlangıç dosyalarındaki `YRG-01`, `YRG-02` ve `YRG-B01` kayıtları bilerek yer tutucu bırakılmıştır. Esas ve karar numaraları uydurulmamıştır. Gerçek kararları ekleyecekseniz:

1. Kararı [Yargıtay karar arama](https://karararama.yargitay.gov.tr) üzerinden bulun.
2. `kaynak` alanına gerçek `E.____/K.____` numarasını yazın.
3. `icerik` alanına kararın ilkesini kendi cümlenizle özetleyin.
4. `dogrulama_durumu` değerini `DOĞRULANDI` yapın.

### Yeni kayıt ekleme örneği

`ajan_b_verileri.json` içindeki `kayitlar` listesine ekleyin:

```json
{
  "id": "EKO-08",
  "kategori": "ekonomi",
  "madde_no": null,
  "baslik": "Güncel TÜFE verisi",
  "anahtar_kelimeler": ["TÜFE", "güncel", "yıllık enflasyon"],
  "icerik": "Son açıklanan TÜİK yıllık TÜFE değeri: [buraya güncel değeri girin].",
  "savunma_notu": "Enflasyon argümanında yalnızca bu kayıttaki değeri kullanın.",
  "kaynak": "TÜİK, [ay yıl]",
  "dogrulama_durumu": "DOĞRULANDI"
}
```

JSON'ın geçerli olduğunu kontrol etmek için:

```bash
python -m json.tool data/ajan_b_verileri.json > /dev/null && echo "JSON geçerli"
```

---

## Sorun giderme

**"MCP sunucusu başlatılamadı" hatası**
`logs/mcp_tenant.log` veya `logs/mcp_landlord.log` dosyasına bakın. Çoğu zaman `mcp` paketinin kurulu olmadığı ya da `data/` klasöründe JSON hatası olduğu görülür.

**Ajan araç çağırmadan konuşuyor**
Uygulama bir kez hatırlatma gönderir. Hâlâ sorun varsa modelin araç çağrısını desteklediğini doğrulayın. Ollama'da `ollama show <model>` çıktısında `tools` yeteneğini arayın.

**Bağlantı reddedildi (Connection refused)**
`ollama serve` çalışıyor mu ve `BASE_URL` doğru mu kontrol edin.

**Token sayıları `≈` ile gösteriliyor**
Sağlayıcı kullanım bilgisi döndürmüyorsa token sayısı karakter sayısından tahmin edilir.

**Ayrıntılı günlük**
Uygulama günlükleri `logs/munazara.log` dosyasına yazılır. Terminal ekranını bozmamak için loglar stderr'e değil dosyaya yönlendirilir.

---

## Lisans ve sorumluluk

Bu proje eğitim ve araştırma amaçlıdır. Bilgi dosyalarındaki hukuki içerik başlangıç taslağıdır. Gerçek bir uyuşmazlıkta kiracı veya ev sahibi olarak karar vermeden önce bir avukata danışın.
# AI-Munazara-Arenas-
