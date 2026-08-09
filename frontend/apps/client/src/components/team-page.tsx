"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  Invitation,
  Membership,
  OrganizationRole,
} from "@workspace/api-client";
import { Copy, MailPlus, ShieldCheck, UserMinus, Users, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
  SubmitButton,
} from "./ui";

export function TeamPage() {
  const t = useTranslations("team");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const id = workspace.selectedOrganizationId!;
  const editable =
    can(workspace.membership?.role, "manage_team") &&
    workspace.membership?.organization_status !== "suspended";
  const [inviteOpen, setInviteOpen] = useState(false);
  const [lastInvitation, setLastInvitation] = useState<Invitation | null>(null);
  const form = useForm<{
    email: string;
    role: Exclude<OrganizationRole, "owner">;
  }>({ defaultValues: { email: "", role: "agent" } });
  const members = useQuery({
    queryKey: ["memberships", id],
    queryFn: () => workspace.api.memberships(id),
  });
  const invitations = useQuery({
    queryKey: ["invitations", id],
    queryFn: () => workspace.api.invitations(id),
    enabled: editable,
  });
  const update = useMutation({
    mutationFn: ({
      membershipId,
      body,
    }: {
      membershipId: string;
      body: Partial<Pick<Membership, "role" | "status">>;
    }) => workspace.api.updateMembership(id, membershipId, body),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: ["memberships", id] }),
  });
  const invite = useMutation({
    mutationFn: (values: {
      email: string;
      role: Exclude<OrganizationRole, "owner">;
    }) => workspace.api.createInvitation(id, values),
    onSuccess: async (result) => {
      setLastInvitation(result);
      form.reset();
      await queryClient.invalidateQueries({ queryKey: ["invitations", id] });
    },
  });
  if (members.isLoading) return <PageSkeleton />;
  if (members.error)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={(members.error as Error).message}
        onRetry={() => void members.refetch()}
      />
    );
  return (
    <>
      <PageHeading
        title={t("title")}
        description={t("description")}
        actions={
          editable ? (
            <button
              className="button primary"
              onClick={() => setInviteOpen(true)}
            >
              <MailPlus />
              {t("invite")}
            </button>
          ) : undefined
        }
      />
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      {inviteOpen ? (
        <section className="panel inline-editor">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("inviteEyebrow")}</span>
              <h2>{t("inviteTitle")}</h2>
              <p>{t("inviteDescription")}</p>
            </div>
            <button
              className="icon-button"
              onClick={() => {
                setInviteOpen(false);
                setLastInvitation(null);
              }}
            >
              <X />
            </button>
          </div>
          {lastInvitation?.invitation_url ? (
            <div className="development-link" role="status">
              <strong>{t("developmentLink")}</strong>
              <code>{lastInvitation.invitation_url}</code>
              <button
                className="button secondary"
                onClick={() =>
                  void navigator.clipboard.writeText(
                    lastInvitation.invitation_url!,
                  )
                }
              >
                <Copy />
                {t("copy")}
              </button>
              <p>{t("developmentOnly")}</p>
            </div>
          ) : (
            <form
              onSubmit={form.handleSubmit((values) => invite.mutate(values))}
            >
              <div className="form-grid two">
                <label className="field">
                  <span>{t("email")}</span>
                  <input type="email" required {...form.register("email")} />
                </label>
                <label className="field">
                  <span>{t("role")}</span>
                  <select {...form.register("role")}>
                    <option value="admin">{t("roles.admin")}</option>
                    <option value="manager">{t("roles.manager")}</option>
                    <option value="agent">{t("roles.agent")}</option>
                    <option value="viewer">{t("roles.viewer")}</option>
                  </select>
                </label>
              </div>
              {invite.error ? (
                <div className="form-alert">
                  {(invite.error as Error).message}
                </div>
              ) : null}
              <div className="role-explainer">
                <ShieldCheck />
                <p>{t("roleHint")}</p>
              </div>
              <div className="form-actions">
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => setInviteOpen(false)}
                >
                  {t("cancel")}
                </button>
                <SubmitButton pending={invite.isPending}>
                  {t("sendInvite")}
                </SubmitButton>
              </div>
            </form>
          )}
        </section>
      ) : null}
      <section className="panel table-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("membersEyebrow")}</span>
            <h2>{t("membersTitle")}</h2>
          </div>
          <span className="count-pill">{members.data!.count}</span>
        </div>
        <div className="responsive-table">
          <table>
            <thead>
              <tr>
                <th>{t("person")}</th>
                <th>{t("role")}</th>
                <th>{t("status")}</th>
                <th>{t("joined")}</th>
                {editable ? (
                  <th>
                    <span className="sr-only">{t("actions")}</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {members.data!.results.map((member) => (
                <tr key={member.id}>
                  <td data-label={t("person")}>
                    <div className="person-cell">
                      <span className="avatar">
                        {member.user_name.slice(0, 1).toUpperCase()}
                      </span>
                      <div>
                        <strong>{member.user_name}</strong>
                        <small>{member.user_email}</small>
                      </div>
                    </div>
                  </td>
                  <td data-label={t("role")}>
                    {editable && member.user !== workspace.user?.id ? (
                      <select
                        aria-label={t("changeRole", { name: member.user_name })}
                        value={member.role}
                        onChange={(event) =>
                          update.mutate({
                            membershipId: member.id,
                            body: {
                              role: event.target.value as OrganizationRole,
                            },
                          })
                        }
                      >
                        <option value="owner">{t("roles.owner")}</option>
                        <option value="admin">{t("roles.admin")}</option>
                        <option value="manager">{t("roles.manager")}</option>
                        <option value="agent">{t("roles.agent")}</option>
                        <option value="viewer">{t("roles.viewer")}</option>
                      </select>
                    ) : (
                      t(`roles.${member.role}`)
                    )}
                  </td>
                  <td data-label={t("status")}>
                    <StatusBadge status={member.status} />
                  </td>
                  <td data-label={t("joined")}>
                    {member.joined_at
                      ? new Intl.DateTimeFormat().format(
                          new Date(member.joined_at),
                        )
                      : "—"}
                  </td>
                  {editable ? (
                    <td>
                      {member.user !== workspace.user?.id &&
                      member.status === "active" ? (
                        <button
                          className="icon-button danger"
                          aria-label={t("deactivate", {
                            name: member.user_name,
                          })}
                          onClick={() => {
                            if (
                              window.confirm(
                                t("deactivateConfirm", {
                                  name: member.user_name,
                                }),
                              )
                            )
                              update.mutate({
                                membershipId: member.id,
                                body: { status: "suspended" },
                              });
                          }}
                        >
                          <UserMinus />
                        </button>
                      ) : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {update.error ? (
          <div className="form-alert">{(update.error as Error).message}</div>
        ) : null}
      </section>
      {editable ? (
        <section className="panel table-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("pendingEyebrow")}</span>
              <h2>{t("pendingTitle")}</h2>
            </div>
          </div>
          {invitations.data?.results.length ? (
            <div className="responsive-table">
              <table>
                <thead>
                  <tr>
                    <th>{t("email")}</th>
                    <th>{t("role")}</th>
                    <th>{t("status")}</th>
                    <th>{t("expires")}</th>
                  </tr>
                </thead>
                <tbody>
                  {invitations.data.results.map((item) => (
                    <tr key={item.id}>
                      <td>{item.email}</td>
                      <td>{t(`roles.${item.role}`)}</td>
                      <td>
                        <StatusBadge status={item.status} />
                      </td>
                      <td>
                        {new Intl.DateTimeFormat().format(
                          new Date(item.expires_at),
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<Users />}
              title={t("noInvitesTitle")}
              description={t("noInvitesDescription")}
            />
          )}
        </section>
      ) : null}
    </>
  );
}
