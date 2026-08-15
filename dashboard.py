# data: 2026-08-15 03:54
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path

from core import (
    CSV_DTYPES,
    GAP_OVERDUE,
    LIQUIPEDIA_ATTRIBUTION,
    awaiting_verdict,
    coverage_gaps,
    patch_sort_key,
)

st.set_page_config(page_title="Dota 2 Pro Match Analysis", layout="wide")

DATA_PATH = Path(__file__).parent / "data" / "matches_flat.csv"
META_PATH = Path(__file__).parent / "data" / "meta.json"
RESOLUTION_PATH = Path(__file__).parent / "data" / "tier1_resolution.json"

ANON_NAMES = {"Radiant", "Dire"}

# Where a proposed league is approved. Approval is merging a pull request, which
# is deliberately the only way: this app is public and deployed from git with an
# ephemeral filesystem, so an approve button here would mean a repository write
# token in a public app. Since `tier1-pipeline-automation/15` a proposed league
# carries the URL of its own pull request, and this is the fallback for a record
# written before that — the honest link, being the list it will appear in.
REPO_PULLS_URL = "https://github.com/Wadith-ops/dota2-pro-match-dashboard/pulls"

LIQUIPEDIA_TIER1_URL = "https://liquipedia.net/dota2/Tier_1_Tournaments"
CC_BY_SA_URL = "https://creativecommons.org/licenses/by-sa/3.0/"

# What an event with no name is called on the page. `coverage_gaps` reads the
# name with `.get`, so it can be absent, and a bare None in a joined sentence
# raises rather than reads oddly.
UNNAMED_EVENT = "(unnamed event)"


@st.cache_data(ttl=1800)
def load_data():
    # CSV_DTYPES keeps patch_label a string; the pipeline writes it, this only
    # stops pandas turning "7.40" back into 7.4 on the way in.
    df = pd.read_csv(DATA_PATH, dtype=CSV_DTYPES)
    df["start_time"] = pd.to_datetime(df["start_time"])
    # Blank reads back as NaN whatever dtype is asked for, and a bare `nan` in
    # a match history table reads as a fault rather than as "nothing to say".
    df["suspect_reason"] = df["suspect_reason"].fillna("")
    df["total_roshan"] = df["radiant_roshan_kills"] + df["dire_roshan_kills"]
    df["total_kills"] = df["radiant_score"] + df["dire_score"]
    df["total_barracks"] = df["radiant_barracks_lost"] + df["dire_barracks_lost"]
    df["total_towers"] = df["radiant_towers_lost"] + df["dire_towers_lost"]
    df["both_lost_barracks"] = (df["radiant_barracks_lost"] >= 1) & (df["dire_barracks_lost"] >= 1)
    df["both_teams_roshan"] = (df["radiant_roshan_kills"] >= 1) & (df["dire_roshan_kills"] >= 1)
    return df


