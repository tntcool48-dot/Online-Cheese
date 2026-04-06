import os
import sys
import time
import json
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
import io
import PIL
import pygetwindow as gw
from datetime import datetime, timedelta
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.panel import Panel

console = Console()
last_action_time = 0  
last_failed_job = {}

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

JSON_FILE = os.path.join(get_app_dir(), "classes.json")
JOIN_BTN_IMAGE = get_resource_path("join_now.png")
PID_FILE = os.path.join(get_app_dir(), "daemon.pid")
LOG_FILE = os.path.join(get_app_dir(), "daemon.log")
RECORDINGS_DIR = os.path.join(get_app_dir(), "Recordings")

if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)

# ==========================================
# 2. DATA MANAGEMENT & UTILS
# ==========================================

def load_data():
    if not os.path.exists(JSON_FILE):
        default_data = {
            "webhook_url": "", 
            "join_delay": 15,
            "screenshot_delay": 300,
            "remote_cooldown": 60,
            "enable_recording": False,
            "recording_duration": 90,
            "remote_topic": f"cheese_cmd_{uuid.uuid4().hex[:12]}",
            "lectures": []
        }
        save_data(default_data)
        return default_data
    
    with open(JSON_FILE, "r") as f:
        data = json.load(f)
        
    updated = False
    if "join_delay" not in data: data["join_delay"] = 15; updated = True
    if "screenshot_delay" not in data: data["screenshot_delay"] = 300; updated = True
    if "remote_cooldown" not in data: data["remote_cooldown"] = 60; updated = True
    if "enable_recording" not in data: data["enable_recording"] = False; updated = True
    if "recording_duration" not in data: data["recording_duration"] = 90; updated = True
    if "remote_topic" not in data: data["remote_topic"] = f"cheese_cmd_{uuid.uuid4().hex[:12]}"; updated = True
        
    for lec in data.get("lectures", []):
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
                
    if updated: save_data(data)
    return data

def save_data(data):
    with open(JSON_FILE, "w") as f:
        json.dump(data, f, indent=4)

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

def bootstrap_ffmpeg():
    ffmpeg_path = os.path.join(get_app_dir(), "ffmpeg.exe")
    if os.path.exists(ffmpeg_path):
        return ffmpeg_path

    print("Downloading audio engine...")
    try:
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.filename.endswith("ffmpeg.exe"):
                        file_info.filename = "ffmpeg.exe" 
                        zip_ref.extract(file_info, get_app_dir())
                        print("Audio engine downloaded successfully.")
                        return ffmpeg_path
    except Exception as e:
        print(f"Failed to download audio engine: {e}")
        return None

