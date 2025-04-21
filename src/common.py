import logging
from typing import Protocol, Tuple, Optional, TypedDict, List, Dict
from rich.logging import RichHandler
from playwright.sync_api import Playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

class LoggerInterface(Protocol):
    def error(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def debug(self, message: str) -> None: ...

class RichLogger(LoggerInterface):
    def __init__(self, log_level: str = "DEBUG"):
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
            markup=True
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



def initialize_browser_and_page(
    p: Playwright, url: str, timeout_ms: int, headless: bool, logger: LoggerInterface
) -> Tuple[Optional[Browser], Optional[Page]]:
    """Launches browser, creates page, sets timeout, and navigates."""
    browser: Optional[Browser] = None
    page: Optional[Page] = None
    try:
        logger.debug(f"Launching Browser (Headless={headless})...")
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.set_default_timeout(timeout_ms)
        logger.info(f"Navigating to {url}...")
        page.goto(url, wait_until="domcontentloaded")
        logger.info("Page Loaded.")
        return browser, page
    except (PlaywrightTimeoutError, PlaywrightError, Exception) as e:
        logger.error(f"Error During Browser Initialization or Navigation: {e}")
        if browser and browser.is_connected():
            try:
                browser.close()
            except Exception as close_err:
                logger.warning(f"Error Closing Browser During Cleanup: {close_err}")
        return None, None

class DocumentationHtmlContent(TypedDict):
    title: str
    tagline: str | None
    inner_html: str


class DocumentationItemRef(TypedDict):
    link_text: str
    relative_path: List[str]

DocumentationMap = Dict[str, DocumentationItemRef]