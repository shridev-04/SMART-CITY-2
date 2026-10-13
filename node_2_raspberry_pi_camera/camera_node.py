"""
============================================================================
 SMART CITY AI — NODE 2 (RASPBERRY PI STREET VISION CAMERA)
 Target Hardware: Raspberry Pi 3B / 4B / 5 / Zero 2W + Pi Camera / USB Webcam

 Operation:
   1. Opens camera hardware.
   2. Warms up auto-exposure (1.5 seconds).
   3. Captures high-resolution street photo.
   4. IMMEDIATELY CLOSES and releases camera hardware (Power save mode).
   5. Uploads photo to FastAPI backend (/upload).
   6. Sleeps until the next synchronized 10-minute clock mark (:00, :10, :20...).
============================================================================
"""

import os
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
import requests

# ============================================================
# CONFIGURATION
# ============================================================

# Replace with your Vercel deployment URL
BACKEND_URL = os.environ.get("BACKEND_URL", "https://smart-city-kappa-three.vercel.app")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "SHRIDEV")
NODE_ID = "node_2_camera"

LOCATION_NAME = "GANDHI_MAIDAN"
LATITUDE = "25.618639"
LONGITUDE = "85.148456"

DEFAULT_INTERVAL_SECONDS = 600  # 10 minutes
MAX_RETRY_SECONDS = 30         # Upload retry timeout
MAX_IMAGE_SIZE = 4 * 1024 * 1024  # 4 MB max

IMAGE_DIR = Path.home() / "smart_city_frames"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("camera_node")


# ============================================================
# 10-MINUTE SYNCHRONIZATION CLOCK CALCULATOR
# ============================================================

def get_seconds_until_next_10min_boundary() -> int:
    """
    Calculates exact sleep seconds so the camera captures in synchronization
    with Node 1 at :00, :10, :20, :30, :40, :50 marks.
    """
    now = datetime.now()
    seconds_past_slot = (now.minute % 10) * 60 + now.second
    remaining = 600 - seconds_past_slot
    if remaining < 8:
        remaining += 600  # Jump to next slot if within 8s to prevent double capture
    return remaining


# ============================================================
# CAMERA HARDWARE INTERFACE: OPEN -> CAPTURE -> IMMEDIATE CLOSE
# ============================================================

def capture_photo_and_close_camera() -> Path:
    """
    Opens camera, allows auto-exposure to stabilize, captures a photo,
    and IMMEDIATELY CLOSES/RELEASES the camera hardware so it does not stay open.
    Supports Picamera2 (official Pi Camera) with OpenCV fallback (USB webcam).
    """
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = IMAGE_DIR / f"frame_{timestamp_str}.jpg"

    captured = False

    # Attempt 1: Official Raspberry Pi Camera Module via Picamera2
    try:
        from picamera2 import Picamera2
        logger.info("[Camera] Initializing Picamera2 hardware interface...")
        camera = Picamera2()
        try:
            camera.configure(camera.create_still_configuration())
            camera.start()
            logger.info("[Camera] Hardware active. Stabilizing auto-exposure (1.5s)...")
            time.sleep(1.5)
            camera.capture_file(str(target_path))
            captured = True
            logger.info("[Camera] Frame captured successfully via Picamera2.")
        finally:
            logger.info("[Camera] Powering OFF camera hardware...")
            try:
                camera.stop()
                camera.close()
            except Exception as e:
                logger.debug("Camera close notice: %s", e)
            logger.info("[Camera] Hardware released and closed.")
    except ImportError:
        logger.info("[Camera] Picamera2 not installed, checking OpenCV USB fallback...")
    except Exception as e:
        logger.warning("[Camera] Picamera2 error: %s. Trying OpenCV fallback...", e)

    # Attempt 2: USB Webcam via OpenCV
    if not captured:
        try:
            import cv2
            logger.info("[Camera] Opening video capture device 0 via OpenCV...")
            cap = cv2.VideoCapture(0)
            try:
                if not cap.isOpened():
                    raise RuntimeError("Could not open video device 0")
                # Warm up sensor
                for _ in range(5):
                    cap.read()
                    time.sleep(0.1)
                ret, frame = cap.read()
                if ret and frame is not None:
                    cv2.imwrite(str(target_path), frame)
                    captured = True
                    logger.info("[Camera] Frame captured successfully via OpenCV.")
                else:
                    raise RuntimeError("Failed to capture valid frame from webcam")
            finally:
                logger.info("[Camera] Releasing OpenCV video device...")
                cap.release()
                logger.info("[Camera] Video device closed.")
        except Exception as e:
            logger.error("[Camera] OpenCV capture failed: %s", e)

    if not captured or not target_path.exists() or target_path.stat().st_size == 0:
        raise RuntimeError("Camera capture failed across all drivers.")

    if target_path.stat().st_size > MAX_IMAGE_SIZE:
        target_path.unlink(missing_ok=True)
        raise RuntimeError("Captured image exceeds 4 MB maximum limit.")

    return target_path


