import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path

st.set_page_config(page_title="Dota 2 Roshan Dashboard", layout="wide")

DATA_PATH = Path(__file__).parent / "data" / "matches_flat.csv"


@st.cache_data
def load_data():
    df = pd.read_csv(DATA_PATH)
    df["start_time"] = pd.to_datetime(df["start_time"])
    df["total_roshan"] = df["radiant_roshan_kills"] + df["dire_roshan_kills"]
    df["patch_label"] = df["patch"].apply(
        lambda x: f"{x:.2f}" if pd.notna(x) else "Unknown"
    )
    return df


@st.cache_data
def build_team_perspective(df_hash: str, df: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "match_id", "league_name", "patch_label", "start_time",
        "duration_mins", "radiant_win", "first_roshan_team", "first_roshan_time_mins",
    ]
    radiant = df[keep + ["radiant_team_name", "radiant_roshan_kills"]].copy()
    radiant.columns = keep + ["team_name", "team_roshan_kills"]
    radiant["side"] = "radiant"
    radiant["team_won"] = radiant["radiant_win"]
    radiant["got_first_roshan"] = radiant["first_roshan_team"] == "radiant"

    dire = df[keep + ["dire_team_name", "dire_roshan_kills"]].copy()
    dire.columns = keep + ["team_name", "team_roshan_kills"]
    dire["side"] = "dire"
    dire["team_won"] = ~dire["radiant_win"]
    dire["got_first_roshan"] = dire["first_roshan_team"] == "dire"

    return pd.concat([radiant, dire], ignore_index=True)


raw = load_data()
team_persp_full = build_team_perspective(str(len(raw)), raw)

# --- Sidebar ---
st.sidebar.title("Filters")

all_leagues = sorted(raw["league_name"].dropna().unique())
selected_leagues = st.sidebar.multiselect("Tournament / League", all_leagues, default=all_leagues)

league_filtered = raw[raw["league_name"].isin(selected_leagues)] if selected_leagues else raw

all_teams = sorted(
    set(league_filtered["radiant_team_name"].dropna())
    | set(league_filtered["dire_team_name"].dropna())
)
selected_teams = st.sidebar.multiselect("Team", all_teams, default=all_teams)

all_patches = sorted(
    raw["patch_label"].dropna().unique(),
    key=lambda x: float(x) if x != "Unknown" else 0,
)
selected_patches = st.sidebar.multiselect("Patch", all_patches, default=all_patches)

side_filter = "Both sides"
if len(selected_teams) == 1:
    side_filter = st.sidebar.radio(
        "Side (single team)", ["Both sides", "As Radiant only", "As Dire only"]
    )

# --- Apply filters ---
filtered = league_filtered.copy()
if selected_teams:
    filtered = filtered[
        filtered["radiant_team_name"].isin(selected_teams)
        | filtered["dire_team_name"].isin(selected_teams)
    ]
if selected_patches:
    filtered = filtered[filtered["patch_label"].isin(selected_patches)]

team_filtered = team_persp_full.copy()
if selected_leagues:
    team_filtered = team_filtered[team_filtered["league_name"].isin(selected_leagues)]
if selected_teams:
    team_filtered = team_filtered[team_filtered["team_name"].isin(selected_teams)]
    if side_filter == "As Radiant only":
        team_filtered = team_filtered[team_filtered["side"] == "radiant"]
    elif side_filter == "As Dire only":
        team_filtered = team_filtered[team_filtered["side"] == "dire"]
if selected_patches:
    team_filtered = team_filtered[team_filtered["patch_label"].isin(selected_patches)]

st.sidebar.metric("Matches selected", len(filtered))

# --- Guard empty ---
if len(filtered) == 0:
    st.warning("No matches match the current filters. Try broadening your selection.")
    st.stop()

# --- Title ---
st.title("Dota 2 Roshan Dashboard")

# --- Section 1: Metrics ---
roshan_with_kill = filtered[filtered["total_roshan"] > 0]
first_rosh_games = filtered[filtered["first_roshan_team"].notna()]
first_rosh_wins = first_rosh_games[
    ((first_rosh_games["first_roshan_team"] == "radiant") & first_rosh_games["radiant_win"])
    | ((first_rosh_games["first_roshan_team"] == "dire") & ~first_rosh_games["radiant_win"])
]
win_rate = len(first_rosh_wins) / len(first_rosh_games) * 100 if len(first_rosh_games) > 0 else 0
avg_first_time = filtered["first_roshan_time_mins"].mean()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Matches", len(filtered))
c2.metric("Avg Roshan Kills / Game", f"{filtered['total_roshan'].mean():.2f}")
c3.metric("First Roshan Win Rate", f"{win_rate:.1f}%")
c4.metric(
    "Avg First Roshan Time",
    f"{avg_first_time:.1f} min" if pd.notna(avg_first_time) else "N/A",
)

st.divider()

# --- Section 2: Kills per game histogram ---
st.subheader("Roshan Kills Per Game")
kill_counts = filtered["total_roshan"].value_counts().sort_index().reset_index()
kill_counts.columns = ["kills", "games"]
mean_val = filtered["total_roshan"].mean()

fig_hist = px.bar(
    kill_counts,
    x="kills",
    y="games",
    labels={"kills": "Total Roshan Kills in Game", "games": "Number of Games"},
    color="games",
    color_continuous_scale="Blues",
)
fig_hist.add_vline(
    x=mean_val,
    line_dash="dash",
    line_color="orange",
    annotation_text=f"mean = {mean_val:.2f}",
    annotation_position="top right",
)
fig_hist.update_layout(coloraxis_showscale=False, showlegend=False)
fig_hist.update_xaxes(dtick=1)
st.plotly_chart(fig_hist, width="stretch")

