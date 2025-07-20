import os
import json
import requests
import hashlib
import time
from pathlib import Path
import uuid

# --- Configuration ---
SCRIPT_DIR = Path(__file__).parent

DB_NAME = "quebec-road-signs"
REAL_SIGN_PHOTOS_TABLE = "real_sign_photos"

# Cloudflare API Credentials (set as environment variables)
ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not ACCOUNT_ID or not API_TOKEN:
    print("ERROR: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN environment variables must be set.")
    exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
    'User-Agent': 'Real-Sign-Photos-Ingester/1.0 (contact@example.com)'
}

# --- Helper Functions (reused from ingest_montreal_opendata.py) ---
def get_d1_database_id(db_name: str) -> str | None:
    """Retrieves the UUID of a D1 database by name."""
    try:
        wrangler_output = os.popen(f"wrangler d1 list --json").read()
        dbs = json.loads(wrangler_output)
        db_info = next((db for db in dbs if db['name'] == db_name), None)
        if not db_info:
            print(f"ERROR: D1 database '{db_name}' not found. Please create it first.")
            return None
        return db_info['uuid']
    except Exception as e:
        print(f"ERROR: Could not get D1 database ID using wrangler: {e}")
        print("Please ensure wrangler is installed and configured, and the database exists.")
        return None

DATABASE_ID = get_d1_database_id(DB_NAME)
if not DATABASE_ID:
    exit(1)

def d1_bulk_import(table_name: str, sql_statements: list[str]):
    """Performs a bulk import of SQL statements to a D1 table using the REST API."""
    if not sql_statements:
        print(f"  No SQL statements for {table_name} to import.")
        return

    d1_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/d1/database/{DATABASE_ID}/import"

    full_sql_command = ";\n".join(sql_statements) + ";"
    hash_str = hashlib.md5(full_sql_command.encode('utf-8')).hexdigest()

    print(f"  Starting D1 bulk import for {len(sql_statements)} statements into {table_name}...")

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

        # 4. Polling
        bookmark = ingest_data["result"]["at_bookmark"]
        payload = {"action": "poll", "current_bookmark": bookmark}
        while True:
            poll_response = requests.post(d1_url, headers=HEADERS, json=payload, timeout=30)
            poll_response.raise_for_status()
            poll_result = poll_response.json()["result"]

            if poll_result["success"] or (not poll_result["success"] and poll_result["error"] == "Not currently importing anything."):
                break
            time.sleep(2) # Poll every 2 seconds

        if poll_result["success"]:
            print(f"  D1 bulk import for {table_name} completed successfully.")
        else:
            print(f"  D1 bulk import for {table_name} failed: {poll_result["error"]}")

    except requests.exceptions.RequestException as e:
        print(f"  Network/Request Error during D1 bulk import for {table_name}: {e}")
    except json.JSONDecodeError:
        print(f"  Failed to decode JSON response from D1 during bulk import for {table_name}: {init_response.text}")
    except Exception as e:
        print(f"  An unexpected error occurred during D1 bulk import for {table_name}: {e}")

def make_sql_insert(table_name: str, data: dict) -> str:
    """Generates a single SQL INSERT statement from a dictionary."""
    columns = ', '.join(data.keys())
    values = ', '.join([f"'{str(v).replace("'", "''")}'" if v is not None else "NULL" for v in data.values()])
    return f"INSERT INTO {table_name} ({columns}) VALUES ({values})"

def upload_image_to_cf_images(image_path: Path) -> str | None:
    """Uploads an image to Cloudflare Images and returns its public URL."""
    if not image_path.exists():
        print(f"  Image file not found: {image_path}")
        return None

    print(f"  Uploading {image_path.name} to Cloudflare Images...")
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/images/v1"
    
    files = {'file': open(image_path, 'rb')}
    headers_form_data = {
        "Authorization": f"Bearer {API_TOKEN}",
        'User-Agent': 'Real-Sign-Photos-Ingester/1.0 (contact@example.com)'
    }

    try:
        response = requests.post(url, headers=headers_form_data, files=files, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result["success"]:
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

# --- Main Ingestion Logic ---
def ingest_real_sign_photos():
    print("\n--- Starting Ingestion of Real-World Sign Photos ---")

    sql_statements_batch = []
    batch_size = 100 # Number of INSERT statements per D1 bulk import call

    while True:
        local_image_path_str = input("Enter local path to image file (or 'q' to quit): ").strip()
        if local_image_path_str.lower() == 'q':
            break

        local_image_path = Path(local_image_path_str)
        if not local_image_path.is_file():
            print("  Invalid file path. Please try again.")
            continue

        # 1. Upload image to Cloudflare Images
        cf_image_url = upload_image_to_cf_images(local_image_path)
        if not cf_image_url:
            print(f"  Failed to upload {local_image_path.name}. Skipping.")
            continue

        # 2. Collect metadata from user
        print("  Collecting metadata for the photo:")
        sign_code = input("    Sign Code (e.g., P-120-10, must exist in sign_definitions): ").strip()
        source = input("    Source (real_world_photo, synthetic_diffusion, google_street_view_screenshot): ").strip()
        latitude = input("    Latitude (optional, leave blank if unknown): ").strip()
        longitude = input("    Longitude (optional, leave blank if unknown): ").strip()
        municipality = input("    Municipality (optional, leave blank if unknown): ").strip()
        real_world_conditions_str = input("    Real-world conditions (comma-separated, e.g., snow_occlusion,blur): ").strip()
        is_synthetic_str = input("    Is synthetic? (yes/no): ").strip().lower()
        related_montreal_instance_id = input("    Related Montreal Instance ID (optional, leave blank if unknown): ").strip()

        real_world_conditions = [cond.strip() for cond in real_world_conditions_str.split(',')] if real_world_conditions_str else []
        is_synthetic = is_synthetic_str == 'yes'

        photo_data = {
            "photo_id": str(uuid.uuid4()),
            "sign_code": sign_code,
            "image_url": cf_image_url,
            "source": source,
            "latitude": float(latitude) if latitude else None,
            "longitude": float(longitude) if longitude else None,
            "municipality": municipality if municipality else None,
            "real_world_conditions": json.dumps(real_world_conditions), # Store as JSON string
            "is_synthetic": is_synthetic,
            "captured_date": time.strftime('%Y-%m-%d %H:%M:%S'),
            "related_montreal_instance_id": related_montreal_instance_id if related_montreal_instance_id else None
        }

        # Generate SQL for real_sign_photos
        sql_insert = make_sql_insert(REAL_SIGN_PHOTOS_TABLE, photo_data).replace("INSERT INTO", "INSERT OR REPLACE INTO")
        sql_statements_batch.append(sql_insert)

        if len(sql_statements_batch) >= batch_size:
            d1_bulk_import(REAL_SIGN_PHOTOS_TABLE, sql_statements_batch)
            sql_statements_batch = []

    # Import any remaining statements in the last batch
    if sql_statements_batch:
        d1_bulk_import(REAL_SIGN_PHOTOS_TABLE, sql_statements_batch)

    print("--- Finished Ingestion of Real-World Sign Photos ---")

if __name__ == '__main__':
    ingest_real_sign_photos()
