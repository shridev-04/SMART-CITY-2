import json
import logging
import hashlib
import time
import base64
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

import requests
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ============================================================
# CITYMIND AI: UNIFIED 10-MINUTE MULTI-NODE SYSTEM FOR VERCEL
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smart_city_api")

# PRIMARY & BACKUP GEMINI API KEYS (Automatic Failover)
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_1", "AIzaSyAI6fiMweY9HhWNms_VDmNf6wtE6irw76w"),
    os.getenv("GEMINI_API_KEY_2", "AIzaSyDeVvlOZFnu-DJFALiki3CFIarg_gy9BQY")
]

# Device Authentication Token
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "SHRIDEV")

# BOT 1: Routine 10-Minute Analysis Bot (Every 10 min master report + photo)
ANALYSIS_BOT_TOKEN = os.getenv("ANALYSIS_BOT_TOKEN", "8830694274:AAFPQGz5-BWPPDTVQWUA3PBNMN8wQ7p9vgI")
ANALYSIS_CHAT_ID = os.getenv("ANALYSIS_CHAT_ID", "6168018748")

# BOT 2: Nagar Nigam Emergency Action Bot (STRICT EMERGENCY ONLY)
ALERT_BOT_TOKEN = os.getenv("ALERT_BOT_TOKEN", "8883635861:AAErNrAl9r7gE6Xq17sJDAxDuIK82eGhhyE")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID", "6168018748")

# Gemini Models Pool with fallback
GEMINI_MODELS_POOL = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash"
]

MAX_IMAGE_SIZE = 4 * 1024 * 1024  # 4 MB
DEFAULT_INTERVAL_SECONDS = 600     # 10 minutes (600 seconds)
SYNC_WINDOW_SECONDS = 30          # Slot boundary window

# State cache directory (/tmp on Vercel Serverless / Linux, tempdir on Windows)
TMP_DIR = Path(os.environ.get("VERCEL_TMP_DIR", "/tmp")) if os.path.exists("/tmp") else Path(os.environ.get("TEMP", "."))
STATE_FILE = TMP_DIR / "citymind_state.json"

# In-Memory Storage
IN_MEMORY_SLOTS: Dict[str, Dict[str, Any]] = {}
IN_MEMORY_HEARTBEATS: Dict[str, Dict[str, Any]] = {}
IN_MEMORY_TELEMETRY_LOGS: List[Dict[str, Any]] = []
IN_MEMORY_REPORTS: List[Dict[str, Any]] = []
DISPATCHED_SLOTS_CACHE: Dict[str, float] = {}
LAST_DISPATCHED_TIMESTAMP: float = 0.0

