"""
CLIP-based verification gate for candidate detections.

After the heuristic detector proposes clips, this module extracts the peak
frame from each candidate and runs it through CLIP zero-shot classification
to confirm it's actually a volleyball action (vs. dead time / idle poses).

Requires: pip install transformers torch pillow
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Action prompts scored positively — higher = more likely a real action
ACTION_PROMPTS = [
    "a volleyball player spiking the ball over the net",
    "a volleyball player serving the ball",
    "a volleyball player digging a ball low to the ground",
    "a volleyball player setting the ball with both hands above their head",
    "a volleyball player blocking at the net with arms raised",
    "a volleyball player jumping to hit the ball",
    "a volleyball player diving for the ball",
]

# Negative prompts — high score on these means discard
IDLE_PROMPTS = [
    "people standing around on a volleyball court",
    "players walking between plays",
    "spectators watching from the stands",
    "a person standing still",
    "players waiting for the serve",
    "an empty volleyball court",
]

# Minimum action score to keep a detection (0–1 range)
CLIP_ACTION_THRESHOLD = 0.55


def _load_clip_model():
    """Lazy-load CLIP model and processor. Cached after first call."""
    from transformers import CLIPModel, CLIPProcessor

    model_name = "openai/clip-vit-base-patch32"
    logger.info("Loading CLIP model: %s", model_name)
    processor = CLIPProcessor.from_pretrained(model_name)
    model = CLIPModel.from_pretrained(model_name)
    model.eval()
    return model, processor


# Module-level cache so we only load once per worker process
_clip_model = None
_clip_processor = None


def _get_clip():
    global _clip_model, _clip_processor
    if _clip_model is None:
        _clip_model, _clip_processor = _load_clip_model()
    return _clip_model, _clip_processor


def _extract_frame(video_path: str, timestamp: float) -> np.ndarray | None:
    """Extract a single frame from a video at the given timestamp."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(timestamp * fps))
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    # Convert BGR → RGB for CLIP
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def score_frame(frame_rgb: np.ndarray) -> tuple[float, float]:
    """
    Score a single frame against action and idle prompts.

    Returns (action_score, idle_score) where each is 0–1
    and they sum to 1.
    """
    import torch
    from PIL import Image

    model, processor = _get_clip()
    image = Image.fromarray(frame_rgb)

    all_prompts = ACTION_PROMPTS + IDLE_PROMPTS
    inputs = processor(text=all_prompts, images=image, return_tensors="pt", padding=True)

    with torch.no_grad():
        outputs = model(**inputs)
        logits = outputs.logits_per_image[0]  # (num_prompts,)
        probs = logits.softmax(dim=0).cpu().numpy()

    action_score = float(probs[:len(ACTION_PROMPTS)].sum())
    idle_score = float(probs[len(ACTION_PROMPTS):].sum())
    return action_score, idle_score


def verify_detections(
    video_path: str,
    detections: list[dict],
    threshold: float = CLIP_ACTION_THRESHOLD,
) -> list[dict]:
    """
    Filter candidate detections using CLIP zero-shot classification.

    For each detection, extracts the peak frame (midpoint of clip),
    scores it against action vs. idle prompts, and keeps only those
    above the threshold.

    Returns the filtered list of detections (same format as input).
    """
    if not detections:
        return []

    try:
        _get_clip()
    except Exception:
        logger.warning("CLIP model not available — skipping verification, keeping all detections")
        return detections

    kept = []
    discarded = 0

    for det in detections:
        peak_time = (det["start"] + det["end"]) / 2
        frame = _extract_frame(video_path, peak_time)
        if frame is None:
            logger.warning("Could not extract frame at %.1fs — keeping detection", peak_time)
            kept.append(det)
            continue

        action_score, idle_score = score_frame(frame)
        logger.debug(
            "CLIP verify t=%.1fs action=%.3f idle=%.3f (%s)",
            peak_time, action_score, idle_score, det["action"],
        )

        if action_score >= threshold:
            # Boost confidence slightly when CLIP confirms
            det = {**det, "confidence": min(det["confidence"] * 1.1, 0.95)}
            kept.append(det)
        else:
            discarded += 1
            logger.info(
                "CLIP rejected detection at %.1fs (%s, score=%.3f < %.3f)",
                peak_time, det["action"], action_score, threshold,
            )

    logger.info(
        "CLIP verification: %d kept, %d discarded (threshold=%.2f)",
        len(kept), discarded, threshold,
    )
    return kept
