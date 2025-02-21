import modal
import os
from pydantic import BaseModel
from fastapi import Request, HTTPException, FastAPI
from supabase import create_client, Client
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings

web_app = FastAPI()

crawl4ai_image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install("crawl4ai==0.4.247")
    .run_commands("crawl4ai-setup")
    .pip_install(
        "fastapi[standard]",
        "supabase",
        "langchain_text_splitters",
        "langchain-openai",
    )
)

app = modal.App(
    name="supavec-api",
    image=crawl4ai_image,
    secrets=[modal.Secret.from_name("supa-secrets")],
)


class ScrapeRequest(BaseModel):
    url: str
    chunk_size: int | None = 1500
    chunk_overlap: int | None = 20


@app.function()
async def log_api_usage(usage_data: dict):
    """Asynchronous function to log API usage to Supabase."""
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    try:
        usage_response = supabase.table("api_usage_logs").insert(usage_data).execute()
        if hasattr(usage_response, "error") and usage_response.error:
            print(f"Warning: Failed to log API usage: {usage_response.error}")
    except Exception as e:
        print(f"Error logging API usage: {str(e)}")


@web_app.post("/web_scrape")
async def scrape_url(request: Request, data: ScrapeRequest):
    import uuid
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    try:
        auth_header = request.headers.get("authorization")
        if not auth_header:
            raise HTTPException(
                status_code=401, detail="Authorization header is required"
            )

        try:
            uuid.UUID(auth_header)
        except ValueError:
            raise HTTPException(
                status_code=401, detail="Invalid authorization header format"
            )

        # Validate API key with Supabase
        response = (
            supabase.table("api_keys")
            .select("team_id, user_id, profiles(email)")
            .eq("api_key", auth_header)
            .single()
            .execute()
        )

        if not response.data or not response.data.get("team_id"):
            raise HTTPException(status_code=401, detail="Invalid API key")

        team_id = response.data["team_id"]

        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            result = await crawler.arun(
                data.url,
                config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    page_timeout=80000,
                ),
            )

            file_id = str(uuid.uuid4())
            file_name = f"{file_id}.txt"

            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=data.chunk_size, chunk_overlap=data.chunk_overlap
            )

            chunks = text_splitter.create_documents(
                texts=[result.markdown],
                metadatas=[{"source": data.url, "file_id": file_id}],
            )

            markdown_bytes = result.markdown.encode("utf-8")

            storage_response = supabase.storage.from_("user-documents").upload(
                path=f"{team_id}/{file_name}",
                file=markdown_bytes,
                file_options={"content-type": "text/plain"},
            )

            if hasattr(storage_response, "error") and storage_response.error:
                raise Exception(f"Storage upload failed: {storage_response.error}")

            embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
            )

            # Insert documents directly using Supabase client
            for chunk in chunks:
                embedding_vector = await embeddings.aembed_documents(
                    [chunk.page_content]
                )
                document_data = {
                    "content": chunk.page_content,
                    "metadata": chunk.metadata,
                    "embedding": embedding_vector[0],
                }
                doc_response = (
                    supabase.table("documents").insert(document_data).execute()
                )
                if hasattr(doc_response, "error") and doc_response.error:
                    raise Exception(f"Document insertion failed: {doc_response.error}")

            # Store file data in Supabase database
            file_data = {
                "file_id": file_id,
                "type": "web_scrape",
                "file_name": file_name,
                "team_id": team_id,
                "storage_path": f"{team_id}/{file_name}",
            }

            file_response = supabase.table("files").insert(file_data).execute()

            if hasattr(file_response, "error") and file_response.error:
                raise Exception(f"File data storage failed: {file_response.error}")

            response_data = {
                "markdown": result.markdown,
                "chunks": [
                    {"text": chunk.page_content, "metadata": chunk.metadata}
                    for chunk in chunks
                ],
                "file_id": file_id,
                "storage_path": f"{team_id}/{file_name}",
            }

            usage_data = {
                "user_id": response.data["user_id"],
                "endpoint": "/web_scrape",
                "success": True,
                "error": None,
            }

            # Spawn logging process asynchronously
            log_api_usage.spawn(usage_data)

            return response_data
    except HTTPException as e:
        # Log failed API usage for HTTP exceptions
        error_usage_data = {
            "user_id": (
                response.data["user_id"]
                if "response" in locals() and response.data
                else None
            ),
            "endpoint": "/web_scrape",
            "success": False,
            "error": e.detail,
        }
        log_api_usage.spawn(error_usage_data)
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        # Log failed API usage for general exceptions
        error_usage_data = {
            "user_id": (
                response.data["user_id"]
                if "response" in locals() and response.data
                else None
            ),
            "endpoint": "/web_scrape",
            "success": False,
            "error": str(e),
        }
        log_api_usage.spawn(error_usage_data)
        return {"error": str(e)}


@app.function()
@modal.asgi_app(label="api")
def fastapi_app():
    return web_app