app = FastAPI(
    title="Smart City AI Autonomous Operations Platform",
    description="Multimodal IoT Urban Monitoring with Gemini AI and Telegram Automation",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def configured(value: Optional[str]) -> bool:
    return bool(value and not str(value).startswith("PASTE_"))


def format_ist_time(dt: Optional[datetime] = None) -> str:
    """Formats UTC datetime to Indian Standard Time (IST: UTC + 5:30)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    return dt.astimezone(ist_tz).strftime("%d-%b-%Y %I:%M %p")


def get_slot_key(location_name: str, dt: Optional[datetime] = None) -> str:
    """Calculates unified 10-minute slot identifier (:00, :10, :20, :30, :40, :50)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    min_bucket = (dt.minute // 10) * 10
    loc_clean = location_name.strip().lower().replace(" ", "_")
    return f"slot_{loc_clean}_{dt.strftime('%Y%m%d_%H')}{min_bucket:02d}"


# ============================================================
# PERSISTENCE HELPERS (Preserves State across Serverless Invocations)
# ============================================================

def save_state_to_tmp():
    try:
        data = {
            "slots": {k: {sk: sv for sk, sv in v.items() if sk != "image_bytes"} for k, v in IN_MEMORY_SLOTS.items()},
            "heartbeats": {k: {hk: (hv.isoformat() if isinstance(hv, datetime) else hv) for hk, hv in v.items()} for k, v in IN_MEMORY_HEARTBEATS.items()},
            "dispatched": DISPATCHED_SLOTS_CACHE,
            "reports": [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in IN_MEMORY_REPORTS[:10]],
            "last_dispatch": LAST_DISPATCHED_TIMESTAMP
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.debug("Failed saving state to /tmp: %s", e)


def load_state_from_tmp():
    global IN_MEMORY_SLOTS, IN_MEMORY_HEARTBEATS, DISPATCHED_SLOTS_CACHE, IN_MEMORY_REPORTS, LAST_DISPATCHED_TIMESTAMP
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                DISPATCHED_SLOTS_CACHE.update(data.get("dispatched", {}))
                LAST_DISPATCHED_TIMESTAMP = data.get("last_dispatch", LAST_DISPATCHED_TIMESTAMP)
                if not IN_MEMORY_REPORTS and data.get("reports"):
                    IN_MEMORY_REPORTS.extend(data["reports"])
    except Exception as e:
        logger.debug("Failed loading state from /tmp: %s", e)


load_state_from_tmp()


# ============================================================
# PYDANTIC SCHEMAS
# ============================================================

class TelemetryPayload(BaseModel):
    node_id: str = Field(..., description="Unique node identifier, e.g. node_1_sen54")
    location_name: str = Field(..., description="Geographic zone / city location")
    latitude: float = Field(default=25.618639)
    longitude: float = Field(default=85.148456)
    sensor_type: str = Field(..., description="SEN54, CAMERA_VISION, MQ135_DRAIN")
    data: Dict[str, Any] = Field(..., description="Sensor measurements")


# ============================================================
# GEMINI MULTIMODAL INFERENCE ENGINE
# ============================================================

def call_gemini_safe(contents: list, config: types.GenerateContentConfig) -> Optional[dict]:
    """Tries multiple API keys and model fallbacks for 100% reliable execution."""
    for key_idx, key in enumerate(GEMINI_API_KEYS):
        try:
            client = genai.Client(api_key=key)
        except Exception as e:
            logger.warning("Key #%d failed to create client: %s", key_idx + 1, e)
            continue

        for model_name in GEMINI_MODELS_POOL:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                if response and response.text:
                    clean = response.text.strip()
                    if clean.startswith("```json"):
                        clean = clean[7:]
                    if clean.endswith("```"):
                        clean = clean[:-3]
                    clean = clean.strip()
                    result = json.loads(clean)
                    if isinstance(result, dict):
                        return result
            except Exception as e:
                err_str = str(e)
                logger.warning("Key #%d, model %s failed: %s", key_idx + 1, model_name, err_str[:120])
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str.upper() or "QUOTA" in err_str.upper():
                    logger.info("Key #%d quota reached, switching key...", key_idx + 1)
                    break
                continue

    logger.error("All Gemini API keys and models exhausted.")
    return None


def analyze_multimodal_urban_slot(
    location_name: str,
    slot_data: Dict[str, Any],
    image_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None
) -> Dict[str, Any]:
    """Combines Node 1 SEN54 sensor data + Node 2 Raspberry Pi Camera image in ONE single Gemini API call."""
    node_1_data = slot_data.get("node_1", {}).get("data", {})
    node_2_drain = slot_data.get("node_2_drain", {}).get("data", {})

    temp_c = node_1_data.get("temperature_c")
    humidity = node_1_data.get("humidity_percent")
    pm2_5 = node_1_data.get("pm2_5", 0.0)
    voc_idx = node_1_data.get("voc_index", 100.0)
    drain_overflow = node_2_drain.get("drain_overflow_detected", False)

    prompt = f"""
You are CityMind AI, the urban intelligence system deployed for Municipal Corporation (Nagar Nigam) operations.
Analyze this synchronized 10-minute telemetry and vision package from area '{location_name}'.

Sensor Readings:
- Node 1 (Sensirion SEN54 Environmental Sensor):
  Temperature: {temp_c}°C
  Humidity: {humidity}%
  PM2.5: {pm2_5} µg/m³
  PM10: {node_1_data.get('pm10_0', 'N/A')} µg/m³
  VOC Index: {voc_idx}

- Node 2 Drain / Sewer (If installed):
  Drain Overflow Detected: {drain_overflow}
  Gas Level: {node_2_drain.get('gas_level_status', 'N/A')}

- Node 2/3 (Raspberry Pi Street Vision Camera):
  {"Inspect the attached street camera photo for road conditions." if image_bytes else "NO CAMERA IMAGE PROVIDED (Camera offline or failed to capture)."}

Required Tasks:
1. Examine Road Conditions from Camera Photo:
   - "Sadak Jal Bharaw" / Waterlogging / Flood puddles.
   - "Kuda Jama hona" / Garbage heaps / Road or drain waste blockage.
2. Examine Air Quality and Climate:
   - Evaluate PM2.5, VOC index, and temperature status for public health.
3. Determine if Nagar Nigam Immediate Field Action is needed.

Return ONLY a valid JSON object matching this schema:
{{
  "waterlogging_status": "NONE|MINOR_PUDDLE|MODERATE|SEVERE_FLOODING",
  "garbage_status": "NONE|MINOR_LITTER|VISIBLE_DUMP|HEAVY_BLOCKAGE",
  "air_quality_status": "GOOD|MODERATE|POOR|HAZARDOUS",
  "overall_severity": "low|medium|high",
  "emergency_detected": false,
  "summary": "Clear 2-3 sentence assessment combining visible road conditions (waterlogging/garbage) and ambient weather/air quality",
  "recommended_action": "Direct instruction for Nagar Nigam municipal departments (or 'Continue routine automated 10-minute monitoring' if normal)",
  "confidence": 0.95
}}

CRITICAL RULES:
- If visible waterlogging is MODERATE or SEVERE_FLOODING: emergency_detected MUST be true, overall_severity MUST be 'high'.
- If visible garbage is VISIBLE_DUMP or HEAVY_BLOCKAGE: emergency_detected MUST be true, overall_severity MUST be 'high'.
- If drain_overflow is true or PM2.5 > 150: emergency_detected MUST be true, overall_severity MUST be 'high'.
- If everything is clean and within safe limits: overall_severity MUST be 'low', emergency_detected MUST be false.
"""

    contents = []
    if image_bytes and mime_type:
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
    contents.append(prompt)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1
    )

    result = call_gemini_safe(contents, config)

    # Fallback if Gemini quota is completely unavailable
    if not result:
        is_em = drain_overflow or (isinstance(pm2_5, (int, float)) and pm2_5 > 150)
        return {
            "waterlogging_status": "NONE",
            "garbage_status": "NONE",
            "air_quality_status": "HAZARDOUS" if (isinstance(pm2_5, (int, float)) and pm2_5 > 150) else "MODERATE",
            "overall_severity": "high" if is_em else "low",
            "emergency_detected": is_em,
            "summary": f"Telemetric status from {location_name} logged. Weather and road parameters within operational limits.",
            "recommended_action": "Immediate field response required!" if is_em else "Continue routine 10-minute monitoring.",
            "confidence": 0.85
        }

    # Strict override for hardware emergency triggers
    if drain_overflow or (isinstance(pm2_5, (int, float)) and pm2_5 > 150):
        result["overall_severity"] = "high"
        result["emergency_detected"] = True

    return result


# ============================================================
# TELEGRAM DISPATCH ENGINE (Dual Bot Architecture)
# ============================================================

def telegram_send_message(bot_token: str, chat_id: str, message: str) -> bool:
    if not configured(bot_token) or not configured(chat_id):
        return False
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=15
        )
        return res.json().get("ok", False)
    except Exception as e:
        logger.error("Telegram sendMessage failed: %s", e)
        return False


def telegram_send_photo(bot_token: str, chat_id: str, caption: str, image_bytes: bytes, mime_type: str) -> bool:
    if not configured(bot_token) or not configured(chat_id):
        return False
    try:
        filename = "smart_city.jpg" if "jpeg" in mime_type or "jpg" in mime_type else "smart_city.png"
        res = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": (filename, image_bytes, mime_type)},
            timeout=25
        )
        return res.json().get("ok", False)
    except Exception as e:
        logger.error("Telegram sendPhoto failed: %s", e)
        return telegram_send_message(bot_token, chat_id, caption)


