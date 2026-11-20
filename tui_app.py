"""AI Münazara Arenası: Textual tabanlı terminal arayüzü.

Çalıştırma:  python tui_app.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Static

from orchestrator import (
    Config,
    DebateEvent,
    DebateFinished,
    DebateOrchestrator,
    ErrorOccurred,
    ModeratorNote,
    SpeechDone,
    Status,
    ToolCalled,
    ToolFinished,
    TurnStarted,
)

APP_TITLE = "AI Münazara Arenası"
DEFAULT_TOPIC = "Konut kiralarında yasal kira artış sınırları kiracıyı mı, ev sahibini mi korumalıdır?"
DEFAULT_ROUNDS = 10
MIN_ROUNDS, MAX_ROUNDS = 1, 30
DISCLAIMER = "Simülasyon: bağlayıcı hukuki tavsiye değildir."
AGENT_ROLES = {
    "A": "Avukat · Kiracı Temsilcisi",
    "B": "Avukat · Ev Sahibi Temsilcisi & Ekonomist",
}


def other(key: str) -> str:
    return "B" if key == "A" else "A"


def aligned_row(agent_key: str, widget: Widget) -> Horizontal:
    """Ajan A'yı sola, Ajan B'yi sağa hizalar."""
    side = "row-left" if agent_key == "A" else "row-right"
    return Horizontal(widget, classes=f"row {side}")


def tool_log_text(tool: str, query: str, summary: Optional[str] = None, is_error: bool = False) -> Text:
    text = Text()
    text.append("[MCP Tool -> ", style="bold #facc15")
    text.append(f'{tool}("{query}")', style="#fde047")
    text.append("]", style="bold #facc15")
    if summary is not None:
        text.append(f"  {summary}", style="bold #f87171" if is_error else "#a3e635")
    return text


class AgentCard(Static):
    """Yan panel: ajanın rolü, modeli, harcanan token ve son MCP sorgusu."""

    def __init__(self, *, agent_key: str, role: str, model: str, base_url: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.agent_key = agent_key
        self.role_text = role
        self.model_name = model
        self.base_url = base_url
        self.token_count = 0
        self.tokens_estimated = False
        self.last_query = "—"
        self.last_result = "—"
        self.state_text = "Bekliyor"

    def on_mount(self) -> None:
        self.refresh_card()

    def clear_state(self) -> None:
        self.token_count = 0
        self.tokens_estimated = False
        self.last_query = "—"
        self.last_result = "—"
        self.state_text = "Bekliyor"
        self.refresh_card()

    def refresh_card(self) -> None:
        tokens = f"{self.token_count:,}".replace(",", ".")
        if self.tokens_estimated and self.token_count:
            tokens = "≈ " + tokens
        text = Text()
        text.append("Rol\n", style="bold")
        text.append(f"{self.role_text}\n\n")
        text.append("Model\n", style="bold")
        text.append(f"{self.model_name}\n", style="cyan")
        text.append(f"{self.base_url}\n\n", style="dim")
        text.append("Harcanan token\n", style="bold")
        text.append(f"{tokens}\n\n", style="green")
        text.append("Son MCP sorgusu\n", style="bold")
        text.append(f"{self.last_query}\n", style="italic #fde047")
        text.append(f"Sonuç: {self.last_result}\n\n", style="#a3e635")
        text.append("Durum: ", style="bold")
        text.append(self.state_text)
        self.update(text)


class DebateArenaApp(App):
    TITLE = APP_TITLE

    CSS = """
    Screen { background: #0b1020; }
    Header { background: #111827; color: #e5e7eb; }

    #body { height: 1fr; }

    AgentCard {
        width: 32;
        height: 1fr;
        padding: 0 1;
        background: #111827;
    }
    #card-a { border: round #3b82f6; }
    #card-b { border: round #f59e0b; }

    #arena {
        width: 1fr;
        height: 1fr;
        border: round #475569;
        padding: 0 1;
        background: #0f172a;
    }

    .row { height: auto; width: 100%; margin-bottom: 1; }
    .row-left { align-horizontal: left; }
    .row-right { align-horizontal: right; }

    .bubble { width: 80%; height: auto; padding: 0 1; }
    .bubble-a { border: round #3b82f6; color: #dbeafe; background: #0f1d3a; }
    .bubble-b { border: round #f59e0b; color: #fef3c7; background: #2a1d07; }

    .mcp-log {
        width: 80%;
        height: auto;
        padding: 0 1;
        border: heavy #eab308;
        color: #fde047;
        background: #1c1917;
    }

    .moderator {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        border: dashed #22c55e;
        color: #dcfce7;
        background: #052e16;
    }

    .error {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        border: heavy #ef4444;
        color: #fecaca;
        background: #2a0a0a;
    }

    #controls { height: auto; padding: 0 1; background: #111827; }
    #statusline { height: 1; color: #93c5fd; }
    .ctrl-row { height: auto; }
    #topic { width: 1fr; }
    #rounds { width: 12; }
    #moderator { width: 1fr; }
    #btn-mod { width: 14; min-width: 14; }
    .ctrl-row Button { margin-right: 1; }
    """

    BINDINGS = [
        Binding("s", "start", "Başlat"),
        Binding("p", "pause", "Duraklat/Devam"),
        Binding("r", "reset", "Sıfırla"),
        Binding("q", "quit", "Çıkış"),
        Binding("ctrl+q", "quit", "Çıkış", priority=True, show=False),
        Binding("escape", "blur", "Girişten çık", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config.from_env()
        self.orch = DebateOrchestrator(self.cfg, self._handle_event)
        self._debate_worker = None
        self._debate_active = False
        self._round_no = 0
        self._round_total = 0
        self._status_text = "Hazır"
        self._pending_log: Optional[tuple[Static, str, str]] = None

    # ------------------------------------------------------------------ düzen
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)

        card_a = AgentCard(
            agent_key="A",
            role=AGENT_ROLES["A"],
            model=self.cfg.agent_a_model or "(tanımlı değil)",
            base_url=self.cfg.agent_a_base_url or "(tanımlı değil)",
            id="card-a",
        )
        card_a.border_title = "Ajan A"
        card_b = AgentCard(
            agent_key="B",
            role=AGENT_ROLES["B"],
            model=self.cfg.agent_b_model or "(tanımlı değil)",
            base_url=self.cfg.agent_b_base_url or "(tanımlı değil)",
            id="card-b",
        )
        card_b.border_title = "Ajan B"

        with Horizontal(id="body"):
            yield card_a
            yield VerticalScroll(id="arena")
            yield card_b

        with Vertical(id="controls"):
            yield Static("", id="statusline")
            with Horizontal(classes="ctrl-row"):
                yield Input(value=DEFAULT_TOPIC, placeholder="Münazara konusu", id="topic")
                yield Input(value=str(DEFAULT_ROUNDS), placeholder="Tur", id="rounds")
            with Horizontal(classes="ctrl-row"):
                yield Input(placeholder="Moderatör müdahalesi (Enter ile gönder)", id="moderator")
                yield Button("Gönder", id="btn-mod")
            with Horizontal(classes="ctrl-row"):
                yield Button(Text("[S] Başlat"), id="btn-start", variant="success")
                yield Button(Text("[P] Duraklat/Devam"), id="btn-pause", variant="warning")
                yield Button(Text("[R] Sıfırla"), id="btn-reset", variant="error")
                yield Button(Text("[Q] Çıkış"), id="btn-quit")

        yield Footer()

    def on_mount(self) -> None:
        self._status(f"{DISCLAIMER} Harf tuşları için önce Esc ile giriş alanından çıkın.")
        issues = self.cfg.problems()
        if issues:
            self._status("Yapılandırma eksik: " + "; ".join(issues) + " (.env dosyasını kontrol edin)")
        self.query_one("#topic", Input).focus()

    # ----------------------------------------------------------- kullanıcı eylemleri
    def action_start(self) -> None:
        if self._debate_active or self.orch.is_running:
            self._status("Münazara zaten çalışıyor.")
            return
        topic = self.query_one("#topic", Input).value.strip()
        if not topic:
            self._status("Konu boş olamaz.")
            return
        raw_rounds = self.query_one("#rounds", Input).value.strip()
        try:
            rounds = int(raw_rounds)
        except ValueError:
            rounds = -1
        if not MIN_ROUNDS <= rounds <= MAX_ROUNDS:
            self._status(f"Tur sayısı {MIN_ROUNDS} ile {MAX_ROUNDS} arasında olmalı.")
            return
        issues = self.cfg.problems()
        if issues:
            self._status("Yapılandırma eksik: " + "; ".join(issues))
            return

        self._clear_view()
        self._debate_active = True
        self._round_total = rounds
        self._debate_worker = self.run_worker(
            self.orch.run(topic, rounds),
            group="debate",
            exclusive=True,
            exit_on_error=False,
            name="debate",
        )
        self._status("Münazara başlatılıyor...")

    def action_pause(self) -> None:
        if not self.orch.is_running:
            self._status("Önce münazarayı başlatın.")
            return
        if self.orch.is_paused:
            self.orch.resume()
        else:
            self.orch.pause()

    def action_reset(self) -> None:
        if self._debate_worker is not None and self._debate_worker.is_running:
            self._debate_worker.cancel()
        self._debate_worker = None
        self.orch.reset()
        self._debate_active = False
        self._clear_view()
        self._status("Sıfırlandı. Yeni konu girip [S] ile başlatın.")

    async def action_quit(self) -> None:
        if self._debate_worker is not None and self._debate_worker.is_running:
            self._debate_worker.cancel()
        await self.orch.shutdown()
        self.exit()

    def action_blur(self) -> None:
        self.screen.set_focus(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("topic", "rounds"):
            self.action_start()
        elif event.input.id == "moderator":
            self._send_moderator(event.input)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-start":
            self.action_start()
        elif button_id == "btn-pause":
            self.action_pause()
        elif button_id == "btn-reset":
            self.action_reset()
        elif button_id == "btn-mod":
            self._send_moderator(self.query_one("#moderator", Input))
        elif button_id == "btn-quit":
            await self.action_quit()

    def _send_moderator(self, box: Input) -> None:
        text = box.value.strip()
        if not text:
            return
        if not self.orch.is_running:
            self._status("Moderatör notu yalnızca münazara sırasında gönderilebilir.")
            return
        if self.orch.add_moderator_note(text):
            box.value = ""

    # ----------------------------------------------------------- orkestratör olayları
    def _handle_event(self, event: DebateEvent) -> None:
        try:
            self._dispatch(event)
        except Exception as exc:  # noqa: BLE001 - arayüz hatası münazarayı düşürmesin
            self._status(f"Arayüz hatası: {exc}")

    def _dispatch(self, ev: DebateEvent) -> None:
        if isinstance(ev, TurnStarted):
            self._round_no = ev.round_no
            self._round_total = ev.total_rounds
            name = self.orch.agents[ev.agent_key].name
            self._card_state(ev.agent_key, "düşünüyor...")
            self._card_state(other(ev.agent_key), "dinliyor")
            self._status(f"{name} Düşünüyor...")

        elif isinstance(ev, ToolCalled):
            log_widget = Static(tool_log_text(ev.tool_name, ev.query), classes="mcp-log")
            self._pending_log = (log_widget, ev.tool_name, ev.query)
            self._append(aligned_row(ev.agent_key, log_widget))
            card = self._card(ev.agent_key)
            card.last_query = f'{ev.tool_name}("{ev.query}")'
            card.last_result = "bekleniyor..."
            card.state_text = "MCP sorgulanıyor"
            card.refresh_card()
            self._status("MCP Sorgulanıyor...")

        elif isinstance(ev, ToolFinished):
            card = self._card(ev.agent_key)
            card.last_result = ev.summary
            card.state_text = "düşünüyor"
            card.refresh_card()
            if self._pending_log is not None:
                widget, tool, query = self._pending_log
                widget.update(tool_log_text(tool, query, ev.summary, ev.is_error))
            self._status(f"{self.orch.agents[ev.agent_key].name} Düşünüyor...")

        elif isinstance(ev, SpeechDone):
            card = self._card(ev.agent_key)
            card.token_count = ev.total_tokens
            card.tokens_estimated = ev.tokens_estimated
            card.state_text = "konuştu"
            card.refresh_card()
            bubble = Static(Text(ev.text), classes=f"bubble bubble-{ev.agent_key.lower()}")
            bubble.border_title = f"{self.orch.agents[ev.agent_key].name} · Tur {ev.round_no}"
            self._append(aligned_row(ev.agent_key, bubble))
            self._pending_log = None

        elif isinstance(ev, ModeratorNote):
            note = Static(
                Text.assemble(
                    ("MODERATÖR", "bold #86efac"),
                    (f" · Tur {ev.round_no}\n", "#86efac"),
                    (ev.text, "#dcfce7"),
                ),
                classes="moderator",
            )
            self._append(note)
            self._status("Moderatör notu iletildi; sıradaki konuşmada uygulanacak.")

        elif isinstance(ev, Status):
            self._status(ev.text)

        elif isinstance(ev, ErrorOccurred):
            self._append(Static(Text(f"HATA: {ev.message}"), classes="error"))
            self._status(f"Hata: {ev.message}")

        elif isinstance(ev, DebateFinished):
            self._debate_active = False
            self._pending_log = None
            outcome = {"completed": "bitti", "stopped": "durdu", "error": "hata"}.get(ev.reason, ev.reason)
            self._card_state("A", outcome)
            self._card_state("B", outcome)
            messages = {
                "completed": "Münazara tamamlandı.",
                "stopped": "Münazara durduruldu.",
                "error": "Münazara hata ile sonlandı (ayrıntı arena panelinde).",
            }
            self._status(messages.get(ev.reason, "Hazır"))

    # ----------------------------------------------------------- yardımcılar
    def _card(self, key: str) -> AgentCard:
        return self.query_one(f"#card-{key.lower()}", AgentCard)

    def _card_state(self, key: str, state: str) -> None:
        card = self._card(key)
        card.state_text = state
        card.refresh_card()

    def _append(self, widget: Widget) -> None:
        arena = self.query_one("#arena", VerticalScroll)
        arena.mount(widget)
        self.call_after_refresh(arena.scroll_end, animate=False)

    def _clear_view(self) -> None:
        self.query_one("#arena", VerticalScroll).remove_children()
        self._card("A").clear_state()
        self._card("B").clear_state()
        self._pending_log = None
        self._round_no = 0
        self._round_total = 0
        self._status(self._status_text)

    def _status(self, text: str) -> None:
        short = text.splitlines()[0][:70] if text else ""
        self._status_text = short
        self.query_one("#statusline", Static).update(Text(text))
        self._refresh_subtitle()

    def _refresh_subtitle(self) -> None:
        if self._round_total:
            self.sub_title = f"Tur: {self._round_no}/{self._round_total} · {self._status_text}"
        else:
            self.sub_title = f"Tur: —/— · {self._status_text}"


if __name__ == "__main__":
    log_files = [
        Path(__file__).resolve().parent / "logs" / name
        for name in ("munazara.log", "mcp_tenant.log", "mcp_landlord.log")
    ]
    log_offsets = {
        path: path.stat().st_size if path.exists() else 0
        for path in log_files
    }
    try:
        DebateArenaApp().run()
    finally:
        # TUI kapandıktan sonra terminal normale döner; bu oturumun loglarını
        # burada göstererek kullanıcıya görünür hale getir.
        import logging

        logging.shutdown()
        printed = False
        for path in log_files:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8", errors="replace") as log_file:
                log_file.seek(log_offsets[path])
                content = log_file.read().strip()
            if content:
                print(f"\n--- {path.name} ---\n{content}")
                printed = True
        if not printed:
            print("\nBu oturumda log kaydı oluşmadı.")
