import os
import base64
import io
from PIL import Image


def pil_to_base64(img):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG')
    return base64.b64encode(buf.getvalue()).decode()


def _groq_vision(messages):
    """Make a Groq API call with vision support."""
    import requests
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        raise ValueError("GROQ_API_KEY not set in .env")

    response = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        },
        json={
            'model': 'meta-llama/llama-4-scout-17b-16e-instruct',
            'messages': messages,
            'max_tokens': 300
        },
        timeout=30
    )
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']


def analyze_image(image_path):
    try:
        img = Image.open(image_path)
        img_b64 = pil_to_base64(img)

        result = _groq_vision([{
            'role': 'user',
            'content': [
                {
                    'type': 'image_url',
                    'image_url': {
                        'url': f'data:image/jpeg;base64,{img_b64}'
                    }
                },
                {
                    'type': 'text',
                    'text': """You are a sports media forensics expert.
Analyze this image and provide:
1. What sport or organization this relates to
2. Whether this is official/professional media
3. Any logos, watermarks, or copyright indicators
4. Risk: likely to be misused or pirated?
5. Verdict: PROTECTED ASSET or GENERIC CONTENT
Max 6 lines."""
                }
            ]
        }])
        return result

    except Exception as e:
        return f"AI Error: {type(e).__name__}: {str(e)}"


def compare_images_ai(image_path1, image_path2):
    try:
        img1 = Image.open(image_path1)
        img2 = Image.open(image_path2)

        result = _groq_vision([{
            'role': 'user',
            'content': [
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{pil_to_base64(img1)}'}
                },
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{pil_to_base64(img2)}'}
                },
                {
                    'type': 'text',
                    'text': """Compare these two images as a forensics expert.
VERDICT: [INFRINGEMENT / LIKELY INFRINGEMENT / NO INFRINGEMENT]
CONFIDENCE: [HIGH / MEDIUM / LOW]
REASON: [one line]"""
                }
            ]
        }])
        return result

    except Exception as e:
        return f"AI Error: {type(e).__name__}: {str(e)}"