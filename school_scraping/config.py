"""Validated shared collection settings. Relative paths use the project root."""

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from school_scraping.common import parse_values, pin, read_json


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_DIR / "config" / "states_pincodes.json"
SAFE_CANDIDATE_LIMIT = 25
DEFAULTS = {
    "refresh_days": 30, "check_interval_hours": 24, "retry_delay_minutes": 60,
    "retries": 3, "delay": 0.5, "limit": SAFE_CANDIDATE_LIMIT, "academic_year": "2025-26",
    "year_id": None,
}
NUMBERS = {"refresh_days", "check_interval_hours", "retry_delay_minutes", "delay"}
INTEGERS = {"retries", "year_id", "limit", "max_task_attempts"}
SETTINGS = set(DEFAULTS) | {"max_task_attempts"}
# Official district catalogs retrieved 2026-11-10; provenance is recorded in
# config/location_geography_reference.json. The sources use different names.
MUMBAI_SOURCE_DISTRICTS = {
    "udise": frozenset({"mumbai ii", "mumbai (suburban)"}),
    "saras": frozenset({"mumbai", "mumbai suburban"}),
}


class ConfigurationError(ValueError):
    pass


def project_path(value):
    path = Path(value)
    return path if path.is_absolute() else PROJECT_DIR / path


def scope_key(source, states=None, districts=None, pincodes=None, all_states=False):
    """Stable scope identity independent of display names and option ordering."""
    scope = {"source": source, "states": sorted(set(s.casefold().strip() for s in (states or []))),
             "districts": sorted(set(d.casefold().strip() for d in (districts or []))),
             "pincodes": sorted(set(pincodes or [])), "all_states": bool(all_states)}
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _unknown(value, allowed, label):
    unknown = set(value) - set(allowed)
    if unknown:
        raise ConfigurationError(f"Unknown {label} setting(s): {', '.join(sorted(unknown))}")


def _object(value, label):
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be a JSON object")


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ConfigurationError(f"{label} must be true or false")


def _settings(value, label):
    for key, item in value.items():
        if key in NUMBERS:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0 or not float(item) < float("inf"):
                raise ConfigurationError(f"{label}.{key} must be a finite nonnegative number")
        if key in INTEGERS:
            if item is None and key in {"year_id", "limit"}:
                continue
            if isinstance(item, bool) or not isinstance(item, int):
                raise ConfigurationError(f"{label}.{key} must be an integer")
            if item < (1 if key in {"limit", "max_task_attempts"} else 0):
                raise ConfigurationError(f"{label}.{key} is outside the permitted range")
        if key == "academic_year" and (not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}", item)):
            raise ConfigurationError(f"{label}.academic_year must be YYYY-YY")


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigurationError(f"{label} must be a list of nonempty strings")


def _validate_target_scope(source, target, states, districts, label):
    if target != "mumbai":
        return
    if ([state.strip().casefold() for state in states] != ["maharashtra"]
            or len(districts) != 2
            or {district.strip().casefold() for district in districts} != MUMBAI_SOURCE_DISTRICTS[source]):
        raise ConfigurationError(f"{label}: Mumbai requires MAHARASHTRA and both verified {source} Mumbai districts")


