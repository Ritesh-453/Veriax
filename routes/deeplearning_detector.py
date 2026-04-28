import torch
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
import numpy as np
import os

# ── Lazy model loading ───────────────────────────────────────────────────────
# DO NOT load the model at import time — it consumes ~400MB RAM and crashes
# free-tier servers (Render 512MB, Cloud Run 256MB default) before any request
# is served. The model is loaded once on first use instead.

_model = None


def get_model():
    """Load MobileNet model on first call only (lazy init)."""
    global _model
    if _model is None:
        print("Loading MobileNet model (lazy)...")
        _model = models.mobilenet_v3_small(
            weights=models.MobileNet_V3_Small_Weights.DEFAULT
        )
        _model.eval()
        print("MobileNet model loaded successfully!")
    return _model


# Image preprocessing pipeline
transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225]
    )
])


def get_embedding(image_path):
    """
    Convert image to deep learning embedding vector.
    This vector represents the IMAGE CONTENT not just pixels.
    """
    try:
        model = get_model()
        img = Image.open(image_path).convert('RGB')
        tensor = transform(img).unsqueeze(0)

        with torch.no_grad():
            # Remove final classification layer to get embeddings
            features = torch.nn.Sequential(
                *list(model.children())[:-1]
            )(tensor)
            embedding = features.squeeze().numpy()

        return embedding
    except Exception as e:
        print(f"Embedding error: {e}")
        return None


def cosine_similarity(vec1, vec2):
    """
    Compare two embedding vectors using cosine similarity.
    Returns 0-100 score.
    """
    try:
        dot = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0

        similarity = dot / (norm1 * norm2)
        # Convert from -1,1 range to 0-100
        score = ((similarity + 1) / 2) * 100
        return round(float(score), 2)
    except Exception as e:
        print(f"Cosine similarity error: {e}")
        return 0


def mobilenet_similarity(img1_path, img2_path):
    """
    Compare two images using MobileNet deep learning embeddings.
    Returns similarity score 0-100.
    """
    try:
        emb1 = get_embedding(img1_path)
        emb2 = get_embedding(img2_path)

        if emb1 is None or emb2 is None:
            return 0

        score = cosine_similarity(emb1, emb2)
        return score

    except Exception as e:
        print(f"MobileNet error: {e}")
        return 0


def save_embedding(image_path, save_dir='database/embeddings'):
    """
    Pre-compute and save embedding for a registered asset.
    Makes future comparisons faster.
    """
    try:
        os.makedirs(save_dir, exist_ok=True)
        filename = os.path.basename(image_path)
        save_path = os.path.join(save_dir, filename + '.npy')

        embedding = get_embedding(image_path)
        if embedding is not None:
            np.save(save_path, embedding)
            return save_path
        return None
    except Exception as e:
        print(f"Save embedding error: {e}")
        return None


def load_embedding(image_path, save_dir='database/embeddings'):
    """Load pre-computed embedding if available."""
    try:
        filename = os.path.basename(image_path)
        save_path = os.path.join(save_dir, filename + '.npy')

        if os.path.exists(save_path):
            return np.load(save_path)
        return None
    except:
        return None


def fast_mobilenet_similarity(img1_path, img2_path):
    """
    Fast version — uses cached embeddings when available.
    """
    try:
        # Try to load cached embedding for asset
        emb1 = load_embedding(img1_path)
        if emb1 is None:
            emb1 = get_embedding(img1_path)
            if emb1 is not None:
                save_embedding(img1_path)

        emb2 = get_embedding(img2_path)

        if emb1 is None or emb2 is None:
            return 0

        return cosine_similarity(emb1, emb2)

    except Exception as e:
        print(f"Fast MobileNet error: {e}")
        return 0