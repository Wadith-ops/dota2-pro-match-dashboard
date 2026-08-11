"""
Tests for the Standard modelling record and the store it is written to.

A Standard record is one match with its per-player timeseries removed: 20.1 KB
against a raw 288 KB, keeping objectives in full, picks and bans, the
per-player aggregates and benchmarks, the teamfight summaries, and the gold and
XP advantage curves. See `tier1-pipeline-automation/10`, ADR-0002 and ADR-0010.

Two properties carry most of the weight here.

**Nothing the serving path reads may be lost.** The CSV is built from Standard
records once the raw sink is off, so `flatten_match` must produce the same row
from the extract as from the payload it came from. That is asserted against
every recorded fixture rather than field by field, because a field-by-field list
is the thing that goes stale.

**Extraction is idempotent.** The same function serves the live pipeline, which
hands it raw payloads, and the one-time backfill, which may hand it either.
"""
import json

import pytest

from core import (
    BENCHMARK_PRECISION,
    STANDARD_MATCH_FIELDS,
    STANDARD_PLAYER_FIELDS,
    STANDARD_TEAMFIGHT_FIELDS,
    extract_standard,
    flatten_match,
    format_json_lines,
    iter_json_array,
    parse_standard_records,
    round_benchmarks,
)

# The fixtures, by the name of the fixture that loads them. Every one is a
# recorded payload as the pipeline stored it.
FIXTURES = ["normal_match", "negative_first_blood_match", "game_mode_1_match",
            "empty_objectives_match"]

# What a Standard record must never carry. Every one is a per-player timeseries
# or matrix, and together they are the 87% — `lane_pos` alone is 15% of a
# player. Listed by name rather than derived from the allowlist so that a field
# quietly added to the allowlist fails here.
DISCARDED_PLAYER_FIELDS = [
    "lane_pos", "damage", "damage_taken", "damage_targets", "damage_inflictor",
    "damage_inflictor_received", "purchase", "purchase_log", "purchase_time",
    "first_purchase_time", "killed", "killed_by", "kills_log", "gold_t", "xp_t",
    "lh_t", "dn_t", "times", "item_usage", "item_win", "item_uses",
    "ability_uses", "ability_targets", "runes_log", "obs_log", "obs_left_log",
    "sen_log", "sen_left_log", "buyback_log", "connection_log", "actions",
    "life_state", "neutral_item_history", "additional_units", "cosmetics",
]

DISCARDED_MATCH_FIELDS = ["chat", "replay_url", "replay_salt", "cosmetics",
                          "all_word_counts", "my_word_counts", "radiant_logo",
                          "dire_logo", "pauses"]

# Roughly 20 KB per match is an acceptance criterion, and it is what keeps the
# store inside the repository: 1,822 matches at the measured 20.1 KB is 37.5 MB,
# well under GitHub's 100 MB per-file limit. The ceiling is loose because it
# guards a design decision, not an exact byte count — what it catches is a fat
# field admitted to an allowlist.
SIZE_CEILING = 32 * 1024


def size(record):
    """The bytes this record costs in the store, as it is actually written."""
    return len(json.dumps(record, separators=(",", ":"), ensure_ascii=False))


@pytest.fixture
def every_match(request):
    """All four recorded payloads, so a property can be asserted over the lot."""
    return [request.getfixturevalue(name) for name in FIXTURES]


class TestWhatStandardKeeps:
    def test_objectives_are_kept_whole(self, normal_match):
        # Not summarised, not counted — the array itself. Everything the
        # dashboard shows is derived from it, and it is 1% of the payload.
        assert extract_standard(normal_match)["objectives"] == normal_match["objectives"]

    def test_picks_and_bans_are_kept_whole(self, normal_match):
        record = extract_standard(normal_match)

        assert record["picks_bans"] == normal_match["picks_bans"]
        assert len(record["picks_bans"]) == 24

    def test_the_gold_and_xp_advantage_curves_are_kept(self, normal_match):
        # Half a kilobyte each and the highest-value-per-byte features in the
        # payload for outcome modelling — the reason Standard was chosen over a
        # leaner tier that kept only final stats.
        record = extract_standard(normal_match)

        assert record["radiant_gold_adv"] == normal_match["radiant_gold_adv"]
        assert record["radiant_xp_adv"] == normal_match["radiant_xp_adv"]

    def test_every_player_keeps_their_aggregates(self, normal_match):
        players = extract_standard(normal_match)["players"]

        assert len(players) == 10
        for player, original in zip(players, normal_match["players"]):
            for field in ["hero_id", "kills", "deaths", "assists", "net_worth",
                          "gold_per_min", "xp_per_min", "hero_damage",
                          "tower_damage", "last_hits", "denies", "lane_role"]:
                assert player[field] == original[field]

    def test_every_player_keeps_their_benchmarks(self, normal_match):
        players = extract_standard(normal_match)["players"]

        assert all("benchmarks" in player for player in players)
        assert set(players[0]["benchmarks"]) == set(
            normal_match["players"][0]["benchmarks"]
        )

    def test_teamfights_are_kept_as_summaries(self, normal_match):
        fights = extract_standard(normal_match)["teamfights"]

        assert len(fights) == len(normal_match["teamfights"])
        assert [set(fight) for fight in fights] == [
            set(STANDARD_TEAMFIGHT_FIELDS)
        ] * len(fights)
        assert fights[0]["deaths"] == normal_match["teamfights"][0]["deaths"]

    def test_the_league_name_the_pipeline_injected_survives(self, normal_match):
        # Not an API field: the pipeline writes the *ledger's* name onto every
        # payload, and it is what the dashboard groups tournaments by. Losing it
        # here would rename every tournament to nothing.
        assert extract_standard(normal_match)["league_name"] == "1win Essence II"


