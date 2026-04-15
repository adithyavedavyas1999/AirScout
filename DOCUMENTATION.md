# AirScout — Project Documentation

**Hazard-Aware Routing Engine for Chicago**

**Author:** Adithya Vedavyas  
**Duration:** June 2025 – October 2025  
**Live Application:** [https://adithyavedavyas1999.github.io/AirScout/](https://adithyavedavyas1999.github.io/AirScout/)  
**Repository:** [https://github.com/adithyavedavyas1999/AirScout](https://github.com/adithyavedavyas1999/AirScout)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Objectives](#3-objectives)
4. [System Architecture](#4-system-architecture)
5. [Technology Stack](#5-technology-stack)
6. [Database Design](#6-database-design)
7. [Data Ingestion Pipelines](#7-data-ingestion-pipelines)
8. [Risk Scoring Model](#8-risk-scoring-model)
9. [Progressive Web Application](#9-progressive-web-application)
10. [Admin Dashboard](#10-admin-dashboard)
11. [Supabase Edge Functions](#11-supabase-edge-functions)
12. [Testing Strategy](#12-testing-strategy)
13. [CI/CD and Deployment](#13-cicd-and-deployment)
14. [Security Considerations](#14-security-considerations)
15. [Configuration Reference](#15-configuration-reference)
16. [Development Timeline](#16-development-timeline)
17. [Challenges and Solutions](#17-challenges-and-solutions)
18. [Future Work](#18-future-work)
19. [References](#19-references)

---

## 1. Executive Summary

AirScout is a risk-based routing engine designed to protect children with asthma from hyper-local pollution hazards in Chicago. The system ingests real-time data from multiple public APIs — including the Chicago Data Portal, the EPA AirNow network, and OpenWeatherMap — to identify demolition sites, traffic congestion zones, school-zone diesel idling corridors, and degraded air quality pockets. It then scores walking routes against these hazards using a weighted proximity model and presents safer alternatives through a mobile-friendly Progressive Web Application.

The project was built over roughly five months, starting with database schema design and data pipeline prototyping in June 2025, through frontend development and real-time integration over the summer, and concluding with testing, CI/CD automation, and deployment in October 2025.

At its core, AirScout addresses a gap that existing navigation apps ignore: none of them factor in localized, transient pollution sources when recommending pedestrian routes. For a parent walking a child with asthma to school, passing within 25 meters of an active demolition site or a line of idling school buses can trigger a severe episode. AirScout makes that invisible risk visible and actionable.

---

## 2. Problem Statement

Childhood asthma affects approximately 1 in 10 children in Chicago, making it one of the highest-prevalence cities in the United States. While ambient air quality indices (AQI) provide a city-wide snapshot, they fail to capture the micro-environments that matter most for vulnerable pedestrians:

- **Demolition and construction dust.** Active demolition permits generate particulate matter (PM2.5 and PM10) concentrated within a few hundred meters of the site. Standard AQI monitors, spaced miles apart, rarely capture these events.
- **Diesel bus idling at schools.** During morning drop-off (7–9 AM) and afternoon pickup (2–4 PM), diesel buses idle near schools for extended periods. The exhaust concentrations within 150 meters of these zones can be several times higher than ambient levels.
- **Traffic congestion hotspots.** Stalled traffic produces elevated NO2 and particulate levels. Pedestrians walking alongside congested corridors are exposed to significantly higher pollutant concentrations than those a block away.
- **Wind-amplified dispersion.** On windy days, particulate matter from a single source can spread across adjacent blocks, extending the effective hazard radius well beyond the physical site.

No commercially available routing application accounts for these transient, localized hazards when generating pedestrian directions. Google Maps and Apple Maps optimize for distance and time; they have no concept of a "demolition permit" or "school zone peak hour." AirScout was built to fill this gap.

---

## 3. Objectives

The project was designed around four primary objectives:

1. **Aggregate hyper-local hazard data** from multiple public sources into a unified, geospatially indexed database that updates on 15-minute to 6-hour intervals depending on data volatility.

2. **Score pedestrian routes** against nearby hazards using a transparent, reproducible risk model that weighs hazard severity, proximity, and environmental factors like wind speed.

3. **Deliver safer alternatives** by integrating with an open-source routing engine (OSRM) to generate multiple walking routes between two points, rank them by pollution exposure, and recommend the safest option.

4. **Provide real-time awareness** through a mobile-friendly PWA with live map updates, push notifications for saved routes, and an administrative dashboard for monitoring system health.

---

## 4. System Architecture

AirScout follows a three-tier architecture: a data ingestion layer, a persistence and computation layer, and a presentation layer.

```
                         ┌──────────────────────────────────┐
                         │        External Data Sources      │
                         │                                    │
                         │  Chicago Data Portal (Socrata)     │
                         │  EPA AirNow (REST API)             │
                         │  OpenWeatherMap (REST API)         │
                         │  OSRM (Routing API)                │
                         └────────────┬───────────────────────┘
                                      │
                         ┌────────────▼───────────────────────┐
                         │     GitHub Actions (Scheduled)      │
                         │                                      │
                         │  Permits     every 6 hours           │
                         │  Schools     daily at 06:00 UTC      │
                         │  Traffic     every 15 minutes         │
                         │  AQI         every 15 minutes         │
                         │  Weather     every 15 minutes         │
                         │  Alerts      every 15 minutes         │
                         └────────────┬────────────────────────┘
                                      │
                         ┌────────────▼────────────────────────┐
                         │   Supabase (PostgreSQL + PostGIS)    │
                         │                                      │
                         │  hazards_active   schools_static     │
                         │  user_subscriptions  alert_history   │
                         │  weather_context  push_subscriptions │
                         │  complaints_311   permits_demolition │
                         │                                      │
                         │  + PostGIS spatial functions          │
                         │  + Row-Level Security policies       │
                         │  + Realtime change broadcasts        │
                         └──────┬──────────────────┬───────────┘
                                │                  │
                    ┌───────────▼──────┐  ┌────────▼──────────┐
                    │  Admin Dashboard │  │   PWA (User App)  │
                    │  (Streamlit)     │  │   Leaflet + Auth  │
                    │  Internal use    │  │   OSRM + Realtime │
                    └──────────────────┘  └───────────────────┘
```

### Data flow

1. **Ingestion.** Python scripts, orchestrated by GitHub Actions on cron schedules, pull data from external APIs, validate and transform it, and upsert rows into the Supabase PostgreSQL database. Each pipeline is idempotent — running it twice with the same source data produces the same database state.

2. **Storage and computation.** Supabase provides PostgreSQL with the PostGIS extension for spatial queries. Server-side SQL functions handle route buffering, hazard intersection, distance calculation, and subscription management. Row-Level Security ensures users can only access their own subscriptions.

3. **Presentation.** The PWA connects to Supabase via its JavaScript client library, calling RPC functions to load hazards and check routes. Supabase Realtime broadcasts database changes over WebSocket, so the map updates without polling. The admin dashboard connects directly to PostgreSQL via SQLAlchemy for richer analytical queries.

4. **Notifications.** A dedicated alert service pipeline runs every 15 minutes, iterating over user subscriptions, checking each saved route against current hazards, and sending Web Push notifications for new threats that exceed the user's severity threshold.

---

## 5. Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Database | PostgreSQL 15 + PostGIS 3.4 (via Supabase) | PostGIS provides spatial indexing (GiST), geometry operations (`ST_Buffer`, `ST_DWithin`, `ST_Transform`), and geographic projections essential for meter-accurate buffering. Supabase adds managed hosting, Realtime subscriptions, and Row-Level Security. |
| Data Ingestion | Python 3.11, Pandas, GeoPandas, Sodapy | Pandas handles tabular transformations efficiently. GeoPandas extends this with spatial operations. Sodapy is the official client for Chicago's Socrata-based Data Portal. |
| Routing Engine | OSRM (Open Source Routing Machine) | OSRM provides fast pedestrian routing with support for alternative routes. The public demo server is used for prototyping; a self-hosted instance can be deployed for production. |
| Air Quality | EPA AirNow API | The authoritative source for real-time AQI readings across the United States. Returns observation data by latitude/longitude with configurable radius. |
| Weather | OpenWeatherMap API | Provides current wind speed, direction, and conditions. The free tier supports the 15-minute polling interval comfortably. |
| Frontend | HTML/CSS/JS (PWA), Leaflet, Supabase JS v2 | A single-page PWA avoids framework overhead while supporting offline caching, push notifications, and home screen installation. Leaflet is the standard for interactive web maps. |
| Admin Dashboard | Streamlit, Folium | Streamlit enables rapid development of data-driven dashboards. Folium renders Leaflet maps server-side and integrates cleanly with Streamlit via the `streamlit-folium` bridge. |
| Edge Functions | Deno (Supabase Functions), TypeScript | Supabase Edge Functions run close to the database, reducing latency for route-checking operations. TypeScript provides type safety for the routing logic. |
| CI/CD | GitHub Actions | Handles both scheduled data pipeline execution and PWA deployment to GitHub Pages, keeping all automation in one place. |
| Push Notifications | Web Push (VAPID), pywebpush | Standards-based push notifications that work across browsers without requiring a native app. |

---

## 6. Database Design

The database schema was developed iteratively across five migration files, each building on the previous one. This section describes the final schema as of migration 005.

### 6.1 Core Tables

**`hazards_active`** — The central table. Each row represents a currently active pollution hazard.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Auto-generated identifier |
| `type` | VARCHAR | One of `PERMIT`, `TRAFFIC`, `SCHOOL`, or `AQI` |
| `severity` | INTEGER (1–5) | 1 = minimal, 5 = critical |
| `description` | TEXT | Human-readable summary |
| `location` | GEOGRAPHY(POINT, 4326) | Longitude/latitude of the hazard |
| `source_id` | VARCHAR (UNIQUE) | Deduplication key from the originating dataset |
| `created_at` | TIMESTAMPTZ | Row insertion time |
| `updated_at` | TIMESTAMPTZ | Last upsert time |
| `expires_at` | TIMESTAMPTZ | When the hazard should be considered stale |
| `metadata` | JSONB | Source-specific details (permit number, AQI reading, etc.) |

A CHECK constraint enforces the valid type values. The `source_id` uniqueness constraint ensures idempotent upserts — running a pipeline multiple times will update existing rows rather than creating duplicates.

**`schools_static`** — Reference table for Chicago school locations.

| Column | Type | Description |
|--------|------|-------------|
| `school_id` | VARCHAR (UNIQUE) | Chicago Data Portal school identifier |
| `school_name` | VARCHAR | Display name |
| `school_type` | VARCHAR | Elementary, High School, etc. |
| `address` | VARCHAR | Street address |
| `location` | GEOGRAPHY(POINT, 4326) | Geographic coordinates |
| `zone_radius_meters` | INTEGER (default 150) | Buffer zone around the school |
| `is_active` | BOOLEAN | Whether the school is currently operational |

**`user_subscriptions`** — Routes that users have saved for monitoring.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Subscription identifier |
| `user_id` | VARCHAR | Supabase Auth user ID (anonymous or authenticated) |
| `route_name` | VARCHAR | User-assigned name |
| `route_geometry` | GEOGRAPHY(LINESTRING, 4326) | The saved route path |
| `push_token` | TEXT | Serialized Web Push subscription |
| `alert_enabled` | BOOLEAN | Whether alerts are active |
| `severity_threshold` | INTEGER (1–5) | Minimum severity to trigger an alert |
| `created_at`, `updated_at` | TIMESTAMPTZ | Timestamps |

A UNIQUE constraint on `(user_id, route_name)` prevents duplicate route names per user.

**`weather_context`** — Stores the latest weather observation per city.

| Column | Type | Description |
|--------|------|-------------|
| `city` | VARCHAR (PK) | City identifier (e.g., "chicago") |
| `data` | JSONB | Wind speed, direction, description, temperature |
| `fetched_at` | TIMESTAMPTZ | When the observation was retrieved |

**`alert_history`** and **`push_subscriptions`** — Track notification delivery and Web Push endpoints respectively. `alert_history` has a composite unique constraint on `(user_id, hazard_source_id, sent_at)` to prevent duplicate alerts.

**`complaints_311`** and **`permits_demolition`** — Caching tables for the zombie permit validation pipeline (described in Section 7.1).

### 6.2 Spatial Indexes

Every table with a `location` column has a GiST index for efficient spatial queries:

```sql
CREATE INDEX idx_hazards_location ON hazards_active USING GIST (location);
CREATE INDEX idx_schools_location ON schools_static USING GIST (location);
CREATE INDEX idx_subscriptions_route ON user_subscriptions USING GIST (route_geometry);
```

A partial index on `hazards_active` filters out expired rows to accelerate the most common query pattern:

```sql
CREATE INDEX idx_hazards_active_not_expired
    ON hazards_active (type, severity DESC)
    WHERE expires_at > NOW();
```

### 6.3 Server-Side Functions

All route-checking and subscription-management logic runs as `SECURITY DEFINER` PostgreSQL functions, called via Supabase's RPC interface. This keeps business logic close to the data and reduces round trips.

**`check_route_hazards(route_wkt, buffer_meters, min_severity)`** — The core spatial query. It:
1. Parses the WKT LINESTRING into a geometry
2. Projects it from WGS 84 (EPSG:4326) to Illinois State Plane (EPSG:26971) for meter-accurate buffering
3. Buffers the projected geometry by the specified distance
4. Projects back to WGS 84
5. Intersects with `hazards_active` where `expires_at > NOW()` and `severity >= min_severity`
6. Returns each hazard's ID, type, severity, description, coordinates, distance in meters, and metadata

**`subscribe_to_route`**, **`get_user_subscriptions`**, **`update_subscription_alerts`**, **`delete_subscription`** — CRUD operations for user subscriptions, enforcing `user_id` ownership.

**`get_map_hazards(min_severity, limit)`** — Returns hazards with extracted latitude/longitude for the PWA map layer.

**`get_nearby_hazards(lon, lat, radius, min_severity, limit)`** — Point-based proximity search.

**`get_weather_context(city)`** — Returns the JSONB weather data for a given city.

### 6.4 Row-Level Security

Row-Level Security (RLS) is enabled on all user-facing tables:

- **`hazards_active`**, **`schools_static`**, **`weather_context`**: Public read access (`USING (true)`).
- **`user_subscriptions`**: Users can only read and modify their own rows (`WHERE auth.uid()::text = user_id`).
- **`alert_history`**: Users can only read their own alert history.
- **`push_subscriptions`**: Users can only manage their own push endpoints.

---

## 7. Data Ingestion Pipelines

Each pipeline is a standalone Python module in the `data_pipeline/` package. They share a centralized database connection module (`db.py`) and a centralized configuration module (`config.py`). All pipelines are idempotent — they use `ON CONFLICT ... DO UPDATE` (upsert) semantics so that repeated execution is safe.

### 7.1 Demolition Permits — "Zombie Permit" Validation

**Module:** `ingest_permits.py`  
**Schedule:** Every 6 hours  
**Data Source:** Chicago Data Portal, dataset `ydr8-5enu` (demolition permits) and `v6vf-nfxy` (311 complaints)

This pipeline implements what I call "zombie permit" filtering. The Chicago Data Portal lists thousands of demolition permits, but many are stale — filed months ago and never acted upon, or already completed. Treating every permit as an active hazard would flood the map with false positives.

The validation works by cross-referencing permits against 311 complaint data:

1. Fetch all demolition permits issued within the configured lookback period.
2. Fetch 311 complaints of types that indicate active demolition (dust, noise, debris) from the same time window.
3. For each permit, check whether any matching complaint exists within 200 meters and 48 hours.
4. Only permits with a corroborating complaint are considered "validated" and inserted into `hazards_active`.

This approach dramatically reduces false positives. In practice, it filters out roughly 70–80% of raw permits, leaving only those with observable ground-truth impact.

**Severity calculation:** Based on permit type and complaint density. A permit with multiple corroborating complaints receives a higher severity score (up to 5).

### 7.2 School Data

**Module:** `ingest_schools.py`  
**Schedule:** Daily at 06:00 UTC  
**Data Source:** Chicago Data Portal, dataset `9xs2-f89t`

Fetches the current list of Chicago public and charter schools, geocodes their locations, and upserts them into `schools_static`. This table serves as reference data for the school zone hazard generator.

### 7.3 School Zone Hazards

**Module:** `generate_school_hazards.py`  
**Schedule:** Every 15 minutes  
**Data Source:** Internal (`schools_static` table)

Generates time-dependent `SCHOOL` hazards during peak hours. The logic checks whether the current time (in the America/Chicago timezone) falls within the morning window (7:00–9:00 AM) or afternoon window (2:00–4:00 PM). If so, it creates a severity-5 hazard at each active school's location with an expiration time set to the end of the current peak window.

Outside of peak hours, no school hazards exist. The `source_id` includes the date and window identifier to ensure proper deduplication.

### 7.4 Traffic Congestion

**Module:** `ingest_traffic.py`  
**Schedule:** Every 15 minutes  
**Data Source:** Chicago Data Portal, dataset `sxs8-h27x` (Chicago Traffic Tracker)

Ingests current traffic congestion segments from the city's traffic tracker. The pipeline applies an additional filter: if a congested segment falls within the zone radius of any school during peak hours, its severity is automatically elevated to 5 (the "school zone hard rule"). This captures the compounding effect of traffic congestion near schools during drop-off and pickup times, when diesel buses add to the pollution burden.

Stale traffic hazards (those not refreshed in the last cycle) are cleaned up automatically.

### 7.5 Air Quality Index

**Module:** `ingest_aqi.py`  
**Schedule:** Every 15 minutes  
**Data Source:** EPA AirNow API (`https://www.airnowapi.org/aq/observation/latLong/current/`)

Queries the AirNow API for current AQI readings within a configurable bounding box centered on Chicago. Readings above a threshold (default: AQI 50, which corresponds to the boundary between "Good" and "Moderate" categories) are converted into `AQI` hazards in the database.

**AQI-to-severity mapping:**

| AQI Range | Category | Severity |
|-----------|----------|----------|
| 0–50 | Good | Not ingested |
| 51–100 | Moderate | 2 |
| 101–150 | Unhealthy for Sensitive Groups | 3 |
| 151–200 | Unhealthy | 4 |
| 201+ | Very Unhealthy / Hazardous | 5 |

The severity mapping was chosen to align with the general risk thresholds used throughout the system, and specifically because children with asthma fall into the "sensitive groups" category that the EPA identifies at AQI 101+.

### 7.6 Weather Context

**Module:** `ingest_weather.py`  
**Schedule:** Every 15 minutes  
**Data Source:** OpenWeatherMap API (`https://api.openweathermap.org/data/2.5/weather`)

Fetches the current weather observation for Chicago and stores it in the `weather_context` table. The primary use is the **wind amplifier** — a multiplier applied to hazard risk scores when wind conditions are likely to spread particulate matter beyond the immediate source area.

**Wind amplifier logic:**

| Wind Speed (mph) | Amplifier |
|-------------------|-----------|
| < 5 | 1.0 (no amplification) |
| 5–10 | Linear interpolation from 1.0 to 1.5 |
| 10–20 | Linear interpolation from 1.5 to 2.0 |
| > 20 | Capped at 2.0 |

The amplifier is stored alongside the weather data so that downstream consumers (the alert service, the admin dashboard) can apply it without re-fetching the API.

### 7.7 Alert Service

**Module:** `alert_service.py`  
**Schedule:** Every 15 minutes  
**Data Source:** Internal (database)

Iterates over all user subscriptions with `alert_enabled = true`. For each subscription:

1. Extracts the saved route geometry.
2. Runs the `check_route_hazards` SQL function against current hazards.
3. Filters results by the user's severity threshold.
4. Checks `alert_history` to avoid re-alerting for the same hazard within a configurable deduplication window.
5. Sends a Web Push notification via the `pywebpush` library using the subscription's push token and the server's VAPID credentials.
6. Records the alert in `alert_history`.

The notification payload includes the hazard type, severity, description, and a deep link back to the PWA map view.

---

## 8. Risk Scoring Model

The risk score is a central concept in AirScout. It is a number from 0 to 100 that represents the cumulative pollution exposure risk along a given route. The scoring formula is consistent across all three execution contexts — the Python backend (`scoring.py`), the Supabase Edge Function (`check-route/index.ts`), and the PWA client-side JavaScript.

### 8.1 Formula

For each hazard within the buffer zone of a route:

```
distance_weight = max(0, 1 - (distance_meters / buffer_meters))
severity_weight = severity / 5
contribution    = distance_weight × severity_weight × 25
```

The total score is the sum of all individual contributions, capped at 100:

```
risk_score = min(100, Σ contributions)
```

### 8.2 Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `buffer_meters` | 25 | Based on EPA guidance that pedestrian-level exposure to point-source pollution is significant within approximately 25 meters. Wider buffers produce too many false positives; narrower ones miss adjacent-block hazards. |
| `RISK_WEIGHT_MULTIPLIER` | 25 | Calibrated so that a single severity-5 hazard at zero distance produces a score of 25, and four such hazards produce 100 (the cap). |
| High threshold | 70 | Three or more high-severity hazards in close proximity. |
| Moderate threshold | 40 | One or two moderate hazards at close range, or several low-severity hazards. |

### 8.3 Risk Levels

| Score Range | Level | Interpretation |
|-------------|-------|---------------|
| 70–100 | HIGH | Route passes through or very near multiple active hazards. Strongly recommend an alternative. |
| 40–69 | MODERATE | Some nearby hazards detected. Consider an alternative if available. |
| 0–39 | LOW | Minimal hazard exposure. Route is relatively safe. |

### 8.4 Design Rationale

The linear distance decay (`1 - d/buffer`) was chosen over an exponential or Gaussian model for two reasons: transparency and simplicity. Parents and caregivers using this tool need to trust the score, and a linear model is easy to explain — "the closer the hazard, the higher the risk, proportionally." More sophisticated models were prototyped during development but did not meaningfully improve route discrimination in testing, while making the score harder to justify to non-technical users.

---

## 9. Progressive Web Application

The user-facing application is a single-page PWA built with vanilla HTML, CSS, and JavaScript. The choice to avoid a framework like React or Vue was deliberate — the application's interactivity is concentrated in the map layer (handled by Leaflet) and in Supabase RPC calls, neither of which benefit significantly from a virtual DOM. The resulting bundle is small (a single HTML file plus external CDN libraries), loads quickly on mobile devices, and is straightforward to deploy to static hosting.

### 9.1 Map Interface

The map is rendered by Leaflet 1.9.4 with the Leaflet.draw plugin for route input. Four tile styles are available (Midnight, Light, Voyager, Explorer), all from CARTO's free basemap service. The map is centered on Chicago at zoom level 12.

Hazard markers are rendered as `CircleMarker` elements with color-coded fills:

| Hazard Type | Color | Semantic |
|-------------|-------|----------|
| PERMIT (Demolition) | Red (#ff4757) | Immediate particulate risk |
| TRAFFIC | Orange (#ffa502) | Elevated NO2 and PM |
| SCHOOL | Blue (#3498db) | Peak-hour diesel exposure |
| AQI | Purple (#9b59b6) | Degraded ambient air quality |

### 9.2 Route Drawing and Risk Assessment

Users draw a route by clicking the "Draw Route" button, which activates Leaflet.draw's polyline tool. Once the route is completed, the application:

1. Extracts the coordinates as `[longitude, latitude]` pairs.
2. Constructs a WKT LINESTRING.
3. Calls the `check_route_hazards` Supabase RPC.
4. Computes the risk score client-side using the same formula as the backend.
5. Displays the score in a floating status card with color-coded severity.
6. Highlights nearby hazards with enlarged, opaque markers showing distance.

### 9.3 Safe Route Finder

The "Safe Route" feature allows users to enter a start and end address. The application:

1. Geocodes both addresses using Nominatim (`https://nominatim.openstreetmap.org/search`).
2. Requests up to 3 alternative walking routes from the OSRM public API (`https://router.project-osrm.org/route/v1/foot/`).
3. For each returned route, calls `check_route_hazards` to compute a risk score.
4. Sorts routes by ascending risk score.
5. Renders all routes on the map with distinct colors (green for safest, orange for moderate, red for most hazardous).
6. Displays a comparison panel showing distance, estimated walking time, risk score, and hazard count for each route.

### 9.4 Authentication

The PWA uses Supabase Auth with anonymous sign-in. On first load, if no existing session is found, the app calls `signInAnonymously()` to obtain a user ID. This ID is used to associate saved routes and subscriptions without requiring email registration. The approach reduces friction to zero — users can save routes and receive alerts without creating an account.

### 9.5 Real-Time Updates

The PWA subscribes to Supabase Realtime on the `hazards_active` table using PostgreSQL's logical replication. When any row is inserted, updated, or deleted, the client receives a notification and refreshes the map markers. A green "Live" indicator in the header confirms the WebSocket connection is active.

As a fallback, the application also polls `loadHazards()` every 120 seconds to handle cases where the Realtime connection is temporarily interrupted.

### 9.6 Push Notifications

When saving a route, the application requests notification permission and, if granted, subscribes to the browser's Push API using the server's VAPID public key. The resulting push subscription is stored alongside the route in the database. The alert service pipeline (Section 7.7) uses this subscription to send notifications when new hazards appear on the route.

### 9.7 Offline Support

The service worker (`sw.js`) implements a cache-first strategy for static assets (HTML, CSS, JavaScript, fonts, map library files). API calls to Supabase, AirNow, OpenWeatherMap, OSRM, and Nominatim bypass the cache and always go to the network. If the network is unavailable, the service worker returns the cached application shell, allowing users to view previously loaded data.

### 9.8 Accessibility

- `user-scalable=yes` on the viewport meta tag to allow pinch-to-zoom.
- `aria-label` attributes on all interactive elements.
- `role="dialog"` and `aria-modal="true"` on modal overlays.
- Focus trapping within open modals to prevent tabbing to obscured elements.
- Escape key handler to close any open modal.

### 9.9 XSS Prevention

All user-generated and API-sourced content rendered in the DOM is passed through an `escapeHtml()` utility that uses `textContent` assignment to neutralize any embedded HTML or script tags. No use of `innerHTML` with unsanitized input.

---

## 10. Admin Dashboard

The admin dashboard (`dashboard/app.py`) is a Streamlit application intended for project administrators and data quality monitoring. It connects directly to the PostgreSQL database via SQLAlchemy (bypassing the Supabase API layer) to provide richer query capabilities.

### Features

- **Live hazard map.** A Folium map with clustered markers for all active hazards, plus circle markers for school locations. Layer toggles for each hazard type. Severity-based filtering via a sidebar slider.
- **Hazard statistics.** Per-type counts, average severity, and maximum severity displayed as styled cards. A bar chart showing the severity distribution across all active hazards.
- **Weather context.** Current wind speed and conditions from the `weather_context` table, shown in the sidebar.
- **Alert metrics.** Total alerts sent, unique users reached, and alerts in the last 24 hours.
- **Hazard detail table.** A searchable, sortable data table of all active hazards with severity displayed as a progress bar.
- **Subscription overview.** A table of all user subscriptions (route names, alert status, severity thresholds).
- **Refresh.** A sidebar button clears the Streamlit cache and forces a fresh database query.

### Caching

All database queries are cached with TTL decorators (`@st.cache_data`):
- Hazards and weather: 60-second TTL
- Schools and subscriptions: 300-second TTL

This balances responsiveness with database load.

---

## 11. Supabase Edge Functions

A single Edge Function (`supabase/functions/check-route/index.ts`) provides a serverless HTTP endpoint for route checking. It runs on Deno and uses the Supabase service role key for database access.

### Modes

**Standard check (`mode: "check"`):** Accepts a route as either a WKT string or a coordinate array, calls `check_route_hazards` via RPC, and returns the risk assessment.

**Safe route (`mode: "safe-route"`):** Accepts origin and destination coordinates, queries OSRM for up to 3 alternative walking routes, checks each against the database, and returns them sorted by risk score with a recommended route.

### CORS

The function includes full CORS headers (`Access-Control-Allow-Origin: *`) to support direct calls from the PWA on any domain.

---

## 12. Testing Strategy

The test suite (`tests/`) contains unit tests for the core logic modules. Tests are written with `pytest` and run automatically on every push and pull request via the `tests.yml` GitHub Actions workflow.

### Test Coverage

| Module | Test File | What is Tested |
|--------|-----------|---------------|
| `scoring.py` | `test_scoring.py` | Risk score calculation for empty, single, and multiple hazard inputs. Threshold classification (HIGH, MODERATE, LOW). Score capping at 100. Custom buffer distances. Human-readable risk messages. |
| `config.py` | `test_config.py` | Correct dataset IDs for all Chicago Data Portal sources. Default values for zombie permit, school zone, geospatial, AQI, weather, routing, and multi-city configurations. |
| `check_route.py` | `test_check_route.py` | Coordinate parsing from JSON arrays. WKT parsing. Buffer polygon generation (ensuring the buffer is a valid polygon larger than the input line). Route length calculation in kilometers. |
| `ingest_aqi.py` | `test_ingest_aqi.py` | AQI-to-severity mapping for all EPA categories (Good through Hazardous). |
| `ingest_weather.py` | `test_weather.py` | Wind amplifier calculation for null input, calm conditions, threshold crossings, high wind, and the 2.0 cap. |

### Running Tests

```bash
# All tests with verbose output
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=data_pipeline --cov-report=term-missing
```

The test suite is designed to run without any external dependencies (no database, no API calls). All tests use in-memory data structures and direct function calls.

---

## 13. CI/CD and Deployment

### 13.1 GitHub Actions Workflows

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| Data Pipelines | `data_pipelines.yml` | Cron (6h, daily, 15min) + manual dispatch | Runs all ingestion scripts on schedule. Each job can be triggered independently via the `job` input parameter. |
| Alert Service | `alert_service.yml` | Every 15 minutes + manual dispatch | Checks subscribed routes and sends push notifications. Supports a `dry_run` mode for testing without sending real notifications. |
| Deploy PWA | `deploy_pwa.yml` | Push to `main` (paths: `pwa/**`) + manual dispatch | Injects Supabase credentials into `config.js`, then deploys the `pwa/` directory to GitHub Pages. |
| Tests | `tests.yml` | Push to `main` (paths: `data_pipeline/**`, `tests/**`, `requirements.txt`) + PRs | Runs pytest with coverage reporting. |

### 13.2 PWA Deployment

The PWA is hosted on GitHub Pages. The deployment workflow:

1. Checks out the repository.
2. Generates `pwa/config.js` from GitHub Secrets (Supabase URL, anon key, VAPID public key).
3. Uploads the `pwa/` directory as a GitHub Pages artifact.
4. Deploys via the `actions/deploy-pages` action.

This approach keeps API credentials out of the repository while allowing static hosting. The `config.js` file is listed in `.gitignore` to prevent accidental commits of credentials.

### 13.3 Secret Management

All sensitive values are stored as GitHub Secrets and injected at runtime:

- Database credentials (`SUPABASE_DB_HOST`, `SUPABASE_DB_PASSWORD`)
- API keys (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `AIRNOW_API_KEY`, `OPENWEATHER_API_KEY`)
- VAPID keys for push notifications
- Chicago Data Portal app token

---

## 14. Security Considerations

### 14.1 Database Access Control

Row-Level Security policies enforce that users can only access their own subscriptions and alert history. The `SECURITY DEFINER` attribute on RPC functions allows them to read `hazards_active` (public data) while respecting subscription ownership constraints.

### 14.2 API Key Protection

API keys never appear in the committed source code. The `.gitignore` excludes `.env`, `pwa/config.js`, and `*.key` files. GitHub Secrets are used for all CI/CD workflows, and the PWA's `config.js` is generated at deploy time.

### 14.3 Input Sanitization

- **SQL injection:** All database queries use parameterized statements via SQLAlchemy's `text()` with bound parameters, or Supabase's RPC interface which handles parameterization.
- **XSS:** The PWA sanitizes all dynamic content through `escapeHtml()` before DOM insertion. No raw `innerHTML` with user input.
- **CORS:** The Edge Function allows cross-origin requests to support PWA deployment on any domain, but the Supabase RLS layer ensures data access control regardless of origin.

### 14.4 Authentication

Supabase anonymous auth provides a user identity without requiring credentials. This is appropriate for the current use case (personal route saving) and can be upgraded to email/social auth without schema changes, since the `user_id` column already stores Supabase auth UIDs.

---

## 15. Configuration Reference

### 15.1 Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SUPABASE_DB_HOST` | PostgreSQL host (e.g., `db.xxxxx.supabase.co`) | Yes |
| `SUPABASE_DB_PASSWORD` | Database password | Yes |
| `SUPABASE_DB_PORT` | Port (5432 for direct, 6543 for connection pooler) | No (default: 5432) |
| `SUPABASE_DB_NAME` | Database name | No (default: postgres) |
| `SUPABASE_DB_USER` | Database user | No (default: postgres) |
| `SUPABASE_URL` | Supabase project API URL | For PWA/Edge |
| `SUPABASE_ANON_KEY` | Supabase publishable (anon) key | For PWA/Edge |
| `AIRNOW_API_KEY` | EPA AirNow API key | For AQI pipeline |
| `OPENWEATHER_API_KEY` | OpenWeatherMap API key | For weather pipeline |
| `CHICAGO_DATA_APP_TOKEN` | Socrata app token (increases rate limits) | Optional |
| `VAPID_PUBLIC_KEY` | Web Push public key | For push notifications |
| `VAPID_PRIVATE_KEY` | Web Push private key | For push notifications |
| `VAPID_EMAIL` | Contact email for VAPID | For push notifications |

### 15.2 Pipeline Configuration

The `data_pipeline/config.py` module defines dataclass-based configuration objects:

- **`ChicagoDataConfig`**: Socrata dataset IDs for permits, complaints, schools, and traffic.
- **`ZombiePermitConfig`**: Complaint radius (200m), time window (48h), max permit age (180 days).
- **`SchoolZoneConfig`**: Morning peak (07:00–09:00), afternoon peak (14:00–16:00), default severity (5), zone radius (150m).
- **`GeoConfig`**: SRID (26971 for Illinois State Plane), buffer distance (25m), coordinate reference system (EPSG:4326).
- **`AQIConfig`**: AirNow bounding box for Chicago, API base URL, AQI threshold (50).
- **`WeatherConfig`**: OpenWeatherMap API base URL, Chicago coordinates, wind threshold (5 mph), max amplifier (2.0).
- **`RoutingConfig`**: OSRM base URL, profile (foot), max alternatives (3).
- **`MultiCityConfig`**: Extensibility point for future multi-city support (currently Chicago only).

---

## 16. Development Timeline

### Phase 1: Research and Foundation (June 2025)

The initial phase focused on understanding the problem domain and establishing the technical foundation. I reviewed EPA guidance on pedestrian-level pollution exposure, studied the Chicago Data Portal's available datasets, and evaluated database options. Supabase was selected for its combination of managed PostgreSQL, PostGIS support, built-in auth, and Realtime subscriptions — all available on the free tier.

The first two database migrations (`001_enable_postgis.sql` and `002_create_tables.sql`) were written during this phase, along with the initial `ingest_permits.py` and `ingest_schools.py` pipelines. The zombie permit validation algorithm was developed iteratively — the first version used a simple distance filter on all permits, which produced unacceptably high false positive rates. Cross-referencing with 311 complaints reduced this dramatically.

### Phase 2: Pipeline Development (July – August 2025)

This phase expanded the data ingestion layer to include traffic congestion, school zone hazard generation, and the alert service. The school zone "hard rule" (severity 5 during peak hours) emerged from analyzing the data — morning and afternoon periods consistently showed the highest overlap between diesel bus presence and pedestrian student traffic.

The `alert_service.py` module was the most complex pipeline to develop, requiring careful handling of deduplication (avoiding repeat alerts for the same hazard), subscription iteration, and Web Push delivery. The alert history schema (`003_alert_history.sql`) was designed during this phase.

### Phase 3: Frontend and API Layer (August – September 2025)

The PWA was developed in August, starting with the map interface and route drawing, then adding the Supabase integration. The `004_api_functions.sql` migration — defining all the RPC functions — went through several iterations as the frontend's data requirements became clearer.

The safe route finder was added in September after integrating with OSRM. The initial implementation used a single route and compared it against a straight-line path, but this was replaced with the current approach of requesting multiple alternatives from OSRM and ranking them by risk score, which produces more useful results.

Supabase Realtime integration was also added during this phase, replacing the original 30-second polling interval with WebSocket-based live updates. The improvement in responsiveness was immediately noticeable during testing.

### Phase 4: Integration and Hardening (September – October 2025)

The final phase focused on bringing all components together and hardening the system. Key work included:

- **EPA AirNow integration** (`ingest_aqi.py`) and the AQI hazard type, requiring `005_enhanced_features.sql`.
- **OpenWeatherMap integration** (`ingest_weather.py`) and the wind amplifier model.
- **Centralized modules** (`db.py` and `scoring.py`) were extracted to eliminate code duplication across pipelines and ensure the risk formula remained consistent.
- **Test suite development** — unit tests for all core logic modules.
- **CI/CD setup** — GitHub Actions workflows for all pipelines, the alert service, PWA deployment, and automated testing.
- **Security review** — SQL injection audit, XSS prevention, credential management.
- **Admin dashboard enhancements** — AQI display, weather context, alert statistics.

The Edge Function (`check-route/index.ts`) was developed as a serverless alternative to calling the database directly from the PWA, providing a clean API for route checking with built-in OSRM integration.

---

## 17. Challenges and Solutions

### Zombie Permits

**Challenge:** The Chicago Data Portal lists over 10,000 demolition permits, the vast majority of which are inactive. Displaying all of them as hazards would make the map unusable and erode user trust.

**Solution:** Cross-reference permits with 311 complaints within 200 meters and 48 hours. Only permits with corroborating evidence of active work are treated as hazards. This reduced false positives by approximately 75%.

### Meter-Accurate Spatial Buffering

**Challenge:** PostGIS `ST_Buffer` on WGS 84 (EPSG:4326) geography operates in degrees, not meters, leading to distorted buffer shapes at Chicago's latitude.

**Solution:** Project to Illinois State Plane East (EPSG:26971) before buffering, then project back. This coordinate system is optimized for northern Illinois and provides meter-accurate results within the Chicago area.

### Risk Score Consistency

**Challenge:** The risk score is computed in three different environments (Python, TypeScript/Deno, browser JavaScript). Any divergence would produce inconsistent results depending on whether a user checks a route via the PWA, the Edge Function, or the alert service.

**Solution:** Extracted the formula into a centralized Python module (`scoring.py`) and documented the constants. The TypeScript and JavaScript implementations use identical constants and logic. Unit tests verify the Python implementation, and the formula's simplicity (no external dependencies, no floating-point edge cases) makes manual verification of the other implementations straightforward.

### Service Worker Cache Staleness

**Challenge:** After deploying a JavaScript fix, returning users were still served the old, broken version from the service worker cache.

**Solution:** Incremented the cache version identifier in `sw.js` (from `v2` to `v3`). The service worker's `activate` event handler deletes all caches except the current version, forcing a fresh download. This is a standard PWA cache-busting pattern, but it was a useful reminder to automate cache versioning in future releases.

### Supabase CDN Variable Conflict

**Challenge:** The Supabase JavaScript CDN library declares a global `supabase` variable. The inline script in `index.html` originally used `let supabase = null` for the client instance, which caused a `SyntaxError` (illegal redeclaration) that killed all JavaScript execution.

**Solution:** Renamed the application-level variable to `sb` to avoid the naming collision. The lesson: when using CDN-loaded libraries that pollute the global namespace, always check for conflicts before declaring variables with the same name.

---

## 18. Future Work

Several enhancements are planned or under consideration:

- **Self-hosted OSRM instance.** The current implementation uses the public OSRM demo server, which has rate limits and no SLA. A Docker-based OSRM deployment with Chicago-specific OSM data would improve reliability and allow custom routing profiles that penalize roads adjacent to known hazard zones.

- **Predictive hazard modeling.** Using historical permit and complaint data to predict where demolition activity is likely to occur in the coming days, allowing proactive route adjustments.

- **Multi-city expansion.** The configuration module already includes a `MultiCityConfig` dataclass. Extending to other cities requires identifying equivalent data sources for permits, schools, and traffic.

- **Email/social authentication.** The current anonymous auth is frictionless but means users lose their saved routes if they clear browser data. Adding optional email sign-in would provide persistence across devices.

- **Custom routing profiles.** Instead of ranking OSRM's standard alternatives by risk, modify the routing graph itself to assign higher weights to road segments near active hazards, producing routes that inherently avoid pollution sources.

- **Historical trend analysis.** Storing time-series hazard data to identify patterns — for example, neighborhoods with persistently poor air quality during specific seasons.

---

## 19. References

1. **Chicago Data Portal.** City of Chicago open data. [https://data.cityofchicago.org/](https://data.cityofchicago.org/)
2. **EPA AirNow API.** Real-time air quality data. [https://docs.airnowapi.org/](https://docs.airnowapi.org/)
3. **OpenWeatherMap API.** Weather data. [https://openweathermap.org/api](https://openweathermap.org/api)
4. **OSRM (Open Source Routing Machine).** [http://project-osrm.org/](http://project-osrm.org/)
5. **Supabase.** Open-source Firebase alternative. [https://supabase.com/docs](https://supabase.com/docs)
6. **PostGIS.** Spatial database extension. [https://postgis.net/documentation/](https://postgis.net/documentation/)
7. **Leaflet.** Interactive maps. [https://leafletjs.com/](https://leafletjs.com/)
8. **Web Push Protocol (RFC 8030).** [https://datatracker.ietf.org/doc/html/rfc8030](https://datatracker.ietf.org/doc/html/rfc8030)
9. **EPA Guide: Near Roadway Air Pollution.** [https://www.epa.gov/air-research/near-roadway-air-pollution-and-health](https://www.epa.gov/air-research/near-roadway-air-pollution-and-health)
10. **American Lung Association — State of the Air: Chicago.** [https://www.lung.org/research/sota](https://www.lung.org/research/sota)
