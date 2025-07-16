import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
from datetime import timedelta, datetime
import plotly.express as px # ИСПРАВЛЕНО: импорт перенесен в начало файла

# --- 1. Конфигурация страницы и Заголовок ---
st.set_page_config(layout="wide", page_title="Анализ 'Судно-Пятно'")

# Добавлены стили для темной темы
st.markdown("""
<style>
/* --- СТИЛИ ДЛЯ ТЕМНОЙ ТЕМЫ --- */
body {
    color: #fff;
    background-color: #111;
}
.stApp {
    background-color: #0E1117;
}
h1, h2, h3, h4, h5, h6 {
    color: #ffffff !important;
}
p, .stMarkdown, .stWrite, div[data-testid="stText"], .stDataFrame {
    color: #fafafa !important;
}
[data-testid="stSidebar"] {
    background-color: #1c1c1e !important;
}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, [data-testid="stSidebar"] label {
     color: #ffffff !important;
}
label[data-testid="stWidgetLabel"] p {
    color: #fafafa !important;
}
/* --- КОНЕЦ СТИЛЕЙ ДЛЯ ТЕМНОЙ ТЕМЫ --- */

/* Уменьшаем отступы между элементами */
div[data-testid="stVerticalBlock"] > div {
    margin-top: 0.5rem !important;
    padding-top: 0 !important;
    margin-bottom: 0.5rem !important;
    padding-bottom: 0 !important;
}
div[data-testid="stFolium"] {
    margin-bottom: 0.5rem !important;
}
h2 {
    margin-top: 0.5rem !important;
    margin-bottom: 0.5rem !important;
}
</style>
""", unsafe_allow_html=True)

st.title("🚢 Анализ связи 'Судно-Пятно' 💧")
st.write("""
Приложение автоматически анализирует данные о разливах и треки судов из репозитория.
Оно находит суда, которые находились в зоне разлива незадолго до его обнаружения,
и предоставляет расширенную аналитику по инцидентам.
""")

# --- Пути к файлам ---
SPILLS_FILE_PATH = 'fields2.geojson'
AIS_FILE_PATH = 'generated_ais_data.csv'

# --- 2. Боковая панель с параметрами ---
st.sidebar.header("Параметры анализа")

time_window_hours = st.sidebar.slider(
    "Временное окно поиска (часы до обнаружения):",
    min_value=1, max_value=168, value=24, step=1,
    help="Искать суда, которые были в зоне разлива за указанное количество часов ДО его обнаружения."
)

date_range = st.sidebar.date_input(
    "Диапазон дат для анализа:",
    value=(datetime(2023, 1, 1), datetime(2025, 6, 15)),
    min_value=datetime(2000, 1, 1),
    max_value=datetime.now(),
    help="Выберите диапазон дат для фильтрации разливов и AIS-данных."
)

# --- 3. Функции для обработки и анализа данных ---
@st.cache_data
def load_spills_data(file_path):
    try:
        gdf = gpd.read_file(file_path)
    except Exception as e:
        st.error(f"Не удалось прочитать GeoJSON файл '{file_path}'. Ошибка: {e}")
        return None
    required_cols = ['slick_name', 'area_sys']
    if not all(col in gdf.columns for col in required_cols):
        missing = [col for col in required_cols if col not in gdf.columns]
        st.error(f"В GeoJSON отсутствуют обязательные поля: {', '.join(missing)}")
        return None
    gdf.rename(columns={'slick_name': 'spill_id', 'area_sys': 'area_sq_km'}, inplace=True)
    if 'date' in gdf.columns and 'time' in gdf.columns:
        gdf['detection_date'] = pd.to_datetime(gdf['date'] + ' ' + gdf['time'], errors='coerce')
    else:
        gdf['detection_date'] = pd.to_datetime(gdf['spill_id'], format='%Y-%m-%d_%H:%M:%S', errors='coerce')
    gdf.dropna(subset=['detection_date'], inplace=True)
    if gdf.empty: return None
    if gdf.crs is None: gdf.set_crs("EPSG:4326", inplace=True)
    else: gdf = gdf.to_crs("EPSG:4326")
    return gdf

@st.cache_data
def load_ais_data(file_path):
    try:
        df = pd.read_csv(file_path, low_memory=False)
    except Exception as e:
        st.error(f"Не удалось прочитать CSV файл '{file_path}'. Ошибка: {e}")
        return None
    required_cols = ['mmsi', 'latitude', 'longitude', 'BaseDateTime']
    if not all(col in df.columns for col in required_cols):
        missing = [col for col in required_cols if col not in df.columns]
        st.error(f"В CSV файле отсутствуют обязательные колонки: {', '.join(missing)}")
        return None
    df['timestamp'] = pd.to_datetime(df['BaseDateTime'], errors='coerce')
    df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.longitude, df.latitude), crs="EPSG:4326")
    return gdf

