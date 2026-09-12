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
    
    import time
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
    ]
    delays = [2, 8, 32]
    
    for attempt in range(3):
        url = endpoints[attempt % len(endpoints)]
        try:
            headers = {'User-Agent': 'SolarPVKarnataka/1.0'}
            response = requests.post(url, data={'data': query}, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
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
            
        except Exception as e:
            print(f"Attempt {attempt + 1} failed on {url}: {e}")
            if attempt < 2:
                time.sleep(delays[attempt])
            else:
                print("NO DATA — do not trust this result")
                return None

if __name__ == "__main__":
    print("Testing land-cost proxy for Bengaluru...")
    print(get_landcost_proxy(12.9716, 77.5946))
