import { withPublicBasePath } from '../utils/publicAssetPath';

/**
 * UI 卡蓄力三段音效：蓄力循环 / 触发成功 / 取消。
 * 音效文件复制自 ar-solar-system-demo（assets/sfx/）。
 * 加载失败时静默降级——音效是增强项，不能阻塞演示。
 */
const HOLD_LOOP_URL = withPublicBasePath('/assets/sfx/ui-hold-loop.wav');
const HOLD_SUCCESS_URL = withPublicBasePath('/assets/sfx/ui-hold-success.mp3');
const HOLD_CANCEL_URL = withPublicBasePath('/assets/sfx/ui-hold-cancel.mp3');

const LOOP_GAIN = 0.32;
const ONE_SHOT_GAIN = 0.5;

export class UiHoldSfx {
  private context: AudioContext | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private loopSource: AudioBufferSourceNode | null = null;
  private loopGain: GainNode | null = null;

  /** 必须在用户手势（点击“开启摄像头”）之后调用，否则浏览器会拒绝 AudioContext。 */
  unlock(): void {
    if (this.context) {
      void this.context.resume();
      return;
    }

    try {
      this.context = new AudioContext();
    } catch {
      return;
    }

    void this.loadAll();
  }

  startHoldLoop(): void {
    const context = this.context;
    const buffer = this.buffers.get(HOLD_LOOP_URL);

    if (!context || !buffer || this.loopSource) {
      return;
    }

    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer;
    source.loop = true;
    gain.gain.setValueAtTime(0, context.currentTime);
    gain.gain.linearRampToValueAtTime(LOOP_GAIN, context.currentTime + 0.08);
    source.connect(gain);
    gain.connect(context.destination);
    source.start();
    this.loopSource = source;
    this.loopGain = gain;
  }

  stopHoldLoop(): void {
    const context = this.context;

    if (!context || !this.loopSource || !this.loopGain) {
      return;
    }

    const source = this.loopSource;
    const gain = this.loopGain;
    this.loopSource = null;
    this.loopGain = null;
    gain.gain.cancelScheduledValues(context.currentTime);
    gain.gain.setValueAtTime(gain.gain.value, context.currentTime);
    gain.gain.linearRampToValueAtTime(0, context.currentTime + 0.06);
    source.stop(context.currentTime + 0.08);
  }

  playSuccess(): void {
    this.playOneShot(HOLD_SUCCESS_URL);
  }

  playCancel(): void {
    this.playOneShot(HOLD_CANCEL_URL);
  }

  private playOneShot(url: string): void {
    const context = this.context;
    const buffer = this.buffers.get(url);

    if (!context || !buffer) {
      return;
    }

    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer;
    gain.gain.value = ONE_SHOT_GAIN;
    source.connect(gain);
    gain.connect(context.destination);
    source.start();
  }

  private async loadAll(): Promise<void> {
    await Promise.all(
      [HOLD_LOOP_URL, HOLD_SUCCESS_URL, HOLD_CANCEL_URL].map(async (url) => {
        try {
          const response = await fetch(url);
          if (!response.ok) {
            return;
          }

          const data = await response.arrayBuffer();
          const buffer = await this.context!.decodeAudioData(data);
          this.buffers.set(url, buffer);
        } catch {
          // 静默降级：缺少音效不影响演示。
        }
      }),
    );
  }
}
