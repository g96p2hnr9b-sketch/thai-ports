"""
Thailand Ports - Green Shipping Route Planner (DEMO)

เพิ่มจากเวอร์ชันก่อน:
1) พยากรณ์อากาศล่วงหน้า 7 วัน (Open-Meteo) + แนะนำวันออกเรือที่ดีที่สุด
2) ประมาณจำนวนเรือที่ต้องใช้ (คร่าวๆ เพราะยังไม่มีข้อมูลดีมานด์จริง)
3) เส้นทางเดินเรือ 2 ทางเลือก: เส้นทางหลัก กับเส้นทางเลี่ยง (อ้อมช่องแคบซุนดาแทนมะละกา
   เฉพาะช่วงที่ข้ามฝั่งอ่าวไทย<->อันดามัน) + โหมดไป-กลับ
   เส้นที่เลือกใช้งานวาดเป็นเส้นทึบ เส้นทางเลือกอื่นวาดเป็นเส้นประ
4) จุดส่ง = ไอคอนกระพริบแบบเรดาร์, จุดรับ = หมุดปักแผนที่
5) ไอคอนเรือขยับไปตามเส้นทางที่เลือก (จำลองการขนส่ง)

** DEMO ** ค่าพิกัด เชื้อเพลิง เกณฑ์อากาศ และเส้นทางเลี่ยงเป็นค่าประมาณ/แบบง่าย
ไม่ใช่เส้นทางเดินเรือทางการหรือข้อมูลดีมานด์จริง
"""

import itertools
import math
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import folium
import pandas as pd
import requests
import searoute as sr
import streamlit as st
from streamlit_folium import st_folium

# ---------------------------------------------------------------
# 1) ข้อมูลท่าเรือ (DEMO)
# ---------------------------------------------------------------
PORTS = [
    {"name": "Bangkok (Khlong Toei)", "lat": 13.7074, "lon": 100.5806, "type": "container"},
    {"name": "Laem Chabang", "lat": 13.0827, "lon": 100.8830, "type": "container"},
    {"name": "Si Racha", "lat": 13.1600, "lon": 100.9300, "type": "sea"},
    {"name": "Sattahip", "lat": 12.6500, "lon": 100.9000, "type": "sea"},
    {"name": "Map Ta Phut", "lat": 12.7150, "lon": 101.1550, "type": "sea"},
    {"name": "Songkhla", "lat": 7.2100, "lon": 100.5800, "type": "container"},
    {"name": "Phuket", "lat": 7.8600, "lon": 98.4200, "type": "sea"},
    {"name": "Ranong", "lat": 9.9640, "lon": 98.6100, "type": "sea"},
    {"name": "Don Sak (Surat Thani)", "lat": 9.3050, "lon": 99.7000, "type": "sea"},
]

TYPE_COLORS = {"sea": "#0b0f3b", "container": "#e0405e"}
TYPE_LABELS = {"sea": "ท่าเรือทะเล / ท่าเรือแม่น้ำ", "container": "ตู้คอนเทนเนอร์"}

RISK_COLORS = {0: "#2e9e5b", 1: "#f0a500", 2: "#d62828"}
RISK_LABELS = {0: "✅ เดินเรือได้ปกติ", 1: "⚠️ ระวัง", 2: "⛔ ไม่ควรออกเรือ"}
RISK_SPEED_FACTOR = {0: 1.0, 1: 0.85, 2: 0.6}

CO2_PER_TON_FUEL = 3.114
REF_SPEED_KN = 14.0

OBJECTIVES = {
    "ระยะทางสั้นที่สุด": "km",
    "เร็วที่สุด": "days",
    "ต้นทุนต่ำสุด": "cost",
    "CO2 ต่ำสุด": "co2",
}

SUNDA_WAYPOINT = {"name": "Sunda Strait (waypoint)", "lat": -6.0, "lon": 105.2}


