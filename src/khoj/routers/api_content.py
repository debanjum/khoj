import asyncio
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Union

from asgiref.sync import sync_to_async
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel
from starlette.authentication import requires

from khoj.database import adapters
from khoj.database.adapters import (
    EntryAdapters,
    get_user_github_config,
    get_user_notion_config,
)
from khoj.database.models import Entry as DbEntry
from khoj.database.models import (
    GithubConfig,
    GithubRepoConfig,
    KhojUser,
    LocalMarkdownConfig,
    LocalOrgConfig,
    LocalPdfConfig,
    LocalPlaintextConfig,
    NotionConfig,
)
from khoj.processor.content.docx.docx_to_entries import DocxToEntries
from khoj.processor.content.pdf.pdf_to_entries import PdfToEntries
from khoj.routers.helpers import (
    ApiIndexedDataLimiter,
    CommonQueryParams,
    configure_content,
    get_file_content,
    get_user_config,
    update_telemetry_state,
)
from khoj.utils import constants, state
from khoj.utils.config import SearchModels
from khoj.utils.rawconfig import (
    ContentConfig,
    FullConfig,
    GithubContentConfig,
    NotionContentConfig,
    SearchConfig,
)
from khoj.utils.state import SearchType
from khoj.utils.yaml import save_config_to_file_updated_state

logger = logging.getLogger(__name__)

api_content = APIRouter()

executor = ThreadPoolExecutor()


class File(BaseModel):
    path: str
    content: Union[str, bytes]


class IndexBatchRequest(BaseModel):
    files: list[File]


class IndexerInput(BaseModel):
    org: Optional[dict[str, str]] = None
    markdown: Optional[dict[str, str]] = None
    pdf: Optional[dict[str, bytes]] = None
    plaintext: Optional[dict[str, str]] = None
    image: Optional[dict[str, bytes]] = None
    docx: Optional[dict[str, bytes]] = None


async def run_in_executor(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, func, *args)


@api_content.put("")
@requires(["authenticated"])
async def put_content(
    request: Request,
    files: List[UploadFile] = [],
    t: Optional[Union[state.SearchType, str]] = state.SearchType.All,
    client: Optional[str] = None,
    user_agent: Optional[str] = Header(None),
    referer: Optional[str] = Header(None),
    host: Optional[str] = Header(None),
    indexed_data_limiter: ApiIndexedDataLimiter = Depends(
        ApiIndexedDataLimiter(
            incoming_entries_size_limit=10,
            subscribed_incoming_entries_size_limit=75,
            total_entries_size_limit=10,
            subscribed_total_entries_size_limit=200,
        )
    ),
):
    return await indexer(request, files, t, True, client, user_agent, referer, host)


@api_content.patch("")
@requires(["authenticated"])
async def patch_content(
    request: Request,
    files: List[UploadFile] = [],
    t: Optional[Union[state.SearchType, str]] = state.SearchType.All,
    client: Optional[str] = None,
    user_agent: Optional[str] = Header(None),
    referer: Optional[str] = Header(None),
    host: Optional[str] = Header(None),
    indexed_data_limiter: ApiIndexedDataLimiter = Depends(
        ApiIndexedDataLimiter(
            incoming_entries_size_limit=10,
            subscribed_incoming_entries_size_limit=75,
            total_entries_size_limit=10,
            subscribed_total_entries_size_limit=200,
        )
    ),
):
    return await indexer(request, files, t, False, client, user_agent, referer, host)


@api_content.get("/github", response_class=Response)
@requires(["authenticated"])
def get_content_github(request: Request) -> Response:
    user = request.user.object
    user_config = get_user_config(user, request)
    del user_config["request"]

    current_github_config = get_user_github_config(user)

    if current_github_config:
        raw_repos = current_github_config.githubrepoconfig.all()
        repos = []
        for repo in raw_repos:
            repos.append(
                GithubRepoConfig(
                    name=repo.name,
                    owner=repo.owner,
                    branch=repo.branch,
                )
            )
        current_config = GithubContentConfig(
            pat_token=current_github_config.pat_token,
            repos=repos,
        )
        current_config = json.loads(current_config.json())
    else:
        current_config = {}  # type: ignore

    user_config["current_config"] = current_config

    # Return config data as a JSON response
    return Response(content=json.dumps(user_config), media_type="application/json", status_code=200)


