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
         license_owner, license_start, license_end)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (name, filename, hashes['phash'], hashes['dhash'], hashes['ahash'],
         wm_filename, license_owner, license_start, license_end)
    )
    db.commit()
    db.close()

    save_asset_firebase(name, filename, hashes['phash'], hashes['dhash'], hashes['ahash'])
    add_asset_block(name, filename, hashes['phash'])

    flash(f'Asset "{name}" registered successfully!')
    return redirect(url_for('assets.list_assets'))


@assets_bp.route('/assets/delete/<int:asset_id>', methods=['POST'])
def delete_asset(asset_id):
    db = get_db(current_app.config['DATABASE'])
    db.execute('DELETE FROM assets WHERE id = ?', (asset_id,))
    db.commit()
    db.close()
    flash('Asset deleted successfully.')
    return redirect(url_for('assets.list_assets'))
