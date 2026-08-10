"use client";

import {
  AdaptiveDpr,
  ContactShadows,
  Environment,
  Lightformer,
  PerspectiveCamera,
  Preload,
} from "@react-three/drei";
import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import {
  Bloom,
  EffectComposer,
  N8AO,
  Noise,
  Vignette,
} from "@react-three/postprocessing";
import { Suspense, useEffect, useMemo, useRef, type RefObject } from "react";
import * as THREE from "three";
import { SVGLoader } from "three/addons/loaders/SVGLoader.js";
import type {
  JourneySample,
  LogoOrbitInput,
  LogoOrbitState,
} from "./cinematic-journey.types";

type SceneProps = {
  interactionDescriptionId: string;
  interactionLabel: string;
  onReady: () => void;
  timeline: RefObject<JourneySample>;
};

type Pose = {
  cameraZ: number;
  position: [number, number, number];
  rotation: [number, number, number];
  scale: number;
};

type LogoMaterials = {
  edge: THREE.MeshPhysicalMaterial;
  face: THREE.MeshPhysicalMaterial;
};

type OrbitPhase = "dragging" | "idle" | "inertia" | "settling";

const ORBIT_PITCH_LIMIT = THREE.MathUtils.degToRad(52);
const ORBIT_YAW_PER_PIXEL = 0.0068;
const ORBIT_PITCH_PER_PIXEL = 0.0048;
const ORBIT_TOUCH_PITCH_PER_PIXEL = 0.0032;

const DESKTOP_POSES: Pose[] = [
  {
    cameraZ: 7.15,
    position: [0, -0.04, 0.08],
    rotation: [-0.035, 0.025, -0.008],
    scale: 0.73,
  },
  {
    cameraZ: 7.65,
    position: [1.46, -0.06, 0],
    rotation: [-0.04, 0.08, -0.012],
    scale: 0.72,
  },
  {
    cameraZ: 7.45,
    position: [1.34, -0.02, 0.08],
    rotation: [0.01, 0.56, -0.02],
    scale: 0.76,
  },
  {
    cameraZ: 7.3,
    position: [1.2, 0.02, 0.1],
    rotation: [-0.42, 0.18, 0.014],
    scale: 0.78,
  },
  {
    cameraZ: 7.65,
    position: [1.38, -0.04, 0.06],
    rotation: [0.035, -0.62, 0.02],
    scale: 0.78,
  },
  {
    cameraZ: 8.05,
    position: [1.08, 0.02, -0.06],
    rotation: [0.02, 0.06, -0.01],
    scale: 0.71,
  },
];

const MOBILE_POSES: Pose[] = [
  {
    cameraZ: 8.1,
    position: [0, 0.47, 0.08],
    rotation: [-0.03, 0.02, -0.006],
    scale: 0.52,
  },
  {
    cameraZ: 8.35,
    position: [0, 0.72, 0],
    rotation: [-0.035, 0.04, -0.008],
    scale: 0.52,
  },
  {
    cameraZ: 8.25,
    position: [0, 0.73, 0.03],
    rotation: [0.01, 0.42, -0.012],
    scale: 0.52,
  },
  {
    cameraZ: 8.15,
    position: [0, 0.75, 0.08],
    rotation: [-0.34, 0.14, 0.01],
    scale: 0.54,
  },
  {
    cameraZ: 8.35,
    position: [0, 0.72, 0],
    rotation: [0.02, -0.5, 0.014],
    scale: 0.53,
  },
  {
    cameraZ: 8.7,
    position: [0, 0.74, -0.08],
    rotation: [0.02, 0.05, -0.006],
    scale: 0.48,
  },
];

export default function CinematicRobotScene({
  interactionDescriptionId,
  interactionLabel,
  timeline,
  onReady,
}: SceneProps) {
  return (
    <Canvas
      shadows="basic"
      dpr={[1, 1.75]}
      camera={{ position: [0, 0, 7.7], fov: 36, near: 0.1, far: 40 }}
      gl={{
        alpha: false,
        antialias: true,
        powerPreference: "high-performance",
        stencil: false,
      }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.ACESFilmicToneMapping;
        gl.toneMappingExposure = 1.12;
      }}
    >
      <color attach="background" args={["#edf5ef"]} />
      <fog attach="fog" args={["#edf5ef", 10, 24]} />
      <Suspense fallback={null}>
        <LogoExperience
          descriptionId={interactionDescriptionId}
          label={interactionLabel}
          timeline={timeline}
        />
        <StudioEnvironment />
        <GroundingShadow />
        <Preload all />
        <RenderReporter onReady={onReady} />
      </Suspense>
      <CinematicGrade />
      <AdaptiveDpr />
    </Canvas>
  );
}

