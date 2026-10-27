# VIDE CORTEX — Frontend Handoff Document (Next.js + TypeScript rebuild)

**Audience:** fullstack engineer rebuilding this UI as a Next.js + TypeScript webapp.
**Source of truth:** the 5 HTML files in this folder. They are the current working product — every feature in them must survive the rebuild (unless noted as "known gap" below).

---

## 1. What Vide is (short version)

**Vide Industrial** is an **industrial fault-detection and predictive-maintenance platform** for factory machines (motors, pumps, compressors, generators, conveyors, HVAC).

How the data flows:

```
Sensors / ESP32 edge devices
        │  POST JSON readings (HTTP) / MQTT / Serial
        ▼
Python FastAPI backend (api/)  ── stores in SQLite (data/industrial.db)
        │  ├─ Rule engine   (fault_detection/)  — deterministic thresholds: voltage sag/swell,
        │  │                                       overload, low power factor, vibration degradation…
        │  ├─ ML model      (ml/)               — trained classifier: fault type, confidence,
        │  │                                       hours-to-maintenance, top factors
        │  ├─ Fusion        (fusion/)           — combines rule + ML into one verdict + urgency
        │  └─ LLM (optional, cloud/llm provider)        — natural-language expert narrative
        ▼
The web UI (this folder) — the operator's "control room"
```

The product is branded **COGITEX VIDE CORTEX** in the UI. The key promise: *edge* (fast, local, rule+small-ML verdicts) vs *cloud* (LLM deep analysis) fused into one actionable verdict — "what's wrong, how urgent, when it fails, what to do about it, and who to call."

The backend runs with `python main.py` and serves the API on `http://127.0.0.1:8000` with Swagger at `/docs`. Interactive API docs are the fastest way for you to explore live shapes.

---

## 2. Architecture of the current UI

Five **self-contained static HTML pages** (inline `<style>` + inline `<script>`, zero build step, zero dependencies). Served by FastAPI at `/static/`.

| File | Route today | Purpose |
|---|---|---|
| `index.html` | `/static/index.html` | **Overview** — fleet dashboard + one-click ML prediction |
| `machines.html` | `/static/machines.html` | **Machines** — registry, profiles, photos, notes, engineer comms |
| `devices.html` | `/static/devices.html` | **Devices** — edge-node registry, linking, health, firmware provisioning |
| `diagnostics.html` | `/static/diagnostics.html` | **Diagnostics** — live graph, edge vs cloud AI, maintenance log, LLM chat |
| `predictions.html` | `/static/predictions.html` | **Predictions** — failure forecast, degradation graph, math log, printable report |

Shared conventions across all pages:

- **Backend URL is user-editable** in-page (top card input, default `http://127.0.0.1:8000/api`) and persisted in `localStorage` under `vide_backend_url`. This exists because the backend often runs on a laptop/edge box at a LAN IP (e.g. `http://10.50.2.191:8000/api`) — **keep this capability**.
- **Polling, not websockets.** Overview polls every 3s (`POLL_MS = 3000`), Diagnostics live-ticks 2–10s (user-selectable), Devices re-checks health every 15s.
- **Graceful offline degradation is a feature, not an error path.** Machines/Devices merge backend data with a localStorage-backed local registry so the operator can still view/edit profiles with no backend. Badges show `LIVE` / `OFFLINE` / `ERROR` / `NOT CONNECTED`.
- **Defensive API calls.** Pages try multiple endpoint paths and accept multiple response shapes (e.g. devices tries `/devices` → `/devices/list` → `/health/detailed`, and accepts `{devices:[...]}` or a bare array). This is intentional resilience against backend version drift.
- `localStorage` is the local persistence layer. Full key list in §5.

---

## 3. Page-by-page feature spec

### 3.1 Overview (`index.html`) → `app/` or `app/overview`

Layout: sidebar (global) + topbar + content + footer bar.

Content:
1. **DEVICE CONNECTION card** — backend URL input, "Test connection" button → `GET /health/detailed`. Shows status text, and the derived **firmware POST target** (`{origin}/api/readings`) that ESP32 firmware should push to.
2. **Fleet tiles (grid4)** — FLEET ONLINE (`x / n`, "online" = latest reading < 60s old), FLEET HEALTH (avg of health scores: critical=20, warning=50, watch=75, healthy=100), OPEN ANOMALIES (count from `GET /diagnostics/critical-events?limit=5`), FLEET POWER DRAW (sum of latest `power_kw`).
3. **Selected machine card** — auto-selects first machine. Four live tiles: LOAD %, POWER kW, TEMPERATURE °C, VIBRATION mm/s. Values **pulse-animate on change** (see §4.6).
4. **LOCAL MODEL button** — "Get instant prediction from local model" → `POST /diagnostics/analyze` with `{use_llm:false, force_ml:true}` using the last reading. Result box shows: rule engine verdict, ML fault type, confidence %, hours-to-maintenance, overall urgency (color-coded), rules/ML agreement, top factors.
5. **ATTENTION QUEUE** — last 5 critical events (`fault_type/description`, machine, time) with a red dot each; empty-state text otherwise.

