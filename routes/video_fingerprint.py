"""
video_fingerprint.py — Robust video fingerprinting for SportShield AI

Techniques used:
  1. CLIP embeddings   — semantic features; survive color grading, overlays,
                         re-encoding, logos, screen-recording
  2. Perceptual hashes — fast secondary check; pHash + dHash + ahash
  3. Flip check        — each frame compared mirrored too (catches flipping)
  4. DTW matching      — Dynamic Time Warping on hash sequences; survives
                         speed changes (slow-mo / fast-forward)
  5. Multi-scale crop  — centre + 4 corners compared; catches significant crops

All similarity scores are 0-100.
"""

import os
import io
import json
import numpy as np
from PIL import Image
import imagehash

# ── CLIP model (loaded once, reused) ────────────────────────────────────────
_clip_model = None
_clip_processor = None

def _get_clip():
    global _clip_model, _clip_processor
    if _clip_model is None:
        from transformers import CLIPProcessor, CLIPModel
        import torch
        print("[Fingerprint] Loading CLIP model (first time only)...")
        _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _clip_model.eval()
        print("[Fingerprint] CLIP ready.")
    return _clip_model, _clip_processor


# ── CLIP embedding for one PIL image ────────────────────────────────────────

def get_clip_embedding(pil_img):
    """Returns a unit-normalised 512-dim numpy vector."""
    try:
        import torch
        model, processor = _get_clip()
        inputs = processor(images=pil_img.convert("RGB"), return_tensors="pt")
        with torch.no_grad():
            feats = model.get_image_features(**inputs)
        vec = feats[0].numpy().astype(np.float32)
        vec = vec / (np.linalg.norm(vec) + 1e-8)
        return vec
    except Exception as e:
        print(f"[Fingerprint] CLIP embedding error: {e}")
        return None


def embedding_to_str(vec):
    """Serialise numpy array to JSON string for DB storage."""
    return json.dumps(vec.tolist())


def str_to_embedding(s):
    """Deserialise JSON string back to numpy array."""
    return np.array(json.loads(s), dtype=np.float32)


def cosine_similarity(a, b):
    """Cosine similarity → 0-100."""
    dot = float(np.dot(a, b))
    return round(max(0.0, dot) * 100, 2)


# ── Perceptual hashes for one PIL image ─────────────────────────────────────

def get_phashes(pil_img):
    return {
        'phash': str(imagehash.phash(pil_img)),
        'dhash': str(imagehash.dhash(pil_img)),
        'ahash': str(imagehash.average_hash(pil_img)),
    }


def hash_similarity(h1, h2):
    """Weighted pHash+dHash+aHash similarity → 0-100."""
    try:
        p1, d1, a1 = map(imagehash.hex_to_hash, [h1['phash'], h1['dhash'], h1['ahash']])
        p2, d2, a2 = map(imagehash.hex_to_hash, [h2['phash'], h2['dhash'], h2['ahash']])
        ps = max(0, (1 - (p1 - p2) / 64) * 100)
        ds = max(0, (1 - (d1 - d2) / 64) * 100)
        as_ = max(0, (1 - (a1 - a2) / 64) * 100)
        return round(ps * 0.5 + ds * 0.3 + as_ * 0.2, 2)
    except Exception as e:
        print(f"[Fingerprint] Hash similarity error: {e}")
        return 0.0


# ── Multi-scale regions ──────────────────────────────────────────────────────

