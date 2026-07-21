import { lerp } from '../utils/math';
import type { NormalizedPoint } from '../utils/types';

const DEFAULT_ALPHA = 0.35;

export class LowPassPointFilter {
  private current: NormalizedPoint | null = null;

  constructor(private readonly alpha = DEFAULT_ALPHA) {}

  smooth(next: NormalizedPoint): NormalizedPoint {
    if (!this.current) {
      this.current = { ...next };
      return { ...this.current };
    }

    this.current = {
      x: lerp(this.current.x, next.x, this.alpha),
      y: lerp(this.current.y, next.y, this.alpha),
      z: lerp(this.current.z, next.z, this.alpha),
      visibility:
        next.visibility === undefined
          ? this.current.visibility
          : lerp(this.current.visibility ?? next.visibility, next.visibility, this.alpha),
    };

    return { ...this.current };
  }

  reset(): void {
    this.current = null;
  }
}

export class LandmarkSmoother {
  private readonly filters = new Map<number, LowPassPointFilter>();

  constructor(private readonly alpha = DEFAULT_ALPHA) {}

  smooth(landmarks: readonly NormalizedPoint[]): NormalizedPoint[] {
    return landmarks.map((landmark, index) => {
      let filter = this.filters.get(index);

      if (!filter) {
        filter = new LowPassPointFilter(this.alpha);
        this.filters.set(index, filter);
      }

      return filter.smooth(landmark);
    });
  }

  reset(): void {
    this.filters.clear();
  }
}
