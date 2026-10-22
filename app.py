import re
from pathlib import Path

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Film Location Explorer",
    page_icon="🎬",
    layout="wide",
)

DEFAULT_CSV = Path(__file__).parent / "film_location_reference.csv"
MAX_MAP_POINTS = 20_000  # keep the map snappy on huge selections
GENRE_SPLIT = re.compile(r"\s*[,|;]\s*")


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner="Loading data…")
def load_data(source) -> pd.DataFrame:
    df = pd.read_csv(source, low_memory=False)

    # Normalise column names (strip stray whitespace)
    df.columns = [c.strip() for c in df.columns]

    # Types
    for col in ("year", "latitude", "longitude"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "year" in df.columns:
        df["year"] = df["year"].astype("Int64")

    # Text columns: make sure they're strings, blanks -> NaN
    text_cols = [
        "film_title", "tmdb_title", "genre_names", "wikipedia", "relation",
        "location_slug", "location_name", "category", "category_label",
        "country", "match_status",
    ]
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip().replace("", pd.NA)

    # Pre-split genres into a list column
    if "genre_names" in df.columns:
        df["_genres"] = df["genre_names"].apply(
            lambda s: [g for g in GENRE_SPLIT.split(s.strip().strip("[]")) if g.strip("'\" ")]
            if isinstance(s, str) else []
        )
    else:
        df["_genres"] = [[] for _ in range(len(df))]

    return df


def sorted_unique(series: pd.Series) -> list:
    return sorted(series.dropna().unique().tolist(), key=lambda x: str(x).lower())


# ----------------------------------------------------------------------------
# Sidebar: data source
# ----------------------------------------------------------------------------
st.sidebar.title("🎬 Filters")



def sidebar_uploader():
    """Rendered at the very bottom of the sidebar. The widget's value is read
    from session_state at the top of the script (before it is drawn)."""
    st.sidebar.divider()
    st.sidebar.file_uploader(
        "Upload a CSV (optional)", type="csv", key="upload_csv",
        help="Use your own file with the same columns instead of the bundled dataset.",
    )


uploaded = st.session_state.get("upload_csv")

if uploaded is not None:
    df = load_data(uploaded)
elif DEFAULT_CSV.exists():
    df = load_data(DEFAULT_CSV)
else:
    st.title("🎬 Film Location Explorer")
    st.info(
        f"No data found. Put `{DEFAULT_CSV.name}` next to `app.py`, "
        "or upload a CSV in the sidebar."
    )
    sidebar_uploader()
    st.stop()

required = {"film_title", "location_name", "latitude", "longitude"}
missing = required - set(df.columns)
if missing:
    st.error(f"The CSV is missing required columns: {', '.join(sorted(missing))}")
    sidebar_uploader()
    st.stop()

# ----------------------------------------------------------------------------
# Sidebar: filters
# ----------------------------------------------------------------------------
if st.sidebar.button("Reset filters", width="stretch"):
    for k in list(st.session_state.keys()):
        if k.startswith("f_"):
            del st.session_state[k]
    st.rerun()

title_query = st.sidebar.text_input(
    "Film title contains", key="f_title", placeholder="e.g. godfather"
)

all_genres = sorted({g for gs in df["_genres"] for g in gs})
sel_genres = st.sidebar.multiselect("Genre", all_genres, key="f_genres")
genre_mode = "any"
if len(sel_genres) > 1:
    genre_mode = st.sidebar.radio(
        "Genre match", ["any", "all"], horizontal=True, key="f_genre_mode",
        help="'any' = film has at least one selected genre; 'all' = film has every selected genre.",
    )

if "year" in df.columns and df["year"].notna().any():
    y_min, y_max = int(df["year"].min()), int(df["year"].max())
    if y_min < y_max:
        year_range = st.sidebar.slider(
            "Year", y_min, y_max, (y_min, y_max), key="f_year"
        )
    else:
        year_range = (y_min, y_max)
else:
    year_range = None

sel_countries = (
    st.sidebar.multiselect("Country", sorted_unique(df["country"]), key="f_countries")
    if "country" in df.columns else []
)

sel_categories = (
    st.sidebar.multiselect(
        "Location type", sorted_unique(df["category_label"]), key="f_cat",
        help="e.g. 'Towns & cities' to look at cities only.",
    )
    if "category_label" in df.columns else []
)

location_query = st.sidebar.text_input(
    "Location / city contains", key="f_loc_query", placeholder="e.g. Paris"
)
sel_locations = st.sidebar.multiselect(
    "Location / city (pick)", sorted_unique(df["location_name"]), key="f_locations"
)

sel_match = (
    st.sidebar.multiselect("Match status", sorted_unique(df["match_status"]), key="f_match")
    if "match_status" in df.columns else []
)

with st.sidebar.expander("More filters"):
    sel_relations = (
        st.multiselect("Relation", sorted_unique(df["relation"]), key="f_rel")
        if "relation" in df.columns else []
    )
    only_with_coords = st.checkbox("Only rows with coordinates", value=False, key="f_coords")

sidebar_uploader()

# ----------------------------------------------------------------------------
# Apply filters
# ----------------------------------------------------------------------------
mask = pd.Series(True, index=df.index)

if title_query:
    q = title_query.strip()
    title_hit = df["film_title"].str.contains(q, case=False, na=False, regex=False)
    if "tmdb_title" in df.columns:
        title_hit |= df["tmdb_title"].str.contains(q, case=False, na=False, regex=False)
    mask &= title_hit

if sel_genres:
    wanted = set(sel_genres)
    if genre_mode == "all":
        mask &= df["_genres"].apply(lambda gs: wanted.issubset(gs))
    else:
        mask &= df["_genres"].apply(lambda gs: bool(wanted.intersection(gs)))

if year_range and (year_range != (y_min, y_max)):
    mask &= df["year"].between(year_range[0], year_range[1])

if sel_countries:
    mask &= df["country"].isin(sel_countries)

if location_query:
    mask &= df["location_name"].str.contains(location_query.strip(), case=False, na=False, regex=False)

if sel_locations:
    mask &= df["location_name"].isin(sel_locations)

if sel_match:
    mask &= df["match_status"].isin(sel_match)

if sel_categories:
    mask &= df["category_label"].isin(sel_categories)

if sel_relations:
    mask &= df["relation"].isin(sel_relations)

if only_with_coords:
    mask &= df["latitude"].notna() & df["longitude"].notna()

fdf = df[mask]
display_cols = [c for c in df.columns if not c.startswith("_")]

# ----------------------------------------------------------------------------
# Header + metrics
# ----------------------------------------------------------------------------
st.title("🎬 Film Location Explorer")
st.caption("Explore where films were shot, set, or filmed-about around the world.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Rows", f"{len(fdf):,}", delta=f"{len(fdf) - len(df):,}" if len(fdf) != len(df) else None)
m2.metric("Films", f"{fdf['film_title'].nunique():,}")
m3.metric("Locations", f"{fdf['location_name'].nunique():,}")
m4.metric("Countries", f"{fdf['country'].nunique():,}" if "country" in fdf.columns else "–")

if fdf.empty:
    st.warning("No results match your filters. Try loosening them.")
    st.stop()

tab_map, tab_table, tab_stats, tab_film = st.tabs(
    ["🗺️ Map", "📋 Table", "📊 Stats", "🎞️ Film detail"]
)

# ----------------------------------------------------------------------------
# Map
# ----------------------------------------------------------------------------
with tab_map:
    mdf = fdf.dropna(subset=["latitude", "longitude"])
    mdf = mdf[mdf["latitude"].between(-90, 90) & mdf["longitude"].between(-180, 180)]

    if mdf.empty:
        st.info("None of the filtered rows have valid coordinates.")
    else:
        if len(mdf) > MAX_MAP_POINTS:
            st.caption(
                f"Showing a random sample of {MAX_MAP_POINTS:,} of {len(mdf):,} points."
            )
            mdf = mdf.sample(MAX_MAP_POINTS, random_state=0)

        map_df = pd.DataFrame({
            "lat": mdf["latitude"].astype(float),
            "lon": mdf["longitude"].astype(float),
            "film": mdf["film_title"].fillna("").astype(str),
            "year": mdf["year"].astype(str) if "year" in mdf.columns else "",
            "location": mdf["location_name"].fillna("").astype(str),
            "country": mdf["country"].fillna("").astype(str) if "country" in mdf.columns else "",
        })

        layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position="[lon, lat]",
            get_radius=40000,
            radius_min_pixels=3,
            radius_max_pixels=12,
            get_fill_color=[230, 57, 70, 160],
            pickable=True,
        )
        view = pdk.ViewState(
            latitude=float(map_df["lat"].mean()),
            longitude=float(map_df["lon"].mean()),
            zoom=1.5 if len(map_df) > 50 else 3,
        )
        st.pydeck_chart(
            pdk.Deck(
                layers=[layer],
                initial_view_state=view,
                tooltip={
                    "html": "<b>{film}</b> ({year})<br/>{location}, {country}",
                    "style": {"color": "white"},
                },
            ),
            width="stretch",
        )

