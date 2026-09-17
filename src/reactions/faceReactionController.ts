import type { FaceLandmarkerResult } from '@mediapipe/tasks-vision';
import {
  computeContainRect,
  mapNormalizedToStagePixel,
  type PreviewLayout,
} from '../interaction/coordinateMapping';
import {
  ExpressionClassifier,
  type ExpressionSnapshot,
  type ReactionExpression,
} from './expressionClassifier';

type VisibleExpression = Exclude<ReactionExpression, 'none'>;
type PlacementSide = 'left' | 'right';

interface EmojiDefinition {
  readonly value: string;
  readonly label: string;
}

const EMOJI_CATALOG: Record<VisibleExpression, EmojiDefinition> = {
  smile: { value: '😊', label: '微笑' },
  grin: { value: '😁', label: '露齿笑' },
  surprise: { value: '😲', label: '惊讶' },
  winkRight: { value: '😉', label: '右眼眨眼' },
  winkLeft: { value: '😜', label: '左眼眨眼' },
};

const POSITION_CONFIG = {
  topLandmark: 10,
  chinLandmark: 152,
  leftTempleLandmark: 234,
  rightTempleLandmark: 454,
  minimumLandmarkCount: 455,
  minimumFaceSizePx: 34,
  stickerFaceRatio: 0.62,
  minimumStickerSizePx: 92,
  maximumStickerSizePx: 210,
  stageSizeRatio: 0.27,
  faceGapPx: 10,
  edgePaddingPx: 14,
  missingFaceGraceMs: 240,
  positionTimeConstantMs: 82,
  sizeTimeConstantMs: 110,
} as const;

/**
 * Face Landmarker 驱动的脸旁反应贴纸。
 *
 * 这里只用 landmarks 计算脸部边界和摆放位置；贴纸保持刚性，不覆盖、旋转或变形真人脸。
 */
export class FaceReactionController {
  private readonly classifier = new ExpressionClassifier();
  private readonly emojis: [HTMLSpanElement, HTMLSpanElement];
  private readonly caption: HTMLSpanElement;
  private readonly card: HTMLDivElement;

  private active = false;
  private disposed = false;
  private lastDetectionSeq = -1;
  private lastTimestamp = Number.NaN;
  private lastFaceSeenAt = Number.NEGATIVE_INFINITY;
  private latestSnapshot: ExpressionSnapshot = { expression: 'none', revision: 0 };
  private displayedRevision = -1;
  private visibleEmojiIndex = 0;
  private placementSide: PlacementSide = 'right';
  private hasPosition = false;
  private targetX = 0;
  private targetY = 0;
  private targetSize: number = POSITION_CONFIG.minimumStickerSizePx;
  private displayX = 0;
  private displayY = 0;
  private displaySize: number = POSITION_CONFIG.minimumStickerSizePx;

  constructor(private readonly root: HTMLDivElement) {
    this.emojis = [
      requireDescendant<HTMLSpanElement>(root, '#face-reaction-emoji-a'),
      requireDescendant<HTMLSpanElement>(root, '#face-reaction-emoji-b'),
    ];
    this.caption = requireDescendant<HTMLSpanElement>(root, '#face-reaction-caption');
    this.card = requireDescendant<HTMLDivElement>(root, '.face-reaction-card');
  }

  setActive(active: boolean): void {
    if (active === this.active) {
      return;
    }

    this.active = active;

    if (!active) {
      this.reset();
    }
  }

  update(
    result: FaceLandmarkerResult | null,
    layout: PreviewLayout,
    timestamp: number,
    detectionSeq: number,
  ): void {
    if (!this.active || this.disposed) {
      return;
    }

    if (detectionSeq !== this.lastDetectionSeq) {
      this.lastDetectionSeq = detectionSeq;
      this.latestSnapshot = this.classifier.update(result, timestamp);
      this.updateTarget(result, layout, timestamp);
    } else {
      this.latestSnapshot = this.classifier.step(timestamp);
    }

    this.updateSmoothedPosition(timestamp);

    const faceIsFresh =
      this.hasPosition
      && timestamp - this.lastFaceSeenAt <= POSITION_CONFIG.missingFaceGraceMs;
    const shouldShow =
      faceIsFresh
      && this.latestSnapshot.expression !== 'none';

    if (
      shouldShow
      && this.latestSnapshot.revision !== this.displayedRevision
    ) {
      this.showSticker(
        this.latestSnapshot.expression as VisibleExpression,
        this.latestSnapshot.revision,
      );
    }

    this.root.classList.toggle('is-visible', shouldShow);
    this.root.setAttribute('aria-hidden', shouldShow ? 'false' : 'true');
  }

  reset(): void {
    this.classifier.reset();
    this.latestSnapshot = { expression: 'none', revision: 0 };
    this.lastDetectionSeq = -1;
    this.lastTimestamp = Number.NaN;
    this.lastFaceSeenAt = Number.NEGATIVE_INFINITY;
    this.displayedRevision = -1;
    this.hasPosition = false;
    this.root.classList.remove('is-visible', 'is-popping', 'is-left', 'is-right');
    this.root.setAttribute('aria-hidden', 'true');
  }

  dispose(): void {
    this.disposed = true;
    this.reset();
  }