@api_content.get("/notion", response_class=Response)
@requires(["authenticated"])
def get_content_notion(request: Request) -> Response:
    user = request.user.object
    user_config = get_user_config(user, request)
    del user_config["request"]

    current_notion_config = get_user_notion_config(user)
    token = current_notion_config.token if current_notion_config else ""
    current_config = NotionContentConfig(token=token)
    current_config = json.loads(current_config.model_dump_json())

    user_config["current_config"] = current_config

    # Return config data as a JSON response
    return Response(content=json.dumps(user_config), media_type="application/json", status_code=200)


@api_content.post("/github", status_code=200)
@requires(["authenticated"])
async def set_content_github(
    request: Request,
    updated_config: Union[GithubContentConfig, None],
    client: Optional[str] = None,
):
    _initialize_config()

    user = request.user.object

    try:
        await adapters.set_user_github_config(
            user=user,
            pat_token=updated_config.pat_token,
            repos=updated_config.repos,
        )
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set Github config")

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="set_content_config",
        client=client,
        metadata={"content_type": "github"},
    )

    return {"status": "ok"}


@api_content.post("/notion", status_code=200)
@requires(["authenticated"])
async def set_content_notion(
    request: Request,
    background_tasks: BackgroundTasks,
    updated_config: Union[NotionContentConfig, None],
    client: Optional[str] = None,
):
    _initialize_config()

    user = request.user.object

    try:
        await adapters.set_notion_config(
            user=user,
            token=updated_config.token,
        )
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set Notion config")

    if updated_config.token:
        # Trigger an async job to configure_content. Let it run without blocking the response.
        background_tasks.add_task(run_in_executor, configure_content, user, {}, False, SearchType.Notion)

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="set_content_config",
        client=client,
        metadata={"content_type": "notion"},
    )

    return {"status": "ok"}


@api_content.delete("/file", status_code=201)
@requires(["authenticated"])
async def delete_content_files(
    request: Request,
    filename: str,
    client: Optional[str] = None,
):
    user = request.user.object

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="delete_file",
        client=client,
    )

    await EntryAdapters.adelete_entry_by_file(user, filename)

    return {"status": "ok"}


class DeleteFilesRequest(BaseModel):
    files: List[str]


@api_content.delete("/files", status_code=201)
@requires(["authenticated"])
async def delete_content_file(
    request: Request,
    files: DeleteFilesRequest,
    client: Optional[str] = None,
):
    user = request.user.object

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="delete_file",
        client=client,
    )

    deleted_count = await EntryAdapters.adelete_entries_by_filenames(user, files.files)

    return {"status": "ok", "deleted_count": deleted_count}


@api_content.get("/size", response_model=Dict[str, int])
@requires(["authenticated"])
async def get_content_size(request: Request, common: CommonQueryParams, client: Optional[str] = None):
    user = request.user.object
    indexed_data_size_in_mb = await sync_to_async(EntryAdapters.get_size_of_indexed_data_in_mb)(user)
    return Response(
        content=json.dumps({"indexed_data_size_in_mb": math.ceil(indexed_data_size_in_mb)}),
        media_type="application/json",
        status_code=200,
    )


@api_content.get("/types", response_model=List[str])
@requires(["authenticated"])
def get_content_types(request: Request, client: Optional[str] = None):
    user = request.user.object
    all_content_types = {s.value for s in SearchType}
    configured_content_types = set(EntryAdapters.get_unique_file_types(user))
    configured_content_types |= {"all"}

    if state.config and state.config.content_type:
        for ctype in state.config.content_type.model_dump(exclude_none=True):
            configured_content_types.add(ctype)

    return list(configured_content_types & all_content_types)


@api_content.delete("/{content_type}", status_code=200)
@requires(["authenticated"])
async def delete_content_type(
    request: Request,
    content_type: SearchType,
    client: Optional[str] = None,
):
    user = request.user.object

    await sync_to_async(EntryAdapters.delete_all_entries)(user, file_type=content_type)

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="delete_content_config",
        client=client,
        metadata={"content_type": content_type},
    )

    enabled_content = await sync_to_async(EntryAdapters.get_unique_file_types)(user)
    return {"status": "ok"}


@api_content.get("/{content_source}", response_model=List[str])
@requires(["authenticated"])
async def get_content_source(
    request: Request,
    content_source: str,
    client: Optional[str] = None,
):
    user = request.user.object

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="get_all_filenames",
        client=client,
    )

    return await sync_to_async(list)(EntryAdapters.get_all_filenames_by_source(user, content_source))  # type: ignore[call-arg]


