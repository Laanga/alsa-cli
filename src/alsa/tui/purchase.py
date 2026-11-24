"""Compra: elegir tarifa, preparar la compra en Chrome y explicar lo que queda en alsa.es."""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from alsa.checkout import Selection, open_checkout
from alsa.core import get_packages, results_url
from alsa.exceptions import AlsaError
from alsa.models import Journey, Package
from alsa.tui import fmt
from alsa.tui.widgets import BusScene

if TYPE_CHECKING:
    from alsa.tui.screens import SearchRequest

STEPS = ["Abrir Chrome", "Buscar en alsa.es", "Elegir el autobús y la tarifa", "Llegar a «Datos del viajero»"]


@dataclass(frozen=True)
class BuyOutcome:
    """Cómo acabó la compra: en Chrome sobre «Datos» o con la lista de alsa.es abierta."""

    in_chrome: bool
    package: Package | None
    browser_opened: bool = True
    reason: str = ""


@dataclass(frozen=True)
class GoToChrome:
    """Elección en el modal de tarifas: preparar la compra en Chrome con esta tarifa."""

    package: Package | None


def journey_summary(j: Journey, req: SearchRequest) -> Text:
    text = Text()
    text.append(f"{fmt.hm(j.departure)}  ", "bold")
    text.append(j.origin_name)
    text.append("  →  ", "bold #40c8ec")
    text.append(f"{fmt.arrival(j.departure, j.arrival)}  ", "bold")
    text.append(j.destination_name)
    details = f"{fmt.long_date(req.day)}  ·  {fmt.duration(j.duration_minutes)}  ·  {j.service_type or 'autobús'}"
    text.append(f"\n{details}  ·  {fmt.travellers(req.adults)}", "dim")
    return text


def _package_prompt(package: Package, adults: int) -> Text:
    text = Text()
    text.append(f"{package.name:<15}", "bold")
    text.append(f"{fmt.price(package.price):>10}", "bold #4ade80")
    if adults > 1:
        text.append(f"  {fmt.price(package.price / adults)} por viajero", "dim")
    if package.recommended:
        text.append("  recomendada por Alsa", "#40c8ec")
    if package.conditions:
        text.append("\n" + "  ·  ".join(package.conditions), "dim")
    return text


