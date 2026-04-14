# AirScout

**Hazard-Aware Routing Engine for Chicago** — Protecting children with asthma from hyper-local pollution sources like idling buses, demolition dust, and poor air quality.

---

## Mission: Protecting Children with Asthma

> **1 in 10 children in Chicago has asthma** — and exposure to localized air pollution can trigger severe attacks. AirScout helps parents and caregivers find safer walking routes to school.

AirScout is specifically designed to protect **asthma-affected children** by:

- Identifying **pollution hotspots** along school routes
- Alerting parents **before** their child walks through hazardous areas
- Flagging **school zones** during high-risk drop-off/pick-up times when diesel buses idle
- Providing **real-time AQI and weather-adjusted scoring**
- **Finding the safest route** between two points using hazard-aware routing

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Zombie Permit Fix** | Demolition permits only count if validated by a 311 complaint within 200m in 48 hours |
| **School Zone Hard Rule** | Areas near schools are automatically HIGH RISK (severity 5) during 7-9 AM and 2-4 PM |
| **25m Geospatial Buffer** | Routes are buffered by 25 meters to catch hazards on adjacent blocks |
| **Hazard-Aware Routing** | OSRM-powered safe route finder that ranks alternatives by pollution exposure |
| **Real-Time AQI** | EPA AirNow integration creates hazards when air quality degrades |
| **Wind-Adjusted Scoring** | OpenWeatherMap wind data amplifies hazard scores when conditions spread particulate matter |
| **Supabase Realtime** | Live map updates when hazards change — no manual refresh needed |
| **Push Notifications** | Web Push alerts when new hazards appear on saved routes |
| **Supabase Auth** | Anonymous authentication for secure user identification |
| **Interactive Map** | Draw routes, find safe routes, see AQI data, get instant risk scores |
| **Admin Dashboard** | Streamlit app for monitoring hazards, AQI, weather, and subscriptions |

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│               Data Sources                                      │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────┐ ┌────────────┐  │
│  │ Chicago Data│ │  EPA AirNow │ │OpenWeather│ │    OSRM    │  │
│  │   Portal    │ │   (AQI)     │ │  (Wind)   │ │  (Routing) │  │
│  └──────┬──────┘ └──────┬──────┘ └─────┬─────┘ └─────┬──────┘  │
└─────────┼───────────────┼──────────────┼─────────────┼─────────┘
          │               │              │             │
          ▼               ▼              ▼             │
┌─────────────────────────────────────────────────────────────────┐
│                 GitHub Actions (CRON)                             │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌──────┐ ┌────────┐          │
│  │Permits │ │Schools │ │Traffic │ │ AQI  │ │Weather │          │
│  │ (6hr)  │ │(daily) │ │(15min) │ │(15m) │ │ (15m)  │          │
│  └────────┘ └────────┘ └────────┘ └──────┘ └────────┘          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│            Supabase (PostgreSQL + PostGIS + Realtime)            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │hazards_active│ │   schools    │ │subscriptions │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
│  ┌──────────────┐ ┌──────────────┐                               │
│  │weather_context│ │alert_history │                               │
│  └──────────────┘ └──────────────┘                               │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
┌────────────────────┐         ┌────────────────────┐
│  Streamlit Admin   │         │    PWA (User)      │◄──── OSRM
│    Dashboard       │         │  Safe Route Finder │
└────────────────────┘         └────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Data Ingestion** | Python, Pandas, Sodapy, GeoPandas |
| **Database** | Supabase (PostgreSQL + PostGIS + Realtime) |
| **Admin Dashboard** | Streamlit |
| **User Frontend** | HTML/JS PWA with Leaflet, Supabase Auth |
| **Routing Engine** | OSRM (Open Source Routing Machine) |
| **Air Quality** | EPA AirNow API |
| **Weather** | OpenWeatherMap API |
| **Orchestration** | GitHub Actions (CRON) |
| **Push Notifications** | Web Push API + VAPID |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Supabase account (free)
- GitHub account

### 1. Clone & Set Up

```bash
git clone https://github.com/adithyavedavyas1999/AirScout.git
cd AirScout
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp env.example .env
```

Edit `.env` with your credentials (Supabase, AirNow, OpenWeatherMap).

### 3. Run Database Migrations

In Supabase SQL Editor, run in order:

1. `database/001_enable_postgis.sql`
2. `database/002_create_tables.sql`
3. `database/003_alert_history.sql`
4. `database/004_api_functions.sql`
5. `database/005_enhanced_features.sql`

### 4. Configure PWA

```bash
cp pwa/config.example.js pwa/config.js
```

Edit `pwa/config.js` with your Supabase URL and anon key.

### 5. Run Data Pipelines

