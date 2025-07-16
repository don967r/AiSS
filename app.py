import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
from datetime import timedelta, datetime

# --- 1. Конфигурация страницы и Заголовок ---
st.set_page_config(layout="wide", page_title="Анализ 'Судно-Пятно'")

# Добавляем CSS для уменьшения отступов
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

# --- ИЗМЕНЕНО: Задаем пути к файлам в репозитории ---
SPILLS_FILE_PATH = 'fields2.geojson'
AIS_FILE_PATH = 'generated_ais_data.csv'

# --- 2. Боковая панель с параметрами ---
st.sidebar.header("Параметры анализа")

# Фильтр по временному окну
time_window_hours = st.sidebar.slider(
    "Временное окно поиска (часы до обнаружения):",
    min_value=1, max_value=168, value=24, step=1,
    help="Искать суда, которые были в зоне разлива за указанное количество часов ДО его обнаружения."
)

# Фильтр по диапазону дат
date_range = st.sidebar.date_input(
    "Диапазон дат для анализа:",
    value=(datetime(2023, 1, 1), datetime(2023, 12, 31)),
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
        st.error(f"Не удалось прочитать GeoJSON файл '{file_path}'. Убедитесь, что он существует в репозитории. Ошибка: {e}")
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

    if gdf['detection_date'].isnull().any():
        failed_count = gdf['detection_date'].isnull().sum()
        st.error(f"Не удалось распознать дату в {failed_count} записях о разливах. Эти записи будут проигнорированы.")
        gdf.dropna(subset=['detection_date'], inplace=True)

    if gdf.empty:
        st.error("После обработки не осталось ни одной записи о разливах с корректной датой.")
        return None

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
        st.error(f"Не удалось прочитать CSV файл '{file_path}'. Убедитесь, что он существует в репозитории. Ошибка: {e}")
        return None

    required_cols = ['mmsi', 'latitude', 'longitude', 'BaseDateTime']
    if not all(col in df.columns for col in required_cols):
        missing = [col for col in required_cols if col not in df.columns]
        st.error(f"В CSV файле отсутствуют обязательные колонки: {', '.join(missing)}")
        return None

    df['timestamp'] = pd.to_datetime(df['BaseDateTime'], errors='coerce')
    df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df.longitude, df.latitude),
        crs="EPSG:4326"
    )

    return gdf

def find_candidates(spills_gdf, vessels_gdf, time_window_hours):
    if spills_gdf is None or vessels_gdf is None:
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

if spills_gdf is None or vessels_gdf is None or spills_gdf.empty or vessels_gdf.empty:
    st.error("Не удалось загрузить или обработать необходимые файлы данных. Анализ остановлен.")
    st.stop()

# --- Фильтрация данных по дате ---
if len(date_range) == 2:
    start_date, end_date = date_range
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)  # Включить весь день
    spills_gdf = spills_gdf[(spills_gdf['detection_date'] >= start_date) & (spills_gdf['detection_date'] <= end_date)]
    vessels_gdf = vessels_gdf[(vessels_gdf['timestamp'] >= start_date) & (vessels_gdf['timestamp'] <= end_date)]

# --- Фильтр по судам ---
vessel_options = vessels_gdf[['mmsi']].drop_duplicates()
if 'vessel_name' in vessels_gdf.columns:
    vessel_options = vessels_gdf[['mmsi', 'vessel_name']].drop_duplicates()
    vessel_options['display'] = vessel_options.apply(
        lambda x: f"{x['vessel_name']} (MMSI: {x['mmsi']})" if pd.notnull(x['vessel_name']) else f"MMSI: {x['mmsi']}", axis=1
    )
else:
    vessel_options['display'] = vessel_options['mmsi'].apply(lambda x: f"MMSI: {x}")

selected_vessels = st.sidebar.multiselect(
    "Выберите суда для анализа:",
    options=vessel_options['display'].tolist(),
    default=None,
    help="Выберите одно или несколько судов для фильтрации. Если ничего не выбрано, показаны все суда."
)

