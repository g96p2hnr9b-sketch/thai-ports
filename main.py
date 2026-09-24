"""
Thailand Ports - Green Shipping Route Planner (DEMO)

ฟีเจอร์:
1) เส้นทางเดินเรือจริง (searoute)
2) เลือกลำดับท่าเรือที่ดีที่สุดตามเป้าหมาย: ระยะสั้นสุด / เร็วสุด / ต้นทุนต่ำสุด / CO2 ต่ำสุด
3) คำนวณน้ำมัน ต้นทุน และ CO2 (ค่าสมมติฐานปรับได้ในแถบด้านข้าง)
4) สภาพอากาศ + คลื่นแบบ live จาก Open-Meteo แล้วประเมินความเสี่ยงต่อการเดินเรือ
5) เปรียบเทียบความเร็วเรือ (slow steaming)

** เป็น DEMO ** ค่าพิกัด ค่าเชื้อเพลิง และเกณฑ์สภาพอากาศเป็นค่าสมมติ ไม่ใช่ข้อมูลจริง
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
# 1) ข้อมูลท่าเรือ (DEMO - พิกัดโดยประมาณ)
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
# ค่าประมาณว่าเรือช้าลงเท่าไรเมื่ออากาศแย่ (DEMO)
RISK_SPEED_FACTOR = {0: 1.0, 1: 0.85, 2: 0.6}

CO2_PER_TON_FUEL = 3.114  # ตัน CO2 ต่อตันน้ำมัน HFO (ค่าอ้างอิง IMO)
REF_SPEED_KN = 14.0  # ความเร็วอ้างอิงที่ใช้กำหนดอัตราสิ้นเปลือง

OBJECTIVES = {
    "ระยะทางสั้นที่สุด": "km",
    "เร็วที่สุด": "days",
    "ต้นทุนต่ำสุด": "cost",
    "CO2 ต่ำสุด": "co2",
}


def short(name):
    return name.split(" (")[0]


# ---------------------------------------------------------------
# 2) เส้นทางเดินเรือ
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
    """(พิกัดเส้นทาง [[lat, lon]...], กม., เป็นเส้นทางเรือจริงหรือไม่)"""
    try:
        r = sr.searoute([lon1, lat1], [lon2, lat2], units="km")
        coords = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
        return coords, float(r["properties"]["length"]), True
    except Exception:
        return [[lat1, lon1], [lat2, lon2]], haversine_km(lat1, lon1, lat2, lon2), False


def leg(a, b):
    """ระยะทางสมมาตร จึงเก็บแคชทิศเดียวแล้วกลับพิกัดเอา"""
    if (a["lat"], a["lon"]) <= (b["lat"], b["lon"]):
        return sea_leg(a["lat"], a["lon"], b["lat"], b["lon"])
    coords, km, real = sea_leg(b["lat"], b["lon"], a["lat"], a["lon"])
    return coords[::-1], km, real


# ---------------------------------------------------------------
# 3) สภาพอากาศ live (Open-Meteo - ฟรี ไม่ต้องใช้ key)
# ---------------------------------------------------------------
def _fetch_one(latlon):
    lat, lon = latlon
    out = {"wind": None, "rain": None, "code": None, "wave": None}
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "weather_code,wind_speed_10m,precipitation",
                "wind_speed_unit": "kmh",
                "timezone": "Asia/Bangkok",
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
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "wave_height",
                "timezone": "Asia/Bangkok",
            },
            timeout=8,
        )
        out["wave"] = r.json()["current"].get("wave_height")
    except Exception:
        pass
    return out


@st.cache_data(ttl=900, show_spinner="กำลังดึงสภาพอากาศล่าสุด...")
def get_all_weather(coords):
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_fetch_one, coords))


def risk_level(w):
    """เกณฑ์ DEMO: 0 ปกติ, 1 ระวัง, 2 ไม่ควรออกเรือ"""
    wind = w["wind"] or 0
    wave = w["wave"] or 0
    rain = w["rain"] or 0
    code = w["code"]
    if code in (95, 96, 99) or wind >= 45 or wave >= 3.0:
        return 2
    if wind >= 30 or wave >= 2.0 or rain >= 2.5 or code in (63, 65, 81, 82):
        return 1
    return 0


# ---------------------------------------------------------------
# 4) คำนวณ ระยะทาง เวลา น้ำมัน CO2 ต้นทุน
# ---------------------------------------------------------------
def route_metrics(order, p):
    km = days = fuel = 0.0
    fuel_per_day = p["fuel_ref"] * (p["speed"] / REF_SPEED_KN) ** 3  # กฎกำลังสาม
    for a, b in zip(order[:-1], order[1:]):
        _, d, _ = leg(a, b)
        risk = max(p["risk"].get(a["name"], 0), p["risk"].get(b["name"], 0))
        eff_speed = p["speed"] * RISK_SPEED_FACTOR[risk]  # อากาศแย่ เรือช้าลงแต่เครื่องยังกินน้ำมันเท่าเดิม
        t = d / (1.852 * eff_speed * 24)
        km += d
        days += t
        fuel += fuel_per_day * t
    co2 = fuel * CO2_PER_TON_FUEL
    cost = fuel * p["fuel_price"] + days * p["opex"]
    return {"km": km, "days": days, "fuel": fuel, "co2": co2, "cost": cost}


def best_order(depot, customers, key, p):
    """OVRP: เริ่มที่ depot ไม่ต้องกลับ ลองทุกลำดับถ้าจุดไม่เกิน 7"""
    if len(customers) <= 7:
        best = min(
            itertools.permutations(customers),
            key=lambda perm: route_metrics([depot, *perm], p)[key],
        )
        return [depot, *best]
    route, cur, left = [depot], depot, list(customers)
    while left:
        nxt = min(left, key=lambda c: route_metrics([cur, c], p)[key])
        route.append(nxt)
        left.remove(nxt)
        cur = nxt
    return route


def route_path(order):
    path, all_real = [], True
    for a, b in zip(order[:-1], order[1:]):
        coords, _, real = leg(a, b)
        path.extend(coords)
        all_real = all_real and real
    return path, all_real


# ---------------------------------------------------------------
# 5) แผนที่
# ---------------------------------------------------------------
def build_map(ports, path, wx, risk, color_by_weather):
    m = folium.Map(location=[10.5, 100.5], zoom_start=6, tiles="OpenStreetMap")

    for p in ports:
        w = wx[p["name"]]
        color = RISK_COLORS[risk[p["name"]]] if color_by_weather else TYPE_COLORS[p["type"]]
        wave = "-" if w["wave"] is None else f"{w['wave']:.1f} ม."
        wind = "-" if w["wind"] is None else f"{w['wind']:.0f} กม./ชม."
        rain = "-" if w["rain"] is None else f"{w['rain']:.1f} มม."
        folium.CircleMarker(
            location=[p["lat"], p["lon"]],
            radius=8,
            color="white",
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=1,
            tooltip=p["name"],
            popup=folium.Popup(
                f"<b>{p['name']}</b><br>{TYPE_LABELS[p['type']]}<br>"
                f"ลม {wind} | คลื่น {wave} | ฝน {rain}<br>"
                f"{RISK_LABELS[risk[p['name']]]}",
                max_width=260,
            ),
        ).add_to(m)

    if path:
        folium.PolyLine(path, color="#2f6fed", weight=4, opacity=0.85).add_to(m)

    if color_by_weather:
        legend_body = (
            '<span style="color:#2e9e5b;">&#9679;</span> เดินเรือได้ปกติ<br>'
            '<span style="color:#f0a500;">&#9679;</span> ระวัง<br>'
            '<span style="color:#d62828;">&#9679;</span> ไม่ควรออกเรือ'
        )
    else:
        legend_body = (
            '<span style="color:#0b0f3b;">&#9679;</span> ท่าเรือทะเล / ท่าเรือแม่น้ำ<br>'
            '<span style="color:#e0405e;">&#9679;</span> ตู้คอนเทนเนอร์'
        )
    legend = f"""
    <div style="position: fixed; top: 12px; left: 50px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,.25); font-size: 13px; color: #222;">
      <b>Thailand</b><br>{legend_body}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    return m