def short(name):
    return name.split(" (")[0]


def is_gulf_side(p):
    """DEMO: คร่าวๆ ว่าท่านี้อยู่ฝั่งอ่าวไทยหรืออันดามัน จาก longitude"""
    return p["lon"] >= 99.5


# ---------------------------------------------------------------
# 2) เส้นทางเดินเรือ 1 ช่วง (leg) + เส้นทางเลี่ยง
# ---------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@lru_cache(maxsize=None)
def sea_leg(lat1, lon1, lat2, lon2):
    try:
        r = sr.searoute([lon1, lat1], [lon2, lat2], units="km")
        coords = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
        return coords, float(r["properties"]["length"]), True
    except Exception:
        return [[lat1, lon1], [lat2, lon2]], haversine_km(lat1, lon1, lat2, lon2), False


def leg(a, b):
    if (a["lat"], a["lon"]) <= (b["lat"], b["lon"]):
        return sea_leg(a["lat"], a["lon"], b["lat"], b["lon"])
    coords, km, real = sea_leg(b["lat"], b["lon"], a["lat"], a["lon"])
    return coords[::-1], km, real


def alt_waypoint(a, b):
    """คืน waypoint สำหรับเส้นทางเลี่ยง ถ้าช่วงนี้ข้ามฝั่งอ่าวไทย<->อันดามัน (เลี่ยงมะละกา อ้อมซุนดา)"""
    if is_gulf_side(a) != is_gulf_side(b):
        return SUNDA_WAYPOINT
    return None


def resolve_legs(order, alt=False):
    """แตกลำดับท่าเรือเป็นคู่จุดต่อจุด แทรก waypoint เลี่ยงถ้า alt=True และช่วงนั้นข้ามฝั่ง"""
    legs, used_alt = [], False
    for a, b in zip(order[:-1], order[1:]):
        wp = alt_waypoint(a, b) if alt else None
        if wp:
            legs.append((a, wp))
            legs.append((wp, b))
            used_alt = True
        else:
            legs.append((a, b))
    return legs, used_alt


def path_from_legs(legs):
    path, all_real = [], True
    for a, b in legs:
        coords, _, real = leg(a, b)
        path.extend(coords)
        all_real = all_real and real
    return path, all_real


def metrics_from_legs(legs, p):
    km = days = fuel = 0.0
    fuel_per_day = p["fuel_ref"] * (p["speed"] / REF_SPEED_KN) ** 3
    for a, b in legs:
        _, d, _ = leg(a, b)
        risk = max(p["risk"].get(a["name"], 0), p["risk"].get(b["name"], 0))
        eff_speed = p["speed"] * RISK_SPEED_FACTOR[risk]
        t = d / (1.852 * eff_speed * 24)
        km += d
        days += t
        fuel += fuel_per_day * t
    co2 = fuel * CO2_PER_TON_FUEL
    cost = fuel * p["fuel_price"] + days * p["opex"]
    return {"km": km, "days": days, "fuel": fuel, "co2": co2, "cost": cost}


def best_order(depot, customers, key, p, round_trip):
    def full(perm):
        seq = [depot, *perm]
        if round_trip:
            seq = seq + [depot]
        return seq

    if len(customers) <= 7:
        best = min(
            itertools.permutations(customers),
            key=lambda perm: metrics_from_legs(resolve_legs(full(perm))[0], p)[key],
        )
        return full(best)

    route, cur, left = [depot], depot, list(customers)
    while left:
        nxt = min(left, key=lambda c: metrics_from_legs(resolve_legs([cur, c])[0], p)[key])
        route.append(nxt)
        left.remove(nxt)
        cur = nxt
    if round_trip:
        route.append(depot)
    return route


