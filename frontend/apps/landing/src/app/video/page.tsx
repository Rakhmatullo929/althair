import { brand } from "@workspace/brand";
import { Logo } from "@workspace/ui";
import type { Metadata } from "next";
import Link from "next/link";
import { ProductVideoPlayer } from "./product-video-player";
import styles from "./video-page.module.css";

const canonicalUrl = "https://www.althair-ai.com/video";
const videoUrl = "/videos/althair-client-demo-0820-v1.mp4";
const posterUrl = "/videos/althair-client-demo-0820-v1-poster.webp";
const productionVideoUrl = `https://www.althair-ai.com${videoUrl}`;
const productionPosterUrl = `https://www.althair-ai.com${posterUrl}`;
const title = "Althair AI — демонстрация платформы";
const description =
  "AI Front Office, CRM, автоматизация обращений и бронирование в одном кабинете.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: canonicalUrl },
  openGraph: {
    type: "website",
    url: canonicalUrl,
    siteName: brand.name,
    title,
    description,
    images: [
      {
        url: productionPosterUrl,
        width: 1280,
        height: 720,
        alt: "Интерфейс платформы Althair AI",
      },
    ],
    videos: [
      {
        url: productionVideoUrl,
        secureUrl: productionVideoUrl,
        type: "video/mp4",
        width: 1920,
        height: 1080,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: [productionPosterUrl],
  },
  robots: { index: true, follow: true },
};

export default function VideoPage() {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link
          aria-label="Перейти на главную Althair AI"
          className={styles.logoLink}
          href="/ru"
        >
          <Logo />
        </Link>
      </header>

      <section className={styles.content} aria-labelledby="video-title">
        <div className={styles.intro}>
          <p className={styles.eyebrow}>Демонстрация продукта · 2:58</p>
          <h1 id="video-title">{title}</h1>
          <p className={styles.subtitle}>{description}</p>
        </div>

        <ProductVideoPlayer posterUrl={posterUrl} videoUrl={videoUrl} />
      </section>
    </main>
  );
}
