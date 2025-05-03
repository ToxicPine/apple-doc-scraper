import logging
from typing import Protocol, TypedDict, List, Dict
from rich.logging import RichHandler


class LoggerInterface(Protocol):
    def error(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def debug(self, message: str) -> None: ...


class RichLogger(LoggerInterface):
    def __init__(self, log_level: str = "INFO"):
        self.logger = logging.getLogger("rich")

        # Clear any existing handlers to avoid duplicates
        if self.logger.handlers:
            self.logger.handlers.clear()

        # Set log level based on parameter
        level = getattr(logging, log_level.upper(), logging.INFO)
        self.logger.setLevel(level)

        # Configure rich handler with more visible formatting
        handler = RichHandler(
            show_path=False,
            rich_tracebacks=True,
            log_time_format="[%X]",
            enable_link_path=False,
            markup=True,
        )

        # Use a formatter that preserves rich formatting
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

        # Ensure propagation is disabled to prevent duplicate logs
        self.logger.propagate = False

    def error(self, message: str) -> None:
        self.logger.error(f"[bold red]{message}[/bold red]")

    def warning(self, message: str) -> None:
        self.logger.warning(f"[bold yellow]{message}[/bold yellow]")

    def info(self, message: str) -> None:
        self.logger.info(f"[cyan]{message}[/cyan]")

    def debug(self, message: str) -> None:
        self.logger.debug(f"[dim]{message}[/dim]")


class DocumentationHtmlContent(TypedDict):
    title: str
    tagline: str | None
    inner_html: str


class DocumentationItemRef(TypedDict):
    link_text: str
    relative_path: List[str]


DocumentationMap = Dict[str, DocumentationItemRef]
