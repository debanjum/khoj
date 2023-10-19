# Standard Modules
from io import BytesIO
from PIL import Image
from urllib.parse import quote
import pytest

# External Packages
from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest

# Internal Packages
from khoj.configure import configure_routes, configure_search_types
from khoj.utils import state
from khoj.utils.state import search_models, content_index, config
from khoj.search_type import text_search, image_search
from khoj.utils.rawconfig import ContentConfig, SearchConfig
from khoj.processor.org_mode.org_to_jsonl import OrgToJsonl
from database.models import KhojUser
from database.adapters import EmbeddingsAdapters


# Test
# ----------------------------------------------------------------------------------------------------
def test_search_with_invalid_content_type(client):
    # Arrange
    user_query = quote("How to call Khoj from Emacs?")

    # Act
    response = client.get(f"/api/search?q={user_query}&t=invalid_content_type")

    # Assert
    assert response.status_code == 422


# ----------------------------------------------------------------------------------------------------
def test_search_with_valid_content_type(client):
    for content_type in ["all", "org", "markdown", "image", "pdf", "github", "notion"]:
        # Act
        response = client.get(f"/api/search?q=random&t={content_type}")
        # Assert
        assert response.status_code == 200, f"Returned status: {response.status_code} for content type: {content_type}"


# ----------------------------------------------------------------------------------------------------
def test_update_with_invalid_content_type(client):
    # Act
    response = client.get(f"/api/update?t=invalid_content_type")

    # Assert
    assert response.status_code == 422


# ----------------------------------------------------------------------------------------------------
def test_regenerate_with_invalid_content_type(client):
    # Act
    response = client.get(f"/api/update?force=true&t=invalid_content_type")

    # Assert
    assert response.status_code == 422


# ----------------------------------------------------------------------------------------------------
def test_index_update(client):
    # Arrange
    files = get_sample_files_data()
    headers = {"x-api-key": "secret"}

    # Act
    response = client.post("/api/v1/index/update", files=files, headers=headers)

    # Assert
    assert response.status_code == 200


# ----------------------------------------------------------------------------------------------------
def test_regenerate_with_valid_content_type(client):
    for content_type in ["all", "org", "markdown", "image", "pdf", "notion"]:
        # Arrange
        files = get_sample_files_data()
        headers = {"x-api-key": "secret"}

        # Act
        response = client.post(f"/api/v1/index/update?t={content_type}", files=files, headers=headers)
        # Assert
        assert response.status_code == 200, f"Returned status: {response.status_code} for content type: {content_type}"