st.divider()

# --- Section 3: Avg kills per team ---
st.subheader("Average Roshan Kills Per Game by Team")
team_agg = (
    team_filtered.groupby("team_name")
    .agg(avg_kills=("team_roshan_kills", "mean"), matches=("match_id", "nunique"))
    .reset_index()
    .sort_values("avg_kills", ascending=True)
)
team_agg["label"] = team_agg.apply(lambda r: f"{r['team_name']} (N={r['matches']})", axis=1)

fig_team = px.bar(
    team_agg,
    x="avg_kills",
    y="label",
    orientation="h",
    color="avg_kills",
    color_continuous_scale="Blues",
    labels={"avg_kills": "Avg Roshan Kills / Game", "label": ""},
    text=team_agg["avg_kills"].apply(lambda v: f"{v:.2f}"),
)
fig_team.update_traces(textposition="outside")
fig_team.update_layout(coloraxis_showscale=False, height=max(400, len(team_agg) * 28))
st.plotly_chart(fig_team, width="stretch")

st.divider()

# --- Section 4: First Roshan timing ---
st.subheader("First Roshan Kill Timing")
timing_data = filtered["first_roshan_time_mins"].dropna()
median_time = timing_data.median()

fig_timing = px.histogram(
    timing_data,
    nbins=20,
    labels={"value": "First Roshan Kill Time (minutes)", "count": "Number of Games"},
)
fig_timing.add_vline(
    x=median_time,
    line_dash="dash",
    line_color="orange",
    annotation_text=f"median = {median_time:.1f} min",
    annotation_position="top right",
)
fig_timing.update_layout(showlegend=False)
st.caption(f"N = {len(timing_data):,} games where Roshan was killed ({len(filtered) - len(timing_data)} excluded — no Roshan killed)")
st.plotly_chart(fig_timing, width="stretch")

st.divider()

# --- Section 5: First Roshan win rate ---
st.subheader("Win Rate When Securing First Roshan")
col_left, col_right = st.columns(2)

with col_left:
    if len(first_rosh_games) > 0:
        won = len(first_rosh_wins)
        lost = len(first_rosh_games) - won
        fig_wr = go.Figure(go.Bar(
            x=["Won", "Lost"],
            y=[won, lost],
            marker_color=["#2ecc71", "#e74c3c"],
            text=[f"{won} ({win_rate:.1f}%)", f"{lost} ({100 - win_rate:.1f}%)"],
            textposition="outside",
        ))
        fig_wr.update_layout(
            title=f"Overall: {win_rate:.1f}% win rate (N={len(first_rosh_games)} games)",
            yaxis_title="Games",
            showlegend=False,
        )
        st.plotly_chart(fig_wr, width="stretch")
    else:
        st.info("No first Roshan data for the current filter selection.")

with col_right:
    if len(selected_teams) >= 1 and len(selected_teams) <= 10:
        team_wr = (
            team_filtered[team_filtered["first_roshan_team"].notna()]
            .groupby("team_name")
            .apply(
                lambda g: pd.Series({
                    "games_with_first_rosh": g["got_first_roshan"].sum(),
                    "wins_with_first_rosh": (g["got_first_roshan"] & g["team_won"]).sum(),
                    "total_games": len(g),
                }),
                include_groups=False,
            )
            .reset_index()
        )
        team_wr = team_wr[team_wr["games_with_first_rosh"] >= 5].copy()
        if len(team_wr) > 0:
            team_wr["win_rate_pct"] = team_wr["wins_with_first_rosh"] / team_wr["games_with_first_rosh"] * 100
            team_wr = team_wr.sort_values("win_rate_pct", ascending=True)
            team_wr["label"] = team_wr.apply(
                lambda r: f"{r['team_name']} (N={int(r['games_with_first_rosh'])})", axis=1
            )
            fig_team_wr = px.bar(
                team_wr,
                x="win_rate_pct",
                y="label",
                orientation="h",
                color="win_rate_pct",
                color_continuous_scale="RdYlGn",
                range_color=[40, 90],
                labels={"win_rate_pct": "Win Rate (%)", "label": ""},
                text=team_wr["win_rate_pct"].apply(lambda v: f"{v:.1f}%"),
            )
            fig_team_wr.update_traces(textposition="outside")
            fig_team_wr.update_layout(
                title="Win Rate After Securing First Roshan (by Team)",
                coloraxis_showscale=False,
            )
            fig_team_wr.update_xaxes(range=[0, 110])
            st.plotly_chart(fig_team_wr, width="stretch")
        else:
            st.info("Not enough data — need ≥5 first-Roshan games per team.")
    else:
        st.info("Select 1–10 teams to see per-team first Roshan win rates.")

st.divider()

# --- Section 6: Data table ---
with st.expander(f"Match data table ({len(filtered):,} matches)"):
    display_cols = [
        "start_time", "league_name", "patch_label",
        "radiant_team_name", "dire_team_name",
        "radiant_roshan_kills", "dire_roshan_kills", "total_roshan",
        "first_roshan_team", "first_roshan_time_mins",
        "aegis_stolen", "aegis_denied", "duration_mins", "radiant_win",
    ]
    display_df = filtered[display_cols].copy()
    display_df["start_time"] = display_df["start_time"].dt.date
    display_df = display_df.sort_values("start_time", ascending=False)
    st.dataframe(display_df, width="stretch")