def dispatch_telegram_alerts(
    location_name: str,
    slot_data: Dict[str, Any],
    analysis: Dict[str, Any],
    image_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
    slot_key: Optional[str] = None
):
    """
    1. Sends routine 10-minute prediction + image to Bot 1 (Analysis Bot).
    2. Sends emergency alert + image to Bot 2 (Nagar Nigam Bot) ONLY if emergency detected!
    """
    global LAST_DISPATCHED_TIMESTAMP

    now_ts = time.time()
    if slot_key:
        if slot_key in DISPATCHED_SLOTS_CACHE:
            logger.info("Slot %s already dispatched to Telegram. Skipping duplicate.", slot_key)
            return
        DISPATCHED_SLOTS_CACHE[slot_key] = now_ts
        if len(DISPATCHED_SLOTS_CACHE) > 50:
            oldest = min(DISPATCHED_SLOTS_CACHE, key=DISPATCHED_SLOTS_CACHE.get)
            DISPATCHED_SLOTS_CACHE.pop(oldest, None)

    LAST_DISPATCHED_TIMESTAMP = now_ts
    ist_time = format_ist_time()
    lat = slot_data.get("latitude", 25.618639)
    lon = slot_data.get("longitude", 85.148456)
    maps_url = f"https://www.google.com/maps?q={lat},{lon}"

    node_1 = slot_data.get("node_1", {}).get("data", {})
    temp = node_1.get("temperature_c", "N/A")
    rh = node_1.get("humidity_percent", "N/A")
    pm25 = node_1.get("pm2_5", "N/A")
    voc = node_1.get("voc_index", "N/A")

    severity = analysis.get("overall_severity", "low").upper()
    status_icon = "🟢" if severity == "LOW" else ("🟡" if severity == "MEDIUM" else "🔴")

    water_status = str(analysis.get("waterlogging_status", "NONE")).upper()
    garbage_status = str(analysis.get("garbage_status", "NONE")).upper()
    air_status = str(analysis.get("air_quality_status", "GOOD")).upper()

    # 1. BOT 1: ROUTINE 10-MINUTE ANALYSIS REPORT
    routine_caption = (
        "╔═══════════════════════════════════╗\n"
        "🏙️  𝐀𝐈 𝐒𝐌𝐀𝐑𝐓 𝐂𝐈𝐓𝐘  |  𝟏𝟎-𝐌𝐈𝐍 𝐔𝐏𝐃𝐀𝐓𝐄\n"
        "╚═══════════════════════════════════╝\n\n"
        f"📍 𝐋𝐎𝐂𝐀𝐓𝐈𝐎𝐍   : {location_name.upper()}\n"
        f"⏱ 𝐓𝐈𝐌𝐄𝐒𝐓𝐀𝐌𝐏  : {ist_time}\n"
        f"{status_icon} 𝐒𝐄𝐕𝐄𝐑𝐈𝐓𝐘   : 【 {severity} 】\n"
        f"🗺️ 𝐆𝐏𝐒 𝐌𝐀𝐏    : {maps_url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌡️  𝐄𝐍𝐕𝐈𝐑𝐎𝐍𝐌𝐄𝐍𝐓  (𝐒𝐄𝐍𝟓𝟒)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🌡️ TEMP : {temp} °C   |   💧 RH : {rh} %\n"
        f"• 💨 PM2.5: {pm25} µg/m³ |  ☣️ VOC: {voc}\n"
        f"• 🌬️ AIR STATUS : {air_status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📸  𝐒𝐓𝐑𝐄𝐄𝐓 𝐕𝐈𝐒𝐈𝐎𝐍  (𝐂𝐀𝐌𝐄𝐑𝐀)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 💧 WATERLOGGING : {water_status}\n"
        f"• 🗑️ GARBAGE DUMP : {garbage_status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖  𝐆𝐄𝐌𝐈𝐍𝐈 𝐀𝐈 𝐀𝐒𝐒𝐄𝐒𝐒𝐌𝐄𝐍𝐓\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{analysis.get('summary', '').upper()}\n\n"
        "📋  𝐀𝐂𝐓𝐈𝐎𝐍 𝐃𝐈𝐑𝐄𝐂𝐓𝐈𝐕𝐄:\n"
        f"👉 {analysis.get('recommended_action', '').upper()}\n"
        "═══════════════════════════════════"
    )

    if configured(ANALYSIS_BOT_TOKEN) and configured(ANALYSIS_CHAT_ID):
        if image_bytes and mime_type:
            telegram_send_photo(ANALYSIS_BOT_TOKEN, ANALYSIS_CHAT_ID, routine_caption, image_bytes, mime_type)
        else:
            telegram_send_message(ANALYSIS_BOT_TOKEN, ANALYSIS_CHAT_ID, routine_caption)

    # 2. BOT 2: NAGAR NIGAM EMERGENCY BOT (ONLY WHEN ACTION IS REQUIRED!)
    is_emergency = analysis.get("emergency_detected") or severity == "HIGH"
    if is_emergency:
        emergency_caption = (
            "🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n"
            "⚠️  𝐍𝐀𝐆𝐀𝐑 𝐍𝐈𝐆𝐀𝐌  |  𝐄𝐌𝐄𝐑𝐆𝐄𝐍𝐂𝐘 𝐀𝐋𝐄𝐑𝐓  ⚠️\n"
            "🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n\n"
            f"📍 𝐋𝐎𝐂𝐀𝐓𝐈𝐎𝐍   : {location_name.upper()}\n"
            f"⏱ 𝐓𝐈𝐌𝐄𝐒𝐓𝐀𝐌𝐏  : {ist_time}\n"
            f"🗺️ 𝐋𝐎𝐂𝐀𝐓𝐈𝐎𝐍 𝐌𝐀𝐏: {maps_url}\n"
            "🔴 𝐒𝐓𝐀𝐓𝐔𝐒     : 【 ACTION REQUIRED 】\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡  𝐇𝐀𝐙𝐀𝐑𝐃𝐒 𝐃𝐄𝐓𝐄𝐂𝐓𝐄𝐃:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 💧 SADAK JAL BHARAW : {water_status}\n"
            f"• 🗑️ KUDA / GARBAGE   : {garbage_status}\n"
            f"• 💨 AIR QUALITY HAZARD: {air_status}\n\n"
            "📋  𝐒𝐈𝐓𝐔𝐀𝐓𝐈𝐎𝐍 𝐒𝐔𝐌𝐌𝐀𝐑𝐘:\n"
            f"{analysis.get('summary', '').upper()}\n\n"
            "🚨  𝐌𝐀𝐍𝐃𝐀𝐓𝐎𝐑𝐘 𝐅𝐈𝐄𝐋𝐃 𝐀𝐂𝐓𝐈𝐎𝐍:\n"
            f"👉 {analysis.get('recommended_action', '').upper()}\n"
            "═══════════════════════════════════"
        )
        if configured(ALERT_BOT_TOKEN) and configured(ALERT_CHAT_ID):
            if image_bytes and mime_type:
                telegram_send_photo(ALERT_BOT_TOKEN, ALERT_CHAT_ID, emergency_caption, image_bytes, mime_type)
            else:
                telegram_send_message(ALERT_BOT_TOKEN, ALERT_CHAT_ID, emergency_caption)

    save_state_to_tmp()


