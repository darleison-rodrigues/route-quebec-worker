import os
import json
import requests
import hashlib
import time
from pathlib import Path

# --- Configuration ---
DB_NAME = "quebec-road-signs"

# Cloudflare API Credentials (set as environment variables)
ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

# Vectorize Index IDs (YOU MUST CREATE THESE IN CLOUDFLARE)
IMAGE_VECTORIZE_INDEX_ID = "quebec-sign-images-vector-index" # e.g., --dimensions 512 --metric cosine
TEXT_VECTORIZE_INDEX_ID = "quebec-sign-text-vector-index"   # e.g., --dimensions 768 --metric cosine

if not ACCOUNT_ID or not API_TOKEN:
    print("ERROR: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN environment variables must be set.")
    exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
    'User-Agent': 'Quebec-Sign-Embeddings-Generator/1.0 (contact@example.com)'
}

# --- Helper Functions ---
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

def query_d1(query: str) -> list[dict]:
    """Executes a read-only query against D1 and returns results."""
    d1_query_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/d1/database/{DATABASE_ID}/query"
    try:
        response = requests.post(d1_query_url, headers=HEADERS, json={'sql': query}, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result["success"]:
            # D1 query results are in result[0]['results'] for simple queries
            return result["result"][0]['results'] if result["result"] and 'results' in result["result"][0] else []
        else:
            print(f"D1 Query Error: {result["errors"]}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"Network/Request Error querying D1: {e}")
        return []
    except json.JSONDecodeError:
        print(f"Failed to decode JSON response from D1: {response.text}")
        return []

def generate_image_embedding(image_url: str) -> list[float] | None:
    """Generates an image embedding using Cloudflare AI."""
    ai_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/@cf/openai/clip-vit-base-patch32"
    try:
        # Download image first
        image_response = requests.get(image_url, stream=True, timeout=10)
        image_response.raise_for_status()
        image_data = image_response.content

        # Send image data to AI model
        response = requests.post(ai_url, headers=HEADERS, data=image_data, timeout=60)
        response.raise_for_status()
        result = response.json()
        if result["success"]:
            return result["result"]["data"]
        else:
            print(f"  Cloudflare AI Image Embedding Error for {image_url}: {result["errors"]}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"  Network/Request Error generating image embedding for {image_url}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"  Failed to decode JSON response from AI: {response.text}")
        return None

def generate_text_embedding(text: str) -> list[float] | None:
    """Generates a text embedding using Cloudflare AI."""
    ai_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/@cf/baai/bge-large-en-v1.5"
    try:
        response = requests.post(ai_url, headers=HEADERS, json={'text': text}, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result["success"]:
            return result["result"]["data"]
        else:
            print(f"  Cloudflare AI Text Embedding Error for \"{text[:50]}...\": {result["errors"]}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"  Network/Request Error generating text embedding for \"{text[:50]}...\": {e}")
        return None
    except json.JSONDecodeError:
        print(f"  Failed to decode JSON response from AI: {response.text}")
        return None

def insert_embeddings_to_vectorize(index_id: str, vectors: list[dict]):
    """Inserts a batch of vectors into a Vectorize index."""
    if not vectors:
        return

    vectorize_url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/indexes/{index_id}/upsert"
    payload = {"vectors": vectors}

    try:
        response = requests.post(vectorize_url, headers=HEADERS, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        if result["success"]:
            print(f"  Successfully inserted {len(vectors)} vectors into Vectorize index '{index_id}'.")
        else:
            print(f"  Vectorize Upsert Error for index '{index_id}': {result["errors"]}")
    except requests.exceptions.RequestException as e:
        print(f"  Network/Request Error inserting vectors into Vectorize index '{index_id}': {e}")
    except json.JSONDecodeError:
        print(f"  Failed to decode JSON response from Vectorize: {response.text}")


# --- Main Embedding Generation Logic ---
def generate_all_embeddings():
    print("--- Starting Embedding Generation ---")

    image_vectors_batch = []
    text_vectors_batch = []
    batch_size = 50 # Adjust based on Vectorize limits and performance

    # 1. Generate Image Embeddings for sign_definitions (canonical images)
    print("\nGenerating image embeddings for sign_definitions...")
    sign_defs = query_d1("SELECT sign_code, original_digital_asset_url FROM sign_definitions WHERE original_digital_asset_url IS NOT NULL;")
    for i, sign_def in enumerate(sign_defs):
        print(f"  Processing sign_definition {i+1}/{len(sign_defs)}: {sign_def['sign_code']}")
        embedding = generate_image_embedding(sign_def['original_digital_asset_url'])
        if embedding:
            image_vectors_batch.append({
                "id": sign_def['sign_code'],
                "values": embedding,
                "metadata": {"type": "sign_definition", "sign_code": sign_def['sign_code']}
            })
        if len(image_vectors_batch) >= batch_size:
            insert_embeddings_to_vectorize(IMAGE_VECTORIZE_INDEX_ID, image_vectors_batch)
            image_vectors_batch = []
    if image_vectors_batch:
        insert_embeddings_to_vectorize(IMAGE_VECTORIZE_INDEX_ID, image_vectors_batch)
    print("Finished image embeddings for sign_definitions.")

    # 2. Generate Image Embeddings for real_sign_photos (real-world/synthetic images)
    print("\nGenerating image embeddings for real_sign_photos...")
    real_photos = query_d1("SELECT photo_id, sign_code, image_url FROM real_sign_photos WHERE image_url IS NOT NULL;")
    image_vectors_batch = [] # Reset batch
    for i, photo in enumerate(real_photos):
        print(f"  Processing real_sign_photo {i+1}/{len(real_photos)}: {photo['photo_id']}")
        embedding = generate_image_embedding(photo['image_url'])
        if embedding:
            image_vectors_batch.append({
                "id": photo['photo_id'],
                "values": embedding,
                "metadata": {"type": "real_photo", "sign_code": photo['sign_code']}
            })
        if len(image_vectors_batch) >= batch_size:
            insert_embeddings_to_vectorize(IMAGE_VECTORIZE_INDEX_ID, image_vectors_batch)
            image_vectors_batch = []
    if image_vectors_batch:
        insert_embeddings_to_vectorize(IMAGE_VECTORIZE_INDEX_ID, image_vectors_batch)
    print("Finished image embeddings for real_sign_photos.")

    # 3. Generate Text Embeddings for sign_definitions explanations
    print("\nGenerating text embeddings for sign_definitions explanations...")
    sign_defs_for_text = query_d1("SELECT sign_code, explanation_fr, explanation_en FROM sign_definitions;")
    for i, sign_def in enumerate(sign_defs_for_text):
        print(f"  Processing text for sign_definition {i+1}/{len(sign_defs_for_text)}: {sign_def['sign_code']}")
        combined_text = f"{sign_def['explanation_fr']} {sign_def['explanation_en']}"
        embedding = generate_text_embedding(combined_text)
        if embedding:
            text_vectors_batch.append({
                "id": sign_def['sign_code'], # Use sign_code as ID for text embeddings
                "values": embedding,
                "metadata": {"type": "sign_explanation", "lang_fr": sign_def['explanation_fr'], "lang_en": sign_def['explanation_en']}
            })
        if len(text_vectors_batch) >= batch_size:
            insert_embeddings_to_vectorize(TEXT_VECTORIZE_INDEX_ID, text_vectors_batch)
            text_vectors_batch = []
    if text_vectors_batch:
        insert_embeddings_to_vectorize(TEXT_VECTORIZE_INDEX_ID, text_vectors_batch)
    print("Finished text embeddings for sign_definitions explanations.")

    print("\n--- Embedding Generation Complete ---")

if __name__ == '__main__':
    generate_all_embeddings()
