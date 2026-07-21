import type { FaceLandmarkerResult, Matrix } from '@mediapipe/tasks-vision';
import { Matrix4, Quaternion, Vector3 } from 'three';
import {
  mapNormalizedToStagePixel,
  type PreviewLayout,
} from '../interaction/coordinateMapping';

/**
 * Face-pose calibration is centralized here so the renderer stays independent
 * from MediaPipe landmark indices and camera/image coordinate conventions.
 */
const FACE_POSE_CONFIG = {
  /** Highest landmark index used below is 454; shorter results are incomplete. */
  minimumLandmarkCount: 455,
  /** Forehead, chin, left temple, right temple: a stable multi-point center. */
  centerLandmarkIndices: [10, 152, 234, 454] as const,
  leftTempleIndex: 234,
  rightTempleIndex: 454,
  /** Programmatic helmet is 2.35 model units wide at its outer shell. */
  helmetModelWidth: 2.35,
  /** Slight overscan keeps the temporary shell outside the detected face. */
  helmetWidthMultiplier: 1.14,
  /** Hold the last valid pose through a few missed inference frames. */
  missingFaceGraceMs: 220,
  minimumFaceWidthPx: 28,
  maximumScale: 520,
} as const;

export interface FacePose {
  position: { x: number; y: number; z: number };
  rotation: Quaternion;
  /** CSS-pixel scale per model unit for the orthographic Three.js scene. */
  scale: number;
  /** Remains true during the short missing-face grace period. */
  faceVisible: boolean;
}

/**
 * Converts Face Landmarker output into a mirror/contain-aware renderer pose.
 * MediaPipe exposes the 4x4 transform as a flattened column-major array, which
 * matches Three.js Matrix4.fromArray storage. The translation is intentionally
 * ignored here because screen position/scale come from contain-mapped landmarks.
 */
export class FacePoseAdapter {
  private readonly pose: FacePose = {
    position: { x: 0, y: 0, z: 0 },
    rotation: new Quaternion(),
    scale: 1,
    faceVisible: false,
  };

  private readonly sourceMatrix = new Matrix4();
  private readonly rotationMatrix = new Matrix4();
  private readonly mirroredRotationMatrix = new Matrix4();
  private readonly mirrorMatrix = new Matrix4().makeScale(-1, 1, 1);
  private readonly scratchPosition = new Vector3();
  private readonly scratchScale = new Vector3();
  private readonly scratchQuaternion = new Quaternion();
  private lastFaceSeenAt = Number.NEGATIVE_INFINITY;

  update(
    result: FaceLandmarkerResult | null,
    layout: PreviewLayout,
    timestamp: number,
  ): FacePose {
    const landmarks = result?.faceLandmarks[0];

    if (
      landmarks &&
      landmarks.length >= FACE_POSE_CONFIG.minimumLandmarkCount &&
      layout.stageWidth > 0 &&
      layout.stageHeight > 0 &&
      layout.sourceWidth > 0 &&
      layout.sourceHeight > 0
    ) {
      const center = this.computeStableCenter(landmarks, layout);
      const leftTemple = mapNormalizedToStagePixel(
        landmarks[FACE_POSE_CONFIG.leftTempleIndex],
        layout,
      );
      const rightTemple = mapNormalizedToStagePixel(
        landmarks[FACE_POSE_CONFIG.rightTempleIndex],
        layout,
      );
      const faceWidth = Math.abs(rightTemple.x - leftTemple.x);
      const scale =
        (faceWidth * FACE_POSE_CONFIG.helmetWidthMultiplier) /
        FACE_POSE_CONFIG.helmetModelWidth;

      if (
        Number.isFinite(center.x) &&
        Number.isFinite(center.y) &&
        Number.isFinite(scale) &&
        faceWidth >= FACE_POSE_CONFIG.minimumFaceWidthPx &&
        scale <= FACE_POSE_CONFIG.maximumScale
      ) {
        this.pose.position.x = center.x - layout.stageWidth / 2;
        this.pose.position.y = layout.stageHeight / 2 - center.y;
        this.pose.position.z = 0;
        this.pose.scale = scale;
        this.pose.faceVisible = true;
        this.lastFaceSeenAt = timestamp;

        const matrix = result?.facialTransformationMatrixes[0];

        if (matrix) {
          this.tryUpdateRotation(matrix, layout.mirrored);
        }

        return this.pose;
      }
    }

    this.pose.faceVisible =
      timestamp - this.lastFaceSeenAt <= FACE_POSE_CONFIG.missingFaceGraceMs;
    return this.pose;
  }

  reset(): void {
    this.pose.position.x = 0;
    this.pose.position.y = 0;
    this.pose.position.z = 0;
    this.pose.rotation.identity();
    this.pose.scale = 1;
    this.pose.faceVisible = false;
    this.lastFaceSeenAt = Number.NEGATIVE_INFINITY;
  }

  private computeStableCenter(
    landmarks: FaceLandmarkerResult['faceLandmarks'][number],
    layout: PreviewLayout,
  ): { x: number; y: number } {
    let x = 0;
    let y = 0;

    for (const index of FACE_POSE_CONFIG.centerLandmarkIndices) {
      const point = mapNormalizedToStagePixel(landmarks[index], layout);
      x += point.x;
      y += point.y;
    }

    return {
      x: x / FACE_POSE_CONFIG.centerLandmarkIndices.length,
      y: y / FACE_POSE_CONFIG.centerLandmarkIndices.length,
    };
  }

  private tryUpdateRotation(matrix: Matrix, mirrored: boolean): boolean {
    if (
      matrix.rows !== 4 ||
      matrix.columns !== 4 ||
      matrix.data.length !== 16 ||
      matrix.data.some((value) => !Number.isFinite(value))
    ) {
      return false;
    }

    this.sourceMatrix.fromArray(matrix.data);

    // Remove the uniform face-geometry scale before extracting orientation.
    this.sourceMatrix.decompose(
      this.scratchPosition,
      this.scratchQuaternion,
      this.scratchScale,
    );
    this.rotationMatrix.makeRotationFromQuaternion(this.scratchQuaternion);

    if (mirrored) {
      // A mirrored selfie view needs S * R * S (S = reflection across X).
      // Two reflections preserve a proper rotation that a quaternion can hold.
      this.mirroredRotationMatrix
        .copy(this.mirrorMatrix)
        .multiply(this.rotationMatrix)
        .multiply(this.mirrorMatrix);
      this.scratchQuaternion.setFromRotationMatrix(this.mirroredRotationMatrix);
    } else {
      this.scratchQuaternion.setFromRotationMatrix(this.rotationMatrix);
    }

    if (
      !Number.isFinite(this.scratchQuaternion.x) ||
      !Number.isFinite(this.scratchQuaternion.y) ||
      !Number.isFinite(this.scratchQuaternion.z) ||
      !Number.isFinite(this.scratchQuaternion.w)
    ) {
      return false;
    }

    this.pose.rotation.copy(this.scratchQuaternion).normalize();
    return true;
  }
}
