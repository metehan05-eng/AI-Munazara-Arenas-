"""Yerel MCP sunucusu: her ajana yalnızca kendi bilgi tabanını sorgulatan tek araç.

Kullanım (orchestrator bu sunucuyu alt süreç olarak otomatik başlatır):
    python mcp_server.py --role tenant      # yalnızca query_tenant_knowledge
    python mcp_server.py --role landlord    # yalnızca query_landlord_econ_knowledge

Not: stdout MCP protokolü tarafından kullanılır; loglar yalnızca stderr'e yazılır.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" if (BASE_DIR / "data").is_dir() else BASE_DIR
TENANT_DATA = DATA_DIR / "ajan_a_verileri.json"
LANDLORD_DATA = DATA_DIR / "ajan_b_verileri.json"

TOP_K = 4
MAX_CONTENT_CHARS = 700

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[mcp-server %(levelname)s] %(message)s",
)
log = logging.getLogger("mcp-server")

# --- Türkçe'ye duyarlı normalizasyon ---------------------------------------
# Büyük/küçük harf ve Türkçe karakter farklarını (ı/i, ş/s, ğ/g ...) ortadan kaldırır.
# Böylece "TÜFE", "tufe" ve "Tufe" aynı sözcük kabul edilir.
_FOLD_TABLE = str.maketrans("çğıöşü", "cgiosu")


def fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "i").lower().translate(_FOLD_TABLE)


_STOPWORDS = {
    fold(w)
    for w in (
        "ve veya ile bir bu şu için da de mi mı mu mü ne nasıl nedir olan olarak gibi "
        "ki hangi kim var madde maddesi"
    ).split()
}


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[^\W_]+", fold(text))
    return [t for t in tokens if t not in _STOPWORDS and (len(t) >= 3 or t.isdigit())]


def _stem(token: str) -> str:
    # Sondan eklemeli dil için basit önek eşleştirmesi: "kiracının" ~ "kiracı"
    return token[:5]


def _field_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    return tokenize(str(value))


# Madde numarası ve başlık, uzun içeriğe göre daha belirleyicidir.
FIELD_WEIGHTS: dict[str, float] = {
    "madde_no": 5.0,
    "baslik": 3.0,
    "anahtar_kelimeler": 3.0,
    "kategori": 1.0,
    "icerik": 1.0,
    "savunma_notu": 1.0,
}


def search(records: list[dict], query: str, top_k: int = TOP_K) -> list[dict]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    scored: list[tuple[float, dict]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        index = {name: _field_tokens(rec.get(name)) for name in FIELD_WEIGHTS}
        score = 0.0
        for qt in query_tokens:
            stem = _stem(qt)
            best = 0.0
            for name, weight in FIELD_WEIGHTS.items():
                if weight > best and any(word.startswith(stem) for word in index[name]):
                    best = weight
            score += best
        if score > 0:
            scored.append((score, rec))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [rec for _, rec in scored[:top_k]]


def _clip(text: Any, limit: int = MAX_CONTENT_CHARS) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[:limit].rstrip() + " ..."


def _load(path: Path) -> tuple[dict | None, str | None]:
    if not path.exists():
        return None, f"Veri dosyası bulunamadı: data/{path.name}"
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return None, f"data/{path.name} geçerli JSON değil (satır {exc.lineno}): {exc.msg}"
    if not isinstance(data, dict) or not isinstance(data.get("kayitlar"), list):
        return None, f"data/{path.name} içinde 'kayitlar' listesi yok"
    return data, None


def _run_query(path: Path, query: str, label: str) -> str:
    q = (query or "").strip()
    if not q:
        return "HATA: Boş sorgu. Madde numarası veya anahtar kelime girin (ör. 'TBK 344', 'teminat')."

    data, error = _load(path)
    if error:
        log.error(error)
        return f"HATA: {error}"

    hits = search(data["kayitlar"], q)
    log.info("sorgu=%r kaynak=%s eşleşme=%d", q, path.name, len(hits))

    if not hits:
        return (
            "Bulunan kayıt sayısı: 0\n"
            f"{label} içinde '{q}' için eşleşme yok. "
            "Madde numarası, kavram veya daha genel anahtar kelimelerle yeniden deneyin."
        )

    lines = [f"Bulunan kayıt sayısı: {len(hits)}", ""]
    for rec in hits:
        lines.append(f"[{rec.get('id', '?')}] {rec.get('baslik', '(başlıksız)')}")
        lines.append(
            f"Kategori: {rec.get('kategori', '-')} | Madde: {rec.get('madde_no') or '-'} | "
            f"Doğrulama: {rec.get('dogrulama_durumu', '-')} | Kaynak: {rec.get('kaynak', '-')}"
        )
        lines.append(f"İçerik: {_clip(rec.get('icerik'))}")
        if rec.get("savunma_notu"):
            lines.append(f"Savunma notu: {_clip(rec['savunma_notu'], 400)}")
        lines.append("")
    return "\n".join(lines).rstrip()


# --- MCP araçları ----------------------------------------------------------
def query_tenant_knowledge(query: str) -> str:
    """Kiracı tarafı bilgi tabanında (Ajan A) arama yapar: TBK maddeleri (TBK 299, 301 onarım, 305 depozito sınırı, 344 kira tavanı, 345 beş yıl kira tespiti ve hakkaniyet indirimi, 347 on yıl uzama güvencesi, 348 ceza yasağı, 355 yeniden kiralama yasağı ve tazminat cezası), TMK 2 dürüstlük kuralı, TCK 237 fiyatları etkileme suçu, Anayasa m.2 ve m.57 konut hakkı, emsal savunmalar ve usuli haklar. Madde numarası veya anahtar kelime ile sorgulayın (ör. 'TBK 345 kira tespiti', 'TBK 355 tazminat', 'TBK 347 uzama', 'TMK 2 dürüstlük', 'TBK 305 depozito', 'yazılı itiraz')."""
    return _run_query(TENANT_DATA, query, "Ajan A bilgi tabanı")


def query_landlord_econ_knowledge(query: str) -> str:
    """Ev sahibi ve ekonomi bilgi tabanında (Ajan B) arama yapar: Mülkiyet hakkı (Anayasa m.35, m.13 ölçülülük, m.2 hukuk devleti, TMK 683 malik yetkisi), TBK 138 aşırı ifa güçlüğü ve uyarlama davası, TBK 315 kiracı temerrüdü ve tahliye, TBK 351 yeni malik ihtiyacı, TBK 352/2 iki haklı ihtar, EKO-08 inşaat maliyet endeksi (İME) artışı, EKO-09 karaborsa ve kayıt dışılık, EKO-10 sermaye kaçışı, TÜFE ve reel kira erimesi, konut arz daralması, bakım/onarım yatırımı erteleme ve ekonomik gerekçeler. Kavram veya anahtar kelime ile sorgulayın (ör. 'TBK 138 uyarlama', 'TBK 315 temerrüt', 'TBK 352 iki haklı ihtar', 'inşaat maliyeti İME', 'karaborsa kayıt dışı', 'reel getiri kaybı', 'konut arzı daralması')."""
    return _run_query(LANDLORD_DATA, query, "Ajan B bilgi tabanı")


TOOLS = {
    "tenant": ("query_tenant_knowledge", query_tenant_knowledge),
    "landlord": ("query_landlord_econ_knowledge", query_landlord_econ_knowledge),
}


def build_server(role: str) -> FastMCP:
    """Verilen rol için yalnızca o role ait aracı kaydeden sunucuyu üretir."""
    if role not in TOOLS:
        raise ValueError(f"Bilinmeyen rol: {role}")
    tool_name, fn = TOOLS[role]
    server = FastMCP("munazara-veri")
    server.add_tool(fn, name=tool_name)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Münazara arenası yerel MCP sunucusu")
    parser.add_argument("--role", choices=sorted(TOOLS), required=True)
    args = parser.parse_args()
    build_server(args.role).run()


if __name__ == "__main__":
    main()