def find_candidates(spills_gdf, vessels_gdf, time_window_hours):
    if spills_gdf is None or vessels_gdf is None: return gpd.GeoDataFrame()
    candidates = gpd.sjoin(vessels_gdf, spills_gdf, predicate='within')
    if candidates.empty: return gpd.GeoDataFrame()
    time_delta = timedelta(hours=time_window_hours)
    candidates = candidates[
        (candidates['timestamp'] <= candidates['detection_date']) &
        (candidates['timestamp'] >= candidates['detection_date'] - time_delta)
    ]
    return candidates

# --- 4. Основная логика приложения ---
spills_gdf = load_spills_data(SPILLS_FILE_PATH)
vessels_gdf = load_ais_data(AIS_FILE_PATH)

if spills_gdf is None or vessels_gdf is None or spills_gdf.empty or vessels_gdf.empty:
    st.error("Не удалось загрузить или обработать необходимые файлы данных. Анализ остановлен.")
    st.stop()

# Фильтрация по дате
if len(date_range) == 2:
    start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1]) + pd.Timedelta(days=1)
    spills_gdf = spills_gdf[(spills_gdf['detection_date'] >= start_date) & (spills_gdf['detection_date'] <= end_date)]
    vessels_gdf = vessels_gdf[(vessels_gdf['timestamp'] >= start_date) & (vessels_gdf['timestamp'] <= end_date)]

# Фильтр по судам
vessel_options = vessels_gdf[['mmsi']].drop_duplicates()
if 'vessel_name' in vessels_gdf.columns:
    vessel_options = vessels_gdf[['mmsi', 'vessel_name']].drop_duplicates()
    vessel_options['display'] = vessel_options.apply(lambda x: f"{x['vessel_name']} (MMSI: {x['mmsi']})" if pd.notnull(x['vessel_name']) else f"MMSI: {x['mmsi']}", axis=1)
else:
    vessel_options['display'] = vessel_options['mmsi'].apply(lambda x: f"MMSI: {x}")

selected_vessels = st.sidebar.multiselect("Выберите суда для анализа:", options=vessel_options['display'].tolist(), default=None)

if selected_vessels:
    selected_mmsi = vessel_options[vessel_options['display'].isin(selected_vessels)]['mmsi'].tolist()
    vessels_gdf = vessels_gdf[vessels_gdf['mmsi'].isin(selected_mmsi)]

# --- 5. Карта и таблица ---
with st.container():
    st.header("Карта разливов и судов-кандидатов")
    if spills_gdf.empty:
        st.warning("Нет данных о разливах в выбранном диапазоне дат.")
    else:
        map_center = [spills_gdf.unary_union.centroid.y, spills_gdf.unary_union.centroid.x]
        # ИСПРАВЛЕНО: Убран сложный JS-блок, используется стандартная атрибуция
        m = folium.Map(location=map_center, zoom_start=8, tiles="CartoDB dark_matter")

        spills_fg = folium.FeatureGroup(name="Пятна разливов").add_to(m)
        for _, row in spills_gdf.iterrows():
            folium.GeoJson(
                row['geometry'],
                style_function=lambda x: {'fillColor': '#FF4500', 'color': '#FFFFFF', 'weight': 1.5, 'fillOpacity': 0.6},
                tooltip=f"<b>Пятно:</b> {row.get('spill_id', 'N/A')}<br><b>Время:</b> {row['detection_date'].strftime('%Y-%m-%d %H:%M')}<br><b>Площадь:</b> {row.get('area_sq_km', 0):.2f} км²"
            ).add_to(spills_fg)

        candidates_df = find_candidates(spills_gdf, vessels_gdf, time_window_hours)

        if not candidates_df.empty:
            candidate_vessels_fg = folium.FeatureGroup(name="Суда-кандидаты").add_to(m)
            for _, row in candidates_df.iterrows():
                vessel_name = row.get('vessel_name', 'Имя не указано')
                folium.Marker(
                    location=[row.geometry.y, row.geometry.x],
                    tooltip=f"<b>Судно:</b> {vessel_name} (MMSI: {row['mmsi']})<br><b>Время прохода:</b> {row['timestamp'].strftime('%Y-%m-%d %H:%M')}<br><b>Внутри пятна:</b> {row['spill_id']}",
                    icon=folium.Icon(color='blue', icon='ship', prefix='fa')
                ).add_to(candidate_vessels_fg)

        folium.LayerControl().add_to(m)
        st_folium(m, width=1200, height=400)

    st.header(f"Таблица судов-кандидатов (найдено в пределах {time_window_hours} часов)")
    if candidates_df.empty:
        st.info("В заданном временном окне суда-кандидаты не найдены.")
    else:
        report_df = candidates_df.drop_duplicates(subset=['spill_id', 'mmsi'])
        desired_cols = ['spill_id', 'mmsi', 'vessel_name', 'timestamp', 'detection_date', 'area_sq_km']
        display_df = report_df[[col for col in desired_cols if col in report_df.columns]].copy()
        display_df.rename(columns={'spill_id': 'ID Пятна', 'mmsi': 'MMSI Судна', 'vessel_name': 'Название судна', 'timestamp': 'Время прохода судна', 'detection_date': 'Время обнаружения пятна', 'area_sq_km': 'Площадь пятна, км²'}, inplace=True)
        st.dataframe(display_df.sort_values(by='Время обнаружения пятна', ascending=False).reset_index(drop=True))

