from flask import Blueprint, render_template, request, jsonify, current_app, flash, redirect, url_for
from database.db import get_db
from database.firebase_db import save_asset_firebase, save_violation_firebase
from routes.blockchain import add_asset_block, add_violation_block
from routes.alerts import send_violation_alert
from routes.watermark import embed_watermark
from PIL import Image
import imagehash
import requests
import os
import uuid
from datetime import datetime
import threading

social_bp = Blueprint('social', __name__)

# ============================================================
# EXCEPTION CHECKING
# ============================================================

def is_exception_account(platform, account_id, account_name, db_path):
    try:
        db = get_db(db_path)
        result = db.execute('''
            SELECT id FROM exceptions
            WHERE (platform = ? OR platform = 'all') AND (
                LOWER(account_id) = LOWER(?) OR
                LOWER(account_name) = LOWER(?)
            )
        ''', (platform, account_id, account_name)).fetchone()
        db.close()
        return result is not None
    except:
        return False

def is_org_account(platform, account_id, account_name, db_path):
    """Check if this is an organisation's OWN account — auto-register their posts"""
    try:
        db = get_db(db_path)
        result = db.execute('''
            SELECT id FROM monitored_accounts
            WHERE (platform = ? OR platform = 'all')
            AND account_type = 'ORG_OWNED'
            AND (LOWER(account_id) = LOWER(?) OR LOWER(account_name) = LOWER(?))
        ''', (platform, account_id, account_name)).fetchone()
        db.close()
        return result is not None
    except:
        return False

# ============================================================
# AUTO REGISTER ASSET
# ============================================================

