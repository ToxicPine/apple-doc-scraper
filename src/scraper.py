import re
import requests
import unicodedata
from pathlib import Path
from typing import Optional, List
import click
from src.parser import parse_apple_docs_json
from playwright.sync_api import Page, Error as PlaywrightError, TimeoutError, Browser, sync_playwright
from src.common import LoggerInterface, RichLogger, initialize_browser_and_page, DocumentationMap, DocumentationHtmlContent
from src.clean_html import perform_html_cleaning, ALLOWED_BY_DEFAULT_TAGS


def extract_documentation_content(
    url: str,
    page: Page,
    timeout_ms: int,
    logger: Optional[LoggerInterface] = None
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
    if logger is None:
        logger = RichLogger()
    
    logger.info(f"Extracting Documentation Content From: {url}")
    
    try:
        # Navigate to the URL
        logger.info(f"Navigating to {url}")
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        
        # Wait for the documentation hero section to be visible
        logger.info("Waiting for Documentation Hero Section...")
        hero_section = page.locator(".documentation-hero")
        hero_section.wait_for(state="visible", timeout=timeout_ms)
        
        # Extract title from the hero section
        title_element = hero_section.locator(".title").first
        title_element.wait_for(state="visible", timeout=timeout_ms)
        title_text = title_element.text_content()
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

        if not tagline_element.is_visible(timeout=timeout_ms):
            logger.info("Abstract Not Found")
            tagline_element = hero_section.locator(".description").first
            if not tagline_element.is_visible(timeout=timeout_ms):
                logger.info("Description Not Found, Trying Final Alternative...")
                tagline_element = hero_section.locator(".tagline").first
                tagline_text = None
        else:
            tagline_text = tagline_element.text_content()
            if tagline_text:
                tagline_text = tagline_text.strip()
                logger.info(f"Extracted tagline: {tagline_text}")
            else:
                logger.error("No Tagline Element Found in the Hero Section.")
                tagline_text = None
        
        # Wait for the main content section
        logger.info("Waiting for Main Content Section...")
        content_section = page.locator(".doc-content").first
        if not content_section.is_visible(timeout=timeout_ms):
            logger.info(".doc-content Not Found, Trying Alternative Selector...")
            content_section = page.locator(".declarations-container").first
        
        # Extract the inner HTML of the content section
        inner_html = content_section.inner_html()
        
        # Create and return the result
        result: DocumentationHtmlContent = {
            "title": title,
            "tagline": tagline_text,
            "inner_html": inner_html
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


def extract_multiple_documentation_contents(
    page: Page,
    urls: list[str],
    timeout_ms: int,
    logger: Optional[LoggerInterface] = None
) -> Optional[dict[str, DocumentationHtmlContent]]:
    """
    Extracts documentation content from multiple URLs.
    
    Args:
        urls: List of URLs to extract documentation from
        timeout_ms: Maximum time to wait for page load in milliseconds
        headless: Whether to run the browser in headless mode
        logger: Optional logger interface for logging
    
    Returns:
        A dictionary mapping URLs to their extracted DocumentationContent objects,
        or None for URLs where extraction failed
    """
    if logger is None:
        logger = RichLogger()
    
    logger.info(f"Extracting Documentation Content from {len(urls)} URLs")
    results: dict[str, DocumentationHtmlContent] = {}
            
    # Process each URL
    for url in urls:
        logger.info(f"Processing URL: {url}")
        result = extract_documentation_content(url, page, timeout_ms, logger)
        if result:
            results[url] = result
        else:
            logger.error(f"Failed to Extract Documentation Content for URL: {url}")
        
    logger.info(f"Completed Extraction for {len(urls)} URLs")
    return results


def sanitize_filename(name: str) -> str:
    """Sanitizes a string to be used as a valid filename."""
    if not name:
        return "untitled"
    # Normalize unicode characters
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    # Replace common separators and problematic characters with underscores
    name = re.sub(r'[\s/\\:<>|?*"]+', '_', name)
    # Remove characters that are definitely invalid (redundant after previous step, but safe)
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Replace multiple underscores with single underscore
    name = re.sub(r'_+', '_', name)
    # Remove leading/trailing underscores/periods
    name = name.strip('._')
    # Limit length (optional, but good practice)
    max_len = 200
    if len(name) > max_len:
        # Try to keep the beginning, cutting at the last underscore before max_len
        safe_name = name[:max_len]
        last_underscore = safe_name.rfind('_')
        if last_underscore > 0:
             name = safe_name[:last_underscore]
        else:
             name = safe_name
        name = name.strip('._')
        if len(name) == 0 : name = "untitled_long"

    if not name:
        name = "untitled"
    return name

def filter_urls_to_fetch(
    urls_to_process_map: DocumentationMap,
    output_dir: Path,
    logger: LoggerInterface
) -> tuple[List[str], DocumentationMap]:
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
    urls_to_fetch: List[str] = []
    skipped_items: DocumentationMap = {}
    logger.info("Checking for Existing Files Based on Map File Link Text and Hierarchy...")

    for url, item in urls_to_process_map.items():
        expected_filename_base = sanitize_filename(item['link_text'])
        expected_target_dir = output_dir.joinpath(*item['relative_path'])
        expected_target_path = expected_target_dir / f"{expected_filename_base}.html"

        if expected_target_path.exists():
            skipped_items[url] = item
        else:
            if not expected_target_dir.exists():
                logger.debug(f"Pre-check Assumed: Target Directory {expected_target_dir} Doesn't Exist Yet.")
            logger.debug(f"Pre-check Assumed: File Not Found at {expected_target_path}, Adding URL {url}")
            urls_to_fetch.append(url)

    logger.info(f"Pre-check Complete. Found {len(urls_to_process_map)} Total Links.")
    logger.info(f"Skipping {len(skipped_items)} URLs based on Existing Files (Pre-check).")
    logger.info(f"Will Attempt to Fetch Content for {len(urls_to_fetch)} Remaining URLs.")

    return urls_to_fetch, skipped_items


def save_fetched_content(
    fetched_data: dict[str, DocumentationHtmlContent],
    urls_to_process_map: DocumentationMap,
    output_dir: Path,
    logger: LoggerInterface
) -> tuple[
        List[str], # Saved
        List[str], # Skipped (Final Check)
        List[str] # Failed
    ]:
    """
    Saves the fetched documentation content to HTML files in a hierarchical structure.

    Args:
        fetched_data: Dictionary mapping URLs to their fetched DocumentationContent (or None).
        urls_to_process_map: The original map to retrieve hierarchical path and link text.
        output_dir: The base directory to save the output files.
        logger: Logger interface for logging messages.

    Returns:
        A tuple containing:
        - int: Count of successfully saved files.
        - int: Count of files skipped because they already existed (final check).
        - int: Count of failures (fetch failed or write error).
    """
    saved_urls: List[str] = []
    failed_urls: List[str] = []
    skipped_final_check_urls: List[str] = []
    logger.info("Processing Fetched Data and Saving to Disk using Hierarchical Structure...")

    for url, content in fetched_data.items():
        if content:
            # Ensure URL exists in the map to get its path
            if url not in urls_to_process_map:
                 logger.error(f"URL '{url}' From fetched_data Not Found in urls_to_process_map. Cannot Determine Save Path. Skipping.")
                 failed_urls.append(url)
                 continue

            # Get the hierarchical path parts associated with the URL
            item = urls_to_process_map[url]
            folder_path_parts = item['relative_path']

            # Use the ACTUAL fetched title for the final filename
            filename_base = sanitize_filename(content['title'])
            # Construct the target directory using the path parts
            target_dir = output_dir.joinpath(*folder_path_parts)
            target_path = target_dir / f"{filename_base}.html"

            # Keep this final check using the fetched title as a safeguard
            if target_path.exists():
                logger.warning(f"Skipping Write (Final Check), File Exists Based on Fetched Title '{content['title']}': {target_path}")
                skipped_final_check_urls.append(url)
                continue

            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                allowed_tags = ALLOWED_BY_DEFAULT_TAGS.copy()
                allowed_tags.add("span")
                cleaned_html = perform_html_cleaning(content['inner_html'], allowed_tags=allowed_tags, logger=logger)
                with target_path.open('w', encoding='utf-8') as f:
                    f.write(f"<!-- Title: {content['title']} -->\n")
                    if content['tagline']:
                        f.write(f"<!-- Tagline: {content['tagline']} -->\n")
                    f.write(f"<!-- Source URL: {url} -->\n\n")
                    f.write(cleaned_html)
                logger.debug(f"Saved Content for '{content['title']}' to {target_path}")
                saved_urls.append(url)
            except OSError as e:
                logger.error(f"OS Error Writing File {target_path}: {e}")
                failed_urls.append(url)
            except Exception as e:
                logger.error(f"Unexpected Error Writing File {target_path}: {e}")
                failed_urls.append(url)
        else:
            # Only count as failure if this URL was actually in our list to process
            if url in urls_to_process_map:
                 logger.warning(f"Failed to Fetch Content for URL: {url}")
                 failed_urls.append(url)
            else:
                 # This case should ideally not happen if fetched_data only contains keys from urls_to_fetch
                 logger.warning(f"Content for URL {url} is None, but it wasn't in the original process map. Ignoring.")


    return saved_urls, skipped_final_check_urls, failed_urls


@click.command()
@click.option('--doc-name', type=str, required=True, help='Name of the Apple Documentation.')
@click.option('--output-dir', type=click.Path(file_okay=False, writable=True, path_type=Path), required=True, help='Path to the output directory where fetched documents will be saved.')
@click.option('--timeout', type=int, default=30000, show_default=True, help='Timeout in milliseconds for page operations.') 
@click.option('--headless', is_flag=True, default=True, show_default=True, help='Run the browser in headless mode.')
def main(doc_name: str, output_dir: Path, timeout: int, headless: bool):
    """
    Scrapes Apple documentation pages listed in an JSON file,
    saving the content to a structured output directory.

    It first checks if an expected file (based on link text from the JSON file)
    exists in the output directory. If it does, the URL is skipped (pre-check).
    Otherwise, it fetches the content. Before saving the fetched content,
    it performs a final check using the *fetched title* to avoid overwriting.
    """
    logger = RichLogger()

    doc_url = f"https://developer.apple.com/tutorials/data/documentation/{doc_name}.json"


    response = requests.get(doc_url)
    response.raise_for_status()
    data = response.text

    output_dir.mkdir(parents=True, exist_ok=True) # Ensure output dir exists

    # --- Parse Map File ---
    urls_to_process_map = parse_apple_docs_json(data, logger)
    if not urls_to_process_map:
        logger.error("Exiting Due to Issues Parsing the Map File or Finding Links.")
        return 1

    # --- Pre-check for Existing Files ---
    urls_to_fetch, skipped_items = filter_urls_to_fetch(urls_to_process_map, output_dir, logger)

    if not urls_to_fetch:
        logger.info("No URLs Left to Fetch after Pre-check. Exiting.")
        return 1

    # --- Initialize Browser & Fetch ---
    fetched_data: Optional[dict[str, DocumentationHtmlContent]] = None
    with sync_playwright() as p:
        browser: Optional[Browser] = None
        page: Optional[Page] = None
        # Try to initialize browser and page
        try:
            browser, page = initialize_browser_and_page(
                p,
                # Navigate to a base URL first for potential cookie setup etc.
                url="https://developer.apple.com/documentation",
                timeout_ms=timeout, # Use the main timeout for initial setup
                headless=headless,
                logger=logger
            )
        except Exception as e:
             logger.error(f"Failed to Initialize Browser or Page: {e}")
             page = None # Ensure page is None if init fails

        if page:
            logger.info(f"Attempting to Fetch Content for {len(urls_to_fetch)} URLs...")
            fetched_data = extract_multiple_documentation_contents(page, urls_to_fetch, timeout, logger)
        else:
             logger.error("Browser Page Initialization Failed. Cannot Fetch Content.")

    if not fetched_data:
        logger.error("Failed to Fetch Content for All URLs. Exiting.")
        return 1

    # --- Save Content ---
    saved, skipped_before_save, failed_to_save = save_fetched_content(
        fetched_data, urls_to_process_map, output_dir, logger
    )

    # --- Final Summary ---
    total_links = len(urls_to_process_map)

    # Recalculate failed/not found based on initial list minus successes and skips
    # Failed count from save_fetched_content includes both fetch failures and write failures.
    completion_message = f"--- Summary ---"
    completion_message += f"\nTotal Links Found in Map: {total_links}"
    completion_message += f"\nSuccessfully Saved: {len(saved)}"
    completion_message += f"\nSkipped (Pre-check - Link Text Match): {len(skipped_items)}"
    completion_message += f"\nSkipped (Final Check - Fetched Title Match): {len(skipped_before_save)}"
    if not fetched_data:
         completion_message += f"\nFailed (Fetch or Write Error): {len(urls_to_fetch)}"
    else:
         completion_message += f"\nFailed (Fetch or Write Error): {len(failed_to_save)}"
    logger.info(completion_message)


if __name__ == "__main__":
    main()