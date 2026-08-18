import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


BASE_URL = "https://college360.co.in"

# Start with the IISc page for testing.
# Later, this can contain all university URLs.
UNIVERSITY_URLS = [
    "https://college360.co.in/university/Indian-Institute-of-Science-IISc-Bangalore-3680/gallery"
]

OUTPUT_FILE = "gallery_data.json"


# --------------------------------------------------
# IMAGE EXTENSIONS
# --------------------------------------------------

IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".avif"
)


# --------------------------------------------------
# CHECK IMAGE URL
# --------------------------------------------------

def is_image_url(url):
    if not url:
        return False

    url = url.lower().split("?")[0]

    return url.endswith(IMAGE_EXTENSIONS)


# --------------------------------------------------
# CLEAN URL
# --------------------------------------------------

def clean_url(url, base_url):
    if not url:
        return None

    url = url.strip()

    if url.startswith("//"):
        url = "https:" + url

    elif url.startswith("/"):
        url = urljoin(base_url, url)

    elif not url.startswith("http"):
        url = urljoin(base_url, url)

    return url


# --------------------------------------------------
# EXTRACT UNIVERSITY NAME
# --------------------------------------------------

def extract_university_name(soup):

    # Try H1 first
    h1 = soup.find("h1")

    if h1:
        name = h1.get_text(" ", strip=True)

        if name:
            return name

    # Try page title
    if soup.title:
        title = soup.title.get_text(" ", strip=True)

        # Remove common suffix
        title = re.sub(
            r"\s*[-|]\s*(Info|Admission|Courses|Fees).*",
            "",
            title,
            flags=re.IGNORECASE
        )

        return title.strip()

    return "Unknown University"


# --------------------------------------------------
# EXTRACT IMAGES
# --------------------------------------------------

def extract_images(html, page_url):

    soup = BeautifulSoup(html, "html.parser")

    images = []

    # ----------------------------------------------
    # 1. Normal <img src="">
    # ----------------------------------------------

    for img in soup.find_all("img"):

        attributes = [
            img.get("src"),
            img.get("data-src"),
            img.get("data-lazy-src"),
            img.get("data-original"),
        ]

        # srcset can contain multiple images
        srcset = img.get("srcset")

        if srcset:
            for item in srcset.split(","):

                item = item.strip()

                if item:
                    attributes.append(
                        item.split(" ")[0]
                    )

        for image_url in attributes:

            image_url = clean_url(
                image_url,
                page_url
            )

            if image_url and is_image_url(image_url):

                images.append({
                    "image_url": image_url,
                    "alt": img.get("alt", "").strip()
                })

    # ----------------------------------------------
    # 2. Background images
    # ----------------------------------------------

    for element in soup.find_all(
        style=True
    ):

        style = element.get("style")

        matches = re.findall(
            r'url\(["\']?(.*?)["\']?\)',
            style
        )

        for image_url in matches:

            image_url = clean_url(
                image_url,
                page_url
            )

            if image_url and is_image_url(image_url):

                images.append({
                    "image_url": image_url,
                    "alt": ""
                })

    # ----------------------------------------------
    # Remove duplicate images
    # ----------------------------------------------

    unique_images = []

    seen = set()

    for image in images:

        image_url = image["image_url"]

        if image_url not in seen:

            seen.add(image_url)

            unique_images.append(image)

    return unique_images


# --------------------------------------------------
# SCRAPE ONE UNIVERSITY
# --------------------------------------------------

async def scrape_university(page, url):

    print("\n" + "=" * 60)
    print("Scraping:")
    print(url)
    print("=" * 60)

    try:

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Wait for JavaScript content
        await page.wait_for_timeout(5000)

        # Scroll to trigger lazy-loaded images
        for _ in range(5):

            await page.mouse.wheel(
                0,
                1500
            )

            await page.wait_for_timeout(
                1000
            )

        # Get fully rendered HTML
        html = await page.content()

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        university_name = extract_university_name(
            soup
        )

        images = extract_images(
            html,
            url
        )

        result = {
            "university_name": university_name,
            "university_url": url,
            "gallery_count": len(images),
            "gallery": images
        }

        print(
            f"University: {university_name}"
        )

        print(
            f"Images found: {len(images)}"
        )

        return result

    except Exception as error:

        print(
            f"ERROR: {error}"
        )

        return {
            "university_name": "Unknown",
            "university_url": url,
            "gallery_count": 0,
            "gallery": [],
            "error": str(error)
        }


# --------------------------------------------------
# SAVE JSON
# --------------------------------------------------

def save_json(data):

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(
        f"\nJSON saved successfully:"
        f" {OUTPUT_FILE}"
    )


# --------------------------------------------------
# MAIN
# --------------------------------------------------

async def main():

    print("\n")
    print("=" * 60)
    print("       COLLEGE360 UNIVERSITY GALLERY SCRAPER")
    print("=" * 60)

    results = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            }
        )

        for url in UNIVERSITY_URLS:

            result = await scrape_university(
                page,
                url
            )

            results.append(result)

        await browser.close()

    # Save everything
    save_json(results)

    print("\nScraping completed!")
    print(
        f"Universities scraped: {len(results)}"
    )


# --------------------------------------------------
# RUN
# --------------------------------------------------

if __name__ == "__main__":

    asyncio.run(main())