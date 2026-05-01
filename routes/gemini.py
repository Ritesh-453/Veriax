import os
import base64
import io
from PIL import Image


def pil_to_base64(img):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG')
    return base64.b64encode(buf.getvalue()).decode()


def _gemini_vision(contents):
    """
    Make a Gemini API call using google-generativeai SDK.
    Model: gemini-1.5-flash (fast, free tier available)
    Get your free API key at: https://aistudio.google.com/app/apikey
    Set it as GEMINI_API_KEY in your .env file.
    """
    import google.generativeai as genai

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in .env — get a free key at aistudio.google.com")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(contents)
    return response.text


def analyze_image(image_path):
    """
    Analyze a single image using Gemini Vision.
    Returns a forensics-style analysis string.
    """
    try:
        img = Image.open(image_path)
        img_b64 = pil_to_base64(img)

        import google.generativeai as genai
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set in .env")

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # Build image part from base64
        image_part = {
            "mime_type": "image/jpeg",
            "data": img_b64
        }

        prompt = """You are a sports media forensics expert.
Analyze this image and provide:
1. What sport or organization this relates to
2. Whether this is official/professional media
3. Any logos, watermarks, or copyright indicators
4. Risk: likely to be misused or pirated?
5. Verdict: PROTECTED ASSET or GENERIC CONTENT
Max 6 lines."""

        response = model.generate_content([
            {"mime_type": "image/jpeg", "data": img_b64},
            prompt
        ])
        return response.text

    except Exception as e:
        return f"AI Error: {type(e).__name__}: {str(e)}"


def compare_images_ai(image_path1, image_path2):
    """
    Compare two images using Gemini Vision for copyright forensics.
    Returns a verdict string.
    """
    try:
        img1 = Image.open(image_path1)
        img2 = Image.open(image_path2)

        import google.generativeai as genai
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set in .env")

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = """Compare these two images as a forensics expert.
VERDICT: [INFRINGEMENT / LIKELY INFRINGEMENT / NO INFRINGEMENT]
CONFIDENCE: [HIGH / MEDIUM / LOW]
REASON: [one line]"""

        response = model.generate_content([
            {"mime_type": "image/jpeg", "data": pil_to_base64(img1)},
            {"mime_type": "image/jpeg", "data": pil_to_base64(img2)},
            prompt
        ])
        return response.text

    except Exception as e:
        return f"AI Error: {type(e).__name__}: {str(e)}"