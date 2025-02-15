import modal

app = modal.App(name="supavec-api")

crawl4ai_image = modal.Image.debian_slim(python_version="3.10").run_commands(
    "pip install crawl4ai==0.4.248",
    "crawl4ai-setup",
)


@app.function(image=crawl4ai_image)
async def scrape_url(url: str):
    from crawl4ai import AsyncWebCrawler

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url)
        print(result.markdown[:300])


@app.local_entrypoint()
def main(url: str):
    scrape_url.remote(url)
