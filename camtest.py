"""Camera + face-tracking check. Writes cam_report.txt next to this file."""
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gazehop  # noqa: E402

lines = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    lines.append(s)


try:
    cap = gazehop.open_camera(gazehop.load_config()["camera_index"])
    if cap is None:
        log("RESULT: camera could NOT be opened from this session")
    else:
        log("camera opened:", int(cap.get(3)), "x", int(cap.get(4)))
        cap.release()
        t = gazehop.Tracker(gazehop.load_config(), type("A", (), {
            "notify": lambda s, m: log("notify", m), "set_state": lambda s, v: None})())
        t._open()
        t0 = time.time()
        n = faces = 0
        feats = []
        while time.time() - t0 < 6:
            ok, fr = t.cap.read()
            if not ok:
                continue
            n += 1
            img = t.mp.Image(image_format=t.mp.ImageFormat.SRGB,
                             data=cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            res = t.landmarker.detect_for_video(img, int((time.time() - t0) * 1000))
            if res.face_landmarks:
                faces += 1
                feats.append(gazehop.features(res.face_landmarks[0]))
        log("frames:", n, "fps:", round(n / 6, 1), "face found in:", faces)
        if feats:
            f = np.array(feats)
            log("features [yaw pitch iris_x iris_y] mean:", f.mean(0).round(3))
            log("features std (lower = steadier):", f.std(0).round(4))
        t._close()
        log("RESULT: OK" if faces > n * 0.8 else "RESULT: face rarely detected - check lighting/position")
except Exception as e:
    log("RESULT: error:", repr(e))

with open(os.path.join(HERE, "cam_report.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
