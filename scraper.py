import os
import re
import requests
import pandas as pd

from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

URL = "https://college360.co.in/university/Indian-Institute-of-Science-IISc-Bangalore-3680"

DATA_FOLDER = "data"
IMAGE_FOLDER = os.path.join(DATA_FOLDER, "gallery")

CSV_FILE = os.path.join(DATA_FOLDER, "gallery_data.csv")


# ============================================================
# CREATE FOLDERS
# ============================================================

os.makedirs(DATA_FOLDER, exist_ok=True)
os.makedirs(IMAGE_FOLDER, exist_ok=True)


# ============================================================
# CLEAN FILE NAME
# ============================================================

def clean_filename(name):

    name = re.sub(r'[<>:"/\\|?*]', '_', name)

    name = re.sub(r'\s+', '_', name)

    return name.strip("_")


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(image_url, folder, number):

    try:

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            )
        }

        response = requests.get(
            image_url,
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:

            print(
                f"❌ Image failed: "
                f"{response.status_code}"
            )

            return ""

        # ----------------------------------------------------
        # Get extension
        # ----------------------------------------------------

        path = urlparse(image_url).path

        extension = os.path.splitext(path)[1].lower()

        if extension not in [
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif"
        ]:

            extension = ".jpg"

        filename = (
            f"image_{number:03d}"
            f"{extension}"
        )

        filepath = os.path.join(
            folder,
            filename
        )

        with open(filepath, "wb") as file:

            file.write(response.content)

        print(f"✅ Downloaded: {filename}")

        return filepath

    except Exception as error:

        print(
            f"❌ Download error: {error}"
        )

        return ""


# ============================================================
# SCRAPE GALLERY
# ============================================================

def scrape_gallery():

    print("\n" + "=" * 60)

    print("College360 Gallery Scraper")

    print("=" * 60)

    print("\nOpening website...")

    results = []

    with sync_playwright() as playwright:

        browser = playwright.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            }
        )

        try:

            page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            print("✅ Page loaded")

        except Exception as error:

            print(
                f"⚠️ Page loading warning: {error}"
            )

        # Give JavaScript time to load
        page.wait_for_timeout(7000)

        # ----------------------------------------------------
        # University name
        # ----------------------------------------------------

        university_name = "Unknown University"

        try:

            university_name = page.locator(
                "h1"
            ).first.inner_text(
                timeout=10000
            ).strip()

        except Exception:

            print(
                "⚠️ University name not found"
            )

        print(
            f"\nUniversity: {university_name}"
        )

        # ----------------------------------------------------
        # Find Gallery section
        # ----------------------------------------------------

        print("\nSearching for Gallery section...")

        gallery_images = []

        # Look for text containing Gallery
        gallery_text = page.get_by_text(
            re.compile(
                r"gallery",
                re.IGNORECASE
            )
        )

        gallery_count = gallery_text.count()

        print(
            f"Gallery elements found: "
            f"{gallery_count}"
        )

        # ----------------------------------------------------
        # Method 1:
        # Find images near Gallery section
        # ----------------------------------------------------

        if gallery_count > 0:

            for i in range(gallery_count):

                try:

                    gallery_element = (
                        gallery_text.nth(i)
                    )

                    # Find nearest parent containers
                    parent = gallery_element.locator(
                        "xpath=.."
                    )

                    # Search images inside parent
                    images = parent.locator("img")

                    count = images.count()

                    for j in range(count):

                        img = images.nth(j)

                        src = (
                            img.get_attribute("src")
                        )

                        if not src:

                            src = (
                                img.get_attribute(
                                    "data-src"
                                )
                            )

                        if not src:

                            src = (
                                img.get_attribute(
                                    "data-lazy-src"
                                )
                            )

                        if src:

                            image_url = urljoin(
                                URL,
                                src
                            )

                            gallery_images.append(
                                image_url
                            )

                except Exception:

                    continue

        # ----------------------------------------------------
        # Remove duplicates
        # ----------------------------------------------------

        gallery_images = list(
            dict.fromkeys(
                gallery_images
            )
        )

        print(
            f"\nGallery images found: "
            f"{len(gallery_images)}"
        )

        # ----------------------------------------------------
        # Create university folder
        # ----------------------------------------------------

        folder_name = clean_filename(
            university_name
        )

        university_folder = os.path.join(
            IMAGE_FOLDER,
            folder_name
        )

        os.makedirs(
            university_folder,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Download images
        # ----------------------------------------------------

        for index, image_url in enumerate(
            gallery_images,
            start=1
        ):

            local_file = download_image(
                image_url,
                university_folder,
                index
            )

            results.append({

                "university_name":
                    university_name,

                "university_url":
                    URL,

                "image_url":
                    image_url,

                "local_file":
                    local_file

            })

        browser.close()

    # ========================================================
    # SAVE CSV
    # ========================================================

    if results:

        df = pd.DataFrame(
            results
        )

        df.to_csv(
            CSV_FILE,
            index=False,
            encoding="utf-8-sig"
        )

        print("\n" + "=" * 60)

        print("✅ SCRAPING COMPLETED")

        print("=" * 60)

        print(
            f"Images: {len(results)}"
        )

        print(
            f"CSV: {CSV_FILE}"
        )

        print(
            f"Images: {IMAGE_FOLDER}"
        )

    else:

        print("\n❌ No gallery images found.")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    scrape_gallery()