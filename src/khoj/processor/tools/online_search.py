import asyncio
import json
import logging
import os
import urllib.parse
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import aiohttp
from bs4 import BeautifulSoup
from markdownify import markdownify

from khoj.database.adapters import ConversationAdapters
from khoj.database.models import Agent, KhojUser, ServerChatSettings, WebScraper
from khoj.processor.conversation import prompts
from khoj.routers.helpers import (
    ChatEvent,
    extract_relevant_info,
    generate_online_subqueries,
    infer_webpage_urls,
)
from khoj.utils.helpers import (
    is_env_var_true,
    is_internal_url,
    is_internet_connected,
    is_none_or_empty,
    timer,
)
from khoj.utils.rawconfig import LocationData

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_ENGINE_ID = os.getenv("GOOGLE_SEARCH_ENGINE_ID")
SERPER_DEV_API_KEY = os.getenv("SERPER_DEV_API_KEY")
SERPER_DEV_URL = "https://google.serper.dev/search"

JINA_SEARCH_API_URL = "https://s.jina.ai/"
JINA_API_KEY = os.getenv("JINA_API_KEY")

FIRECRAWL_USE_LLM_EXTRACT = is_env_var_true("FIRECRAWL_USE_LLM_EXTRACT")

OLOSTEP_QUERY_PARAMS = {
    "timeout": 35,  # seconds
    "waitBeforeScraping": 0,  # seconds
    "saveHtml": "False",
    "saveMarkdown": "True",
    "removeCSSselectors": "default",
    "htmlTransformer": "none",
    "removeImages": "True",
    "fastLane": "True",
    # Similar to Stripe's API, the expand parameters avoid the need to make a second API call
    # to retrieve the dataset (from the dataset API) if you only need the markdown or html.
    "expandMarkdown": "True",
    "expandHtml": "False",
}

DEFAULT_MAX_WEBPAGES_TO_READ = 1
MAX_WEBPAGES_TO_INFER = 10


async def search_online(
    query: str,
    conversation_history: dict,
    location: LocationData,
    user: KhojUser,
    send_status_func: Optional[Callable] = None,
    custom_filters: List[str] = [],
    max_webpages_to_read: int = DEFAULT_MAX_WEBPAGES_TO_READ,
    query_images: List[str] = None,
    previous_subqueries: Set = set(),
    agent: Agent = None,
    query_files: str = None,
    tracer: dict = {},
):
    query += " ".join(custom_filters)
    if not is_internet_connected():
        logger.warning("Cannot search online as not connected to internet")
        yield {}
        return

    # Breakdown the query into subqueries to get the correct answer
    new_subqueries = await generate_online_subqueries(
        query,
        conversation_history,
        location,
        user,
        query_images=query_images,
        agent=agent,
        tracer=tracer,
        query_files=query_files,
    )
    subqueries = list(new_subqueries - previous_subqueries)
    response_dict: Dict[str, Dict[str, List[Dict] | Dict]] = {}

    if is_none_or_empty(subqueries):
        logger.info("No new subqueries to search online")
        yield response_dict
        return

    if GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID:
        search_engine = "Google"
        search_func = search_with_google
    elif SERPER_DEV_API_KEY:
        search_engine = "Serper"
        search_func = search_with_serper
    elif JINA_API_KEY:
        search_engine = "Jina"
        search_func = search_with_jina
    else:
        search_engine = "Searxng"
        search_func = search_with_searxng

    logger.info(f"🌐 Searching the Internet with {search_engine} for {subqueries}")
    if send_status_func:
        subqueries_str = "\n- " + "\n- ".join(subqueries)
        async for event in send_status_func(f"**Searching the Internet for**: {subqueries_str}"):
            yield {ChatEvent.STATUS: event}

    with timer(f"Internet searches for {subqueries} took", logger):
        search_tasks = [search_func(subquery, location) for subquery in subqueries]
        search_results = await asyncio.gather(*search_tasks)
        response_dict = {subquery: search_result for subquery, search_result in search_results}

    # Gather distinct web pages from organic results for subqueries without an instant answer.
    webpages: Dict[str, Dict] = {}
    for subquery in response_dict:
        if "answerBox" in response_dict[subquery]:
            continue
        for idx, organic in enumerate(response_dict[subquery].get("organic", [])):
            link = organic.get("link")
            if link in webpages and idx < max_webpages_to_read:
                webpages[link]["queries"].add(subquery)
            # Content of web pages is directly available when Jina is used for search.
            elif idx < max_webpages_to_read:
                webpages[link] = {"queries": {subquery}, "content": organic.get("content")}
            # Only keep webpage content for up to max_webpages_to_read organic results.
            if idx >= max_webpages_to_read and not is_none_or_empty(organic.get("content")):
                organic["content"] = None
                response_dict[subquery]["organic"][idx] = organic

    # Read, extract relevant info from the retrieved web pages
    if webpages:
        logger.info(f"Reading web pages at: {webpages.keys()}")
        if send_status_func:
            webpage_links_str = "\n- " + "\n- ".join(webpages.keys())
            async for event in send_status_func(f"**Reading web pages**: {webpage_links_str}"):
                yield {ChatEvent.STATUS: event}
    tasks = [
        read_webpage_and_extract_content(
            data["queries"], link, data.get("content"), user=user, agent=agent, tracer=tracer
        )
        for link, data in webpages.items()
    ]
    results = await asyncio.gather(*tasks)

    # Collect extracted info from the retrieved web pages
    for subqueries, url, webpage_extract in results:
        if webpage_extract is not None:
            response_dict[subqueries.pop()]["webpages"] = {"link": url, "snippet": webpage_extract}

    yield response_dict


