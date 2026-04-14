"""
AirScout Admin Dashboard
========================

Streamlit application for monitoring AirScout hazards, AQI data,
weather context, user subscriptions, and pipeline health.

Run with: streamlit run dashboard/app.py
"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

import streamlit as st
import pandas as pd
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
from sqlalchemy import create_engine, text

CHICAGO_TZ = ZoneInfo("America/Chicago")
CHICAGO_CENTER = [41.8781, -87.6298]

HAZARD_COLORS = {
    "PERMIT": "#FF6B6B",
    "TRAFFIC": "#FFA500",
    "SCHOOL": "#4ECDC4",
    "AQI": "#9B59B6",
}

HAZARD_ICONS = {
    "PERMIT": "building",
    "TRAFFIC": "car",
    "SCHOOL": "graduation-cap",
    "AQI": "cloud",
}


@st.cache_resource
def get_engine():
    host = os.environ.get("SUPABASE_DB_HOST")
    port = os.environ.get("SUPABASE_DB_PORT", "5432")
    dbname = os.environ.get("SUPABASE_DB_NAME", "postgres")
    user = os.environ.get("SUPABASE_DB_USER", "postgres")
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not host or not password:
        st.error("Database credentials not configured. Set SUPABASE_DB_HOST and SUPABASE_DB_PASSWORD in .env")
        return None
    url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    return create_engine(url, echo=False)


@st.cache_data(ttl=60)
def fetch_active_hazards(_engine) -> pd.DataFrame:
    query = """
        SELECT id, type, severity, description, source_id,
               ST_X(location::geometry) as longitude,
               ST_Y(location::geometry) as latitude,
               created_at, updated_at, expires_at, metadata
        FROM hazards_active WHERE expires_at > NOW()
        ORDER BY severity DESC, created_at DESC
    """
    with _engine.connect() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=300)
def fetch_schools(_engine) -> pd.DataFrame:
    query = """
        SELECT school_id, school_name, school_type, address,
               ST_X(location::geometry) as longitude,
               ST_Y(location::geometry) as latitude,
               zone_radius_meters, is_active
        FROM schools_static WHERE is_active = TRUE
    """
    with _engine.connect() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=60)
def fetch_hazard_stats(_engine) -> dict:
    query = """
        SELECT type, COUNT(*) as count, AVG(severity) as avg_severity, MAX(severity) as max_severity
        FROM hazards_active WHERE expires_at > NOW() GROUP BY type
    """
    with _engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return {"by_type": df.to_dict("records"), "total": int(df["count"].sum()) if not df.empty else 0}


@st.cache_data(ttl=300)
def fetch_user_subscriptions(_engine) -> pd.DataFrame:
    query = """
        SELECT id, user_id, route_name, alert_enabled, severity_threshold, created_at, updated_at
        FROM user_subscriptions ORDER BY created_at DESC LIMIT 100
    """
    with _engine.connect() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=60)
def fetch_weather_context(_engine) -> dict | None:
    try:
        with _engine.connect() as conn:
            result = conn.execute(text("SELECT data FROM weather_context WHERE city = 'chicago'"))
            row = result.fetchone()
            return row[0] if row else None
    except Exception:
        return None


@st.cache_data(ttl=60)
def fetch_alert_stats(_engine) -> dict:
    query = """
        SELECT COUNT(*) as total_alerts,
               COUNT(DISTINCT user_id) as unique_users,
               COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '24 hours') as last_24h
        FROM alert_history
    """
    try:
        with _engine.connect() as conn:
            df = pd.read_sql(query, conn)
        return df.iloc[0].to_dict() if not df.empty else {"total_alerts": 0, "unique_users": 0, "last_24h": 0}
    except Exception:
        return {"total_alerts": 0, "unique_users": 0, "last_24h": 0}


def get_folium_color(hex_color: str) -> str:
    return {"#FF6B6B": "red", "#FFA500": "orange", "#4ECDC4": "green", "#9B59B6": "purple"}.get(hex_color, "blue")


def create_hazard_map(hazards_df: pd.DataFrame, schools_df: pd.DataFrame = None) -> folium.Map:
    m = folium.Map(location=CHICAGO_CENTER, zoom_start=11, tiles="CartoDB positron")
    marker_cluster = MarkerCluster(name="Hazards")

    for _, hazard in hazards_df.iterrows():
        h_type = hazard["type"]
        color = HAZARD_COLORS.get(h_type, "#666666")
        icon = HAZARD_ICONS.get(h_type, "info-sign")
        desc = str(hazard.get("description", ""))

        popup_html = f"""
        <div style="width:220px">
            <h4 style="color:{color}">{h_type} Hazard</h4>
            <p><b>Severity:</b> {hazard['severity']}/5</p>
            <p><b>Description:</b> {desc[:120]}</p>
            <p><b>Source:</b> {hazard['source_id']}</p>
            <p><b>Expires:</b> {hazard['expires_at']}</p>
        </div>
        """
        folium.Marker(
            location=[hazard["latitude"], hazard["longitude"]],
            popup=folium.Popup(popup_html, max_width=260),
            icon=folium.Icon(color=get_folium_color(color), icon=icon, prefix="fa"),
            tooltip=f"{h_type}: {desc[:40]}",
        ).add_to(marker_cluster)

    marker_cluster.add_to(m)

    if schools_df is not None and not schools_df.empty:
        school_group = folium.FeatureGroup(name="Schools")
        for _, school in schools_df.iterrows():
            folium.CircleMarker(
                location=[school["latitude"], school["longitude"]],
                radius=5, color="#2E86AB", fill=True, fillOpacity=0.6,
                popup=school["school_name"], tooltip=school["school_name"],
            ).add_to(school_group)
        school_group.add_to(m)

    folium.LayerControl().add_to(m)
    return m


def main():
    st.set_page_config(page_title="AirScout Admin Dashboard", page_icon="🌬️", layout="wide", initial_sidebar_state="expanded")

    st.markdown("""
    <style>
    .hazard-card { padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }
    .permit-card { background-color: #FFE5E5; border-left: 4px solid #FF6B6B; }
    .traffic-card { background-color: #FFF3E0; border-left: 4px solid #FFA500; }
    .school-card { background-color: #E0F7FA; border-left: 4px solid #4ECDC4; }
    .aqi-card { background-color: #F3E5F5; border-left: 4px solid #9B59B6; }
    .metric-big { font-size: 2rem; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

    st.title("AirScout Admin Dashboard")
    st.caption(f"Last updated: {datetime.now(CHICAGO_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    engine = get_engine()
    if engine is None:
        st.stop()

    with st.sidebar:
        st.header("Controls")
        if st.button("Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.subheader("Filter Hazards")
        show_permits = st.checkbox("Demolition Permits", value=True)
        show_traffic = st.checkbox("Traffic Congestion", value=True)
        show_schools = st.checkbox("School Zones", value=True)
        show_aqi = st.checkbox("Air Quality (AQI)", value=True)
        min_severity = st.slider("Minimum Severity", 1, 5, 1)

        st.divider()
        st.subheader("Quick Stats")
        stats = fetch_hazard_stats(engine)
        st.metric("Total Active Hazards", stats["total"])

        alert_stats = fetch_alert_stats(engine)
        st.metric("Alerts (24h)", alert_stats.get("last_24h", 0))
        st.metric("Active Users", alert_stats.get("unique_users", 0))

        weather = fetch_weather_context(engine)
        if weather:
            st.divider()
            st.subheader("Weather Context")
            st.write(f"Wind: {weather.get('wind_speed_mph', 'N/A')} mph")
            st.write(f"Conditions: {weather.get('description', 'N/A')}")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.header("Live Hazard Map")
        hazards_df = fetch_active_hazards(engine)
        schools_df = fetch_schools(engine)

        type_filter = []
        if show_permits: type_filter.append("PERMIT")
        if show_traffic: type_filter.append("TRAFFIC")
        if show_schools: type_filter.append("SCHOOL")
        if show_aqi: type_filter.append("AQI")

        filtered = hazards_df[(hazards_df["type"].isin(type_filter)) & (hazards_df["severity"] >= min_severity)]
        st.caption(f"Showing {len(filtered)} hazards")

        hazard_map = create_hazard_map(filtered, schools_df)
        st_folium(hazard_map, width=None, height=500, returned_objects=[])

    with col2:
        st.header("Statistics")
        if not hazards_df.empty:
            for stat in stats["by_type"]:
                h_type = stat["type"]
                color = HAZARD_COLORS.get(h_type, "#666")
                st.markdown(f"""
                <div class="hazard-card {h_type.lower()}-card">
                    <h3 style="color:{color}">{h_type}</h3>
                    <p class="metric-big">{int(stat['count'])}</p>
                    <p>Avg Severity: {stat['avg_severity']:.1f}</p>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No active hazards")

        st.subheader("Severity Distribution")
        if not hazards_df.empty:
            st.bar_chart(hazards_df["severity"].value_counts().sort_index())

    st.divider()
    st.header("Hazard Details")

    if not filtered.empty:
        display_df = filtered[["type", "severity", "description", "source_id", "expires_at"]].copy()
        display_df["expires_at"] = pd.to_datetime(display_df["expires_at"]).dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(display_df, column_config={
            "type": st.column_config.TextColumn("Type", width="small"),
            "severity": st.column_config.ProgressColumn("Severity", min_value=1, max_value=5, format="%d/5"),
            "description": st.column_config.TextColumn("Description", width="large"),
            "source_id": st.column_config.TextColumn("Source ID", width="medium"),
            "expires_at": st.column_config.TextColumn("Expires", width="medium"),
        }, use_container_width=True, hide_index=True)

    st.divider()
    st.header("User Subscriptions")
    subscriptions_df = fetch_user_subscriptions(engine)
    if not subscriptions_df.empty:
        st.dataframe(subscriptions_df, column_config={
            "user_id": st.column_config.TextColumn("User ID", width="medium"),
            "route_name": st.column_config.TextColumn("Route", width="medium"),
            "alert_enabled": st.column_config.CheckboxColumn("Alerts", width="small"),
            "severity_threshold": st.column_config.NumberColumn("Min Severity", width="small"),
        }, use_container_width=True, hide_index=True)
    else:
        st.info("No user subscriptions yet")

    st.divider()
    st.caption("AirScout - Risk-Based Routing Engine for Chicago")


if __name__ == "__main__":
    main()
