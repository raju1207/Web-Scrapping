import argparse
import copy
import json
import tempfile
import unittest
from pathlib import Path

from school_scraping.config import (
    ConfigurationError, DEFAULT_CONFIG, PROJECT_DIR, SAFE_CANDIDATE_LIMIT, add_arguments,
    load_config, resolve_jobs, resolve_source_args, scope_key,
)


def example():
    return {
        "version": 1,
        "defaults": {"refresh_days": 30, "check_interval_hours": 24, "retry_delay_minutes": 60,
                     "academic_year": "2025-26", "year_id": 12},
        "database": {"enabled": False},
        "recovery": {"state_dir": ".scraping_state", "max_task_attempts": 5},
        "scheduler": {"time": "02:00", "timezone": "Asia/Kolkata", "sources": ["udise", "saras"]},
        "sources": {
            "udise": {"enabled": True, "all_states": False, "locations": [
                {"name": "punjab", "enabled": True, "state": "Punjab", "districts": ["Ludhiana"], "pincodes": ["140001"]},
                {"name": "haryana", "enabled": True, "state": "Haryana", "districts": [], "pincodes": ["134001", "134002"]}]},
            "saras": {"enabled": False, "locations": []},
        },
    }


def source_parser():
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--state", action="append", default=[])
    scope.add_argument("--all-states", action="store_true")
    parser.add_argument("--district", action="append", default=[])
    parser.add_argument("--pincode", action="append", default=[])
    parser.add_argument("--academic-year", default="2025-26")
    parser.add_argument("--year-id", type=int)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=.5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_DIR / "data")
    parser.add_argument("--school-list", action="append", type=Path, default=[])
    parser.add_argument("--headers-file", type=Path)
    return parser


