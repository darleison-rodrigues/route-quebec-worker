-- Table 1: sign_definitions
-- Stores the canonical definition and explanation for each type of traffic sign.
-- This is where your digital assets and their explanations will primarily reside.
CREATE TABLE sign_definitions (
    sign_code TEXT PRIMARY KEY, -- e.g., 'P-120-10', 'P-010-fr', 'P-250-1A'
    explanation_fr TEXT NOT NULL,
    explanation_en TEXT NOT NULL,
    category TEXT, -- e.g., 'PRESCRIPTION', 'DANGER', 'TRAVAUX', 'INDICATION'
    -- Additional fields from RPA/RTP codification if available and relevant for definition
    rpa_description TEXT,
    rpa_code TEXT,
    rtp_description TEXT,
    original_digital_asset_url TEXT -- URL to the pristine digital asset (e.g., Wikimedia)
);

-- Index for efficient lookup by sign_code (already primary key, so indexed by default)
-- CREATE INDEX idx_sign_definitions_category ON sign_definitions (category);


-- Table 2: poles
-- Stores the physical location and basic information about sign poles.
-- Data from 'Poteaux de signalisation' dataset.
CREATE TABLE poles (
    pole_id TEXT PRIMARY KEY, -- POTEAU_ID_POT
    municipality TEXT NOT NULL, -- NOM_ARROND
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    x_coord REAL,              -- Coordonnée X (NAD83 MTM8)
    y_coord REAL,              -- Coordonnée Y (NAD83 MTM8)
    date_conception TEXT,      -- DATE_CONCEPTION_POT (store as TEXT for flexibility, or convert to DATE)
    version INTEGER,           -- POTEAU_VERSION_POT
    is_on_street BOOLEAN       -- PAS_SUR_RUE
);

-- Indexes for efficient spatial and municipal queries
CREATE INDEX idx_poles_location ON poles (latitude, longitude);
CREATE INDEX idx_poles_municipality ON poles (municipality);


-- Table 3: montreal_open_data_sign_instances
-- Stores information about specific physical instances of signs from Montreal Open Data.
-- This table does NOT contain images directly.
CREATE TABLE montreal_open_data_sign_instances (
    instance_id TEXT PRIMARY KEY, -- Unique ID for each physical sign instance (e.g., UUID)
    sign_code TEXT NOT NULL,      -- Foreign Key to sign_definitions.sign_code (PANNEAU_ID_RPA)
    pole_id TEXT,                 -- Foreign Key to poles.pole_id (POTEAU_ID_POT), can be NULL if not on a known pole
    panel_id TEXT,                -- PANNEAU_ID_PAN
    panel_position_on_pole INTEGER, -- POSITION_POP
    arrow_code INTEGER,           -- FLECHE_PAN
    toponymic_code TEXT,          -- TOPONYME_PAN
    category_description TEXT,    -- DESCRIPTION_CAT (can be redundant if linked to sign_definitions, but useful for direct query)
    rep_description TEXT,         -- DESCRIPTION_REP
    rtp_description TEXT,         -- DESCRIPTION_RTP
    source TEXT NOT NULL,         -- 'montreal_open_data'
    last_updated TEXT -- Store as TEXT for flexibility, or convert to DATETIME
);

-- Indexes for efficient lookups
CREATE INDEX idx_montreal_sign_instances_sign_code ON montreal_open_data_sign_instances (sign_code);
CREATE INDEX idx_montreal_sign_instances_pole_id ON montreal_open_data_sign_instances (pole_id);


-- Table 4: real_sign_photos
-- Stores metadata and Cloudflare Image URLs for real-world and synthetic photos of signs.
-- Images from this table will be vectorized for visual search.
CREATE TABLE real_sign_photos (
    photo_id TEXT PRIMARY KEY, -- Unique ID for each photo (e.g., UUID)
    sign_code TEXT NOT NULL,   -- Foreign Key to sign_definitions.sign_code
    image_url TEXT NOT NULL,   -- Cloudflare Images URL for this specific photo
    source TEXT NOT NULL,      -- 'real_world_photo', 'synthetic_diffusion', 'google_street_view_screenshot'
    latitude REAL,
    longitude REAL,
    municipality TEXT,
    real_world_conditions TEXT, -- JSON array: ["snow_occlusion", "blur", "vandalism"]
    is_synthetic BOOLEAN,
    captured_date TEXT,        -- Date/time photo was taken/generated
    related_montreal_instance_id TEXT -- Optional FK to montreal_open_data_sign_instances.instance_id
);