class BuyScreen(ModalScreen[GoToChrome | BuyOutcome | None]):
    """Elige la tarifa del autobús y lanza la compra en Chrome."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "cancelar")]
    VERTICAL_BREAKPOINTS: ClassVar[list[tuple[int, str]]] = [(0, "-short"), (32, "-tall")]

    def __init__(self, journey: Journey, req: SearchRequest) -> None:
        super().__init__()
        self.journey = journey
        self.req = req
        self.packages: list[Package] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="buy-box", classes="dialog"):
            yield Label("Comprar en alsa.es", classes="dialog-title")
            yield Static(journey_summary(self.journey, self.req), id="buy-summary")
            yield Label("Elige la tarifa", classes="dialog-subtitle")
            yield OptionList(Option(Text("Cargando las tarifas de alsa.es…", style="italic dim"), disabled=True), id="packages")
            yield Static(
                "Chrome se abrirá con este autobús y la tarifa ya elegidos, en la página de datos del viajero. "
                "Allí rellenas tus datos y pagas; Alsa suma sus gastos de gestión.",
                classes="dialog-text",
                id="buy-note",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Ir a pagar", id="go", variant="primary", compact=True)
                yield Button("Solo abrir la lista en alsa.es", id="web", compact=True, classes="link")
                yield Button("Cancelar", id="cancel", compact=True, classes="link")

    def on_mount(self) -> None:
        self.query_one("#go", Button).focus()
        self._load_packages()

    @work(thread=True, exclusive=True, group="packages", exit_on_error=False)
    def _load_packages(self) -> list[Package]:
        req = self.req
        return get_packages(
            req.origin.id, req.destination.id, req.alsa_date, req.adults, self.journey.departure, self.journey.origin_id
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "packages" or event.state not in (WorkerState.SUCCESS, WorkerState.ERROR):
            return
        listing = self.query_one("#packages", OptionList)
        listing.clear_options()
        self.packages = event.worker.result if event.state == WorkerState.SUCCESS else []
        if not self.packages:
            listing.add_option(
                Option(Text("No se pudieron cargar las tarifas; se elegirá la más barata.", style="italic #fbbf24"), disabled=True)
            )
            return
        listing.add_options([Option(_package_prompt(p, self.req.adults)) for p in self.packages])
        listing.highlighted = 0
        listing.focus()

    @on(OptionList.OptionSelected, "#packages")
    def _package_chosen(self) -> None:
        self._go()

    @on(Button.Pressed, "#go")
    def _go(self) -> None:
        listing = self.query_one("#packages", OptionList)
        index = listing.highlighted
        package = self.packages[index] if self.packages and index is not None else None
        self.dismiss(GoToChrome(package))

    @on(Button.Pressed, "#web")
    def _web(self) -> None:
        url = results_url(self.req.origin.id, self.req.destination.id, self.req.alsa_date, self.req.adults)
        opened = _open_in_browser(url, self.app)
        self.dismiss(BuyOutcome(in_chrome=False, package=None, browser_opened=opened))

    @on(Button.Pressed, "#cancel")
    def action_close(self) -> None:
        self.workers.cancel_group(self, "packages")
        self.dismiss(None)


def _open_in_browser(url: str, app: App) -> bool:
    try:
        opened = webbrowser.open(url, new=2)
    except webbrowser.Error:
        opened = False
    if not opened:
        app.copy_to_clipboard(url)
    return opened


class CheckoutScreen(ModalScreen[BuyOutcome]):
    """Progreso mientras Chrome hace la búsqueda, elige el bus y llega a «Datos»."""

    VERTICAL_BREAKPOINTS: ClassVar[list[tuple[int, str]]] = [(0, "-short"), (28, "-tall")]

    def __init__(self, journey: Journey, req: SearchRequest, package: Package | None) -> None:
        super().__init__()
        self.journey = journey
        self.req = req
        self.package = package
        self.current = -1

    def compose(self) -> ComposeResult:
        with Vertical(id="checkout-box", classes="dialog"):
            yield Label("Preparando tu compra en Chrome", classes="dialog-title")
            yield BusScene(id="checkout-scene")
            yield Static(id="checkout-steps", classes="dialog-text")
            yield Static(Text("Tarda unos segundos; no cierres la ventana de Chrome.", style="dim"), classes="dialog-text")

    def on_mount(self) -> None:
        self.query_one(BusScene).cruise()
        self._render_steps()
        self._run()

    def _render_steps(self) -> None:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=2)
        grid.add_column()
        labels = list(STEPS)
        labels[2] = f"Elegir el de las {fmt.hm(self.journey.departure)}" + (f" con tarifa {self.package.name}" if self.package else "")
        for i, label in enumerate(labels):
            if i < self.current:
                grid.add_row(Text("✓", "bold #4ade80"), Text(label, "dim"))
            elif i == self.current:
                grid.add_row(Text("›", "bold #40c8ec"), Text(label, "bold"))
            else:
                grid.add_row(Text("·", "dim"), Text(label, "dim"))
        self.query_one("#checkout-steps", Static).update(grid)

    def _step(self, index: int) -> None:
        self.current = index
        self._render_steps()

    @work(thread=True, exclusive=True, group="checkout", exit_on_error=False)
    def _run(self) -> str:
        req, j = self.req, self.journey
        selection = Selection(
            origin_id=req.origin.id,
            destination_id=req.destination.id,
            date=req.alsa_date,
            adults=req.adults,
            departure_ms=int(j.departure.timestamp() * 1000),
            origin_stop_id=j.origin_id,
            package_id=self.package.id if self.package else None,
        )
        return open_checkout(selection, on_step=lambda n: self.app.call_from_thread(self._step, n))

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "checkout":
            return
        if event.state == WorkerState.SUCCESS:
            self.query_one(BusScene).stop()
            self.dismiss(BuyOutcome(in_chrome=True, package=self.package))
        elif event.state == WorkerState.ERROR:
            self.query_one(BusScene).stop()
            error = event.worker.error
            reason = str(error) if isinstance(error, AlsaError) else f"error inesperado: {error}"
            url = results_url(self.req.origin.id, self.req.destination.id, self.req.alsa_date, self.req.adults)
            opened = _open_in_browser(url, self.app)
            self.dismiss(BuyOutcome(in_chrome=False, package=self.package, browser_opened=opened, reason=reason))


class PurchaseScreen(ModalScreen[str | None]):
    """Lo que queda por hacer en el navegador."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "volver a los resultados"),
    ]

    def __init__(self, journey: Journey, req: SearchRequest, outcome: BuyOutcome) -> None:
        super().__init__()
        self.journey = journey
        self.req = req
        self.outcome = outcome

    def compose(self) -> ComposeResult:
        j, outcome = self.journey, self.outcome
        package = outcome.package
        head = Text()
        if outcome.in_chrome:
            title = "Ya puedes pagar en Chrome"
            head.append("✓ Chrome está en «Datos del viajero»", "bold #4ade80")
            head.append(f" con el autobús de las {fmt.hm(j.departure)} desde {j.origin_name}")
            if package:
                head.append(" y la tarifa ")
                head.append(package.name, "bold")
                head.append(" (")
                head.append(fmt.price(package.price), "bold #4ade80")
                head.append(")")
            head.append(".")
            steps = [
                "Acepta o rechaza las cookies de alsa.es.",
                "Rellena los datos de cada viajero y el email y teléfono de contacto.",
                "Elige cómo pagar y pulsa «Pagar». Alsa suma sus gastos de gestión al total.",
            ]
        else:
            title = "Termina la compra en el navegador"
            if outcome.reason:
                head.append(f"No se pudo preparar la compra en Chrome ({outcome.reason}).\n", "#fbbf24")
            if outcome.browser_opened:
                head.append("✓ alsa.es abierto con la lista de autobuses de tu búsqueda.", "bold #4ade80")
            else:
                url = results_url(self.req.origin.id, self.req.destination.id, self.req.alsa_date, self.req.adults)
                head.append("No se pudo abrir el navegador; enlace copiado al portapapeles:\n", "bold #fbbf24")
                head.append(url, "#40c8ec")
            fare = f" y elige la tarifa {package.name}" if package else " y elige la tarifa"
            steps = [
                "Acepta o rechaza las cookies de alsa.es.",
                f"En el de las {fmt.hm(j.departure)} desde {j.origin_name}, pulsa «Ver tarifas»{fare}.",
                "Pulsa «Continuar», rellena los datos de los viajeros y paga.",
            ]
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=3)
        grid.add_column()
        for n, step in enumerate(steps, start=1):
            grid.add_row(Text(f" {n} ", "bold #0b1220 on #40c8ec"), Text(step))
        with Vertical(id="purchase-box", classes="dialog"):
            yield Label(title, classes="dialog-title")
            yield Static(head, classes="dialog-text")
            yield Static(grid, classes="dialog-text")
            yield Static(Text("Alsa te envía el billete por email al terminar el pago.", "dim"), classes="dialog-text")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Volver a los resultados", id="back", variant="primary", compact=True)
                yield Button("Nueva búsqueda", id="new", compact=True, classes="link")
                yield Button("Salir", id="quit", compact=True, classes="link")

    def on_mount(self) -> None:
        self.query_one("#back", Button).focus()

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#new")
    def _new(self) -> None:
        self.dismiss("new")

    @on(Button.Pressed, "#quit")
    def _quit(self) -> None:
        self.dismiss("quit")
