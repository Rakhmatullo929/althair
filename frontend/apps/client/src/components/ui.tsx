"use client";

import { AlertCircle, LoaderCircle, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeading({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="skeleton-grid" aria-label="Loading" aria-busy="true">
      <div className="skeleton skeleton-title" />
      <div className="skeleton-cards">
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" />
      </div>
      <div className="skeleton skeleton-panel" />
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon ? <div className="empty-icon">{icon}</div> : null}
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ErrorState({
  title,
  description,
  requestId,
  onRetry,
}: {
  title: string;
  description: string;
  requestId?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle aria-hidden="true" />
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
        {requestId ? (
          <p className="technical-detail">Request ID: {requestId}</p>
        ) : null}
      </div>
      {onRetry ? (
        <button className="button secondary" onClick={onRetry}>
          <RotateCcw aria-hidden="true" /> Retry
        </button>
      ) : null}
    </div>
  );
}

export function SubmitButton({
  pending,
  children,
}: {
  pending: boolean;
  children: ReactNode;
}) {
  return (
    <button className="button primary" type="submit" disabled={pending}>
      {pending ? <LoaderCircle className="spin" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status-badge status-${status}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}