-- Indexes for efficient lookups
CREATE INDEX idx_real_sign_photos_sign_code ON real_sign_photos (sign_code);
CREATE INDEX idx_real_sign_photos_source ON real_sign_photos (source);
CREATE INDEX idx_real_sign_photos_location ON real_sign_photos (latitude, longitude);


-- Table 5: construction_zones
-- Stores information about construction projects and their associated disruptions.
-- Data from 'Entraves et travaux en cours' dataset.
CREATE TABLE construction_zones (
    permit_id TEXT PRIMARY KEY,     -- id
    permit_number TEXT,             -- permit_permitid
    borough_id TEXT,                -- boroughid
    current_status TEXT,            -- currentstatus
    start_date TEXT,                -- duration_startdate (store as TEXT, convert in Worker if needed)
    end_date TEXT,                  -- duration_enddate (store as TEXT, convert in Worker if needed)
    reason_category TEXT,           -- reason_category
    occupancy_name TEXT,            -- occupancyname
    submitter_category TEXT,        -- submittercategory
    organization_name TEXT,         -- organizationname
    -- Day-specific active/alldayround flags (consider consolidating if too many columns)
    active_mon BOOLEAN, active_tue BOOLEAN, active_wed BOOLEAN,
    active_thu BOOLEAN, active_fri BOOLEAN, active_sat BOOLEAN, active_sun BOOLEAN,
    allday_mon BOOLEAN, allday_tue BOOLEAN, allday_wed BOOLEAN,
    allday_thu BOOLEAN, allday_fri BOOLEAN, allday_sat BOOLEAN, allday_sun BOOLEAN,
    -- Add lat/lon or link to poles if construction is tied to specific poles
    latitude REAL,
    longitude REAL
);

-- Indexes for efficient queries on construction zones
CREATE INDEX idx_construction_zones_status ON construction_zones (current_status);
CREATE INDEX idx_construction_zones_location ON construction_zones (latitude, longitude);


-- Table 6: construction_impact_details
-- Stores detailed impact information for construction projects.
-- Data from 'Impacts des entraves et travaux en cours' dataset.
CREATE TABLE construction_impact_details (
    impact_id TEXT PRIMARY KEY,     -- Unique ID for this impact detail
    permit_id TEXT NOT NULL,        -- FK to construction_zones.permit_id
    street_id TEXT,
    street_impact_width TEXT,
    street_impact_type TEXT,
    nb_free_parking_places INTEGER,
    sidewalk_blocked_type TEXT,
    back_sidewalk_blocked_type TEXT,
    bike_path_blocked_type TEXT,
    street_name TEXT,
    from_name TEXT,
    to_name TEXT,
    length REAL,
    is_arterial BOOLEAN,
    stm_impact_blocked_type TEXT,
    other_provider_impact_blocked_type TEXT,
    reserved_lane_blocked_type TEXT
);

-- Indexes for efficient queries
CREATE INDEX idx_construction_impact_details_permit_id ON construction_impact_details (permit_id);


-- Table 7: taxi_stands
-- Stores information about taxi waiting stands, which are a type of parking restriction.
-- Data from 'Postes d'attente de taxi' dataset.
CREATE TABLE taxi_stands (
    taxi_stand_id TEXT PRIMARY KEY, -- id
    status TEXT,                    -- Etat_poste (actif, temporaire, fermé)
    operation_hours TEXT,           -- Heure_operation
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    num_places INTEGER,             -- Nb_place
    name TEXT,                      -- Nom
    type TEXT,                      -- Type (Type de propriété)
    location_details TEXT,          -- localisation (Précisions sur la localisation)
    x_coord REAL,                   -- loc_x (NAD83 MTM8)
    y_coord REAL,                   -- loc_y (NAD83 MTM8)
    municipality TEXT               -- NOM_ARROND (inferred or added during ingestion)
);

-- Indexes for efficient queries on taxi stands
CREATE INDEX idx_taxi_stands_location ON taxi_stands (latitude, longitude);
CREATE INDEX idx_taxi_stands_status ON taxi_stands (status);
CREATE INDEX idx_taxi_stands_municipality ON taxi_stands (municipality);