async def search_with_searxng(query: str, location: LocationData) -> Tuple[str, Dict[str, List[Dict]]]:
    """Search using local SearXNG instance."""
    # Use environment variable or default to localhost
    searxng_url = os.getenv("KHOJ_SEARXNG_URL", "http://localhost:42113")
    search_url = f"{searxng_url}/search"
    country_code = location.country_code.lower() if location and location.country_code else "us"

    params = {"q": query, "format": "html", "language": "en", "country": country_code, "categories": "general"}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, params=params) as response:
                if response.status != 200:
                    logger.error(f"SearXNG search failed to call {searxng_url}: {await response.text()}")
                    return query, {}

                html_content = await response.text()

                soup = BeautifulSoup(html_content, "html.parser")
                organic_results = []

                for result in soup.find_all("article", class_="result"):
                    title_elem = result.find("a", rel="noreferrer")
                    if title_elem:
                        title = title_elem.text.strip()
                        link = title_elem["href"]

                        description_elem = result.find("p", class_="content")
                        description = description_elem.text.strip() if description_elem else None

                        organic_results.append({"title": title, "link": link, "description": description})

                extracted_search_result = {"organic": organic_results}

                return query, extracted_search_result

        except Exception as e:
            logger.error(f"Error searching with SearXNG: {str(e)}")
            return query, {}


async def search_with_google(query: str, location: LocationData) -> Tuple[str, Dict[str, List[Dict]]]:
    country_code = location.country_code.lower() if location and location.country_code else "us"
    base_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_ENGINE_ID,
        "q": query,
        "cr": f"country{country_code.upper()}",  # Country restrict parameter
        "gl": country_code,  # Geolocation parameter
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(base_url, params=params) as response:
            if response.status != 200:
                logger.error(await response.text())
                return query, {}

            json_response = await response.json()

            # Transform Google's response format to match Serper's format
            organic_results = []
            if "items" in json_response:
                organic_results = [
                    {
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "content": None,  # Google Search API doesn't provide full content
                    }
                    for item in json_response["items"]
                ]

            # Format knowledge graph if available
            knowledge_graph = {}
            if "knowledge_graph" in json_response:
                kg = json_response["knowledge_graph"]
                knowledge_graph = {
                    "title": kg.get("name", ""),
                    "description": kg.get("description", ""),
                    "type": kg.get("type", ""),
                }

            extracted_search_result: Dict[str, Any] = {"organic": organic_results}

            if knowledge_graph:
                extracted_search_result["knowledgeGraph"] = knowledge_graph

            return query, extracted_search_result


