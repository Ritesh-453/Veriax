from routes.blockchain import add_asset_block
from routes.watermark import embed_watermark
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from database.db import get_db
from database.firebase_db import save_asset_firebase
from PIL import Image
import imagehash
import os
import uuid
from datetime import date, datetime, timedelta

assets_bp = Blueprint('assets', __name__)


# ============================================================
# HELPERS — HASHING
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


def get_frame_hashes(frame_path):
    """Hash a single video keyframe. Returns dict or None."""
    return get_all_hashes(frame_path)


# ============================================================
# HELPERS — LICENSE & RISK
# ============================================================

def get_license_status(asset):
    if not asset.get('license_end'):
        return 'NO_LICENSE'
    try:
        end = date.fromisoformat(asset['license_end'])
        today = date.today()
        days_left = (end - today).days
        if days_left < 0:
            return 'EXPIRED'
        elif days_left <= 30:
            return 'EXPIRING_SOON'
        else:
            return 'ACTIVE'
    except:
        return 'NO_LICENSE'


def get_risk_trend(asset_id, db):
    """
    Returns trend info for an asset:
    - violations_this_week: count
    - violations_last_week: count
    - trend: 'UP', 'DOWN', 'STABLE', 'CLEAN'
    - trend_label: human readable
    """
    try:
        this_week = db.execute('''
            SELECT COUNT(*) FROM violations
            WHERE asset_id = ?
            AND detected_at >= DATE('now', '-7 days')
        ''', (asset_id,)).fetchone()[0]

        last_week = db.execute('''
            SELECT COUNT(*) FROM violations
            WHERE asset_id = ?
            AND detected_at >= DATE('now', '-14 days')
            AND detected_at < DATE('now', '-7 days')
        ''', (asset_id,)).fetchone()[0]

        total = db.execute(
            'SELECT COUNT(*) FROM violations WHERE asset_id = ?',
            (asset_id,)
        ).fetchone()[0]

        if this_week == 0 and total == 0:
            return {'trend': 'CLEAN', 'label': '✓ Clean — no violations',
                    'color': '#10b981', 'this_week': 0, 'total': 0}
        elif this_week == 0:
            return {'trend': 'CLEAN', 'label': f'✓ Clean for 7 days ({total} total)',
                    'color': '#10b981', 'this_week': 0, 'total': total}
        elif this_week > last_week:
            return {'trend': 'UP', 'label': f'↑ {this_week} violations this week',
                    'color': '#e11d48', 'this_week': this_week, 'total': total}
        elif this_week < last_week:
            return {'trend': 'DOWN', 'label': f'↓ Decreasing ({this_week} this week)',
                    'color': '#f97316', 'this_week': this_week, 'total': total}
        else:
            return {'trend': 'STABLE', 'label': f'→ {this_week} violations this week',
                    'color': '#f59e0b', 'this_week': this_week, 'total': total}
    except:
        return {'trend': 'CLEAN', 'label': 'No data', 'color': '#94a3b8',
                'this_week': 0, 'total': 0}


@assets_bp.route('/assets')
def list_assets():
    db = get_db(current_app.config['DATABASE'])
    assets_raw = db.execute(
        'SELECT * FROM assets ORDER BY uploaded_at DESC'
    ).fetchall()

    assets = []
    for a in assets_raw:
        a_dict = dict(a)
        a_dict['license_status'] = get_license_status(a_dict)
        a_dict['risk_trend'] = get_risk_trend(a_dict['id'], db)

        # Attach frame count for video assets
        if a_dict.get('asset_type') == 'VIDEO':
            fc = db.execute(
                'SELECT COUNT(*) FROM video_frames WHERE asset_id = ?',
                (a_dict['id'],)
            ).fetchone()[0]
            a_dict['registered_frames'] = fc
        else:
            a_dict['registered_frames'] = 0

        assets.append(a_dict)

    db.close()
    return render_template('assets.html', assets=assets)


