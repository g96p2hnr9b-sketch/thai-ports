"""
Thailand Ports Map - เว็บแผนที่ท่าเรือ (สไตล์ Searates)
รัน:  streamlit run app.py

ติดตั้ง:  pip install streamlit folium streamlit-folium pandas

หมายเหตุ: พิกัดด้านล่างเป็นค่าประมาณ สำหรับใช้ตัวอย่างเท่านั้น
          ควรตรวจสอบกับแหล่งข้อมูลจริง (เช่น การท่าเรือแห่งประเทศไทย) แล้วอ้างอิงในรายงาน
"""

import math

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

# ---------------------------------------------------------------
# 1) ข้อมูลท่าเรือ (แก้/เพิ่มได้ หรือโหลดจาก ports.csv แทน)
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


def haversine_km(a, b):
    """ระยะทางเส้นตรงบนผิวโลก (กม.)"""
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dphi = p2 - p1
    dlmb = math.radians(b["lon"] - a["lon"])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def solve_route(depot, customers):
    """
    ตัวอย่าง solver แบบ Nearest Neighbor สำหรับ OVRP (ไม่ต้องกลับ depot)
    ภายหลังเปลี่ยนเป็น solver ของทีม (OR-Tools ฯลฯ) ได้ โดยคืนค่า list ของท่าตามลำดับ
    """
    route, current, left = [depot], depot, list(customers)
    while left:
        nxt = min(left, key=lambda c: haversine_km(current, c))
        route.append(nxt)
        left.remove(nxt)
        current = nxt
    return route


def build_map(ports, route=None):
    m = folium.Map(location=[13.0, 100.5], zoom_start=6, tiles="CartoDB positron")

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

    if route and len(route) > 1:
        folium.PolyLine(
            [[p["lat"], p["lon"]] for p in route],
            color="#2f6fed",
            weight=4,
            opacity=0.8,
        ).add_to(m)

    legend = """
    <div style="position: fixed; top: 12px; left: 50px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,.25); font-size: 13px;">
      <b>Thailand</b><br>
      <span style="color:#0b0f3b;">&#9679;</span> ท่าเรือทะเล / ท่าเรือแม่น้ำ<br>
      <span style="color:#e0405e;">&#9679;</span> ตู้คอนเทนเนอร์
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    return m


# ---------------------------------------------------------------
# 2) หน้าเว็บ (Streamlit)
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

route = None
if show_route and cust_names:
    depot = next(p for p in PORTS if p["name"] == depot_name)
    customers = [p for p in PORTS if p["name"] in cust_names]
    route = solve_route(depot, customers)

st_folium(build_map(filtered, route), height=520, returned_objects=[])

if route:
    total = sum(haversine_km(route[i], route[i + 1]) for i in range(len(route) - 1))
    st.subheader("ผลเส้นทาง")
    st.write(" → ".join(p["name"] for p in route))
    st.metric("ระยะทางรวม (เส้นตรง)", f"{total:,.1f} กม.")

st.subheader("รายการท่าเรือ")
show_df = pd.DataFrame(filtered)
if not show_df.empty:
    show_df["type"] = show_df["type"].map(LABELS)
    st.dataframe(show_df, hide_index=True)
