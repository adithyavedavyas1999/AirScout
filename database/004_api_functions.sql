-- ============================================================
-- AirScout Database Migration: 004_api_functions.sql
-- SQL functions for API endpoints (Edge Functions)
-- ============================================================
-- Run this AFTER 003_alert_history.sql

-- ============================================================
-- FUNCTION: check_route_hazards
-- Called by Edge Function to check a route for hazards
-- ============================================================
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
    -- Create buffer around route
    -- Transform to Illinois State Plane (meters) for accurate buffering
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
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: subscribe_to_route
-- Creates a new route subscription for a user
-- ============================================================
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
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: get_user_subscriptions
-- Get all subscriptions for a user
-- ============================================================
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
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: update_subscription_alerts
-- Enable/disable alerts for a subscription
-- ============================================================
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
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: delete_subscription
-- Delete a user's subscription
-- ============================================================
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
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: get_active_hazards_summary
-- Get summary of active hazards (for PWA dashboard)
-- ============================================================
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
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: get_nearby_hazards
-- Get hazards near a point (for PWA "around me" feature)
-- ============================================================
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
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- Grant execute permissions to authenticated users
-- ============================================================
GRANT EXECUTE ON FUNCTION check_route_hazards TO authenticated;
GRANT EXECUTE ON FUNCTION subscribe_to_route TO authenticated;
GRANT EXECUTE ON FUNCTION get_user_subscriptions TO authenticated;
GRANT EXECUTE ON FUNCTION update_subscription_alerts TO authenticated;
GRANT EXECUTE ON FUNCTION delete_subscription TO authenticated;
GRANT EXECUTE ON FUNCTION get_active_hazards_summary TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_nearby_hazards TO anon, authenticated;

-- ============================================================
-- COMMENTS
-- ============================================================
COMMENT ON FUNCTION check_route_hazards IS 'Check a route (25m buffer) for active hazards';
COMMENT ON FUNCTION subscribe_to_route IS 'Subscribe a user to receive alerts for a route';
COMMENT ON FUNCTION get_user_subscriptions IS 'Get all route subscriptions for a user';
COMMENT ON FUNCTION get_nearby_hazards IS 'Get hazards near a location for the PWA';

