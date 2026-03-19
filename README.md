# 🛡️ SportShield AI
### Digital Asset Protection for Sports Media

> Built for Google Solution Challenge 2026 | Problem Statement: Digital Asset Protection

![Python](https://img.shields.io/badge/Python-3.14-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-green)
![Firebase](https://img.shields.io/badge/Firebase-Firestore-orange)
![Gemini](https://img.shields.io/badge/Google-Gemini%20AI-red)
![SDG](https://img.shields.io/badge/UN%20SDG-8%20%7C%209%20%7C%2016-purple)

---

## 🌍 Problem Statement

Sports organizations generate massive volumes of high-value digital media that rapidly
scatter across global platforms. This vast visibility gap leaves proprietary content
highly vulnerable to widespread digital misappropriation, unauthorized redistribution,
and intellectual property violations.

**The scale of the problem:**
- $28B+ lost annually to sports media piracy
- 500M+ sports images shared without authorization every year
- Organizations have no scalable way to detect violations in real-time

---

## 💡 Our Solution

SportShield AI is a scalable digital asset protection platform that uses AI-powered
fingerprinting to detect, track and flag unauthorized use of official sports media.

### How it works
```
Official Asset → Triple-Hash Fingerprint → Asset Registry (Firebase)
                                                    ↓
Suspect Image → Hash Comparison + Gemini AI → Similarity Score → Alert
                                                    ↓
                              Violation Log → PDF Report → Rights Holder
```

---

## ✨ Key Features

| Feature | Description |
|--------|-------------|
| 🔍 Triple-Hash Detection | pHash + dHash + aHash for 95%+ accuracy |
| 🤖 Gemini AI Analysis | Google's vision AI analyzes and compares images |
| ☁️ Firebase Backend | Real-time cloud storage via Google Firestore |
| 📊 Live Dashboard | Violation trends and most targeted assets |
| 📄 PDF Reports | Legal-grade evidence export with timestamps |
| 🔬 EXIF Metadata | Camera, location and date forensic analysis |
| 🌍 SDG Aligned | Supports UN SDGs 8, 9 and 16 |

---

## 🛠️ Tech Stack

### Google Technologies
- **Google Gemini AI** (`gemini-2.0-flash`) — Image analysis and comparison
- **Firebase Firestore** — Cloud database for assets and violations
- **Google Cloud** — Project infrastructure

### Core Technologies
- **Python 3.14** + **Flask** — Backend framework
- **imagehash** — Perceptual hashing (pHash, dHash, aHash)
- **SQLite** — Local database (dual-write with Firebase)
- **ReportLab** — PDF report generation
- **Matplotlib** — Analytics charts
- **exifread** — Image metadata extraction

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Google Cloud account
- Firebase project
- Gemini API key

### Installation
```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/sportshield-ai.git
cd sportshield-ai

# Install dependencies
pip install flask pillow imagehash requests python-dotenv exifread
pip install google-generativeai firebase-admin reportlab matplotlib

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys
```

### Environment Variables
```env
SECRET_KEY=your_secret_key
DATABASE=database/sportshield.db
UPLOAD_FOLDER=uploads
GEMINI_API_KEY=your_gemini_api_key
FIREBASE_KEY=firebase-key.json
```

### Run the app
```bash
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

---



## 🌍 UN SDG Alignment

| SDG | Goal | How SportShield Helps |
|-----|------|----------------------|
| **SDG 16** | Peace, Justice & Strong Institutions | Provides legal-grade IP violation evidence |
| **SDG 9** | Industry, Innovation & Infrastructure | AI-powered scalable detection infrastructure |
| **SDG 8** | Decent Work & Economic Growth | Protects revenue streams of content creators |

---

## 👥 Team

Built by a 4-member IT Engineering team for Google Solution Challenge 2026.

| Role | Responsibility |
|------|---------------|
| Team Lead | Backend + AI integration |
| Frontend Dev | UI/UX + Dashboard |
| Cloud Engineer | Firebase + deployment |
| Research | SDG alignment + documentation |

---

## 📁 Project Structure
```
sportshield-ai/
├── app.py                 # Main Flask application
├── .env                   # Environment variables
├── firebase-key.json      # Firebase credentials
├── database/
│   ├── db.py             # SQLite database
│   └── firebase_db.py    # Firebase Firestore
├── routes/
│   ├── assets.py         # Asset registration
│   ├── scan.py           # Image scanning
│   ├── gemini.py         # Gemini AI integration
│   ├── report.py         # PDF generation
│   └── dashboard.py      # Analytics
├── templates/
│   ├── base.html         # Base layout
│   ├── index.html        # Dashboard
│   ├── assets.html       # Asset registry
│   ├── scan.html         # Scan page
│   ├── violations.html   # Violations log
│   └── sdg.html          # SDG alignment
└── uploads/              # Stored images
```

---

## 📄 License

MIT License — Built for Google Solution Challenge 2026

---

*SportShield AI — Protecting the integrity of digital sports media*