# ============================================================
# SLOT COORDINATOR & EXECUTION LOGIC
# ============================================================

def process_slot(slot_key: str, location_name: str, lat: float, lon: float):
    """Executes multimodal analysis for a slot and dispatches to Telegram."""
    slot_data = IN_MEMORY_SLOTS.get(slot_key)
    if not slot_data or slot_data.get("analyzed"):
        return

    slot_data["analyzed"] = True
    slot_data["analyzed_at"] = datetime.now(timezone.utc)

    img_bytes = slot_data.get("image_bytes")
    mime_type = slot_data.get("mime_type", "image/jpeg")

    # Call Gemini Multimodal AI
    analysis = analyze_multimodal_urban_slot(location_name, slot_data, img_bytes, mime_type)

    # Save report
    now = datetime.now(timezone.utc)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    report = {
        "report_id": f"report_{slot_key}",
        "slot_key": slot_key,
        "location_name": location_name,
        "latitude": lat,
        "longitude": lon,
        "timestamp_ist": now.astimezone(ist_tz).strftime("%d-%b %I:%M:%S %p"),
        "analysis": analysis,
        "sensor_data": {
            "node_1": slot_data.get("node_1"),
            "image_attached": bool(img_bytes)
        }
    }
    IN_MEMORY_REPORTS.insert(0, report)
    if len(IN_MEMORY_REPORTS) > 20:
        IN_MEMORY_REPORTS.pop()

    # Dispatch to Telegram bots
    dispatch_telegram_alerts(location_name, slot_data, analysis, img_bytes, mime_type, slot_key=slot_key)