@assets_bp.route('/assets/upload', methods=['POST'])
def upload_asset():
    if 'image' not in request.files:
        flash('No file selected')
        return redirect(url_for('index'))

    file = request.files['image']
    name = request.form.get('name', file.filename)
    license_owner = request.form.get('license_owner', '').strip() or None
    license_start = request.form.get('license_start', '').strip() or None
    license_end = request.form.get('license_end', '').strip() or None

    db = get_db(current_app.config['DATABASE'])
    existing = db.execute('SELECT id FROM assets WHERE name = ?', (name,)).fetchone()
    if existing:
        db.close()
        flash(f'Asset "{name}" already exists in registry!')
        return redirect(url_for('assets.list_assets'))

    if file.filename == '':
        flash('No file selected')
        return redirect(url_for('index'))

    filename = f"{uuid.uuid4().hex}_{file.filename}"
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    hashes = get_all_hashes(filepath)
    if not hashes:
        flash('Could not process image')
        return redirect(url_for('index'))

    wm_path, wm_text = embed_watermark(filepath, name, current_app.config['UPLOAD_FOLDER'])
    wm_filename = os.path.basename(wm_path) if wm_path else filename

    db.execute(
        '''INSERT INTO assets
        (name, filename, phash, dhash, ahash, watermark_file,
         license_owner, license_start, license_end, asset_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'IMAGE')''',
        (name, filename, hashes['phash'], hashes['dhash'], hashes['ahash'],
         wm_filename, license_owner, license_start, license_end)
    )
    db.commit()
    db.close()

    save_asset_firebase(name, filename, hashes['phash'], hashes['dhash'], hashes['ahash'])
    add_asset_block(name, filename, hashes['phash'])

    flash(f'Asset "{name}" registered successfully!')
    return redirect(url_for('assets.list_assets'))


# ============================================================
# ROUTES — VIDEO UPLOAD (new)
# ============================================================

