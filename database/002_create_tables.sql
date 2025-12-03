-- ============================================================
-- AirScout Database Migration: 002_create_tables.sql
-- Core tables for hazard tracking and user subscriptions
-- ============================================================
-- Run this AFTER 001_enable_postgis.sql

-- ============================================================
-- TABLE: hazards_active
-- Stores active pollution hazards (permits, traffic, school zones)
-- ============================================================
CREATE TABLE IF NOT EXISTS hazards_active (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Hazard classification
    type VARCHAR(20) NOT NULL CHECK (type IN ('PERMIT', 'TRAFFIC', 'SCHOOL')),
    
    -- Severity scale: 1-5 (1=low, 5=critical)
    -- PERMIT: Based on complaint volume and recency
    -- TRAFFIC: Based on congestion level
    -- SCHOOL: Hard-coded to 5 during peak hours
    severity INTEGER NOT NULL CHECK (severity >= 1 AND severity <= 5),
    
    -- Geographic location (SRID 4326 = WGS84, standard GPS coordinates)
    location GEOMETRY(POINT, 4326) NOT NULL,
    
    -- Human-readable description for display
    description TEXT,
    
    -- Source reference (e.g., permit number, 311 complaint ID)
    -- UNIQUE constraint enables upsert via ON CONFLICT
    source_id VARCHAR(100) UNIQUE,
    
    -- Temporal bounds
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    
    -- Metadata from source systems
    metadata JSONB DEFAULT '{}'::jsonb
);

-- ============================================================
-- TABLE: user_subscriptions
-- User routes to monitor for hazard alerts
-- ============================================================
CREATE TABLE IF NOT EXISTS user_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Unique user identifier (from auth or device)
    user_id VARCHAR(255) NOT NULL,
    
    -- Route geometry as LINESTRING for buffer calculations
    -- SRID 4326 = WGS84 (standard lat/lng)
    route_geometry GEOMETRY(LINESTRING, 4326) NOT NULL,
    
    -- Push notification token (FCM, APNs, or web push)
    push_token TEXT,
    
    -- User preferences
    alert_enabled BOOLEAN DEFAULT TRUE,
    severity_threshold INTEGER DEFAULT 3 CHECK (severity_threshold >= 1 AND severity_threshold <= 5),
    
    -- Route metadata
    route_name VARCHAR(255),
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Ensure one route per user_id+route_name combination
    UNIQUE(user_id, route_name)
);

