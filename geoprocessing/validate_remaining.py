from fetch_substation_distance import get_substation_distance_km
from fetch_protected_buffer import get_buffer_status
from fetch_landcost_proxy import get_landcost_proxy

def test_substation():
    print("--- Substation Distance Check ---")
    bengaluru_dist = get_substation_distance_km(12.9716, 77.5946)
    bidar_dist = get_substation_distance_km(17.91, 77.51)
    if bengaluru_dist is None:
        bengaluru_str = "NO DATA"
    else:
        bengaluru_str = f"{bengaluru_dist:.2f} km"
        
    if bidar_dist is None:
        bidar_str = "NO DATA"
    else:
        bidar_str = f"{bidar_dist:.2f} km"
        
    print(f"Bengaluru (urban): {bengaluru_str}")
    print(f"Bidar (rural): {bidar_str}")
    if bengaluru_dist is not None and bidar_dist is not None and bengaluru_dist < bidar_dist:
        print("Substation test: PASS")
    else:
        print("Substation test: FAIL (Expected Bengaluru distance < Bidar distance, or data missing)")
    print()

def test_buffer():
    print("--- Protected Area/Water Buffer Check ---")
    bandipur_status = get_buffer_status(11.6667, 76.6283)
    rural_status = get_buffer_status(17.91, 77.51)
    bidar_eastern_edge_status = get_buffer_status(17.9, 77.5)
    
    print(f"Bandipur Core: {bandipur_status}")
    print(f"Safe Rural Point: {rural_status}")
    print(f"Bidar Eastern Edge (UTM check): {bidar_eastern_edge_status}")
    
    if bandipur_status == "excluded" and rural_status == "eligible" and bidar_eastern_edge_status == "eligible":
        print("Buffer test: PASS")
    else:
        print("Buffer test: FAIL (Expected Bandipur: excluded, others: eligible)")
    print()

def test_landcost():
    print("--- Land-cost Proxy Check ---")
    bengaluru_score = get_landcost_proxy(12.9716, 77.5946)
    bidar_score = get_landcost_proxy(17.91, 77.51)
    
    if bengaluru_score is None:
        bengaluru_str = "NO DATA"
    else:
        bengaluru_str = f"{bengaluru_score:.2f}"
        
    if bidar_score is None:
        bidar_str = "NO DATA"
    else:
        bidar_str = f"{bidar_score:.2f}"
        
    print(f"Bengaluru (urban) score: {bengaluru_str}")
    print(f"Bidar (rural) score: {bidar_str}")
    
    if bengaluru_score is not None and bidar_score is not None and bengaluru_score < bidar_score:
        print("Land-cost proxy test: PASS")
    else:
        print("Land-cost proxy test: FAIL (Expected Bengaluru score < Bidar score, or data missing)")
    print()

if __name__ == "__main__":
    test_substation()
    test_buffer()
    test_landcost()
