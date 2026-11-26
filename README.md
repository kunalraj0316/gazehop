# GazeHop

Switch keyboard focus to the monitor you are looking at, using an ordinary webcam.

## Requirements

- Windows
- Python 3.11
- A webcam
- Two or more monitors

## Install and run

1. Double-click `setup.bat` once.
2. Double-click `run.bat`. A round tray icon appears: grey is off, green is on.
3. Press `Ctrl+Alt+L`. The first time, it calibrates: look at the dot on each screen.

The first use downloads the MediaPipe face model from Google.

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+L` | Turn on or off |
| `Ctrl+Alt+C` | Calibrate again |

Quit from the tray icon's right-click menu. To check the camera, run `camtest.bat`.

## Settings (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `toggle_hotkey` | `ctrl+alt+l` | On/off hotkey |
| `calibrate_hotkey` | `ctrl+alt+c` | Calibration hotkey |
| `camera_index` | `0` | Which camera to try first |
| `dwell_ms` | `500` | How long you must look before focus moves |
| `switch_margin` | `0.85` | Lower values need a clearer look to switch |
| `typing_pause_ms` | `2000` | No switching until this long after a key press |
| `move_cursor` | `false` | Also move the mouse pointer |

## Privacy

Webcam frames are processed in memory and are not saved or sent anywhere by this code.
The camera is released when GazeHop is off.

To pause switching while you type, GazeHop checks for key presses while it is on. It keeps
only the time of the last key press, never which key.

## Limitations

- It relies on head direction as well as eye movement.
- Recalibrate if you move your screens or camera.
- It turns itself off when the screen layout changes. Turn it on again; it recalibrates if needed.
- The typing pause may not detect typing in windows running as administrator.
- Focus can still move to the wrong window. Check before typing anything sensitive.

## Disclaimer

An independent project, not affiliated with any commercial product.
Provided as is, without warranty. See [LICENSE](LICENSE).
