import os
import csv
import json
import requests
import hashlib
import time
from pathlib import Path
import uuid

# --- Configuration ---
SCRIPT_DIR = Path(__file__).parent
MONTREAL_OPENDATA_DIR = SCRIPT_DIR / 'dataset' / 'montreal_opendata'

DB_NAME = "quebec-road-signs"

# Cloudflare API Credentials (set as environment variables)
ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not ACCOUNT_ID or not API_TOKEN:
    print("ERROR: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN environment variables must be set.")
    exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
    'User-Agent': 'Montreal-OpenData-Ingester/1.0 (contact@example.com)'
}

# --- Helper Functions for Cloudflare API Interactions ---
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
        # print(f"  Ingestion initiated. Status: {ingest_data}") # Too verbose

        # 4. Polling
        bookmark = ingest_data["result"]["at_bookmark"]
        payload = {"action": "poll", "current_bookmark": bookmark}
        while True:
            poll_response = requests.post(d1_url, headers=HEADERS, json=payload, timeout=30)
            poll_response.raise_for_status()
            poll_result = poll_response.json()["result"]
            # print(f"  Polling D1 import status: {poll_result}") # Too verbose

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
    # Escape single quotes in values by doubling them
    values = ', '.join([f"'{str(v).replace("'", "''")}'" if v is not None else "NULL" for v in data.values()])
    return f"INSERT INTO {table_name} ({columns}) VALUES ({values})"


# --- Ingestion Functions for Each Table ---
def ingest_poles_data():
    print("\n--- Ingesting Poles Data ---")
    csv_path = MONTREAL_OPENDATA_DIR / 'poteaux-de-signalisation.csv'
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found. Skipping poles ingestion.")
        return

    sql_statements_batch = []
    batch_size = 1000 # Adjust based on D1 limits and performance

    with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for i, row in enumerate(reader):
            # print(f"Processing pole row {i+1}: {row.get('POTEAU_ID_POT')}")
            try:
                pole_data = {
                    "pole_id": row['POTEAU_ID_POT'],
                    "municipality": row.get('NOM_ARROND', 'Montreal'), # Default to Montreal if not specified
                    "latitude": float(row['LATITUDE']),
                    "longitude": float(row['LONGITUDE']),
                    "x_coord": float(row['MTM8_X']) if row.get('MTM8_X') else None,
                    "y_coord": float(row['MTM8_Y']) if row.get('MTM8_Y') else None,
                    "date_conception": row.get('DATE_CONCEPTION_POT'),
                    "version": int(row['POTEAU_VERSION_POT']) if row.get('POTEAU_VERSION_POT') else None,
                    "is_on_street": bool(int(row['PAS_SUR_RUE'])) if row.get('PAS_SUR_RUE') else False
                }
                sql_statements_batch.append(make_sql_insert("poles", pole_data).replace("INSERT INTO", "INSERT OR REPLACE INTO"))

                if len(sql_statements_batch) >= batch_size:
                    d1_bulk_import("poles", sql_statements_batch)
                    sql_statements_batch = []
            except Exception as e:
                print(f"  Error processing pole row {i+1}: {e} - Data: {row}")

    if sql_statements_batch:
        d1_bulk_import("poles", sql_statements_batch)
    print("--- Finished Ingesting Poles Data ---")

def ingest_montreal_sign_instances_data():
    print("\n--- Ingesting Montreal Sign Instances Data ---")
    csv_paths = [
        MONTREAL_OPENDATA_DIR / 'signalisation_stationnement.csv',
        MONTREAL_OPENDATA_DIR / 'signalisation_excluant_stationnement.csv'
    ]

    sql_statements_batch = []
    batch_size = 1000

    for csv_path in csv_paths:
        if not csv_path.exists():
            print(f"ERROR: {csv_path} not found. Skipping.")
            continue
        
        print(f"Processing {csv_path.name}...")
        with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for i, row in enumerate(reader):
                # print(f"Processing sign instance row {i+1}: {row.get('PANNEAU_ID_PAN')}")
                try:
                    instance_data = {
                        "instance_id": str(uuid.uuid4()), # Generate a unique ID for each instance
                        "sign_code": row['PANNEAU_ID_RPA'],
                        "pole_id": row['POTEAU_ID_POT'],
                        "panel_id": row.get('PANNEAU_ID_PAN'),
                        "panel_position_on_pole": int(row['POSITION_POP']) if row.get('POSITION_POP') else None,
                        "arrow_code": int(row['FLECHE_PAN']) if row.get('FLECHE_PAN') else None,
                        "toponymic_code": row.get('TOPONYME_PAN'),
                        "category_description": row.get('DESCRIPTION_CAT'),
                        "rep_description": row.get('DESCRIPTION_REP'),
                        "rtp_description": row.get('DESCRIPTION_RTP'),
                        "source": "montreal_open_data",
                        "last_updated": row.get('DATE_CONCEPTION_POT', time.strftime('%Y-%m-%d %H:%M:%S')) # Use conception date or current time
                    }
                    sql_statements_batch.append(make_sql_insert("montreal_open_data_sign_instances", instance_data).replace("INSERT INTO", "INSERT OR REPLACE INTO"))

                    if len(sql_statements_batch) >= batch_size:
                        d1_bulk_import("montreal_open_data_sign_instances", sql_statements_batch)
                        sql_statements_batch = []
                except Exception as e:
                    print(f"  Error processing sign instance row {i+1} from {csv_path.name}: {e} - Data: {row}")

    if sql_statements_batch:
        d1_bulk_import("montreal_open_data_sign_instances", sql_statements_batch)
    print("--- Finished Ingesting Montreal Sign Instances Data ---")

def ingest_construction_data():
    print("\n--- Ingesting Construction Data ---")
    zones_csv_path = MONTREAL_OPENDATA_DIR / 'entraves-travaux-en-cours.csv'
    impacts_csv_path = MONTREAL_OPENDATA_DIR / 'impacts-entraves-travaux-en-cours.csv'

    if not zones_csv_path.exists():
        print(f"ERROR: {zones_csv_path} not found. Skipping construction zones ingestion.")
        return
    if not impacts_csv_path.exists():
        print(f"ERROR: {impacts_csv_path} not found. Skipping construction impacts ingestion.")
        # return # Allow zones to be ingested even if impacts are missing

    # Ingest Construction Zones first
    sql_statements_zones = []
    batch_size = 1000
    print(f"Processing {zones_csv_path.name}...")
    with open(zones_csv_path, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for i, row in enumerate(reader):
            try:
                zone_data = {
                    "permit_id": row['id'],
                    "permit_number": row.get('permit_permit_id'),
                    "borough_id": row.get('boroughid'),
                    "current_status": row.get('currentstatus'),
                    "start_date": row.get('duration_start_date'),
                    "end_date": row.get('duration_end_date'),
                    "reason_category": row.get('reason_category'),
                    "occupancy_name": row.get('occupancy_name'),
                    "submitter_category": row.get('submittercategory'),
                    "organization_name": row.get('organizationname'),
                    "active_mon": bool(row.get('duration_days_mon_active') == 'true'),
                    "active_tue": bool(row.get('duration_days_tue_active') == 'true'),
                    "active_wed": bool(row.get('duration_days_wed_active') == 'true'),
                    "active_thu": bool(row.get('duration_days_thu_active') == 'true'),
                    "active_fri": bool(row.get('duration_days_fri_active') == 'true'),
                    "active_sat": bool(row.get('duration_days_sat_active') == 'true'),
                    "active_sun": bool(row.get('duration_days_sun_active') == 'true'),
                    "allday_mon": bool(row.get('duration_days_mon_all_day_round') == 'true'),
                    "allday_tue": bool(row.get('duration_days_tue_all_day_round') == 'true'),
                    "allday_wed": bool(row.get('duration_days_wed_all_day_round') == 'true'),
                    "allday_thu": bool(row.get('duration_days_thu_all_day_round') == 'true'),
                    "allday_fri": bool(row.get('duration_days_fri_all_day_round') == 'true'),
                    "allday_sat": bool(row.get('duration_days_sat_all_day_round') == 'true'),
                    "allday_sun": bool(row.get('duration_days_sun_all_day_round') == 'true'),
                    "latitude": float(row['latitude']) if row.get('latitude') else None,
                    "longitude": float(row['longitude']) if row.get('longitude') else None
                }
                sql_statements_zones.append(make_sql_insert("construction_zones", zone_data).replace("INSERT INTO", "INSERT OR REPLACE INTO"))

                if len(sql_statements_zones) >= batch_size:
                    d1_bulk_import("construction_zones", sql_statements_zones)
                    sql_statements_zones = []
            except Exception as e:
                print(f"  Error processing construction zone row {i+1}: {e} - Data: {row}")
    if sql_statements_zones:
        d1_bulk_import("construction_zones", sql_statements_zones)
    print("--- Finished Ingesting Construction Zones Data ---")

    # Ingest Construction Impact Details
    sql_statements_impacts = []
    if impacts_csv_path.exists():
        print(f"Processing {impacts_csv_path.name}...")
        with open(impacts_csv_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for i, row in enumerate(reader):
                try:
                    impact_data = {
                        "impact_id": str(uuid.uuid4()), # Generate unique ID
                        "permit_id": row['id_request'],
                        "street_id": row.get('streetid'),
                        "street_impact_width": row.get('streetimpactwidth'),
                        "street_impact_type": row.get('streetimpacttype'),
                        "nb_free_parking_places": int(row['nbfreeparkingplace']) if row.get('nbfreeparkingplace') else None,
                        "sidewalk_blocked_type": row.get('sidewalk_blockedtype'),
                        "back_sidewalk_blocked_type": row.get('backsidewalk_blockedtype'),
                        "bike_path_blocked_type": row.get('bikepath_blockedtype'),
                        "street_name": row.get('name'),
                        "from_name": row.get('fromname'),
                        "to_name": row.get('toname'),
                        "length": float(row['length']) if row.get('length') else None,
                        "is_arterial": bool(row.get('isarterial') == 'true'),
                        "stm_impact_blocked_type": row.get('stmimpact_blockedtype'),
                        "other_provider_impact_blocked_type": row.get('otherproviderimpact_blockedtype'),
                        "reserved_lane_blocked_type": row.get('reservedlane_blockedtype')
                    }
                    sql_statements_impacts.append(make_sql_insert("construction_impact_details", impact_data).replace("INSERT INTO", "INSERT OR REPLACE INTO"))

                    if len(sql_statements_impacts) >= batch_size:
                        d1_bulk_import("construction_impact_details", sql_statements_impacts)
                        sql_statements_impacts = []
                except Exception as e:
                    print(f"  Error processing construction impact row {i+1}: {e} - Data: {row}")
        if sql_statements_impacts:
            d1_bulk_import("construction_impact_details", sql_statements_impacts)
    print("--- Finished Ingesting Construction Impact Details Data ---")

def ingest_taxi_stands_data():
    print("\n--- Ingesting Taxi Stands Data ---")
    csv_path = MONTREAL_OPENDATA_DIR / 'postestaxi.csv'
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found. Skipping taxi stands ingestion.")
        return

    sql_statements_batch = []
    batch_size = 1000

    with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for i, row in enumerate(reader):
            try:
                taxi_data = {
                    "taxi_stand_id": str(uuid.uuid4()), # Generate unique ID
                    "status": row.get('Etat_poste'),
                    "operation_hours": row.get('Heure_operation'),
                    "latitude": float(row['Lat']),
                    "longitude": float(row['Long']),
                    "num_places": int(row['Nb_place']) if row.get('Nb_place') else None,
                    "name": row.get('Nom'),
                    "type": row.get('Type'),
                    "location_details": row.get('Localisation'),
                    "x_coord": float(row['MTM8_X']) if row.get('MTM8_X') else None,
                    "y_coord": float(row['MTM8_Y']) if row.get('MTM8_Y') else None,
                    "municipality": row.get('NOM_ARROND', 'Montreal') # Default to Montreal
                }
                sql_statements_batch.append(make_sql_insert("taxi_stands", taxi_data).replace("INSERT INTO", "INSERT OR REPLACE INTO"))

                if len(sql_statements_batch) >= batch_size:
                    d1_bulk_import("taxi_stands", sql_statements_batch)
                    sql_statements_batch = []
            except Exception as e:
                print(f"  Error processing taxi stand row {i+1}: {e} - Data: {row}")

    if sql_statements_batch:
        d1_bulk_import("taxi_stands", sql_statements_batch)
    print("--- Finished Ingesting Taxi Stands Data ---")


# --- Main Execution Flow ---
if __name__ == '__main__':
    print("\n--- Starting Montreal Open Data Ingestion ---")
    
    # Ensure the montreal_opendata directory exists
    os.makedirs(MONTREAL_OPENDATA_DIR, exist_ok=True)
    print(f"Please ensure Montreal Open Data CSVs are in: {MONTREAL_OPENDATA_DIR}")

    # Ingest in order of dependency
    ingest_poles_data()
    ingest_montreal_sign_instances_data()
    ingest_construction_data()
    ingest_taxi_stands_data()

    print("\n--- Montreal Open Data Ingestion Complete ---")
