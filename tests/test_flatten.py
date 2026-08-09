"""
Characterisation tests for the flattening transforms.

Expected values are read off the raw objectives array by hand, not recomputed
the way the code computes them. They record what the pipeline produces *today*,
so that the correctness fixes in tier1-pipeline-automation/04 can be shown to
change what they intended and nothing else.
"""
from core import build_rows, flatten_match, flatten_objectives


class TestFlattenObjectives:
    def test_counts_every_objective_type_in_a_complete_match(self, normal_match):
        # Hand-counted from the 32 objectives of match 8920872222:
        # 2 Roshan kills (both team 2), first at t=1635; 3 tormentor kills
        # (2 radiant, 1 dire); 10 badguys towers and 3 goodguys towers;
        # 4 badguys barracks; first blood at t=162; 6 couriers lost.
        # The two CHAT_MESSAGE_AEGIS events are pickups, not steals or denies.
        assert flatten_objectives(normal_match["objectives"]) == {
            "radiant_roshan_kills": 2,
            "dire_roshan_kills": 0,
            "first_roshan_time": 1635,
            "first_roshan_team": "radiant",
            "aegis_stolen": 0,
            "aegis_denied": 0,
            "radiant_tormentor_kills": 2,
            "dire_tormentor_kills": 1,
            "radiant_towers_lost": 3,
            "dire_towers_lost": 10,
            "radiant_barracks_lost": 0,
            "dire_barracks_lost": 4,
            "first_blood_time": 162,
            "courier_kills": 6,
        }

    def test_returns_zeroes_when_the_match_records_no_objectives(
        self, empty_objectives_match
    ):
        # 8809802771 is a 52-minute game whose payload omits `objectives`
        # entirely — the field is absent, not empty. Every count comes back
        # zero, which is indistinguishable from a game where nothing happened;
        # that is why these matches have to be flagged rather than trusted.
        assert "objectives" not in empty_objectives_match

        assert flatten_objectives(empty_objectives_match.get("objectives")) == {
            "radiant_roshan_kills": 0,
            "dire_roshan_kills": 0,
            "first_roshan_time": None,
            "first_roshan_team": None,
            "aegis_stolen": 0,
            "aegis_denied": 0,
            "radiant_tormentor_kills": 0,
            "dire_tormentor_kills": 0,
            "radiant_towers_lost": 0,
            "dire_towers_lost": 0,
            "radiant_barracks_lost": 0,
            "dire_barracks_lost": 0,
            "first_blood_time": None,
            "courier_kills": 0,
        }

    def test_counts_the_ancient_as_neither_tower_nor_barracks(self, normal_match):
        # The final building_kill is `npc_dota_badguys_fort`. It carries the
        # side marker every building event carries, so it would be miscounted
        # by anything matching on side alone.
        keys = [o.get("key") for o in normal_match["objectives"]]
        assert "npc_dota_badguys_fort" in keys

        result = flatten_objectives(normal_match["objectives"])
        assert result["dire_towers_lost"] == 10
        assert result["dire_barracks_lost"] == 4

    def test_records_the_first_roshan_only(self, game_mode_1_match):
        # Both Roshan kills in 8514293820 are Dire's, at t=1150 and t=1850.
        result = flatten_objectives(game_mode_1_match["objectives"])
        assert result["dire_roshan_kills"] == 2
        assert result["radiant_roshan_kills"] == 0
        assert result["first_roshan_time"] == 1150
        assert result["first_roshan_team"] == "dire"


