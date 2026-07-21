import os
import sys
import time
import json
import hashlib
import webbrowser
import requests
import schedule
import pyautogui
import subprocess
import threading
import uuid
import urllib.parse
import urllib.request
import zipfile
import shutil
import tempfile
import win32api
import win32con
import win32gui
import win32process
try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None
from datetime import datetime, timedelta
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.panel import Panel

console = Console()
APP_VERSION = "2.0.0"
last_action_time = 0  
last_failed_job = {}
join_automation_lock = threading.Lock()

DEFAULT_DATA = {
    "webhook_url": "",
    "join_delay": 15,
    "screenshot_delay": 300,
    "remote_cooldown": 60,
    "enable_recording": False,
    "recording_duration": 90,
    "recordings_dir": "",
    "remote_topic": "",
    "lectures": []
}

JOIN_SEARCH_TIMEOUT = 35
JOIN_SEARCH_INTERVAL = 2
FFMPEG_DOWNLOAD_LIMIT = 400 * 1024 * 1024
FFMPEG_ARCHIVE_NAME = "ffmpeg-master-latest-win64-gpl.zip"

# ==========================================
# 1. PATHING & PID MANAGEMENT
# ==========================================

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def get_data_dir():
    if not getattr(sys, 'frozen', False):
        return get_app_dir()
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        return get_app_dir()
    data_dir = os.path.join(local_app_data, "OnlineCheese")
    try:
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    except OSError:
        return get_app_dir()


APP_DIR = get_app_dir()
DATA_DIR = get_data_dir()
JSON_FILE = os.path.join(DATA_DIR, "classes.json")
JOIN_BTN_IMAGE = get_resource_path("join_now.png")
PID_FILE = os.path.join(DATA_DIR, "daemon.pid")
LOG_FILE = os.path.join(DATA_DIR, "daemon.log")
RECORDINGS_DIR = os.path.join(DATA_DIR, "Recordings")
RECORDING_STATE_FILE = os.path.join(DATA_DIR, "recording_state.json")
RECORDING_STOP_FILE = os.path.join(DATA_DIR, "recording.stop")
RECORDING_LOG_FILE = os.path.join(DATA_DIR, "recording_ffmpeg.log")

LOCAL_RECORDINGS_FALLBACK = os.path.join(DATA_DIR, "Recordings")


def default_recordings_dir():
    for drive_letter in ("E", "G"):
        drive_root = f"{drive_letter}:\\"
        if os.path.isdir(drive_root):
            return os.path.join(drive_root, "Online Cheese Recordings")
    return LOCAL_RECORDINGS_FALLBACK


def activate_recordings_dir(requested_path):
    global RECORDINGS_DIR
    requested_path = os.path.expandvars(os.path.expanduser(str(requested_path or "").strip()))
    if not requested_path:
        requested_path = default_recordings_dir()
    if not os.path.isabs(requested_path):
        requested_path = os.path.abspath(os.path.join(DATA_DIR, requested_path))
    try:
        os.makedirs(requested_path, exist_ok=True)
        RECORDINGS_DIR = requested_path
    except OSError as e:
        os.makedirs(LOCAL_RECORDINGS_FALLBACK, exist_ok=True)
        RECORDINGS_DIR = LOCAL_RECORDINGS_FALLBACK
        print(f"Recording drive unavailable ({e}). Using temporary fallback: {RECORDINGS_DIR}")
    return RECORDINGS_DIR


RECORDINGS_DIR = activate_recordings_dir(default_recordings_dir())


def legacy_config_candidates():
    candidate_paths = []

    def add_candidate(path):
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized != os.path.normcase(os.path.abspath(JSON_FILE)) and normalized not in candidate_paths:
            candidate_paths.append(normalized)

    common_roots = {
        APP_DIR,
        os.getcwd(),
        os.path.dirname(APP_DIR),
        os.path.join(APP_DIR, "dist"),
        os.path.join(os.path.dirname(APP_DIR), "dist"),
    }
    user_profile = os.getenv("USERPROFILE")
    if user_profile:
        for folder_name in ("Online Cheese", "online cheeze", "Online-Cheese"):
            common_roots.add(os.path.join(user_profile, folder_name))
            common_roots.add(os.path.join(user_profile, folder_name, "dist"))
        for base_name in ("Desktop", "Downloads", "Documents"):
            base_dir = os.path.join(user_profile, base_name)
            common_roots.add(base_dir)
            for folder_name in ("Online Cheese", "online cheeze", "Online-Cheese"):
                common_roots.add(os.path.join(base_dir, folder_name))
                common_roots.add(os.path.join(base_dir, folder_name, "dist"))

    for root in common_roots:
        add_candidate(os.path.join(root, "classes.json"))
    return candidate_paths


def read_legacy_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        if not isinstance(value, dict) or not isinstance(value.get("lectures", []), list):
            return None
        return value
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def lecture_identity(lecture):
    if not isinstance(lecture, dict):
        return None
    lecture_days = lecture.get("days", [])
    if not isinstance(lecture_days, list):
        lecture_days = []
    return (
        str(lecture.get("name", "")).strip().casefold(),
        str(lecture.get("url", "")).strip(),
        str(lecture.get("time", "")).strip(),
        tuple(sorted(str(day).strip().lower() for day in lecture_days if day)),
    )


def migrate_legacy_configuration():
    if DATA_DIR == APP_DIR:
        return

    legacy_options = []
    for candidate in legacy_config_candidates():
        value = read_legacy_config(candidate)
        if value is not None:
            score = (
                len(value.get("lectures", [])),
                bool(value.get("webhook_url")),
                os.path.getmtime(candidate),
            )
            legacy_options.append((score, candidate, value))
    if not legacy_options:
        return

    _, source_path, legacy_data = max(legacy_options, key=lambda item: item[0])
    current_data = read_legacy_config(JSON_FILE)
    migration_needed = current_data is None

    if current_data is None:
        migrated_data = legacy_data
        migration_needed = True
    else:
        migrated_data = dict(current_data)
        migrated_data["lectures"] = list(current_data.get("lectures", []))
        known_lectures = {lecture_identity(item) for item in migrated_data["lectures"]}
        used_ids = {
            str(item.get("id"))
            for item in migrated_data["lectures"]
            if isinstance(item, dict) and item.get("id") is not None
        }
        numeric_ids = [int(value) for value in used_ids if value.isdigit()]
        next_id = max(numeric_ids, default=0) + 1
        for lecture in legacy_data.get("lectures", []):
            identity = lecture_identity(lecture)
            if identity and identity not in known_lectures:
                imported_lecture = dict(lecture)
                imported_id = str(imported_lecture.get("id", ""))
                if not imported_id or imported_id in used_ids:
                    while str(next_id) in used_ids:
                        next_id += 1
                    imported_id = str(next_id)
                    imported_lecture["id"] = imported_id
                    next_id += 1
                used_ids.add(imported_id)
                migrated_data["lectures"].append(imported_lecture)
                known_lectures.add(identity)
                migration_needed = True
        fresh_target = (
            not current_data.get("lectures")
            and not current_data.get("webhook_url")
            and not current_data.get("enable_recording")
            and all(
                current_data.get(key, DEFAULT_DATA[key]) == DEFAULT_DATA[key]
                for key in ("join_delay", "screenshot_delay", "remote_cooldown", "recording_duration")
            )
        )
        migratable_settings = (
            "webhook_url",
            "join_delay",
            "screenshot_delay",
            "remote_cooldown",
            "enable_recording",
            "recording_duration",
            "remote_topic",
        )
        for key in migratable_settings:
            legacy_value = legacy_data.get(key)
            current_value = current_data.get(key)
            default_value = DEFAULT_DATA.get(key)
            should_import = (
                key not in current_data
                or current_value in (None, "")
                or (current_value == default_value and legacy_value != default_value)
                or (fresh_target and key == "remote_topic")
            )
            if should_import and legacy_value not in (None, "") and current_value != legacy_value:
                migrated_data[key] = legacy_value
                migration_needed = True

    if not migration_needed:
        return

    try:
        os.makedirs(os.path.dirname(JSON_FILE), exist_ok=True)
        if os.path.exists(JSON_FILE):
            backup_path = f"{JSON_FILE}.pre-migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(JSON_FILE, backup_path)
        temp_path = f"{JSON_FILE}.{os.getpid()}.migration.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(migrated_data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, JSON_FILE)
        print(f"Imported legacy lecture data from: {source_path}")
    except OSError as migration_error:
        print(f"Could not migrate the legacy configuration: {migration_error}")


