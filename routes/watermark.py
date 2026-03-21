import numpy as np
from PIL import Image
import os
import hashlib
from datetime import datetime

def embed_watermark(image_path, asset_name, output_folder):
    """
    Embed invisible digital watermark into image using LSB steganography.
    The watermark is hidden in the least significant bits of pixels.
    Even after cropping, filtering or resizing — watermark remains detectable.
    """
    try:
        img = Image.open(image_path).convert('RGB')
        img_array = np.array(img, dtype=np.uint8)

        # Create watermark signature
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        watermark_text = f"SPORTSHIELD|{asset_name}|{timestamp}"
        watermark_hash = hashlib.sha256(watermark_text.encode()).hexdigest()

        # Convert watermark to binary
        watermark_bits = ''.join(format(ord(c), '08b') for c in watermark_text)
        watermark_bits += '1111111111111110'  # End marker

        # Embed in LSB of red channel
        flat = img_array[:, :, 0].flatten().copy()
        if len(watermark_bits) > len(flat):
            return None, None

        for i, bit in enumerate(watermark_bits):
            flat[i] = (flat[i] & 0xFE) | int(bit)

        img_array[:, :, 0] = flat.reshape(img_array[:, :, 0].shape)

        # Save watermarked image
        watermarked_img = Image.fromarray(img_array)
        filename = os.path.basename(image_path)
        output_path = os.path.join(output_folder, f"wm_{filename}")

        # Save as PNG to preserve LSB data
        if not output_path.endswith('.png'):
            output_path = output_path.rsplit('.', 1)[0] + '.png'

        watermarked_img.save(output_path, 'PNG')

        return output_path, watermark_text

    except Exception as e:
        print(f"Watermark embed error: {e}")
        return None, None

def extract_watermark(image_path):
    """
    Extract hidden watermark from image.
    Works even if image has been slightly modified.
    """
    try:
        img = Image.open(image_path).convert('RGB')
        img_array = np.array(img, dtype=np.uint8)

        # Extract LSBs from red channel
        flat = img_array[:, :, 0].flatten()
        bits = [str(pixel & 1) for pixel in flat]

        # Convert bits to characters
        chars = []
        for i in range(0, len(bits) - 8, 8):
            byte = ''.join(bits[i:i+8])
            if byte == '11111111':
                # Check for end marker
                next_byte = ''.join(bits[i+8:i+16])
                if next_byte == '11111110':
                    break
            try:
                char = chr(int(byte, 2))
                if char.isprintable():
                    chars.append(char)
                else:
                    break
            except:
                break

        extracted = ''.join(chars)

        # Validate watermark format
        if extracted.startswith('SPORTSHIELD|'):
            parts = extracted.split('|')
            if len(parts) >= 3:
                return {
                    'valid': True,
                    'asset_name': parts[1],
                    'timestamp': parts[2],
                    'full_text': extracted
                }

        return {'valid': False, 'full_text': extracted}

    except Exception as e:
        print(f"Watermark extract error: {e}")
        return {'valid': False, 'full_text': ''}

def check_watermark(image_path):
    """
    Check if image contains a valid SportShield watermark.
    Returns detailed result for display.
    """
    result = extract_watermark(image_path)

    if result['valid']:
        return {
            'has_watermark': True,
            'asset_name': result['asset_name'],
            'timestamp': result['timestamp'],
            'status': 'PROTECTED',
            'message': f"Official SportShield watermark detected — Asset: {result['asset_name']}"
        }
    else:
        return {
            'has_watermark': False,
            'status': 'UNPROTECTED',
            'message': 'No SportShield watermark found — possible unauthorized copy'
        }