# ----------------------------------------------------------------------------
# Table
# ----------------------------------------------------------------------------
with tab_table:
    st.dataframe(
        fdf[display_cols],
        width="stretch",
        hide_index=True,
        height=550,
        column_config={
            "wikipedia": st.column_config.LinkColumn("wikipedia"),
            "year": st.column_config.NumberColumn("year", format="%d"),
        },
    )
    st.download_button(
        "⬇️ Download filtered CSV",
        data=fdf[display_cols].to_csv(index=False).encode("utf-8"),
        file_name="film_locations_filtered.csv",
        mime="text/csv",
    )

# ----------------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------------
with tab_stats:
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Top countries")
        if "country" in fdf.columns:
            st.bar_chart(fdf["country"].value_counts().head(15))

        st.subheader("Top locations")
        st.bar_chart(fdf["location_name"].value_counts().head(15))

    with c2:
        st.subheader("Rows per year")
        if "year" in fdf.columns:
            by_year = fdf.dropna(subset=["year"]).groupby("year").size()
            by_year.index = by_year.index.astype(int)
            st.bar_chart(by_year)


    # ---------------- Genre analysis (counted per film, not per location row) ----
    st.divider()
    st.header("Genres")

    films_df = fdf.drop_duplicates(subset=["film_title", "year"])
    films_g = films_df[films_df["_genres"].apply(len) > 0]

    if films_g.empty:
        st.info("No genre information for the current selection.")
    else:
        st.caption(
            f"Based on {len(films_g):,} unique films with genre info "
            f"(a film with 3 genres counts once in each)."
        )

        # 1) Genre distribution
        g_counts = (
            films_g["_genres"].explode().value_counts()
            .rename_axis("genre").reset_index(name="films")
        )
        g_counts["share"] = g_counts["films"] / len(films_g)

        st.subheader("Genre distribution")
        dist = (
            alt.Chart(g_counts)
            .mark_bar()
            .encode(
                y=alt.Y("genre:N", sort="-x", title=None),
                x=alt.X("films:Q", title="Films"),
                tooltip=[
                    alt.Tooltip("genre:N"),
                    alt.Tooltip("films:Q", title="Films", format=","),
                    alt.Tooltip("share:Q", title="% of films", format=".1%"),
                ],
            )
            .properties(height=max(200, 24 * len(g_counts)))
        )
        st.altair_chart(dist, width="stretch")

        # 2) Cross-genre matrix (co-occurrence)
        st.subheader("Cross-genre mix")
        mode = st.radio(
            "Show",
            ["Number of films", "% of row genre"],
            horizontal=True,
            key="f_cross_mode",
            help="'% of row genre': of all films in the row genre, the share that "
                 "also have the column genre (e.g. Romance films that are also Comedy).",
        )

        order = g_counts["genre"].tolist()
        onehot = (
            films_g["_genres"].explode().reset_index()
            .assign(v=1)
            .pivot_table(index="index", columns="_genres", values="v",
                         aggfunc="max", fill_value=0)
            .reindex(columns=order, fill_value=0)
        )
        co = onehot.T.dot(onehot)  # genre x genre co-occurrence counts
        co.index.name = None
        co.columns.name = None
        totals = pd.Series(co.values.diagonal(), index=co.index)

        long = co.stack().rename("films").reset_index()
        long.columns = ["row_genre", "col_genre", "films"]
        long = long[long["row_genre"] != long["col_genre"]]  # hide diagonal
        long["pct"] = long["films"] / long["row_genre"].map(totals)

        value_col = "films" if mode == "Number of films" else "pct"
        fmt = ",.0f" if value_col == "films" else ".0%"

        base = alt.Chart(long).encode(
            x=alt.X("col_genre:N", sort=order, title=None,
                    axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("row_genre:N", sort=order, title=None),
        )
        heat = base.mark_rect().encode(
            color=alt.Color(f"{value_col}:Q", title=None, scale=alt.Scale(scheme="reds")),
            tooltip=[
                alt.Tooltip("row_genre:N", title="Genre"),
                alt.Tooltip("col_genre:N", title="Also"),
                alt.Tooltip("films:Q", title="Films", format=","),
                alt.Tooltip("pct:Q", title=f"% of row genre", format=".1%"),
            ],
        )
        labels = base.mark_text(fontSize=9).encode(
            text=alt.Text(f"{value_col}:Q", format=fmt),
            color=alt.condition(
                alt.datum[value_col] > long[value_col].max() * 0.55,
                alt.value("white"), alt.value("black"),
            ),
        )
        st.altair_chart(
            (heat + labels).properties(height=max(300, 28 * len(order))),
            width="stretch",
        )

        # 3) Most common exact mixes
        st.subheader("Most common genre mixes")
        combos = films_g[films_g["_genres"].apply(len) >= 2]["_genres"].apply(
            lambda gs: " + ".join(sorted(set(gs)))
        )
        if combos.empty:
            st.info("No multi-genre films in this selection.")
        else:
            top_n = st.slider("How many mixes", 5, 30, 15, key="f_combo_n")
            top = combos.value_counts().head(top_n).rename_axis("mix").reset_index(name="films")
            st.altair_chart(
                alt.Chart(top).mark_bar().encode(
                    y=alt.Y("mix:N", sort="-x", title=None),
                    x=alt.X("films:Q", title="Films"),
                    tooltip=["mix:N", alt.Tooltip("films:Q", format=",")],
                ).properties(height=max(200, 24 * len(top))),
                width="stretch",
            )

    if "match_status" in fdf.columns:
        st.subheader("Match status")
        st.bar_chart(fdf["match_status"].fillna("(none)").value_counts())

# ----------------------------------------------------------------------------
# Film detail
# ----------------------------------------------------------------------------
with tab_film:
    films = sorted_unique(fdf["film_title"])
    choice = st.selectbox("Pick a film", films, index=None, placeholder="Search a film…")
    if choice:
        fd = df[df["film_title"] == choice]
        first = fd.iloc[0]
        left, right = st.columns([1, 2])
        with left:
            st.subheader(choice)
            if pd.notna(first.get("year")):
                st.write(f"**Year:** {int(first['year'])}")
            if pd.notna(first.get("genre_names")):
                st.write(f"**Genres:** {first['genre_names']}")
            if pd.notna(first.get("tmdb_title")):
                st.write(f"**TMDB title:** {first['tmdb_title']}")
            if pd.notna(first.get("tmdb_id")):
                st.write(f"**TMDB id:** {first['tmdb_id']}")
            if pd.notna(first.get("wikipedia")):
                st.markdown(f"[Wikipedia]({first['wikipedia']})")
            st.write(f"**Locations:** {fd['location_name'].nunique()}")
        with right:
            fd_map = fd.dropna(subset=["latitude", "longitude"])
            if fd_map.empty:
                st.info("No coordinates for this film.")
            else:
                st.map(
                    fd_map.rename(columns={"latitude": "lat", "longitude": "lon"})[["lat", "lon"]]
                )
        st.dataframe(
            fd[display_cols], width="stretch", hide_index=True,
            column_config={"wikipedia": st.column_config.LinkColumn("wikipedia")},
        )