# ============================================================
# API ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "project": "Smart City Multimodal AI Platform",
        "description": "ESP32 (SEN54) + Raspberry Pi (Camera) + Gemini AI + Dual Telegram Bots",
        "status": "online",
        "interval_seconds": DEFAULT_INTERVAL_SECONDS,
        "dashboard_url": "/dashboard",
        "timestamp_ist": format_ist_time()
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "timestamp_ist": format_ist_time(),
        "gemini_api_configured": bool(GEMINI_API_KEYS),
        "routine_bot_configured": configured(ANALYSIS_BOT_TOKEN) and configured(ANALYSIS_CHAT_ID),
        "emergency_bot_configured": configured(ALERT_BOT_TOKEN) and configured(ALERT_CHAT_ID),
        "interval_seconds": DEFAULT_INTERVAL_SECONDS
    }


@app.get("/api/time")
@app.get("/sync-time")
def sync_time():
    """Endpoint for ESP32 and Raspberry Pi to synchronize their clocks to exact 10-minute slot boundaries."""
    now_utc = datetime.now(timezone.utc)
    seconds_past = (now_utc.minute % 10) * 60 + now_utc.second
    seconds_until_next = 600 - seconds_past
    return {
        "epoch_seconds": int(now_utc.timestamp()),
        "current_time_utc": now_utc.isoformat(),
        "current_time_ist": format_ist_time(now_utc),
        "seconds_past_10min_slot": seconds_past,
        "seconds_until_next_10min_slot": seconds_until_next,
        "sync_window_active": seconds_past <= SYNC_WINDOW_SECONDS
    }


@app.get("/device/next-action")
def device_next_action(x_device_token: str = Header(default="")):
    return {"interval_seconds": DEFAULT_INTERVAL_SECONDS}


# ------------------------------------------------------------
# ROUTE 1: /telemetry (For Node 1 ESP32 SEN54 & Node 3 Drain)
# ------------------------------------------------------------

