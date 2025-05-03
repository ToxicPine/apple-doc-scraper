import json
import sys
from typing import Optional
from src.common import LoggerInterface, DocumentationMap

# --- Constants ---
BASE_URL = "https://developer.apple.com"


# --- Parsing Function ---
def parse_apple_docs_json(
    json_string: str, logger: LoggerInterface
) -> DocumentationMap:
    """Parses the Apple documentation JSON string and extracts hierarchical link info.

    Args:
        json_string: A string containing the JSON data.

    Returns:
        A dictionary where keys are document titles and values are tuples
        containing a list of directory path components and the full URL.
        Returns None if JSON parsing fails.
    """
    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as e:
        logger.error(f"Error Decoding Documentation JSON: {e}")
        raise e

    references = data.get("references", {})
    if not references:
        logger.warning("No 'references' found in the JSON data. Result will be empty.")
        return {}

    parsed_docs: DocumentationMap = {}

    for identifier, ref_data in references.items():
        item_title = ref_data.get("title")
        given_url = ref_data.get("url")

        if not isinstance(given_url, str):
            logger.warning(
                f"Reference with Identifier '{identifier}' Missing URL. Skipping."
            )
            continue

        if not isinstance(item_title, str):
            logger.warning(
                f"Reference with Identifier '{identifier}' Missing Title. Skipping."
            )
            continue

        if item_title in parsed_docs:
            logger.warning(
                f"Duplicate Title '{item_title}' Found for Identifier '{identifier}'. Keeping First Occurrence."
            )
            continue

        if given_url:
            if given_url.startswith("/"):
                full_url = BASE_URL + given_url
            else:
                full_url = given_url

            dir_components = [comp for comp in given_url.strip("/").split("/") if comp]
            parsed_docs[full_url] = {
                "link_text": item_title,
                "relative_path": dir_components,
            }
        else:
            # Attempt to construct URL from identifier if standard URL is missing
            if identifier.startswith("doc://"):
                path = identifier.split("doc://")[1]
                # Basic heuristic cleanup - might need refinement
                logger.info(f"Path: {path}")
                if path.startswith("com.apple.documentation/"):
                    path = path.replace("com.apple.documentation/", "", 1)
                    potential_url = f"{BASE_URL}/documentation/{path}"
                elif path.startswith("com.apple."):
                    # Handle other com.apple.X domains
                    domain = path.split("/")[0].replace("com.apple.", "")
                    remaining_path = "/".join(path.split("/")[1:])
                    potential_url = (
                        f"{BASE_URL}/documentation/{domain}/{remaining_path}"
                    )
                else:
                    potential_url = f"{BASE_URL}/{path}"

                # Parse directory structure from constructed path
                dir_components = [comp for comp in path.strip("/").split("/") if comp]
                parsed_docs[potential_url] = {
                    "link_text": item_title,
                    "relative_path": dir_components,
                }
                logger.info(
                    f"Constructed URL for '{item_title}' from Identifier: {potential_url}"
                )
            else:
                logger.warning(
                    f"Reference '{item_title}' (id: {identifier}) Missing 'url' and Identifier Format is Unknown. Skipping."
                )

    return parsed_docs
