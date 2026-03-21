from flask import Blueprint, jsonify, request, current_app
from database.db import get_db
from routes.opencv_detector import combined_opencv_score
from routes.deeplearning_detector import fast_mobilenet_similarity
from PIL import Image
import imagehash
import os
import uuid

api_bp = Blueprint('api', __name__)

def compare_all(asset, filepath, upload_folder):
    """Run all 3 detection methods and return combined score"""
    try:
        img = Image.open(filepath)
        scan_hashes = {
            'phash': str(imagehash.phash(img)),
            'dhash': str(imagehash.dhash(img)),
            'ahash': str(imagehash.average_hash(img))
        }

        # Hash score
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

        # OpenCV score
        asset_path = os.path.join(upload_folder, asset['filename'])
        opencv_score = combined_opencv_score(asset_path, filepath) \
            if os.path.exists(asset_path) else 0

        # Deep learning score
        dl_score = fast_mobilenet_similarity(asset_path, filepath) \
            if os.path.exists(asset_path) else 0

        # Combined
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
        return {'hash_score': 0, 'opencv_score': 0,
                'dl_score': 0, 'final_score': 0}

# ============================================================
# API ENDPOINTS
# ============================================================

@api_bp.route('/api/v1/status', methods=['GET'])
def status():
    """Health check endpoint"""
    db = get_db(current_app.config['DATABASE'])
    total_assets = db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
    total_violations = db.execute(
        'SELECT COUNT(*) FROM violations'
    ).fetchone()[0]
    db.close()
    return jsonify({
        'status': 'operational',
        'version': '1.0.0',
        'name': 'SportShield AI',
        'total_assets': total_assets,
        'total_violations': total_violations,
        'detection_methods': ['pHash', 'dHash', 'aHash', 'SIFT', 'ORB', 'MobileNet']
    })

@api_bp.route('/api/v1/assets', methods=['GET'])
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
def api_scan():
    """
    Scan an image for violations
    POST with multipart/form-data containing 'image' file
    Returns similarity scores against all registered assets
    """
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image provided'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename'}), 400

    # Save temp file
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
            scores = compare_all(
                asset, temp_path,
                current_app.config['UPLOAD_FOLDER']
            )
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
                # Save to database
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
def api_register():
    """
    Register a new asset via API
    POST with multipart/form-data containing 'image' and 'name'
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
        wm_path, wm_text = embed_watermark(
            filepath, name,
            current_app.config['UPLOAD_FOLDER']
        )
        wm_filename = os.path.basename(wm_path) if wm_path else filename

        db = get_db(current_app.config['DATABASE'])
        cursor = db.execute(
            '''INSERT INTO assets
            (name, filename, phash, dhash, ahash, watermark_file)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (name, filename, hashes['phash'],
             hashes['dhash'], hashes['ahash'], wm_filename)
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