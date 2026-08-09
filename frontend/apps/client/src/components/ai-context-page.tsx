"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AssistantContextFields } from "@workspace/api-client";
import { Dialog } from "@workspace/ui";
import {
  Bot,
  CheckCircle2,
  Clock3,
  Eye,
  Save,
  Send,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { canEditWorkspace, shouldWarnUnsaved } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

const emptyContext: AssistantContextFields = {
  assistant_name: "",
  business_summary: "",
  business_description: "",
  target_customers: "",
  products_services: "",
  service_area: "",
  supported_languages: ["ru"],
  default_language: "ru",
  tone_of_voice: "",
  introduction: "",
  escalation_instructions: "",
  prohibited_topics: "",
  prohibited_actions: "",
  fallback_response: "",
  additional_instructions: "",
};

export function AiContextPage() {
  const t = useTranslations("aiContext");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const id = workspace.selectedOrganizationId!;
  const editable = canEditWorkspace(
    workspace.membership?.role,
    workspace.membership?.organization_status,
    "manage_company",
  );
  const publishable = canEditWorkspace(
    workspace.membership?.role,
    workspace.membership?.organization_status,
    "publish_context",
  );
  const [confirming, setConfirming] = useState(false);
  const query = useQuery({
    queryKey: ["assistant-context", id],
    queryFn: () => workspace.api.assistantContext(),
  });
  const revisions = useQuery({
    queryKey: ["assistant-revisions", id],
    queryFn: () => workspace.api.assistantRevisions(),
  });
  const form = useForm<AssistantContextFields>({ defaultValues: emptyContext });
  const watched = {
    ...emptyContext,
    ...useWatch({ control: form.control }),
  } as AssistantContextFields;
  useEffect(() => {
    if (query.data) form.reset(query.data);
  }, [form, query.data]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (shouldWarnUnsaved(form.formState.isDirty)) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [form.formState.isDirty]);
  const save = useMutation({
    mutationFn: (values: AssistantContextFields) =>
      workspace.api.updateAssistantContext(values),
    onSuccess: async (data) => {
      form.reset(data);
      await queryClient.invalidateQueries({
        queryKey: ["assistant-context", id],
      });
    },
  });
  const publish = useMutation({
    mutationFn: async () => {
      if (form.formState.isDirty) await save.mutateAsync(form.getValues());
      return workspace.api.publishAssistantContext();
    },
    onSuccess: async () => {
      setConfirming(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assistant-context", id] }),
        queryClient.invalidateQueries({
          queryKey: ["assistant-revisions", id],
        }),
        queryClient.invalidateQueries({ queryKey: ["overview", id] }),
      ]);
    },
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={(query.error as Error).message}
        onRetry={() => void query.refetch()}
      />
    );
  const profile = query.data!;
  const toggleLanguage = (language: "ru" | "uz" | "en") => {
    const current = form.getValues("supported_languages");
    const next = current.includes(language)
      ? current.filter((item) => item !== language)
      : [...current, language];
    if (next.length)
      form.setValue("supported_languages", next, { shouldDirty: true });
  };
  return (
    <>
      <PageHeading
        title={t("title")}
        description={t("description")}
        actions={
          <div className="heading-status">
            <StatusBadge status={profile.status} />
            <span>{t("version", { version: profile.version })}</span>
          </div>
        }
      />
      <div className="info-banner">
        <Sparkles />
        <div>
          <strong>{t("activationLaterTitle")}</strong>
          <p>{t("activationLaterDescription")}</p>
        </div>
      </div>
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      <div className="context-layout">
        <form
          className="panel context-form"
          onSubmit={form.handleSubmit((values) => save.mutate(values))}
        >
          {form.formState.isDirty ? (
            <div className="unsaved-banner" role="status">
              <Clock3 />
              {t("unsaved")}
            </div>
          ) : null}
          <ContextSection
            eyebrow={t("identityEyebrow")}
            title={t("identityTitle")}
            description={t("identityDescription")}
          >
            <label className="field">
              <span>{t("assistantName")}</span>
              <input
                disabled={!editable}
                required
                {...form.register("assistant_name")}
              />
            </label>
            <div className="field">
              <span>{t("languages")}</span>
              <div className="choice-row">
                {(["ru", "uz", "en"] as const).map((language) => (
                  <button
                    key={language}
                    className={
                      watched.supported_languages.includes(language)
                        ? "choice active"
                        : "choice"
                    }
                    type="button"
                    disabled={!editable}
                    aria-pressed={watched.supported_languages.includes(
                      language,
                    )}
                    onClick={() => toggleLanguage(language)}
                  >
                    {language.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
            <label className="field">
              <span>{t("defaultLanguage")}</span>
              <select
                disabled={!editable}
                {...form.register("default_language")}
              >
                <option value="ru">Русский</option>
                <option value="uz">O‘zbekcha</option>
                <option value="en">English</option>
              </select>
            </label>
            <label className="field">
              <span>{t("tone")}</span>
              <input disabled={!editable} {...form.register("tone_of_voice")} />
            </label>
          </ContextSection>
          <ContextSection
            eyebrow={t("businessEyebrow")}
            title={t("businessTitle")}
            description={t("businessDescriptionHint")}
          >
            <label className="field field-span">
              <span>{t("summary")}</span>
              <textarea
                disabled={!editable}
                required
                rows={3}
                {...form.register("business_summary")}
              />
            </label>
            <label className="field field-span">
              <span>{t("descriptionField")}</span>
              <textarea
                disabled={!editable}
                required
                rows={6}
                {...form.register("business_description")}
              />
            </label>
            <label className="field">
              <span>{t("customers")}</span>
              <textarea
                disabled={!editable}
                rows={4}
                {...form.register("target_customers")}
              />
            </label>
            <label className="field">
              <span>{t("services")}</span>
              <textarea
                disabled={!editable}
                required
                rows={4}
                {...form.register("products_services")}
              />
            </label>
            <label className="field field-span">
              <span>{t("area")}</span>
              <input disabled={!editable} {...form.register("service_area")} />
            </label>
          </ContextSection>
          <ContextSection
            eyebrow={t("behaviorEyebrow")}
            title={t("behaviorTitle")}
            description={t("behaviorDescription")}
          >
            <label className="field field-span">
              <span>{t("introduction")}</span>
              <textarea
                disabled={!editable}
                required
                rows={4}
                {...form.register("introduction")}
              />
            </label>
            <label className="field">
              <span>{t("escalation")}</span>
              <textarea
                disabled={!editable}
                rows={5}
                {...form.register("escalation_instructions")}
              />
            </label>
            <label className="field">
              <span>{t("fallback")}</span>
              <textarea
                disabled={!editable}
                required
                rows={5}
                {...form.register("fallback_response")}
              />
            </label>
          </ContextSection>
          <ContextSection
            eyebrow={t("boundariesEyebrow")}
            title={t("boundariesTitle")}
            description={t("boundariesDescription")}
          >
            <label className="field">
              <span>{t("prohibitedTopics")}</span>
              <textarea
                disabled={!editable}
                rows={5}
                {...form.register("prohibited_topics")}
              />
            </label>
            <label className="field">
              <span>{t("prohibitedActions")}</span>
              <textarea
                disabled={!editable}
                rows={5}
                {...form.register("prohibited_actions")}
              />
            </label>
            <label className="field field-span">
              <span>{t("additional")}</span>
              <textarea
                disabled={!editable}
                rows={5}
                {...form.register("additional_instructions")}
              />
            </label>
          </ContextSection>
          {save.error || publish.error ? (
            <div className="form-alert" role="alert">
              {((save.error ?? publish.error) as Error).message}
            </div>
          ) : null}
          {editable ? (
            <div className="sticky-form-actions">
              <button
                className="button secondary"
                type="submit"
                disabled={!form.formState.isDirty || save.isPending}
              >
                <Save />
                {save.isPending ? t("saving") : t("saveDraft")}
              </button>
              {publishable ? (
                <button
                  className="button primary"
                  type="button"
                  onClick={() => setConfirming(true)}
                >
                  <Send />
                  {t("publish")}
                </button>
              ) : null}
            </div>
          ) : null}
        </form>
        <aside className="context-sidebar">
          <section className="panel preview-panel">
            <div className="preview-heading">
              <Eye />
              <div>
                <span className="eyebrow">{t("previewEyebrow")}</span>
                <h2>{t("previewTitle")}</h2>
              </div>
            </div>
            <div className="assistant-preview">
              <div className="preview-avatar">
                <Bot />
              </div>
              <div>
                <strong>{watched.assistant_name || t("unnamed")}</strong>
                <span>{watched.tone_of_voice || t("tonePlaceholder")}</span>
              </div>
            </div>
            <blockquote>
              {watched.introduction || t("introductionPlaceholder")}
            </blockquote>
            <dl>
              <div>
                <dt>{t("understands")}</dt>
                <dd>{watched.business_summary || t("summaryPlaceholder")}</dd>
              </div>
              <div>
                <dt>{t("serves")}</dt>
                <dd>{watched.target_customers || t("customersPlaceholder")}</dd>
              </div>
              <div>
                <dt>{t("handsOff")}</dt>
                <dd>
                  {watched.escalation_instructions ||
                    t("escalationPlaceholder")}
                </dd>
              </div>
            </dl>
            <div className="preview-warning">
              <ShieldAlert />
              <p>{t("noPromptGenerated")}</p>
            </div>
          </section>
          <section className="panel revision-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{t("historyEyebrow")}</span>
                <h2>{t("historyTitle")}</h2>
              </div>
            </div>
            {revisions.data?.length ? (
              <ol>
                {revisions.data.slice(0, 5).map((revision) => (
                  <li key={revision.id}>
                    <CheckCircle2 />
                    <div>
                      <strong>
                        {t("publishedVersion", { version: revision.version })}
                      </strong>
                      <span>{revision.published_by_name}</span>
                      <time>
                        {new Intl.DateTimeFormat(undefined, {
                          dateStyle: "medium",
                          timeStyle: "short",
                        }).format(new Date(revision.published_at))}
                      </time>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p>{t("noHistory")}</p>
            )}
          </section>
        </aside>
      </div>
      {confirming ? (
        <Dialog
          open={confirming}
          onOpenChange={setConfirming}
          title={t("confirmTitle")}
          description={t("confirmDescription", {
            nextVersion: profile.version + 1,
          })}
          closeLabel={t("cancel")}
          className="confirm-dialog"
        >
          <div className="dialog-icon">
            <Send />
          </div>
          <div className="form-actions">
            <button
              className="button secondary"
              onClick={() => setConfirming(false)}
            >
              {t("cancel")}
            </button>
            <button
              className="button primary"
              onClick={() => publish.mutate()}
              disabled={publish.isPending}
            >
              {publish.isPending ? t("publishing") : t("confirmPublish")}
            </button>
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

function ContextSection({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="context-section">
      <div className="section-heading">
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      <div className="form-grid two">{children}</div>
    </section>
  );
}
