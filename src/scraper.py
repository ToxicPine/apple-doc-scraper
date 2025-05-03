import re
import requests
import unicodedata
import asyncio
from pathlib import Path
from typing import Optional, List, Callable, Tuple, Literal
import click
import threading
from src.parser import parse_apple_docs_json
from playwright.async_api import (
    Page,
    Error as PlaywrightError,
    TimeoutError,
    Browser,
    Playwright,
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)
from src.common import (
    LoggerInterface,
    RichLogger,
    DocumentationMap,
    DocumentationHtmlContent,
)
from src.clean_html import perform_html_cleaning

type UrlAndSaveLocation = Tuple[str, List[str]]


async def extract_documentation_content(
    url: str, page: Page, timeout_ms: int, logger: LoggerInterface
) -> Optional[DocumentationHtmlContent]:
    """
    Extracts the title, tagline, and content HTML from an Apple documentation page.

    Args:
        url: The URL of the Apple documentation page to scrape
        page: The Playwright page object to use
        timeout_ms: Maximum time to wait for page load in milliseconds
        logger: Optional logger interface for logging

    Returns:
        A DocumentationContent object with title, tagline, and inner_html fields,
        or None if extraction failed
    """
    logger.info(f"Extracting Documentation Content From: {url}")

    try:
        # Navigate to the URL
        logger.info(f"Navigating to {url}")
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        # Wait for the documentation hero section to be visible
        logger.info("Waiting for Documentation Hero Section...")
        hero_section = page.locator(".documentation-hero")
        await hero_section.wait_for(state="visible", timeout=timeout_ms)

        # Extract title from the hero section
        title_element = hero_section.locator(".title").first
        await title_element.wait_for(state="visible", timeout=timeout_ms)
        title_text = await title_element.text_content()
        if title_text:
            title = title_text.strip()
            logger.info(f"Extracted title: {title}")
        else:
            logger.error("No Title Element Found in the Hero Section.")
            return None

        # Extract tagline from the hero section
        # Try to locate the tagline using the primary selector
        tagline_element = hero_section.locator(".abstract").first
        tagline_text: str | None = None

        if not await tagline_element.is_visible(timeout=timeout_ms):
            logger.info("Abstract Not Found")
            tagline_element = hero_section.locator(".description").first
            if not await tagline_element.is_visible(timeout=timeout_ms):
                logger.info("Description Not Found, Trying Final Alternative...")
                tagline_element = hero_section.locator(".tagline").first
                tagline_text = None
        else:
            tagline_text = await tagline_element.text_content()
            if tagline_text:
                tagline_text = tagline_text.strip()
                logger.info(f"Extracted tagline: {tagline_text}")
            else:
                logger.error("No Tagline Element Found in the Hero Section.")
                tagline_text = None

        # Wait for the main content section
        logger.info("Waiting for Main Content Section...")
        content_section = page.locator(".doc-content").first
        if not await content_section.is_visible(timeout=timeout_ms):
            logger.info(".doc-content Not Found, Trying Alternative Selector...")
            content_section = page.locator(".declarations-container").first

        # Extract the inner HTML of the content section
        inner_html = await content_section.inner_html()

        # Create and return the result
        result: DocumentationHtmlContent = {
            "title": title,
            "tagline": tagline_text,
            "inner_html": inner_html,
        }

        logger.info("Successfully Extracted Documentation Content.")
        return result

    except TimeoutError:
        logger.error("Timeout Waiting for Page Elements")
        return None
    except PlaywrightError as e:
        logger.error(f"Playwright Error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected Error During Extraction: {e}")
        return None


async def worker(
    items: List[UrlAndSaveLocation],
    page: Page,
    timeout_ms: int,
    on_success: Callable[[UrlAndSaveLocation, DocumentationHtmlContent], None],
    on_failure: Callable[[UrlAndSaveLocation], None],
    logger: LoggerInterface,
) -> None:
    """Worker function that processes a chunk of URLs in parallel."""
    for url, relative_path in items:
        try:
            result = await extract_documentation_content(url, page, timeout_ms, logger)
            if result:
                logger.info(f"Successfully Extracted Documentation Content for {url}")
                on_success((url, relative_path), result)
            else:
                logger.error(f"Failed to Extract Documentation Content for {url}")
                on_failure((url, relative_path))
        except Exception as e:
            logger.error(f"Error During Processing {url}: {e}")
            on_failure((url, relative_path))


def sanitize_filename(name: str) -> str:
    """Sanitizes a string to be used as a valid filename."""
    if not name:
        return "untitled"
    # Normalize unicode characters
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    # Replace common separators and problematic characters with underscores
    name = re.sub(r'[\s/\\:<>|?*"]+', "_", name)
    # Remove characters that are definitely invalid (redundant after previous step, but safe)
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    # Replace multiple underscores with single underscore
    name = re.sub(r"_+", "_", name)
    # Remove leading/trailing underscores/periods
    name = name.strip("._")
    # Limit length (optional, but good practice)
    max_len = 200
    if len(name) > max_len:
        # Try to keep the beginning, cutting at the last underscore before max_len
        safe_name = name[:max_len]
        last_underscore = safe_name.rfind("_")
        if last_underscore > 0:
            name = safe_name[:last_underscore]
        else:
            name = safe_name
        name = name.strip("._")
        if len(name) == 0:
            name = "untitled_long"

    if not name:
        name = "untitled"
    return name


