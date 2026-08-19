import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

OUTPUT_DIR = Path("data")

COMBINED_JSON_FILE = OUTPUT_DIR / "all_gallery_data.json"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    )
}


# ============================================================
# COLLEGE / UNIVERSITY URLS
# ============================================================

SOURCES = [

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
        "url": "https://college360.co.in/college/New-Horizon-College-of-Engineering-(NHCE)-Bangalore-3690/gallery"
    },

    {
        "name": "MSRIT_Bangalore",
        "url": "https://college360.co.in/college/MS-Ramaiah-Institute-of-Technology-RIT-Bangalore-3693/gallery"
    },

    {
        "name": "PESU_Bangalore",
        "url": "https://college360.co.in/college/PES-University-(PESU)-Bangalore-3686/gallery"
    },

    {
        "name": "NMIT_Bangalore",
        "url": "https://college360.co.in/college/Nitte-Meenakshi-Institute-of-Technology-NMIT-Bangalore-3687/gallery"
    },

    {
        "name": "CMRIT_Bangalore",
        "url": "https://college360.co.in/college/CMR-Institute-of-Technology-CMRIT-Bangalore-3684/gallery"
    },

    {
        "name": "Alliance_University_Bangalore",
        "url": "https://college360.co.in/university/Alliance-University-Bangalore-3678/gallery"
    },

    # --------------------------------------------------------
    # You can add more URLs here.
    #
    # If the same URL is added twice, the program will
    # automatically remove the duplicate.
    # --------------------------------------------------------

]


# ============================================================
# REMOVE DUPLICATE COLLEGE URLS
# ============================================================

def remove_duplicate_urls(sources):

    unique_sources = []

    seen_urls = set()

    for source in sources:

        url = source["url"].strip()

        # Normalize URL
        url = url.rstrip("/")

        if url in seen_urls:

            print(
                f"Duplicate URL removed: "
                f"{source['name']}"
            )

            continue

        seen_urls.add(url)

        unique_sources.append(
            {
                "name": source["name"],
                "url": url
            }
        )

    return unique_sources


# ============================================================
# CLEAN URL
# ============================================================

def clean_url(url, base_url):

    if not url:
        return None

    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    return urljoin(
        base_url,
        url
    )


# ============================================================
# CHECK IMAGE URL
# ============================================================

def is_image_url(url):

    if not url:
        return False

    url_without_query = (
        url.lower()
        .split("?")[0]
    )

    image_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".avif",
        ".bmp",
        ".svg"
    )

    return url_without_query.endswith(
        image_extensions
    )


# ============================================================
# EXTRACT IMAGE URLS
# ============================================================

