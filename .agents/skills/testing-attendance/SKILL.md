---
name: testing-attendance
description: Run and test the Face Attendance System (FastAPI + InsightFace) locally end-to-end. Use when verifying recognition, enrollment, gallery-cache, or attendance UI changes.
---

# Testing the Face Attendance System

## Run the app
A Python venv with deps already lives in the repo (`.venv/`). Start the server from the repo root:
```bash
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8013
```
Open `http://127.0.0.1:8013/`. First recognition request lazily loads the InsightFace model (slow once), then logs `Loaded embedding gallery (N students).`

No secrets/credentials are required. No CI is configured on the repo.

## Architecture quick map
- `backend/services/recognizer.py` — `FaceRecognizer`, embedding store, and an in-memory `{roll_no: embedding}` gallery cache (`load_gallery()` / `invalidate_gallery()`). `match_against_all` reads the cache for the default `EMBED_DIR`.
- `backend/routers/students.py` — enroll / list / remove. Calls `invalidate_gallery()` after any embedding change.
- `backend/routers/attendance.py` — `POST /api/attendance/process` (photo + date).
- `frontend/templates/index.html` + `frontend/static/app.js` — single-page UI. Tabs wired in `DOMContentLoaded` (app.js bottom). NOTE: app.js may reference tabs (e.g. Video, Metrics) that have no HTML; init functions must null-guard or the whole app crashes on load.
- Data on disk: embeddings `data/embeddings/<roll>.npy`, student photos `data/student_photos/<roll>/*.jpg`, class uploads `data/uploads/class_<date>_*.jpg`.

## Useful API checks (curl)
```bash
curl -s http://127.0.0.1:8013/health
curl -s http://127.0.0.1:8013/api/students | python3 -m json.tool
curl -s -X POST -F "photo=@<class.jpg>" -F "date=2098-01-01" http://127.0.0.1:8013/api/attendance/process | python3 -m json.tool
curl -s -X POST -F "roll_no=NEW01" -F "name=Test" -F "photos=@<face.jpg>" http://127.0.0.1:8013/api/students/enroll
# cleanup test data:
curl -s -X DELETE "http://127.0.0.1:8013/api/students/NEW01?keep_history=false"
curl -s -X DELETE http://127.0.0.1:8013/api/attendance/2098-01-01
```
Use a far-future date (e.g. `2098-01-01`) for test attendance so you don't pollute real records, then reset it.

## Adversarial test: gallery cache invalidation
The strongest proof that enroll/remove correctly invalidate the in-memory gallery:
1. Pick a class photo where some detected face matches no enrolled student (verify with the detector + `match_against_all`; an unmatched face scores ~0).
2. Crop that face (with ~40% margin) to a jpg.
3. Process the photo once → that person is Absent/unrecognized.
4. Enroll a NEW student using the crop.
5. Re-process the SAME photo **without restarting the server** → the new student should now be Present at high confidence (~0.9+).
If invalidation were broken, the new student would stay Absent until restart (stale cached gallery).

## UI testing tips
- Navigate via the left sidebar (Dashboard, Enroll Student, Manage Students, Take Attendance, ...).
- Upload files via the GTK picker: click the upload zone, then `Ctrl+L` and type the absolute path.
- Dashboard stat cards show `—` (and Manage Students 500s / shows nothing) when the JS or `/api/students` is broken; a healthy dashboard shows the student count and a “Take attendance →” link.
- Known minor cosmetic issue (unrelated to recognition): Manage Students “Enrolled” column may show “Invalid Date”.

## Devin Secrets Needed
None.