class TestWhatStandardDiscards:
    def test_the_per_player_timeseries_are_gone(self, normal_match):
        players = extract_standard(normal_match)["players"]

        for field in DISCARDED_PLAYER_FIELDS:
            assert field not in players[0], field

    def test_the_match_level_chatter_is_gone(self, normal_match):
        record = extract_standard(normal_match)

        for field in DISCARDED_MATCH_FIELDS:
            assert field not in record, field

    def test_the_per_player_teamfight_breakdown_is_gone(self, normal_match):
        # A quarter of the record for a breakdown the criterion does not ask
        # for: "teamfight summaries" is the fight, not every player in it.
        assert "players" not in extract_standard(normal_match)["teamfights"][0]

    def test_a_record_is_around_twenty_kilobytes(self, every_match):
        sizes = [size(extract_standard(match)) for match in every_match]

        assert max(sizes) < SIZE_CEILING, sizes

    def test_a_record_is_a_small_fraction_of_the_payload_it_came_from(
        self, normal_match
    ):
        assert size(extract_standard(normal_match)) < size(normal_match) / 8


class TestTheServingPathIsUnaffected:
    def test_the_flat_row_is_the_same_either_way(self, every_match, patch_map):
        # The load-bearing test of the whole split. The dashboard reads the CSV,
        # the CSV is built from Standard records, and this says the substitution
        # changes nothing about them.
        for match in every_match:
            assert flatten_match(extract_standard(match), patch_map) == \
                   flatten_match(match, patch_map)

    def test_a_suspect_match_is_still_suspect_after_extraction(
        self, empty_objectives_match, patch_map
    ):
        # The one that would fail silently: dropping `objectives` in extraction
        # would flag every match rather than none, and dropping the scores would
        # flag them all as zero-kill games.
        row = flatten_match(extract_standard(empty_objectives_match), patch_map)

        assert row["is_suspect"] is True
        assert row["suspect_reason"] == "no_objectives"


class TestExtractionIsTotal:
    def test_extracting_twice_changes_nothing(self, every_match):
        for match in every_match:
            once = extract_standard(match)
            assert extract_standard(once) == once

    def test_an_empty_payload_extracts_to_an_empty_record(self):
        assert extract_standard({}) == {}

    def test_a_field_the_payload_does_not_carry_is_left_out_not_nulled(self):
        # Presence-based. A record full of nulls would claim to know that an
        # unparsed match had no teamfights, rather than that nobody has looked.
        assert extract_standard({"match_id": 1}) == {"match_id": 1}

    def test_the_players_key_survives_a_payload_that_is_not_a_list(self):
        assert "players" not in extract_standard({"match_id": 1, "players": None})

    def test_the_allowlists_do_not_overlap_with_the_discard_lists(self):
        assert not set(STANDARD_MATCH_FIELDS) & set(DISCARDED_MATCH_FIELDS)
        assert not set(STANDARD_PLAYER_FIELDS) & set(DISCARDED_PLAYER_FIELDS)


class TestBenchmarkRounding:
    def test_percentiles_are_rounded_not_dropped(self, normal_match):
        benchmarks = extract_standard(normal_match)["players"][0]["benchmarks"]

        assert benchmarks["gold_per_min"]["pct"] == round(
            normal_match["players"][0]["benchmarks"]["gold_per_min"]["pct"],
            BENCHMARK_PRECISION,
        )

    def test_integers_are_left_alone(self):
        assert round_benchmarks({"gold_per_min": {"raw": 902, "pct": 0.5}}) == {
            "gold_per_min": {"raw": 902, "pct": 0.5}
        }

    def test_a_shape_this_does_not_recognise_passes_through(self):
        # This runs over every player of every match ever fetched. A payload
        # shaped differently from today's has to survive extraction, not end it.
        assert round_benchmarks(None) is None
        assert round_benchmarks({"gold_per_min": 4}) == {"gold_per_min": 4}