# --- 6. Расширенная аналитика ---
st.header("Дополнительная аналитика")
tab1, tab2, tab3 = st.tabs(["📊 Аналитика по судам", "📍 Горячие точки (Hotspots)", "🔍 Аналитика по инцидентам"])

if not candidates_df.empty:
    unique_incidents = candidates_df.drop_duplicates(subset=['mmsi', 'spill_id'])
    ship_names = unique_incidents[['mmsi', 'vessel_name']].drop_duplicates() if 'vessel_name' in unique_incidents.columns else None

    with tab1:
        st.subheader("Антирейтинг по количеству связанных пятен")
        ship_incident_counts = unique_incidents.groupby('mmsi').size().reset_index(name='incident_count').sort_values('incident_count', ascending=False)
        if ship_names is not None: ship_incident_counts = pd.merge(ship_incident_counts, ship_names, on='mmsi', how='left')
        st.dataframe(ship_incident_counts.reset_index(drop=True))
        
        st.subheader("Антирейтинг по суммарной площади связанных пятен (км²)")
        ship_area_sum = unique_incidents.groupby('mmsi')['area_sq_km'].sum().reset_index(name='total_area_sq_km').sort_values('total_area_sq_km', ascending=False)
        if ship_names is not None: ship_area_sum = pd.merge(ship_area_sum, ship_names, on='mmsi', how='left')
        st.dataframe(ship_area_sum.reset_index(drop=True))

    with tab3:
        st.subheader("Пятна с наибольшим количеством судов-кандидатов")
        spill_candidate_counts = candidates_df.groupby('spill_id')['mmsi'].nunique().reset_index(name='candidate_count').sort_values('candidate_count', ascending=False)
        st.dataframe(spill_candidate_counts.reset_index(drop=True))

        st.subheader("Главные подозреваемые (минимальное время до обнаружения)")
        candidates_df['time_to_detection'] = candidates_df['detection_date'] - candidates_df['timestamp']
        prime_suspects_df = candidates_df.loc[candidates_df.groupby('spill_id')['time_to_detection'].idxmin()]
        display_cols = ['spill_id', 'mmsi', 'vessel_name', 'time_to_detection', 'area_sq_km']
        st.dataframe(prime_suspects_df[[col for col in display_cols if col in prime_suspects_df.columns]].sort_values('area_sq_km', ascending=False))

        if 'VesselType' in unique_incidents.columns:
            with st.expander("🚢 Аналитика по типам судов"):
                vessel_type_analysis = unique_incidents.groupby('VesselType').agg(incident_count=('spill_id', 'count'), total_area_sq_km=('area_sq_km', 'sum')).sort_values('incident_count', ascending=False).reset_index()
                st.dataframe(vessel_type_analysis)
                
                fig = px.pie(vessel_type_analysis, names='VesselType', values='incident_count', title='Распределение инцидентов по типам судов', labels={'VesselType':'Тип судна', 'incident_count':'Количество инцидентов'})
                fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)', 'paper_bgcolor': 'rgba(0, 0, 0, 0)', 'font': {'color': '#ffffff'}})
                st.plotly_chart(fig)

with tab2:
    st.subheader("Карта 'горячих точек' разливов")
    if spills_gdf.empty:
        st.warning("Нет данных для отображения карты горячих точек.")
    else:
        # ИСПРАВЛЕНО: Убран сложный JS-блок, используется стандартная атрибуция
        m_heatmap = folium.Map(location=map_center, zoom_start=8, tiles="CartoDB dark_matter")
        heat_data = [[point.xy[1][0], point.xy[0][0], row['area_sq_km']] for index, row in spills_gdf.iterrows() for point in [row['geometry'].centroid]]
        HeatMap(heat_data, radius=15, blur=20, max_zoom=10).add_to(m_heatmap)
        folium.LayerControl().add_to(m_heatmap)
        st_folium(m_heatmap, width=1200, height=400)
