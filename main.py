import modal
import os
from pydantic import BaseModel
from fastapi import Request, HTTPException
from supabase import create_client, Client

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
            supabase.table("api_keys").select("*").eq("api_key", auth_header).execute()
        )

        if not response.data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            result = await crawler.arun(
                data.url,
                config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    page_timeout=80000,
                ),
            )

            return {"markdown": result.markdown}
    except HTTPException as e:
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        return {"error": str(e)}