def filter_urls_to_fetch(
    urls_to_process_map: DocumentationMap, output_dir: Path, logger: LoggerInterface
) -> tuple[List[UrlAndSaveLocation], DocumentationMap]:
    """
    Checks for existing files based on link text and hierarchy, determines which URLs need fetching.

    Args:
        urls_to_process_map: Dictionary mapping URLs to (folder_path_parts, link_text).
        output_dir: The base directory where output files are stored.
        logger: Logger interface for logging messages.

    Returns:
        A tuple containing:
        - list[str]: A list of URLs that need to be fetched.
        - int: The count of URLs skipped due to existing files (pre-check).
    """
    urls_to_fetch: List[UrlAndSaveLocation] = []
    skipped_items: DocumentationMap = {}
    logger.info(
        "Checking for Existing Files Based on Map File Link Text and Hierarchy..."
    )

    for url, item in urls_to_process_map.items():
        expected_filename_base = sanitize_filename(item["link_text"])
        expected_target_dir = output_dir.joinpath(*item["relative_path"])
        expected_target_path = expected_target_dir / f"{expected_filename_base}.html"

        if expected_target_path.exists():
            skipped_items[url] = item
        else:
            if not expected_target_dir.exists():
                logger.debug(
                    f"Pre-check Assumed: Target Directory {expected_target_dir} Doesn't Exist Yet."
                )
            logger.debug(
                f"Pre-check Assumed: File Not Found at {expected_target_path}, Adding URL {url}"
            )
            urls_to_fetch.append((url, item["relative_path"]))

    logger.info(f"Pre-check Complete. Found {len(urls_to_process_map)} Total Links.")
    logger.info(
        f"Skipping {len(skipped_items)} URLs based on Existing Files (Pre-check)."
    )
    logger.info(
        f"Will Attempt to Fetch Content for {len(urls_to_fetch)} Remaining URLs."
    )

    return urls_to_fetch, skipped_items


def save_content(
    url: str,
    relative_path: List[str],
    content: DocumentationHtmlContent,
    output_dir: Path,
    logger: LoggerInterface,
) -> Literal["success", "skipped", "failed"]:
    """
    Saves a single documentation content to an HTML file.

    Args:
        url: The source URL of the documentation.
        relative_path: The relative path to the documentation.
        content: The DocumentationHtmlContent to save.
        output_dir: The directory to save the file in.
        logger: Logger interface for logging messages.

    Returns:
        bool: True if the content was successfully saved, False otherwise.
    """
    try:
        # Use the ACTUAL fetched title for the final filename
        filename_base = sanitize_filename(content["title"])
        # Construct the target directory using the path parts
        target_dir = output_dir.joinpath(*relative_path)
        target_path = target_dir / f"{filename_base}.html"

        # Check if file already exists
        if target_path.exists():
            logger.warning(f"Skipping Write, File Already Exists: {target_path}")
            return "skipped"

        # Ensure the directory exists
        target_dir.mkdir(parents=True, exist_ok=True)

        # Clean the HTML content
        allowed_tags = {
            "button",
            "div",
            "span",
            "p",
            "a",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "h7",
            "h8",
            "h9",
            "h10",
        }

        cleaned_html = perform_html_cleaning(
            content["inner_html"], allowed_tags=allowed_tags, logger=logger
        )

        # Write the file
        with target_path.open("w", encoding="utf-8") as f:
            f.write(f"<!-- Title: {content['title']} -->\n")
            if content["tagline"]:
                f.write(f"<!-- Tagline: {content['tagline']} -->\n")
            f.write(f"<!-- Source URL: {url} -->\n\n")
            f.write(cleaned_html)

        logger.debug(f"Saved Content for '{content['title']}' to {target_path}")
        return "success"

    except OSError as e:
        logger.error(f"OS Error Writing File: {e}")
        return "failed"
    except Exception as e:
        logger.error(f"Unexpected Error Writing File: {e}")
        return "failed"


class ResultStorage:
    """
    A class that holds the results of the scraping process.
    """

    url_status_map: dict[str, Literal["success", "skipped", "failed"]] = {}
    _lock: threading.RLock

    def __init__(self):
        self._lock = threading.RLock()

    def add_url_status(
        self, url: str, status: Literal["success", "skipped", "failed"]
    ) -> None:
        with self._lock:
            self.url_status_map[url] = status

    def get_saved_urls(self) -> List[str]:
        with self._lock:
            return [
                url
                for url, status in self.url_status_map.items()
                if status == "success"
            ]

    def get_skipped_urls(self) -> List[str]:
        with self._lock:
            return [
                url
                for url, status in self.url_status_map.items()
                if status == "skipped"
            ]

    def get_failed_urls(self) -> List[str]:
        with self._lock:
            return [
                url for url, status in self.url_status_map.items() if status == "failed"
            ]