def start_audio_recording(class_name, duration_mins, webhook_url):
    ffmpeg_path = bootstrap_ffmpeg()
    if not ffmpeg_path:
        send_discord_ping(webhook_url, "Recording Error", "Failed", "Could not locate or download the audio engine.")
        return

    safe_name = "".join([c for c in class_name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"{safe_name}_{timestamp}.m4a"
    output_path = os.path.join(RECORDINGS_DIR, filename.replace(" ", "_"))
    
    duration_secs = int(duration_mins) * 60

    print(f"Starting background audio recording for {duration_mins} minutes...")
    
    cmd = [
        ffmpeg_path,
        "-y", 
        "-f", "wasapi", 
        "-i", "default", 
        "-t", str(duration_secs),
        "-c:a", "aac", 
        "-b:a", "128k", 
        output_path
    ]
    
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        subprocess.Popen(cmd, startupinfo=startupinfo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        send_discord_ping(webhook_url, "Recording Started", "Info", f"Audio for **{class_name}** is recording. It will save to the Recordings folder in {duration_mins} minutes.")
    except Exception as e:
        print(f"Failed to launch recorder: {e}")
        send_discord_ping(webhook_url, "Recording Error", "Failed", str(e))

def stop_audio_recording(silent=False, webhook_url=""):
    try:
        output = subprocess.getoutput('tasklist /FI "IMAGENAME eq ffmpeg.exe"')
        if "ffmpeg.exe" in output:
            subprocess.run("taskkill /IM ffmpeg.exe /F", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not silent: console.print("[bold green]Recording stopped and saved.[/bold green]\n")
            if webhook_url: send_discord_ping(webhook_url, "Recording Stopped", "Success", "The active audio recording was manually stopped and saved.")
        else:
            if not silent: console.print("[yellow]No active recording found.[/yellow]\n")
            if webhook_url: send_discord_ping(webhook_url, "Stop Recording", "Info", "There was no active recording to stop.")
    except Exception as e:
        if not silent: console.print(f"[bold red]Failed to stop recording: {e}[/bold red]\n")

# ==========================================
# 4. NOTIFICATIONS & AUTOMATION
# ==========================================

def send_discord_ping(webhook_url, class_name, status="Success", details=""):
    if not webhook_url: return
    
    if status == "Success":
        color = 65280; title = f"Joined: {class_name}"
    elif status == "Failed":
        color = 16711680; title = f"Failed: {class_name}"
    else:
        color = 3447003; title = f"Info: {class_name}"

    data = {
        "content": "🧀 **Online Cheese Update**",
        "embeds": [{"title": title, "description": details, "color": color, "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}]
    }
    try: requests.post(webhook_url, json=data, timeout=5)
    except: pass

def capture_and_send_screenshot(webhook_url, class_name):
    if not webhook_url: return
    screenshot_path = os.path.join(get_app_dir(), f"verify_{int(time.time())}.png")
    try:
        pyautogui.screenshot(screenshot_path)
        with open(screenshot_path, "rb") as f:
            requests.post(webhook_url, data={"content": f"**Verification:** {class_name}"}, files={"file": (os.path.basename(screenshot_path), f, "image/png")}, timeout=15)
        print(f"Screenshot sent for {class_name}")
    except Exception as e:
        print(f"Screenshot failed: {e}")
        send_discord_ping(webhook_url, "Screenshot Error", "Failed", str(e))
    finally:
        if os.path.exists(screenshot_path): os.remove(screenshot_path)

def execute_join(name, url, webhook_url, join_delay, screenshot_delay, enable_recording, recording_duration):
    global last_failed_job
    print(f"\n[{time.strftime('%H:%M:%S')}] Launching {name}...")
    send_discord_ping(webhook_url, name, "Info", f"Opening Teams... waiting {join_delay}s for app to load.")
    
    webbrowser.open(url)
    time.sleep(join_delay) 
    
    print("Forcing Teams to the foreground...")
    try:
        teams_windows = gw.getWindowsWithTitle('Teams')
        if teams_windows:
            teams_win = teams_windows[0]
            if teams_win.isMinimized:
                teams_win.restore()
            teams_win.activate()
            time.sleep(1.5) 
    except Exception as e:
        print(f"Could not force window focus: {e}")
    
    print("Searching for 'Join Now'...")
    try:
        btn_location = pyautogui.locateCenterOnScreen(JOIN_BTN_IMAGE, confidence=0.8)
        if btn_location:
            pyautogui.click(btn_location)
            print("Button clicked.")
            mins_display = round(screenshot_delay / 60, 1)
            send_discord_ping(webhook_url, name, "Success", f"Starting {mins_display}-minute verification timer.")
            threading.Timer(float(screenshot_delay), capture_and_send_screenshot, args=[webhook_url, name]).start()
            
            if enable_recording:
                start_audio_recording(name, recording_duration, webhook_url)
            
            if last_failed_job and last_failed_job.get("name") == name:
                last_failed_job = {}
        else:
            print("Failed to find button.")
            send_discord_ping(webhook_url, name, "Failed", "Button not visible on screen. Send 'retry' to attempt again.")
            last_failed_job = {"name": name, "url": url, "webhook_url": webhook_url, "join_delay": join_delay, "screenshot_delay": screenshot_delay, "enable_recording": enable_recording, "recording_duration": recording_duration}
    except Exception as e:
        print(f"Join Exception: {e}")
        send_discord_ping(webhook_url, name, "Failed", f"Error: {str(e)}. Send 'retry' to attempt again.")
        last_failed_job = {"name": name, "url": url, "webhook_url": webhook_url, "join_delay": join_delay, "screenshot_delay": screenshot_delay, "enable_recording": enable_recording, "recording_duration": recording_duration}

# ==========================================
# 5. REMOTE LISTENER THREAD
# ==========================================

def ntfy_listener(topic, webhook_url):
    global last_action_time
    global last_failed_job
    if not topic or not webhook_url: return
    
    url = f"https://ntfy.sh/{topic}/json"
    print(f"Connecting to remote listener at {url}...")
    while True:
        try:
            response = requests.get(url, stream=True, timeout=86400)
            for line in response.iter_lines():
                if line:
                    event_data = json.loads(line.decode('utf-8'))
                    if event_data.get("event") == "message":
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
                                delta = next_run - datetime.now()
                                hours, remainder = divmod(delta.seconds, 3600)
                                minutes, _ = divmod(remainder, 60)
                                time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                                send_discord_ping(webhook_url, "System Ping", "Info", f"Next lecture is scheduled for **{next_run.strftime('%A at %H:%M')}** (in {time_str}).")
                            else:
                                send_discord_ping(webhook_url, "System Ping", "Info", "There are no lectures currently scheduled.")
                        elif msg == "retry":
                            if last_failed_job:
                                send_discord_ping(webhook_url, "Manual Retry", "Info", f"Attempting to rejoin: {last_failed_job['name']}...")
                                threading.Thread(target=execute_join, kwargs=last_failed_job, daemon=True).start()
                            else:
                                send_discord_ping(webhook_url, "Manual Retry", "Failed", "There are no recently failed classes to retry.")
                        elif msg in ["stop", "stop record"]:
                            stop_audio_recording(silent=True, webhook_url=webhook_url)
                        else:
                            capture_and_send_screenshot(webhook_url, "Manual Remote Request")
        except Exception as e:
            print(f"Listener dropped connection: {e}. Retrying in 10s...")
            time.sleep(10)

# ==========================================
# 6. DAEMON PROCESSES & SYSTEM CONTROLS
# ==========================================

def check_daemon_status():
    if not os.path.exists(PID_FILE):
        return False, None
    with open(PID_FILE, "r") as f:
        pid = f.read().strip()
    if not pid: return False, None
    output = subprocess.getoutput(f'tasklist /FI "PID eq {pid}"')
    return (pid in output), pid

def kill_daemon(silent=False):
    is_running, pid = check_daemon_status()
    if is_running:
        subprocess.run(f"taskkill /PID {pid} /F", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not silent: console.print(f"[bold red]Daemon (PID: {pid}) terminated.[/bold red]\n")
    else:
        if not silent: console.print("[yellow]Daemon process not found. Cleaning up cache.[/yellow]\n")
    if os.path.exists(PID_FILE): os.remove(PID_FILE)

def run_daemon():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
        
    data = load_data()
    join_delay = data.get("join_delay", 15)
    screenshot_delay = data.get("screenshot_delay", 300)
    enable_recording = data.get("enable_recording", False)
    recording_duration = data.get("recording_duration", 90)
    webhook = data.get("webhook_url", "")
    topic = data.get("remote_topic", "")
    
    threading.Thread(target=ntfy_listener, args=(topic, webhook), daemon=True).start()
    
    for lec in data.get("lectures", []):
        for day in lec["days"]:
            day_func = getattr(schedule.every(), day)
            day_func.at(lec["time"]).do(execute_join, name=lec["name"], url=lec["url"], webhook_url=webhook, join_delay=join_delay, screenshot_delay=screenshot_delay, enable_recording=enable_recording, recording_duration=recording_duration)
    
    print("Schedules loaded. Entering main waiting loop.")
    while True:
        schedule.run_pending()
        time.sleep(10)

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
        
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            
        if not silent:
            console.print("[bold green]Daemon launched. You can safely close this window.[/bold green]\n")
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
        vbs_content = f'Set WshShell = CreateObject("WScript.Shell")\n'
        if getattr(sys, 'frozen', False):
            vbs_content += f'WshShell.Run """{exe_path}"" --background", 0, False'
        else:
            vbs_content += f'WshShell.Run "python ""{exe_path}"" --background", 0, False'
            
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
    
    current_webhook = data.get('webhook_url', 'Not Set')
    hidden_webhook = f"{current_webhook[:35]}..." if len(current_webhook) > 35 else current_webhook
    rec_status = "[green]Enabled[/green]" if data.get('enable_recording') else "[red]Disabled[/red]"
    
    console.print(f"Discord Webhook: [cyan]{hidden_webhook}[/cyan]")
    console.print(f"App Load Wait Delay: [cyan]{data.get('join_delay', 15)} seconds[/cyan]")
    console.print(f"Screenshot Timer: [cyan]{data.get('screenshot_delay', 300)} seconds[/cyan]")
    console.print(f"Remote Cooldown: [cyan]{data.get('remote_cooldown', 60)} seconds[/cyan]")
    console.print(f"Auto-Record Audio: {rec_status} [cyan]({data.get('recording_duration', 90)} mins)[/cyan]")
    console.print(f"Remote Trigger URL: [cyan]https://ntfy.sh/{data.get('remote_topic')}[/cyan]\n")
    
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
    if Confirm.ask("[yellow]Toggle Auto-Recording of Class Audio?[/yellow]"):
        data["enable_recording"] = not data.get("enable_recording", False)
        if data["enable_recording"]:
            data["recording_duration"] = IntPrompt.ask("[green]Default recording length (in minutes)[/green]", default=data.get('recording_duration', 90))
        changed = True
        
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
        if all(d in valid_days for d in days): break
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
    
    new_id = str(max([int(l["id"]) for l in data["lectures"]] + [0]) + 1)
    data["lectures"].append({"id": new_id, "name": name, "days": days, "time": time_str, "url": url})
    save_data(data)
    console.print(f"\n[bold green]Saved {name}. Link optimized.[/bold green]")
    auto_refresh_daemon()

def remove_lecture():
    list_lectures()
    data = load_data()
    if not data["lectures"]: return
    target_id = Prompt.ask("[green]Enter the ID of the lecture to remove (or 'q' to cancel)[/green]")
    if target_id.lower() == 'q': return
    
    original_len = len(data["lectures"])
    data["lectures"] = [l for l in data["lectures"] if l["id"] != target_id]
    if len(data["lectures"]) < original_len:
        save_data(data)
        console.print("[bold green]Lecture removed.[/bold green]")
        auto_refresh_daemon()
    else: console.print("[bold red]ID not found.[/bold red]\n")

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
        table.add_row(lec["id"], lec["name"], ", ".join([d.capitalize()[:3] for d in lec["days"]]), lec["time"])
    console.print("\n", table, "\n")

def show_help():
    help_text = """
    [bold cyan]How to use Remote Commands:[/bold cyan]
    Go to Option 5 (Settings) and copy your unique ntfy.sh URL. Open it on your phone.
    - Send [bold green]ping[/bold green]: Returns the time remaining until your next scheduled class.
    - Send [bold green]retry[/bold green]: Forces the app to attempt joining the last class that failed.
    - Send [bold green]stop[/bold green]: Manually ends and saves an ongoing audio recording early.
    - Send [bold green]anything else[/bold green]: Forces the app to take a screenshot and send it to Discord.
    
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
    console.print(Panel(help_text, title="🧀 Online Cheese Help Guide", border_style="blue"))

# ==========================================
# 8. MAIN MENU
# ==========================================

def main_menu():
    dev_mode = False
    while True:
        console.clear()
        console.print(Panel.fit("[bold yellow]🧀 Online Cheese[/bold yellow]", border_style="yellow"))
        
        is_running, _ = check_daemon_status()
        status_color = "bold green" if is_running else "dim red"
        status_text = "Running" if is_running else "Stopped"
        
        menu = Table(show_header=False, box=None)
        
        menu.add_row("[bold cyan]━━━ Schedule & Links ━━━[/bold cyan]", "")
        menu.add_row("[cyan]1.[/cyan]", "View Schedule")
        menu.add_row("[cyan]2.[/cyan]", "Add a Lecture")
        menu.add_row("[cyan]3.[/cyan]", "Remove a Lecture")
        menu.add_row("[cyan]4.[/cyan]", "Convert a Teams Link")
        
        menu.add_row("", "")
        
        menu.add_row("[bold yellow]━━━ Configuration ━━━[/bold yellow]", "")
        menu.add_row("[yellow]5.[/yellow]", "Settings (Webhook, Audio Recording, Timers)")
        
        menu.add_row("", "")
        
        menu.add_row("[bold magenta]━━━ System & Daemon ━━━[/bold magenta]", "")
        menu.add_row("[magenta]6.[/magenta]", f"Start Background Daemon [{status_color}]({status_text})[/{status_color}]")
        menu.add_row("[magenta]7.[/magenta]", "Check Daemon Status")
        menu.add_row("[magenta]8.[/magenta]", "Toggle Windows Startup")
        menu.add_row("[magenta]9.[/magenta]", "Stop Active Audio Recording")
        menu.add_row("[red]10.[/red]", "Kill Background Daemon")
        
        menu.add_row("", "")
        
        menu.add_row("[bold blue]━━━ Application ━━━[/bold blue]", "")
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
        elif choice == "1": list_lectures()
        elif choice == "2": add_lecture()
        elif choice == "3": remove_lecture()
        elif choice == "4": link_converter_tool()
        elif choice == "5": manage_settings()
        elif choice == "6": 
            spawn_background_daemon()
        elif choice == "7": 
            is_run, pid = check_daemon_status()
            if is_run: console.print(f"[bold green]Daemon is running (PID: {pid}).[/bold green]\n")
            else: console.print("[bold red]Daemon is not running.[/bold red]\n")
        elif choice == "8": toggle_startup()
        elif choice == "9": stop_audio_recording()
        elif choice == "10": kill_daemon()
        elif choice == "11": show_help()
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
        elif choice == "0": sys.exit(0)
        else:
            continue
            
        console.input("\n[dim white]Press Enter to return to the menu...[/dim white]")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--background":
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
        try: main_menu()
        except KeyboardInterrupt: sys.exit(0)