# ----------------------------------------------------------------------------------------------------
def test_regenerate_with_github_fails_without_pat(client):
    # Act
    response = client.get(f"/api/update?force=true&t=github")

    # Arrange
    files = get_sample_files_data()
    headers = {"x-api-key": "secret"}

    # Act
    response = client.post(f"/api/v1/index/update?t=github", files=files, headers=headers)
    # Assert
    assert response.status_code == 200, f"Returned status: {response.status_code} for content type: github"


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.skip(reason="Flaky test on parallel test runs")
def test_get_configured_types_via_api(client, sample_org_data):
    # Act
    text_search.setup(OrgToJsonl, sample_org_data, regenerate=False)

    enabled_types = EmbeddingsAdapters.get_unique_file_types(user=None).all().values_list("file_type", flat=True)

    # Assert
    assert list(enabled_types) == ["org"]


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_get_api_config_types(client, search_config: SearchConfig, sample_org_data, default_user2: KhojUser):
    # Arrange
    text_search.setup(OrgToJsonl, sample_org_data, regenerate=False, user=default_user2)

    # Act
    response = client.get(f"/api/config/types")

    # Assert
    assert response.status_code == 200
    assert response.json() == ["all", "org", "image"]


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_get_configured_types_with_no_content_config(fastapi_app: FastAPI):
    # Arrange
    state.SearchType = configure_search_types(config)
    original_config = state.config.content_type
    state.config.content_type = None

    configure_routes(fastapi_app)
    client = TestClient(fastapi_app)

    # Act
    response = client.get(f"/api/config/types")

    # Assert
    assert response.status_code == 200
    assert response.json() == ["all"]

    # Restore
    state.config.content_type = original_config


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_image_search(client, content_config: ContentConfig, search_config: SearchConfig):
    # Arrange
    search_models.image_search = image_search.initialize_model(search_config.image)
    content_index.image = image_search.setup(
        content_config.image, search_models.image_search.image_encoder, regenerate=False
    )
    query_expected_image_pairs = [
        ("kitten", "kitten_park.jpg"),
        ("a horse and dog on a leash", "horse_dog.jpg"),
        ("A guinea pig eating grass", "guineapig_grass.jpg"),
    ]

    for query, expected_image_name in query_expected_image_pairs:
        # Act
        response = client.get(f"/api/search?q={query}&n=1&t=image")

        # Assert
        assert response.status_code == 200
        actual_image = Image.open(BytesIO(client.get(response.json()[0]["entry"]).content))
        expected_image = Image.open(content_config.image.input_directories[0].joinpath(expected_image_name))

        # Assert
        assert expected_image == actual_image


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_notes_search(client, search_config: SearchConfig, sample_org_data, default_user2: KhojUser):
    # Arrange
    text_search.setup(OrgToJsonl, sample_org_data, regenerate=False, user=default_user2)
    user_query = quote("How to git install application?")

    # Act
    response = client.get(f"/api/search?q={user_query}&n=1&t=org&r=true")

    # Assert
    assert response.status_code == 200
    # assert actual_data contains "Khoj via Emacs" entry
    search_result = response.json()[0]["entry"]
    assert "git clone https://github.com/khoj-ai/khoj" in search_result


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_notes_search_with_only_filters(
    client, content_config: ContentConfig, search_config: SearchConfig, sample_org_data, default_user2: KhojUser
):
    # Arrange
    text_search.setup(
        OrgToJsonl,
        sample_org_data,
        regenerate=False,
        user=default_user2,
    )
    user_query = quote('+"Emacs" file:"*.org"')

    # Act
    response = client.get(f"/api/search?q={user_query}&n=1&t=org")

    # Assert
    assert response.status_code == 200
    # assert actual_data contains word "Emacs"
    search_result = response.json()[0]["entry"]
    assert "Emacs" in search_result


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_notes_search_with_include_filter(client, sample_org_data, default_user2: KhojUser):
    # Arrange
    text_search.setup(OrgToJsonl, sample_org_data, regenerate=False, user=default_user2)
    user_query = quote('How to git install application? +"Emacs"')

    # Act
    response = client.get(f"/api/search?q={user_query}&n=1&t=org")

    # Assert
    assert response.status_code == 200
    # assert actual_data contains word "Emacs"
    search_result = response.json()[0]["entry"]
    assert "Emacs" in search_result


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_notes_search_with_exclude_filter(client, sample_org_data, default_user2: KhojUser):
    # Arrange
    text_search.setup(
        OrgToJsonl,
        sample_org_data,
        regenerate=False,
        user=default_user2,
    )
    user_query = quote('How to git install application? -"clone"')

    # Act
    response = client.get(f"/api/search?q={user_query}&n=1&t=org")

    # Assert
    assert response.status_code == 200
    # assert actual_data does not contains word "clone"
    search_result = response.json()[0]["entry"]
    assert "clone" not in search_result


# ----------------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_different_user_data_not_accessed(client, sample_org_data, default_user: KhojUser):
    # Arrange
    text_search.setup(OrgToJsonl, sample_org_data, regenerate=False, user=default_user)
    user_query = quote("How to git install application?")

    # Act
    response = client.get(f"/api/search?q={user_query}&n=1&t=org")

    # Assert
    assert response.status_code == 200
    # assert actual response has no data as the default_user is different from the user making the query (anonymous)
    assert len(response.json()) == 0


def get_sample_files_data():
    return {
        "files": ("path/to/filename.org", "* practicing piano", "text/org"),
        "files": ("path/to/filename1.org", "** top 3 reasons why I moved to SF", "text/org"),
        "files": ("path/to/filename2.org", "* how to build a search engine", "text/org"),
        "files": ("path/to/filename.pdf", "Moore's law does not apply to consumer hardware", "application/pdf"),
        "files": ("path/to/filename1.pdf", "The sun is a ball of helium", "application/pdf"),
        "files": ("path/to/filename2.pdf", "Effect of sunshine on baseline human happiness", "application/pdf"),
        "files": ("path/to/filename.txt", "data,column,value", "text/plain"),
        "files": ("path/to/filename1.txt", "<html>my first web page</html>", "text/plain"),
        "files": ("path/to/filename2.txt", "2021-02-02 Journal Entry", "text/plain"),
        "files": ("path/to/filename.md", "# Notes from client call", "text/markdown"),
        "files": (
            "path/to/filename1.md",
            "## Studying anthropological records from the Fatimid caliphate",
            "text/markdown",
        ),
        "files": ("path/to/filename2.md", "**Understanding science through the lens of art**", "text/markdown"),
    }