function CinematicGrade() {
  const { size } = useThree();
  const mobile = size.width < 700;

  return (
    <EffectComposer multisampling={mobile ? 0 : 2}>
      {mobile ? (
        <></>
      ) : (
        <N8AO
          aoRadius={0.5}
          intensity={1.05}
          distanceFalloff={0.78}
          quality="medium"
          halfRes
        />
      )}
      <Bloom
        intensity={0.42}
        luminanceThreshold={0.9}
        luminanceSmoothing={0.2}
        mipmapBlur
      />
      <Vignette offset={0.4} darkness={0.34} />
      <Noise opacity={0.018} premultiply />
    </EffectComposer>
  );
}

function GroundingShadow() {
  const { size } = useThree();

  return (
    <ContactShadows
      position={[size.width < 700 ? 0 : 0.72, -2.06, -0.1]}
      scale={7.2}
      opacity={0.28}
      blur={3.2}
      far={5}
      resolution={size.width < 700 ? 256 : 512}
      color="#214c3a"
    />
  );
}

function LogoExperience({
  descriptionId,
  label,
  timeline,
}: {
  descriptionId: string;
  label: string;
  timeline: RefObject<JourneySample>;
}) {
  const interaction = useLogoInteractionController({
    descriptionId,
    label,
    timeline,
  });
  const actor = useRef<THREE.Group>(null);
  const orbitGroup = useRef<THREE.Group>(null);
  const logoMesh = useRef<THREE.Mesh>(null);
  const cameraRig = useRef<THREE.PerspectiveCamera>(null);
  const storyLight = useRef<THREE.PointLight>(null);
  const rearLight = useRef<THREE.PointLight>(null);
  const rootElement = useRef<HTMLElement | null>(null);
  const lastShotIndex = useRef(0);
  const lastDiagnosticAt = useRef(0);
  const { gl, pointer, size } = useThree();
  const geometry = useExactLogoGeometry();
  const materials = useLogoMaterials();
  const materialsRef = useRef(materials);
  const workingColor = useMemo(() => new THREE.Color(), []);
  const calmEmissive = useMemo(() => new THREE.Color("#064b32"), []);
  const liveEmissive = useMemo(() => new THREE.Color("#27d98b"), []);
  const orbitEuler = useMemo(() => new THREE.Euler(0, 0, 0, "YXZ"), []);
  const targetQuaternion = useMemo(() => new THREE.Quaternion(), []);
  const renderedEuler = useMemo(() => new THREE.Euler(0, 0, 0, "YXZ"), []);
  const projectedCorner = useMemo(() => new THREE.Vector3(), []);

  useEffect(() => {
    rootElement.current = gl.domElement.closest<HTMLElement>(
      "[data-cinematic-journey]",
    );
  }, [gl]);

  /* eslint-disable react-hooks/immutability -- The R3F frame loop intentionally advances a mutable orbit ref without causing React renders. */
  useFrame(({ camera, clock }, delta) => {
    const sample = timeline.current;
    const orbit = interaction.current;
    const mobile = size.width < 700;
    const poses = mobile ? MOBILE_POSES : DESKTOP_POSES;
    const current = poses[Math.min(sample.index, poses.length - 1)];
    const next = poses[Math.min(sample.index + 1, poses.length - 1)];
    const exit = THREE.MathUtils.clamp((sample.local - 0.68) / 0.32, 0, 1);
    const blend = THREE.MathUtils.smoothstep(exit, 0, 1);
    const transitionArc =
      sample.index < poses.length - 1 ? Math.sin(blend * Math.PI) : 0;
    const transitionDirection = sample.index % 2 === 0 ? 1 : -1;
    if (lastShotIndex.current !== sample.index) {
      if (orbit.hasInteracted) orbit.settleToStory = true;
      lastShotIndex.current = sample.index;
    }

    if (!orbit.dragging) {
      orbit.yaw += orbit.yawVelocity * delta;
      orbit.pitch = THREE.MathUtils.clamp(
        orbit.pitch + orbit.pitchVelocity * delta,
        -ORBIT_PITCH_LIMIT,
        ORBIT_PITCH_LIMIT,
      );

      const friction = Math.exp(-3.15 * delta);
      orbit.yawVelocity *= friction;
      orbit.pitchVelocity *= friction;
      if (Math.abs(orbit.yawVelocity) < 0.004) orbit.yawVelocity = 0;
      if (Math.abs(orbit.pitchVelocity) < 0.004) orbit.pitchVelocity = 0;

      if (
        orbit.hasInteracted &&
        orbit.lastInputAt > 0 &&
        performance.now() - orbit.lastInputAt > 7600
      ) {
        orbit.settleToStory = true;
      }

      if (orbit.settleToStory) {
        const normalizedYaw = normalizeAngle(orbit.yaw);
        const nearestFullTurn = orbit.yaw - normalizedYaw;
        orbit.yawVelocity = THREE.MathUtils.damp(
          orbit.yawVelocity,
          0,
          7,
          delta,
        );
        orbit.pitchVelocity = THREE.MathUtils.damp(
          orbit.pitchVelocity,
          0,
          7,
          delta,
        );
        orbit.yaw = THREE.MathUtils.damp(
          orbit.yaw,
          nearestFullTurn,
          1.8,
          delta,
        );
        orbit.pitch = THREE.MathUtils.damp(orbit.pitch, 0, 1.8, delta);

        if (
          Math.abs(normalizeAngle(orbit.yaw)) < 0.002 &&
          Math.abs(orbit.pitch) < 0.002 &&
          Math.abs(orbit.yawVelocity) < 0.006 &&
          Math.abs(orbit.pitchVelocity) < 0.006
        ) {
          orbit.yaw = 0;
          orbit.pitch = 0;
          orbit.yawVelocity = 0;
          orbit.pitchVelocity = 0;
          orbit.settleToStory = false;
          orbit.input = "idle";
        }
      }
    }

    const orbitActive =
      orbit.dragging ||
      orbit.settleToStory ||
      Math.abs(orbit.yawVelocity) > 0.006 ||
      Math.abs(orbit.pitchVelocity) > 0.006 ||
      (orbit.lastInputAt > 0 && performance.now() - orbit.lastInputAt < 900);
    const pointerWeight = mobile || orbitActive ? 0 : 1 - transitionArc;
    const holdWeight = 1 - transitionArc;
    const breath = Math.sin(clock.elapsedTime * 0.52) * 0.018 * holdWeight;

    const targetPosition = [0, 1, 2].map((axis) =>
      THREE.MathUtils.lerp(current.position[axis], next.position[axis], blend),
    ) as [number, number, number];
    const targetRotation = [0, 1, 2].map((axis) =>
      THREE.MathUtils.lerp(current.rotation[axis], next.rotation[axis], blend),
    ) as [number, number, number];

    targetPosition[1] += breath + transitionArc * 0.055;
    targetPosition[2] += transitionArc * 0.14;
    targetRotation[0] +=
      transitionArc * 0.045 - pointer.y * 0.026 * pointerWeight;
    targetRotation[1] +=
      transitionArc * transitionDirection * 0.09 +
      pointer.x * 0.052 * pointerWeight;
    targetRotation[2] += transitionArc * transitionDirection * 0.028;

    const targetScale =
      THREE.MathUtils.lerp(current.scale, next.scale, blend) *
      (1 + transitionArc * 0.022);
    const targetCameraZ =
      THREE.MathUtils.lerp(current.cameraZ, next.cameraZ, blend) -
      transitionArc * 0.12;

    if (actor.current) {
      dampVector(actor.current.position, targetPosition, 7, delta);
      dampEuler(actor.current.rotation, targetRotation, 7, delta);
      const scale = THREE.MathUtils.damp(
        actor.current.scale.x,
        targetScale,
        7,
        delta,
      );
      actor.current.scale.setScalar(scale);
    }

    if (orbitGroup.current) {
      const introProgress = orbit.hasInteracted
        ? 1
        : THREE.MathUtils.clamp((clock.elapsedTime - 0.12) / 2.45, 0, 1);
      const introEase = 1 - Math.pow(1 - introProgress, 4);
      const introYaw = THREE.MathUtils.lerp(-1.16, 0, introEase);
      const introPitch = THREE.MathUtils.lerp(0.1, 0, introEase);
      const idleWeight = orbitActive ? 0 : introEase;
      const idleYaw = Math.sin(clock.elapsedTime * 0.34) * 0.036 * idleWeight;
      const idlePitch = Math.sin(clock.elapsedTime * 0.27 + 0.8) * 0.014;

      orbitEuler.set(
        orbit.pitch + introPitch + idlePitch,
        orbit.yaw + introYaw + idleYaw,
        0,
        "YXZ",
      );
      targetQuaternion.setFromEuler(orbitEuler);
      orbitGroup.current.quaternion.slerp(
        targetQuaternion,
        1 - Math.exp(-(orbit.dragging ? 18 : 10) * delta),
      );
    }

    if (cameraRig.current) {
      cameraRig.current.position.z = THREE.MathUtils.damp(
        cameraRig.current.position.z,
        targetCameraZ,
        6,
        delta,
      );
      cameraRig.current.lookAt(mobile ? 0 : 0.55, mobile ? 0.42 : 0, 0);
    }

    const weights = journeyWeights(sample);
    const receiveWeight = weights[2];
    const contextWeight = weights[3];
    const actionWeight = weights[4];
    const saveWeight = weights[5];
    const energy =
      receiveWeight * 0.22 +
      contextWeight * 0.64 +
      actionWeight * 0.92 +
      saveWeight * 0.48;
    const liveMaterials = materialsRef.current;

    liveMaterials.face.emissiveIntensity = THREE.MathUtils.damp(
      liveMaterials.face.emissiveIntensity,
      0.12 + energy + transitionArc * 0.52,
      7,
      delta,
    );
    liveMaterials.edge.emissiveIntensity = THREE.MathUtils.damp(
      liveMaterials.edge.emissiveIntensity,
      0.09 + contextWeight * 0.36 + saveWeight * 0.7 + transitionArc,
      8,
      delta,
    );
    workingColor.lerpColors(calmEmissive, liveEmissive, Math.min(1, energy));
    liveMaterials.face.emissive.lerp(workingColor, 1 - Math.exp(-6 * delta));

    if (storyLight.current) {
      storyLight.current.intensity = THREE.MathUtils.damp(
        storyLight.current.intensity,
        7 + energy * 12 + transitionArc * 6,
        8,
        delta,
      );
      storyLight.current.position.x =
        targetPosition[0] +
        THREE.MathUtils.lerp(-2.1, 2.1, blend) * transitionDirection;
      storyLight.current.position.y =
        targetPosition[1] + 0.65 + transitionArc * 0.5;
      storyLight.current.position.z = 2.6;
    }

    if (rearLight.current) {
      rearLight.current.intensity = THREE.MathUtils.damp(
        rearLight.current.intensity,
        2.5 + saveWeight * 16,
        8,
        delta,
      );
      rearLight.current.position.x = targetPosition[0];
      rearLight.current.position.y = targetPosition[1];
    }

    if (
      rootElement.current &&
      clock.elapsedTime - lastDiagnosticAt.current >= 0.12
    ) {
      lastDiagnosticAt.current = clock.elapsedTime;
      const phase = orbitPhase(orbit);
      const root = rootElement.current;
      root.dataset.orbitState = phase;
      root.dataset.orbitInput = orbit.input;
      root.dataset.orbitYaw = orbit.yaw.toFixed(4);
      root.dataset.orbitPitch = orbit.pitch.toFixed(4);
      root.dataset.orbitDragging = orbit.dragging ? "true" : "false";
      root.dataset.orbitInertia = phase === "inertia" ? "true" : "false";
      gl.domElement.setAttribute("data-orbit-state", phase);

      if (orbitGroup.current) {
        renderedEuler.setFromQuaternion(orbitGroup.current.quaternion, "YXZ");
        root.dataset.orbitRenderedYaw = renderedEuler.y.toFixed(4);
        root.dataset.orbitRenderedPitch = renderedEuler.x.toFixed(4);
      }

      const bounds = geometry.boundingBox;
      if (logoMesh.current && bounds) {
        logoMesh.current.updateWorldMatrix(true, false);
        camera.updateMatrixWorld();
        let minX = Number.POSITIVE_INFINITY;
        let minY = Number.POSITIVE_INFINITY;
        let maxX = Number.NEGATIVE_INFINITY;
        let maxY = Number.NEGATIVE_INFINITY;
        for (const x of [bounds.min.x, bounds.max.x]) {
          for (const y of [bounds.min.y, bounds.max.y]) {
            for (const z of [bounds.min.z, bounds.max.z]) {
              projectedCorner
                .set(x, y, z)
                .applyMatrix4(logoMesh.current.matrixWorld)
                .project(camera);
              minX = Math.min(minX, projectedCorner.x);
              minY = Math.min(minY, projectedCorner.y);
              maxX = Math.max(maxX, projectedCorner.x);
              maxY = Math.max(maxY, projectedCorner.y);
            }
          }
        }
        root.dataset.actorNdc = [minX, minY, maxX, maxY]
          .map((value) => value.toFixed(3))
          .join(",");
      }
    }
  });
  /* eslint-enable react-hooks/immutability */

  return (
    <>
      <PerspectiveCamera
        ref={cameraRig}
        makeDefault
        position={[0, 0, DESKTOP_POSES[0].cameraZ]}
        fov={36}
        near={0.1}
        far={40}
      />

      <group ref={actor} position={DESKTOP_POSES[0].position} scale={0.73}>
        <group ref={orbitGroup}>
          <mesh
            ref={logoMesh}
            name="exact-althair-logo-webgl"
            geometry={geometry}
            material={[materials.face, materials.edge]}
            castShadow
            receiveShadow
          />
        </group>
      </group>

      <pointLight
        ref={storyLight}
        position={[-1.4, 0.7, 2.6]}
        intensity={7}
        distance={8}
        decay={2}
        color="#9ff3c5"
      />
      <pointLight
        ref={rearLight}
        position={[0, 0, -2.8]}
        intensity={2.5}
        distance={7}
        decay={2}
        color="#55e6a0"
      />
    </>
  );
}

