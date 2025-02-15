import modal

crawl4ai_image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install("crawl4ai==0.4.247")
    .run_commands("crawl4ai-setup")
    .pip_install("fastapi[standard]")
)

app = modal.App(name="supavec-api", image=crawl4ai_image)


@app.function()
@modal.web_endpoint(method="POST")
async def scrape_url(data: dict):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

    url = data.get("url")

    try:
        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            result = await crawler.arun(
                url,
                config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    page_timeout=80000,
                ),
            )

            return {"markdown": result.markdown}
    except Exception as e:
        return {"error": str(e)}
