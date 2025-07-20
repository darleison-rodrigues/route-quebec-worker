# Building an Intelligent Traffic Sign Recognition System for Quebec: A Cloudflare-Powered Approach

## Executive Summary

This report details the architectural design and implementation of a robust, scalable, and cost-effective traffic sign recognition system tailored for the unique bilingual and complex regulations of Quebec, Canada. Leveraging Cloudflare's serverless and AI-driven ecosystem (Workers, D1, Images, Vectorize, Workers AI), the system aims to provide mobile users with highly contextual and meaningful answers regarding parking restrictions and construction zone alerts, moving beyond simple 'yes/no' responses to nuanced, location-aware advice.

The core innovation lies in a Retrieval-Augmented Generation (RAG) architecture that combines visual similarity search with a rich, multi-source knowledge base. This approach ensures high accuracy even with imperfect real-world image inputs, while maintaining cost-efficiency and rapid iteration capabilities.

## 1. Project Goal & Problem Statement

The primary objective is to develop a mobile application that helps users understand complex Quebec traffic signs, particularly those related to parking and construction. Traditional image recognition often falls short due to real-world conditions (blur, occlusion, weather) and the need for deep contextual understanding (local bylaws, temporary rules).

Our solution addresses this by:
*   Accurately identifying signs from real-world photos.
*   Providing detailed, bilingual explanations.
*   Integrating real-time municipal data (parking, construction, taxi stands) to offer actionable advice.
*   Ensuring scalability and cost-effectiveness for a mobile-first application.

## 2. Architectural Design: The Cloudflare Ecosystem

The system is built entirely on Cloudflare's developer platform, chosen for its edge-native performance, serverless scalability, and integrated AI capabilities.

### Key Cloudflare Components:

*   **Cloudflare Workers:** Serverless compute environment for the application's backend logic (API endpoint, RAG orchestration, VLM inference).
*   **Cloudflare D1:** A serverless SQL database (SQLite-compatible) used as the structured knowledge base for all sign metadata, pole locations, and municipal regulations.
*   **Cloudflare Images:** A dedicated service for storing, optimizing, and serving all image assets (canonical sign images, real-world photos, synthetic data). It provides global CDN delivery and automatic resizing.
*   **Cloudflare Vectorize:** A serverless vector database for storing and querying high-dimensional embeddings, enabling efficient similarity search for both images and text.
*   **Cloudflare Workers AI:** Provides access to pre-trained machine learning models (Vision-Language Models, Embedding Models) directly from Workers, without managing GPUs.

## 3. Data Model: A Multi-Table D1 Schema

To provide meaningful, contextual answers, a normalized, multi-table schema was designed for Cloudflare D1. This structure allows for clear separation of concerns and efficient querying of related data.

### D1 Tables & Their Roles:

1.  **`sign_definitions`**: The canonical catalog of all sign *types*. Stores pristine digital assets (e.g., Wikimedia SVGs), official codes (`sign_code`), and detailed bilingual explanations (`explanation_fr`, `explanation_en`). This table serves as the ground truth for sign meaning.
2.  **`poles`**: Stores the physical location and metadata of sign poles from Montreal's open data (`POTEAU_ID_POT`, `latitude`, `longitude`, `municipality`).
3.  **`montreal_open_data_sign_instances`**: Records specific instances of signs found in Montreal's open data inventory. These link to `sign_definitions` (via `sign_code`) and `poles` (via `pole_id`). Crucially, this table does *not* store images directly, but rather metadata about the physical sign's presence.
4.  **`real_sign_photos`**: Stores metadata and Cloudflare Images URLs for all *visual examples* of signs. This includes photos taken in the real world (e.g., Samsung S23), synthetically generated images, and Google Street View screenshots. Each entry links to a `sign_definition` and can include location and real-world conditions (`real_world_conditions` as JSON array).
5.  **`construction_zones`**: Stores high-level information about construction projects (permit details, status, dates, general location).
6.  **`construction_impact_details`**: Provides granular details about the impact of construction on specific streets (parking places affected, sidewalk/bike path blockages).
7.  **`taxi_stands`**: Stores locations and operational details of taxi waiting stands, which represent a specific type of parking restriction.

### Relationships (Conceptual):

*   `sign_definitions` defines `montreal_open_data_sign_instances` and `real_sign_photos`.
*   `poles` hosts `montreal_open_data_sign_instances`.
*   `construction_zones` has `construction_impact_details`.
*   `real_sign_photos` can depict a `montreal_open_data_sign_instance`.

## 4. Data Ingestion Pipeline (Offline Processing)

Given the large size and diverse sources of data, an offline, phased ingestion pipeline was designed using Python scripts and Cloudflare's D1 Bulk Import REST API for efficiency and cost-effectiveness.

### Ingestion Scripts & Libraries Used:

*   **`ingest_digital_assets.py`**: Ingests canonical sign definitions.
    *   **Input:** `dataset/dataset.csv` (Wikimedia data), local image files (`dataset/images/`).
    *   **Process:** Reads CSV, uploads images to Cloudflare Images (using `requests` library), generates SQL `INSERT OR REPLACE` statements for `sign_definitions`, and pushes them to D1 via the D1 Bulk Import API.