async def initialize_browser(
    p: Playwright, headless: bool, logger: LoggerInterface
) -> Browser:
    """Launches browser, creates page, sets timeout, and navigates."""
    try:
        logger.debug(f"Launching Browser (Headless={headless})...")
        browser = await p.chromium.launch(headless=headless)
        return browser
    except (PlaywrightTimeoutError, PlaywrightError, Exception) as e:
        logger.error(f"Error During Browser Initialization or Navigation: {e}")
        raise e


async def async_main(
    documentation: Tuple[str, ...],
    num_workers: int,
    output_dir: Path,
    timeout: int,
    headless: bool,
):
    """
    Scrapes Apple documentation pages listed in an JSON file,
    saving the content to a structured output directory.

    It first checks if an expected file (based on link text from the JSON file)
    exists in the output directory. If it does, the URL is skipped (pre-check).
    Otherwise, it fetches the content. Before saving the fetched content,
    it performs a final check using the *fetched title* to avoid overwriting.
    """
    logger = RichLogger()

    urls_to_process_map: DocumentationMap = {}

    for doc_name in documentation:
        doc_url = (
            f"https://developer.apple.com/tutorials/data/documentation/{doc_name}.json"
        )

        logger.info(f"Fetching documentation index from: {doc_url}")
        response = requests.get(doc_url)
        response.raise_for_status()
        data = response.text

        # Parse the current documentation and merge with existing results
        current_map = parse_apple_docs_json(data, logger)
        if current_map:
            urls_to_process_map.update(current_map)

    del data

    output_dir.mkdir(parents=True, exist_ok=True)

    if not urls_to_process_map:
        logger.error("Exiting Due to Issues Parsing the Map File or Finding Links.")
        return 1

    # --- Pre-check for Existing Files ---
    urls_to_fetch, skipped_items = filter_urls_to_fetch(
        urls_to_process_map, output_dir, logger
    )

    if not urls_to_fetch:
        logger.info("No URLs Left to Fetch after Pre-check. Exiting.")
        return 1

    # --- Initialize Browser & Fetch ---
    result_storage = ResultStorage()
    chunks = [urls_to_fetch[i::num_workers] for i in range(num_workers)]

    def on_success(
        url_and_save_location: UrlAndSaveLocation,
        documentation_data: DocumentationHtmlContent,
    ) -> None:
        url, relative_path = url_and_save_location
        saving_result = save_content(
            url, relative_path, documentation_data, output_dir, logger
        )
        result_storage.add_url_status(url, saving_result)
        return None

    def on_failure(url_and_save_location: UrlAndSaveLocation) -> None:
        url, relative_path = url_and_save_location
        result_storage.add_url_status(url, "failed")
        return None

    async with async_playwright() as p:
        browser = await initialize_browser(p, headless, logger)
        pages = [await browser.new_page() for _ in range(num_workers)]

        # Create and run worker tasks
        tasks = [
            worker(chunks[i], pages[i], timeout, on_success, on_failure, logger)
            for i in range(num_workers)
        ]
        await asyncio.gather(*tasks)
        await browser.close()

    # Final Summary
    total_links = len(urls_to_process_map)
    saved_urls = result_storage.get_saved_urls()
    skipped_urls = result_storage.get_skipped_urls()
    failed_urls = result_storage.get_failed_urls()

    completion_message = f"--- Summary ---"
    completion_message += f"\nTotal Links Found in Map: {total_links}"
    completion_message += f"\nSuccessfully Saved: {len(saved_urls)}"
    completion_message += f"\nSkipped (Pre-check): {len(skipped_items)}"
    completion_message += f"\nSkipped (Other Reasons): {len(skipped_urls)}"
    completion_message += f"\nFailed (Fetch or Write Error): {len(failed_urls)}"
    logger.info(completion_message)


@click.command()
@click.option(
    "--documentation",
    type=str,
    multiple=True,
    required=True,
    help="Name of the Apple Documentation.",
)
@click.option(
    "--num-workers",
    type=int,
    default=4,
    show_default=True,
    help="Number of workers to use for fetching content.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    required=True,
    help="Path to the output directory where fetched documents will be saved.",
)
@click.option(
    "--timeout",
    type=int,
    default=30000,
    show_default=True,
    help="Timeout in milliseconds for page operations.",
)
@click.option(
    "--headless",
    is_flag=True,
    default=True,
    show_default=True,
    help="Run the browser in headless mode.",
)
def main(
    documentation: Tuple[str, ...],
    num_workers: int,
    output_dir: Path,
    timeout: int,
    headless: bool,
):
    asyncio.run(async_main(documentation, num_workers, output_dir, timeout, headless))


if __name__ == "__main__":
    main()
