import {
  ACESFilmicToneMapping,
  AmbientLight,
  BoxGeometry,
  Color,
  DirectionalLight,
  DoubleSide,
  Group,
  MathUtils,
  Mesh,
  MeshStandardMaterial,
  OrthographicCamera,
  PlaneGeometry,
  Quaternion,
  Scene,
  SphereGeometry,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
  type Material,
  type Object3D,
} from 'three';
import type { PreviewLayout } from '../interaction/coordinateMapping';
import type { FacePose } from '../tracking/facePoseAdapter';

const HELMET_MOTION = {
  /** Full assembly/disassembly time, independent of display refresh rate. */
  assemblyDurationSeconds: 1.15,
  /** Exponential convergence rates; alpha = 1 - exp(-rate * deltaSeconds). */
  positionResponse: 13,
  rotationResponse: 11,
  scaleResponse: 10,
  visibilityResponse: 9,
  maximumDeltaSeconds: 0.1,
} as const;

export type HelmetState = 'hidden' | 'assembling' | 'equipped' | 'disassembling';

interface AnimatedPart {
  object: Group;
  homePosition: Vector3;
  homeScale: Vector3;
  entryOffset: Vector3;
  start: number;
  end: number;
  materials: Array<{ material: MeshStandardMaterial; opacity: number }>;
}

export class HelmetRenderer {
  private readonly renderer: WebGLRenderer;
  private readonly scene = new Scene();
  private readonly camera = new OrthographicCamera(-1, 1, 1, -1, 0.1, 2000);
  private readonly helmetRoot = new Group();
  private readonly parts: AnimatedPart[] = [];
  private readonly currentPosition = new Vector3();
  private readonly targetPosition = new Vector3();
  private readonly currentRotation = new Quaternion();
  private readonly targetRotation = new Quaternion();
  private currentScale = 1;
  private targetScale = 1;
  private faceOpacity = 0;
  private animationProgress = 0;
  private targetEquipped = false;
  private faceModeActive = false;
  private hasPose = false;
  private lastTimestamp: number | null = null;
  private renderedWidth = 0;
  private renderedHeight = 0;
  private renderedPixelRatio = 0;
  private disposed = false;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.renderer = new WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setClearColor(new Color(0x000000), 0);
    this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.camera.position.set(0, 0, 1000);
    this.camera.lookAt(0, 0, 0);

    this.helmetRoot.name = 'HelmetRoot';
    this.helmetRoot.visible = false;
    this.scene.add(this.helmetRoot);
    this.scene.add(new AmbientLight(0x80b8c8, 1.35));

    const keyLight = new DirectionalLight(0xffd99a, 2.2);
    keyLight.position.set(-3, 5, 8);
    this.scene.add(keyLight);

    const rimLight = new DirectionalLight(0x58dfff, 2.8);
    rimLight.position.set(5, 1, 4);
    this.scene.add(rimLight);