def auto_register_asset(name, image_url, platform, post_url, db_path, upload_folder):
    """
    Automatically register an image/video thumbnail as a protected asset.
    Called when an org account posts new content.
    """
    try:
        print(f"[AUTO-REGISTER] {name} from {platform}")

        # Download the image
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(image_url, timeout=10, headers=headers)
        if response.status_code != 200:
            print(f"Failed to download: {image_url}")
            return None

        filename = f"social_{platform}_{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(upload_folder, filename)
        with open(filepath, 'wb') as f:
            f.write(response.content)

        # Generate hashes
        img = Image.open(filepath)
        hashes = {
            'phash': str(imagehash.phash(img)),
            'dhash': str(imagehash.dhash(img)),
            'ahash': str(imagehash.average_hash(img))
        }

        # Check if already registered (avoid duplicates)
        db = get_db(db_path)
        existing = db.execute(
            'SELECT id FROM assets WHERE phash = ?', (hashes['phash'],)
        ).fetchone()

        if existing:
            print(f"[AUTO-REGISTER] Already registered: {name}")
            db.close()
            return existing['id']

        # Embed watermark
        wm_path, wm_text = embed_watermark(filepath, name, upload_folder)
        wm_filename = os.path.basename(wm_path) if wm_path else filename

        # Save to SQLite
        cursor = db.execute('''
            INSERT INTO assets (name, filename, phash, dhash, ahash, watermark_file)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, filename, hashes['phash'], hashes['dhash'],
              hashes['ahash'], wm_filename))
        asset_id = cursor.lastrowid
        db.commit()
        db.close()

        # Save to Firebase
        save_asset_firebase(name, filename, hashes['phash'],
                            hashes['dhash'], hashes['ahash'])

        # Add to blockchain
        add_asset_block(name, filename, hashes['phash'])

        print(f"[AUTO-REGISTER] Success! Asset ID: {asset_id} — {name}")
        return asset_id

    except Exception as e:
        print(f"[AUTO-REGISTER] Error: {e}")
        return None

# ============================================================
# COMPARE AGAINST REGISTRY
# ============================================================

def compare_against_registry(image_path, db_path, upload_folder):
    try:
        from routes.opencv_detector import combined_opencv_score
        from routes.deeplearning_detector import fast_mobilenet_similarity

        db = get_db(db_path)
        assets = db.execute('SELECT * FROM assets').fetchall()
        db.close()

        results = []
        img = Image.open(image_path)
        scan_phash = str(imagehash.phash(img))
        scan_dhash = str(imagehash.dhash(img))
        scan_ahash = str(imagehash.average_hash(img))

        for asset in assets:
            try:
                p1 = imagehash.hex_to_hash(asset['phash'])
                d1 = imagehash.hex_to_hash(asset['dhash'])
                a1 = imagehash.hex_to_hash(asset['ahash'])
                p2 = imagehash.hex_to_hash(scan_phash)
                d2 = imagehash.hex_to_hash(scan_dhash)
                a2 = imagehash.hex_to_hash(scan_ahash)

                hash_score = round(
                    max(0,(1-(p1-p2)/64)*100)*0.5 +
                    max(0,(1-(d1-d2)/64)*100)*0.3 +
                    max(0,(1-(a1-a2)/64)*100)*0.2, 2
                )

                asset_path = os.path.join(upload_folder, asset['filename'])
                opencv_score = 0
                dl_score = 0

                if os.path.exists(asset_path):
                    opencv_score = combined_opencv_score(asset_path, image_path)
                    dl_score = fast_mobilenet_similarity(asset_path, image_path)

                if dl_score > 0 and opencv_score > 0:
                    final = (hash_score*0.2)+(opencv_score*0.3)+(dl_score*0.5)
                elif opencv_score > 0:
                    final = (hash_score*0.3)+(opencv_score*0.7)
                else:
                    final = hash_score

                final = round(final, 2)

                if final > 65:
                    results.append({
                        'asset_id': asset['id'],
                        'asset_name': asset['name'],
                        'similarity': final,
                        'risk': 'CRITICAL' if final>=90 else 'HIGH' if final>=70 else 'MEDIUM'
                    })
            except:
                continue

        return sorted(results, key=lambda x: x['similarity'], reverse=True)

    except Exception as e:
        print(f"Comparison error: {e}")
        return []

# ============================================================
# SAVE SOCIAL POST RECORD
# ============================================================

def save_social_post(platform, post_id, account_id, account_name,
                     post_url, media_url, caption, post_type,
                     violation_found, similarity, db_path):
    try:
        db = get_db(db_path)
        # Check duplicate
        existing = db.execute(
            'SELECT id FROM social_posts WHERE post_id=? AND platform=?',
            (post_id, platform)
        ).fetchone()
        if existing:
            db.close()
            return
        db.execute('''
            INSERT INTO social_posts
            (platform, post_id, account_id, account_name, post_url,
             media_url, caption, post_type, scan_status,
             violation_found, similarity, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (platform, post_id, account_id, account_name, post_url,
              media_url, caption[:200] if caption else '', post_type,
              'SCANNED', 1 if violation_found else 0,
              similarity, datetime.now().isoformat()))
        db.commit()
        db.close()
    except Exception as e:
        print(f"Save post error: {e}")

# ============================================================
# YOUTUBE INTEGRATION
# ============================================================

def fetch_youtube_channel_posts(channel_id_or_name, api_key, db_path, upload_folder):
    """
    Fetch latest posts from a YouTube channel.
    If it's an org channel → auto-register.
    If it's someone else → scan for violations.
    """
    results = {'registered': [], 'violations': [], 'errors': []}

    try:
        # Search for channel
        search_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            'key': api_key,
            'channelId': channel_id_or_name,
            'part': 'snippet',
            'type': 'video',
            'maxResults': 10,
            'order': 'date'
        }
        response = requests.get(search_url, params=params, timeout=10)
        data = response.json()
        items = data.get('items', [])

        if not items:
            # Try searching by name
            params2 = {
                'key': api_key,
                'q': channel_id_or_name,
                'part': 'snippet',
                'type': 'video',
                'maxResults': 10,
                'order': 'date'
            }
            response2 = requests.get(search_url, params=params2, timeout=10)
            items = response2.json().get('items', [])

        for item in items:
            try:
                video_id = item['id'].get('videoId', '')
                if not video_id:
                    continue

                snippet = item['snippet']
                channel_name = snippet.get('channelTitle', '')
                channel_id = snippet.get('channelId', '')
                title = snippet.get('title', '')
                post_url = f"https://youtube.com/watch?v={video_id}"
                thumbnail_url = snippet.get('thumbnails', {}).get(
                    'high', {}
                ).get('url', f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg")

                print(f"[YOUTUBE] Processing: {title[:50]} by {channel_name}")

                # Is this an org's own account?
                if is_org_account('youtube', channel_id, channel_name, db_path):
                    print(f"[YOUTUBE] ORG ACCOUNT — Auto-registering: {title}")
                    asset_id = auto_register_asset(
                        f"{channel_name} — {title[:40]}",
                        thumbnail_url, 'youtube', post_url,
                        db_path, upload_folder
                    )
                    if asset_id:
                        results['registered'].append({
                            'title': title,
                            'channel': channel_name,
                            'url': post_url,
                            'asset_id': asset_id
                        })
                    save_social_post(
                        'youtube', video_id, channel_id, channel_name,
                        post_url, thumbnail_url, title, 'VIDEO',
                        False, 0, db_path
                    )
                    continue

                # Is this an exception account?
                if is_exception_account('youtube', channel_id, channel_name, db_path):
                    print(f"[YOUTUBE] EXCEPTION — Skipping: {channel_name}")
                    continue

                # Regular account — scan for violations
                try:
                    img_response = requests.get(
                        thumbnail_url, timeout=8,
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    if img_response.status_code == 200:
                        temp_path = os.path.join(
                            upload_folder, f"yt_scan_{uuid.uuid4().hex}.jpg"
                        )
                        with open(temp_path, 'wb') as f:
                            f.write(img_response.content)

                        violations = compare_against_registry(
                            temp_path, db_path, upload_folder
                        )

                        if violations:
                            top = violations[0]
                            results['violations'].append({
                                'platform': 'youtube',
                                'title': title,
                                'channel': channel_name,
                                'url': post_url,
                                'similarity': top['similarity'],
                                'asset_name': top['asset_name']
                            })

                            save_social_post(
                                'youtube', video_id, channel_id, channel_name,
                                post_url, thumbnail_url, title, 'VIDEO',
                                True, top['similarity'], db_path
                            )

                            # Save violation
                            db = get_db(db_path)
                            db.execute(
                                'INSERT INTO violations (asset_id, found_url, similarity) VALUES (?, ?, ?)',
                                (top['asset_id'], post_url, top['similarity'])
                            )
                            db.commit()
                            db.close()

                            save_violation_firebase(
                                top['asset_id'], top['asset_name'],
                                top['similarity'], temp_path
                            )
                            add_violation_block(
                                top['asset_name'], top['similarity'],
                                ['Hash', 'OpenCV', 'MobileNet'],
                                post_url, 'YOUTUBE'
                            )
                            send_violation_alert(
                                top['asset_name'], top['similarity'], post_url
                            )
                        else:
                            save_social_post(
                                'youtube', video_id, channel_id, channel_name,
                                post_url, thumbnail_url, title, 'VIDEO',
                                False, 0, db_path
                            )

                        if os.path.exists(temp_path):
                            os.remove(temp_path)

                except Exception as e:
                    print(f"[YOUTUBE] Scan error: {e}")

            except Exception as e:
                print(f"[YOUTUBE] Item error: {e}")
                continue

    except Exception as e:
        print(f"[YOUTUBE] Channel error: {e}")
        results['errors'].append(str(e))

    return results

# ============================================================
# INSTAGRAM INTEGRATION
# ============================================================

def fetch_instagram_posts(username, db_path, upload_folder):
    """
    Fetch and process Instagram posts for a given username.
    Org accounts → auto-register. Others → scan for violations.
    """
    results = {'registered': [], 'violations': [], 'errors': []}

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        url = f"https://www.instagram.com/{username}/?__a=1&__d=dis"
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            results['errors'].append(f"Could not fetch Instagram: {username}")
            return results

        data = response.json()
        user_data = data.get('graphql', {}).get('user', {})
        user_id = user_data.get('id', username)
        full_name = user_data.get('full_name', username)
        edges = user_data.get('edge_owner_to_timeline_media', {}).get('edges', [])

        is_org = is_org_account('instagram', user_id, username, db_path)
        is_exception = is_exception_account('instagram', user_id, username, db_path)

        if is_exception:
            print(f"[INSTAGRAM] EXCEPTION — Skipping: {username}")
            return results

        for edge in edges[:10]:
            try:
                node = edge.get('node', {})
                post_id = node.get('id', '')
                shortcode = node.get('shortcode', '')
                post_url = f"https://instagram.com/p/{shortcode}"
                img_url = node.get('display_url', '')
                caption_edges = node.get('edge_media_to_caption', {}).get('edges', [])
                caption = caption_edges[0].get('node', {}).get('text', '') if caption_edges else ''

                if not img_url:
                    continue

                print(f"[INSTAGRAM] Processing post: {shortcode} by {username}")

                if is_org:
                    print(f"[INSTAGRAM] ORG ACCOUNT — Auto-registering")
                    asset_id = auto_register_asset(
                        f"{full_name} — {caption[:40] if caption else shortcode}",
                        img_url, 'instagram', post_url, db_path, upload_folder
                    )
                    if asset_id:
                        results['registered'].append({
                            'post_id': shortcode,
                            'caption': caption[:60],
                            'url': post_url,
                            'asset_id': asset_id
                        })
                    save_social_post(
                        'instagram', post_id, user_id, username,
                        post_url, img_url, caption, 'IMAGE',
                        False, 0, db_path
                    )
                    continue

                # Scan for violations
                img_response = requests.get(img_url, timeout=8, headers=headers)
                if img_response.status_code == 200:
                    temp_path = os.path.join(
                        upload_folder, f"ig_scan_{uuid.uuid4().hex}.jpg"
                    )
                    with open(temp_path, 'wb') as f:
                        f.write(img_response.content)

                    violations = compare_against_registry(
                        temp_path, db_path, upload_folder
                    )

                    if violations:
                        top = violations[0]
                        results['violations'].append({
                            'platform': 'instagram',
                            'account': username,
                            'url': post_url,
                            'caption': caption[:60],
                            'similarity': top['similarity'],
                            'asset_name': top['asset_name']
                        })

                        save_social_post(
                            'instagram', post_id, user_id, username,
                            post_url, img_url, caption, 'IMAGE',
                            True, top['similarity'], db_path
                        )

                        db = get_db(db_path)
                        db.execute(
                            'INSERT INTO violations (asset_id, found_url, similarity) VALUES (?, ?, ?)',
                            (top['asset_id'], post_url, top['similarity'])
                        )
                        db.commit()
                        db.close()

                        send_violation_alert(
                            top['asset_name'], top['similarity'], post_url
                        )
                    else:
                        save_social_post(
                            'instagram', post_id, user_id, username,
                            post_url, img_url, caption, 'IMAGE',
                            False, 0, db_path
                        )

                    if os.path.exists(temp_path):
                        os.remove(temp_path)

            except Exception as e:
                print(f"[INSTAGRAM] Post error: {e}")
                continue

    except Exception as e:
        print(f"[INSTAGRAM] Error: {e}")
        results['errors'].append(str(e))

    return results

# ============================================================
# RUN ALL PLATFORMS
# ============================================================

def run_full_social_scan(db_path, upload_folder, youtube_api_key):
    """Run scan across all monitored accounts on all platforms"""
    print(f"[SOCIAL SCAN] Starting full scan at {datetime.now()}")

    db = get_db(db_path)
    monitored = db.execute(
        'SELECT * FROM monitored_accounts WHERE is_active=1'
    ).fetchall()
    db.close()

    all_results = {
        'registered': [],
        'violations': [],
        'total_scanned': 0
    }

    for account in monitored:
        platform = account['platform']
        account_id = account['account_id']
        account_name = account['account_name']

        print(f"[SOCIAL SCAN] Processing: {account_name} on {platform}")

        if platform == 'youtube' and youtube_api_key:
            results = fetch_youtube_channel_posts(
                account_id, youtube_api_key, db_path, upload_folder
            )
            all_results['registered'].extend(results.get('registered', []))
            all_results['violations'].extend(results.get('violations', []))

        elif platform == 'instagram':
            results = fetch_instagram_posts(account_id, db_path, upload_folder)
            all_results['registered'].extend(results.get('registered', []))
            all_results['violations'].extend(results.get('violations', []))

        all_results['total_scanned'] += 1

    print(f"[SOCIAL SCAN] Done. Registered: {len(all_results['registered'])}, Violations: {len(all_results['violations'])}")
    return all_results

# ============================================================
# ROUTES
# ============================================================

@social_bp.route('/social')
def social_dashboard():
    db = get_db(current_app.config['DATABASE'])
    posts = db.execute(
        'SELECT * FROM social_posts ORDER BY detected_at DESC LIMIT 50'
    ).fetchall()
    exceptions = db.execute(
        'SELECT * FROM exceptions ORDER BY added_at DESC'
    ).fetchall()
    monitored = db.execute(
        'SELECT * FROM monitored_accounts ORDER BY added_at DESC'
    ).fetchall()
    stats = {
        'total_posts': db.execute('SELECT COUNT(*) FROM social_posts').fetchone()[0],
        'violations': db.execute('SELECT COUNT(*) FROM social_posts WHERE violation_found=1').fetchone()[0],
        'youtube': db.execute('SELECT COUNT(*) FROM social_posts WHERE platform="youtube"').fetchone()[0],
        'instagram': db.execute('SELECT COUNT(*) FROM social_posts WHERE platform="instagram"').fetchone()[0],
        'registered': db.execute('SELECT COUNT(*) FROM social_posts WHERE violation_found=0').fetchone()[0],
    }
    db.close()
    return render_template('social.html',
                           posts=posts, exceptions=exceptions,
                           monitored=monitored, stats=stats)

@social_bp.route('/social/scan', methods=['POST'])
def run_social_scan():
    platform = request.form.get('platform', 'all')
    youtube_api_key = os.getenv('YOUTUBE_API_KEY', '')
    db_path = current_app.config['DATABASE']
    upload_folder = current_app.config['UPLOAD_FOLDER']

    results = run_full_social_scan(db_path, upload_folder, youtube_api_key)

    return jsonify({
        'status': 'success',
        'registered': len(results['registered']),
        'violations_found': len(results['violations']),
        'total_scanned': results['total_scanned'],
        'message': f"Scan complete. Auto-registered {len(results['registered'])} assets. Found {len(results['violations'])} violations."
    })

@social_bp.route('/social/exceptions/add', methods=['POST'])
def add_exception():
    platform = request.form.get('platform')
    account_id = request.form.get('account_id')
    account_name = request.form.get('account_name')
    reason = request.form.get('reason', 'Authorized account')

    if not all([platform, account_id, account_name]):
        flash('All fields are required!')
        return redirect(url_for('social.social_dashboard'))

    db = get_db(current_app.config['DATABASE'])
    existing = db.execute(
        'SELECT id FROM exceptions WHERE platform=? AND LOWER(account_id)=LOWER(?)',
        (platform, account_id)
    ).fetchone()

    if existing:
        flash(f'{account_name} is already in exceptions!')
        db.close()
        return redirect(url_for('social.social_dashboard'))

    db.execute(
        'INSERT INTO exceptions (platform, account_id, account_name, reason) VALUES (?, ?, ?, ?)',
        (platform, account_id, account_name, reason)
    )
    db.commit()
    db.close()
    flash(f'Added {account_name} to exceptions — they will never receive copyright claims!')
    return redirect(url_for('social.social_dashboard'))

@social_bp.route('/social/exceptions/delete/<int:exc_id>', methods=['POST'])
def delete_exception(exc_id):
    db = get_db(current_app.config['DATABASE'])
    db.execute('DELETE FROM exceptions WHERE id=?', (exc_id,))
    db.commit()
    db.close()
    flash('Exception removed!')
    return redirect(url_for('social.social_dashboard'))

@social_bp.route('/social/monitor/add', methods=['POST'])
def add_monitored_account():
    platform = request.form.get('platform')
    account_id = request.form.get('account_id')
    account_name = request.form.get('account_name')
    account_type = request.form.get('account_type', 'MONITOR')

    if not all([platform, account_id, account_name]):
        flash('All fields are required!')
        return redirect(url_for('social.social_dashboard'))

    db = get_db(current_app.config['DATABASE'])
    db.execute('''
        INSERT INTO monitored_accounts
        (platform, account_id, account_name, account_type)
        VALUES (?, ?, ?, ?)
    ''', (platform, account_id, account_name, account_type))
    db.commit()
    db.close()

    type_msg = "Their posts will be AUTO-REGISTERED as protected assets!" if account_type == 'ORG_OWNED' else "Their posts will be monitored for violations."
    flash(f'Added {account_name} on {platform}. {type_msg}')
    return redirect(url_for('social.social_dashboard'))

@social_bp.route('/social/monitor/delete/<int:acc_id>', methods=['POST'])
def delete_monitored(acc_id):
    db = get_db(current_app.config['DATABASE'])
    db.execute('DELETE FROM monitored_accounts WHERE id=?', (acc_id,))
    db.commit()
    db.close()
    flash('Account removed from monitoring!')
    return redirect(url_for('social.social_dashboard'))