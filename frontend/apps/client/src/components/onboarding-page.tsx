"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  AssistantContextFields,
  OnboardingState,
  WorkingHours,
} from "@workspace/api-client";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Building2,
  Check,
  GitBranch,
  PartyPopper,
  Users,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageSkeleton } from "./ui";

type OnboardingValues = {
  name: string;
  public_name: string;
  industry: string;
  website: string;
  phone: string;
  email: string;
  timezone: string;
  language: "ru" | "uz" | "en";
  business_summary: string;
  business_description: string;
  target_customers: string;
  products_services: string;
  service_area: string;
  branch_name: string;
  branch_address: string;
  branch_phone: string;
  branch_timezone: string;
  branch_open: string;
  branch_close: string;
  invite_email: string;
  invite_role: "admin" | "manager" | "agent" | "viewer";
  assistant_name: string;
  tone_of_voice: string;
  introduction: string;
  escalation_instructions: string;
  prohibited_topics: string;
  prohibited_actions: string;
  fallback_response: string;
  additional_instructions: string;
};

const steps = [
  ["company", Building2],
  ["business", Building2],
  ["branches", GitBranch],
  ["team", Users],
  ["behavior", Bot],
  ["review", PartyPopper],
] as const;

function valuesFromState(state: OnboardingState): OnboardingValues {
  const branch =
    state.branches.find((item) => item.is_active) ?? state.branches[0];
  const period = branch?.working_hours.mon?.[0];
  return {
    name: state.organization.name,
    public_name: state.profile.public_business_name,
    industry: state.organization.industry,
    website: String(state.profile.public_contact_information.website ?? ""),
    phone: String(state.profile.public_contact_information.phone ?? ""),
    email: String(state.profile.public_contact_information.email ?? ""),
    timezone: state.organization.timezone,
    language: state.organization.default_language,
    business_summary: state.assistant_context.business_summary,
    business_description: state.assistant_context.business_description,
    target_customers: state.assistant_context.target_customers,
    products_services: state.assistant_context.products_services,
    service_area: state.assistant_context.service_area,
    branch_name: branch?.name ?? "",
    branch_address: branch?.address ?? "",
    branch_phone: branch?.phone ?? "",
    branch_timezone: branch?.timezone ?? state.organization.timezone,
    branch_open: period?.open ?? "09:00",
    branch_close: period?.close ?? "18:00",
    invite_email: "",
    invite_role: "agent",
    assistant_name: state.assistant_context.assistant_name,
    tone_of_voice: state.assistant_context.tone_of_voice,
    introduction: state.assistant_context.introduction,
    escalation_instructions: state.assistant_context.escalation_instructions,
    prohibited_topics: state.assistant_context.prohibited_topics,
    prohibited_actions: state.assistant_context.prohibited_actions,
    fallback_response: state.assistant_context.fallback_response,
    additional_instructions: state.assistant_context.additional_instructions,
  };
}

