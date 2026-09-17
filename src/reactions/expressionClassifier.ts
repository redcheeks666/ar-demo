import type { Category, FaceLandmarkerResult } from '@mediapipe/tasks-vision';

export type ReactionExpression =
  | 'none'
  | 'smile'
  | 'grin'
  | 'surprise'
  | 'winkRight'
  | 'winkLeft';

export interface ExpressionSnapshot {
  readonly expression: ReactionExpression;
  /** 每次进入新的可见表情都会递增，用于重播贴纸弹出动画。 */
  readonly revision: number;
}

const CONFIG = {
  stableExpressionMs: 500,
  candidateDropoutGraceMs: 120,
  missingFaceGraceMs: 240,
  scoreSmoothingAlpha: 0.42,
} as const;

const THRESHOLDS = {
  winkClosed: 0.42,
  winkOtherEyeMaximum: 0.48,
  winkMinimumDifference: 0.16,
  smileMouthClosedMaximum: 0.2,
  smileSpread: 0.28,
  grinJawOpen: 0.2,
  grinSpread: 0.23,
  surpriseJawOpen: 0.22,
  surpriseUpperFaceTotal: 0.12,
} as const;

/**
 * 把连续 blendshape 分数转换成少量、稳定的表情状态。
 *
 * 所有新状态（包含恢复自然表情）都必须连续稳定 0.5 秒才会切换，
 * 避免临界值抖动造成 Emoji 频繁跳变。
 */
export class ExpressionClassifier {
  private expression: ReactionExpression = 'none';
  private candidate: ReactionExpression = 'none';
  private candidateSince = Number.NEGATIVE_INFINITY;
  private candidateLastSeenAt = Number.NEGATIVE_INFINITY;
  private lastFaceSeenAt = Number.NEGATIVE_INFINITY;
  private readonly smoothedScores = new Map<string, number>();
  private revision = 0;

  update(result: FaceLandmarkerResult | null, timestamp: number): ExpressionSnapshot {
    const hasFace = Boolean(result?.faceLandmarks[0]?.length);
    const categories = result?.faceBlendshapes[0]?.categories;

    if (!hasFace || !categories?.length) {
      return this.handleMissingFace(timestamp);
    }

    this.lastFaceSeenAt = timestamp;
    return this.observe(this.classify(categories), timestamp);
  }

  step(timestamp: number): ExpressionSnapshot {
    if (timestamp - this.lastFaceSeenAt > CONFIG.missingFaceGraceMs) {
      this.candidate = 'none';
      this.setExpression('none');
    }

    return this.getSnapshot();
  }

  reset(): void {
    this.expression = 'none';
    this.candidate = 'none';
    this.candidateSince = Number.NEGATIVE_INFINITY;
    this.candidateLastSeenAt = Number.NEGATIVE_INFINITY;
    this.lastFaceSeenAt = Number.NEGATIVE_INFINITY;
    this.smoothedScores.clear();
  }

  private classify(categories: Category[]): ReactionExpression {
    for (const category of categories) {
      const previous = this.smoothedScores.get(category.categoryName);
      const value = previous === undefined
        ? category.score
        : previous + (category.score - previous) * CONFIG.scoreSmoothingAlpha;
      this.smoothedScores.set(category.categoryName, value);
    }

    const blinkLeft = score(this.smoothedScores, 'eyeBlinkLeft');
    const blinkRight = score(this.smoothedScores, 'eyeBlinkRight');
    const jawOpen = score(this.smoothedScores, 'jawOpen');
    const smile = average(
      score(this.smoothedScores, 'mouthSmileLeft'),
      score(this.smoothedScores, 'mouthSmileRight'),
    );
    const mouthStretch = average(
      score(this.smoothedScores, 'mouthStretchLeft'),
      score(this.smoothedScores, 'mouthStretchRight'),
    );
    const mouthSpread = Math.max(smile, mouthStretch * 0.9);
    const eyeWide = average(
      score(this.smoothedScores, 'eyeWideLeft'),
      score(this.smoothedScores, 'eyeWideRight'),
    );
    const browOuterUp = average(
      score(this.smoothedScores, 'browOuterUpLeft'),
      score(this.smoothedScores, 'browOuterUpRight'),
    );
    const browRaised = Math.max(
      score(this.smoothedScores, 'browInnerUp'),
      browOuterUp,
    );

    // MediaPipe 的 Left / Right 指真人自身的左右侧，不受自拍镜像影响。
    if (
      blinkRight >= THRESHOLDS.winkClosed
      && blinkLeft <= THRESHOLDS.winkOtherEyeMaximum
      && blinkRight - blinkLeft >= THRESHOLDS.winkMinimumDifference
    ) {
      return 'winkRight';
    }

    if (
      blinkLeft >= THRESHOLDS.winkClosed
      && blinkRight <= THRESHOLDS.winkOtherEyeMaximum
      && blinkLeft - blinkRight >= THRESHOLDS.winkMinimumDifference
    ) {
      return 'winkLeft';
    }

    if (
      jawOpen >= THRESHOLDS.surpriseJawOpen
      && eyeWide + browRaised >= THRESHOLDS.surpriseUpperFaceTotal
    ) {
      return 'surprise';
    }

    // Face Landmarker 不检测牙齿；“露齿笑”由张嘴 + 嘴角咧开共同推断。
    if (jawOpen >= THRESHOLDS.grinJawOpen && mouthSpread >= THRESHOLDS.grinSpread) {
      return 'grin';
    }

    if (
      jawOpen <= THRESHOLDS.smileMouthClosedMaximum
      && mouthSpread >= THRESHOLDS.smileSpread
    ) {
      return 'smile';
    }

    return 'none';
  }

  private handleMissingFace(timestamp: number): ExpressionSnapshot {
    if (timestamp - this.lastFaceSeenAt > CONFIG.missingFaceGraceMs) {
      this.candidate = 'none';
      this.setExpression('none');
    }

    return this.getSnapshot();
  }

  private observe(expression: ReactionExpression, timestamp: number): ExpressionSnapshot {
    if (expression === this.expression) {
      this.candidate = expression;
      this.candidateSince = timestamp;
      this.candidateLastSeenAt = timestamp;
      return this.getSnapshot();
    }

    if (
      expression === 'none'
      && this.candidate !== 'none'
      && timestamp - this.candidateLastSeenAt <= CONFIG.candidateDropoutGraceMs
    ) {
      return this.getSnapshot();
    }

    if (expression !== this.candidate) {
      this.candidate = expression;
      this.candidateSince = timestamp;
      this.candidateLastSeenAt = timestamp;
      return this.getSnapshot();
    }

    this.candidateLastSeenAt = timestamp;

    if (timestamp - this.candidateSince >= CONFIG.stableExpressionMs) {
      this.setExpression(expression);
      this.candidateSince = timestamp;
    }

    return this.getSnapshot();
  }

  private setExpression(expression: ReactionExpression): void {
    if (expression === this.expression) {
      return;
    }

    this.expression = expression;

    if (expression !== 'none') {
      this.revision += 1;
    }
  }

  private getSnapshot(): ExpressionSnapshot {
    return { expression: this.expression, revision: this.revision };
  }
}

function score(scores: ReadonlyMap<string, number>, name: string): number {
  return scores.get(name) ?? 0;
}

function average(a: number, b: number): number {
  return (a + b) * 0.5;
}
