from routes.opencv_detector import combined_opencv_score
from flask import Blueprint, render_template, request, current_app, jsonify
from database.db import get_db
from database.firebase_db import save_violation_firebase
from PIL import Image
import imagehash
import requests
import os
import uuid
from datetime import datetime
import threading
import time

scanner_bp = Blueprint('scanner', __name__)

def get_all_hashes(path):
    try:
        img = Image.open(path)
        return {
            'phash': str(imagehash.phash(img)),
            'dhash': str(imagehash.dhash(img)),
            'ahash': str(imagehash.average_hash(img))
        }
    except:
        return None

def compare_hashes(h1, h2_phash, h2_dhash, h2_ahash):
    try:
        p1 = imagehash.hex_to_hash(h1['phash'])
        d1 = imagehash.hex_to_hash(h1['dhash'])
        a1 = imagehash.hex_to_hash(h1['ahash'])
        p2 = imagehash.hex_to_hash(h2_phash)
        d2 = imagehash.hex_to_hash(h2_dhash)
        a2 = imagehash.hex_to_hash(h2_ahash)
        p_score = max(0, (1 - (p1 - p2) / 64) * 100)
        d_score = max(0, (1 - (d1 - d2) / 64) * 100)
        a_score = max(0, (1 - (a1 - a2) / 64) * 100)
        return round((p_score * 0.5) + (d_score * 0.3) + (a_score * 0.2), 2)
    except:
        return 0

def search_and_scan(asset, db_path, upload_folder):
    try:
        # SCANNER SWITCH — Change to True to enable, False to disable
        SCANNER_ENABLED = False

        serpapi_key = os.getenv('SERPAPI_KEY')
        if not SCANNER_ENABLED:
            print("Scanner disabled — set SCANNER_ENABLED=True to enable")
            return []
        if not serpapi_key:
            print("SerpApi key missing")
            return []

        search_url = "https://serpapi.com/search"
        params = {
            'api_key': serpapi_key,
            'engine': 'google_images',
            'q': asset['name'] + ' sports logo',
            'num': 5
        }

        response = requests.get(search_url, params=params, timeout=15)
        results = response.json()

        violations = []
        items = results.get('images_results', [])
        print(f"Found {len(items)} images for {asset['name']}")

        for item in items:
            img_url = item.get('original', '')
            page_url = item.get('link', '')

            if not img_url:
                continue

            try:
                img_response = requests.get(img_url, timeout=8,
                    headers={'User-Agent': 'Mozilla/5.0'})

                if img_response.status_code == 200:
                    temp_filename = f"scan_web_{uuid.uuid4().hex}.jpg"
                    temp_path = os.path.join(upload_folder, temp_filename)

                    with open(temp_path, 'wb') as f:
                        f.write(img_response.content)

                    scan_hashes = get_all_hashes(temp_path)
                    if scan_hashes:
                        # Hash score
                        hash_score = compare_hashes(
                            scan_hashes,
                            asset['phash'],
                            asset['dhash'],
                            asset['ahash']
                        )

                        # OpenCV SIFT+ORB score
                        asset_path = os.path.join(
                            upload_folder,
                            asset['filename']
                        )
                        opencv_score = 0
                        if os.path.exists(asset_path):
                            opencv_score = combined_opencv_score(
                                asset_path, temp_path
                            )

                        # Combined score
                        if opencv_score > 0:
                            similarity = round(
                                (hash_score * 0.3) + (opencv_score * 0.7), 2
                            )
                        else:
                            similarity = hash_score

                        print(f"Hash: {hash_score}% | OpenCV: {opencv_score}% | Final: {similarity}% — {asset['name']}")

                        if similarity > 60:
                            violations.append({
                                'asset_id': asset['id'],
                                'asset_name': asset['name'],
                                'similarity': similarity,
                                'found_url': page_url,
                                'img_url': img_url,
                                'filename': temp_filename
                            })

                            db = get_db(db_path)
                            db.execute(
                                '''INSERT INTO violations
                                (asset_id, found_url, similarity)
                                VALUES (?, ?, ?)''',
                                (asset['id'], page_url, similarity)
                            )
                            db.commit()
                            db.close()

                            save_violation_firebase(
                                asset['id'],
                                asset['name'],
                                similarity,
                                temp_filename
                            )

                            from routes.alerts import send_violation_alert
                            send_violation_alert(
                                asset['name'],
                                similarity,
                                page_url
                            )
                        else:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)

            except Exception as e:
                print(f"Error scanning image {img_url}: {e}")
                continue

        return violations

    except Exception as e:
        print(f"Search error: {e}")
        return []

def run_scheduled_scan(app):
    with app.app_context():
        while True:
            print(f"[{datetime.now()}] Running scheduled scan...")
            try:
                db = get_db(app.config['DATABASE'])
                assets = db.execute('SELECT * FROM assets').fetchall()
                db.close()

                total_violations = 0
                for asset in assets:
                    violations = search_and_scan(
                        asset,
                        app.config['DATABASE'],
                        app.config['UPLOAD_FOLDER']
                    )
                    total_violations += len(violations)

                print(f"[{datetime.now()}] Scan complete. Found {total_violations} violations.")
            except Exception as e:
                print(f"Scheduled scan error: {e}")

            time.sleep(86400)

@scanner_bp.route('/scanner')
def scanner_dashboard():
    db = get_db(current_app.config['DATABASE'])
    assets = db.execute('SELECT * FROM assets').fetchall()
    recent_violations = db.execute('''
        SELECT v.*, a.name as asset_name
        FROM violations v
        JOIN assets a ON v.asset_id = a.id
        WHERE v.found_url IS NOT NULL
        ORDER BY v.detected_at DESC LIMIT 20
    ''').fetchall()
    db.close()
    return render_template('scanner.html',
                           assets=assets,
                           violations=recent_violations)

@scanner_bp.route('/scanner/run', methods=['POST'])
def manual_scan():
    try:
        db = get_db(current_app.config['DATABASE'])
        assets = db.execute('SELECT * FROM assets').fetchall()
        db.close()

        all_violations = []
        for asset in assets:
            violations = search_and_scan(
                asset,
                current_app.config['DATABASE'],
                current_app.config['UPLOAD_FOLDER']
            )
            all_violations.extend(violations)

        return jsonify({
            'status': 'success',
            'violations_found': len(all_violations),
            'message': f'Scan complete. Found {len(all_violations)} potential violations.'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})