# ---------------------------------------------------------------
# 3) สภาพอากาศ live + พยากรณ์ล่วงหน้า (Open-Meteo - ฟรี ไม่ต้องใช้ key)
# ---------------------------------------------------------------
def _fetch_current(latlon):
    lat, lon = latlon
    out = {"wind": None, "rain": None, "code": None, "wave": None}
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current": "weather_code,wind_speed_10m,precipitation",
                "wind_speed_unit": "kmh", "timezone": "Asia/Bangkok",
            },
            timeout=8,
        )
        cur = r.json()["current"]
        out["wind"] = cur.get("wind_speed_10m")
        out["rain"] = cur.get("precipitation")
        out["code"] = cur.get("weather_code")
    except Exception:
        pass
    try:
        r = requests.get(
            "https://marine-api.open-meteo.com/v1/marine",
            params={"latitude": lat, "longitude": lon, "current": "wave_height", "timezone": "Asia/Bangkok"},
            timeout=8,
        )
        out["wave"] = r.json()["current"].get("wave_height")
    except Exception:
        pass
    return out


def _fetch_forecast(latlon):
    lat, lon = latlon
    days = []
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": "weather_code,wind_speed_10m_max,precipitation_sum",
                "wind_speed_unit": "kmh", "timezone": "Asia/Bangkok", "forecast_days": 7,
            },
            timeout=8,
        )
        d = r.json()["daily"]
        for i, date in enumerate(d["time"]):
            days.append({
                "date": date,
                "wind": d["wind_speed_10m_max"][i],
                "rain": d["precipitation_sum"][i],
                "code": d["weather_code"][i],
                "wave": None,
            })
    except Exception:
        return []
    try:
        r = requests.get(
            "https://marine-api.open-meteo.com/v1/marine",
            params={"latitude": lat, "longitude": lon, "daily": "wave_height_max",
                    "timezone": "Asia/Bangkok", "forecast_days": 7},
            timeout=8,
        )
        w = r.json()["daily"]["wave_height_max"]
        for i in range(min(len(w), len(days))):
            days[i]["wave"] = w[i]
    except Exception:
        pass
    return days


@st.cache_data(ttl=900, show_spinner="กำลังดึงสภาพอากาศล่าสุด...")
def get_all_weather(coords):
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_fetch_current, coords))


@st.cache_data(ttl=1800, show_spinner="กำลังดึงพยากรณ์อากาศ 7 วัน...")
def get_all_forecast(coords):
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_fetch_forecast, coords))


def risk_level(w):
    wind, wave, rain, code = w.get("wind") or 0, w.get("wave") or 0, w.get("rain") or 0, w.get("code")
    if code in (95, 96, 99) or wind >= 45 or wave >= 3.0:
        return 2
    if wind >= 30 or wave >= 2.0 or rain >= 2.5 or code in (63, 65, 81, 82):
        return 1
    return 0


def best_departure_day(stop_names, forecast_by_port):
    """เทียบ 7 วันข้างหน้า เลือกวันที่ความเสี่ยงรวม (สูงสุดของทุกท่าที่ผ่าน) ต่ำที่สุด"""
    rows = []
    ref = forecast_by_port.get(stop_names[0], [])
    for i, day in enumerate(ref):
        risks = []
        for n in stop_names:
            fc = forecast_by_port.get(n, [])
            risks.append(risk_level(fc[i]) if i < len(fc) else 0)
        rows.append({"date": day["date"], "risk": max(risks) if risks else 0})
    if not rows:
        return None, []
    best = min(rows, key=lambda r: (r["risk"], r["date"]))
    return best, rows


# ---------------------------------------------------------------
# 4) แผนที่
# ---------------------------------------------------------------
RADAR_CSS = """
<style>
.radar-wrap { position:relative; width:22px; height:22px; }
.radar-dot { position:absolute; width:10px; height:10px; left:6px; top:6px;
             background:#2f6fed; border:2px solid white; border-radius:50%; z-index:2; }
.radar-ping { position:absolute; width:22px; height:22px; left:0; top:0;
              background:#2f6fed; border-radius:50%; opacity:.65;
              animation: radarPing 1.6s ease-out infinite; }
@keyframes radarPing {
  0%   { transform: scale(0.3); opacity:.65; }
  100% { transform: scale(2.4); opacity:0; }
}
</style>
"""