    this.createHelmet();
    this.canvas.hidden = true;
  }

  getState(): HelmetState {
    if (this.animationProgress <= 0) {
      return this.targetEquipped ? 'assembling' : 'hidden';
    }

    if (this.animationProgress >= 1) {
      return this.targetEquipped ? 'equipped' : 'disassembling';
    }

    return this.targetEquipped ? 'assembling' : 'disassembling';
  }

  setFaceMode(active: boolean): void {
    if (this.faceModeActive === active) {
      return;
    }

    this.faceModeActive = active;

    if (!active) {
      this.targetEquipped = false;
      this.animationProgress = 0;
      this.faceOpacity = 0;
      this.hasPose = false;
      this.lastTimestamp = null;
      this.helmetRoot.visible = false;
      this.canvas.hidden = true;
      this.renderer.clear();
    }
  }

  setEquipped(equipped: boolean): void {
    if (!this.faceModeActive || this.disposed) {
      return;
    }

    this.targetEquipped = equipped;
  }

  update(pose: FacePose, layout: PreviewLayout, timestamp: number): void {
    if (this.disposed || !this.faceModeActive) {
      return;
    }

    const deltaSeconds = this.computeDeltaSeconds(timestamp);
    this.updateAnimation(deltaSeconds);

    if (pose.faceVisible) {
      this.targetPosition.set(pose.position.x, pose.position.y, pose.position.z);
      this.targetRotation.copy(pose.rotation);
      this.targetScale = pose.scale;

      if (!this.hasPose) {
        this.currentPosition.copy(this.targetPosition);
        this.currentRotation.copy(this.targetRotation);
        this.currentScale = this.targetScale;
        this.hasPose = true;
      }
    }

    const positionAlpha = dampAlpha(HELMET_MOTION.positionResponse, deltaSeconds);
    const rotationAlpha = dampAlpha(HELMET_MOTION.rotationResponse, deltaSeconds);
    const scaleAlpha = dampAlpha(HELMET_MOTION.scaleResponse, deltaSeconds);
    const visibilityAlpha = dampAlpha(HELMET_MOTION.visibilityResponse, deltaSeconds);

    if (this.hasPose) {
      this.currentPosition.lerp(this.targetPosition, positionAlpha);
      this.currentRotation.slerp(this.targetRotation, rotationAlpha);
      this.currentScale = MathUtils.lerp(this.currentScale, this.targetScale, scaleAlpha);
    }

    this.faceOpacity = MathUtils.lerp(
      this.faceOpacity,
      pose.faceVisible ? 1 : 0,
      visibilityAlpha,
    );

    const shouldRender =
      this.animationProgress > 0.0001 && this.faceOpacity > 0.005 && this.hasPose;

    if (!shouldRender) {
      this.helmetRoot.visible = false;
      this.canvas.hidden = true;
      return;
    }

    this.resize(layout);
    this.canvas.hidden = false;
    this.helmetRoot.visible = true;
    this.helmetRoot.position.copy(this.currentPosition);
    this.helmetRoot.quaternion.copy(this.currentRotation);
    this.helmetRoot.scale.setScalar(this.currentScale);
    this.applyPartAnimation();
    this.renderer.render(this.scene, this.camera);
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }

    this.disposed = true;
    const geometries = new Set<{ dispose: () => void }>();
    const materials = new Set<Material>();

    this.helmetRoot.traverse((object) => {
      if (!(object instanceof Mesh)) {
        return;
      }

      geometries.add(object.geometry);
      const meshMaterials = Array.isArray(object.material)
        ? object.material
        : [object.material];
      meshMaterials.forEach((material) => materials.add(material));
    });

    geometries.forEach((geometry) => geometry.dispose());
    materials.forEach((material) => material.dispose());
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.canvas.hidden = true;
  }

  private computeDeltaSeconds(timestamp: number): number {
    const delta =
      this.lastTimestamp === null
        ? 1 / 60
        : Math.min(
            Math.max((timestamp - this.lastTimestamp) / 1000, 0),
            HELMET_MOTION.maximumDeltaSeconds,
          );
    this.lastTimestamp = timestamp;
    return delta;
  }

  private updateAnimation(deltaSeconds: number): void {
    const direction = this.targetEquipped ? 1 : -1;
    this.animationProgress = MathUtils.clamp(
      this.animationProgress +
        (direction * deltaSeconds) / HELMET_MOTION.assemblyDurationSeconds,
      0,
      1,
    );
  }

  private resize(layout: PreviewLayout): void {
    const width = Math.max(Math.round(layout.stageWidth), 1);
    const height = Math.max(Math.round(layout.stageHeight), 1);
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);

    if (
      width !== this.renderedWidth ||
      height !== this.renderedHeight ||
      pixelRatio !== this.renderedPixelRatio
    ) {
      this.renderedWidth = width;
      this.renderedHeight = height;
      this.renderedPixelRatio = pixelRatio;
      this.renderer.setPixelRatio(pixelRatio);
      this.renderer.setSize(width, height, false);
      this.camera.left = -width / 2;
      this.camera.right = width / 2;
      this.camera.top = height / 2;
      this.camera.bottom = -height / 2;
      this.camera.updateProjectionMatrix();
    }
  }

  private applyPartAnimation(): void {
    for (const part of this.parts) {
      const localProgress = MathUtils.clamp(
        (this.animationProgress - part.start) / (part.end - part.start),
        0,
        1,
      );
      const eased = smootherStep(localProgress);
      part.object.position
        .copy(part.homePosition)
        .addScaledVector(part.entryOffset, 1 - eased);
      part.object.scale.copy(part.homeScale).multiplyScalar(0.78 + eased * 0.22);

      for (const entry of part.materials) {
        entry.material.opacity = entry.opacity * eased * this.faceOpacity;
      }
    }
  }

  private createHelmet(): void {
    const shellMaterial = () =>
      new MeshStandardMaterial({
        color: 0x111c25,
        metalness: 0.82,
        roughness: 0.3,
        transparent: true,
      });
    const edgeMaterial = () =>
      new MeshStandardMaterial({
        color: 0xb68b45,
        emissive: 0x5f3d14,
        emissiveIntensity: 0.65,
        metalness: 0.76,
        roughness: 0.28,
        transparent: true,
      });
    const glowMaterial = () =>
      new MeshStandardMaterial({
        color: 0x8defff,
        emissive: 0x2adcf7,
        emissiveIntensity: 3.4,
        metalness: 0.2,
        roughness: 0.18,
        transparent: true,
      });

    const crown = new Group();
    crown.name = 'Crown';
    const crownDome = new Mesh(
      new SphereGeometry(1.2, 28, 14, 0, Math.PI * 2, 0, Math.PI * 0.47),
      shellMaterial(),
    );
    crownDome.scale.set(1, 1.02, 0.42);
    crownDome.position.set(0, 0.45, -0.02);
    crown.add(crownDome);
    crown.add(this.box(1.9, 0.22, 0.34, edgeMaterial(), 0, 0.7, 0.34));
    this.addPart(crown, new Vector3(0, 1.35, 0), 0, 0.52);

    const templeLeft = this.shellColumn('TempleLeft', -1.02, 0.19, -0.1);
    this.addPart(templeLeft, new Vector3(-1.1, 0.05, 0), 0.08, 0.6);

    const templeRight = this.shellColumn('TempleRight', 1.02, 0.19, 0.1);
    this.addPart(templeRight, new Vector3(1.1, 0.05, 0), 0.08, 0.6);

    const cheekLeft = new Group();
    cheekLeft.name = 'CheekLeft';
    cheekLeft.add(this.box(0.38, 0.96, 0.42, shellMaterial(), -0.78, -0.58, 0.27, -0.2));
    cheekLeft.add(this.box(0.11, 0.7, 0.46, edgeMaterial(), -0.58, -0.54, 0.36, -0.2));
    this.addPart(cheekLeft, new Vector3(-1.15, -0.14, 0), 0.22, 0.72);

    const cheekRight = new Group();
    cheekRight.name = 'CheekRight';
    cheekRight.add(this.box(0.38, 0.96, 0.42, shellMaterial(), 0.78, -0.58, 0.27, 0.2));
    cheekRight.add(this.box(0.11, 0.7, 0.46, edgeMaterial(), 0.58, -0.54, 0.36, 0.2));
    this.addPart(cheekRight, new Vector3(1.15, -0.14, 0), 0.22, 0.72);

    const jaw = new Group();
    jaw.name = 'Jaw';
    jaw.add(this.box(1.2, 0.3, 0.46, shellMaterial(), 0, -1.18, 0.27));
    jaw.add(this.box(0.48, 0.16, 0.5, edgeMaterial(), 0, -1.03, 0.36));
    this.addPart(jaw, new Vector3(0, -1.2, 0), 0.38, 0.82);

    const visor = new Group();
    visor.name = 'Visor';
    const visorMaterial = new MeshStandardMaterial({
      color: 0x163c48,
      emissive: 0x0d7e91,
      emissiveIntensity: 0.8,
      metalness: 0.25,
      roughness: 0.16,
      opacity: 0.48,
      transparent: true,
      side: DoubleSide,
    });
    const visorMesh = new Mesh(new PlaneGeometry(1.65, 0.62), visorMaterial);
    visorMesh.position.set(0, 0.08, 0.48);
    visor.add(visorMesh);
    visor.add(this.box(1.72, 0.07, 0.12, edgeMaterial(), 0, 0.41, 0.49));
    visor.add(this.box(1.72, 0.07, 0.12, edgeMaterial(), 0, -0.25, 0.49));
    this.addPart(visor, new Vector3(0, 0, 0.72), 0.58, 0.9);

    const eyeLeft = new Group();
    eyeLeft.name = 'EyeLightLeft';
    eyeLeft.add(this.box(0.48, 0.09, 0.1, glowMaterial(), -0.48, 0.1, 0.57, -0.08));
    this.addPart(eyeLeft, new Vector3(-0.12, 0, 0.15), 0.82, 1);

    const eyeRight = new Group();
    eyeRight.name = 'EyeLightRight';
    eyeRight.add(this.box(0.48, 0.09, 0.1, glowMaterial(), 0.48, 0.1, 0.57, 0.08));
    this.addPart(eyeRight, new Vector3(0.12, 0, 0.15), 0.82, 1);
  }

  private shellColumn(name: string, x: number, y: number, rotationZ: number): Group {
    const group = new Group();
    group.name = name;
    const material = new MeshStandardMaterial({
      color: 0x111c25,
      metalness: 0.82,
      roughness: 0.3,
      transparent: true,
    });
    group.add(this.box(0.34, 1.55, 0.48, material, x, y, 0.12, rotationZ));
    return group;
  }

  private box(
    width: number,
    height: number,
    depth: number,
    material: MeshStandardMaterial,
    x: number,
    y: number,
    z: number,
    rotationZ = 0,
  ): Mesh {
    const mesh = new Mesh(new BoxGeometry(width, height, depth), material);
    mesh.position.set(x, y, z);
    mesh.rotation.z = rotationZ;
    return mesh;
  }

  private addPart(
    object: Group,
    entryOffset: Vector3,
    start: number,
    end: number,
  ): void {
    const materials: AnimatedPart['materials'] = [];
    object.traverse((child: Object3D) => {
      if (!(child instanceof Mesh)) {
        return;
      }

      const meshMaterials = Array.isArray(child.material)
        ? child.material
        : [child.material];

      for (const material of meshMaterials) {
        if (material instanceof MeshStandardMaterial) {
          material.transparent = true;
          materials.push({ material, opacity: material.opacity });
        }
      }
    });

    this.helmetRoot.add(object);
    this.parts.push({
      object,
      homePosition: object.position.clone(),
      homeScale: object.scale.clone(),
      entryOffset,
      start,
      end,
      materials,
    });
  }
}

function dampAlpha(response: number, deltaSeconds: number): number {
  return 1 - Math.exp(-response * deltaSeconds);
}

function smootherStep(value: number): number {
  return value * value * value * (value * (value * 6 - 15) + 10);
}