function useLogoInteractionController({
  descriptionId,
  label,
  timeline,
}: {
  descriptionId: string;
  label: string;
  timeline: RefObject<JourneySample>;
}) {
  const interaction = useRef<LogoOrbitState>({
    dragging: false,
    hasInteracted: false,
    input: "idle",
    lastInputAt: 0,
    pitch: 0,
    pitchVelocity: 0,
    settleToStory: false,
    shotIndex: 0,
    yaw: 0,
    yawVelocity: 0,
  });
  const { gl } = useThree();

  useEffect(() => {
    const canvas = gl.domElement;
    const root = canvas.closest<HTMLElement>("[data-cinematic-journey]");
    canvas.setAttribute("tabindex", "0");
    canvas.setAttribute("role", "application");
    canvas.setAttribute("aria-label", label);
    canvas.setAttribute("aria-describedby", descriptionId);
    canvas.setAttribute(
      "aria-keyshortcuts",
      "ArrowLeft ArrowRight ArrowUp ArrowDown Home Escape",
    );
    canvas.setAttribute("data-logo-rotator", "true");
    canvas.setAttribute("data-orbit-state", "idle");
    if (root) {
      root.dataset.orbitMode = "free-360-inertia";
      root.dataset.orbitReady = "true";
      root.dataset.orbitInput = "idle";
      root.dataset.orbitState = "idle";
    }

    let pointerId: number | null = null;
    let pointerKind: "mouse" | "pen" | "touch" = "mouse";
    let startX = 0;
    let startY = 0;
    let lastX = 0;
    let lastY = 0;
    let lastTime = 0;
    let horizontalTouch = false;

    const updateUi = (phase: OrbitPhase, input?: LogoOrbitInput) => {
      canvas.setAttribute("data-orbit-state", phase);
      if (root) {
        root.dataset.orbitState = phase;
        root.dataset.orbitDragging = phase === "dragging" ? "true" : "false";
        root.dataset.orbitInertia = phase === "inertia" ? "true" : "false";
        if (input) root.dataset.orbitInput = input;
      }
    };

    const rememberInput = (input: LogoOrbitInput) => {
      const orbit = interaction.current;
      orbit.hasInteracted = true;
      orbit.input = input;
      orbit.lastInputAt = performance.now();
      orbit.settleToStory = false;
      orbit.shotIndex = timeline.current.index;
      updateUi("dragging", input);
    };

    const safelyCapture = (id: number) => {
      try {
        canvas.setPointerCapture(id);
      } catch {
        // Pointer capture is a progressive enhancement on older Safari builds.
      }
    };

    const safelyRelease = (id: number) => {
      try {
        if (canvas.hasPointerCapture(id)) canvas.releasePointerCapture(id);
      } catch {
        // The pointer may already have been released by the browser.
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      if (pointerId !== null) return;
      if (event.pointerType !== "touch" && event.button !== 0) return;

      pointerId = event.pointerId;
      pointerKind =
        event.pointerType === "touch"
          ? "touch"
          : event.pointerType === "pen"
            ? "pen"
            : "mouse";
      startX = lastX = event.clientX;
      startY = lastY = event.clientY;
      lastTime = event.timeStamp;
      horizontalTouch = pointerKind !== "touch";
      interaction.current.yawVelocity = 0;
      interaction.current.pitchVelocity = 0;

      if (pointerKind !== "touch") {
        rememberInput("pointer");
        interaction.current.dragging = true;
        safelyCapture(event.pointerId);
      }
    };

    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return;

      if (pointerKind === "touch" && !horizontalTouch) {
        const totalX = event.clientX - startX;
        const totalY = event.clientY - startY;
        if (
          Math.abs(totalY) > 9 &&
          Math.abs(totalY) > Math.abs(totalX) * 1.12
        ) {
          pointerId = null;
          interaction.current.dragging = false;
          interaction.current.yawVelocity = 0;
          interaction.current.pitchVelocity = 0;
          updateUi("idle");
          return;
        }
        if (
          Math.abs(totalX) < 9 ||
          Math.abs(totalX) < Math.abs(totalY) * 1.05
        ) {
          return;
        }

        horizontalTouch = true;
        rememberInput("touch");
        interaction.current.dragging = true;
        safelyCapture(event.pointerId);
        lastX = event.clientX;
        lastY = event.clientY;
        lastTime = event.timeStamp;
        return;
      }

      if (!interaction.current.dragging) return;
      const elapsed = THREE.MathUtils.clamp(
        (event.timeStamp - lastTime) / 1000,
        0.008,
        0.06,
      );
      const deltaYaw = (event.clientX - lastX) * ORBIT_YAW_PER_PIXEL;
      const deltaPitch =
        pointerKind === "touch"
          ? (event.clientY - lastY) * ORBIT_TOUCH_PITCH_PER_PIXEL
          : (event.clientY - lastY) * ORBIT_PITCH_PER_PIXEL;
      const orbit = interaction.current;
      orbit.yaw += deltaYaw;
      orbit.pitch = THREE.MathUtils.clamp(
        orbit.pitch + deltaPitch,
        -ORBIT_PITCH_LIMIT,
        ORBIT_PITCH_LIMIT,
      );
      if (Math.abs(deltaYaw) > 0.0001 || Math.abs(deltaPitch) > 0.0001) {
        const nextYawVelocity = THREE.MathUtils.clamp(
          deltaYaw / elapsed,
          -7,
          7,
        );
        const nextPitchVelocity = THREE.MathUtils.clamp(
          deltaPitch / elapsed,
          -4.5,
          4.5,
        );
        orbit.yawVelocity = THREE.MathUtils.lerp(
          orbit.yawVelocity,
          nextYawVelocity,
          0.58,
        );
        orbit.pitchVelocity = THREE.MathUtils.lerp(
          orbit.pitchVelocity,
          nextPitchVelocity,
          0.58,
        );
      }
      orbit.lastInputAt = performance.now();
      lastX = event.clientX;
      lastY = event.clientY;
      lastTime = event.timeStamp;
    };

    const finishPointer = (event: PointerEvent, cancelled: boolean) => {
      if (event.pointerId !== pointerId) return;
      const releasedId = pointerId;
      pointerId = null;
      const orbit = interaction.current;
      const wasDragging = orbit.dragging;
      orbit.dragging = false;
      orbit.lastInputAt = performance.now();
      if (cancelled) {
        orbit.yawVelocity = 0;
        orbit.pitchVelocity = 0;
      }
      const hasInertia =
        !cancelled &&
        wasDragging &&
        (Math.abs(orbit.yawVelocity) > 0.04 ||
          Math.abs(orbit.pitchVelocity) > 0.04);
      updateUi(hasInertia ? "inertia" : "idle", orbit.input);
      safelyRelease(releasedId);
    };

    const onPointerUp = (event: PointerEvent) => finishPointer(event, false);
    const onPointerCancel = (event: PointerEvent) => finishPointer(event, true);
    const onLostPointerCapture = (event: PointerEvent) => {
      if (event.pointerId === pointerId) finishPointer(event, false);
    };

    const onKeyDown = (event: KeyboardEvent) => {
      const yawDirection =
        event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      const pitchDirection =
        event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;

      if (yawDirection || pitchDirection) {
        event.preventDefault();
        const orbit = interaction.current;
        orbit.hasInteracted = true;
        orbit.input = "keyboard";
        orbit.lastInputAt = performance.now();
        orbit.settleToStory = false;
        orbit.shotIndex = timeline.current.index;
        if (yawDirection) {
          orbit.yaw += yawDirection * THREE.MathUtils.degToRad(12);
          orbit.yawVelocity = yawDirection * 0.72;
        }
        if (pitchDirection) {
          orbit.pitch = THREE.MathUtils.clamp(
            orbit.pitch + pitchDirection * THREE.MathUtils.degToRad(8),
            -ORBIT_PITCH_LIMIT,
            ORBIT_PITCH_LIMIT,
          );
          orbit.pitchVelocity = pitchDirection * 0.42;
        }
        updateUi("inertia", "keyboard");
        return;
      }

      if (event.key === "Home" || event.key === "Escape") {
        event.preventDefault();
        const orbit = interaction.current;
        orbit.hasInteracted = true;
        orbit.input = "keyboard";
        orbit.lastInputAt = performance.now();
        orbit.dragging = false;
        orbit.yawVelocity = 0;
        orbit.pitchVelocity = 0;
        orbit.settleToStory = true;
        updateUi("settling", "keyboard");
      }
    };

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerCancel);
    canvas.addEventListener("lostpointercapture", onLostPointerCapture);
    canvas.addEventListener("keydown", onKeyDown);

    return () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerCancel);
      canvas.removeEventListener("lostpointercapture", onLostPointerCapture);
      canvas.removeEventListener("keydown", onKeyDown);
      canvas.removeAttribute("aria-describedby");
      canvas.removeAttribute("aria-keyshortcuts");
      canvas.removeAttribute("aria-label");
      canvas.removeAttribute("role");
      canvas.removeAttribute("tabindex");
      canvas.removeAttribute("data-logo-rotator");
      canvas.removeAttribute("data-orbit-state");
    };
  }, [descriptionId, gl, interaction, label, timeline]);

  return interaction;
}