Behavior: `refreshAll()` runs every 3s once connected. `window._lastReading` caches the selected machine's latest reading for the prediction button.

### 3.2 Machines (`machines.html`) → `app/machines`

Master–detail layout (340px list + detail pane).

- **List**: loads `GET /machines`, merges **local-only machines** (created while offline) marked `_local`. Search filter across name/type/location. Each row: 52px photo thumbnail (first photo, or `▦` fallback), name, type · location, health **pill** (`HEALTHY` green / `WARNING`+`WATCH` amber / `CRITICAL` red).
- **+ Add machine** — `prompt()` for name, creates a local profile entry immediately (backend `POST /api/machines` sync is a *known gap* — only profile PATCH is attempted; see §7).
- **Profile editor** — machine type, location/line, rated load kW, **engineer name / email / phone** (intl format e.g. `+234…`). Save persists locally then tries `PATCH` then `PUT` on `/machines/{name}` with `{machine_type, location, rated_load_kw}` (first success wins). Note: **the current backend has no PATCH/PUT — it only has GET/POST/DELETE** (`api/routes/machines.py`), so sync silently fails today. Decide with the client whether you add the backend endpoint or keep local-only profiles.
- **Photos** — upload (multi-file) downscaled client-side via canvas to max 800px JPEG q0.72 (localStorage size defense) or paste URL. Gallery grid with remove buttons.
- **Notes log** — flag level `INFO | WARN | CRITICAL`, timestamped, each note has a checkbox controlling whether it goes into the outbound message. Delete per note.
- **SEND TO ENGINEER** — composes a plain-text report (`buildMessage()`): header "VIDE CORTEX — MACHINE REPORT", profile + last reading + photo count + flag summary + selected notes. Send via `mailto:`, `sms:`, `wa.me` (phone digits only), or clipboard. Live **Preview** textarea.

### 3.3 Devices (`devices.html`) → `app/devices`

"Devices are the plumbing; machines are the assets."