migrate_legacy_configuration()

# ==========================================
# 2. DATA MANAGEMENT & UTILS
# ==========================================

def load_data():
    if not os.path.exists(JSON_FILE):
        default_data = dict(DEFAULT_DATA)
        default_data["lectures"] = []
        default_data["remote_topic"] = f"cheese_cmd_{uuid.uuid4().hex}"
        default_data["recordings_dir"] = default_recordings_dir()
        activate_recordings_dir(default_data["recordings_dir"])
        save_data(default_data)
        return default_data

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("The configuration root must be a JSON object.")
    except (OSError, ValueError, json.JSONDecodeError) as e:
        backup_path = f"{JSON_FILE}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copy2(JSON_FILE, backup_path)
        except OSError:
            backup_path = "unavailable"
        print(f"Configuration could not be read ({e}). A backup was saved to {backup_path}.")
        data = dict(DEFAULT_DATA)
        data["lectures"] = []
        data["remote_topic"] = f"cheese_cmd_{uuid.uuid4().hex}"
        data["recordings_dir"] = default_recordings_dir()
        activate_recordings_dir(data["recordings_dir"])
        save_data(data)
        return data
        
    updated = False
    numeric_settings = {
        "join_delay": (15, 1, 180),
        "screenshot_delay": (300, 0, 86400),
        "remote_cooldown": (60, 0, 3600),
        "recording_duration": (90, 1, 720),
    }
    for key, (default, minimum, maximum) in numeric_settings.items():
        try:
            clean_value = max(minimum, min(maximum, int(data.get(key, default))))
        except (TypeError, ValueError):
            clean_value = default
        if data.get(key) != clean_value:
            data[key] = clean_value
            updated = True

    if not isinstance(data.get("enable_recording"), bool):
        data["enable_recording"] = bool(data.get("enable_recording", False))
        updated = True
    if not isinstance(data.get("webhook_url"), str):
        data["webhook_url"] = ""
        updated = True
    if not isinstance(data.get("lectures"), list):
        data["lectures"] = []
        updated = True
    if not isinstance(data.get("remote_topic"), str) or not data.get("remote_topic"):
        data["remote_topic"] = f"cheese_cmd_{uuid.uuid4().hex}"
        updated = True
    if not isinstance(data.get("recordings_dir"), str) or not data.get("recordings_dir", "").strip():
        data["recordings_dir"] = default_recordings_dir()
        updated = True
    activate_recordings_dir(data["recordings_dir"])
        
    for lec in data.get("lectures", []):
        if not isinstance(lec, dict):
            continue
        time_val = lec.get("time", "")
        try:
            clean_time = datetime.strptime(time_val, "%H:%M").strftime("%H:%M")
            if clean_time != time_val:
                lec["time"] = clean_time
                updated = True
        except ValueError:
            try:
                clean_time = datetime.strptime(time_val, "%I:%M %p").strftime("%H:%M")
                lec["time"] = clean_time
                updated = True
            except ValueError:
                pass 
                
    if updated:
        save_data(data)
    return data

def save_data(data):
    temp_path = f"{JSON_FILE}.{os.getpid()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, JSON_FILE)

def clean_teams_link(url):
    url = url.strip()
    if "dl/launcher" in url and "url=" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if 'url' in qs:
            decoded = qs['url'][0]
            decoded = decoded.replace("/_#/", "/") 
            return f"msteams://teams.microsoft.com{decoded}"
            
    if url.startswith("https://"):
        return url.replace("https://", "msteams://", 1)
        
    if not url.startswith("msteams://"):
        return f"msteams://{url.lstrip('/')}"
        
    return url

# ==========================================
# 3. AUDIO RECORDING ENGINE
# ==========================================

def hidden_process_kwargs():
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = win32con.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": 0x08000000}


def process_exists(pid):
    try:
        handle = win32api.OpenProcess(
            getattr(win32con, "PROCESS_QUERY_LIMITED_INFORMATION", 0x1000),
            False,
            int(pid),
        )
        handle.Close()
        return True
    except Exception:
        return False


def read_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def write_json_file(path, value):
    temp_path = f"{path}.{os.getpid()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)