  private updateTarget(
    result: FaceLandmarkerResult | null,
    layout: PreviewLayout,
    timestamp: number,
  ): void {
    const landmarks = result?.faceLandmarks[0];

    if (
      !landmarks
      || landmarks.length < POSITION_CONFIG.minimumLandmarkCount
      || layout.stageWidth <= 0
      || layout.stageHeight <= 0
    ) {
      return;
    }

    const top = mapNormalizedToStagePixel(
      landmarks[POSITION_CONFIG.topLandmark],
      layout,
    );
    const chin = mapNormalizedToStagePixel(
      landmarks[POSITION_CONFIG.chinLandmark],
      layout,
    );
    const templeA = mapNormalizedToStagePixel(
      landmarks[POSITION_CONFIG.leftTempleLandmark],
      layout,
    );
    const templeB = mapNormalizedToStagePixel(
      landmarks[POSITION_CONFIG.rightTempleLandmark],
      layout,
    );
    const faceLeft = Math.min(templeA.x, templeB.x);
    const faceRight = Math.max(templeA.x, templeB.x);
    const faceTop = Math.min(top.y, chin.y);
    const faceBottom = Math.max(top.y, chin.y);
    const faceWidth = faceRight - faceLeft;
    const faceHeight = faceBottom - faceTop;

    if (
      !Number.isFinite(faceWidth)
      || !Number.isFinite(faceHeight)
      || Math.min(faceWidth, faceHeight) < POSITION_CONFIG.minimumFaceSizePx
    ) {
      return;
    }

    const containRect = computeContainRect(layout);
    const maximumSize = Math.min(
      POSITION_CONFIG.maximumStickerSizePx,
      Math.min(layout.stageWidth, layout.stageHeight) * POSITION_CONFIG.stageSizeRatio,
    );
    const size = clamp(
      Math.max(faceWidth, faceHeight) * POSITION_CONFIG.stickerFaceRatio,
      POSITION_CONFIG.minimumStickerSizePx,
      maximumSize,
    );
    const gap = POSITION_CONFIG.faceGapPx;
    const rightX = faceRight + gap + size * 0.5;
    const leftX = faceLeft - gap - size * 0.5;
    const minimumX = containRect.x + POSITION_CONFIG.edgePaddingPx + size * 0.5;
    const maximumX =
      containRect.x
      + containRect.width
      - POSITION_CONFIG.edgePaddingPx
      - size * 0.5;
    const rightFits = rightX <= maximumX;
    const leftFits = leftX >= minimumX;

    if (this.placementSide === 'right' && !rightFits && leftFits) {
      this.placementSide = 'left';
    } else if (this.placementSide === 'left' && !leftFits && rightFits) {
      this.placementSide = 'right';
    } else if (!rightFits && !leftFits) {
      const rightSpace = maximumX - faceRight;
      const leftSpace = faceLeft - minimumX;
      this.placementSide = rightSpace >= leftSpace ? 'right' : 'left';
    }

    const desiredX = this.placementSide === 'right' ? rightX : leftX;
    const desiredY = faceTop + faceHeight * 0.3;
    const minimumY = containRect.y + POSITION_CONFIG.edgePaddingPx + size * 0.5;
    const maximumY =
      containRect.y
      + containRect.height
      - POSITION_CONFIG.edgePaddingPx
      - size * 0.5;

    this.targetX = clamp(desiredX, minimumX, maximumX);
    this.targetY = clamp(desiredY, minimumY, maximumY);
    this.targetSize = size;
    this.lastFaceSeenAt = timestamp;

    if (!this.hasPosition) {
      this.displayX = this.targetX;
      this.displayY = this.targetY;
      this.displaySize = this.targetSize;
      this.hasPosition = true;
    }

    this.root.classList.toggle('is-left', this.placementSide === 'left');
    this.root.classList.toggle('is-right', this.placementSide === 'right');
  }

  private updateSmoothedPosition(timestamp: number): void {
    if (!this.hasPosition) {
      return;
    }

    const deltaMs = Number.isFinite(this.lastTimestamp)
      ? clamp(timestamp - this.lastTimestamp, 0, 50)
      : 16.7;
    this.lastTimestamp = timestamp;
    const positionAlpha = smoothingAlpha(
      deltaMs,
      POSITION_CONFIG.positionTimeConstantMs,
    );
    const sizeAlpha = smoothingAlpha(deltaMs, POSITION_CONFIG.sizeTimeConstantMs);

    this.displayX += (this.targetX - this.displayX) * positionAlpha;
    this.displayY += (this.targetY - this.displayY) * positionAlpha;
    this.displaySize += (this.targetSize - this.displaySize) * sizeAlpha;
    this.root.style.setProperty('--reaction-x', `${this.displayX.toFixed(2)}px`);
    this.root.style.setProperty('--reaction-y', `${this.displayY.toFixed(2)}px`);
    this.root.style.setProperty('--reaction-size', `${this.displaySize.toFixed(2)}px`);
  }

  private showSticker(expression: VisibleExpression, revision: number): void {
    const emoji = EMOJI_CATALOG[expression];

    const nextEmojiIndex = this.visibleEmojiIndex === 0 ? 1 : 0;
    const nextEmoji = this.emojis[nextEmojiIndex];
    const previousEmoji = this.emojis[this.visibleEmojiIndex];
    nextEmoji.textContent = emoji.value;
    nextEmoji.classList.add('is-active');
    previousEmoji.classList.remove('is-active');
    this.visibleEmojiIndex = nextEmojiIndex;
    this.caption.textContent = emoji.label;
    this.root.dataset.expression = expression;
    this.displayedRevision = revision;

    this.root.classList.remove('is-popping');
    void this.card.offsetWidth;
    this.root.classList.add('is-popping');
  }
}

function requireDescendant<T extends Element>(root: Element, selector: string): T {
  const element = root.querySelector(selector);

  if (!element) {
    throw new Error(`Face reaction element ${selector} is missing.`);
  }

  return element as T;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

function smoothingAlpha(deltaMs: number, timeConstantMs: number): number {
  return 1 - Math.exp(-deltaMs / timeConstantMs);
}
