import asyncio, json, math, random
import hashlib
import io
import ipaddress
import secrets
from collections import deque
from typing import Optional, Dict, List, Tuple, NamedTuple, Literal
import numpy as np
import osmnx as ox
from scipy.optimize import linear_sum_assignment
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body, Request
from starlette.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import csv
import time
import networkx as nx
import httpx
import logging
import os
import shutil
import pandas as pd

from fastapi import Response
import platform
import subprocess
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from importlib import metadata as package_metadata

from functools import lru_cache
from contextlib import asynccontextmanager
import threading

# Capture the backend source once, when this module loads.
BACKEND_SOURCE_PATH = os.path.abspath(__file__)
with open(BACKEND_SOURCE_PATH, "rb") as source_file:
    BACKEND_SOURCE_BYTES = source_file.read()

BACKEND_SOURCE_INFO = {
    "filename": os.path.basename(BACKEND_SOURCE_PATH),
    "snapshot_filename": "backend_source.py",
    "sha256": hashlib.sha256(BACKEND_SOURCE_BYTES).hexdigest(),
    "size_bytes": len(BACKEND_SOURCE_BYTES),
}


# ============================================================
# F-023: Deferred Environment Snapshot
# ============================================================

# Do not execute pip freeze when app.py is imported.
PACKAGE_SNAPSHOT_BYTES = None
PACKAGE_SNAPSHOT_INFO = None


def ensure_package_snapshot():
    """
    Capture installed Python packages once per process.

    The snapshot is generated when preparing a simulation run,
    rather than during Python module import.
    """
    global PACKAGE_SNAPSHOT_BYTES, PACKAGE_SNAPSHOT_INFO

    # Reuse the snapshot for subsequent runs in this process.
    if PACKAGE_SNAPSHOT_BYTES is not None:
        return

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--all"],
            check=True,
            capture_output=True,
            timeout=30,
        )

    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ) as error:
        raise RuntimeError(
            "Cannot prepare a reproducible simulation run: "
            "the installed-package snapshot could not be captured. "
            "Check pip in the active virtual environment."
        ) from error

    snapshot = result.stdout

    if not snapshot.strip():
        raise RuntimeError(
            "The installed-package snapshot is empty."
        )

    info = {
        "filename": "requirements-freeze.txt",
        "sha256": hashlib.sha256(snapshot).hexdigest(),
        "size_bytes": len(snapshot),
        "generator": "python -m pip freeze --all",
        "capture_stage": "run_preparation",
        "warnings": (
            result.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip() or None
        ),
    }

    # Publish only after successful capture.
    PACKAGE_SNAPSHOT_BYTES = snapshot
    PACKAGE_SNAPSHOT_INFO = info

EXPERIMENT_CONFIG = {
    "S1": {
        "vehicles": 12,
        "requests": 30,
        "initial_soc_range": [1.0, 1.0],
    },
    "S2": {
        "vehicles": 18,
        "requests": 50,
        "initial_soc_range": [1.0, 1.0],
    },
    "S3": {
        "vehicles": 24,
        "requests": 100,
        "initial_soc_range": [1.0, 1.0],
    },
    "S4": {
        "vehicles": 12,
        "requests": 100,
        "initial_soc_range": [1.0, 1.0],
    },
}
# ==============================
# Experiment Configuration
# ==============================
# Stage 2: reproducibility seeds
TRIAL_SEEDS = [
    1001,
    2001,
    3001,
    4001,
    5001,
    6001,
    7001,
    8001,
    9001,
    10001,
    11001,
    12001,
    13001,
    14001,
    15001,
    16001,
    17001,
    18001,
    19001,
    20001
]

# Version 2 replaces the old arithmetic seed offsets.
SEED_DERIVATION_VERSION = 2

# Fixed identifiers: do not renumber these when adding a stream.
SEED_STREAM_IDS = {
    'demand': 1,
    'arrival': 2,
    'algorithm': 3,
    'initial_soc': 4,
}


def derive_trial_seed(trial_seed, stream):
    """Derive a reproducible 128-bit seed for one named stream."""
    from numpy.random import SeedSequence

    if (
        isinstance(trial_seed, bool)
        or not isinstance(trial_seed, int)
        or trial_seed < 0
    ):
        raise ValueError(
            "The trial seed must be a non-negative integer."
        )

    if stream not in SEED_STREAM_IDS:
        raise ValueError(f"Unknown random stream: {stream!r}")

    sequence = SeedSequence([
        SEED_DERIVATION_VERSION,
        trial_seed,
        SEED_STREAM_IDS[stream],
    ])

    words = sequence.generate_state(4)

    # Explicit word order avoids depending on machine byte order.
    return sum(
        int(word) << (32 * index)
        for index, word in enumerate(words)
    )


TRIAL_SEED = TRIAL_SEEDS[0]

DEMAND_SEED = derive_trial_seed(TRIAL_SEED, "demand")
ARRIVAL_SEED = derive_trial_seed(TRIAL_SEED, "arrival")
ALGORITHM_SEED = derive_trial_seed(TRIAL_SEED, "algorithm")
INITIAL_SOC_SEED = derive_trial_seed(TRIAL_SEED, "initial_soc")

# Retained temporarily because legacy code still references them.

DEMAND_RNG = random.Random(DEMAND_SEED)
ARRIVAL_RNG = random.Random(ARRIVAL_SEED)
ALGORITHM_RNG = random.Random(ALGORITHM_SEED)
INITIAL_SOC_RNG = random.Random(INITIAL_SOC_SEED)
def reset_random_streams() -> None:
    DEMAND_RNG.seed(DEMAND_SEED)
    ARRIVAL_RNG.seed(ARRIVAL_SEED)
    ALGORITHM_RNG.seed(ALGORITHM_SEED)
    INITIAL_SOC_RNG.seed(INITIAL_SOC_SEED)

def set_trial_seed(seed):
    global TRIAL_SEED, RANDOM_SEED
    global DEMAND_SEED, ARRIVAL_SEED, ALGORITHM_SEED
    global INITIAL_SOC_SEED

    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or seed < 0
    ):
        raise ValueError(
            "The trial seed must be a non-negative integer."
        )

    TRIAL_SEED = seed
    RANDOM_SEED = seed

    DEMAND_SEED = derive_trial_seed(seed, "demand")
    ARRIVAL_SEED = derive_trial_seed(seed, "arrival")
    ALGORITHM_SEED = derive_trial_seed(seed, "algorithm")
    INITIAL_SOC_SEED = derive_trial_seed(seed, "initial_soc")

    reset_random_streams()


# ==============================
# Random Seed
# ==============================

RANDOM_SEED = TRIAL_SEED


random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ── Global metrics ────────────────────────────────────────────────
fulfilled_requests     = 0
total_travel_time      = 0.0
total_energy_consumed  = 0.0
total_idle_time        = 0.0
total_charging_cost    = 0.0
assignment_runtime_ms = []
dispatch_runtime_ms = []
spawned_requests_total = 0

# ── UGV Vehicle Dynamics Parameters ──────────────────────────────
# Confirmed vehicle and passenger inputs.

# ============================================================
# GMTLSV Vehicle Mass and Passenger Configuration
# ============================================================

# Manufacturer-listed empty mass including batteries: 825 kg.
# Passenger payload is excluded. No added autonomy-equipment mass is modeled.
# Source: GMTLSV six-seat enclosed electric golf-cart product listing.
UGV_EMPTY_MASS_KG = 825.0

# Assumed passenger mass for simulation.
UGV_PASSENGER_MASS_KG = 80.0

# Research assumption: driverless operation with up to
# six passenger positions. Physical seating configuration
# must be verified because the manufacturer lists six seats.
UGV_PASSENGER_CAPACITY = 6

# Compatibility alias for existing energy-model helpers.
UGV_MASS_KG = UGV_EMPTY_MASS_KG

UGV_G = 9.81
UGV_C_RR = 0.012
UGV_RHO = 1.23

# Dimensions and gearing from the user-supplied specification.
UGV_BODY_WIDTH = 1.38
UGV_HEIGHT = 1.70             # Overall body height, not centre-of-mass height.
UGV_TRACK_WIDTH = 1.17
UGV_WHEELBASE = 2.995
UGV_GEAR_RATIO = 12.49
UGV_WHEEL_RADIUS = 0.2675

# Provisional aerodynamic and efficiency assumptions.
UGV_A = 2.0                  # Frontal area in m²; not track width x height.
UGV_C_D = 0.50
UGV_ETA = 0.90               # Overall battery-to-wheel efficiency.
UGV_GEAR_EFFICIENCY = 0.95   # Motor shaft to wheels; included within UGV_ETA.

UGV_T_MOTOR_MAX = 55.0        # Assumed shaft torque limit, not measured.
UGV_MOTOR_SHAFT_POWER_W = 4000.0
UGV_ROTATIONAL_MASS_FACTOR = 1.20
# GMTLSV manufacturer lists two speed modes: 25 and 40 km/h.
# FIX-19 preserves the manufacturer's 25 km/h lower-mode capability.
# The provisional campus operating cap is 22 km/h, with a target
# fleet moving average of 20 km/h. Neither is an observed result.
# Vehicle acceleration, motor limits and braking remain modeled.
# The manufacturer-listed 40 km/h mode is not used.
MANUFACTURER_LOW_SPEED_MODE_KPH = 25.0
CAMPUS_OPERATING_SPEED_CAP_KPH = 22.0
CAMPUS_TARGET_AVERAGE_MOVING_SPEED_KPH = 20.0
SPEED_KPH = CAMPUS_OPERATING_SPEED_CAP_KPH
SPEED_POLICY_VERSION = 2
UGV_V_MAX = SPEED_KPH / 3.6
UGV_ACCEL_MAX = 0.8
UGV_BRAKE_MAX = 1.5

UGV_MODEL_NOTES = {'mass': 'GMTLSV product specification lists 825 kg including batteries, excluding '
         'passenger payload. No additional autonomy-equipment mass is included in this '
         'manufacturer-vehicle benchmark.',
 'occupancy': 'Manufacturer lists six passenger positions. Passenger mass of 80 kg per '
              'occupant remains a study assumption. Autonomous dispatch is simulated; '
              'autonomy hardware is not represented as added physical vehicle mass.',
 'specification': 'Manufacturer-listed values from '
                  'https://gmtlsv.com/product/6-seat-lithium-battery-electric-golf-cart-with-ac-heater/ '
                  'and user-supplied screenshots; actual delivered hardware has not been '
                  'independently inspected.',
 'speed': 'Manufacturer lower speed mode: 25 km/h; an additional 40 km/h mode is '
          'advertised. FIX-19 applies a provisional 22 km/h campus operating cap. The '
          'target fleet moving average is 20 km/h, to be measured rather than assumed. '
          'Acceleration, braking, motor power and torque remain enforced by the motion '
          'model.',
 'speed_provenance': '25 km/h is the manufacturer-listed lower speed mode. 22 km/h is an '
                     'initial campus operating-cap experiment. 20 km/h is a target moving '
                     'average, not a hard speed cap or a guaranteed simulated result.',
 'motor_power': 'Manufacturer-listed 60 V, 4 kW motor. 4 kW modeled as motor shaft output '
                'ceiling; continuous and peak output distinction is not verified.',
 'torque': '55 Nm remains a provisional modeled shaft torque ceiling; manufacturer '
           'torque-speed curve has not been obtained.',
 'losses': 'Gear efficiency 0.95 and overall battery-to-wheel efficiency 0.90 remain '
           'simulation assumptions.',
 'road_load': 'Flat roads; aerodynamic resistance is held constant within each modeled '
              'motion phase.',
 'assumptions': 'Frontal area, aerodynamic drag, rolling resistance, efficiencies, '
                'acceleration/braking limits and rotational mass factor remain provisional '
                'modeling assumptions.',
 'geometry': 'Manufacturer-listed 535 mm tire diameter and 12.49:1 transaxle agree with '
             'modeled wheel radius and gear ratio. No steering or rollover model in pooled '
             'execution.'}

# ============================================================
# GMTLSV Auxiliary Electrical Power Model
# ============================================================

# Provisional baseline load for standard-vehicle electrical accessories.
# No autonomous computing/sensor hardware is assumed in vehicle mass.
# This 50 W electrical load is NOT manufacturer-measured.
UGV_BASE_AUX_POWER_W = 50.0

# HVAC operating mode:
# "off"     = Air conditioning and heating disabled
# "cooling" = Air conditioning enabled
# "heating" = Heating enabled
UGV_HVAC_MODE = "off"

# Manufacturer-listed cooling/heating power.
# Cooling and heating are alternative operating modes;
# their power values are not added together.
UGV_HVAC_RATED_POWER_W = 800.0

# Validate configuration.
if UGV_HVAC_MODE not in ("off", "cooling", "heating"):
    raise ValueError(
        "UGV_HVAC_MODE must be off, cooling, or heating."
    )

if (
    not math.isfinite(UGV_BASE_AUX_POWER_W)
    or UGV_BASE_AUX_POWER_W < 0.0
):
    raise ValueError(
        "UGV_BASE_AUX_POWER_W must be finite and non-negative."
    )

if (
    not math.isfinite(UGV_HVAC_RATED_POWER_W)
    or UGV_HVAC_RATED_POWER_W < 0.0
):
    raise ValueError(
        "UGV_HVAC_RATED_POWER_W must be finite and non-negative."
    )

# Active HVAC electrical load.
UGV_HVAC_ACTIVE_POWER_W = (
    UGV_HVAC_RATED_POWER_W
    if UGV_HVAC_MODE != "off"
    else 0.0
)

# Parked vehicles have their HVAC switched off.
UGV_IDLE_POWER_W = UGV_BASE_AUX_POWER_W

# Driving includes baseline electronics and optional HVAC.
# This simplified model keeps the selected HVAC mode active
# throughout all driving legs, including empty travel.
UGV_DRIVING_AUX_POWER_W = (
    UGV_BASE_AUX_POWER_W + UGV_HVAC_ACTIVE_POWER_W
)


if (
    not math.isfinite(UGV_DRIVING_AUX_POWER_W)
    or UGV_DRIVING_AUX_POWER_W < 0.0
):
    raise ValueError(
        "UGV_DRIVING_AUX_POWER_W must be finite and non-negative."
    )
if not math.isfinite(UGV_IDLE_POWER_W) or UGV_IDLE_POWER_W < 0.0:
    raise ValueError(
        "UGV_IDLE_POWER_W must be finite and non-negative."
    )
# GMTLSV lists a 60 V, 25 A onboard charger.
# FIX-18 uses 1500 W nominal DC output (60 V x 25 A).
# At 150 Ah this represents a nominal 1/6 C current rate.
# Actual charging curve, battery chemistry and efficiencies are unverified.
CHARGER_DC_POWER_W = 1500.0  # Manufacturer nominal 60 V x 25 A; real charge profile unverified.
CHARGER_EFFICIENCY = 0.90          # AC grid -> DC bus
BATTERY_CHARGE_EFFICIENCY = 0.85   # Battery input -> stored energy

# SOC-based power taper approximation.
CHARGE_TAPER_START_SOC = 0.80
CHARGE_TAPER_END_FRACTION = 0.10

# "stack_on" uses UGV_IDLE_POWER_W.
# "sleep" uses the provisional sleep load below.
PLUGGED_POWER_POLICY = "stack_on"
PLUGGED_SLEEP_POWER_W = 5.0

CHARGING_LOG_SCHEMA_VERSION = 2
ENERGY_CONSUMPTION_BASIS = "battery_withdrawal"


# Benchmark variable electricity price in MYR per kWh.
# Excludes fuel adjustments, taxes, and fixed monthly charges.
ELECTRICITY_PRICE_MYR_PER_KWH = 0.5068

# GMTLSV table: five 12 V, 150 Ah modules at the 3-hour rating.
# Series configuration ASSUMED: 60 V, 150 Ah = 9000 Wh nominal.
# Lithium-title vs maintenance-free-table chemistry remains unresolved.
BATTERY_NOMINAL_V = 60.0
BATTERY_CAPACITY_AH = 150.0
BATTERY_CAPACITY_WH = BATTERY_NOMINAL_V * BATTERY_CAPACITY_AH

# Provisional dispatch reserve; not a validated battery-life/DoD limit.
BATTERY_RESERVE_FRACTION = 0.20

ENERGY_PARAMETER_NOTES = {'battery': 'Manufacturer product table lists five maintenance-free 12 V, 150 Ah batteries '
            '(capacity quoted at the 3-hour rate). Series connection is ASSUMED from the '
            '60 V drive system, giving 60 V, 150 Ah and 9000 Wh NOMINAL energy. Nominal '
            'nameplate energy is not measured usable capacity.',
 'chemistry': 'UNRESOLVED manufacturer-page inconsistency: product title states lithium '
              'battery, while the detailed component table lists maintenance-free 12 V, '
              '150 Ah batteries at a 3-hour rate. No chemistry-specific discharge or '
              'charge model is claimed.',
 'charger': 'Manufacturer lists a 60 V, 25 A onboard charger. The modeled 1500 W DC power '
            'is the nominal product 60 V x 25 A, not a validated constant-output charging '
            'curve. Battery chemistry and charging profile remain unverified.',
 'reserve': '20% of nominal battery energy is a research dispatch reserve policy, not a '
            'manufacturer-confirmed cutoff or usable-depth-of-discharge specification.',
 'speed_dependency': 'FIX-19 uses a provisional 22 km/h campus operating cap below the '
                     "manufacturer's 25 km/h lower-mode maximum. Route predictions, motion "
                     'execution and energy estimates share this operating cap. The '
                     'achieved fleet average must be independently measured.',
 'efficiencies_and_taper': 'Charger grid efficiency 0.90, battery charge efficiency 0.85, '
                           'taper starting at 80% SOC and terminal power fraction 0.10 '
                           'remain provisional, not measured calibration.',
 'range': 'Manufacturer advertises 70-80 km loaded range. Test conditions, HVAC state, '
          'battery chemistry and usable depth of discharge are not established. The '
          'advertised range is not used to tune model coefficients.',
 'auxiliary_load': '50 W non-HVAC load is a provisional standard-vehicle '
                   'electrical-accessory assumption, not a measured autonomous-computing '
                   'or sensor load.'}

# Retained for existing callers: now a dispatch SOC floor,
# not the level below which parked charging starts.
CHARGING_THRESHOLD_WH = (
    BATTERY_CAPACITY_WH * BATTERY_RESERVE_FRACTION
)

if (
    not math.isfinite(BATTERY_CAPACITY_WH)
    or BATTERY_CAPACITY_WH <= 0.0
):
    raise ValueError("Battery capacity must be finite and positive.")

if (
    not math.isfinite(BATTERY_RESERVE_FRACTION)
    or not 0.0 <= BATTERY_RESERVE_FRACTION < 1.0
):
    raise ValueError(
        "Battery reserve fraction must be finite and in [0, 1)."
    )


# Per-mover velocity state (m/s) — replaces fixed SPEED_KPH assumption
mover_velocity: Dict[int, float] = {}

# ── Run/Experiment state ──────────────────────────────────────────
RUN = {
    "run_id": None,
    "algo": None,
    "experiment_started": False,
    "scheduler_task": None,
    "scheduler_error": None,
    "shutting_down": False,
    "csv_path": None,
    "fleet_log_csv_path": None,
    "velocity_log_csv_path": None,
    "request_csv_path": None,
    "request_records": {},
    "vehicle_csv_path": None,
    "vehicle_records": {},
    "vehicle_results_written": False,
    "assignment_csv_path": None,
    "assignment_event_counter": 0,
    "charging_csv_path": None,
    "charging_event_counter": 0,
    "grid_import_Wh": 0.0,
    "summary_csv_path": None,
    "summary_written": False,
    "req_seq_counter": 0,
    "req_seq": {},
    "trial_index": 0,
    "canonical_mode": True,
    "workload_size": 0,
    "workload_released": 0,
    "batch_running": False,
    "batch_total": 0,
    "batch_completed": 0,
    "batch_current": None,
    "batch_run_id": None,
    "batch_error": None,
    "vehicle_assigned": {},
    "vehicle_completed": {},
    "vehicle_req_dist_km": {},
    "vehicle_req_energy_Wh": {},
}

REQUEST_RESULT_FIELDS = [
    "run_id",
    "scenario",
    "trial_seed",
    "trial_index",
    "algorithm",
    "request_id",
    "vehicle_id",
    "created_time_s",
    "assigned_time_s",
    "pickup_time_s",
    "dropoff_time_s",
    "dispatch_delay_s",
    "waiting_time_s",
    "passenger_time_s",
    "response_time_s",
    "passenger_distance_km",
    "vehicle_SOC_before_Wh",
    "vehicle_SOC_after_Wh",
    "assignment_cost",
    "request_completed",
    "maximum_wait_violation",
    "completion_status",
    "failure_reason",
]

VEHICLE_RESULT_FIELDS = [
    "run_id",
    "depot_layout",
    "home_depot_id",
    "home_depot_node",
    "scenario",
    "trial_seed",
    "trial_index",
    "algorithm",
    "vehicle_id",
    "assigned_requests",
    "completed_requests",
    "productive_distance_km",
    "empty_pickup_distance_km",
    "return_distance_km",
    "total_distance_km",
    "driving_time_s",
    "idle_time_s",
    "charging_time_s",
    "energy_consumed_Wh",
    "energy_charged_Wh",
    "minimum_SOC_Wh",
    "final_SOC_Wh",
    "utilization_percent",
]

ASSIGNMENT_EVENT_FIELDS = [
    "run_id",
    "scenario",
    "trial_seed",
    "trial_index",
    "algorithm",
    "event_id",
    "simulation_tick",
    "event_time_s",
    "request_id",
    "request_sequence",
    "pickup_node",
    "drop_node",
    "vehicle_id",
    "predicted_pickup_time_s",
    "predicted_trip_time_s",
    "predicted_total_time_s",
    "predicted_incremental_route_energy_Wh",
    "predicted_incremental_route_time_s",
    "predicted_remaining_route_energy_Wh",
    "predicted_remaining_route_time_s",
    "vehicle_SOC_before_Wh",
    "energy_feasible",
    "assignment_cost",
]

CHARGING_EVENT_FIELDS = [
    "schema_version",
    "run_id",
    "scenario",
    "trial_seed",
    "trial_index",
    "algorithm",
    "session_id",
    "vehicle_id",
    "session_type",
    "end_reason",
    "start_time_s",
    "end_time_s",
    "plugged_duration_s",
    "charging_duration_s",
    "vehicle_SOC_before_Wh",
    "vehicle_SOC_after_Wh",
    "energy_charged_Wh",
    "battery_withdrawn_Wh",
    "parked_load_energy_Wh",
    "grid_energy_kWh",
    "charging_cost",
]

RUN_SUMMARY_FIELDS = [
    'run_id',
    'depot_layout',
    'depot_ids',
    'scenario',
    'trial_seed',
    'trial_index',
    'demand_seed',
    'arrival_seed',
    'algorithm_seed',
    'initial_soc_seed',
    'algorithm',
    'cost_mode',
    'alpha_energy',
    'beta_time',
    'soc_cost_weight',
    'energy_reference_Wh',
    'time_reference_s',
    'initial_soc_low',
    'initial_soc_high',
    'fleet_size',
    'spawned_requests',
    'completed_requests',
    'uncompleted_requests',
    'direct_depot_feasible_requests',
    'direct_depot_infeasible_requests',
    'direct_depot_feasible_uncompleted_requests',
    'direct_depot_infeasible_uncompleted_requests',
    'expired_while_waiting_requests',
    'expired_after_assignment_requests',
    'other_uncompleted_requests',
    'fulfillment_rate_percent',
    'maximum_wait_violations',
    'mean_dispatch_delay_s',
    'mean_waiting_time_s',
    'mean_response_time_s',
    'pickup_wait_limit_s',
    'pickup_wait_request_count',
    'pickup_wait_observed_count',
    'pickup_wait_deadline_censored_count',
    'pickup_wait_incomplete_count',
    'mean_deadline_limited_pickup_wait_s',
    'mean_passenger_time_s',
    'mean_dispatch_delay_assigned_s',
    'dispatch_delay_sample_count',
    'mean_post_assignment_wait_picked_up_s',
    'post_assignment_wait_sample_count',
    'mean_pickup_response_picked_up_s',
    'pickup_response_sample_count',
    'mean_in_vehicle_time_dropped_off_s',
    'in_vehicle_time_sample_count',
    'mean_assignment_cost',
    'total_distance_km',
    'total_energy_consumed_Wh',
    'total_battery_withdrawn_Wh',
    'total_grid_import_Wh',
    'driving_Wh',
    'dwell_Wh',
    'dwell_time_s',
    'battery_parked_Wh',
    'battery_Wh_per_completed_request',
    'total_energy_charged_Wh',
    'total_charging_cost',
    'minimum_fleet_SOC_Wh',
    'mean_vehicle_utilization_percent',
    'assignment_runtime_ms',
    'dispatch_count',
    'matching_round_count',
    'matching_selected_pairs_total',
    'matching_maximum_pairs_total',
    'matching_cardinality_gap_total',
    'matching_cardinality_gap_rounds',
    'matching_cardinality_gap_round_percent',
    'dispatch_runtime_mean_ms',
    'dispatch_runtime_p95_ms',
    'dispatch_runtime_p99_ms',
    'dispatch_runtime_max_ms',
    'dispatch_budget_ms',
    'dispatch_budget_miss_count',
    'dispatch_budget_miss_percent',
    'simulation_duration_s',
    'dispatch_window_s',
    'dispatch_urgent_slack_s',
]


# ── FastAPI app ───────────────────────────────────────────────────
@asynccontextmanager
async def _v2_lifespan(application):
    await on_start()
    try:
        yield
    finally:
        await on_shutdown()

app = FastAPI(lifespan=_v2_lifespan)

# Trusted browser origins. Configure this list for another frontend.
_V2_ALLOWED_ORIGINS = tuple(
    item.strip().rstrip("/")
    for item in os.environ.get(
        "V2_ALLOWED_UI_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if item.strip()
)

if not _V2_ALLOWED_ORIGINS or any(
    "*" in item or not item.startswith(("http://", "https://"))
    for item in _V2_ALLOWED_ORIGINS
):
    raise ValueError(
        "V2_ALLOWED_UI_ORIGINS must be an explicit origin allowlist."
    )

_V2_CONTROL_TOKEN = os.environ.get("V2_CONTROL_TOKEN", "")


def _control_request_allowed(host, remote_host, origin, authorization):
    """Allow trusted local control or an explicit bearer token.

    Reverse proxies must apply their own access control.
    Forwarded-IP headers are not trusted here.
    """
    if origin is not None and origin not in _V2_ALLOWED_ORIGINS:
        return False

    raw_host = (host or "").lower().strip()
    hostname = (
        raw_host[:raw_host.index("]") + 1]
        if raw_host.startswith("[") and "]" in raw_host
        else raw_host.split(":", 1)[0]
    )

    try:
        is_local = ipaddress.ip_address(
            remote_host or ""
        ).is_loopback
    except ValueError:
        is_local = False

    if is_local and hostname in {
        "localhost", "127.0.0.1", "[::1]"
    }:
        return True

    return bool(_V2_CONTROL_TOKEN) and secrets.compare_digest(
        authorization or "",
        "Bearer " + _V2_CONTROL_TOKEN,
    )


@app.middleware("http")
async def reject_untrusted_control_requests(request: Request, call_next):
    if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        client = request.client.host if request.client else ""

        if not _control_request_allowed(
            request.headers.get("host", ""),
            client,
            request.headers.get("origin"),
            request.headers.get("authorization", ""),
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "error": "Control API access denied",
                },
            )

    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_V2_ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=[
        "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"
    ],
    allow_headers=["Content-Type", "Authorization"],
)

ALPHA_ENERGY = 1.0
BETA_TIME = 1.0
# Shared API/headless default: no SOC preference unless gamma is explicit.
SOC_COST_WEIGHT = 0.0
DT_S = 0.30

# Dispatch at fixed simulation-time boundaries; 0 preserves every tick.
# Provisional dispatch batching window for diagnostic testing.
# Not yet the final benchmark configuration.
DISPATCH_WINDOW_S = 30.0

# Select before preparing a run. API: set V2_DEPOT_LAYOUT before startup.
DEPOT_LAYOUT = os.environ.get("V2_DEPOT_LAYOUT", "single")
if DEPOT_LAYOUT not in ("single", "two", "three"):
    raise ValueError(
        "V2_DEPOT_LAYOUT must be single, two, or three."
    )

# P02/P08/P16 were fixed after exploratory hotspot accessibility
# screening. They are not claimed to be independently preregistered
# or globally k-median optimal.
THREE_DEPOT_PROVENANCE = (
    "exploratory_hotspot_accessibility_screening_then_fixed_ids; "
    "not_preregistered; not_proven_k_median_optimal"
)

# Configurable two-depot research pilot.
# Default preserves the original P04/P08 policy.
# Separate three-depot pilot configuration.
# Default pair configuration remains unchanged.
THREE_DEPOT_IDS = tuple(
    item.strip().upper()
    for item in os.environ.get(
        "V2_THREE_DEPOT_IDS", "P02,P08,P16"
    ).split(",")
)

if (
    len(THREE_DEPOT_IDS) != 3
    or len(set(THREE_DEPOT_IDS)) != 3
    or any(
        len(pid) != 3
        or pid[0] != "P"
        or not pid[1:].isdigit()
        for pid in THREE_DEPOT_IDS
    )
):
    raise ValueError(
        "V2_THREE_DEPOT_IDS requires three distinct parking IDs."
    )

TWO_DEPOT_IDS = tuple(
    x.strip().upper()
    for x in os.environ.get(
        "V2_TWO_DEPOT_IDS", "P04,P08"
    ).split(",")
)
if (
    len(TWO_DEPOT_IDS) != 2
    or len(set(TWO_DEPOT_IDS)) != 2
    or any(
        len(x) != 3 or x[0] != "P" or not x[1:].isdigit()
        for x in TWO_DEPOT_IDS
    )
):
    raise ValueError(
        "V2_TWO_DEPOT_IDS must contain two distinct "
        "registered parking IDs, e.g. P13,P08."
    )
# One early dispatch per waiting request entering this deadline slack.
# Set to 0 to disable early dispatches.
DISPATCH_URGENT_SLACK_S = 60.0

# Provisional wall-clock budget for one dispatch operation (milliseconds).
# Fixed at 300 ms, including when FAST_SIMULATION is enabled.
DISPATCH_BUDGET_MS = 300.0

if not math.isfinite(DISPATCH_BUDGET_MS) or DISPATCH_BUDGET_MS <= 0.0:
    raise ValueError("DISPATCH_BUDGET_MS must be finite and positive.")

# Use False when watching the simulation on the map.
# Use True for accelerated experiment runs.
FAST_SIMULATION = True

if FAST_SIMULATION:
    SIM_SLEEP_S = 0.001
else:
    SIM_SLEEP_S = DT_S
ARRIVAL_WINDOW_S = 1800.0  # 30-minute controlled request-arrival window
MAX_WAIT_S = 8 * 60  # Maximum passenger waiting time: 480 seconds.

DEPOT_MAX_PICKUP_TIME_S = float(MAX_WAIT_S)
DEPOT_MAX_SNAP_M = 50.0
DEPOT_SELECTION_METHOD = (
    "stop_aware_direct_pickup_coverage_then_reference_deadhead"
)
DEPOT_DEMAND_MODEL = "weighted_OD_before_duplicate_pair_rejection"
# Default scenario at startup; CLI selection is applied later.
CURRENT_SCENARIO = "S1"
SEED_MOVERS = EXPERIMENT_CONFIG[CURRENT_SCENARIO]["vehicles"]
# SPEED_KPH and UGV_V_MAX use the campus operating cap; manufacturer capability is separately recorded.
SNAPSHOT_EVERY   = 50
ASSIGNMENT_METHOD = "greedy"
rr_counter = 0  # Next buggy ID to consider for Round-Robin.

ALGO_NAMES = {
    "hungarian":    "Hungarian (Optimal)",
    "greedy":       "Greedy",
    "mcmf":         "Min-Cost Flow",
    "auction":      "Auction",
    "random":       "Random Baseline",
    "roundrobin":   "Round-Robin",
    "greedy_minmax": "Bottleneck (Min-Max)",
    "annealing":    "Simulated Annealing",
}

# ── Global graph ──────────────────────────────────────────────────
G = None
vehicle_data = {}
HOTSPOT_DATA = []
INPUT_FINGERPRINTS = {}

class CanonicalRequest(NamedTuple):
    request_id: int
    pickup_node: int
    dropoff_node: int
    arrival_time_s: float
    max_wait_s: int

# OD-only realization
TRIAL_DEMAND = ()

# Unified immutable canonical trial
TRIAL_REQUESTS = ()

# Mutable queue used by the current simulation run.
# The original TRIAL_REQUESTS remains unchanged.
canonical_queue = deque()
# One algorithm-independent depot for the primary benchmark.
BENCHMARK_DEPOT = None

GRAPH_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "utp_graph.graphml",
)

# ── Graph helpers ─────────────────────────────────────────────────
def safe_edge_data(u, v):
    data_all = G.get_edge_data(u, v)
    if data_all is None:
        logger.warning(f"[Graph] Missing edge ({u}, {v})")
        return None
    return min(data_all.values(), key=lambda d: d.get("length", 0.0))


def load_graph():
    """Load the fixed local road graph; never download benchmark inputs."""
    global G

    if G is not None:
        return G

    try:
        with open(GRAPH_PATH, "rb") as file:
            graph_bytes = file.read()
        loaded_graph = ox.load_graphml(
            graphml_str=graph_bytes.decode("utf-8-sig")
        )
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Required benchmark road graph is missing: {GRAPH_PATH}. "
            "Restore the fixed utp_graph.graphml file before starting."
        ) from error

    # Preserve the benchmark's bidirectional-road assumption.
    if loaded_graph.is_directed():
        loaded_graph = ox.convert.to_undirected(loaded_graph)

    if (
        loaded_graph.number_of_nodes() < 2
        or loaded_graph.number_of_edges() == 0
    ):
        raise ValueError(
            "Saved road graph must contain at least two nodes "
            "and one road segment."
        )

    if not nx.is_connected(loaded_graph):
        raise ValueError(
            "Saved road graph is disconnected; expected a "
            "single connected road network for this benchmark."
        )

    for u, v, key, data in loaded_graph.edges(keys=True, data=True):
        length = float(data.get("length", math.nan))

        if not math.isfinite(length) or length <= 0.0:
            raise ValueError(
                f"Road segment {u} -> {v} (key {key}) has "
                f"invalid length {length!r}; expected a finite, "
                "positive length in metres."
            )

        data["length"] = length

    # Publish only after the complete graph passes validation.
    graph_fingerprint = {
        "filename": os.path.basename(GRAPH_PATH),
        "sha256": hashlib.sha256(graph_bytes).hexdigest(),
        "size_bytes": len(graph_bytes),
    }
    G = loaded_graph
    INPUT_FINGERPRINTS["road_graph"] = graph_fingerprint
    logger.info("[Graph] Loaded fixed local road graph from %s", GRAPH_PATH)
    return G

