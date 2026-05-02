import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path

st.set_page_config(page_title="Dota 2 Pro Match Analysis", layout="wide")

DATA_PATH = Path(__file__).parent / "data" / "matches_flat.csv"

ANON_NAMES = {"Radiant", "Dire"}


@st.cache_data
def load_data():
    df = pd.read_csv(DATA_PATH)
    df["start_time"] = pd.to_datetime(df["start_time"])
    df["total_roshan"] = df["radiant_roshan_kills"] + df["dire_roshan_kills"]
    df["total_kills"] = df["radiant_score"] + df["dire_score"]
    df["total_barracks"] = df["radiant_barracks_lost"] + df["dire_barracks_lost"]
    df["both_lost_barracks"] = (df["radiant_barracks_lost"] >= 1) & (df["dire_barracks_lost"] >= 1)
    df["patch_label"] = df["patch"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "Unknown")
    return df


@st.cache_data
def build_team_perspective(df_hash: str, df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "match_id", "league_name", "patch_label", "start_time",
        "duration_mins", "radiant_win", "first_roshan_team", "first_roshan_time_mins",
        "total_roshan", "total_kills", "total_barracks", "both_lost_barracks",
    ]
    radiant = df[base_cols].copy()
    radiant["team_name"] = df["radiant_team_name"].values
    radiant["team_roshan_kills"] = df["radiant_roshan_kills"].values
    radiant["team_kills"] = df["radiant_score"].values
    radiant["team_barracks_killed"] = df["dire_barracks_lost"].values
    radiant["side"] = "Radiant"
    radiant["team_won"] = df["radiant_win"].values
    radiant["got_first_roshan"] = (df["first_roshan_team"] == "radiant").values

    dire = df[base_cols].copy()
    dire["team_name"] = df["dire_team_name"].values
    dire["team_roshan_kills"] = df["dire_roshan_kills"].values
    dire["team_kills"] = df["dire_score"].values
    dire["team_barracks_killed"] = df["radiant_barracks_lost"].values
    dire["side"] = "Dire"
    dire["team_won"] = (~df["radiant_win"]).values
    dire["got_first_roshan"] = (df["first_roshan_team"] == "dire").values

    return pd.concat([radiant, dire], ignore_index=True)


raw = load_data()
team_persp_full = build_team_perspective(str(len(raw)), raw)

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.title("Filters")

all_leagues = sorted(raw["league_name"].dropna().unique())
selected_leagues = st.sidebar.multiselect("Tournament / League", all_leagues, default=all_leagues)
league_filtered = raw[raw["league_name"].isin(selected_leagues)] if selected_leagues else raw

all_teams = sorted(
    set(league_filtered["radiant_team_name"].dropna())
    | set(league_filtered["dire_team_name"].dropna())
    - ANON_NAMES
)
selected_teams = st.sidebar.multiselect("Team", all_teams, default=[])

all_patches = sorted(
    raw["patch_label"].dropna().unique(),
    key=lambda x: float(x) if x != "Unknown" else 0,
)
selected_patches = st.sidebar.multiselect("Patch", all_patches, default=all_patches)

side_filter = "Both sides"
if len(selected_teams) == 1:
    side_filter = st.sidebar.radio("Side (single team)", ["Both sides", "As Radiant only", "As Dire only"])

# ── Apply filters ─────────────────────────────────────────────────────────────
filtered = league_filtered.copy()
if selected_teams:
    filtered = filtered[
        filtered["radiant_team_name"].isin(selected_teams)
        | filtered["dire_team_name"].isin(selected_teams)
    ]
if selected_patches:
    filtered = filtered[filtered["patch_label"].isin(selected_patches)]

team_filtered = team_persp_full[~team_persp_full["team_name"].isin(ANON_NAMES)].copy()
if selected_leagues:
    team_filtered = team_filtered[team_filtered["league_name"].isin(selected_leagues)]
