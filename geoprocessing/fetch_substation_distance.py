"""
geoprocessing/fetch_substation_distance.py
-------------------------------------------
Queries OpenStreetMap via Overpass API to determine:
1. Distance to nearest qualifying substation (>=33 kV), per criteria.md Criterion 4.
2. Distance to nearest high-voltage transmission line (>=33 kV).

CRITERIA.MD REQUIREMENT & ARCHITECTURAL DECISION ON UNTAGGED SUBSTATIONS:
--------------------------------------------------------------------------
Criteria.md Criterion 4 specifies that a 50 MWp plant requires interconnection at
>=33 kV (or >=66 kV).

In OpenStreetMap, power features fall into several categories:
- power=substation with explicit voltage tag (e.g. voltage=110000, voltage=66kV, voltage=33kV)
- power=substation with substation=transmission (in India, transmission substations are >=66kV)
- power=substation with no voltage tag at all
- power=line (overhead transmission line conductors)
- power=minor_line (11kV / low-voltage distribution poles)

Untagged Substation Decision:
Many rural OSM nodes in Karnataka are tagged simply as 'power=substation' without
voltage information. In rural India, a large fraction of these are small 11kV/415V
distribution transformers or village substations which CANNOT evacuate 50 MWp of solar
power under KERC/KPTCL grid code.
Therefore:
- Primary qualifying substation: Requires confirmed voltage >= 33,000 V OR
  substation="transmission".
- Untagged substations are EXCLUDED from the primary qualifying distance to prevent
  false positives (e.g. village distribution boxes).
- If an untagged substation is closer than the nearest confirmed >=33kV substation,
  the result is flagged with `low_confidence = True` and an explanatory reason string,
  indicating that field/KPTCL single-line diagram verification is required.
- Transmission lines (power=line) are tracked as a separate secondary metric and
  are NEVER substituted for an actual substation.
"""

import math
import threading
import time
from typing import NamedTuple, Optional, Tuple, Dict, Any
import requests


# ---------------------------------------------------------------------------
# Exceptions & Return Types
# ---------------------------------------------------------------------------

class SubstationDataUnavailable(Exception):
    """Raised when all Overpass API endpoints fail and substation proximity cannot be determined."""
    pass