function useExactLogoGeometry() {
  const svg = useLoader(SVGLoader, "/robot-head.svg");
  const geometry = useMemo(() => {
    const shapes = svg.paths.flatMap((path) => path.toShapes());
    const exactGeometry = new THREE.ExtrudeGeometry(shapes, {
      bevelEnabled: true,
      bevelSegments: 5,
      bevelSize: 1.8,
      bevelThickness: 2,
      curveSegments: 12,
      depth: 18,
      steps: 1,
    });
    exactGeometry.scale(0.0255, -0.0255, 0.0255);
    exactGeometry.center();
    exactGeometry.computeVertexNormals();
    return exactGeometry;
  }, [svg]);

  useEffect(() => () => geometry.dispose(), [geometry]);
  return geometry;
}

function useLogoMaterials() {
  const roughnessMap = useMemo(() => createRoughnessTexture(), []);
  const materials = useMemo<LogoMaterials>(
    () => ({
      face: new THREE.MeshPhysicalMaterial({
        clearcoat: 0.76,
        clearcoatRoughness: 0.18,
        color: "#078b58",
        emissive: "#064b32",
        emissiveIntensity: 0.12,
        envMapIntensity: 1.45,
        metalness: 0.16,
        roughness: 0.34,
        roughnessMap,
      }),
      edge: new THREE.MeshPhysicalMaterial({
        clearcoat: 0.58,
        clearcoatRoughness: 0.18,
        color: "#0a805e",
        emissive: "#064b37",
        emissiveIntensity: 0.09,
        envMapIntensity: 2.1,
        metalness: 0.52,
        roughness: 0.25,
        roughnessMap,
      }),
    }),
    [roughnessMap],
  );

  useEffect(
    () => () => {
      Object.values(materials).forEach((material) => material.dispose());
      roughnessMap.dispose();
    },
    [materials, roughnessMap],
  );

  return materials;
}

