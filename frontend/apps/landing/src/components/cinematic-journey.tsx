"use client";

import { Badge, Container, buttonStyles } from "@workspace/ui";
import { ArrowRight, Check } from "lucide-react";
import Image from "next/image";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { EarlyAccessDialog } from "./early-access-dialog";
import {
  journeyShotIds,
  type JourneySample,
  type JourneyShotId,
} from "./cinematic-journey.types";
import styles from "./cinematic-journey.module.css";

const CinematicRobotScene = dynamic(() => import("./cinematic-robot-scene"), {
  ssr: false,
  loading: () => <RobotPoster mode="loading" />,
});

type Message = { label: string; text: string };
type SceneCopy = {
  identityKicker: string;
  identityProof: string[];
  identityStatus: string;
  identityStep: string;
  loading: string;
  orbitCue: string;
  orbitLabel: string;
  scrollCue: string;
  stageKicker: string;
  state: string;
  telemetry: string;
};
type Step = { title: string; description: string };
type StageMeta = { status: string; proof: string[] };

type CinematicJourneyProps = {
  channelEyebrow: string;
  channelNames: string[];
  hero: {
    badge: string;
    description: string;
    hub: string;
    identityTitle: string;
    messages: Message[];
    note: string;
    primary: string;
    scene: SceneCopy;
    secondary: string;
    titleBefore: string;
    titleHighlight: string;
    visualLabel: string;
  };
  how: {
    description: string;
    eyebrow: string;
    stageMeta: StageMeta[];
    steps: Step[];
    title: string;
  };
};

type RenderPath = "checking" | "live" | "reduced" | "unsupported";
type ShotRange = { end: number; id: JourneyShotId; start: number };

