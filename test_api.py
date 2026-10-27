import json, time, http.client

time.sleep(3)

conn = http.client.HTTPConnection('127.0.0.1', 8000, timeout=5)
conn.request('GET', '/api/machines')
resp = conn.getresponse()
data = resp.read().decode()
print('Machines status:', resp.status)
if resp.status == 200:
    machines = json.loads(data)
    print('Number of machines:', len(machines))
    if machines:
        print('First machine:', machines[0].get('machine_name'))
conn.close()

conn2 = http.client.HTTPConnection('127.0.0.1', 8000, timeout=5)
conn2.request('GET', '/api/readings/summary')
resp2 = conn2.getresponse()
data2 = resp2.read().decode()
print('Summary status:', resp2.status)
if resp2.status == 200:
    summary = json.loads(data2)
    print('Summary:', json.dumps(summary, indent=2)[:300])
conn2.close()

conn3 = http.client.HTTPConnection('127.0.0.1', 8000, timeout=5)
conn3.request('GET', '/api/anomalies')
resp3 = conn3.getresponse()
data3 = resp3.read().decode()
print('Anomalies status:', resp3.status)
anomalies = json.loads(data3) if resp3.status == 200 else []
print('Anomalies count:', len(anomalies))
conn3.close()