- **Tiles**: DEVICES total, ONLINE (<60s, derived from linked machine's latest reading), UNLINKED count, AVG MSG RATE (msg/s from last 10 readings of each linked machine).
- **Registry list** — merged from backend (`/devices` etc.; backend currently has no `/devices` route — this page runs local-only today, see §7) + local `vide_devices`. Status pill: `ONLINE` (<60s) / `STALE` (<5min) / `OFFLINE` / `LOCAL ONLY`.
- **Identity editor** — device type, firmware version, IP/location, notes. Save → local + best-effort `PATCH /devices/{id}`.
- **LINKING card** — select machine, Link/Unlink. Linking = which machine's `/readings/recent/{machine}` to watch for this device's health. Synced via `PATCH /devices/{id} {linked_machine}`.
- **HEALTH card** — samples in last 10, message rate, biggest gap (s), RSSI/battery if firmware reports it (`rssi`, `battery_v`, `battery_pct` fields), last payload summary. "Re-check health" button.
- **PROVISIONING card** — the onboarding tool for firmware people: shows POST target URL and a **copy-ready sample JSON payload** with the device_id prefilled:
  ```json
  { "device_id": "edge-01", "machine_name": "MACHINE_NAME",
    "voltage_v": 230, "current_amps": 12.5, "power_kw": 2.6, "power_factor": 0.9,
    "frequency_hz": 50, "temperature_c": 62.5, "vibration_mm_s": 1.8, "load_factor_pct": 75 }
  ```
  Copy payload + copy POST URL buttons. The rule: firmware must POST at least `machine_name` + one of `power_kw` / `temperature_c` / `vibration_mm_s`.

### 3.4 Diagnostics (`diagnostics.html`) → `app/diagnostics/[machine]`

The heaviest page. Sections top-to-bottom:

1. **TARGET + SOURCE** — backend URL, machine `<select>`, range (`last 30 / 60 / 120` readings), Load.
2. **LIVE STATS** — 8 tiles (temp, vibration, power, load, voltage/current, PF/frequency, last seen, sample count) with averages in the sub-line. Temp tile turns amber >70°C, red >85°C; vibration amber >4mm/s, red >7mm/s. Trend line: `ΔT +2.3°C · ΔV +0.41mm/s over 60 pts`.
3. **WORKING BEHAVIOUR — HISTORY GRAPH** — hand-rolled `<canvas>` chart (no libs), 4 fixed-scale series: TEMP 0–100°C `#ff7a1a`, VIB 0–10mm/s `#ff5252`, POWER 0–15kW `#3ddc84`, LOAD 0–100% `#6aa8ff`. 5-point moving-average smoothing (toggleable), legend series toggles, **live pulse dot on newest point** and a **scan-sweep gradient** animation across the graph. Live loop re-fetches on interval (2/3/5/10s, pause/resume button, blinking LIVE dot). Redraws at 150fps-ish for animation; skips when `document.hidden`.
4. **EDGE vs CLOUD cards** (side by side, green left border = edge, blue left border = cloud):
   - Edge: `POST /diagnostics/analyze {use_llm:false, force_ml:true}` → rule verdict, ML fault, confidence, hours left, urgency, agreement, top factors, **measured latency ms**.
   - Cloud: same endpoint with `use_llm:true` → cloud verdict, confidence, hours, urgency, **narrative**, latency.
5. **AI VERDICT card** — "Run both + fuse" button. Fuses edge+cloud (higher urgency wins), MATCH/DIFFER note, colored border by urgency. "Log verdict to maintenance" button pushes the verdict text into the maintenance log as a `Diagnosis` record by technician "VIDE AI".
6. **PRIOR MAINTENANCE DONE** — table (date/type/tech/notes) fetched from first path that works among `/maintenance/machine/{name}` → `/machines/{name}/maintenance` → `/maintenance/history?machine=` (actual backend route: **`GET /api/maintenance/history/{machine_name}`**). Add form: date, type dropdown (Inspection / Bearing replacement / Lubrication / Rewind / Alignment / Cleaning / Other), technician, notes → `POST /api/maintenance` (actual backend route is **`POST /api/maintenance/record`** — mismatch, see §7) + local fallback copy.
7. **REACH MAINTENANCE ENGINEER** — contact info from the machine profile, auto-composed message with last reading + trend + verdicts, send Email/SMS/WhatsApp.
8. **LLM chat panel** — "ASK ABOUT THIS MACHINE", checkbox "Send machine context" appends a `[CONTEXT …]` block (last reading + trend + edge/cloud results) to the prompt. Tries in order: `/diagnostics/llm` → `/diagnostics/ask` → `/llm/chat` → `/diagnostics/analyze (use_llm:true)`. **The real backend route is `POST /api/diagnostics/chat {message}`** — see §7. Offline fallback message shows local context instead.

### 3.5 Predictions (`predictions.html`) → `app/predictions`

"Cortex verdict on when machines fail, what to change."

- **History depth selector** — last 200 / 1000 / ~3000 ("2-month window") readings.
- **CORTEX VERDICT card** (blue left border) — 4 tiles: PREDICTED FAILURE (date + hours/days left), FAULT (+confidence), URGENCY (+agreement), RISK 0–100 (sub-line: `act now` ≥80 / `plan shutdown` ≥60 / `watch closely` ≥30 / else `routine watch`). Plus: **"What to change / do"** — rule-based recommended actions derived from fault keywords (bearings → "Replace DE/NDE bearings + re-lubricate"; temp → clean cooling; vibration → re-align; electrical → megger windings; lubrication → grease per spec) + scheduling advice (<48h schedule shutdown, <168h plan this week, else next window). ML line and math line shown separately. "Re-run Cortex" + "Print report".
- **Fusion logic** — `H = min(math RUL, ML hours_to_maintenance)`; risk = max(math risk, urgency-implied risk). Math risk = `55*(temp/90) + 45*(vib/7.1)` clamped 0–100.
- **MATH layer (client-side!)** — ordinary least-squares linear regression of temperature and vibration vs hours → slopes °C/h, mm/s/h, projected hours-to-cross-failure (thresholds: **90°C, 7.1mm/s**), driving the RUL + risk when the ML model is absent. Show your work: "Slopes: temp +0.012°C/h · vib +0.0004mm/s/h · RUL 410h via temperature".
- **DEGRADATION GRAPH** — canvas chart: solid history lines (temp 0–100, vib 0–10), **dashed forecast continuation** from the regression, dashed horizontal **failure threshold lines**, vertical dashed "FORECAST →" divider.
- **MATH PREDICTIONS LOG** — audit trail of every model run (time, model source, fault, hours, confidence, risk), last 20, persisted in localStorage, clear button.
- **LLM — 2-MONTH MACHINE CONTEXT** — deep-history chat. Builds a `deepCtx` of downsampled **daily aggregates** (n, avg/max temp, avg vib, avg power per day) + profile + last 10 maintenance records + math log + verdict, appended to the prompt (keeps tokens small). Same endpoint fallback chain as Diagnostics.
- **PRINTABLE REPORT** — `@media print` styles + a generated `#print-report` block: verdict table, graph as PNG (`canvas.toDataURL`), math log table, maintenance table, last LLM note. White background, black borders for print.

---

## 4. Design language — READ THIS TWICE

> **The pixelated, terminal-CRT look is the product's character. It is not a placeholder. Do not "modernize" it into rounded cards, soft shadows, glassmorphism, or a generic dashboard theme.** The client explicitly wants this preserved.

### 4.1 Color tokens (dark is default, light is a toggle)

```css
:root{ /* dark */
  --bg:#0b0c10; --panel:#131519; --line:#24262c; --line-lit:#3a3d46;
  --ink:#e7e8ec; --dim:#71757f; --dim2:#4b4e57;
  --grad1:#ff3d1a; --grad2:#ff9d1a; --accent:#ff7a1a;   /* orange gradient family */
  --ok:#3ddc84; --warn:#ffd020; --high:#ffb020; --crit:#ff5252;
  /* diagnostics/predictions extras */
  --edge:#3ddc84; --cloud:#6aa8ff;
}
[data-theme="light"]{
  --bg:#f2f3f5; --panel:#ffffff; --line:#d8dbe0; --line-lit:#b9bec7;
  --ink:#16181d; --dim:#5b606b; --dim2:#9a9faa;
  --accent:#e85d04;
  --ok:#0d7a3f; --warn:#8a6d00; --high:#b25e00; --crit:#d00000;
}
```

Theme toggle persisted under `vide_theme`, applied via `data-theme` attribute on `<html>`. Only the Overview page has the toggle button today — in the Next.js app make it global (sidebar or topbar), with `class` or `data-theme` strategy + cookie/localStorage so no flash-of-wrong-theme.

### 4.2 Non-negotiable visual signatures

1. **Monospace everything**: `font-family:'JetBrains Mono','Consolas',monospace`. Load JetBrains Mono (self-host the woff2 if offline deployment matters — factories have bad internet).
2. **2px hard borders everywhere** (`--line`), never 1px hairlines or border-radius. Everything is `border-radius: 0`.
3. **Scanline background**: the subtle grid overlay on `body`:
   ```css
   background:
     linear-gradient(var(--bg),var(--bg)),
     repeating-linear-gradient(0deg, rgba(255,255,255,0.015) 0 1px, transparent 1px 4px),
     repeating-linear-gradient(90deg, rgba(255,255,255,0.015) 0 1px, transparent 1px 4px);
   ```
   This 4px pixel-grid texture is core to the identity. Keep it.
4. **`image-rendering: pixelated`** on `body` and the logo.
5. **The pixel logo**: a hand-drawn 16×16 SVG made of 1px `<rect>`s with `shape-rendering="crispEdges"` and an orange gradient (`#ff3d1a → #ff9d1a`) fill. It's an abstract pixel creature/mascot — **copy the SVG exactly** (it's duplicated in all 5 files; put it once in a shared React component).
6. **Card corner notch**: `.card::before` — a 6×6px `--line-lit` square pinned at the card's top-left corner (`top:-2px; left:-2px`). Small, weird, distinctive. Keep it.
7. **Dashed dividers** inside cards: `.kv { border-bottom: 1px dashed var(--line) }` key/value rows (label dim-left, bold right).
8. **Uppercase eyebrows**: 9px, `letter-spacing: 2px`, `--dim2`, above every card's content. Section labels are always tiny uppercase spaced-out text (`WORKSPACE`, `CONTROL ROOM / DIAGNOSTICS`, etc).
9. **Status language**: pills/badges/dots in 9px uppercase with tinted backgrounds + matching border colors (e.g. live badge `bg:#0f2417 color:#3ddc84 border:#1c4a30`).
10. **Flat, hard shadows** — none. Depth comes from panel-vs-bg contrast and border color only.