def build_map(all_ports, depot, customers, primary_legs, alt_legs, show_alt,
              selected_is_alt, wx, risk, color_by_weather, show_ship):
    m = folium.Map(location=[10.5, 100.5], zoom_start=6, tiles="OpenStreetMap")
    m.get_root().header.add_child(folium.Element(RADAR_CSS))

    special = {depot["name"]} | {c["name"] for c in customers} if depot else set()

    for p in all_ports:
        if p["name"] in special:
            continue
        w = wx.get(p["name"], {})
        color = RISK_COLORS[risk.get(p["name"], 0)] if color_by_weather else TYPE_COLORS[p["type"]]
        wave = "-" if w.get("wave") is None else f"{w['wave']:.1f} ม."
        wind = "-" if w.get("wind") is None else f"{w['wind']:.0f} กม./ชม."
        folium.CircleMarker(
            location=[p["lat"], p["lon"]], radius=8, color="white", weight=2,
            fill=True, fill_color=color, fill_opacity=1, tooltip=p["name"],
            popup=folium.Popup(f"<b>{p['name']}</b><br>{TYPE_LABELS[p['type']]}<br>ลม {wind} | คลื่น {wave}", max_width=250),
        ).add_to(m)

    # จุดส่ง: ไอคอนเรดาร์กระพริบ
    if depot:
        folium.Marker(
            [depot["lat"], depot["lon"]],
            tooltip=f"จุดส่ง: {depot['name']}",
            icon=folium.DivIcon(html='<div class="radar-wrap"><div class="radar-ping"></div><div class="radar-dot"></div></div>'),
        ).add_to(m)

    # จุดรับ: หมุดปักแผนที่
    for c in customers:
        folium.Marker(
            [c["lat"], c["lon"]],
            tooltip=f"จุดรับ: {c['name']}",
            icon=folium.Icon(color="red", icon="flag"),
        ).add_to(m)

    primary_path, _ = path_from_legs(primary_legs)
    alt_path, _ = path_from_legs(alt_legs) if alt_legs else (None, True)

    solid_path = alt_path if selected_is_alt else primary_path
    dashed_path = primary_path if selected_is_alt else alt_path

    if dashed_path and show_alt:
        folium.PolyLine(dashed_path, color="#9aa4c4", weight=3, opacity=0.8, dash_array="6,8").add_to(m)
    if solid_path:
        folium.PolyLine(solid_path, color="#2f6fed", weight=4, opacity=0.9).add_to(m)

    legend_body = (
        '<span style="color:#2e9e5b;">&#9679;</span> เดินเรือได้ปกติ<br>'
        '<span style="color:#f0a500;">&#9679;</span> ระวัง<br>'
        '<span style="color:#d62828;">&#9679;</span> ไม่ควรออกเรือ'
        if color_by_weather else
        '<span style="color:#0b0f3b;">&#9679;</span> ท่าเรือทะเล / ท่าเรือแม่น้ำ<br>'
        '<span style="color:#e0405e;">&#9679;</span> ตู้คอนเทนเนอร์<br>'
        '📡 จุดส่ง &nbsp; 📍 จุดรับ'
    )
    m.get_root().html.add_child(folium.Element(f"""
    <div style="position: fixed; top: 12px; left: 50px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,.25); font-size: 13px; color: #222;">
      <b>Thailand</b><br>{legend_body}
    </div>
    """))

    if show_ship and solid_path and len(solid_path) > 1:
        map_var = m.get_name()
        ship_js = f"""
        <script>
        (function() {{
          var path = {solid_path};
          var idx = 0;
          var icon = L.divIcon({{html:'🚢', className:'', iconSize:[26,26]}});
          var marker = L.marker(path[0], {{icon: icon}}).addTo({map_var});
          setInterval(function() {{
            idx = (idx + 1) % path.length;
            marker.setLatLng(path[idx]);
          }}, 250);
        }})();
        </script>
        """
        m.get_root().script.add_child(folium.Element(ship_js))

    return m