def load_config(path=DEFAULT_CONFIG):
    path = project_path(path)
    try:
        config = copy.deepcopy(read_json(path))
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"Cannot read collection configuration: {path}") from error
    _object(config, "configuration")
    _unknown(config, {"version", "defaults", "scheduler", "sources", "database", "recovery"}, "top-level")
    if isinstance(config.get("version"), bool) or config.get("version", 1) != 1:
        raise ConfigurationError("Unsupported configuration version; expected 1")
    defaults = config.setdefault("defaults", {})
    _object(defaults, "defaults")
    _unknown(defaults, SETTINGS, "defaults")
    _settings(defaults, "defaults")
    config["defaults"] = {**DEFAULTS, **defaults}
    database = config.setdefault("database", {"enabled": False})
    _object(database, "database")
    _unknown(database, {"enabled"}, "database")
    _boolean(database.setdefault("enabled", False), "database.enabled")
    recovery = config.setdefault("recovery", {})
    _object(recovery, "recovery")
    _unknown(recovery, {"state_dir", "max_task_attempts"}, "recovery")
    recovery.setdefault("state_dir", ".scraping_state")
    recovery.setdefault("max_task_attempts", 5)
    if not isinstance(recovery["state_dir"], str) or not recovery["state_dir"].strip():
        raise ConfigurationError("recovery.state_dir must be a nonempty path")
    _settings({"max_task_attempts": recovery["max_task_attempts"]}, "recovery")
    scheduler = config.setdefault("scheduler", {})
    _object(scheduler, "scheduler")
    _unknown(scheduler, {"enabled", "time", "timezone", "sources"}, "scheduler")
    scheduler.setdefault("enabled", False)
    scheduler.setdefault("time", "02:00")
    scheduler.setdefault("timezone", "Asia/Kolkata")
    scheduler.setdefault("sources", ["udise", "saras"])
    _boolean(scheduler["enabled"], "scheduler.enabled")
    if not isinstance(scheduler["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", scheduler["time"]):
        raise ConfigurationError("scheduler.time must be a valid HH:MM time")
    try:
        ZoneInfo(scheduler["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError) as error:
        raise ConfigurationError("scheduler.timezone must be a valid IANA timezone") from error
    _strings(scheduler["sources"], "scheduler.sources")
    if set(scheduler["sources"]) - {"udise", "saras"}:
        raise ConfigurationError("scheduler.sources only accepts udise and saras")
    if len(set(scheduler["sources"])) != len(scheduler["sources"]):
        raise ConfigurationError("scheduler.sources cannot contain duplicates")
    sources = config.setdefault("sources", {})
    _object(sources, "sources")
    _unknown(sources, {"udise", "saras"}, "source")
    for source in ("udise", "saras"):
        section = sources.setdefault(source, {"enabled": False, "locations": []})
        _object(section, f"sources.{source}")
        _unknown(section, SETTINGS | {"enabled", "all_states", "locations", "school_lists", "headers_file", "storage_state"}, source)
        _settings(section, source)
        _boolean(section.setdefault("enabled", True), f"{source}.enabled")
        _boolean(section.setdefault("all_states", False), f"{source}.all_states")
        section.setdefault("locations", [])
        if not isinstance(section["locations"], list):
            raise ConfigurationError(f"{source}.locations must be a list")
        if "school_lists" in section:
            _strings(section["school_lists"], f"{source}.school_lists")
        for field in ("headers_file", "storage_state"):
            if field in section and section[field] is not None and (not isinstance(section[field], str) or not section[field].strip()):
                raise ConfigurationError(f"{source}.{field} must be a nonempty path or null")
        names = set()
        for index, location in enumerate(section["locations"]):
            label = f"{source}.locations[{index}]"
            _object(location, label)
            _unknown(location, SETTINGS | {"name", "enabled", "state", "districts", "pincodes",
                                           "coverage_target", "coverage_reference"}, label)
            _settings(location, label)
            _boolean(location.setdefault("enabled", True), f"{label}.enabled")
            state = location.get("state")
            if not isinstance(state, str) or not state.strip():
                raise ConfigurationError(f"{label}.state must be a nonempty official state name")
            location.setdefault("name", re.sub(r"[^a-z0-9]+", "_", state.casefold()).strip("_"))
            if not isinstance(location["name"], str) or not location["name"].strip() or location["name"] in names:
                raise ConfigurationError(f"{label}.name must be nonempty and unique within its source")
            names.add(location["name"])
            if "coverage_target" in location and (not isinstance(location["coverage_target"], str)
                                                   or location["coverage_target"] not in {"chandigarh", "delhi", "mumbai"}):
                raise ConfigurationError(f"{label}.coverage_target must be chandigarh, delhi or mumbai")
            if "coverage_reference" in location and (not isinstance(location["coverage_reference"], str)
                                                       or not location["coverage_reference"].strip()):
                raise ConfigurationError(f"{label}.coverage_reference must be a nonempty provenance path")
            for field in ("districts", "pincodes"):
                location.setdefault(field, [])
                _strings(location[field], f"{label}.{field}")
            if any(pin(value) is None for value in location["pincodes"]):
                raise ConfigurationError(f"{label}.pincodes must contain exact six-digit PIN values")
            location["pincodes"] = list(dict.fromkeys(pin(value) for value in location["pincodes"]))
            _validate_target_scope(source, location.get("coverage_target"), [state], location["districts"], label)
        if section["all_states"] and any(location["enabled"] for location in section["locations"]):
            raise ConfigurationError(f"{source}: all_states cannot be combined with enabled location scopes")
    config["_path"] = str(path.resolve())
    return config


def resolve_jobs(config, source, overrides=None):
    """Return enabled location plans; a source-wide all-states plan is explicit."""
    if source not in {"udise", "saras"}:
        raise ConfigurationError("Unknown scraper source")
    overrides = overrides or {}
    section = config["sources"][source]
    if not section["enabled"] and not overrides.get("force_enabled"):
        return []
    settings = {**config["defaults"], "max_task_attempts": config["recovery"]["max_task_attempts"],
                **{key: value for key, value in section.items() if key in SETTINGS}}
    locations = ([{"name": "all_states", "enabled": True, "state": None, "districts": [], "pincodes": []}]
                 if section["all_states"] else section["locations"])
    jobs = []
    for location in locations:
        if not location["enabled"]:
            continue
        job = {**settings, **location, "all_states": section["all_states"],
               "database": config["database"]["enabled"],
               "state_dir": project_path(config["recovery"]["state_dir"])}
        for field in ("school_lists", "headers_file", "storage_state"):
            if field in section:
                job[field] = section[field]
        job.update({key: value for key, value in overrides.items() if key != "force_enabled"})
        job["limit"] = job.get("limit") or SAFE_CANDIDATE_LIMIT
        job["scope_key"] = scope_key(source, [job["state"]] if job["state"] else [],
                                      job["districts"], job["pincodes"], job["all_states"])
        jobs.append(job)
    return jobs


def add_arguments(parser):
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Shared JSON configuration (relative paths use project root).")
    parser.add_argument("--location", action="append", default=[], help="Enabled configuration location name; repeat as needed.")
    parser.add_argument("--refresh-days", type=float, default=None, help="Periodically re-fetch detail records after this many days.")
    parser.add_argument("--check-interval-hours", type=float, default=None, help="Minimum interval between successful location checks.")
    parser.add_argument("--retry-delay-minutes", type=float, default=None, help="Delay before retrying eligible failed tasks.")
    parser.add_argument("--max-task-attempts", type=int, default=None, help="Maximum automatic attempts for a failed task.")
    parser.add_argument("--retry-failed", action="store_true", help="Resume eligible failed tasks in the requested scope.")
    parser.add_argument("--sync-pending", action="store_true", help="Replay pending local events/imports before collection.")
    parser.add_argument("--database", action=argparse.BooleanOptionalAction, default=None,
                        help="Enable PostgreSQL integration after applying the reviewed migration; disabled by default.")
    parser.add_argument("--state-dir", type=Path, default=None, help="Local crash-safe recovery state directory.")
    parser.add_argument("--unlimited", action="store_true",
                        help="Explicitly evaluate every source candidate; omitted/null limits otherwise use a safe bounded default.")
    parser.add_argument("--max-duration", type=float, default=None, metavar="SECONDS",
                        help="Stop safely after this many elapsed seconds; a timed run does not establish complete coverage.")


def explicit_options(argv=None):
    return {value[2:].split("=", 1)[0].replace("-", "_") for value in (sys.argv[1:] if argv is None else argv)
            if value.startswith("--")}


def _recovery_path(args, config, explicit, overrides):
    if "state_dir" in explicit:
        return project_path(overrides["state_dir"])
    if "data_dir" in explicit and hasattr(args, "data_dir"):
        return project_path(args.data_dir) / ".scraping_state"
    return project_path(config["recovery"]["state_dir"])


def _source_paths(selected, section, source, explicit):
    if source == "udise":
        if "school_list" not in explicit and "udise_school_list" not in explicit:
            selected.school_list = [project_path(path) for path in section.get("school_lists", [])]
        if "headers_file" not in explicit and "udise_headers_file" not in explicit and section.get("headers_file"):
            selected.headers_file = project_path(section["headers_file"])
    elif "storage_state" not in explicit and section.get("storage_state"):
        selected.storage_state = project_path(section["storage_state"])


def _collection_limits(selected, args, explicit):
    """A null or omitted configured limit never silently enables full collection."""
    unlimited = "unlimited" in explicit and bool(getattr(args, "unlimited", False))
    if unlimited and "limit" in explicit:
        raise ConfigurationError("--limit and --unlimited cannot be combined")
    selected.unlimited = unlimited
    selected.limit = None if unlimited else (getattr(selected, "limit", None) or SAFE_CANDIDATE_LIMIT)
    selected.collection_mode = "unlimited" if unlimited else "bounded"
    duration = getattr(args, "max_duration", None)
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                 or not 0 < duration < float("inf")):
        raise ConfigurationError("--max-duration must be a finite positive number of seconds")
    selected.max_duration = duration
    return selected


def resolve_source_args(args, source, argv=None):
    """Map config plans onto existing CLI namespaces without mutating the caller.

    Explicit CLI geography stays one batch, preserving the existing run-wide limit.
    Configured locations remain separate batches so their PINs cannot cross states.
    """
    config = load_config(getattr(args, "config", DEFAULT_CONFIG))
    explicit = explicit_options(argv)
    if "limit" in explicit and "unlimited" in explicit:
        raise ConfigurationError("--limit and --unlimited cannot be combined")
    cli_scope = bool(explicit & {"state", "all_states"})
    overrides = {}
    for key in SETTINGS | {"database", "state_dir"}:
        if key in explicit and hasattr(args, key):
            overrides[key] = getattr(args, key)
    if "no_database" in explicit:
        overrides["database"] = False
    _settings(overrides, "CLI")
    if cli_scope:
        settings = {**config["defaults"], "max_task_attempts": config["recovery"]["max_task_attempts"],
                    **{key: value for key, value in config["sources"][source].items() if key in SETTINGS}, **overrides}
        selected = copy.copy(args)
        for key, value in settings.items():
            setattr(selected, key, value)
        selected.state = parse_values(getattr(args, "state", []))
        selected.district = parse_values(getattr(args, "district", []))
        selected.pincode = parse_values(getattr(args, "pincode", []))
        if not selected.state and not getattr(selected, "all_states", False):
            raise ConfigurationError("An explicit state selection cannot be empty")
        if any(pin(value) is None for value in selected.pincode):
            raise ConfigurationError("Pincodes must contain exact six-digit PIN values")
        selected.location_name = "cli_scope"
        selected.scope_key = scope_key(source, selected.state, selected.district, selected.pincode,
                                       getattr(selected, "all_states", False))
        selected.database = overrides.get("database", config["database"]["enabled"])
        selected.state_dir = _recovery_path(args, config, explicit, overrides)
        selected.config = Path(config["_path"])
        _source_paths(selected, config["sources"][source], source, explicit)
        return [_collection_limits(selected, args, explicit)]
    jobs = resolve_jobs(config, source, overrides)
    wanted = parse_values(getattr(args, "location", []))
    if wanted:
        missing = set(wanted) - {job["name"] for job in jobs}
        if missing:
            raise ConfigurationError("Unknown or disabled location(s): " + ", ".join(sorted(missing)))
        jobs = [job for job in jobs if job["name"] in wanted]
    namespaces = []
    for job in jobs:
        selected = copy.copy(args)
        for key in SETTINGS | {"database", "state_dir", "all_states", "scope_key", "coverage_target", "coverage_reference"}:
            if key in job:
                setattr(selected, key, job[key])
        selected.state = [job["state"]] if job["state"] else []
        selected.district = parse_values(getattr(args, "district", [])) if "district" in explicit else list(job["districts"])
        selected.pincode = parse_values(getattr(args, "pincode", [])) if "pincode" in explicit else list(job["pincodes"])
        if any(pin(value) is None for value in selected.pincode):
            raise ConfigurationError("Pincodes must contain exact six-digit PIN values")
        selected.location_name = job["name"]
        _validate_target_scope(source, job.get("coverage_target"), selected.state, selected.district, "Resolved location scope")
        selected.scope_key = scope_key(source, selected.state, selected.district, selected.pincode, selected.all_states)
        selected.state_dir = _recovery_path(args, config, explicit, overrides)
        selected.config = Path(config["_path"])
        _source_paths(selected, config["sources"][source], source, explicit)
        namespaces.append(_collection_limits(selected, args, explicit))
    if not namespaces and getattr(args, "sync_pending", False):
        selected = copy.copy(args)
        for key, value in {**config["defaults"], "max_task_attempts": config["recovery"]["max_task_attempts"], **overrides}.items():
            setattr(selected, key, value)
        selected.state, selected.district, selected.pincode = [], [], []
        selected.all_states = False
        selected.location_name = "maintenance"
        selected.scope_key = scope_key(source)
        selected.database = overrides.get("database", config["database"]["enabled"])
        selected.state_dir = _recovery_path(args, config, explicit, overrides)
        selected.config = Path(config["_path"])
        namespaces.append(_collection_limits(selected, args, explicit))
    return namespaces
