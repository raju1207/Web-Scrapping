import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path("data")

COLLEGES = [
    {
        "name": "IIITB_Bangalore",
        "url": "https://college360.co.in/college/IIITB-Bangalore-International-Institute-of-Information-Technology-3993/gallery"
    },
    {
        "name": "BMSCE_Bangalore",
        "url": "https://college360.co.in/college/BMS-College-of-Engineering-BMSCE-Bangalore-3692/gallery"
    },
    {
        "name": "AIT_Bangalore",
        "url": "https://college360.co.in/college/Dr.-Ambedkar-Institute-of-Technology-AIT-Bangalore-3685/gallery"
    },
    {
        "name": "Jain_University_Bangalore",
        "url": "https://college360.co.in/college/Jain-University-JU-Bangalore-3691/gallery"
    },
    {
        "name": "NHCE_Bangalore",
        "url": "https://college360.co.in/college/New-Horizon-College-of-Engineering-%28NHCE%29-Bangalore-3690/gallery"
    }
]


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    )
}


# ============================================================
# CREATE FOLDER
# ============================================================

def create_college_folders(college_name):

    college_dir = BASE_DIR / college_name
    images_dir = college_dir / "images"

    college_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    images_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return college_dir, images_dir


# ============================================================
# CLEAN IMAGE URL
# ============================================================

def clean_image_url(image_url, page_url):

    if not image_url:
        return None

    image_url = image_url.strip()

    if image_url.startswith("//"):
        image_url = "https:" + image_url

    elif image_url.startswith("/"):
        image_url = urljoin(
            page_url,
            image_url
        )

    elif not image_url.startswith("http"):
        image_url = urljoin(
            page_url,
            image_url
        )

    return image_url


# ============================================================
# CHECK IMAGE URL
# ============================================================

def is_valid_image_url(url):

    if not url:
        return False

    url_lower = url.lower()

    # Remove query parameters
    url_lower = url_lower.split("?")[0]

    extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".avif"
    )

    return url_lower.endswith(extensions)


# ============================================================
# EXTRACT IMAGE URLS
# ============================================================

def extract_image_urls(html, page_url):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    image_urls = []

    # --------------------------------------------------------
    # IMG TAGS
    # --------------------------------------------------------

    for img in soup.find_all("img"):

        possible_urls = [
            img.get("src"),
            img.get("data-src"),
            img.get("data-lazy-src"),
            img.get("data-original"),
            img.get("data-image"),
        ]

        # ----------------------------------------------------
        # SRCSET
        # ----------------------------------------------------

        srcset = img.get("srcset")

        if srcset:

            for item in srcset.split(","):

                item = item.strip()

                if item:

                    possible_urls.append(
                        item.split()[0]
                    )

        # ----------------------------------------------------
        # PROCESS URLs
        # ----------------------------------------------------

        for image_url in possible_urls:

            image_url = clean_image_url(
                image_url,
                page_url
            )

            if image_url:

                image_urls.append(
                    image_url
                )

    # --------------------------------------------------------
    # LINK TAGS
    # --------------------------------------------------------

    for link in soup.find_all("a"):

        href = link.get("href")

        href = clean_image_url(
            href,
            page_url
        )

        if href and is_valid_image_url(href):

            image_urls.append(href)

    # --------------------------------------------------------
    # BACKGROUND IMAGES
    # --------------------------------------------------------

    for element in soup.find_all(
        style=True
    ):

        style = element.get("style")

        matches = re.findall(
            r'url\(["\']?(.*?)["\']?\)',
            style
        )

        for image_url in matches:

            image_url = clean_image_url(
                image_url,
                page_url
            )

            if image_url:

                image_urls.append(
                    image_url
                )

    # --------------------------------------------------------
    # REMOVE DUPLICATES
    # --------------------------------------------------------

    unique_urls = []

    seen = set()

    for url in image_urls:

        if url not in seen:

            seen.add(url)

            unique_urls.append(url)

    return unique_urls


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(
    image_url,
    image_path
):

    try:

        response = requests.get(
            image_url,
            headers=HEADERS,
            timeout=30,
            stream=True
        )

        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type",
            ""
        ).lower()

        # ----------------------------------------------------
        # Check whether response is actually an image
        # ----------------------------------------------------

        if "image" not in content_type:

            print(
                f"    ⚠ Not an image: {image_url}"
            )

            return False

        with open(
            image_path,
            "wb"
        ) as file:

            for chunk in response.iter_content(
                chunk_size=8192
            ):

                if chunk:

                    file.write(chunk)

        return True

    except Exception as error:

        print(
            f"    ✗ Download failed: {error}"
        )

        return False