# ============================================================
# HTTP MULTIPART UPLOAD ENGINE
# ============================================================

def upload_photo(photo_path: Path) -> dict:
    url = f"{BACKEND_URL.rstrip('/')}/upload"
    headers = {"x-device-token": DEVICE_TOKEN}
    data = {
        "node_id": NODE_ID,
        "location_name": LOCATION_NAME,
        "latitude": LATITUDE,
        "longitude": LONGITUDE
    }

    with open(photo_path, "rb") as f:
        files = {"file": ("street_vision.jpg", f, "image/jpeg")}
        logger.info("[HTTP] Uploading frame to %s...", url)
        response = requests.post(url, headers=headers, data=data, files=files, timeout=45)

    response.raise_for_status()
    result = response.json()

    if result.get("success") is True:
        logger.info("[HTTP] Upload successful! Slot Key: %s", result.get("slot_key"))
        # Delete local copy after successful upload to conserve SD card storage
        photo_path.unlink(missing_ok=True)
        return result

    raise RuntimeError(f"Backend rejected upload: {result}")


def upload_with_retry(photo_path: Path, max_retry_seconds: int = MAX_RETRY_SECONDS) -> bool:
    start_time = time.time()
    attempt = 1

    while (time.time() - start_time) < max_retry_seconds:
        try:
            upload_photo(photo_path)
            return True
        except Exception as e:
            elapsed = int(time.time() - start_time)
            remaining = max(0, max_retry_seconds - elapsed)
            logger.warning("[HTTP] Attempt #%d failed: %s. %ds remaining in retry window.", attempt, e, remaining)
            if remaining <= 2:
                break
            time.sleep(min(3, remaining))
            attempt += 1

    logger.error("[HTTP] Upload failed after %ds of continuous retries.", max_retry_seconds)
    photo_path.unlink(missing_ok=True)
    return False


# ============================================================
# MAIN EXECUTION ROUTINE
# ============================================================

def main():
    logger.info("==========================================")
    logger.info(" Smart City AI — Node 2 (Pi Camera)")
    logger.info(" Location : %s", LOCATION_NAME)
    logger.info(" Backend  : %s", BACKEND_URL)
    logger.info(" Mode     : Open -> Capture -> Close (10m)")
    logger.info("==========================================")

    # 1. IMMEDIATE STARTUP CAPTURE (Bootstrap verification)
    logger.info("[Startup] Capturing initial bootstrap street photo...")
    try:
        photo = capture_photo_and_close_camera()
        upload_with_retry(photo)
    except Exception as e:
        logger.warning("[Startup] Initial bootstrap capture encountered error: %s", e)

    # 2. SYNCHRONIZED 10-MINUTE LOOP
    while True:
        sleep_seconds = get_seconds_until_next_10min_boundary()
        logger.info("[Scheduler] Camera hardware is OFF/SLEEPING. Next capture in %d seconds (~%d mins).",
                    sleep_seconds, sleep_seconds // 60)
        time.sleep(sleep_seconds)

        logger.info("[Scheduler] 10-minute boundary reached! Waking camera hardware...")
        try:
            photo = capture_photo_and_close_camera()
            upload_with_retry(photo)
            logger.info("[Scheduler] Cycle complete. Camera closed.")
        except Exception as e:
            logger.exception("[Scheduler] Capture or upload failed during cycle: %s", e)


if __name__ == "__main__":
    main()
