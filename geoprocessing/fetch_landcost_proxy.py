import requests
import math

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_landcost_proxy(lat, lon):
    # Query within ~100km (roughly 1.0 degrees)
    delta = 1.0
    bbox = f"{lat - delta},{lon - delta},{lat + delta},{lon + delta}"
    
    query = f"""
    [out:json][timeout:25];
    (
      node["place"="city"]({bbox});
      node["place"="town"]({bbox});
    );
    out geom;
    """
    
    req_key = f"OVERPASS_LANDCOST: bbox={bbox} query={query.strip()}"
    try:
        from raw_cache import get_cached, save_cached, is_replay_mode
    except ImportError:
        from geoprocessing.raw_cache import get_cached, save_cached, is_replay_mode

    cached = get_cached(req_key)
    if is_replay_mode():
        if cached is not None:
            data = cached
        else:
            raise RuntimeError(f"REPLAY mode active: missing cache for landcost at ({lat}, {lon})")
    elif cached is not None:
        data = cached
    else:
        import time
        endpoints = [
            "https://overpass.openstreetmap.fr/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter",
        ]
        delays = [2, 8, 32]
        data = None

        for attempt in range(3):
            url = endpoints[attempt % len(endpoints)]
            try:
                headers = {'User-Agent': 'SolarPVKarnataka/1.0'}
                response = requests.post(url, data={'data': query}, headers=headers, timeout=30)
                response.raise_for_status()
                data = response.json()
                save_cached(req_key, data, {"service": "OVERPASS_LANDCOST", "bbox": bbox, "lat": lat, "lon": lon})
                break
            except Exception as e:
                print(f"Attempt {attempt + 1} failed on {url}: {e}")
                if attempt < 2:
                    time.sleep(delays[attempt])

    if data is None:
        print("NO DATA — do not trust this result")
        return None

    min_dist = float('inf')
    for element in data.get('elements', []):
        if element['type'] == 'node':
            dist = haversine(lat, lon, element['lat'], element['lon'])
            if dist < min_dist:
                min_dist = dist
                
    if min_dist == float('inf'):
        print("NO DATA — do not trust this result")
        return None
    
    # Normalize distance (capped at 50km = score 1.0, 0km = score 0.0)
    max_dist = 50.0
    capped_dist = min(min_dist, max_dist)
    score = capped_dist / max_dist
    return score

if __name__ == "__main__":
    print("Testing land-cost proxy for Bengaluru...")
    print(get_landcost_proxy(12.9716, 77.5946))