export function OnboardingPage() {
  const t = useTranslations("onboarding");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const router = useRouter();
  const id = workspace.selectedOrganizationId!;
  const [currentStep, setCurrentStep] = useState(1);
  const [saveError, setSaveError] = useState<Error | null>(null);
  const query = useQuery({
    queryKey: ["onboarding", id],
    queryFn: () => workspace.api.onboarding(id),
  });
  const form = useForm<OnboardingValues>();
  useEffect(() => {
    if (query.data) {
      form.reset(valuesFromState(query.data));
      queueMicrotask(() =>
        setCurrentStep(
          query.data!.profile.onboarding_completed_at
            ? 6
            : query.data!.profile.onboarding_current_step,
        ),
      );
    }
  }, [form, query.data]);
  const mutation = useMutation({
    mutationFn: async ({
      step,
      values,
    }: {
      step: number;
      values: OnboardingValues;
    }) => {
      setSaveError(null);
      const common = { step };
      if (step === 1)
        return workspace.api.saveOnboarding(id, {
          ...common,
          organization: {
            name: values.name,
            industry: values.industry,
            timezone: values.timezone,
            default_language: values.language,
          },
          profile: {
            public_business_name: values.public_name,
            public_contact_information: {
              website: values.website,
              phone: values.phone,
              email: values.email,
            },
          },
        });
      if (step === 2)
        return workspace.api.saveOnboarding(id, {
          ...common,
          assistant_context: {
            business_summary: values.business_summary,
            business_description: values.business_description,
            target_customers: values.target_customers,
            products_services: values.products_services,
            service_area: values.service_area,
          },
        });
      if (step === 3) {
        const periods = [
          { open: values.branch_open, close: values.branch_close },
        ];
        const working_hours: WorkingHours = {
          mon: periods,
          tue: periods,
          wed: periods,
          thu: periods,
          fri: periods,
        };
        const branchBody = {
          name: values.branch_name,
          address: values.branch_address,
          phone: values.branch_phone,
          timezone: values.branch_timezone,
          working_hours,
          is_active: true,
        };
        const branch =
          query.data?.branches.find((item) => item.is_active) ??
          query.data?.branches[0];
        if (branch) await workspace.api.updateBranch(id, branch.id, branchBody);
        else await workspace.api.createBranch(id, branchBody);
        return workspace.api.saveOnboarding(id, common);
      }
      if (step === 4) {
        if (values.invite_email)
          await workspace.api.createInvitation(id, {
            email: values.invite_email,
            role: values.invite_role,
          });
        return workspace.api.saveOnboarding(id, common);
      }
      if (step === 5)
        return workspace.api.saveOnboarding(id, {
          ...common,
          assistant_context: {
            assistant_name: values.assistant_name,
            supported_languages: [values.language],
            default_language: values.language,
            tone_of_voice: values.tone_of_voice,
            introduction: values.introduction,
            escalation_instructions: values.escalation_instructions,
            prohibited_topics: values.prohibited_topics,
            prohibited_actions: values.prohibited_actions,
            fallback_response: values.fallback_response,
            additional_instructions: values.additional_instructions,
          } satisfies Partial<AssistantContextFields>,
        });
      return workspace.api.saveOnboarding(id, { ...common, complete: true });
    },
    onSuccess: async (state, variables) => {
      form.reset(valuesFromState(state));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["onboarding", id] }),
        queryClient.invalidateQueries({ queryKey: ["overview", id] }),
      ]);
      if (variables.step === 6) router.replace("./");
      else setCurrentStep(Math.min(6, variables.step + 1));
    },
    onError: (error) => setSaveError(error as Error),
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
  const state = query.data!;
  const next = form.handleSubmit((values) =>
    mutation.mutate({ step: currentStep, values }),
  );
  return (
    <div className="onboarding-shell">
      <header className="onboarding-header">
        <div>
          <span className="eyebrow">{t("eyebrow")}</span>
          <h1>{t("title")}</h1>
          <p>{t("description")}</p>
        </div>
        <div className="onboarding-progress">
          <span>{t("progress", { current: currentStep, total: 6 })}</span>
          <div className="progress-track">
            <span
              style={{
                width: `${(state.profile.onboarding_completed_steps.length / 6) * 100}%`,
              }}
            />
          </div>
        </div>
      </header>
      <ol className="stepper" aria-label={t("stepsLabel")}>
        {steps.map(([key, Icon], index) => {
          const number = index + 1;
          const complete =
            state.profile.onboarding_completed_steps.includes(number);
          return (
            <li
              key={key}
              className={
                number === currentStep ? "current" : complete ? "complete" : ""
              }
            >
              <button
                type="button"
                onClick={() => {
                  if (
                    complete ||
                    number <= state.profile.onboarding_current_step
                  )
                    setCurrentStep(number);
                }}
                disabled={
                  !complete && number > state.profile.onboarding_current_step
                }
              >
                <span>{complete ? <Check /> : <Icon />}</span>
                <div>
                  <small>{t("step", { number })}</small>
                  <strong>{t(`steps.${key}`)}</strong>
                </div>
              </button>
            </li>
          );
        })}
      </ol>
      <form className="panel onboarding-card" onSubmit={next}>
        <div className="onboarding-card-heading">
          <span className="step-number">0{currentStep}</span>
          <div>
            <span className="eyebrow">
              {t(`content.${steps[currentStep - 1]![0]}.eyebrow`)}
            </span>
            <h2>{t(`content.${steps[currentStep - 1]![0]}.title`)}</h2>
            <p>{t(`content.${steps[currentStep - 1]![0]}.description`)}</p>
          </div>
        </div>
        {currentStep === 1 ? <CompanyStep form={form} t={t} /> : null}
        {currentStep === 2 ? <BusinessStep form={form} t={t} /> : null}
        {currentStep === 3 ? <BranchStep form={form} t={t} /> : null}
        {currentStep === 4 ? (
          <TeamStep form={form} t={t} owner={workspace.user?.email ?? ""} />
        ) : null}
        {currentStep === 5 ? <BehaviorStep form={form} t={t} /> : null}
        {currentStep === 6 ? (
          <ReviewStep values={form.getValues()} t={t} state={state} />
        ) : null}
        {saveError ? (
          <div className="form-alert" role="alert">
            <strong>{t("saveFailed")}</strong>
            <span>{saveError.message}</span>
          </div>
        ) : null}
        <div className="wizard-actions">
          <button
            type="button"
            className="button secondary"
            disabled={currentStep === 1 || mutation.isPending}
            onClick={() => setCurrentStep((step) => Math.max(1, step - 1))}
          >
            <ArrowLeft />
            {t("back")}
          </button>
          <span>{t("autoSaveHint")}</span>
          <button
            type="submit"
            className="button primary"
            disabled={mutation.isPending}
          >
            {mutation.isPending
              ? t("saving")
              : currentStep === 6
                ? t("finish")
                : t("saveContinue")}
            {currentStep < 6 ? <ArrowRight /> : <PartyPopper />}
          </button>
        </div>
      </form>
    </div>
  );
}

