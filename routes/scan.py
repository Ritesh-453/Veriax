from routes.watermark import check_watermark
from routes.deeplearning_detector import fast_mobilenet_similarity
from routes.opencv_detector import combined_opencv_score, get_keypoint_count
from flask import Blueprint, render_template, request, current_app
from database.db import get_db
from PIL import Image
from routes.blockchain import add_violation_block
import imagehash
import exifread
import os
from routes.gemini import analyze_image, compare_images_ai

scan_bp = Blueprint('scan', __name__)

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

def compare_hashes(h1_dict, h2_phash, h2_dhash, h2_ahash):
    try:
        p1 = imagehash.hex_to_hash(h1_dict['phash'])
        d1 = imagehash.hex_to_hash(h1_dict['dhash'])
        a1 = imagehash.hex_to_hash(h1_dict['ahash'])
        p2 = imagehash.hex_to_hash(h2_phash)
        d2 = imagehash.hex_to_hash(h2_dhash)
        a2 = imagehash.hex_to_hash(h2_ahash)

        p_score = max(0, (1 - (p1 - p2) / 64) * 100)
        d_score = max(0, (1 - (d1 - d2) / 64) * 100)
        a_score = max(0, (1 - (a1 - a2) / 64) * 100)

        final = (p_score * 0.5) + (d_score * 0.3) + (a_score * 0.2)
        return round(final, 2)
    except:
        return 0

def get_exif_data(path):
    try:
        with open(path, 'rb') as f:
            tags = exifread.process_file(f, stop_tag='UNDEF', details=False)
        data = {}
        for tag, val in tags.items():
            if tag in ['Image Make', 'Image Model', 'EXIF DateTimeOriginal',
                       'GPS GPSLatitude', 'GPS GPSLongitude']:
                data[tag] = str(val)
        return data
    except:
        return {}

def get_risk_label(similarity):
    if similarity >= 90:
        return 'CRITICAL', '#e11d48'
    elif similarity >= 70:
        return 'HIGH', '#f97316'
    elif similarity >= 50:
        return 'MEDIUM', '#f59e0b'
    else:
        return 'LOW', '#10b981'
    
def combined_similarity(hash_score, opencv_score, dl_score):
    """
    Intelligently combine Hash + OpenCV + Deep Learning scores
    Each method catches different types of violations
    """
    active_methods = sum([
        1 if hash_score > 0 else 0,
        1 if opencv_score > 0 else 0,
        1 if dl_score > 0 else 0
    ])

    if active_methods == 3:
        # All methods working — weighted combination
        final = (hash_score * 0.2) + (opencv_score * 0.3) + (dl_score * 0.5)
    elif active_methods == 2:
        if dl_score > 0 and opencv_score > 0:
            final = (opencv_score * 0.4) + (dl_score * 0.6)
        elif dl_score > 0:
            final = (hash_score * 0.3) + (dl_score * 0.7)
        else:
            final = (hash_score * 0.3) + (opencv_score * 0.7)
    else:
        final = hash_score

    return round(final, 2)

@scan_bp.route('/scan', methods=['GET', 'POST'])
def scan():
    results = []
    exif_data = {}
    scan_filename = None
    ai_analysis = None

    if request.method == 'POST':
        if 'image' not in request.files:
            return render_template('scan.html', results=results)

        file = request.files['image']
        if file.filename == '':
            return render_template('scan.html', results=results)

        scan_filename = f"scan_{file.filename}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], scan_filename)
        file.save(filepath)

        # Get hashes
        scan_hashes = get_all_hashes(filepath)
        exif_data = get_exif_data(filepath)

        # Gemini AI analysis of scanned image
        ai_analysis = analyze_image(filepath)
        # Check for watermark
        watermark_result = check_watermark(filepath)

        if not scan_hashes:
            return render_template('scan.html', results=results,
                                   error="Could not process image")

        db = get_db(current_app.config['DATABASE'])
        assets = db.execute('SELECT * FROM assets').fetchall()

        for asset in assets:
            # Hash similarity
            hash_score = compare_hashes(
                scan_hashes,
                asset['phash'],
                asset['dhash'],
                asset['ahash']
            )

            # OpenCV SIFT + ORB similarity
            asset_path = os.path.join(
                current_app.config['UPLOAD_FOLDER'],
                asset['filename']
            )
            opencv_score = 0
            if os.path.exists(asset_path):
                opencv_score = combined_opencv_score(asset_path, filepath)

            # MobileNet deep learning similarity
            dl_score = 0
            if os.path.exists(asset_path):
                dl_score = fast_mobilenet_similarity(asset_path, filepath)

            # Combined final score
            similarity = combined_similarity(hash_score, opencv_score, dl_score)
            risk_label, risk_color = get_risk_label(similarity)

            # For high matches — get AI comparison too
            ai_verdict = None

                    # Auto-save violations above 70%
            if similarity > 70:
                db.execute(
                    'INSERT INTO violations (asset_id, similarity) VALUES (?, ?)',
                    (asset['id'], similarity)
                )
                db.commit()

                # Save to Firebase
                from database.firebase_db import save_violation_firebase
                save_violation_firebase(
                    asset['id'],
                    asset['name'],
                    similarity,
                    asset['filename']
                )

                # Add to blockchain
                add_violation_block(
                    asset['name'],
                    similarity,
                    ['pHash', 'dHash', 'aHash', 'SIFT', 'ORB', 'MobileNet'],
                    scan_type='IMAGE'   
                )

                # Send email alert
                from routes.alerts import send_violation_alert
                send_violation_alert(asset['name'], similarity)

            results.append({
                'asset_name': asset['name'],
                'filename': asset['filename'],
                'similarity': similarity,
                'hash_score': hash_score,
                'opencv_score': opencv_score,
                'dl_score': dl_score,
                'status': 'ALERT' if similarity > 70 else 'SAFE',
                'risk_label': risk_label,
                'risk_color': risk_color,
                'ai_verdict': ai_verdict
            })
            
            results.append({
                'asset_name': asset['name'],
                'filename': asset['filename'],
                'similarity': similarity,
                'status': 'ALERT' if similarity > 70 else 'SAFE',
                'risk_label': risk_label,
                'risk_color': risk_color,
                'ai_verdict': ai_verdict
            })

        db.close()
        results.sort(key=lambda x: x['similarity'], reverse=True)

    return render_template('scan.html', results=results,
                           exif_data=exif_data,
                           scan_filename=scan_filename,
                           ai_analysis=ai_analysis,
                           watermark_result=watermark_result if request.method == 'POST' else None)

@scan_bp.route('/violations')
def violations():
    db = get_db(current_app.config['DATABASE'])
    rows = db.execute('''
        SELECT v.*, a.name as asset_name, a.filename as asset_filename
        FROM violations v
        JOIN assets a ON v.asset_id = a.id
        ORDER BY v.detected_at DESC
    ''').fetchall()
    db.close()
    return render_template('violations.html', violations=rows)