### 4.3 Motion — "machined, not smooth"

Animations are intentionally steppy/CRT-like. Use `steps()` timing, not ease:

```css
@keyframes pulse{ 0%{ color:var(--accent); } 100%{ color:var(--ink); } }
.value.pulse{ animation:pulse .6s steps(4); }        /* tile value flash on change */
@keyframes blink{ 50%{ opacity:.25; } }
.livedot.on{ animation:blink 1s steps(2) infinite; }  /* LIVE indicator */
button:active{ transform:translate(1px,1px); }        /* physical key-press */
```

Canvas extras (Diagnostics/Predictions): pulsing glow dot on the newest data point (`Math.sin(Date.now()/350)` radius) and a sweeping scan gradient across the chart (`Date.now()%3000`). These sell the "live radar" feel — keep them (or an equivalent) in the rebuild.

### 4.4 Component inventory → suggested React components

| Current CSS class | React component | Notes |
|---|---|---|
| `.sidebar`, `.brand`, `.nav` | `<Sidebar>` in root layout | nav: Overview `/`, Machines `/machines`, Devices `/devices`, Diagnostics `/diagnostics`, Predictions `/predictions` |
| `.topbar` | `<Topbar>` | crumb + title + badge(s) + theme toggle |
| `.card`, `.eyebrow` | `<Card label="...">` | keep corner notch via CSS |
| `.tile` (+`.warn`/`.crit`) | `<Tile label value unit sub state>` | value pulse handled internally |
| `.kv` | `<KV label value>` | |
| `.badge`, `.pill`, `.flag`, `.status-dot` | `<Badge tone="ok|warn|crit|dim">` | |
| `.empty-state` | `<EmptyState>` | dashed border box — used everywhere, keep the copy style |
| `.mitem` / `.ditem` list | `<SelectList>` | master-detail pattern on Machines/Devices |
| `#toast` | `<Toast>` (context) | 2.2–2.4s auto-hide, fixed bottom-center |
| `.chatlog` / `.msg` | `<ChatPanel>` | user right/dark, llm left/blue-tinted |
| `canvas#chart` | `<TimeSeriesChart>` / `<ForecastChart>` | see §6 |
| `table.maint` / `table.mlog` | `<DataTable>` | uppercase 9px th, dashed row borders |
| `button`, `.secondary`, `.danger`, `.small` | `<Button variant>` | |