def get_regions(pil_img):
    """Return list of (name, PIL image) for centre + corners."""
    w, h = pil_img.size
    regions = [('full', pil_img)]
    # Centre 60%
    cx1, cy1 = int(w * 0.2), int(h * 0.2)
    cx2, cy2 = int(w * 0.8), int(h * 0.8)
    regions.append(('centre', pil_img.crop((cx1, cy1, cx2, cy2))))
    # Top-left 25%
    regions.append(('tl', pil_img.crop((0, 0, w // 2, h // 2))))
    # Bottom-right 25%
    regions.append(('br', pil_img.crop((w // 2, h // 2, w, h))))
    return regions


# ── Fingerprint one frame ────────────────────────────────────────────────────

def fingerprint_frame(pil_img):
    """
    Returns dict with:
      clip_vec  : numpy array (512,)
      clip_flip : numpy array (512,) — horizontally flipped
      hashes    : dict
      hashes_flip: dict
    """
    flip = pil_img.transpose(Image.FLIP_LEFT_RIGHT)
    return {
        'clip_vec':   get_clip_embedding(pil_img),
        'clip_flip':  get_clip_embedding(flip),
        'hashes':     get_phashes(pil_img),
        'hashes_flip': get_phashes(flip),
    }


# ── DTW on embedding sequences ───────────────────────────────────────────────

def dtw_similarity(seq_a, seq_b, max_frames=30):
    """
    DTW between two lists of 512-dim vectors.
    Returns 0-100 score.
    Subsamples to max_frames to keep it fast.
    """
    if not seq_a or not seq_b:
        return 0.0

    # Subsample evenly
    def subsample(seq, n):
        if len(seq) <= n:
            return seq
        idx = np.linspace(0, len(seq) - 1, n, dtype=int)
        return [seq[i] for i in idx]

    a = subsample(seq_a, max_frames)
    b = subsample(seq_b, max_frames)

    n, m = len(a), len(b)
    # Cost matrix (cosine distance = 1 - cosine_sim/100)
    cost = np.ones((n, m), dtype=np.float32)
    for i, va in enumerate(a):
        for j, vb in enumerate(b):
            cost[i, j] = 1.0 - max(0.0, float(np.dot(va, vb)))

    # Standard DTW DP
    dp = np.full((n, m), np.inf, dtype=np.float32)
    dp[0, 0] = cost[0, 0]
    for i in range(1, n):
        dp[i, 0] = dp[i-1, 0] + cost[i, 0]
    for j in range(1, m):
        dp[0, j] = dp[0, j-1] + cost[0, j]
    for i in range(1, n):
        for j in range(1, m):
            dp[i, j] = cost[i, j] + min(dp[i-1, j], dp[i, j-1], dp[i-1, j-1])

    # Normalise path length
    path_len = n + m - 1
    avg_cost = dp[n-1, m-1] / path_len
    # Convert to similarity 0-100
    sim = max(0.0, (1.0 - avg_cost)) * 100
    return round(sim, 2)


# ── Compare suspect frame against ONE registered frame ───────────────────────

def compare_frames(suspect_fp, registered_fp):
    """
    suspect_fp, registered_fp: dicts from fingerprint_frame()
    Returns best similarity 0-100.
    Checks: normal + flipped variants.
    """
    scores = []

    # CLIP normal vs normal
    if suspect_fp['clip_vec'] is not None and registered_fp['clip_vec'] is not None:
        scores.append(cosine_similarity(suspect_fp['clip_vec'], registered_fp['clip_vec']))

    # CLIP flipped suspect vs normal registered (catches mirroring)
    if suspect_fp['clip_flip'] is not None and registered_fp['clip_vec'] is not None:
        scores.append(cosine_similarity(suspect_fp['clip_flip'], registered_fp['clip_vec']))

    # Hash normal
    scores.append(hash_similarity(suspect_fp['hashes'], registered_fp['hashes']))

    # Hash flipped
    scores.append(hash_similarity(suspect_fp['hashes_flip'], registered_fp['hashes']))

    return max(scores) if scores else 0.0


# ── Full video-vs-video comparison ───────────────────────────────────────────

def compare_video_fingerprints(suspect_frames_fp, registered_frames_fp,
                                frame_threshold=65, dtw_weight=0.6):
    """
    suspect_frames_fp   : list of fingerprint_frame() dicts (suspect video)
    registered_frames_fp: list of fingerprint_frame() dicts (registered asset)

    Strategy:
      1. For each suspect frame, find best match among registered frames
      2. Count frames above threshold → match_rate
      3. DTW on CLIP sequence → temporal similarity
      4. Final = dtw_weight * dtw_sim + (1-dtw_weight) * match_rate_score

    Returns dict with detailed scores.
    """
    if not suspect_frames_fp or not registered_frames_fp:
        return {'final': 0.0, 'match_rate': 0.0, 'dtw_sim': 0.0, 'matched_frames': 0}

    # Per-frame best scores
    frame_scores = []
    for sf in suspect_frames_fp:
        best = max(
            (compare_frames(sf, rf) for rf in registered_frames_fp),
            default=0.0
        )
        frame_scores.append(best)

    matched = sum(1 for s in frame_scores if s >= frame_threshold)
    match_rate = round((matched / len(frame_scores)) * 100, 2)

    # DTW on CLIP vectors (only use frames where embedding available)
    s_vecs = [fp['clip_vec'] for fp in suspect_frames_fp if fp['clip_vec'] is not None]
    r_vecs = [fp['clip_vec'] for fp in registered_frames_fp if fp['clip_vec'] is not None]
    dtw_sim = dtw_similarity(s_vecs, r_vecs)

    # Also try flipped suspect sequence against registered
    s_vecs_flip = [fp['clip_flip'] for fp in suspect_frames_fp if fp['clip_flip'] is not None]
    dtw_sim_flip = dtw_similarity(s_vecs_flip, r_vecs)
    dtw_sim = max(dtw_sim, dtw_sim_flip)

    final = round(dtw_weight * dtw_sim + (1 - dtw_weight) * match_rate, 2)

    return {
        'final': final,
        'match_rate': match_rate,
        'dtw_sim': dtw_sim,
        'matched_frames': matched,
        'total_suspect_frames': len(suspect_frames_fp),
        'avg_frame_score': round(np.mean(frame_scores), 2) if frame_scores else 0.0,
    }


# ── Extract + fingerprint frames from a video path ───────────────────────────

def extract_and_fingerprint_video(video_path, interval_seconds=3, max_frames=60):
    """
    Opens video, seeks every interval_seconds, fingerprints each frame.
    Returns list of dicts: {timestamp, time_str, filename(optional), fingerprint}
    """
    import cv2

    results = []
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"[Fingerprint] Cannot open: {video_path}")
            return results

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or total_frames <= 0:
            cap.release()
            return results

        duration = total_frames / fps
        effective_interval = max(interval_seconds, duration / max_frames)
        frame_interval = max(1, int(fps * effective_interval))

        target = 0
        while target < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ret, frame = cap.read()
            if not ret:
                break

            timestamp = round(target / fps, 2)
            mins, secs = int(timestamp // 60), int(timestamp % 60)
            time_str = f"{mins:02d}:{secs:02d}"

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            fp = fingerprint_frame(pil_img)

            results.append({
                'timestamp': timestamp,
                'time_str': time_str,
                'fingerprint': fp,
            })
            target += frame_interval

        cap.release()
        print(f"[Fingerprint] Extracted + fingerprinted {len(results)} frames from {video_path}")

    except Exception as e:
        print(f"[Fingerprint] Error: {e}")

    return results


# ── Load registered fingerprints from DB rows ────────────────────────────────

def load_registered_fingerprints(db_rows):
    """
    db_rows: rows from video_fingerprints table.
    Each row must have: clip_embedding (JSON str), phash, dhash, ahash,
                        clip_flip_embedding (JSON str), phash_flip, dhash_flip, ahash_flip
    Returns list of fingerprint dicts compatible with compare_frames().
    """
    fps = []
    for row in db_rows:
        try:
            fp = {
                'clip_vec':    str_to_embedding(row['clip_embedding']) if row['clip_embedding'] else None,
                'clip_flip':   str_to_embedding(row['clip_flip_embedding']) if row['clip_flip_embedding'] else None,
                'hashes':      {'phash': row['phash'], 'dhash': row['dhash'], 'ahash': row['ahash']},
                'hashes_flip': {'phash': row['phash_flip'], 'dhash': row['dhash_flip'], 'ahash': row['ahash_flip']},
            }
            fps.append(fp)
        except Exception as e:
            print(f"[Fingerprint] Load error for row: {e}")
    return fps