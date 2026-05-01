import os
import base64
import io
import hashlib
import requests
from PIL import Image

# Grok (xAI) — used as backend, displayed as "Gemini AI Analysis" in UI
GROK_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROK_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# In-memory cache: image hash → analysis result
_analysis_cache = {}


def pil_to_base64(img):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=75)
    return base64.b64encode(buf.getvalue()).decode()


def _image_hash(image_path):
    try:
        with open(image_path, 'rb') as f:
            return hashlib.md5(f.read(8192)).hexdigest()
    except:
        return None


def _call_grok(prompt, img_b64):
    """
    Call Grok (xAI) API with vision support.
    Uses XAI_API_KEY from .env
    """
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        raise ValueError("XAI_API_KEY not set in .env")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}",
                            "detail": "low"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        "max_tokens": 300,
        "temperature": 0.2
    }

    try:
        response = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=30)
        print(f"[Grok] Status: {response.status_code}")
        print(f"[Grok] Response: {response.text[:500]}")
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[Grok] API error: {e}")
        return None


def analyze_image(image_path):
    """
    Analyze a single image using Grok Vision.
    Displayed as 'Gemini AI Analysis' in the UI.
    Cached — same image won't be re-analyzed on repeated scans.
    """
    cache_key = _image_hash(image_path)
    if cache_key and cache_key in _analysis_cache:
        print(f"[Grok] Cache hit for {image_path}")
        return _analysis_cache[cache_key]

    try:
        img = Image.open(image_path)
        img.thumbnail((512, 512))
        img_b64 = pil_to_base64(img)

        prompt = """You are a sports media forensics expert.
Analyze this image and provide:
1. What sport or organization this relates to
2. Whether this is official/professional media
3. Any logos, watermarks, or copyright indicators
4. Risk: likely to be misused or pirated?
5. Verdict: PROTECTED ASSET or GENERIC CONTENT
Max 6 lines."""

        result = _call_grok(prompt, img_b64)

        if result is None:
            result = "⚠ AI analysis unavailable. Please try again."

        if cache_key:
            _analysis_cache[cache_key] = result

        return result

    except Exception as e:
        return f"AI Error: {type(e).__name__}: {str(e)}"


def compare_images_ai(image_path1, image_path2):
    """
    Compare two images using Grok Vision for copyright forensics.
    """
    try:
        img1 = Image.open(image_path1)
        img2 = Image.open(image_path2)
        img1.thumbnail((512, 512))
        img2.thumbnail((512, 512))

        img1_b64 = pil_to_base64(img1)
        img2_b64 = pil_to_base64(img2)

        api_key = os.getenv('GROQ_API_KEY')
        if not api_key:
            raise ValueError("XAI_API_KEY not set in .env")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": GROK_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img1_b64}", "detail": "low"}
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img2_b64}", "detail": "low"}
                        },
                        {
                            "type": "text",
                            "text": """Compare these two images as a forensics expert.
VERDICT: [INFRINGEMENT / LIKELY INFRINGEMENT / NO INFRINGEMENT]
CONFIDENCE: [HIGH / MEDIUM / LOW]
REASON: [one line]"""
                        }
                    ]
                }
            ],
            "max_tokens": 150,
            "temperature": 0.1
        }

        response = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        result = data["choices"][0]["message"]["content"]
        return result if result else "⚠ AI analysis unavailable. Try again."

    except Exception as e:
        return f"AI Error: {type(e).__name__}: {str(e)}"