def load_hotspots():
    """Validate the hotspot CSV before replacing the loaded data."""
    from decimal import Decimal, InvalidOperation

    global HOTSPOT_DATA

    if G is None:
        raise RuntimeError("Load the road graph before loading hotspots.")

    hotspot_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "hotspot_locations.csv",
    )
    records = []
    active_nodes = set()
    active_weights = []

    try:
        with open(hotspot_path, "rb") as file:
            hotspot_bytes = file.read()
        with io.StringIO(
            hotspot_bytes.decode("utf-8-sig"), newline=""
        ) as file:
            reader = csv.reader(file, strict=True)
            columns = next(reader, None)
            if not columns:
                raise ValueError("The hotspot CSV is empty.")

            columns = [column.strip() for column in columns]
            if any(not column for column in columns):
                raise ValueError("Hotspot CSV column names cannot be blank.")
            if len(columns) != len(set(columns)):
                raise ValueError("Hotspot CSV column names must be unique.")

            missing = {"node", "demand_weight"} - set(columns)
            if missing:
                raise ValueError(
                    f"Hotspot CSV is missing columns: {', '.join(sorted(missing))}."
                )

            for values in reader:
                if not values:
                    continue
                line = reader.line_num
                if len(values) != len(columns):
                    raise ValueError(
                        f"Hotspot CSV line {line} has {len(values)} fields; "
                        f"expected {len(columns)}."
                    )

                record = dict(zip(columns, values))

                # Read IDs exactly; never truncate or round a road-node ID.
                try:
                    node_value = Decimal(record["node"].strip())
                except InvalidOperation as error:
                    raise ValueError(
                        f"Hotspot CSV line {line}: node must be an integer ID."
                    ) from error

                if (
                    not node_value.is_finite()
                    or node_value != node_value.to_integral_value()
                ):
                    raise ValueError(
                        f"Hotspot CSV line {line}: node must be a finite integer ID."
                    )

                node = int(node_value)
                if node not in G:
                    raise ValueError(
                        f"Hotspot CSV line {line}: node {node} "
                        "does not exist in the loaded road graph."
                    )

                # Blank weights are unverified; zero weights are inactive.
                raw_weight = record["demand_weight"].strip()
                weight = None
                if raw_weight:
                    try:
                        weight = float(raw_weight)
                    except ValueError as error:
                        raise ValueError(
                            f"Hotspot CSV line {line}: demand_weight "
                            "must be a number or blank."
                        ) from error
                    if not math.isfinite(weight) or weight < 0.0:
                        raise ValueError(
                            f"Hotspot CSV line {line}: demand_weight "
                            "must be finite and non-negative."
                        )

                record["node"] = node
                record["demand_weight"] = weight
                records.append(record)
                if weight is not None and weight > 0.0:
                    active_nodes.add(node)
                    active_weights.append(weight)

        if len(active_nodes) < 2:
            raise ValueError(
                "At least two distinct road nodes must have positive "
                "demand weights. Blank and zero weights are inactive."
            )
        if not math.isfinite(sum(active_weights)):
            raise ValueError(
                "The total demand weight is too large. Reduce all positive "
                "weights by the same factor, preserving their proportions."
            )

    except Exception:
        logger.exception("[Hotspot] Failed loading hotspots from %s", hotspot_path)
        raise

    hotspot_fingerprint = {
        "filename": os.path.basename(hotspot_path),
        "sha256": hashlib.sha256(hotspot_bytes).hexdigest(),
        "size_bytes": len(hotspot_bytes),
    }
    HOTSPOT_DATA = records
    INPUT_FINGERPRINTS["hotspots"] = hotspot_fingerprint
    logger.info(
        "[Hotspot] Loaded %s rows with %s distinct active road nodes.",
        len(records),
        len(active_nodes),
    )
    return HOTSPOT_DATA

# ==============================
# Permanent Parking Registry
# ==============================
PARKING_REGISTRY = [
    {"parking_id": "P01", "lat": 4.385419660283187, "lon": 100.97480170397027},
    {"parking_id": "P02", "lat": 4.386626604129822, "lon": 100.97494811562197},
    {"parking_id": "P03", "lat": 4.386745747009279, "lon": 100.97210452631391},
    {"parking_id": "P04", "lat": 4.3869677390018165, "lon": 100.97101366944887},
    {"parking_id": "P05", "lat": 4.38701913466872, "lon": 100.96909660654157},
    {"parking_id": "P06", "lat": 4.388060838039719, "lon": 100.96691197510117},
    {"parking_id": "P07", "lat": 4.387962767326511, "lon": 100.96377506158386},
    {"parking_id": "P08", "lat": 4.385750239406761, "lon": 100.96399986142039},
    {"parking_id": "P09", "lat": 4.383767560397038, "lon": 100.96241587428328},
    {"parking_id": "P10", "lat": 4.379602357715967, "lon": 100.96072170841705},
    {"parking_id": "P11", "lat": 4.380574574341162, "lon": 100.96892360733796},
    {"parking_id": "P12", "lat": 4.383431429853988, "lon": 100.96899503598554},
    {"parking_id": "P13", "lat": 4.383290107245586, "lon": 100.9696867867982},
    {"parking_id": "P14", "lat": 4.38422390894735, "lon": 100.96777712543035},
    {"parking_id": "P15", "lat": 4.383622132041206, "lon": 100.96837631306931},
    {"parking_id": "P16", "lat": 4.383162448163241, "lon": 100.96838169397353},
    {"parking_id": "P17", "lat": 4.383959140612495, "lon": 100.96975916548959},
    {"parking_id": "P18", "lat": 4.384967056164759, "lon": 100.96955236426712},
    {"parking_id": "P19", "lat": 4.384134540873872, "lon": 100.97057901542145},
    {"parking_id": "P20", "lat": 4.384564656056687, "lon": 100.96988614757065},
    {"parking_id": "P21", "lat": 4.385215818954718, "lon": 100.97984079785604},
]


# Defined before startup; updated by initialise_benchmark().
parking_slots = []


def get_registered_parking_coordinates():
    return [
        [parking["lat"], parking["lon"]]
        for parking in PARKING_REGISTRY
    ]

def get_parking_record_by_coordinates(
    parking_coordinates,
    tolerance=1e-9,
):
    """
    Return the permanent parking record matching
    the supplied [latitude, longitude].
    """

    if not parking_coordinates:
        return None

    if len(parking_coordinates) != 2:
        return None

    latitude, longitude = parking_coordinates

    for parking in PARKING_REGISTRY:
        latitude_matches = math.isclose(
            latitude,
            parking["lat"],
            abs_tol=tolerance,
        )

        longitude_matches = math.isclose(
            longitude,
            parking["lon"],
            abs_tol=tolerance,
        )

        if latitude_matches and longitude_matches:
            return parking

    return None






from copy import deepcopy

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))




def nearest_node(lon, lat):
    return ox.distance.nearest_nodes(G, lon, lat)


# ── Run state helpers ─────────────────────────────────────────────
def _make_run_id() -> str:
    return str(time.time_ns())

def simulation_time_s() -> float:
    return round(tick * DT_S, 6)
def pickup_deadline_exceeded(record, now_s: float) -> bool:
    if record.get("pickup_time_s") is not None:
        return False

    created_s = float(record["created_time_s"])
    limit_s = float(record["_max_wait_s"])
    now_s = float(now_s)

    if (
        not all(
            math.isfinite(value)
            for value in (created_s, limit_s, now_s)
        )
        or limit_s <= 0.0
    ):
        raise ValueError(
            "Request times must be finite and max_wait_s "
            "must be positive."
        )

    return round(now_s - created_s, 6) > limit_s


def expire_waiting_requests() -> int:
    now_s = simulation_time_s()
    expired = 0

    for request_id in list(requests_waiting):
        record = RUN["request_records"].get(request_id)

        if record is None:
            raise RuntimeError(
                f"Missing request record: {request_id}"
            )

        if not pickup_deadline_exceeded(record, now_s):
            continue

        record.update(
            request_completed=0,
            maximum_wait_violation=1,
            completion_status="uncompleted",
            failure_reason="pickup_deadline_exceeded",
        )

        engine = _SHARED_RIDE_ENGINE
        if engine is not None and engine.run_id == RUN["run_id"]:
            engine._close_wait_diagnostics(request_id, "expired")

        requests_waiting.pop(request_id)
        expired += 1

        logger.info(
            "[Deadline] Request %s expired before assignment "
            "at %.6f s.",
            request_id,
            now_s,
        )

    return expired



def release_due_canonical_requests() -> int:
    """Release requests whose scheduled arrival time has been reached."""

    global spawned_requests_total

    current_time_s = simulation_time_s()
    released = 0

    while (
        canonical_queue
        and canonical_queue[0].created_time_s
        <= current_time_s + 1e-9
    ):
        request = canonical_queue.popleft()

        requests_waiting[request.id] = request
        register_request_record(request)

        spawned_requests_total += 1
        released += 1

        logger.info(
            f"[CanonicalDemand] Released request={request.id} "
            f"arrival_time_s={request.created_time_s:.6f} "
            f"simulation_time_s={current_time_s:.6f}"
        )

    if released:
        RUN["workload_released"] += released

    return released

VELOCITY_LOG_FIELDS = [
    "tick",
    "time_s",
    "mover_id",
    "velocity_kmh",
    "acceleration_ms2",
    "sampled_tick_energy_Wh",
    "status",
]


FLEET_LOG_FIELDS = [
    "trial_seed",
    "tick",
    "event_type",
    "algo",
    "mover_id",
    "request_id",
    "total_cost",
    "runtime_ms",
    "fulfilled_requests",
    "pending_requests",
    "total_travel_time_s",
    "total_energy_consumed_Wh",
    "total_idle_time_s",
    "fleet_util_pct",
]


VEHICLE_MATRIX_FIELDS = [
    "Vehicle",
    "Assigned Requests",
    "Completed Requests",
    "Total Distance Covered per Request (km)",
]

def build_run_settings(scenario, algorithm, trial_index, *, seed=None):
    """Capture settings for the pending run before active state is changed."""
    missing_inputs = {"road_graph", "hotspots"} - INPUT_FINGERPRINTS.keys()
    if missing_inputs:
        raise RuntimeError(
            "Load and validate benchmark inputs before preparing a run: "
            + ", ".join(sorted(missing_inputs))
        )
    # F-023: Capture the Python environment before preparing run files.
    ensure_package_snapshot()

    selected_trial_seed = TRIAL_SEED if seed is None else seed

    seeds = {
        "TRIAL_SEED": selected_trial_seed,
        "DEMAND_SEED": derive_trial_seed(
            selected_trial_seed, "demand"
        ),
        "ARRIVAL_SEED": derive_trial_seed(
            selected_trial_seed, "arrival"
        ),
        "ALGORITHM_SEED": derive_trial_seed(
            selected_trial_seed, "algorithm"
        ),
        "INITIAL_SOC_SEED": derive_trial_seed(
            selected_trial_seed, "initial_soc"
        ),
    }

    setting_names = {
        "ALPHA_ENERGY", "BETA_TIME", "SOC_COST_WEIGHT", "DT_S", "MAX_WAIT_S",
        "ARRIVAL_WINDOW_S", "BATTERY_CAPACITY_WH",
        "BATTERY_NOMINAL_V", "BATTERY_CAPACITY_AH",
        "ENERGY_PARAMETER_NOTES",
        "BATTERY_RESERVE_FRACTION",
        "CHARGING_THRESHOLD_WH",
        "CHARGER_DC_POWER_W", "CHARGER_EFFICIENCY",
        "BATTERY_CHARGE_EFFICIENCY",
        "CHARGE_TAPER_START_SOC", "CHARGE_TAPER_END_FRACTION",
        "PLUGGED_POWER_POLICY", "PLUGGED_SLEEP_POWER_W",
        "CHARGING_LOG_SCHEMA_VERSION", "ENERGY_CONSUMPTION_BASIS",
        "ELECTRICITY_PRICE_MYR_PER_KWH",
        "FAST_SIMULATION", "SIM_SLEEP_S", "DISPATCH_BUDGET_MS",
        "SNAPSHOT_EVERY", "SPEED_KPH", "SPEED_POLICY_VERSION",
            "MANUFACTURER_LOW_SPEED_MODE_KPH",
            "CAMPUS_OPERATING_SPEED_CAP_KPH",
            "CAMPUS_TARGET_AVERAGE_MOVING_SPEED_KPH",
        "LEG_CACHE_SIZE",

        "DEPOT_MAX_PICKUP_TIME_S",
        "DEPOT_MAX_SNAP_M",
        "DEPOT_SELECTION_METHOD",
        "DEPOT_DEMAND_MODEL",
        "SOC_COST_WEIGHT",
        "ASSIGNMENT_COST_MODE",
        "COST_ENERGY_REFERENCE_WH",
        "COST_TIME_REFERENCE_S",
        "BOARDING_DWELL_S",
        "ALIGHTING_DWELL_S",
        "DWELL_AUX_POWER_W",
        "MAX_RIDE_FACTOR",
        "MAX_EXTRA_RIDE_S",
    }
    setting_names.update({
        "DISPATCH_WINDOW_S",
        "DISPATCH_URGENT_SLACK_S",
    })

    parameters = {
        name: value
        for name, value in sorted(globals().items())
        if name.startswith("UGV_") or name in setting_names
    }

    package_versions = {}
    for name in (
        "numpy", "scipy", "networkx", "osmnx", "geopandas",
        "pandas", "shapely", "pyproj", "fastapi", "pydantic",
        "uvicorn", "aiofiles", "httpx",
    ):
        try:
            package_versions[name] = package_metadata.version(name)
        except package_metadata.PackageNotFoundError:
            package_versions[name] = None

    return {
        "schema_version": 2,
        "shared_ride_model": get_pooling_model_info(),
        "speed_policy": {
            "policy_version": SPEED_POLICY_VERSION,
            "maximum_speed_kph": float(SPEED_KPH),
            "manufacturer_low_speed_mode_kph": float(MANUFACTURER_LOW_SPEED_MODE_KPH),
            "campus_operating_speed_cap_kph": float(CAMPUS_OPERATING_SPEED_CAP_KPH),
            "campus_target_average_moving_speed_kph": float(CAMPUS_TARGET_AVERAGE_MOVING_SPEED_KPH),
            "maximum_speed_m_s": float(UGV_V_MAX),
            "speed_is_upper_bound_not_constant": True,
            "motion_model": "stop_aware_acceleration_braking_torque_power_limited",
            "depot_pickup_coverage_uses_current_speed_cap": True,
            "basis": "declared_benchmark_assumption_hardware_unverified",
        },
        "scenario": scenario,
        "scenario_config": dict(EXPERIMENT_CONFIG[scenario]),
        "algorithm": algorithm,
        "trial_index": trial_index,
        "configured_seeds": seeds,
        "seed_derivation": {
            "version": SEED_DERIVATION_VERSION,
            "method": (
                "NumPy SeedSequence([version, trial_seed, stream_id]); "
                "four uint32 words combined into one Python integer"
            ),
            "seed_bits": 128,
            "stream_ids": {
                name: SEED_STREAM_IDS[name]
                for name in (
                    "demand",
                    "arrival",
                    "algorithm",
                    "initial_soc",
                )
            },
            "scenario_policy": "shared streams across scenarios",
            "algorithm_policy": "shared stream seeds across algorithms",
        },
        "parameters": parameters,
        "benchmark_depot": deepcopy(BENCHMARK_DEPOT),
        "depot_layout": DEPOT_LAYOUT,
        "depot_snap_policy": {
            "maximum_snap_m": float(DEPOT_MAX_SNAP_M),
            "rule": "exclude_candidates_exceeding_maximum",
        },
        "three_depot_selection": {
            "parking_ids": list(THREE_DEPOT_IDS),
            "method": "experimenter_fixed_after_exploratory_screening",
            "provenance": THREE_DEPOT_PROVENANCE,
            "claim_of_global_optimality": False,
            "frozen_across_algorithms_scenarios_seeds": True,
        },
        "output_schema": {
            "request_results_version": 3,
            "undefined_request_metrics_omitted": ['pickup_distance_km', 'return_distance_km', 'total_distance_km', 'predicted_energy_Wh', 'predicted_return_energy_Wh', 'actual_energy_Wh', 'actual_return_energy_Wh', 'actual_total_energy_Wh', 'energy_prediction_error_percent'],
            "explanation": (
                "Shared-route energy and empty/return distances "
                "are not attributable to individual passengers "
                "without a declared allocation rule."
            ),
        },
        "depot_allocation_policy": (
            f"all vehicles at reference {BENCHMARK_DEPOT['parking_id']}"
            if DEPOT_LAYOUT == "single"
            else (
                (
                    f"{TWO_DEPOT_IDS[0]}/{TWO_DEPOT_IDS[1]}; "
                    f"{TWO_DEPOT_IDS[0]} count="
                    "round(fleet_size*5/12), bounded"
                )
                if DEPOT_LAYOUT == "two"
                else (
                    f"{'/'.join(THREE_DEPOT_IDS)}; "
                    "balanced integer allocation; "
                    "first depots receive remainder"
                )
            )
        ),
        "vehicle_home_depots": [
            {
                "vehicle_id": vehicle_id,
                "parking_id": str(row["parking_id"]),
                "node": int(row["node"]),
            }
            for vehicle_id, row in enumerate(
                get_depot_allocation(
                    EXPERIMENT_CONFIG[scenario]["vehicles"]
                ), 1
            )
        ],
        "depot_selection_method": (
            BENCHMARK_DEPOT["method"] if DEPOT_LAYOUT == "single"
            else "fixed_registered_parking_ids_manual"
        ),
        "depot_demand_model": (
            BENCHMARK_DEPOT["demand_model"] if DEPOT_LAYOUT == "single"
            else "not_used_for_fixed_multi_depot_selection"
        ),
        "reference_single_depot_selection_method": BENCHMARK_DEPOT["method"],
        "reference_single_depot_demand_model": BENCHMARK_DEPOT["demand_model"],
        "depot_selection_is_experimenter_specified": DEPOT_LAYOUT != "single",
        "input_files": deepcopy(INPUT_FINGERPRINTS),
        "backend_source": deepcopy(BACKEND_SOURCE_INFO),
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.platform(),
            "machine_architecture": platform.machine(),
            "processor": platform.processor() or None,
            "logical_cpu_count": os.cpu_count(),
            "package_versions": package_versions,
            "python_executable": sys.executable,
            "package_snapshot": deepcopy(PACKAGE_SNAPSHOT_INFO),
        },
    }

def prepare_run_output_files(
    run_id, prepared_fleet, prepared_trial, *, run_settings
):
    """Create run outputs and preserve the complete planned request workload."""
    if not prepared_trial:
        raise ValueError("Cannot prepare run files without a canonical workload.")

    results_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        run_id,
    )
    file_specs = (
        ("csv_path", "vehicle_matrix.csv", VEHICLE_MATRIX_FIELDS),
        ("request_csv_path", "request_results.csv", REQUEST_RESULT_FIELDS),
        ("vehicle_csv_path", "vehicle_results.csv", VEHICLE_RESULT_FIELDS),
        ("assignment_csv_path", "assignment_events.csv", ASSIGNMENT_EVENT_FIELDS),
        ("charging_csv_path", "charging_events.csv", CHARGING_EVENT_FIELDS),
        ("summary_csv_path", "run_summary.csv", RUN_SUMMARY_FIELDS),
        ("fleet_log_csv_path", "fleet_log.csv", FLEET_LOG_FIELDS),
        ("velocity_log_csv_path", "velocity_log.csv", VELOCITY_LOG_FIELDS),
        (
            "canonical_requests_csv_path",
            "canonical_requests.csv",
            CanonicalRequest._fields,
        ),
    )
    prepared_paths = {
        key: os.path.join(results_dir, filename)
        for key, filename, _ in file_specs
    }
    prepared_paths["canonical_requests_sha256_path"] = os.path.join(
        results_dir, "canonical_requests.sha256"
    )
    prepared_paths["settings_json_path"] = os.path.join(
        results_dir, "run_settings.json"
    )

    # Existing run directories must never be overwritten or removed here.
    os.makedirs(results_dir, exist_ok=False)

    try:
        for key, _, fields in file_specs:
            with open(
                prepared_paths[key],
                "x",
                newline="",
                encoding="utf-8",
            ) as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()

                if key == "csv_path":
                    for vehicle_id in sorted(prepared_fleet):
                        writer.writerow({"Vehicle": vehicle_label(vehicle_id)})

                elif key == "canonical_requests_csv_path":
                    writer.writerows(
                        request._asdict() for request in prepared_trial
                    )

        with open(
            prepared_paths["canonical_requests_csv_path"], "rb"
        ) as file:
            workload_sha256 = hashlib.sha256(file.read()).hexdigest()

        with open(
            prepared_paths["canonical_requests_sha256_path"],
            "x",
            encoding="ascii",
            newline="\n",
        ) as file:
            file.write(f"{workload_sha256}  canonical_requests.csv\n")

        source_snapshot_path = os.path.join(
            results_dir,
            BACKEND_SOURCE_INFO["snapshot_filename"],
        )
        with open(source_snapshot_path, "xb") as file:
            file.write(BACKEND_SOURCE_BYTES)

        package_snapshot_path = os.path.join(
            results_dir,
            PACKAGE_SNAPSHOT_INFO["filename"],
        )
        with open(package_snapshot_path, "xb") as file:
            file.write(PACKAGE_SNAPSHOT_BYTES)

        settings = dict(run_settings)
        settings["run_id"] = run_id
        settings["canonical_workload"] = {
            "filename": "canonical_requests.csv",
            "sha256": workload_sha256,
            "request_count": len(prepared_trial),
        }
        settings["initial_fleet"] = [
            {
                "vehicle_id": mover.id,
                "node": mover.node,
                "soc_Wh": float(mover.soc_Wh),
                "home_depot_id": mover.home_depot_id,
                "home_depot_node": mover.home_depot_node,
            }
            for _, mover in sorted(prepared_fleet.items())
        ]

        with open(
            prepared_paths["settings_json_path"],
            "x",
            encoding="utf-8",
            newline="\n",
        ) as file:
            json.dump(
                settings, file, indent=2, sort_keys=True, allow_nan=False
            )
            file.write("\n")

    except Exception:
        try:
            shutil.rmtree(results_dir)
        except OSError:
            logger.exception(
                "[Reset] Could not remove incomplete output directory: %s",
                results_dir,
            )
        raise

    return prepared_paths


_CHARGING_SESSIONS = {}
_CHARGING_ROWS = []


def parked_energy_step(soc_Wh, duration_s, plugged):
    """Return battery and grid flows for one parked interval."""
    capacity = float(BATTERY_CAPACITY_WH)
    soc = float(soc_Wh)
    dt = float(duration_s)
    power = float(CHARGER_DC_POWER_W)
    eta_grid = float(CHARGER_EFFICIENCY)
    eta_store = float(BATTERY_CHARGE_EFFICIENCY)
    knee = float(CHARGE_TAPER_START_SOC)
    tail = float(CHARGE_TAPER_END_FRACTION)
    load = float(UGV_IDLE_POWER_W)

    if PLUGGED_POWER_POLICY not in {"stack_on", "sleep"}:
        raise ValueError(
            "PLUGGED_POWER_POLICY must be stack_on or sleep."
        )

    if plugged and PLUGGED_POWER_POLICY == "sleep":
        load = float(PLUGGED_SLEEP_POWER_W)

    values = (
        capacity, soc, dt, power, eta_grid,
        eta_store, knee, tail, load,
    )

    if not all(math.isfinite(v) for v in values):
        raise ValueError("Parked-energy parameters must be finite.")

    if (
        capacity <= 0
        or not 0 <= soc <= capacity
        or dt < 0
        or load < 0
    ):
        raise ValueError(
            "Invalid parked SOC, duration, capacity or load."
        )

    if (
        power <= 0
        or not 0 < eta_grid <= 1
        or not 0 < eta_store <= 1
    ):
        raise ValueError("Invalid charger power or efficiency.")

    if not 0 <= knee < 1 or not 0 < tail <= 1:
        raise ValueError("Invalid taper parameters.")

    # The charger supplies the parked load first.
    # The battery covers any shortfall.
    supplied_load_W = min(load, power) if plugged else 0.0
    withdrawn = (load - supplied_load_W) * dt / 3600.0
    stored = 0.0
    charge_time = 0.0

    if plugged and soc < capacity and power > load and dt > 0.0:
        # Exact solution for the assumed linear SOC taper, not measured BMS behavior.
        base_Wh_s = (power - load) * eta_store / 3600.0
        knee_Wh = capacity * knee
        next_soc = soc
        remaining_s = dt

        # Constant-power charging below 80% SOC.
        if next_soc < knee_Wh:
            t = min(remaining_s, (knee_Wh - next_soc) / base_Wh_s)
            next_soc += base_Wh_s * t
            charge_time += t
            remaining_s -= t

        # Integrate linear charging-power taper with respect to SOC.
        if remaining_s > 0.0 and next_soc < capacity:
            if tail == 1.0:
                t = min(remaining_s, (capacity - next_soc) / base_Wh_s)
                next_soc += base_Wh_s * t
                charge_time += t
            else:
                slope = (1.0 - tail) / (capacity - knee_Wh)
                start_factor = max(tail, 1.0 - slope * (next_soc - knee_Wh))
                time_to_full = math.log(start_factor / tail) / (slope * base_Wh_s)
                t = min(remaining_s, time_to_full)
                if t >= time_to_full:
                    next_soc = capacity
                else:
                    next_soc += -start_factor * math.expm1(-slope * base_Wh_s * t) / slope
                charge_time += t

        stored = min(capacity - soc, max(0.0, next_soc - soc))

    after = min(capacity, soc + stored - withdrawn)

    if after < 0:
        raise RuntimeError(
            "Insufficient battery energy for the parked load."
        )

    grid_Wh = (
        supplied_load_W * dt / 3600.0
        + stored / eta_store
    ) / eta_grid

    return {
        "soc_after_Wh": after,
        "stored_Wh": stored,
        "withdrawn_Wh": withdrawn,
        "parked_load_Wh": load * dt / 3600.0,
        "grid_Wh": grid_Wh,
        "charging_time_s": charge_time,
    }


def close_charging_session(vehicle_id, reason):
    """Close in memory; defer file I/O until the run stops."""
    session = _CHARGING_SESSIONS.pop(vehicle_id, None)

    if session is None:
        return

    session["end_reason"] = reason
    session["session_type"] = (
        "charging"
        if session["energy_charged_Wh"] > 0.0
        else "battery_support"
        if session["battery_withdrawn_Wh"] > 0.0
        else "float_only"
    )

    _CHARGING_ROWS.append(session)


def finish_charging_sessions(reason):
    """Close open sessions and flush completed rows once."""
    for vehicle_id in sorted(_CHARGING_SESSIONS):
        close_charging_session(vehicle_id, reason)

    if not _CHARGING_ROWS:
        return

    path = RUN.get("charging_csv_path")

    if not path:
        raise RuntimeError(
            "charging_csv_path has not been configured."
        )

    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=CHARGING_EVENT_FIELDS,
        )
        writer.writerows(_CHARGING_ROWS)

    _CHARGING_ROWS.clear()


def advance_parked_vehicle(m, duration_s, plugged, start_time_s):
    """Update SOC, session totals and per-vehicle metrics."""
    global total_idle_time
    global total_energy_consumed
    global total_charging_cost

    if duration_s <= 0.0:
        return

    price = float(ELECTRICITY_PRICE_MYR_PER_KWH)

    if not math.isfinite(price) or price < 0:
        raise ValueError(
            "Electricity price must be finite and non-negative."
        )

    if plugged and int(m.node) != vehicle_home_node(m):
        raise RuntimeError(
            f"Vehicle {m.id}: charging attempted away from home."
        )

    before = float(m.soc_Wh)
    flow = parked_energy_step(before, duration_s, plugged)
    cost = flow["grid_Wh"] / 1000.0 * price

    if plugged:
        session = _CHARGING_SESSIONS.get(m.id)

        if session is None:
            RUN["charging_event_counter"] += 1

            session = {
                "schema_version": CHARGING_LOG_SCHEMA_VERSION,
                "run_id": RUN["run_id"],
                "scenario": CURRENT_SCENARIO,
                "trial_seed": RUN["trial_seed"],
                "trial_index": RUN.get("trial_index", 0),
                "algorithm": RUN["algo"],
                "session_id": RUN["charging_event_counter"],
                "vehicle_id": m.id,
                "start_time_s": float(start_time_s),
                "end_time_s": float(start_time_s),
                "plugged_duration_s": 0.0,
                "charging_duration_s": 0.0,
                "vehicle_SOC_before_Wh": before,
                "vehicle_SOC_after_Wh": before,
                "energy_charged_Wh": 0.0,
                "battery_withdrawn_Wh": 0.0,
                "parked_load_energy_Wh": 0.0,
                "grid_energy_kWh": 0.0,
                "charging_cost": 0.0,
            }

            _CHARGING_SESSIONS[m.id] = session

        session["end_time_s"] = float(start_time_s + duration_s)
        session["plugged_duration_s"] += duration_s
        session["charging_duration_s"] += flow["charging_time_s"]
        session["vehicle_SOC_after_Wh"] = flow["soc_after_Wh"]
        session["energy_charged_Wh"] += flow["stored_Wh"]
        session["battery_withdrawn_Wh"] += flow["withdrawn_Wh"]
        session["parked_load_energy_Wh"] += flow["parked_load_Wh"]
        session["grid_energy_kWh"] += flow["grid_Wh"] / 1000.0
        session["charging_cost"] += cost

    else:
        close_charging_session(m.id, "unplugged")

    m.soc_Wh = flow["soc_after_Wh"]
    mover_velocity[m.id] = 0.0
    m.speed_kph = 0.0

    total_idle_time += duration_s
    total_energy_consumed += flow["withdrawn_Wh"]
    total_charging_cost += cost

    # F-020: Record electrical energy imported from the grid.
    # This is separate from energy withdrawn from the battery.
    RUN["grid_import_Wh"] += flow["grid_Wh"]


    record = RUN["vehicle_records"].get(m.id)

    if record is not None:
        record["idle_time_s"] += duration_s
        record["energy_consumed_Wh"] += flow["withdrawn_Wh"]
        record["charging_time_s"] += flow["charging_time_s"]
        record["energy_charged_Wh"] += flow["stored_Wh"]
        record["minimum_SOC_Wh"] = min(
            record["minimum_SOC_Wh"],
            before,
            flow["soc_after_Wh"],
        )



def summarize_dispatch_timings():
    """Summarize per-dispatch wall-clock latency using linear percentiles."""
    samples = np.asarray(dispatch_runtime_ms, dtype=float)

    if (
        samples.ndim != 1
        or not np.all(np.isfinite(samples))
        or np.any(samples < 0.0)
    ):
        raise ValueError("Dispatch timings must be finite and non-negative.")

    count = int(samples.size)
    misses = int(np.count_nonzero(samples > DISPATCH_BUDGET_MS))

    p95_ms, p99_ms = (
        np.percentile(samples, [95, 99], method="linear")
        if count else (None, None)
    )

    return {
        "dispatch_count": count,
        "dispatch_runtime_mean_ms": (
            round(float(np.mean(samples)), 6) if count else None
        ),
        "dispatch_runtime_p95_ms": (
            round(float(p95_ms), 6) if count else None
        ),
        "dispatch_runtime_p99_ms": (
            round(float(p99_ms), 6) if count else None
        ),
        "dispatch_runtime_max_ms": (
            round(float(np.max(samples)), 6) if count else None
        ),
        "dispatch_budget_ms": float(DISPATCH_BUDGET_MS),
        "dispatch_budget_miss_count": misses,
        "dispatch_budget_miss_percent": (
            round(100.0 * misses / count, 6) if count else None
        ),
    }

def summarize_deadline_limited_pickup_waits(
    records, now_s, limit_s
):
    """Summarize creation-to-pickup waits with a fixed deadline cap."""
    limit_s = float(limit_s)
    now_s = float(now_s)

    if not math.isfinite(limit_s) or limit_s <= 0.0:
        raise ValueError("Pickup-wait limit must be finite and positive.")
    if not math.isfinite(now_s):
        raise ValueError("Simulation time must be finite.")

    waits = []
    observed = 0
    censored = 0
    incomplete = 0

    for record in records:
        created = float(record["created_time_s"])
        pickup = record.get("pickup_time_s")

        if not math.isfinite(created):
            raise ValueError("Request creation time must be finite.")

        if pickup is not None:
            pickup = float(pickup)
            elapsed = pickup - created

            if (
                not math.isfinite(pickup)
                or elapsed < -1e-6
                or pickup > now_s + 1e-6
            ):
                raise ValueError("Invalid pickup timestamp.")

            waits.append(min(limit_s, max(0.0, elapsed)))
            observed += 1

        elif now_s - created >= limit_s - 1e-6:
            waits.append(limit_s)
            censored += 1

        else:
            # The deadline has not been observed yet.
            incomplete += 1

    mean_wait = None
    if waits and incomplete == 0:
        mean_wait = round(math.fsum(waits) / len(waits), 6)

    return {
        "pickup_wait_limit_s": limit_s,
        "pickup_wait_request_count": observed + censored + incomplete,
        "pickup_wait_observed_count": observed,
        "pickup_wait_deadline_censored_count": censored,
        "pickup_wait_incomplete_count": incomplete,
        "mean_deadline_limited_pickup_wait_s": mean_wait,
    }
def summarize_uncompleted_requests(records):
    """Count terminal failures by assignment/pickup stage."""
    counts = {
        "expired_while_waiting_requests": 0,
        "expired_after_assignment_requests": 0,
        "other_uncompleted_requests": 0,
    }
    pickup_expiry_reasons = {
        "pickup_deadline_exceeded",
        "pickup_expired",
    }

    for record in records:
        if int(record.get("request_completed", 0)) == 1:
            continue

        pickup_expired = (
            record.get("failure_reason") in pickup_expiry_reasons
            and record.get("pickup_time_s") is None
        )

        if pickup_expired:
            if record.get("assigned_time_s") is None:
                counts["expired_while_waiting_requests"] += 1
            else:
                counts["expired_after_assignment_requests"] += 1
        else:
            counts["other_uncompleted_requests"] += 1

    return counts


