# 🌬️ AirScout

**Risk-Based Routing Engine for Chicago** — Protecting children with asthma from hyper-local pollution sources like idling buses and demolition dust.

---

## 🎯 Mission: Protecting Children with Asthma

> **1 in 10 children in Chicago has asthma** — and exposure to localized air pollution can trigger severe attacks. AirScout helps parents and caregivers find safer walking routes to school.

AirScout is specifically designed to protect **asthma-affected children** by:

- 🚸 **Identifying pollution hotspots** along school routes
- ⚠️ **Alerting parents** before their child walks through hazardous areas
- 🏫 **Flagging school zones** during high-risk drop-off/pick-up times when diesel buses idle
- 📱 **Providing real-time updates** so families can make informed decisions

**This isn't about air quality indexes** — it's about avoiding the specific block where a demolition crew is kicking up dust, or the intersection where 15 school buses are idling their diesel engines.

---

## 📖 Overview

AirScout is a **real-time hazard detection system** that helps Chicago families avoid pollution hotspots. Unlike traditional air quality apps that measure ambient conditions, AirScout identifies **specific pollution sources** and warns users when they're on their child's route to school.

### The Problem

- 🏗️ **Demolition sites** generate harmful particulate matter (PM2.5, PM10) that can trigger asthma attacks
- 🚌 **Diesel buses idling** near schools create localized pollution 5-10x worse than background levels
- 🚗 **Traffic congestion** concentrates vehicle exhaust at intersections children must cross
- 😷 **Children are more vulnerable** — they breathe faster and their lungs are still developing

### The Solution

AirScout combines multiple Chicago data sources to create a **risk-based routing engine** that:

1. **Validates** demolition permits against 311 complaints (no "zombie permits")
2. **Hard-codes** school zones as high-risk during drop-off/pick-up hours (7-9 AM, 2-4 PM)
3. **Buffers** user routes by 25 meters to catch hazards on adjacent blocks
4. **Alerts** parents via push notifications when hazards appear on saved routes

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🧟 **Zombie Permit Fix** | Demolition permits only count if validated by a 311 complaint within 200m in the last 48 hours |
| 🏫 **School Zone Hard Rule** | Areas near schools are automatically HIGH RISK (severity 5) during 7-9 AM and 2-4 PM |
| 📐 **25m Geospatial Buffer** | Routes are buffered by 25 meters to catch hazards on adjacent blocks |
| 🔔 **Push Notifications** | Real-time alerts when new hazards appear on your saved routes |
| 🗺️ **Interactive Map** | Draw routes, see hazards, get instant risk scores |
| 📊 **Admin Dashboard** | Streamlit app for monitoring and validation |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Chicago Data Portal                         │
│  (Permits, 311 Complaints, Schools, Traffic)                    │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                   GitHub Actions (CRON)                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │   Permits    │ │   Schools    │ │   Traffic    │             │
│  │  (6 hours)   │ │   (daily)    │ │  (15 min)    │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Supabase (PostgreSQL + PostGIS)                 │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │hazards_active│ │   schools    │ │subscriptions │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
└─────────────────────┬───────────────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
┌──────────────────┐    ┌──────────────────┐
│  Streamlit Admin │    │    PWA (User)    │
│    Dashboard     │    │  Route Alerts    │
└──────────────────┘    └──────────────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Data Ingestion** | Python, Pandas, Sodapy, Geopandas |
| **Database** | Supabase (PostgreSQL + PostGIS) |
| **Admin Dashboard** | Streamlit |
| **User Frontend** | HTML/JS PWA with Leaflet |
| **Orchestration** | GitHub Actions (CRON) |
| **Push Notifications** | Web Push API |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Supabase account (free)
- GitHub account

### 1. Clone the Repository

```bash
git clone https://github.com/adithyavedavyas1999/AirScout.git
cd AirScout
```

### 2. Set Up Python Environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Supabase