@api_content.delete("/{content_source}", status_code=200)
@requires(["authenticated"])
async def delete_content_source(
    request: Request,
    content_source: str,
    client: Optional[str] = None,
):
    user = request.user.object

    content_object = map_config_to_object(content_source)
    if content_object is None:
        raise ValueError(f"Invalid content source: {content_source}")
    elif content_object != "Computer":
        await content_object.objects.filter(user=user).adelete()
    await sync_to_async(EntryAdapters.delete_all_entries)(user, file_source=content_source)

    if content_source == DbEntry.EntrySource.NOTION:
        await NotionConfig.objects.filter(user=user).adelete()
    elif content_source == DbEntry.EntrySource.GITHUB:
        await GithubConfig.objects.filter(user=user).adelete()

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="delete_content_config",
        client=client,
        metadata={"content_source": content_source},
    )

    enabled_content = await sync_to_async(EntryAdapters.get_unique_file_types)(user)
    return {"status": "ok"}


@api_content.post("/convert", status_code=200)
@requires(["authenticated"])
async def convert_documents(
    request: Request,
    files: List[UploadFile],
    client: Optional[str] = None,
):
    MAX_FILE_SIZE_MB = 10  # 10MB limit
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

    converted_files = []
    supported_files = ["org", "markdown", "pdf", "plaintext", "docx"]

    for file in files:
        # Check file size first
        file_size = 0
        content = await file.read()
        file_size = len(content)
        await file.seek(0)  # Reset file pointer

        if file_size > MAX_FILE_SIZE_BYTES:
            logger.warning(
                f"Skipped converting oversized file ({file_size / 1024 / 1024:.1f}MB) sent by {client} client: {file.filename}"
            )
            continue

        file_data = get_file_content(file)
        if file_data.file_type in supported_files:
            extracted_content = (
                file_data.content.decode(file_data.encoding) if file_data.encoding else file_data.content
            )

            if file_data.file_type == "docx":
                entries_per_page = DocxToEntries.extract_text(file_data.content)
                annotated_pages = [
                    f"Page {index} of {file_data.name}:\n\n{entry}" for index, entry in enumerate(entries_per_page)
                ]
                extracted_content = "\n".join(annotated_pages)

            elif file_data.file_type == "pdf":
                entries_per_page = PdfToEntries.extract_text(file_data.content)
                annotated_pages = [
                    f"Page {index} of {file_data.name}:\n\n{entry}" for index, entry in enumerate(entries_per_page)
                ]
                extracted_content = "\n".join(annotated_pages)
            else:
                # Convert content to string
                extracted_content = extracted_content.decode("utf-8")

            # Calculate size in bytes. Some of the content might be in bytes, some in str.
            if isinstance(extracted_content, str):
                size_in_bytes = len(extracted_content.encode("utf-8"))
            elif isinstance(extracted_content, bytes):
                size_in_bytes = len(extracted_content)
            else:
                size_in_bytes = 0
                logger.warning(f"Unexpected content type: {type(extracted_content)}")

            converted_files.append(
                {
                    "name": file_data.name,
                    "content": extracted_content,
                    "file_type": file_data.file_type,
                    "size": size_in_bytes,
                }
            )
        else:
            logger.warning(f"Skipped converting unsupported file type sent by {client} client: {file.filename}")

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="convert_documents",
        client=client,
    )

    return Response(content=json.dumps(converted_files), media_type="application/json", status_code=200)