def summarize_direct_depot_accessibility(trial_requests, request_records):
    """
    Classify requests using the direct depot-to-pickup journey.

    This is a reference accessibility diagnostic.
    It does not determine whether an already-positioned
    or shared-ride vehicle could serve the request.
    """
    if G is None or BENCHMARK_DEPOT is None:
        raise RuntimeError(
            "Graph and benchmark depot must be initialized."
        )

    active_nodes = sorted({
        int(row["node"])
        for row in get_depot_allocation(
            EXPERIMENT_CONFIG[CURRENT_SCENARIO]["vehicles"]
        )
    })

    candidate_paths = [
        nx.single_source_dijkstra(
            G, node, weight="length"
        )[1]
        for node in active_nodes
    ]

    pickup_time_cache = {}
    feasible_ids = set()
    infeasible_ids = set()

    for request in trial_requests:
        pickup_node = int(request.pickup_node)

        if pickup_node not in pickup_time_cache:
            options = [
                estimate_direct_pickup_completion_s(
                    paths[pickup_node]
                )
                for paths in candidate_paths
                if pickup_node in paths
            ]

            if not options:
                raise RuntimeError(
                    f"No active depot route to pickup {pickup_node}."
                )

            pickup_time_cache[pickup_node] = min(options)

        pickup_time_s = pickup_time_cache[pickup_node]

        if pickup_time_s <= request.max_wait_s:
            feasible_ids.add(request.request_id)
        else:
            infeasible_ids.add(request.request_id)

    if (feasible_ids | infeasible_ids) != set(request_records):
        raise RuntimeError(
            "Direct-depot classification does not match request records."
        )

    missed_ids = {
        request_id
        for request_id, record in request_records.items()
        if int(record.get("request_completed", 0)) != 1
    }

    return {
        "direct_depot_feasible_requests": len(feasible_ids),

        "direct_depot_infeasible_requests": len(infeasible_ids),

        "direct_depot_feasible_uncompleted_requests": len(
            feasible_ids & missed_ids
        ),

        "direct_depot_infeasible_uncompleted_requests": len(
            infeasible_ids & missed_ids
        ),
    }

def write_run_summary_if_complete() -> None:
    if RUN["summary_written"]:
        return

    if (
        RUN.get("canonical_mode")
        and (
            canonical_queue
            or RUN.get("workload_released", 0)
            < RUN.get("workload_size", 0)
        )
    ):
        return

    request_records = list(RUN["request_records"].values())
    if not request_records:
        return

    if any(not record.get("_written") for record in request_records):
        if not all_requests_and_vehicles_settled():
            return
        finalise_uncompleted_request_records()
        if any(not record.get("_written") for record in request_records):
            return

    path = RUN.get("summary_csv_path")
    if not path:
        raise RuntimeError("summary_csv_path has not been configured")

    vehicle_records = list(RUN["vehicle_records"].values())

    def mean_or_none(values):
        clean_values = [
            float(value)
            for value in values
            if value is not None
        ]
        if not clean_values:
            return None
        return round(sum(clean_values) / len(clean_values), 6)

    spawned_requests = len(request_records)
    completed_requests = sum(
        int(record.get("request_completed", 0))
        for record in request_records
    )
    uncompleted_requests = spawned_requests - completed_requests
    fulfillment_rate_percent = round(
        100.0 * completed_requests / spawned_requests, 6
    )
    maximum_wait_violations = sum(
        int(record.get("maximum_wait_violation", 0))
        for record in request_records
    )

    total_distance_km = round(
        sum(
            float(record.get("total_distance_km", 0.0))
            for record in vehicle_records
        ),
        6,
    )
    total_energy_charged_Wh = round(
        sum(
            float(record.get("energy_charged_Wh", 0.0))
            for record in vehicle_records
        ),
        6,
    )
    total_energy_consumed_Wh = round(
        sum(
            float(record.get("energy_consumed_Wh", 0.0))
            for record in vehicle_records
        ),
        6,
    )
    minimum_soc_values = [
        float(record.get("minimum_SOC_Wh", 0.0))
        for record in vehicle_records
    ]
    minimum_fleet_SOC_Wh = (
        round(min(minimum_soc_values), 6)
        if minimum_soc_values
        else None
    )
    mean_vehicle_utilization_percent = mean_or_none(
        record.get("utilization_percent")
        for record in vehicle_records
    )

    soc_low, soc_high = EXPERIMENT_CONFIG[CURRENT_SCENARIO]["initial_soc_range"]

    summary = {
        "run_id": RUN["run_id"],
        "depot_layout": DEPOT_LAYOUT,
        "depot_ids": ";".join(sorted({
            str(row["home_depot_id"])
            for row in vehicle_records
        })),
        "scenario": CURRENT_SCENARIO,
        "trial_seed": RUN["trial_seed"],
        "trial_index": RUN.get("trial_index", 0),
        "demand_seed": RUN["demand_seed"],
        "arrival_seed": derive_trial_seed(RUN["trial_seed"], "arrival"),
        "algorithm_seed": RUN["algorithm_seed"],
        "initial_soc_seed": RUN["initial_soc_seed"],
        "algorithm": RUN["algo"],
        "cost_mode": ASSIGNMENT_COST_MODE,
        "alpha_energy": float(ALPHA_ENERGY),
        "beta_time": float(BETA_TIME),
        "soc_cost_weight": float(SOC_COST_WEIGHT),
        "energy_reference_Wh": float(COST_ENERGY_REFERENCE_WH),
        "time_reference_s": float(COST_TIME_REFERENCE_S),
        "initial_soc_low": float(soc_low),
        "initial_soc_high": float(soc_high),
        "fleet_size": len(vehicle_records),
        "spawned_requests": spawned_requests,
        "completed_requests": completed_requests,
        "uncompleted_requests": uncompleted_requests,
        "fulfillment_rate_percent": fulfillment_rate_percent,
        "maximum_wait_violations": maximum_wait_violations,
        "mean_dispatch_delay_s": mean_or_none(
            record.get("dispatch_delay_s") for record in request_records
        ),
        "mean_waiting_time_s": mean_or_none(
            record.get("waiting_time_s") for record in request_records
        ),
        "mean_response_time_s": mean_or_none(
            record.get("response_time_s") for record in request_records
        ),
        "mean_passenger_time_s": mean_or_none(
            record.get("passenger_time_s") for record in request_records
        ),
        "mean_assignment_cost": mean_or_none(
            record.get("assignment_cost") for record in request_records
        ),
        "total_distance_km": total_distance_km,
        "total_energy_consumed_Wh": total_energy_consumed_Wh,

        # Battery energy withdrawn during driving,
        # dwell and unplugged parked operation.
        "total_battery_withdrawn_Wh": total_energy_consumed_Wh,

        # Electrical energy imported from the charger/grid.
        "total_grid_import_Wh": round(
            float(RUN["grid_import_Wh"]), 6
        ),

        "battery_Wh_per_completed_request": (

            round(total_energy_consumed_Wh / completed_requests, 6)
            if completed_requests > 0
            else None
        ),
        "total_energy_charged_Wh": total_energy_charged_Wh,
        "total_charging_cost": round(float(total_charging_cost), 6),
        "minimum_fleet_SOC_Wh": minimum_fleet_SOC_Wh,
        "mean_vehicle_utilization_percent": mean_vehicle_utilization_percent,
        "assignment_runtime_ms": mean_or_none(assignment_runtime_ms),
        "simulation_duration_s": simulation_time_s(),
    }
    summary.update({
        "dispatch_window_s": float(DISPATCH_WINDOW_S),
        "dispatch_urgent_slack_s": float(DISPATCH_URGENT_SLACK_S),
    })
    for explicit_name, legacy_name, value_name, count_name in (
        (
            "mean_dispatch_delay_assigned_s",
            "mean_dispatch_delay_s",
            "dispatch_delay_s",
            "dispatch_delay_sample_count",
        ),
        (
            "mean_post_assignment_wait_picked_up_s",
            "mean_waiting_time_s",
            "waiting_time_s",
            "post_assignment_wait_sample_count",
        ),
        (
            "mean_pickup_response_picked_up_s",
            "mean_response_time_s",
            "response_time_s",
            "pickup_response_sample_count",
        ),
        (
            "mean_in_vehicle_time_dropped_off_s",
            "mean_passenger_time_s",
            "passenger_time_s",
            "in_vehicle_time_sample_count",
        ),
    ):
        summary[explicit_name] = summary[legacy_name]
        summary[count_name] = sum(
            record.get(value_name) is not None
            for record in request_records
        )

    summary.update(
        summarize_deadline_limited_pickup_waits(
            request_records,
            now_s=simulation_time_s(),
            limit_s=MAX_WAIT_S,
        )
    )
    failure_counts = summarize_uncompleted_requests(request_records)
    if sum(failure_counts.values()) != uncompleted_requests:
        raise RuntimeError(
            "Failure breakdown does not match the uncompleted request count."
        )
    
    summary.update(failure_counts)

    # F-027: Distinguish direct-depot accessibility
    # from operationally uncompleted requests.
    summary.update(
        summarize_direct_depot_accessibility(
            TRIAL_REQUESTS,
            RUN["request_records"],
        )
    )

    summary.update(summarize_dispatch_timings())


    engine = _SHARED_RIDE_ENGINE
    if engine is None or engine.run_id != RUN["run_id"]:
        raise RuntimeError(
            "The current run's shared-ride engine is unavailable."
        )

    # F-019: Final matching-cardinality statistics

    count = engine.matching_round_count

    summary.update({
        "matching_round_count": count,

        "matching_selected_pairs_total":
            engine.matching_selected_pairs_total,

        "matching_maximum_pairs_total":
            engine.matching_maximum_pairs_total,

        "matching_cardinality_gap_total":
            engine.matching_cardinality_gap_total,

        "matching_cardinality_gap_rounds":
            engine.matching_cardinality_gap_rounds,

        "matching_cardinality_gap_round_percent": (
            round(
                100.0
                * engine.matching_cardinality_gap_rounds
                / count,
                6,
            )
            if count else None
        ),
    })

    energy = engine.energy_summary()
    for field in (
        "driving_Wh",
        "dwell_Wh",
        "dwell_time_s",
        "battery_parked_Wh",
    ):
        summary[field] = round(float(energy[field]), 6)

    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=RUN_SUMMARY_FIELDS)
        writer.writerow({
            field: summary.get(field)
            for field in RUN_SUMMARY_FIELDS
        })

    RUN["summary_written"] = True

def initialise_vehicle_records() -> None:
    RUN["vehicle_records"] = {}
    RUN["vehicle_results_written"] = False

    for vehicle_id, mover in movers.items():
        RUN["vehicle_records"][vehicle_id] = {
            "run_id": RUN["run_id"],
            "depot_layout": DEPOT_LAYOUT,
            "home_depot_id": str(mover.home_depot_id),
            "home_depot_node": int(mover.home_depot_node),
            "scenario": CURRENT_SCENARIO,
            "trial_seed": RUN["trial_seed"],
            "trial_index": RUN.get(
                "trial_index",
                0
            ),
            "algorithm": RUN["algo"],
            "vehicle_id": vehicle_id,
            "assigned_requests": 0,
            "completed_requests": 0,
            "productive_distance_km": 0.0,
            "empty_pickup_distance_km": 0.0,
            "return_distance_km": 0.0,
            "total_distance_km": 0.0,
            "driving_time_s": 0.0,
            "idle_time_s": 0.0,
            "charging_time_s": 0.0,
            "energy_consumed_Wh": 0.0,
            "energy_charged_Wh": 0.0,
            "minimum_SOC_Wh": float(mover.soc_Wh),
            "final_SOC_Wh": float(mover.soc_Wh),
            "utilization_percent": 0.0,
        }

def write_vehicle_results_if_complete() -> None:
    if RUN["vehicle_results_written"]:
        return

    if (
        RUN.get("canonical_mode")
        and (
            canonical_queue
            or RUN.get("workload_released", 0)
            < RUN.get("workload_size", 0)
        )
    ):
        return

    request_records = RUN["request_records"]

    if not request_records:
        return

    if any(not record.get("_written") for record in request_records.values()):
        if not all_requests_and_vehicles_settled():
            return
        finalise_uncompleted_request_records()
        if any(
            not record.get("_written")
            for record in request_records.values()
        ):
            return

    path = RUN.get("vehicle_csv_path")

    if not path:
        raise RuntimeError(
            "vehicle_csv_path has not been configured"
        )

    for vehicle_id, vehicle_record in RUN["vehicle_records"].items():
        mover = movers.get(vehicle_id)

        if mover is not None:
            vehicle_record["final_SOC_Wh"] = float(mover.soc_Wh)

        observed_time_s = (
            vehicle_record["driving_time_s"]
            + vehicle_record["idle_time_s"]
        )

        if observed_time_s > 0:
            vehicle_record["utilization_percent"] = round(
                100.0
                * vehicle_record["driving_time_s"]
                / observed_time_s,
                6,
            )

    with open(
        path,
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=VEHICLE_RESULT_FIELDS,
        )

        for vehicle_record in RUN["vehicle_records"].values():
            writer.writerow({
                field: vehicle_record.get(field)
                for field in VEHICLE_RESULT_FIELDS
            })

    RUN["vehicle_results_written"] = True


def register_request_record(request) -> None:
    created_time_s = (
        request.created_time_s
        if request.created_time_s is not None
        else simulation_time_s()
    )

    RUN["request_records"][request.id] = {
        "run_id": RUN["run_id"],
        "scenario": CURRENT_SCENARIO,
        "trial_seed": RUN["trial_seed"],
        "trial_index": RUN.get(
            "trial_index",
            0
        ),
        "algorithm": RUN["algo"],
        "request_id": request.id,
        "vehicle_id": None,
        "created_time_s": float(created_time_s),
        "assigned_time_s": None,
        "pickup_time_s": None,
        "dropoff_time_s": None,
        "dispatch_delay_s": None,
        "waiting_time_s": None,
        "passenger_time_s": None,
        "response_time_s": None,
        "pickup_distance_km": None,
        "passenger_distance_km": None,
        "return_distance_km": None,
        "total_distance_km": None,
        "predicted_energy_Wh": None,
        "predicted_return_energy_Wh": None,
        "actual_energy_Wh": None,
        "actual_return_energy_Wh": None,
        "actual_total_energy_Wh": None,
        "energy_prediction_error_percent": None,
        "vehicle_SOC_before_Wh": None,
        "vehicle_SOC_after_Wh": None,
        "assignment_cost": None,
        "request_completed": 0,
        "maximum_wait_violation": 0,
        "completion_status": "pending",
        "failure_reason": None,
        "_pickup_distance_m": 0.0,
        "_passenger_distance_m": 0.0,
        "_return_distance_m": 0.0,
        "_actual_energy_Wh": 0.0,
        "_actual_service_energy_Wh": 0.0,
        "_actual_return_energy_Wh": 0.0,
        "_max_wait_s": request.max_wait_s,
        "_written": False,
    }

def update_request_record(
    request_id: int,
    **changes,
) -> None:
    record = RUN["request_records"].get(request_id)

    if record is None:
        logger.warning(
            f"[Request CSV] Missing request record: {request_id}"
        )
        return

    record.update(changes)

def finalise_request_timing(request_id: int) -> None:
    record = RUN["request_records"].get(request_id)

    if record is None:
        logger.warning(
            f"[Request CSV] Missing request record: {request_id}"
        )
        return

    if record.get("_written"):
        return

    created = record.get("created_time_s")
    assigned = record.get("assigned_time_s")
    pickup = record.get("pickup_time_s")
    dropoff = record.get("dropoff_time_s")

    if None in (
        created,
        assigned,
        pickup,
        dropoff,
    ):
        logger.warning(
            f"[Request CSV] Incomplete timestamps: {request_id}"
        )

        return

    record["dispatch_delay_s"] = round(
    assigned - created,
    6,
    )

    record["waiting_time_s"] = round(
        pickup - assigned,
        6,
    )

    record["response_time_s"] = round(
        pickup - created,
        6,
    )

    record["passenger_time_s"] = round(
        dropoff - pickup,
        6,
    )

    record["maximum_wait_violation"] = int(
        record["response_time_s"] > record["_max_wait_s"]
    )
    record["completion_status"] = "completed"
    record["failure_reason"] = None
    with open(
        RUN["request_csv_path"],
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=REQUEST_RESULT_FIELDS,
        )

        writer.writerow({
            field: record.get(field)
            for field in REQUEST_RESULT_FIELDS
        })

    record["_written"] = True


def all_requests_and_vehicles_settled() -> bool:
    """Finish only after passengers, stops, dwells and returns finish."""
    return bool(
        RUN.get("canonical_mode")
        and RUN.get("workload_released", 0)
        >= RUN.get("workload_size", 0)
        and not canonical_queue
        and not requests_waiting
        and not requests_inflight
        and all(
            not exec_q[m.id]
            and not m.planned_stops
            and not m.onboard_request_ids
            and m.status == "idle"
            for m in movers.values()
        )
    )


def finalise_uncompleted_request_records() -> None:
    """Write terminal records for requests that could not be completed."""
    if not all_requests_and_vehicles_settled():
        return

    now = simulation_time_s()
    pending = [
        record
        for record in RUN["request_records"].values()
        if not record.get("_written")
    ]
    if not pending:
        return

    for record in pending:
        assigned = record.get("assigned_time_s")
        pickup = record.get("pickup_time_s")
        dropoff = record.get("dropoff_time_s")
        created = record.get("created_time_s")

        if assigned is not None and created is not None:
            record["dispatch_delay_s"] = round(assigned - created, 6)
        if pickup is not None and assigned is not None:
            record["waiting_time_s"] = round(pickup - assigned, 6)
        if pickup is not None and created is not None:
            record["response_time_s"] = round(pickup - created, 6)
        if dropoff is not None and pickup is not None:
            record["passenger_time_s"] = round(dropoff - pickup, 6)

        response = record.get("response_time_s")

        if response is not None:
            record["maximum_wait_violation"] = int(
                response > record["_max_wait_s"]
            )

        elif pickup is None and created is not None:
            # Pickup has not occurred, but the wait may exceed the limit.
            elapsed_wait_s = now - created

            record["maximum_wait_violation"] = int(
                elapsed_wait_s > record["_max_wait_s"]
            )

        if record.get("assigned_time_s") is None:
            reason = "not_assigned"
        elif record.get("pickup_time_s") is None:
            reason = "not_picked_up"
        elif record.get("dropoff_time_s") is None:
            reason = "not_dropped_off"
        else:
            reason = "not_finalised"

        record["completion_status"] = "uncompleted"
        record["failure_reason"] = record.get("failure_reason") or reason
        # F-030: Request-level energy is not attributed in the
        # shared-ride engine. Do not report missing values as zero.
        record["actual_energy_Wh"] = None
        record["actual_return_energy_Wh"] = None
        record["actual_total_energy_Wh"] = None
        record["energy_prediction_error_percent"] = None


    with open(
        RUN["request_csv_path"],
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=REQUEST_RESULT_FIELDS)
        for record in pending:
            writer.writerow({
                field: record.get(field)
                for field in REQUEST_RESULT_FIELDS
            })
            record["_written"] = True


def _ensure_vehicle_state():
    for vid in movers.keys():
        RUN["vehicle_assigned"].setdefault(vid, set())
        RUN["vehicle_completed"].setdefault(vid, set())


_V2_RUN_OUTPUT_PATH_KEYS = (
    "csv_path",
    "request_csv_path",
    "vehicle_csv_path",
    "assignment_csv_path",
    "charging_csv_path",
    "summary_csv_path",
    "fleet_log_csv_path",
    "velocity_log_csv_path",
    "canonical_requests_csv_path",
    "canonical_requests_sha256_path",
    "settings_json_path",
)

def reset_run_state(
    new_algo: str,
    seed=None,
    *,
    scenario: str = None,
    trial_index: int = None,
):
    global fulfilled_requests, spawned_requests_total
    global total_travel_time, total_energy_consumed, total_idle_time
    global total_charging_cost, tick
    global rr_counter
    global TRIAL_DEMAND, TRIAL_REQUESTS, canonical_queue
    global CURRENT_SCENARIO, ASSIGNMENT_METHOD

    selected_scenario = CURRENT_SCENARIO if scenario is None else scenario
    selected_trial_index = (
        RUN["trial_index"] if trial_index is None else trial_index
    )

    if (
        not isinstance(selected_scenario, str)
        or selected_scenario not in EXPERIMENT_CONFIG
    ):
        raise ValueError(f"Invalid scenario: {selected_scenario}")

    if not isinstance(new_algo, str) or new_algo not in ALGO_FUNCS:
        raise ValueError(f"Invalid algorithm: {new_algo}")

    if (
        isinstance(selected_trial_index, bool)
        or not isinstance(selected_trial_index, int)
        or not 0 <= selected_trial_index < len(TRIAL_SEEDS)
    ):
        raise ValueError(
            f"Invalid trial index {selected_trial_index}. "
            f"Choose an integer from 0 to {len(TRIAL_SEEDS) - 1}."
        )

    if BENCHMARK_DEPOT is None:
            raise RuntimeError(
                "BENCHMARK_DEPOT has not been initialized."
            )
    
    starting_parking = BENCHMARK_DEPOT["parking"]
    # Retained as the single-depot reference for legacy reporting.

    fleet_size = EXPERIMENT_CONFIG[selected_scenario]["vehicles"]                   
    if (
        isinstance(fleet_size, bool)
        or not isinstance(fleet_size, int)
        or fleet_size <= 0
    ):
        raise ValueError("The scenario's vehicle count must be a positive integer.")

    selected_seed = TRIAL_SEED if seed is None else seed
    if isinstance(selected_seed, bool) or not isinstance(selected_seed, int):
        raise ValueError("The trial seed must be an integer.")

    home_rows = get_depot_allocation(fleet_size)

    soc_range = EXPERIMENT_CONFIG[selected_scenario].get(
        "initial_soc_range", [1.0, 1.0]
    )
    if not isinstance(soc_range, (list, tuple)) or len(soc_range) != 2:
        raise ValueError("initial_soc_range must contain two fractions.")

    soc_low, soc_high = map(float, soc_range)
    if (
        not math.isfinite(soc_low)
        or not math.isfinite(soc_high)
        or not 0.0 <= soc_low <= soc_high <= 1.0
    ):
        raise ValueError("Require 0 <= initial SOC low <= high <= 1.")

    # Prepare this run's SOC stream without altering the active run.
    prepared_soc_seed = (
        INITIAL_SOC_SEED
        if seed is None
        else derive_trial_seed(selected_seed, "initial_soc")
    )
    prepared_soc_rng = random.Random(prepared_soc_seed)

    prepared_movers = {}
    for vehicle_id in range(1, fleet_size + 1):
        soc_fraction = (
            soc_low
            if soc_low == soc_high
            else prepared_soc_rng.uniform(soc_low, soc_high)
        )
        home = home_rows[vehicle_id - 1]
        parking_lat, parking_lon = map(float, home["parking"])

        prepared_movers[vehicle_id] = Mover(
            id=vehicle_id,
            lon=parking_lon,
            lat=parking_lat,
            node=int(home["node"]),
            home_depot_id=str(home["parking_id"]),
            home_depot_node=int(home["node"]),
            speed_kph=SPEED_KPH,
            soc_Wh=BATTERY_CAPACITY_WH * soc_fraction,
            status="idle",
            route_coords=[],
        )

    prepared_exec_q = {vid: deque() for vid in prepared_movers}
    prepared_serving = {vid: None for vid in prepared_movers}
    prepared_phase = {vid: "idle" for vid in prepared_movers}
    prepared_velocity = {vid: 0.0 for vid in prepared_movers}

    request_count = EXPERIMENT_CONFIG[selected_scenario]["requests"]
    prepared_demand, prepared_trial = prepare_canonical_trial(
        request_count,
        demand_seed=(
            DEMAND_SEED
            if seed is None
            else derive_trial_seed(selected_seed, "demand")
        ),
        arrival_seed=(
            ARRIVAL_SEED
            if seed is None
            else derive_trial_seed(selected_seed, "arrival")
        ),
    )
    prepared_queue = deque(
        build_requests_from_canonical_trial(prepared_trial)
    )


    if len(prepared_queue) != request_count:
        raise RuntimeError(
            "Runtime request count does not match the prepared trial."
        )

    prepared_run_id = _make_run_id()
    prepared_csv_paths = prepare_run_output_files(
        prepared_run_id,
        prepared_movers,
        prepared_trial,
        run_settings=build_run_settings(
            selected_scenario,
            new_algo,
            selected_trial_index,
            seed=seed,
        ),
    )

    # Apply settings only after the fleet, requests and output files are ready.
    if seed is None:
        reset_random_streams()
    else:
        set_trial_seed(selected_seed)

    INITIAL_SOC_RNG.setstate(prepared_soc_rng.getstate())

    finish_charging_sessions("reset")
    clear_path_cache()

    CURRENT_SCENARIO = selected_scenario
    ASSIGNMENT_METHOD = new_algo
    RUN["trial_index"] = selected_trial_index
    TRIAL_DEMAND, TRIAL_REQUESTS = prepared_demand, prepared_trial
    RUN["trial_seed"] = TRIAL_SEED
    
    # ==========================================
    # 1. Reset global metrics
    # ==========================================
    fulfilled_requests = 0
    spawned_requests_total = 0

    total_travel_time = 0.0
    total_energy_consumed = 0.0
    total_idle_time = 0.0
    total_charging_cost = 0.0

    assignment_runtime_ms.clear()
    dispatch_runtime_ms.clear()
    tick = 0

    # Reset Round-Robin position
    rr_counter = 0

    # ==========================================
    # 2. Clear old requests and trip information
    # ==========================================
    requests_waiting.clear()
    requests_inflight.clear()
    canonical_queue = prepared_queue
    RUN["workload_size"] = len(canonical_queue)
    RUN["workload_released"] = 0

    assignment_meta.clear()
    trip_record.clear()

    # ==========================================
  # 3. Use the same depot for every algorithm
    # ==========================================
    
    # ==========================================
    # 4. Rebuild the fleet for the selected scenario
    # ==========================================
    movers.clear()
    movers.update(prepared_movers)

    exec_q.clear()
    exec_q.update(prepared_exec_q)

    serving.clear()
    serving.update(prepared_serving)

    phase.clear()
    phase.update(prepared_phase)

    mover_velocity.clear()
    mover_velocity.update(prepared_velocity)

    
    # ==========================================
    # 5. Reset run-recording information
    # ==========================================
    RUN["run_id"] = prepared_run_id
    RUN["algo"] = new_algo
    RUN["experiment_started"] = False
    RUN["run_error"] = None
    RUN["trial_seed"] = TRIAL_SEED
    RUN["demand_seed"] = DEMAND_SEED
    RUN["algorithm_seed"] = ALGORITHM_SEED
    RUN["initial_soc_seed"] = INITIAL_SOC_SEED
    RUN["req_seq_counter"] = 0
    RUN["req_seq"] = {}

    RUN["vehicle_assigned"] = {}
    RUN["vehicle_completed"] = {}
    RUN["vehicle_req_dist_km"] = {}
    RUN["vehicle_req_energy_Wh"] = {}
    RUN["request_records"] = {}
    RUN["assignment_event_counter"] = 0
    RUN["charging_event_counter"] = 0
    RUN["grid_import_Wh"] = 0.0
    RUN["summary_written"] = False
    RUN.update(prepared_csv_paths)

    _ensure_vehicle_state()
    initialise_vehicle_records()

    logger.info(
        f"[Reset] Complete reset finished | "
        f"Algorithm={new_algo} | "
        f"Vehicles={len(movers)} | "
        f"Active depots={','.join(d['parking_id'] for d in _v2_active_depots())} | "
        f"Run ID={RUN['run_id']}"
    )

def reset_to_trial(
    trial_index: int,
    algorithm: str = None,
    scenario: str = None,
):
    """Prepare the selected trial before changing the current run."""
    if (
        isinstance(trial_index, bool)
        or not isinstance(trial_index, int)
        or not 0 <= trial_index < len(TRIAL_SEEDS)
    ):
        raise ValueError(
            f"Invalid trial index {trial_index}. "
            f"Choose an integer from 0 to {len(TRIAL_SEEDS) - 1}."
        )

    selected_algorithm = (
        ASSIGNMENT_METHOD if algorithm is None else algorithm
    )

    reset_run_state(
        new_algo=selected_algorithm,
        seed=TRIAL_SEEDS[trial_index],
        scenario=scenario,
        trial_index=trial_index,
    )

    return {
        "scenario": CURRENT_SCENARIO,
        "trial_index": RUN["trial_index"],
        "trial_seed": RUN["trial_seed"],
        "algorithm": RUN["algo"],
        "workload_size": RUN["workload_size"],
    }

def experiment_run_is_complete() -> bool:
    """Return True after work is settled and final output has been saved."""
    return bool(
        RUN.get("run_error") is None
        and not RUN.get("experiment_started", False)
        and all_requests_and_vehicles_settled()
        and RUN.get("summary_written", False)
        and RUN.get("vehicle_results_written", False)
    )
def finish_experiment_batch(task):
    """Clean up this batch without changing a replacement run."""
    cancelled = task.cancelled()
    error = None if cancelled else task.exception()

    # An older task must not change the state of a newer batch.
    if RUN.get("batch_task") is not task:
        return

    try:
        if cancelled:
            message = "CancelledError: Experiment batch was cancelled."
        elif error is not None:
            message = f"{type(error).__name__}: {error}"
        else:
            return

        RUN["batch_error"] = message
        batch_run_id = RUN.get("batch_run_id")

        if (
            batch_run_id is not None
            and RUN.get("run_id") == batch_run_id
        ):
            if experiment_run_is_complete():
                # Results finished before the worker could count this run.
                RUN["batch_completed"] += 1
            else:
                RUN["experiment_started"] = False
                if RUN.get("run_error") is None:
                    RUN["run_error"] = message

        if cancelled and RUN.get("shutting_down", False):
            logger.info("[BatchRunner] %s", message)
        else:
            logger.warning("[BatchRunner] %s", message)

    finally:
        RUN["batch_running"] = False
        RUN["batch_task"] = None
        RUN["batch_run_id"] = None

_V2_BATCH_PREP_BUSY = False
_V2_BATCH_PREP_WORKER = None

async def _v2_async_batch_prepare(experiment):
    """Prepare one batch run without blocking the event loop."""
    global _V2_BATCH_PREP_BUSY, _V2_BATCH_PREP_WORKER

    if RUN.get("shutting_down"):
        raise RuntimeError("Batch preparation rejected during shutdown.")

    if _V2_CONTROL_RESET_BUSY or _V2_BATCH_PREP_BUSY:
        raise RuntimeError("Another state preparation is in progress.")

    _V2_BATCH_PREP_BUSY = True

    def prepare():
        with _V2_RESET_STATE_LOCK:
            if RUN.get("shutting_down"):
                raise RuntimeError(
                    "Batch preparation rejected during shutdown."
                )

            reset_to_trial(
                trial_index=experiment["trial_index"],
                algorithm=experiment["algorithm"],
                scenario=experiment["scenario"],
            )

            prepared_run_id = RUN["run_id"]

            settings_path = Path(RUN["settings_json_path"])
            settings = json.loads(
                settings_path.read_text(encoding="utf-8")
            )
            settings["execution"] = _v2_batch_execution_metadata()

            temporary_path = settings_path.with_name(
                settings_path.name + ".execution.tmp"
            )

            try:
                with temporary_path.open(
                    "x", encoding="utf-8", newline="\n"
                ) as stream:
                    json.dump(
                        settings, stream,
                        indent=2, sort_keys=True,
                        allow_nan=False,
                    )
                    stream.write("\n")

                os.replace(temporary_path, settings_path)

            finally:
                temporary_path.unlink(missing_ok=True)

            load_graph()
            write_vehicle_matrix_csv()
            release_due_canonical_requests()

            return prepared_run_id

    try:
        worker = asyncio.create_task(
            asyncio.to_thread(prepare)
        )
        _V2_BATCH_PREP_WORKER = worker
    except BaseException:
        _V2_BATCH_PREP_BUSY = False
        raise

    def finish(done):
        global _V2_BATCH_PREP_BUSY, _V2_BATCH_PREP_WORKER
        _V2_BATCH_PREP_WORKER = None
        _V2_BATCH_PREP_BUSY = False
        if not done.cancelled():
            done.exception()

    worker.add_done_callback(finish)

    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Never abandon a shared-state mutation.
        await asyncio.shield(worker)
        raise


# FIX-08: Fail-closed, read-only archive verification.
_V2_RESUME_COMPARE_KEYS = (
    "execution",
    "schema_version", "shared_ride_model", "scenario",
    "scenario_config", "algorithm", "trial_index",
    "configured_seeds", "seed_derivation", "parameters",
    "benchmark_depot", "depot_layout", "output_schema",
    "depot_allocation_policy", "vehicle_home_depots",
    "depot_selection_method", "depot_demand_model",
    "input_files", "backend_source", "environment",
)

def _v2_batch_execution_metadata():
    """Execution identity for scheduler-driven batch experiments."""
    return {"mode": "scheduler_batch"}

def _v2_resume_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024), b""
        ):
            digest.update(block)
    return digest.hexdigest()

