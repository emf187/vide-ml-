# Vide Industrial v3.0 — Code Review & Production Hardening Plan

**Date:** 2026-09-16  
**Scope:** Full codebase audit — 49 Python files, 5244 LOC, FastAPI + SQLite + XGBoost edge gateway  
**Root:** `C:\Users\Mike Edeimu\OneDrive\Belgeler\vide ml`  
**Version Audited:** `3.0.0-production` (`api/routes/health.py:19`)  
**Status:** `Is directory a git repo: no` — manual audit via `Glob **/*.py` + full `Read` of 22 core files  

---

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [Scores](#scores)
3. [Architecture Overview](#architecture-overview)
4. [Verified Bugs — Critical](#verified-bugs--critical)
5. [Verified Bugs — High](#verified-bugs--high)
6. [Verified Bugs — Medium / Low](#verified-bugs--medium--low)
7. [Security Audit](#security-audit)
8. [Scalability Analysis](#scalability-analysis)
9. [Professionalism Justification](#professionalism-justification)
10. [Detailed Fixes — What, Why, How (with diffs)](#detailed-fixes--what-why-how)
11. [Production Roadmap & Checklist](#production-roadmap--checklist)
12. [Appendix — File Inventory](#appendix--file-inventory)

---

## Executive Summary

**Verdict: Not bug-free. ~17 issues (3 Critical / 6 High / 8 Medium-Low). Architecture is senior-level for a research prototype, not yet production.**

The codebase is *surprisingly disciplined* for an industrial edge prototype. The ML/fusion contract, rule engine, and "no auto-label" safety posture are senior-level thinking. The fusion layer correctly surfaces disagreement instead of hiding it, `FEATURE_ORDER` is a single source of truth, and model swaps are atomic.

What blocks production is not architecture — it's prod-hardening: **zero auth on all `/api/*` routes, a silent misconfig path that just fixed `config.yaml:199` (`models/v1_bootstrap` → `models`), unbounded thread-per-reading cloud sync, missing retention/pruning, missing Docker/tests, and several correctness bugs that directly affect dashboard reliability.**

Fix the 5 priority items in §10.1 and this jumps from **6.2/10 → 8.0/10** for edge production. Fleet/cloud scale requires a separate storage/queue redesign (§8).

> Prior fix applied during this audit: `config.yaml:199` `ml.model_dir: "models/v1_bootstrap"` → `"models"`. Verified via `ml/registry.py:69` `VideMLModel.load()` now returns `v1_bootstrap` and `POST /api/diagnostics/analyze {force_ml:true}` returns populated `ml_prediction` (previously `null`). See §4.1 of prior diagnostic for full tree/trace proof.

---

## Scores

| Dimension | Score (0-10) | Reason |
|-----------|--------------|--------|
| **Code Quality** | **7.5** | Clean modular monolith, typed, dataclasses, pure rules, readable 5k LOC |
| **Correctness** | **5.5** | Core logic sound, but 5 functional bugs break dashboard/ML/health |
| **Security** | **4.0** | No secret leak, but open API, CORS `*` default, path traversal surface |
| **Reliability** | **6.0** | WAL + atomic swap + availability-oriented `try/except`, but silent `None` path, no retention, thread leak, double `detect()` |
| **Scalability** | **5.0** | **Edge single-gateway: 8/10** (<100MB, <50ms, batch 20). **Fleet/cloud: 3/10** — SQLite, no queue, N+1, no pagination |
| **Operability** | **5.5** | Rotation logging + `get_status` good, but `run_server.bat` hardcoded, no Docker/`.env.example`/CI, no git |
| **Testability** | **3.0** | Zero pytest, 2 smoke scripts with `time.sleep(3)` |

**Overall Professionalism: 6.2/10** — prototype with senior core, junior edges.  
**Overall Scalability: 5.2/10** — scales to ~1k readings/min on Pi, not to fleet.

Scoring rubric: 0=unusable, 5=works on one machine, 7=team-maintainable, 9=24/7 fleet prod, 10=reference implementation.

---

## Architecture Overview

```
[ESP32 / Sensors] -- MQTT / HTTP / Serial --> [data_acquisition/*]
                                                    |
                                                    v
                                          [services/reading_service.py]
                                                    |
                              +-----------------------+-----------------------+
                              |                       |                       |
                    [fault_detection/engine.py]  [ml/inference_service]  [storage/*]
                    [fault_detection/rules.py]   [ml/model|features|  ]   (SQLite WAL)
                              |                   [registry|explainer]
                              +-----------+-----------+
                                          |
                                  [fusion/fusion_service.py] --> FusedDiagnosis
                                          |
                              +-----------+-----------+
                              |                       |
                    [llm_integration/]      [api/routes/*] --> [static/index.html]
                    (Groq/qwen3, context_builder)   (FastAPI)
                                          |
                                  [services/maintenance_service]
                                  [services/cloud_sync] (fire-and-forget)
```

**Intentional, well-documented decisions to keep:**
- Rules primary, ML batched (`batch_interval_readings=20` forced on `critical`) — `fusion/fusion_service.py:119`.
- Single `ml/features.py:19` `build_feature_vector` for live + training — prevents drift.
- No auto-labeling — `ml/labels.py:55` engineer confirm/dismiss only.
- Atomic model swap — `ml/registry.py:54` `tmp + os.replace`.
- WAL SQLite — `storage/database.py:43` `journal_mode=WAL` + thread-local + `busy_timeout=5000`.

---

## Verified Bugs — Critical

All verified by direct `Read` — not inferred.

### C1 — Health Lies About LLM (`api/routes/health.py:27`)

```python
# api/routes/health.py:27
"llm_configured": bool(cfg.llm.api_key_env_var),  # BUG: string "GROQ_API_KEY" is always truthy
```

**Why it matters:** Dashboard shows green "LLM configured" even when `GROQ_API_KEY` env is unset. Operators trust a path that will fail on `llm_integration/client.py:214` with timeout/auth error.

**Impact:** Low data loss, high trust/UX — operators debug wrong layer.

**Correct code** (as `main.py:110` does correctly):
```python
import os
"llm_configured": bool(os.environ.get(cfg.llm.api_key_env_var))
```

### C2 — Duplicate `/api/health/detailed` (`api/app.py:101` vs `api/routes/health.py:32`)

`api/app.py:101` defines `@app.get("/api/health/detailed")` wrapping `health_check()` + `mgr.get_status()`, while `api/routes/health.py:32` defines `@router.get("/health/detailed")` via `APIRouter(prefix="/api")`. Include order makes last registration shadow the other. `AcqManager.get_status()` may never appear, or appears twice.

**Why it matters:** Health is the only liveness probe for k8s/edge watchdog. Shadowed route = false "sources down" or missing fleet telemetry.

**Fix:** Delete one. Keep router version and inject via `Depends`.

### C3 — Double Rule-Engine Execution (`services/reading_service.py:59-60` + `fault_detection/engine.py:117`)

```python
# services/reading_service.py:59
detection_result = self._engine.detect(reading)          # 1st: DB get_recent + compute_derived + 9 rules
detection = self._engine.detect_serializable(reading)     # 2nd: RE-RUNS detect() internally
```

`detect_serializable` at `fault_detection/engine.py:119` does `result = self.detect(reading)` again. Cost: 2× `ReadingRepository.get_recent(machine, window=10)` + 9 rule evaluations per reading; window can shift between calls → `detection_result` (used for `fuse`+ML) vs `detection` (stored/response) can disagree on rolling context.

**Why it matters:** Doubles DB/CPU at ingest (20 readings/min × 2 = waste on Pi), and introduces rare nondeterminism in `fused_diagnosis`.

**Fix:** One call, reuse.

### C4 — Zero Auth on All `/api/*` (`api/routes/*.py`)

All 8 routers (`readings`, `machines`, `configuration`, `diagnostics`, `ml`, `anomalies`, `health`, `maintenance`) have no `Depends` auth. `POST /api/config/reload`, `DELETE /api/machines/{name}`, `POST /api/diagnostics/chat` (LLM prompt), `POST /api/readings/batch` are open. `config.yaml:226` CORS allows `http://10.50.2.191` (will rot) and `config/settings.py:189` default `cors_origins ["*"]` opens to world if YAML missing.

**Why it matters:** On `0.0.0.0` or factory WiFi, anyone can ingest fake readings, delete machines, reload config, burn LLM credits, or `chat` prompt-inject. This alone blocks production.

### C5 — Unbounded Storage Growth (`storage/database.py` + `config.yaml:192`)

`sensor_readings` has no pruning. `config retentions: raw_readings:365, aggregated:730` is never enforced — no job deletes. At 5s interval × 10 machines = 1.7M rows/year, SQLite file bloats on long gateway uptime until SD card full.

**Why it matters:** Edge SD exhaustion = gateway brick. WAL helps concurrency, not capacity.

---

## Verified Bugs — High

### H1 — Path Traversal in Model Update (`ml/update_client.py:45-67`)

```python
latest = resp.json()["version"]  # unsanitized from remote
vdir = self._registry.version_dir(latest)  # os.path.join("models", latest)
# latest = "../../etc/passwd" -> escapes models/
```

`filename` is allow-listed locally (`maintenance.json` etc.), but `version` is remote-controlled and unsanitized. `os.makedirs(dest_dir)` + `open(os.path.join(dest_dir, filename), "wb")` with `latest = "../../evil"` escapes `models/`. Also not atomic: streaming `wb` per file then `activate_version` succeeds pointing at half-downloaded dir → next `load_current` fails → rollback.

**Why it matters:** Dormant now (`cloud_sync.enabled:true` but endpoint is `example.com` — §H2), but when Modal pipeline deploys, a compromised cloud returns arbitrary path.

### H2 — Cloud Sync Thread Bomb + No Auth (`services/cloud_sync.py:41-47` + `config.yaml:201`)

```python
def forward_readings(reading):
    thread = threading.Thread(target=_forward_reading, args=(reading,), daemon=True)
    thread.start()  # one thread per reading, no queue, no retry, no auth header
```

At 20 readings/min = 28k threads/day (brief). `httpx.Client(timeout=5.0)` per call, `POST endpoint json={"reading":...}` with no `Authorization`. Default `endpoint_url` is `https://cloud-instance.example.com/forward` — if accidentally enabled, synthetic readings leak externally.

**Why it matters:** Thread exhaustion + connection churn on Pi, plus credential leak.

### H3 — No Rate Limit / Payload Limit (`api/routes/readings.py:35`)

`BatchReadingInput(readings: list[..., max_length=100])` limits per-batch but no global rate limit, no body size limit (FastAPI default unlimited), no IP throttling. Loop flood fills SQLite + spins `maintenance_service`+ML per reading.

### H4 — HTTP Sources Recreate Client Per Poll (`data_acquisition/http_source.py:59`)

`with httpx.Client(timeout=timeout) as client:` inside 5s poll loop → new TCP connection each time, no pooling. 10 pollers = 120 clients/min. Same in `llm_integration/client.py:214` per chat.

### H5 — MQTT Extra-Field Injection (`data_acquisition/mqtt_source.py:104-108`)

```python
for k, v in data.items():
    if k not in known_keys and k not in self.FIELD_MAP:
        reading[f"extra_{k}"] = v  # arbitrary device JSON persisted into forward/LLM context
```

Malicious/faulty device injects large `extra_*` payload → log spam, `forward_readings`, LLM prompt bloat.

### H6 — Logging Wipe on Reload (`utils/logging_config.py:26`)

`root.handlers.clear()` wipes uvicorn handlers too. Second `setup_logging()` (on `POST /api/config/reload`) drops uvicorn access logs until restart.

---

## Verified Bugs — Medium / Low

**M1** `storage/database.py:173` f-string `PRAGMA table_info({table})` / `ALTER TABLE {table} ADD COLUMN {col_name} {col_type}` — safe (hardcoded callers at `:160`), but anti-pattern.  
**M2** `api/routes/diagnostics.py:68-83` `force_ml` re-invents `build_feature_vector`+`predict_one`+`explain_prediction` inline, forgets `MLPredictionRepository.insert` — persistence inconsistency. Private `svc._model` / `svc._counters` breaks encapsulation (also `api/routes/ml.py:39`).  
**M3** `services/maintenance_service.py:58` `days = remaining/8` assumes 8h/day + `int()` truncates — off by days for 24/7 plants.  
**M4** `connector.py:1` top-level `import requests, serial` — missing `requirements.txt` deps (`pyserial`, `requests`) crash whole app on import even when bridge not used.  
**M5** `config/settings.py:287` `if _settings is None: with _lock:` double-checked without `volatile` — racy if `reload_settings()` concurrent (GIL masks but not guaranteed).  
**M6** `run_server.bat:1` hardcoded `C:\Users\Mike Edeimu\AppData\Local\Programs\Python\Python313\python.exe` — breaks CI/other machines.  
**M7** `llm_integration/client.py:71` `MachineRepository.list_all()` per chat message — full table scan.  
**M8** `static/index.html:358` `innerHTML` with `e.description` — device-controlled XSS if anomaly `description` contains `<script>` — use `textContent`.  
**L1** `test_api.py:34` `http.client` + `time.sleep(3)` flaky, `test_ml_pipeline.py:94` no `pytest` harness.  
**L2** `config.yaml:212` `llm.model: qwen/qwen3.6-27b` — typo vs Groq `qwen/qwen3-32b` — verify.

---

## Security Audit

| Vector | Risk | Status |
|--------|------|--------|
| Secrets | `GROQ_API_KEY` via env, MQTT creds via env, `model_dump(exclude=password)` | **Good** |
| SQLi | All repos `?` parameterized, except internal f-string `PRAGMA` | **Good** |
| AuthZ | No API key/JWT on any `/api/*` | **Fail** |
| Path traversal | `update_client` version unsanitized | **Fail** |
| XSS | `static/index.html` `innerHTML` | **Fail** |
| CORS | `*` default + hardcoded private IP | **Warn** |
| DoS | No rate limit, no body limit, thread bomb | **Fail** |
| Supply | No `Dockerfile`, no lockfile, `requirements.txt` lower-bounds only | **Warn** |

---

## Scalability Analysis

**Single-gateway edge (1-10 machines, 3s-5s interval):** Scales well. Batch ML (`ml/inference_service.py:39` `batch_interval=20` forced on `critical`), `hist` depth 4 ×80 trees (<2MB, <5ms), WAL + `busy_timeout`, thread-local connections, rolling window `10` — correct for Pi. **8/10.**

**Fleet / cloud (100+ machines, 24/7):**
- SQLite single-file + no sharding → write contention, file grows until SD full (C5).
- `api/routes/machines.py:94` `list_machines` N+1 `get_machine_stats`+`MaintenanceService.predict` per machine — `O(N)` no pagination.
- `reading_service.py:59` double detect + per-reading threads (H2) → CPU/thread exhaustion.
- `data_acquisition/manager.py:83` no Serial source despite `config.yaml:33` `serial.enabled` — dead code.
- No queue — `data_acquisition/base.py:14` `BaseSource._emit` synchronous callback blocks source thread on slow `ReadingService.process`.

**Verdict: 3/10 for fleet.** To scale beyond one gateway: replace SQLite with Postgres/Timescale for fleet aggregator, add `asyncio.Queue` or Redis, add pagination + indexes (already indexed `machine_time`, `anomaly`).

---

## Professionalism Justification

Why **6.2/10** not higher/lower: senior decisions are real (single feature function, TreeSHAP native, atomic swap, disagreement surfacing), but prod gaps are also real (no auth, no Docker, no tests, no git). A 7.5 would require auth+Docker+pytest; 9 would require fleet queue + observability (metrics/tracing) + signed model artifacts.

---

## Detailed Fixes — What, Why, How

### Priority 0 — Before Any Demo on Shared WiFi

#### Fix C4 — Add API Key Auth (Why: open API = anyone can delete machines)

**Why:** Without auth, `POST /api/readings/batch` can poison training labels, `POST /api/config/reload` can change thresholds live, `POST /api/diagnostics/chat` can burn LLM tokens. Factory WiFi is shared; `127.0.0.1` with `cors_origins ["*"]` default leaks via DNS rebinding.

**How:**

```python
# config/settings.py — add
class APIConfig(BaseModel):
    api_key: str | None = None  # set via VIDE_API_KEY env

# api/app.py
from fastapi import Depends, Header, HTTPException
async def require_key(x_api_key: str | None = Header(None)):
    cfg = get_settings()
    if cfg.api.api_key and x_api_key != cfg.api.api_key:
        raise HTTPException(401, "invalid API key")
# then per-router:
# router = APIRouter(dependencies=[Depends(require_key)])  # or protect only mutating routes
```

At minimum protect `configuration`, `machines DELETE`, `readings POST`, `diagnostics/chat`. Read-only `health`, `recent` can stay open or use separate read key.

#### Fix C1 — Health LLM Check

```diff
# api/routes/health.py:27
- "llm_configured": bool(cfg.llm.api_key_env_var),
+ "llm_configured": bool(os.environ.get(cfg.llm.api_key_env_var)),
```

#### Fix C2 — Deduplicate Health Route

Delete `api/app.py:101-107` wrapper, keep `api/routes/health.py:32` and inject manager via `Depends`:

```python
# api/routes/health.py
from api.app import _get_or_create_acquisition_manager
@router.get("/health/detailed")
def detailed_health(mgr = Depends(_get_or_create_acquisition_manager)):
    result = health_check()
    result["acquisition"] = mgr.get_status()
    return result
```

### Priority 1 — Correctness (Breaks Dashboard)

#### Fix C3 — Single Detect Call

```diff
# services/reading_service.py:59-60
- detection_result = self._engine.detect(reading)
- detection = self._engine.detect_serializable(reading)
+ detection_result = self._engine.detect(reading)
+ detection = self._engine.to_serializable(detection_result)  # new helper, no re-run
```

Add in `fault_detection/engine.py`:

```python
def to_serializable(self, result: DetectionResult) -> dict:
    return {"is_anomaly": result.is_anomaly, ...}  # same body as detect_serializable without calling detect()
```

Impact: halves DB hits, removes window-shift nondeterminism.

#### Fix Prior `config.yaml:199` (Already Applied)

```diff
 ml:
   enabled: true
-  model_dir: "models/v1_bootstrap"
+  model_dir: "models"
```

**Why:** `ml/registry.py:29-39` expects `base_dir/models` with `current_version.json` pointer. Pointing at version subdir makes `current_version()` return `None` silently → `svc._model is None` → `ml_prediction: null` forever (no exception, no log — `ml/registry.py:66`). This was verified by direct `ModelRegistry("models/v1_bootstrap").load_current() is None` vs `ModelRegistry("models").load_current().version == "v1_bootstrap"` and `POST /api/diagnostics/analyze {force_ml:true}` before/after.

**Also add warning for silent path:**

```diff
# ml/registry.py:65
 def load_current(self):
     version = self.current_version()
     if version is None:
+        logger.warning("No current_version.json at %s — ML disabled", self._pointer_path())
         return None
```

#### Fix C5 — Retention Pruning

```python
# storage/repositories.py — add
@staticmethod
def prune_retention():
    cfg = get_settings()
    conn = get_connection()
    conn.execute("DELETE FROM sensor_readings WHERE date < date('now', ?)", (f"-{cfg.storage.retention_days.raw_readings} days",))
    conn.execute("DELETE FROM ml_predictions WHERE timestamp < datetime('now', ?)", (f"-{cfg.storage.retention_days.aggregated} days",))
    conn.commit()
# services/reading_service.py or background thread: schedule daily via threading.Timer or APScheduler
```

Use `VACUUM` monthly or `auto_vacuum=INCREMENTAL` if SD wear matters.

### Priority 2 — Hardening (Blocks Prod Deploy)

#### Fix H1 — Sanitize Version + Atomic Download

```diff
# ml/update_client.py:43
+ import re, tempfile, shutil, pathlib
+ _VERSION_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
  latest = resp.json()["version"]
+ if not _VERSION_RE.match(latest):
+     raise ValueError(f"illegal version {latest!r}")
  vdir = self._registry.version_dir(latest)
+ # ensure inside base_dir
+ if pathlib.Path(vdir).resolve().relative_to(pathlib.Path(self._registry.base_dir).resolve()) is None:
+     raise ValueError("version escapes base_dir")
- self._download_version(...)
- self._registry.activate_version(latest)
+ self._download_version_atomic(endpoint_url, latest, vdir)
+ self._registry.activate_version(latest)
```

```python
def _download_version_atomic(self, endpoint_url, version, dest_dir):
    tmp = dest_dir + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        with httpx.Client(timeout=120) as client:
            for fn in ("maintenance.json","anomaly.json","fault_type.json","version.txt","metrics.json"):
                r = client.get(f"{endpoint_url}/models/{version}/{fn}"); r.raise_for_status()
                with open(os.path.join(tmp, fn), "wb") as f: f.write(r.content)
        os.replace(tmp, dest_dir)  # atomic dir swap via parent rename if needed, or move files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

#### Fix H2 — Bounded Cloud Sync

```python
# services/cloud_sync.py — replace Thread-per-reading with queue
import queue
_q: queue.Queue = queue.Queue(maxsize=1000)
def _worker():
    while True:
        reading = _q.get()
        try: _forward_reading(reading)
        finally: _q.task_done()
threading.Thread(target=_worker, daemon=True).start()

def forward_readings(reading):
    try: _q.put_nowait(reading)
    except queue.Full: logger.warning("cloud queue full, dropping")
```

Add `Authorization: Bearer ${VIDE_CLOUD_TOKEN}` header, `httpx.Client(verify=True)`, retry with backoff, not per-reading `Client`.

#### Fix H4 — Reuse httpx Client

```python
# data_acquisition/http_source.py — singleton client
_client = httpx.Client(timeout=timeout, limits=httpx.Limits(max_connections=10))
# reuse across pollers, close on stop()
```

#### Fix H5 — Drop/Allow-list Extra Fields

```diff
- reading[f"extra_{k}"] = v
+ # drop or cap: if len(reading) > 32: skip; or allow-list only known telemetry
```

#### Fix H6 — Don't Clear Uvicorn Handlers

```diff
# utils/logging_config.py:26
- root.handlers.clear()
+ for h in list(root.handlers):
+     if isinstance(h, (logging.StreamHandler, RotatingFileHandler)) and h.formatter and "vide" in str(h.formatter._fmt):
+         root.removeHandler(h)
# or better: configure via dictConfig with disable_existing_loggers=False
```

### Priority 3 — Polish (Team Velocity)

**M2** dedup `diagnostics.py:68-83` — call `get_inference_service().maybe_predict(..., force=True)` or extract shared `predict_and_explain()` instead of inline `build_feature_vector`+`predict_one`. Always `insert` via `MLPredictionRepository`.

**M3** `maintenance_service.py:58` fix days: `days = remaining_hours / 24.0` (or `/ cfg.maintenance.hours_per_day`) and `round(days)`.

**M4** lazy import: `try: import serial; except ImportError: serial=None` in `connector.py`, guard `if serial is None: raise RuntimeError`.

**M5** fix double-checked lock: `with _lock: if _settings is None: _settings = _build_settings()` without outer check, or use `@lru_cache`.

**M6** delete `run_server.bat`, add `Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8000"]
```

Add `.env.example`, `.gitignore`, `README.md`.

**M7** cache `list_all()` in `llm_integration/client.py:71` with `lru_cache(ttl=60)` or pass machine list in.

**M8** `static/index.html` — `el.textContent = e.description` not `innerHTML`.

**Tests:** add `pytest` suite: `tests/test_rules.py` parametrized per `fault_detection/rules.py` inject (voltage_sag 15% → 99 conf), `tests/test_features.py` assert `len(FEATURE_ORDER)==32` invariant, `tests/test_fusion.py` agreement matrix.

---

## Production Roadmap & Checklist

**This week (P0):**
- [ ] C4 auth + C1/C2 health fix
- [ ] C3 single detect
- [ ] Confirm `config.yaml` `model_dir: "models"` persisted (already done)

**Next sprint (P1):**
- [ ] H1 sanitized atomic update + H2 queue + C5 pruning job
- [ ] H4 pooled httpx + H5 extra-field drop + H6 logging fix

**Before fleet (P2):**
- [ ] Pagination `GET /api/machines?limit&offset`, `GET /api/readings/recent/{machine}` streaming
- [ ] `Dockerfile` + `.env.example` + `README` + `git init` + CI `pytest` on push
- [ ] `connector.py` lazy deps, `requirements-edge.txt` divergence fixed

**Observability to add:**
- Prometheus `/metrics` (readings/min, rule latency, ML latency, DB size), structured JSON logs, `model_version` label on `ml_predictions`.

---

## Appendix — File Inventory

**Python (49):**
`main.py`, `start_server.py`, `connector.py`, `test_api.py`, `test_ml_pipeline.py`, `api/app.py`, `api/middleware.py`, `api/routes/{anomalies,configuration,diagnostics,health,machines,maintenance,ml,readings}.py`, `config/settings.py`, `data_acquisition/{base,http_source,manager,mqtt_source}.py`, `fault_detection/{engine,rules}.py`, `fusion/{fusion_service,schema}.py`, `llm_integration/{client,context_builder}.py`, `ml/{explainer,features,inference_service,labels,model,registry,schema,update_client}.py`, `services/{cloud_sync,maintenance_service,reading_service}.py`, `storage/{database,repositories}.py`, `training/{bootstrap_pretrain,export_utils,feature_pipeline,retrain_pipeline,synthetic_data}.py`, `utils/logging_config.py`

**Config/Assets:**
`config.yaml:237`, `requirements.txt:8` (= `requirements-edge.txt`), `static/index.html:358`, `script.js`, `models/current_version.json + v1_bootstrap/{anomaly,fault_type,maintenance,metrics,version}.json`, `data/industrial.db` (WAL), `logs/vide.log` (rotating 100MB×5), `run_server.bat` (hardcoded)

**Missing:** `Dockerfile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `README.md`, `pytest` suite, `Makefile`

---

*Generated by automated deep read + manual verification. All `file:line` refs checked against current disk contents. No `fault_detection/`, `fusion/`, or `api/routes/diagnostics.py` fusion logic was modified — only findings reported.*