# ---------------------------------------------------------------
# 6) หน้าเว็บ
# ---------------------------------------------------------------
st.set_page_config(page_title="Green Shipping Planner", layout="wide")
st.title("Thailand Ports – Green Shipping")
st.caption("DEMO: ข้อมูลและค่าสมมติทั้งหมดยังไม่ใช่ข้อมูลจริง")

names = [p["name"] for p in PORTS]
by_name = {p["name"]: p for p in PORTS}

# สภาพอากาศ live
weather_list = get_all_weather(tuple((p["lat"], p["lon"]) for p in PORTS))
WX = {p["name"]: w for p, w in zip(PORTS, weather_list)}
RISK = {n: risk_level(w) for n, w in WX.items()}

with st.sidebar:
    st.header("ตัวกรอง")
    types = st.multiselect(
        "ประเภท",
        options=list(TYPE_LABELS.keys()),
        default=list(TYPE_LABELS.keys()),
        format_func=lambda k: TYPE_LABELS[k],
    )
    color_by_weather = st.checkbox("สีหมุดตามสภาพอากาศ", value=False)

    st.header("เส้นทาง (OVRP)")
    depot_name = st.selectbox("จุดส่ง (Depot)", names)
    cust_names = st.multiselect(
        "จุดรับ (Customers)", [n for n in names if n != depot_name]
    )
    objective_label = st.selectbox("เป้าหมาย", list(OBJECTIVES.keys()), index=3)

    with st.expander("ค่าสมมติของเรือ (DEMO)"):
        speed = st.slider("ความเร็วเรือ (น็อต)", 8, 18, 12)
        cargo = st.number_input("น้ำหนักสินค้า (ตัน)", 100, 50000, 5000, step=100)
        fuel_ref = st.number_input("น้ำมัน @14 น็อต (ตัน/วัน)", 5.0, 100.0, 25.0, step=1.0)
        fuel_price = st.number_input("ราคาน้ำมัน (USD/ตัน)", 100.0, 2000.0, 600.0, step=50.0)
        opex = st.number_input("ค่าใช้จ่ายเรือ (USD/วัน)", 0.0, 50000.0, 8000.0, step=500.0)

