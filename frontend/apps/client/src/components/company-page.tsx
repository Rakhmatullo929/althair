"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { useTranslations } from "next-intl";
import type { Organization, OrganizationProfile } from "@workspace/api-client";
import { useWorkspace } from "./workspace-provider";
import {
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
  SubmitButton,
} from "./ui";
import { can } from "@/lib/permissions";

type CompanyValues = {
  name: string;
  public_business_name: string;
  industry: string;
  website: string;
  phone: string;
  email: string;
  timezone: string;
  default_language: "ru" | "uz" | "en";
};

export function CompanyPage() {
  const t = useTranslations("company");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const id = workspace.selectedOrganizationId!;
  const orgQuery = useQuery({
    queryKey: ["organization", id],
    queryFn: () => workspace.api.organization(id),
  });
  const profileQuery = useQuery({
    queryKey: ["profile", id],
    queryFn: () => workspace.api.profile(id),
  });
  const editable =
    can(workspace.membership?.role, "manage_company") &&
    workspace.membership?.organization_status !== "suspended";
  const form = useForm<CompanyValues>({
    defaultValues: {
      name: "",
      public_business_name: "",
      industry: "generic",
      website: "",
      phone: "",
      email: "",
      timezone: "Asia/Tashkent",
      default_language: "ru",
    },
  });
  useEffect(() => {
    if (orgQuery.data && profileQuery.data)
      form.reset({
        name: orgQuery.data.name,
        public_business_name: profileQuery.data.public_business_name,
        industry: orgQuery.data.industry,
        website: String(
          profileQuery.data.public_contact_information.website ?? "",
        ),
        phone: String(profileQuery.data.public_contact_information.phone ?? ""),
        email: String(profileQuery.data.public_contact_information.email ?? ""),
        timezone: orgQuery.data.timezone,
        default_language: orgQuery.data.default_language,
      });
  }, [form, orgQuery.data, profileQuery.data]);
  const mutation = useMutation({
    mutationFn: async (values: CompanyValues) => {
      const organization = await workspace.api.updateOrganization(id, {
        name: values.name,
        industry: values.industry,
        timezone: values.timezone,
        default_language: values.default_language,
      } as Partial<Organization>);
      const profile = await workspace.api.updateProfile(id, {
        public_business_name: values.public_business_name,
        public_contact_information: {
          ...profileQuery.data?.public_contact_information,
          website: values.website,
          phone: values.phone,
          email: values.email,
        },
      } as Partial<OrganizationProfile>);
      return { organization, profile };
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["organization", id] }),
        queryClient.invalidateQueries({ queryKey: ["profile", id] }),
      ]);
    },
  });
  if (orgQuery.isLoading || profileQuery.isLoading) return <PageSkeleton />;
  if (orgQuery.error || profileQuery.error)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={String((orgQuery.error ?? profileQuery.error) as Error)}
        onRetry={() => {
          void orgQuery.refetch();
          void profileQuery.refetch();
        }}
      />
    );
  return (
    <>
      <PageHeading
        title={t("title")}
        description={t("description")}
        actions={
          <div className="status-stack">
            <StatusBadge status={orgQuery.data!.status} />
            <span>
              {t("onboarding", {
                progress: profileQuery.data!.onboarding_completion_percentage,
              })}
            </span>
          </div>
        }
      />
      {!editable ? (
        <div className="readonly-note" role="status">
          {t("readOnly")}
        </div>
      ) : null}
      <form
        className="panel settings-form"
        onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
      >
        {mutation.error ? (
          <div className="form-alert" role="alert">
            {(mutation.error as Error).message}
          </div>
        ) : null}
        {mutation.isSuccess ? (
          <div className="success-inline" role="status">
            {t("saved")}
          </div>
        ) : null}
        <section>
          <div className="section-heading">
            <span className="eyebrow">{t("identityEyebrow")}</span>
            <h2>{t("identityTitle")}</h2>
            <p>{t("identityDescription")}</p>
          </div>
          <div className="form-grid two">
            <label className="field">
              <span>{t("legalName")}</span>
              <input disabled={!editable} required {...form.register("name")} />
            </label>
            <label className="field">
              <span>{t("publicName")}</span>
              <input
                disabled={!editable}
                required
                {...form.register("public_business_name")}
              />
            </label>
            <label className="field">
              <span>{t("industry")}</span>
              <select disabled={!editable} {...form.register("industry")}>
                <option value="generic">{t("industries.generic")}</option>
                <option value="beauty">{t("industries.beauty")}</option>
                <option value="clinic">{t("industries.clinic")}</option>
                <option value="education">{t("industries.education")}</option>
                <option value="auto_service">{t("industries.auto")}</option>
                <option value="other">{t("industries.other")}</option>
              </select>
            </label>
            <label className="field">
              <span>{t("website")}</span>
              <input
                type="url"
                disabled={!editable}
                {...form.register("website")}
              />
            </label>
          </div>
        </section>
        <section>
          <div className="section-heading">
            <span className="eyebrow">{t("contactEyebrow")}</span>
            <h2>{t("contactTitle")}</h2>
          </div>
          <div className="form-grid two">
            <label className="field">
              <span>{t("phone")}</span>
              <input
                type="tel"
                disabled={!editable}
                {...form.register("phone")}
              />
            </label>
            <label className="field">
              <span>{t("email")}</span>
              <input
                type="email"
                disabled={!editable}
                {...form.register("email")}
              />
            </label>
            <label className="field">
              <span>{t("timezone")}</span>
              <input disabled={!editable} {...form.register("timezone")} />
            </label>
            <label className="field">
              <span>{t("language")}</span>
              <select
                disabled={!editable}
                {...form.register("default_language")}
              >
                <option value="ru">Русский</option>
                <option value="uz">O‘zbekcha</option>
                <option value="en">English</option>
              </select>
            </label>
          </div>
        </section>
        {editable ? (
          <div className="form-actions">
            <button
              type="button"
              className="button secondary"
              disabled={!form.formState.isDirty}
              onClick={() => form.reset()}
            >
              {t("cancel")}
            </button>
            <SubmitButton pending={mutation.isPending}>
              {t("save")}
            </SubmitButton>
          </div>
        ) : null}
        <div className="audit-line">
          <span>
            {t("created")}:{" "}
            {new Intl.DateTimeFormat().format(
              new Date(orgQuery.data!.created_at),
            )}
          </span>
          <span>
            {t("updated")}:{" "}
            {new Intl.DateTimeFormat().format(
              new Date(orgQuery.data!.updated_at),
            )}
          </span>
        </div>
      </form>
    </>
  );
}
