import cv2
import os
import uuid
import numpy as np
from PIL import Image
import imagehash
from flask import current_app


# ============================================================
# FRAME EXTRACTION
# ============================================================

def extract_keyframes(video_path, interval_seconds=2):
    """
    Extract keyframes by SEEKING directly to each interval position
    instead of decoding every frame. This is dramatically faster —
    a 5-minute video at 3s intervals = 100 seeks, not 9000 frame reads.
    Scene change detection runs only on the sampled frames (not all frames).
    """
    keyframes = []
    temp_dir = os.path.join('uploads', 'video_frames')
    os.makedirs(temp_dir, exist_ok=True)

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Could not open video: {video_path}")
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Guard against corrupt/invalid video metadata
        if fps <= 0 or total_frames <= 0:
            print(f"Invalid video metadata: fps={fps}, frames={total_frames}")
            cap.release()
            return []

        duration = total_frames / fps
        print(f"Video: {duration:.1f}s, {fps:.1f}fps, {total_frames} frames")

        # Cap at 120 keyframes max to prevent runaway storage/processing
        max_frames = 120
        effective_interval = max(interval_seconds, duration / max_frames)
        frame_interval = max(1, int(fps * effective_interval))

        prev_gray_small = None  # Store small thumbnail for scene change, not full frame

        # SEEK directly to each target frame — skip all frames in between
        target_frame = 0
        while target_frame < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ret, frame = cap.read()
            if not ret:
                break

            timestamp = target_frame / fps

            # Scene change detection on a small thumbnail (fast)
            small = cv2.resize(frame, (160, 90))
            gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            is_scene_change = False
            if prev_gray_small is not None:
                diff = cv2.absdiff(gray_small, prev_gray_small)
                score = np.mean(diff)
                # Threshold 45 (was 30) — reduces false positives in fast-motion sports
                if score > 45:
                    is_scene_change = True
            prev_gray_small = gray_small

            # Save frame as JPEG (quality 85 — good balance of size vs fidelity)
            frame_filename = f"frame_{uuid.uuid4().hex}.jpg"
            frame_path = os.path.join(temp_dir, frame_filename)
            cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

            keyframes.append({
                'timestamp': round(timestamp, 2),
                'path': frame_path,
                'filename': frame_filename,
                'time_str': format_timestamp(timestamp),
                'is_scene_change': is_scene_change
            })

            target_frame += frame_interval

        cap.release()
        print(f"Extracted {len(keyframes)} keyframes (seek-based, no dedup needed)")
        return keyframes

    except Exception as e:
        print(f"Keyframe extraction error: {e}")
        return []


def extract_and_hash_keyframes(video_path, interval_seconds=3):
    """
    Combined extraction + hashing in one pass.
    Hashes from cv2 frame in memory — no disk re-open needed.
    Returns list of dicts ready for bulk DB insert.
    """
    results = []
    temp_dir = os.path.join('uploads', 'video_frames')
    os.makedirs(temp_dir, exist_ok=True)

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps <= 0 or total_frames <= 0:
            cap.release()
            return []

        duration = total_frames / fps
        print(f"[Register] Video: {duration:.1f}s @ {fps:.1f}fps")

        max_frames = 120
        effective_interval = max(interval_seconds, duration / max_frames)
        frame_interval = max(1, int(fps * effective_interval))
        print(f"[Register] ~{int(total_frames/frame_interval)} keyframes to extract")

        target_frame = 0
        while target_frame < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ret, frame = cap.read()
            if not ret:
                break

            timestamp = round(target_frame / fps, 2)

            # Save JPEG to disk
            frame_filename = f"frame_{uuid.uuid4().hex}.jpg"
            frame_path = os.path.join(temp_dir, frame_filename)
            cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

            # Hash directly from cv2 frame in memory — no re-open
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                phash = str(imagehash.phash(pil_img))
                dhash = str(imagehash.dhash(pil_img))
                ahash = str(imagehash.average_hash(pil_img))
            except Exception as he:
                print(f"[Register] Hash error at frame {target_frame}: {he}")
                target_frame += frame_interval
                continue

            results.append({
                'filename': frame_filename,
                'path': frame_path,
                'timestamp': timestamp,
                'time_str': format_timestamp(timestamp),
                'phash': phash,
                'dhash': dhash,
                'ahash': ahash,
            })
            target_frame += frame_interval

        cap.release()
        print(f"[Register] Done: {len(results)} frames extracted and hashed")
        return results

    except Exception as e:
        print(f"[Register] Error: {e}")
        return []


