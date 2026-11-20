"""Münazara çekirdeği: iki ajan, her ajanın MCP araç sunucusu ve LLM çağrıları.

Bu modül Textual'dan bağımsızdır. Gelişmeler (olaylar) bir callback üzerinden
arayüze bildirilir; TUI bu olaylarla ekranı günceller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" if (BASE_DIR / "data").is_dir() else BASE_DIR
LOG_DIR = BASE_DIR / "logs"
MCP_SERVER_SCRIPT = BASE_DIR / "mcp_server.py"
TENANT_TOOL = "query_tenant_knowledge"
LANDLORD_TOOL = "query_landlord_econ_knowledge"

load_dotenv(BASE_DIR / ".env")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Arayüzü bozmamak için loglar stderr yerine dosyaya yazılır.
log = logging.getLogger("munazara")
log.setLevel(logging.INFO)
log.propagate = False
_file_handler = logging.FileHandler(LOG_DIR / "munazara.log", encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
log.addHandler(_file_handler)

MAX_HISTORY_ENTRIES = 18
MAX_SPEECH_CHARS_IN_PROMPT = 3000

SYSTEM_PROMPT_A = """Sen "Ajan A"sın: Türkiye'de KİRACI tarafını temsil eden, son derece dişli, tavizsiz ve AGRESİF bir duruşma avukatısın.
Görevin: Barınma hakkını tavizsizce savunmak; ev sahiplerinin doymak bilmeyen rant hırsını, fahiş artış baskılarını ve piyasa dayatmalarını hukuken ve mantıken yerle bir etmek.

Üslup ve Karakter:
- Pasif ve savunmada kalma! Duruşma salonundaki iddialı, keskin dilli, karşı tarafın tezlerini çürüten ve çelişkilerini yüzüne vuran AGRESİF ve polemikçi bir avukat gibi konuş.
- Karşı tarafın "mülkiyet hakkı" ve "reel getiri" söylemlerini bir sömürü kılıfı olarak nitele; retorik sorular sor, meslektaşının iddialarını doğrudan ve sertçe hedef al (ör. "Meslektaşım açıkça kamu yararını ve anayasal barınma hakkını hiçe sayıyor!", "Ev sahibinin kâr marjı bir ailenin başını soktuğu yuvadan daha mı kutsaldır?!").

Kurallar:
1. Her konuşmadan önce MUTLAKA query_tenant_knowledge aracını çağırarak bilgi tabanından kanıt topla. Kanıtsız iddia kurma.
2. Dayandığın kayıtları [KAYIT-ID] biçiminde belirt, ör. [TBK-344], [TBK-345], [TBK-355], [TMK-2].
3. Bilgi tabanında olmayan madde numarası, karar veya rakam UYDURMA.
4. Kiracı bakışını koru: Barınma hakkı yaşam hakkıdır, yasal sınırlar emredicidir, güç asimetrisi kiracıyı korumayı zorunlu kılar.
5. Ajan B'nin son argümanındaki zayıf noktayı ve çelişkiyi yakala, sertçe çürüt; ardından bilgi tabanından daha önce kullanmadığın YENİ bir hukuki madde veya gerekçe getirerek karşı tarafı köşeye sıkıştır.
6. Kendi önceki konuşmalarını tekrarlama. Asla aynı cümle veya paragrafları art arda tekrar etme. Kalıp ve sıradan cümleler kullanma.
7. MODERATÖR NOTLARI her zaman önceliklidir ve derhal uygulanır.
8. Yanıtın akıcı, vurucu ve etkileyici bir Türkçe ile olsun. Konuşmanı detaylandırarak derinleştir; yaklaşık 180-250 kelime ve 2-3 güçlü paragraf halinde yaz.
9. Bu bir münazara simülasyonudur; bağlayıcı hukuki tavsiye değildir."""

SYSTEM_PROMPT_B = """Sen "Ajan B"sin: Türkiye'de EV SAHİBİ tarafını temsil eden, son derece dişli, analitik, tavizsiz ve AGRESİF bir avukat ve ekonomistsin.
Görevin: Mülkiyet hakkını, sermaye güvenliğini ve serbest piyasa dengelerini tavizsizce savunmak; kiracı tarafının popülist, romantik ve duygusal söylemlerini ekonomik yasalarla ve anayasal hükümlerle çökertmek.

