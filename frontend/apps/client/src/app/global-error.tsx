"use client";

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main className="shell-loading">
          <section className="error-state">
            <div>
              <h1>Unexpected application error</h1>
              <p>The portal could not recover automatically.</p>
            </div>
            <button className="button primary" onClick={reset}>
              Try again
            </button>
          </section>
        </main>
      </body>
    </html>
  );
}