def deduplicate_frames(keyframes):
    if not keyframes:
        return keyframes
    result = [keyframes[0]]
    for frame in keyframes[1:]:
        if frame['timestamp'] - result[-1]['timestamp'] >= 0.5:
            result.append(frame)
    return result


def format_timestamp(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"


# ============================================================
# GEMINI — MATCH/HIGHLIGHT CONTENT DETECTION
# ============================================================

def analyze_frame_for_match_content(frame_path):
    """
    Uses Groq (llama-4-scout vision) to detect if a frame is from
    a professional sports match or broadcast highlight.
    """
    try:
        import requests as req
        import base64, io

        img = Image.open(frame_path)
        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG')
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        prompt = """You are a sports broadcast forensics expert analyzing video frames.

Analyze this frame and detect if it is from a professional sports match or broadcast highlight.

Look for these indicators:
- Scoreboard or score overlay (team names, scores, timer)
- Broadcast watermarks/logos (Star Sports, JioCinema, ESPN, Sky Sports, etc.)
- Stadium/arena crowd footage
- Match action (players, ball, field markings, pitch)
- Commentary lower-thirds or captions
- IPL/cricket/football/sports specific overlays
- Professional broadcast graphics or transitions
- Referee/umpire presence

Respond ONLY in this exact format (no extra text):
MATCH_CONTENT: YES or NO
CONFIDENCE: 0-100
INDICATORS: comma-separated list of what you found (e.g. scoreboard,stadium crowd,broadcast logo) or NONE
VERDICT: one sentence summary"""

        import google.generativeai as genai
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError('GEMINI_API_KEY not set in .env')
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content([
            {'mime_type': 'image/jpeg', 'data': img_b64},
            prompt
        ])
        text = response.text
        return _parse_gemini_verdict(text)

    except Exception as e:
        print(f"[Gemini] Frame analysis error: {e}")
        return {
            'is_match_content': False,
            'confidence': 0,
            'indicators': [],
            'verdict': f'AI unavailable: {str(e)[:60]}',
            'raw': str(e)
        }


def _parse_gemini_verdict(text):
    result = {
        'is_match_content': False,
        'confidence': 0,
        'indicators': [],
        'verdict': '',
        'raw': text
    }
    try:
        for line in text.strip().splitlines():
            line = line.strip()
            if line.startswith('MATCH_CONTENT:'):
                result['is_match_content'] = 'YES' in line.upper()
            elif line.startswith('CONFIDENCE:'):
                val = line.split(':', 1)[1].strip()
                result['confidence'] = int(''.join(filter(str.isdigit, val)) or '0')
            elif line.startswith('INDICATORS:'):
                val = line.split(':', 1)[1].strip()
                if val.upper() != 'NONE':
                    result['indicators'] = [i.strip() for i in val.split(',') if i.strip()]
            elif line.startswith('VERDICT:'):
                result['verdict'] = line.split(':', 1)[1].strip()
    except Exception as e:
        print(f"[Gemini] Parse error: {e}")
    return result


def analyze_video_for_match_content(keyframes, sample_size=8):
    """
    Samples frames evenly, runs Gemini on each, aggregates into
    a video-level match content verdict.
    """
    if not keyframes:
        return _empty_video_verdict()

    step = max(1, len(keyframes) // sample_size)
    sampled = keyframes[::step][:sample_size]

    print(f"[Gemini] Analyzing {len(sampled)} frames for match content...")

    frame_results = []
    match_frame_count = 0
    confidence_sum = 0
    all_indicators = set()

    for frame in sampled:
        result = analyze_frame_for_match_content(frame['path'])
        result['timestamp'] = frame['time_str']
        result['frame_filename'] = frame['filename']
        frame_results.append(result)

        if result['is_match_content']:
            match_frame_count += 1
            confidence_sum += result['confidence']
            all_indicators.update(result['indicators'])

        print(f"  [{frame['time_str']}] Match:{result['is_match_content']} "
              f"Conf:{result['confidence']}% Indicators:{result['indicators']}")

    total = len(sampled)
    match_pct = round((match_frame_count / total) * 100, 1) if total > 0 else 0
    avg_confidence = round(confidence_sum / match_frame_count, 1) if match_frame_count > 0 else 0

    if match_pct >= 60 and avg_confidence >= 75:
        risk_level = 'CRITICAL'
        is_match = True
        verdict = (f"HIGH CONFIDENCE: This video contains unauthorized sports broadcast content "
                   f"({match_pct}% of frames matched, avg {avg_confidence}% confidence).")
    elif match_pct >= 40 or avg_confidence >= 60:
        risk_level = 'HIGH'
        is_match = True
        verdict = (f"LIKELY VIOLATION: Multiple frames show match/highlight broadcast indicators "
                   f"({match_pct}% match rate, avg {avg_confidence}% confidence).")
    elif match_pct >= 20:
        risk_level = 'MEDIUM'
        is_match = True
        verdict = (f"POSSIBLE VIOLATION: Some frames contain broadcast content — manual review "
                   f"recommended ({match_pct}% match rate).")
    elif match_pct > 0:
        risk_level = 'LOW'
        is_match = False
        verdict = f"LOW RISK: Minimal match indicators detected ({match_pct}% match rate). Likely safe."
    else:
        risk_level = 'SAFE'
        is_match = False
        verdict = "CLEAN: No match or broadcast content detected in this video."

    return {
        'is_match_content': is_match,
        'overall_confidence': avg_confidence,
        'match_frames': match_frame_count,
        'total_frames_analyzed': total,
        'match_percentage': match_pct,
        'all_indicators': sorted(all_indicators),
        'frame_results': frame_results,
        'verdict': verdict,
        'risk_level': risk_level
    }


def _empty_video_verdict():
    return {
        'is_match_content': False,
        'overall_confidence': 0,
        'match_frames': 0,
        'total_frames_analyzed': 0,
        'match_percentage': 0,
        'all_indicators': [],
        'frame_results': [],
        'verdict': 'Could not extract frames from video.',
        'risk_level': 'SAFE'
    }


# ============================================================
# REGION EXTRACTION
# ============================================================

def extract_content_regions(frame_path):
    regions = []
    try:
        img = cv2.imread(frame_path)
        if img is None:
            return regions

        h, w = img.shape[:2]
        regions.append(('full', frame_path))

        cy1, cy2 = int(h * 0.2), int(h * 0.8)
        cx1, cx2 = int(w * 0.2), int(w * 0.8)
        center = img[cy1:cy2, cx1:cx2]
        center_path = frame_path.replace('.jpg', '_center.jpg')
        cv2.imwrite(center_path, center)
        regions.append(('center', center_path))

        tl = img[0:int(h*0.15), 0:int(w*0.2)]
        tl_path = frame_path.replace('.jpg', '_tl.jpg')
        cv2.imwrite(tl_path, tl)
        regions.append(('top_left', tl_path))

        tr = img[0:int(h*0.15), int(w*0.8):w]
        tr_path = frame_path.replace('.jpg', '_tr.jpg')
        cv2.imwrite(tr_path, tr)
        regions.append(('top_right', tr_path))

        bottom = img[int(h*0.85):h, 0:w]
        bot_path = frame_path.replace('.jpg', '_bottom.jpg')
        cv2.imwrite(bot_path, bottom)
        regions.append(('bottom_score', bot_path))

    except Exception as e:
        print(f"Region extraction error: {e}")

    return regions


# ============================================================
# ASSET-BASED SCAN — with robust fingerprint fallback
# ============================================================

def scan_video(video_path, assets, db_path, upload_folder):
    from routes.opencv_detector import combined_opencv_score
    from routes.deeplearning_detector import fast_mobilenet_similarity
    from database.db import get_db
    import imagehash

    violations = []
    keyframes = extract_keyframes(video_path, interval_seconds=3)
    if not keyframes:
        return violations

    # Stage 2 prep: fingerprint suspect frames with CLIP
    print("[Scan] Generating robust fingerprints for suspect video...")
    try:
        from routes.video_fingerprint import (
            fingerprint_frame, compare_video_fingerprints, load_registered_fingerprints
        )
        suspect_fps = [fingerprint_frame(Image.open(kf['path'])) for kf in keyframes]
        print(f"[Scan] Suspect fingerprints ready: {len(suspect_fps)} frames")
    except Exception as e:
        print(f"[Scan] Robust fingerprinting unavailable: {e}")
        suspect_fps = []

    for asset in assets:
        asset_dict = dict(asset)
        asset_violations = []
        stage1_score = 0
        is_video_asset = asset_dict.get('asset_type') == 'VIDEO'

        # ── Stage 1A: IMAGE asset — compare suspect frames vs single registered image ──
        if not is_video_asset:
            asset_path = os.path.join(upload_folder, asset_dict['filename'])
            if os.path.exists(asset_path):
                for frame in keyframes:
                    try:
                        regions = extract_content_regions(frame['path'])
                        best_region_score = 0
                        best_region_name = 'full'
                        for region_name, region_path in regions:
                            try:
                                img1 = Image.open(asset_path)
                                img2 = Image.open(region_path)
                                h1 = imagehash.phash(img1)
                                h2 = imagehash.phash(img2)
                                hash_score = max(0, (1 - (h1 - h2) / 64) * 100)
                                opencv_score = combined_opencv_score(asset_path, region_path)
                                dl_score = fast_mobilenet_similarity(asset_path, region_path)
                                if dl_score > 0 and opencv_score > 0:
                                    rs = (hash_score*0.2)+(opencv_score*0.3)+(dl_score*0.5)
                                elif opencv_score > 0:
                                    rs = (hash_score*0.3)+(opencv_score*0.7)
                                else:
                                    rs = hash_score
                                rs = round(rs, 2)
                                if rs > best_region_score:
                                    best_region_score = rs
                                    best_region_name = region_name
                                if region_name != 'full' and os.path.exists(region_path):
                                    os.remove(region_path)
                            except Exception:
                                continue
                        threshold = 60 if best_region_name in ['center', 'full'] else 70
                        if best_region_score > threshold:
                            asset_violations.append({
                                'timestamp': frame['timestamp'],
                                'time_str': frame['time_str'],
                                'frame_path': frame['path'],
                                'frame_filename': frame['filename'],
                                'similarity': best_region_score,
                                'matched_region': best_region_name,
                                'asset_name': asset_dict['name'],
                                'asset_id': asset_dict['id'],
                                'is_scene_change': frame.get('is_scene_change', False),
                                'detection_method': 'HASH+OPENCV+DL',
                            })
                        stage1_score = max(stage1_score, best_region_score)
                    except Exception:
                        continue

        # ── Stage 1B: VIDEO asset — compare suspect frames vs registered keyframe hashes ──
        else:
            try:
                db = get_db(db_path)
                reg_frames = db.execute(
                    'SELECT * FROM video_frames WHERE asset_id = ? ORDER BY timestamp',
                    (asset_dict['id'],)
                ).fetchall()
                db.close()

                # Cap to max 60 evenly-spaced registered frames to keep comparison fast
                if len(reg_frames) > 60:
                    step = len(reg_frames) // 60
                    reg_frames = reg_frames[::step][:60]

                print(f"[Scan] VIDEO asset '{asset_dict['name']}': {len(reg_frames)} registered frames to compare")

                frame_dir = os.path.join(upload_folder, 'video_frames')
                for sus_frame in keyframes:
                    best_score = 0
                    try:
                        sus_img = Image.open(sus_frame['path'])
                        sus_ph = imagehash.phash(sus_img)
                        sus_dh = imagehash.dhash(sus_img)
                        sus_ah = imagehash.average_hash(sus_img)
                    except Exception:
                        continue

                    for reg in reg_frames:
                        reg_dict = dict(reg)
                        try:
                            # Hash comparison against stored hashes (no disk I/O needed)
                            ph_score = max(0, (1 - (sus_ph - imagehash.hex_to_hash(reg_dict['phash'])) / 64) * 100) if reg_dict.get('phash') else 0
                            dh_score = max(0, (1 - (sus_dh - imagehash.hex_to_hash(reg_dict['dhash'])) / 64) * 100) if reg_dict.get('dhash') else 0
                            ah_score = max(0, (1 - (sus_ah - imagehash.hex_to_hash(reg_dict['ahash'])) / 64) * 100) if reg_dict.get('ahash') else 0
                            hash_score = round((ph_score * 0.5 + dh_score * 0.3 + ah_score * 0.2), 2)

                            # ── Fast pre-filter: skip expensive ops if hashes don't match ──
                            # If hash score < 40, images are too different — no point running SIFT/MobileNet
                            if hash_score < 40:
                                score = hash_score
                            else:
                                # Also try image comparison if registered frame file exists
                                reg_frame_path = os.path.join(frame_dir, reg_dict['frame_filename'])
                                if os.path.exists(reg_frame_path):
                                    opencv_score = combined_opencv_score(reg_frame_path, sus_frame['path'])
                                    dl_score = fast_mobilenet_similarity(reg_frame_path, sus_frame['path'])
                                    if dl_score > 0 and opencv_score > 0:
                                        score = (hash_score*0.2)+(opencv_score*0.3)+(dl_score*0.5)
                                    elif opencv_score > 0:
                                        score = (hash_score*0.3)+(opencv_score*0.7)
                                    else:
                                        score = hash_score
                                else:
                                    score = hash_score

                            score = round(score, 2)
                            if score > best_score:
                                best_score = score
                            # Early exit — no need to check more registered frames
                            if best_score >= 90:
                                break
                        except Exception:
                            continue

                    stage1_score = max(stage1_score, best_score)
                    if best_score >= 60:
                        asset_violations.append({
                            'timestamp': sus_frame['timestamp'],
                            'time_str': sus_frame['time_str'],
                            'frame_path': sus_frame['path'],
                            'frame_filename': sus_frame['filename'],
                            'similarity': best_score,
                            'matched_region': 'video_frame_hash',
                            'asset_name': asset_dict['name'],
                            'asset_id': asset_dict['id'],
                            'is_scene_change': sus_frame.get('is_scene_change', False),
                            'detection_method': 'VIDEO_FRAME_HASH+OPENCV+DL',
                        })
                        print(f"[Scan] Match at {sus_frame['time_str']}: {best_score}%")
            except Exception as e:
                print(f"[Scan] Stage1B error for VIDEO asset: {e}")

        # Stage 2: robust CLIP + DTW (only for VIDEO assets with stored fingerprints)
        stage2_score = 0
        stage2_result = {}
        if suspect_fps and asset_dict.get('asset_type') == 'VIDEO':
            try:
                db = get_db(db_path)
                rows = db.execute(
                    'SELECT * FROM video_fingerprints WHERE asset_id = ? ORDER BY frame_index',
                    (asset_dict['id'],)
                ).fetchall()
                db.close()
                if rows:
                    from routes.video_fingerprint import load_registered_fingerprints
                    reg_fps = load_registered_fingerprints(rows)
                    stage2_result = compare_video_fingerprints(suspect_fps, reg_fps)
                    stage2_score = stage2_result.get('final', 0)
                    print(f"[Scan] '{asset_dict['name']}' S1:{stage1_score}% "
                          f"S2:{stage2_score}% "
                          f"(DTW:{stage2_result.get('dtw_sim',0)}% "
                          f"MR:{stage2_result.get('match_rate',0)}%)")
            except Exception as e:
                print(f"[Scan] Stage2 error: {e}")

        # Final = best of both stages
        if stage2_score > 60 and stage2_score > stage1_score:
            asset_violations.append({
                'timestamp': 0,
                'time_str': '00:00',
                'frame_path': keyframes[0]['path'] if keyframes else '',
                'frame_filename': keyframes[0]['filename'] if keyframes else '',
                'similarity': stage2_score,
                'matched_region': 'video_fingerprint',
                'asset_name': asset_dict['name'],
                'asset_id': asset_dict['id'],
                'is_scene_change': False,
                'detection_method': (
                    f"CLIP+DTW (DTW:{stage2_result.get('dtw_sim',0)}% "
                    f"MR:{stage2_result.get('match_rate',0)}%)"
                ),
            })

        if asset_violations:
            violations.extend(asset_violations)

    cleanup_frames(keyframes, keep_violations=violations)
    return violations
def cleanup_frames(keyframes, keep_violations=None):
    keep_paths = set()
    if keep_violations:
        keep_paths = {v['frame_path'] for v in keep_violations}

    for frame in keyframes:
        if frame['path'] not in keep_paths:
            try:
                if os.path.exists(frame['path']):
                    os.remove(frame['path'])
            except:
                pass