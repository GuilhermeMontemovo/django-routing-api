"""
Gera um mapa interativo da rota com paradas de combustível.
Uso: python test_map.py [start_lat,start_lon end_lat,end_lon]
"""
import sys
import folium
import requests

# ---------------------------------------------------------------------------
# Configuração da rota
# ---------------------------------------------------------------------------
DEFAULT_START = "41.8781,-87.6298"   # Chicago
DEFAULT_END = "29.7604,-95.3698"     # Houston

start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
end = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_END

print(f"Buscando rota: {start} → {end} ...")
r = requests.get("http://localhost:8000/api/route/", params={"start": start, "end": end})
if r.status_code != 200:
    print(f"Erro {r.status_code}: {r.text}")
    sys.exit(1)

data = r.json()
stops = data["stops"]
total_miles = data["total_miles"]
total_cost = data["total_fuel_cost"]
total_gallons = data["total_gallons"]
mpg = data["mpg_used"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def color_by_price(price, prices):
    """Verde = barato, vermelho = caro (relativo aos stops escolhidos)."""
    if not prices:
        return "red"
    mn, mx = min(prices), max(prices)
    if mx == mn:
        return "orange"
    ratio = (price - mn) / (mx - mn)
    if ratio < 0.33:
        return "green"
    elif ratio < 0.66:
        return "orange"
    return "red"


# Precomputar preços dos stops
stop_prices = [s["price"] for s in stops]

# Pontos-chave: Start + Stops + End (usando mileage da rota, não haversine)
start_lat, start_lon = map(float, start.split(","))
end_lat, end_lon = map(float, end.split(","))

waypoints = [
    {"label": "Start", "lat": start_lat, "lon": start_lon, "mileage": 0}
]
for s in stops:
    waypoints.append({
        "label": s["name"],
        "lat": s["lat"],
        "lon": s["lon"],
        "mileage": s.get("mileage", 0),
    })
waypoints.append({
    "label": "End",
    "lat": end_lat,
    "lon": end_lon,
    "mileage": total_miles,
})

# Distância entre waypoints consecutivos (pela rota, via mileage)
for i in range(1, len(waypoints)):
    prev_mi = waypoints[i - 1].get("mileage", 0) or 0
    cur_mi = waypoints[i].get("mileage", 0) or 0
    waypoints[i]["dist_from_prev"] = cur_mi - prev_mi

# ---------------------------------------------------------------------------
# Criar mapa
# ---------------------------------------------------------------------------
coords = data["route_geojson"]["geometry"]["coordinates"]
mid = coords[len(coords) // 2]
m = folium.Map(location=[mid[1], mid[0]], zoom_start=6, tiles="CartoDB positron")

# Rota
folium.GeoJson(
    data["route_geojson"],
    style_function=lambda x: {"color": "#2563eb", "weight": 5, "opacity": 0.8},
    name="Rota",
).add_to(m)

# ---------------------------------------------------------------------------
# Marcador Start
# ---------------------------------------------------------------------------
folium.Marker(
    [start_lat, start_lon],
    icon=folium.Icon(color="green", icon="play", prefix="fa"),
    popup=folium.Popup(f"""
        <div style="font-family:system-ui;min-width:180px">
            <h4 style="margin:0 0 6px;color:#16a34a">🟢 Partida</h4>
            <b>Coordenadas:</b> {start_lat:.4f}, {start_lon:.4f}<br>
            <b>Rota total:</b> {total_miles:.0f} mi
        </div>
    """, max_width=300),
).add_to(m)

# ---------------------------------------------------------------------------
# Marcadores dos Stops com detalhes
# ---------------------------------------------------------------------------
for i, s in enumerate(stops):
    wp = waypoints[i + 1]  # +1 porque waypoints[0] é Start
    dist_prev = wp.get("dist_from_prev", 0)

    # Distância até o próximo stop (ou End)
    if i + 2 < len(waypoints):
        dist_next = waypoints[i + 2].get("dist_from_prev", 0)
    else:
        dist_next = 0

    icon_color = color_by_price(s["price"], stop_prices)
    stop_num = i + 1

    popup_html = f"""
    <div style="font-family:system-ui;min-width:220px;font-size:13px">
        <h4 style="margin:0 0 8px;color:#dc2626">⛽ Parada {stop_num}/{len(stops)}</h4>
        <table style="border-collapse:collapse;width:100%">
            <tr><td style="padding:2px 8px 2px 0;color:#666">Posto</td>
                <td style="padding:2px 0"><b>{s['name']}</b></td></tr>
            <tr><td style="padding:2px 8px 2px 0;color:#666">Endereço</td>
                <td style="padding:2px 0">{s.get('address', 'N/A')}</td></tr>
            <tr><td colspan="2"><hr style="margin:4px 0;border-color:#eee"></td></tr>
            <tr><td style="padding:2px 8px 2px 0;color:#666">Preço/gal</td>
                <td style="padding:2px 0"><b>${s['price']:.3f}</b></td></tr>
            <tr><td style="padding:2px 8px 2px 0;color:#666">Galões</td>
                <td style="padding:2px 0">{s['gallons']:.1f} gal</td></tr>
            <tr><td style="padding:2px 8px 2px 0;color:#666">Custo</td>
                <td style="padding:2px 0"><b>${s['cost']:.2f}</b></td></tr>
            <tr><td colspan="2"><hr style="margin:4px 0;border-color:#eee"></td></tr>
            <tr><td style="padding:2px 8px 2px 0;color:#666">Dist. anterior</td>
                <td style="padding:2px 0">{dist_prev:.0f} mi</td></tr>
            <tr><td style="padding:2px 8px 2px 0;color:#666">Dist. próximo</td>
                <td style="padding:2px 0">{dist_next:.0f} mi</td></tr>
            <tr><td style="padding:2px 8px 2px 0;color:#666">Milha na rota</td>
                <td style="padding:2px 0">~{s.get('mileage', 0):.0f} mi</td></tr>
        </table>
    </div>
    """
    folium.Marker(
        location=[s["lat"], s["lon"]],
        icon=folium.Icon(color=icon_color, icon="gas-pump", prefix="fa"),
        popup=folium.Popup(popup_html, max_width=320),
        tooltip=f"#{stop_num} {s['name']} — ${s['price']:.3f}/gal",
    ).add_to(m)

    # Linha tracejada do stop anterior ao stop atual
    if i == 0:
        prev_lat, prev_lon = start_lat, start_lon
    else:
        prev_lat, prev_lon = stops[i - 1]["lat"], stops[i - 1]["lon"]

    folium.PolyLine(
        [[prev_lat, prev_lon], [s["lat"], s["lon"]]],
        color="#f97316", weight=2, dash_array="8",
        opacity=0.6,
        tooltip=f"{dist_prev:.0f} mi",
    ).add_to(m)

# Linha tracejada do último stop ao End
if stops:
    last = stops[-1]
    folium.PolyLine(
        [[last["lat"], last["lon"]], [end_lat, end_lon]],
        color="#f97316", weight=2, dash_array="8",
        opacity=0.6,
        tooltip=f"{waypoints[-1].get('dist_from_prev', 0):.0f} mi",
    ).add_to(m)

# ---------------------------------------------------------------------------
# Marcador End
# ---------------------------------------------------------------------------
folium.Marker(
    [end_lat, end_lon],
    icon=folium.Icon(color="black", icon="flag-checkered", prefix="fa"),
    popup=folium.Popup(f"""
        <div style="font-family:system-ui;min-width:180px">
            <h4 style="margin:0 0 6px">🏁 Destino</h4>
            <b>Coordenadas:</b> {end_lat:.4f}, {end_lon:.4f}
        </div>
    """, max_width=300),
).add_to(m)

# ---------------------------------------------------------------------------
# Painel de resumo (canto superior direito)
# ---------------------------------------------------------------------------
stops_summary_rows = ""
for i, s in enumerate(stops):
    wp = waypoints[i + 1]
    stops_summary_rows += f"""
    <tr>
        <td style="padding:3px 6px">{i+1}</td>
        <td style="padding:3px 6px">{s['name'][:25]}</td>
        <td style="padding:3px 6px;text-align:right">${s['price']:.3f}</td>
        <td style="padding:3px 6px;text-align:right">{s['gallons']:.1f}</td>
        <td style="padding:3px 6px;text-align:right">${s['cost']:.2f}</td>
        <td style="padding:3px 6px;text-align:right">{wp.get('dist_from_prev',0):.0f}</td>
    </tr>"""

summary_html = f"""
<div style="
    position:fixed;top:10px;right:10px;z-index:9999;
    background:white;padding:14px 18px;border-radius:8px;
    box-shadow:0 2px 12px rgba(0,0,0,0.15);font-family:system-ui;font-size:12px;
    max-height:90vh;overflow-y:auto;min-width:420px;
">
    <h3 style="margin:0 0 10px;font-size:15px">📍 Resumo da Rota</h3>
    <table style="margin-bottom:10px;font-size:12px">
        <tr><td style="color:#666;padding-right:12px">Distância total</td>
            <td><b>{total_miles:.0f} milhas</b></td></tr>
        <tr><td style="color:#666;padding-right:12px">Paradas</td>
            <td><b>{len(stops)}</b></td></tr>
        <tr><td style="color:#666;padding-right:12px">Galões totais</td>
            <td><b>{total_gallons:.1f} gal</b></td></tr>
        <tr><td style="color:#666;padding-right:12px">Custo total</td>
            <td><b style="color:#dc2626">${total_cost:.2f}</b></td></tr>
        <tr><td style="color:#666;padding-right:12px">Consumo</td>
            <td>{mpg} MPG</td></tr>
        <tr><td style="color:#666;padding-right:12px">Custo/milha</td>
            <td>${total_cost/total_miles:.3f}</td></tr>
    </table>

    <table style="width:100%;border-collapse:collapse;font-size:11px">
        <thead>
            <tr style="background:#f1f5f9;font-weight:600">
                <th style="padding:4px 6px;text-align:left">#</th>
                <th style="padding:4px 6px;text-align:left">Posto</th>
                <th style="padding:4px 6px;text-align:right">$/gal</th>
                <th style="padding:4px 6px;text-align:right">Gal</th>
                <th style="padding:4px 6px;text-align:right">Custo</th>
                <th style="padding:4px 6px;text-align:right">Dist</th>
            </tr>
        </thead>
        <tbody>{stops_summary_rows}</tbody>
    </table>
    <div style="margin-top:8px;color:#999;font-size:10px">
        🟢 barato &nbsp; 🟠 médio &nbsp; 🔴 caro (relativo)
    </div>
</div>
"""

m.get_root().html.add_child(folium.Element(summary_html))

# Layer control
folium.LayerControl().add_to(m)

# ---------------------------------------------------------------------------
# Salvar
# ---------------------------------------------------------------------------
m.save("route_map.html")
print(f"\n✅ Mapa salvo em route_map.html")
print(f"   Rota: {start} → {end}")
print(f"   Distância: {total_miles:.0f} mi | Paradas: {len(stops)} | Custo: ${total_cost:.2f}")
print(f"   Abra no browser para visualizar!")
