"""
train/augment_and_finetune.py

Augmentation pipeline for training data + fine-tuning guidance.
Run this script to augment your enrolled student photos before
re-training/fine-tuning your face recognition embeddings for better
robustness in classroom conditions (variable lighting, angles, occlusion).

Usage:
    python -m train.augment_and_finetune --photos_dir data/student_photos --output_dir data/augmented
"""

import cv2
import numpy as np
import os
import argparse
import logging
from pathlib import Path
import random
import json

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


# ── Augmentation transforms ────────────────────────────────────────

def random_brightness(img: np.ndarray, factor_range=(0.4, 1.6)) -> np.ndarray:
    """Simulate different classroom lighting conditions."""
    factor = random.uniform(*factor_range)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * factor, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def random_rotation(img: np.ndarray, angle_range=(-30, 30)) -> np.ndarray:
    """Simulate head tilt / camera angle variation."""
    angle = random.uniform(*angle_range)
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)


def random_flip(img: np.ndarray) -> np.ndarray:
    """Horizontal flip."""
    return cv2.flip(img, 1)


def random_blur(img: np.ndarray, max_ksize=3) -> np.ndarray:
    """Simulate motion blur or low-res cameras."""
    k = random.choice([1, 3, max_ksize])
    if k <= 1:
        return img
    return cv2.GaussianBlur(img, (k, k), 0)


def random_noise(img: np.ndarray, sigma_range=(5, 20)) -> np.ndarray:
    """Add Gaussian noise to simulate poor lighting sensors."""
    sigma = random.uniform(*sigma_range)
    noise = np.random.normal(0, sigma, img.shape).astype(np.float32)
    noisy = np.clip(img.astype(np.float32) + noise, 0, 255)
    return noisy.astype(np.uint8)


def random_occlusion(img: np.ndarray, max_ratio=0.25) -> np.ndarray:
    """
    Randomly occlude part of the face (simulate masks, glasses, hair).
    """
    h, w = img.shape[:2]
    occ_h = int(h * random.uniform(0.05, max_ratio))
    occ_w = int(w * random.uniform(0.1,  0.4))
    y = random.randint(0, h - occ_h)
    x = random.randint(0, w - occ_w)
    result = img.copy()
    result[y:y+occ_h, x:x+occ_w] = random.randint(0, 255)  # random color block
    return result


def random_contrast(img: np.ndarray, alpha_range=(0.7, 1.4)) -> np.ndarray:
    """Simulate backlit / underlit conditions."""
    alpha = random.uniform(*alpha_range)
    beta  = random.uniform(-20, 20)
    return np.clip(alpha * img.astype(np.float32) + beta, 0, 255).astype(np.uint8)


def color_jitter(img: np.ndarray) -> np.ndarray:
    """Slightly shift hue/saturation for color robustness."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 0] = (hsv[:, :, 0] + random.uniform(-10, 10)) % 180
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * random.uniform(0.8, 1.2), 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


AUGMENTATIONS = [
    ("brightness",  random_brightness),
    ("rotation",    random_rotation),
    ("flip",        random_flip),
    ("blur",        random_blur),
    ("noise",       random_noise),
    ("occlusion",   random_occlusion),
    ("contrast",    random_contrast),
    ("color_jitter",color_jitter),
]


def augment_image(img: np.ndarray, n_transforms: int = 3) -> np.ndarray:
    """Apply n_transforms random augmentations to an image."""
    chosen = random.sample(AUGMENTATIONS, min(n_transforms, len(AUGMENTATIONS)))
    result = img.copy()
    for name, fn in chosen:
        try:
            result = fn(result)
        except Exception as e:
            logger.warning(f"Augmentation '{name}' failed: {e}")
    return result


# ── Main augmentation runner ───────────────────────────────────────

def augment_student_photos(
    photos_dir: str,
    output_dir: str,
    augmentations_per_photo: int = 8,
):
    """
    For each student folder in photos_dir, generate N augmented variants
    of each photo and save them to output_dir/{roll_no}/.

    Args:
        photos_dir:             Root dir with one subfolder per student (roll_no)
        output_dir:             Where to save augmented photos
        augmentations_per_photo: How many augmented versions per source photo
    """
    photos_dir = Path(photos_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {}
    student_dirs = [d for d in photos_dir.iterdir() if d.is_dir()]

    logger.info(f"Found {len(student_dirs)} students in {photos_dir}")

    for student_dir in student_dirs:
        roll_no = student_dir.name
        out_student = output_dir / roll_no
        out_student.mkdir(exist_ok=True)

        image_files = [
            f for f in student_dir.iterdir()
            if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]

        count = 0
        for img_path in image_files:
            src = cv2.imread(str(img_path))
            if src is None:
                continue

            # Copy original
            cv2.imwrite(str(out_student / f"orig_{img_path.name}"), src)
            count += 1

            # Generate augmented variants
            for i in range(augmentations_per_photo):
                aug = augment_image(src, n_transforms=random.randint(2, 4))
                aug_name = f"aug_{i:02d}_{img_path.stem}.jpg"
                cv2.imwrite(str(out_student / aug_name), aug)
                count += 1

        stats[roll_no] = count
        logger.info(f"  {roll_no}: {count} total images (originals + augmented)")

    logger.info(f"\nDone. Total images generated: {sum(stats.values())}")
    with open(output_dir / "augmentation_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    return stats


# ── Re-generate embeddings from augmented photos ───────────────────

def regenerate_embeddings_from_augmented(augmented_dir: str, embeddings_dir: str):
    """
    After augmentation, re-enroll all students using the augmented photo sets.
    This gives much more robust average embeddings.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from backend.services.face_detector import FaceDetector
    from backend.services.recognizer import FaceRecognizer

    detector   = FaceDetector()
    recognizer = FaceRecognizer(embeddings_dir=embeddings_dir)

    aug_dir = Path(augmented_dir)
    student_dirs = [d for d in aug_dir.iterdir() if d.is_dir()]
    logger.info(f"Regenerating embeddings for {len(student_dirs)} students…")

    for student_dir in student_dirs:
        roll_no = student_dir.name
        image_files = [
            f for f in student_dir.iterdir()
            if f.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ]

        embeddings = []
        for img_path in image_files:
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            
            # Detect faces on the original BGR image (without CLAHE)
            faces = detector.detect(img, is_group=False)
            if faces:
                best = max(faces, key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]))
                emb = best.get("embedding")
                if emb is None:
                    emb = recognizer.get_embedding(best["face_crop"])
                if emb is not None:
                    embeddings.append(emb)

        if embeddings:
            processed = recognizer.enroll_from_embeddings(roll_no, embeddings)
            logger.info(f"  {roll_no}: {processed} augmented embeddings saved.")
        else:
            logger.warning(f"  {roll_no}: No faces detected — skipping.")

    logger.info("Embedding regeneration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Augment student photos and regenerate embeddings.")
    parser.add_argument("--photos_dir",   default="data/student_photos")
    parser.add_argument("--output_dir",   default="data/augmented")
    parser.add_argument("--embeddings_dir", default="data/embeddings")
    parser.add_argument("--aug_per_photo", type=int, default=8)
    parser.add_argument("--regen_embeddings", action="store_true",
                        help="After augmenting, regenerate all embeddings")
    args = parser.parse_args()

    augment_student_photos(
        photos_dir=args.photos_dir,
        output_dir=args.output_dir,
        augmentations_per_photo=args.aug_per_photo,
    )

    if args.regen_embeddings:
        regenerate_embeddings_from_augmented(args.output_dir, args.embeddings_dir)