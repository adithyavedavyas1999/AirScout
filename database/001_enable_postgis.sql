-- ============================================================
-- AirScout Database Migration: 001_enable_postgis.sql
-- Enable PostGIS extension for geospatial operations
-- ============================================================
-- Run this FIRST in Supabase SQL Editor

-- Enable PostGIS extension (required for geometry types and spatial functions)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Verify PostGIS is enabled
SELECT PostGIS_Version();