@assets_bp.route('/assets/upload_video', methods=['POST'])
def upload_video_asset():
    """
    Registers an official video as a protected asset.
    Steps:
      1. Save the video file
      2. Extract keyframes using existing extract_keyframes()
      3. Hash each keyframe (phash + dhash + ahash)
      4. Insert one row into assets (asset_type = 'VIDEO')
      5. Insert one row per keyframe into video_frames
      6. Clean up the raw video file (frames are kept as evidence)
    """
    from routes.video_scanner import extract_and_hash_keyframes

    if 'video' not in request.files:
        flash('No video file selected')
        return redirect(url_for('assets.list_assets'))

    file = request.files['video']
    if file.filename == '':
        flash('No video file selected')
        return redirect(url_for('assets.list_assets'))

    # Accept common video formats
    allowed = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        flash(f'Unsupported video format "{ext}". Allowed: mp4, mov, avi, mkv, webm, m4v')
        return redirect(url_for('assets.list_assets'))

    name = request.form.get('name', '').strip() or os.path.splitext(file.filename)[0]
    license_owner = request.form.get('license_owner', '').strip() or None
    license_start = request.form.get('license_start', '').strip() or None
    license_end = request.form.get('license_end', '').strip() or None
    interval = int(request.form.get('interval', 3))  # keyframe interval in seconds

    # ── Duplicate name check ───────────────────────────────
    db = get_db(current_app.config['DATABASE'])
    existing = db.execute('SELECT id FROM assets WHERE name = ?', (name,)).fetchone()
    if existing:
        db.close()
        flash(f'Asset "{name}" already exists in registry!')
        return redirect(url_for('assets.list_assets'))

    # ── Save video temporarily ─────────────────────────────
    video_filename = f"reg_video_{uuid.uuid4().hex}{ext}"
    video_path = os.path.join(current_app.config['UPLOAD_FOLDER'], video_filename)
    file.save(video_path)

    # ── Extract keyframes + hash in one pass ──────────────
    # Hash frames from PIL object in memory (no re-open from disk).
    # All DB inserts done in one executemany batch.
    keyframes_data = extract_and_hash_keyframes(video_path, interval_seconds=interval)

    if not keyframes_data:
        if os.path.exists(video_path):
            os.remove(video_path)
        db.close()
        flash('Could not extract frames from video. Please check the file and try again.')
        return redirect(url_for('assets.list_assets'))

    duration = keyframes_data[-1]['timestamp'] if keyframes_data else 0
    thumbnail_filename = keyframes_data[0]['filename']

    # ── Insert parent asset row ────────────────────────────
    cursor = db.execute(
        '''INSERT INTO assets
        (name, filename, phash, dhash, ahash, watermark_file,
         license_owner, license_start, license_end,
         asset_type, duration, frame_count)
        VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, 'VIDEO', ?, ?)''',
        (name, thumbnail_filename, '', '', '',
         license_owner, license_start, license_end,
         round(duration, 2), len(keyframes_data))
    )
    asset_id = cursor.lastrowid

    # ── Batch insert all keyframe rows in one transaction ──
    db.executemany(
        '''INSERT INTO video_frames
        (asset_id, frame_filename, timestamp, time_str, phash, dhash, ahash)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        [(asset_id, kf['filename'], kf['timestamp'],
          kf['time_str'], kf['phash'], kf['dhash'], kf['ahash'])
         for kf in keyframes_data]
    )
    frames_registered = len(keyframes_data)
    db.commit()
    db.close()

    # ── Log to blockchain ──────────────────────────────────
    add_asset_block(name, video_filename, f'VIDEO:{frames_registered}_frames')

    # ── Clean up the raw video (frames are kept) ──────────
    if os.path.exists(video_path):
        os.remove(video_path)

    # ── Robust fingerprinting (CLIP + flip hashes) ────────
    # Runs in background thread — user gets flash immediately.
    import threading as _threading
    def _run_fingerprint(app_obj, aid, kf_data):
        import os as _os
        with app_obj.app_context():
            try:
                from routes.video_fingerprint import fingerprint_frame, embedding_to_str
                from PIL import Image as _Image
                from database.db import get_db as _get_db
                db2 = _get_db(app_obj.config['DATABASE'])
                frame_dir = _os.path.join(app_obj.config['UPLOAD_FOLDER'], 'video_frames')
                for idx, kf in enumerate(kf_data):
                    try:
                        pil_img = _Image.open(_os.path.join(frame_dir, kf['filename']))
                        fp = fingerprint_frame(pil_img)
                        flip_h = fp['hashes_flip']
                        db2.execute("""
                            INSERT INTO video_fingerprints
                            (asset_id, frame_index, timestamp, time_str,
                             clip_embedding, clip_flip_embedding,
                             phash, dhash, ahash,
                             phash_flip, dhash_flip, ahash_flip)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            aid, idx, kf['timestamp'], kf['time_str'],
                            embedding_to_str(fp['clip_vec']) if fp['clip_vec'] is not None else None,
                            embedding_to_str(fp['clip_flip']) if fp['clip_flip'] is not None else None,
                            kf['phash'], kf['dhash'], kf['ahash'],
                            flip_h['phash'], flip_h['dhash'], flip_h['ahash'],
                        ))
                    except Exception as fe:
                        print(f"[Fingerprint] Frame {idx} error: {fe}")
                db2.commit()
                db2.close()
                print(f"[Fingerprint] Done for asset {aid}")
            except Exception as e:
                print(f"[Fingerprint] Background error: {e}")

    t = _threading.Thread(
        target=_run_fingerprint,
        args=(current_app._get_current_object(), asset_id, keyframes_data),
        daemon=True
    )
    t.start()

    flash(
        f'✓ Video "{name}" registered successfully! '
        f'{frames_registered} keyframes extracted and fingerprinted. '
        f'Robust CLIP fingerprinting running in background.'
    )
    return redirect(url_for('assets.list_assets'))


@assets_bp.route('/assets/delete/<int:asset_id>', methods=['POST'])
def delete_asset(asset_id):
    db = get_db(current_app.config['DATABASE'])

    # For video assets, also remove stored keyframe image files
    asset = db.execute('SELECT * FROM assets WHERE id = ?', (asset_id,)).fetchone()
    if asset and dict(asset).get('asset_type') == 'VIDEO':
        frames = db.execute(
            'SELECT frame_filename FROM video_frames WHERE asset_id = ?',
            (asset_id,)
        ).fetchall()
        frame_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'video_frames')
        for f in frames:
            frame_path = os.path.join(frame_dir, f['frame_filename'])
            if os.path.exists(frame_path):
                try:
                    os.remove(frame_path)
                except:
                    pass

    db.execute('DELETE FROM assets WHERE id = ?', (asset_id,))
    db.commit()
    db.close()
    flash('Asset deleted successfully.')
    return redirect(url_for('assets.list_assets'))