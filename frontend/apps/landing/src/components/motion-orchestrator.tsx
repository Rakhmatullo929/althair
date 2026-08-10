"use client";

import { BrandMark } from "@workspace/brand/mark";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

type IntroState = "active" | "exiting" | "done";

const INTRO_STORAGE_KEY = "althair-motion-intro-seen";

export function MotionOrchestrator() {
  const t = useTranslations("motion.intro");
  const sources = t.raw("sources") as string[];
  const [introState, setIntroState] = useState<IntroState>("active");

  useEffect(() => {
    const root = document.documentElement;
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let introSeen = false;
    try {
      introSeen = window.sessionStorage.getItem(INTRO_STORAGE_KEY) === "1";
    } catch {
      // Storage can be blocked in privacy modes; motion still works without it.
    }

    root.classList.add("motion-ready");

    if (reduceMotion || introSeen) {
      root.classList.add("motion-intro-skipped");
    } else {
      root.classList.add("motion-intro-active");
      try {
        window.sessionStorage.setItem(INTRO_STORAGE_KEY, "1");
      } catch {
        // Treat the intro as session-local when storage is unavailable.
      }
    }

    const revealTargets = Array.from(
      document.querySelectorAll<HTMLElement>(".reveal, .motion-item"),
    );

    for (const target of revealTargets) {
      if (target.classList.contains("motion-item")) {
        const siblings = target.parentElement
          ? Array.from(target.parentElement.children).filter((node) =>
              node.classList.contains("motion-item"),
            )
          : [];
        const order = Math.max(0, siblings.indexOf(target));
        target.style.setProperty(
          "--motion-delay",
          `${Math.min(order, 5) * 55}ms`,
        );
      }
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-revealed");
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -9% 0px", threshold: 0.08 },
    );

    revealTargets.forEach((target) => observer.observe(target));

    let frame = 0;
    const updateScrollProgress = () => {
      frame = 0;
      const hero = document.querySelector<HTMLElement>(".althair-hero");
      const heroRange = Math.max((hero?.offsetHeight ?? 1) * 0.72, 1);
      const heroProgress = Math.min(Math.max(window.scrollY / heroRange, 0), 1);
      const pageRange = Math.max(
        document.documentElement.scrollHeight - window.innerHeight,
        1,
      );
      root.style.setProperty("--hero-progress", heroProgress.toFixed(4));
      root.style.setProperty(
        "--page-progress",
        Math.min(Math.max(window.scrollY / pageRange, 0), 1).toFixed(4),
      );
    };

    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(updateScrollProgress);
    };

    updateScrollProgress();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });

    const skipTimer =
      reduceMotion || introSeen
        ? window.setTimeout(() => setIntroState("done"), 0)
        : 0;
    const exitTimer =
      reduceMotion || introSeen
        ? 0
        : window.setTimeout(() => setIntroState("exiting"), 1050);
    const doneTimer =
      reduceMotion || introSeen
        ? 0
        : window.setTimeout(() => {
            setIntroState("done");
            root.classList.remove("motion-intro-active");
          }, 1680);

    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
      if (skipTimer) window.clearTimeout(skipTimer);
      if (exitTimer) window.clearTimeout(exitTimer);
      if (doneTimer) window.clearTimeout(doneTimer);
      root.classList.remove(
        "motion-ready",
        "motion-intro-active",
        "motion-intro-skipped",
      );
      root.style.removeProperty("--hero-progress");
      root.style.removeProperty("--page-progress");
    };
  }, []);

  return (
    <>
      {introState !== "done" ? (
        <div
          className={`althair-intro ${introState === "exiting" ? "is-exiting" : ""}`}
          aria-hidden="true"
        >
          <span className="intro-panel intro-panel-left" />
          <span className="intro-panel intro-panel-right" />
          <div className="intro-blueprint" />
          <div className="intro-core">
            <p>{t("kicker")}</p>
            <BrandMark className="intro-mark" />
            <div className="intro-progress">
              <span />
            </div>
            <strong>{t("flow")}</strong>
          </div>
          <span className="intro-source intro-source-one">{sources[0]}</span>
          <span className="intro-source intro-source-two">{sources[1]}</span>
          <span className="intro-source intro-source-three">{sources[2]}</span>
          <span className="intro-source intro-source-four">{sources[3]}</span>
        </div>
      ) : null}
      <div className="page-progress-rail" aria-hidden="true">
        <span />
      </div>
    </>
  );
}
