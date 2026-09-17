// Syncs MediaPipe runtime assets into public/ so the demo never depends on
// external CDNs at runtime (jsdelivr / storage.googleapis.com are unreliable
// or blocked in some demo environments).
//
// - wasm: copied from the installed @mediapipe/tasks-vision package, so the
//   runtime always matches the npm version — no pinned-URL drift.
// - *.task / *.tflite models: downloaded once from Google's model storage and
//   kept locally afterwards.
//
// Runs automatically via the `predev` / `prebuild` npm hooks.
// (Adapted from ar-solar-system-demo/scripts/sync-mediapipe-assets.mjs.)
import {
  cpSync,
  existsSync,
  mkdirSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const wasmSourceDir = join(
  projectRoot,
  'node_modules/@mediapipe/tasks-vision/wasm',
);
const wasmTargetDir = join(projectRoot, 'public/mediapipe/wasm');
const modelsTargetDir = join(projectRoot, 'public/mediapipe/models');

const MODELS = [
  {
    file: 'blaze_face_short_range.tflite',
    url: 'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite',
    minBytes: 100_000,
  },
  {
    file: 'face_landmarker.task',
    url: 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
    minBytes: 1_000_000,
  },
  {
    file: 'gesture_recognizer.task',
    url: 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task',
    minBytes: 1_000_000,
  },
  {
    file: 'hand_landmarker.task',
    url: 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
    minBytes: 1_000_000,
  },
];

if (!existsSync(wasmSourceDir)) {
  console.error(
    '[mediapipe-sync] 找不到 node_modules/@mediapipe/tasks-vision/wasm，请先 npm install。',
  );
  process.exit(1);
}

mkdirSync(wasmTargetDir, { recursive: true });
cpSync(wasmSourceDir, wasmTargetDir, { recursive: true });
console.log('[mediapipe-sync] wasm 已同步。');

mkdirSync(modelsTargetDir, { recursive: true });

let failed = 0;

for (const model of MODELS) {
  const targetPath = join(modelsTargetDir, model.file);
  const hasValidModel =
    existsSync(targetPath) && statSync(targetPath).size >= model.minBytes;

  if (hasValidModel) {
    continue;
  }

  try {
    const response = await fetch(model.url);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    writeFileSync(targetPath, Buffer.from(await response.arrayBuffer()));
    console.log(`[mediapipe-sync] ${model.file} 下载完成。`);
  } catch (error) {
    failed += 1;
    console.error(
      `[mediapipe-sync] ${model.file} 下载失败（${error}）。请在有网络时重跑，或手动下载\n  ${model.url}\n到 ${targetPath}`,
    );
  }
}

if (failed > 0) {
  console.error(
    `[mediapipe-sync] ${failed} 个模型缺失，对应演示将无法运行；其余功能不受影响。`,
  );
}
