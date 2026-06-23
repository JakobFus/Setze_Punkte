import streamlit as st
import requests
import math
import csv
import time
import os
import tempfile
import zipfile
import io
from pyproj import Transformer
import geopandas as gpd
from shapely.geometry import Point

# --- Konfiguration Transformer ---
EPSG_CODE = "EPSG:25832"
transformer_zu_gps = Transformer.from_crs(EPSG_CODE, "EPSG:4326", always_xy=True)
transformer_zu_utm = Transformer.from_crs("EPSG:4326", EPSG_CODE, always_xy=True)

def utm_zu_gps(ost, nord):
    return transformer_zu_gps.transform(ost, nord)

def gps_zu_utm(lat, lon):
    ost, nord = transformer_zu_utm.transform(lon, lat)
    return int(round(ost)), int(round(nord))

# --- Math Hilfsfunktionen ---
def berechne_distanz(lat1, lon1, lat2, lon2):
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def berechne_winkel(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d_lon = lon2 - lon1
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(d_lon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360

# --- API Aufruf ---
def hole_osm_haeuser_bbox(min_lon, min_lat, max_lon, max_lat):
    server = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:csv(::lat, ::lon; false; "\\t")][timeout:180][maxsize:1073741824];
    nwr({min_lat},{min_lon},{max_lat},{max_lon})["addr:housenumber"];
    out center qt;
    """
    headers = {"User-Agent": "Streamlit_Wind_Scanner/1.0"}
    for versuch in range(1, 4):
        try:
            response = requests.post(server, data={'data': query}, headers=headers, timeout=180)
            response.raise_for_status()
            lines = response.text.strip().split('\n')
            elemente = []
            for line in lines:
                parts = line.split('\t')
                if len(parts) >= 2 and parts[0] and parts[1]:
                    try:
                        elemente.append({'lat': float(parts[0]), 'lon': float(parts[1])})
                    except ValueError:
                        continue
            return elemente
        except:
            time.sleep(5)
    return []

# ==========================================
# ⬇️ STREAMLIT BENUTZEROBERFLÄCHE ⬇️
# ==========================================
st.set_page_config(page_title="Windpark Haus-Scanner", page_icon="🌬️", layout="wide")

st.title("🌬️ Windpark Haus-Scanner (Shapefile Edition)")
st.markdown("Füge deine Anlagendaten ein, lade die Isolinie hoch und exportiere die bereinigte CSV für windPRO.")

with st.sidebar:
    st.header("⚙️ Einstellungen")
    sektoren_anzahl = st.number_input("Anzahl der Sektoren (z.B. 180 oder 360)", min_value=1, value=180)
    nutze_sektoren_logik = st.checkbox("Sektoren-Filter aktiv (Nur das nächste Haus pro Sektor)", value=True)

st.subheader("1. WEA-Tabelle (Aus Excel kopieren)")
wea_text = st.text_area("Füge hier deine Tabelle inkl. Kopfzeile ein:", height=150)

st.subheader("2. Shapefile (Als ZIP hochladen)")
st.info("💡 Markiere auf deinem PC die 4 Shapefile-Dateien (.shp, .shx, .dbf, .prj), mache einen Rechtsklick -> 'Zu ZIP-Datei komprimieren' und lade diese ZIP hier hoch.")
uploaded_zip = st.file_uploader("ZIP-Datei auswählen", type="zip")

if st.button("🚀 Analyse starten", type="primary"):
    if not wea_text.strip():
        st.error("Bitte füge zuerst die WEA-Tabelle ein.")
    elif not uploaded_zip:
        st.error("Bitte lade die Shapefile-ZIP hoch.")
    else:
        with st.spinner("Analysiere Daten... Bitte warten..."):
            try:
                # 1. Tabelle auslesen
                lines = [line for line in wea_text.strip().split('\n') if line.strip()]
                header = lines[0].split('\t')
                ost_idx = next(i for i, col in enumerate(header) if 'Ost' in col.strip())
                nord_idx = next(i for i, col in enumerate(header) if 'Nord' in col.strip())
                
                wea_liste_utm = []
                for line in lines[1:]:
                    cols = line.split('\t')
                    if len(cols) > max(ost_idx, nord_idx):
                        try:
                            ost = float(cols[ost_idx].replace(',', '.'))
                            nord = float(cols[nord_idx].replace(',', '.'))
                            if (ost, nord) not in wea_liste_utm:
                                wea_liste_utm.append((ost, nord))
                        except ValueError:
                            continue
                
                st.success(f"✅ {len(wea_liste_utm)} Windenergieanlagen erkannt.")

                # 2. Shapefile aus ZIP entpacken und laden
                with tempfile.TemporaryDirectory() as tmpdir:
                    with zipfile.ZipFile(uploaded_zip, 'r') as zip_ref:
                        zip_ref.extractall(tmpdir)
                    
                    shp_file = [f for f in os.listdir(tmpdir) if f.endswith('.shp')][0]
                    poly_gdf = gpd.read_file(os.path.join(tmpdir, shp_file))
                    poly_gdf = poly_gdf.to_crs("EPSG:4326")
                
                st.success("✅ Shapefile erfolgreich geladen.")

                # 3. Download und Filterung
                min_lon, min_lat, max_lon, max_lat = poly_gdf.total_bounds
                alle_haeuser = hole_osm_haeuser_bbox(min_lon, min_lat, max_lon, max_lat)
                
                haeuser_im_polygon = []
                for h in alle_haeuser:
                    punkt = Point(h['lon'], h['lat'])
                    if poly_gdf.contains(punkt).any():
                        haeuser_im_polygon.append(h)
                
                st.info(f"🏠 {len(haeuser_im_polygon)} Häuser im Polygon gefunden. Ordne Sektoren zu...")

                # 4. WEA Logik
                wea_gps = [utm_zu_gps(o, n) for o, n in wea_liste_utm]
                zentrum_lat = sum([w[0] for w in wea_gps]) / len(wea_gps)
                zentrum_lon = sum([w[1] for w in wea_gps]) / len(wea_gps)

                einzigartige_haeuser = {}
                grad_pro_sektor = 360 / sektoren_anzahl

                for wea_lat, wea_lon in wea_gps:
                    haeuser_fuer_wea = []
                    for h in haeuser_im_polygon:
                        haeuser_fuer_wea.append({
                            'lat': h['lat'],
                            'lon': h['lon'],
                            'distanz': berechne_distanz(wea_lat, wea_lon, h['lat'], h['lon']),
                            'winkel': berechne_winkel(wea_lat, wea_lon, h['lat'], h['lon'])
                        })
                        
                    if nutze_sektoren_logik:
                        haeuser_fuer_wea.sort(key=lambda x: x['distanz'])
                        sektoren = {s: None for s in range(sektoren_anzahl)}
                        for haus in haeuser_fuer_wea:
                            idx = int(haus['winkel'] // grad_pro_sektor) % sektoren_anzahl
                            if sektoren[idx] is None:
                                sektoren[idx] = haus
                        for haus in sektoren.values():
                            if haus is not None:
                                einzigartige_haeuser[(haus['lat'], haus['lon'])] = haus
                    else:
                        for haus in haeuser_fuer_wea:
                            einzigartige_haeuser[(haus['lat'], haus['lon'])] = haus

                # 5. Sortieren und CSV erstellen (im Arbeitsspeicher)
                liste_fuer_export = list(einzigartige_haeuser.values())
                for h in liste_fuer_export:
                    h['winkel_zum_zentrum'] = berechne_winkel(zentrum_lat, zentrum_lon, h['lat'], h['lon'])
                liste_fuer_export.sort(key=lambda x: x['winkel_zum_zentrum'])

                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer, delimiter=";")
                headers = [
                    "Object ID", "Ost ", "Nord ", "Z", "Object description", "User label",
                    "Shadow Type", "Shadow Angle", "Width", "Height", "Height Above Ground",
                    "Slope", "Direction mode", "Use for flicker", "Use for Glare",
                    "Glare Type", "Glare Angle", "FOV", "h.a.g."
                ]
                writer.writerow(headers)

                for nummer, haus in enumerate(liste_fuer_export, start=1):
                    ost_utm, nord_utm = gps_zu_utm(haus['lat'], haus['lon'])
                    writer.writerow([
                        "Shadow", ost_utm, nord_utm, "", "", f"IP {nummer:03d}",
                        "0", "0", "0,1", "0,1", "2", "90", "1", "1", "0", "0", "0", "360", "0"
                    ])

                st.success(f"🎉 FERTIG! Insgesamt {len(liste_fuer_export)} Häuser im Uhrzeigersinn sortiert.")
                
                # Download Button
                st.download_button(
                    label="💾 Bereinigte CSV für windPRO herunterladen",
                    data=csv_buffer.getvalue().encode('utf-8'),
                    file_name="relevante_haeuser_park.csv",
                    mime="text/csv",
                    type="primary"
                )

            except Exception as e:
                st.error(f"❌ Ein Fehler ist aufgetreten: {e}")
