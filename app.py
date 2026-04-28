from flask import Flask, render_template, send_from_directory, request, redirect, url_for, flash
from dotenv import load_dotenv
from flask_cors import CORS
import os
import uuid

load_dotenv()

# ── Resolve absolute paths so they work regardless of working directory ───────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, os.getenv('UPLOAD_FOLDER', 'uploads'))
DATABASE = os.path.join(BASE_DIR, os.getenv('DATABASE', 'database/sportshield.db'))

# Ensure critical directories exist on startup
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'video_frames'), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'batch_scans'), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'database', 'embeddings'), exist_ok=True)
os.makedirs(os.path.dirname(DATABASE), exist_ok=True)

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv('SECRET_KEY', 'sportshield2026')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DATABASE'] = DATABASE

# ── Database init ─────────────────────────────────────────────────────────────
from database.db import init_db
init_db(app.config['DATABASE'])

# ── Blueprints ────────────────────────────────────────────────────────────────
# NOTE: deeplearning_detector is imported INSIDE routes (lazy), not here.
# Importing it at the top level would load PyTorch/MobileNet immediately and
# consume ~400MB RAM, crashing free-tier servers before any request is served.

from routes.assets import assets_bp
from routes.scan import scan_bp
from routes.report import report_bp
from routes.scanner import scanner_bp, run_scheduled_scan
from routes.api import api_bp
from routes.social_media import social_bp
from routes.batch_scan import batch_bp

app.register_blueprint(assets_bp)
app.register_blueprint(scan_bp)
app.register_blueprint(report_bp)
app.register_blueprint(scanner_bp)
app.register_blueprint(api_bp)
app.register_blueprint(social_bp)
app.register_blueprint(batch_bp)

# ── Background scanner (disabled on free tier — uncomment for paid plan) ──────
# import threading
# scanner_thread = threading.Thread(
#     target=run_scheduled_scan,
#     args=(app,),
#     daemon=True
# )
# scanner_thread.start()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    from routes.dashboard import get_dashboard_data
    data = get_dashboard_data(app.config['DATABASE'])
    return render_template('index.html', **data)


@app.route('/sdg')
def sdg():
    return render_template('sdg.html')


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/uploads/video_frames/<filename>')
def video_frame(filename):
    return send_from_directory(
        os.path.join(app.config['UPLOAD_FOLDER'], 'video_frames'),
        filename
    )


@app.route('/api-docs')
def api_docs():
    return render_template('api_docs.html')


@app.route('/video', methods=['GET', 'POST'])
def video_scan():
    violations = []
    video_info = None

    if request.method == 'POST':
        if 'video' not in request.files:
            return render_template('video_scan.html', violations=[], video_info=None)

        file = request.files['video']
        interval = int(request.form.get('interval', 3))

        if file.filename == '':
            return render_template('video_scan.html', violations=[], video_info=None)

        # Save video
        video_filename = f"video_{uuid.uuid4().hex}_{file.filename}"
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
        file.save(video_path)

        # Get assets
        from database.db import get_db
        db = get_db(app.config['DATABASE'])
        assets = db.execute('SELECT * FROM assets').fetchall()
        db.close()

        # Extract keyframes and scan
        from routes.video_scanner import extract_keyframes, scan_video
        keyframes = extract_keyframes(video_path, interval)

        video_info = {
            'frames_scanned': len(keyframes),
            'duration': round(len(keyframes) * interval, 1)
        }

        # Scan frames
        violations = scan_video(
            video_path,
            assets,
            app.config['DATABASE'],
            app.config['UPLOAD_FOLDER']
        )

        # Save violations to database
        if violations:
            db = get_db(app.config['DATABASE'])
            for v in violations:
                db.execute(
                    'INSERT INTO violations (asset_id, similarity) VALUES (?, ?)',
                    (v['asset_id'], v['similarity'])
                )
                from routes.alerts import send_violation_alert
                send_violation_alert(v['asset_name'], v['similarity'])
            db.commit()
            db.close()

        # Clean up video file
        if os.path.exists(video_path):
            os.remove(video_path)

    return render_template('video_scan.html', violations=violations, video_info=video_info)


@app.route('/blockchain')
def blockchain():
    from routes.blockchain import get_chain_stats, load_chain
    stats = get_chain_stats()
    chain = load_chain()
    return render_template('blockchain.html', stats=stats, chain=chain)


@app.route('/blockchain/verify')
def blockchain_verify():
    from routes.blockchain import verify_chain
    valid, message = verify_chain()
    flash(f"{'✓' if valid else '✗'} {message}")
    return redirect(url_for('blockchain'))


if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)