class GridConnectionInfo(NamedTuple):
    """
    Structured grid connection details separating substation from transmission line.

    Fields
    ------
    substation_dist_km : float | None
        Euclidean distance in km to nearest qualifying (>=33 kV) substation.
    transmission_line_dist_km : float | None
        Euclidean distance in km to nearest >=33 kV transmission line corridor.
    nearest_any_substation_dist_km : float | None
        Distance to nearest substation of any voltage (including untagged).
    substation_name : str | None
        Name or operator tag of the matched qualifying substation.
    substation_voltage_v : float | None
        Voltage in Volts of the matched qualifying substation.
    substation_osm_id : str | None
        OSM type and ID of matched substation (e.g. 'way/336571787').
    transmission_line_osm_id : str | None
        OSM type and ID of matched transmission line (e.g. 'way/336571794').
    low_confidence : bool
        True if the nearest substation lacks voltage tags or data is incomplete.
    confidence_reason : str | None
        Reason for low_confidence flag.
    """
    substation_dist_km: Optional[float]
    transmission_line_dist_km: Optional[float]
    nearest_any_substation_dist_km: Optional[float]
    substation_name: Optional[str]
    substation_voltage_v: Optional[float]
    substation_osm_id: Optional[str]
    transmission_line_osm_id: Optional[str]
    low_confidence: bool
    confidence_reason: Optional[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two points in kilometers."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def parse_osm_voltage_v(voltage_str: Any) -> Optional[float]:
    """
    Parse an OSM voltage tag into Volts (float).
    Handles '110000', '66 kV', '33kV', multi-voltages '110000;11000' (takes max), etc.
    """
    if not voltage_str:
        return None
    raw = str(voltage_str).replace(",", "").strip()
    parts = raw.split(";")
    max_v = 0.0
    found = False

    for part in parts:
        s = part.strip().lower()
        if not s:
            continue
        multiplier = 1.0
        if "kv" in s:
            multiplier = 1000.0
            s = s.replace("kv", "").strip()
        elif "v" in s:
            s = s.replace("v", "").strip()

        try:
            val = float(s) * multiplier
            # If value is e.g. 33, 66, 110, 220, 400 without kV unit
            if val < 1000.0 and multiplier == 1.0 and val >= 11.0:
                val *= 1000.0
            max_v = max(max_v, val)
            found = True
        except ValueError:
            continue

    return max_v if found else None


def _get_element_min_distance(lat: float, lon: float, el: dict) -> Tuple[float, Optional[Tuple[float, float]]]:
    """Compute minimum distance from (lat, lon) to an OSM node, way, or relation geometry."""
    min_dist = float('inf')
    closest_pt = None
    if el.get('type') == 'node' and 'lat' in el and 'lon' in el:
        d = haversine(lat, lon, el['lat'], el['lon'])
        return d, (el['lat'], el['lon'])
    elif 'geometry' in el:
        for pt in el['geometry']:
            d = haversine(lat, lon, pt['lat'], pt['lon'])
            if d < min_dist:
                min_dist = d
                closest_pt = (pt['lat'], pt['lon'])
    return min_dist, closest_pt


# ---------------------------------------------------------------------------
# Core Overpass Query
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Core Overpass Query & Cache
# ---------------------------------------------------------------------------

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OVERPASS_DELAYS = [2.0, 8.0, 20.0]

_grid_cache: Dict[Tuple[float, float, float], GridConnectionInfo] = {}
_grid_lock = threading.Lock()


def get_grid_connection_info(lat: float, lon: float, delta: float = 0.5) -> GridConnectionInfo:
    """
    Query Overpass API for power infrastructure within `delta` degrees (~55 km) of (lat, lon).
    Separately calculates:
    1. Distance to nearest confirmed >=33 kV substation.
    2. Distance to nearest >=33 kV transmission line.
    3. Low confidence flag if untagged substations exist closer than confirmed ones.

    Raises
    ------
    SubstationDataUnavailable
        If all Overpass API endpoints fail/timeout.
    """
    cache_key = (round(lat, 4), round(lon, 4), round(delta, 3))
    with _grid_lock:
        if cache_key in _grid_cache:
            return _grid_cache[cache_key]

    bbox = f"{lat - delta},{lon - delta},{lat + delta},{lon + delta}"

    # Query both substations and lines within bbox; filter lines to avoid 504 gateway timeouts
    query = f"""
    [out:json][timeout:30][maxsize:536870912];
    (
      node["power"="substation"]({bbox});
      way["power"="substation"]({bbox});
      relation["power"="substation"]({bbox});
      way["power"="line"](if:!t["voltage"] || number(t["voltage"])>=33000)({bbox});
    );
    out geom;
    """

    data = None
    last_err = None

    for attempt, url in enumerate(OVERPASS_ENDPOINTS):
        try:
            headers = {'User-Agent': 'SolarPVKarnataka/1.2'}
            response = requests.post(url, data={'data': query}, headers=headers, timeout=35)
            if response.status_code == 200:
                data = response.json()
                break
            else:
                last_err = f"HTTP {response.status_code}: {response.text[:100]}"
        except Exception as e:
            last_err = str(e)

        if attempt < len(OVERPASS_ENDPOINTS) - 1:
            time.sleep(OVERPASS_DELAYS[attempt])

    if data is None:
        raise SubstationDataUnavailable(
            f"Overpass API unreachable for substation query at ({lat}, {lon}): {last_err}"
        )

    elements = data.get('elements', [])

    # Track best matches
    best_qualifying_sub_dist = float('inf')
    best_qualifying_sub_el = None

    best_any_sub_dist = float('inf')
    best_any_sub_el = None

    best_line_dist = float('inf')
    best_line_el = None

    for el in elements:
        tags = el.get('tags', {})
        power_type = tags.get('power')
        voltage_v = parse_osm_voltage_v(tags.get('voltage'))
        substation_kind = tags.get('substation', '').lower()

        dist, _ = _get_element_min_distance(lat, lon, el)
        if dist == float('inf'):
            continue

        if power_type == 'substation':
            # Any substation
            if dist < best_any_sub_dist:
                best_any_sub_dist = dist
                best_any_sub_el = el

            # Qualifying >= 33 kV substation check:
            # Qualifies if voltage is explicitly >= 33,000 V OR tagged as transmission substation
            is_qualifying = False
            if voltage_v is not None and voltage_v >= 33000.0:
                is_qualifying = True
            elif substation_kind == 'transmission':
                is_qualifying = True

            if is_qualifying and dist < best_qualifying_sub_dist:
                best_qualifying_sub_dist = dist
                best_qualifying_sub_el = el

        elif power_type == 'line':
            # Transmission lines: >= 33 kV, or untagged power=line (which are transmission in OSM)
            if voltage_v is None or voltage_v >= 33000.0:
                if dist < best_line_dist:
                    best_line_dist = dist
                    best_line_el = el

    # Extract substation details
    sub_dist = best_qualifying_sub_dist if best_qualifying_sub_dist != float('inf') else None
    any_sub_dist = best_any_sub_dist if best_any_sub_dist != float('inf') else None
    line_dist = best_line_dist if best_line_dist != float('inf') else None

    sub_name = None
    sub_voltage = None
    sub_id = None
    if best_qualifying_sub_el is not None:
        stags = best_qualifying_sub_el.get('tags', {})
        sub_name = stags.get('name') or stags.get('operator') or stags.get('ref')
        sub_voltage = parse_osm_voltage_v(stags.get('voltage'))
        sub_id = f"{best_qualifying_sub_el.get('type')}/{best_qualifying_sub_el.get('id')}"

    line_id = None
    if best_line_el is not None:
        line_id = f"{best_line_el.get('type')}/{best_line_el.get('id')}"

    # Determine confidence status
    low_confidence = False
    confidence_reason = None

    if sub_dist is None:
        low_confidence = True
        if any_sub_dist is not None:
            confidence_reason = (
                f"No confirmed >=33kV substation found within {delta*111:.0f} km, but an untagged "
                f"substation exists at {any_sub_dist:.2f} km. Field verification required."
            )
        else:
            confidence_reason = f"No substation found within {delta*111:.0f} km in OSM."
    elif any_sub_dist is not None and any_sub_dist < sub_dist:
        # An untagged substation is closer than the nearest confirmed >=33kV substation
        low_confidence = True
        confidence_reason = (
            f"An untagged substation exists at {any_sub_dist:.2f} km, closer than the nearest "
            f"confirmed >=33kV substation at {sub_dist:.2f} km ({sub_name or 'unnamed'}). "
            f"KPTCL SLD verification recommended."
        )

    res = GridConnectionInfo(
        substation_dist_km=round(sub_dist, 4) if sub_dist is not None else None,
        transmission_line_dist_km=round(line_dist, 4) if line_dist is not None else None,
        nearest_any_substation_dist_km=round(any_sub_dist, 4) if any_sub_dist is not None else None,
        substation_name=sub_name,
        substation_voltage_v=sub_voltage,
        substation_osm_id=sub_id,
        transmission_line_osm_id=line_id,
        low_confidence=low_confidence,
        confidence_reason=confidence_reason,
    )

    with _grid_lock:
        _grid_cache[cache_key] = res

    return res


# ---------------------------------------------------------------------------
# Backward-compatible API
# ---------------------------------------------------------------------------

def get_substation_distance_km(lat: float, lon: float) -> Optional[float]:
    """
    Return distance in km to nearest qualifying (>=33 kV) substation.
    Maintains backward compatibility with score_candidate.py.
    """
    info = get_grid_connection_info(lat, lon)
    return info.substation_dist_km


def get_transmission_line_distance_km(lat: float, lon: float) -> Optional[float]:
    """
    Return distance in km to nearest >=33 kV transmission line.
    """
    info = get_grid_connection_info(lat, lon)
    return info.transmission_line_dist_km


if __name__ == "__main__":
    print("Testing grid connection info for Rank 3 coordinate (11.5000, 77.1000)...")
    res = get_grid_connection_info(11.5, 77.1)
    print(f"Substation distance (>=33kV): {res.substation_dist_km} km")
    print(f"Substation name: {res.substation_name}")
    print(f"Substation voltage: {res.substation_voltage_v} V")
    print(f"Substation OSM ID: {res.substation_osm_id}")
    print(f"Transmission line distance: {res.transmission_line_dist_km} km")
    print(f"Transmission line OSM ID: {res.transmission_line_osm_id}")
    print(f"Low confidence: {res.low_confidence} ({res.confidence_reason})")