function createRoughnessTexture() {
  const size = 96;
  const data = new Uint8Array(size * size * 4);
  for (let index = 0; index < size * size; index += 1) {
    const x = index % size;
    const y = Math.floor(index / size);
    const noise =
      148 +
      Math.round(Math.sin(x * 0.67 + y * 1.13) * 15) +
      Math.round(Math.sin(x * 1.91 - y * 0.43) * 8);
    const offset = index * 4;
    data[offset] = noise;
    data[offset + 1] = noise;
    data[offset + 2] = noise;
    data[offset + 3] = 255;
  }
  const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(4, 4);
  texture.needsUpdate = true;
  return texture;
}

function StudioEnvironment() {
  return (
    <>
      <ambientLight intensity={0.16} color="#dff2e7" />
      <directionalLight
        position={[-4.8, 6.8, 5.8]}
        intensity={2.55}
        color="#f7fff8"
        castShadow
        shadow-mapSize={[1024, 1024]}
      />
      <pointLight
        position={[4.4, 1.6, 3.8]}
        intensity={12}
        distance={11}
        color="#83dcae"
      />
      <Environment resolution={192} environmentIntensity={1.08}>
        <Lightformer
          form="rect"
          intensity={4.2}
          color="#ffffff"
          position={[-4, 4, 5]}
          rotation={[0, 0.45, 0]}
          scale={[5, 5, 1]}
        />
        <Lightformer
          form="ring"
          intensity={2.4}
          color="#72d6a1"
          position={[4, 1, 1]}
          rotation={[0, -Math.PI / 2, 0]}
          scale={3}
        />
        <Lightformer
          form="rect"
          intensity={1.4}
          color="#dff79e"
          position={[0, -4, 2]}
          rotation={[Math.PI / 2, 0, 0]}
          scale={[4, 2, 1]}
        />
        <Lightformer
          form="rect"
          intensity={1.9}
          color="#d9fff0"
          position={[0, 2, -4]}
          rotation={[0, Math.PI, 0]}
          scale={[3, 4, 1]}
        />
      </Environment>
    </>
  );
}

