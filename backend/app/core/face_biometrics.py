import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Base directory for models
MODEL_DIR = Path(__file__).resolve().parent.parent / "vision_models"
YUNET_MODEL_PATH = str(MODEL_DIR / "face_detection_yunet.onnx")
SFACE_MODEL_PATH = str(MODEL_DIR / "face_recognition_sface.onnx")


class VisionPipeline:
    _instance: Optional["VisionPipeline"] = None

    def __init__(self):
        if not os.path.exists(YUNET_MODEL_PATH) or not os.path.exists(SFACE_MODEL_PATH):
            raise FileNotFoundError(
                f"Model files missing in {MODEL_DIR}. Expected face_detection_yunet.onnx and face_recognition_sface.onnx"
            )

        # Default size, will be dynamically updated based on input frame size
        self.detector = cv2.FaceDetectorYN.create(
            YUNET_MODEL_PATH,
            "",
            (320, 320),
            score_threshold=0.6,
            nms_threshold=0.3,
            top_k=5000,
        )
        self.recognizer = cv2.FaceRecognizerSF.create(SFACE_MODEL_PATH, "")
        logger.info("VisionPipeline initialized successfully with YuNet and SFace models.")

    @classmethod
    def get_instance(cls) -> "VisionPipeline":
        if cls._instance is None:
            cls._instance = VisionPipeline()
        return cls._instance

    @staticmethod
    def enhance_low_light(img: np.ndarray) -> Tuple[np.ndarray, bool, float]:
        """
        Enhances dark or poorly lit frames using adaptive Gamma Correction and CLAHE
        in LAB color space. Returns (enhanced_image, was_enhanced, original_luminance).
        """
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l_mean = float(np.mean(l))

        # If average luminance is low (< 75 out of 255), apply low-light enhancement
        if l_mean < 75.0:
            # Adaptive gamma: darker images get stronger boost
            gamma = max(0.20, min(0.85, math.log(0.5) / math.log(max(l_mean / 255.0, 0.03))))
            table = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)]).astype("uint8")
            l_gamma = cv2.LUT(l, table)

            # CLAHE for contrast enhancement on the luminance channel
            clahe = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(8, 8))
            l_clahe = clahe.apply(l_gamma)

            enhanced_lab = cv2.merge([l_clahe, a, b])
            enhanced_bgr = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

            # Bilateral filter to smooth sensor noise in shadows while preserving sharp eye boundaries
            denoised = cv2.bilateralFilter(enhanced_bgr, d=5, sigmaColor=40, sigmaSpace=40)
            return denoised, True, l_mean

        return img, False, l_mean

    def detect_faces(self, img: np.ndarray) -> Tuple[Optional[np.ndarray], bool, float]:
        """
        Applies low-light enhancement if needed, sets detector input size,
        and detects faces with 5 key landmarks.
        """
        h, w = img.shape[:2]
        processed_img, low_light_active, l_mean = self.enhance_low_light(img)

        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(processed_img)

        return faces, low_light_active, l_mean

    @staticmethod
    def is_face_masked(aligned_face: np.ndarray) -> bool:
        """
        Determines whether the employee is wearing a cleanroom face mask
        by analyzing color characteristics (Hue, Saturation) and texture variance
        in the mouth/chin zone versus human skin tones.
        """
        h, w = aligned_face.shape[:2]
        lower_face = aligned_face[int(h * 0.60) : int(h * 0.95), int(w * 0.20) : int(w * 0.80)]
        if lower_face.size == 0:
            return False

        hsv_lower = cv2.cvtColor(lower_face, cv2.COLOR_BGR2HSV)
        h_chan, s_chan, v_chan = cv2.split(hsv_lower)
        total_pixels = max(1, lower_face.shape[0] * lower_face.shape[1])

        # 1. Skin tone detection (typical human skin under cleanroom/normal lighting)
        skin_pixels = int(np.sum(
            (((h_chan <= 25) | (h_chan >= 165)) & (s_chan >= 20) & (s_chan <= 210) & (v_chan >= 45))
        ))
        skin_ratio = skin_pixels / total_pixels

        # 2. Color check: Cleanroom surgical masks are usually blue/cyan/teal/green
        # Hue in [75, 140] corresponds to cyan/blue/green medical masks
        mask_color_pixels = int(np.sum((h_chan >= 75) & (h_chan <= 140) & (s_chan > 30)))
        mask_color_ratio = mask_color_pixels / total_pixels

        # 3. White mask check: Low saturation with high brightness
        white_mask_pixels = int(np.sum((s_chan < 25) & (v_chan > 165)))
        white_mask_ratio = white_mask_pixels / total_pixels

        # 4. Dark mask check: Very low brightness
        dark_mask_pixels = int(np.sum(v_chan < 50))
        dark_mask_ratio = dark_mask_pixels / total_pixels

        # If significant bare skin is detected (>40%), it is unmasked
        if skin_ratio > 0.40 and mask_color_ratio < 0.20 and white_mask_ratio < 0.35:
            return False

        # Texture variance check: Smooth surgical material vs textured facial features
        gray_lower = cv2.cvtColor(lower_face, cv2.COLOR_BGR2GRAY)
        variance = float(np.var(gray_lower))

        return (
            (mask_color_ratio > 0.25)
            or (white_mask_ratio > 0.40 and variance < 150.0)
            or (dark_mask_ratio > 0.50)
            or (skin_ratio < 0.20 and variance < 80.0)
        )

    def extract_features(
        self, img: np.ndarray, face: np.ndarray
    ) -> Dict[str, Any]:
        """
        Extracts both full-face and periocular (eye-region) normalized embedding vectors.
        Returns:
            - full_face_vector: 128-d float array (or None if face heavily masked)
            - periocular_vector: 128-d float array (focused strictly on eyes + eyebrows)
            - is_masked: bool
            - landmarks: dict of eye, nose, mouth positions
            - confidence: detection confidence
        """
        # Align crop to standard 112x112
        aligned_face = self.recognizer.alignCrop(img, face)

        # Full face feature
        full_feature = self.recognizer.feature(aligned_face)
        full_vector = full_feature[0].tolist()

        # Check for cleanroom mask
        masked = self.is_face_masked(aligned_face)

        # Periocular region extraction:
        # In a 112x112 aligned face, the eyes are standardized at y ~ 40-45.
        # Eyebrows are at y ~ 20-35. The nose bridge starts at y ~ 50.
        # We crop the eye strip [y: 15 to 65, x: 10 to 102], completely above surgical/cleanroom masks.
        periocular_strip = aligned_face[15:65, 10:102]
        # Resize back to 112x112 for the recognizer feature extractor
        periocular_resized = cv2.resize(periocular_strip, (112, 112), interpolation=cv2.INTER_CUBIC)
        periocular_feature = self.recognizer.feature(periocular_resized)
        periocular_vector = periocular_feature[0].tolist()

        landmarks = {
            "right_eye": (float(face[4]), float(face[5])),
            "left_eye": (float(face[6]), float(face[7])),
            "nose_tip": (float(face[8]), float(face[9])),
            "right_mouth": (float(face[10]), float(face[11])),
            "left_mouth": (float(face[12]), float(face[13])),
        }

        return {
            "bbox": (int(face[0]), int(face[1]), int(face[2]), int(face[3])),
            "confidence": float(face[14]),
            "landmarks": landmarks,
            "is_masked": masked,
            "full_face_vector": full_vector,
            "periocular_vector": periocular_vector,
        }

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """
        Calculates cosine similarity between two feature vectors:
        score = (v1 . v2) / (||v1|| * ||v2||)
        """
        a = np.array(v1, dtype=np.float32)
        b = np.array(v2, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))


def decode_image_base64(b64_str: str) -> np.ndarray:
    """Decodes a base64 encoded image string (with or without data:image/... prefix)."""
    import base64
    if "," in b64_str:
        b64_str = b64_str.split(",", 1)[1]
    img_bytes = base64.b64decode(b64_str)
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("No se pudo decodificar la imagen enviada")
    return img
