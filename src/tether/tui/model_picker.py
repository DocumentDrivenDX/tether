"""Choose a discovered model before opening its session composition."""

from typing import cast

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select


class ModelPicker(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ModelPicker { align: center middle; }
    ModelPicker > Vertical { width: 80%; height: auto; padding: 1 2; border: solid $primary; }
    ModelPicker Select { width: 1fr; }
    """

    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__()
        self.names = names

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Choose a Databricks model · system.ai chat services")
            yield Select(
                ((name, name) for name in self.names), allow_blank=False, id="model-choice"
            )
            yield Label("Listed access and API metadata do not guarantee invocation permission.")
            yield Button("Start", id="start-model", variant="primary")
            yield Button("Cancel", id="cancel-model")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-model":
            self.dismiss(None)
        elif event.button.id == "start-model":
            selected = cast(Select[str], self.query_one("#model-choice", Select)).value
            if isinstance(selected, str):
                self.dismiss(selected)