class ConfigurationTests(unittest.TestCase):
    def write(self, root, config):
        path = Path(root) / "settings.json"
        path.write_text(json.dumps(config))
        return path

    def test_shipped_configuration_keeps_background_scheduling_and_database_disabled(self):
        config = load_config(DEFAULT_CONFIG)
        self.assertFalse(config["database"]["enabled"])
        self.assertFalse(config["scheduler"]["enabled"])
        for source in ("udise", "saras"):
            self.assertFalse(config["sources"][source]["all_states"])
            self.assertEqual({location["name"] for location in config["sources"][source]["locations"]
                              if "coverage_target" in location},
                             {"chandigarh", "delhi", "mumbai"})
            self.assertTrue(all(not location["enabled"] for location in config["sources"][source]["locations"]
                                if location["name"] in {"chandigarh", "delhi", "mumbai"}))

    def test_requested_targets_use_verified_source_specific_geographic_scopes(self):
        config = load_config(DEFAULT_CONFIG)
        for source in ("udise", "saras"):
            targets = {location["name"]: location for location in config["sources"][source]["locations"]}
            self.assertEqual(targets["chandigarh"]["state"], "CHANDIGARH")
            self.assertEqual(targets["delhi"]["state"], "DELHI")
            self.assertEqual(targets["chandigarh"]["districts"], [])
            self.assertEqual(targets["delhi"]["districts"], [])
            self.assertEqual(targets["mumbai"]["state"], "MAHARASHTRA")
            self.assertEqual(set(targets["mumbai"]["districts"]),
                             {"MUMBAI II", "MUMBAI (SUBURBAN)"} if source == "udise"
                             else {"MUMBAI", "MUMBAI SUBURBAN"})
            for name in ("chandigarh", "delhi", "mumbai"):
                self.assertEqual(targets[name]["pincodes"], [])
                self.assertEqual(targets[name]["coverage_reference"], "config/location_geography_reference.json")

    def test_mumbai_target_rejects_statewide_and_unrelated_district_scopes(self):
        for source in ("udise", "saras"):
            for fields in ({"districts": []}, {"districts": ["PUNE", "MUMBAI"]}, {"state": "DELHI"}):
                with tempfile.TemporaryDirectory() as root:
                    config = json.loads(DEFAULT_CONFIG.read_text())
                    location = next(location for location in config["sources"][source]["locations"]
                                    if location["name"] == "mumbai")
                    location.update(fields)
                    with self.subTest(source=source, fields=fields), self.assertRaisesRegex(ConfigurationError, "Mumbai requires"):
                        load_config(self.write(root, config))

    def test_mumbai_cli_location_filter_cannot_expand_verified_scope(self):
        with tempfile.TemporaryDirectory() as root:
            config = json.loads(DEFAULT_CONFIG.read_text())
            next(location for location in config["sources"]["saras"]["locations"]
                 if location["name"] == "mumbai")["enabled"] = True
            argv = ["--config", str(self.write(root, config)), "--location", "mumbai", "--district", "PUNE"]
            with self.assertRaisesRegex(ConfigurationError, "Mumbai requires"):
                resolve_source_args(source_parser().parse_args(argv), "saras", argv)

    def test_paired_geographies_are_separate_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            config = load_config(self.write(root, example()))
            jobs = resolve_jobs(config, "udise")
        self.assertEqual([job["state"] for job in jobs], ["Punjab", "Haryana"])
        self.assertEqual(jobs[0]["pincodes"], ["140001"])
        self.assertEqual(jobs[1]["pincodes"], ["134001", "134002"])
        self.assertNotEqual(jobs[0]["scope_key"], jobs[1]["scope_key"])

    def test_disabled_locations_and_source_are_skipped(self):
        config = example()
        config["sources"]["udise"]["locations"][0]["enabled"] = False
        with tempfile.TemporaryDirectory() as root:
            config = load_config(self.write(root, config))
        self.assertEqual([job["name"] for job in resolve_jobs(config, "udise")], ["haryana"])
        self.assertEqual(resolve_jobs(config, "saras"), [])

    def test_location_settings_override_source_and_shared_defaults(self):
        config = example()
        config["sources"]["udise"]["refresh_days"] = 10
        config["sources"]["udise"]["locations"][0]["refresh_days"] = 2
        with tempfile.TemporaryDirectory() as root:
            jobs = resolve_jobs(load_config(self.write(root, config)), "udise")
        self.assertEqual([job["refresh_days"] for job in jobs], [2, 10])

    def test_null_limit_is_bounded_and_unlimited_requires_explicit_cli_flag(self):
        with tempfile.TemporaryDirectory() as root:
            config = example()
            config["defaults"]["limit"] = None
            path = self.write(root, config)
            base = ["--config", str(path), "--location", "haryana"]
            bounded = resolve_source_args(source_parser().parse_args(base), "udise", base)[0]
            full = [*base, "--unlimited", "--max-duration", "120"]
            unlimited = resolve_source_args(source_parser().parse_args(full), "udise", full)[0]
        self.assertEqual(bounded.limit, SAFE_CANDIDATE_LIMIT)
        self.assertFalse(bounded.unlimited)
        self.assertEqual(bounded.collection_mode, "bounded")
        self.assertIsNone(unlimited.limit)
        self.assertTrue(unlimited.unlimited)
        self.assertEqual(unlimited.max_duration, 120)
        self.assertEqual(unlimited.collection_mode, "unlimited")

    def test_cli_and_location_candidate_limits_are_honored(self):
        with tempfile.TemporaryDirectory() as root:
            config = example()
            config["defaults"]["limit"] = 19
            config["sources"]["udise"]["limit"] = 11
            config["sources"]["udise"]["locations"][1]["limit"] = 7
            base = ["--config", str(self.write(root, config)), "--location", "haryana"]
            inherited = resolve_source_args(source_parser().parse_args(base), "udise", base)[0]
            explicit = [*base, "--limit", "2"]
            selected = resolve_source_args(source_parser().parse_args(explicit), "udise", explicit)[0]
            full = [*base, "--unlimited"]
            unlimited = resolve_source_args(source_parser().parse_args(full), "udise", full)[0]
        self.assertEqual(inherited.limit, 7)
        self.assertEqual(selected.limit, 2)
        self.assertIsNone(unlimited.limit)

    def test_conflicting_limits_and_invalid_time_budgets_are_rejected(self):
        for extra in (["--limit", "2", "--unlimited"], ["--max-duration", "0"],
                      ["--max-duration", "-1"], ["--max-duration", "nan"], ["--max-duration", "inf"]):
            argv = ["--state", "Punjab", *extra]
            with self.subTest(extra=extra), self.assertRaises(ConfigurationError):
                resolve_source_args(source_parser().parse_args(argv), "udise", argv)

    def test_coverage_metadata_is_preserved_and_validated(self):
        with tempfile.TemporaryDirectory() as root:
            config = example()
            location = config["sources"]["udise"]["locations"][0]
            location.update({"coverage_target": "chandigarh", "coverage_reference": "config/geography.json"})
            loaded = load_config(self.write(root, config))
            self.assertEqual(loaded["sources"]["udise"]["locations"][0]["coverage_target"], "chandigarh")
            for change in ({"coverage_target": "all_india"}, {"coverage_reference": ""}):
                invalid = copy.deepcopy(config)
                invalid["sources"]["udise"]["locations"][0].update(change)
                with self.subTest(change=change), self.assertRaises(ConfigurationError):
                    load_config(self.write(root, invalid))

    def test_valid_configured_pincodes_normalize_surrounding_whitespace(self):
        with tempfile.TemporaryDirectory() as root:
            config = example()
            config["sources"]["udise"]["locations"][0]["pincodes"] = [" 140001 ", "140001"]
            loaded = load_config(self.write(root, config))
        self.assertEqual(loaded["sources"]["udise"]["locations"][0]["pincodes"], ["140001"])

    def test_invalid_settings_are_rejected_before_execution(self):
        modifications = [
            lambda c: c.update({"password": "never-log"}),
            lambda c: c["defaults"].update({"refresh_days": -1}),
            lambda c: c["defaults"].update({"delay": float("nan")}),
            lambda c: c["defaults"].update({"year_id": True}),
            lambda c: c["defaults"].update({"unlimited": True}),
            lambda c: c["defaults"].update({"academic_year": "Real Time"}),
            lambda c: c["recovery"].update({"max_task_attempts": 0}),
            lambda c: c["scheduler"].update({"time": "25:00"}),
            lambda c: c["scheduler"].update({"timezone": "No/SuchZone"}),
            lambda c: c["scheduler"].update({"sources": ["udise", "udise"]}),
            lambda c: c["sources"]["udise"]["locations"][0].update({"pincodes": ["Address 140001"]}),
            lambda c: c["sources"]["udise"]["locations"][0].update({"enabled": "yes"}),
            lambda c: c["sources"]["udise"]["locations"][1].update({"name": "punjab"}),
        ]
        for index, modify in enumerate(modifications):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as root:
                config = example()
                modify(config)
                with self.assertRaises(ConfigurationError):
                    load_config(self.write(root, config))

    def test_all_states_mode_is_explicit_and_exclusive(self):
        config = example()
        config["sources"]["udise"]["all_states"] = True
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ConfigurationError):
                load_config(self.write(root, config))
            config["sources"]["udise"]["locations"] = []
            jobs = resolve_jobs(load_config(self.write(root, config)), "udise")
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["all_states"])
        self.assertEqual(jobs[0]["name"], "all_states")

    def test_scope_identity_is_stable_when_inputs_are_reordered(self):
        self.assertEqual(scope_key("udise", ["Punjab", "Haryana"], ["A", "B"], ["140001", "134001"]),
                         scope_key("udise", ["haryana", "Punjab"], ["B", "A"], ["134001", "140001"]))
        self.assertNotEqual(scope_key("udise", ["Punjab"]), scope_key("saras", ["Punjab"]))

    def test_explicit_cli_geography_stays_one_batch_and_overrides_disabled_examples(self):
        argv = ["--state", "Punjab", "--state", "Haryana", "--limit", "2", "--refresh-days", "1",
                "--academic-year", "2026-27", "--year-id", "0"]
        args = source_parser().parse_args(argv)
        plans = resolve_source_args(args, "udise", argv)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].state, ["Punjab", "Haryana"])
        self.assertEqual(plans[0].limit, 2)
        self.assertEqual(plans[0].refresh_days, 1)
        self.assertEqual(plans[0].academic_year, "2026-27")
        self.assertEqual(plans[0].year_id, 0)
        self.assertEqual(args.state, ["Punjab", "Haryana"])

    def test_cli_numeric_and_year_values_are_validated(self):
        for extra in (["--refresh-days", "-1"], ["--max-task-attempts", "0"], ["--academic-year", "bad"]):
            argv = ["--state", "Punjab", *extra]
            with self.subTest(extra=extra), self.assertRaises(ConfigurationError):
                resolve_source_args(source_parser().parse_args(argv), "udise", argv)

    def test_custom_data_directory_also_isolates_recovery_state(self):
        with tempfile.TemporaryDirectory() as root:
            argv = ["--state", "Punjab", "--data-dir", root]
            plan = resolve_source_args(source_parser().parse_args(argv), "udise", argv)[0]
            self.assertEqual(plan.state_dir, Path(root) / ".scraping_state")
            self.assertFalse(plan.state_dir.exists())

    def test_config_location_and_explicit_filter_override(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, example())
            argv = ["--config", str(path), "--location", "haryana", "--pincode", "134003", "--limit", "1"]
            plans = resolve_source_args(source_parser().parse_args(argv), "udise", argv)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].state, ["Haryana"])
        self.assertEqual(plans[0].pincode, ["134003"])
        self.assertEqual(plans[0].limit, 1)

    def test_unknown_or_disabled_location_is_rejected(self):
        argv = ["--location", "punjab"]
        with self.assertRaisesRegex(ConfigurationError, "Unknown or disabled"):
            resolve_source_args(source_parser().parse_args(argv), "udise", argv)

    def test_source_paths_are_project_relative_even_with_cli_geography(self):
        config = example()
        config["sources"]["udise"]["school_lists"] = ["exports/punjab.json"]
        config["sources"]["udise"]["headers_file"] = "private/headers.json"
        with tempfile.TemporaryDirectory() as root:
            argv = ["--config", str(self.write(root, config)), "--state", "Punjab"]
            plan = resolve_source_args(source_parser().parse_args(argv), "udise", argv)[0]
        self.assertEqual(plan.school_list, [PROJECT_DIR / "exports/punjab.json"])
        self.assertEqual(plan.headers_file, PROJECT_DIR / "private/headers.json")

    def test_sync_pending_works_without_enabled_locations_or_source_access(self):
        with tempfile.TemporaryDirectory() as root:
            argv = ["--sync-pending", "--data-dir", root]
            plans = resolve_source_args(source_parser().parse_args(argv), "udise", argv)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].location_name, "maintenance")
            self.assertEqual(plans[0].state, [])
            self.assertFalse(plans[0].all_states)
            self.assertFalse(plans[0].database)
            self.assertFalse(plans[0].state_dir.exists())


if __name__ == "__main__":
    unittest.main()
