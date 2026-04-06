# 🧀 Online Cheese

A lightweight, headless automation tool that automatically joins your Microsoft Teams lectures so you never miss a class. 

## ✨ Features
* **Silent Background Daemon:** Runs invisibly in the background and monitors your system schedule.
* **Smart Link Converter:** Automatically strips the Microsoft browser launcher out of Teams links.
* **Computer Vision Bypass:** Visually detects and clicks the "Join Now" button to bypass lobbies.
* **Remote Phone Triggers:** Link a private 
tfy.sh URL to ping the daemon from your phone to check your schedule or force an instant screenshot.
* **Discord Integration:** Sends rich embed logs and screenshot verifications directly to your private Discord server.

## 🚀 How to Use
1. Download online_cheese.exe from the Releases tab.
2. Run the executable to open the interactive terminal menu.
3. Go to **Settings** to configure your Discord Webhook URL.
4. Add your class links and times.
5. Hit **Start Background Daemon** and close the window!

## 🛠️ Building from Source
If you prefer to run the raw Python script or compile it yourself:
\\\ash
pip install schedule pyautogui pygetwindow pywin32 Pillow requests rich opencv-python
python -m PyInstaller --clean --onefile --hidden-import=pygetwindow --hidden-import=pywin32 --add-data "join_now.png;." online_cheese.py
\\\
*(Note: You must have a tight, cropped screenshot of the Teams join button named join_now.png in the root directory).*