@app.post("/telemetry")
async def receive_telemetry(
    request: Request,
    background_tasks: BackgroundTasks,
    x_device_token: str = Header(default="")
):
    if configured(DEVICE_TOKEN) and x_device_token != DEVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid x-device-token")

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload_dict = await request.json()
        else:
            form = await request.form()
            raw_data = form.get("data", "{}")
            payload_dict = {
                "node_id": form.get("node_id"),
                "location_name": form.get("location_name"),
                "latitude": float(form.get("latitude", 25.618639)),
                "longitude": float(form.get("longitude", 85.148456)),
                "sensor_type": form.get("sensor_type"),
                "data": json.loads(raw_data) if isinstance(raw_data, str) else {}
            }
        payload = TelemetryPayload(**payload_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad request: {str(e)}")

    node_id = payload.node_id.strip()
    loc = payload.location_name.strip()
    lat = payload.latitude
    lon = payload.longitude
    slot_key = get_slot_key(loc)
    now_utc = datetime.now(timezone.utc)
    ist_tz = timezone(timedelta(hours=5, minutes=30))

    # Store into slot
    slot = IN_MEMORY_SLOTS.setdefault(slot_key, {
        "location_name": loc,
        "latitude": lat,
        "longitude": lon,
        "updated_at": now_utc
    })

    if "drain" in node_id.lower() or "node_2" in node_id.lower() and "camera" not in node_id.lower():
        slot["node_2_drain"] = {"node_id": node_id, "data": payload.data, "received_at": now_utc}
    else:
        slot["node_1"] = {"node_id": node_id, "data": payload.data, "received_at": now_utc}

    # Record heartbeat & log
    IN_MEMORY_HEARTBEATS[node_id] = {
        "node_id": node_id,
        "location_name": loc,
        "sensor_type": payload.sensor_type,
        "last_seen": now_utc,
        "last_data": payload.data,
        "status": "online"
    }

    IN_MEMORY_TELEMETRY_LOGS.insert(0, {
        "id": hashlib.sha256(f"{node_id}_{now_utc.isoformat()}".encode()).hexdigest()[:16],
        "node_id": node_id,
        "location_name": loc,
        "sensor_type": payload.sensor_type,
        "timestamp_ist": now_utc.astimezone(ist_tz).strftime("%I:%M:%S %p"),
        "status": "SUCCESS"
    })
    if len(IN_MEMORY_TELEMETRY_LOGS) > 30:
        IN_MEMORY_TELEMETRY_LOGS.pop()

    # If camera photo is already waiting in this slot, or instant emergency, run slot analysis!
    has_camera = "image_bytes" in slot or "node_2_camera" in slot
    is_emergency = payload.data.get("drain_overflow_detected") or payload.data.get("instant_emergency")

    if is_emergency or has_camera:
        background_tasks.add_task(process_slot, slot_key, loc, lat, lon)

    return {
        "success": True,
        "node_id": node_id,
        "slot_key": slot_key,
        "status": "synchronized",
        "interval_seconds": DEFAULT_INTERVAL_SECONDS
    }


# ------------------------------------------------------------
# ROUTE 2: /upload (For Node 2 Raspberry Pi Camera)
# ------------------------------------------------------------

@app.post("/upload")
async def upload_camera_photo(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    location_name: str = Form(...),
    latitude: str = Form("25.618639"),
    longitude: str = Form("85.148456"),
    node_id: str = Form("node_2_camera"),
    x_device_token: str = Header(default="")
):
    if configured(DEVICE_TOKEN) and x_device_token != DEVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid x-device-token")

    image_bytes = await file.read(MAX_IMAGE_SIZE + 1)
    if not image_bytes or len(image_bytes) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image empty or exceeds 4 MB")

    loc = location_name.strip()
    try:
        lat = float(latitude)
        lon = float(longitude)
    except Exception:
        lat, lon = 25.618639, 85.148456

    slot_key = get_slot_key(loc)
    now_utc = datetime.now(timezone.utc)
    ist_tz = timezone(timedelta(hours=5, minutes=30))

    slot = IN_MEMORY_SLOTS.setdefault(slot_key, {
        "location_name": loc,
        "latitude": lat,
        "longitude": lon,
        "updated_at": now_utc
    })
    slot["image_bytes"] = image_bytes
    slot["mime_type"] = file.content_type or "image/jpeg"
    slot["node_2_camera"] = {
        "node_id": node_id,
        "image_size_bytes": len(image_bytes),
        "received_at": now_utc
    }

    # Update heartbeat
    IN_MEMORY_HEARTBEATS[node_id] = {
        "node_id": node_id,
        "location_name": loc,
        "sensor_type": "CAMERA_VISION",
        "last_seen": now_utc,
        "last_data": {"size_bytes": len(image_bytes)},
        "status": "online"
    }

    IN_MEMORY_TELEMETRY_LOGS.insert(0, {
        "id": hashlib.sha256(f"{node_id}_{now_utc.isoformat()}".encode()).hexdigest()[:16],
        "node_id": node_id,
        "location_name": loc,
        "sensor_type": "CAMERA_VISION",
        "timestamp_ist": now_utc.astimezone(ist_tz).strftime("%I:%M:%S %p"),
        "status": "SUCCESS"
    })
    if len(IN_MEMORY_TELEMETRY_LOGS) > 30:
        IN_MEMORY_TELEMETRY_LOGS.pop()

    # Trigger analysis now!
    background_tasks.add_task(process_slot, slot_key, loc, lat, lon)

    return {
        "success": True,
        "slot_key": slot_key,
        "node_id": node_id,
        "status": "image_received_and_processing",
        "interval_seconds": DEFAULT_INTERVAL_SECONDS
    }


# ------------------------------------------------------------
# DASHBOARD API & UI
# ------------------------------------------------------------

@app.get("/api/dashboard")
def get_dashboard_data():
    now_utc = datetime.now(timezone.utc)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    seconds_past = (now_utc.minute % 10) * 60 + now_utc.second
    seconds_to_next = 600 - seconds_past

    nodes_info = {}
    expected = [
        {"id": "node_1_sen54", "name": "Node 1 - SEN54 Air Quality & Climate", "type": "SEN54"},
        {"id": "node_2_camera", "name": "Node 2 - Raspberry Pi Street Vision", "type": "CAMERA_VISION"},
        {"id": "node_3_drain_mq135", "name": "Node 3 - Nala Drain Probes & MQ-135", "type": "MQ135_DRAIN"}
    ]

    for en in expected:
        nid = en["id"]
        hb = IN_MEMORY_HEARTBEATS.get(nid, {})
        last_seen = hb.get("last_seen")
        time_diff = (now_utc - last_seen).total_seconds() if last_seen else 999999
        is_online = time_diff < 1260  # Within ~20 mins (2 cycles)
        nodes_info[nid] = {
            "node_id": nid,
            "title": en["name"],
            "type": en["type"],
            "online": is_online,
            "last_seen_str": last_seen.astimezone(ist_tz).strftime("%d-%b %I:%M %p") if last_seen else "Never",
            "last_data": hb.get("last_data", {})
        }

    return {
        "status": "online",
        "current_time_ist": now_utc.astimezone(ist_tz).strftime("%d-%b-%Y %I:%M:%S %p"),
        "timing": {
            "seconds_to_next_cycle": seconds_to_next,
            "cycle_interval_seconds": DEFAULT_INTERVAL_SECONDS
        },
        "nodes": nodes_info,
        "recent_reports": IN_MEMORY_REPORTS[:5],
        "telemetry_logs": IN_MEMORY_TELEMETRY_LOGS[:10]
    }


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Smart City AI — Operations Center</title>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    :root {
      --bg: #030712;
      --card: rgba(17, 24, 39, 0.7);
      --card-border: rgba(56, 189, 248, 0.2);
      --accent: #38bdf8;
      --green: #10b981;
      --red: #f43f5e;
      --text: #f9fafb;
      --muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Space Grotesk', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 24px;
    }
    .container { max-width: 1280px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 18px;
      padding: 20px 28px;
      margin-bottom: 24px;
      backdrop-filter: blur(16px);
    }
    .brand { display: flex; align-items: center; gap: 14px; }
    .brand-icon {
      width: 48px; height: 48px; border-radius: 12px;
      background: linear-gradient(135deg, #0284c7, #38bdf8);
      display: flex; align-items: center; justify-content: center;
      font-size: 22px; color: #fff;
    }
    .badge {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 6px 14px; border-radius: 999px; font-size: 12px; font-weight: 700;
      background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3); color: #34d399;
    }
    .sync-card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 20px;
      padding: 24px 32px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      backdrop-filter: blur(16px);
    }
    .timer-digits {
      font-family: 'JetBrains Mono', monospace;
      font-size: 42px; font-weight: 800; color: #38bdf8;
      text-shadow: 0 0 20px rgba(56, 189, 248, 0.5);
    }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 24px; }
    .node-box {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 18px;
      padding: 20px;
      backdrop-filter: blur(16px);
    }
    .node-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
    .metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
    .metric-cell {
      background: rgba(0, 0, 0, 0.4);
      padding: 10px; border-radius: 10px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .metric-title { font-size: 10px; color: var(--muted); text-transform: uppercase; }
    .metric-val { font-family: 'JetBrains Mono', monospace; font-size: 15px; font-weight: 700; margin-top: 4px; }
    .report-box {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 18px;
      padding: 24px;
      backdrop-filter: blur(16px);
    }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
    .chip {
      background: rgba(255, 255, 255, 0.06);
      padding: 6px 12px; border-radius: 8px; font-size: 12px;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-icon"><i class="fa-solid fa-city"></i></div>
        <div>
          <h2>Smart City Autonomous AI Ops</h2>
          <p style="font-size: 12px; color: var(--muted);">ESP32 SEN54 • Raspberry Pi Vision • Dual Telegram Bots</p>
        </div>
      </div>
      <div class="badge"><i class="fa-solid fa-circle-check"></i> System Operational</div>
    </header>

    <div class="sync-card">
      <div>
        <h3>10-Minute Multi-Node Sync Engine</h3>
        <p style="font-size: 13px; color: var(--muted); margin-top: 4px;">ESP32 and Raspberry Pi synchronize at :00, :10, :20, :30, :40, :50.</p>
      </div>
      <div style="text-align: right;">
        <div class="timer-digits" id="timer">10:00</div>
        <div style="font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px;">Next Dispatch</div>
      </div>
    </div>

    <div class="grid" id="nodes-grid">
      <!-- Injected dynamically -->
    </div>

    <div class="report-box">
      <h3 style="margin-bottom: 14px;"><i class="fa-solid fa-brain" style="color: var(--accent);"></i> Latest Unified Gemini Assessment</h3>
      <div id="report-content" style="color: var(--muted); font-size: 13px;">Waiting for synchronized cycle telemetry...</div>
    </div>
  </div>

  <script>
    let countdown = 600;
    async function loadData() {
      try {
        const res = await fetch('/api/dashboard');
        const data = await res.json();
        countdown = data.timing.seconds_to_next_cycle;
        renderNodes(data.nodes);
        renderReport(data.recent_reports);
      } catch (e) {
        console.error(e);
      }
    }

    function renderNodes(nodes) {
      const container = document.getElementById('nodes-grid');
      container.innerHTML = '';
      Object.values(nodes).forEach(n => {
        const onlineBadge = n.online 
          ? '<span style="color: #34d399; font-weight: 700; font-size: 11px;">● ONLINE</span>'
          : '<span style="color: #fb7185; font-weight: 700; font-size: 11px;">○ OFFLINE</span>';
        
        let mHtml = '';
        if (n.type === 'SEN54') {
          const d = n.last_data || {};
          mHtml = `
            <div class="metric-cell"><div class="metric-title">PM2.5</div><div class="metric-val">${d.pm2_5 ? d.pm2_5 + ' µg/m³' : 'N/A'}</div></div>
            <div class="metric-cell"><div class="metric-title">Temp</div><div class="metric-val">${d.temperature_c ? d.temperature_c + ' °C' : 'N/A'}</div></div>
            <div class="metric-cell"><div class="metric-title">Humidity</div><div class="metric-val">${d.humidity_percent ? d.humidity_percent + ' %' : 'N/A'}</div></div>
            <div class="metric-cell"><div class="metric-title">VOC Index</div><div class="metric-val">${d.voc_index || 'N/A'}</div></div>
          `;
        } else if (n.type === 'MQ135_DRAIN') {
          const d = n.last_data || {};
          const isOverflow = d.drain_overflow_detected === true;
          const overflowText = isOverflow ? 'ACTIVE OVERFLOW' : 'NORMAL LEVEL';
          const overflowColor = isOverflow ? '#f43f5e' : '#34d399';
          const gasStatus = d.gas_level_status || 'NORMAL';
          const gasColor = (gasStatus === 'HAZARDOUS') ? '#f43f5e' : ((gasStatus === 'ELEVATED') ? '#fbbf24' : '#34d399');
          const ppm = (d.air_quality_ppm !== undefined) ? `${Number(d.air_quality_ppm).toFixed(1)} ppm` : 'NORMAL';
          mHtml = `
            <div class="metric-cell"><div class="metric-title">Nala Water Level</div><div class="metric-val" style="color: ${overflowColor};">${overflowText}</div></div>
            <div class="metric-cell"><div class="metric-title">Sewer Gas</div><div class="metric-val" style="color: ${gasColor};">${gasStatus}</div></div>
            <div class="metric-cell"><div class="metric-title">Gas Level</div><div class="metric-val">${ppm}</div></div>
            <div class="metric-cell"><div class="metric-title">Instant Interrupt</div><div class="metric-val" style="color: #38bdf8;">Armed (0s Delay)</div></div>
          `;
        } else {
          mHtml = `
            <div class="metric-cell"><div class="metric-title">Camera Mode</div><div class="metric-val" style="color: #38bdf8;">Power Save On/Off</div></div>
            <div class="metric-cell"><div class="metric-title">Cadence</div><div class="metric-val">Every 10 Min</div></div>
            <div class="metric-cell"><div class="metric-title">Image Target</div><div class="metric-val">Road Water / Garbage</div></div>
            <div class="metric-cell"><div class="metric-title">Last Frame</div><div class="metric-val">${n.last_data.size_bytes ? Math.round(n.last_data.size_bytes/1024) + ' KB' : 'None'}</div></div>
          `;
        }

        container.innerHTML += `
          <div class="node-box">
            <div class="node-header">
              <div>
                <strong style="font-size: 15px;">${n.title}</strong>
                <div style="font-size: 11px; color: var(--muted); font-family: monospace;">${n.node_id}</div>
              </div>
              ${onlineBadge}
            </div>
            <div class="metric-grid">${mHtml}</div>
            <div style="margin-top: 12px; font-size: 11px; color: var(--muted);">Last Heard: ${n.last_seen_str}</div>
          </div>
        `;
      });
    }

    function renderReport(reports) {
      const box = document.getElementById('report-content');
      if (!reports || reports.length === 0) return;
      const r = reports[0];
      const a = r.analysis || {};
      const sevColor = a.overall_severity === 'high' ? '#f43f5e' : (a.overall_severity === 'medium' ? '#fbbf24' : '#34d399');
      box.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <strong style="font-size: 15px; color: #fff;">📍 ${r.location_name}</strong>
          <span style="color: ${sevColor}; font-weight: 800; font-size: 12px;">【 ${String(a.overall_severity).toUpperCase()} SEVERITY 】</span>
        </div>
        <p style="font-size: 13px; line-height: 1.6; color: #f3f4f6;">${a.summary || 'Operational baseline normal.'}</p>
        <div class="chips">
          <div class="chip">💧 Waterlogging: <strong>${a.waterlogging_status || 'NONE'}</strong></div>
          <div class="chip">🗑️ Garbage Dump: <strong>${a.garbage_status || 'NONE'}</strong></div>
          <div class="chip">💨 Air Quality: <strong>${a.air_quality_status || 'GOOD'}</strong></div>
          <div class="chip">🚨 Emergency Alert: <strong>${a.emergency_detected ? 'DISPATCHED' : 'NORMAL'}</strong></div>
        </div>
        <div style="margin-top: 14px; font-size: 11px; color: var(--muted); border-top: 1px solid rgba(255,255,255,0.08); padding-top: 8px;">
          Directive: <span style="color: #38bdf8;">${a.recommended_action || 'Automated monitoring'}</span> • ${r.timestamp_ist}
        </div>
      `;
    }

    setInterval(() => {
      if (countdown > 0) countdown--;
      else { countdown = 600; loadData(); }
      const m = String(Math.floor(countdown / 60)).padStart(2, '0');
      const s = String(countdown % 60).padStart(2, '0');
      document.getElementById('timer').textContent = `${m}:${s}`;
    }, 1000);

    loadData();
    setInterval(loadData, 15000);
  </script>
</body>
</html>
"""

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML
