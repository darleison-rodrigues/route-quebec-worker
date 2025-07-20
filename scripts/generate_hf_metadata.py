import csv
import json
from pathlib import Path

# --- Configuration ---
script_dir = Path(__file__).parent
dataset_dir = script_dir / 'dataset'
csv_file_path = dataset_dir / 'dataset.csv'
jsonl_file_path = dataset_dir / 'metadata.jsonl'

def generate_metadata_jsonl():
    print(f"--- Generating Hugging Face metadata.jsonl ---")
    print(f"Reading from: {csv_file_path}")
    print(f"Writing to: {jsonl_file_path}")

    if not csv_file_path.exists():
        print(f"ERROR: CSV file not found at {csv_file_path}. Please ensure it exists.")
        return

    metadata_entries = []

    with open(csv_file_path, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Map CSV row to schema.sql / metadata.jsonl structure
            image_id = row['reference_id'] # Using reference_id as image_id for simplicity
            file_name = row['image'] # This is already the relative path like images/P-010-fr.png
            
            # Basic explanation placeholders
            explanation_fr = f"Panneau de signalisation du Québec: {row['reference_id']}."
            explanation_en = f"Quebec road sign: {row['reference_id']}."

            # If the 'explanation' column in CSV has content, use it for both FR/EN for now
            if row.get('explanation') and row['explanation'].strip():
                explanation_fr = row['explanation'].strip()
                explanation_en = row['explanation'].strip()

            entry = {
                "image_id": image_id,
                "file_name": file_name, # Relative path for Hugging Face
                "source": "digital_asset", # Default for initial scrape
                "reference_id": row['reference_id'],
                "explanation_fr": explanation_fr,
                "explanation_en": explanation_en,
                "municipality": None, # New field, defaulting to None
                "real_world_conditions": [], # No real-world conditions for digital assets
                "is_synthetic": False,
                "original_url": row.get('url', '') # Include the original URL
            }
            metadata_entries.append(entry)

    if metadata_entries:
        with open(jsonl_file_path, 'w', encoding='utf-8') as jsonlfile:
            for entry in metadata_entries:
                jsonlfile.write(json.dumps(entry, ensure_ascii=False) + '\n')
        print(f"Successfully generated {len(metadata_entries)} entries in {jsonl_file_path}")
    else:
        print("No entries found in CSV to generate metadata.jsonl.")

    print("--- Finished Generating metadata.jsonl ---")

if __name__ == '__main__':
    generate_metadata_jsonl()