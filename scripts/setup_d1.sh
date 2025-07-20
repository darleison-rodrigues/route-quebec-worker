#!/bin/bash

DB_NAME="quebec-road-signs"
SCHEMA_FILE="./d1_schema.sql"

echo "--- Setting up Cloudflare D1 Database: $DB_NAME ---"

# 1. Create the D1 database
echo "Creating D1 database '$DB_NAME'..."
wrangler d1 create "$DB_NAME"

if [ $? -ne 0 ]; then
    echo "Error: Failed to create D1 database. Exiting."
    exit 1
fi

echo "Database '$DB_NAME' created successfully."

# 2. Apply the schema
echo "Applying schema from $SCHEMA_FILE to '$DB_NAME'..."
wrangler d1 execute "$DB_NAME" --local --file="$SCHEMA_FILE"

if [ $? -ne 0 ]; then
    echo "Error: Failed to apply schema. Exiting."
    exit 1
fi

echo "Schema applied successfully to '$DB_NAME'."
echo "--- D1 Setup Complete ---"
