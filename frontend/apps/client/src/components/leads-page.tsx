"use client";

import type { Lead } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleDollarSign,
  KanbanSquare,
  List,
  Plus,
  Search,
  Trophy,
  XCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { canManageCrm, crmQueryKeys } from "@/lib/crm";
import { CrmDialog, formatDateTime } from "./crm-shared";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

export function LeadsPage() {
  const t = useTranslations("crm");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const membership = workspace.membership!;
  const editable =
    canManageCrm(membership.role) &&
    !["suspended", "archived"].includes(membership.organization_status);
  const [pipelineId, setPipelineId] = useState("");
  const [listMode, setListMode] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [lostLead, setLostLead] = useState<Lead | null>(null);
  const [lostReason, setLostReason] = useState("");
  const [contactId, setContactId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [value, setValue] = useState("");
  const [currency, setCurrency] = useState("UZS");
  const [notice, setNotice] = useState("");
  const pipelines = useQuery({
    queryKey: crmQueryKeys.pipelines(organizationId),
    queryFn: () => workspace.api.pipelines(),
  });
  const effectivePipelineId =
    pipelineId ||
    pipelines.data?.find((item) => item.is_default)?.id ||
    pipelines.data?.[0]?.id ||
    "";
  const leads = useQuery({
    queryKey: crmQueryKeys.leads(organizationId, effectivePipelineId),
    queryFn: () =>
      workspace.api.leads({ pipeline: effectivePipelineId || undefined }),
    enabled: Boolean(effectivePipelineId),
  });
  const contacts = useQuery({
    queryKey: crmQueryKeys.contacts(organizationId),
    queryFn: () => workspace.api.contacts({ status: "active", page_size: 100 }),
  });
  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: crmQueryKeys.root(organizationId),
    });
  const mutation = useMutation({
    mutationFn: (action: () => Promise<unknown>) => action(),
    onSuccess: async () => {
      setNotice(t("common.saved"));
      await invalidate();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const create = useMutation({
    mutationFn: () =>
      workspace.api.createLead({
        contact: contactId,
        title,
        description,
        pipeline: effectivePipelineId,
        estimated_value: value || null,
        currency: value ? currency : "",
      }),
    onSuccess: async () => {
      setCreateOpen(false);
      setTitle("");
      setDescription("");
      setValue("");
      await invalidate();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const pipeline = pipelines.data?.find(
    (item) => item.id === effectivePipelineId,
  );
  const rows = leads.data?.results ?? [];

  return (
    <>
      <PageHeading
        title={t("leads.title")}
        description={t("leads.description")}
        actions={
          <div className="page-action-row">
            <button
              className="button secondary"
              onClick={() => setListMode(!listMode)}
            >
              {listMode ? <KanbanSquare /> : <List />}
              {listMode ? t("leads.kanbanView") : t("leads.listView")}
            </button>
            {editable ? (
              <button
                className="button primary"
                onClick={() => {
                  setContactId(contacts.data?.results[0]?.id ?? "");
                  setCreateOpen(true);
                }}
              >
                <Plus />
                {t("leads.new")}
              </button>
            ) : null}
          </div>
        }
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      <div className="pipeline-toolbar">
        <label>
          {t("leads.pipeline")}
          <select
            value={effectivePipelineId}
            onChange={(event) => setPipelineId(event.target.value)}
          >
            {pipelines.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <span>{t("leads.realDataNote")}</span>
      </div>
      {leads.isLoading || pipelines.isLoading ? (
        <PageSkeleton />
      ) : leads.error ? (
        <ErrorState
          title={t("common.error")}
          description={(leads.error as Error).message}
        />
      ) : !pipeline ? (
        <EmptyState
          icon={<KanbanSquare />}
          title={t("leads.noPipeline")}
          description={t("leads.noPipelineHint")}
        />
      ) : listMode ? (
        <section className="panel">
          <div className="responsive-table-wrap">
            <table className="responsive-table">
              <thead>
                <tr>
                  <th>{t("leads.lead")}</th>
                  <th>{t("leads.contact")}</th>
                  <th>{t("leads.stage")}</th>
                  <th>{t("leads.assignee")}</th>
                  <th>{t("leads.followUp")}</th>
                  <th>{t("leads.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((lead) => (
                  <tr key={lead.id}>
                    <td data-label={t("leads.lead")}>
                      <button
                        className="contact-row-button"
                        onClick={() => setSelectedLead(lead)}
                      >
                        <strong>{lead.title}</strong>
                      </button>
                    </td>
                    <td data-label={t("leads.contact")}>{lead.contact_name}</td>
                    <td data-label={t("leads.stage")}>
                      <StatusBadge status={lead.stage_name} />
                    </td>
                    <td data-label={t("leads.assignee")}>
                      {lead.assigned_name ?? t("status.unassigned")}
                    </td>
                    <td data-label={t("leads.followUp")}>
                      {lead.next_follow_up_at
                        ? formatDateTime(lead.next_follow_up_at)
                        : "—"}
                    </td>
                    <td data-label={t("leads.actions")}>
                      <select
                        aria-label={t("leads.moveLead", { title: lead.title })}
                        value={lead.stage}
                        disabled={!editable}
                        onChange={(event) => {
                          const stageId = event.target.value;
                          mutation.mutate(() =>
                            workspace.api.moveLead(lead.id, stageId),
                          );
                        }}
                      >
                        {pipeline.stages
                          .filter((stage) => stage.is_active)
                          .map((stage) => (
                            <option key={stage.id} value={stage.id}>
                              {stage.name}
                            </option>
                          ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <section className="kanban-board" aria-label={t("leads.kanbanLabel")}>
          {pipeline.stages
            .filter((stage) => stage.is_active)
            .map((stage) => {
              const stageLeads = rows.filter((lead) => lead.stage === stage.id);
              return (
                <section
                  className="kanban-column"
                  key={stage.id}
                  aria-labelledby={`stage-${stage.id}`}
                >
                  <header>
                    <span
                      className={`stage-token token-${stage.color_token}`}
                    />
                    <h2 id={`stage-${stage.id}`}>{stage.name}</h2>
                    <span>{stageLeads.length}</span>
                  </header>
                  <div className="kanban-cards">
                    {stageLeads.map((lead) => (
                      <article className="lead-card" key={lead.id}>
                        <button
                          className="lead-card-main"
                          onClick={() => setSelectedLead(lead)}
                        >
                          <strong>{lead.title}</strong>
                          <span>{lead.contact_name}</span>
                          {lead.description ? <p>{lead.description}</p> : null}
                          {lead.estimated_value ? (
                            <small>
                              <CircleDollarSign />
                              {new Intl.NumberFormat(undefined).format(
                                Number(lead.estimated_value),
                              )}{" "}
                              {lead.currency}
                            </small>
                          ) : null}
                        </button>
                        <label>
                          <span className="sr-only">
                            {t("leads.moveLead", { title: lead.title })}
                          </span>
                          <select
                            value={lead.stage}
                            disabled={!editable}
                            onChange={(event) => {
                              const stageId = event.target.value;
                              mutation.mutate(() =>
                                workspace.api.moveLead(lead.id, stageId),
                              );
                            }}
                          >
                            {pipeline.stages
                              .filter((item) => item.is_active)
                              .map((item) => (
                                <option value={item.id} key={item.id}>
                                  {item.name}
                                </option>
                              ))}
                          </select>
                        </label>
                      </article>
                    ))}
                    {!stageLeads.length ? (
                      <div className="kanban-empty">
                        {t("leads.emptyStage")}
                      </div>
                    ) : null}
                  </div>
                </section>
              );
            })}
        </section>
      )}
      {rows.length === 0 && !leads.isLoading ? (
        <EmptyState
          icon={<Search />}
          title={t("leads.emptyTitle")}
          description={t("leads.emptyDescription")}
        />
      ) : null}
      {createOpen ? (
        <CrmDialog
          title={t("leads.createTitle")}
          description={t("leads.createDescription")}
          closeLabel={t("common.close")}
          onClose={() => setCreateOpen(false)}
        >
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate();
            }}
          >
            <label className="field">
              {t("leads.contact")}
              <select
                autoFocus
                value={contactId}
                onChange={(event) => setContactId(event.target.value)}
                required
              >
                {contacts.data?.results.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              {t("leads.leadTitle")}
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
                maxLength={200}
              />
            </label>
            <label className="field">
              {t("leads.descriptionLabel")}
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={4}
                maxLength={5000}
              />
            </label>
            <div className="form-grid two">
              <label className="field">
                {t("leads.optionalValue")}
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                />
              </label>
              <label className="field">
                {t("leads.currency")}
                <select
                  value={currency}
                  onChange={(event) => setCurrency(event.target.value)}
                  disabled={!value}
                >
                  <option>UZS</option>
                  <option>USD</option>
                  <option>EUR</option>
                </select>
              </label>
            </div>
            <p className="muted-copy">{t("leads.valueHint")}</p>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setCreateOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button
                className="button primary"
                disabled={create.isPending || !contactId}
              >
                <Plus />
                {t("leads.create")}
              </button>
            </div>
          </form>
        </CrmDialog>
      ) : null}
      {selectedLead ? (
        <CrmDialog
          title={selectedLead.title}
          description={selectedLead.contact_name}
          closeLabel={t("common.close")}
          onClose={() => setSelectedLead(null)}
        >
          <div className="lead-detail">
            <dl>
              <div>
                <dt>{t("leads.stage")}</dt>
                <dd>
                  <StatusBadge status={selectedLead.stage_name} />
                </dd>
              </div>
              <div>
                <dt>{t("leads.assignee")}</dt>
                <dd>{selectedLead.assigned_name ?? t("status.unassigned")}</dd>
              </div>
              <div>
                <dt>{t("leads.source")}</dt>
                <dd>{selectedLead.source_channel_type || t("leads.manual")}</dd>
              </div>
              <div>
                <dt>{t("leads.value")}</dt>
                <dd>
                  {selectedLead.estimated_value
                    ? `${selectedLead.estimated_value} ${selectedLead.currency}`
                    : t("leads.notProvided")}
                </dd>
              </div>
            </dl>
            <p>{selectedLead.description || t("leads.noDescription")}</p>
            {editable ? (
              <div className="form-actions">
                <button
                  className="button secondary"
                  onClick={() =>
                    mutation.mutate(() =>
                      workspace.api.winLead(selectedLead.id),
                    )
                  }
                >
                  <Trophy />
                  {t("leads.markWon")}
                </button>
                <button
                  className="button danger-ghost"
                  onClick={() => {
                    setLostLead(selectedLead);
                    setSelectedLead(null);
                  }}
                >
                  <XCircle />
                  {t("leads.markLost")}
                </button>
              </div>
            ) : null}
          </div>
        </CrmDialog>
      ) : null}
      {lostLead ? (
        <CrmDialog
          title={t("leads.lostTitle")}
          description={lostLead.title}
          closeLabel={t("common.close")}
          onClose={() => setLostLead(null)}
        >
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate(() =>
                workspace.api.loseLead(lostLead.id, lostReason),
              );
              setLostLead(null);
              setLostReason("");
            }}
          >
            <label className="field">
              {t("leads.lostReason")}
              <textarea
                autoFocus
                value={lostReason}
                onChange={(event) => setLostReason(event.target.value)}
                required
                maxLength={500}
                rows={4}
              />
            </label>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setLostLead(null)}
              >
                {t("common.cancel")}
              </button>
              <button
                className="button danger-ghost"
                disabled={!lostReason.trim()}
              >
                {t("leads.confirmLost")}
              </button>
            </div>
          </form>
        </CrmDialog>
      ) : null}
    </>
  );
}
