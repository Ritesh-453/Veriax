import cv2
import os
import uuid
import numpy as np
from PIL import Image
from flask import current_app


def extract_keyframes(video_path, interval_seconds=2):
    """
    Extract keyframes from video every N seconds.
    Also extracts scene-change frames for better coverage of edited clips.
    Returns list of frame dicts.
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
        duration = total_frames / fps if fps > 0 else 0

        print(f"Video: {duration:.1f}s, {fps:.1f}fps, {total_frames} frames")

        frame_interval = max(1, int(fps * interval_seconds))
        prev_gray = None
        frame_count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            is_interval_frame = (frame_count % frame_interval == 0)

            # Scene change detection — catches cuts in edited highlight clips
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            is_scene_change = False
            if prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray)
                score = np.mean(diff)
                if score > 30:  # threshold for scene cut
                    is_scene_change = True
            prev_gray = gray

            if is_interval_frame or is_scene_change:
                timestamp = frame_count / fps if fps > 0 else 0
                frame_filename = f"frame_{uuid.uuid4().hex}.jpg"
                frame_path = os.path.join(temp_dir, frame_filename)
                cv2.imwrite(frame_path, frame)
                keyframes.append({
                    'timestamp': round(timestamp, 2),
                    'path': frame_path,
                    'filename': frame_filename,
                    'time_str': format_timestamp(timestamp),
                    'is_scene_change': is_scene_change
                })

            frame_count += 1

        cap.release()
        print(f"Extracted {len(keyframes)} keyframes (interval + scene changes)")

        # Deduplicate very close frames (within 0.5s)
        keyframes = deduplicate_frames(keyframes)
        print(f"After dedup: {len(keyframes)} frames")
        return keyframes

    except Exception as e:
        print(f"Keyframe extraction error: {e}")
        return []


def deduplicate_frames(keyframes):
    """Remove duplicate frames that are too close together in time."""
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


def extract_content_regions(frame_path):
    """
    Extract multiple regions from a frame:
    - Full frame
    - Center crop (main content, ignores borders/logos)
    - 4 corner crops (catches watermarks even if partially removed)
    - Logo zones (top-left, top-right corners — common watermark positions)

    This helps detect violations even when the main logo is cropped/removed
    by checking the actual gameplay content in the center.
    """
    regions = []
    try:
        img = cv2.imread(frame_path)
        if img is None:
            return regions

        h, w = img.shape[:2]

        # Full frame
        regions.append(('full', frame_path))

        # Center 60% crop — main gameplay content
        cy1, cy2 = int(h * 0.2), int(h * 0.8)
        cx1, cx2 = int(w * 0.2), int(w * 0.8)
        center = img[cy1:cy2, cx1:cx2]
        center_path = frame_path.replace('.jpg', '_center.jpg')
        cv2.imwrite(center_path, center)
        regions.append(('center', center_path))

        # Top-left watermark zone (Star Sports / JioCinema logo position)
        tl = img[0:int(h*0.15), 0:int(w*0.2)]
        tl_path = frame_path.replace('.jpg', '_tl.jpg')
        cv2.imwrite(tl_path, tl)
        regions.append(('top_left', tl_path))

        # Top-right watermark zone
        tr = img[0:int(h*0.15), int(w*0.8):w]
        tr_path = frame_path.replace('.jpg', '_tr.jpg')
        cv2.imwrite(tr_path, tr)
        regions.append(('top_right', tr_path))

        # Bottom strip — scoreboard area (IPL scorecard overlay)
        bottom = img[int(h*0.85):h, 0:w]
        bot_path = frame_path.replace('.jpg', '_bottom.jpg')
        cv2.imwrite(bot_path, bottom)
        regions.append(('bottom_score', bot_path))

    except Exception as e:
        print(f"Region extraction error: {e}")

    return regions


def scan_video(video_path, assets, db_path, upload_folder):
    """
    Scan video for violations against registered assets.
    Enhanced: checks multiple regions per frame to detect logo-removed/cropped clips.
    Returns list of violations with timestamps.
    """
    from routes.opencv_detector import combined_opencv_score
    from routes.deeplearning_detector import fast_mobilenet_similarity
    import imagehash

    violations = []
    keyframes = extract_keyframes(video_path, interval_seconds=2)

    if not keyframes:
        return violations

    print(f"Scanning {len(keyframes)} frames against {len(assets)} assets...")

    for asset in assets:
        asset_path = os.path.join(upload_folder, asset['filename'])
        if not os.path.exists(asset_path):
            continue

        asset_violations = []

        for frame in keyframes:
            try:
                # Get multiple regions from frame
                regions = extract_content_regions(frame['path'])
                best_region_score = 0
                best_region_name = 'full'

                for region_name, region_path in regions:
                    try:
                        # Hash comparison
                        img1 = Image.open(asset_path)
                        img2 = Image.open(region_path)
                        hash1 = imagehash.phash(img1)
                        hash2 = imagehash.phash(img2)
                        hash_score = max(0, (1 - (hash1 - hash2) / 64) * 100)

                        # OpenCV
                        opencv_score = combined_opencv_score(asset_path, region_path)

                        # MobileNet
                        dl_score = fast_mobilenet_similarity(asset_path, region_path)

                        # Combined
                        if dl_score > 0 and opencv_score > 0:
                            region_score = (hash_score*0.2) + (opencv_score*0.3) + (dl_score*0.5)
                        elif opencv_score > 0:
                            region_score = (hash_score*0.3) + (opencv_score*0.7)
                        else:
                            region_score = hash_score

                        region_score = round(region_score, 2)

                        if region_score > best_region_score:
                            best_region_score = region_score
                            best_region_name = region_name

                        # Clean up temp region files (not full frame)
                        if region_name != 'full' and os.path.exists(region_path):
                            os.remove(region_path)

                    except Exception as re:
                        print(f"Region scan error ({region_name}): {re}")
                        continue

                # Lower threshold for content-matched regions (center crop)
                # This catches clips where the logo is removed but content is same
                threshold = 60 if best_region_name in ['center', 'full'] else 70

                if best_region_score > threshold:
                    print(f"[{best_region_name}] Match at {frame['time_str']}: {best_region_score}%")
                    asset_violations.append({
                        'timestamp': frame['timestamp'],
                        'time_str': frame['time_str'],
                        'frame_path': frame['path'],
                        'frame_filename': frame['filename'],
                        'similarity': best_region_score,
                        'matched_region': best_region_name,
                        'asset_name': asset['name'],
                        'asset_id': asset['id'],
                        'is_scene_change': frame.get('is_scene_change', False)
                    })

            except Exception as e:
                print(f"Frame scan error: {e}")
                continue

        if asset_violations:
            violations.extend(asset_violations)
            print(f"Found {len(asset_violations)} violations for {asset['name']}")

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
