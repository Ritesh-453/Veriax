from flask import Blueprint, jsonify, request, current_app
from database.db import get_db
from routes.opencv_detector import combined_opencv_score
from routes.deeplearning_detector import fast_mobilenet_similarity
from PIL import Image
import imagehash
import os
import uuid
import hashlib
import secrets
from datetime import datetime
from functools import wraps

api_bp = Blueprint('api', __name__)


# ============================================================
# API KEY AUTH
# ============================================================

def require_api_key(f):
    """
    Decorator — protects any route with API key authentication.
    Pass key as: Header 'X-API-Key: your_key'  OR  ?api_key=your_key
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if not key:
            return jsonify({
                'success': False,
                'error': 'API key required. Pass as X-API-Key header or ?api_key= param.'
            }), 401

        db = get_db(current_app.config['DATABASE'])
        row = db.execute(
            'SELECT * FROM api_keys WHERE api_key = ? AND is_active = 1', (key,)
        ).fetchone()

        if not row:
            db.close()
            return jsonify({'success': False, 'error': 'Invalid or inactive API key.'}), 403

        # Update last used + request count
        db.execute(
            'UPDATE api_keys SET last_used = ?, total_requests = total_requests + 1 WHERE api_key = ?',
            (datetime.now().isoformat(), key)
        )
        db.commit()
        db.close()

        return f(*args, **kwargs)
    return decorated


def compare_all(asset, filepath, upload_folder):
    """Run all 3 detection methods and return combined score"""
    try:
        img = Image.open(filepath)
        scan_hashes = {
            'phash': str(imagehash.phash(img)),
            'dhash': str(imagehash.dhash(img)),
            'ahash': str(imagehash.average_hash(img))
        }

        p1 = imagehash.hex_to_hash(asset['phash'])
        d1 = imagehash.hex_to_hash(asset['dhash'])
        a1 = imagehash.hex_to_hash(asset['ahash'])
        p2 = imagehash.hex_to_hash(scan_hashes['phash'])
        d2 = imagehash.hex_to_hash(scan_hashes['dhash'])
        a2 = imagehash.hex_to_hash(scan_hashes['ahash'])
        hash_score = round(
            max(0, (1-(p1-p2)/64)*100)*0.5 +
            max(0, (1-(d1-d2)/64)*100)*0.3 +
            max(0, (1-(a1-a2)/64)*100)*0.2, 2
        )

        asset_path = os.path.join(upload_folder, asset['filename'])
        opencv_score = combined_opencv_score(asset_path, filepath) \
            if os.path.exists(asset_path) else 0
        dl_score = fast_mobilenet_similarity(asset_path, filepath) \
            if os.path.exists(asset_path) else 0

        if dl_score > 0 and opencv_score > 0:
            final = (hash_score*0.2) + (opencv_score*0.3) + (dl_score*0.5)
        elif opencv_score > 0:
            final = (hash_score*0.3) + (opencv_score*0.7)
        else:
            final = hash_score

        return {
            'hash_score': hash_score,
            'opencv_score': opencv_score,
            'dl_score': dl_score,
            'final_score': round(final, 2)
        }
    except Exception as e:
        return {'hash_score': 0, 'opencv_score': 0, 'dl_score': 0, 'final_score': 0}


# ============================================================
# API KEY MANAGEMENT ENDPOINTS
# ============================================================

@api_bp.route('/api/v1/keys/generate', methods=['POST'])
def generate_api_key():
    """
    Generate a new API key.
    POST JSON: { "name": "My App Name" }
    No auth required for generation (it's like a signup).
    """
    data = request.get_json() or {}
    name = data.get('name', 'Unnamed App')

    new_key = 'ss_' + secrets.token_hex(24)  # e.g. ss_a1b2c3...

    db = get_db(current_app.config['DATABASE'])
    db.execute(
        'INSERT INTO api_keys (api_key, name, created_at) VALUES (?, ?, ?)',
        (new_key, name, datetime.now().isoformat())
    )
    db.commit()
    db.close()

    return jsonify({
        'success': True,
        'api_key': new_key,
        'name': name,
        'message': 'Save this key — it will not be shown again.',
        'usage': 'Pass as header: X-API-Key: ' + new_key
    })


@api_bp.route('/api/v1/keys', methods=['GET'])
def list_api_keys():
    """List all API keys (masked for security)"""
    db = get_db(current_app.config['DATABASE'])
    rows = db.execute(
        'SELECT id, name, is_active, last_used, total_requests, created_at FROM api_keys ORDER BY created_at DESC'
    ).fetchall()
    db.close()

    keys = []
    for r in rows:
        keys.append({
            'id': r['id'],
            'name': r['name'],
            'is_active': bool(r['is_active']),
            'last_used': r['last_used'],
            'total_requests': r['total_requests'],
            'created_at': r['created_at']
        })

    return jsonify({'success': True, 'count': len(keys), 'keys': keys})


@api_bp.route('/api/v1/keys/<int:key_id>/revoke', methods=['POST'])
def revoke_api_key(key_id):
    """Revoke (deactivate) an API key"""
    db = get_db(current_app.config['DATABASE'])
    db.execute('UPDATE api_keys SET is_active = 0 WHERE id = ?', (key_id,))
    db.commit()
    db.close()
    return jsonify({'success': True, 'message': f'Key {key_id} revoked.'})


# ============================================================
# PROTECTED API ENDPOINTS (require API key)
# ============================================================

@api_bp.route('/api/v1/status', methods=['GET'])
def status():
    """Health check — public, no key required"""
    db = get_db(current_app.config['DATABASE'])
    total_assets = db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
    total_violations = db.execute('SELECT COUNT(*) FROM violations').fetchone()[0]
    db.close()
    return jsonify({
        'status': 'operational',
        'version': '2.0.0',
        'name': 'SportShield AI',
        'total_assets': total_assets,
        'total_violations': total_violations,
        'detection_methods': ['pHash', 'dHash', 'aHash', 'SIFT', 'ORB', 'MobileNet'],
        'authentication': 'API key required for scan/register endpoints'
    })


@api_bp.route('/api/v1/assets', methods=['GET'])
@require_api_key
def get_assets():
    """Get all registered assets"""
    db = get_db(current_app.config['DATABASE'])
    assets = db.execute(
        'SELECT id, name, uploaded_at FROM assets ORDER BY uploaded_at DESC'
    ).fetchall()
    db.close()
    return jsonify({
        'success': True,
        'count': len(assets),
        'assets': [dict(a) for a in assets]
    })


@api_bp.route('/api/v1/scan', methods=['POST'])
@require_api_key
def api_scan():
    """
    Scan an image for violations.
    POST multipart/form-data with 'image' file.
    Header: X-API-Key: your_key
    """
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image provided'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename'}), 400

    temp_filename = f"api_scan_{uuid.uuid4().hex}_{file.filename}"
    temp_path = os.path.join(current_app.config['UPLOAD_FOLDER'], temp_filename)
    file.save(temp_path)

    try:
        db = get_db(current_app.config['DATABASE'])
        assets = db.execute('SELECT * FROM assets').fetchall()
        db.close()

        results = []
        violations = []

        for asset in assets:
            scores = compare_all(asset, temp_path, current_app.config['UPLOAD_FOLDER'])
            risk = 'CRITICAL' if scores['final_score'] >= 90 else \
                   'HIGH' if scores['final_score'] >= 70 else \
                   'MEDIUM' if scores['final_score'] >= 50 else 'LOW'

            result = {
                'asset_id': asset['id'],
                'asset_name': asset['name'],
                'scores': scores,
                'risk_level': risk,
                'is_violation': scores['final_score'] > 70
            }
            results.append(result)

            if scores['final_score'] > 70:
                violations.append(result)
                db = get_db(current_app.config['DATABASE'])
                db.execute(
                    'INSERT INTO violations (asset_id, similarity) VALUES (?, ?)',
                    (asset['id'], scores['final_score'])
                )
                db.commit()
                db.close()

        results.sort(key=lambda x: x['scores']['final_score'], reverse=True)

        return jsonify({
            'success': True,
            'total_assets_checked': len(assets),
            'violations_found': len(violations),
            'results': results
        })

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@api_bp.route('/api/v1/violations', methods=['GET'])
@require_api_key
def get_violations():
    """Get all violations with optional limit"""
    limit = request.args.get('limit', 50, type=int)
    db = get_db(current_app.config['DATABASE'])
    rows = db.execute('''
        SELECT v.id, v.similarity, v.found_url, v.detected_at,
               a.name as asset_name
        FROM violations v
        JOIN assets a ON v.asset_id = a.id
        ORDER BY v.detected_at DESC
        LIMIT ?
    ''', (limit,)).fetchall()
    db.close()
    return jsonify({
        'success': True,
        'count': len(rows),
        'violations': [dict(r) for r in rows]
    })


@api_bp.route('/api/v1/register', methods=['POST'])
@require_api_key
def api_register():
    """
    Register a new asset via API.
    POST multipart/form-data with 'image' and 'name'.
    Header: X-API-Key: your_key
    """
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image provided'}), 400

    file = request.files['image']
    name = request.form.get('name', file.filename)

    filename = f"{uuid.uuid4().hex}_{file.filename}"
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    try:
        img = Image.open(filepath)
        hashes = {
            'phash': str(imagehash.phash(img)),
            'dhash': str(imagehash.dhash(img)),
            'ahash': str(imagehash.average_hash(img))
        }

        from routes.watermark import embed_watermark
        wm_path, wm_text = embed_watermark(filepath, name, current_app.config['UPLOAD_FOLDER'])
        wm_filename = os.path.basename(wm_path) if wm_path else filename

        db = get_db(current_app.config['DATABASE'])
        cursor = db.execute(
            '''INSERT INTO assets (name, filename, phash, dhash, ahash, watermark_file)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (name, filename, hashes['phash'], hashes['dhash'], hashes['ahash'], wm_filename)
        )
        asset_id = cursor.lastrowid
        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'asset_id': asset_id,
            'name': name,
            'filename': filename,
            'watermark_file': wm_filename,
            'fingerprint': hashes['phash'][:16] + '...'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
