import os
import csv
import json
import requests
import hashlib
import time
from pathlib import Path

# --- Configuration ---
SCRIPT_DIR = Path(__file__).parent
DATASET_DIR = SCRIPT_DIR / 'dataset'
CSV_FILE_PATH = DATASET_DIR / 'dataset.csv'
IMAGES_LOCAL_DIR = DATASET_DIR / 'images'

DB_NAME = "quebec-road-signs"
SIGN_DEFINITIONS_TABLE = "sign_definitions"

# Cloudflare API Credentials (set as environment variables)
ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not ACCOUNT_ID or not API_TOKEN:
    print("ERROR: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN environment variables must be set.")
    exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
    'User-Agent': 'Quebec-Sign-Dataset-Ingester/1.0 (contact@example.com)' # Be descriptive!
}

# --- Helper Functions for Cloudflare API Interactions ---
def upload_image_to_cf_images(image_path: Path) -> str | None:
    """Uploads an image to Cloudflare Images and returns its public URL."""
    if not image_path.exists():
        print(f"  Image file not found: {image_path}")
        return None

    print(f"  Uploading {image_path.name} to Cloudflare Images...")
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/images/v1"
    
    files = {'file': open(image_path, 'rb')}
    # Cloudflare Images API uses form-data, so Content-Type should not be json
    headers_form_data = {
        "Authorization": f"Bearer {API_TOKEN}",
        'User-Agent': 'Quebec-Sign-Dataset-Ingester/1.0 (contact@example.com)'
    }

    try:
        response = requests.post(url, headers=headers_form_data, files=files, timeout=30)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        result = response.json()
        if result["success"]:
            print(f"  Successfully uploaded. Public URL: {result["result"]["variants"][0]}")
            return result["result"]["variants"][0] # Return the default public variant
        else:
            print(f"  Cloudflare Images API Error: {result["errors"]}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"  Network/Request Error uploading image: {e}")
        return None
    except json.JSONDecodeError:
        print(f"  Failed to decode JSON response from Cloudflare Images: {response.text}")
        return None

def d1_bulk_import(db_name: str, table_name: str, sql_statements: list[str]):
    """Performs a bulk import of SQL statements to a D1 table using the REST API."""
    if not sql_statements:
        print("  No SQL statements to import.")
        return

    # Get DATABASE_ID from wrangler (assuming it's already created)
    try:
        wrangler_output = os.popen(f"wrangler d1 list --json").read()
        dbs = json.loads(wrangler_output)
        db_info = next((db for db in dbs if db['name'] == db_name), None)
        if not db_info:
            print(f"ERROR: D1 database '{db_name}' not found. Please create it first.")
            return
        database_id = db_info['uuid']
    except Exception as e:
        print(f"ERROR: Could not get D1 database ID using wrangler: {e}")
        print("Please ensure wrangler is installed and configured, and the database exists.")
        return

    d1_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/d1/database/{database_id}/import"

    full_sql_command = ";\n".join(sql_statements) + ";"
    hash_str = hashlib.md5(full_sql_command.encode('utf-8')).hexdigest()

    print(f"  Starting D1 bulk import for {len(sql_statements)} statements...")

    try:
        # 1. Init upload
        init_response = requests.post(d1_url, headers=HEADERS, json={
            "action": "init",
            "etag": hash_str,
        }, timeout=30)
        init_response.raise_for_status()
        upload_data = init_response.json()
        upload_url = upload_data["result"]["upload_url"]
        filename = upload_data["result"]["filename"]

        # 2. Upload to R2 (via the provided upload_url)
        r2_response = requests.put(upload_url, data=full_sql_command.encode('utf-8'), timeout=60)
        r2_response.raise_for_status()
        r2_etag = r2_response.headers.get("ETag", "").replace('"', '')

        if r2_etag != hash_str:
            raise Exception(f"ETag mismatch during R2 upload. Expected {hash_str}, got {r2_etag}")

        # 3. Start ingestion
        ingest_response = requests.post(d1_url, headers=HEADERS, json={
            "action": "ingest",
            "etag": hash_str,
            "filename": filename,
        }, timeout=30)
        ingest_response.raise_for_status()
        ingest_data = ingest_response.json()
        print(f"  Ingestion initiated. Status: {ingest_data}")

        # 4. Polling
        bookmark = ingest_data["result"]["at_bookmark"]
        payload = {"action": "poll", "current_bookmark": bookmark}
        while True:
            poll_response = requests.post(d1_url, headers=HEADERS, json=payload, timeout=30)
            poll_response.raise_for_status()
            poll_result = poll_response.json()["result"]
            print(f"  Polling D1 import status: {poll_result}")

            if poll_result["success"] or (not poll_result["success"] and poll_result["error"] == "Not currently importing anything."):
                break
            time.sleep(2) # Poll every 2 seconds

        if poll_result["success"]:
            print("  D1 bulk import completed successfully.")
        else:
            print(f"  D1 bulk import failed: {poll_result["error"]}")

    except requests.exceptions.RequestException as e:
        print(f"  Network/Request Error during D1 bulk import: {e}")
    except json.JSONDecodeError:
        print(f"  Failed to decode JSON response during D1 bulk import: {response.text}")
    except Exception as e:
        print(f"  An unexpected error occurred during D1 bulk import: {e}")


def make_sql_insert(table_name: str, data: dict) -> str:
    """Generates a single SQL INSERT statement from a dictionary."""
    columns = ', '.join(data.keys())
    values = ', '.join([f"'{str(v).replace("'", "''")}'" if v is not None else "NULL" for v in data.values()])
    return f"INSERT INTO {table_name} ({columns}) VALUES ({values})"


# --- Main Ingestion Logic ---
def ingest_digital_assets():
    print("--- Starting Ingestion of Digital Assets ---")

    if not CSV_FILE_PATH.exists():
        print(f"ERROR: CSV file not found at {CSV_FILE_PATH}. Exiting.")
        return

    sql_statements_batch = []
    batch_size = 100 # Number of INSERT statements per D1 bulk import call

    with open(CSV_FILE_PATH, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for i, row in enumerate(reader):
            print(f"Processing row {i+1}: {row['reference_id']}")
            
            local_image_name = Path(row['image']).name # e.g., P-010-fr.png
            local_image_path = IMAGES_LOCAL_DIR / local_image_name

            cf_image_url = upload_image_to_cf_images(local_image_path)
            if not cf_image_url:
                print(f"  Skipping {row['reference_id']} due to image upload failure.")
                continue

            # Prepare data for sign_definitions table
            sign_definition_data = {
                "sign_code": row['reference_id'],
                "explanation_fr": row.get('explanation', '') or f"Panneau de signalisation du Québec: {row['reference_id']}.",
                "explanation_en": row.get('explanation', '') or f"Quebec road sign: {row['reference_id']}.",
                "category": None, # Will be populated later or manually
                "rpa_description": None,
                "rpa_code": None,
                "rtp_description": None,
                "original_digital_asset_url": cf_image_url
            }
            
            # Generate SQL for sign_definitions
            # Use INSERT OR REPLACE to handle updates if script is re-run
            sql_insert = make_sql_insert(SIGN_DEFINITIONS_TABLE, sign_definition_data).replace("INSERT INTO", "INSERT OR REPLACE INTO")
            sql_statements_batch.append(sql_insert)

            if len(sql_statements_batch) >= batch_size:
                d1_bulk_import(DB_NAME, SIGN_DEFINITIONS_TABLE, sql_statements_batch)
                sql_statements_batch = []

    # Import any remaining statements in the last batch
    if sql_statements_batch:
        d1_bulk_import(DB_NAME, SIGN_DEFINITIONS_TABLE, sql_statements_batch)

    print("--- Finished Ingestion of Digital Assets ---")

if __name__ == '__main__':
    ingest_digital_assets()