def validate_ffmpeg(ffmpeg_path):
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return False
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            **hidden_process_kwargs(),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def bootstrap_ffmpeg():
    candidates = [
        get_resource_path("ffmpeg.exe"),
        os.path.join(APP_DIR, "ffmpeg.exe"),
        os.path.join(DATA_DIR, "ffmpeg.exe"),
        shutil.which("ffmpeg"),
    ]
    for candidate in dict.fromkeys(path for path in candidates if path):
        if validate_ffmpeg(candidate):
            return candidate

    print("Downloading audio engine...")
    ffmpeg_path = os.path.join(DATA_DIR, "ffmpeg.exe")
    zip_path = None
    download_path = f"{ffmpeg_path}.download"
    try:
        release_base = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
        url = f"{release_base}/{FFMPEG_ARCHIVE_NAME}"
        checksum_url = f"{release_base}/checksums.sha256"
        checksum_request = urllib.request.Request(checksum_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(checksum_request, timeout=30) as checksum_response:
            checksum_text = checksum_response.read(64 * 1024).decode("utf-8", errors="strict")
        expected_hash = None
        for line in checksum_text.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[1].lstrip("*").endswith(FFMPEG_ARCHIVE_NAME):
                expected_hash = parts[0].lower()
                break
        if not expected_hash or len(expected_hash) != 64:
            raise RuntimeError("Could not verify the published FFmpeg checksum.")

        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp_zip:
            zip_path = temp_zip.name
            with urllib.request.urlopen(req, timeout=30) as response:
                declared_size = int(response.headers.get("Content-Length", "0") or 0)
                if declared_size > FFMPEG_DOWNLOAD_LIMIT:
                    raise RuntimeError("The audio engine download is unexpectedly large.")
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > FFMPEG_DOWNLOAD_LIMIT:
                        raise RuntimeError("The audio engine download exceeded the safety limit.")
                    temp_zip.write(chunk)

        archive_hash = hashlib.sha256()
        with open(zip_path, "rb") as downloaded_archive:
            while True:
                chunk = downloaded_archive.read(1024 * 1024)
                if not chunk:
                    break
                archive_hash.update(chunk)
        if archive_hash.hexdigest().lower() != expected_hash:
            raise RuntimeError("The FFmpeg download did not match its published SHA-256 checksum.")

        with zipfile.ZipFile(zip_path) as zip_ref:
            member = next(
                (item for item in zip_ref.infolist() if os.path.basename(item.filename).lower() == "ffmpeg.exe"),
                None,
            )
            if member is None:
                raise RuntimeError("The downloaded archive did not contain ffmpeg.exe.")
            with zip_ref.open(member) as source, open(download_path, "wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)

        if not validate_ffmpeg(download_path):
            raise RuntimeError("The downloaded audio engine failed its startup check.")
        os.replace(download_path, ffmpeg_path)
        print("Audio engine downloaded successfully.")
        return ffmpeg_path
    except Exception as e:
        print(f"Failed to download audio engine: {e}")
        return None
    finally:
        for temporary_path in (zip_path, download_path):
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass


def ffmpeg_log_tail(max_lines=12):
    try:
        with open(RECORDING_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:]).strip()
    except OSError:
        return ""


class RecordingManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None

    def _active_external_state(self):
        state = read_json_file(RECORDING_STATE_FILE)
        owner_pid = state.get("owner_pid")
        if owner_pid and process_exists(owner_pid):
            return state
        if state and os.path.exists(RECORDING_STATE_FILE):
            try:
                os.remove(RECORDING_STATE_FILE)
            except OSError:
                pass
        return {}

    def is_active(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return True
        return bool(self._active_external_state())

    def start(self, class_name, duration_mins, webhook_url):
        if pyaudio is None:
            details = "PyAudioWPatch is not installed. Run: pip install PyAudioWPatch==0.2.12.8"
            print(details)
            send_discord_ping(webhook_url, "Recording Error", "Failed", details)
            return False

        with self.lock:
            if (self.thread and self.thread.is_alive()) or self._active_external_state():
                details = "A recording is already active, so a second recording was not started."
                print(details)
                send_discord_ping(webhook_url, "Recording Busy", "Failed", details)
                return False
            self.stop_event.clear()
            if os.path.exists(RECORDING_STOP_FILE):
                try:
                    os.remove(RECORDING_STOP_FILE)
                except OSError:
                    pass
            self.thread = threading.Thread(
                target=self._record,
                args=(class_name, int(duration_mins), webhook_url),
                daemon=True,
                name="online-cheese-recorder",
            )
            self.thread.start()
        return True

    def stop(self, wait=False, timeout=20):
        with self.lock:
            local_thread = self.thread if self.thread and self.thread.is_alive() else None
            if local_thread:
                self.stop_event.set()

        state = self._active_external_state()
        if not local_thread and not state:
            return False, False

        if state and not local_thread:
            try:
                write_json_file(RECORDING_STOP_FILE, {
                    "owner_pid": state.get("owner_pid"),
                    "requested_at": datetime.now().isoformat(),
                })
            except OSError as e:
                print(f"Could not send the recording stop request: {e}")
                return False, False

        if wait:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and self.is_active():
                time.sleep(0.2)
        return True, not self.is_active()

    def _stop_was_requested(self):
        if self.stop_event.is_set():
            return True
        request = read_json_file(RECORDING_STOP_FILE)
        return bool(request) and request.get("owner_pid") in (None, os.getpid())

    @staticmethod
    def _default_loopback_device(audio):
        wasapi_info = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_output = audio.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
        if default_output.get("isLoopbackDevice"):
            return default_output

        default_name = default_output.get("name", "").lower()
        loopbacks = list(audio.get_loopback_device_info_generator())
        for device in loopbacks:
            device_name = device.get("name", "").lower()
            if default_name and (default_name in device_name or device_name in default_name):
                return device
        if loopbacks:
            return loopbacks[0]
        raise RuntimeError("Windows did not expose a WASAPI loopback recording device.")

    def _record(self, class_name, duration_mins, webhook_url):
        safe_name = "".join(c for c in class_name if c.isalnum() or c in " -_").strip() or "Lecture"
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        unique_suffix = uuid.uuid4().hex[:6]
        final_path = os.path.join(RECORDINGS_DIR, f"{safe_name}_{timestamp}_{unique_suffix}.m4a".replace(" ", "_"))
        partial_path = final_path[:-4] + ".partial.m4a"
        encoder = None
        encoder_log = None
        stream = None
        audio = None
        stopped_early = False
        started = False

        try:
            ffmpeg_path = bootstrap_ffmpeg()
            if not ffmpeg_path:
                raise RuntimeError("Could not locate or download the FFmpeg encoder.")

            audio = pyaudio.PyAudio()
            device = self._default_loopback_device(audio)
            channels = max(1, min(2, int(device.get("maxInputChannels", 2))))
            sample_rate = int(float(device.get("defaultSampleRate", 48000)))
            chunk_size = 1024

            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                frames_per_buffer=chunk_size,
                input=True,
                input_device_index=int(device["index"]),
            )

            encoder_log = open(RECORDING_LOG_FILE, "a", encoding="utf-8")
            encoder_log.write(f"\n--- Recording {class_name} at {datetime.now().isoformat()} ---\n")
            encoder_log.flush()
            encoder = subprocess.Popen(
                [
                    ffmpeg_path,
                    "-hide_banner", "-loglevel", "warning", "-y",
                    "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    partial_path,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=encoder_log,
                **hidden_process_kwargs(),
            )

            first_chunk = stream.read(chunk_size, exception_on_overflow=False)
            encoder.stdin.write(first_chunk)
            encoder.stdin.flush()
            time.sleep(0.25)
            if encoder.poll() is not None:
                raise RuntimeError(f"FFmpeg exited during startup. {ffmpeg_log_tail()}")

            write_json_file(RECORDING_STATE_FILE, {
                "owner_pid": os.getpid(),
                "class_name": class_name,
                "started_at": datetime.now().isoformat(),
                "output_path": final_path,
            })
            started = True
            device_name = device.get("name", "Default speakers")
            print(f"Recording system audio from {device_name} for up to {duration_mins} minutes...")
            send_discord_ping(
                webhook_url,
                "Recording Started",
                "Info",
                f"Audio for **{class_name}** is recording from **{device_name}** for up to {duration_mins} minutes.",
            )

            deadline = time.monotonic() + (duration_mins * 60)
            while time.monotonic() < deadline:
                if self._stop_was_requested():
                    stopped_early = True
                    break
                chunk = stream.read(chunk_size, exception_on_overflow=False)
                encoder.stdin.write(chunk)

            stream.stop_stream()
            stream.close()
            stream = None
            encoder.stdin.close()
            return_code = encoder.wait(timeout=25)
            if return_code != 0:
                raise RuntimeError(f"FFmpeg could not finalize the recording. {ffmpeg_log_tail()}")
            if not os.path.exists(partial_path) or os.path.getsize(partial_path) < 1024:
                raise RuntimeError("The recorder did not produce a usable audio file.")

            os.replace(partial_path, final_path)
            action = "stopped early and saved" if stopped_early else "completed and saved"
            print(f"Recording {action}: {final_path}")
            send_discord_ping(
                webhook_url,
                "Recording Saved",
                "Success",
                f"Audio for **{class_name}** was {action} as `{os.path.basename(final_path)}`.",
            )
        except Exception as e:
            details = str(e).strip() or type(e).__name__
            print(f"Recording failed: {details}")
            send_discord_ping(webhook_url, "Recording Error", "Failed", details)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if encoder is not None and encoder.poll() is None:
                try:
                    if encoder.stdin and not encoder.stdin.closed:
                        encoder.stdin.close()
                    encoder.wait(timeout=10)
                except Exception:
                    try:
                        encoder.terminate()
                        encoder.wait(timeout=5)
                    except Exception:
                        encoder.kill()
            if encoder_log is not None:
                encoder_log.close()
            if audio is not None:
                audio.terminate()
            state = read_json_file(RECORDING_STATE_FILE)
            if state.get("owner_pid") == os.getpid():
                try:
                    os.remove(RECORDING_STATE_FILE)
                except OSError:
                    pass
            if os.path.exists(RECORDING_STOP_FILE):
                try:
                    os.remove(RECORDING_STOP_FILE)
                except OSError:
                    pass
            with self.lock:
                if self.thread is threading.current_thread():
                    self.thread = None
            if started and os.path.exists(partial_path):
                print(f"A partial recording was retained for recovery: {partial_path}")


recording_manager = RecordingManager()

def start_audio_recording(class_name, duration_mins, webhook_url):
    return recording_manager.start(class_name, duration_mins, webhook_url)

def stop_audio_recording(silent=False, webhook_url=""):
    requested, finalized = recording_manager.stop(wait=not silent)
    if not requested:
        if not silent:
            console.print("[yellow]No active recording found.[/yellow]\n")
        if webhook_url:
            send_discord_ping(webhook_url, "Stop Recording", "Info", "There was no active recording to stop.")
        return False
    if not silent:
        if finalized:
            console.print("[bold green]Recording stopped and finalized.[/bold green]\n")
        else:
            console.print("[yellow]Stop requested; the audio file is still finalizing.[/yellow]\n")
    return True

# ==========================================
# 4. NOTIFICATIONS & AUTOMATION
# ==========================================

def window_process_name(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            pid,
        )
        try:
            return os.path.basename(win32process.GetModuleFileNameEx(handle, 0)).lower()
        finally:
            handle.Close()
    except Exception:
        return ""


def enumerate_teams_windows():
    windows = []

    def collect(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            process_name = window_process_name(hwnd)
            is_teams = process_name in {"ms-teams.exe", "teams.exe"} or "teams" in title.lower()
            if not is_teams:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width < 300 or height < 200:
                return
            windows.append({
                "hwnd": hwnd,
                "title": title or "Microsoft Teams",
                "process": process_name,
                "rect": (left, top, right, bottom),
                "area": width * height,
            })
        except Exception:
            pass

    win32gui.EnumWindows(collect, None)
    windows.sort(key=lambda item: item["area"], reverse=True)
    return windows


def wait_for_teams_windows(previous_handles, timeout):
    deadline = time.monotonic() + max(1, float(timeout))
    latest = []
    while time.monotonic() < deadline:
        latest = enumerate_teams_windows()
        new_windows = [item for item in latest if item["hwnd"] not in previous_handles]
        if new_windows:
            return new_windows + [item for item in latest if item["hwnd"] in previous_handles]
        time.sleep(0.5)
    return latest


def foreground_is_window(hwnd):
    try:
        foreground = win32gui.GetForegroundWindow()
        if foreground == hwnd:
            return True
        root_flag = getattr(win32con, "GA_ROOTOWNER", 3)
        return win32gui.GetAncestor(foreground, root_flag) == win32gui.GetAncestor(hwnd, root_flag)
    except Exception:
        return False


def force_window_foreground(hwnd):
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False

    attached_threads = []
    current_thread = win32api.GetCurrentThreadId()
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        foreground = win32gui.GetForegroundWindow()
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        foreground_thread = 0
        if foreground:
            foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground)

        for thread_id in {target_thread, foreground_thread}:
            if thread_id and thread_id != current_thread:
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached_threads.append(thread_id)
                except Exception:
                    pass

        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        try:
            win32gui.SetActiveWindow(hwnd)
            win32gui.SetFocus(hwnd)
        except Exception:
            pass
    except Exception as e:
        print(f"Primary foreground attempt failed: {e}")
    finally:
        for thread_id in reversed(attached_threads):
            try:
                win32process.AttachThreadInput(current_thread, thread_id, False)
            except Exception:
                pass

    if not foreground_is_window(hwnd):
        try:
            # A brief Alt key event satisfies Windows' foreground-lock rules on
            # systems that reject SetForegroundWindow from a background daemon.
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        except Exception as e:
            print(f"Fallback foreground attempt failed: {e}")

    time.sleep(0.35)
    return foreground_is_window(hwnd)


def window_capture_region(hwnd):
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if width > 0 and height > 0:
            return left, top, width, height
    except Exception:
        pass
    return None


def locate_join_button(hwnd):
    region = window_capture_region(hwnd)
    try:
        return pyautogui.locateCenterOnScreen(
            JOIN_BTN_IMAGE,
            confidence=0.80,
            grayscale=True,
            region=region,
        )
    except pyautogui.ImageNotFoundException:
        return None


def send_discord_ping(webhook_url, class_name, status="Success", details=""):
    if not webhook_url:
        return False
    
    if status == "Success":
        color = 65280
        title = f"Joined: {class_name}"
    elif status == "Failed":
        color = 16711680
        title = f"Failed: {class_name}"
    else:
        color = 3447003
        title = f"Info: {class_name}"

    data = {
        "content": "🧀 **Online Cheese Update**",
        "embeds": [{"title": title, "description": details, "color": color, "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}]
    }
    try:
        response = requests.post(webhook_url, json=data, timeout=10)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Discord notification failed: {type(e).__name__}: {e}")
        return False

def capture_and_send_screenshot(webhook_url, class_name, preferred_hwnd=None):
    if not webhook_url:
        return False
    screenshot_path = os.path.join(DATA_DIR, f"verify_{int(time.time())}_{uuid.uuid4().hex[:6]}.png")
    try:
        teams_windows = enumerate_teams_windows()
        if not teams_windows:
            raise RuntimeError("No Teams window was available for a private verification screenshot.")
        target = next((item for item in teams_windows if item["hwnd"] == preferred_hwnd), teams_windows[0])
        force_window_foreground(target["hwnd"])
        region = window_capture_region(target["hwnd"])
        if not region:
            raise RuntimeError("The Teams window bounds could not be determined.")
        pyautogui.screenshot(screenshot_path, region=region)
        with open(screenshot_path, "rb") as f:
            response = requests.post(webhook_url, data={"content": f"**Verification:** {class_name}"}, files={"file": (os.path.basename(screenshot_path), f, "image/png")}, timeout=15)
            response.raise_for_status()
        print(f"Screenshot sent for {class_name}")
        return True
    except Exception as e:
        print(f"Screenshot failed: {e}")
        send_discord_ping(webhook_url, "Screenshot Error", "Failed", str(e))
        return False
    finally:
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)

def execute_join(name, url, webhook_url, join_delay, screenshot_delay, enable_recording, recording_duration):
    global last_failed_job
    job = {"name": name, "url": url, "webhook_url": webhook_url, "join_delay": join_delay, "screenshot_delay": screenshot_delay, "enable_recording": enable_recording, "recording_duration": recording_duration}
    if not join_automation_lock.acquire(timeout=max(60, int(join_delay) + JOIN_SEARCH_TIMEOUT)):
        details = "Another Teams join operation was still active, so this lecture could not take control of the screen."
        print(details)
        send_discord_ping(webhook_url, name, "Failed", details)
        last_failed_job = job
        return False

    try:
        print(f"\n[{time.strftime('%H:%M:%S')}] Launching {name}...")
        send_discord_ping(webhook_url, name, "Info", f"Opening Teams and waiting up to {join_delay}s for its meeting window.")
        previous_handles = {item["hwnd"] for item in enumerate_teams_windows()}
        if not webbrowser.open(url):
            raise RuntimeError("Windows did not accept the Teams link.")

        candidates = wait_for_teams_windows(previous_handles, join_delay)
        search_deadline = time.monotonic() + JOIN_SEARCH_TIMEOUT
        clicked = False
        clicked_hwnd = None
        foreground_confirmed = False

        while time.monotonic() < search_deadline and not clicked:
            current = enumerate_teams_windows()
            known_handles = {item["hwnd"] for item in candidates}
            candidates = [item for item in candidates if win32gui.IsWindow(item["hwnd"])]
            candidates.extend(item for item in current if item["hwnd"] not in known_handles)

            if not candidates:
                print("Waiting for a Teams window to appear...")
                time.sleep(JOIN_SEARCH_INTERVAL)
                continue

            for candidate in candidates:
                print(f"Bringing Teams to the foreground: {candidate['title']}")
                foreground_confirmed = force_window_foreground(candidate["hwnd"])
                if not foreground_confirmed:
                    print("Windows has not confirmed foreground focus yet; retrying the window anyway.")
                button = locate_join_button(candidate["hwnd"])
                if button:
                    force_window_foreground(candidate["hwnd"])
                    pyautogui.click(button.x, button.y)
                    clicked = True
                    clicked_hwnd = candidate["hwnd"]
                    break
            if not clicked:
                time.sleep(JOIN_SEARCH_INTERVAL)

        if not clicked:
            focus_note = " Windows also refused to confirm foreground focus." if not foreground_confirmed else ""
            raise RuntimeError(f"The Join Now button was not found after {JOIN_SEARCH_TIMEOUT} seconds.{focus_note}")

        print("Join Now clicked with Teams in the foreground.")
        mins_display = round(screenshot_delay / 60, 1)
        send_discord_ping(webhook_url, name, "Success", f"Teams was foregrounded and joined. Verification is scheduled in {mins_display} minutes.")
        verification_timer = threading.Timer(float(screenshot_delay), capture_and_send_screenshot, args=[webhook_url, name, clicked_hwnd])
        verification_timer.daemon = True
        verification_timer.start()

        if enable_recording:
            start_audio_recording(name, recording_duration, webhook_url)

        if last_failed_job and last_failed_job.get("name") == name:
            last_failed_job = {}
        return True
    except Exception as e:
        details = str(e).strip() or type(e).__name__
        print(f"Join failed: {details}")
        send_discord_ping(webhook_url, name, "Failed", f"{details} Send 'retry' to attempt again.")
        last_failed_job = job
        return False
    finally:
        join_automation_lock.release()

# ==========================================
# 5. REMOTE LISTENER THREAD
# ==========================================

def ntfy_listener(topic, webhook_url):
    global last_action_time
    global last_failed_job
    if not topic:
        return
    
    url = f"https://ntfy.sh/{topic}/json"
    print("Connecting to the private remote-command listener...")
    retry_delay = 5
    while True:
        try:
            with requests.get(url, stream=True, timeout=(15, 300)) as response:
                response.raise_for_status()
                retry_delay = 5
                for line in response.iter_lines():
                    if not line:
                        continue
                    event_data = json.loads(line.decode('utf-8'))
                    if event_data.get("event") != "message":
                        continue
                    msg = event_data.get("message", "").strip().lower()
                    print(f"Remote command received: {msg}")

                    current_data = load_data()
                    cooldown = current_data.get("remote_cooldown", 60)

                    current_time = time.time()
                    if current_time - last_action_time < cooldown:
                        print(f"Rate limited. Cooldown active for {cooldown}s.")
                        send_discord_ping(webhook_url, "Rate Limit Block", "Failed", f"Command ignored due to the {cooldown}-second cooldown.")
                        continue

                    last_action_time = current_time

                    if msg == "ping":
                        next_run = schedule.next_run()
                        if next_run:
                            remaining_seconds = max(0, int((next_run - datetime.now()).total_seconds()))
                            days, remainder = divmod(remaining_seconds, 86400)
                            hours, remainder = divmod(remainder, 3600)
                            minutes, _ = divmod(remainder, 60)
                            parts = []
                            if days:
                                parts.append(f"{days}d")
                            if hours:
                                parts.append(f"{hours}h")
                            parts.append(f"{minutes}m")
                            send_discord_ping(webhook_url, "System Ping", "Info", f"Next lecture is scheduled for **{next_run.strftime('%A at %H:%M')}** (in {' '.join(parts)}).")
                        else:
                            send_discord_ping(webhook_url, "System Ping", "Info", "There are no lectures currently scheduled.")
                    elif msg == "retry":
                        if last_failed_job:
                            send_discord_ping(webhook_url, "Manual Retry", "Info", f"Attempting to rejoin: {last_failed_job['name']}...")
                            threading.Thread(target=execute_join, kwargs=dict(last_failed_job), daemon=True).start()
                        else:
                            send_discord_ping(webhook_url, "Manual Retry", "Failed", "There are no recently failed classes to retry.")
                    elif msg in ["stop", "stop record"]:
                        stop_audio_recording(silent=True, webhook_url=webhook_url)
                    else:
                        capture_and_send_screenshot(webhook_url, "Manual Remote Request")
        except Exception as e:
            print(f"Remote listener disconnected ({type(e).__name__}). Retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay = min(300, retry_delay * 2)

# ==========================================
# 6. DAEMON PROCESSES & SYSTEM CONTROLS
# ==========================================

def get_process_identity(pid):
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            int(pid),
        )
        try:
            times = win32process.GetProcessTimes(handle)
            executable = os.path.normcase(os.path.abspath(win32process.GetModuleFileNameEx(handle, 0)))
            return {
                "pid": int(pid),
                "created": round(times["CreationTime"].timestamp(), 3),
                "executable": executable,
            }
        finally:
            handle.Close()
    except Exception:
        return {}


def check_daemon_status():
    if not os.path.exists(PID_FILE):
        return False, None
    saved_identity = read_json_file(PID_FILE)
    pid = saved_identity.get("pid")
    if not pid or "created" not in saved_identity or "executable" not in saved_identity:
        return False, pid
    current_identity = get_process_identity(pid)
    matches = bool(current_identity) and (
        current_identity.get("created") == saved_identity.get("created")
        and current_identity.get("executable") == saved_identity.get("executable")
    )
    return matches, int(pid)

def kill_daemon(silent=False):
    is_running, pid = check_daemon_status()
    if is_running:
        if recording_manager.is_active():
            recording_manager.stop(wait=True, timeout=20)
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **hidden_process_kwargs(),
        )
        if not silent:
            if result.returncode == 0:
                console.print(f"[bold red]Daemon (PID: {pid}) terminated.[/bold red]\n")
            else:
                console.print(f"[bold red]Windows could not terminate daemon PID {pid}.[/bold red]\n")
    else:
        if not silent:
            console.print("[yellow]Daemon process not found. Cleaning up stale state.[/yellow]\n")
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def run_in_background(job_function, *args, **kwargs):
    threading.Thread(
        target=job_function,
        args=args,
        kwargs=kwargs,
        daemon=True,
        name=f"online-cheese-job-{uuid.uuid4().hex[:6]}",
    ).start()


def normalize_lecture_time(value):
    for time_format in ("%H:%M", "%I:%M %p"):
        try:
            return datetime.strptime(str(value).strip(), time_format).strftime("%H:%M")
        except ValueError:
            pass
    return None

def run_daemon():
    identity = get_process_identity(os.getpid())
    if not identity:
        raise RuntimeError("Could not establish a safe daemon process identity.")
    write_json_file(PID_FILE, identity)
        
    data = load_data()
    join_delay = data.get("join_delay", 15)
    screenshot_delay = data.get("screenshot_delay", 300)
    enable_recording = data.get("enable_recording", False)
    recording_duration = data.get("recording_duration", 90)
    webhook = data.get("webhook_url", "")
    topic = data.get("remote_topic", "")
    
    if topic:
        threading.Thread(target=ntfy_listener, args=(topic, webhook), daemon=True, name="online-cheese-remote").start()

    schedule.clear()
    valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    scheduled_count = 0
    for lec in data.get("lectures", []):
        if not isinstance(lec, dict):
            print("Skipping a malformed lecture entry that is not an object.")
            continue
        lecture_time = normalize_lecture_time(lec.get("time"))
        lecture_days = lec.get("days", [])
        if not lecture_time or not isinstance(lecture_days, list):
            print(f"Skipping malformed lecture schedule: {lec.get('name', 'Unnamed lecture')}")
            continue
        for day in lecture_days:
            day = str(day).lower()
            if day not in valid_days:
                print(f"Skipping invalid day '{day}' for {lec.get('name', 'Unnamed lecture')}")
                continue
            day_func = getattr(schedule.every(), day)
            day_func.at(lecture_time).do(
                run_in_background,
                execute_join,
                name=str(lec.get("name", "Lecture")),
                url=str(lec.get("url", "")),
                webhook_url=webhook,
                join_delay=join_delay,
                screenshot_delay=screenshot_delay,
                enable_recording=enable_recording,
                recording_duration=recording_duration,
            )
            scheduled_count += 1

    print(f"Loaded {scheduled_count} scheduled lecture occurrence(s). Entering main waiting loop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(2)
    finally:
        saved_identity = read_json_file(PID_FILE)
        if saved_identity.get("pid") == os.getpid() and os.path.exists(PID_FILE):
            os.remove(PID_FILE)

def spawn_background_daemon(silent=False):
    kill_daemon(silent=True)
    time.sleep(0.5)
    try:
        kwargs = {}
        if sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000 
            kwargs = {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}

        cmd = [sys.executable, '--background'] if getattr(sys, 'frozen', False) else [sys.executable, os.path.abspath(__file__), '--background']
        
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        deadline = time.monotonic() + 8
        daemon_ready = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            daemon_ready, _ = check_daemon_status()
            if daemon_ready:
                break
            time.sleep(0.2)

        if not daemon_ready:
            raise RuntimeError("The daemon did not report a healthy startup. Check daemon.log for details.")
        if not silent:
            console.print("[bold green]Daemon launched and verified. You can safely close this window.[/bold green]\n")
    except Exception as e:
        if not silent:
            console.print(f"[bold red]Failed to start: {e}[/bold red]\n")

def toggle_startup():
    startup_dir = os.path.join(os.getenv('APPDATA'), r"Microsoft\Windows\Start Menu\Programs\Startup")
    vbs_path = os.path.join(startup_dir, "OnlineCheese.vbs")

    if os.path.exists(vbs_path):
        os.remove(vbs_path)
        console.print("[bold red]Removed from Windows Startup.[/bold red]\n")
    else:
        exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        vbs_content = 'Set WshShell = CreateObject("WScript.Shell")\n'
        if getattr(sys, 'frozen', False):
            vbs_content += f'WshShell.Run """{exe_path}"" --background", 0, False'
        else:
            vbs_content += f'WshShell.Run """{sys.executable}"" ""{exe_path}"" --background", 0, False'
            
        with open(vbs_path, "w") as f: 
            f.write(vbs_content)
        console.print("[bold green]Added to Windows Startup.[/bold green]\n")

def auto_refresh_daemon():
    is_running, _ = check_daemon_status()
    if is_running:
        spawn_background_daemon(silent=True)

# ==========================================
# 7. INTERACTIVE MODULES & HELP
# ==========================================

def manage_settings():
    data = load_data()
    console.print(Panel.fit("[bold yellow]Configuration & Settings[/bold yellow]", border_style="yellow"))
    
    current_webhook = data.get('webhook_url') or 'Not Set'
    hidden_webhook = f"{current_webhook[:35]}..." if len(current_webhook) > 35 else current_webhook
    rec_status = "[green]Enabled[/green]" if data.get('enable_recording') else "[red]Disabled[/red]"
    remote_topic = data.get('remote_topic', '')
    remote_secret = remote_topic.removeprefix("cheese_cmd_")
    legacy_remote_topic = len(remote_secret) < 24
    configured_recordings_dir = data.get("recordings_dir", default_recordings_dir())
    
    console.print(f"Discord Webhook: [cyan]{hidden_webhook}[/cyan]")
    console.print(f"App Load Wait Delay: [cyan]{data.get('join_delay', 15)} seconds[/cyan]")
    console.print(f"Screenshot Timer: [cyan]{data.get('screenshot_delay', 300)} seconds[/cyan]")
    console.print(f"Remote Cooldown: [cyan]{data.get('remote_cooldown', 60)} seconds[/cyan]")
    console.print(f"Auto-Record Audio: {rec_status} [cyan]({data.get('recording_duration', 90)} mins)[/cyan]")
    console.print(f"Recording Folder: [cyan]{configured_recordings_dir}[/cyan]")
    if os.path.normcase(os.path.abspath(configured_recordings_dir)) != os.path.normcase(os.path.abspath(RECORDINGS_DIR)):
        console.print(f"[yellow]Configured drive is unavailable; currently using {RECORDINGS_DIR}.[/yellow]")
    console.print(f"Remote Trigger URL: [cyan]https://ntfy.sh/{remote_topic}[/cyan]")
    if legacy_remote_topic:
        console.print("[yellow]This is a legacy short remote URL. Rotate it below for stronger protection.[/yellow]")
    console.print()
    
    changed = False
    if Confirm.ask("[yellow]Change Discord Webhook URL?[/yellow]"):
        data["webhook_url"] = Prompt.ask("[green]New Webhook URL[/green]")
        changed = True
    if Confirm.ask("[yellow]Change app load wait delay?[/yellow]"):
        data["join_delay"] = IntPrompt.ask("[green]New wait time (in seconds)[/green]", default=data.get('join_delay', 15))
        changed = True
    if Confirm.ask("[yellow]Change verification screenshot timer?[/yellow]"):
        data["screenshot_delay"] = IntPrompt.ask("[green]New screenshot timer (in seconds)[/green]", default=data.get('screenshot_delay', 300))
        changed = True
    if Confirm.ask("[yellow]Change remote cooldown rate limit?[/yellow]"):
        data["remote_cooldown"] = IntPrompt.ask("[green]New cooldown (in seconds)[/green]", default=data.get('remote_cooldown', 60))
        changed = True
    if legacy_remote_topic and Confirm.ask("[yellow]Rotate the legacy private remote trigger URL?[/yellow]"):
        data["remote_topic"] = f"cheese_cmd_{uuid.uuid4().hex}"
        changed = True
    if Confirm.ask("[yellow]Toggle Auto-Recording of Class Audio?[/yellow]"):
        data["enable_recording"] = not data.get("enable_recording", False)
        if data["enable_recording"]:
            data["recording_duration"] = IntPrompt.ask("[green]Default recording length (in minutes)[/green]", default=data.get('recording_duration', 90))
        changed = True
    if Confirm.ask("[yellow]Change the recording folder?[/yellow]"):
        requested_dir = Prompt.ask("[green]Recording folder[/green]", default=configured_recordings_dir).strip()
        expanded_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(requested_dir)))
        active_dir = activate_recordings_dir(expanded_dir)
        if os.path.normcase(active_dir) == os.path.normcase(expanded_dir):
            data["recordings_dir"] = expanded_dir
            changed = True
        else:
            activate_recordings_dir(configured_recordings_dir)
            console.print("[bold red]That folder could not be used, so the setting was not changed.[/bold red]")
        
    if changed:
        save_data(data)
        console.print("[bold green]Settings saved.[/bold green]")
        auto_refresh_daemon()

def link_converter_tool():
    console.print(Panel.fit("[bold cyan]Link Converter[/bold cyan]", border_style="cyan"))
    raw_url = Prompt.ask("[green]Paste Teams link[/green]")
    clean_url = clean_teams_link(raw_url)
    console.print(f"\n[bold green]Cleaned Link:[/bold green]\n[cyan]{clean_url}[/cyan]\n")

def add_lecture():
    data = load_data()
    if not data.get("webhook_url") and Confirm.ask("[yellow]No Discord Webhook found. Add one now?[/yellow]"):
        data["webhook_url"] = Prompt.ask("[green]Discord Webhook URL[/green]")
    
    name = Prompt.ask("\n[green]Class Name[/green]")
    valid_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    while True:
        days_str = Prompt.ask("[green]Days (comma-separated)[/green]")
        days = [d.strip().lower() for d in days_str.split(",")]
        if all(d in valid_days for d in days):
            break
        console.print("[red]Invalid day found.[/red]")
        
    while True:
        time_input = Prompt.ask("[green]Time (HH:MM in 24h format)[/green]").strip()
        try:
            time_str = datetime.strptime(time_input, "%H:%M").strftime("%H:%M")
            break
        except ValueError:
            try:
                time_str = datetime.strptime(time_input, "%I:%M %p").strftime("%H:%M")
                break
            except ValueError:
                console.print("[red]Invalid time format. Please use strict HH:MM.[/red]")
                
    raw_url = Prompt.ask("[green]Teams Link[/green]")
    url = clean_teams_link(raw_url)
    
    numeric_ids = [
        int(str(lecture.get("id")))
        for lecture in data["lectures"]
        if isinstance(lecture, dict) and str(lecture.get("id", "")).isdigit()
    ]
    new_id = str(max(numeric_ids, default=0) + 1)
    data["lectures"].append({"id": new_id, "name": name, "days": days, "time": time_str, "url": url})
    save_data(data)
    console.print(f"\n[bold green]Saved {name}. Link optimized.[/bold green]")
    auto_refresh_daemon()

def remove_lecture():
    list_lectures()
    data = load_data()
    if not data["lectures"]:
        return
    target_id = Prompt.ask("[green]Enter the ID of the lecture to remove (or 'q' to cancel)[/green]")
    if target_id.lower() == 'q':
        return
    
    original_len = len(data["lectures"])
    data["lectures"] = [
        lecture
        for lecture in data["lectures"]
        if not isinstance(lecture, dict) or str(lecture.get("id")) != target_id
    ]
    if len(data["lectures"]) < original_len:
        save_data(data)
        console.print("[bold green]Lecture removed.[/bold green]")
        auto_refresh_daemon()
    else:
        console.print("[bold red]ID not found.[/bold red]\n")

def list_lectures():
    data = load_data()
    lectures = data.get("lectures", [])
    if not lectures:
        console.print("\n[yellow]Your schedule is empty.[/yellow]\n")
        return
    table = Table(title="Your Schedule", title_style="bold cyan", border_style="cyan")
    table.add_column("ID", style="dim", width=3)
    table.add_column("Class Name", style="cyan")
    table.add_column("Days", style="green")
    table.add_column("Time", justify="right", style="yellow")
    for lec in lectures:
        if not isinstance(lec, dict):
            continue
        days = lec.get("days", []) if isinstance(lec.get("days", []), list) else []
        table.add_row(
            str(lec.get("id", "?")),
            str(lec.get("name", "Unnamed lecture")),
            ", ".join(str(day).capitalize()[:3] for day in days),
            str(lec.get("time", "Invalid")),
        )
    console.print("\n", table, "\n")

def show_help():
    help_text = """
    [bold cyan]How to use Remote Commands:[/bold cyan]
    Go to Option 5 (Settings) and copy your unique ntfy.sh URL. Open it on your phone.
    - Send [bold green]ping[/bold green]: Returns the time remaining until your next scheduled class.
    - Send [bold green]retry[/bold green]: Forces the app to attempt joining the last class that failed.
    - Send [bold green]stop[/bold green]: Manually ends and saves an ongoing audio recording early.
    - Send [bold green]anything else[/bold green]: Sends a Teams-window-only screenshot to Discord.

    [bold cyan]Joining and Recording:[/bold cyan]
    During a join attempt, Online Cheese restores Teams, brings its meeting window to the foreground, and retries
    the Join now search for a short period. Avoid moving the mouse during this step. Audio recording captures the
    default Windows speaker output through WASAPI loopback and finalizes it as an M4A in the Recordings folder.
    
    [bold cyan]The Link Converter:[/bold cyan]
    Online Cheese strips the browser launcher out of Teams links so they open in the desktop app. 
    You can use Option 4 to manually convert links, or paste links directly into Option 2 when adding a class.
    
    [bold cyan]System Controls:[/bold cyan]
    - [bold yellow]Start Stealth Daemon:[/bold yellow] Launches the background worker. You can close the menu afterwards.
    - [bold yellow]Toggle Windows Startup:[/bold yellow] Adds a script to Windows Startup so the daemon runs automatically on boot.
    - [bold yellow]Kill Background Daemon:[/bold yellow] Stops the background worker.
    
    [bold cyan]Developer Tools:[/bold cyan]
    Type [bold red]dev[/bold red] in the main menu prompt to unlock hidden testing options.
    """
    console.print(Panel(help_text, title="Online Cheese Help Guide", border_style="blue"))

# ==========================================
# 8. MAIN MENU
# ==========================================

def main_menu():
    dev_mode = False
    while True:
        console.clear()
        console.print(Panel.fit(f"[bold yellow]Online Cheese v{APP_VERSION}[/bold yellow]", border_style="yellow"))
        
        is_running, _ = check_daemon_status()
        status_color = "bold green" if is_running else "dim red"
        status_text = "Running" if is_running else "Stopped"
        
        menu = Table(show_header=False, box=None)
        
        menu.add_row("[bold cyan]--- Schedule & Links ---[/bold cyan]", "")
        menu.add_row("[cyan]1.[/cyan]", "View Schedule")
        menu.add_row("[cyan]2.[/cyan]", "Add a Lecture")
        menu.add_row("[cyan]3.[/cyan]", "Remove a Lecture")
        menu.add_row("[cyan]4.[/cyan]", "Convert a Teams Link")
        
        menu.add_row("", "")
        
        menu.add_row("[bold yellow]--- Configuration ---[/bold yellow]", "")
        menu.add_row("[yellow]5.[/yellow]", "Settings (Webhook, Audio Recording, Timers)")
        
        menu.add_row("", "")
        
        menu.add_row("[bold magenta]--- System & Daemon ---[/bold magenta]", "")
        menu.add_row("[magenta]6.[/magenta]", f"Start Background Daemon [{status_color}]({status_text})[/{status_color}]")
        menu.add_row("[magenta]7.[/magenta]", "Check Daemon Status")
        menu.add_row("[magenta]8.[/magenta]", "Toggle Windows Startup")
        menu.add_row("[magenta]9.[/magenta]", "Stop Active Audio Recording")
        menu.add_row("[red]10.[/red]", "Kill Background Daemon")
        
        menu.add_row("", "")
        
        menu.add_row("[bold blue]--- Application ---[/bold blue]", "")
        menu.add_row("[blue]11.[/blue]", "Help / About")
        
        if dev_mode:
            menu.add_row("[dim yellow]98.[/dim yellow]", "[dim yellow]DEV: Test Join Immediately[/dim yellow]")
            menu.add_row("[dim yellow]99.[/dim yellow]", "[dim yellow]DEV: Test Timer Loop (1m)[/dim yellow]")
            
        menu.add_row("[red]0.[/red]", "Exit")
        
        console.print(menu)
        choice = Prompt.ask("\n[green]Select an option[/green]").strip().lower()
        
        if choice == "dev":
            dev_mode = not dev_mode
            console.print("[bold red]Developer Mode Toggled.[/bold red]\n")
        elif choice == "1":
            list_lectures()
        elif choice == "2":
            add_lecture()
        elif choice == "3":
            remove_lecture()
        elif choice == "4":
            link_converter_tool()
        elif choice == "5":
            manage_settings()
        elif choice == "6": 
            spawn_background_daemon()
        elif choice == "7": 
            is_run, pid = check_daemon_status()
            if is_run:
                console.print(f"[bold green]Daemon is running (PID: {pid}).[/bold green]\n")
            else:
                console.print("[bold red]Daemon is not running.[/bold red]\n")
        elif choice == "8":
            toggle_startup()
        elif choice == "9":
            stop_audio_recording()
        elif choice == "10":
            kill_daemon()
        elif choice == "11":
            show_help()
        elif choice == "98" and dev_mode:
            url = Prompt.ask("\n[green]Enter msteams:// link[/green]")
            data = load_data()
            execute_join("DEV Test", url, data.get("webhook_url", ""), data.get("join_delay", 15), data.get("screenshot_delay", 300), data.get("enable_recording", False), data.get("recording_duration", 90))
        elif choice == "99" and dev_mode:
            test_url = Prompt.ask("[green]Enter the msteams:// link to test[/green]")
            data = load_data()
            target_time = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M")
            schedule.every().day.at(target_time).do(execute_join, name="Timer Test Dummy", url=test_url, webhook_url=data.get("webhook_url", ""), join_delay=data.get("join_delay", 15), screenshot_delay=data.get("screenshot_delay", 300), enable_recording=data.get("enable_recording", False), recording_duration=data.get("recording_duration", 90))
            try:
                while True:
                    schedule.run_pending()
                    time.sleep(5)
            except KeyboardInterrupt:
                schedule.clear()
        elif choice == "0":
            sys.exit(0)
        else:
            continue
            
        console.input("\n[dim white]Press Enter to return to the menu...[/dim white]")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print(f"Online Cheese {APP_VERSION}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--background":
        sys.stdout = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
        try: 
            print(f"\n--- Daemon Started at {datetime.now()} ---")
            run_daemon()
        except Exception as e: 
            import traceback
            print(f"DAEMON CRASH: {e}")
            traceback.print_exc()
    else:
        try:
            main_menu()
        except KeyboardInterrupt:
            sys.exit(0)