function RenderReporter({ onReady }: { onReady: () => void }) {
  const { scene } = useThree();
  const frames = useRef(0);
  const reported = useRef(false);

  useFrame(() => {
    if (reported.current) return;
    frames.current += 1;
    if (frames.current < 4) return;

    const root = document.querySelector<HTMLElement>(
      "[data-cinematic-journey]",
    );
    if (root) {
      let actorMeshes = 0;
      let meshes = 0;
      let triangles = 0;
      scene.traverse((object) => {
        if (!(object instanceof THREE.Mesh) || !isVisibleInTree(object)) return;
        if (object.name === "exact-althair-logo-webgl") actorMeshes += 1;
        const positionCount = object.geometry.attributes.position?.count ?? 0;
        meshes += 1;
        triangles += object.geometry.index
          ? object.geometry.index.count / 3
          : positionCount / 3;
      });
      root.dataset.sceneMeshes = String(meshes);
      root.dataset.actorMeshes = String(actorMeshes);
      root.dataset.orbitMode = "free-360-inertia";
      root.dataset.orbitReady = "true";
      root.dataset.proceduralModel = "false";
      root.dataset.triangles = String(Math.round(triangles));
      root.dataset.modelType = "exact-svg-logo-webgl-3d";
    }
    reported.current = true;
    onReady();
  });
  return null;
}

