"use client";

import { useRef, useState } from "react";
import styles from "./video-page.module.css";

type ProductVideoPlayerProps = {
  posterUrl: string;
  videoUrl: string;
};

export function ProductVideoPlayer({
  posterUrl,
  videoUrl,
}: ProductVideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [hasError, setHasError] = useState(false);

  const retry = () => {
    setHasError(false);
    videoRef.current?.load();
  };

  return (
    <div className={styles.playerGroup}>
      <div className={styles.playerFrame}>
        <video
          aria-label="Демонстрация платформы Althair AI"
          className={styles.video}
          controls
          onError={() => setHasError(true)}
          onLoadedMetadata={() => setHasError(false)}
          playsInline
          poster={posterUrl}
          preload="metadata"
          ref={videoRef}
        >
          <source src={videoUrl} type="video/mp4" />
          Ваш браузер не поддерживает воспроизведение видео.
        </video>
      </div>

      {hasError ? (
        <div className={styles.error} role="alert">
          <p>Видео сейчас не удалось загрузить.</p>
          <div className={styles.errorActions}>
            <button
              className={styles.retryButton}
              onClick={retry}
              type="button"
            >
              Повторить
            </button>
            <a href={videoUrl} rel="noreferrer" target="_blank">
              Открыть видео отдельно
            </a>
          </div>
        </div>
      ) : (
        <a
          className={styles.directLink}
          href={videoUrl}
          rel="noreferrer"
          target="_blank"
        >
          Открыть видео отдельно
        </a>
      )}
    </div>
  );
}
