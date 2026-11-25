# ClipForge

> **Local-first AI Video Clipping & Repurposing Studio**  
> Turn long-form video recordings and podcasts into high-engagement 9:16 vertical video clips with intelligent speaker framing and synchronized animated captions.

---

## Features

- **Adaptive 9:16 Portrait Framing**:
  - **Smart Face Crop**: Automatically tracks and centers single speakers.
  - **Split Gaming**: Places facecam on top and 16:9 gameplay footage on bottom.
  - **Split 2-Speaker**: Tracks dual interview speakers and stacks them vertically.
  - **Cinema Clean**: Letterboxed portrait layout preserving full cinematic wide framing.
  - **Blur Fill**: Centered landscape footage over a stylized blurred background.
  - **Auto Hybrid**: Automatically detects video type and applies the optimal framing.
- **Word-Level Subtitles**:
  - Synchronized, word-level highlighted subtitle animations.
  - Multiple aesthetic presets: **Glow Accent**, **Cyber Neon**, **Fire Gradient**, **Clean Cinema**, **Viral Pop**, and more.
  - Automatic preset matching based on content genre.
- **Dynamic Clip Discovery**:
  - Speech energy and highlight analysis.
  - Multi-clip generation: generate top highlights or extract all potential clips.
- **Local & Private by Default**:
  - Deterministic FFmpeg media processing pipeline.
  - Local faster-whisper speech recognition with zero cloud lock-in.
  - Optional Gemini API integration for semantic clip scoring.

---

## Windows Installation Guide

ClipForge runs natively on **Windows 10** and **Windows 11** (64-bit).

### 1. Prerequisites

Ensure you have the following installed on your Windows machine:

1. **Git for Windows**:
   - Download from [git-scm.com](https://git-scm.com/download/win).
2. **Node.js (v18 or v20 LTS)**:
   - Download from [nodejs.org](https://nodejs.org/).
3. **Python (3.10 to 3.12)**:
   - Download from [python.org](https://www.python.org/downloads/windows/).
   - Ensure the checkbox **"Add python.exe to PATH"** is selected during installation.
4. **FFmpeg**:
   - Via Windows Terminal (winget):
     ```powershell
     winget install Gyan.FFmpeg
     ```
   - Alternatively, download essentials build from [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/) and add its `bin` folder to your Windows System `PATH`.
   - Verify installation in PowerShell:
     ```powershell
     ffmpeg -version
     ffprobe -version
     ```

---

### 2. Clone the Repository

```powershell
git clone https://github.com/frekzy06/clipforge.git
cd clipforge
```

---

### 3. Backend Setup (FastAPI & Python)

From the project root directory:

```powershell
# Navigate to the backend directory
cd services\api

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
.\venv\Scripts\Activate.ps1
```

> *Tip*: If PowerShell reports script execution errors, run:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

Install Python dependencies:

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

*(Optional)* Configure API Keys:
If using Gemini API for cloud clip discovery, copy `.env.example`:
```powershell
copy .env.example .env
```
Leave it blank if you want 100% offline local processing.

---

### 4. Frontend Setup (Next.js)

Open a second PowerShell terminal:

```powershell
cd apps\web
npm install
```

---

### 5. Running ClipForge

#### Terminal 1 — Backend (FastAPI):
```powershell
cd services\api
.\venv\Scripts\Activate.ps1
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

#### Terminal 2 — Frontend (Next.js):
```powershell
cd apps\web
npm run dev
```

Open your browser to: **http://localhost:3000**

---

## Project Structure

```text
clipforge/
├── apps/
│   └── web/                   # Next.js frontend (React, TailwindCSS, Video Player)
├── services/
│   └── api/                   # FastAPI backend
│       ├── main.py            # API routing and export orchestration
│       ├── discovery.py       # Highlight discovery
│       ├── layouts.py         # 9:16 layout math and FFmpeg filtergraphs
│       ├── transcription.py   # Word-level speech recognition
│       └── requirements.txt
├── scripts/                   # Font and utility scripts
├── tests/                     # Test suite (FastAPI, Layouts, Discovery)
├── .gitignore                 # Safe rules preventing key and media leaks
└── README.md                  # Setup documentation
```

---

## Running Tests

To run the automated test suite:

```powershell
pytest tests/ -q
```

---

## License

MIT License