```bash
python -m data_pipeline.ingest_schools
python -m data_pipeline.ingest_permits
python -m data_pipeline.generate_school_hazards
python -m data_pipeline.ingest_traffic
python -m data_pipeline.ingest_aqi
python -m data_pipeline.ingest_weather
```

### 6. Run Tests

```bash
pytest tests/ -v
```

### 7. Launch Dashboard

```bash
streamlit run dashboard/app.py
```

### 8. Test PWA Locally

```bash
cd pwa && python -m http.server 8080
```

---

## Project Structure

```
AirScout/
├── .github/workflows/
│   ├── data_pipelines.yml      # All ingestion jobs (permits, schools, traffic, AQI, weather)
│   ├── alert_service.yml       # Route checking & push notifications
│   ├── deploy_pwa.yml          # GitHub Pages deployment
│   └── tests.yml               # CI test runner
│
├── data_pipeline/
│   ├── db.py                   # Centralized database connection
│   ├── scoring.py              # Centralized risk scoring logic
│   ├── config.py               # All configuration (data portal, AQI, weather, routing)
│   ├── ingest_permits.py       # Zombie Permit validation
│   ├── ingest_schools.py       # School data
│   ├── ingest_traffic.py       # Traffic + school zone override
│   ├── ingest_aqi.py           # EPA AirNow AQI data
│   ├── ingest_weather.py       # Wind/weather for scoring
│   ├── generate_school_hazards.py
│   ├── check_route.py          # Route checker + OSRM safe routing
│   └── alert_service.py        # Push notification service
│
├── database/
│   ├── 001_enable_postgis.sql
│   ├── 002_create_tables.sql
│   ├── 003_alert_history.sql
│   ├── 004_api_functions.sql
│   └── 005_enhanced_features.sql
│
├── dashboard/
│   └── app.py                  # Streamlit admin (hazards, AQI, weather, subscriptions)
│
├── pwa/
│   ├── index.html              # PWA with auth, realtime, safe routing, AQI
│   ├── config.example.js
│   ├── manifest.json
│   └── sw.js
│
├── supabase/functions/
│   └── check-route/index.ts    # Edge function with safe-route support
│
├── tests/
│   ├── test_scoring.py
│   ├── test_config.py
│   ├── test_check_route.py
│   ├── test_ingest_aqi.py
│   └── test_weather.py
│
├── scripts/
│   └── generate_vapid_keys.py
│
├── requirements.txt
└── env.example
```

---

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SUPABASE_DB_HOST` | Database host | Yes |
| `SUPABASE_DB_PASSWORD` | Database password | Yes |
| `SUPABASE_DB_PORT` | Database port (5432 local, 6543 pooler) | No |
| `SUPABASE_DB_USER` | Database user | No |
| `SUPABASE_URL` | Supabase API URL | For PWA |
| `SUPABASE_ANON_KEY` | Supabase publishable key | For PWA |
| `AIRNOW_API_KEY` | EPA AirNow API key | For AQI |
| `OPENWEATHER_API_KEY` | OpenWeatherMap key | For weather |
| `CHICAGO_DATA_APP_TOKEN` | Chicago Data Portal token | Optional |
| `VAPID_PUBLIC_KEY` | Push notification public key | For push |
| `VAPID_PRIVATE_KEY` | Push notification private key | For push |
| `VAPID_EMAIL` | Contact email for push | For push |

### GitHub Secrets

Add all environment variables above as GitHub Secrets for Actions.

---

## Automation Schedule

| Job | Schedule | Purpose |
|-----|----------|---------|
| Permit Ingestion | Every 6 hours | Validate demolition permits |
| School Data | Daily at 6 AM | Refresh school locations |
| School Hazards | Every 15 min | Generate peak hour hazards |
| Traffic Data | Every 15 min | Ingest congestion data |
| AQI Data | Every 15 min | EPA air quality readings |
| Weather Update | Every 15 min | Wind data for scoring |
| Alert Service | Every 15 min | Check routes & send notifications |
| Tests | On push/PR | Automated test suite |

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=data_pipeline --cov-report=term-missing

# Dry run pipelines
python -m data_pipeline.ingest_permits --dry-run
python -m data_pipeline.alert_service --dry-run

# Check a route
python -m data_pipeline.check_route --coords '[[-87.63,41.88],[-87.64,41.92]]'

# Find safest route
python -m data_pipeline.check_route --start '[-87.63,41.88]' --end '[-87.64,41.92]'
```

---

## Deployment

### PWA (GitHub Pages)

1. Go to repo **Settings > Pages**
2. Set **Source** to **GitHub Actions**
3. Add secrets: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `VAPID_PUBLIC_KEY`
4. Run the **Deploy PWA** workflow

### Push Notifications

```bash
python scripts/generate_vapid_keys.py
```

Add the generated keys to GitHub Secrets.
