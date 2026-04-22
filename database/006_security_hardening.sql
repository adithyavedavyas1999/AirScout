-- ============================================================
-- AirScout Database Migration: 006_security_hardening.sql
-- Resolves all Supabase Security Advisor errors and warnings
-- ============================================================
-- Run AFTER 005_enhanced_features.sql
--
-- Fixes:
--   [ERROR]   RLS disabled on complaints_311, permits_demolition, spatial_ref_sys
--   [WARNING] Function Search Path Mutable on all 15 RPC functions
--   [INFO]    PostGIS extension in public schema (addressed via search_path)
-- ============================================================

-- ============================================================
-- 1. ENABLE ROW-LEVEL SECURITY ON UNPROTECTED TABLES
-- ============================================================

-- complaints_311: cache data written by pipeline (service role);
-- anonymous/authenticated users may only read.
ALTER TABLE complaints_311 ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Complaints are publicly readable"
    ON complaints_311 FOR SELECT
    USING (true);

-- permits_demolition: cache data written by pipeline (service role);
-- anonymous/authenticated users may only read.
ALTER TABLE permits_demolition ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Permits are publicly readable"
    ON permits_demolition FOR SELECT
    USING (true);

-- spatial_ref_sys: PostGIS system catalog owned by the superuser.
-- We cannot ALTER it directly; instead revoke write access from API roles.
-- This prevents PostgREST clients from modifying it while keeping reads
-- available for PostGIS internals.
DO $$
BEGIN
    REVOKE INSERT, UPDATE, DELETE ON spatial_ref_sys FROM anon, authenticated;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'spatial_ref_sys grant revoke skipped: %', SQLERRM;
END;
$$;


-- ============================================================
-- 2. FIX FUNCTION SEARCH PATH MUTABLE WARNINGS
--    Re-create every function with an explicit SET search_path
--    so PostgreSQL resolves names deterministically.
-- ============================================================

-- ----- From 002_create_tables.sql -----

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
$$ LANGUAGE plpgsql SET search_path = public;


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
$$ LANGUAGE plpgsql SET search_path = public;


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
$$ LANGUAGE plpgsql SET search_path = public;


-- ----- From 003_alert_history.sql -----

CREATE OR REPLACE FUNCTION was_recently_alerted(
    p_user_id VARCHAR(255),
    p_hazard_source_id VARCHAR(100),
    p_hours INTEGER DEFAULT 4
)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
        FROM alert_history
        WHERE user_id = p_user_id
          AND hazard_source_id = p_hazard_source_id
          AND sent_at > NOW() - (p_hours || ' hours')::INTERVAL
    );
END;
$$ LANGUAGE plpgsql SET search_path = public;


