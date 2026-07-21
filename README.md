# 🧀 Online Cheese

Online Cheese is a lightweight Windows automation tool that opens and joins scheduled Microsoft Teams lectures.

## Features

- Runs as a silent background daemon and monitors a weekly lecture schedule.
- Opens Teams deep links, finds the meeting window, and forces it into the foreground.
- Retries the visual **Join now** search inside the Teams window instead of searching only once.
- Records system audio through Windows WASAPI loopback and saves finalized M4A files.
- Stores recordings on `E:\Online Cheese Recordings` when that HDD is available (`G:` is the secondary HDD fallback).
- Sends join status and Teams-only verification screenshots to a Discord webhook.
- Accepts `ping`, `retry`, and `stop` commands through a private ntfy topic.

## Usage

1. Run `online_cheese.exe`, or run `python online_cheese.py` from source.
2. Open **Settings** and configure the Discord webhook, delays, and recording options.
3. Add each lecture's Teams link, days, and start time.
4. Choose **Start Background Daemon**. The menu can then be closed safely.

Online Cheese brings Teams to the foreground while joining, so avoid using the mouse during the short join attempt. Only the Teams window is used for verification screenshots. Make sure everyone involved has consented to any recording and that recording is permitted by your institution and local rules.

## Running from source

Python 3.12 is recommended. Install the dependencies:

```powershell
python -m pip install schedule pyautogui pywin32 Pillow requests rich opencv-python PyAudioWPatch==0.2.12.8
python online_cheese.py
```

FFmpeg is used only to encode captured PCM audio as M4A. Place `ffmpeg.exe` beside the script or on `PATH`; if it is absent, Online Cheese downloads a Windows build on the first recording attempt.

The packaged executable stores its configuration, logs, downloaded FFmpeg binary, and fallback recording folder under `%LOCALAPPDATA%\OnlineCheese`. An older `classes.json` is migrated automatically. Source runs remain portable and continue using the repository folder for non-recording app data.

## Upgrading from an older version

You do not need to recreate your lectures or settings. On first launch, the packaged app searches its own folder, its parent folder, common `dist` locations, and conventional Online Cheese folders under Desktop, Downloads, and Documents. It imports the valid legacy `classes.json` containing the most lecture data. If the new version already has data, missing lectures are merged without replacing newer settings; the pre-migration file is backed up first.

Recordings are separate from configuration. On this machine the default is `E:\Online Cheese Recordings`, with `G:` as the second HDD choice and `%LOCALAPPDATA%\OnlineCheese\Recordings` as a temporary fallback if neither drive is connected. The folder can be changed at any time under **Settings**.

## Building the executable

```powershell
python -m pip install pyinstaller
python -m PyInstaller --clean --onefile --add-data "join_now.png;." online_cheese.py
```

The `join_now.png` file must be a tightly cropped screenshot of the Teams **Join now** button. If Teams changes this button's appearance, replace the image and rebuild.