-- ============================================================
-- TABLE: schools_static
-- Reference table for Chicago Public Schools locations
-- Used for School Zone hard-coded logic (7-9 AM, 2-4 PM)
-- ============================================================
CREATE TABLE IF NOT EXISTS schools_static (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- School identification
    school_id VARCHAR(50) UNIQUE NOT NULL,
    school_name VARCHAR(255) NOT NULL,
    
    -- Geographic location
    location GEOMETRY(POINT, 4326) NOT NULL,
    address TEXT,
    
    -- School zone radius in meters (default 150m for diesel idling concern)
    zone_radius_meters INTEGER DEFAULT 150,
    
    -- School type (for potential filtering)
    school_type VARCHAR(50), -- e.g., 'Elementary', 'High School', 'Charter'
    
    -- Active status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- TABLE: complaints_311
-- Cache of recent 311 complaints for Zombie Permit validation
-- ============================================================
CREATE TABLE IF NOT EXISTS complaints_311 (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- 311 system reference
    service_request_id VARCHAR(50) UNIQUE NOT NULL,
    
    -- Complaint classification
    -- SVR = Severe Weather/Road condition
    -- NOI = Noise complaint
    complaint_type VARCHAR(20) NOT NULL,
    
    -- Geographic location
    location GEOMETRY(POINT, 4326) NOT NULL,
    
    -- Complaint details
    description TEXT,
    status VARCHAR(50),
    
    -- Timestamps
    created_date TIMESTAMPTZ NOT NULL,
    closed_date TIMESTAMPTZ,
    
    -- Track when we fetched this record
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- TABLE: permits_demolition
-- Cache of active demolition permits
-- ============================================================
CREATE TABLE IF NOT EXISTS permits_demolition (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Permit system reference
    permit_number VARCHAR(50) UNIQUE NOT NULL,
    
    -- Geographic location
    location GEOMETRY(POINT, 4326) NOT NULL,
    
    -- Permit details
    permit_type VARCHAR(100),
    work_description TEXT,
    address TEXT,
    
    -- Permit dates
    issue_date DATE,
    expiration_date DATE,
    
    -- Validation status (has matching 311 complaint within 200m?)
    is_validated BOOLEAN DEFAULT FALSE,
    validated_at TIMESTAMPTZ,
    validating_complaint_id VARCHAR(50),
    
    -- Track when we fetched this record
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INDEXES for performance
-- ============================================================

-- Spatial indexes (GIST) for geometry columns
CREATE INDEX IF NOT EXISTS idx_hazards_location ON hazards_active USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_subscriptions_route ON user_subscriptions USING GIST (route_geometry);
CREATE INDEX IF NOT EXISTS idx_schools_location ON schools_static USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_complaints_location ON complaints_311 USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_permits_location ON permits_demolition USING GIST (location);

-- Temporal indexes for expiration queries
CREATE INDEX IF NOT EXISTS idx_hazards_expires ON hazards_active (expires_at);
CREATE INDEX IF NOT EXISTS idx_complaints_created ON complaints_311 (created_date);

-- Type-based filtering
CREATE INDEX IF NOT EXISTS idx_hazards_type ON hazards_active (type);
CREATE INDEX IF NOT EXISTS idx_complaints_type ON complaints_311 (complaint_type);

-- User lookup
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON user_subscriptions (user_id);

-- ============================================================
-- FUNCTIONS for common operations
-- ============================================================

-- Function to clean up expired hazards
CREATE OR REPLACE FUNCTION cleanup_expired_hazards()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM hazards_active 
    WHERE expires_at < NOW();
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function to check if a point is within buffer of a route
-- Uses ST_DWithin for efficient spatial query (200m default buffer)
CREATE OR REPLACE FUNCTION hazards_near_route(
    route GEOMETRY,
    buffer_meters FLOAT DEFAULT 25.0
)
RETURNS SETOF hazards_active AS $$
BEGIN
    RETURN QUERY
    SELECT h.*
    FROM hazards_active h
    WHERE ST_DWithin(
        h.location::geography,
        route::geography,
        buffer_meters
    )
    AND h.expires_at > NOW();
END;
$$ LANGUAGE plpgsql;

-- Function to validate permits against 311 complaints (Zombie Permit logic)
-- Returns permits that have a complaint within 200m in last 48 hours
CREATE OR REPLACE FUNCTION get_validated_permits(
    radius_meters FLOAT DEFAULT 200.0,
    hours_lookback INTEGER DEFAULT 48
)
RETURNS TABLE (
    permit_number VARCHAR(50),
    permit_location GEOMETRY,
    complaint_id VARCHAR(50),
    complaint_type VARCHAR(20),
    distance_meters FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        p.permit_number,
        p.location AS permit_location,
        c.service_request_id AS complaint_id,
        c.complaint_type,
        ST_Distance(p.location::geography, c.location::geography) AS distance_meters
    FROM permits_demolition p
    INNER JOIN complaints_311 c
        ON ST_DWithin(
            p.location::geography,
            c.location::geography,
            radius_meters
        )
    WHERE c.created_date >= NOW() - (hours_lookback || ' hours')::INTERVAL
      AND c.complaint_type IN ('SVR', 'NOI');
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- ROW LEVEL SECURITY (RLS) - Enable for Supabase
-- ============================================================

-- Enable RLS on user-facing tables
ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see/modify their own subscriptions
CREATE POLICY "Users can manage own subscriptions" ON user_subscriptions
    FOR ALL
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

-- Hazards are read-only for all authenticated users
ALTER TABLE hazards_active ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Hazards are publicly readable" ON hazards_active
    FOR SELECT
    USING (true);

-- Schools are read-only reference data
ALTER TABLE schools_static ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Schools are publicly readable" ON schools_static
    FOR SELECT
    USING (true);

-- ============================================================
-- COMMENTS for documentation
-- ============================================================

COMMENT ON TABLE hazards_active IS 'Active pollution hazards from validated permits, traffic, and school zones';
COMMENT ON TABLE user_subscriptions IS 'User routes monitored for hazard alerts';
COMMENT ON TABLE schools_static IS 'Chicago Public Schools locations for school zone logic';
COMMENT ON TABLE complaints_311 IS 'Cache of 311 complaints for permit validation (Zombie Permit fix)';
COMMENT ON TABLE permits_demolition IS 'Cache of demolition permits awaiting validation';

COMMENT ON FUNCTION get_validated_permits IS 'Zombie Permit logic: Returns permits validated by nearby 311 complaints';
COMMENT ON FUNCTION hazards_near_route IS 'Geospatial Buffer logic: Returns hazards within buffer of user route';


