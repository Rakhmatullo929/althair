"use client";

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

export function CrmDialog({
  title,
  description,
  closeLabel,
  onClose,
  children,
}: {
  title: string;
  description?: string;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="crm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="crm-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <h2 id="crm-dialog-title" ref={headingRef} tabIndex={-1}>
              {title}
            </h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label={closeLabel}
          >
            <X aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

export function PlainText({ value }: { value: string }) {
  const parts = value.split(/(https?:\/\/[^\s]+)/g);
  return (
    <span className="plain-message">
      {parts.map((part, index) =>
        /^https?:\/\//.test(part) ? (
          <a
            key={`${part}-${index}`}
            href={part}
            target="_blank"
            rel="noreferrer"
          >
            {part}
          </a>
        ) : (
          <span key={`${part}-${index}`}>{part}</span>
        ),
      )}
    </span>
  );
}

export function formatDateTime(value: string, locale?: string) {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
