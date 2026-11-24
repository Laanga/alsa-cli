"""App de Textual: tema Alsa, carga de paradas y navegación entre pantallas."""

from __future__ import annotations

from typing import ClassVar

from textual import work
from textual.app import App
from textual.binding import Binding, BindingType
from textual.theme import Theme
from textual.widgets._select import SelectOverlay
from textual.worker import Worker, WorkerState

from alsa.core import get_all_stations
from alsa.exceptions import AlsaError
from alsa.tui.screens import HelpScreen, SearchScreen
from alsa.tui.stations import StationIndex

# El desplegable de Select trae su atajo en inglés; lo traducimos para el pie.
SelectOverlay.BINDINGS = [Binding("escape", "dismiss", "cerrar")]

ALSA_THEME = Theme(
    name="alsa",
    primary="#1d6fe0",
    secondary="#40c8ec",
    accent="#40c8ec",
    foreground="#e6ecf5",
    background="#0b1220",
    surface="#121b2e",
    panel="#18233a",
    success="#4ade80",
    warning="#fbbf24",
    error="#f87171",
    dark=True,
)


class AlsaApp(App[None]):
    TITLE = "alsa"
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "salir", priority=True),
        Binding("ctrl+r", "reload_stations", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.stations: StationIndex | None = None
        self.stations_error: str | None = None

    def on_mount(self) -> None:
        self.register_theme(ALSA_THEME)
        self.theme = "alsa"
        self.push_screen(SearchScreen())
        self.load_stations()

    @work(thread=True, exclusive=True, group="stations", exit_on_error=False)
    def load_stations(self) -> StationIndex:
        return StationIndex(get_all_stations())

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "stations":
            return
        if event.state == WorkerState.SUCCESS:
            self.stations = event.worker.result
            self.stations_error = None
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            self.stations_error = str(error) if isinstance(error, AlsaError) else f"error inesperado: {error}"
        else:
            return
        for screen in self.screen_stack:
            if isinstance(screen, SearchScreen):
                screen.refresh_stations_status()

    def action_reload_stations(self) -> None:
        if self.stations is None:
            self.stations_error = None
            for screen in self.screen_stack:
                if isinstance(screen, SearchScreen):
                    screen.refresh_stations_status()
            self.load_stations()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())


def run() -> None:
    AlsaApp().run()