# ============================================================
# GET IMAGE EXTENSION
# ============================================================

def get_image_extension(url):

    parsed_url = urlparse(url)

    path = parsed_url.path.lower()

    if path.endswith(".jpeg"):
        return ".jpeg"

    if path.endswith(".png"):
        return ".png"

    if path.endswith(".webp"):
        return ".webp"

    if path.endswith(".gif"):
        return ".gif"

    if path.endswith(".avif"):
        return ".avif"

    return ".jpg"


# ============================================================
# SCRAPE ONE COLLEGE
# ============================================================

async def scrape_college(
    page,
    college
):

    college_name = college["name"]
    college_url = college["url"]

    print("\n")
    print("=" * 70)
    print(f"SCRAPING: {college_name}")
    print("=" * 70)

    college_dir, images_dir = create_college_folders(
        college_name
    )

    try:

        # ----------------------------------------------------
        # OPEN PAGE
        # ----------------------------------------------------

        await page.goto(
            college_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        print("✓ Page loaded")

        # ----------------------------------------------------
        # Wait for JavaScript
        # ----------------------------------------------------

        await page.wait_for_timeout(
            5000
        )

        # ----------------------------------------------------
        # Scroll page
        # This helps lazy-loaded gallery images
        # ----------------------------------------------------

        for _ in range(10):

            await page.mouse.wheel(
                0,
                1500
            )

            await page.wait_for_timeout(
                1000
            )

        # ----------------------------------------------------
        # Get rendered HTML
        # ----------------------------------------------------

        html = await page.content()

        # ----------------------------------------------------
        # Extract images
        # ----------------------------------------------------

        image_urls = extract_image_urls(
            html,
            college_url
        )

        print(
            f"✓ Found {len(image_urls)} image URLs"
        )

        # ----------------------------------------------------
        # Download images
        # ----------------------------------------------------

        gallery_data = []

        for index, image_url in enumerate(
            image_urls,
            start=1
        ):

            extension = get_image_extension(
                image_url
            )

            filename = (
                f"image_{index:03d}"
                f"{extension}"
            )

            image_path = (
                images_dir /
                filename
            )

            print(
                f"  [{index}/{len(image_urls)}] "
                f"Downloading {filename}"
            )

            success = download_image(
                image_url,
                image_path
            )

            if success:

                gallery_data.append(
                    {
                        "image_number": index,
                        "image_url": image_url,
                        "local_file": str(
                            Path("images") /
                            filename
                        )
                    }
                )

        # ----------------------------------------------------
        # JSON DATA
        # ----------------------------------------------------

        json_data = {
            "college_name": college_name,
            "college_url": college_url,
            "total_images": len(
                gallery_data
            ),
            "gallery": gallery_data
        }

        # ----------------------------------------------------
        # SAVE JSON
        # ----------------------------------------------------

        json_file = (
            college_dir /
            "gallery.json"
        )

        with open(
            json_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                json_data,
                file,
                indent=4,
                ensure_ascii=False
            )

        print(
            f"\n✓ JSON saved:"
            f" {json_file}"
        )

        print(
            f"✓ Images saved:"
            f" {images_dir}"
        )

        return json_data

    except Exception as error:

        print(
            f"\n✗ Error scraping "
            f"{college_name}: {error}"
        )

        return {
            "college_name": college_name,
            "college_url": college_url,
            "total_images": 0,
            "gallery": [],
            "error": str(error)
        }


# ============================================================
# MAIN
# ============================================================

async def main():

    print("\n")
    print("=" * 70)
    print("       COLLEGE360 GALLERY SCRAPER")
    print("=" * 70)

    BASE_DIR.mkdir(
        exist_ok=True
    )

    results = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            },
            user_agent=HEADERS["User-Agent"]
        )

        # ----------------------------------------------------
        # SCRAPE EACH COLLEGE
        # ----------------------------------------------------

        for college in COLLEGES:

            result = await scrape_college(
                page,
                college
            )

            results.append(
                result
            )

        await browser.close()

    print("\n")
    print("=" * 70)
    print("SCRAPING COMPLETED")
    print("=" * 70)

    print(
        f"Total colleges: {len(results)}"
    )

    for result in results:

        print(
            f"{result['college_name']}: "
            f"{result['total_images']} images"
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())