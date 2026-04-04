from flask import Blueprint, render_template, current_app, jsonify
from database.db import get_db
from datetime import datetime
import os

demo_bp = Blueprint('demo', __name__)

@demo_bp.route('/demo')
def demo_page():
    """
    Permanent demo page — shows SportShield in action without uploading anything.
    Uses existing registered assets from the DB for the demo flow.
    """
    db = get_db(current_app.config['DATABASE'])

    # Get real assets from DB for display
    assets = db.execute(
        'SELECT * FROM assets ORDER BY uploaded_at DESC LIMIT 6'
    ).fetchall()

    total_assets = db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
    total_violations = db.execute('SELECT COUNT(*) FROM violations').fetchone()[0]

    # Get recent violations for timeline demo
    recent_violations = db.execute('''
        SELECT v.similarity, v.detected_at, a.name as asset_name
        FROM violations v
        JOIN assets a ON v.asset_id = a.id
        ORDER BY v.detected_at DESC LIMIT 5
    ''').fetchall()

    db.close()

    # Impact stats (calculated from real data + estimated)
    revenue_protected = total_violations * 50000  # ₹50,000 per violation estimate
    dmca_generated = total_violations
    assets_protected = total_assets

    return render_template('demo.html',
        assets=assets,
        total_assets=total_assets,
        total_violations=total_violations,
        recent_violations=recent_violations,
        revenue_protected=revenue_protected,
        dmca_generated=dmca_generated,
        assets_protected=assets_protected
    )


@demo_bp.route('/demo/simulate')
def simulate_scan():
    """
    Returns a simulated scan result as JSON for the live demo animation.
    Uses real assets from DB if available, otherwise uses demo data.
    """
    db = get_db(current_app.config['DATABASE'])
    asset = db.execute('SELECT * FROM assets ORDER BY RANDOM() LIMIT 1').fetchone()
    db.close()

    if asset:
        asset_name = asset['name']
    else:
        asset_name = "Olympic Rings — Paris 2024"

    # Simulate realistic detection scores building up
    return jsonify({
        'asset_name': asset_name,
        'hash_score': 91.2,
        'opencv_score': 87.6,
        'dl_score': 94.3,
        'final_score': 92.1,
        'risk_label': 'CRITICAL',
        'risk_color': '#e11d48',
        'status': 'VIOLATION',
        'detection_methods': ['pHash', 'dHash', 'aHash', 'SIFT', 'ORB', 'MobileNet'],
        'blockchain_hash': 'a3f7c2e1b8d94f56',
        'timestamp': datetime.now().strftime('%d %b %Y, %H:%M:%S'),
        'dmca_ready': True
    })
