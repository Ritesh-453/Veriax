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
import time

scanner_bp = Blueprint('scanner', __name__)

# ============================================================
# TARGET PLATFORMS — searched one by one
# ============================================================
TARGET_PLATFORMS = [
    {'name': 'YouTube',    'site': 'youtube.com'},
    {'name': 'Instagram',  'site': 'instagram.com'},
    {'name': 'Facebook',   'site': 'facebook.com'},
    {'name': 'Twitter',    'site': 'twitter.com'},
    {'name': 'Reddit',     'site': 'reddit.com'},
    {'name': 'ESPN',       'site': 'espn.com'},
    {'name': 'Cricbuzz',   'site': 'cricbuzz.com'},
    {'name': 'NDTV Sports','site': 'sports.ndtv.com'},
    {'name': 'Sky Sports', 'site': 'skysports.com'},
    {'name': 'BBC Sport',  'site': 'bbc.com/sport'},
]


# ============================================================
# HASH HELPERS
# ============================================================

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


# ============================================================
# SERPAPI — site-specific image search
# ============================================================

def search_platform(asset_name, site, serpapi_key, num=3):
    """Search one specific platform for asset images via SerpAPI."""
    try:
        response = requests.get(
            'https://serpapi.com/search',
            params={
                'api_key': serpapi_key,
                'engine': 'google_images',
                'q': f'site:{site} {asset_name}',
                'num': num,
                'ijn': '0'
            },
            timeout=15
        )
        data = response.json()
        items = data.get('images_results', [])[:num]  # Hard slice
        # Filter to only keep results actually from the target site
        filtered = [i for i in items if site in i.get('link','') or site in i.get('original','')]
        result = filtered[:num] if filtered else items[:num]
        print(f"  [{site}] Found {len(result)} relevant images")
        return result
    except Exception as e:
        print(f"  [{site}] Search error: {e}")
        return []


# ============================================================
# MAIN SCAN FUNCTION
# ============================================================

def search_and_scan(asset, db_path, upload_folder):
    try:
        serpapi_key = os.getenv('SERPAPI_KEY')
        if not serpapi_key:
            print("[Scanner] SERPAPI_KEY missing in .env")
            return []

        violations = []
        print(f"\n[Scanner] Scanning '{asset['name']}' across {len(TARGET_PLATFORMS)} platforms...")

        for platform in TARGET_PLATFORMS:
            items = search_platform(
                asset['name'],
                platform['site'],
                serpapi_key,
                num=5
            )[:5]  # Hard limit to 5 per platform

            for item in items:
                img_url = item.get('original', '')
                page_url = item.get('link', '')

                if not img_url:
                    continue

                try:
                    img_response = requests.get(
                        img_url, timeout=8,
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    if img_response.status_code != 200:
                        continue

                    content_type = img_response.headers.get('Content-Type', '')
                    if 'image' not in content_type:
                        continue

                    temp_filename = f"scan_web_{uuid.uuid4().hex}.jpg"
                    temp_path = os.path.join(upload_folder, temp_filename)
                    with open(temp_path, 'wb') as f:
                        f.write(img_response.content)

                    scan_hashes = get_all_hashes(temp_path)
                    if not scan_hashes:
                        os.remove(temp_path)
                        continue

                    hash_score = compare_hashes(
                        scan_hashes,
                        asset['phash'], asset['dhash'], asset['ahash']
                    )

                    asset_path = os.path.join(upload_folder, asset['filename'])
                    opencv_score = 0
                    if os.path.exists(asset_path):
                        opencv_score = combined_opencv_score(asset_path, temp_path)

                    similarity = round(
                        (hash_score * 0.3) + (opencv_score * 0.7), 2
                    ) if opencv_score > 0 else hash_score

                    print(f"    Hash:{hash_score}% OpenCV:{opencv_score}% "
                          f"Final:{similarity}% — {platform['name']}")

                    if similarity > 50:
                        violations.append({
                            'asset_id': asset['id'],
                            'asset_name': asset['name'],
                            'similarity': similarity,
                            'found_url': page_url,
                            'platform': platform['name'],
                            'filename': temp_filename
                        })

                        db = get_db(db_path)
                        db.execute(
                            'INSERT INTO violations (asset_id, found_url, similarity) VALUES (?, ?, ?)',
                            (asset['id'], page_url, similarity)
                        )
                        db.commit()
                        db.close()

                        save_violation_firebase(
                            asset['id'], asset['name'], similarity, temp_filename
                        )

                        from routes.alerts import send_violation_alert
                        send_violation_alert(asset['name'], similarity, page_url)

                    else:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)

                except Exception as e:
                    print(f"    Image error: {e}")
                    continue

        print(f"[Scanner] Done. Found {len(violations)} violations for '{asset['name']}'")
        return violations

    except Exception as e:
        print(f"[Scanner] Error: {e}")
        return []


# ============================================================
# SCHEDULED SCAN
# ============================================================

def run_scheduled_scan(app):
    with app.app_context():
        while True:
            print(f"[{datetime.now()}] Running scheduled scan...")
            try:
                db = get_db(app.config['DATABASE'])
                assets = db.execute('SELECT * FROM assets').fetchall()
                db.close()
                total = 0
                for asset in assets:
                    violations = search_and_scan(
                        asset, app.config['DATABASE'], app.config['UPLOAD_FOLDER']
                    )
                    total += len(violations)
                print(f"[{datetime.now()}] Scan complete. {total} violations found.")
            except Exception as e:
                print(f"Scheduled scan error: {e}")
            time.sleep(86400)


# ============================================================
# ROUTES
# ============================================================

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

    # Detect platform from URL
    platform_counts = {}
    for v in recent_violations:
        url = v['found_url'] or ''
        platform = 'other'
        for p in TARGET_PLATFORMS:
            if p['site'] in url:
                platform = p['name']
                break
        platform_counts[platform] = platform_counts.get(platform, 0) + 1

    return render_template('scanner.html',
                           assets=assets,
                           violations=recent_violations,
                           platform_counts=platform_counts,
                           target_platforms=TARGET_PLATFORMS)


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

        platform_summary = {}
        for v in all_violations:
            p = v.get('platform', 'unknown')
            platform_summary[p] = platform_summary.get(p, 0) + 1

        return jsonify({
            'status': 'success',
            'violations_found': len(all_violations),
            'by_platform': platform_summary,
            'message': f'Scan complete. Found {len(all_violations)} violations across '
                       f'{len(platform_summary)} platforms.'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})