@st.cache_data(ttl=1800)
def load_json(path: Path) -> dict:
    """
    One of the pipeline's committed side files, or {} when it is absent or
    malformed.

    Every caller degrades rather than fails: a page that cannot read
    `meta.json` still has the CSV to count, and one that cannot read the
    resolution still has every figure on it. A missing side file must never be
    the reason the dashboard is down.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def load_meta() -> dict:
    """What the last pipeline run recorded about its own coverage."""
    return load_json(META_PATH)


def load_resolution() -> dict:
    """Which Tier 1 event resolved to which OpenDota league, and which to none."""
    return load_json(RESOLUTION_PATH)


def measured(df: pd.DataFrame) -> pd.DataFrame:
    """
    The rows a figure may be computed from: everything except suspect matches.

    A suspect match is one whose objectives OpenDota never recorded, so its
    zeroes mean "unknown" and cannot be told apart from "nothing happened". One
    is a 96-minute game holding zero towers and zero Roshans. Averaging those
    zeroes in drags every objective metric down by a real amount.

    They are excluded from the figures, not from the dataset — every match
    history table still lists them, flagged. See ADR-0007.
    """
    return df[~df["is_suspect"]]


def note_exclusions(df: pd.DataFrame) -> None:
    """
    States how much of a selection the figures under it leave out.

    Silent when the count is zero, and never silent when it is not: an
    exclusion the reader cannot see is the thing this whole feature exists to
    stop.
    """
    count = int(df["is_suspect"].sum())
    if count == 0:
        return

    st.caption(
        f":orange[{count} of {len(df):,} matches excluded from the figures below "
        "— objective data missing. They remain in match history.]"
    )


def suspect_label(reason: str) -> str:
    """
    The match-history mark for a flagged row, blank for an ordinary match.

    The stored reason is machine-shaped — `no_towers_lost;no_hero_kills` — and
    this is the only place it is read by a person.
    """
    if not reason:
        return ""

    return "⚠ " + ", ".join(part.replace("_", " ") for part in reason.split(";"))


def by_patch(df: pd.DataFrame) -> pd.DataFrame:
    """
    Orders a per-patch frame by release.

    Sorting the labels as plain strings puts "7.9" after "7.40" and only lands
    "Unknown" last by accident of capitalisation. `core.patch_sort_key` is the
    one definition of patch order, and every patch axis is built from it —
    through here for a frame, through `patch_order` for a category list.
    """
    return df.sort_values("patch_label", key=lambda labels: labels.map(patch_sort_key))


def patch_order(df: pd.DataFrame) -> list:
    """The patch labels present in `df`, in release order."""
    return sorted(df["patch_label"].dropna().unique(), key=patch_sort_key)


def format_date(value) -> str | None:
    """Renders a date as '5 Aug 2026'. %-d is not portable to Windows, so the day
    is formatted separately."""
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(stamp):
        return None
    return f"{stamp.day} {stamp:%b %Y}"


def format_window(start, end) -> str:
    """
    An event's date window, as '13 Aug 2026 – 23 Aug 2026'.

    Liquipedia publishes "TBD" for an unscheduled event and the parser keeps
    that as no date, so a blank window is a real state and says so.
    """
    opens, closes = format_date(start), format_date(end)

    if opens and closes:
        return opens if opens == closes else f"{opens} – {closes}"

    return opens or closes or "Dates TBD"


def window_verdict(record: dict) -> str:
    """
    Whether the league in this row was the only candidate in its event's window,
    or won a contested one.

    A contested window is where the resolver made a *judgement* rather than
    found the single league that fitted, so it is the row most worth a second
    look before the verdict is given. The runners-up and their scores are in
    `data/tier1_resolution.json`.
    """
    if not record["ambiguous"]:
        return "Only candidate"

    others = record["runners_up"]
    return f"⚠ Contested — {others} other candidate{'' if others == 1 else 's'}"


def coverage_line(df: pd.DataFrame, meta: dict) -> str:
    """
    States what the dataset holds, at the point of use.

    Match and tournament counts are derived from the CSV actually loaded, never
    from meta.json, so the line can never advertise coverage the page does not
    have. Only what the CSV cannot supply — when the pipeline last ran, and how
    many matches were excluded — is read from meta.
    """
    parts = []

    # "Data updated", not "Data as of": generated_at only reaches production when
    # the CSV changes, so it marks the last run that changed data, not the last
    # run. `latest match` is the staleness signal.
    generated = format_date(meta.get("generated_at"))
    if generated:
        parts.append(f"Data updated **{generated}**")

    latest = format_date(df["start_time"].max())
    if latest:
        parts.append(f"latest match **{latest}**")

    parts.append(f"**{len(df):,}** matches")
    parts.append(f"**{df['league_name'].nunique()}** tournaments")

    # Rendered only once the count is real. It read null before suspect matches
    # were classified, and the clause stayed off rather than claim "0 excluded"
    # while five of them skewed every average — a false assurance is worse than
    # silence for someone reading this to price a bet. A pipeline old enough to
    # predate the count still writes null, and the clause still stays off.
    excluded = meta.get("excluded_count")
    if isinstance(excluded, int) and not isinstance(excluded, bool) and excluded >= 0:
        parts.append(f"**{excluded}** excluded for missing data")

    return " · ".join(parts)


def match_history(selection: pd.DataFrame) -> None:
    """
    Every match in the selection, newest first — including the ones no figure
    on the page is computed from, marked in the `Data` column.

    Takes the whole selection rather than the measured rows on purpose. A match
    that was played belongs in the record of what was played, whatever OpenDota
    managed to record about it; the figures above are where the exclusion goes.
    """
    history = selection[[
        "start_time", "league_name", "patch_label",
        "radiant_team_name", "dire_team_name", "winner",
        "duration_mins", "total_roshan", "total_kills", "total_barracks",
        "total_towers", "both_lost_barracks", "both_teams_roshan",
        "suspect_reason",
    ]].copy().sort_values("start_time", ascending=False)

    history["start_time"] = history["start_time"].dt.strftime("%Y-%m-%d")
    history["duration_mins"] = history["duration_mins"].round(1)
    history["both_lost_barracks"] = history["both_lost_barracks"].map({True: "Yes", False: "No"})
    history["both_teams_roshan"] = history["both_teams_roshan"].map({True: "Yes", False: "No"})
    history["suspect_reason"] = history["suspect_reason"].map(suspect_label)

    st.dataframe(
        history.rename(columns={
            "start_time": "Date", "league_name": "Tournament", "patch_label": "Patch",
            "radiant_team_name": "Radiant", "dire_team_name": "Dire", "winner": "Winner",
            "duration_mins": "Duration (min)", "total_roshan": "Roshans",
            "total_kills": "Kills", "total_barracks": "Barracks", "total_towers": "Towers",
            "both_lost_barracks": "Both Lost Racks", "both_teams_roshan": "Both Slew Rosh",
            "suspect_reason": "Data",
        }),
        use_container_width=True, hide_index=True,
    )


# Everything the calculator prices, and how each reads.
OVER_UNDER_METRICS = [
    ("Kills",          "total_kills",    "%.1f"),
    ("Roshans",        "total_roshan",   "%.2f"),
    ("Barracks",       "total_barracks", "%.2f"),
    ("Towers",         "total_towers",   "%.2f"),
    ("Duration (min)", "duration_mins",  "%.1f"),
]


def over_under(selection: pd.DataFrame, avg_label: str, key_prefix: str) -> None:
    """
    The probability panel: two objective conditions, then a line per metric and
    the share of games that finished above it.

    Takes the whole selection and measures it here, so no caller can price a
    line off a match whose objectives were never recorded — every figure in this
    panel is a share of games meeting an objective condition, and such a match
    silently answers "no" to all of them.
    """
    priced = measured(selection)

    if len(priced) == 0:
        st.info(
            "No matches with complete objective data in this selection, so there "
            "is nothing to price."
        )
        return

    caption = f"Based on {len(priced)} match{'es' if len(priced) != 1 else ''}"
    if len(priced) != len(selection):
        caption += (f" — {len(selection) - len(priced)} of {len(selection)} left out "
                    "for missing objective data")
    st.caption(f"{caption}. Enter a line to see the probability it goes over.")

    n_both_racks = int(priced["both_lost_barracks"].sum())
    n_both_rosh  = int(priced["both_teams_roshan"].sum())
    prob_col1, prob_col2 = st.columns(2)
    prob_col1.metric("Both Teams Lost Barracks",
                     f"{n_both_racks / len(priced) * 100:.1f}%",
                     f"{n_both_racks}/{len(priced)} games")
    prob_col2.metric("Both Teams Slew Roshan",
                     f"{n_both_rosh / len(priced) * 100:.1f}%",
                     f"{n_both_rosh}/{len(priced)} games")
    st.divider()

    header_cols = st.columns(len(OVER_UNDER_METRICS))
    for col, (label, col_key, fmt) in zip(header_cols, OVER_UNDER_METRICS):
        col.markdown(f"**{label}**")
        col.caption(f"{avg_label}: {fmt % priced[col_key].mean()}")

    input_cols = st.columns(len(OVER_UNDER_METRICS))
    for col, (label, col_key, fmt) in zip(input_cols, OVER_UNDER_METRICS):
        line = col.number_input(
            "Line", min_value=0.0, value=None,
            placeholder="e.g. 39.5", step=0.5,
            key=f"{key_prefix}_{col_key}", label_visibility="collapsed",
        )
        if line is not None:
            n_over  = int((priced[col_key] > line).sum())
            n_under = int((priced[col_key] <= line).sum())
            col.metric("Over",  f"{n_over / len(priced) * 100:.1f}%",
                       f"{n_over}/{len(priced)} games")
            col.metric("Under", f"{n_under / len(priced) * 100:.1f}%",
                       f"{n_under}/{len(priced)} games")


def upcoming_tab() -> None:
    """
    Coverage state, where Wade already looks: the Tier 1 events with no OpenDota
    league, and the leagues the resolver found that nobody has judged. Both read
    straight out of `data/tier1_resolution.json` and neither touches the
    selection, which is why this tab renders before the empty-selection guard.

    **Read-only, and it stays that way.** There is no approve button: this app is
    public and Streamlit Cloud deploys it from git onto an ephemeral filesystem,
    so writing a verdict back would mean a repository write token in a public
    app. Approval is merging a pull request, which is one tap on a phone. See
    `tier1-pipeline-automation/09`.
    """
    st.subheader("Upcoming & Coverage")

    resolution = load_resolution()
    events = resolution.get("events") or []

    if not events:
        # Distinct from "no gaps", and the difference matters: this is the page
        # not knowing, where "no gaps" is the page knowing there is nothing to
        # report. Saying the second while meaning the first is the kind of false
        # assurance the coverage line was built to stop.
        st.warning(
            "No coverage record to show. `data/tier1_resolution.json` is written "
            "by the pipeline; this deployment is missing it or it holds no "
            "events. Nothing else on the dashboard is affected."
        )
    else:
        resolved_on = format_date(resolution.get("generated_at"))
        if resolved_on:
            st.caption(f"Coverage resolved **{resolved_on}** · "
                       f"**{len(events)}** Tier 1 events on the horizon")

        gaps = coverage_gaps(events, pd.Timestamp.now(tz="UTC").date())
        overdue = [gap for gap in gaps if gap["state"] == GAP_OVERDUE]

        # ── Known gaps ───────────────────────────────────────────────────────
        st.markdown("### Known gaps")
        st.caption(
            "Tier 1 events with no OpenDota league. An event that has not "
            "started has nothing to find yet; one that has started and still "
            "has no league is coverage this dashboard is missing."
        )

        if not gaps:
            st.success("No gaps — every Tier 1 event on the horizon has a league.")
        else:
            # The distinction is the point of the list, so it is said in words
            # above the table as well as marked in it. An overdue gap is the
            # failure this feature exists to make visible; an upcoming one is
            # just a calendar.
            if overdue:
                st.error(
                    f"**{len(overdue)} event{'s' if len(overdue) != 1 else ''} "
                    f"under way or finished with no data:** "
                    + ", ".join(gap["event"] or UNNAMED_EVENT for gap in overdue)
                )

            # "Overdue" and "Upcoming" are the glossary's two states, and the
            # labels are those words. A synonym here — "Scheduled" — reads as a
            # third state to anyone holding CONTEXT.md.
            gap_table = pd.DataFrame([{
                "Status": ("🔴 Overdue" if gap["state"] == GAP_OVERDUE
                           else "🕓 Upcoming"),
                "Event": gap["event"] or UNNAMED_EVENT,
                "Dates": format_window(gap["start_date"], gap["end_date"]),
            } for gap in gaps])

            st.dataframe(gap_table, use_container_width=True, hide_index=True)

        st.divider()

        # ── Pending candidates ───────────────────────────────────────────────
        st.markdown("### Awaiting a verdict")
        st.caption(
            "Leagues the resolver matched to a Tier 1 event that nobody has "
            "decided to cover or reject. Approval is merging the pull request "
            "that proposes them — this page only reports."
        )

        pending = awaiting_verdict(events)

        if not pending:
            st.success(
                "Nothing awaiting a decision — every resolved league has a verdict."
            )
        else:
            pending_table = pd.DataFrame([{
                "Event": record["event"] or UNNAMED_EVENT,
                "Dates": format_window(record["start_date"], record["end_date"]),
                "League": record["league_name"] or "—",
                "League ID": record["league_id"],
                # Absent rather than zero: a run that never re-ranked this
                # window carries the mapping without the evidence, and "0
                # matches" would be a different and false claim.
                "Matches": record["match_count"],
                "Team overlap": (None if record["overlap"] is None
                                 else record["overlap"] * 100),
                # A contested window is the row most worth a second look, so it
                # is stated rather than left to be inferred from a count.
                "Window": window_verdict(record),
                "Verdict": record["verdict"] or "unrecorded",
                "Review": record["pull_request"] or REPO_PULLS_URL,
            } for record in pending])

            # Where no pull request has been recorded — a proposal the run has
            # not opened yet, or a record written before `15` — the link is the
            # repository's open pull requests and the label says exactly that.
            # Calling a list of every open PR "the pull request" would be the
            # tab making a claim it cannot keep.
            proposed = any(record["pull_request"] for record in pending)

            st.dataframe(
                pending_table, use_container_width=True, hide_index=True,
                column_config={
                    "League ID": st.column_config.NumberColumn(format="%d"),
                    # A missing count makes the column float, and "60.0
                    # matches" is not a thing. Blank stays blank either way.
                    "Matches": st.column_config.NumberColumn(format="%d"),
                    "Team overlap": st.column_config.NumberColumn(format="%.1f%%"),
                    "Review": st.column_config.LinkColumn(
                        display_text="Pull request" if proposed else "Open PRs"
                    ),
                },
            )

    st.divider()

    # ── Attribution ──────────────────────────────────────────────────────────
    # A condition of using Liquipedia's free API, not a courtesy — the Tier 1
    # list on this tab *is* their data. It credits the source of the list rather
    # than the rows, so it renders whether or not there are any, and it sits
    # outside the branch above for that reason. See ADR-0006.
    st.caption(
        f"{LIQUIPEDIA_ATTRIBUTION} — event names and date windows from "
        f"[Tier 1 Tournaments]({LIQUIPEDIA_TIER1_URL}), used under "
        f"[CC BY-SA 3.0]({CC_BY_SA_URL}). League ids and match counts are from "
        "the OpenDota API."
    )


@st.cache_data
def build_team_perspective(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "match_id", "league_name", "patch_label", "start_time", "is_suspect",
        "duration_mins", "radiant_win", "first_roshan_team", "first_roshan_time_mins",
        "total_roshan", "total_kills", "total_barracks", "total_towers", "both_lost_barracks", "both_teams_roshan",
    ]
    radiant = df[base_cols].copy()
    radiant["team_name"] = df["radiant_team_name"].values
    radiant["team_roshan_kills"] = df["radiant_roshan_kills"].values
    radiant["team_kills"] = df["radiant_score"].values
    radiant["team_barracks_killed"] = df["dire_barracks_lost"].values
    radiant["team_towers_killed"] = df["dire_towers_lost"].values
    radiant["side"] = "Radiant"
    radiant["team_won"] = df["radiant_win"].values
    radiant["got_first_roshan"] = (df["first_roshan_team"] == "radiant").values

    dire = df[base_cols].copy()
    dire["team_name"] = df["dire_team_name"].values
    dire["team_roshan_kills"] = df["dire_roshan_kills"].values
    dire["team_kills"] = df["dire_score"].values
    dire["team_barracks_killed"] = df["radiant_barracks_lost"].values
    dire["team_towers_killed"] = df["radiant_towers_lost"].values
    dire["side"] = "Dire"
    dire["team_won"] = (~df["radiant_win"]).values
    dire["got_first_roshan"] = (df["first_roshan_team"] == "dire").values

    return pd.concat([radiant, dire], ignore_index=True)


raw = load_data()
team_persp_full = build_team_perspective(raw)

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.title("Filters")

league_start = raw.groupby("league_name")["start_time"].min()
all_leagues = league_start.sort_values(ascending=False).index.tolist()
selected_leagues = st.sidebar.multiselect("Tournament / League", all_leagues, default=all_leagues)
league_filtered = raw[raw["league_name"].isin(selected_leagues)] if selected_leagues else raw

all_teams = sorted(
    set(league_filtered["radiant_team_name"].dropna())
    | set(league_filtered["dire_team_name"].dropna())
    - ANON_NAMES
)
selected_teams = st.sidebar.multiselect("Team", all_teams, default=[])

all_patches = patch_order(raw)
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

# Every figure on the page is computed from these two frames; `filtered` and
# `team_filtered` survive only for the match history tables, which list what was
# played whether or not its objectives were recorded.
measured_matches      = measured(filtered)
measured_teams = measured(team_filtered)

st.sidebar.metric("Matches selected", len(filtered))
if len(filtered) != len(measured_matches):
    st.sidebar.caption(f"{len(filtered) - len(measured_matches)} excluded from figures")

# ── Page ──────────────────────────────────────────────────────────────────────
st.title("Dota 2 Pro Match Analysis")

# Coverage describes the whole dataset, not the current selection, so it renders
# before the empty-filter guard below — the moment the page is otherwise blank is
# exactly when "what is actually in here?" needs answering.
st.caption(coverage_line(raw, load_meta()))

# Why the guard is split from the `st.stop()` it belongs to: Tabs 1–5 are all
# computed from the selection and have nothing to draw when it is empty, but
# Upcoming reads none of it, and it is the tab that answers "why is a tournament
# missing?" — the question an empty page provokes. So the warning renders here,
# above the tabs and worded exactly as before; the stop waits until Upcoming has
# drawn. Same reasoning as the coverage line above, which has always rendered
# before this guard.
selection_note = None

if len(filtered) == 0:
    selection_note = "No matches match the current filters. Try broadening your selection."
elif len(measured_matches) == 0:
    # Reachable only by filtering down to suspect matches alone. Saying so beats
    # a page of NaNs, and beats quietly averaging zeroes that mean "unknown".
    selection_note = (
        f"All {len(filtered)} matches in this selection are missing their objective "
        "data, so there are no figures to show. Broaden the selection to see them "
        "alongside complete matches."
    )

if selection_note:
    st.warning(selection_note)

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["Team", "Tournament", "Meta Trends", "Head to Head", "Drilldown", "Upcoming"]
)

# Out of tab order on purpose — see the guard above. Everything it draws comes
# from `data/tier1_resolution.json` and nothing from the selection, so it is the
# one tab that can be drawn before the page decides whether it has figures.
with tab6:
    upcoming_tab()

if selection_note:
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — TEAM
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    if len(measured_teams) == 0:
        st.info("No team data for the current selection.")
        st.stop()

    note_exclusions(filtered)

    if len(selected_teams) == 1:
        st.subheader(selected_teams[0])
    elif len(selected_teams) > 1:
        st.subheader(f"{len(selected_teams)} teams selected")
    else:
        st.subheader("All Teams")

    # ── KPIs ─────────────────────────────────────────────────────────────────
    avg_team_rosh     = measured_teams["team_roshan_kills"].mean()
    pct_2plus_rosh    = (measured_teams["team_roshan_kills"] >= 2).mean() * 100
    avg_total_rosh    = measured_teams["total_roshan"].mean()
    pct_3plus_rosh    = (measured_teams["total_roshan"] >= 3).mean() * 100
    avg_team_kills    = measured_teams["team_kills"].mean()
    avg_total_kills   = measured_teams["total_kills"].mean()
    avg_team_barr     = measured_teams["team_barracks_killed"].mean()
    avg_total_barr    = measured_teams["total_barracks"].mean()
    pct_both_barr     = measured_teams["both_lost_barracks"].mean() * 100
    avg_duration      = measured_matches["duration_mins"].mean()

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

    # ── Team Stats Table ──────────────────────────────────────────────────────
    st.subheader("Team Breakdown")

    team_table = (
        measured_teams.groupby("team_name")
        .agg(
            matches=("match_id", "nunique"),
            wins=("team_won", "sum"),
            avg_team_roshans=("team_roshan_kills", "mean"),
            avg_total_roshans=("total_roshan", "mean"),
            avg_team_kills=("team_kills", "mean"),
            avg_total_kills=("total_kills", "mean"),
            avg_team_barracks=("team_barracks_killed", "mean"),
            avg_total_barracks=("total_barracks", "mean"),
            pct_team_2plus_rosh=("team_roshan_kills", lambda x: (x >= 2).mean() * 100),
            pct_total_3plus_rosh=("total_roshan", lambda x: (x >= 3).mean() * 100),
            pct_both_barracks=("both_lost_barracks", lambda x: x.mean() * 100),
            avg_duration=("duration_mins", "mean"),
        )
        .reset_index()
    )
    team_table["win_pct"] = team_table["wins"] / team_table["matches"] * 100
    team_table = team_table.sort_values("matches", ascending=False)

    display_team = team_table.rename(columns={
        "team_name": "Team",
        "matches": "Matches",
        "win_pct": "Win %",
        "avg_team_roshans": "Avg Team Roshans/Game",
        "avg_total_roshans": "Avg Total Roshans/Game",
        "avg_team_kills": "Avg Team Kills/Game",
        "avg_total_kills": "Avg Total Kills/Game",
        "avg_team_barracks": "Avg Team Barracks/Game",
        "avg_total_barracks": "Avg Total Barracks/Game",
        "pct_team_2plus_rosh": "Team 2+ Roshans %",
        "pct_total_3plus_rosh": "Total 3+ Roshans %",
        "pct_both_barracks": "Both Lost Barracks %",
        "avg_duration": "Avg Duration (min)",
    })
    for col in ["Avg Team Roshans/Game", "Avg Total Roshans/Game",
                "Avg Team Barracks/Game", "Avg Total Barracks/Game"]:
        display_team[col] = display_team[col].round(2)
    for col in ["Win %", "Avg Team Kills/Game", "Avg Total Kills/Game",
                "Team 2+ Roshans %", "Total 3+ Roshans %", "Both Lost Barracks %", "Avg Duration (min)"]:
        display_team[col] = display_team[col].round(1)

    st.dataframe(
        display_team[["Team", "Matches", "Win %",
                       "Avg Team Roshans/Game", "Avg Total Roshans/Game",
                       "Avg Team Kills/Game", "Avg Total Kills/Game",
                       "Avg Team Barracks/Game", "Avg Total Barracks/Game",
                       "Team 2+ Roshans %", "Total 3+ Roshans %", "Both Lost Barracks %",
                       "Avg Duration (min)"]],
        use_container_width=True, hide_index=True,
    )

    st.divider()

    # ── Roshan ───────────────────────────────────────────────────────────────
    st.subheader("Roshan")
    col_rp, col_rt = st.columns(2)

    rosh_patch = (
        measured_teams.groupby("patch_label")
        .agg(avg_team=("team_roshan_kills", "mean"), avg_total=("total_roshan", "mean"))
        .reset_index().pipe(by_patch)
    )
    fig_rp = go.Figure([
        go.Bar(name="Team Kills", x=rosh_patch["patch_label"], y=rosh_patch["avg_team"], marker_color="#4C9BE8"),
        go.Bar(name="Total in Match", x=rosh_patch["patch_label"], y=rosh_patch["avg_total"], marker_color="#A8D1F5"),
    ])
    fig_rp.update_layout(barmode="group", title="By Patch", xaxis_title="Patch",
                         yaxis_title="Avg / Game", legend=dict(orientation="h", y=-0.35))
    col_rp.plotly_chart(fig_rp, use_container_width=True)

    rosh_tourn = (
        measured_teams.groupby("league_name")
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
        measured_teams.groupby("patch_label")
        .agg(avg_team=("team_kills", "mean"), avg_total=("total_kills", "mean"))
        .reset_index().pipe(by_patch)
    )
    fig_kp = go.Figure([
        go.Bar(name="Team Kills", x=kills_patch["patch_label"], y=kills_patch["avg_team"], marker_color="#E88C4C"),
        go.Bar(name="Total in Match", x=kills_patch["patch_label"], y=kills_patch["avg_total"], marker_color="#F5C8A8"),
    ])
    fig_kp.update_layout(barmode="group", title="By Patch", xaxis_title="Patch",
                         yaxis_title="Avg / Game", legend=dict(orientation="h", y=-0.35))
    col_kp.plotly_chart(fig_kp, use_container_width=True)

    kills_tourn = (
        measured_teams.groupby("league_name")
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
        measured_teams.groupby("patch_label")
        .agg(
            avg_team=("team_barracks_killed", "mean"),
            avg_total=("total_barracks", "mean"),
            pct_both=("both_lost_barracks", "mean"),
        )
        .reset_index().pipe(by_patch)
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
        measured_teams.groupby("league_name")
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

    mean_dur = measured_matches["duration_mins"].mean()
    fig_gl = px.histogram(measured_matches, x="duration_mins", nbins=25,
                          title="Distribution of Game Lengths",
                          labels={"duration_mins": "Duration (min)", "count": "Games"},
                          color_discrete_sequence=["#9B59B6"])
    fig_gl.add_vline(x=mean_dur, line_dash="dash", line_color="orange",
                     annotation_text=f"mean = {mean_dur:.1f} min", annotation_position="top right")
    fig_gl.update_layout(showlegend=False)
    col_gl1.plotly_chart(fig_gl, use_container_width=True)

    fig_gl_box = px.box(measured_matches, x="patch_label", y="duration_mins",
                        title="Game Length by Patch",
                        labels={"patch_label": "Patch", "duration_mins": "Duration (min)"},
                        color="patch_label",
                        category_orders={"patch_label": patch_order(measured_matches)},
                        color_discrete_sequence=px.colors.qualitative.Set2)
    fig_gl_box.update_layout(showlegend=False)
    col_gl2.plotly_chart(fig_gl_box, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TOURNAMENT
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Tournament Overview")

    note_exclusions(filtered)

    tourn_stats = (
        measured_matches.groupby("league_name")
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

    note_exclusions(filtered)

    patch_stats = (
        measured_matches.groupby("patch_label")
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
        .pipe(by_patch)
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

    fig_m4 = px.violin(measured_matches, x="patch_label", y="duration_mins",
                       box=True, points=False,
                       title="Game Length Distribution by Patch",
                       labels={"patch_label": "Patch", "duration_mins": "Duration (min)"},
                       color="patch_label",
                       category_orders={"patch_label": patch_order(measured_matches)},
                       color_discrete_sequence=px.colors.qualitative.Set2)
    fig_m4.update_layout(showlegend=False)
    col4.plotly_chart(fig_m4, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — HEAD TO HEAD
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("Head to Head")

    h2h_teams = sorted(
        set(filtered["radiant_team_name"].dropna())
        | set(filtered["dire_team_name"].dropna())
        - ANON_NAMES
    )

    col_a, col_b = st.columns(2)
    team_a = col_a.selectbox("Team A", ["— select —"] + h2h_teams, key="h2h_a")
    team_b = col_b.selectbox("Team B", ["— select —"] + h2h_teams, key="h2h_b")

    if team_a == "— select —" or team_b == "— select —":
        st.info("Select two teams above to see their head-to-head record.")
    elif team_a == team_b:
        st.warning("Select two different teams.")
    else:
        h2h = filtered[
            ((filtered["radiant_team_name"] == team_a) & (filtered["dire_team_name"] == team_b))
            | ((filtered["radiant_team_name"] == team_b) & (filtered["dire_team_name"] == team_a))
        ].copy()

        if len(h2h) == 0:
            st.warning(f"No matches found between {team_a} and {team_b} with the current filters.")
        else:
            h2h["winner"] = h2h.apply(
                lambda r: r["radiant_team_name"] if r["radiant_win"] else r["dire_team_name"], axis=1
            )
            h2h["team_a_won"] = h2h["winner"] == team_a

            # The record counts every match. Who won is recorded on the match
            # itself and is right even when the objectives are missing, so
            # dropping a played game from a head-to-head record would state
            # something false about a series the user can see listed below.
            # The figures under it are a different question — see `measured`.
            team_a_wins = int(h2h["team_a_won"].sum())
            team_b_wins = len(h2h) - team_a_wins
            p_team_a = team_a_wins / len(h2h) * 100
            p_team_b = team_b_wins / len(h2h) * 100

            # ── Record ───────────────────────────────────────────────────────
            st.markdown(f"### {team_a} vs {team_b}")
            r1, r2, r3, r4, r5 = st.columns(5)
            r1.metric("Matches Played", len(h2h))
            r2.metric(f"{team_a} Wins", team_a_wins)
            r3.metric(f"{team_b} Wins", team_b_wins)
            r4.metric(f"{team_a} Win %", f"{p_team_a:.1f}%")
            r5.metric(f"{team_b} Win %", f"{p_team_b:.1f}%")

            st.divider()

            h2h_stats = measured(h2h)
            note_exclusions(h2h)

            # ── Per-team comparative measured_matches ────────────────────────────────
            h2h_team = build_team_perspective(h2h_stats)
            h2h_team = h2h_team[h2h_team["team_name"].isin([team_a, team_b])]

            comp = (
                h2h_team.groupby("team_name")
                .agg(
                    avg_roshans=("team_roshan_kills", "mean"),
                    avg_kills=("team_kills", "mean"),
                    avg_barracks=("team_barracks_killed", "mean"),
                    avg_towers=("team_towers_killed", "mean"),
                )
                .reset_index()
            )
            # Preserve Team A / Team B order in charts
            comp["team_name"] = pd.Categorical(comp["team_name"], categories=[team_a, team_b], ordered=True)
            comp = comp.sort_values("team_name")
            bar_colors = ["#4C9BE8", "#E88C4C", "#AAAAAA"]

            avg_total_roshans = h2h_stats["total_roshan"].mean()
            avg_total_kills = h2h_stats["total_kills"].mean()
            avg_total_barracks = h2h_stats["total_barracks"].mean()
            avg_total_towers = h2h_stats["total_towers"].mean()

            x_labels = list(comp["team_name"]) + ["Match Total"]

            col1, col2 = st.columns(2)

            fig_h1 = go.Figure(go.Bar(
                x=x_labels,
                y=list(comp["avg_roshans"]) + [avg_total_roshans],
                marker_color=bar_colors,
                text=[f"{v:.2f}" for v in list(comp["avg_roshans"]) + [avg_total_roshans]],
                textposition="outside",
            ))
            fig_h1.update_layout(title="Avg Roshans/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            col1.plotly_chart(fig_h1, use_container_width=True)

            fig_h2 = go.Figure(go.Bar(
                x=x_labels,
                y=list(comp["avg_kills"]) + [avg_total_kills],
                marker_color=bar_colors,
                text=[f"{v:.1f}" for v in list(comp["avg_kills"]) + [avg_total_kills]],
                textposition="outside",
            ))
            fig_h2.update_layout(title="Avg Kills/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            col2.plotly_chart(fig_h2, use_container_width=True)

            col3, col4 = st.columns(2)

            fig_h3 = go.Figure(go.Bar(
                x=x_labels,
                y=list(comp["avg_barracks"]) + [avg_total_barracks],
                marker_color=bar_colors,
                text=[f"{v:.2f}" for v in list(comp["avg_barracks"]) + [avg_total_barracks]],
                textposition="outside",
            ))
            fig_h3.update_layout(title="Avg Barracks Destroyed/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            col3.plotly_chart(fig_h3, use_container_width=True)

            fig_h4 = go.Figure(go.Bar(
                x=x_labels,
                y=list(comp["avg_towers"]) + [avg_total_towers],
                marker_color=bar_colors,
                text=[f"{v:.2f}" for v in list(comp["avg_towers"]) + [avg_total_towers]],
                textposition="outside",
            ))
            fig_h4.update_layout(title="Avg Towers Destroyed/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            col4.plotly_chart(fig_h4, use_container_width=True)

            st.divider()

            # ── Match history table ───────────────────────────────────────
            st.subheader("Match History")
            match_history(h2h)

            st.divider()

            # ── Over/Under probability calculator ────────────────────────
            st.subheader("Over/Under Calculator")
            over_under(h2h, "H2H avg", "ou")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — DRILLDOWN
# ══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.subheader("Drilldown")

    all_tournaments = league_start.sort_values(ascending=False).index.tolist()
    all_dd_teams = sorted(
        set(raw["radiant_team_name"].dropna())
        | set(raw["dire_team_name"].dropna())
        - ANON_NAMES
    )

    dd_col1, dd_col2 = st.columns(2)
    dd_tournaments = dd_col1.multiselect("Tournament", all_tournaments, key="dd_tourn")
    dd_team = dd_col2.selectbox("Team", ["— all —"] + all_dd_teams, key="dd_team")

    has_tournament = len(dd_tournaments) > 0
    has_team = dd_team != "— all —"

    if not has_tournament and not has_team:
        st.info("Select a tournament and/or team to see the drilldown.")
    else:
        dd = raw.copy()
        if has_tournament:
            dd = dd[dd["league_name"].isin(dd_tournaments)]
        if has_team:
            dd = dd[
                (dd["radiant_team_name"] == dd_team)
                | (dd["dire_team_name"] == dd_team)
            ]

        if len(dd) == 0:
            st.warning("No matches found with the current filters.")
        else:
            dd = dd.copy()
            dd["winner"] = dd.apply(
                lambda r: r["radiant_team_name"] if r["radiant_win"] else r["dire_team_name"], axis=1
            )

            # ── Summary KPIs ──────────────────────────────────────────────
            if has_team:
                dd["team_won"] = dd["winner"] == dd_team
                team_wins   = int(dd["team_won"].sum())
                team_losses = len(dd) - team_wins
                p_win       = team_wins / len(dd) * 100
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Matches", len(dd))
                k2.metric(f"{dd_team} Wins", team_wins)
                k3.metric(f"{dd_team} Losses", team_losses)
                k4.metric(f"{dd_team} Win %", f"{p_win:.1f}%")
            else:
                st.metric("Matches Found", len(dd))

            st.divider()

            # ── Avg measured_matches bar charts ──────────────────────────────────────
            # The record above counts every match played; the averages below
            # cannot, since a match with no recorded objectives contributes
            # zeroes that mean "unknown".
            dd_stats = measured(dd)
            note_exclusions(dd)

            avg_total_rosh   = dd_stats["total_roshan"].mean()
            avg_total_kills  = dd_stats["total_kills"].mean()
            avg_total_barr   = dd_stats["total_barracks"].mean()
            avg_total_towers = dd_stats["total_towers"].mean()

            if has_team:
                dd_team_persp = build_team_perspective(dd_stats)
                dd_team_persp = dd_team_persp[dd_team_persp["team_name"] == dd_team]
                avg_team_rosh   = dd_team_persp["team_roshan_kills"].mean()
                avg_team_kills  = dd_team_persp["team_kills"].mean()
                avg_team_barr   = dd_team_persp["team_barracks_killed"].mean()
                avg_team_towers = dd_team_persp["team_towers_killed"].mean()
                x_labels  = [dd_team, "Match Total"]
                bar_colors = ["#4C9BE8", "#AAAAAA"]
                rosh_y   = [avg_team_rosh,   avg_total_rosh]
                kills_y  = [avg_team_kills,  avg_total_kills]
                barr_y   = [avg_team_barr,   avg_total_barr]
                towers_y = [avg_team_towers, avg_total_towers]
            else:
                x_labels  = ["Match Total"]
                bar_colors = ["#AAAAAA"]
                rosh_y   = [avg_total_rosh]
                kills_y  = [avg_total_kills]
                barr_y   = [avg_total_barr]
                towers_y = [avg_total_towers]

            dc1, dc2 = st.columns(2)
            dc3, dc4 = st.columns(2)

            fig_d1 = go.Figure(go.Bar(
                x=x_labels, y=rosh_y, marker_color=bar_colors,
                text=[f"{v:.2f}" for v in rosh_y], textposition="outside",
            ))
            fig_d1.update_layout(title="Avg Roshans/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            dc1.plotly_chart(fig_d1, use_container_width=True)

            fig_d2 = go.Figure(go.Bar(
                x=x_labels, y=kills_y, marker_color=bar_colors,
                text=[f"{v:.1f}" for v in kills_y], textposition="outside",
            ))
            fig_d2.update_layout(title="Avg Kills/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            dc2.plotly_chart(fig_d2, use_container_width=True)

            fig_d3 = go.Figure(go.Bar(
                x=x_labels, y=barr_y, marker_color=bar_colors,
                text=[f"{v:.2f}" for v in barr_y], textposition="outside",
            ))
            fig_d3.update_layout(title="Avg Barracks Destroyed/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            dc3.plotly_chart(fig_d3, use_container_width=True)

            fig_d4 = go.Figure(go.Bar(
                x=x_labels, y=towers_y, marker_color=bar_colors,
                text=[f"{v:.2f}" for v in towers_y], textposition="outside",
            ))
            fig_d4.update_layout(title="Avg Towers Destroyed/Game", yaxis_title="Avg / Game",
                                  showlegend=False, xaxis_title="")
            dc4.plotly_chart(fig_d4, use_container_width=True)

            st.divider()

            # ── Match history table ───────────────────────────────────────
            st.subheader("Match History")
            match_history(dd)

            st.divider()

            # ── Over/Under probability calculator ─────────────────────────
            st.subheader("Over/Under Calculator")
            over_under(dd, "Avg", "dd_ou")


