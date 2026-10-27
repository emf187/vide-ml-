import sqlite3
con = sqlite3.connect("data/industrial.db")
con.row_factory = sqlite3.Row
print("--- MX-2047 ml latest ---")
for r in con.execute("SELECT timestamp, hours_to_maintenance, anomaly_probability_next_window, fault_type, fault_type_confidence FROM ml_predictions WHERE machine_name=? ORDER BY timestamp DESC LIMIT 3", ("MX-2047",)):
    print(dict(r))
print("--- all machines ml latest ---")
for r in con.execute("SELECT machine_name, MAX(timestamp) m FROM ml_predictions GROUP BY machine_name"):
    print(dict(r))
print("--- summary ---")
print(dict(con.execute("SELECT COUNT(DISTINCT machine_name) mc, AVG(CASE WHEN is_running=1 THEN load_factor_pct END) al, SUM(is_anomaly) an FROM sensor_readings").fetchone()))
