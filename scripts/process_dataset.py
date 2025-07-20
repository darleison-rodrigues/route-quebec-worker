

import os
import re
import csv
import requests
import json
from urllib.parse import urljoin
from pathlib import Path

# --- Configuration ---
script_dir = Path(__file__).parent
base_dir = script_dir.parent / 'dataset'
html_source_file = base_dir / 'images.html'
images_output_dir = base_dir / 'images'
csv_file_path = base_dir / 'dataset.csv'
metadata_jsonl_path = script_dir.parent / 'data' / 'metadata.jsonl' # New line
base_url = 'https://commons.wikimedia.org'

# --- Wikimedia Policy Compliance ---
# Set a descriptive User-Agent. Replace with your actual contact info.
# This is REQUIRED by Wikimedia's policy.
headers = {
    'User-Agent': 'Quebec-Sign-Dataset-Builder/1.0 (https://rdltechworks.com; darleison.rodrigues@rdltechworks.ca)'
}

# --- Setup ---
os.makedirs(images_output_dir, exist_ok=True)
img_regex = re.compile(r'<img\s+[^>]*?alt="([^"]*)"[^>]*?src="([^"]*)"[^>]*?>', re.IGNORECASE)

# Load metadata from metadata.jsonl
metadata_entries = {}
if os.path.exists(metadata_jsonl_path):
    with open(metadata_jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line)
                metadata_entries[entry['reference_id']] = entry
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON from line: {line.strip()} - {e}")
    print(f"Loaded {len(metadata_entries)} metadata entries from {metadata_jsonl_path}")
else:
    print(f"WARNING: Metadata JSONL not found at {metadata_jsonl_path}. Using default N/A values.")


# --- Main Logic ---
def process_source_file():
    """
    Scans a single HTML file, downloads images with a proper User-Agent,
    and populates a CSV including the source URL.
    """
    print("--- Starting Dataset Processor ---")
    print(f"Using User-Agent: {headers['User-Agent']}")
    print(f"Please update the User-Agent in the script with your contact information.\n")

    if not os.path.exists(html_source_file):
        print(f"ERROR: Source file not found at '{html_source_file}'. Exiting.")
        return

    new_csv_rows = []
    processed_refs = set()

    # Load existing reference IDs from CSV to avoid duplicates
    try:
        if os.path.exists(csv_file_path) and os.path.getsize(csv_file_path) > 0:
            with open(csv_file_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                try:
                    next(reader) # Skip header
                    for row in reader:
                        if row:
                            processed_refs.add(row[0])
                except StopIteration:
                    pass # File is empty
            print(f"Loaded {len(processed_refs)} existing reference IDs from CSV.")
    except IOError as e:
        print(f"Could not read existing CSV file, starting fresh. Reason: {e}")

    print(f"Processing source file: {html_source_file}")
    try:
        with open(html_source_file, 'r', encoding='utf-8') as f:
            content = f.read()
            matches = img_regex.finditer(content)

            for match in matches:
                reference_id = match.group(1).strip()
                image_url = match.group(2).strip()

                if not reference_id or not image_url or reference_id in processed_refs:
                    continue

                full_image_url = urljoin(base_url, image_url)
                print(f"Processing new reference: {reference_id}")
                
                # Download the image using the required headers
                response = requests.get(full_image_url, headers=headers, stream=True, timeout=10)
                response.raise_for_status()

                safe_filename_base = "".join(c for c in reference_id if c.isalnum() or c in ('-', '_')).rstrip()
                file_ext = Path(full_image_url.split('?')[0]).suffix or '.jpg'
                image_filename = f"{safe_filename_base}{file_ext}"
                local_image_path = images_output_dir / image_filename

                with open(local_image_path, 'wb') as img_f:
                    for chunk in response.iter_content(8192):
                        img_f.write(chunk)
                
                print(f"  -> Image saved to: {local_image_path}")

                # Get sign definition from loaded metadata, or use N/A if not found
                sign_info = metadata_entries.get(reference_id, {})
                explanation_fr = sign_info.get('explanation_fr', 'N/A')
                explanation_en = sign_info.get('explanation_en', 'N/A')
                # Category, RPA/RTP descriptions are not in metadata.jsonl, so keep as N/A for now
                category = "N/A"
                rpa_description = "N/A"
                rpa_code = "N/A"
                rtp_description = "N/A"

                new_csv_rows.append([
                    reference_id, explanation_fr, explanation_en, category,
                    rpa_description, rpa_code, rtp_description, full_image_url
                ])
                processed_refs.add(reference_id)

    except requests.exceptions.RequestException as e:
        print(f"  -> ERROR: Could not download an image. Reason: {e}. Check your User-Agent header.")
    except Exception as e:
        print(f"  -> ERROR: An unexpected error occurred. Reason: {e}")

    if new_csv_rows:
        print(f"\nFound {len(new_csv_rows)} new items. Appending to {csv_file_path}...")
        is_new_file = not os.path.exists(csv_file_path) or os.path.getsize(csv_file_path) == 0
        try:
            with open(csv_file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if is_new_file:
                    # Updated header row to match sign_definitions schema
                    writer.writerow([
                        'sign_code', 'explanation_fr', 'explanation_en', 'category',
                        'rpa_description', 'rpa_code', 'rtp_description', 'original_digital_asset_url'
                    ])
                writer.writerows(new_csv_rows)
            print("CSV file updated successfully.")
        except IOError as e:
            print(f"  -> ERROR: Could not write to CSV file. Reason: {e}")
    else:
        print("\nNo new items found to add to the dataset.")

    print("\n--- Script Finished ---")

if __name__ == '__main__':
    process_source_file()
