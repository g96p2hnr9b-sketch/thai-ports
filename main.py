"""
Thailand Ports Map - เว็บแผนที่ท่าเรือ (สไตล์ Searates)

เวอร์ชันนี้:
- ใช้ไลบรารี searoute คำนวณ "เส้นทางเดินเรือจริง" (อ้อมแหลม ผ่านช่องแคบ ไม่ตัดผ่านแผ่นดิน)
- เปลี่ยนแผนที่พื้นหลังเป็น OpenStreetMap (ไม่ต้องใช้ API key)
- แก้สีตัวหนังสือใน legend

หมายเหตุ: พิกัดท่าเรือเป็นค่าประมาณ ควรตรวจกับแหล่งข้อมูลจริงและอ้างอิงในรายงาน
"""

import math
from functools import lru_cache

import folium
import pandas as pd
import searoute as sr
import streamlit as st
from streamlit_folium import st_folium

# ---------------------------------------------------------------
# 1) ข้อมูลท่าเรือ
#    type: "sea" = ท่าเรือทะเล/แม่น้ำ, "container" = ตู้คอนเทนเนอร์
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

COLORS = {"sea": "#0b0f3b", "container": "#e0405e"}
LABELS = {"sea": "ท่าเรือทะเล / ท่าเรือแม่น้ำ", "container": "ตู้คอนเทนเนอร์"}


# ---------------------------------------------------------------
# 2) ระยะทาง
# ---------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    """ระยะเส้นตรง (ใช้สำรองเมื่อคำนวณเส้นทางเรือไม่ได้)"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@lru_cache(maxsize=None)
def sea_leg(lat1, lon1, lat2, lon2):
    """
    คืนค่า (พิกัดเส้นทาง [[lat, lon], ...], ระยะทาง กม., เป็นเส้นทางเรือจริงหรือไม่)
    searoute ใช้รูปแบบ [lon, lat]
    """
    try:
        r = sr.searoute([lon1, lat1], [lon2, lat2], units="km")
        coords = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
        km = float(r["properties"]["length"])
        return coords, km, True
    except Exception:
        km = haversine_km(lat1, lon1, lat2, lon2)
        return [[lat1, lon1], [lat2, lon2]], km, False


def leg(a, b):
    return sea_leg(a["lat"], a["lon"], b["lat"], b["lon"])


def solve_route(depot, customers):
    """
    ตัวอย่าง solver แบบ Nearest Neighbor สำหรับ OVRP (ไม่กลับ depot)
    ใช้ระยะทางเดินเรือจริง ภายหลังเปลี่ยนเป็น solver ของทีมได้
    """
    route, current, left = [depot], depot, list(customers)
    while left:
        nxt = min(left, key=lambda c: leg(current, c)[1])
        route.append(nxt)
        left.remove(nxt)
        current = nxt
    return route


# ---------------------------------------------------------------
# 3) แผนที่
# ---------------------------------------------------------------
def build_map(ports, path=None):
    m = folium.Map(location=[10.5, 100.5], zoom_start=6, tiles="OpenStreetMap")

    for p in ports:
        folium.CircleMarker(
            location=[p["lat"], p["lon"]],
            radius=8,
            color="white",
            weight=2,
            fill=True,
            fill_color=COLORS[p["type"]],
            fill_opacity=1,
            tooltip=p["name"],
            popup=folium.Popup(
                f"<b>{p['name']}</b><br>{LABELS[p['type']]}<br>"
                f"{p['lat']:.4f}, {p['lon']:.4f}",
                max_width=250,
            ),
        ).add_to(m)

    if path:
        folium.PolyLine(path, color="#2f6fed", weight=4, opacity=0.85).add_to(m)

    legend = """
    <div style="position: fixed; top: 12px; left: 50px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,.25); font-size: 13px; color: #222;">
      <b>Thailand</b><br>
      <span style="color:#0b0f3b;">&#9679;</span> ท่าเรือทะเล / ท่าเรือแม่น้ำ<br>
      <span style="color:#e0405e;">&#9679;</span> ตู้คอนเทนเนอร์
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    return m


# ---------------------------------------------------------------
# 4) หน้าเว็บ (Streamlit)
# ---------------------------------------------------------------
st.set_page_config(page_title="Thailand Ports", layout="wide")
st.title("Thailand Ports")

df = pd.DataFrame(PORTS)

with st.sidebar:
    st.header("ตัวกรอง")
    types = st.multiselect(
        "ประเภท",
        options=list(LABELS.keys()),
        default=list(LABELS.keys()),
        format_func=lambda k: LABELS[k],
    )
    st.header("เส้นทาง (ตัวอย่าง OVRP)")
    names = df["name"].tolist()
    depot_name = st.selectbox("จุดส่ง (Depot)", names)
    cust_names = st.multiselect(
        "จุดรับ (Customers)", [n for n in names if n != depot_name]
    )
    show_route = st.checkbox("แสดงเส้นทาง", value=True)

filtered = [p for p in PORTS if p["type"] in types]

route, path, total, all_real = None, None, 0.0, True
if show_route and cust_names:
    depot = next(p for p in PORTS if p["name"] == depot_name)
    customers = [p for p in PORTS if p["name"] in cust_names]
    route = solve_route(depot, customers)

    path = []
    for a, b in zip(route[:-1], route[1:]):
        coords, km, real = leg(a, b)
        path.extend(coords)
        total += km
        all_real = all_real and real

st_folium(build_map(filtered, path), height=520, returned_objects=[])

if route:
    st.subheader("ผลเส้นทาง")
    st.write(" → ".join(p["name"] for p in route))
    label = "ระยะทางรวม (เดินเรือ)" if all_real else "ระยะทางรวม (เส้นตรง - สำรอง)"
    st.metric(label, f"{total:,.1f} กม.")
    if not all_real:
        st.warning("คำนวณเส้นทางเรือบางช่วงไม่ได้ จึงใช้ระยะเส้นตรงแทน")

st.subheader("รายการท่าเรือ")
show_df = pd.DataFrame(filtered)
if not show_df.empty:
    show_df["type"] = show_df["type"].map(LABELS)
    st.dataframe(show_df, hide_index=True)