async def search_with_serper(query: str, location: LocationData) -> Tuple[str, Dict[str, List[Dict]]]:
    country_code = location.country_code.lower() if location and location.country_code else "us"
    payload = json.dumps({"q": query, "gl": country_code})
    headers = {"X-API-KEY": SERPER_DEV_API_KEY, "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(SERPER_DEV_URL, headers=headers, data=payload) as response:
            if response.status != 200:
                logger.error(await response.text())
                return query, {}
            json_response = await response.json()
            extraction_fields = ["organic", "answerBox", "peopleAlsoAsk", "knowledgeGraph"]
            extracted_search_result = {
                field: json_response[field]
                for field in extraction_fields
                if not is_none_or_empty(json_response.get(field))
            }

            return query, extracted_search_result


async def read_webpages(
    query: str,
    conversation_history: dict,
    location: LocationData,
    user: KhojUser,
    send_status_func: Optional[Callable] = None,
    query_images: List[str] = None,
    agent: Agent = None,
    max_webpages_to_read: int = DEFAULT_MAX_WEBPAGES_TO_READ,
    query_files: str = None,
    tracer: dict = {},
):
    "Infer web pages to read from the query and extract relevant information from them"
    logger.info(f"Inferring web pages to read")
    urls = await infer_webpage_urls(
        query,
        conversation_history,
        location,
        user,
        query_images,
        agent=agent,
        query_files=query_files,
        tracer=tracer,
    )

    # Get the top 10 web pages to read
    urls = urls[:max_webpages_to_read]

    logger.info(f"Reading web pages at: {urls}")
    if send_status_func:
        webpage_links_str = "\n- " + "\n- ".join(list(urls))
        async for event in send_status_func(f"**Reading web pages**: {webpage_links_str}"):
            yield {ChatEvent.STATUS: event}
    tasks = [read_webpage_and_extract_content({query}, url, user=user, agent=agent, tracer=tracer) for url in urls]
    results = await asyncio.gather(*tasks)

    response: Dict[str, Dict] = defaultdict(dict)
    response[query]["webpages"] = [
        {"query": qs.pop(), "link": url, "snippet": extract} for qs, url, extract in results if extract is not None
    ]
    yield response


async def read_webpage(
    url, scraper_type=None, api_key=None, api_url=None, subqueries=None, agent=None
) -> Tuple[str | None, str | None]:
    if scraper_type == WebScraper.WebScraperType.FIRECRAWL and FIRECRAWL_USE_LLM_EXTRACT:
        return None, await query_webpage_with_firecrawl(url, subqueries, api_key, api_url, agent)
    elif scraper_type == WebScraper.WebScraperType.FIRECRAWL:
        return await read_webpage_with_firecrawl(url, api_key, api_url), None
    elif scraper_type == WebScraper.WebScraperType.OLOSTEP:
        return await read_webpage_with_olostep(url, api_key, api_url), None
    elif scraper_type == WebScraper.WebScraperType.JINA:
        return await read_webpage_with_jina(url, api_key, api_url), None
    else:
        return await read_webpage_at_url(url), None


async def read_webpage_and_extract_content(
    subqueries: set[str],
    url: str,
    content: str = None,
    user: KhojUser = None,
    agent: Agent = None,
    tracer: dict = {},
) -> Tuple[set[str], str, Union[None, str]]:
    # Select the web scrapers to use for reading the web page
    web_scrapers = await ConversationAdapters.aget_enabled_webscrapers()
    # Only use the direct web scraper for internal URLs
    if is_internal_url(url):
        web_scrapers = [scraper for scraper in web_scrapers if scraper.type == WebScraper.WebScraperType.DIRECT]

    # Fallback through enabled web scrapers until we successfully read the web page
    extracted_info = None
    for scraper in web_scrapers:
        try:
            # Read the web page
            if is_none_or_empty(content):
                with timer(f"Reading web page with {scraper.type} at '{url}' took", logger, log_level=logging.INFO):
                    content, extracted_info = await read_webpage(
                        url, scraper.type, scraper.api_key, scraper.api_url, subqueries, agent
                    )

            # Extract relevant information from the web page
            if is_none_or_empty(extracted_info):
                with timer(f"Extracting relevant information from web page at '{url}' took", logger):
                    extracted_info = await extract_relevant_info(
                        subqueries, content, user=user, agent=agent, tracer=tracer
                    )

            # If we successfully extracted information, break the loop
            if not is_none_or_empty(extracted_info):
                break
        except Exception as e:
            logger.warning(f"Failed to read web page with {scraper.type} at '{url}' with {e}")
            # If this is the last web scraper in the list, log an error
            if scraper.name == web_scrapers[-1].name:
                logger.error(f"All web scrapers failed for '{url}'")

    return subqueries, url, extracted_info


async def read_webpage_at_url(web_url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(web_url, headers=headers, timeout=30) as response:
            response.raise_for_status()
            html = await response.text()
            parsed_html = BeautifulSoup(html, "html.parser")
            body = parsed_html.body.get_text(separator="\n", strip=True)
            return markdownify(body)


async def read_webpage_with_olostep(web_url: str, api_key: str, api_url: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}"}
    web_scraping_params: Dict[str, Union[str, int, bool]] = OLOSTEP_QUERY_PARAMS.copy()  # type: ignore
    web_scraping_params["url"] = web_url

    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, params=web_scraping_params, headers=headers) as response:
            response.raise_for_status()
            response_json = await response.json()
            return response_json["markdown_content"]


async def read_webpage_with_jina(web_url: str, api_key: str, api_url: str) -> str:
    jina_reader_api_url = f"{api_url}/{web_url}"
    headers = {"Accept": "application/json", "X-Timeout": "30"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with aiohttp.ClientSession() as session:
        async with session.get(jina_reader_api_url, headers=headers) as response:
            response.raise_for_status()
            response_json = await response.json()
            return response_json["data"]["content"]


async def read_webpage_with_firecrawl(web_url: str, api_key: str, api_url: str) -> str:
    firecrawl_api_url = f"{api_url}/v1/scrape"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    params = {"url": web_url, "formats": ["markdown"], "excludeTags": ["script", ".ad"]}

    async with aiohttp.ClientSession() as session:
        async with session.post(firecrawl_api_url, json=params, headers=headers) as response:
            response.raise_for_status()
            response_json = await response.json()
            return response_json["data"]["markdown"]


async def query_webpage_with_firecrawl(
    web_url: str, queries: set[str], api_key: str, api_url: str, agent: Agent = None
) -> str:
    firecrawl_api_url = f"{api_url}/v1/scrape"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    schema = {
        "type": "object",
        "properties": {
            "relevant_extract": {"type": "string"},
        },
        "required": [
            "relevant_extract",
        ],
    }

    personality_context = (
        prompts.personality_context.format(personality=agent.personality) if agent and agent.personality else ""
    )
    system_prompt = f"""
{prompts.system_prompt_extract_relevant_information}

{personality_context}
User Query: {", ".join(queries)}

Collate only relevant information from the website to answer the target query and in the provided JSON schema.
""".strip()

    params = {"url": web_url, "formats": ["extract"], "extract": {"systemPrompt": system_prompt, "schema": schema}}

    async with aiohttp.ClientSession() as session:
        async with session.post(firecrawl_api_url, json=params, headers=headers) as response:
            response.raise_for_status()
            response_json = await response.json()
            return response_json["data"]["extract"]["relevant_extract"]


async def search_with_jina(query: str, location: LocationData) -> Tuple[str, Dict[str, List[Dict]]]:
    encoded_query = urllib.parse.quote(query)
    jina_search_api_url = f"{JINA_SEARCH_API_URL}/{encoded_query}"
    headers = {"Accept": "application/json"}

    # First check for jina scraper configuration in database
    default_jina_scraper = (
        await ServerChatSettings.objects.filter()
        .prefetch_related("web_scraper")
        .filter(web_scraper__type=WebScraper.WebScraperType.JINA)
        .afirst()
    )
    if default_jina_scraper and default_jina_scraper.web_scraper:
        jina_scraper = default_jina_scraper.web_scraper
    else:
        # Fallback to first configured Jina scraper in DB if no server settings
        jina_scraper = await WebScraper.objects.filter(type=WebScraper.WebScraperType.JINA).afirst()

    # Get API key from DB scraper config or environment variable
    api_key = jina_scraper.api_key if jina_scraper and jina_scraper.api_key else JINA_API_KEY

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with aiohttp.ClientSession() as session:
        async with session.get(jina_search_api_url, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Jina search failed: {error_text}")
                return query, {}
            response_json = await response.json()
            parsed_response = [
                {
                    "title": item["title"],
                    "content": item.get("content"),
                    # rename description -> snippet for consistency
                    "snippet": item["description"],
                    # rename url -> link for consistency
                    "link": item["url"],
                }
                for item in response_json["data"]
            ]
            return query, {"organic": parsed_response}


def deduplicate_organic_results(online_results: dict) -> dict:
    """Deduplicate organic search results based on links across all queries."""
    # Keep track of seen links to filter out duplicates across queries
    seen_links = set()
    deduplicated_results = {}

    # Process each query's results
    for query, results in online_results.items():
        # Filter organic results keeping only first occurrence of each link
        filtered_organic = []
        for result in results.get("organic", []):
            link = result.get("link")
            if link and link not in seen_links:
                seen_links.add(link)
                filtered_organic.append(result)

        # Update results with deduplicated organic entries
        deduplicated_results[query] = {**results, "organic": filtered_organic}

    return deduplicated_results