export function CinematicJourney({
  channelEyebrow,
  channelNames,
  hero,
  how,
}: CinematicJourneyProps) {
  const journeyRef = useRef<HTMLElement>(null);
  const timeline = useRef<JourneySample>({ index: 0, local: 0 });
  const activeIndexRef = useRef(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [renderPath, setRenderPath] = useState<RenderPath>("checking");
  const [sceneReady, setSceneReady] = useState(false);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        setRenderPath("reduced");
        return;
      }

      const probe = document.createElement("canvas");
      const context = probe.getContext("webgl2") ?? probe.getContext("webgl");
      if (!context) {
        console.info("[althair-journey] poster fallback: WebGL unavailable");
        setRenderPath("unsupported");
        return;
      }

      const loseContext = context.getExtension("WEBGL_lose_context");
      loseContext?.loseContext();
      setRenderPath("live");
    });

    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const root = journeyRef.current;
    if (!root) return;

    let frame = 0;
    let ranges: ShotRange[] = [];

    const measure = () => {
      const sections = Array.from(
        root.querySelectorAll<HTMLElement>("[data-shot]"),
      );
      ranges = sections.map((section, index) => ({
        id: (section.dataset.shot ?? journeyShotIds[index]) as JourneyShotId,
        start: section.getBoundingClientRect().top + window.scrollY,
        end:
          (sections[index + 1]?.getBoundingClientRect().top ??
            root.getBoundingClientRect().bottom) + window.scrollY,
      }));
    };

    const update = () => {
      frame = 0;
      if (!ranges.length) measure();

      const focusY = window.scrollY + window.innerHeight * 0.52;
      let index = 0;
      for (let candidate = 1; candidate < ranges.length; candidate += 1) {
        if (focusY >= ranges[candidate].start) index = candidate;
        else break;
      }

      const range = ranges[index] ?? ranges[0];
      if (!range) return;
      const span = Math.max(1, range.end - range.start);
      const local = Math.min(Math.max((focusY - range.start) / span, 0), 1);
      timeline.current = { index, local };
      root.dataset.activeShot = range.id;
      root.style.setProperty("--shot-local", local.toFixed(4));

      if (activeIndexRef.current !== index) {
        activeIndexRef.current = index;
        setActiveIndex(index);
      }
    };

    const requestUpdate = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(update);
    };

    const resize = new ResizeObserver(() => {
      measure();
      requestUpdate();
    });
    resize.observe(root);
    window.addEventListener("scroll", requestUpdate, { passive: true });
    window.addEventListener("resize", requestUpdate, { passive: true });
    document.fonts.ready.then(() => {
      measure();
      requestUpdate();
    });
    measure();
    frame = window.requestAnimationFrame(update);

    return () => {
      resize.disconnect();
      window.removeEventListener("scroll", requestUpdate);
      window.removeEventListener("resize", requestUpdate);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  const handleSceneReady = useCallback(() => setSceneReady(true), []);
  const activeMeta =
    activeIndex === 0
      ? {
          status: hero.scene.identityStatus,
          proof: hero.scene.identityProof,
        }
      : activeIndex === 1
        ? {
            status: hero.hub,
            proof: hero.messages.slice(0, 3).map((message) => message.label),
          }
        : (how.stageMeta[activeIndex - 2] ?? how.stageMeta[0]);
  const isReady =
    renderPath !== "checking" && (renderPath !== "live" || sceneReady);

  return (
    <>
      <section
        ref={journeyRef}
        id="top"
        className={styles.journey}
        data-active-shot={journeyShotIds[activeIndex]}
        data-cinematic-journey="true"
        data-render-path={renderPath === "live" ? "live" : "poster"}
        data-render-reason={renderPath}
        data-scene-ready={isReady ? "true" : "false"}
        data-orbit-ready={
          renderPath === "live" && sceneReady ? "true" : "false"
        }
        aria-label={hero.visualLabel}
      >
        <div className={styles.stickyStage}>
          <div className={styles.brandBackdrop} aria-hidden="true">
            <span>ALTHAIR</span>
            <span>AI</span>
          </div>
          <div className={styles.sceneFrame}>
            {renderPath === "live" ? (
              <CinematicRobotScene
                interactionDescriptionId="althair-logo-orbit-instructions"
                interactionLabel={hero.visualLabel}
                timeline={timeline}
                onReady={handleSceneReady}
              />
            ) : (
              <RobotPoster
                loadingLabel={hero.scene.loading}
                mode={renderPath === "checking" ? "loading" : "poster"}
              />
            )}
          </div>
          <span id="althair-logo-orbit-instructions" className="sr-only">
            {hero.scene.orbitLabel}
          </span>
          <div className={styles.sceneGrain} aria-hidden="true" />
          {renderPath === "live" ? (
            <div className={styles.orbitCue} aria-hidden="true">
              <i />
              <span>{hero.scene.orbitCue}</span>
            </div>
          ) : null}
          <div className={styles.telemetry} aria-hidden="true">
            <span>{hero.scene.telemetry}</span>
            <span className={styles.telemetryLive}>
              <i /> {hero.scene.state}
            </span>
          </div>
          <div className={styles.statusPanel} aria-hidden="true">
            <span>{String(activeIndex).padStart(2, "0")} / 05</span>
            <strong>{activeMeta?.status}</strong>
            <div>
              {(activeMeta?.proof ?? []).map((item) => (
                <em key={item}>{item}</em>
              ))}
            </div>
          </div>
          <div className={styles.shotRail} aria-hidden="true">
            {journeyShotIds.map((id, index) => (
              <span
                key={id}
                className={index === activeIndex ? styles.shotRailActive : ""}
              />
            ))}
          </div>
        </div>

        <div className={styles.chapters}>
          <section
            data-shot="identity"
            className={`${styles.shot} ${styles.identityShot} ${
              activeIndex === 0 ? styles.shotActive : ""
            }`}
          >
            <div className={styles.identityFrame}>
              <p className={styles.identityKicker}>
                {hero.scene.identityKicker}
              </p>
              <div className={styles.identityOutline}>ALTHAIR</div>
              <div className={styles.identityCaption}>
                <span>{hero.scene.identityStep}</span>
                <strong>{hero.identityTitle}</strong>
              </div>
              <div className={styles.scrollCue}>
                <span>{hero.scene.scrollCue}</span>
                <i />
              </div>
            </div>
          </section>

          <section
            data-shot="ready"
            className={`${styles.shot} ${styles.heroShot} ${
              activeIndex === 1 ? styles.shotActive : ""
            }`}
          >
            <div className={styles.heroCopy}>
              <p className={styles.kicker}>{hero.scene.stageKicker}</p>
              <Badge className="hero-badge">
                <span className="status-beacon" />
                {hero.badge}
              </Badge>
              <h1 className={styles.heroTitle}>
                {hero.titleBefore} <span>{hero.titleHighlight}</span>
              </h1>
              <p className={styles.heroDescription}>{hero.description}</p>
              <div className={styles.heroActions}>
                <EarlyAccessDialog label={hero.primary} />
                <a
                  href="#how"
                  className={buttonStyles({
                    variant: "secondary",
                    className: "hero-secondary-action",
                  })}
                >
                  {hero.secondary}
                  <ArrowRight className="size-4" />
                </a>
              </div>
              <p className={styles.heroNote}>
                <Check />
                {hero.note}
              </p>
            </div>
          </section>

          {how.steps.map((step, index) => {
            const shotIndex = index + 2;
            const processNumber = index + 1;
            const shotId = journeyShotIds[shotIndex];
            const meta = how.stageMeta[index];
            return (
              <section
                key={shotId}
                id={index === 0 ? "how" : `how-${shotId}`}
                data-shot={shotId}
                className={`${styles.shot} ${styles.processShot} ${
                  activeIndex === shotIndex ? styles.shotActive : ""
                }`}
              >
                <div className={styles.processCopy}>
                  <p className={styles.processKicker}>
                    {how.eyebrow} / {String(processNumber).padStart(2, "0")}
                  </p>
                  {index === 0 ? <h2>{how.title}</h2> : null}
                  <div className={styles.stepIndex}>
                    {String(processNumber).padStart(2, "0")}
                  </div>
                  <h3>{step.title}</h3>
                  <p>{step.description}</p>
                  {index === 0 ? (
                    <p className={styles.processLead}>{how.description}</p>
                  ) : null}
                  <div className={styles.proofRow}>
                    {(meta?.proof ?? []).map((item) => (
                      <span key={item}>{item}</span>
                    ))}
                  </div>
                </div>
              </section>
            );
          })}
        </div>
      </section>

      <div className="signal-rail" aria-label={channelEyebrow}>
        <Container className="signal-rail-inner">
          <span className="signal-rail-label">{channelEyebrow}</span>
          {channelNames.slice(0, 5).map((name) => (
            <span className="signal-rail-item" key={name}>
              <span />
              {name}
            </span>
          ))}
          <span className="signal-rail-item">
            <span />
            CRM
          </span>
        </Container>
      </div>
    </>
  );
}

function RobotPoster({
  loadingLabel,
  mode,
}: {
  loadingLabel?: string;
  mode: "loading" | "poster";
}) {
  return (
    <div className={styles.poster}>
      <span className={`${styles.posterOrbit} ${styles.posterOrbitOne}`} />
      <span className={`${styles.posterOrbit} ${styles.posterOrbitTwo}`} />
      <div className={styles.posterHead}>
        {[4, 3, 2, 1].map((layer) => (
          <Image
            key={layer}
            src="/robot-head.svg"
            alt=""
            fill
            priority
            unoptimized
            sizes="(max-width: 640px) 64vw, 34rem"
            className={styles.posterDepth}
            style={{
              transform: `translate3d(${layer * -1.5}px, ${layer * 1.5}px, 0)`,
              opacity: 0.12 + layer * 0.04,
            }}
          />
        ))}
        <Image
          src="/robot-head.svg"
          alt=""
          fill
          priority
          unoptimized
          sizes="(max-width: 640px) 64vw, 34rem"
        />
      </div>
      {mode === "loading" && loadingLabel ? (
        <div className={styles.sceneLoader}>
          <span>{loadingLabel}</span>
          <i />
        </div>
      ) : null}
    </div>
  );
}
