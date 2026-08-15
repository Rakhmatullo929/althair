"use client";

import {
  ArrowUp,
  Headphones,
  Loader2,
  MessageCircle,
  RotateCcw,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Language = "ru" | "uz" | "en";
type Config = {
  installation_key: string;
  display_name: string;
  assistant_label: string;
  greeting: string;
  offline_message: string;
  human_handoff_message: string;
  privacy_policy_url: string;
  terms_url: string;
  consent_text: string;
  require_consent: boolean;
  require_prechat_form: boolean;
  collect_name: boolean;
  collect_email: boolean;
  collect_phone: boolean;
  default_language: Language;
  supported_languages: Language[];
  origin_proof: string;
};
type EventItem = {
  id: number;
  type: string;
  message?: {
    id: string;
    sender: "visitor" | "ai" | "employee" | "system";
    body: string;
    occurred_at: string;
  };
  status?: string;
};
type Session = {
  session_id: string;
  session_token: string;
  expires_at: string;
};

const copy = {
  en: {
    start: "Start conversation",
    consent: "Consent is required to start.",
    name: "Name",
    email: "Email",
    phone: "Phone",
    send: "Send",
    placeholder: "Write a message…",
    human: "Talk to a person",
    close: "Close chat",
    reconnect: "Reconnect",
    expired: "This session expired. Start a new conversation.",
    offline: "Connection lost. Retrying…",
    retry: "Try again",
    powered: "Secure Web Chat",
    privacy: "Privacy",
    terms: "Terms",
    handed: "A team member will continue here.",
    required: "Please complete the required fields.",
    ai: "AI generated",
    delivered: "Delivered",
  },
  ru: {
    start: "Начать диалог",
    consent: "Для начала нужно согласие.",
    name: "Имя",
    email: "Email",
    phone: "Телефон",
    send: "Отправить",
    placeholder: "Напишите сообщение…",
    human: "Позвать оператора",
    close: "Закрыть чат",
    reconnect: "Подключиться снова",
    expired: "Сессия истекла. Начните новый диалог.",
    offline: "Связь потеряна. Подключаемся…",
    retry: "Повторить",
    powered: "Безопасный Web Chat",
    privacy: "Конфиденциальность",
    terms: "Условия",
    handed: "Диалог продолжит сотрудник.",
    required: "Заполните обязательные поля.",
    ai: "Создано AI",
    delivered: "Доставлено",
  },
  uz: {
    start: "Suhbatni boshlash",
    consent: "Boshlash uchun rozilik kerak.",
    name: "Ism",
    email: "Email",
    phone: "Telefon",
    send: "Yuborish",
    placeholder: "Xabar yozing…",
    human: "Operatorni chaqirish",
    close: "Chatni yopish",
    reconnect: "Qayta ulanish",
    expired: "Sessiya tugadi. Yangi suhbat boshlang.",
    offline: "Aloqa uzildi. Qayta ulanmoqda…",
    retry: "Qayta urinish",
    powered: "Xavfsiz Web Chat",
    privacy: "Maxfiylik",
    terms: "Shartlar",
    handed: "Suhbatni xodim davom ettiradi.",
    required: "Majburiy maydonlarni to‘ldiring.",
    ai: "AI yaratgan",
    delivered: "Yetkazildi",
  },
} as const;

function storage(key: string) {
  return {
    get: () => {
      try {
        const value = sessionStorage.getItem(key);
        return value ? (JSON.parse(value) as Session) : null;
      } catch {
        return null;
      }
    },
    set: (value: Session | null) => {
      try {
        if (value) sessionStorage.setItem(key, JSON.stringify(value));
        else sessionStorage.removeItem(key);
      } catch {
        /* privacy mode */
      }
    },
  };
}

export function WebChatWidget({
  publicKey,
  initialLocale = "ru",
  embedded = false,
}: {
  publicKey: string;
  initialLocale?: Language;
  embedded?: boolean;
}) {
  const api = (
    process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1"
  ).replace(/\/$/, "");
  const sessionStore = useMemo(
    () => storage(`althair:webchat:${publicKey}`),
    [publicKey],
  );
  const [config, setConfig] = useState<Config | null>(null);
  const [language, setLanguage] = useState<Language>(initialLocale);
  const [session, setSession] = useState<Session | null>(null);
  const sessionRef = useRef<Session | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [cursor, setCursor] = useState(0);
  const cursorRef = useRef(0);
  const [body, setBody] = useState("");
  const [identity, setIdentity] = useState({ name: "", email: "", phone: "" });
  const [consent, setConsent] = useState(false);
  const [state, setState] = useState<
    | "loading"
    | "welcome"
    | "active"
    | "sending"
    | "offline"
    | "expired"
    | "closed"
  >("loading");
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const labels = copy[language];

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const stored = sessionStore.get();
      if (stored) {
        sessionRef.current = stored;
        setSession(stored);
        setState("active");
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [sessionStore]);

  const mergeEvents = useCallback((incoming: EventItem[]) => {
    setEvents((current) =>
      Array.from(
        new Map(
          [...current, ...incoming].map((item) => [item.id, item]),
        ).values(),
      ).sort((a, b) => a.id - b.id),
    );
    const last = incoming.at(-1)?.id;
    if (last)
      setCursor((value) => {
        const next = Math.max(value, last);
        cursorRef.current = next;
        return next;
      });
  }, []);

  useEffect(() => {
    let active = true;
    const accept = (payload: Config) => {
      if (!active || payload.installation_key !== publicKey) return;
      setConfig(payload);
      setLanguage(
        payload.supported_languages.includes(initialLocale)
          ? initialLocale
          : payload.default_language,
      );
      // Config and sessionStorage hydrate independently. Preserve an already
      // restored active session if the config response wins the race.
      setState((current) =>
        session || current === "active" ? "active" : "welcome",
      );
    };
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; config?: Config };
      if (data?.type === "althair:webchat:init" && data.config)
        accept(data.config);
    };
    window.addEventListener("message", onMessage);
    const timer = window.setTimeout(
      () => {
        if (config) return;
        fetch(
          `${api}/public/web-chat/installations/${encodeURIComponent(publicKey)}/config/`,
          { headers: { Accept: "application/json" }, cache: "no-store" },
        )
          .then(async (response) => {
            if (!response.ok) throw new Error("widget_unavailable");
            return response.json() as Promise<Config>;
          })
          .then(accept)
          .catch(() => {
            if (active) {
              setError("widget_unavailable");
              setState("offline");
            }
          });
      },
      embedded ? 800 : 0,
    );
    return () => {
      active = false;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
    };
  }, [api, config, embedded, initialLocale, publicKey, session]);

  const authorizedFetch = useCallback(
    async (path: string, options: RequestInit = {}) => {
      if (!session) throw new Error("session_missing");
      const response = await fetch(
        `${api}/public/web-chat/sessions/${session.session_id}/${path}`,
        {
          ...options,
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${session.session_token}`,
            ...(options.headers ?? {}),
          },
        },
      );
      if (response.status === 401) {
        if (sessionRef.current?.session_token === session.session_token) {
          sessionRef.current = null;
          setState("expired");
          sessionStore.set(null);
        }
        throw new Error("session_expired");
      }
      if (!response.ok) {
        const data = (await response.json().catch(() => ({}))) as {
          error?: { code?: string };
        };
        throw new Error(data.error?.code ?? "request_failed");
      }
      return response;
    },
    [api, session, sessionStore],
  );

  const poll = useCallback(async () => {
    if (!session) return;
    try {
      const response = await authorizedFetch(`messages/?after=${cursor}`);
      const data = (await response.json()) as { events: EventItem[] };
      mergeEvents(data.events);
      setState((value) => (value === "offline" ? "active" : value));
    } catch (caught) {
      if ((caught as Error).message !== "session_expired") setState("offline");
    }
  }, [authorizedFetch, cursor, mergeEvents, session]);

  useEffect(() => {
    if (!session || !["active", "offline", "sending"].includes(state)) return;
    const visiblePoll = () => {
      if (document.visibilityState === "visible") void poll();
    };
    const initial = window.setTimeout(visiblePoll, 0);
    const interval = window.setInterval(visiblePoll, 2500);
    document.addEventListener("visibilitychange", visiblePoll);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", visiblePoll);
    };
  }, [poll, session, state]);
  useEffect(() => {
    if (!session || !["active", "offline", "sending"].includes(state)) return;
    const controller = new AbortController();
    let reconnect: number | undefined;
    const connect = async () => {
      try {
        const response = await authorizedFetch(
          `events/?after=${cursorRef.current}`,
          {
            headers: { "Last-Event-ID": String(cursorRef.current) },
            signal: controller.signal,
          },
        );
        const text = await response.text();
        const incoming = text
          .split("\n")
          .filter((line) => line.startsWith("data: "))
          .flatMap((line) => {
            try {
              return [JSON.parse(line.slice(6)) as EventItem];
            } catch {
              return [];
            }
          });
        if (incoming.length) mergeEvents(incoming);
      } catch {
        /* polling remains the transport fallback */
      }
      if (!controller.signal.aborted)
        reconnect = window.setTimeout(() => void connect(), 1200);
    };
    void connect();
    return () => {
      controller.abort();
      if (reconnect) window.clearTimeout(reconnect);
    };
  }, [authorizedFetch, mergeEvents, session, state]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  async function start() {
    if (
      !config ||
      (config.require_consent && !consent) ||
      (config.require_prechat_form &&
        config.collect_name &&
        !identity.name.trim())
    ) {
      setError(labels.required);
      return;
    }
    setError("");
    setState("loading");
    try {
      const response = await fetch(
        `${api}/public/web-chat/installations/${encodeURIComponent(publicKey)}/sessions/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({
            origin_proof: config.origin_proof,
            consent_accepted: consent,
            language,
          }),
        },
      );
      if (!response.ok) throw new Error("session_failed");
      const next = (await response.json()) as Session;
      if (identity.name || identity.email || identity.phone) {
        const identityResponse = await fetch(
          `${api}/public/web-chat/sessions/${next.session_id}/identity/`,
          {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${next.session_token}`,
            },
            body: JSON.stringify(identity),
          },
        );
        if (!identityResponse.ok) throw new Error("identity_failed");
      }
      sessionStore.set(next);
      sessionRef.current = next;
      setSession(next);
      setState("active");
    } catch {
      setError("session_failed");
      setState("welcome");
    }
  }

  async function send() {
    const text = body.trim();
    if (!text || !session) return;
    setBody("");
    setState("sending");
    setError("");
    try {
      const response = await authorizedFetch("messages/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ body: text }),
      });
      const data = (await response.json()) as { message: EventItem["message"] };
      mergeEvents([{ id: cursor + 1, type: "message", message: data.message }]);
      setState("active");
      await poll();
    } catch (caught) {
      setBody(text);
      setError((caught as Error).message);
      if (state !== "expired") setState("active");
    }
  }

  async function handoff() {
    try {
      await authorizedFetch("handoff/", { method: "POST" });
      mergeEvents([{ id: cursor + 1, type: "handoff", status: "requested" }]);
    } catch (caught) {
      setError((caught as Error).message);
    }
  }
  async function close() {
    try {
      await authorizedFetch("close/", { method: "POST" });
    } finally {
      sessionStore.set(null);
      sessionRef.current = null;
      setSession(null);
      setEvents([]);
      setState("closed");
    }
  }
  const restart = () => {
    sessionStore.set(null);
    sessionRef.current = null;
    setSession(null);
    setEvents([]);
    setCursor(0);
    setState("welcome");
  };

  return (
    <section
      className={`webchat-widget ${embedded ? "is-embedded" : ""}`}
      aria-label={config?.display_name ?? "Web Chat"}
    >
      <header className="webchat-widget-header">
        <div className="webchat-avatar">
          <MessageCircle />
        </div>
        <div>
          <strong>{config?.assistant_label ?? "Web Chat"}</strong>
          <span aria-live="polite">
            {state === "offline" ? labels.offline : config?.display_name}
          </span>
        </div>
        {session ? (
          <button aria-label={labels.close} onClick={() => void close()}>
            <X />
          </button>
        ) : null}
      </header>
      {state === "loading" ? (
        <div className="webchat-center">
          <Loader2 className="spin" />
          <span>{labels.reconnect}</span>
        </div>
      ) : null}
      {(state === "welcome" || state === "closed") && config ? (
        <section className="webchat-welcome">
          <div className="webchat-welcome-icon">
            <MessageCircle />
          </div>
          <h1>{config.greeting}</h1>
          <label>
            <span className="sr-only">Language</span>
            <select
              value={language}
              onChange={(event) => setLanguage(event.target.value as Language)}
            >
              {config.supported_languages.map((item) => (
                <option key={item} value={item}>
                  {item.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          {config.collect_name ? (
            <label>
              <span>{labels.name}</span>
              <input
                value={identity.name}
                onChange={(event) =>
                  setIdentity({ ...identity, name: event.target.value })
                }
              />
            </label>
          ) : null}
          {config.collect_email ? (
            <label>
              <span>{labels.email}</span>
              <input
                type="email"
                value={identity.email}
                onChange={(event) =>
                  setIdentity({ ...identity, email: event.target.value })
                }
              />
            </label>
          ) : null}
          {config.collect_phone ? (
            <label>
              <span>{labels.phone}</span>
              <input
                type="tel"
                value={identity.phone}
                onChange={(event) =>
                  setIdentity({ ...identity, phone: event.target.value })
                }
              />
            </label>
          ) : null}
          {config.require_consent ? (
            <label className="webchat-consent">
              <input
                type="checkbox"
                checked={consent}
                onChange={(event) => setConsent(event.target.checked)}
              />
              <span>{config.consent_text}</span>
            </label>
          ) : null}
          {error ? (
            <div className="webchat-error" role="alert">
              {error}
            </div>
          ) : null}
          <button className="webchat-primary" onClick={() => void start()}>
            {labels.start}
          </button>
          <div className="webchat-legal">
            {config.privacy_policy_url ? (
              <a
                href={config.privacy_policy_url}
                target="_blank"
                rel="noreferrer"
              >
                {labels.privacy}
              </a>
            ) : null}
            {config.terms_url ? (
              <a href={config.terms_url} target="_blank" rel="noreferrer">
                {labels.terms}
              </a>
            ) : null}
          </div>
        </section>
      ) : null}
      {state === "expired" ? (
        <section className="webchat-center">
          <RotateCcw />
          <p>{labels.expired}</p>
          <button className="webchat-primary" onClick={restart}>
            {labels.retry}
          </button>
        </section>
      ) : null}
      {session && ["active", "sending", "offline"].includes(state) ? (
        <>
          <section className="webchat-timeline" aria-live="polite">
            {events
              .filter((event) => event.message || event.type === "handoff")
              .map((event) =>
                event.message ? (
                  <article
                    className={`webchat-message ${event.message.sender === "visitor" ? "from-visitor" : "from-team"}`}
                    key={`${event.id}:${event.message.id}`}
                  >
                    <span>
                      {event.message.sender === "ai"
                        ? `${config?.assistant_label} · ${labels.ai}`
                        : event.message.sender === "employee"
                          ? config?.display_name
                          : ""}
                    </span>
                    <p>{event.message.body}</p>
                    <time dateTime={event.message.occurred_at}>
                      {new Date(event.message.occurred_at).toLocaleTimeString(
                        language,
                        { hour: "2-digit", minute: "2-digit" },
                      )}
                      {event.message.sender === "visitor"
                        ? ` · ${labels.delivered}`
                        : ""}
                    </time>
                  </article>
                ) : (
                  <div className="webchat-system" key={event.id}>
                    <Headphones />
                    {labels.handed}
                  </div>
                ),
              )}
            <div ref={endRef} />
          </section>
          <div className="webchat-actions">
            <button onClick={() => void handoff()}>
              <Headphones />
              {labels.human}
            </button>
          </div>
          {error ? (
            <div className="webchat-error" role="alert">
              {error}
            </div>
          ) : null}
          <form
            className="webchat-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <label className="sr-only" htmlFor="webchat-message">
              {labels.placeholder}
            </label>
            <textarea
              id="webchat-message"
              maxLength={4000}
              rows={1}
              value={body}
              placeholder={labels.placeholder}
              onChange={(event) => setBody(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            <button
              aria-label={labels.send}
              disabled={!body.trim() || state === "sending"}
            >
              <ArrowUp />
            </button>
          </form>
        </>
      ) : null}
      <footer className="webchat-powered">{labels.powered}</footer>
    </section>
  );
}