*   **`ingest_montreal_opendata.py`**: Ingests structured data from various Montreal Open Data CSVs.
    *   **Input:** `poteaux-de-signalisation.csv`, `signalisation_stationnement.csv`, `signalisation_excluant_stationnement.csv`, `entraves-travaux-en-cours.csv`, `impacts-entraves-travaux-en-cours.csv`, `postestaxi.csv`.
    *   **Process:** Reads CSVs (using Python's `csv` module), maps columns to D1 schema, generates SQL `INSERT OR REPLACE` statements for `poles`, `montreal_open_data_sign_instances`, `construction_zones`, `construction_impact_details`, and `taxi_stands`. Pushes to D1 via the D1 Bulk Import API.
    *   **Libraries:** `os`, `csv`, `json`, `requests`, `hashlib`, `time`, `pathlib`, `uuid`.
*   **`ingest_real_sign_photos.py`**: Ingests user-contributed or synthetically generated real-world sign photos.
    *   **Input:** Local image files, user-provided metadata (via prompts).
    *   **Process:** Prompts user for metadata, uploads images to Cloudflare Images (using `requests`), generates SQL `INSERT OR REPLACE` statements for `real_sign_photos`, and pushes to D1 via the D1 Bulk Import API.
    *   **Libraries:** `os`, `json`, `requests`, `hashlib`, `time`, `pathlib`, `uuid`.

### D1 Bulk Import Mechanism:

All ingestion scripts leverage the D1 Bulk Import REST API. This involves:
1.  Generating a large SQL `INSERT` statement (or multiple batched statements).
2.  Hashing the SQL content.
3.  Initiating an upload with D1 API to get a signed R2 URL.
4.  Uploading the SQL content directly to the signed R2 URL.
5.  Notifying D1 to start ingestion.
6.  Polling D1 for ingestion status.

This method is highly efficient for large datasets, minimizing API calls and ensuring reliable data transfer.

## 5. Offline Embedding Generation

To power the RAG system, high-dimensional vector embeddings are generated offline and stored in Cloudflare Vectorize.

### Embedding Script & Libraries Used:

*   **`generate_embeddings.py`**: Orchestrates the generation and insertion of embeddings.
    *   **Process:**
        *   **Image Embeddings:** Queries D1 for `original_digital_asset_url` from `sign_definitions` and `image_url` from `real_sign_photos`. Downloads images (using `requests`), sends image data to Cloudflare Workers AI (`@cf/openai/clip-vit-base-patch32` model) to generate 512-dimension embeddings. Inserts embeddings into `quebec-sign-images-vector-index` in Vectorize.
        *   **Text Embeddings:** Queries D1 for `explanation_fr` and `explanation_en` from `sign_definitions`. Concatenates text, sends to Cloudflare Workers AI (`@cf/baai/bge-large-en-v1.5` model) to generate 768-dimension embeddings. Inserts embeddings into `quebec-sign-text-vector-index` in Vectorize.
    *   **Libraries:** `os`, `json`, `requests`, `hashlib`, `time`, `pathlib`.

## 6. Application Runtime Flow (Cloudflare Worker)

The Cloudflare Worker acts as the intelligent backend, orchestrating the RAG process to provide contextual answers.

### Key Steps:

1.  **User Input:** Mobile app sends an image (for visual query) OR text (for semantic query), along with the user's current location (lat/lon, municipality).
2.  **Authentication & Validation:** Worker authenticates the request (API Key) and validates input.
3.  **Embedding Generation (User Input):** Generates an embedding for the user's image (if visual) or text query (if textual) using Cloudflare Workers AI.
4.  **Vector Search (RAG - Similarity):**
    *   **Visual Query:** Searches `quebec-sign-images-vector-index` in Vectorize for visually similar `sign_definitions` (canonical) and `real_sign_photos` (real-world examples).
    *   **Textual Query:** Searches `quebec-sign-text-vector-index` in Vectorize for semantically similar `sign_definitions` explanations.
5.  **D1 Lookup (Contextual Retrieval):** Uses the results from Vectorize to query D1 across `sign_definitions`, `montreal_open_data_sign_instances`, `poles`, `construction_zones`, `construction_impact_details`, and `taxi_stands` tables. This retrieves all relevant structured data based on sign type and location.
6.  **Prompt Construction:** Dynamically builds a rich, contextual prompt for the Vision-Language Model (VLM) using all retrieved data. This includes the user's image (if applicable), explanations, real-world conditions, and location-specific rules (e.g., active construction, taxi stand hours).
7.  **VLM Inference:** Sends the comprehensive prompt and user's image to Cloudflare Workers AI (`@cf/meta/llama-3.2-11b-vision-instruct`).
8.  **Structured Response:** The VLM processes the request and returns a structured JSON response with detailed explanations, parking implications, construction alerts, and confidence scores.
9.  **Return to Mobile App:** The Worker sends the actionable JSON response back to the mobile application.

## 7. Conclusion & Future Outlook

This architecture provides a robust and cost-effective foundation for an intelligent traffic sign recognition system. By combining a multi-source knowledge base in D1 with efficient vector search and powerful AI models on Cloudflare's edge, the system can deliver highly contextual and meaningful answers to users.

Future enhancements could include:
*   **Synthetic Data Generation:** Leveraging multimodal diffusion models to further expand the `real_sign_photos` dataset with diverse, challenging conditions.
*   **Real-time Data Integration:** More sophisticated integration with live municipal data feeds for even more up-to-date information.
*   **Advanced VLM Fine-tuning:** Fine-tuning a specialized VLM on the curated dataset for even higher accuracy on Quebec-specific signs.

This project demonstrates a practical application of modern AI and serverless technologies to solve a real-world problem, offering a blueprint for building intelligent, data-driven applications at scale.