if selected_teams:
    team_filtered = team_filtered[team_filtered["team_name"].isin(selected_teams)]
    if side_filter == "As Radiant only":
        team_filtered = team_filtered[team_filtered["side"] == "Radiant"]
    elif side_filter == "As Dire only":
        team_filtered = team_filtered[team_filtered["side"] == "Dire"]
if selected_patches:
    team_filtered = team_filtered[team_filtered["patch_label"].isin(selected_patches)]

st.sidebar.metric("Matches selected", len(filtered))

if len(filtered) == 0:
    st.warning("No matches match the current filters. Try broadening your selection.")
    st.stop()

# ── Page ──────────────────────────────────────────────────────────────────────
st.title("Dota 2 Pro Match Analysis")
tab1, tab2, tab3 = st.tabs(["Team", "Tournament", "Meta Trends"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — TEAM
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    if len(team_filtered) == 0:
        st.info("No team data for the current selection.")
        st.stop()

    if len(selected_teams) == 1:
        st.subheader(selected_teams[0])
    elif len(selected_teams) > 1:
        st.subheader(f"{len(selected_teams)} teams selected")
    else:
        st.subheader("All Teams")

    # ── KPIs ─────────────────────────────────────────────────────────────────
    avg_team_rosh     = team_filtered["team_roshan_kills"].mean()
    pct_2plus_rosh    = (team_filtered["team_roshan_kills"] >= 2).mean() * 100
    avg_total_rosh    = team_filtered["total_roshan"].mean()
    pct_3plus_rosh    = (team_filtered["total_roshan"] >= 3).mean() * 100
    avg_team_kills    = team_filtered["team_kills"].mean()
    avg_total_kills   = team_filtered["total_kills"].mean()
    avg_team_barr     = team_filtered["team_barracks_killed"].mean()
    avg_total_barr    = team_filtered["total_barracks"].mean()
    pct_both_barr     = team_filtered["both_lost_barracks"].mean() * 100
    avg_duration      = filtered["duration_mins"].mean()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Avg Team Roshans/Game", f"{avg_team_rosh:.2f}")
    c2.metric("Team 2+ Roshans", f"{pct_2plus_rosh:.1f}%")
    c3.metric("Avg Total Roshans/Game", f"{avg_total_rosh:.2f}")
    c4.metric("Match 3+ Roshans", f"{pct_3plus_rosh:.1f}%")
    c5.metric("Avg Game Length", f"{avg_duration:.1f} min")

    c6, c7, c8, c9 = st.columns(4)
    c6.metric("Avg Team Kills/Game", f"{avg_team_kills:.1f}")
    c7.metric("Avg Total Kills/Game", f"{avg_total_kills:.1f}")
    c8.metric("Avg Team Barracks/Game", f"{avg_team_barr:.2f}")
    c9.metric("Both Lost Barracks", f"{pct_both_barr:.1f}%")

    st.divider()

    # ── Roshan ───────────────────────────────────────────────────────────────
    st.subheader("Roshan")
    col_rp, col_rt = st.columns(2)

    rosh_patch = (
        team_filtered.groupby("patch_label")
        .agg(avg_team=("team_roshan_kills", "mean"), avg_total=("total_roshan", "mean"))
        .reset_index().sort_values("patch_label")
    )
    fig_rp = go.Figure([
        go.Bar(name="Team Kills", x=rosh_patch["patch_label"], y=rosh_patch["avg_team"], marker_color="#4C9BE8"),
        go.Bar(name="Total in Match", x=rosh_patch["patch_label"], y=rosh_patch["avg_total"], marker_color="#A8D1F5"),
    ])
    fig_rp.update_layout(barmode="group", title="By Patch", xaxis_title="Patch",
                         yaxis_title="Avg / Game", legend=dict(orientation="h", y=-0.35))
    col_rp.plotly_chart(fig_rp, use_container_width=True)

    rosh_tourn = (
        team_filtered.groupby("league_name")
        .agg(avg_team=("team_roshan_kills", "mean"), avg_total=("total_roshan", "mean"))
        .reset_index().sort_values("avg_total", ascending=True)
    )
    fig_rt = go.Figure([
        go.Bar(name="Team Kills", x=rosh_tourn["avg_team"], y=rosh_tourn["league_name"], orientation="h", marker_color="#4C9BE8"),
        go.Bar(name="Total in Match", x=rosh_tourn["avg_total"], y=rosh_tourn["league_name"], orientation="h", marker_color="#A8D1F5"),
    ])
    fig_rt.update_layout(barmode="group", title="By Tournament", xaxis_title="Avg / Game",
                         yaxis_title="", legend=dict(orientation="h", y=-0.25),
                         height=max(300, len(rosh_tourn) * 45))
    col_rt.plotly_chart(fig_rt, use_container_width=True)

    st.divider()

    # ── Kills ─────────────────────────────────────────────────────────────────
    st.subheader("Kills")
    col_kp, col_kt = st.columns(2)

    kills_patch = (
        team_filtered.groupby("patch_label")
        .agg(avg_team=("team_kills", "mean"), avg_total=("total_kills", "mean"))
        .reset_index().sort_values("patch_label")
    )
    fig_kp = go.Figure([
        go.Bar(name="Team Kills", x=kills_patch["patch_label"], y=kills_patch["avg_team"], marker_color="#E88C4C"),
        go.Bar(name="Total in Match", x=kills_patch["patch_label"], y=kills_patch["avg_total"], marker_color="#F5C8A8"),
    ])
    fig_kp.update_layout(barmode="group", title="By Patch", xaxis_title="Patch",
                         yaxis_title="Avg / Game", legend=dict(orientation="h", y=-0.35))
    col_kp.plotly_chart(fig_kp, use_container_width=True)

    kills_tourn = (
        team_filtered.groupby("league_name")
        .agg(avg_team=("team_kills", "mean"), avg_total=("total_kills", "mean"))
        .reset_index().sort_values("avg_total", ascending=True)
    )
    fig_kt = go.Figure([
        go.Bar(name="Team Kills", x=kills_tourn["avg_team"], y=kills_tourn["league_name"], orientation="h", marker_color="#E88C4C"),
        go.Bar(name="Total in Match", x=kills_tourn["avg_total"], y=kills_tourn["league_name"], orientation="h", marker_color="#F5C8A8"),
    ])
    fig_kt.update_layout(barmode="group", title="By Tournament", xaxis_title="Avg / Game",
                         yaxis_title="", legend=dict(orientation="h", y=-0.25),
                         height=max(300, len(kills_tourn) * 45))
    col_kt.plotly_chart(fig_kt, use_container_width=True)

    st.divider()

    # ── Barracks ──────────────────────────────────────────────────────────────
    st.subheader("Barracks")
    col_bp, col_bt = st.columns(2)

    barr_patch = (
        team_filtered.groupby("patch_label")
        .agg(
            avg_team=("team_barracks_killed", "mean"),
            avg_total=("total_barracks", "mean"),
            pct_both=("both_lost_barracks", "mean"),
        )
        .reset_index().sort_values("patch_label")
    )
    barr_patch["pct_both_pct"] = barr_patch["pct_both"] * 100

    fig_bp = go.Figure([
        go.Bar(name="Team Killed", x=barr_patch["patch_label"], y=barr_patch["avg_team"], marker_color="#4CE87A"),
        go.Bar(name="Total in Match", x=barr_patch["patch_label"], y=barr_patch["avg_total"], marker_color="#A8F5C0"),
    ])
    fig_bp.update_layout(barmode="group", title="Avg Barracks Destroyed — By Patch",
                         xaxis_title="Patch", yaxis_title="Avg / Game",
                         legend=dict(orientation="h", y=-0.35))
    col_bp.plotly_chart(fig_bp, use_container_width=True)

    fig_bp2 = go.Figure([
        go.Bar(x=barr_patch["patch_label"], y=barr_patch["pct_both_pct"],
               marker_color="#E8C84C",
               text=barr_patch["pct_both_pct"].apply(lambda v: f"{v:.1f}%"),
               textposition="outside"),
    ])
    fig_bp2.update_layout(title="% Games Both Teams Lost Barracks — By Patch",
                          xaxis_title="Patch", yaxis_title="%",
                          yaxis_range=[0, max(barr_patch["pct_both_pct"].max() * 1.3, 10)],
                          showlegend=False)
    col_bp.plotly_chart(fig_bp2, use_container_width=True)

    barr_tourn = (
        team_filtered.groupby("league_name")
        .agg(
            avg_team=("team_barracks_killed", "mean"),
            avg_total=("total_barracks", "mean"),
            pct_both=("both_lost_barracks", "mean"),
        )
        .reset_index().sort_values("avg_total", ascending=True)
    )
    barr_tourn["pct_both_pct"] = barr_tourn["pct_both"] * 100

    fig_bt = go.Figure([
        go.Bar(name="Team Killed", x=barr_tourn["avg_team"], y=barr_tourn["league_name"], orientation="h", marker_color="#4CE87A"),
        go.Bar(name="Total in Match", x=barr_tourn["avg_total"], y=barr_tourn["league_name"], orientation="h", marker_color="#A8F5C0"),
    ])
    fig_bt.update_layout(barmode="group", title="Avg Barracks Destroyed — By Tournament",
                         xaxis_title="Avg / Game", yaxis_title="",
                         legend=dict(orientation="h", y=-0.25),
                         height=max(300, len(barr_tourn) * 45))
    col_bt.plotly_chart(fig_bt, use_container_width=True)

    fig_bt2 = go.Figure([
        go.Bar(x=barr_tourn["pct_both_pct"], y=barr_tourn["league_name"], orientation="h",
               marker_color="#E8C84C",
               text=barr_tourn["pct_both_pct"].apply(lambda v: f"{v:.1f}%"),
               textposition="outside"),
    ])
    max_pct = barr_tourn["pct_both_pct"].max()
    fig_bt2.update_layout(title="% Games Both Teams Lost Barracks — By Tournament",
                          xaxis_title="%", yaxis_title="",
                          xaxis_range=[0, min(100, max(max_pct * 1.3 + 5, 10))],
                          showlegend=False,
                          height=max(300, len(barr_tourn) * 45))
    col_bt.plotly_chart(fig_bt2, use_container_width=True)

    st.divider()

    # ── Game Length ───────────────────────────────────────────────────────────
    st.subheader("Game Length")
    col_gl1, col_gl2 = st.columns(2)

    mean_dur = filtered["duration_mins"].mean()
    fig_gl = px.histogram(filtered, x="duration_mins", nbins=25,
                          title="Distribution of Game Lengths",
                          labels={"duration_mins": "Duration (min)", "count": "Games"},
                          color_discrete_sequence=["#9B59B6"])
    fig_gl.add_vline(x=mean_dur, line_dash="dash", line_color="orange",
                     annotation_text=f"mean = {mean_dur:.1f} min", annotation_position="top right")
    fig_gl.update_layout(showlegend=False)
    col_gl1.plotly_chart(fig_gl, use_container_width=True)

    fig_gl_box = px.box(filtered, x="patch_label", y="duration_mins",
                        title="Game Length by Patch",
                        labels={"patch_label": "Patch", "duration_mins": "Duration (min)"},
                        color="patch_label",
                        color_discrete_sequence=px.colors.qualitative.Set2)
    fig_gl_box.update_layout(showlegend=False)
    col_gl2.plotly_chart(fig_gl_box, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TOURNAMENT
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Tournament Overview")

    tourn_stats = (
        filtered.groupby("league_name")
        .agg(
            matches=("match_id", "nunique"),
            first_match=("start_time", "min"),
            last_match=("start_time", "max"),
            avg_roshan=("total_roshan", "mean"),
            pct_3plus_roshan=("total_roshan", lambda x: (x >= 3).mean() * 100),
            avg_kills=("total_kills", "mean"),
            avg_barracks=("total_barracks", "mean"),
            pct_both_barracks=("both_lost_barracks", lambda x: x.mean() * 100),
            avg_duration=("duration_mins", "mean"),
        )
        .reset_index()
        .sort_values("first_match")
    )

    display_t = tourn_stats.copy()
    display_t["Period"] = display_t.apply(
        lambda r: f"{r['first_match'].strftime('%b %Y')} – {r['last_match'].strftime('%b %Y')}", axis=1
    )
    display_t = display_t.drop(columns=["first_match", "last_match"])
    for col in ["avg_roshan", "avg_barracks"]:
        display_t[col] = display_t[col].round(2)
    for col in ["pct_3plus_roshan", "pct_both_barracks", "avg_duration"]:
        display_t[col] = display_t[col].round(1)
    display_t["avg_kills"] = display_t["avg_kills"].round(1)
    display_t = display_t.rename(columns={
        "league_name": "Tournament", "matches": "Matches",
        "avg_roshan": "Avg Roshans/Game", "pct_3plus_roshan": "3+ Roshans %",
        "avg_kills": "Avg Kills/Game",
        "avg_barracks": "Avg Barracks/Game", "pct_both_barracks": "Both Lost Barracks %",
        "avg_duration": "Avg Duration (min)",
    })
    st.dataframe(
        display_t[["Tournament", "Matches", "Period", "Avg Roshans/Game", "3+ Roshans %",
                   "Avg Kills/Game", "Avg Barracks/Game", "Both Lost Barracks %", "Avg Duration (min)"]],
        use_container_width=True, hide_index=True
    )

    st.divider()

    col1, col2 = st.columns(2)

    fig_t1 = px.bar(tourn_stats, x="league_name", y="avg_roshan",
                    color="avg_roshan", color_continuous_scale="Blues",
                    title="Avg Total Roshans per Game",
                    labels={"league_name": "", "avg_roshan": "Avg"},
                    text=tourn_stats["avg_roshan"].round(2))
    fig_t1.update_traces(textposition="outside")
    fig_t1.update_layout(coloraxis_showscale=False, xaxis_tickangle=-30)
    col1.plotly_chart(fig_t1, use_container_width=True)

    fig_t2 = px.bar(tourn_stats, x="league_name", y="avg_kills",
                    color="avg_kills", color_continuous_scale="Oranges",
                    title="Avg Total Kills per Game",
                    labels={"league_name": "", "avg_kills": "Avg"},
                    text=tourn_stats["avg_kills"].round(1))
    fig_t2.update_traces(textposition="outside")
    fig_t2.update_layout(coloraxis_showscale=False, xaxis_tickangle=-30)
    col2.plotly_chart(fig_t2, use_container_width=True)

    col3, col4 = st.columns(2)

    fig_t3 = px.bar(tourn_stats, x="league_name", y="avg_barracks",
                    color="avg_barracks", color_continuous_scale="Greens",
                    title="Avg Total Barracks Destroyed per Game",
                    labels={"league_name": "", "avg_barracks": "Avg"},
                    text=tourn_stats["avg_barracks"].round(2))
    fig_t3.update_traces(textposition="outside")
    fig_t3.update_layout(coloraxis_showscale=False, xaxis_tickangle=-30)
    col3.plotly_chart(fig_t3, use_container_width=True)

    fig_t4 = px.bar(tourn_stats, x="league_name", y="avg_duration",
                    color="avg_duration", color_continuous_scale="Purples",
                    title="Avg Game Duration (min)",
                    labels={"league_name": "", "avg_duration": "Minutes"},
                    text=tourn_stats["avg_duration"].round(1))
    fig_t4.update_traces(textposition="outside")
    fig_t4.update_layout(coloraxis_showscale=False, xaxis_tickangle=-30)
    col4.plotly_chart(fig_t4, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — META TRENDS
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("Meta Trends by Patch")

    patch_stats = (
        filtered.groupby("patch_label")
        .agg(
            matches=("match_id", "nunique"),
            avg_roshan=("total_roshan", "mean"),
            pct_3plus_roshan=("total_roshan", lambda x: (x >= 3).mean() * 100),
            avg_kills=("total_kills", "mean"),
            avg_barracks=("total_barracks", "mean"),
            pct_both_barracks=("both_lost_barracks", lambda x: x.mean() * 100),
            avg_duration=("duration_mins", "mean"),
        )
        .reset_index()
        .sort_values("patch_label")
    )

    patch_cols = st.columns(len(patch_stats))
    for i, (_, row) in enumerate(patch_stats.iterrows()):
        with patch_cols[i]:
            st.markdown(f"**Patch {row['patch_label']}**")
            st.caption(f"{int(row['matches'])} matches")
            st.metric("Avg Roshans/Game", f"{row['avg_roshan']:.2f}")
            st.metric("3+ Roshans", f"{row['pct_3plus_roshan']:.1f}%")
            st.metric("Avg Kills/Game", f"{row['avg_kills']:.1f}")
            st.metric("Avg Barracks/Game", f"{row['avg_barracks']:.2f}")
            st.metric("Both Lost Barracks", f"{row['pct_both_barracks']:.1f}%")
            st.metric("Avg Duration", f"{row['avg_duration']:.1f} min")

    st.divider()

    col1, col2 = st.columns(2)

    fig_m1 = px.bar(patch_stats, x="patch_label", y="avg_roshan",
                    color="patch_label", color_discrete_sequence=px.colors.qualitative.Set2,
                    title="Avg Total Roshans per Game by Patch",
                    labels={"patch_label": "Patch", "avg_roshan": "Avg / Game"},
                    text=patch_stats["avg_roshan"].round(2))
    fig_m1.update_traces(textposition="outside")
    fig_m1.update_layout(showlegend=False)
    col1.plotly_chart(fig_m1, use_container_width=True)

    fig_m2 = px.bar(patch_stats, x="patch_label", y="avg_kills",
                    color="patch_label", color_discrete_sequence=px.colors.qualitative.Set2,
                    title="Avg Total Kills per Game by Patch",
                    labels={"patch_label": "Patch", "avg_kills": "Avg / Game"},
                    text=patch_stats["avg_kills"].round(1))
    fig_m2.update_traces(textposition="outside")
    fig_m2.update_layout(showlegend=False)
    col2.plotly_chart(fig_m2, use_container_width=True)

    col3, col4 = st.columns(2)

    fig_m3 = px.bar(patch_stats, x="patch_label", y="avg_barracks",
                    color="patch_label", color_discrete_sequence=px.colors.qualitative.Set2,
                    title="Avg Total Barracks Destroyed per Game by Patch",
                    labels={"patch_label": "Patch", "avg_barracks": "Avg / Game"},
                    text=patch_stats["avg_barracks"].round(2))
    fig_m3.update_traces(textposition="outside")
    fig_m3.update_layout(showlegend=False)
    col3.plotly_chart(fig_m3, use_container_width=True)

    fig_m4 = px.violin(filtered, x="patch_label", y="duration_mins",
                       box=True, points=False,
                       title="Game Length Distribution by Patch",
                       labels={"patch_label": "Patch", "duration_mins": "Duration (min)"},
                       color="patch_label",
                       color_discrete_sequence=px.colors.qualitative.Set2)
    fig_m4.update_layout(showlegend=False)
    col4.plotly_chart(fig_m4, use_container_width=True)