def extract_image_urls(
    html,
    page_url
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    image_urls = []

    # --------------------------------------------------------
    # <img> TAGS
    # --------------------------------------------------------

    for img in soup.find_all("img"):

        possible_urls = [

            img.get("src"),

            img.get("data-src"),

            img.get("data-lazy-src"),

            img.get("data-original"),

            img.get("data-image"),

            img.get("data-url"),

            img.get("data-fsrc")
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
        # PROCESS IMAGE URLS
        # ----------------------------------------------------

        for image_url in possible_urls:

            image_url = clean_url(
                image_url,
                page_url
            )

            if not image_url:
                continue

            if not is_image_url(image_url):
                continue

            image_urls.append(
                image_url
            )

    # --------------------------------------------------------
    # <a href=""> IMAGE LINKS
    # --------------------------------------------------------

    for link in soup.find_all("a"):

        href = link.get("href")

        href = clean_url(
            href,
            page_url
        )

        if not href:
            continue

        if is_image_url(href):

            image_urls.append(
                href
            )

    # --------------------------------------------------------
    # BACKGROUND IMAGES
    # --------------------------------------------------------

    for element in soup.find_all(
        style=True
    ):

        style = element.get("style")

        if not style:
            continue

        import re

        matches = re.findall(
            r'url\(["\']?(.*?)["\']?\)',
            style
        )

        for image_url in matches:

            image_url = clean_url(
                image_url,
                page_url
            )

            if not image_url:
                continue

            if is_image_url(image_url):

                image_urls.append(
                    image_url
                )

    # --------------------------------------------------------
    # REMOVE DUPLICATE IMAGE URLS
    # --------------------------------------------------------

    unique_image_urls = []

    seen_images = set()

    for image_url in image_urls:

        # Remove URL fragment
        image_url = image_url.split("#")[0]

        if image_url in seen_images:
            continue

        seen_images.add(
            image_url
        )

        unique_image_urls.append(
            image_url
        )

    return unique_image_urls


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(
    image_url
):

    try:

        response = requests.get(
            image_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        content = response.content

        if not content:
            return None

        content_type = response.headers.get(
            "Content-Type",
            ""
        ).lower()

        # ----------------------------------------------------
        # Detect MIME type
        # ----------------------------------------------------

        if "image/" in content_type:

            mime_type = content_type.split(
                ";"
            )[0].strip()

        else:

            mime_type = (
                mimetypes.guess_type(
                    urlparse(
                        image_url
                    ).path
                )[0]
                or "image/jpeg"
            )

        # ----------------------------------------------------
        # Convert image bytes to Base64
        # ----------------------------------------------------

        base64_data = base64.b64encode(
            content
        ).decode(
            "utf-8"
        )

        return {
            "mime_type": mime_type,
            "base64_data": base64_data,
            "size_bytes": len(content)
        }

    except Exception as error:

        print(
            f"    Download failed: "
            f"{error}"
        )

        return None


# ============================================================
# GET PAGE TITLE
# ============================================================

def get_page_title(soup):

    if soup.title:

        return soup.title.get_text(
            " ",
            strip=True
        )

    return ""


# ============================================================
# GET COLLEGE NAME
# ============================================================

def get_college_name(
    soup,
    default_name
):

    # --------------------------------------------------------
    # Try H1
    # --------------------------------------------------------

    h1 = soup.find("h1")

    if h1:

        name = h1.get_text(
            " ",
            strip=True
        )

        if name:

            return name

    # --------------------------------------------------------
    # Try page title
    # --------------------------------------------------------

    title = get_page_title(
        soup
    )

    if title:

        return title

    return default_name


# ============================================================
# SCRAPE ONE COLLEGE
# ============================================================

async def scrape_college(
    page,
    source
):

    college_name = source["name"]

    college_url = source["url"]

    print("\n")
    print("=" * 75)
    print(
        f"SCRAPING: {college_name}"
    )
    print("=" * 75)

    try:

        # ----------------------------------------------------
        # OPEN PAGE
        # ----------------------------------------------------

        await page.goto(
            college_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        print(
            "✓ Page loaded"
        )

        # ----------------------------------------------------
        # Wait for JavaScript
        # ----------------------------------------------------

        await page.wait_for_timeout(
            5000
        )

        # ----------------------------------------------------
        # Scroll page
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

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # ----------------------------------------------------
        # College name
        # ----------------------------------------------------

        actual_name = get_college_name(
            soup,
            college_name
        )

        # ----------------------------------------------------
        # Extract image URLs
        # ----------------------------------------------------

        image_urls = extract_image_urls(
            html,
            college_url
        )

        print(
            f"✓ Unique images found: "
            f"{len(image_urls)}"
        )

        # ----------------------------------------------------
        # Download images and convert Base64
        # ----------------------------------------------------

        gallery = []

        for index, image_url in enumerate(
            image_urls,
            start=1
        ):

            print(
                f"  Downloading image "
                f"{index}/{len(image_urls)}"
            )

            image_data = download_image(
                image_url
            )

            if image_data is None:

                print(
                    "    ✗ Skipped"
                )

                continue

            gallery.append(
                {
                    "image_number": index,

                    "image_url": image_url,

                    "mime_type": image_data[
                        "mime_type"
                    ],

                    "size_bytes": image_data[
                        "size_bytes"
                    ],

                    "image_base64": image_data[
                        "base64_data"
                    ]
                }
            )

            print(
                "    ✓ Saved in JSON"
            )

        # ----------------------------------------------------
        # Final JSON object
        # ----------------------------------------------------

        result = {

            "college_name": actual_name,

            "source_name": college_name,

            "college_url": college_url,

            "total_images_found": len(
                image_urls
            ),

            "total_images_saved": len(
                gallery
            ),

            "gallery": gallery
        }

        # ----------------------------------------------------
        # Create college directory
        # ----------------------------------------------------

        college_dir = (
            OUTPUT_DIR /
            college_name
        )

        college_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Save individual JSON
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
                result,
                file,
                indent=4,
                ensure_ascii=False
            )

        print(
            f"\n✓ JSON saved:"
            f" {json_file}"
        )

        return result

    except Exception as error:

        print(
            f"\n✗ Error:"
            f" {error}"
        )

        # ----------------------------------------------------
        # Save error JSON
        # ----------------------------------------------------

        error_result = {

            "college_name": college_name,

            "source_name": college_name,

            "college_url": college_url,

            "total_images_found": 0,

            "total_images_saved": 0,

            "gallery": [],

            "error": str(error)
        }

        college_dir = (
            OUTPUT_DIR /
            college_name
        )

        college_dir.mkdir(
            parents=True,
            exist_ok=True
        )

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
                error_result,
                file,
                indent=4,
                ensure_ascii=False
            )

        return error_result


# ============================================================
# SAVE COMBINED JSON
# ============================================================

def save_combined_json(
    results
):

    combined_data = {

        "total_unique_colleges": len(
            results
        ),

        "colleges": results
    }

    with open(
        COMBINED_JSON_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            combined_data,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(
        f"\n✓ Combined JSON saved:"
        f" {COMBINED_JSON_FILE}"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    print("\n")

    print("=" * 75)
    print(
        "       COLLEGE360 GALLERY SCRAPER"
    )
    print("=" * 75)

    # --------------------------------------------------------
    # Remove duplicate college URLs
    # --------------------------------------------------------

    unique_sources = remove_duplicate_urls(
        SOURCES
    )

    print(
        f"\nTotal URLs provided: "
        f"{len(SOURCES)}"
    )

    print(
        f"Unique URLs to scrape: "
        f"{len(unique_sources)}"
    )

    # --------------------------------------------------------
    # Create output directory
    # --------------------------------------------------------

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    results = []

    # --------------------------------------------------------
    # Start Playwright
    # --------------------------------------------------------

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            },

            user_agent=HEADERS[
                "User-Agent"
            ]
        )

        # ----------------------------------------------------
        # Scrape unique colleges
        # ----------------------------------------------------

        for source in unique_sources:

            result = await scrape_college(
                page,
                source
            )

            results.append(
                result
            )

        await browser.close()

    # --------------------------------------------------------
    # Save combined JSON
    # --------------------------------------------------------

    save_combined_json(
        results
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print("\n")

    print("=" * 75)
    print(
        "SCRAPING COMPLETED"
    )
    print("=" * 75)

    for result in results:

        print(
            f"{result['source_name']}: "
            f"{result['total_images_saved']} "
            f"images saved"
        )

    print("\n")
    print(
        "✓ All image data is stored "
        "inside JSON as Base64."
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )