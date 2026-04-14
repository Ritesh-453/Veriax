from routes.video_scanner import scan_video
from flask import Flask, render_template, send_from_directory, request
from dotenv import load_dotenv
from routes.blockchain import get_chain_stats, load_chain, verify_chain
import os
import uuid
import threading

from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv('SECRET_KEY')
app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER')
app.config['DATABASE'] = os.getenv('DATABASE')

from database.db import init_db
init_db(app.config['DATABASE'])

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

# Start background scanner thread
# Background scanner disabled during development
# Uncomment below for production deployment
# scanner_thread = threading.Thread(
#     target=run_scheduled_scan,
#     args=(app,),
#     daemon=True
# )
# scanner_thread.start()

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
            return render_template('video_scan.html',
                                   violations=[], video_info=None)

        file = request.files['video']
        interval = int(request.form.get('interval', 3))

        if file.filename == '':
            return render_template('video_scan.html',
                                   violations=[], video_info=None)

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
        from routes.video_scanner import extract_keyframes
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
                # Send email alert
                from routes.alerts import send_violation_alert
                send_violation_alert(v['asset_name'], v['similarity'])
            db.commit()
            db.close()

        # Clean up video file
        if os.path.exists(video_path):
            os.remove(video_path)

    return render_template('video_scan.html',
                           violations=violations,
                           video_info=video_info)

@app.route('/blockchain')
def blockchain():
    stats = get_chain_stats()
    chain = load_chain()
    return render_template('blockchain.html', stats=stats, chain=chain)

@app.route('/blockchain/verify')
def blockchain_verify():
    from routes.blockchain import verify_chain
    valid, message = verify_chain()
    from flask import flash
    flash(f"{'✓' if valid else '✗'} {message}")
    return redirect(url_for('blockchain'))

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)