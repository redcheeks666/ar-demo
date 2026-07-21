import type { NormalizedPoint } from './types';

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function clamp01(value: number): number {
  return clamp(value, 0, 1);
}

export function lerp(from: number, to: number, alpha: number): number {
  return from + (to - from) * alpha;
}

export function averagePoints(points: readonly NormalizedPoint[]): NormalizedPoint {
  if (points.length === 0) {
    return { x: 0, y: 0, z: 0 };
  }

  const sum = points.reduce(
    (acc, point) => ({
      x: acc.x + point.x,
      y: acc.y + point.y,
      z: acc.z + point.z,
    }),
    { x: 0, y: 0, z: 0 },
  );

  return {
    x: sum.x / points.length,
    y: sum.y / points.length,
    z: sum.z / points.length,
  };
}

export function distance2d(a: NormalizedPoint, b: NormalizedPoint): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

export function distance3d(a: NormalizedPoint, b: NormalizedPoint): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}