# ---------------------------------------------------------------
# 5) หน้าเว็บ
# ---------------------------------------------------------------
st.set_page_config(page_title="Green Shipping Planner", layout="wide")
st.title("Thailand Ports – Green Shipping")
st.caption("DEMO: ข้อมูล เส้นทางเลี่ยง และค่าสมมติทั้งหมดยังไม่ใช่ข้อมูลจริง")

names = [p["name"] for p in PORTS]
by_name = {p["name"]: p for p in PORTS}

weather_list = get_all_weather(tuple((p["lat"], p["lon"]) for p in PORTS))
WX = {p["name"]: w for p, w in zip(PORTS, weather_list)}
RISK = {n: risk_level(w) for n, w in WX.items()}

with st.sidebar:
    st.header("ตัวกรอง")
    types = st.multiselect("ประเภท", options=list(TYPE_LABELS.keys()), default=list(TYPE_LABELS.keys()),
                            format_func=lambda k: TYPE_LABELS[k])
    color_by_weather = st.checkbox("สีหมุดตามสภาพอากาศ", value=False)
    show_ship = st.checkbox("แสดงไอคอนเรือเคลื่อนที่", value=True)

    st.header("เส้นทาง (OVRP)")
    depot_name = st.selectbox("จุดส่ง (Depot)", names)
    cust_names = st.multiselect("จุดรับ (Customers)", [n for n in names if n != depot_name])
    objective_label = st.selectbox("เป้าหมาย", list(OBJECTIVES.keys()), index=3)
    round_trip = st.checkbox("ไป-กลับ (Round trip)", value=False)

    route_choice = st.radio(
        "เส้นทางที่ใช้งาน",
        ["เส้นทางหลัก (ผ่านช่องแคบมะละกา)", "เส้นทางเลี่ยง (อ้อมช่องแคบซุนดา)"],
        help="เส้นทางเลี่ยงจะต่างจากเส้นทางหลักเฉพาะช่วงที่ข้ามฝั่งอ่าวไทย <-> อันดามัน",
    )

    with st.expander("ค่าสมมติของเรือ (DEMO)"):
        speed = st.slider("ความเร็วเรือ (น็อต)", 8, 18, 12)
        fuel_ref = st.number_input("น้ำมัน @14 น็อต (ตัน/วัน)", 5.0, 100.0, 25.0, step=1.0)
        fuel_price = st.number_input("ราคาน้ำมัน (USD/ตัน)", 100.0, 2000.0, 600.0, step=50.0)
        opex = st.number_input("ค่าใช้จ่ายเรือ (USD/วัน)", 0.0, 50000.0, 8000.0, step=500.0)
        ship_capacity = st.number_input("ความจุเรือ 1 ลำ (ตัน)", 100, 100000, 5000, step=100)
        horizon_days = st.number_input("กรอบเวลาวางแผน (วัน)", 1, 365, 30)

params = {"speed": float(speed), "fuel_ref": fuel_ref, "fuel_price": fuel_price, "opex": opex, "risk": RISK}
filtered = [p for p in PORTS if p["type"] in types]

order = None
primary_legs, alt_legs = [], []
if cust_names:
    depot = by_name[depot_name]
    customers = [by_name[n] for n in cust_names]
    key = OBJECTIVES[objective_label]
    with st.spinner("กำลังคำนวณเส้นทางเดินเรือ..."):
        order = best_order(depot, customers, key, params, round_trip)
        primary_legs, used_alt = resolve_legs(order, alt=False)
        alt_legs_raw, has_alt = resolve_legs(order, alt=True)
        alt_legs = alt_legs_raw if has_alt else []

