import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
from datetime import timedelta, datetime

# --- 1. Конфигурация страницы и Заголовок ---
st.set_page_config(layout="wide", page_title="Анализ 'Судно-Пятно'")

st.markdown("""
<style>
/* Уменьшаем отступы между элементами */
div[data-testid="stVerticalBlock"] > div {
    margin-top: 0.5rem !important;
    padding-top: 0 !important;
    margin-bottom: 0.5rem !important;
    padding-bottom: 0 !important;
}
/* Уменьшаем отступы для карты */
div[data-testid="stFolium"] {
    margin-bottom: 0.5rem !important;
}
/* Уменьшаем отступы для заголовков */
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

# --- Задаем пути к файлам в репозитории ---
SPILLS_FILE_PATH = 'fields2.geojson'
AIS_FILE_PATH = 'generated_ais_data.csv'
ROUTES_FILE_PATH = 'routs.geojson'

# --- 2. Боковая панель с параметрами ---
st.sidebar.header("Параметры анализа")

dark_mode_map = st.sidebar.toggle("Включить темную тему для карты", value=False, help="Переключает тему карт между светлой и темной.")

time_window_hours = st.sidebar.slider(
    "Временное окно поиска (часы до обнаружения):",
    min_value=1, max_value=168, value=24, step=1,
    help="Искать суда, которые были в зоне разлива за указанное количество часов ДО его обнаружения."
)

date_range = st.sidebar.date_input(
    "Диапазон дат для анализа:",
    value=(datetime(2022, 1, 1), datetime(2025, 7, 15)),
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2030, 12, 31),
    help="Выберите диапазон дат для фильтрации разливов и AIS-данных."
)

st.sidebar.header("Управление слоями")
st.sidebar.info("Управляет видимостью слоев по умолчанию. Их можно также переключать на самой карте.")
show_spills = st.sidebar.checkbox("Пятна разливов", value=True)
show_ships = st.sidebar.checkbox("Суда-кандидаты", value=True)
show_routes = st.sidebar.checkbox("Судовые трассы", value=True)


# --- 3. Функции для обработки и анализа данных ---
@st.cache_data
def load_spills_data(file_path):
    try:
        gdf = gpd.read_file(file_path)
    except Exception as e:
        st.error(f"Не удалось прочитать GeoJSON файл '{file_path}'. Ошибка: {e}")
        return gpd.GeoDataFrame()
    required_cols = ['slick_name', 'area_sys']
    if not all(col in gdf.columns for col in required_cols):
        missing = [col for col in required_cols if col not in gdf.columns]
        st.error(f"В GeoJSON отсутствуют обязательные поля: {', '.join(missing)}")
        return gpd.GeoDataFrame()
    gdf.rename(columns={'slick_name': 'spill_id', 'area_sys': 'area_sq_km'}, inplace=True)
    if 'date' in gdf.columns and 'time' in gdf.columns:
        gdf['detection_date'] = pd.to_datetime(gdf['date'] + ' ' + gdf['time'], errors='coerce')
    else:
        gdf['detection_date'] = pd.to_datetime(gdf['spill_id'], format='%Y-%m-%d_%H:%M:%S', errors='coerce')
    gdf.dropna(subset=['detection_date'], inplace=True)
    if gdf.crs is None:
        gdf.set_crs("EPSG:4326", inplace=True)
    else:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf

@st.cache_data
def load_ais_data(file_path):
    try:
        df = pd.read_csv(file_path, low_memory=False)
    except Exception as e:
        st.error(f"Не удалось прочитать CSV файл '{file_path}'. Ошибка: {e}")
        return gpd.GeoDataFrame()
    required_cols = ['mmsi', 'latitude', 'longitude', 'BaseDateTime']
    if not all(col in df.columns for col in required_cols):
        missing = [col for col in required_cols if col not in df.columns]
        st.error(f"В CSV файле отсутствуют обязательные колонки: {', '.join(missing)}")
        return gpd.GeoDataFrame()
    df['timestamp'] = pd.to_datetime(df['BaseDateTime'], errors='coerce')
    df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df.longitude, df.latitude),
        crs="EPSG:4326"
    )
    return gdf

@st.cache_data
def load_routes_data(file_path):
    try:
        gdf = gpd.read_file(file_path)
        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        else:
            gdf = gdf.to_crs("EPSG:4326")
        return gdf
    except Exception as e:
        st.warning(f"Не удалось прочитать файл трасс '{file_path}'. Слой не будет отображен. Ошибка: {e}")
        return gpd.GeoDataFrame()

def find_candidates(spills_gdf, vessels_gdf, time_window_hours):
    if spills_gdf.empty or vessels_gdf.empty:
        return gpd.GeoDataFrame()
    candidates = gpd.sjoin(vessels_gdf, spills_gdf, predicate='within')
    if candidates.empty:
        return gpd.GeoDataFrame()
    time_delta = timedelta(hours=time_window_hours)
    candidates = candidates[
        (candidates['timestamp'] <= candidates['detection_date']) &
        (candidates['timestamp'] >= candidates['detection_date'] - time_delta)
    ]
    return candidates

# --- 4. Основная логика приложения ---
spills_gdf = load_spills_data(SPILLS_FILE_PATH)
vessels_gdf = load_ais_data(AIS_FILE_PATH)
routes_gdf = load_routes_data(ROUTES_FILE_PATH)

if spills_gdf.empty or vessels_gdf.empty:
    st.error("Не удалось загрузить или обработать основные файлы данных (разливы, AIS). Анализ остановлен.")
    st.stop()

if len(date_range) == 2:
    start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1]) + pd.Timedelta(days=1)
    spills_gdf = spills_gdf[(spills_gdf['detection_date'] >= start_date) & (spills_gdf['detection_date'] <= end_date)]
    vessels_gdf = vessels_gdf[(vessels_gdf['timestamp'] >= start_date) & (vessels_gdf['timestamp'] <= end_date)]

vessel_options = vessels_gdf[['mmsi']].drop_duplicates()
if 'vessel_name' in vessels_gdf.columns:
    vessel_options = vessels_gdf[['mmsi', 'vessel_name']].drop_duplicates()
    vessel_options['display'] = vessel_options.apply(
        lambda x: f"{x['vessel_name']} (MMSI: {x['mmsi']})" if pd.notnull(x['vessel_name']) else f"MMSI: {x['mmsi']}", axis=1
    )
else:
    vessel_options['display'] = vessel_options['mmsi'].apply(lambda x: f"MMSI: {x}")

selected_vessels_display = st.sidebar.multiselect(
    "Выберите суда для фильтрации:",
    options=vessel_options['display'].tolist(),
    help="Фильтрует данные на карте и в таблицах по выбранным судам."
)

filtered_routes_gdf = routes_gdf.copy()
if selected_vessels_display:
    selected_mmsi = vessel_options[vessel_options['display'].isin(selected_vessels_display)]['mmsi'].tolist()
    vessels_gdf = vessels_gdf[vessels_gdf['mmsi'].isin(selected_mmsi)]
    if not filtered_routes_gdf.empty and 'mmsi' in filtered_routes_gdf.columns:
        filtered_routes_gdf = filtered_routes_gdf[filtered_routes_gdf['mmsi'].isin(selected_mmsi)]

# --- 5. Отображение карты и таблицы ---
with st.container():
    st.header("Карта разливов и судов-кандидатов")
    if spills_gdf.empty:
        st.warning("Нет данных о разливах в выбранном диапазоне дат.")
    else:
        map_center = [spills_gdf.unary_union.centroid.y, spills_gdf.unary_union.centroid.x]
        map_tiles = "CartoDB dark_matter" if dark_mode_map else "CartoDB positron"
        
        m = folium.Map(location=map_center, zoom_start=8, tiles=map_tiles)
        
        candidates_df = find_candidates(spills_gdf, vessels_gdf, time_window_hours)

        # --- ИЗМЕНЕНО: Создаем слои, управляя их видимостью по умолчанию ---

        # Слой 1: Пятна разливов
        spills_fg = folium.FeatureGroup(name="Пятна разливов", show=show_spills)
        for _, row in spills_gdf.iterrows():
            folium.GeoJson(
                row['geometry'],
                style_function=lambda x: {'fillColor': '#B22222', 'color': 'black', 'weight': 1.5, 'fillOpacity': 0.6},
                tooltip=f"<b>Пятно:</b> {row.get('spill_id', 'N/A')}<br>"
                        f"<b>Время:</b> {row['detection_date'].strftime('%Y-%m-%d %H:%M')}<br>"
                        f"<b>Площадь:</b> {row.get('area_sq_km', 0):.2f} км²"
            ).add_to(spills_fg)
        spills_fg.add_to(m)

        # Слой 2: Суда-кандидаты
        candidate_vessels_fg = folium.FeatureGroup(name="Суда-кандидаты", show=show_ships)
        if not candidates_df.empty:
            for _, row in candidates_df.iterrows():
                vessel_name = row.get('vessel_name', 'Имя не указано')
                folium.Marker(
                    location=[row.geometry.y, row.geometry.x],
                    tooltip=f"<b>Судно:</b> {vessel_name} (MMSI: {row['mmsi']})<br>"
                            f"<b>Время прохода:</b> {row['timestamp'].strftime('%Y-%m-%d %H:%M')}<br>"
                            f"<b>Внутри пятна:</b> {row['spill_id']}",
                    icon=folium.Icon(color='blue', icon='ship', prefix='fa')
                ).add_to(candidate_vessels_fg)
        candidate_vessels_fg.add_to(m)
        
        # Слой 3: Судовые трассы
        routes_fg = folium.FeatureGroup(name="Судовые трассы", show=show_routes)
        if not filtered_routes_gdf.empty:
            for _, row in filtered_routes_gdf.iterrows():
                tooltip_text = f"<b>Трек судна (MMSI: {row.get('mmsi', 'N/A')})</b>"
                folium.GeoJson(
                    row['geometry'],
                    style_function=lambda x: {'color': '#007FFF', 'weight': 2.5, 'opacity': 0.7},
                    tooltip=tooltip_text
                ).add_to(routes_fg)
        routes_fg.add_to(m)

        # --- ДОБАВЛЕНО: Добавляем контрол для переключения слоев на карте ---
        folium.LayerControl().add_to(m) 
        
        st_folium(m, width=1200, height=450, returned_objects=[])

    st.header(f"Таблица судов-кандидатов (в пределах {time_window_hours} часов)")
    if candidates_df.empty:
        st.info("В заданном временном окне и с учетом фильтров суда-кандидаты не найдены.")
    else:
        report_df = candidates_df.drop_duplicates(subset=['spill_id', 'mmsi'])
        desired_cols = ['spill_id', 'mmsi', 'vessel_name', 'timestamp', 'detection_date', 'area_sq_km']
        existing_cols = [col for col in desired_cols if col in report_df.columns]
        display_df = report_df[existing_cols].copy()
        rename_dict = {
            'spill_id': 'ID Пятна', 'mmsi': 'MMSI Судна', 'vessel_name': 'Название судна',
            'timestamp': 'Время прохода', 'detection_date': 'Время обнаружения', 'area_sq_km': 'Площадь, км²'
        }
        display_df.rename(columns=rename_dict, inplace=True)
        st.dataframe(display_df.sort_values(by='Время обнаружения', ascending=False).reset_index(drop=True))

# --- 6. Блок с расширенной аналитикой ---
# (Код для вкладок остается без изменений)
st.header("Дополнительная аналитика")
tab1, tab2, tab3 = st.tabs(["📊 Аналитика по судам", "📍 Горячие точки (Hotspots)", "🔍 Аналитика по инцидентам"])

candidates_df_for_analytics = find_candidates(load_spills_data(SPILLS_FILE_PATH), load_ais_data(AIS_FILE_PATH), time_window_hours)

with tab1:
    if not candidates_df_for_analytics.empty:
        unique_incidents = candidates_df_for_analytics.drop_duplicates(subset=['mmsi', 'spill_id'])
        ship_names = unique_incidents[['mmsi', 'vessel_name']].drop_duplicates('mmsi')
        
        st.subheader("Антирейтинг по количеству связанных пятен")
        ship_incident_counts = unique_incidents.groupby('mmsi').size().reset_index(name='incident_count').sort_values('incident_count', ascending=False)
        st.dataframe(pd.merge(ship_incident_counts, ship_names, on='mmsi', how='left'))
        
        st.subheader("Антирейтинг по суммарной площади связанных пятен (км²)")
        ship_area_sum = unique_incidents.groupby('mmsi')['area_sq_km'].sum().reset_index(name='total_area_sq_km').sort_values('total_area_sq_km', ascending=False)
        st.dataframe(pd.merge(ship_area_sum, ship_names, on='mmsi', how='left'))
    else:
        st.info("Нет данных для аналитики по судам.")

with tab2:
    st.subheader("Карта 'горячих точек' разливов")
    if spills_gdf.empty:
        st.warning("Нет данных для отображения карты горячих точек.")
    else:
        m_heatmap = folium.Map(location=map_center, zoom_start=8, tiles=map_tiles)
        heat_data = [[point.xy[1][0], point.xy[0][0], row['area_sq_km']] for _, row in spills_gdf.iterrows() for point in [row['geometry'].centroid]]
        HeatMap(heat_data, radius=15, blur=20).add_to(m_heatmap)
        st_folium(m_heatmap, width=1200, height=400, returned_objects=[])

with tab3:
    if not candidates_df_for_analytics.empty:
        unique_incidents = candidates_df_for_analytics.drop_duplicates(subset=['mmsi', 'spill_id'])
        st.subheader("Пятна с наибольшим количеством судов-кандидатов")
        spill_candidate_counts = candidates_df_for_analytics.groupby('spill_id')['mmsi'].nunique().reset_index(name='candidate_count').sort_values('candidate_count', ascending=False)
        st.dataframe(spill_candidate_counts)

        st.subheader("Главные подозреваемые (минимальное время до обнаружения)")
        candidates_df_for_analytics['time_to_detection'] = candidates_df_for_analytics['detection_date'] - candidates_df_for_analytics['timestamp']
        prime_suspects_idx = candidates_df_for_analytics.groupby('spill_id')['time_to_detection'].idxmin()
        prime_suspects_df = candidates_df_for_analytics.loc[prime_suspects_idx]
        display_cols = ['spill_id', 'mmsi', 'vessel_name', 'time_to_detection', 'area_sq_km']
        st.dataframe(prime_suspects_df[[col for col in display_cols if col in prime_suspects_df]].sort_values('area_sq_km', ascending=False))
    else:
        st.info("Нет данных для аналитики по инцидентам.")
