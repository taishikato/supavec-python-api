import modal
import os
from pydantic import BaseModel
from fastapi import Request, HTTPException
from supabase import create_client, Client
import uuid
from io import BytesIO

crawl4ai_image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install("crawl4ai==0.4.247")
    .run_commands("crawl4ai-setup")
    .pip_install("fastapi[standard]", "supabase")
)

app = modal.App(
    name="supavec-api",
    image=crawl4ai_image,
    secrets=[modal.Secret.from_name("supavec")],
)


class ScrapeRequest(BaseModel):
    url: str
    chunk_size: int | None = 1500
    chunk_overlap: int | None = 20


@app.function()
@modal.web_endpoint(method="POST")
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

            # Generate a unique file ID and name
            file_id = str(uuid.uuid4())
            file_name = f"{file_id}.txt"

            # Convert markdown content to bytes
            markdown_bytes = result.markdown.encode("utf-8")

            # Upload to Supabase Storage
            storage_response = supabase.storage.from_("user-documents").upload(
                path=f"{team_id}/{file_name}",
                file=markdown_bytes,
                file_options={"content-type": "text/plain"},
            )

            if hasattr(storage_response, "error") and storage_response.error:
                raise Exception(f"Storage upload failed: {storage_response.error}")

            return {
                "markdown": result.markdown,
                "file_id": file_id,
                "storage_path": f"{team_id}/{file_name}",
            }
    except HTTPException as e:
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        return {"error": str(e)}
