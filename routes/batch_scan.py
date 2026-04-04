import os
import uuid
import zipfile
import io
import csv
from flask import Blueprint, render_template, request, current_app, make_response, jsonify
from database.db import get_db
from PIL import Image
import imagehash
from routes.opencv_detector import combined_opencv_score
from routes.deeplearning_detector import fast_mobilenet_similarity
from routes.blockchain import add_violation_block
from routes.alerts import send_violation_alert

batch_bp = Blueprint('batch', __name__)

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}


def get_risk_label(similarity):
    if similarity >= 90:
        return 'CRITICAL', '#e11d48'
    elif similarity >= 70:
        return 'HIGH', '#f97316'
    elif similarity >= 50:
        return 'MEDIUM', '#f59e0b'
    else:
        return 'LOW', '#10b981'


def scan_single_image(filepath, assets, upload_folder):
    """Scan one image against all registered assets, return best match."""
    try:
        img = Image.open(filepath)
        scan_hashes = {
            'phash': str(imagehash.phash(img)),
            'dhash': str(imagehash.dhash(img)),
            'ahash': str(imagehash.average_hash(img))
        }
    except:
        return None

    best = {'similarity': 0, 'asset_name': None, 'asset_id': None,
            'hash_score': 0, 'opencv_score': 0, 'dl_score': 0}

    for asset in assets:
        asset_path = os.path.join(upload_folder, asset['filename'])

        # Hash score
        try:
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
        except:
            hash_score = 0

        opencv_score = 0
        dl_score = 0
        if os.path.exists(asset_path):
            try:
                opencv_score = combined_opencv_score(asset_path, filepath)
                dl_score = fast_mobilenet_similarity(asset_path, filepath)
            except:
                pass

        # Combined
        active = sum([1 if s > 0 else 0 for s in [hash_score, opencv_score, dl_score]])
        if active == 3:
            final = (hash_score*0.2) + (opencv_score*0.3) + (dl_score*0.5)
        elif active == 2:
            if dl_score > 0 and opencv_score > 0:
                final = (opencv_score*0.4) + (dl_score*0.6)
            elif dl_score > 0:
                final = (hash_score*0.3) + (dl_score*0.7)
            else:
                final = (hash_score*0.3) + (opencv_score*0.7)
        else:
            final = hash_score
        final = round(final, 2)

        if final > best['similarity']:
            best = {
                'similarity': final,
                'asset_name': asset['name'],
                'asset_id': asset['id'],
                'hash_score': hash_score,
                'opencv_score': opencv_score,
                'dl_score': dl_score
            }

    return best


@batch_bp.route('/batch', methods=['GET', 'POST'])
def batch_scan():
    results = []
    batch_id = None
    summary = {}

    if request.method == 'POST':
        if 'zipfile' not in request.files:
            return render_template('batch_scan.html', error='No zip file uploaded', results=results)

        zfile = request.files['zipfile']
        if not zfile.filename.endswith('.zip'):
            return render_template('batch_scan.html', error='Please upload a .zip file', results=results)

        batch_id = uuid.uuid4().hex[:8].upper()
        upload_folder = current_app.config['UPLOAD_FOLDER']
        batch_dir = os.path.join(upload_folder, f'batch_{batch_id}')
        os.makedirs(batch_dir, exist_ok=True)

        # Extract zip
        try:
            with zipfile.ZipFile(zfile, 'r') as zf:
                all_names = zf.namelist()
                image_names = [
                    n for n in all_names
                    if os.path.splitext(n.lower())[1] in ALLOWED_EXTENSIONS
                    and not n.startswith('__')
                ]
                if not image_names:
                    return render_template('batch_scan.html',
                                           error='No valid images found in zip (jpg, png, webp supported)',
                                           results=results)
                zf.extractall(batch_dir, members=image_names)
        except Exception as e:
            return render_template('batch_scan.html', error=f'Could not read zip: {e}', results=results)

        db = get_db(current_app.config['DATABASE'])
        assets = db.execute('SELECT * FROM assets').fetchall()

        total = len(image_names)
        violations_found = 0

        for img_name in image_names:
            img_path = os.path.join(batch_dir, img_name)
            if not os.path.exists(img_path):
                continue

            best = scan_single_image(img_path, assets, upload_folder)
            if best is None:
                results.append({
                    'filename': os.path.basename(img_name),
                    'similarity': 0,
                    'asset_name': 'Could not process',
                    'status': 'ERROR',
                    'risk_label': 'N/A',
                    'risk_color': '#94a3b8',
                    'hash_score': 0,
                    'opencv_score': 0,
                    'dl_score': 0
                })
                continue

            risk_label, risk_color = get_risk_label(best['similarity'])
            is_violation = best['similarity'] > 70

            if is_violation:
                violations_found += 1
                db.execute(
                    'INSERT INTO violations (asset_id, similarity) VALUES (?, ?)',
                    (best['asset_id'], best['similarity'])
                )
                db.commit()

                # Save to batch_scans table
                db.execute(
                    '''INSERT INTO batch_scans
                    (batch_id, filename, status, highest_similarity, matched_asset, risk_level)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                    (batch_id, os.path.basename(img_name), 'VIOLATION',
                     best['similarity'], best['asset_name'], risk_label)
                )
                db.commit()

                add_violation_block(
                    best['asset_name'], best['similarity'],
                    ['pHash', 'dHash', 'aHash', 'SIFT', 'ORB', 'MobileNet'],
                    scan_type='BATCH'
                )
                send_violation_alert(best['asset_name'], best['similarity'])
            else:
                db.execute(
                    '''INSERT INTO batch_scans
                    (batch_id, filename, status, highest_similarity, matched_asset, risk_level)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                    (batch_id, os.path.basename(img_name), 'SAFE',
                     best['similarity'], best['asset_name'], risk_label)
                )
                db.commit()

            results.append({
                'filename': os.path.basename(img_name),
                'similarity': best['similarity'],
                'asset_name': best['asset_name'] or 'No match',
                'status': 'VIOLATION' if is_violation else 'SAFE',
                'risk_label': risk_label,
                'risk_color': risk_color,
                'hash_score': best['hash_score'],
                'opencv_score': best['opencv_score'],
                'dl_score': best['dl_score']
            })

        db.close()
        results.sort(key=lambda x: x['similarity'], reverse=True)

        summary = {
            'batch_id': batch_id,
            'total': total,
            'violations': violations_found,
            'safe': total - violations_found,
            'violation_rate': round((violations_found / total * 100), 1) if total > 0 else 0
        }

    return render_template('batch_scan.html', results=results,
                           summary=summary, batch_id=batch_id)


@batch_bp.route('/batch/export/<batch_id>')
def export_batch_csv(batch_id):
    """Export batch scan results as CSV"""
    db = get_db(current_app.config['DATABASE'])
    rows = db.execute(
        'SELECT * FROM batch_scans WHERE batch_id = ? ORDER BY highest_similarity DESC',
        (batch_id,)
    ).fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Filename', 'Status', 'Similarity %', 'Matched Asset', 'Risk Level', 'Scanned At'])
    for r in rows:
        writer.writerow([
            r['filename'], r['status'], r['highest_similarity'],
            r['matched_asset'], r['risk_level'], r['scanned_at']
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=batch_{batch_id}.csv'
    return response