def _v2_resume_verified_archive(folder, experiment, expected):
    """Return (eligible, reason); reject incomplete archives."""
    try:
        folder = Path(folder)

        if not folder.is_dir() or folder.is_symlink():
            return False, "Archive directory missing or linked"

        settings = json.loads(
            (folder / "run_settings.json").read_text("utf-8")
        )

        if settings.get("run_id") != folder.name:
            return False, "Archive run ID mismatch"

        for key in _V2_RESUME_COMPARE_KEYS:
            if key not in expected or settings.get(key) != expected[key]:
                return False, "Configuration mismatch: " + key

        if (
            settings["scenario"] != experiment["scenario"]
            or settings["algorithm"] != experiment["algorithm"]
            or int(settings["trial_index"]) != int(experiment["trial_index"])
            or int(settings["configured_seeds"]["TRIAL_SEED"])
                != int(experiment["trial_seed"])
        ):
            return False, "Manifest identity mismatch"

        def archive_file(name):
            if (
                not isinstance(name, str)
                or name in ("", ".", "..")
                or Path(name).name != name
            ):
                raise ValueError("Unsafe archive filename")

            path = folder / name

            if path.is_symlink():
                raise ValueError("Linked archive file")

            return path

        backend = expected["backend_source"]

        if _v2_resume_sha256(
            archive_file(backend["snapshot_filename"])
        ) != backend["sha256"]:
            return False, "Backend source snapshot mismatch"

        package = expected["environment"]["package_snapshot"]

        if _v2_resume_sha256(
            archive_file(package["filename"])
        ) != package["sha256"]:
            return False, "Package snapshot mismatch"

        workload = settings["canonical_workload"]
        planned = int(expected["scenario_config"]["requests"])
        vehicles = int(expected["scenario_config"]["vehicles"])

        if int(workload["request_count"]) != planned:
            return False, "Canonical request count mismatch"

        if _v2_resume_sha256(
            archive_file(workload["filename"])
        ) != workload["sha256"]:
            return False, "Canonical workload checksum mismatch"

        def rows(name):
            with archive_file(name).open(
                newline="", encoding="utf-8"
            ) as stream:
                reader = csv.DictReader(stream)

                if reader.fieldnames is None:
                    raise ValueError("Missing CSV header")

                return list(reader)

        for diagnostic in (
            "vehicle_matrix.csv",
            "assignment_events.csv",
            "charging_events.csv",
            "fleet_log.csv",
            "velocity_log.csv",
        ):
            with archive_file(diagnostic).open(
                newline="", encoding="utf-8"
            ) as stream:
                if not next(csv.reader(stream), None):
                    return False, "Missing diagnostic CSV header"

        canonical = rows("canonical_requests.csv")
        summaries = rows("run_summary.csv")
        requests = rows("request_results.csv")
        fleet = rows("vehicle_results.csv")

        if len(canonical) != planned or len(summaries) != 1:
            return False, "Canonical or summary rows incomplete"

        if len(requests) != planned or len(fleet) != vehicles:
            return False, "Request or vehicle rows incomplete"

        summary = summaries[0]

        identity = {
            "run_id": folder.name,
            "scenario": experiment["scenario"],
            "algorithm": experiment["algorithm"],
            "trial_index": str(experiment["trial_index"]),
            "trial_seed": str(experiment["trial_seed"]),
        }

        for key, value in identity.items():
            if str(summary.get(key)) != str(value):
                return False, "Summary identity mismatch"

        completed = int(summary["completed_requests"])
        uncompleted = int(summary["uncompleted_requests"])

        if (
            completed < 0
            or uncompleted < 0
            or completed + uncompleted != planned
            or int(summary["spawned_requests"]) != planned
            or int(summary["fleet_size"]) != vehicles
        ):
            return False, "Summary totals inconsistent"

        canonical_ids = [
            r["request_id"] for r in canonical
        ]
        request_ids = [
            r["request_id"] for r in requests
        ]

        if (
            len(set(canonical_ids)) != planned
            or len(set(request_ids)) != planned
            or set(request_ids) != set(canonical_ids)
        ):
            return False, "Request IDs inconsistent"

        completed_rows = sum(
            int(r["request_completed"]) for r in requests
        )

        if completed_rows != completed:
            return False, "Request completion count inconsistent"

        # FIX-08: Do not confuse populated CSV rows with
        # finalized request outcomes.
        for record in requests:
            flag = int(record["request_completed"])
            status = record.get("completion_status", "")

            if status == "completed":
                if (
                    flag != 1
                    or not record.get("dropoff_time_s")
                    or record.get("failure_reason")
                ):
                    return False, "Invalid completed request evidence"

            elif status == "uncompleted":
                if flag != 0 or not record.get("failure_reason"):
                    return False, "Invalid uncompleted request evidence"

            else:
                return False, "Nonterminal request result"

        duration = float(summary["simulation_duration_s"])

        if not 0.0 < duration < float("inf"):
            return False, "Invalid simulation duration"

        vehicle_ids = [
            r["vehicle_id"] for r in fleet
        ]

        if len(set(vehicle_ids)) != vehicles:
            return False, "Duplicate vehicle IDs"

        for record in requests + fleet:
            for key, value in identity.items():
                if str(record.get(key)) != str(value):
                    return False, "Output identity mismatch"

        return True, "Verified completed archive"

    except (
        OSError, ValueError, TypeError, KeyError,
        UnicodeError, csv.Error
    ) as error:
        return False, "Invalid archive: " + str(error)