### 4.5 Copy & voice

Keep the terse terminal voice: uppercase labels, no sentence case on chrome ("FLEET ONLINE", "ATTENTION QUEUE", "NO SOURCE CONNECTED"). Body copy is short and operational. Footer bar on every page: left `VIDE CORTEX / <SECTION>`, right `LIVE DATA` / `NO DATA SOURCE CONNECTED`.

### 4.6 Number formatting conventions

Observed in the UI — reuse: temp `1` decimal + `°C`, vibration `2` decimals + `mm/s`, power `1` decimal + `kW`, load `1` decimal + `%`, confidence `0` decimals + `%`, hours `0` decimals + `h`, rate `2–3` decimals + `msg/s`. Units render as a small dim `.unit` span inside the big value.

---

## 5. localStorage contract — PRESERVE THESE KEYS

Operators move between machines/browsers and rely on this state. Keep the same keys (or migrate with a one-time reader):

| Key | Shape | Written by |
|---|---|---|
| `vide_backend_url` | `string` — e.g. `http://127.0.0.1:8000/api` | all pages |
| `vide_theme` | `"dark" \| "light"` | Overview |
| `vide_machine_profiles` | `{ [machine_name]: { type, location, rated_load_kw, engineer_name, engineer_email, engineer_phone } }` | Machines (read by Diagnostics, Predictions) |
| `vide_machine_notes` | `{ [machine_name]: Array<{ id:number, ts:ISOstring, text:string, flag:"info"\|"warn"\|"critical", send:boolean }> }` | Machines |
| `vide_machine_photos` | `{ [machine_name]: string[] }` — dataURL or URL | Machines |
| `vide_devices` | `{ [device_id]: { type, fw, ip, notes, linked: machine_name\|null } }` | Devices |
| `vide_maintenance_log` | `{ [machine_name]: Array<{ date, type, technician, notes }> }` | Diagnostics |
| `vide_pred_log` | `{ [machine_name]: Array<{ ts, fault, conf, urg, hours, risk, action, src }> }` | Predictions |

In TS: one `lib/storage.ts` with typed get/set helpers per key + zod schemas optional. These are the seed of a later backend "profiles" API — structure them cleanly so the swap is easy.

---

## 6. API reference (what the UI actually consumes)

Base URL = the user's configured base (default `http://127.0.0.1:8000/api`). All routes are prefixed `/api` on the server. CORS origins are configured in `config.yaml` (`api.cors_origins`) — **add your Next.js dev origin (`http://localhost:3000`) there** or proxy through Next rewrites (recommended, see §8).

### Endpoints used by the UI

