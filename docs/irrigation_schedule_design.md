# Irrigation schedule — design & assumptions

This document describes how `scripts/sprinkler_schedule.py` decides **sprinkler ON/OFF** and **run duration (minutes)** from forecast **humidity** and **rain**, using defaults suitable for a small residential lawn zone.

**Related scripts**

| Script | Role |
|--------|------|
| `scripts/fetch_weather.py` | Pull forecast from [Open-Meteo](https://open-meteo.com/) |
| `scripts/sprinkler_schedule.py` | Apply rules → ON/OFF + minutes + estimated gallons |

---

## 1. Design goal

Combine **weather-aware scheduling** (industry practice) with **simple humidity bands** when soil sensors are not yet wired:

1. **Do not water** if rain is likely tonight or tomorrow (avoid double-watering).
2. **Shorter runs** when air humidity is high (less evaporation demand).
3. **Longer runs** when air humidity is low (more drying).
4. Estimate **gallons** and **mm depth** from assumed zone flow rate.

This is a **v1 heuristic**, not a full ET (evapotranspiration) model. Soil moisture from your `heli_*` CSV should override or blend with these rules in production.

---

## 2. References (web)

| Source | What we borrowed |
|--------|------------------|
| [Rain Bird — zone flow / GPM](https://www.rainbird.com/homeowners/how-many-sprinklers-can-be-used-zone-or-valve) | Bucket test; typical rotor ~3 GPM/head; zone totals |
| [Hunter residential design guide (PDF)](https://www.hunterirrigation.com/sites/default/files/2025-02/LIT-226-RevI-DG-ResidentialSystem-US-web.pdf) | Zone GPM must not exceed supply; ~75–80% design capacity |
| [Hydrawise water triggers](https://support.hydrawise.com/hc/en-us/articles/360009285814-Water-Triggers-Overview) | Skip when rain probability high; adjust by humidity/temperature |
| [Rain Bird weather-based irrigation](https://www.rainbird.com/weatherbasedirrigation) | Seasonal adjust + rain sensor skip |
| FAO / crop guides (general) | Sprinkler application efficiency ~**80%** (vs drip ~90–95%) |

---

## 3. Default hardware assumptions

These are **placeholders** until you measure your system (bucket test at hose bib or meter).

| Parameter | Default | Typical range | Notes |
|-----------|---------|---------------|--------|
| **Zone flow** | **8 GPM** | 6–12 GPM | 3/4" meter ≈ 11 GPM available; design at **75%** ≈ **8.25 GPM** ([MyPlumbingPal / Hunter](https://myplumbingpal.com/irrigation-systems/residential-sprinkler-system-design-capacity/)) |
| **Head flow** | ~3 GPM each | 1.5–4 GPM | Rain Bird 5000 @ 35 PSI example |
| **Heads per zone** | 2–3 | 1–4 | Must sum ≤ zone capacity |
| **Application efficiency** | **80%** | 70–85% | Sprinkler spray loss |
| **Zone area** | **1000 ft²** | 500–2000 ft² | Adjust `--zone-area-sqft` |
| **Base run time** | **20 min** | 10–30 min | Peak summer reference; adjust `--base-minutes` |

### Gallons per minute (examples)

| Setup | Assumed GPM | 20 min run | 10 min run |
|-------|-------------|------------|------------|
| Small drip/manifold | 2 GPM | 40 gal | 20 gal |
| **Default residential zone** | **8 GPM** | **160 gal** | **80 gal** |
| Large 1" service zone | 12 GPM | 240 gal | 120 gal |

Formula used in code:

```text
gallons = flow_gpm × duration_minutes
depth_mm ≈ (gpm × efficiency × 231 in³/gal) / (area_ft² × 144 in²/ft²) × 25.4 mm/in × minutes
```

---

## 4. Watering window

Default **night window**: **22:00 – 06:00** local time (lower evaporation, common municipal guidance).

`sprinkler_schedule.py` checks rain in that window for **tonight** and **tomorrow daytime** for the “rain tomorrow → skip tonight” rule.

---

## 5. Decision rules (v1)

### 5.1 Sprinkler OFF (skip)

| Condition | Rationale |
|-----------|-----------|
| Rain probability ≥ **50%** during tonight's window | Hydrawise-style “high probability of rain” abort |
| Any **rain-likely hour** tonight (precip > 0.05 mm, rain WMO code, or prob ≥ 50%) | Watering during rain wastes water |
| Tomorrow total precip ≥ **2 mm** OR max rain prob ≥ **50%** OR ≥ **3** rain-likely hours | **Your rule:** don't water tonight if tomorrow rains |
| Next-24h average humidity ≥ **85%** | Very humid air → minimal evaporation; skip unless soil is dry |

### 5.2 Duration multiplier (humidity)

Uses **next 24 h average relative humidity** as a proxy when soil data is unavailable.

| Humidity (24h avg) | Band | Multiplier | Example @ 20 min base |
|--------------------|------|------------|------------------------|
| 0 – 35% | very_dry | **1.25** | 25 min |
| 35 – 50% | dry | **1.00** | 20 min |
| 50 – 65% | moderate | **0.75** | 15 min |
| 65 – 80% | humid | **0.50** | 10 min |
| 80 – 100% | very_humid | **0.25** | 5 min |

```text
duration_minutes = round(base_minutes × humidity_multiplier × rain_factor)
```

### 5.3 Rain reduction (not full skip)

If rain probability is **30–49%** tonight or tomorrow: `rain_factor = 0.5` (half duration).

---

## 6. Example decision table (base = 20 min, 8 GPM)

| Scenario | ON? | Minutes | Gallons (approx) |
|----------|-----|---------|------------------|
| Dry (30% RH), no rain | Yes | 25 | 200 |
| Moderate (55% RH), no rain | Yes | 15 | 120 |
| Humid (72% RH), no rain | Yes | 10 | 80 |
| Very humid (88% RH) | No | 0 | 0 |
| Rain tomorrow (60% prob) | No | 0 | 0 |
| Light rain chance (40% prob), 45% RH | Yes | 10 | 80 |

Run live for your city:

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose"
```

---

## 7. Output fields (JSON)

| Field | Meaning |
|-------|---------|
| `sprinkler_on` | `true` / `false` |
| `duration_minutes` | Valve open time |
| `duration_factor` | humidity × rain multiplier |
| `estimated_gallons` | `flow_gpm × minutes` |
| `estimated_depth_mm` | Approx. depth over `zone_area_sqft` |
| `skip_reason` | Why OFF (if any) |
| `rain_checks` | Tonight + tomorrow rain stats |

---

## 8. Future integration (smart sprinkler)

| Input | Override |
|-------|----------|
| Soil moisture / humidity from `heli_tx` CSV | Force OFF if soil wet; extend if dry despite humid air |
| Camera / zone mask | Direction only (not in this script) |
| STM32 / `hp_tk_rx` | Send `duration_minutes` + angle to hardware |

Suggested merge formula (future):

```text
final_minutes = min(soil_based_max, weather_duration) × zone_factor
run if soil_dry AND NOT weather_skip
```

---

## 9. Configuration

Copy and edit:

```bash
cp scripts/irrigation_config.example.json scripts/irrigation_config.json
```

CLI overrides:

```bash
python3 scripts/sprinkler_schedule.py \
  --city "San Jose" \
  --base-minutes 20 \
  --flow-gpm 8 \
  --zone-area-sqft 1200 \
  --json
```

---

## 10. Limitations

- **Relative humidity ≠ soil moisture** — always prefer your soil sensor when available.
- Open-Meteo is forecast, not measured rain at your yard.
- Single zone; no multi-zone staggering.
- No wind, freeze, or ET calculation in v1.

Measure your **actual GPM** with a bucket test and update `--flow-gpm` for accurate gallon estimates.