if selected_vessels:
    selected_mmsi = vessel_options[vessel_options['display'].isin(selected_vessels)]['mmsi'].tolist()
    vessels_gdf = vessels_gdf[vessels_gdf['mmsi'].isin(selected_mmsi)]

# --- 5. Отображение карты и таблицы в одном контейнере ---
with st.container():
    st.header("Карта разливов и судов-кандидатов")
    if spills_gdf.empty:
        st.warning("Нет данных о разливах в выбранном диапазоне дат.")
    else:
        map_center = [spills_gdf.unary_union.centroid.y, spills_gdf.unary_union.centroid.x]
        m = folium.Map(
            location=map_center,
            zoom_start=8,
            tiles="CartoDB positron",
            attribution_control=False  # Отключаем стандартную атрибуцию
        )

        # Добавляем пользовательскую атрибуцию без флага
        folium.map.CustomPane("labels").add_to(m)
        m.get_root().html.add_child(folium.Element("""
        <script>
            L.Control.Attribution.prototype._update = function() {
                if (!this._map) { return; }
                var attribs = [];
                for (var i in this._attributions) {
                    if (this._attributions[i]) {
                        attribs.push(i);
                    }
                }
                var prefixAndAttribs = [];
                prefixAndAttribs.push('© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, © <a href="https://carto.com/attributions">CARTO</a>');
                this._container.innerHTML = prefixAndAttribs.join(' | ');
            };
        </script>
        """))

        # Слой с пятнами
        spills_fg = folium.FeatureGroup(name="Пятна разливов").add_to(m)
        for _, row in spills_gdf.iterrows():
            folium.GeoJson(
                row['geometry'],
                style_function=lambda x: {'fillColor': '#B22222', 'color': 'black', 'weight': 1.5, 'fillOpacity': 0.6},
                tooltip=f"<b>Пятно:</b> {row.get('spill_id', 'N/A')}<br>"
                        f"<b>Время:</b> {row['detection_date'].strftime('%Y-%m-%d %H:%M')}<br>"
                        f"<b>Площадь:</b> {row.get('area_sq_km', 0):.2f} км²"
            ).add_to(spills_fg)

        candidates_df = find_candidates(spills_gdf, vessels_gdf, time_window_hours)

        if not candidates_df.empty:
            candidate_vessels_fg = folium.FeatureGroup(name="Суда-кандидаты").add_to(m)
            for _, row in candidates_df.iterrows():
                vessel_name = row.get('vessel_name', 'Имя не указано')
                folium.Marker(
                    location=[row.geometry.y, row.geometry.x],
                    tooltip=f"<b>Судно:</b> {vessel_name} (MMSI: {row['mmsi']})<br>"
                            f"<b>Время прохода:</b> {row['timestamp'].strftime('%Y-%m-%d %H:%M')}<br>"
                            f"<b>Внутри пятна:</b> {row['spill_id']}",
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
        existing_cols = [col for col in desired_cols if col in report_df.columns]
        display_df = report_df[existing_cols].copy()

        rename_dict = {
            'spill_id': 'ID Пятна',
            'mmsi': 'MMSI Судна',
            'vessel_name': 'Название судна',
            'timestamp': 'Время прохода судна',
            'detection_date': 'Время обнаружения пятна',
            'area_sq_km': 'Площадь пятна, км²'
        }
        display_df.rename(columns=rename_dict, inplace=True)
        st.dataframe(display_df.sort_values(by='Время обнаружения пятна', ascending=False).reset_index(drop=True))

# --- 6. Блок с расширенной аналитикой ---
st.header("Дополнительная аналитика")
tab1, tab2, tab3 = st.tabs(["📊 Аналитика по судам", "📍 Горячие точки (Hotspots)", "🔍 Аналитика по инцидентам"])

with tab1:
    st.subheader("Антирейтинг по количеству связанных пятен")
    unique_incidents = candidates_df.drop_duplicates(subset=['mmsi', 'spill_id'])
    ship_incident_counts = unique_incidents.groupby('mmsi').size().reset_index(name='incident_count') \
        .sort_values('incident_count', ascending=False).reset_index(drop=True)
    if 'vessel_name' in unique_incidents.columns:
        ship_names = unique_incidents[['mmsi', 'vessel_name']].drop_duplicates()
        ship_incident_counts = pd.merge(ship_incident_counts, ship_names, on='mmsi', how='left')
    st.dataframe(ship_incident_counts)
    
    st.subheader("Антирейтинг по суммарной площади связанных пятен (км²)")
    ship_area_sum = unique_incidents.groupby('mmsi')['area_sq_km'].sum().reset_index(name='total_area_sq_km') \
        .sort_values('total_area_sq_km', ascending=False).reset_index(drop=True)
    if 'vessel_name' in unique_incidents.columns:
        ship_area_sum = pd.merge(ship_area_sum, ship_names, on='mmsi', how='left')
    st.dataframe(ship_area_sum)

with tab2:
    st.subheader("Карта 'горячих точек' разливов")
    if spills_gdf.empty:
        st.warning("Нет данных для отображения карты горячих точек.")
    else:
        m_heatmap = folium.Map(
            location=map_center,
            zoom_start=8,
            tiles="CartoDB positron",
            attribution_control=False  # Отключаем стандартную атрибуцию
        )
        # Добавляем пользовательскую атрибуцию без флага
        m_heatmap.get_root().html.add_child(folium.Element("""
        <script>
            L.Control.Attribution.prototype._update = function() {
                if (!this._map) { return; }
                var attribs = [];
                for (var i in this._attributions) {
                    if (this._attributions[i]) {
                        attribs.push(i);
                    }
                }
                var prefixAndAttribs = [];
                prefixAndAttribs.push('© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, © <a href="https://carto.com/attributions">CARTO</a>');
                this._container.innerHTML = prefixAndAttribs.join(' | ');
            };
        </script>
        """))

        heat_data = [[point.xy[1][0], point.xy[0][0], row['area_sq_km']] for index, row in spills_gdf.iterrows() for point in [row['geometry'].centroid]]
        HeatMap(heat_data, radius=15, blur=20, max_zoom=10).add_to(m_heatmap)
        folium.LayerControl().add_to(m_heatmap)
        st_folium(m_heatmap, width=1200, height=400)

with tab3:
    st.subheader("Пятна с наибольшим количеством судов-кандидатов")
    spill_candidate_counts = candidates_df.groupby('spill_id')['mmsi'].nunique().reset_index(name='candidate_count') \
        .sort_values('candidate_count', ascending=False).reset_index(drop=True)
    st.dataframe(spill_candidate_counts)

    st.subheader("Главные подозреваемые (минимальное время до обнаружения)")
    candidates_df['time_to_detection'] = candidates_df['detection_date'] - candidates_df['timestamp']
    prime_suspects_idx = candidates_df.groupby('spill_id')['time_to_detection'].idxmin()
    prime_suspects_df = candidates_df.loc[prime_suspects_idx]

    display_cols = ['spill_id', 'mmsi', 'vessel_name', 'time_to_detection', 'area_sq_km']
    existing_display_cols = [col for col in display_cols if col in prime_suspects_df.columns]
    st.dataframe(prime_suspects_df[existing_display_cols].sort_values('area_sq_km', ascending=False))

    if 'VesselType' in unique_incidents.columns:
        with st.expander("🚢 Аналитика по типам судов"):
            vessel_type_analysis = unique_incidents.groupby('VesselType').agg(
                incident_count=('spill_id', 'count'),
                total_area_sq_km=('area_sq_km', 'sum')
            ).sort_values('incident_count', ascending=False).reset_index()
            st.dataframe(vessel_type_analysis)

            import plotly.express as px
            fig = px.pie(vessel_type_analysis, names='VesselType', values='incident_count',
                         title='Распределение инцидентов по типам судов',
                         labels={'VesselType':'Тип судна', 'incident_count':'Количество инцидентов'})
            st.plotly_chart(fig)