function dampVector(
  vector: THREE.Vector3,
  target: [number, number, number],
  lambda: number,
  delta: number,
) {
  vector.x = THREE.MathUtils.damp(vector.x, target[0], lambda, delta);
  vector.y = THREE.MathUtils.damp(vector.y, target[1], lambda, delta);
  vector.z = THREE.MathUtils.damp(vector.z, target[2], lambda, delta);
}

function dampEuler(
  euler: THREE.Euler,
  target: [number, number, number],
  lambda: number,
  delta: number,
) {
  euler.x = THREE.MathUtils.damp(euler.x, target[0], lambda, delta);
  euler.y = THREE.MathUtils.damp(euler.y, target[1], lambda, delta);
  euler.z = THREE.MathUtils.damp(euler.z, target[2], lambda, delta);
}

function normalizeAngle(angle: number) {
  return Math.atan2(Math.sin(angle), Math.cos(angle));
}

function orbitPhase(orbit: LogoOrbitState): OrbitPhase {
  if (orbit.dragging) return "dragging";
  if (orbit.settleToStory) return "settling";
  if (
    Math.abs(orbit.yawVelocity) > 0.006 ||
    Math.abs(orbit.pitchVelocity) > 0.006
  ) {
    return "inertia";
  }
  return "idle";
}

function isVisibleInTree(object: THREE.Object3D) {
  let current: THREE.Object3D | null = object;
  while (current) {
    if (!current.visible) return false;
    current = current.parent;
  }
  return true;
}

function journeyWeights(sample: JourneySample) {
  const weights = [0, 0, 0, 0, 0, 0];
  const exit = THREE.MathUtils.clamp((sample.local - 0.68) / 0.32, 0, 1);
  const blend = THREE.MathUtils.smoothstep(exit, 0, 1);
  weights[sample.index] = 1 - blend;
  weights[Math.min(sample.index + 1, weights.length - 1)] += blend;
  return weights;
}