def _v2_resume_find_compatible(experiment):
    """Find one fully verified completed run; never select ambiguously."""
    with _V2_RESET_STATE_LOCK:
        expected = build_run_settings(
            experiment["scenario"],
            experiment["algorithm"],
            experiment["trial_index"],
            seed=experiment["trial_seed"],
        )
        expected["execution"] = _v2_batch_execution_metadata()

    results_root = Path(__file__).resolve().parent / "results"

    if not results_root.is_dir():
        return None

    matching = []

    for folder in sorted(results_root.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue

        settings_file = folder / "run_settings.json"

        if not settings_file.is_file() or settings_file.is_symlink():
            continue

        try:
            settings = json.loads(
                settings_file.read_text(encoding="utf-8")
            )

            archived_source = settings.get(
                "backend_source", {}
            )

            if not isinstance(archived_source, dict):
                continue

            if (
                archived_source.get("sha256")
                != expected["backend_source"]["sha256"]
            ):
                continue

            if (
                settings.get("scenario") != experiment["scenario"]
                or settings.get("algorithm") != experiment["algorithm"]
                or settings.get("trial_index")
                    != experiment["trial_index"]
                or settings.get("configured_seeds", {}).get(
                    "TRIAL_SEED"
                ) != experiment["trial_seed"]
            ):
                continue

            verified, reason = _v2_resume_verified_archive(
                folder, experiment, expected
            )

            if verified:
                matching.append(str(folder))

        except (
            OSError, ValueError, KeyError,
            TypeError, UnicodeError
        ):
            # Invalid archives are never eligible for skipping.
            continue

    if len(matching) > 1:
        raise RuntimeError(
            "Resume ambiguity: multiple verified archives for "
            + str((
                experiment["scenario"],
                experiment["algorithm"],
                experiment["trial_index"],
                experiment["trial_seed"],
            ))
        )

    return matching[0] if matching else None

async def run_experiment_batch(manifest, *, resume=False):
    """Continue independent trials; report failures separately."""
    prepared_run_id = None
    for experiment in manifest:
        previous_run_id = RUN.get("run_id")
        try:
            if resume:
                archived = await asyncio.to_thread(
                    _v2_resume_find_compatible,
                    experiment,
                )

                if RUN.get("shutting_down"):
                    raise RuntimeError(
                        "Shutdown interrupted resume verification."
                    )

                if archived is not None:
                    RUN["batch_skipped"] = (
                        RUN.get("batch_skipped", 0) + 1
                    )
                    RUN.setdefault(
                        "batch_resumed_archives", []
                    ).append({
                        "scenario": experiment["scenario"],
                        "algorithm": experiment["algorithm"],
                        "trial_index": experiment["trial_index"],
                        "trial_seed": experiment["trial_seed"],
                        "archive": archived,
                    })
                    continue
            # No new run belongs to this iteration until reset succeeds.
            prepared_run_id = None
            RUN["batch_run_id"] = None
            RUN["batch_current"] = experiment

            ensure_scheduler_running()
            prepared_run_id = await _v2_async_batch_prepare(
                experiment
            )

            if RUN.get("shutting_down"):
                raise RuntimeError(
                    "Shutdown interrupted batch preparation."
                )

            RUN["batch_run_id"] = prepared_run_id
            RUN["experiment_started"] = True
            run_started_at = asyncio.get_running_loop().time()
            max_wait_seconds = 3600

            while True:
                if RUN.get("run_id") != prepared_run_id:
                    raise RuntimeError(
                        "The active run changed while the batch was waiting."
                    )

                run_error = RUN.get("run_error")
                if run_error is not None:
                    raise RuntimeError(f"Run failed: {run_error}")

                if experiment_run_is_complete():
                    break

                elapsed_seconds = (
                    asyncio.get_running_loop().time() - run_started_at
                )
                if elapsed_seconds > max_wait_seconds:
                    raise TimeoutError(
                        f"Experiment timed out: {experiment}"
                    )

                await asyncio.sleep(0.5)

            RUN["batch_completed"] += 1
            RUN["batch_run_id"] = None
            prepared_run_id = None

        except asyncio.CancelledError:
            message = (
                "CancelledError: Batch interrupted before "
                "the current trial was counted."
            )

            # An existing simulation step must finish before
            # cancellation changes shared run state.
            active_step = _V2_SCHEDULER_STEP_WORKER
            if active_step is not None and not active_step.done():
                try:
                    await asyncio.shield(active_step)
                except Exception:
                    pass

            current_id = RUN.get("run_id")
            interrupted_id = prepared_run_id

            # A preparation worker may have created a new run
            # before cancellation was delivered.
            if (
                interrupted_id is None
                and current_id != previous_run_id
            ):
                interrupted_id = current_id

            completed = False

            if (
                interrupted_id is not None
                and current_id == interrupted_id
            ):
                completed = experiment_run_is_complete()

                if completed:
                    # The trial finished before cancellation.
                    RUN["batch_completed"] += 1
                else:
                    RUN["experiment_started"] = False

                    if RUN.get("run_error") is None:
                        RUN["run_error"] = message

            if not completed:
                RUN["batch_failed"] = (
                    RUN.get("batch_failed", 0) + 1
                )

                RUN.setdefault("batch_failures", []).append({
                    "scenario": experiment["scenario"],
                    "algorithm": experiment["algorithm"],
                    "trial_index": experiment["trial_index"],
                    "trial_seed": experiment.get("trial_seed"),
                    "run_id": interrupted_id,
                    "status": "interrupted",
                    "error": message,
                })

            RUN["batch_error"] = message
            RUN["batch_current"] = None
            raise

        except Exception as error:
            message = f"{type(error).__name__}: {error}"

            # Shutdown is not an ordinary experiment failure.
            if RUN.get("shutting_down"):
                raise

            failed_run_id = prepared_run_id

            # Preparation may fail after creating a new run.
            if (
                failed_run_id is None
                and RUN.get("run_id") != previous_run_id
            ):
                failed_run_id = RUN.get("run_id")

            # Never mark an earlier completed run as failed.
            if (
                failed_run_id is not None
                and RUN.get("run_id") == failed_run_id
            ):
                RUN["experiment_started"] = False

                if RUN.get("run_error") is None:
                    RUN["run_error"] = message

            RUN["batch_error"] = message
            RUN["batch_failed"] = RUN.get("batch_failed", 0) + 1

            RUN.setdefault("batch_failures", []).append({
                "scenario": experiment["scenario"],
                "algorithm": experiment["algorithm"],
                "trial_index": experiment["trial_index"],
                "trial_seed": experiment.get("trial_seed"),
                "run_id": failed_run_id,
                "error": message,
            })

            logger.exception(
                "[BatchRunner] Trial failed; continuing batch"
            )

        finally:
            RUN["batch_run_id"] = None
            prepared_run_id = None

    RUN["batch_current"] = None

def seq_label(seq):
    return f"req{seq}"


def vehicle_label(vid):
    return f"R{vid:02d}"

def _format_req_map(vid, value_map):
    pairs = [(s, val) for (v, s), val in value_map.items() if v == vid]
    pairs.sort(key=lambda x: x[0])
    return ";".join([f"{seq_label(s)}:{round(val,3)}" for s, val in pairs])

def _format_req_set(req_set):
    if not req_set: return ""
    return ";".join([seq_label(s) for s in sorted(req_set)])

def write_vehicle_matrix_csv():
    if not RUN.get("csv_path"): return
    _ensure_vehicle_state()
    fieldnames = VEHICLE_MATRIX_FIELDS
    with open(RUN["csv_path"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for vid in sorted(movers.keys()):
            assigned  = RUN["vehicle_assigned"].get(vid, set())
            completed = RUN["vehicle_completed"].get(vid, set())
            w.writerow({
                "Vehicle": vehicle_label(vid),
                "Assigned Requests": _format_req_set(assigned),
                "Completed Requests": _format_req_set(completed),
                "Total Distance Covered per Request (km)": _format_req_map(vid, RUN["vehicle_req_dist_km"]),
            })


# ── Simulation state ──────────────────────────────────────────────
class PassengerStop(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    kind: Literal["pickup", "dropoff"]
    request_id: int = Field(gt=0, strict=True)
    node: int = Field(strict=True)


class Mover(BaseModel):
    id: int
    lon: float
    lat: float
    soc_Wh: float = BATTERY_CAPACITY_WH
    speed_kph: float = SPEED_KPH
    node: Optional[int] = None
    status: str = "idle"
    home_depot_node: Optional[int] = None
    home_depot_id: Optional[str] = None
    route_coords: List[Tuple[float, float]] = Field(default_factory=list)
    _edge_u: Optional[int] = None
    _edge_v: Optional[int] = None
    _edge_prog: float = 0.0

    # One request represents one passenger.
    onboard_request_ids: List[int] = Field(
        default_factory=list,
        max_length=UGV_PASSENGER_CAPACITY,
    )

    planned_stops: List[PassengerStop] = Field(default_factory=list)

    @property
    def passengers_onboard(self) -> int:
        return len(self.onboard_request_ids)

    @property
    def mass_kg(self) -> float:
        return (
            UGV_EMPTY_MASS_KG
            + UGV_PASSENGER_MASS_KG * self.passengers_onboard
        )

    def board_passenger(self, request_id: int) -> None:
        if type(request_id) is not int or request_id <= 0:
            raise ValueError("Passenger request ID must be a positive integer.")
        if request_id in self.onboard_request_ids:
            raise RuntimeError(f"Request {request_id} is already onboard.")
        if self.passengers_onboard >= UGV_PASSENGER_CAPACITY:
            raise RuntimeError(f"Vehicle {self.id} has no free passenger seat.")

        self.onboard_request_ids.append(request_id)

    def dropoff_passenger(self, request_id: int) -> None:
        if type(request_id) is not int or request_id <= 0:
            raise ValueError("Passenger request ID must be a positive integer.")
        if request_id not in self.onboard_request_ids:
            raise RuntimeError(f"Request {request_id} is not onboard.")

        self.onboard_request_ids.remove(request_id)

class RideReq(BaseModel):
    id: int
    pickup_lon: float
    pickup_lat: float
    drop_lon: float
    drop_lat: float
    max_wait_s: int = MAX_WAIT_S
    created_time_s: Optional[float] = None
    pickup_node: Optional[int] = None
    drop_node:   Optional[int] = None

def stop_plan_masses(m: Mover, stops) -> Tuple[float, ...]:
    """Validate passenger order/capacity and return mass for each leg.

    The first value is the current mass. Each subsequent value is
    the mass after that stop, including the empty final return leg.
    """
    onboard = set(m.onboard_request_ids)

    if len(onboard) != m.passengers_onboard:
        raise RuntimeError("The onboard passenger list contains duplicates.")
    if len(onboard) > UGV_PASSENGER_CAPACITY:
        raise RuntimeError("The vehicle already exceeds passenger capacity.")

    seen_requests = set(onboard)
    masses = [
        UGV_EMPTY_MASS_KG + UGV_PASSENGER_MASS_KG * len(onboard)
    ]

    for stop in stops:
        if not isinstance(stop, PassengerStop):
            raise TypeError("Every planned stop must be a PassengerStop.")

        request_id = stop.request_id

        if stop.kind == "pickup":
            if request_id in seen_requests:
                raise ValueError(
                    f"Request {request_id} has a repeated pickup."
                )
            if len(onboard) >= UGV_PASSENGER_CAPACITY:
                raise ValueError(
                    f"Pickup {request_id} would exceed passenger capacity."
                )

            seen_requests.add(request_id)
            onboard.add(request_id)

        else:
            if request_id not in onboard:
                raise ValueError(
                    f"Request {request_id} cannot be dropped off "
                    "before boarding, or more than once."
                )
            onboard.remove(request_id)

        masses.append(
            UGV_EMPTY_MASS_KG + UGV_PASSENGER_MASS_KG * len(onboard)
        )

    if onboard:
        raise ValueError(
            f"Missing drop-off stops for requests: {sorted(onboard)}."
        )

    return tuple(masses)


def set_vehicle_stop_plan(m: Mover, stops) -> Tuple[float, ...]:
    """Store a passenger stop plan only after validation succeeds."""
    candidate = list(stops)
    masses = stop_plan_masses(m, candidate)
    m.planned_stops = candidate
    return masses


def generate_trial_demand(request_count, *, demand_seed=None):
    """Prepare reproducible OD pairs without changing the saved trial."""

    if (
        isinstance(request_count, bool)
        or not isinstance(request_count, int)
        or request_count <= 0
    ):
        raise ValueError(
            "request_count must be a positive integer, such as 30."
        )

    if G is None:
        raise ValueError("Graph has not been loaded.")

    if not HOTSPOT_DATA:
        raise ValueError("Hotspot data has not been loaded.")

    # Local RNG dedicated only to trial demand
    if demand_seed is None:
        demand_seed = DEMAND_SEED

    rng = random.Random(demand_seed)

    valid_hotspots = []
    hotspot_weights = []

    for hotspot in HOTSPOT_DATA:

        weight_raw = hotspot.get("demand_weight")

        if pd.isna(weight_raw):
            continue

        weight = float(weight_raw)

        if weight <= 0:
            continue

        valid_hotspots.append(hotspot)
        hotspot_weights.append(weight)

    active_nodes = {
        int(hotspot["node"])
        for hotspot in valid_hotspots
    }
    active_node_count = len(active_nodes)

    if active_node_count < 2:
        raise ValueError(
            "At least two distinct hotspot nodes need positive demand weights."
        )

    # Each pickup can be paired with every other active node.
    max_unique_pairs = active_node_count * (active_node_count - 1)

    if request_count > max_unique_pairs:
        raise ValueError(
            f"Requested {request_count} unique pickup-destination pairs, "
            f"but {active_node_count} distinct active hotspot nodes "
            f"allow at most {max_unique_pairs}. "
            "Reduce the request count or add valid weighted hotspot nodes."
        )

    generated_pairs = []
    seen_pairs = set()

    attempts = 0
    max_attempts = request_count * 200

    while (
        len(generated_pairs) < request_count
        and attempts < max_attempts
    ):
        attempts += 1

        pickup = rng.choices(
            valid_hotspots,
            weights=hotspot_weights,
            k=1
        )[0]

        a = int(pickup["node"])

        dropoff_candidates = []
        dropoff_weights = []

        for hotspot, weight in zip(
            valid_hotspots,
            hotspot_weights
        ):
            node = int(hotspot["node"])

            if node != a:
                dropoff_candidates.append(hotspot)
                dropoff_weights.append(weight)

        if not dropoff_candidates:
            continue

        dropoff = rng.choices(
            dropoff_candidates,
            weights=dropoff_weights,
            k=1
        )[0]

        b = int(dropoff["node"])

        # Do not allow duplicate OD pairs
        if (a, b) in seen_pairs:
            continue

        # OD pair must be routable on the UTP graph
        try:
            nx.shortest_path(
                G,
                a,
                b,
                weight="length"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

        seen_pairs.add((a, b))
        generated_pairs.append((a, b))

    if len(generated_pairs) != request_count:
        raise RuntimeError(
            f"Could only generate {len(generated_pairs)} "
            f"of {request_count} required trial requests."
        )

    # Keep the prepared pairs local until the full trial is ready.
    prepared_demand = tuple(generated_pairs)

    logger.info(
        f"[TrialDemand] Prepared {len(prepared_demand)} OD pairs | "
        f"demand_seed={demand_seed}"
    )

    return prepared_demand

def prepare_canonical_trial(
    request_count,
    *,
    demand_seed=None,
    arrival_seed=None,
):
    """Build matching trial datasets without replacing the active trial."""
    if (
        isinstance(MAX_WAIT_S, bool)
        or not isinstance(MAX_WAIT_S, int)
        or MAX_WAIT_S <= 0
    ):
        raise ValueError(
            "MAX_WAIT_S must be a positive integer number of seconds, "
            "such as 480."
        )

    arrival_times = generate_trial_arrival_schedule(
        request_count,
        arrival_seed=arrival_seed,
    )
    od_pairs = generate_trial_demand(
        request_count,
        demand_seed=demand_seed,
    )

    if (
        len(od_pairs) != request_count
        or len(arrival_times) != request_count
    ):
        raise RuntimeError(
            "OD demand and arrival schedule must each contain "
            f"exactly {request_count} entries."
        )

    canonical_requests = []
    for request_id, (
        (pickup_node, dropoff_node),
        arrival_time_s,
    ) in enumerate(zip(od_pairs, arrival_times), start=1):
        canonical_requests.append(
            CanonicalRequest(
                request_id=request_id,
                pickup_node=int(pickup_node),
                dropoff_node=int(dropoff_node),
                arrival_time_s=float(arrival_time_s),
                max_wait_s=MAX_WAIT_S,
            )
        )

    return od_pairs, tuple(canonical_requests)


def generate_canonical_trial(request_count):
    """Prepare and publish the frozen trial for existing callers."""
    global TRIAL_DEMAND, TRIAL_REQUESTS

    TRIAL_DEMAND, TRIAL_REQUESTS = prepare_canonical_trial(request_count)

    logger.info(
        f"[CanonicalTrial] Frozen {len(TRIAL_REQUESTS)} requests | "
        f"scenario={CURRENT_SCENARIO} | "
        f"trial_seed={TRIAL_SEED}"
    )

    return TRIAL_REQUESTS


def build_requests_from_canonical_trial(canonical_requests=None):
    """Build fresh runtime requests from a supplied or active frozen trial."""
    if canonical_requests is None:
        canonical_requests = TRIAL_REQUESTS

    if not canonical_requests:
        raise ValueError("The canonical trial must contain at least one request.")

    reqs = []
    for canonical in canonical_requests:
        a = canonical.pickup_node
        b = canonical.dropoff_node

        reqs.append(
            RideReq(
                id=canonical.request_id,
                pickup_lat=G.nodes[a]["y"],
                pickup_lon=G.nodes[a]["x"],
                drop_lat=G.nodes[b]["y"],
                drop_lon=G.nodes[b]["x"],
                pickup_node=a,
                drop_node=b,
                created_time_s=canonical.arrival_time_s,
                max_wait_s=canonical.max_wait_s,
            )
        )

    return reqs


def generate_trial_arrival_schedule(request_count, *, arrival_seed=None):
    """Generate a reproducible, validated request-arrival schedule."""

    if (
        isinstance(request_count, bool)
        or not isinstance(request_count, int)
        or request_count <= 0
    ):
        raise ValueError(
            "request_count must be a positive integer, such as 30."
        )

    window_error = (
        "ARRIVAL_WINDOW_S must be a finite, non-negative number "
        "of seconds. Use 0 to release all requests at the start."
    )

    if isinstance(ARRIVAL_WINDOW_S, bool):
        raise ValueError(window_error)

    try:
        arrival_window_s = float(ARRIVAL_WINDOW_S)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(window_error) from error

    if not math.isfinite(arrival_window_s) or arrival_window_s < 0.0:
        raise ValueError(window_error)

    # Keep the arrival RNG independent of every other random stream.
    if arrival_seed is None:
        arrival_seed = ARRIVAL_SEED

    rng = random.Random(arrival_seed)

    arrival_times = [
        rng.random() * arrival_window_s
        for _ in range(request_count)
    ]
    arrival_times.sort()

    frozen_arrivals = tuple(
        round(t, 6)
        for t in arrival_times
    )

    logger.info(
        f"[TrialArrival] Frozen {len(frozen_arrivals)} arrivals | "
        f"window={arrival_window_s:.0f}s | "
        f"arrival_seed={arrival_seed}"
    )

    return frozen_arrivals





     
def _hotspot_node_weights():
    weights = {}

    for hotspot in HOTSPOT_DATA:
        raw = hotspot.get("demand_weight")
        if raw is None or pd.isna(raw):
            continue

        weight = float(raw)
        if not math.isfinite(weight):
            raise ValueError("Hotspot weights must be finite.")
        if weight <= 0.0:
            continue

        node = int(hotspot["node"])
        weights[node] = weights.get(node, 0.0) + weight

    if len(weights) < 2:
        raise ValueError("At least two weighted hotspot nodes are required.")

    return dict(sorted(weights.items()))


def _od_marginals(node_w):
    """Reference OD probabilities BEFORE duplicate-pair rejection."""
    total = math.fsum(node_w.values())
    pickup = {n: w / total for n, w in node_w.items()}

    other_weight = {
        i: math.fsum(w for n, w in node_w.items() if n != i)
        for i in node_w
    }

    dropoff = {
        j: math.fsum(
            pickup[i] * (wj / other_weight[i])
            for i in node_w if i != j
        )
        for j, wj in node_w.items()
    }

    return pickup, dropoff

def estimate_direct_pickup_completion_s(path):
    """
    Estimate depot-to-pickup completion time for an empty GMTLSV.

    Uses the same stop-aware motion function and 0.30-second
    simulation time budget as SharedRideEngine._leg().

    Includes boarding dwell because the simulation records
    pickup time when boarding is complete.

    Does not include dispatch queueing or additional stops.
    """
    if not path:
        raise ValueError("Pickup route cannot be empty.")

    lengths = []

    for u, v in zip(path[:-1], path[1:]):
        data = safe_edge_data(u, v)

        if data is None:
            raise RuntimeError(
                f"Missing road segment {u} -> {v}."
            )

        length_m = float(data.get("length", math.nan))

        if not math.isfinite(length_m) or length_m <= 0.0:
            raise ValueError(
                f"Invalid road length for {u} -> {v}."
            )

        lengths.append(length_m)

    remaining_m = math.fsum(lengths)
    speed = 0.0
    travel_time_s = 0.0
    budget_s = float(DT_S)

    if not math.isfinite(budget_s) or budget_s <= 0.0:
        raise ValueError("DT_S must be finite and positive.")

    while remaining_m > 0.0:

        (
            new_speed,
            acceleration,
            traction_force,
            duration_s,
            distance_m,
        ) = calculate_stop_motion_step(
            speed,
            UGV_EMPTY_MASS_KG,
            remaining_m,
            budget_s,
        )

        if duration_s <= 0.0 or distance_m <= 0.0:
            raise RuntimeError(
                "Depot pickup prediction made no progress."
            )

        travel_time_s += duration_s

        remaining_m = max(
            0.0,
            remaining_m - distance_m,
        )

        speed = new_speed
        budget_s -= duration_s

        if budget_s <= 1e-12:
            budget_s = float(DT_S)

    if speed != 0.0:
        raise RuntimeError(
            "Pickup prediction did not finish at rest."
        )

    # Pickup is recorded after boarding is complete.
    return travel_time_s + float(BOARDING_DWELL_S)


def _candidate_tables(parking_candidates, hotspot_nodes):
    if G is None or G.number_of_nodes() == 0:
        raise ValueError("Load the road graph first.")

    if G.is_directed() or not nx.is_connected(G):
        raise ValueError(
            "Depot selection requires a connected undirected graph."
        )

    tables, cache = [], {}

    # Remove repeated coordinates, but retain distinct physical parkings
    # even when they share a snapped road node.
    coordinates = sorted({
        (float(p[0]), float(p[1]))
        for p in parking_candidates
    })

    for lat, lon in coordinates:
        node = int(nearest_node(lon, lat))
        parking = [lat, lon]

        record = get_parking_record_by_coordinates(parking)
        parking_id = record["parking_id"] if record else None
        candidate_id = (
            str(parking_id)
            if parking_id is not None
            else f"node={node};lat={lat:.17g};lon={lon:.17g}"
        )

        # Parkings sharing a node reuse the same routes and travel times.
        if node not in cache:
            dist, paths = nx.single_source_dijkstra(
                G, node, weight="length"
            )

            if any(n not in dist for n in hotspot_nodes):
                raise ValueError(
                    f"Depot node {node} cannot reach every hotspot."
                )

            
            # F-021: Use the same stop-aware motion model
            # and boarding-completion definition as passenger service.
            times = {
                n: estimate_direct_pickup_completion_s(paths[n])
                for n in hotspot_nodes
            }


            cache[node] = dist, times

        dist, times = cache[node]

        snap_m = 1000.0 * haversine(
            lat,
            lon,
            float(G.nodes[node]["y"]),
            float(G.nodes[node]["x"]),
        )

        if snap_m > DEPOT_MAX_SNAP_M:
            logger.warning(
                "[Depot] Excluding %s: snap %.1f m exceeds %.1f m at node %s.",
                candidate_id, snap_m, DEPOT_MAX_SNAP_M, node,
            )
            continue

        tables.append({
            "candidate_id": candidate_id,
            "parking_id": parking_id,
            "parking": parking,
            "node": node,
            "snap_distance_m": snap_m,
            "dist": dist,
            "pickup_time_s": times,
        })

    if not tables:
        raise RuntimeError(
            "No eligible depot parkings remain after the snap-distance limit."
        )
    return tables


def _rank_candidates(tables, node_w):
    pickup, dropoff = _od_marginals(node_w)
    rows = []

    for t in tables:
        dist, times = t["dist"], t["pickup_time_s"]

        pick_m = math.fsum(
            pickup[n] * dist[n] for n in pickup
        )
        return_m = math.fsum(
            dropoff[n] * dist[n] for n in dropoff
        )

        rows.append({
            **{k: t[k] for k in (
                "candidate_id",
                "parking_id",
                "parking",
                "node",
                "snap_distance_m",
            )},
            "expected_pickup_leg_m": pick_m,
            "expected_return_leg_m": return_m,
            "expected_deadhead_m": pick_m + return_m,
            "worst_pickup_distance_m": max(
                dist[n] for n in pickup
            ),
            "worst_pickup_time_s": max(
                times[n] for n in pickup
            ),
            "covered_demand_fraction": math.fsum(
                pickup[n] for n in pickup
                if times[n] <= DEPOT_MAX_PICKUP_TIME_S
            ),
            "half_deadline_coverage_fraction": math.fsum(
                pickup[n] for n in pickup
                if times[n] <= 0.5 * DEPOT_MAX_PICKUP_TIME_S
            ),
        })

    return sorted(rows, key=lambda r: (
        -r["covered_demand_fraction"],
        r["expected_deadhead_m"],
        r["worst_pickup_time_s"],
        r["candidate_id"],
        tuple(r["parking"]),
    ))



def find_common_benchmark_depot(parking_candidates):
    """Select a common depot using the reference OD demand model."""
    if not parking_candidates:
        raise ValueError("No parking candidates available.")

    node_w = _hotspot_node_weights()

    tables = _candidate_tables(
        parking_candidates,
        list(node_w),
    )

    rows = _rank_candidates(tables, node_w)
    best = rows[0]

    # Distance-only lower bound over all road nodes.
    pickup, dropoff = _od_marginals(node_w)
    score = dict.fromkeys(G.nodes, 0.0)

    for n in node_w:
        distances = nx.single_source_dijkstra_path_length(
            G,
            n,
            weight="length",
        )

        for v, distance in distances.items():
            score[v] += (
                pickup[n] + dropoff[n]
            ) * distance

    ideal_node = min(
        score,
        key=lambda v: (score[v], int(v)),
    )

    bound_m = float(score[ideal_node])

    gap_pct = (
        100.0
        * (best["expected_deadhead_m"] - bound_m)
        / bound_m
        if bound_m > 0.0
        else None
    )

    logger.info(
        "[BenchmarkDepot] %s node=%s reference_deadhead=%.1f m "
        "pickup_accessibility=%.3f distance_only_bound=%.1f m",
        best["candidate_id"],
        best["node"],
        best["expected_deadhead_m"],
        best["covered_demand_fraction"],
        bound_m,
    )

    return {
        "parking": best["parking"],
        "cost": best["expected_deadhead_m"],
        "cost_units": "m/reference_request",
        "node": best["node"],
        "parking_id": best["parking_id"],
        "method": DEPOT_SELECTION_METHOD,
        "demand_model": DEPOT_DEMAND_MODEL,
        "max_pickup_time_s": DEPOT_MAX_PICKUP_TIME_S,
        "covered_demand_fraction": best["covered_demand_fraction"],
        "unconstrained_lower_bound_m": bound_m,
        "unconstrained_best_node": int(ideal_node),
        "deadhead_gap_to_unconstrained_bound_percent": gap_pct,
        "candidates": rows,
    }


movers:           Dict[int, Mover]          = {}
exec_q:           Dict[int, deque]          = {}
trip_record:      Dict[int, dict]           = {}
serving:          Dict[int, Optional[int]]  = {}
phase:            Dict[int, str]            = {}
requests_waiting: Dict[int, RideReq]        = {}
requests_inflight: Dict[int, RideReq]       = {}
assignment_meta:  Dict[int, dict]           = {}
tick = 0
sockets: List[WebSocket] = []

def get_runtime_benchmark_depot():
    """Return the common depot coordinates: [latitude, longitude]."""
    if BENCHMARK_DEPOT is None:
        raise RuntimeError(
            "BENCHMARK_DEPOT has not been initialized."
        )

    return BENCHMARK_DEPOT["parking"]


def get_runtime_benchmark_depot_node():
    """Return the common depot's road-network node."""
    if BENCHMARK_DEPOT is None:
        raise RuntimeError(
            "BENCHMARK_DEPOT has not been initialized."
        )

    return int(BENCHMARK_DEPOT["node"])

def get_depot_allocation(fleet_size):
    """Deterministic home-depot allocation, independent of trial demand."""
    if type(fleet_size) is not int or fleet_size < 1:
        raise ValueError("Fleet size must be positive.")

    if BENCHMARK_DEPOT is None:
        raise RuntimeError("Benchmark depot not initialized.")

    if DEPOT_LAYOUT == "single":
        return [BENCHMARK_DEPOT] * fleet_size

    if DEPOT_LAYOUT == "three":
        if fleet_size < 3:
            raise ValueError(
                "Three depots require at least three vehicles."
            )

        candidates = {
            str(row["parking_id"]): row
            for row in BENCHMARK_DEPOT["candidates"]
        }

        missing = set(THREE_DEPOT_IDS) - set(candidates)
        if missing:
            raise RuntimeError(
                f"Three-depot parking IDs missing: {sorted(missing)}"
            )

        # Deterministic balanced allocation.
        # For S1/S4, 12 vehicles -> 4 per depot.
        # S2: 18 -> 6 each; S3: 24 -> 8 each.
        base, remainder = divmod(fleet_size, 3)

        return [
            candidates[pid]
            for index, pid in enumerate(THREE_DEPOT_IDS)
            for _ in range(base + int(index < remainder))
        ]

    if DEPOT_LAYOUT != "two" or fleet_size < 2:
        raise ValueError("Two depots require at least two vehicles.")

    candidates = {
        str(row["parking_id"]): row
        for row in BENCHMARK_DEPOT["candidates"]
    }

    first, second = TWO_DEPOT_IDS

    if not {first, second}.issubset(candidates):
        raise RuntimeError(
            f"Selected depots {first}/{second} not registered."
        )

    # Preserve the original proportional split.
    # For S1, first depot gets 5 and second gets 7.
    count_first = max(
        1, min(fleet_size - 1, round(fleet_size * 5 / 12))
    )

    return (
        [candidates[first]] * count_first
        + [candidates[second]] * (fleet_size - count_first)
    )


def vehicle_home_node(vehicle):
    if vehicle.home_depot_node is None:
        raise RuntimeError(
            f"Vehicle {vehicle.id} has no registered home depot."
        )
    return int(vehicle.home_depot_node)


def _v2_active_depots():
    counts = {}

    for entry in get_depot_allocation(
        EXPERIMENT_CONFIG[CURRENT_SCENARIO]["vehicles"]
    ):
        pid = str(entry["parking_id"])

        if pid not in counts:
            counts[pid] = {
                "parking_id": pid,
                "parking": list(entry["parking"]),
                "node": int(entry["node"]),
                "vehicle_count": 0,
            }

        counts[pid]["vehicle_count"] += 1

    return list(counts.values())


def get_benchmark_parking_payload():
    reference = get_runtime_benchmark_depot()
    depot = BENCHMARK_DEPOT
    single = DEPOT_LAYOUT == "single"

    sites = _v2_active_depots()

    # Legacy clients display the first ACTUAL depot.
    # Multi-depot clients should display every benchmark_depots entry.
    marker = reference if single else sites[0]["parking"]
    marker_id = (
        depot["parking_id"]
        if single else sites[0]["parking_id"]
    )

    return {
        "depot_layout": DEPOT_LAYOUT,
        "benchmark_depots": sites,
        "legacy_marker_is_partial": not single,
        "reference_single_depot": reference,
        "reference_single_depot_id": depot["parking_id"],
        "benchmark_depot": marker,
        "benchmark_depot_expected_deadhead_m": (
            float(depot["cost"]) if single else None
        ),
        "depot_selection_method": (
            depot["method"] if single
            else "fixed_registered_parking_ids_manual"
        ),
        "depot_demand_model": (
            depot["demand_model"] if single
            else "not_used_for_fixed_multi_depot_selection"
        ),
        "optimized_parking_id": marker_id,
        "optimized_parking": marker,
        "optimized_parking_lat": marker[0],
        "optimized_parking_lon": marker[1],
        "parking_cost": (
            float(depot["cost"]) if single else None
        ),
        "parking_cost_units": (
            depot["cost_units"] if single else None
        ),
    }



# ── Path helpers ──────────────────────────────────────────────────
def edge_time_s(length_m, speed_kph):
    if speed_kph <= 0:
        speed_kph = SPEED_KPH

    return length_m / (speed_kph * 1000 / 3600.0)

# ============================================================
# Energy Model Parameters
# ============================================================

# Obsolete energy coefficients removed. Active traction and
# battery-withdrawal calculations remain unchanged.

# ============================================================
# Path Time and Predicted Energy Calculation
# ============================================================
def calculate_driving_energy_Wh(traction_force_N, distance_m, duration_s):
    """Battery energy for traction and auxiliary loads; no regeneration."""
    traction_force_N = float(traction_force_N)
    distance_m = float(distance_m)
    duration_s = float(duration_s)

    if not all(map(math.isfinite, (
        traction_force_N, distance_m, duration_s,
        UGV_ETA, UGV_DRIVING_AUX_POWER_W,
    ))):
        raise ValueError("Driving-energy inputs must be finite.")

    if distance_m < 0.0 or duration_s < 0.0:
        raise ValueError("Distance and duration must be non-negative.")

    if not 0.0 < UGV_ETA <= 1.0:
        raise ValueError("UGV_ETA must be greater than 0 and at most 1.")

    if UGV_DRIVING_AUX_POWER_W < 0.0:
        raise ValueError("Driving auxiliary power must be non-negative.")

    traction_energy_Wh = (
        max(0.0, traction_force_N) * distance_m
        / (UGV_ETA * 3600.0)
    )
    auxiliary_energy_Wh = (
        UGV_DRIVING_AUX_POWER_W * duration_s / 3600.0
    )

    return traction_energy_Wh + auxiliary_energy_Wh


_PATH_CACHE = {}
_PATH_CACHE_GRAPH = None
_ROUTE_TIME_ENERGY_CACHE = {}


def clear_path_cache():
    """Clear both route caches after a reset or an in-place graph change."""
    global _PATH_CACHE_GRAPH

    _PATH_CACHE.clear()
    _ROUTE_TIME_ENERGY_CACHE.clear()
    _PATH_CACHE_GRAPH = G


def shortest_path(src, dst):
    # A replacement graph must not reuse paths from the previous graph.
    if G is not _PATH_CACHE_GRAPH:
        clear_path_cache()

    key = (src, dst)

    if key not in _PATH_CACHE:
        try:
            path = nx.shortest_path(G, src, dst, weight="length")
        except nx.NetworkXNoPath:
            logger.warning(
                "[Graph] No path between %s and %s", src, dst
            )
            path = []

        # Protect the cached route from accidental modification.
        _PATH_CACHE[key] = tuple(path)

    # Callers receive their own list, as before.
    return list(_PATH_CACHE[key])


# Provisional modelling assumptions, not measured vehicle specifications.
BOARDING_DWELL_S = 10.0
ALIGHTING_DWELL_S = 5.0
# Use the same auxiliary operating mode during boarding and alighting.
DWELL_AUX_POWER_W = UGV_DRIVING_AUX_POWER_W
MAX_RIDE_FACTOR = 2.0
MAX_EXTRA_RIDE_S = 300.0
# Assignment cost scaling.
# Keep the existing formula until normalized mode is explicitly selected.
ASSIGNMENT_COST_MODE = "normalized"

# Fixed reference scales for normalized costs.
# These are provisional reference quantities, not measured optimal values.
COST_ENERGY_REFERENCE_WH = 100.0
COST_TIME_REFERENCE_S = 600.0
def assignment_cost_terms(
    delta_energy_wh,
    delta_time_s,
    soc_fraction,
    alpha,
    beta,
    gamma,
):
    """Return the weighted energy, time and SOC contributions."""
    if ASSIGNMENT_COST_MODE == "raw":
        energy_term = alpha * delta_energy_wh
        time_term = beta * delta_time_s

    elif ASSIGNMENT_COST_MODE == "normalized":
        energy_scale = float(COST_ENERGY_REFERENCE_WH)
        time_scale = float(COST_TIME_REFERENCE_S)

        if (
            not math.isfinite(energy_scale)
            or energy_scale <= 0.0
            or not math.isfinite(time_scale)
            or time_scale <= 0.0
        ):
            raise ValueError(
                "Cost reference scales must be finite and positive."
            )

        energy_term = alpha * delta_energy_wh / energy_scale
        time_term = beta * delta_time_s / time_scale

    else:
        raise ValueError(
            f"Unknown assignment cost mode: {ASSIGNMENT_COST_MODE!r}"
        )

    # SOC deficit is already dimensionless.
    soc_term = gamma * (1.0 - soc_fraction)

    return energy_term, time_term, soc_term
LEG_CACHE_SIZE = 8192

def get_pooling_model_info():
    """Build metadata from the settings currently used by the engine."""
    terms = [
        name for name, weight in (
            ("time", BETA_TIME),
            ("energy", ALPHA_ENERGY),
            ("soc", SOC_COST_WEIGHT),
        )
        if weight > 0.0
    ]

    return {
        "version": 2,
        "dispatch_policy_version": 1,
        # The dispatch policy is unchanged; version the speed policy separately.
        "speed_policy_version": SPEED_POLICY_VERSION,
        "maximum_speed_kph": float(SPEED_KPH),
        "manufacturer_low_speed_mode_kph": float(MANUFACTURER_LOW_SPEED_MODE_KPH),
        "campus_operating_speed_cap_kph": float(CAMPUS_OPERATING_SPEED_CAP_KPH),
        "campus_target_average_moving_speed_kph": float(CAMPUS_TARGET_AVERAGE_MOVING_SPEED_KPH),
        "maximum_speed_m_s": float(UGV_V_MAX),
        "speed_is_upper_bound_not_constant": True,
        "speed_basis": "manufacturer_low_mode_with_campus_operating_cap",
        "dispatch_runtime_definition": (
            "Wall-clock duration of active dispatch() operations, "
            "including cost matrix construction, solver execution, "
            "assignment activation, maximum-matching diagnostics, "
            "and dispatch diagnostic processing. "
            "Calls without an eligible cost-matrix attempt are excluded. "
            "External sensing, communication, physical vehicle "
            "control and final file export are not included."
        ),
        "matching_cardinality_policy": (
            "diagnostic_only: exact maximum feasible count logged; "
            "baseline matching outputs not altered"
        ),
        "dispatch_window_s": float(DISPATCH_WINDOW_S),
        "dispatch_urgent_slack_s": float(DISPATCH_URGENT_SLACK_S),
        "dispatch_schedule": (
            "every_tick"
            if DISPATCH_WINDOW_S == 0.0
            else "fixed_simulation_time_boundaries"
        ),
        "dispatch_boundary_rounding": "first_tick_at_or_after_boundary",
        "dispatch_urgent_policy": (
            "disabled"
            if DISPATCH_WINDOW_S == 0.0 or DISPATCH_URGENT_SLACK_S == 0.0
            else "once_per_waiting_request_on_entering_deadline_slack"
        ),
        "source_sha256": BACKEND_SOURCE_INFO["sha256"],
        "passengers_per_request": 1,
        "boarding_dwell_s": BOARDING_DWELL_S,
        "alighting_dwell_s": ALIGHTING_DWELL_S,
        "dwell_aux_power_w": DWELL_AUX_POWER_W,
        "max_ride_factor": MAX_RIDE_FACTOR,
        "max_extra_ride_s": MAX_EXTRA_RIDE_S,
        "alpha_energy": float(ALPHA_ENERGY),
        "beta_time": float(BETA_TIME),
        "soc_cost_weight": float(SOC_COST_WEIGHT),
        "assignment_cost_mode": ASSIGNMENT_COST_MODE,
        "energy_reference_Wh": float(COST_ENERGY_REFERENCE_WH),
        "time_reference_s": float(COST_TIME_REFERENCE_S),
        "soc_weight_convention": (
            "dimensionless coefficient on SOC deficit"
            if ASSIGNMENT_COST_MODE == "normalized"
            else "legacy raw-cost coefficient on SOC deficit"
        ),
        "cost_variant": "_plus_".join(terms) if terms else "zero_cost",
        "energy_feasibility": "remaining_plan_energy_plus_reserve",
        "battery_reserve_fraction": float(BATTERY_RESERVE_FRACTION),
        "insertion_policy": "preserve stop order; current service destination locked",
        "return_policy": "return after all stops; returning vehicles finish at depot",
        "max_active_requests": "vehicle passenger capacity",
        "pickup_timestamp": "boarding complete",
        "dropoff_timestamp": "alighting complete",
        "cost": (
            (
                "alpha * incremental route Wh / energy_reference_Wh"
                " + beta * incremental route seconds / time_reference_s"
                " + gamma * (1 - SOC / capacity)"
            )
            if ASSIGNMENT_COST_MODE == "normalized"
            else (
                "alpha * incremental route Wh"
                " + beta * incremental route seconds"
                " + gamma * (1 - SOC / capacity)"
            )
        ),
        "energy_accounting": "vehicle totals; individual shared-trip energy left blank",
        "velocity_log_semantics": (
            "Every third tick when enabled. sampled_tick_energy_Wh covers "
            "only the logged DT_S step, not the three-tick interval. "
            "Never sum sparse samples as total battery consumption."
        ),
        "request_distance": (
            "distance onboard only; shared passenger distances are not fleet distance"
        ),
        "assumptions": (
            "dwell and ride limits are provisional; flat roads; no regeneration"
        ),
    }


@dataclass(frozen=True)
class Motion:
    duration: float
    speed: float
    acceleration: float
    force: float
    distance: float
    final_speed: float


@dataclass(frozen=True)
class Leg:
    nodes: tuple
    mass: float
    distance: float
    duration: float
    energy: float
    phases: tuple


@dataclass
class VehicleState:
    leg: object = None
    target: object = None
    phase_index: int = 0
    phase_elapsed: float = 0.0
    leg_elapsed: float = 0.0
    leg_energy: float = 0.0
    dwell_remaining: object = None
    acceleration: float = 0.0


class SharedRideEngine:
    def __init__(self, backend):
        self.b = backend
        self.run_id = backend.RUN["run_id"]
        self.states = {vid: VehicleState() for vid in backend.movers}
        self.leg_cache = OrderedDict()
        self.graph = backend.G
        self.events = []
        self.vehicle_soc_snapshots = []
        self.stop_events = []
        self.plan_events = []
        # F-031: Completed vehicle-leg energy audit trail.
        self.leg_execution_events = []
        self.timing_events = []
        # F-037B: One record per complete dispatch invocation.
        self.dispatch_invocation_events = []
        self.dispatch_invocation_counter = 0

        # F-019: Matching-cardinality diagnostics
        self.matching_round_count = 0
        self.matching_selected_pairs_total = 0
        self.matching_maximum_pairs_total = 0
        self.matching_cardinality_gap_total = 0
        self.matching_cardinality_gap_rounds = 0

        self.cost_diagnostic_events = []
        self.wait_diagnostic_stats = {}
        self.wait_diagnostic_rows = []
        self.wait_diagnostic_closed = set()
        self.velocity_events = []
        self.record_velocity = True
        self._dispatch_window_slot = -1
        self._urgent_dispatch_request_ids = set()

        window = float(backend.DISPATCH_WINDOW_S)
        slack = float(backend.DISPATCH_URGENT_SLACK_S)

        if not math.isfinite(window) or window < 0.0:
            raise ValueError(
                "DISPATCH_WINDOW_S must be finite and non-negative."
            )

        if not math.isfinite(slack) or not 0.0 <= slack < backend.MAX_WAIT_S:
            raise ValueError(
                "Require 0 <= DISPATCH_URGENT_SLACK_S < MAX_WAIT_S."
            )

        self.pair_evaluations = 0
        self.feasible_pair_evaluations = 0
        self.energy_blocked_pair_evaluations = 0
        self.non_energy_feasible_insertions = 0
        self.energy_blocked_insertions = 0
        self.energy_blocked_pairs = set()
        self.driving_Wh = 0.0
        self.dwell_Wh = 0.0
        self.dwell_s = 0.0
        self.initial_soc = {
            vid: float(m.soc_Wh)
            for vid, m in backend.movers.items()
        }
        self.active = {vid: set() for vid in backend.movers}
        self.direct_time = {}
        self.ride_deadline = {}
        self._output_ready = False
        self.parameters = self._parameters()

        for value in (
            BOARDING_DWELL_S,
            ALIGHTING_DWELL_S,
            DWELL_AUX_POWER_W,
            MAX_RIDE_FACTOR,
            MAX_EXTRA_RIDE_S,
            SOC_COST_WEIGHT,
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    "Shared-ride settings must be finite and non-negative."
                )

        if backend.DT_S <= 0.0 or MAX_RIDE_FACTOR < 1.0:
            raise ValueError(
                "Require positive DT_S and MAX_RIDE_FACTOR >= 1."
            )

    def _parameters(self):
        b = self.b
        return tuple(getattr(b, name) for name in (
            "DT_S",
            "MAX_WAIT_S",
            "DISPATCH_WINDOW_S",
            "DISPATCH_URGENT_SLACK_S",
            "DEPOT_LAYOUT",
            "UGV_EMPTY_MASS_KG",
            "UGV_PASSENGER_MASS_KG",
            "UGV_PASSENGER_CAPACITY",
            "UGV_G",
            "UGV_C_RR",
            "UGV_RHO",
            "UGV_C_D",
            "UGV_A",
            "UGV_BRAKE_MAX",
            "UGV_ACCEL_MAX",
            "UGV_V_MAX",
            "UGV_ETA",
            "UGV_GEAR_RATIO",
            "UGV_T_MOTOR_MAX",
            "UGV_MOTOR_SHAFT_POWER_W",
            "UGV_GEAR_EFFICIENCY",
            "UGV_ROTATIONAL_MASS_FACTOR",
            "UGV_WHEEL_RADIUS",
            "UGV_DRIVING_AUX_POWER_W",
            "UGV_IDLE_POWER_W",
            "BATTERY_CAPACITY_WH",
            "BATTERY_RESERVE_FRACTION",
            "ALPHA_ENERGY", "BETA_TIME", "SOC_COST_WEIGHT",
            "ASSIGNMENT_COST_MODE",
            "COST_ENERGY_REFERENCE_WH", "COST_TIME_REFERENCE_S",
            "BOARDING_DWELL_S", "ALIGHTING_DWELL_S",
            "DWELL_AUX_POWER_W", "MAX_RIDE_FACTOR", "MAX_EXTRA_RIDE_S",
            "CHARGER_DC_POWER_W", "CHARGER_EFFICIENCY",
            "BATTERY_CHARGE_EFFICIENCY",
            "CHARGE_TAPER_START_SOC", "CHARGE_TAPER_END_FRACTION",
            "PLUGGED_POWER_POLICY", "PLUGGED_SLEEP_POWER_W",
            "DISPATCH_BUDGET_MS",
        ))

    def _leg(self, src, dst, mass):
        b = self.b
        key = (src, dst, float(mass))

        if key in self.leg_cache:
            self.leg_cache.move_to_end(key)
            return self.leg_cache[key]

        nodes = b.shortest_path(src, dst)
        if not nodes:
            return None

        lengths = []

        for u, v in zip(nodes, nodes[1:]):
            data = b.safe_edge_data(u, v)
            length = float(data["length"]) if data else math.nan

            if not math.isfinite(length) or length <= 0:
                raise ValueError(
                    f"Invalid road length on {u} -> {v}."
                )

            lengths.append(length)

        distance = math.fsum(lengths)
        remaining = distance
        speed = 0.0
        duration = 0.0
        energy = 0.0
        phases = []
        budget = float(b.DT_S)

        # Parameters are fixed within this simulated leg.
        fast_eta = float(b.UGV_ETA)
        fast_aux_w = float(b.UGV_DRIVING_AUX_POWER_W)
        fast_energy_valid = (
            math.isfinite(fast_eta)
            and math.isfinite(fast_aux_w)
            and 0.0 < fast_eta <= 1.0
            and fast_aux_w >= 0.0
        )
        fast_energy_denominator = fast_eta * 3600.0

        # Cache a steady-cruise force only after the original
        # motion controller has validated it for this leg.
        cruise_force = None
        cruise_braking_m = None

        while remaining > 0.0:
            # FIX-16 CANDIDATE: consolidate consecutive steady-cruise
            # phase updates while retaining each original arithmetic step.
            # The normal path below handles all other conditions.
            if (
                cruise_force is not None
                and speed == b.UGV_V_MAX
                and fast_energy_valid
                and math.isfinite(cruise_force)
                and phases
                and phases[-1].acceleration == 0.0
                and phases[-1].force == cruise_force
                and phases[-1].final_speed == speed
            ):
                previous_phase = phases[-1]
                merged_duration = previous_phase.duration
                merged_distance = previous_phase.distance
                cruise_steps = 0
                positive_force = max(0.0, float(cruise_force))

                # FIX-17 CANDIDATE: Reuse validated step arithmetic
                # only when the cruise time budget is unchanged.
                # Accumulate energy once per original simulated step.
                cached_budget = None
                cached_ds = None
                cached_step_energy = None

                while remaining > (
                    cruise_braking_m
                    + 1e-9 * max(
                        1.0, remaining, cruise_braking_m
                    )
                    + speed * budget
                    + 1e-6
                ):
                    dt = budget

                    if cached_budget != budget:
                        ds = 0.5 * (speed + speed) * budget

                        # Preserve the original invalid-input path.
                        if (
                            not math.isfinite(dt)
                            or not math.isfinite(ds)
                            or dt <= 0.0
                            or ds <= 0.0
                        ):
                            break

                        traction_energy_Wh = (
                            positive_force * float(ds)
                            / fast_energy_denominator
                        )
                        auxiliary_energy_Wh = (
                            fast_aux_w * float(dt) / 3600.0
                        )

                        cached_budget = budget
                        cached_ds = ds
                        cached_step_energy = (
                            traction_energy_Wh
                            + auxiliary_energy_Wh
                        )
                    else:
                        ds = cached_ds

                    energy += cached_step_energy

                    # Preserve the original addition order, once per step.
                    merged_duration += dt
                    merged_distance += ds
                    duration += dt
                    remaining = max(0.0, remaining - ds)
                    budget -= dt

                    if budget <= 1e-12:
                        budget = float(b.DT_S)

                    cruise_steps += 1

                if cruise_steps:
                    phases[-1] = Motion(
                        merged_duration,
                        previous_phase.speed,
                        0.0,
                        cruise_force,
                        merged_distance,
                        speed,
                    )
                    continue
            if (
                cruise_force is not None
                and speed == b.UGV_V_MAX
                and remaining > (
                    cruise_braking_m
                    + 1e-9 * max(
                        1.0, remaining, cruise_braking_m
                    )
                    + speed * budget
                    + 1e-6
                )
            ):
                v1 = speed
                acceleration = 0.0
                force = cruise_force
                dt = budget
                ds = 0.5 * (speed + speed) * budget
            else:
                v1, acceleration, force, dt, ds = (
                    b.calculate_stop_motion_step(
                        speed, mass, remaining, budget
                    )
                )

                if (
                    cruise_force is None
                    and speed == b.UGV_V_MAX
                    and v1 == speed
                    and acceleration == 0.0
                    and dt == budget
                    and ds == 0.5 * (speed + speed) * budget
                ):
                    braking_m = (
                        speed * speed
                        / (2.0 * float(b.UGV_BRAKE_MAX))
                    )

                    if remaining > (
                        braking_m
                        + 1e-9 * max(
                            1.0, remaining, braking_m
                        )
                        + speed * budget
                        + 1e-6
                    ):
                        cruise_force = force
                        cruise_braking_m = braking_m

            if dt <= 0.0 or ds <= 0.0:
                raise RuntimeError(
                    "Route prediction made no progress."
                )

            # Same formula and arithmetic order as the original
            # helper for finite, positive-distance motion steps.
            # Preserve the original validation/error path otherwise.
            if (
                fast_energy_valid
                and math.isfinite(force)
                and math.isfinite(ds)
                and math.isfinite(dt)
                and ds >= 0.0
                and dt >= 0.0
            ):
                traction_energy_Wh = (
                    max(0.0, float(force)) * float(ds)
                    / fast_energy_denominator
                )
                auxiliary_energy_Wh = (
                    fast_aux_w * float(dt) / 3600.0
                )
                energy += traction_energy_Wh + auxiliary_energy_Wh
            else:
                energy += b.calculate_driving_energy_Wh(
                    force, ds, dt
                )
            motion = Motion(dt, speed, acceleration, force, ds, v1)

            # Merge consecutive constant-speed phases.
            if (
                phases
                and acceleration == 0.0
                and phases[-1].acceleration == 0.0
                and phases[-1].force == force
                and phases[-1].final_speed == speed
            ):
                old = phases[-1]
                phases[-1] = Motion(
                    old.duration + dt,
                    old.speed,
                    0.0,
                    force,
                    old.distance + ds,
                    v1,
                )
            else:
                phases.append(motion)

            duration += dt
            remaining = max(0.0, remaining - ds)
            speed = v1
            budget -= dt

            if budget <= 1e-12:
                budget = float(b.DT_S)

        if speed != 0.0:
            raise RuntimeError(
                "Predicted leg did not finish at rest."
            )

        leg = Leg(
            tuple(nodes),
            float(mass),
            distance,
            duration,
            energy,
            tuple(phases),
        )

        self.leg_cache[key] = leg

        if len(self.leg_cache) > LEG_CACHE_SIZE:
            self.leg_cache.popitem(last=False)

        return leg

    @staticmethod
    def _dwell(stop):
        if stop.kind == "pickup":
            return BOARDING_DWELL_S
        return ALIGHTING_DWELL_S

    def _predict(self, m, stops):
        """Predict all remaining stops and the final return to depot."""
        b = self.b
        state = self.states[m.id]
        masses = b.stop_plan_masses(m, stops)
        elapsed = 0.0
        energy = 0.0
        pickups = {}
        drops = {}
        source = m.node

        for index, stop in enumerate(stops):
            if index == 0 and state.leg is not None:
                if stop != state.target:
                    raise ValueError(
                        "Cannot replace the current service destination."
                    )

                leg = state.leg
                travel_s = max(
                    0.0, leg.duration - state.leg_elapsed
                )
                travel_e = max(
                    0.0, leg.energy - state.leg_energy
                )
                dwell = (
                    state.dwell_remaining
                    if state.dwell_remaining is not None
                    else self._dwell(stop)
                )
            else:
                leg = self._leg(source, stop.node, masses[index])

                if leg is None:
                    return None

                travel_s = leg.duration
                travel_e = leg.energy
                dwell = self._dwell(stop)

            elapsed += travel_s + dwell
            energy += travel_e + DWELL_AUX_POWER_W * dwell / 3600.0

            if stop.kind == "pickup":
                pickups[stop.request_id] = elapsed
            else:
                drops[stop.request_id] = elapsed

            source = stop.node

        home = self._leg(
            source,
            b.vehicle_home_node(m),
            masses[-1],
        )

        if home is None:
            return None

        return {
            "duration": elapsed + home.duration,
            "energy": energy + home.energy,
            "pickups": pickups,
            "drops": drops,
        }

    def eligible(self, m):
        state = self.states[m.id]

        return (
            len(self.active[m.id]) < self.b.UGV_PASSENGER_CAPACITY
            and not (
                state.leg is not None
                and state.target is None
            )
        )

    def _wait_diagnostics(self, request_id):
        if request_id not in self.wait_diagnostic_stats:
            self.wait_diagnostic_stats[request_id] = dict.fromkeys((
                "dispatch_rounds",
                "no_eligible_vehicle_rounds",
                "feasible_not_selected_rounds",
                "pair_evaluations",
                "ineligible_or_active_pairs",
                "baseline_unavailable_pairs",
                "direct_leg_unavailable_pairs",
                "early_pickup_deadline_rejected_pairs",
                "geographic_pickup_deadline_rejected_pairs",
                "feasible_pair_evaluations",
                "energy_blocked_pair_evaluations",
                "insertion_evaluations",
                "capacity_or_order_rejected_insertions",
                "prediction_unavailable_insertions",
                "pickup_deadline_rejected_insertions",
                "ride_limit_rejected_insertions",
                "otherwise_feasible_insertions",
                "energy_rejected_insertions",
            ), 0)

        return self.wait_diagnostic_stats[request_id]

    def _close_wait_diagnostics(self, request_id, exit_kind):
        if request_id in self.wait_diagnostic_closed:
            return

        record = self.b.RUN["request_records"][request_id]
        stats = self._wait_diagnostics(request_id)

        self.wait_diagnostic_rows.append({
            "schema_version": 3,
            "run_id": self.run_id,
            "request_id": request_id,
            "waiting_exit": exit_kind,
            "created_time_s": record["created_time_s"],
            "waiting_exit_time_s": self.b.simulation_time_s(),
            "terminal_failure_reason": (
                record.get("failure_reason", "")
                if exit_kind == "expired" else ""
            ),
            "had_feasible_pair": int(
                stats["feasible_pair_evaluations"] > 0
            ),
            "had_energy_blocked_pair": int(
                stats["energy_blocked_pair_evaluations"] > 0
            ),
            "unique_energy_blocked_vehicles": sum(
                rid == request_id
                for _, rid in self.energy_blocked_pairs
            ),
            **stats,
        })

        self.wait_diagnostic_closed.add(request_id)

    
    def pair_cost(self, m, request, alpha, beta):
        b = self.b
        self.pair_evaluations += 1
        diag = self._wait_diagnostics(request.id)
        diag["pair_evaluations"] += 1
        state = self.states[m.id]

        
        if not self.eligible(m) or request.id in self.active[m.id]:
            diag["ineligible_or_active_pairs"] += 1
            return math.inf, None

        # F-039E: Optimistic pickup-time lower bound.
        # A vehicle already travelling cannot insert a new
        # pickup before completing its committed leg.
        if state.leg is not None:
            remaining_leg_s = max(
                0.0,
                state.leg.duration - state.leg_elapsed,
            )

            earliest_possible_pickup_s = (
                b.simulation_time_s() + remaining_leg_s
            )

            if b.pickup_deadline_exceeded(
                b.RUN["request_records"][request.id],
                earliest_possible_pickup_s,
            ):
                diag["early_pickup_deadline_rejected_pairs"] += 1
                return math.inf, None

        
        # F-040E: Geographic pickup-time lower bound.

        # Ignore acceleration, braking, and intermediate stops
        # so this estimate remains optimistically fast.
        if state.leg is not None:
            committed_s = max(
                0.0,
                state.leg.duration - state.leg_elapsed,
            )

            # A moving vehicle must finish its current leg.
            if state.target is not None:
                anchor_node = state.target.node
            else:
                # A return-to-depot leg has no service target.
                anchor_node = (
                    b.vehicle_home_node(m)
                )
        else:
            committed_s = 0.0
            anchor_node = m.node

        anchor_data = b.G.nodes[anchor_node]
        pickup_data = b.G.nodes[int(request.pickup_node)]

        straight_distance_m = 1000.0 * b.haversine(
            float(anchor_data["y"]),
            float(anchor_data["x"]),
            float(pickup_data["y"]),
            float(pickup_data["x"]),
        )

        if (
            not math.isfinite(straight_distance_m)
            or straight_distance_m < 0.0
        ):
            raise ValueError(
                "Invalid geographic pickup distance."
            )

        minimum_pickup_travel_s = (
            straight_distance_m / b.UGV_V_MAX
        )

        optimistic_pickup_s = (
            b.simulation_time_s()
            + committed_s
            + minimum_pickup_travel_s
        )

        # Small numerical safety margin avoids rejecting
        # borderline cases due to floating-point rounding.
        if b.pickup_deadline_exceeded(
            b.RUN["request_records"][request.id],
            optimistic_pickup_s - 1e-6,
        ):
            diag[
                "geographic_pickup_deadline_rejected_pairs"
            ] += 1
            return math.inf, None
        
        old = list(m.planned_stops)
        baseline = self._predict(m, old)

        if baseline is None:
            diag["baseline_unavailable_pairs"] += 1
            return math.inf, None

        pickup = b.PassengerStop(
            kind="pickup",
            request_id=request.id,
            node=int(request.pickup_node),
        )
        drop = b.PassengerStop(
            kind="dropoff",
            request_id=request.id,
            node=int(request.drop_node),
        )

        direct = self._leg(
            request.pickup_node,
            request.drop_node,
            b.UGV_EMPTY_MASS_KG + b.UGV_PASSENGER_MASS_KG,
        )

        if direct is None:
            diag["direct_leg_unavailable_pairs"] += 1
            return math.inf, None

        now = b.simulation_time_s()
        best = None
        has_non_energy_feasible_plan = False

        # Preserve the current destination while moving or dwelling.
        first = 1 if state.leg is not None else 0

        for i in range(first, len(old) + 1):
            for j in range(i + 1, len(old) + 2):
                diag["insertion_evaluations"] += 1
                stops = old[:]
                stops.insert(i, pickup)
                stops.insert(j, drop)

                try:
                    b.stop_plan_masses(m, stops)
                except ValueError:
                    diag["capacity_or_order_rejected_insertions"] += 1
                    continue

                prediction = self._predict(m, stops)

                if prediction is None:
                    diag["prediction_unavailable_insertions"] += 1
                    continue

                feasible = True

                for rid, t in prediction["pickups"].items():
                    if b.pickup_deadline_exceeded(
                        b.RUN["request_records"][rid],
                        now + t,
                    ):
                        diag["pickup_deadline_rejected_insertions"] += 1
                        feasible = False
                        break

                # A missed pickup deadline already rejects this insertion.
                if not feasible:
                    continue

                for rid, t in prediction["drops"].items():
                    if rid in m.onboard_request_ids:
                        limit = self.ride_deadline[rid]
                    else:
                        direct_s = (
                            direct.duration
                            if rid == request.id
                            else self.direct_time[rid]
                        )
                        limit = (
                            now
                            + prediction["pickups"][rid]
                            + MAX_RIDE_FACTOR * direct_s
                            + MAX_EXTRA_RIDE_S
                        )

                    if now + t > limit + 1e-8:
                        diag["ride_limit_rejected_insertions"] += 1
                        feasible = False
                        break

                reserve = (
                    b.BATTERY_CAPACITY_WH
                    * b.BATTERY_RESERVE_FRACTION
                )
                required = prediction["energy"] + reserve

                if not feasible:
                    continue

                has_non_energy_feasible_plan = True
                self.non_energy_feasible_insertions += 1
                diag["otherwise_feasible_insertions"] += 1

                if required > float(m.soc_Wh) + 1e-9:
                    self.energy_blocked_insertions += 1
                    diag["energy_rejected_insertions"] += 1
                    continue

                delta_e = max(
                    0.0,
                    prediction["energy"] - baseline["energy"],
                )
                delta_t = max(
                    0.0,
                    prediction["duration"] - baseline["duration"],
                )

                energy_term, time_term, soc_term = (
                    b.assignment_cost_terms(
                        delta_energy_wh=delta_e,
                        delta_time_s=delta_t,
                        soc_fraction=(
                            float(m.soc_Wh) / b.BATTERY_CAPACITY_WH
                        ),
                        alpha=alpha,
                        beta=beta,
                        gamma=b.SOC_COST_WEIGHT,
                    )
                )

                cost = energy_term + time_term + soc_term

                rank = (
                    cost,
                    prediction["pickups"][request.id],
                    i,
                    j,
                )

                if best is None or rank < best[0]:
                    best = (
                        rank,
                        {
                            "stops": tuple(stops),
                            "prediction": prediction,
                            "t_pick": prediction["pickups"][request.id],
                            "required_energy_Wh": required,
                            "delta_energy": delta_e,
                            "delta_time": delta_t,
                            "direct_time": direct.duration,
                        },
                    )

        if best is None:
            if has_non_energy_feasible_plan:
                self.energy_blocked_pair_evaluations += 1
                self.energy_blocked_pairs.add((m.id, request.id))
                diag["energy_blocked_pair_evaluations"] += 1

            return math.inf, None

        self.feasible_pair_evaluations += 1
        diag["feasible_pair_evaluations"] += 1
        return best[0][0], best[1]

    def _fleet_event(
        self, kind, m=None, rid=None, cost=None, runtime=None
    ):
        b = self.b
        active = sum(
            v.status != "idle"
            for v in b.movers.values()
        )

        self.events.append([
            b.TRIAL_SEED,
            b.tick,
            kind,
            b.ASSIGNMENT_METHOD,
            m.id if m else None,
            rid,
            cost,
            runtime,
            b.fulfilled_requests,
            len(b.requests_waiting) + len(b.requests_inflight),
            b.total_travel_time,
            b.total_energy_consumed,
            b.total_idle_time,
            100.0 * active / len(b.movers) if b.movers else None,
        ])

        # V2 per-vehicle SOC observations at existing snapshot ticks.
        # Reporting only; never modifies vehicle state.
        if kind == "SNAPSHOT":
            snapshot_s = b.simulation_time_s()

            for vehicle_id, vehicle in sorted(b.movers.items()):
                ledger = b.RUN["vehicle_records"][vehicle_id]

                self.vehicle_soc_snapshots.append({
                    "schema_version": 1,
                    "run_id": self.run_id,
                    "trial_seed": b.TRIAL_SEED,
                    "algorithm": b.ASSIGNMENT_METHOD,
                    "tick": b.tick,
                    "simulation_time_s": snapshot_s,
                    "vehicle_id": vehicle_id,
                    "home_depot_id": vehicle.home_depot_id,
                    "home_depot_node": vehicle.home_depot_node,
                    "initial_SOC_Wh": float(
                        self.initial_soc[vehicle_id]
                    ),
                    "SOC_Wh": float(vehicle.soc_Wh),
                    "reserve_SOC_Wh": float(
                        b.BATTERY_CAPACITY_WH
                        * b.BATTERY_RESERVE_FRACTION
                    ),
                    "energy_consumed_Wh": float(
                        ledger["energy_consumed_Wh"]
                    ),
                    "energy_charged_Wh": float(
                        ledger["energy_charged_Wh"]
                    ),
                    "status": vehicle.status,
                    "passengers_onboard": vehicle.passengers_onboard,
                })

    def _within_request_stats(self, C, meta):
        groups = dof = 0
        energy_ties = time_ties = cost_ties = 0
        ss_e = ss_t = cross = 0.0

        for j in range(C.shape[1]):
            indices = np.flatnonzero(np.isfinite(C[:, j]))

            if len(indices) < 2:
                continue

            values = np.array([
                (
                    meta[i, j]["delta_energy"],
                    meta[i, j]["delta_time"],
                )
                for i in indices
            ], dtype=float)

            # Subtract a reference first so identical values give exact zeros.
            energy = values[:, 0] - values[0, 0]
            duration = values[:, 1] - values[0, 1]

            energy_ties += int(np.ptp(energy) == 0.0)
            time_ties += int(np.ptp(duration) == 0.0)
            cost_ties += int(np.ptp(C[indices, j]) == 0.0)

            energy -= energy.mean()
            duration -= duration.mean()

            ss_e += float(energy @ energy)
            ss_t += float(duration @ duration)
            cross += float(energy @ duration)
            groups += 1
            dof += len(indices) - 1

        correlation = None
        if ss_e > 0.0 and ss_t > 0.0:
            correlation = max(-1.0, min(
                1.0, cross / math.sqrt(ss_e) / math.sqrt(ss_t)
            ))

        return {
            "within_request_groups": groups,
            "within_request_dof": dof,
            "within_request_energy_ss": ss_e,
            "within_request_time_ss": ss_t,
            "within_request_cross_sum": cross,
            "within_request_pearson_r": correlation,
            "within_request_energy_tie_groups": energy_ties,
            "within_request_time_tie_groups": time_ties,
            "within_request_cost_tie_groups": cost_ties,
        }
    
    def _record_cost_diagnostics(self, round_index, C, meta):
        values = np.asarray([
            (plan["delta_energy"], plan["delta_time"])
            for (i, j), plan in meta.items()
            if np.isfinite(C[i, j])
        ], dtype=float).reshape(-1, 2)

        n = len(values)
        mean_e = mean_t = correlation = None
        ss_e = ss_t = cross = 0.0
        status = "insufficient_pairs"

        if n:
            energy, duration = values[:, 0], values[:, 1]
            mean_e, mean_t = float(energy.mean()), float(duration.mean())
            centered_e, centered_t = energy - mean_e, duration - mean_t
            ss_e = float(centered_e @ centered_e)
            ss_t = float(centered_t @ centered_t)
            cross = float(centered_e @ centered_t)

            if n >= 2:
                e_tol = 1e-12 * max(1.0, float(np.max(np.abs(energy))))
                t_tol = 1e-12 * max(1.0, float(np.max(np.abs(duration))))

                if np.ptp(energy) <= e_tol or np.ptp(duration) <= t_tol:
                    status = "constant_or_near_constant"
                else:
                    correlation = max(-1.0, min(
                        1.0, cross / math.sqrt(ss_e) / math.sqrt(ss_t)
                    ))
                    status = "defined"

        mean_weighted_energy = None
        mean_weighted_time = None

        if mean_e is not None and mean_t is not None:
            mean_weighted_energy, mean_weighted_time, _ = (
                self.b.assignment_cost_terms(
                    delta_energy_wh=mean_e,
                    delta_time_s=mean_t,
                    soc_fraction=1.0,
                    alpha=self.b.ALPHA_ENERGY,
                    beta=self.b.BETA_TIME,
                    gamma=0.0,
                )
            )
        self.cost_diagnostic_events.append({
            "schema_version": 3,
            "assignment_cost_mode": self.b.ASSIGNMENT_COST_MODE,
            "energy_reference_Wh": self.b.COST_ENERGY_REFERENCE_WH,
            "time_reference_s": self.b.COST_TIME_REFERENCE_S,
            **self._within_request_stats(C, meta),
            "tick": self.b.tick,
            "round": round_index,
            "vehicles": C.shape[0],
            "requests": C.shape[1],
            "feasible_pairs": n,
            "mean_delta_energy_Wh": mean_e,
            "mean_delta_time_s": mean_t,
            "energy_centered_ss": ss_e,
            "time_centered_ss": ss_t,
            "energy_time_centered_sum": cross,
            "pearson_r": correlation,
            "correlation_status": status,
            "mean_weighted_energy_cost": mean_weighted_energy,
            "mean_weighted_time_cost": mean_weighted_time,
        })

    def dispatch(self):
        b = self.b
        pending_cost_diagnostics = []
        dispatch_start = time.perf_counter()
        did_dispatch = False

        # Mark the start of this invocation's internal timing rows.
        timing_start_index = len(self.timing_events)


        # Replan between rounds so later assignments use updated plans.
        for round_index in range(b.UGV_PASSENGER_CAPACITY):
            fleet = [
                m
                for _, m in sorted(b.movers.items())
                if self.eligible(m)
            ]
            requests = sorted(
                b.requests_waiting.values(),
                key=lambda r: (r.created_time_s, r.id),
            )

            if not requests:
                break

            if not fleet:
                for request in requests:
                    diag = self._wait_diagnostics(request.id)
                    diag["dispatch_rounds"] += 1
                    diag["no_eligible_vehicle_rounds"] += 1
                break

            did_dispatch = True
            start = time.perf_counter()
            C = np.full(
                (len(fleet), len(requests)),
                math.inf,
            )
            meta = {}

       

            for i, m in enumerate(fleet):
                for j, request in enumerate(requests):
                    C[i, j], meta[i, j] = self.pair_cost(
                        m,
                        request,
                        b.ALPHA_ENERGY,
                        b.BETA_TIME,
                    )
            matrix_ms = (
                time.perf_counter() - start
            ) * 1000.0

            start = time.perf_counter()
            fn = b.ALGO_FUNCS[b.ASSIGNMENT_METHOD]

            if not np.isfinite(C).any():
                pairs = []
            elif b.ASSIGNMENT_METHOD in {"random", "annealing"}:
                pairs = fn(C, rng=b.ALGORITHM_RNG)
            elif b.ASSIGNMENT_METHOD == "roundrobin":
                pairs = fn(
                    C,
                    vehicle_ids=[m.id for m in fleet],
                )
            else:
                pairs = fn(C)

            
            solver_ms = (
                time.perf_counter() - start
            ) * 1000.0

            # F-019: Validate the assignments before activation.
            # Diagnostic time is separate from solver runtime.
            start = time.perf_counter()

            matched_count = b.validate_assignment_pairs(C, pairs)

            max_matching_count = b.maximum_feasible_matching_size(C)

            cardinality_gap = max_matching_count - matched_count

            if cardinality_gap < 0:
                raise RuntimeError(
                    "Solver returned more pairs than the feasible maximum."
                )

            cardinality_diagnostic_ms = (
                time.perf_counter() - start
            ) * 1000.0

            self.matching_round_count += 1
            self.matching_selected_pairs_total += matched_count
            self.matching_maximum_pairs_total += max_matching_count
            self.matching_cardinality_gap_total += cardinality_gap
            self.matching_cardinality_gap_rounds += int(
                cardinality_gap > 0
            )

            # Continue with the original assignment activation.
            start = time.perf_counter()
            used_i = set()
            used_j = set()


            for i, j in pairs:
                if (
                    i in used_i
                    or j in used_j
                    or not math.isfinite(float(C[i, j]))
                ):
                    raise RuntimeError(
                        "Solver returned a duplicate or infeasible assignment."
                    )

                used_i.add(i)
                used_j.add(j)

                m = fleet[i]
                request = requests[j]
                plan = meta[i, j]

                b.set_vehicle_stop_plan(m, plan["stops"])
                b.requests_waiting.pop(request.id)
                b.requests_inflight[request.id] = request
                self.active[m.id].add(request.id)
                self.direct_time[request.id] = plan["direct_time"]

                b.update_request_record(
                    request.id,
                    vehicle_id=m.id,
                    assigned_time_s=b.simulation_time_s(),
                    assignment_cost=float(C[i, j]),
                    vehicle_SOC_before_Wh=float(m.soc_Wh),
                )

                b.RUN["vehicle_records"][m.id]["assigned_requests"] += 1
                b.RUN["req_seq_counter"] += 1
                seq = b.RUN["req_seq_counter"]
                b.RUN["req_seq"][request.id] = seq
                b.RUN["vehicle_assigned"].setdefault(
                    m.id, set()
                ).add(seq)


                # F-028: Persist each committed assignment event.
                b.RUN["assignment_event_counter"] += 1

                request_row = b.RUN["request_records"][request.id]

                assignment_event = {
                    field: None
                    for field in b.ASSIGNMENT_EVENT_FIELDS
                }

                assignment_event.update({
                    "run_id": request_row.get("run_id"),
                    "scenario": request_row.get("scenario"),
                    "trial_seed": request_row.get("trial_seed"),
                    "trial_index": request_row.get("trial_index"),
                    "algorithm": request_row.get("algorithm"),
                    "event_id": b.RUN["assignment_event_counter"],
                    "simulation_tick": b.tick,
                    "event_time_s": b.simulation_time_s(),
                    "request_id": request.id,
                    "request_sequence": seq,
                    "pickup_node": getattr(
                        request, "pickup_node", None
                    ),
                    "drop_node": request.drop_node,
                    "vehicle_id": m.id,
                    "vehicle_SOC_before_Wh": float(m.soc_Wh),

                    # Time predictions are relative to assignment.
                    "predicted_pickup_time_s": float(
                        plan["prediction"]["pickups"][request.id]
                    ),
                    "predicted_trip_time_s": float(
                        plan["prediction"]["drops"][request.id]
                        - plan["prediction"]["pickups"][request.id]
                    ),
                    "predicted_total_time_s": float(
                        plan["prediction"]["drops"][request.id]
                    ),

                    # Incremental cost of adding this request.
                    "predicted_incremental_route_energy_Wh": float(
                        plan["delta_energy"]
                    ),
                    "predicted_incremental_route_time_s": float(
                        plan["delta_time"]
                    ),

                    # Full remaining plan after accepting the request.
                    "predicted_remaining_route_energy_Wh": float(
                        plan["prediction"]["energy"]
                    ),
                    "predicted_remaining_route_time_s": float(
                        plan["prediction"]["duration"]
                    ),

                    "energy_feasible": int(
                        float(m.soc_Wh)
                        >= float(plan["required_energy_Wh"]) - 1e-9
                    ),
                    "assignment_cost": float(C[i, j]),
                })

                with open(
                    b.RUN["assignment_csv_path"],
                    "a",
                    newline="",
                    encoding="utf-8",
                ) as assignment_file:
                    writer = csv.DictWriter(
                        assignment_file,
                        fieldnames=b.ASSIGNMENT_EVENT_FIELDS,
                    )
                    writer.writerow(assignment_event)

                self.plan_events.append({
                    "time_s": b.simulation_time_s(),
                    "vehicle_id": m.id,
                    "request_id": request.id,
                    "incremental_energy_Wh": plan["delta_energy"],
                    "incremental_time_s": plan["delta_time"],
                    "cost": float(C[i, j]),
                    "remaining_route_energy_Wh": plan["prediction"]["energy"],
                    "remaining_route_time_s": plan["prediction"]["duration"],
                    "energy_margin_Wh": (
                        float(m.soc_Wh) - plan["required_energy_Wh"]
                    ),
                    "stops": json.dumps(
                        [s.model_dump() for s in m.planned_stops],
                        sort_keys=True,
                    ),
                })

                self._fleet_event(
                    "ASSIGN",
                    m,
                    request.id,
                    float(C[i, j]),
                )

            activation_ms = (
                time.perf_counter() - start
            ) * 1000.0

            b.assignment_runtime_ms.append(solver_ms)

            self.timing_events.append({
                "tick": b.tick,
                "round": round_index,
                "vehicles": len(fleet),
                "requests": len(requests),
                "finite_pairs": int(np.isfinite(C).sum()),
                "matching_count": matched_count,
                "max_feasible_matching_count": max_matching_count,
                "matching_cardinality_gap": cardinality_gap,
                "cardinality_diagnostic_ms": cardinality_diagnostic_ms,
                "matrix_ms": matrix_ms,
                "solver_ms": solver_ms,
                "activation_ms": activation_ms,
            })

            self._fleet_event("RUNTIME", runtime=solver_ms)
            pending_cost_diagnostics.append((
                round_index,
                C,
                meta,
                [request.id for request in requests],
                used_j,
            ))

            if not pairs:
                break

        

        for round_index, C, meta, request_ids, selected in pending_cost_diagnostics:
            self._record_cost_diagnostics(round_index, C, meta)

            for j, request_id in enumerate(request_ids):
                diag = self._wait_diagnostics(request_id)
                diag["dispatch_rounds"] += 1

                if j in selected:
                    self._close_wait_diagnostics(request_id, "assigned")
                elif np.isfinite(C[:, j]).any():
                    diag["feasible_not_selected_rounds"] += 1

        # F-022: Complete instrumented dispatch runtime.
        # Includes matrix construction, solver execution,
        # assignment activation, and diagnostic processing.

        if did_dispatch:
            elapsed_ms = (
                time.perf_counter() - dispatch_start
            ) * 1000.0

            if (
                not math.isfinite(elapsed_ms)
                or elapsed_ms < 0.0
            ):
                raise RuntimeError(
                    "Invalid dispatch runtime measurement."
                )

            b.dispatch_runtime_ms.append(elapsed_ms)

            # F-037B: One audit row per complete dispatch operation.
            round_rows = self.timing_events[timing_start_index:]

            if not round_rows:
                raise RuntimeError(
                    "Dispatch completed without internal timing rows."
                )

            phase_names = (
                "matrix_ms",
                "solver_ms",
                "cardinality_diagnostic_ms",
                "activation_ms",
            )

            phase_totals = {
                name: sum(float(row[name]) for row in round_rows)
                for name in phase_names
            }

            self.dispatch_invocation_counter += 1

            self.dispatch_invocation_events.append({
                "run_id": b.RUN["run_id"],
                "dispatch_id": self.dispatch_invocation_counter,
                "simulation_tick": b.tick,
                "simulation_time_s": b.simulation_time_s(),
                "algorithm": b.ASSIGNMENT_METHOD,

                "round_count": len(round_rows),

                # First-round workload, before subsequent matches.
                "initial_vehicles": int(
                    round_rows[0]["vehicles"]
                ),
                "initial_requests": int(
                    round_rows[0]["requests"]
                ),
                "initial_feasible_pairs": int(
                    round_rows[0]["finite_pairs"]
                ),

                # Total matching work across all rounds.
                "candidate_pairs_evaluated": sum(
                    int(row["vehicles"]) * int(row["requests"])
                    for row in round_rows
                ),
                "feasible_pairs_evaluated": sum(
                    int(row["finite_pairs"])
                    for row in round_rows
                ),
                "total_matches": sum(
                    int(row["matching_count"])
                    for row in round_rows
                ),

                "matrix_ms": phase_totals["matrix_ms"],
                "solver_ms": phase_totals["solver_ms"],
                "cardinality_diagnostic_ms": phase_totals[
                    "cardinality_diagnostic_ms"
                ],
                "activation_ms": phase_totals["activation_ms"],

                "complete_dispatch_ms": elapsed_ms,
                "other_dispatch_ms": (
                    elapsed_ms - sum(phase_totals.values())
                ),

                "dispatch_budget_ms": float(
                    b.DISPATCH_BUDGET_MS
                ),
                "budget_missed": int(
                    elapsed_ms > b.DISPATCH_BUDGET_MS
                ),
            })


    def _start_leg(self, m):
        b = self.b
        state = self.states[m.id]
        target = m.planned_stops[0] if m.planned_stops else None
        destination = (
            target.node
            if target
            else b.vehicle_home_node(m)
        )

        if target is None and m.node == destination:
            b.phase[m.id] = m.status = "idle"
            b.serving[m.id] = None
            b.mover_velocity[m.id] = m.speed_kph = 0.0
            return False

        leg = self._leg(m.node, destination, m.mass_kg)

        if leg is None:
            raise RuntimeError(
                "Accepted route became unreachable."
            )

        b.set_route(m, list(leg.nodes))

        state.leg = leg
        state.target = target
        state.phase_index = 0
        state.phase_elapsed = 0.0
        state.leg_elapsed = 0.0
        state.leg_energy = 0.0
        state.dwell_remaining = None

        b.phase[m.id] = m.status = (
            "to_" + target.kind
            if target
            else "returning"
        )
        b.serving[m.id] = (
            target.request_id
            if target
            else None
        )

        return True

    def _withdraw(self, m, energy):
        b = self.b

        # F-049: Validate initial SOC before energy withdrawal.
        soc_before_Wh = float(m.soc_Wh)

        if (
            not math.isfinite(soc_before_Wh)
            or soc_before_Wh < 0.0
            or soc_before_Wh > b.BATTERY_CAPACITY_WH
        ):
            raise RuntimeError(
                f"Vehicle {m.id}: invalid initial battery SOC "
                f"({soc_before_Wh} Wh)."
            )

        if (
            not math.isfinite(energy)
            or energy < 0
            or energy > m.soc_Wh + 1e-9
        ):
            raise RuntimeError(
                f"Vehicle {m.id}: invalid or unaffordable energy withdrawal."
            )

        # F-034: Verify the battery reserve during active
        # driving and dwell energy withdrawals.
        reserve_Wh = (
            b.BATTERY_CAPACITY_WH
            * b.BATTERY_RESERVE_FRACTION
        )

        soc_after_Wh = float(m.soc_Wh) - float(energy)

        if soc_after_Wh < reserve_Wh - 1e-6:
            raise RuntimeError(
                f"BATTERY RESERVE VIOLATION: "
                f"vehicle={m.id}, " 
                f"soc_before_Wh={m.soc_Wh:.6f}, "
                f"withdrawal_Wh={energy:.6f}, "
                f"soc_after_Wh={soc_after_Wh:.6f}, "
                f"reserve_Wh={reserve_Wh:.6f}"
            )

        m.soc_Wh = max(0.0, m.soc_Wh - energy)
        b.total_energy_consumed += energy

        record = b.RUN["vehicle_records"][m.id]
        record["energy_consumed_Wh"] += energy
        record["minimum_SOC_Wh"] = min(
            record["minimum_SOC_Wh"],
            m.soc_Wh,
        )

    def _position(self, m, distance):
        b = self.b
        q = b.exec_q[m.id]

        while distance > 0.0 and q:
            u, v = q[0]

            if m._edge_u is None:
                m._edge_u = u
                m._edge_v = v
                m._edge_prog = 0.0

            length = float(b.safe_edge_data(u, v)["length"])
            left = length * (1.0 - m._edge_prog)

            if distance >= left:
                distance -= left
                q.popleft()
                m.node = v
                m.lon = b.G.nodes[v]["x"]
                m.lat = b.G.nodes[v]["y"]
                m._edge_u = None
                m._edge_v = None
                m._edge_prog = 0.0
            else:
                m._edge_prog += distance / length
                m.lon, m.lat = b.interpolate_on_edge(
                    u, v, m._edge_prog
                )
                distance = 0.0

        if distance > 1e-6:
            raise RuntimeError(
                "Vehicle moved beyond the end of its leg."
            )

    def _finish_stop(self, m, now):
        b = self.b
        state = self.states[m.id]
        stop = state.target

        if (
            stop != m.planned_stops[0]
            or b.mover_velocity[m.id] != 0.0
        ):
            raise RuntimeError(
                "Service must occur at the planned stop and at rest."
            )

        rid = stop.request_id
        record = b.RUN["request_records"][rid]
        m.planned_stops.pop(0)
        before = m.mass_kg

        if stop.kind == "pickup":
            if b.pickup_deadline_exceeded(record, now):
                # Remove only this passenger's remaining stop.
                m.planned_stops = [
                    s for s in m.planned_stops
                    if s.request_id != rid
                ]

                self.active[m.id].remove(rid)
                b.requests_inflight.pop(rid)

                record.update(
                    completion_status="uncompleted",
                    maximum_wait_violation=1,
                    failure_reason="pickup_deadline_exceeded",
                )
                event_kind = "pickup_expired"
            else:
                m.board_passenger(rid)
                record["pickup_time_s"] = round(now, 6)
                self.ride_deadline[rid] = (
                    now
                    + MAX_RIDE_FACTOR * self.direct_time[rid]
                    + MAX_EXTRA_RIDE_S
                )
                event_kind = "pickup"
        else:
            if record["request_completed"]:
                raise RuntimeError(
                    "Duplicate passenger completion."
                )

            # F-033: Verify the actual passenger drop-off
            # respects the ride-time limit established at pickup.
            ride_deadline = self.ride_deadline.get(rid)

            if ride_deadline is None:
                raise RuntimeError(
                    f"Request {rid}: missing ride-time deadline."
                )

            if not math.isfinite(ride_deadline):
                raise RuntimeError(
                    f"Request {rid}: invalid ride-time deadline."
                )

            if now > ride_deadline + 1e-6:
                raise RuntimeError(
                    f"RIDE-TIME VIOLATION: request={rid}, "
                    f"vehicle={m.id}, "
                    f"dropoff_time_s={now:.6f}, "
                    f"deadline_s={ride_deadline:.6f}, "
                    f"excess_s={now - ride_deadline:.6f}"
                )

            m.dropoff_passenger(rid)
            self.active[m.id].remove(rid)
            b.requests_inflight.pop(rid)

            record.update(
                dropoff_time_s=round(now, 6),
                vehicle_SOC_after_Wh=float(m.soc_Wh),
                request_completed=1,
                passenger_distance_km=(
                    record["_passenger_distance_m"] / 1000.0
                ),
            )

            # Shared-leg energy is counted once in vehicle totals.
            # Individual request energy remains undefined.
            b.finalise_request_timing(rid)
            b.fulfilled_requests += 1
            b.RUN["vehicle_records"][m.id]["completed_requests"] += 1

            seq = b.RUN["req_seq"][rid]
            b.RUN["vehicle_completed"].setdefault(
                m.id, set()
            ).add(seq)

            b.RUN["vehicle_req_dist_km"][(m.id, seq)] = (
                record["passenger_distance_km"]
            )

            self._fleet_event("COMPLETE", m, rid)
            event_kind = "dropoff"

        self.stop_events.append({
            "time_s": now,
            "vehicle_id": m.id,
            "request_id": rid,
            "event": event_kind,
            "node": m.node,
            "mass_before_kg": before,
            "mass_after_kg": m.mass_kg,
            "passengers_onboard": m.passengers_onboard,
            "speed_m_s": b.mover_velocity[m.id],
            "soc_Wh": m.soc_Wh,
        })

        b.stop_plan_masses(m, m.planned_stops)

        state.leg = None
        state.target = None
        state.dwell_remaining = None

        b.serving[m.id] = (
            m.planned_stops[0].request_id
            if m.planned_stops
            else None
        )

    def advance(self, m, budget, start):
        b = self.b
        state = self.states[m.id]
        now = start

        while budget > 1e-12:
            if state.leg is None and not self._start_leg(m):
                b.advance_parked_vehicle(
                    m, budget, True, now
                )
                break

            leg = state.leg

            if state.phase_index < len(leg.phases):
                motion = leg.phases[state.phase_index]
                remaining = motion.duration - state.phase_elapsed
                dt = min(budget, remaining)

                v0 = (
                    motion.speed
                    + motion.acceleration * state.phase_elapsed
                )
                v1 = v0 + motion.acceleration * dt
                ds = max(0.0, 0.5 * (v0 + v1) * dt)

                energy = b.calculate_driving_energy_Wh(
                    motion.force, ds, dt
                )

                self._withdraw(m, energy)
                self.driving_Wh += energy
                self._position(m, ds)

                state.leg_elapsed += dt
                state.leg_energy += energy
                state.phase_elapsed += dt

                if dt == remaining:
                    state.phase_index += 1
                    state.phase_elapsed = 0.0
                    v1 = motion.final_speed

                b.mover_velocity[m.id] = max(0.0, v1)
                state.acceleration = motion.acceleration
                m.speed_kph = max(0.0, v1) * 3.6

                vr = b.RUN["vehicle_records"][m.id]
                vr["driving_time_s"] += dt
                vr["total_distance_km"] += ds / 1000.0

                if m.passengers_onboard:
                    distance_key = "productive_distance_km"
                elif state.target is None:
                    distance_key = "return_distance_km"
                else:
                    distance_key = "empty_pickup_distance_km"

                vr[distance_key] += ds / 1000.0

                for rid in m.onboard_request_ids:
                    b.RUN["request_records"][rid][
                        "_passenger_distance_m"
                    ] += ds

                b.update_travel_time(dt)
                now += dt
                budget -= dt
                continue

            if abs(state.leg_elapsed - leg.duration) > 1e-6:
                raise RuntimeError(
                    "Leg execution time differs from its prediction."
                )

            if abs(state.leg_energy - leg.energy) > 1e-6:
                raise RuntimeError(
                    "Leg execution energy differs from its prediction."
                )

            
            # F-031: Record each completed driving leg exactly once.
            #
            # For pickup/drop-off legs, dwell_remaining is None
            # only on the first arrival at the destination.
            #
            # During subsequent dwell ticks, the completed leg
            # remains in state.leg, so it must not be logged again.
            #
            # Return-to-depot legs have no dwell and are cleared
            # immediately after reaching the depot.

            if (
                state.target is None
                or state.dwell_remaining is None
            ):
                self.leg_execution_events.append({
                    "run_id": b.RUN["run_id"],
                    "vehicle_id": m.id,
                    "leg_type": (
                        f"to_{state.target.kind}"
                        if state.target is not None
                        else "return_to_depot"
                    ),
                    "target_request_id": (
                        state.target.request_id
                        if state.target is not None
                        else None
                    ),
                    "start_time_s": now - state.leg_elapsed,
                    "end_time_s": now,
                    "start_node": leg.nodes[0],
                    "end_node": leg.nodes[-1],
                    "distance_m": float(leg.distance),
                    "predicted_duration_s": float(leg.duration),
                    "executed_duration_s": float(
                        state.leg_elapsed
                    ),
                    "predicted_driving_energy_Wh": float(
                        leg.energy
                    ),
                    "executed_driving_energy_Wh": float(
                        state.leg_energy
                    ),
                    "driving_energy_error_Wh": float(
                        state.leg_energy - leg.energy
                    ),
                })


            # Finish exactly at the node after the braking phase.
            b.exec_q[m.id].clear()
            m.node = leg.nodes[-1]
            m.lon = b.G.nodes[m.node]["x"]
            m.lat = b.G.nodes[m.node]["y"]
            m._edge_u = None
            m._edge_v = None
            m._edge_prog = 0.0
            b.mover_velocity[m.id] = m.speed_kph = 0.0
            state.acceleration = 0.0

            if state.target is None:
                state.leg = None
                m.route_coords = []
                continue

            if state.dwell_remaining is None:
                state.dwell_remaining = self._dwell(state.target)

            b.phase[m.id] = m.status = "dwelling"

            dt = min(budget, state.dwell_remaining)
            energy = DWELL_AUX_POWER_W * dt / 3600.0

            self._withdraw(m, energy)
            self.dwell_Wh += energy
            self.dwell_s += dt
            b.RUN["vehicle_records"][m.id]["idle_time_s"] += dt
            b.total_idle_time += dt

            state.dwell_remaining = max(
                0.0, state.dwell_remaining - dt
            )
            now += dt
            budget -= dt

            if state.dwell_remaining <= 1e-12:
                self._finish_stop(m, now)

    def step(self):
        b = self.b

        if b.RUN["run_id"] != self.run_id:
            raise RuntimeError(
                "Attempted to advance a replaced run."
            )

        if (
            b.G is not self.graph
            or self._parameters() != self.parameters
        ):
            raise RuntimeError(
                "Reset the run after changing graph or run parameters."
            )

        b.release_due_canonical_requests()
        b.expire_waiting_requests()
        now = b.simulation_time_s()
        if self._dispatch_due(now):
            self.dispatch()

        for _, m in sorted(b.movers.items()):
            if (
                not math.isfinite(m.soc_Wh)
                or not 0 <= m.soc_Wh <= b.BATTERY_CAPACITY_WH
            ):
                raise RuntimeError(
                    "Invalid battery state."
                )

            before = b.RUN["vehicle_records"][m.id][
                "energy_consumed_Wh"
            ]

            self.advance(m, b.DT_S, now)

            if self.record_velocity and (b.tick + 1) % 3 == 0:
                self.velocity_events.append([
                    b.tick + 1,
                    now + b.DT_S,
                    m.id,
                    m.speed_kph,
                    self.states[m.id].acceleration,
                    (
                        b.RUN["vehicle_records"][m.id][
                            "energy_consumed_Wh"
                        ] - before
                    ),
                    m.status,
                ])

        b.tick += 1

        if b.tick % b.SNAPSHOT_EVERY == 0:
            self._fleet_event("SNAPSHOT")

    def _dispatch_due(self, now):
        b = self.b
        window = float(b.DISPATCH_WINDOW_S)

        # Preserve the original call pattern when batching is disabled.
        if window == 0.0:
            return True

        # Fixed boundaries: 0, window, 2 * window, ...
        # A boundary between ticks is handled on the next tick.
        slot = math.floor((now + 1e-9) / window)
        regular_due = slot > self._dispatch_window_slot
        self._dispatch_window_slot = slot

        waiting = b.requests_waiting
        self._urgent_dispatch_request_ids.intersection_update(waiting)

        if not waiting:
            return False

        slack = float(b.DISPATCH_URGENT_SLACK_S)
        urgent_ids = set()

        # Zero disables early dispatches.
        if slack > 0.0:
            urgent_ids = {
                request_id
                for request_id, request in waiting.items()
                if now - float(request.created_time_s)
                >= float(b.MAX_WAIT_S) - slack - 1e-9
            }

        newly_urgent = urgent_ids - self._urgent_dispatch_request_ids

        if regular_due or newly_urgent:
            self._urgent_dispatch_request_ids.update(urgent_ids)
            return True

        return False

    def _csv(self, name, rows):
        if not rows:
            return

        path = (
            Path(self.b.RUN["request_csv_path"]).parent
            / name
        )
        new = not path.exists()

        with path.open(
            "a", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=list(rows[0]),
            )

            if new:
                writer.writeheader()

            writer.writerows(rows)

        rows.clear()

    def flush(self):
        b = self.b
        directory = Path(b.RUN["request_csv_path"]).parent

        if not self._output_ready:
            (directory / "shared_ride_model.json").write_text(
                json.dumps(get_pooling_model_info(), indent=2)
            )
            self._output_ready = True

        self._csv(
            "passenger_events.csv",
            self.stop_events,
        )
        self._csv(
            "pool_plan_events.csv",
            self.plan_events,
        )
        # F-031: Export predicted versus executed leg energy.
        self._csv(
            "leg_execution_events.csv",
            self.leg_execution_events,
        )
        self._csv(
            "timing.csv",
            self.timing_events,
        )

        # F-037B: Export complete per-dispatch latency records.
        self._csv(
            "dispatch_invocation_events.csv",
            self.dispatch_invocation_events,
        )

        self._csv(
            "cost_diagnostics.csv",
            self.cost_diagnostic_events,
        )
        self._csv(
            "request_wait_diagnostics.csv",
            self.wait_diagnostic_rows,
        )
        self._csv(
            "vehicle_soc_snapshots.csv",
            self.vehicle_soc_snapshots,
        )

        if self.velocity_events:
            with open(
                b.RUN["velocity_log_csv_path"],
                "a",
                newline="",
                encoding="utf-8",
            ) as f:
                csv.writer(f).writerows(self.velocity_events)

            self.velocity_events.clear()

        if self.events:
            with open(
                b.RUN["fleet_log_csv_path"],
                "a",
                newline="",
                encoding="utf-8",
            ) as f:
                csv.writer(f).writerows(self.events)

            self.events.clear()
    def energy_summary(self):
        """Summarize battery withdrawals using the engine's existing counters."""
        b = self.b

        if self.run_id != b.RUN["run_id"]:
            raise RuntimeError("Energy summary belongs to a different run.")

        battery_total = sum(
            float(record["energy_consumed_Wh"])
            for record in b.RUN["vehicle_records"].values()
        )
        driving = float(self.driving_Wh)
        dwell = float(self.dwell_Wh)
        dwell_time = float(self.dwell_s)

        for name, value in (
            ("battery_withdrawal_Wh", battery_total),
            ("driving_Wh", driving),
            ("dwell_Wh", dwell),
            ("dwell_time_s", dwell_time),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise RuntimeError(
                    f"Invalid energy-summary value: {name}={value!r}"
                )

        parked = battery_total - driving - dwell
        tolerance = max(
            1e-6,
            1e-9 * max(battery_total, driving + dwell),
        )
        if parked < -tolerance:
            raise RuntimeError(
                "Driving and dwell energy exceed total battery withdrawals: "
                f"total={battery_total:.9f} Wh, "
                f"driving={driving:.9f} Wh, "
                f"dwell={dwell:.9f} Wh."
            )

        completed = b.fulfilled_requests
        return {
            "driving_Wh": driving,
            "dwell_Wh": dwell,
            "dwell_time_s": dwell_time,
            "battery_parked_Wh": max(0.0, parked),
            "battery_withdrawal_Wh": battery_total,
            "battery_Wh_per_completed_request": (
                battery_total / completed if completed else None
            ),
        }

    def finalise_requests(self):
        b = self.b

        if not b.all_requests_and_vehicles_settled():
            return

        # One terminal request writer. This preserves observed
        # assignment/pickup timing for unfinished requests.
        # Already-written records are ignored.
        b.finalise_uncompleted_request_records()

        for vid, m in b.movers.items():
            record = b.RUN["vehicle_records"][vid]
            balance = (
                self.initial_soc[vid]
                + record["energy_charged_Wh"]
                - record["energy_consumed_Wh"]
            )

            if not math.isclose(
                balance,
                m.soc_Wh,
                abs_tol=1e-6,
                rel_tol=1e-9,
            ):
                raise RuntimeError(
                    f"Vehicle {vid} battery balance failed."
                )

        blocked_request_ids = sorted({
            rid for _, rid in self.energy_blocked_pairs
        })

        self._csv(
            "energy_feasibility_summary.csv",
            [{
                "schema_version": 1,
                "run_id": self.run_id,
                "scenario": b.CURRENT_SCENARIO,
                "algorithm": b.ASSIGNMENT_METHOD,
                "trial_seed": b.TRIAL_SEED,
                "pair_evaluations": self.pair_evaluations,
                "feasible_pair_evaluations": (
                    self.feasible_pair_evaluations
                ),
                "energy_blocked_pair_evaluations": (
                    self.energy_blocked_pair_evaluations
                ),
                "other_infeasible_pair_evaluations": (
                    self.pair_evaluations
                    - self.feasible_pair_evaluations
                    - self.energy_blocked_pair_evaluations
                ),
                "non_energy_feasible_insertions": (
                    self.non_energy_feasible_insertions
                ),
                "energy_blocked_insertions": (
                    self.energy_blocked_insertions
                ),
                "unique_energy_blocked_pairs": (
                    len(self.energy_blocked_pairs)
                ),
                "requests_with_any_energy_blocked_pair": (
                    len(blocked_request_ids)
                ),
                "energy_blocked_request_ids": json.dumps(
                    blocked_request_ids
                ),
            }],
        )

        self._csv(
            "pool_energy_summary.csv",
            [self.energy_summary()],
        )

_SHARED_RIDE_ENGINE = None


def get_shared_ride_engine():
    global _SHARED_RIDE_ENGINE

    if (
        _SHARED_RIDE_ENGINE is None
        or _SHARED_RIDE_ENGINE.run_id != RUN["run_id"]
    ):
        _SHARED_RIDE_ENGINE = SharedRideEngine(
            sys.modules[__name__]
        )

    return _SHARED_RIDE_ENGINE


def pair_cost(
    m,
    r,
    alpha=ALPHA_ENERGY,
    beta=BETA_TIME,
):
    """Cost of inserting one passenger into the remaining shared route."""
    return get_shared_ride_engine().pair_cost(
        m,
        r,
        alpha,
        beta,
    )




# ── Assignment algorithms ─────────────────────────────────────────

def maximum_feasible_matching_size(C):
    """
    Find the maximum possible number of vehicle-request assignments.

    Only finite-cost pairs are considered feasible.
    This is a diagnostic function: it does not modify solver decisions.
    """
    C = np.asarray(C, dtype=float)

    if C.ndim != 2 or np.isnan(C).any() or np.isneginf(C).any():
        raise ValueError(
            "C must be a 2D matrix of finite costs or +inf."
        )

    n, m = C.shape

    if n == 0 or m == 0:
        return 0

    adjacency = [
        np.flatnonzero(np.isfinite(C[i])).tolist()
        for i in range(n)
    ]

    request_owner = [-1] * m

    def augment(i, seen_requests):
        for j in adjacency[i]:
            if j in seen_requests:
                continue

            seen_requests.add(j)
            previous = request_owner[j]

            if previous == -1 or augment(previous, seen_requests):
                request_owner[j] = i
                return True

        return False

    return sum(
        int(augment(i, set()))
        for i in range(n)
    )


def validate_assignment_pairs(C, pairs):
    """
    Validate solver assignments before modifying simulation state.

    Checks:
    - Valid pair format
    - Valid vehicle and request indices
    - No duplicate vehicle assignments
    - No duplicate request assignments
    - Every selected pair is feasible

    Returns the number of selected pairs.
    """
    C = np.asarray(C, dtype=float)

    if C.ndim != 2:
        raise ValueError(
            "C must be a two-dimensional cost matrix."
        )

    used_rows = set()
    used_cols = set()

    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise RuntimeError(
                "Solver returned a malformed assignment pair."
            )

        i, j = pair

        if (
            not isinstance(i, (int, np.integer))
            or not isinstance(j, (int, np.integer))
            or isinstance(i, (bool, np.bool_))
            or isinstance(j, (bool, np.bool_))
        ):
            raise RuntimeError(
                "Solver returned non-integer assignment indices."
            )

        if not (
            0 <= i < C.shape[0]
            and 0 <= j < C.shape[1]
        ):
            raise RuntimeError(
                "Solver returned out-of-bounds indices."
            )

        if (
            i in used_rows
            or j in used_cols
            or not np.isfinite(C[i, j])
        ):
            raise RuntimeError(
                "Solver returned duplicate or infeasible assignment."
            )

        used_rows.add(i)
        used_cols.add(j)

    return len(used_rows)

def assign_hungarian(C):
    """Maximize feasible assignments, then minimize their total cost."""
    C = np.asarray(C, dtype=float)
    if C.ndim != 2:
        raise ValueError("C must be a two-dimensional cost matrix.")

    n, m = C.shape
    if n == 0 or m == 0:
        return []

    feasible = np.isfinite(C)
    neighbours = [
        np.flatnonzero(feasible[i]).tolist()
        for i in range(n)
    ]
    request_owner = [-1] * m

    # Find how many real assignments are possible.
    def augment(i, seen):
        for j in neighbours[i]:
            if j in seen:
                continue
            seen.add(j)

            previous_row = request_owner[j]
            if previous_row == -1 or augment(previous_row, seen):
                request_owner[j] = i
                return True

        return False

    max_matches = 0
    for i in range(n):
        if augment(i, set()):
            max_matches += 1

    if max_matches == 0:
        return []

    # Allow exactly the unavoidable number of idle vehicles.
    dummy_count = n - max_matches
    augmented_cost = np.full(
        (n, m + dummy_count),
        np.inf,
        dtype=float,
    )
    augmented_cost[:, :m] = np.where(feasible, C, np.inf)
    augmented_cost[:, m:] = 0.0

    row_ind, col_ind = linear_sum_assignment(augmented_cost)

    # Dummy columns mean "no assignment" and are not returned.
    return [
        (int(i), int(j))
        for i, j in zip(row_ind, col_ind)
        if j < m
    ]


# ------------------------------------------------------------
def assign_greedy(C):
    """
    Greedy assignment baseline.

    Selects the lowest cost feasible
    vehicle-request pair iteratively.
    """

    n, m = C.shape

    pairs = []

    used_vehicle = set()
    used_request = set()


    while (
        len(used_vehicle) < n
        and len(used_request) < m
    ):

        best_cost = float("inf")
        best_pair = None


        for i in range(n):

            if i in used_vehicle:
                continue


            for j in range(m):

                if j in used_request:
                    continue


                if (
                    np.isfinite(C[i,j])
                    and C[i,j] < best_cost
                ):

                    best_cost = C[i,j]
                    best_pair = (i,j)



        if best_pair is None:
            break



        i,j = best_pair


        pairs.append(
            (i,j)
        )


        used_vehicle.add(i)

        used_request.add(j)



    return pairs


     
# ------------------------------------------------------------

def assign_mcmf(C):
    """Maximize feasible assignments, then minimize their total cost.

    Integer edge weights preserve the finite float costs in C exactly.
    """
    C = np.asarray(C, dtype=float)
    if C.ndim != 2:
        raise ValueError("C must be a two-dimensional cost matrix.")

    n, m = C.shape
    if n == 0 or m == 0:
        return []

    # Express each feasible cost as an exact fraction.
    ratios = {}
    for i in range(n):
        for j in range(m):
            cost = float(C[i, j])
            if math.isfinite(cost):
                ratios[(i, j)] = cost.as_integer_ratio()

    if not ratios:
        return []

    # Binary-float denominators are powers of two, so the largest
    # denominator is divisible by every smaller denominator.
    common_denominator = max(
        denominator for _, denominator in ratios.values()
    )

    flow_graph = nx.DiGraph()
    source = "source"
    sink = "sink"

    # Each buggy can receive at most one request.
    for i in range(n):
        flow_graph.add_edge(
            source, f"v{i}", capacity=1, weight=0
        )

    # Use the same exact scale for every feasible assignment cost.
    for (i, j), (numerator, denominator) in ratios.items():
        integer_cost = numerator * (
            common_denominator // denominator
        )
        flow_graph.add_edge(
            f"v{i}",
            f"r{j}",
            capacity=1,
            weight=integer_cost,
        )

    # Each request can receive at most one buggy.
    for j in range(m):
        flow_graph.add_edge(
            f"r{j}", sink, capacity=1, weight=0
        )

    try:
        logger.debug("[MCMF] starting solver")
        flow = nx.max_flow_min_cost(flow_graph, source, sink)
        logger.debug("[MCMF] solver finished")
    except Exception as e:
        raise RuntimeError(f"MCMF solver failed: {e}") from e

    pairs = [
        (i, j)
        for i, j in ratios
        if flow.get(f"v{i}", {}).get(f"r{j}", 0) > 0
    ]

    logger.debug(
        f"[MCMF DEBUG] pairs={len(pairs)}, "
        f"vehicles={n}, requests={m}"
    )
    return pairs  


def assign_auction(C, epsilon=1e-6, max_bids=200000):
    """Approximate auction with unmatched options and a bid limit.

    epsilon is measured after costs are normalized.
    """
    C = np.asarray(C, dtype=float)
    if C.ndim != 2 or np.isnan(C).any() or np.isneginf(C).any():
        raise ValueError("C must be a 2D matrix of finite costs or +inf.")
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("epsilon must be positive and finite.")
    if isinstance(max_bids, bool) or not isinstance(max_bids, int) or max_bids < 1:
        raise ValueError("max_bids must be a positive integer.")

    n, m = C.shape
    feasible = np.isfinite(C)
    if n == 0 or m == 0 or not feasible.any():
        return []

    # Add dummy rows and columns so leaving work unmatched is possible.
    scale = max(1.0, float(np.max(np.abs(C[feasible]))))
    size = n + m
    costs = np.zeros((size, size))
    costs[:n, :m] = C / scale
    costs[:n, m:] = 2.0 * min(n, m) + 2.0

    prices = np.zeros(size)
    target_epsilon = min(float(epsilon), 1.0 / (4.0 * size))
    bid_epsilon = 0.25
    bids = 0

    # Start with larger bids, then refine them while retaining prices.
    while True:
        owners = np.full(size, -1, dtype=int)
        assigned = np.full(size, -1, dtype=int)
        free = deque(range(size))

        while free:
            if bids >= max_bids:
                raise RuntimeError(
                    f"Auction did not converge within {max_bids} bids."
                )
            bids += 1
            i = free.popleft()

            values = -costs[i] - prices
            j = int(np.argmax(values))
            best_value = float(values[j])
            values[j] = -np.inf
            second_value = float(np.max(values))

            increment = best_value - second_value + bid_epsilon
            new_price = prices[j] + increment
            if not np.isfinite(new_price) or new_price <= prices[j]:
                raise RuntimeError("Auction price precision exhausted.")
            prices[j] = new_price

            old_owner = int(owners[j])
            owners[j] = i
            assigned[i] = j
            if old_owner != -1:
                assigned[old_owner] = -1
                free.append(old_owner)

        if bid_epsilon <= target_epsilon:
            break
        bid_epsilon = max(target_epsilon, bid_epsilon / 5.0)

    # Only return real vehicle-request pairs; discard dummy assignments.
    return [
        (i, int(j))
        for i, j in enumerate(assigned[:n])
        if 0 <= j < m and feasible[i, j]
    ]



    
# ------------------------------------------------------------

def assign_random(C, rng=None):
    if rng is None:
        raise ValueError(
            "RNG must be provided for reproducible benchmarking"
        )

    n, m = C.shape
    feasible = []

    for i in range(n):
        for j in range(m):
            if np.isfinite(C[i, j]):
                feasible.append((i, j))

    rng.shuffle(feasible)

    result = []
    used_i = set()
    used_j = set()

    for i, j in feasible:
        if i not in used_i and j not in used_j:
            result.append((i, j))
            used_i.add(i)
            used_j.add(j)

    return result



# ------------------------------------------------------------


def assign_round_robin(C, start_index=0, vehicle_ids=None):
    """Assign requests in order, rotating through feasible vehicles.

    With vehicle_ids, remember the next buggy ID between dispatches.
    Without vehicle_ids, use a local rotation for standalone comparisons.
    Returned row indices always refer to the original cost matrix.
    """
    global rr_counter

    n, m = C.shape

    if vehicle_ids is not None:
        vehicle_ids = list(vehicle_ids)

        if len(vehicle_ids) != n or len(set(vehicle_ids)) != n:
            raise ValueError(
                "vehicle_ids must contain one unique ID per matrix row."
            )

    if n == 0 or m == 0:
        return []

    row_order = list(range(n))

    if vehicle_ids is None:
        pointer = start_index % n

    else:
        row_order.sort(key=lambda row: vehicle_ids[row])

        pointer = next(
            (
                position
                for position, row in enumerate(row_order)
                if vehicle_ids[row] >= rr_counter
            ),
            0,
        )

    pairs = []
    used = set()

    for j in range(m):
        if len(used) == n:
            break

        for _ in range(n):
            i = row_order[pointer]
            pointer = (pointer + 1) % n

            if i not in used and math.isfinite(C[i, j]):
                pairs.append((i, j))
                used.add(i)
                break

    if vehicle_ids is not None and pairs:
        last_row = pairs[-1][0]
        rr_counter = vehicle_ids[last_row] + 1

    return pairs


# ------------------------------------------------------------


def assign_greedy_minmax(C):
    """Maximize assignments, then minimize their highest cost.

    This is an exact bottleneck solver for the current cost matrix.
    The existing function name is kept for compatibility.
    """
    n, m = C.shape

    feasible = [
        [
            (j, float(C[i, j]))
            for j in range(m)
            if math.isfinite(C[i, j])
        ]
        for i in range(n)
    ]
    limits = sorted({
        cost for row in feasible for _, cost in row
    })

    if not limits:
        return []

    def matching_at(limit):
        request_owner = [-1] * m

        def assign_vehicle(i, seen_requests):
            for j, cost in feasible[i]:
                if cost > limit or j in seen_requests:
                    continue

                seen_requests.add(j)
                previous = request_owner[j]

                if previous == -1 or assign_vehicle(
                    previous, seen_requests
                ):
                    request_owner[j] = i
                    return True

            return False

        for i in range(n):
            assign_vehicle(i, set())

        return sorted(
            (i, j)
            for j, i in enumerate(request_owner)
            if i != -1
        )

    target_count = len(matching_at(limits[-1]))
    low, high = 0, len(limits) - 1

    while low < high:
        middle = (low + high) // 2

        if len(matching_at(limits[middle])) == target_count:
            high = middle
        else:
            low = middle + 1

    return matching_at(limits[low])



# ------------------------------------------------------------


def assign_simulated_annealing(
    C,
    max_iter=3000,
    T=1.0,
    alpha=0.99,
    rng=None,
):
    """Maximize feasible assignments, then search for a lower total cost.

    Returns (vehicle_row, request_column) pairs.
    Non-finite costs mark unavailable assignments.
    Cost optimization is heuristic; a global minimum is not guaranteed.
    T uses the same units as the assignment costs.
    """
    if rng is None:
        raise ValueError(
            "RNG must be provided for reproducible benchmarking."
        )

    if (
        isinstance(max_iter, bool)
        or not isinstance(max_iter, int)
        or max_iter < 0
    ):
        raise ValueError("max_iter must be a non-negative integer.")

    temperature = float(T)
    cooling = float(alpha)

    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("T must be finite and positive.")

    if not math.isfinite(cooling) or not 0.0 < cooling < 1.0:
        raise ValueError("alpha must be finite and between 0 and 1.")

    n, m = C.shape

    costs = {}
    for i in range(n):
        for j in range(m):
            cost = float(C[i, j])
            if math.isfinite(cost):
                costs[i, j] = cost

    feasible = list(costs)

    if not feasible:
        return []

    # One vehicle or one request: solve directly.
    if min(n, m) == 1:
        return [min(feasible, key=lambda pair: costs[pair])]

    # Build a randomized starting matching with maximum pair count.
    rng.shuffle(feasible)

    neighbours = [[] for _ in range(n)]
    for i, j in feasible:
        neighbours[i].append(j)

    request_owner = [-1] * m

    def augment(i, seen_requests):
        for j in neighbours[i]:
            if j in seen_requests:
                continue

            seen_requests.add(j)
            previous = request_owner[j]

            if previous == -1 or augment(previous, seen_requests):
                request_owner[j] = i
                return True

        return False

    vehicle_order = list(range(n))
    rng.shuffle(vehicle_order)

    for i in vehicle_order:
        augment(i, set())

    current = [
        (i, j)
        for j, i in enumerate(request_owner)
        if i != -1
    ]

    def objective(pairs):
        return math.fsum(costs[pair] for pair in pairs)

    current_cost = objective(current)
    best = current[:]
    best_cost = current_cost
    pair_count = len(current)

    # Every move preserves the number of assignments.
    moves = []

    if pair_count >= 2:
        moves.append("cycle")

    if pair_count < m:
        moves.append("request")

    if pair_count < n:
        moves.append("vehicle")

    for _ in range(max_iter):
        move = rng.choice(moves)

        count = rng.randint(
            2 if move == "cycle" else 1,
            pair_count,
        )

        selected = rng.sample(range(pair_count), count)
        candidate = current[:]

        if move == "vehicle":
            # Introduce an unused vehicle and rearrange a chain of pairs.
            used = {i for i, _ in current}

            carry = rng.choice([
                i for i in range(n)
                if i not in used
            ])

            for index in selected:
                i, j = candidate[index]
                candidate[index] = (carry, j)
                carry = i

        else:
            if move == "cycle":
                # Rotate requests among two or more assigned vehicles.
                carry = current[selected[-1]][1]

            else:
                # Introduce an unused request into a chain of pairs.
                used = {j for _, j in current}

                carry = rng.choice([
                    j for j in range(m)
                    if j not in used
                ])

            for index in selected:
                i, j = candidate[index]
                candidate[index] = (i, carry)
                carry = j

        # Reject moves containing an unavailable vehicle-request pair.
        if all(pair in costs for pair in candidate):
            candidate_cost = objective(candidate)
            delta = candidate_cost - current_cost

            if delta <= 0.0 or (
                temperature > 0.0
                and rng.random() < math.exp(-delta / temperature)
            ):
                current = candidate
                current_cost = candidate_cost

                if current_cost < best_cost:
                    best = current[:]
                    best_cost = current_cost

        # Cool after every attempted move, including rejected moves.
        temperature *= cooling

    return sorted(best)




ALGO_FUNCS = {
    "hungarian": assign_hungarian,
    "mcmf": assign_mcmf,
    "auction": assign_auction,
    "greedy": assign_greedy,
    "random": assign_random,
    "roundrobin": assign_round_robin,
    "greedy_minmax": assign_greedy_minmax,
    "annealing": assign_simulated_annealing,
}

def build_experiment_manifest():
    """Build the complete reproducible experiment plan."""

    manifest = []

    for scenario_name in EXPERIMENT_CONFIG:
        for trial_index, trial_seed in enumerate(
            TRIAL_SEEDS
        ):
            for algorithm_name in ALGO_FUNCS:

                manifest.append({
                    "depot_layout": DEPOT_LAYOUT,
                    "scenario": scenario_name,
                    "algorithm": algorithm_name,
                    "trial_index": trial_index,
                    "trial_seed": trial_seed,
                })

    return manifest

# ── Route helpers ─────────────────────────────────────────────────
def edge_coords(u, v):
    """Return road coordinates in travel order, as (latitude, longitude)."""
    data = safe_edge_data(u, v)

    if data is None:
        raise RuntimeError(f"Missing road segment {u} -> {v}.")

    start = (
        float(G.nodes[u]["y"]),
        float(G.nodes[u]["x"]),
    )
    end = (
        float(G.nodes[v]["y"]),
        float(G.nodes[v]["x"]),
    )

    geom = data.get("geometry")

    if geom is None:
        return [start, end]

    points = [
        (float(p[1]), float(p[0]))
        for p in geom.coords
    ]

    if len(points) < 2 or not all(
        math.isfinite(value)
        for point in points
        for value in point
    ):
        raise ValueError(
            f"Invalid road geometry for {u} -> {v}."
        )

    forward_error = (
        haversine(*start, *points[0])
        + haversine(*end, *points[-1])
    )

    reverse_error = (
        haversine(*start, *points[-1])
        + haversine(*end, *points[0])
    )

    if reverse_error < forward_error:
        points.reverse()

    return points

def path_to_coords(path_nodes):
    coords=[]
    for u,v in zip(path_nodes[:-1],path_nodes[1:]):
        seg=edge_coords(u,v)
        if coords and seg:
            if coords[-1]==seg[0]: coords.extend(seg[1:])
            else: coords.extend(seg)
        else: coords.extend(seg)
    return coords

def interpolate_on_edge(u, v, frac) -> Tuple[float, float]:
    """Return (longitude, latitude) at a fraction of the displayed road."""
    frac = float(frac)

    if not math.isfinite(frac) or not 0.0 <= frac <= 1.0:
        raise ValueError(
            f"Invalid road progress: {frac!r}."
        )

    points = edge_coords(u, v)

    if frac == 0.0:
        return (
            float(G.nodes[u]["x"]),
            float(G.nodes[u]["y"]),
        )

    if frac == 1.0:
        return (
            float(G.nodes[v]["x"]),
            float(G.nodes[v]["y"]),
        )

    lengths_m = [
        haversine(*a, *b) * 1000.0
        for a, b in zip(points[:-1], points[1:])
    ]

    total_length_m = math.fsum(lengths_m)

    if (
        not math.isfinite(total_length_m)
        or total_length_m <= 0.0
    ):
        raise ValueError(
            f"Zero or invalid geometry length for {u} -> {v}."
        )

    # Use the same geometry for the route line and the buggy position.
    remaining_m = frac * total_length_m

    for start, end, length_m in zip(
        points[:-1],
        points[1:],
        lengths_m,
    ):
        if length_m <= 0.0:
            continue

        if remaining_m <= length_m:
            fraction = remaining_m / length_m

            lat = start[0] + fraction * (
                end[0] - start[0]
            )
            lon = start[1] + fraction * (
                end[1] - start[1]
            )

            return lon, lat

        remaining_m -= length_m

    return points[-1][1], points[-1][0]

# ── UGV Dynamics: core step function ─────────────────────────────


def ensure_scheduler_run_active(run_id):
    """Stop an outdated scheduler step before it changes simulation state."""
    if (
        RUN.get("run_id") != run_id
        or not RUN.get("experiment_started", False)
        or RUN.get("run_error") is not None
    ):
        raise RuntimeError(
            f"Scheduler step for run {run_id!r} is no longer active."
        )




# ── API endpoints ─────────────────────────────────────────────────
# Bounded in-process cache for interactive map tiles.
_V2_TILE_AGENT = os.environ.get(
    "V2_TILE_USER_AGENT",
    "UTP-UGV-Research-MapViewer/1.0"
)
_V2_TILE_CACHE = OrderedDict()
_V2_TILE_MAX_ENTRIES = 512
_V2_TILE_TTL_SECONDS = 7 * 86400

@app.get("/tiles/{z}/{x}/{y}.png")
async def tiles(z: int, x: int, y: int):
    if not (0 <= z <= 19 and 0 <= x < 2**z and 0 <= y < 2**z):
        return Response(status_code=404)

    key = (z, x, y)
    now = time.monotonic()
    cached = _V2_TILE_CACHE.get(key)

    if cached is not None and cached[0] > now:
        _V2_TILE_CACHE.move_to_end(key)
        return Response(
            content=cached[1],
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": _V2_TILE_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get(
                "content-type", ""
            ).lower()

            if "image/png" not in content_type:
                raise ValueError("Tile provider returned non-PNG content")

    except (httpx.HTTPError, ValueError) as error:
        logger.warning(
            "[Tiles] Tile fetch failed for %s: %s",
            key, error
        )
        return Response(
            content="Map tile unavailable",
            status_code=502,
            media_type="text/plain",
        )

    _V2_TILE_CACHE[key] = (
        now + _V2_TILE_TTL_SECONDS,
        response.content,
    )
    _V2_TILE_CACHE.move_to_end(key)

    while len(_V2_TILE_CACHE) > _V2_TILE_MAX_ENTRIES:
        _V2_TILE_CACHE.popitem(last=False)

    return Response(
        content=response.content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800"},
    )

@app.get("/get_parking_slots")
async def get_parking_slots():
    return {
        # Keep this for the current viewer
        "parking_slots": parking_slots,

        # New permanent parking information
        "parking_registry": PARKING_REGISTRY,
    }

@app.post("/telemetry")
async def telemetry(m: Mover):
    if (
        RUN.get("canonical_mode", True)
        or RUN.get("batch_running", False)
    ):
        return {
            "ok": False,
            "error": (
                "External vehicle updates are disabled in canonical "
                "mode or while an experiment batch is running."
            ),
        }

    load_graph()
    m.node = nearest_node(m.lon, m.lat)
    movers[m.id] = m
    exec_q.setdefault(m.id, deque())
    serving.setdefault(m.id, None)
    phase[m.id] = "idle"
    mover_velocity.setdefault(m.id, 0.0)
    return {"ok": True}

@app.post("/request")
async def new_request(r: RideReq):
    if (
        RUN.get("canonical_mode", True)
        or RUN.get("batch_running", False)
    ):
        return {
            "ok": False,
            "error": (
                "Manual ride requests are disabled in canonical "
                "mode or while an experiment batch is running."
            ),
        }

    load_graph()
    r.pickup_node = nearest_node(r.pickup_lon, r.pickup_lat)
    r.drop_node   = nearest_node(r.drop_lon,   r.drop_lat)
    requests_waiting[r.id] = r
    register_request_record(r)
    return {"ok": True}

# Execute slow reset preparation off the ASGI event loop.
# The event-loop-owned flag prevents overlapping control resets.
_V2_CONTROL_RESET_BUSY = False
_V2_CONTROL_RESET_WORKER = None
_V2_RESET_STATE_LOCK = threading.RLock()

async def _v2_async_prepare_reset(operation, *args, **kwargs):
    global _V2_CONTROL_RESET_BUSY, _V2_CONTROL_RESET_WORKER

    if RUN.get("shutting_down"):
        raise RuntimeError("Cannot reset during shutdown.")

    if _V2_CONTROL_RESET_BUSY:
        raise RuntimeError("Another reset is already active.")

    if RUN.get("experiment_started") or RUN.get("batch_running"):
        raise RuntimeError("Cannot reset during a trial or batch.")

    _V2_CONTROL_RESET_BUSY = True

    def locked_reset():
        with _V2_RESET_STATE_LOCK:
            if RUN.get("shutting_down"):
                raise RuntimeError("Cannot reset during shutdown.")
            return operation(*args, **kwargs)

    try:
        worker = asyncio.create_task(
            asyncio.to_thread(locked_reset)
        )
        _V2_CONTROL_RESET_WORKER = worker
    except BaseException:
        _V2_CONTROL_RESET_BUSY = False
        raise

    def finish_worker(done):
        global _V2_CONTROL_RESET_BUSY, _V2_CONTROL_RESET_WORKER
        _V2_CONTROL_RESET_WORKER = None
        _V2_CONTROL_RESET_BUSY = False
        if not done.cancelled():
            done.exception()

    worker.add_done_callback(finish_worker)

    return await asyncio.shield(worker)

def _v2_selection_is_unchanged(algorithm, scenario, trial_index):
    """Reuse only a complete, unstarted preparation of this exact trial."""
    if (
        not isinstance(trial_index, int)
        or isinstance(trial_index, bool)
        or not 0 <= trial_index < len(TRIAL_SEEDS)
    ):
        return False

    paths = (
        RUN.get("csv_path"),
        RUN.get("settings_json_path"),
    )

    return (
        not RUN.get("shutting_down", False)
        and not RUN.get("experiment_started", False)
        and not RUN.get("batch_running", False)
        and not _V2_CONTROL_RESET_BUSY
        and not _V2_BATCH_PREP_BUSY
        and RUN.get("run_id") is not None
        and RUN.get("run_error") is None
        and not RUN.get("summary_written", False)
        and not RUN.get("vehicle_results_written", False)
        and RUN.get("workload_released", 0) == 0
        and tick == 0
        and ASSIGNMENT_METHOD == algorithm
        and RUN.get("algo") == algorithm
        and CURRENT_SCENARIO == scenario
        and RUN.get("trial_index") == trial_index
        and RUN.get("trial_seed") == TRIAL_SEEDS[trial_index]
        and RUN.get("workload_size", 0) > 0
        and all(
            isinstance(path, str)
            and bool(path)
            and os.path.isfile(path)
            for path in paths
        )
    )

@app.post("/set_algorithm")
async def set_algorithm(method: str = Body(..., embed=True)):
    if (
        RUN.get("experiment_started", False)
        or RUN.get("batch_running", False)
    ):
        return {
            "ok": False,
            "error": (
                "Cannot change the algorithm while a trial or "
                "experiment batch is running."
            ),
        }

    m = method.lower()
    if m not in ALGO_FUNCS:
        return {
            "ok": False,
            "error": f"Invalid method '{method}'",
        }

    if _v2_selection_is_unchanged(
        m, CURRENT_SCENARIO, RUN.get("trial_index")
    ):
        return {
            "ok": True,
            "method": m,
            "label": ALGO_NAMES.get(m, m),
            **get_benchmark_parking_payload(),
            "csv": RUN.get("csv_path"),
            "unchanged": True,
        }

    try:
        await _v2_async_prepare_reset(reset_run_state, new_algo=m)
    except (ValueError, RuntimeError, OSError) as error:
        return {"ok": False, "error": str(error)}

    return {
        "ok": True,
        "method": m,
        "label": ALGO_NAMES.get(m, m),
        **get_benchmark_parking_payload(),
        "csv": RUN["csv_path"],
    }

@app.post("/set_trial")
async def set_trial(
    trial_index: int = Body(..., embed=True),
    scenario: str = Body(None, embed=True)
):
    """Reset the simulation using a selected trial index."""

    if (
        RUN.get("experiment_started", False)
        or RUN.get("batch_running", False)
    ):
        return {
            "ok": False,
            "error": (
                "Cannot change the trial or scenario while a trial "
                "or experiment batch is running."
            ),
        }

    selected_scenario = (
        CURRENT_SCENARIO if scenario is None else scenario
    )

    if _v2_selection_is_unchanged(
        ASSIGNMENT_METHOD, selected_scenario, trial_index
    ):
        return {
            "ok": True,
            "scenario": CURRENT_SCENARIO,
            "trial_index": RUN["trial_index"],
            "trial_seed": RUN["trial_seed"],
            "algorithm": RUN["algo"],
            "workload_size": RUN["workload_size"],
            "unchanged": True,
        }

    try:
        trial_info = await _v2_async_prepare_reset(
            reset_to_trial,
            trial_index,
            scenario=scenario
        )

    except (ValueError, RuntimeError, OSError) as error:
        return {
            "ok": False,
            "error": str(error)
        }

    return {
        "ok": True,
        **trial_info
    }

@app.get("/experiment_manifest")
def get_experiment_manifest():
    """Return a summary of the planned experiment runs."""

    manifest = build_experiment_manifest()

    return {
        "total_runs": len(manifest),
        "scenarios": list(EXPERIMENT_CONFIG.keys()),
        "algorithms": list(ALGO_FUNCS.keys()),
        "trial_seeds": list(TRIAL_SEEDS),
        "first_run": manifest[0]
        if manifest
        else None,
        "last_run": manifest[-1]
        if manifest
        else None,
    }

@app.get("/experiment_status")
async def get_experiment_status():
    return _v2_unlocked_get_experiment_status()


def _v2_unlocked_get_experiment_status():
    """Return the progress of the automatic experiment batch."""

    return {
        "running": RUN.get(
            "batch_running",
            False
        ),
        "total_runs": RUN.get(
            "batch_total",
            0
        ),
        "completed_runs": RUN.get(
            "batch_completed",
            0
        ),
        "failed_runs": RUN.get("batch_failed", 0),
        "skipped_runs": RUN.get("batch_skipped", 0),
        "resumed_archives": list(
            RUN.get("batch_resumed_archives", [])
        ),
        "resume_enabled": RUN.get(
            "batch_resume_enabled", False
        ),
        "attempted_runs": (
            RUN.get("batch_completed", 0)
            + RUN.get("batch_failed", 0)
        ),
        "failures": list(RUN.get("batch_failures", [])),
        "current_run": RUN.get(
            "batch_current"
        ),
        "error": RUN.get(
            "batch_error"
        ),
    }

@app.post("/run_experiment_batch")
async def start_experiment_batch(
    max_runs: int = Body(None, embed=True),
    resume: bool = Body(False, embed=True),
):
    """Start the complete automatic experiment batch."""
    if _V2_CONTROL_RESET_BUSY:
        return {"ok": False, "message": "A control reset is in progress."}
    try:
        ensure_scheduler_running()
    except RuntimeError as error:
        return {"ok": False, "message": str(error)}

    if (
        RUN.get("batch_running", False)
        or RUN.get("experiment_started", False)
    ):
        return {
            "ok": False,
            "message": (
                "A trial or experiment batch is already running. "
                "Wait for it to finish before starting a batch."
            ),
        }

    all_runs = build_experiment_manifest()

    if max_runs is not None:
        if max_runs < 1:
            return {
                "ok": False,
                "message": (
                    "max_runs must be at least 1."
                )
            }

        total_runs = min(
            max_runs,
            len(all_runs)
        )
    else:
        total_runs = len(all_runs)

    batch_coro = run_experiment_batch(
        all_runs[:total_runs],
        resume=resume,
    )

    RUN["batch_running"] = True
    RUN["batch_total"] = total_runs
    RUN["batch_completed"] = 0
    RUN["batch_failed"] = 0
    RUN["batch_failures"] = []
    RUN["batch_skipped"] = 0
    RUN["batch_resumed_archives"] = []
    RUN["batch_resume_enabled"] = resume
    RUN["batch_current"] = None
    RUN["batch_run_id"] = None
    RUN["batch_error"] = None
    RUN["batch_task"] = None

    try:
        RUN["batch_task"] = asyncio.create_task(batch_coro)
        RUN["batch_task"].add_done_callback(finish_experiment_batch)
    except Exception as error:
        batch_coro.close()
        RUN["batch_running"] = False
        RUN["batch_error"] = f"{type(error).__name__}: {error}"
        raise

    return {
        "ok": True,
        "message": (
            "Automatic experiment batch started."
        ),
        "total_runs": total_runs
    }

def _v2_read_locked(snapshot_fn):
    with _V2_RESET_STATE_LOCK:
        return snapshot_fn()

@app.get("/get_algorithm")
async def get_algorithm():
    return await asyncio.to_thread(
        _v2_read_locked, _v2_unlocked_get_algorithm
    )


def _v2_unlocked_get_algorithm():
    return {
        "method": ASSIGNMENT_METHOD,
        "label": ALGO_NAMES.get(
            ASSIGNMENT_METHOD,
            ASSIGNMENT_METHOD,
        ),
        **get_benchmark_parking_payload(),
    }

@app.post("/spawn_requests")

async def spawn_requests(n: int = None):
    """Start the frozen canonical workload."""
    if _V2_CONTROL_RESET_BUSY:
        return {"ok": False, "error": "A control reset is in progress."}
    try:
        ensure_scheduler_running()
    except RuntimeError as error:
        return {"ok": False, "error": str(error)}

    if (
        RUN.get("batch_running", False)
        or RUN.get("experiment_started", False)
    ):
        return {
            "ok": False,
            "error": (
                "Manual start is unavailable while a trial or "
                "experiment batch is running."
            ),
        }

    if RUN.get("run_error") is not None:
        return {
            "ok": False,
            "error": (
                "This run stopped after an error. Reset the trial "
                "before starting again. "
                f"Details: {RUN['run_error']}"
            ),
        }

    if (
        RUN.get("summary_written", False)
        or RUN.get("vehicle_results_written", False)
    ):
        return {
            "ok": False,
            "error": (
                "This run has already saved its final results. "
                "Reset the trial before starting another run."
            ),
        }

    if RUN.get("algo") is None or RUN.get("csv_path") is None:
        return {
            "ok": False,
            "error": (
                "Trial is not prepared. Use /set_trial to "
                "select and prepare a canonical trial."
            ),
        }

    expected = RUN.get("workload_size", 0)

    if n is not None and n != expected:
        return {
            "ok": False,
            "error": (
                "The benchmark uses the frozen canonical workload. "
                f"Requested {n} requests, but the trial contains "
                f"{expected} requests."
            ),
        }

    try:
        settings_path = Path(RUN["settings_json_path"])
        settings = json.loads(
            settings_path.read_text(encoding="utf-8")
        )

        settings["execution"] = {
            "mode": "scheduler_manual"
        }

        staged = settings_path.with_name(
            settings_path.name + ".manual.tmp"
        )

        try:
            with staged.open(
                "x", encoding="utf-8", newline="\n"
            ) as stream:
                json.dump(
                    settings, stream,
                    indent=2, sort_keys=True,
                    allow_nan=False,
                )
                stream.write("\n")

            os.replace(staged, settings_path)

        finally:
            staged.unlink(missing_ok=True)

        load_graph()
        # Write the initial vehicle CSV before releasing requests.
        write_vehicle_matrix_csv()
        added = release_due_canonical_requests()
    except Exception as error:
        RUN["experiment_started"] = False
        RUN["run_error"] = f"{type(error).__name__}: {error}"
        logger.exception("[ManualStart] Trial preparation failed")
        return {
            "ok": False,
            "error": (
                "Could not start the trial. Reset the trial before "
                f"trying again. Details: {RUN['run_error']}"
            ),
        }

    # Start only after both preparation steps succeed.
    RUN["experiment_started"] = True

    return {
        "ok": True,
        "added": added,
        "requested": expected,
        "canonical": True,
        "queued": len(canonical_queue),
        "released_total": RUN.get("workload_released", 0),
        "pending_total": len(requests_waiting),
        "csv": RUN["csv_path"],
    }


def mover_payload(mover):
    payload = mover.model_dump()
    payload["request_id"] = serving.get(mover.id)
    payload["passengers_onboard"] = mover.passengers_onboard
    payload["mass_kg"] = mover.mass_kg
    return payload


@app.get("/state")
async def state():
    return await asyncio.to_thread(
        _v2_read_locked, _v2_unlocked_state
    )


def _v2_unlocked_state():

    return {
        "tick": tick,
        "fulfilled_requests": fulfilled_requests,
        "waiting": len(requests_waiting),
        "inflight": len(requests_inflight),
        "canonical_workload": RUN.get(
            "canonical_mode",
            False
        ),
        "trial_index": RUN.get(
            "trial_index",
            0
        ),
        "trial_seed": RUN.get(
            "trial_seed",
            TRIAL_SEED
        ),
        "demand_seed": RUN.get(
            "demand_seed",
            DEMAND_SEED
        ),
        "workload_size": RUN.get(
            "workload_size",
            0
        ),
        "workload_released": RUN.get(
            "workload_released",
            0
        ),
        "workload_queued": len(canonical_queue),

        **get_benchmark_parking_payload(),

        # NEW
        "summary_written": RUN["summary_written"],

        "movers": [mover_payload(m) for m in movers.values()],
        "requests": [
            r.model_dump()
            for r in list(requests_waiting.values())
            + list(requests_inflight.values())
        ]
    }

async def broadcast_state():
    if (
        _V2_CONTROL_RESET_BUSY
        or not _V2_RESET_STATE_LOCK.acquire(blocking=False)
    ):
        return

    try:
        if not sockets: return
        payload = {
            "tick": tick,
            "movers": [mover_payload(m) for m in movers.values()],
            "requests":[r.model_dump() for r in list(requests_waiting.values())+list(requests_inflight.values())],
            **get_benchmark_parking_payload(),
            "fulfilled_requests":fulfilled_requests,
        }
        msg = json.dumps(payload)
        clients = list(sockets)

    finally:
        _V2_RESET_STATE_LOCK.release()

    async def send_with_deadline(ws):
        try:
            await asyncio.wait_for(
                ws.send_text(msg), timeout=0.25
            )
            return None
        except Exception:
            return ws

    failures = await asyncio.gather(
        *(send_with_deadline(ws) for ws in clients)
    )

    for ws in failures:
        if ws is not None and ws in sockets:
            sockets.remove(ws)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    client = ws.client.host if ws.client else ""

    if not _control_request_allowed(
        ws.headers.get("host", ""),
        client,
        ws.headers.get("origin"),
        ws.headers.get("authorization", ""),
    ):
        await ws.close(code=1008)
        return

    await ws.accept()
    sockets.append(ws)

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected normally.")
    except Exception:
        logger.exception("WebSocket connection failed.")
    finally:
        if ws in sockets:
            sockets.remove(ws)


# ── Route setup ───────────────────────────────────────────────────
def set_route(m: Mover, path_nodes):
    close_charging_session(m.id, "departure")
    q=exec_q[m.id]; q.clear()
    for u,v in zip(path_nodes[:-1],path_nodes[1:]): q.append((u,v))
    m._edge_u=None; m._edge_v=None; m._edge_prog=0.0
    m.route_coords=path_to_coords(path_nodes)


def update_travel_time(step):
    global total_travel_time
    total_travel_time += step


# F-038C: Cache validation and derived drivetrain limits.
# The complete parameter tuple is the cache key, so changed
# simulation parameters are validated again automatically.
@lru_cache(maxsize=32)
def _motion_static_data(settings):
    (
        gravity,
        rolling_coefficient,
        air_density,
        drag_coefficient,
        frontal_area,
        rotational_factor,
        max_acceleration,
        max_speed,
        motor_torque,
        gear_ratio,
        wheel_radius,
        gear_efficiency,
        drivetrain_efficiency,
        motor_power,
    ) = settings

    parameters_finite = all(
        math.isfinite(value) for value in settings
    )

    if not parameters_finite:
        return False, False, None, None

    parameters_valid = not (
        min(
            gravity,
            air_density,
            frontal_area,
            max_acceleration,
            max_speed,
            motor_torque,
            gear_ratio,
            wheel_radius,
            motor_power,
        ) <= 0.0
        or min(
            rolling_coefficient,
            drag_coefficient,
        ) < 0.0
        or rotational_factor < 1.0
        or not (
            0.0 < drivetrain_efficiency
            <= gear_efficiency <= 1.0
        )
    )

    if not parameters_valid:
        return True, False, None, None

    torque_force_limit = (
        motor_torque
        * gear_ratio
        * gear_efficiency
        / wheel_radius
    )

    wheel_power_limit = (
        motor_power * gear_efficiency
    )

    return (
        True,
        True,
        torque_force_limit,
        wheel_power_limit,
    )

def calculate_motion_step(v_current, mass_kg, step_budget_s=None):
    """One constant-acceleration phase, limited by torque and shaft power.

    Return (final_speed, acceleration, wheel_force, duration, distance).
    The power constraint uses the highest speed reached in this phase.
    Road resistance is approximated at the phase's starting speed.
    """
    v = float(v_current)
    mass = float(mass_kg)
    dt = float(DT_S if step_budget_s is None else step_budget_s)

    settings = (
        UGV_G, UGV_C_RR, UGV_RHO, UGV_C_D, UGV_A,
        UGV_ROTATIONAL_MASS_FACTOR, UGV_ACCEL_MAX, UGV_V_MAX,
        UGV_T_MOTOR_MAX, UGV_GEAR_RATIO, UGV_WHEEL_RADIUS,
        UGV_GEAR_EFFICIENCY, UGV_ETA, UGV_MOTOR_SHAFT_POWER_W,
    )

    
    (
        parameters_finite,
        parameters_valid,
        torque_force_limit,
        wheel_power_limit,
    ) = _motion_static_data(settings)

    if not (
        math.isfinite(v)
        and math.isfinite(mass)
        and math.isfinite(dt)
        and parameters_finite
    ):
        raise ValueError(
            "Motion inputs and parameters must be finite."
        )

    if mass <= 0.0 or dt <= 0.0 or not 0.0 <= v <= UGV_V_MAX:
        raise ValueError(
            "Invalid mass, time step or speed."
        )

    if not parameters_valid:
        raise ValueError(
            "Invalid motion limits or drivetrain efficiencies."
        )


    resistance = (
        mass * UGV_G * UGV_C_RR
        + 0.5 * UGV_RHO * UGV_C_D * UGV_A * v * v
    )
    effective_mass = UGV_ROTATIONAL_MASS_FACTOR * mass

    if (
        resistance > torque_force_limit
        or resistance * v > wheel_power_limit
    ):
        raise RuntimeError(
            "The configured motor cannot maintain this speed and load."
        )

    # Solve:
    # (resistance + effective_mass*a) * (v + a*dt) <= P_wheel.
    # This limits power throughout the constant-acceleration phase.
    headroom = max(0.0, wheel_power_limit - resistance * v)
    linear = effective_mass * v + resistance * dt
    denominator = linear + math.sqrt(
        linear * linear
        + 4.0 * effective_mass * dt * headroom
    )
    power_acceleration = (
        2.0 * headroom / denominator
        if denominator > 0.0
        else 0.0
    )

    acceleration = max(0.0, min(
        UGV_ACCEL_MAX,
        (UGV_V_MAX - v) / dt,
        (torque_force_limit - resistance) / effective_mass,
        power_acceleration,
    ))

    final_speed = min(UGV_V_MAX, v + acceleration * dt)
    force = resistance + effective_mass * acceleration
    distance = 0.5 * (v + final_speed) * dt

    return final_speed, acceleration, force, dt, distance
    
def ensure_scheduler_running():
    """Reject new work when the simulation scheduler is unavailable."""
    if RUN.get("shutting_down", False):
        raise RuntimeError(
            "The backend is shutting down. "
            "Restart it before starting another run."
        )

    task = RUN.get("scheduler_task")
    if task is not None and not task.done():
        return

    detail = (
        RUN.get("scheduler_error")
        or "The scheduler task is not running."
    )
    raise RuntimeError(
        f"{detail} Restart the backend before starting another run."
    )


def finish_scheduler(task):
    """Record scheduler termination and stop any unfinished active run."""
    cancelled = task.cancelled()
    error = None if cancelled else task.exception()

    if RUN.get("scheduler_task") is not task:
        return

    if cancelled:
        message = "CancelledError: Simulation scheduler was cancelled."
    elif error is not None:
        message = f"{type(error).__name__}: {error}"
    else:
        message = "RuntimeError: Simulation scheduler exited unexpectedly."

    RUN["scheduler_task"] = None
    RUN["scheduler_error"] = message

    if RUN.get("experiment_started", False):
        RUN["experiment_started"] = False
        if (
            RUN.get("run_error") is None
            and not experiment_run_is_complete()
        ):
            RUN["run_error"] = message

    if cancelled and RUN.get("shutting_down", False):
        logger.info("[Scheduler] %s", message)
    else:
        logger.error("[Scheduler] %s", message)

    finish_charging_sessions(
        "shutdown"
        if RUN.get("shutting_down")
        else "scheduler_stopped"
    )

# ── Scheduler loop ────────────────────────────────────────────────
_V2_SCHEDULER_STEP_WORKER = None

def _v2_scheduler_step_atomic(step_run_id):
    """Execute one complete simulation step under the shared-state lock."""
    with _V2_RESET_STATE_LOCK:
        engine = None
        try:
            ensure_scheduler_run_active(step_run_id)

            engine = get_shared_ride_engine()
            engine.step()
            engine.flush()

            run_finished = all_requests_and_vehicles_settled()

            if run_finished:
                engine.finalise_requests()
                finish_charging_sessions("run_complete")

                write_vehicle_results_if_complete()
                write_run_summary_if_complete()
                write_vehicle_matrix_csv()

                if not (
                    RUN.get("summary_written", False)
                    and RUN.get("vehicle_results_written", False)
                ):
                    raise RuntimeError(
                        "Settled run did not produce final output files."
                    )
            return run_finished
        except Exception:
            if engine is not None:
                try:
                    engine.flush()
                except Exception:
                    logger.exception(
                        "[Scheduler] Worker error flush failed."
                    )
            raise

async def scheduler_loop():
    """Execute simulation steps without blocking the ASGI event loop."""
    global _V2_SCHEDULER_STEP_WORKER
    while True:
        if RUN.get("shutting_down", False):
            await asyncio.sleep(SIM_SLEEP_S)
            continue
        step_run_id = RUN.get("run_id")
        engine = None

        try:
            if _V2_CONTROL_RESET_BUSY or _V2_BATCH_PREP_BUSY:
                await asyncio.sleep(SIM_SLEEP_S)
                continue
            if not RUN["experiment_started"]:
                finish_charging_sessions(
                    "run_error"
                    if RUN.get("run_error")
                    else "stopped"
                )
                await asyncio.sleep(SIM_SLEEP_S)
                continue

            worker = asyncio.create_task(
                asyncio.to_thread(
                    _v2_scheduler_step_atomic,
                    step_run_id,
                )
            )
            _V2_SCHEDULER_STEP_WORKER = worker

            def _clear_step_worker(done):
                global _V2_SCHEDULER_STEP_WORKER
                if _V2_SCHEDULER_STEP_WORKER is done:
                    _V2_SCHEDULER_STEP_WORKER = None

            worker.add_done_callback(_clear_step_worker)

            try:
                run_finished = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Do not abandon a partially executed simulation step.
                await asyncio.shield(worker)
                raise

            await broadcast_state()
            ensure_scheduler_run_active(step_run_id)

            if run_finished:
                RUN["experiment_started"] = False

            await asyncio.sleep(SIM_SLEEP_S)

        except Exception as error:
            if RUN.get("run_id") == step_run_id:
                RUN["experiment_started"] = False

                if RUN.get("run_error") is None:
                    RUN["run_error"] = (
                        f"{type(error).__name__}: {error}"
                    )

                logger.exception(
                    "[Scheduler] Run %s stopped. "
                    "Reset before restarting.",
                    step_run_id,
                )

                if engine is not None:
                    try:
                        engine.flush()
                    except Exception:
                        logger.exception(
                            "[Scheduler] Could not flush "
                            "diagnostic rows."
                        )

            else:
                logger.warning(
                    "[Scheduler] Discarding error "
                    "from replaced run %s: %s",
                    step_run_id,
                    error,
                )

            await asyncio.sleep(SIM_SLEEP_S)     
    

async def on_start():
    for task_key in ("batch_task", "scheduler_task"):
        task = RUN.get(task_key)
        if task is not None and not task.done():
            raise RuntimeError(
                "Cannot start the backend while its previous background "
                "tasks are still running. Stop it fully before restarting."
            )

    RUN["shutting_down"] = False
    initialise_benchmark()

    reset_to_trial(
        0,
        algorithm=ASSIGNMENT_METHOD,
        scenario=CURRENT_SCENARIO,
    )

    logger.debug("[Log] Fleet log: %s", RUN["fleet_log_csv_path"])
    logger.debug("[Log] Velocity log: %s", RUN["velocity_log_csv_path"])

    scheduler_coro = scheduler_loop()
    try:
        scheduler_task = asyncio.create_task(scheduler_coro)
    except Exception:
        scheduler_coro.close()
        raise

    RUN["scheduler_error"] = None
    RUN["scheduler_task"] = scheduler_task
    scheduler_task.add_done_callback(finish_scheduler)


async def on_shutdown():
    """Stop the batch and scheduler before the backend exits."""
    RUN["shutting_down"] = True

    # Finish an active simulation step before changing run state.
    active_step = _V2_SCHEDULER_STEP_WORKER
    if active_step is not None:
        await asyncio.gather(
            active_step,
            return_exceptions=True,
        )

    # Wait for preparation before cancelling batch tasks.
    active_prepare = _V2_BATCH_PREP_WORKER
    if active_prepare is not None:
        await asyncio.gather(
            active_prepare,
            return_exceptions=True,
        )

    tracked_tasks = (
        (RUN.get("scheduler_task"), finish_scheduler),
        (RUN.get("batch_task"), finish_experiment_batch),
    )

    # Preserve errors from tasks that finished before shutdown began.
    for task, finish in tracked_tasks:
        if task is not None and task.done():
            finish(task)

    if RUN.get("experiment_started", False):
        RUN["experiment_started"] = False
        if (
            RUN.get("run_error") is None
            and not experiment_run_is_complete()
        ):
            RUN["run_error"] = (
                "CancelledError: Backend shutdown interrupted the active run."
            )

    tasks = [
        task
        for task, _ in tracked_tasks
        if task is not None
    ]

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    reset_worker = _V2_CONTROL_RESET_WORKER
    if reset_worker is not None:
        await asyncio.gather(
            reset_worker,
            return_exceptions=True,
        )

    finish_charging_sessions("shutdown")
    logger.info("[Shutdown] Background tasks stopped.")

def calculate_stop_motion_step(
    v_current,
    mass_kg,
    distance_to_stop_m,
    step_budget_s,
):
    """Advance one flat-road motion phase towards a service stop.

    Return (speed, acceleration, force, elapsed_seconds, distance).
    A phase can end before the time budget when braking begins
    or the destination is reached.
    """
    v = float(v_current)
    mass = float(mass_kg)
    remaining = float(distance_to_stop_m)
    budget = float(step_budget_s)
    brake = float(UGV_BRAKE_MAX)

    if not all(map(math.isfinite, (
        v, mass, remaining, budget, brake,
    ))):
        raise ValueError("Stop-motion inputs must be finite.")

    if not 0.0 <= v <= UGV_V_MAX:
        raise ValueError(
            "Speed must be between zero and the speed cap."
        )

    if (
        mass <= 0.0
        or remaining < 0.0
        or budget <= 0.0
        or brake <= 0.0
    ):
        raise ValueError(
            "Invalid mass, distance, time budget or braking limit."
        )

    if remaining == 0.0:
        if v != 0.0:
            raise RuntimeError(
                "A moving vehicle cannot already be at its stop."
            )
        return 0.0, 0.0, 0.0, 0.0, 0.0

    braking_distance = v * v / (2.0 * brake)
    tolerance_m = 1e-9 * max(
        1.0, remaining, braking_distance
    )

    if remaining + tolerance_m < braking_distance:
        raise RuntimeError(
            "Insufficient distance to stop within the braking limit."
        )


    # Exact steady-cruise fast path. Preserve the original
    # validation, braking-distance check, and all other branches.
    if (
        v == UGV_V_MAX
        and remaining > (
            braking_distance + tolerance_m
            + v * budget + 1e-6
        )
    ):
        settings = (
            UGV_G, UGV_C_RR, UGV_RHO, UGV_C_D, UGV_A,
            UGV_ROTATIONAL_MASS_FACTOR, UGV_ACCEL_MAX,
            UGV_V_MAX, UGV_T_MOTOR_MAX, UGV_GEAR_RATIO,
            UGV_WHEEL_RADIUS, UGV_GEAR_EFFICIENCY,
            UGV_ETA, UGV_MOTOR_SHAFT_POWER_W,
        )
        finite, valid, torque_limit, power_limit = (
            _motion_static_data(settings)
        )

        if finite and valid:
            resistance = (
                mass * UGV_G * UGV_C_RR
                + 0.5 * UGV_RHO * UGV_C_D
                * UGV_A * v * v
            )

            # Never bypass motor torque or shaft-power limits.
            if (
                resistance <= torque_limit
                and resistance * v <= power_limit
            ):
                return (
                    v, 0.0, resistance, budget,
                    0.5 * (v + v) * budget,
                )

    if v > 0.0 and remaining <= braking_distance + tolerance_m:
        # Decelerate so the vehicle stops exactly at the destination.
        acceleration = -(v * v) / (2.0 * remaining)
        stop_time = 2.0 * remaining / v
        elapsed = min(budget, stop_time)

        resistance = (
            mass * UGV_G * UGV_C_RR
            + 0.5 * UGV_RHO * UGV_C_D * UGV_A * v * v
        )
        force = (
            UGV_ROTATIONAL_MASS_FACTOR * mass * acceleration
            + resistance
        )

        if elapsed == stop_time:
            return 0.0, acceleration, force, elapsed, remaining

    else:
        # Use the existing torque- and power-limited controller.
        _, acceleration, force, _, _ = calculate_motion_step(
            v,
            mass_kg=mass,
            step_budget_s=budget,
        )

        if acceleration < 0.0 or (
            v == 0.0 and acceleration == 0.0
        ):
            raise RuntimeError(
                "The motor cannot execute this stop-to-stop leg."
            )

        headroom = remaining - braking_distance

        if acceleration > 0.0:
            # Find the transition from acceleration to braking.
            qa = (
                0.5 * acceleration
                * (1.0 + acceleration / brake)
            )
            qb = v * (1.0 + acceleration / brake)

            to_braking = (
                2.0 * headroom
                / (
                    qb
                    + math.sqrt(
                        qb * qb + 4.0 * qa * headroom
                    )
                )
            )
            to_cruise = (UGV_V_MAX - v) / acceleration
            elapsed = min(budget, to_braking, to_cruise)
        else:
            elapsed = min(budget, headroom / v)

    if elapsed <= 0.0 or not math.isfinite(elapsed):
        raise RuntimeError(
            "Stop-motion calculation made no progress."
        )

    speed = max(
        0.0,
        min(UGV_V_MAX, v + acceleration * elapsed),
    )
    distance = 0.5 * (v + speed) * elapsed

    if distance > remaining + tolerance_m:
        raise RuntimeError(
            "Stop-motion calculation overshot the destination."
        )

    return (
        speed,
        acceleration,
        force,
        elapsed,
        min(distance, remaining),
    )

def initialise_benchmark():
    """Load the fixed inputs and choose the shared depot."""
    global parking_slots, BENCHMARK_DEPOT

    load_graph()
    load_hotspots()

    parking_slots = get_registered_parking_coordinates()
    logger.info(
        "[Parking] Using %s permanent registered parking slots.",
        len(parking_slots),
    )

    BENCHMARK_DEPOT = find_common_benchmark_depot(parking_slots)


def run_headless(
    scenario,
    algorithm,
    trial_index,
    *,
    max_sim_s=4 * 3600,
    flush_every=200,
    record_velocity=False,
):
    """Run one trial synchronously in a standalone process."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "Run headless experiments in a separate process."
        )

    for key in ("scheduler_task", "batch_task"):
        task = RUN.get(key)
        if task is not None and not task.done():
            raise RuntimeError(
                "A scheduler or batch task is already active."
            )

    if any(
        RUN.get(key, False)
        for key in (
            "experiment_started",
            "batch_running",
            "headless_running",
        )
    ):
        raise RuntimeError("Another experiment is already active.")

    if type(flush_every) is not int or flush_every < 1:
        raise ValueError("flush_every must be a positive integer.")

    max_sim_s = float(max_sim_s)
    if not math.isfinite(max_sim_s) or max_sim_s <= 0.0:
        raise ValueError("max_sim_s must be finite and positive.")

    if not math.isfinite(DT_S) or DT_S <= 0.0:
        raise ValueError("DT_S must be finite and positive.")

    if scenario not in EXPERIMENT_CONFIG or algorithm not in ALGO_FUNCS:
        raise ValueError("Unknown scenario or algorithm.")

    if type(trial_index) is not int or not 0 <= trial_index < len(TRIAL_SEEDS):
        raise ValueError("Invalid trial_index.")

    if type(record_velocity) is not bool:
        raise ValueError("record_velocity must be True or False.")

    started = time.perf_counter()
    engine = None
    prepared_run_id = None
    folder = None
    RUN["headless_running"] = True

    def save_status(status, error=None):
        report = {
            "status": status,
            "run_id": prepared_run_id,
            "scenario": scenario,
            "algorithm": algorithm,
            "trial_index": trial_index,
            "trial_seed": RUN.get("trial_seed"),
            "completed_requests": fulfilled_requests,
            "energy_blocked_pair_evaluations": (
                None if engine is None
                else engine.energy_blocked_pair_evaluations
            ),
            "energy_blocked_insertions": (
                None if engine is None
                else engine.energy_blocked_insertions
            ),
            "simulation_time_s": simulation_time_s(),
            "wall_time_s": time.perf_counter() - started,
            "results_dir": str(folder.resolve()),
            "error": error,
        }

        (folder / "headless_status.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
        return report

    try:
        if BENCHMARK_DEPOT is None:
            initialise_benchmark()

        reset_to_trial(
            trial_index,
            algorithm=algorithm,
            scenario=scenario,
        )

        prepared_run_id = RUN["run_id"]
        folder = Path(RUN["request_csv_path"]).parent

        settings_path = Path(RUN["settings_json_path"])
        settings = json.loads(
            settings_path.read_text(encoding="utf-8")
        )
        settings["execution"] = {
            "mode": "headless",
            "tick_sleep_s": 0.0,
            "flush_every_ticks": flush_every,
            "velocity_log_enabled": record_velocity,
            "max_sim_s": max_sim_s,
        }
        settings_path.write_text(
            json.dumps(settings, indent=2) + "\n",
            encoding="utf-8",
        )

        engine = get_shared_ride_engine()
        engine.record_velocity = record_velocity

        write_vehicle_matrix_csv()
        save_status("running")

        while not all_requests_and_vehicles_settled():
            if RUN["run_id"] != prepared_run_id:
                raise RuntimeError(
                    "The active run changed during headless execution."
                )

            if RUN.get("run_error") is not None:
                raise RuntimeError(RUN["run_error"])

            if (tick + 1) * DT_S > max_sim_s + 1e-9:
                raise TimeoutError(
                    "The next step would exceed max_sim_s."
                )

            engine.step()

            if tick % flush_every == 0:
                engine.flush()

        engine.flush()
        engine.finalise_requests()
        finish_charging_sessions("run_complete")

        write_vehicle_results_if_complete()
        write_run_summary_if_complete()
        write_vehicle_matrix_csv()

        if not experiment_run_is_complete():
            raise RuntimeError(
                "Run ended without complete final results."
            )

        return save_status("completed")

    except (Exception, KeyboardInterrupt) as error:
        if (
            prepared_run_id is not None
            and RUN.get("run_id") == prepared_run_id
        ):
            message = f"{type(error).__name__}: {error}"
            RUN["run_error"] = message

            if engine is not None:
                try:
                    engine.flush()
                except Exception:
                    logger.exception(
                        "[Headless] Could not flush buffered rows."
                    )

            try:
                finish_charging_sessions("run_error")
            except Exception:
                logger.exception(
                    "[Headless] Could not close charging sessions."
                )

            if folder is not None:
                try:
                    save_status("failed", message)
                except Exception:
                    logger.exception(
                        "[Headless] Could not save failure status."
                    )
        raise

    finally:
        RUN["headless_running"] = False


def headless_main():
    global DEPOT_LAYOUT
    global DISPATCH_WINDOW_S, DISPATCH_URGENT_SLACK_S
    global ALPHA_ENERGY, BETA_TIME, SOC_COST_WEIGHT
    global ASSIGNMENT_COST_MODE
    global COST_ENERGY_REFERENCE_WH, COST_TIME_REFERENCE_S
    import argparse

    parser = argparse.ArgumentParser(
        description="Run one headless fleet experiment."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(EXPERIMENT_CONFIG),
        default="S1",
    )
    parser.add_argument(
        "--algorithm",
        choices=sorted(ALGO_FUNCS),
        default="hungarian",
    )
    parser.add_argument("--trial-index", type=int, default=0)
    parser.add_argument("--max-sim-s", type=float, default=14400.0)
    parser.add_argument("--flush-every", type=int, default=200)
    parser.add_argument("--velocity-log", action="store_true")
    parser.add_argument(
        "--initial-soc-range",
        type=float,
        nargs=2,
        metavar=("LOW", "HIGH"),
        help="Starting SOC fractions, for example 0.20 0.22.",
    )
    parser.add_argument(
        "--alpha-energy",
        type=float,
        default=ALPHA_ENERGY,
        metavar="ALPHA",
        help="Energy cost weight; 0 disables the explicit energy cost term.",
    )
    parser.add_argument(
        "--beta-time",
        type=float,
        default=BETA_TIME,
        metavar="BETA",
        help="Time cost weight; 0 disables the explicit time cost term.",
    )
    parser.add_argument(
        "--soc-cost-weight",
        type=float,
        default=None,
        metavar="GAMMA",
        help=(
            "SOC preference weight. Defaults to the module-level "
            "SOC_COST_WEIGHT (0.0); override explicitly when needed."
        ),
    )

    parser.add_argument(
        "--cost-mode",
        choices=("raw", "normalized"),
        default=ASSIGNMENT_COST_MODE,
        help="Assignment cost formula.",
    )

    parser.add_argument(
        "--energy-reference-wh",
        type=float,
        default=COST_ENERGY_REFERENCE_WH,
        help="Positive energy reference for normalized costs.",
    )

    parser.add_argument(
        "--time-reference-s",
        type=float,
        default=COST_TIME_REFERENCE_S,
        help="Positive time reference for normalized costs.",
    )
    parser.add_argument(
        "--depot-layout",
        choices=("single", "two", "three"),
        default=DEPOT_LAYOUT,
        help="Single, two, or three registered depot locations.",
    )
    parser.add_argument(
        "--dispatch-window-s",
        type=float,
        default=DISPATCH_WINDOW_S,
        metavar="SECONDS",
        help="Fixed dispatch window in simulation seconds; 0 dispatches every tick.",
    )
    parser.add_argument(
        "--dispatch-urgent-slack-s",
        type=float,
        default=DISPATCH_URGENT_SLACK_S,
        metavar="SECONDS",
        help=(
            "One early dispatch per waiting request entering this deadline slack. "
            "0 disables early dispatch; ignored when the dispatch window is 0."
        ),
    )
    args = parser.parse_args()

    gamma = (
        SOC_COST_WEIGHT
        if args.soc_cost_weight is None
        else args.soc_cost_weight
    )

    for option, value in (
        ("--alpha-energy", args.alpha_energy),
        ("--beta-time", args.beta_time),
        ("--soc-cost-weight", gamma),
        ("--dispatch-window-s", args.dispatch_window_s),
        ("--dispatch-urgent-slack-s", args.dispatch_urgent_slack_s),
    ):
        if not math.isfinite(value) or value < 0.0:
            parser.error(
                f"{option} must be finite and non-negative."
            )

    for option, value in (
        ("--energy-reference-wh", args.energy_reference_wh),
        ("--time-reference-s", args.time_reference_s),
    ):
        if not math.isfinite(value) or value <= 0.0:
            parser.error(
                f"{option} must be finite and positive."
            )

    if args.dispatch_urgent_slack_s >= MAX_WAIT_S:
        parser.error(
            "--dispatch-urgent-slack-s must be smaller than MAX_WAIT_S."
        )

    DEPOT_LAYOUT = args.depot_layout
    DISPATCH_WINDOW_S = float(args.dispatch_window_s)
    DISPATCH_URGENT_SLACK_S = float(args.dispatch_urgent_slack_s)
    ASSIGNMENT_COST_MODE = args.cost_mode
    COST_ENERGY_REFERENCE_WH = float(args.energy_reference_wh)
    COST_TIME_REFERENCE_S = float(args.time_reference_s)
    ALPHA_ENERGY = float(args.alpha_energy)
    BETA_TIME = float(args.beta_time)
    SOC_COST_WEIGHT = float(gamma)

    logger.info(
        "[Headless] Cost mode=%s | alpha=%g beta=%g gamma=%g "
        "| energy_reference_Wh=%g time_reference_s=%g",
        ASSIGNMENT_COST_MODE,
        ALPHA_ENERGY,
        BETA_TIME,
        SOC_COST_WEIGHT,
        COST_ENERGY_REFERENCE_WH,
        COST_TIME_REFERENCE_S,
    )

    logger.info(
        "[Headless] Dispatch window=%g s | urgent slack=%g s",
        DISPATCH_WINDOW_S,
        DISPATCH_URGENT_SLACK_S,
    )

    if args.initial_soc_range is not None:
        low, high = args.initial_soc_range

        if (
            not math.isfinite(low)
            or not math.isfinite(high)
            or not 0.0 <= low <= high <= 1.0
        ):
            parser.error(
                "Require 0 <= initial SOC LOW <= HIGH <= 1."
            )

        EXPERIMENT_CONFIG[args.scenario] = {
            **EXPERIMENT_CONFIG[args.scenario],
            "initial_soc_range": [low, high],
        }

    try:
        result = run_headless(
            args.scenario,
            args.algorithm,
            args.trial_index,
            max_sim_s=args.max_sim_s,
            flush_every=args.flush_every,
            record_velocity=args.velocity_log,
        )
    except KeyboardInterrupt:
        print("Headless run interrupted.", file=sys.stderr)
        return 130
    except Exception:
        logger.exception("[Headless] Run failed")
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(headless_main())