class TestReadingTheStore:
    def read(self, *lines):
        return parse_standard_records(lines)

    def test_records_come_back_in_the_order_they_were_written(self):
        records, damaged = self.read('{"match_id": 1}', '{"match_id": 2}')

        assert [record["match_id"] for record in records] == [1, 2]
        assert damaged == 0

    def test_the_last_line_for_a_match_wins_and_keeps_its_place(self):
        # Order is first-seen so that re-fetching a match does not move its row
        # in the CSV; the value is the last so that the parsed replay wins.
        records, _ = self.read(
            '{"match_id": 1, "v": "first"}',
            '{"match_id": 2}',
            '{"match_id": 1, "v": "second"}',
        )

        assert [record["match_id"] for record in records] == [1, 2]
        assert records[0]["v"] == "second"

    def test_blank_lines_are_not_records(self):
        records, damaged = self.read('{"match_id": 1}', "", "   ", "\n")

        assert len(records) == 1
        assert damaged == 0

    def test_a_damaged_line_is_counted_and_skipped(self):
        # The only way to produce one is a crash mid-append. Refusing to read
        # the other ten thousand lines over it would be the expensive answer.
        records, damaged = self.read(
            '{"match_id": 1}', '{"match_id": 2, "objec', '{"match_id": 3}'
        )

        assert [record["match_id"] for record in records] == [1, 3]
        assert damaged == 1

    def test_a_line_that_is_not_an_object_is_damaged(self):
        records, damaged = self.read("[1, 2, 3]", "null")

        assert records == []
        assert damaged == 2

    def test_a_record_with_no_match_id_is_kept_and_never_collapsed(self):
        # Two payloads with no id are two payloads, not one overwriting the
        # other. Nothing produces them today, and silently merging matches is
        # not the failure to choose when something starts to.
        records, damaged = self.read('{"a": 1}', '{"a": 2}')

        assert len(records) == 2
        assert damaged == 0

    def test_what_was_written_is_what_is_read(self, normal_match):
        record = extract_standard(normal_match)
        records, damaged = parse_standard_records(
            format_json_lines([record]).splitlines()
        )

        assert records == [record]
        assert damaged == 0


class TestFormattingTheStore:
    def test_one_record_is_one_line(self, every_match):
        text = format_json_lines(extract_standard(m) for m in every_match)

        assert text.count("\n") == len(every_match)
        assert text.endswith("\n")

    def test_nothing_written_for_nothing_to_write(self):
        assert format_json_lines([]) == ""

    def test_lines_carry_no_indentation(self, normal_match):
        # `indent=2` on a store this size is 30% of it spent on whitespace, and
        # nothing reads it by eye.
        line = format_json_lines([extract_standard(normal_match)])

        assert "\n" not in line[:-1]

    def test_non_ascii_is_written_as_itself(self):
        # Team names carry it. `ensure_ascii` would treble their length and
        # make the file unreadable to anything that greps it.
        assert format_json_lines([{"name": "Тундра"}]) == '{"name":"Тундра"}\n'


class TestStreamingTheLegacyStore:
    """
    `iter_json_array` is how the 1 GB raw file is read at all: `json.load` needs
    the whole document and its parsed form in memory together, which is why that
    file could never be processed anywhere but the machine that wrote it.
    """

    def stream(self, text, chunk_size):
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
        return list(iter_json_array(chunks or [""]))

    def test_objects_come_back_whole_however_the_chunks_fall(self):
        text = json.dumps([{"match_id": 1, "name": "one"},
                           {"match_id": 2, "name": "two"},
                           {"match_id": 3, "name": "three"}])

        # Every chunk size from one character up, so every possible boundary —
        # mid-key, mid-string, mid-number, between objects — is exercised.
        for chunk_size in range(1, len(text) + 1):
            assert self.stream(text, chunk_size) == json.loads(text), chunk_size

    def test_an_empty_array_yields_nothing(self):
        assert self.stream("[]", 1) == []

    def test_whitespace_and_indentation_are_ignored(self):
        assert self.stream('[\n  {"a": 1},\n  {"a": 2}\n]\n', 3) == [
            {"a": 1}, {"a": 2}
        ]

    def test_a_value_cut_short_by_the_end_of_the_stream_is_not_yielded(self):
        # A truncated file — the 1 GB store, interrupted mid-rewrite, which is
        # the failure mode that motivated all of this. Better to lose the last
        # match, which re-fetches, than to record half of it as whole.
        assert self.stream('[{"a": 1}, {"a": 2', 4) == [{"a": 1}]

    def test_a_document_that_is_not_an_array_is_refused(self):
        with pytest.raises(ValueError):
            self.stream('{"a": 1}', 4)

    def test_nested_arrays_and_objects_are_not_mistaken_for_the_outer_one(self):
        text = json.dumps([{"objectives": [{"t": 1}, {"t": 2}], "p": [[1], [2]]},
                           {"objectives": []}])

        assert self.stream(text, 7) == json.loads(text)