| Method | Path | Used for | Notes |
|---|---|---|---|
| GET | `/health/detailed` | connection test, factory name, acquisition status | |
| GET | `/machines` | list + `health_status` + `stats` + `maintenance_prediction` | |
| POST | `/machines` | register `{machine_name, machine_type, location, rated_load_kw}` | UI doesn't call it yet (gap) |
| DELETE | `/machines/{name}` | | UI doesn't call it yet (gap) |
| GET | `/readings/recent/{machine_name}?limit=N` | **the workhorse** — history for tiles, graphs, predictions | 404s when no readings — catch it |
| POST | `/readings` | ingest single reading (firmware target) | |
| POST | `/readings/batch` | ingest up to 100 | |
| GET | `/diagnostics/critical-events?limit=N` | Overview attention queue | |
| POST | `/diagnostics/analyze` | edge/cloud verdicts (see body below) | the AI core |
| POST | `/diagnostics/chat` | LLM chat `{message}` | UI's fallback chain doesn't hit it — fix in rebuild |
| GET | `/maintenance/history/{machine_name}` | maintenance table | UI's fallback chain misses it — fix in rebuild |
| POST | `/maintenance/record` | add maintenance record | UI posts `/maintenance` — fix in rebuild |
| GET | `/ml/status`, `/ml/predictions/{m}`, `/ml/predictions/{m}/history` | available, not yet used by UI | opportunity |
| GET | `/anomalies`, `/anomalies/machine/{m}` | available, not yet used | opportunity |
| GET | `/config`, `/config/thresholds`, `/config/rules`, `/config/reload` | available, not yet used | opportunity |

### `POST /api/diagnostics/analyze` — request

```ts
interface AnalyzeRequest {
  machine_name: string;
  machine_type?: string;      // "motor" | "pump" | "compressor" | "generator" | "conveyor" | "hvac" | "custom"
  rated_load_kw?: number;     // default 10
  voltage_v?: number | null;
  current_amps?: number | null;
  power_kw?: number | null;
  power_factor?: number;      // default 1
  frequency_hz?: number;      // default 50
  temperature_c?: number | null;      // (accepted via reading passthrough)
  vibration_mm_s?: number | null;
  load_factor_pct?: number | null;
  use_llm?: boolean;          // false = edge only, true = add LLM narrative
  force_ml?: boolean;         // true = bypass ML batching, immediate prediction
}
```

### `POST /api/diagnostics/analyze` — response

```ts
interface AnalyzeResponse {
  is_anomaly: boolean;
  derived_metrics: Record<string, number>;   // power, reactive ratio, stability etc.
  all_faults: Array<{ type: string; severity: string; description?: string; /* ... */ }>;
  all_rule_evaluations: Array<{ rule: string; triggered: boolean; /* ... */ }>;
  primary_fault: { type: string; /* ... */ } | null;
  ml_prediction: {
    machine_name: string;
    model_version: string;
    fault_type: string;
    fault_type_confidence: number;      // 0..1
    hours_to_maintenance: number;
    feature_importance: Record<string, number>;
    top_factors: string[];              // human-readable drivers
  } | null;                             // null when model not trained/loaded
  fused_diagnosis: {
    overall_urgency: "critical" | "high" | "medium" | "low" | "none";
    agreement: string;                  // e.g. rules/ML agreement text
    summary: string;
  };
  llm_analysis?: string;                // when use_llm=true
  llm_error?: string;                   // LLM not configured / failed
}
```

### Reading object (returned by `/readings/recent/{m}`)

```ts
interface Reading {
  machine_name: string;
  timestamp: string;          // ISO
  voltage_v: number | null;
  current_amps: number | null;
  power_kw: number | null;
  power_factor: number | null;
  frequency_hz: number | null;
  energy_kwh: number | null;
  temperature_c: number | null;
  vibration_mm_s: number | null;
  acceleration_g: number | null;
  pressure_bar: number | null;
  flow_lpm: number | null;
  load_factor_pct: number | null;
  // devices page also checks for: rssi, battery_v, battery_pct
}
```

**Important:** responses arrive **newest-first** from the API; the graph pages `.reverse()` to oldest→newest for plotting. Be consistent about this in the rebuild.

### Health statuses

`health_status` on machines: `"healthy" | "watch" | "warning" | "critical"` (from maintenance urgency). Urgency everywhere: `"none" | "low" | "medium" | "high" | "critical"`. Color map: none/low → ok green, medium → warn yellow, high → amber, critical → red.

Machine types with default maintenance intervals (config.yaml): motor 2000h, pump 1500h, compressor 1800h, generator 2500h, conveyor 3000h, hvac 4000h, custom 2000h.

---

## 7. Known gaps & backend mismatches — fix or preserve consciously

These are things the HTML papers over with fallbacks. Don't silently drop the fallbacks, and ideally fix the backend side:

1. **No `PATCH`/`PUT /machines/{name}`** in backend — machine profile sync silently no-ops. Add the endpoint or keep profiles client-side.
2. **No `/devices` routes at all** in backend — the entire Devices registry is localStorage-only right now. Options: (a) keep local-only, (b) add device CRUD + `linked_machine` column to the backend (recommended — provisioning workflow is valuable).
3. **Maintenance route mismatch** — UI calls `POST /api/maintenance` and tries `GET /api/maintenance/machine/{name}`; backend exposes `POST /api/maintenance/record` and `GET /api/maintenance/history/{name}`. Works only via fallback guessing. Pin the real routes in your typed API client.
4. **LLM chat route mismatch** — UI probes `/diagnostics/llm`, `/diagnostics/ask`, `/llm/chat`; backend only has `POST /api/diagnostics/chat {message}`. Result: the chat panel currently always shows the OFFLINE fallback. Wire it to `/diagnostics/chat` properly in the rebuild (big UX win, cheap).
5. **LLM disabled by default** (`config.yaml: llm.enabled: false`, needs `GROQ_API_KEY`). Cloud analysis fails gracefully — keep that UX.
6. **`GET /readings/recent/{m}` returns 404 (not empty array) when a machine has no readings** — every caller must catch it. (Consider fixing the backend to return `[]`.)
7. **`POST /diagnostics/analyze` requires `temperature_c`/`vibration_mm_s`** for the ML feature vector but the Pydantic model doesn't declare them (they ride along in `model_dump()`? — no: undeclared fields are dropped). The UI sends them anyway. Verify against live `/docs` and, if they're dropped, extend `AnalyzeReadingInput` in `api/routes/diagnostics.py`.
8. **Auth: none.** No login, no API keys. Decide early whether the webapp needs auth (a factory console on a LAN probably wants at least a shared PIN; if public, definitely auth).
9. **`db.json`/`connector.py`/`script.js`** at repo root are dev scratch — not part of the UI contract.
10. **CORS** — add `http://localhost:3000` (or use Next rewrites to dodge CORS entirely — see §8).

---

## 8. Next.js + TypeScript implementation guidance

### 8.1 Setup

- **Next.js 14/15 App Router + TypeScript**, `src/` dir. React Server Components for layout/static chrome; **all data fetching is client-side** (the backend URL is dynamic per-user and polling-based) — so pages that talk to the API are `"use client"` components (or client islands inside RSC layouts).
- **Tailwind or plain CSS Modules — your call**, but the design is token-heavy: put the CSS custom properties from §4.1 in `globals.css` `:root` and drive components with `var(--*)`. Don't hardcode hexes in components.
- ESLint + Prettier, `tsc --noEmit` in CI.

### 8.2 Routing map

```
app/
  layout.tsx                ← Sidebar + Topbar + theme provider + toast provider
  page.tsx                  ← Overview        (index.html)
  machines/page.tsx         ← Machines        (machines.html)
  devices/page.tsx          ← Devices         (devices.html)
  diagnostics/page.tsx      ← Diagnostics, machine via ?m= query or select
                             (or diagnostics/[machine]/page.tsx — nice URL)
  predictions/page.tsx      ← Predictions     (predictions.html)
```

Sidebar `active` state via `usePathname()`. Preserve the footer bar + breadcrumb per page.

### 8.3 Data layer

- `lib/api.ts`: a typed fetch client. One `ApiProvider` React context holding `{ baseUrl, setBaseUrl, status }` where status is `"unknown" | "testing" | "live" | "offline"`. All pages read base URL from context (seeded from localStorage), **not from per-page inputs** — keep the input in a settings popover in the topbar so it stays global (behavioral improvement, same capability).
- TypeScript types in `lib/types.ts` straight from §6.
- Polling: a small `usePoll(fn, ms, { enabled })` hook that respects `document.visibilityState` (port the existing hidden-tab skip). SWR/React-Query with `refetchInterval` also works and gives you caching + dedupe — recommended if you want less hand-rolled code; keep `refetchOnWindowFocus` off for kiosk feel.
- API errors → toast + badge state, never crash a page. The current app's resilience bar (§2) is the acceptance bar.

### 8.4 Charts