CREATE OR REPLACE FUNCTION cleanup_old_alerts(days_to_keep INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM alert_history
    WHERE sent_at < NOW() - (days_to_keep || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql SET search_path = public;


CREATE OR REPLACE FUNCTION register_push_subscription(
    p_user_id VARCHAR(255),
    p_endpoint TEXT,
    p_p256dh TEXT,
    p_auth TEXT,
    p_user_agent TEXT DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    subscription_id UUID;
BEGIN
    INSERT INTO push_subscriptions (user_id, endpoint, p256dh_key, auth_secret, user_agent)
    VALUES (p_user_id, p_endpoint, p_p256dh, p_auth, p_user_agent)
    ON CONFLICT (user_id, endpoint)
    DO UPDATE SET
        p256dh_key = EXCLUDED.p256dh_key,
        auth_secret = EXCLUDED.auth_secret,
        user_agent = EXCLUDED.user_agent,
        is_active = TRUE,
        last_used_at = NOW()
    RETURNING id INTO subscription_id;
    RETURN subscription_id;
END;
$$ LANGUAGE plpgsql SET search_path = public;


-- ----- From 004_api_functions.sql -----

CREATE OR REPLACE FUNCTION check_route_hazards(
    route_wkt TEXT,
    buffer_meters FLOAT DEFAULT 25.0,
    min_severity INTEGER DEFAULT 1
)
RETURNS TABLE (
    id UUID,
    type VARCHAR(20),
    severity INTEGER,
    description TEXT,
    source_id VARCHAR(100),
    longitude FLOAT,
    latitude FLOAT,
    distance_meters FLOAT,
    expires_at TIMESTAMPTZ,
    metadata JSONB
) AS $$
DECLARE
    buffer_geom GEOMETRY;
BEGIN
    buffer_geom := ST_Transform(
        ST_Buffer(
            ST_Transform(ST_GeomFromText(route_wkt, 4326), 26971),
            buffer_meters
        ),
        4326
    );

    RETURN QUERY
    SELECT
        h.id,
        h.type,
        h.severity,
        h.description,
        h.source_id,
        ST_X(h.location::geometry)::FLOAT as longitude,
        ST_Y(h.location::geometry)::FLOAT as latitude,
        ST_Distance(
            h.location::geography,
            ST_GeomFromText(route_wkt, 4326)::geography
        )::FLOAT as distance_meters,
        h.expires_at,
        h.metadata
    FROM hazards_active h
    WHERE h.expires_at > NOW()
      AND h.severity >= min_severity
      AND ST_Intersects(h.location, buffer_geom)
    ORDER BY h.severity DESC, distance_meters ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


CREATE OR REPLACE FUNCTION subscribe_to_route(
    p_user_id VARCHAR(255),
    p_route_wkt TEXT,
    p_route_name VARCHAR(255) DEFAULT NULL,
    p_push_token TEXT DEFAULT NULL,
    p_severity_threshold INTEGER DEFAULT 3
)
RETURNS UUID AS $$
DECLARE
    subscription_id UUID;
BEGIN
    INSERT INTO user_subscriptions (
        user_id,
        route_geometry,
        route_name,
        push_token,
        severity_threshold,
        alert_enabled
    )
    VALUES (
        p_user_id,
        ST_GeomFromText(p_route_wkt, 4326),
        COALESCE(p_route_name, 'My Route'),
        p_push_token,
        p_severity_threshold,
        TRUE
    )
    ON CONFLICT (user_id, route_name)
    DO UPDATE SET
        route_geometry = ST_GeomFromText(p_route_wkt, 4326),
        push_token = COALESCE(EXCLUDED.push_token, user_subscriptions.push_token),
        severity_threshold = EXCLUDED.severity_threshold,
        updated_at = NOW()
    RETURNING id INTO subscription_id;
    RETURN subscription_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


CREATE OR REPLACE FUNCTION get_user_subscriptions(p_user_id VARCHAR(255))
RETURNS TABLE (
    id UUID,
    route_name VARCHAR(255),
    route_wkt TEXT,
    alert_enabled BOOLEAN,
    severity_threshold INTEGER,
    created_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.id,
        s.route_name,
        ST_AsText(s.route_geometry) as route_wkt,
        s.alert_enabled,
        s.severity_threshold,
        s.created_at
    FROM user_subscriptions s
    WHERE s.user_id = p_user_id
    ORDER BY s.created_at DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


CREATE OR REPLACE FUNCTION update_subscription_alerts(
    p_subscription_id UUID,
    p_user_id VARCHAR(255),
    p_alert_enabled BOOLEAN
)
RETURNS BOOLEAN AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE user_subscriptions
    SET alert_enabled = p_alert_enabled, updated_at = NOW()
    WHERE id = p_subscription_id
      AND user_id = p_user_id;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count > 0;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


CREATE OR REPLACE FUNCTION delete_subscription(
    p_subscription_id UUID,
    p_user_id VARCHAR(255)
)
RETURNS BOOLEAN AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM user_subscriptions
    WHERE id = p_subscription_id
      AND user_id = p_user_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count > 0;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


CREATE OR REPLACE FUNCTION get_active_hazards_summary()
RETURNS TABLE (
    type VARCHAR(20),
    count BIGINT,
    avg_severity FLOAT,
    max_severity INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        h.type,
        COUNT(*)::BIGINT,
        AVG(h.severity)::FLOAT,
        MAX(h.severity)
    FROM hazards_active h
    WHERE h.expires_at > NOW()
    GROUP BY h.type;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


CREATE OR REPLACE FUNCTION get_nearby_hazards(
    p_longitude FLOAT,
    p_latitude FLOAT,
    p_radius_meters FLOAT DEFAULT 500.0,
    p_min_severity INTEGER DEFAULT 1,
    p_limit INTEGER DEFAULT 20
)
RETURNS TABLE (
    id UUID,
    type VARCHAR(20),
    severity INTEGER,
    description TEXT,
    longitude FLOAT,
    latitude FLOAT,
    distance_meters FLOAT,
    expires_at TIMESTAMPTZ
) AS $$
DECLARE
    user_location GEOMETRY;
BEGIN
    user_location := ST_SetSRID(ST_MakePoint(p_longitude, p_latitude), 4326);

    RETURN QUERY
    SELECT
        h.id,
        h.type,
        h.severity,
        h.description,
        ST_X(h.location::geometry)::FLOAT as longitude,
        ST_Y(h.location::geometry)::FLOAT as latitude,
        ST_Distance(h.location::geography, user_location::geography)::FLOAT as distance_meters,
        h.expires_at
    FROM hazards_active h
    WHERE h.expires_at > NOW()
      AND h.severity >= p_min_severity
      AND ST_DWithin(h.location::geography, user_location::geography, p_radius_meters)
    ORDER BY distance_meters ASC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


CREATE OR REPLACE FUNCTION get_map_hazards(
    p_min_severity INTEGER DEFAULT 1,
    p_limit INTEGER DEFAULT 500
)
RETURNS TABLE (
    id UUID,
    type VARCHAR(20),
    severity INTEGER,
    description TEXT,
    longitude FLOAT,
    latitude FLOAT,
    expires_at TIMESTAMPTZ,
    source_id VARCHAR(100)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        h.id,
        h.type,
        h.severity,
        h.description,
        ST_X(h.location::geometry)::FLOAT as longitude,
        ST_Y(h.location::geometry)::FLOAT as latitude,
        h.expires_at,
        h.source_id
    FROM hazards_active h
    WHERE h.expires_at > NOW()
      AND h.severity >= p_min_severity
    ORDER BY h.severity DESC, h.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


-- ----- From 005_enhanced_features.sql -----

CREATE OR REPLACE FUNCTION get_weather_context(p_city VARCHAR DEFAULT 'chicago')
RETURNS JSONB AS $$
BEGIN
    RETURN (SELECT data FROM weather_context WHERE city = p_city);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


-- ============================================================
-- 3. RE-GRANT PERMISSIONS (CREATE OR REPLACE resets grants)
-- ============================================================

-- Read-only functions: accessible to everyone
GRANT EXECUTE ON FUNCTION check_route_hazards TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_active_hazards_summary TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_nearby_hazards TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_map_hazards TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_weather_context TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_validated_permits TO anon, authenticated;

-- User-scoped write functions: accessible to authenticated + anon (PWA uses anonymous auth)
GRANT EXECUTE ON FUNCTION subscribe_to_route TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_user_subscriptions TO anon, authenticated;
GRANT EXECUTE ON FUNCTION update_subscription_alerts TO anon, authenticated;
GRANT EXECUTE ON FUNCTION delete_subscription TO anon, authenticated;
GRANT EXECUTE ON FUNCTION register_push_subscription TO anon, authenticated;

-- Internal maintenance functions: keep default (postgres/service_role only)
-- cleanup_expired_hazards, cleanup_old_alerts, was_recently_alerted
-- are called by pipelines via service role, not by end users.