params = {
    "speed": float(speed),
    "cargo": cargo,
    "fuel_ref": fuel_ref,
    "fuel_price": fuel_price,
    "opex": opex,
    "risk": RISK,
}

filtered = [p for p in PORTS if p["type"] in types]

order, path, all_real = None, None, True
if cust_names:
    depot = by_name[depot_name]
    customers = [by_name[n] for n in cust_names]
    with st.spinner("กำลังคำนวณเส้นทางเดินเรือ..."):
        key = OBJECTIVES[objective_label]
        order = best_order(depot, customers, key, params)
        path, all_real = route_path(order)

st_folium(
    build_map(filtered, path, WX, RISK, color_by_weather),
    height=520,
    returned_objects=[],
)

# ---------- ผลเส้นทาง ----------
if order:
    st.subheader("ผลเส้นทาง")
    st.write(" → ".join(short(p["name"]) for p in order))

    m = route_metrics(order, params)
    baseline = route_metrics([depot, *customers], params)  # ลำดับตามที่เลือกมา

    c1, c2 = st.columns(2)
    c1.metric("ระยะทาง", f"{m['km']:,.0f} กม.")
    c2.metric("เวลา", f"{m['days']:.1f} วัน")
    c3, c4 = st.columns(2)
    c3.metric(
        "CO2",
        f"{m['co2']:,.1f} ตัน",
        delta=f"{m['co2'] - baseline['co2']:,.1f} ตัน เทียบลำดับที่เลือก",
        delta_color="inverse",
    )
    c4.metric(
        "ต้นทุน",
        f"${m['cost']:,.0f}",
        delta=f"${m['cost'] - baseline['cost']:,.0f}",
        delta_color="inverse",
    )
    intensity = m["co2"] * 1e6 / (params["cargo"] * m["km"]) if m["km"] else 0
    st.caption(f"น้ำมันรวม {m['fuel']:,.1f} ตัน | ความเข้มคาร์บอน {intensity:,.1f} g CO2/ตัน-กม.")
    if not all_real:
        st.warning("คำนวณเส้นทางเรือบางช่วงไม่ได้ จึงใช้ระยะเส้นตรงแทน")

    # เตือนสภาพอากาศตามจุดที่ผ่าน
    stop_risks = [(short(p["name"]), RISK[p["name"]]) for p in order]
    red = [n for n, r in stop_risks if r == 2]
    yellow = [n for n, r in stop_risks if r == 1]
    if red:
        st.error("⛔ ไม่ควรออกเรือ: อากาศรุนแรงที่ " + ", ".join(red))
    elif yellow:
        st.warning("⚠️ ควรระวัง: สภาพอากาศไม่ดีที่ " + ", ".join(yellow))
    else:
        st.success("✅ สภาพอากาศตลอดเส้นทางเหมาะกับการเดินเรือ")

    # ---------- เปรียบเทียบเป้าหมาย ----------
    st.subheader("เปรียบเทียบเป้าหมาย")
    rows = []
    for label, k in OBJECTIVES.items():
        o = best_order(depot, customers, k, params)
        mm = route_metrics(o, params)
        rows.append(
            {
                "เป้าหมาย": label,
                "ลำดับ": " → ".join(short(x["name"]) for x in o),
                "กม.": round(mm["km"]),
                "วัน": round(mm["days"], 1),
                "น้ำมัน (ตัน)": round(mm["fuel"], 1),
                "CO2 (ตัน)": round(mm["co2"], 1),
                "ต้นทุน (USD)": round(mm["cost"]),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True)

    # ---------- เปรียบเทียบความเร็ว ----------
    st.subheader("ความเร็วเรือ กับ CO2 (slow steaming)")
    srows = []
    for sp in (8, 10, 12, 14, 16, 18):
        pp = {**params, "speed": float(sp)}
        mm = route_metrics(order, pp)
        srows.append(
            {
                "น็อต": sp,
                "วัน": round(mm["days"], 1),
                "น้ำมัน (ตัน)": round(mm["fuel"], 1),
                "CO2 (ตัน)": round(mm["co2"], 1),
                "ต้นทุน (USD)": round(mm["cost"]),
            }
        )
    st.dataframe(pd.DataFrame(srows), hide_index=True)
    st.caption("เรือช้าลง CO2 ลดลงมาก แต่ใช้เวลานานขึ้น และมีค่าใช้จ่ายรายวันเพิ่ม")

# ---------- สภาพอากาศแต่ละท่า ----------
st.subheader("สภาพอากาศแต่ละท่า (live)")
wrows = []
for p in filtered:
    w = WX[p["name"]]
    wrows.append(
        {
            "ท่าเรือ": p["name"],
            "ลม (กม./ชม.)": w["wind"],
            "คลื่น (ม.)": w["wave"],
            "ฝน (มม.)": w["rain"],
            "สถานะ": RISK_LABELS[RISK[p["name"]]],
        }
    )
if wrows:
    st.dataframe(pd.DataFrame(wrows), hide_index=True)
st.caption(
    "ข้อมูลอากาศจาก Open-Meteo (อัปเดตทุก 15 นาที) | เกณฑ์เตือนเป็นค่าสมมติสำหรับ DEMO | "
    "ค่า '-' คือไม่มีข้อมูลจุดนั้น"
)

st.subheader("รายการท่าเรือ")
show_df = pd.DataFrame(filtered)
if not show_df.empty:
    show_df["type"] = show_df["type"].map(TYPE_LABELS)
    st.dataframe(show_df, hide_index=True)