Üslup ve Karakter:
- Alttan alma! Duruşma salonunda karşı tarafın popülist ajitasyonlarını soğuk ekonomik gerçeklerle, enflasyon verileriyle ve Anayasa hükümleriyle ezen AGRESİF bir hatip ol.
- Karşı tarafın "barınma hakkı" adı altında mülk sahiplerini hayır kurumuna çevirme çabasını ve popülizmini sertçe eleştir (ör. "Meslektaşım duygusal ajitasyon yaparak iktisat kurallarını ilga edebileceğini sanıyor!", "Ev sahibini günah keçisi ilan eden bu popülizm piyasayı kuruttu; yarın kiracılar kiralayacak tek bir ev bulamadığında kimi suçlayacaksınız?!").

Kurallar:
1. Her konuşmadan önce MUTLAKA query_landlord_econ_knowledge aracını çağırarak bilgi tabanından kanıt topla. Kanıtsız iddia kurma.
2. Dayandığın kayıtları [KAYIT-ID] biçiminde belirt, ör. [EKO-36], [TBK-138], [TMK-683], [EKO-08].
3. Rakam kullanırken yalnızca kayıtlardaki veya moderatör notundaki değerleri kullan. Bilgi tabanında olmayan madde veya istatistik UYDURMA.
4. Ev sahibi bakışını koru: Mülkiyet hakkı Anayasa ile korunan kutsal bir haktır; enflasyon altında sermaye erimesi mülkiyet gasbıdır; yapay kira tavanları arzı öldürür ve yeni kiralık ev arayanları felakete sürükler.
5. Ajan A'nın iddialarındaki ekonomik cehaleti ve tutarsızlığı yüzüne vur; ardından bilgi tabanından daha önce kullanmadığın YENİ bir ekonomik veya hukuki kayıt getirerek karşı saldırıya geç.
6. Kendi önceki konuşmalarını tekrarlama. Asla aynı cümle veya paragrafları art arda tekrar etme.
7. MODERATÖR NOTLARI her zaman önceliklidir ve derhal uygulanır.
8. Yanıtın akıcı, vurucu ve etkileyici bir Türkçe ile olsun. Konuşmanı detaylandırarak derinleştir; yaklaşık 180-250 kelime ve 2-3 güçlü paragraf halinde yaz.
9. Bu bir münazara simülasyonudur; bağlayıcı hukuki tavsiye değildir."""

NUDGE_TEMPLATE = "Hatırlatma: Yanıt yazmadan önce {tool} aracını çağırarak kanıt topla. Aracı çağırdıktan sonra yanıtını yaz."


# --- Yapılandırma ----------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    base_url: str
    api_key: str
    agent_a_model: str
    agent_b_model: str
    agent_a_base_url: str
    agent_a_api_key: str
    agent_b_base_url: str
    agent_b_api_key: str
    temperature: float
    max_response_tokens: int
    max_tool_rounds: int
    presence_penalty: float = 0.3
    frequency_penalty: float = 0.4

    @classmethod
    def from_env(cls) -> "Config":
        base_url = os.getenv("BASE_URL", "http://localhost:11434/v1").strip()
        api_key = os.getenv("API_KEY", "").strip()
        return cls(
            base_url=base_url,
            api_key=api_key,
            agent_a_model=os.getenv("AGENT_A_MODEL", "").strip(),
            agent_b_model=os.getenv("AGENT_B_MODEL", "").strip(),
            agent_a_base_url=os.getenv("AGENT_A_BASE_URL", "").strip() or base_url,
            agent_a_api_key=os.getenv("AGENT_A_API_KEY", "").strip() or api_key or "not-needed",
            agent_b_base_url=os.getenv("AGENT_B_BASE_URL", "").strip() or base_url,
            agent_b_api_key=os.getenv("AGENT_B_API_KEY", "").strip() or api_key or "not-needed",
            temperature=_env_float("TEMPERATURE", 0.7),
            max_response_tokens=_env_int("MAX_RESPONSE_TOKENS", 1500),
            max_tool_rounds=max(1, _env_int("MAX_TOOL_ROUNDS", 1)),
            presence_penalty=_env_float("PRESENCE_PENALTY", 0.3),
            frequency_penalty=_env_float("FREQUENCY_PENALTY", 0.4),
        )

    def problems(self) -> list[str]:
        issues: list[str] = []
        if not self.agent_a_base_url:
            issues.append("Ajan A için BASE_URL veya AGENT_A_BASE_URL tanımlı değil")
        if not self.agent_b_base_url:
            issues.append("Ajan B için BASE_URL veya AGENT_B_BASE_URL tanımlı değil")
        if not self.agent_a_model:
            issues.append("AGENT_A_MODEL tanımlı değil")
        if not self.agent_b_model:
            issues.append("AGENT_B_MODEL tanımlı değil")
        for name in ("ajan_a_verileri.json", "ajan_b_verileri.json"):
            if not (DATA_DIR / name).exists():
                issues.append(f"{DATA_DIR.name}/{name} bulunamadı")
        return issues


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    title: str
    tool_name: str
    model: str
    system_prompt: str


# --- Arayüze giden olaylar -------------------------------------------------
@dataclass
class TurnStarted:
    agent_key: str
    round_no: int
    total_rounds: int


@dataclass
class ToolCalled:
    agent_key: str
    tool_name: str
    query: str


@dataclass
class ToolFinished:
    agent_key: str
    tool_name: str
    summary: str
    is_error: bool


@dataclass
class SpeechDone:
    agent_key: str
    round_no: int
    text: str
    total_tokens: int
    tokens_estimated: bool


@dataclass
class ModeratorNote:
    text: str
    round_no: int


@dataclass
class Status:
    text: str


@dataclass
class ErrorOccurred:
    message: str


@dataclass
class DebateFinished:
    reason: str  # "completed" | "stopped" | "error"


DebateEvent = Union[
    TurnStarted, ToolCalled, ToolFinished, SpeechDone, ModeratorNote, Status, ErrorOccurred, DebateFinished
]


# --- MCP araç ana bilgisayarı ----------------------------------------------
class MCPToolHost:
    """Bir MCP sunucusunu (stdio alt süreci) arka planda canlı tutar.

    Bağlantı, açıldığı aynı asyncio görevinde kapatılır. Bu, anyio cancel-scope
    kuralları gereği gereklidir; aksi halde kapanışta RuntimeError oluşabilir.
    """

    def __init__(self, role: str, log_path: Path) -> None:
        self.role = role
        self.log_path = log_path
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._session: Optional[ClientSession] = None
        self._tools: list[dict[str, Any]] = []
        self._error: Optional[str] = None

    async def start(self) -> None:
        async with self._lock:
            if self._session is not None:
                return
            self._ready = asyncio.Event()
            self._stop = asyncio.Event()
            self._error = None
            self._task = asyncio.create_task(self._lifetime(), name=f"mcp-{self.role}")
            await self._ready.wait()
            if self._session is None:
                raise RuntimeError(
                    f"MCP sunucusu başlatılamadı ({self.role}): {self._error or 'bilinmeyen hata'}"
                )

    async def _lifetime(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(MCP_SERVER_SCRIPT), "--role", self.role],
        )
        try:
            with self.log_path.open("a", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        self._tools = [
                            {
                                "type": "function",
                                "function": {
                                    "name": tool.name,
                                    "description": tool.description or "",
                                    "parameters": tool.inputSchema,
                                },
                            }
                            for tool in listed.tools
                        ]
                        self._session = session
                        self._ready.set()
                        await self._stop.wait()
        except Exception as exc:  # noqa: BLE001 - hata metne çevrilip arayüze taşınır
            self._error = f"{type(exc).__name__}: {exc}"
            log.error("MCP %s hatası: %s", self.role, self._error)
        finally:
            self._session = None
            self._ready.set()

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except Exception:  # noqa: BLE001
            task.cancel()
        self._task = None
        self._session = None

    def openai_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        session = self._session
        if session is None:
            detail = self._error or ""
            return f"HATA: MCP sunucusu ({self.role}) bağlı değil. {detail}".strip(), True
        try:
            result = await session.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001
            return f"HATA: Araç çağrısı başarısız oldu: {exc}", True
        text = "\n".join(
            part.text for part in result.content if getattr(part, "type", None) == "text"
        ).strip()
        return (text or "(boş yanıt)"), bool(result.isError)


# --- Yardımcılar -----------------------------------------------------------
@dataclass
class _Speech:
    agent_key: str
    round_no: int
    text: str


@dataclass
class _Note:
    text: str
    round_no: int
    delivered_to: set[str] = field(default_factory=set)


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _summarize(text: str, is_error: bool) -> str:
    if is_error:
        return "hata: " + (text.splitlines()[0][:60] if text else "bilinmiyor")
    match = re.search(r"Bulunan kayıt sayısı: (\d+)", text)
    if match:
        count = int(match.group(1))
        return f"{count} kayıt bulundu" if count else "eşleşme yok"
    return (text.splitlines() or ["boş"])[0][:60]


def _clean_and_deduplicate(text: str) -> str:
    """Modelin ürettiği tekrarlanan paragrafları ve ardışık aynı cümleleri temizler."""
    if not text:
        return ""
    # 1. Paragraf düzeyinde tekilleştirme
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    unique_paras: list[str] = []
    seen_paras: set[str] = set()
    for p in paras:
        norm = re.sub(r"\s+", " ", p).strip().lower()
        if norm not in seen_paras:
            seen_paras.add(norm)
            unique_paras.append(p)

    # 2. Paragraf içi ardışık aynı cümle kontrolü
    final_paras: list[str] = []
    for p in unique_paras:
        sentences = re.split(r"(?<=[.!?])\s+", p)
        if len(sentences) > 1:
            u_sentences: list[str] = []
            for s in sentences:
                s_strip = s.strip()
                if not s_strip:
                    continue
                s_norm = re.sub(r"\s+", " ", s_strip).strip().lower()
                if not u_sentences or s_norm != re.sub(r"\s+", " ", u_sentences[-1]).strip().lower():
                    u_sentences.append(s_strip)
            final_paras.append(" ".join(u_sentences))
        else:
            final_paras.append(p)

    return "\n\n".join(final_paras).strip()


# --- Orkestrasyon ----------------------------------------------------------
class DebateOrchestrator:
    def __init__(self, config: Config, on_event: Callable[[DebateEvent], None]) -> None:
        self.cfg = config
        self._on_event = on_event
        self.clients = {
            "A": AsyncOpenAI(base_url=config.agent_a_base_url, api_key=config.agent_a_api_key),
            "B": AsyncOpenAI(base_url=config.agent_b_base_url, api_key=config.agent_b_api_key),
        }
        self.agents: dict[str, AgentSpec] = {
            "A": AgentSpec(
                key="A",
                name="Ajan A",
                title="Avukat · Kiracı Temsilcisi",
                tool_name=TENANT_TOOL,
                model=config.agent_a_model,
                system_prompt=SYSTEM_PROMPT_A,
            ),
            "B": AgentSpec(
                key="B",
                name="Ajan B",
                title="Avukat · Ev Sahibi Temsilcisi & Ekonomist",
                tool_name=LANDLORD_TOOL,
                model=config.agent_b_model,
                system_prompt=SYSTEM_PROMPT_B,
            ),
        }
        self._hosts = {
            "A": MCPToolHost("tenant", LOG_DIR / "mcp_tenant.log"),
            "B": MCPToolHost("landlord", LOG_DIR / "mcp_landlord.log"),
        }
        self._resume = asyncio.Event()
        self._resume.set()
        self._running = False
        self.topic = ""
        self.total_rounds = 0
        self.current_round = 0
        self.tokens: dict[str, int] = {"A": 0, "B": 0}
        self.tokens_estimated: dict[str, bool] = {"A": False, "B": False}
        self._speeches: list[_Speech] = []
        self._notes: list[_Note] = []

    # ---- durum ----
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._running and not self._resume.is_set()

    # ---- yaşam döngüsü ----
    async def ensure_ready(self) -> None:
        issues = self.cfg.problems()
        if issues:
            raise RuntimeError("; ".join(issues))
        for host in self._hosts.values():
            await host.start()

    async def shutdown(self) -> None:
        for host in self._hosts.values():
            await host.stop()

    def reset(self) -> None:
        self._speeches.clear()
        self._notes.clear()
        self.current_round = 0
        self.tokens = {"A": 0, "B": 0}
        self.tokens_estimated = {"A": False, "B": False}

    # ---- kullanıcı kontrolleri ----
    def pause(self) -> None:
        if self._running and self._resume.is_set():
            self._resume.clear()
            self._emit(Status("Duraklatıldı. Devam etmek için [P]"))

    def resume(self) -> None:
        if self._running and not self._resume.is_set():
            self._resume.set()
            self._emit(Status("Devam ediliyor"))

    def add_moderator_note(self, text: str) -> bool:
        text = text.strip()
        if not self._running or not text:
            return False
        note = _Note(text=text, round_no=self.current_round)
        self._notes.append(note)
        self._emit(ModeratorNote(text=text, round_no=self.current_round))
        return True

    # ---- münazara döngüsü ----
    async def run(self, topic: str, total_rounds: int) -> None:
        if self._running:
            raise RuntimeError("Münazara zaten çalışıyor.")
        self.reset()
        self.topic = topic.strip()
        self.total_rounds = total_rounds
        self._running = True
        self._resume.set()
        reason = "completed"
        try:
            self._emit(Status("MCP sunucuları başlatılıyor..."))
            await self.ensure_ready()
            self._emit(Status("MCP sunucuları hazır. Münazara başlıyor."))
            for rnd in range(1, total_rounds + 1):
                for key in ("A", "B"):
                    await self._resume.wait()
                    self.current_round = rnd
                    await self._speak(key, rnd)
        except asyncio.CancelledError:
            reason = "stopped"
            raise
        except Exception as exc:  # noqa: BLE001
            reason = "error"
            log.exception("Münazara hatası")
            self._emit(ErrorOccurred(f"{type(exc).__name__}: {exc}"))
        finally:
            self._running = False
            self._resume.set()
            self._emit(DebateFinished(reason))

    async def _speak(self, key: str, rnd: int) -> None:
        agent = self.agents[key]
        self._emit(TurnStarted(agent_key=key, round_no=rnd, total_rounds=self.total_rounds))
        text = await self._generate(agent, rnd)
        self._speeches.append(_Speech(agent_key=key, round_no=rnd, text=text))
        self._emit(
            SpeechDone(
                agent_key=key,
                round_no=rnd,
                text=text,
                total_tokens=self.tokens[key],
                tokens_estimated=self.tokens_estimated[key],
            )
        )

    async def _generate(self, agent: AgentSpec, rnd: int) -> str:
        """Ajanın bir konuşmasını üretir; gerekirse MCP araçlarını çağırır."""
        tools = self._hosts[agent.key].openai_tools()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": agent.system_prompt},
            {"role": "user", "content": self._build_user_prompt(agent, rnd)},
        ]
        tool_steps = 0
        nudged = False

        while True:
            allow_tools = bool(tools) and tool_steps < self.cfg.max_tool_rounds
            message = await self._chat(agent, messages, tools if allow_tools else None)
            content = (message.content or "").strip()
            tool_calls = list(message.tool_calls or [])

            if not tool_calls:
                # Model kanıt toplamadan konuşmaya başladıysa bir kez hatırlat.
                if allow_tools and tool_steps == 0 and not nudged:
                    nudged = True
                    if content:
                        messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {"role": "user", "content": NUDGE_TEMPLATE.format(tool=agent.tool_name)}
                    )
                    continue
                if content:
                    return _clean_and_deduplicate(content) or content
                finish_reason = getattr(message, "finish_reason", None) or "bilinmiyor"
                has_reasoning = bool(getattr(message, "reasoning_content", None))
                raise RuntimeError(
                    f"{agent.name} boş yanıt döndürdü (model: {agent.model}, "
                    f"bitiş: {finish_reason}, reasoning var: {'evet' if has_reasoning else 'hayır'})"
                )

            if tool_steps > self.cfg.max_tool_rounds + 1:
                raise RuntimeError("Araç çağrısı döngüsü sınırı aşıldı")
            tool_steps += 1

            payloads = [
                {
                    "id": call.id or f"call_{tool_steps}_{index}",
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments or "{}",
                    },
                }
                for index, call in enumerate(tool_calls)
            ]
            messages.append({"role": "assistant", "content": content, "tool_calls": payloads})
            for payload in payloads:
                result = await self._execute_tool(
                    agent, payload["function"]["name"], payload["function"]["arguments"]
                )
                messages.append({"role": "tool", "tool_call_id": payload["id"], "content": result})

    async def _chat(self, agent: AgentSpec, messages: list[dict[str, Any]], tools: Optional[list]) -> Any:
        kwargs: dict[str, Any] = {
            "model": agent.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_response_tokens,
        }
        if self.cfg.presence_penalty:
            kwargs["presence_penalty"] = self.cfg.presence_penalty
        if self.cfg.frequency_penalty:
            kwargs["frequency_penalty"] = self.cfg.frequency_penalty
        # NVIDIA'nın Nemotron 3.5 endpoint'i varsayılan olarak yoğun reasoning
        # kullanır; arena kısa yanıt istediği için yalnızca bu modele kapat.
        base_url = self.cfg.agent_a_base_url if agent.key == "A" else self.cfg.agent_b_base_url
        model_name = agent.model.lower()
        if "nvidia.com" in base_url.lower() and model_name.startswith("nvidia/nemotron-3.5-super-vl"):
            kwargs["reasoning_effort"] = "none"
        elif "nvidia.com" in base_url.lower() and model_name.endswith("nvidia/nemotron-3.5-bolt-e4b"):
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = False
        delays = (2, 4, 8)
        for attempt in range(len(delays) + 1):
            last_error: Optional[Exception] = None
            try:
                response = await self.clients[agent.key].chat.completions.create(**kwargs)
                break
            except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
                last_error = exc
                status_code = exc.status_code if isinstance(exc, APIStatusError) else None
                if (
                    isinstance(exc, APIStatusError)
                    and status_code == 400
                    and ("penalty" in str(exc).lower() or "extra" in str(exc).lower())
                ):
                    kwargs.pop("presence_penalty", None)
                    kwargs.pop("frequency_penalty", None)
                    continue
                retryable = not isinstance(exc, APIStatusError) or status_code == 429 or status_code >= 500
            if not retryable or attempt >= len(delays):
                assert last_error is not None
                if isinstance(last_error, APIStatusError):
                    body = last_error.body
                    detail: Any = body
                    if isinstance(body, dict):
                        detail = body.get("error", body)
                        if isinstance(detail, dict):
                            detail = detail.get("message") or detail.get("detail") or detail
                    detail_text = str(detail or last_error)[:1000]
                    request_id = getattr(last_error, "request_id", None)
                    log.error(
                        "API isteği reddedildi: ajan=%s model=%s durum=%s request_id=%s detay=%s",
                        agent.name,
                        agent.model,
                        status_code,
                        request_id or "-",
                        detail_text,
                    )
                    raise RuntimeError(
                        f"{agent.name} API isteği reddedildi ({status_code}): {detail_text}"
                    ) from last_error
                raise last_error
            delay = delays[attempt]
            if status_code == 429:
                status = f"Rate limit (429), {delay}sn bekleniyor..."
            else:
                error_name = f"API hatası ({status_code})" if status_code else "Geçici bağlantı hatası"
                status = f"{error_name}, {delay}sn sonra yeniden denenecek..."
            self._emit(Status(f"{agent.name}: {status}"))
            await asyncio.sleep(delay)
        if not response.choices:
            raise RuntimeError(f"{agent.name}: API boş yanıt döndürdü")
        message = response.choices[0].message

        # Token sayımı: sağlayıcı kullanım bilgisi vermezse kabaca tahmin edilir.
        usage = getattr(response, "usage", None)
        total = getattr(usage, "total_tokens", None) if usage else None
        if total:
            self.tokens[agent.key] += int(total)
        else:
            sent = sum(len(str(m.get("content") or "")) for m in messages)
            self.tokens[agent.key] += (sent + len(message.content or "")) // 4
            self.tokens_estimated[agent.key] = True
        return message

    async def _execute_tool(self, agent: AgentSpec, name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        query = str(args.get("query", "")).strip()

        if name != agent.tool_name:
            return f"HATA: '{name}' bu ajan için tanımlı değil. Yalnızca '{agent.tool_name}' kullanılabilir."

        self._emit(ToolCalled(agent_key=agent.key, tool_name=name, query=query))
        text, is_error = await self._hosts[agent.key].call(name, {"query": query})
        self._emit(
            ToolFinished(
                agent_key=agent.key,
                tool_name=name,
                summary=_summarize(text, is_error),
                is_error=is_error,
            )
        )
        return text

    def _build_user_prompt(self, agent: AgentSpec, rnd: int) -> str:
        opp_key = "B" if agent.key == "A" else "A"
        parts = [
            f"KONU: {self.topic}",
            f"TUR: {rnd}/{self.total_rounds}",
            f"ROLÜN: {agent.name} · {agent.title}",
            "",
            "TARTIŞMA GEÇMİŞİ:",
        ]
        history = self._speeches[-MAX_HISTORY_ENTRIES:]
        if history:
            for sp in history:
                speaker = self.agents[sp.agent_key].name
                parts.append(f"[Tur {sp.round_no} · {speaker}]: {_clip(sp.text, MAX_SPEECH_CHARS_IN_PROMPT)}")
        else:
            parts.append("(Henüz konuşma yok.)")
        parts.append("")

        own_history = [sp for sp in self._speeches if sp.agent_key == agent.key]
        if own_history:
            parts.append("KENDİ ÖNCEKİ KONUŞMALARIN (iddiaları ve cümleleri tekrar etme):")
            for sp in own_history[-2:]:
                parts.append(f"[Tur {sp.round_no}]: {_clip(sp.text, MAX_SPEECH_CHARS_IN_PROMPT)}")
            parts.append("")

            # Daha önce kullanılan kayıtları belirle ve tekrarı engelle
            used_records: list[str] = []
            for sp in own_history:
                for match in re.findall(r"\[([A-Z0-9_\-]+)\]", sp.text):
                    if match not in used_records and any(
                        match.startswith(pfx) for pfx in ("TBK", "EKO", "ANAY", "ARG", "PRO", "YRG", "HMK", "TMK")
                    ):
                        used_records.append(match)

            if used_records:
                records_str = ", ".join(f"[{r}]" for r in used_records)
                parts.append(f"DAHA ÖNCE KULLANDIĞIN KAYITLAR: {records_str}")
                parts.append(
                    "ÖNEMLİ KURAL (Tekrar Yasağı): Bu kayıtları ve argümanları önceki turlarda zaten sundun. "
                    f"Şimdi {agent.tool_name} ile bilgi tabanından HENÜZ KULLANMADIĞIN YENİ ve FARKLI bir kanıt/madde ara "
                    "ve tartışmayı yeni bir boyuta taşı."
                )
                parts.append("")

        if self._notes:
            parts.append("MODERATÖR NOTLARI (tartışma boyunca geçerli, öncelikli):")
            for note in self._notes:
                tag = " [YENİ]" if agent.key not in note.delivered_to else ""
                parts.append(f"- (Tur {note.round_no}){tag} {note.text}")
                # Not bu ajana iletildi; bir sonraki konuşmada [YENİ] olarak işaretlenmez.
                note.delivered_to.add(agent.key)
            parts.append("")

        has_opponent_speech = any(sp.agent_key == opp_key for sp in self._speeches)
        opp_name = self.agents[opp_key].name
        if has_opponent_speech:
            parts.append(
                f"GÖREVİN: Önce {agent.tool_name} aracını çağırarak kanıt topla. "
                f"Sonra {opp_name}'in son argümanına cevap vererek konuşmanı yap."
            )
        else:
            parts.append(
                f"GÖREVİN: Önce {agent.tool_name} aracını çağırarak kanıt topla. "
                "Ardından açılış konuşmanı yap."
            )
        return "\n".join(parts)

    def _emit(self, event: DebateEvent) -> None:
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - arayüz hatası münazarayı durdurmasın
            log.exception("Olay işleyicisi hatası")