class TestFlattenMatch:
    def test_builds_a_match_row_from_a_complete_payload(self, normal_match, patch_map):
        row = flatten_match(normal_match, patch_map)

        assert row["match_id"] == 8920872222
        assert row["league_id"] == 20009
        assert row["league_name"] == "1win Essence II"
        assert row["patch"] == "7.41"          # patch id 60, via the injected map
        assert row["start_time"] == 1785416889
        assert row["duration_secs"] == 3101
        assert row["duration_mins"] == 51.7    # 3101 / 60 = 51.68
        assert row["radiant_win"] is True
        assert row["radiant_score"] == 46
        assert row["dire_score"] == 33
        assert row["game_mode"] == 2

    def test_reads_both_team_identities(self, normal_match, patch_map):
        row = flatten_match(normal_match, patch_map)

        assert row["radiant_team_id"] == 10150538
        assert row["radiant_team_name"] == "LGD Gaming"
        assert row["dire_team_id"] == 9338413
        assert row["dire_team_name"] == "MOUZ"

    def test_merges_the_objective_counts_into_the_row(self, normal_match, patch_map):
        row = flatten_match(normal_match, patch_map)

        assert row["dire_towers_lost"] == 10
        assert row["dire_barracks_lost"] == 4
        assert row["radiant_roshan_kills"] == 2
        assert row["courier_kills"] == 6

    def test_converts_objective_timings_to_minutes(self, normal_match, patch_map):
        row = flatten_match(normal_match, patch_map)

        assert row["first_blood_time"] == 162
        assert row["first_blood_time_mins"] == 2.7      # 162 / 60 = 2.70
        assert row["first_roshan_time"] == 1635
        assert row["first_roshan_time_mins"] == 27.2    # 1635 / 60 = 27.25

    def test_keeps_a_pre_horn_first_blood_negative(
        self, negative_first_blood_match, patch_map
    ):
        # ADR-0003: first blood at t=-37 is a real kill before the horn, not a
        # clock artefact, and must survive flattening with its sign intact.
        row = flatten_match(negative_first_blood_match, patch_map)

        assert row["first_blood_time"] == -37
        assert row["first_blood_time_mins"] == -0.6

    def test_keeps_a_non_captains_mode_match(self, game_mode_1_match, patch_map):
        # Mode 1 is All Pick. Flattening records the mode and keeps the match;
        # dropping it here is what hid a whole tournament for two months.
        row = flatten_match(game_mode_1_match, patch_map)

        assert row["game_mode"] == 1
        assert row["match_id"] == 8514293820
        assert row["duration_mins"] == 40.7

    def test_falls_back_to_side_names_for_anonymous_teams(self, patch_map):
        row = flatten_match({"match_id": 1, "radiant_team": None}, patch_map)

        assert row["radiant_team_name"] == "Radiant"
        assert row["dire_team_name"] == "Dire"
        assert row["radiant_team_id"] is None

    def test_passes_an_unmapped_patch_through_as_its_raw_id(self, patch_map):
        # A patch released after the constants call was made still has to land
        # in the row rather than blanking the column.
        row = flatten_match({"match_id": 1, "patch": 99}, patch_map)

        assert row["patch"] == "99"

    def test_blanks_a_timing_of_exactly_zero(self, patch_map):
        # Characterising a known defect, not endorsing it. First blood at
        # exactly t=0 is falsy, so the minutes column comes back None while the
        # seconds column keeps the 0. Four matches in the dataset hit this; see
        # the closing note of ADR-0003. tier1-pipeline-automation/04 owns the
        # fix, and this test is here so that fix is visibly a change rather
        # than a silent one.
        row = flatten_match(
            {
                "match_id": 1,
                "objectives": [{"type": "CHAT_MESSAGE_FIRSTBLOOD", "time": 0}],
            },
            patch_map,
        )

        assert row["first_blood_time"] == 0
        assert row["first_blood_time_mins"] is None


class TestBuildRows:
    def test_returns_one_row_per_match_in_order(
        self, normal_match, game_mode_1_match, patch_map
    ):
        rows = build_rows([normal_match, game_mode_1_match], patch_map)

        assert [row["match_id"] for row in rows] == [8920872222, 8514293820]

    def test_returns_nothing_for_no_matches(self, patch_map):
        assert build_rows([], patch_map) == []