async def indexer(
    request: Request,
    files: list[UploadFile],
    t: Optional[Union[state.SearchType, str]] = state.SearchType.All,
    regenerate: bool = False,
    client: Optional[str] = None,
    user_agent: Optional[str] = Header(None),
    referer: Optional[str] = Header(None),
    host: Optional[str] = Header(None),
):
    user = request.user.object
    method = "regenerate" if regenerate else "sync"
    index_files: Dict[str, Dict[str, str]] = {
        "org": {},
        "markdown": {},
        "pdf": {},
        "plaintext": {},
        "image": {},
        "docx": {},
    }
    try:
        logger.info(f"📬 Updating content index via API call by {client} client")
        for file in files:
            file_data = get_file_content(file)
            if file_data.file_type in index_files:
                index_files[file_data.file_type][file_data.name] = (
                    file_data.content.decode(file_data.encoding) if file_data.encoding else file_data.content
                )
            else:
                logger.warning(f"Skipped indexing unsupported file type sent by {client} client: {file_data.name}")

        indexer_input = IndexerInput(
            org=index_files["org"],
            markdown=index_files["markdown"],
            pdf=index_files["pdf"],
            plaintext=index_files["plaintext"],
            image=index_files["image"],
            docx=index_files["docx"],
        )

        if state.config == None:
            logger.info("📬 Initializing content index on first run.")
            default_full_config = FullConfig(
                content_type=None,
                search_type=SearchConfig.model_validate(constants.default_config["search-type"]),
                processor=None,
            )
            state.config = default_full_config
            default_content_config = ContentConfig(
                org=None,
                markdown=None,
                pdf=None,
                docx=None,
                image=None,
                github=None,
                notion=None,
                plaintext=None,
            )
            state.config.content_type = default_content_config
            save_config_to_file_updated_state()
            configure_search(state.search_models, state.config.search_type)

        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,
            configure_content,
            user,
            indexer_input.model_dump(),
            regenerate,
            t,
        )
        if not success:
            raise RuntimeError(f"Failed to {method} {t} data sent by {client} client into content index")
        logger.info(f"Finished {method} {t} data sent by {client} client into content index")
    except Exception as e:
        logger.error(f"Failed to {method} {t} data sent by {client} client into content index: {e}", exc_info=True)
        logger.error(
            f"🚨 Failed to {method} {t} data sent by {client} client into content index: {e}",
            exc_info=True,
        )
        return Response(content="Failed", status_code=500)

    indexing_metadata = {
        "num_org": len(index_files["org"]),
        "num_markdown": len(index_files["markdown"]),
        "num_pdf": len(index_files["pdf"]),
        "num_plaintext": len(index_files["plaintext"]),
        "num_image": len(index_files["image"]),
        "num_docx": len(index_files["docx"]),
    }

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="index/update",
        client=client,
        user_agent=user_agent,
        referer=referer,
        host=host,
        metadata=indexing_metadata,
    )

    logger.info(f"📪 Content index updated via API call by {client} client")

    indexed_filenames = ",".join(file for ctype in index_files for file in index_files[ctype]) or ""
    return Response(content=indexed_filenames, status_code=200)


def configure_search(search_models: SearchModels, search_config: Optional[SearchConfig]) -> Optional[SearchModels]:
    # Run Validation Checks
    if search_models is None:
        search_models = SearchModels()

    return search_models


def map_config_to_object(content_source: str):
    if content_source == DbEntry.EntrySource.GITHUB:
        return GithubConfig
    if content_source == DbEntry.EntrySource.NOTION:
        return NotionConfig
    if content_source == DbEntry.EntrySource.COMPUTER:
        return "Computer"


async def map_config_to_db(config: FullConfig, user: KhojUser):
    if config.content_type:
        if config.content_type.org:
            await LocalOrgConfig.objects.filter(user=user).adelete()
            await LocalOrgConfig.objects.acreate(
                input_files=config.content_type.org.input_files,
                input_filter=config.content_type.org.input_filter,
                index_heading_entries=config.content_type.org.index_heading_entries,
                user=user,
            )
        if config.content_type.markdown:
            await LocalMarkdownConfig.objects.filter(user=user).adelete()
            await LocalMarkdownConfig.objects.acreate(
                input_files=config.content_type.markdown.input_files,
                input_filter=config.content_type.markdown.input_filter,
                index_heading_entries=config.content_type.markdown.index_heading_entries,
                user=user,
            )
        if config.content_type.pdf:
            await LocalPdfConfig.objects.filter(user=user).adelete()
            await LocalPdfConfig.objects.acreate(
                input_files=config.content_type.pdf.input_files,
                input_filter=config.content_type.pdf.input_filter,
                index_heading_entries=config.content_type.pdf.index_heading_entries,
                user=user,
            )
        if config.content_type.plaintext:
            await LocalPlaintextConfig.objects.filter(user=user).adelete()
            await LocalPlaintextConfig.objects.acreate(
                input_files=config.content_type.plaintext.input_files,
                input_filter=config.content_type.plaintext.input_filter,
                index_heading_entries=config.content_type.plaintext.index_heading_entries,
                user=user,
            )
        if config.content_type.github:
            await adapters.set_user_github_config(
                user=user,
                pat_token=config.content_type.github.pat_token,
                repos=config.content_type.github.repos,
            )
        if config.content_type.notion:
            await adapters.set_notion_config(
                user=user,
                token=config.content_type.notion.token,
            )


def _initialize_config():
    if state.config is None:
        state.config = FullConfig()
        state.config.search_type = SearchConfig.model_validate(constants.default_config["search-type"])
