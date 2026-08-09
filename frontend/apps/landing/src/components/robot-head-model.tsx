"use client";

import Image from "next/image";
import { useRef, type PointerEvent } from "react";

type Message = { label: string; text: string };

export function RobotHeadModel({
  label,
  hub,
  messages,
}: {
  label: string;
  hub: string;
  messages: Message[];
}) {
  const stageRef = useRef<HTMLDivElement>(null);

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const stage = stageRef.current;
    if (!stage) return;
    const bounds = stage.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width - 0.5;
    const y = (event.clientY - bounds.top) / bounds.height - 0.5;
    stage.style.setProperty("--model-rotate-y", `${x * 12}deg`);
    stage.style.setProperty("--model-rotate-x", `${y * -9}deg`);
    stage.style.setProperty("--model-light-x", `${50 + x * 30}%`);
    stage.style.setProperty("--model-light-y", `${38 + y * 22}%`);
  }

  function resetModel() {
    const stage = stageRef.current;
    if (!stage) return;
    stage.style.setProperty("--model-rotate-y", "-5deg");
    stage.style.setProperty("--model-rotate-x", "2deg");
    stage.style.setProperty("--model-light-x", "42%");
    stage.style.setProperty("--model-light-y", "30%");
  }

  return (
    <div
      ref={stageRef}
      className="robot-stage"
      role="img"
      aria-label={label}
      onPointerMove={handlePointerMove}
      onPointerLeave={resetModel}
    >
      <div className="robot-aperture" aria-hidden="true">
        <span className="robot-orbit robot-orbit-one" />
        <span className="robot-orbit robot-orbit-two" />
        <span className="robot-axis robot-axis-x" />
        <span className="robot-axis robot-axis-y" />
        <svg
          className="robot-signal-flow"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          focusable="false"
        >
          <path
            className="signal-flow-line"
            pathLength="1"
            d="M88 31 C75 32 68 44 53 49"
          />
          <path
            className="signal-flow-line signal-flow-line-out"
            pathLength="1"
            d="M49 53 C39 60 31 69 14 75"
          />
          <circle className="signal-flow-packet" r="0.72">
            <animateMotion
              dur="2.4s"
              begin="0.25s"
              repeatCount="indefinite"
              path="M88 31 C75 32 68 44 53 49"
            />
          </circle>
          <circle
            className="signal-flow-packet signal-flow-packet-out"
            r="0.72"
          >
            <animateMotion
              dur="2.4s"
              begin="1.05s"
              repeatCount="indefinite"
              path="M49 53 C39 60 31 69 14 75"
            />
          </circle>
        </svg>
        <div className="robot-gimbal">
          <span className="robot-halo" />
          <div className="robot-head-stack">
            {Array.from({ length: 10 }, (_, index) => (
              <Image
                key={index}
                src="/robot-head.svg"
                alt=""
                draggable={false}
                fill
                sizes="(max-width: 640px) 46vw, 19rem"
                unoptimized
                loading="eager"
                className="robot-depth-layer"
                style={{
                  transform: `translate3d(${13 - index * 1.4}px, ${15 - index * 1.55}px, ${index * 2}px)`,
                }}
              />
            ))}
            <Image
              src="/robot-head.svg"
              alt=""
              draggable={false}
              fill
              sizes="(max-width: 640px) 46vw, 19rem"
              unoptimized
              loading="eager"
              className="robot-face"
            />
            <span className="robot-sheen" />
            <svg
              viewBox="203.60475 123.35311 142.7905 150.04754"
              className="robot-features"
              focusable="false"
            >
              <path
                d="M244.78 200.3c1.39-1.59 3.11-2.43 5.64-2.76 2.18-.28 5.54.49 7.5 1.73 2.48 1.56 4.82 5.14 4.63 7.07l-.13 1.42-6.59-.02c-5.77-.01-6.82-.14-8.47-1.01-3.87-2.04-4.68-4.05-2.58-6.43Zm42.13 4.51c1.68-4.02 4.71-6.54 8.57-7.11 5.71-.86 11.36 2.17 10.19 5.46-.24.66-1.19 1.81-2.11 2.56-2.04 1.64-5.32 2.26-12.04 2.27-5.42 0-5.83-.28-4.61-3.18Z"
                className="robot-eye-glow"
              />
              <path
                d="M265.65 243.6c-1.61-1.37-3.21-4.29-2.76-5.03.18-.29 4.8-.47 12.03-.47h11.73v1.36c0 1.77-3.08 4.98-6.04 6.3-4.61 2.05-11.09 1.11-14.96-2.16Z"
                className="robot-mouth-glow"
              />
            </svg>
          </div>
        </div>
        <div className="model-index">
          <span>ALTHAIR / 01</span>
          <span className="model-status-dot" />
        </div>
        <div className="model-caption">
          <span>{hub}</span>
          <strong>{messages[1]?.label ?? "AI"}</strong>
        </div>
        {messages[0] ? (
          <div className="model-note model-note-one">
            <span>{messages[0].label}</span>
            <p>{messages[0].text}</p>
          </div>
        ) : null}
        {messages[2] ? (
          <div className="model-note model-note-two">
            <span>{messages[2].label}</span>
            <p>{messages[2].text}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