1. Create a new project at [supabase.com](https://supabase.com)
2. Go to **Settings → Database** and copy your credentials
3. Create your environment file:

```bash
cp env.example .env
```

Edit `.env` with your Supabase credentials:

```env
SUPABASE_DB_HOST=db.xxxxx.supabase.co
SUPABASE_DB_PASSWORD=your-password
```

### 4. Run Database Migrations

In Supabase SQL Editor, run these files in order:

1. `database/001_enable_postgis.sql`
2. `database/002_create_tables.sql`
3. `database/003_alert_history.sql`
4. `database/004_api_functions.sql`

### 5. Configure PWA

```bash
cp pwa/config.example.js pwa/config.js
```

Edit `pwa/config.js` with your Supabase URL and anon key.

### 6. Run Data Pipelines

```bash
# Ingest school data (run once)
python data_pipeline/ingest_schools.py

# Ingest demolition permits
python data_pipeline/ingest_permits.py

# Generate school zone hazards (during peak hours)
python data_pipeline/generate_school_hazards.py

# Ingest traffic data
python data_pipeline/ingest_traffic.py
```

### 7. Launch Admin Dashboard

```bash
streamlit run dashboard/app.py
```

### 8. Test PWA Locally

```bash
cd pwa
python -m http.server 8080
# Open http://localhost:8080
```

---

## 📁 Project Structure

```
AirScout/
├── .github/workflows/          # GitHub Actions
│   ├── data_pipelines.yml      # All ingestion jobs
│   └── alert_service.yml       # Route checking & notifications
│
├── data_pipeline/              # Python scripts
│   ├── ingest_permits.py       # Zombie Permit logic
│   ├── ingest_schools.py       # School data
│   ├── ingest_traffic.py       # Traffic + school override
│   ├── generate_school_hazards.py  # Peak hour hazards
│   ├── check_route.py          # 25m buffer route checker
│   └── alert_service.py        # Push notification service
│
├── database/                   # SQL migrations
│   ├── 001_enable_postgis.sql
│   ├── 002_create_tables.sql
│   ├── 003_alert_history.sql
│   └── 004_api_functions.sql
│
├── dashboard/                  # Streamlit admin
│   └── app.py
│
├── pwa/                        # Progressive Web App
│   ├── index.html
│   ├── config.example.js
│   ├── manifest.json
│   └── sw.js
│
├── scripts/
│   └── generate_vapid_keys.py  # Push notification keys
│
├── requirements.txt
└── env.example
```

---

## 🔧 Configuration

### Environment Variables

#### For Local Development (.env file)

| Variable | Description | Example |
|----------|-------------|---------|
| `SUPABASE_DB_HOST` | Direct database host | `db.xxx.supabase.co` |
| `SUPABASE_DB_PORT` | Direct port | `5432` |
| `SUPABASE_DB_USER` | Database user | `postgres` |
| `SUPABASE_DB_PASSWORD` | Database password | Your password |
| `SUPABASE_DB_NAME` | Database name | `postgres` |

#### For GitHub Actions (use pooler)

| Variable | Description | Example |
|----------|-------------|---------|
| `SUPABASE_DB_HOST` | Pooler host | `aws-0-us-east-1.pooler.supabase.com` |
| `SUPABASE_DB_PORT` | Pooler port | `6543` |
| `SUPABASE_DB_USER` | Pooler user | `postgres.your-project-ref` |

> **Why different?** Supabase blocks direct connections from cloud servers. The pooler is designed for external/cloud connections.

### GitHub Secrets (for Actions)

Add these secrets in **Settings → Secrets and variables → Actions**:

| Secret | Description | Example |
|--------|-------------|---------|
| `SUPABASE_DB_HOST` | Pooler host (NOT direct) | `aws-0-us-east-1.pooler.supabase.com` |
| `SUPABASE_DB_PORT` | Pooler port | `6543` |
| `SUPABASE_DB_USER` | Pooler username | `postgres.your-project-ref` |
| `SUPABASE_DB_PASSWORD` | Database password | Your password |
| `SUPABASE_URL` | Supabase API URL | `https://xxx.supabase.co` |
| `SUPABASE_ANON_KEY` | Publishable API key | Your anon/publishable key |
| `VAPID_PUBLIC_KEY` | Push notification public key | Generated key |
| `VAPID_PRIVATE_KEY` | Push notification private key | Generated key |
| `VAPID_EMAIL` | Contact email for push | Your email |

> **Note:** GitHub Actions requires the Supabase **connection pooler** (not direct connection) because cloud servers are blocked from direct database access.

---

## 📊 Database Schema

### Core Tables

| Table | Purpose |
|-------|---------|
| `hazards_active` | Active pollution hazards (PERMIT, TRAFFIC, SCHOOL) |
| `user_subscriptions` | User routes with push notification settings |
| `schools_static` | Chicago Public Schools locations |
| `alert_history` | Sent notifications (prevents duplicates) |

### Key Functions

| Function | Description |
|----------|-------------|
| `check_route_hazards(route_wkt, buffer_m)` | Check route for hazards within buffer |
| `subscribe_to_route(user_id, route_wkt, ...)` | Subscribe to route alerts |
| `get_nearby_hazards(lon, lat, radius)` | Get hazards near a location |

---

## 🗓️ Automation Schedule

| Job | Schedule | Purpose |
|-----|----------|---------|
| Permit Ingestion | Every 6 hours | Validate demolition permits |
| School Data | Daily at 6 AM | Refresh school locations |
| School Hazards | Every 15 min | Generate peak hour hazards |
| Traffic Data | Every 15 min | Ingest congestion data |
| Alert Service | Every 15 min | Check routes & send notifications |

---

## 🧪 Testing

### Dry Run Mode

All scripts support `--dry-run` to test without database writes:

```bash
python data_pipeline/ingest_permits.py --dry-run
python data_pipeline/alert_service.py --dry-run
```

### Check a Route

```bash
python data_pipeline/check_route.py --coords '[[-87.63,41.88],[-87.64,41.92]]'
```

---

## 🚀 Deployment

### PWA Deployment (GitHub Pages)

1. Go to repo **Settings → Pages**
2. Under "Build and deployment", set **Source** to **GitHub Actions**
3. Add the required secrets (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `VAPID_PUBLIC_KEY`)
4. Go to **Actions → "Deploy PWA"** and click **Run workflow**
5. Your PWA will be live at `https://username.github.io/AirScout/`

### Push Notifications Setup

1. Generate VAPID keys:
   ```bash
   python scripts/generate_vapid_keys.py
   ```
2. Add `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_EMAIL` to GitHub Secrets

---

## 🌐 Live Demo

**https://adithyavedavyas1999.github.io/AirScout/**