selected_is_alt = route_choice.startswith("เส้นทางเลี่ยง")
if selected_is_alt and not alt_legs:
    st.sidebar.info("ทุกช่วงของเส้นทางนี้อยู่ฝั่งเดียวกัน จึงไม่มีเส้นทางเลี่ยงแบบอ้อมซุนดาให้เลือก ใช้เส้นทางหลักแทน")
    selected_is_alt = False

st_folium(
    build_map(filtered, by_name.get(depot_name) if cust_names else None,
              [by_name[n] for n in cust_names] if cust_names else [],
              primary_legs, alt_legs, bool(alt_legs), selected_is_alt,
              WX, RISK, color_by_weather, show_ship),
    height=520, returned_objects=[],
)

# ---------- ผลเส้นทาง ----------
if order:
    active_legs = alt_legs if (selected_is_alt and alt_legs) else primary_legs
    m1 = metrics_from_legs(active_legs, params)
    m2 = metrics_from_legs(primary_legs, params) if (selected_is_alt and alt_legs) else None

    st.subheader("ผลเส้นทาง")
    st.write(" → ".join(short(p["name"]) for p in order))
    if alt_legs:
        st.caption("เส้นทึบ = เส้นทางที่เลือกใช้งาน | เส้นประ = อีกทางเลือกหนึ่ง")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ระยะทาง", f"{m1['km']:,.0f} กม.")
    c2.metric("เวลา", f"{m1['days']:.1f} วัน")
    c3.metric("CO2", f"{m1['co2']:,.1f} ตัน")
    c4.metric("ต้นทุน", f"${m1['cost']:,.0f}")
    if m2:
        st.caption(
            f"เทียบเส้นทางหลัก: ระยะทาง {m1['km']-m2['km']:+,.0f} กม. | "
            f"เวลา {m1['days']-m2['days']:+.1f} วัน | CO2 {m1['co2']-m2['co2']:+,.1f} ตัน | "
            f"ต้นทุน ${m1['cost']-m2['cost']:+,.0f}"
        )

    stop_risks = [(short(p["name"]), RISK.get(p["name"], 0)) for p in order]
    red = [n for n, r in stop_risks if r == 2]
    yellow = [n for n, r in stop_risks if r == 1]
    if red:
        st.error("⛔ ไม่ควรออกเรือตอนนี้: อากาศรุนแรงที่ " + ", ".join(red))
    elif yellow:
        st.warning("⚠️ ควรระวัง: สภาพอากาศไม่ดีที่ " + ", ".join(yellow))
    else:
        st.success("✅ สภาพอากาศปัจจุบันเหมาะกับการเดินเรือตลอดเส้นทาง")

    # ---------- เปรียบเทียบเป้าหมาย ----------
    st.subheader("เปรียบเทียบเป้าหมาย")
    rows = []
    for label, k in OBJECTIVES.items():
        o = best_order(depot, customers, k, params, round_trip)
        legs_o, _ = resolve_legs(o, alt=selected_is_alt and bool(alt_legs))
        mm = metrics_from_legs(legs_o, params)
        rows.append({"เป้าหมาย": label, "ลำดับ": " → ".join(short(x["name"]) for x in o),
                      "กม.": round(mm["km"]), "วัน": round(mm["days"], 1),
                      "CO2 (ตัน)": round(mm["co2"], 1), "ต้นทุน (USD)": round(mm["cost"])})
    st.dataframe(pd.DataFrame(rows), hide_index=True)

    # ---------- พยากรณ์อากาศ + วันออกเรือที่ดีที่สุด ----------
    st.subheader("พยากรณ์อากาศ 7 วัน และวันออกเรือที่แนะนำ")
    stop_names = [p["name"] for p in order]
    fc_list = get_all_forecast(tuple((p["lat"], p["lon"]) for p in PORTS))
    FORECAST = {p["name"]: fc for p, fc in zip(PORTS, fc_list)}
    best_day, day_rows = best_departure_day(stop_names, FORECAST)
    if best_day:
        st.success(f"📅 วันที่แนะนำให้ออกเรือ: **{best_day['date']}** ({RISK_LABELS[best_day['risk']]})")
        st.dataframe(
            pd.DataFrame([{"วันที่": r["date"], "สถานะรวมของเส้นทาง": RISK_LABELS[r["risk"]]} for r in day_rows]),
            hide_index=True,
        )
        st.caption("ประเมินจากความเสี่ยงสูงสุดของทุกท่าที่เส้นทางผ่านในวันนั้น (ยังไม่รวมผลต่อค่าใช้จ่าย/CO2 ด้านบน)")
    else:
        st.info("ดึงพยากรณ์อากาศล่วงหน้าไม่สำเร็จในขณะนี้")

    # ---------- ประมาณจำนวนเรือ ----------
    st.subheader("ประมาณจำนวนเรือที่ต้องใช้ (คร่าวๆ)")
    st.caption("ยังไม่มีข้อมูลดีมานด์สินค้าจริง ตัวเลขนี้เป็นแค่การประมาณเพื่อ demo เท่านั้น")
    demand = {}
    dcols = st.columns(min(4, len(customers)))
    for i, c in enumerate(customers):
        with dcols[i % len(dcols)]:
            demand[c["name"]] = st.number_input(f"ดีมานด์ {short(c['name'])} (ตัน)", 0, 200000, 1000, step=100, key=f"d_{c['name']}")
    total_demand = sum(demand.values())
    ships_by_capacity = math.ceil(total_demand / ship_capacity) if ship_capacity else 0
    round_days = m1["days"] * (1 if round_trip else 2)  # ถ้าไม่ตั้งไป-กลับ ประมาณว่าต้องวิ่งกลับมาอีกเที่ยวเปล่า
    trips_per_ship = max(1, math.floor(horizon_days / round_days)) if round_days > 0 else 1
    ships_by_schedule = math.ceil(ships_by_capacity / trips_per_ship) if ships_by_capacity else 0

    e1, e2, e3 = st.columns(3)
    e1.metric("ดีมานด์รวม", f"{total_demand:,.0f} ตัน")
    e2.metric("เรือที่ต้องใช้ (ตามความจุ)", f"{ships_by_capacity} ลำ")
    e3.metric(f"เรือที่ต้องใช้ (ใน {horizon_days} วัน)", f"{ships_by_schedule} ลำ")
    st.caption(f"1 ลำวิ่งได้ประมาณ {trips_per_ship} เที่ยวใน {horizon_days} วัน (รอบละ ~{round_days:.1f} วัน รวมไป-กลับ)")

# ---------- สภาพอากาศแต่ละท่า ----------
st.subheader("สภาพอากาศแต่ละท่า (live)")
wrows = [{"ท่าเรือ": p["name"], "ลม (กม./ชม.)": WX[p["name"]]["wind"], "คลื่น (ม.)": WX[p["name"]]["wave"],
          "ฝน (มม.)": WX[p["name"]]["rain"], "สถานะ": RISK_LABELS[RISK[p["name"]]]} for p in filtered]
if wrows:
    st.dataframe(pd.DataFrame(wrows), hide_index=True)
st.caption("ข้อมูลอากาศจาก Open-Meteo | เกณฑ์เตือนและเส้นทางเลี่ยงเป็นค่าสมมติสำหรับ DEMO")

st.subheader("รายการท่าเรือ")
show_df = pd.DataFrame(filtered)
if not show_df.empty:
    show_df["type"] = show_df["type"].map(TYPE_LABELS)
    st.dataframe(show_df, hide_index=True)
