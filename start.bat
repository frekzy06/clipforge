@echo off
set "PATH=C:\Users\frekzy_06\AppData\Local\Programs\ffmpeg-9.1-essentials_build\bin;C:\Users\frekzy_06\AppData\Local\Programs\python311;C:\Users\frekzy_06\AppData\Local\Programs\python311\Scripts;C:\Users\frekzy_06\AppData\Local\Programs\nodejs;%PATH%"

echo Starting ClipForge Backend...
start "ClipForge Backend" cmd /k "cd /d C:\Users\frekzy_06\Desktop\Projects\clipforge\services\api && python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload"

echo Starting ClipForge Frontend...
start "ClipForge Frontend" cmd /k "cd /d C:\Users\frekzy_06\Desktop\Projects\clipforge\apps\web && npm run dev"

echo ClipForge is launching!
echo Backend:  http://127.0.0.1:8000
echo Frontend: http://localhost:3000
