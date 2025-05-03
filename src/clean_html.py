from typing import Literal
from bs4 import BeautifulSoup, NavigableString, Comment, Tag
from src.common import LoggerInterface, RichLogger


# ===============================================
# Core HTML Cleaning Function
# ===============================================
def perform_html_cleaning(
    html_content: str,
    logger: LoggerInterface,
    allowed_tags: set[str],
    allowed_attributes: set[str] = {"href", "expanded"},
    parser: Literal["lxml", "html.parser"] = "lxml",
) -> str:
    """
    Performs the actual cleaning operations on the HTML string.

    Args:
        html_content (str): The raw HTML content as a string.
        allowed_tags (set): A set of tag names to keep.
        allowed_attributes (set): A set of attribute names to keep.
        parser (str): The parser to use ('lxml' or 'html.parser').

    Returns:
        str: The cleaned HTML content as a prettified string.
    """
    soup = BeautifulSoup(html_content, parser)

    # --- Step 1: Initial Cleanup (Unwrap non-allowed, clean attributes) ---
    logger.debug("Step 1: Cleaning tags and attributes...")
    for tag in reversed(soup.find_all(True)):
        if not hasattr(tag, "name"):
            continue
        if tag.name in allowed_tags:
            current_attributes = list(tag.attrs.keys())
            for attr in current_attributes:
                if attr not in allowed_attributes:
                    del tag[attr]
        else:
            # Unwrap non-allowed tags
            if hasattr(tag, "unwrap"):
                tag.unwrap()

    # --- Step 2: Remove Free Text within Divs ---
    logger.debug("Step 2: Removing free text inside <div> tags...")
    for div in soup.find_all("div"):
        for content in list(div.contents):
            if isinstance(content, NavigableString):
                if content.strip():
                    content.extract()

    # --- Step 3: Remove HTML Comments ---
    logger.debug("Step 3: Removing HTML comments...")
    comments = soup.find_all(string=lambda text: isinstance(text, Comment))
    for comment in comments:
        comment.extract()

    # --- Step 4: Remove Empty Divs (Repeat until no changes) ---
    logger.debug("Step 4: Removing empty <div> tags...")
    while True:
        changed = False
        for div in reversed(soup.find_all(True)):
            if not div.parent:
                continue
            is_empty_or_whitespace = not div.contents or all(
                isinstance(c, NavigableString) and not c.strip() for c in div.contents
            )
            if is_empty_or_whitespace:
                div.decompose()
                changed = True
        if not changed:
            break

    # --- Step 5: Simplify Div Nesting (Repeat until no changes) ---
    logger.debug("Step 5: Simplifying nested <div> tags...")
    while True:
        changed = False
        for div in reversed(soup.find_all("div")):
            if not div.parent:
                continue
            meaningful_children = [
                c
                for c in div.contents
                if not (isinstance(c, NavigableString) and not c.strip())
            ]
            if (
                len(meaningful_children) == 1
                and hasattr(meaningful_children[0], "name")
                and meaningful_children[0].name == "div"
            ):
                if meaningful_children[0].parent:
                    div.unwrap()
                    changed = True
        if not changed:
            break

    # --- Step 6: Unwrap divs that only contain a single <a> tag ---
    logger.debug("Step 6: Unwrapping divs that only contain a single <a> tag...")
    for div in reversed(soup.find_all("div")):
        if not div.parent:
            continue
        meaningful_children = [
            c
            for c in div.contents
            if not (isinstance(c, NavigableString) and not c.strip())
        ]
        if (
            len(meaningful_children) == 1
            and hasattr(meaningful_children[0], "name")
            and meaningful_children[0].name == "a"
        ):
            if meaningful_children[0].parent:
                div.unwrap()

    # --- Step 7: Unwrap <a> tags that contain <h2> tags ---
    logger.debug("Step 7: Unwrapping <a> tags that contain <h2> tags...")
    for a_tag in reversed(soup.find_all("a")):
        if not a_tag.parent:
            continue

        # Check if the <a> tag contains an <h2> tag
        h2_tags = a_tag.find_all("h2")
        if h2_tags:
            # Unwrap the <a> tag to keep the <h2> but remove the link wrapper
            a_tag.unwrap()

    return soup.prettify()