Two options:
1. **Port the canvas code as-is** into a React `<canvas>` component (it's ~40 lines each, dependency-free, and the pixel-CRT styling is bespoke — the glow dot, scan sweep, dashed forecast, threshold lines). Use `requestAnimationFrame` with a `cancelled` cleanup flag instead of `setInterval`, and `ResizeObserver` instead of `window.resize`.
2. If you prefer a lib, use one with full custom styling control (uPlot is fast and pixel-friendly). **Recreate**: fixed scales (NOT auto-scale — that's a deliberate choice so noise isn't exaggerated), `steps()`-feel, the exact series colors, threshold/forecast dashes, and the sweep animation.

### 8.5 The tricky bits (port carefully)

- **Photo pipeline** (Machines): canvas downscale to ≤800px JPEG q0.72 before storage, with the "storage full" catch + keep-last-5 fallback. If you move photos to the backend or S3, keep client-side downscale anyway (bandwidth).
- **Message composer** (Machines/Diagnostics): `buildMessage()` plain-text format + `mailto:` / `sms:` / `wa.me` links. WhatsApp strips non-digits from the phone. Keep exact formats — people recognize these messages.
- **Print report** (Predictions): `@media print` styles + `canvas.toDataURL()` snapshot into the report. In Next, render the report component hidden and `window.print()` — or generate server-side PDF later.
- **Local machine merge**: `machines.filter(not in backend)` merged list with `_local` flag must keep working offline.
- **The `prompt()`/`confirm()` dialogs** for add-machine/delete — replace with proper in-app modals styled to the theme (a genuine UX upgrade, same behavior).

### 8.6 Deployment / environment

- Backend env: `VIDE_CONFIG_PATH`, `GROQ_API_KEY` (optional LLM), broker creds via config.yaml.
- Frontend env: `NEXT_PUBLIC_DEFAULT_API_URL` (fallback when localStorage empty). Better: `NEXT_PUBLIC_API_PROXY=true` + a rewrite:
  ```js
  // next.config.js
  module.exports = { async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.API_URL}/api/:path*` }];
  }};
  ```
  With the proxy, the default base URL becomes same-origin `/api` → **no CORS changes needed** and the firmware POST target stays the backend's real address (compute it from the backend, not from `window.location` — note `fwTarget()` currently derives it from the API base URL, keep that logic).
- The client's deployment reality: this often runs **on-prem near the machines, sometimes fully offline**. Bundle fonts, no external CDNs, no analytics pings, graceful behavior with zero internet.

---

## 9. Testing your rebuild (acceptance checklist)

1. `python main.py` → backend up at `:8000` (`/docs` loads).
2. Seed a machine + readings (or use `test_api.py` / `test_ml_pipeline.py` at repo root; or `POST /api/readings` with the sample payload from §3.3 for `machine_name: "demo-motor"`).
3. Point the webapp at the backend → badges go `LIVE`, sidebar dot green.
4. Overview: tiles populate, pulse on change, critical events list, **instant prediction returns rule + ML + fused verdict** (train the model first via `training/` if `ml_prediction` is null — UI must show "Model not loaded" gracefully).
5. Machines: profile save, photo upload + gallery, notes with flags, message preview + all 4 send buttons.
6. Devices: register local device, link to machine, health computes from the linked machine's readings, provisioning payload copies with correct device_id.
7. Diagnostics: graph draws with all 4 series + legend toggles + smoothing toggle, live loop updates, edge analysis runs (~ms latency shown), cloud analysis runs (needs LLM key) or fails gracefully, fuse verdict, maintenance add + list, LLM chat gets an answer (fix §7.4 to verify).
8. Predictions: load 1000 readings, cortex verdict tiles fill, math slopes + RUL show, forecast dashes render past the divider, math log appends each run, print report renders legibly in print preview.
9. Kill the backend → every page shows `OFFLINE`/`ERROR` gracefully, local data still visible, no white screens, no unhandled rejections.
10. Toggle light mode → everything readable (test the graph colors in light mode too; canvas uses hardcoded dark colors today — a known wart, fix it with a theme-aware palette).

---

## 10. Tips (earned the hard way)

- **`force_ml: true` on `/diagnostics/analyze`** is what makes the button feel instant. Without it the ML layer batches predictions (every ~20 readings) and the UI would return nothing.
- **Urgency ranking** when fusing edge+cloud: `critical(4) > high(3) > medium(2) > low(1) > none(0)` — higher wins. Predictions page also maps urgency→risk `{critical:95, high:80, medium:60, low:30, none:10}`.
- **Online = latest reading < 60s old.** Stale = < 5min (Devices). These constants are load-bearing for the status language.
- **Daily aggregation** for the 2-month LLM context (§3.5) exists to keep prompts small — replicate it server-side if you ever move context-building to the API.
- **Fixed chart scales** (temp 0–100, vib 0–10, power 0–15, load 0–100) — deliberate. Auto-scaling made noise look like disasters in an earlier iteration.
- Don't fetch `/machines/{name}` for the detail pages — the UI uses `/machines` + `/readings/recent/{name}?limit=N` because it's fewer round-trips while polling.
- The backend is **single-worker SQLite with WAL** — fine for a LAN console, don't hammer it with per-tile requests; batch per machine per tick like `refreshAll()` does (`Promise.all` over machines, `limit=1`).
- Keep the whole thing **keyboard-driveable and kiosk-friendly**: big-ish targets, no hover-only affordances, high contrast. This is a factory control-room screen.
- If you add pages (Settings, Alerts ack via `POST /diagnostics/critical-events/{id}/confirm` — the confirm flow exists in the backend but has **no UI yet**), match the design language in §4 and the terse uppercase copy voice.

---

