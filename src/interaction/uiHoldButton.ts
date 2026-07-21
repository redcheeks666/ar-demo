import {
  EMPTY_INDEX_FINGER_UI_HOLD,
  updateIndexFingerUiHold,
  type IndexFingerUiHoldState,
  type UiButtonStageRect,
} from './indexFingerUiHold';
import type { PreviewLayout } from './coordinateMapping';
import type { TrackedHand } from '../utils/types';
import type { UiHoldSfx } from '../audio/sfx';
import {
  UI_BUTTON_FINGER_HIT_SLOP_PX,
  UI_HOLD_REQUIRED_MS,
} from '../tracking/config';

/**
 * 单张 HUD 卡片的蓄力控制器。
 *
 * 复刻参考项目 demo-hand-model-button 的行为：
 * - 食指指尖进入卡片矩形开始蓄力，进度写入 --focus-action-progress；
 * - 蓄满触发后进入 needsClear：指尖必须离开卡片才能再次蓄力（防连触），
 *   离开前进度保持满格显示；
 * - 追加鼠标/触摸长按兜底：按住卡片走同样的蓄力时长，松开即取消。
 */
export interface UiHoldButtonOptions {
  requiredMs?: number;
  hitSlopPx?: number;
  onTrigger: () => void;
}

const CANCEL_SFX_MIN_PROGRESS = 0.15;

export class UiHoldButton {
  private readonly requiredMs: number;
  private readonly hitSlopPx: number;
  private readonly onTrigger: () => void;

  private fingerHold: IndexFingerUiHoldState = EMPTY_INDEX_FINGER_UI_HOLD;
  private fingerNeedsClear = false;
  private pointerDownAt: number | null = null;
  private pointerConsumed = false;
  private enabled = true;
  private wasCharging = false;
  private lastShownProgress = 0;
  private lastProgressValue = '';

  constructor(
    private readonly button: HTMLButtonElement,
    private readonly stageElement: HTMLElement,
    private readonly sfx: UiHoldSfx,
    options: UiHoldButtonOptions,
  ) {
    this.requiredMs = options.requiredMs ?? UI_HOLD_REQUIRED_MS;
    this.hitSlopPx = options.hitSlopPx ?? UI_BUTTON_FINGER_HIT_SLOP_PX;
    this.onTrigger = options.onTrigger;

    button.addEventListener('pointerdown', (event) => {
      if (!this.enabled) {
        return;
      }

      event.preventDefault();
      button.setPointerCapture(event.pointerId);
      this.pointerDownAt = performance.now();
      this.pointerConsumed = false;
    });

    const releasePointer = () => {
      this.pointerDownAt = null;
      this.pointerConsumed = false;
    };

    button.addEventListener('pointerup', releasePointer);
    button.addEventListener('pointercancel', releasePointer);
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;

    if (!enabled) {
      this.pointerDownAt = null;
      this.pointerConsumed = false;
    }
  }

  /** 每帧调用。active=false 时（例如加载中）不参与蓄力。 */
  update(input: {
    hands: readonly TrackedHand[];
    layout: PreviewLayout;
    timestamp: number;
    active: boolean;
  }): void {
    const active = input.active && this.enabled && !this.button.hidden;
    const rect = this.getStageRect(input.layout);

    let fingerProgress = 0;
    let fingerHit = false;
    let fingerTriggered = false;

    if (this.fingerNeedsClear) {
      const clearProbe = updateIndexFingerUiHold({
        active,
        hands: input.hands,
        layout: input.layout,
        rect,
        previous: EMPTY_INDEX_FINGER_UI_HOLD,
        requiredMs: this.requiredMs,
        timestamp: input.timestamp,
        hitSlopPx: this.hitSlopPx,
      });

      if (!active || clearProbe.hit === null) {
        this.fingerNeedsClear = false;
      } else {
        // 触发后指尖仍停留：保持满格显示，不重新蓄力。
        fingerProgress = 1;
      }

      this.fingerHold = EMPTY_INDEX_FINGER_UI_HOLD;
    } else {
      const holdUpdate = updateIndexFingerUiHold({
        active,
        hands: input.hands,
        layout: input.layout,
        rect,
        previous: this.fingerHold,
        requiredMs: this.requiredMs,
        timestamp: input.timestamp,
        hitSlopPx: this.hitSlopPx,
      });

      this.fingerHold = holdUpdate.hold;
      fingerProgress = holdUpdate.progress;
      fingerHit = holdUpdate.hit !== null;
      fingerTriggered = holdUpdate.triggered;
    }

    let pointerProgress = 0;
    let pointerCharging = false;
    let pointerTriggered = false;

    if (this.pointerDownAt !== null) {
      if (this.pointerConsumed) {
        pointerProgress = 1;
      } else {
        pointerProgress = Math.min(
          1,
          (performance.now() - this.pointerDownAt) / this.requiredMs,
        );
        pointerCharging = true;

        if (pointerProgress >= 1) {
          pointerTriggered = true;
          this.pointerConsumed = true;
          pointerCharging = false;
        }
      }
    }

    const triggered = fingerTriggered || pointerTriggered;
    const charging = !triggered && (fingerHit || pointerCharging);
    const progress = Math.max(fingerProgress, pointerProgress);

    if (triggered) {
      if (fingerTriggered) {
        this.fingerNeedsClear = true;
        this.fingerHold = EMPTY_INDEX_FINGER_UI_HOLD;
      }

      this.sfx.stopHoldLoop();
      this.sfx.playSuccess();
      this.setProgress(1);
      this.button.classList.remove('is-charging');
      this.wasCharging = false;
      this.lastShownProgress = 1;
      this.onTrigger();
      return;
    }

    if (charging && !this.wasCharging) {
      this.sfx.startHoldLoop();
    } else if (!charging && this.wasCharging) {
      this.sfx.stopHoldLoop();

      if (this.lastShownProgress >= CANCEL_SFX_MIN_PROGRESS) {
        this.sfx.playCancel();
      }
    }

    this.wasCharging = charging;
    this.lastShownProgress = progress;
    this.setProgress(progress);
    this.button.classList.toggle('is-charging', charging);
  }

  private setProgress(progress: number): void {
    const value = progress.toFixed(3);

    if (value === this.lastProgressValue) {
      return;
    }

    this.lastProgressValue = value;
    this.button.style.setProperty('--focus-action-progress', value);
  }

  private getStageRect(layout: PreviewLayout): UiButtonStageRect | null {
    if (this.button.hidden) {
      return null;
    }

    const buttonRect = this.button.getBoundingClientRect();
    const stageRect = this.stageElement.getBoundingClientRect();

    if (buttonRect.width <= 0 || buttonRect.height <= 0) {
      return null;
    }

    const scaleX = stageRect.width > 0 ? layout.stageWidth / stageRect.width : 1;
    const scaleY = stageRect.height > 0 ? layout.stageHeight / stageRect.height : 1;

    return {
      x: (buttonRect.left - stageRect.left) * scaleX,
      y: (buttonRect.top - stageRect.top) * scaleY,
      width: buttonRect.width * scaleX,
      height: buttonRect.height * scaleY,
    };
  }
}