type StepProps = {
  form: ReturnType<typeof useForm<OnboardingValues>>;
  t: ReturnType<typeof useTranslations>;
};
function CompanyStep({ form, t }: StepProps) {
  return (
    <div className="form-grid two">
      <Field t={t} form={form} name="name" label="legalName" required />
      <Field t={t} form={form} name="public_name" label="publicName" required />
      <label className="field">
        <span>{t("fields.industry")}</span>
        <select {...form.register("industry")}>
          <option value="generic">{t("industries.generic")}</option>
          <option value="beauty">{t("industries.beauty")}</option>
          <option value="clinic">{t("industries.clinic")}</option>
          <option value="education">{t("industries.education")}</option>
          <option value="auto_service">{t("industries.auto")}</option>
          <option value="other">{t("industries.other")}</option>
        </select>
      </label>
      <Field t={t} form={form} name="website" label="website" type="url" />
      <Field t={t} form={form} name="phone" label="phone" type="tel" />
      <Field t={t} form={form} name="email" label="email" type="email" />
      <Field t={t} form={form} name="timezone" label="timezone" required />
      <label className="field">
        <span>{t("fields.language")}</span>
        <select {...form.register("language")}>
          <option value="ru">Русский</option>
          <option value="uz">O‘zbekcha</option>
          <option value="en">English</option>
        </select>
      </label>
    </div>
  );
}
function BusinessStep({ form, t }: StepProps) {
  return (
    <div className="form-grid two">
      <TextField
        t={t}
        form={form}
        name="business_summary"
        label="businessSummary"
        required
        rows={3}
        span
      />
      <TextField
        t={t}
        form={form}
        name="business_description"
        label="businessDescription"
        required
        rows={6}
        span
      />
      <TextField
        t={t}
        form={form}
        name="target_customers"
        label="targetCustomers"
        rows={4}
      />
      <TextField
        t={t}
        form={form}
        name="products_services"
        label="productsServices"
        required
        rows={4}
      />
      <Field t={t} form={form} name="service_area" label="serviceArea" span />
    </div>
  );
}
function BranchStep({ form, t }: StepProps) {
  return (
    <div className="form-grid two">
      <Field t={t} form={form} name="branch_name" label="branchName" required />
      <Field
        t={t}
        form={form}
        name="branch_timezone"
        label="branchTimezone"
        required
      />
      <Field
        t={t}
        form={form}
        name="branch_address"
        label="branchAddress"
        span
      />
      <Field
        t={t}
        form={form}
        name="branch_phone"
        label="branchPhone"
        type="tel"
      />
      <div className="hours-pair">
        <Field
          t={t}
          form={form}
          name="branch_open"
          label="opens"
          type="time"
          required
        />
        <Field
          t={t}
          form={form}
          name="branch_close"
          label="closes"
          type="time"
          required
        />
      </div>
      <div className="inline-note field-span">
        <GitBranch />
        <p>{t("branchWeekdayHint")}</p>
      </div>
    </div>
  );
}
function TeamStep({ form, t, owner }: StepProps & { owner: string }) {
  return (
    <div>
      <div className="owner-card">
        <span className="avatar">{owner.slice(0, 1).toUpperCase()}</span>
        <div>
          <strong>{owner}</strong>
          <span>{t("ownerRole")}</span>
        </div>
        <Check />
      </div>
      <div className="section-heading">
        <h3>{t("optionalInvite")}</h3>
        <p>{t("optionalInviteHint")}</p>
      </div>
      <div className="form-grid two">
        <Field
          t={t}
          form={form}
          name="invite_email"
          label="inviteEmail"
          type="email"
        />
        <label className="field">
          <span>{t("fields.inviteRole")}</span>
          <select {...form.register("invite_role")}>
            <option value="admin">{t("roles.admin")}</option>
            <option value="manager">{t("roles.manager")}</option>
            <option value="agent">{t("roles.agent")}</option>
            <option value="viewer">{t("roles.viewer")}</option>
          </select>
        </label>
      </div>
      <div className="role-cards">
        <article>
          <strong>{t("roles.admin")}</strong>
          <p>{t("roleDescriptions.admin")}</p>
        </article>
        <article>
          <strong>{t("roles.manager")}</strong>
          <p>{t("roleDescriptions.manager")}</p>
        </article>
        <article>
          <strong>{t("roles.agent")}</strong>
          <p>{t("roleDescriptions.agent")}</p>
        </article>
      </div>
    </div>
  );
}
function BehaviorStep({ form, t }: StepProps) {
  return (
    <div className="form-grid two">
      <Field
        t={t}
        form={form}
        name="assistant_name"
        label="assistantName"
        required
      />
      <Field t={t} form={form} name="tone_of_voice" label="tone" />
      <TextField
        t={t}
        form={form}
        name="introduction"
        label="introduction"
        required
        rows={4}
        span
      />
      <TextField
        t={t}
        form={form}
        name="escalation_instructions"
        label="handoff"
        rows={5}
      />
      <TextField
        t={t}
        form={form}
        name="fallback_response"
        label="fallback"
        required
        rows={5}
      />
      <TextField
        t={t}
        form={form}
        name="prohibited_topics"
        label="prohibitedTopics"
        rows={4}
      />
      <TextField
        t={t}
        form={form}
        name="prohibited_actions"
        label="prohibitedActions"
        rows={4}
      />
      <TextField
        t={t}
        form={form}
        name="additional_instructions"
        label="additionalInstructions"
        rows={4}
        span
      />
    </div>
  );
}
function ReviewStep({
  values,
  t,
  state,
}: {
  values: OnboardingValues;
  t: ReturnType<typeof useTranslations>;
  state: OnboardingState;
}) {
  const summaries = [
    ["company", values.public_name || values.name],
    ["business", values.business_summary],
    ["branch", values.branch_name],
    ["team", values.invite_email || t("ownerOnly")],
    ["assistant", values.assistant_name],
  ] as const;
  return (
    <div className="review-grid">
      {summaries.map(([key, value]) => (
        <article key={key}>
          <span className="review-check">
            <Check />
          </span>
          <div>
            <small>{t(`review.${key}`)}</small>
            <strong>{value || t("missing")}</strong>
          </div>
        </article>
      ))}
      <div className="review-callout">
        <Bot />
        <div>
          <strong>{t("reviewReadyTitle")}</strong>
          <p>{t("reviewReadyDescription")}</p>
        </div>
      </div>
      {state.profile.onboarding_completed_at ? (
        <div className="success-inline">{t("alreadyComplete")}</div>
      ) : null}
    </div>
  );
}
function Field({
  form,
  t,
  name,
  label,
  type = "text",
  required = false,
  span = false,
}: StepProps & {
  name: keyof OnboardingValues;
  label: string;
  type?: string;
  required?: boolean;
  span?: boolean;
}) {
  return (
    <label className={`field ${span ? "field-span" : ""}`}>
      <span>
        {t(`fields.${label}`)}
        {required ? " *" : ""}
      </span>
      <input
        type={type}
        required={required}
        {...form.register(name, { required })}
      />
    </label>
  );
}
function TextField({
  form,
  t,
  name,
  label,
  rows,
  required = false,
  span = false,
}: StepProps & {
  name: keyof OnboardingValues;
  label: string;
  rows: number;
  required?: boolean;
  span?: boolean;
}) {
  return (
    <label className={`field ${span ? "field-span" : ""}`}>
      <span>
        {t(`fields.${label}`)}
        {required ? " *" : ""}
      </span>
      <textarea
        rows={rows}
        required={required}
        {...form.register(name, { required })}
      />
    </label>
  );
}
