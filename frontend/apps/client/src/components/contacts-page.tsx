"use client";

import type { ContactIdentityType } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  AtSign,
  ContactRound,
  Merge,
  NotebookPen,
  Pencil,
  Phone,
  Plus,
  Search,
  ShieldAlert,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { canManageCrm, canOperateCrm, crmQueryKeys } from "@/lib/crm";
import { CrmDialog, formatDateTime } from "./crm-shared";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

export function ContactsPage() {
  const t = useTranslations("crm");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const membership = workspace.membership!;
  const readOnly = ["suspended", "archived"].includes(
    membership.organization_status,
  );
  const canEdit = canOperateCrm(membership.role) && !readOnly;
  const canMerge = canManageCrm(membership.role) && !readOnly;
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [identityType, setIdentityType] =
    useState<ContactIdentityType>("phone");
  const [identityValue, setIdentityValue] = useState("");
  const [editingIdentityId, setEditingIdentityId] = useState<string | null>(
    null,
  );
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState("");
  const contacts = useQuery({
    queryKey: crmQueryKeys.contacts(organizationId, search),
    queryFn: () => workspace.api.contacts({ search, status: "active" }),
  });
  const contactRows = contacts.data?.results ?? [];
  const effectiveSelectedId =
    contactRows.find((item) => item.id === selectedId)?.id ??
    contactRows[0]?.id ??
    null;
  const detail = useQuery({
    queryKey: crmQueryKeys.contact(
      organizationId,
      effectiveSelectedId ?? "none",
    ),
    queryFn: () => workspace.api.contact(effectiveSelectedId!),
    enabled: Boolean(effectiveSelectedId),
  });
  const notes = useQuery({
    queryKey: [
      ...crmQueryKeys.contact(organizationId, effectiveSelectedId ?? "none"),
      "notes",
    ],
    queryFn: () => workspace.api.contactNotes(effectiveSelectedId!),
    enabled: Boolean(effectiveSelectedId),
  });
  const invalidate = async () =>
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
      workspace.api.createContact({
        display_name: name,
        company_name: company,
        preferred_language: "ru",
        identities: identityValue
          ? [{ type: identityType, raw_value: identityValue, is_primary: true }]
          : [],
      }),
    onSuccess: async (contact) => {
      setCreateOpen(false);
      setSelectedId(contact.id);
      setName("");
      setCompany("");
      setIdentityValue("");
      await invalidate();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const selected = detail.data;
  const openEdit = () => {
    if (!selected) return;
    setName(selected.display_name);
    setCompany(selected.company_name);
    setEditOpen(true);
  };
  const addIdentity = () => {
    if (!selected || !identityValue.trim()) return;
    const value = identityValue;
    setIdentityValue("");
    const editingId = editingIdentityId;
    setEditingIdentityId(null);
    mutation.mutate(() =>
      editingId
        ? workspace.api.updateContactIdentity(selected.id, editingId, {
            type: identityType,
            raw_value: value,
          })
        : workspace.api.addContactIdentity(selected.id, {
            type: identityType,
            raw_value: value,
          }),
    );
  };
  const addNote = () => {
    if (!selected || !note.trim()) return;
    const body = note;
    setNote("");
    mutation.mutate(() => workspace.api.addContactNote(selected.id, body));
  };

  return (
    <>
      <PageHeading
        title={t("contacts.title")}
        description={t("contacts.description")}
        actions={
          canEdit ? (
            <button
              className="button primary"
              onClick={() => {
                setName("");
                setCompany("");
                setIdentityValue("");
                setCreateOpen(true);
              }}
            >
              <Plus />
              {t("contacts.new")}
            </button>
          ) : undefined
        }
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      <div className="contacts-layout">
        <section className="contacts-list-panel">
          <label className="inbox-search">
            <Search />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("contacts.search")}
              aria-label={t("contacts.search")}
            />
          </label>
          {contacts.isLoading ? (
            <PageSkeleton />
          ) : contacts.error ? (
            <ErrorState
              title={t("common.error")}
              description={(contacts.error as Error).message}
            />
          ) : contacts.data?.results.length ? (
            <div className="responsive-table-wrap">
              <table className="responsive-table contacts-table">
                <thead>
                  <tr>
                    <th>{t("contacts.contact")}</th>
                    <th>{t("contacts.identity")}</th>
                    <th>{t("contacts.language")}</th>
                    <th>{t("contacts.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {contacts.data.results.map((contact) => (
                    <tr
                      key={contact.id}
                      className={
                        effectiveSelectedId === contact.id ? "selected" : ""
                      }
                      onClick={() => setSelectedId(contact.id)}
                    >
                      <td data-label={t("contacts.contact")}>
                        <button
                          className="contact-row-button"
                          onClick={() => setSelectedId(contact.id)}
                        >
                          <span className="conversation-avatar">
                            {contact.display_name.slice(0, 1)}
                          </span>
                          <span>
                            <strong>{contact.display_name}</strong>
                            <small>
                              {contact.company_name || t("contacts.noCompany")}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td data-label={t("contacts.identity")}>
                        {contact.identities[0]?.raw_value ?? "—"}
                      </td>
                      <td data-label={t("contacts.language")}>
                        {contact.preferred_language.toUpperCase()}
                      </td>
                      <td data-label={t("contacts.status")}>
                        <StatusBadge status={contact.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<ContactRound />}
              title={t("contacts.emptyTitle")}
              description={t("contacts.emptyDescription")}
            />
          )}
        </section>
        <aside
          className="contact-detail-panel"
          aria-label={t("contacts.detailLabel")}
        >
          {detail.isLoading ? (
            <PageSkeleton />
          ) : selected ? (
            <>
              <header className="contact-detail-header">
                <span className="conversation-avatar large">
                  {selected.display_name.slice(0, 1)}
                </span>
                <div>
                  <h2>{selected.display_name}</h2>
                  <p>{selected.company_name || t("contacts.noCompany")}</p>
                </div>
              </header>
              {selected.duplicate_suggestions.length ? (
                <div className="duplicate-warning" role="status">
                  <ShieldAlert />
                  <div>
                    <strong>{t("contacts.duplicateTitle")}</strong>
                    <p>
                      {t("contacts.duplicateDescription", {
                        count: selected.duplicate_suggestions.length,
                      })}
                    </p>
                  </div>
                </div>
              ) : null}
              <div className="contact-toolbar">
                {canEdit ? (
                  <button className="button secondary" onClick={openEdit}>
                    <Pencil />
                    {t("common.edit")}
                  </button>
                ) : null}
                {canMerge ? (
                  <button
                    className="button secondary"
                    onClick={() => setMergeOpen(true)}
                  >
                    <Merge />
                    {t("contacts.merge")}
                  </button>
                ) : null}
                {canEdit ? (
                  <button
                    className="button danger-ghost"
                    onClick={() =>
                      mutation.mutate(() =>
                        workspace.api.updateContact(selected.id, {
                          status: "archived",
                        }),
                      )
                    }
                  >
                    <Archive />
                    {t("contacts.archive")}
                  </button>
                ) : null}
              </div>
              <section className="detail-section">
                <h3>{t("contacts.identities")}</h3>
                {selected.identities.length ? (
                  <ul className="identity-cards">
                    {selected.identities.map((identity) => (
                      <li key={identity.id}>
                        {identity.type === "phone" ? <Phone /> : <AtSign />}
                        <div>
                          <span>{t(`identity.${identity.type}`)}</span>
                          <strong>{identity.raw_value}</strong>
                          <small>{identity.normalized_value}</small>
                        </div>
                        {identity.is_primary ? (
                          <StatusBadge status="primary" />
                        ) : null}
                        {canEdit ? (
                          <button
                            className="icon-button"
                            aria-label={t("contacts.editIdentity", {
                              value: identity.raw_value,
                            })}
                            onClick={() => {
                              setEditingIdentityId(identity.id);
                              setIdentityType(identity.type);
                              setIdentityValue(identity.raw_value);
                            }}
                          >
                            <Pencil />
                          </button>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted-copy">{t("contacts.noIdentities")}</p>
                )}
                {canEdit ? (
                  <div className="inline-form">
                    <select
                      value={identityType}
                      onChange={(event) =>
                        setIdentityType(
                          event.target.value as ContactIdentityType,
                        )
                      }
                      aria-label={t("contacts.identityType")}
                    >
                      <option value="phone">{t("identity.phone")}</option>
                      <option value="email">{t("identity.email")}</option>
                      <option value="web_chat">{t("identity.web_chat")}</option>
                      <option value="external">{t("identity.external")}</option>
                    </select>
                    <input
                      value={identityValue}
                      onChange={(event) => setIdentityValue(event.target.value)}
                      placeholder={t("contacts.identityValue")}
                      aria-label={t("contacts.identityValue")}
                    />
                    <button
                      className="button secondary"
                      onClick={addIdentity}
                      disabled={!identityValue.trim()}
                    >
                      {editingIdentityId ? <Pencil /> : <Plus />}
                      {editingIdentityId ? t("common.save") : t("common.add")}
                    </button>
                  </div>
                ) : null}
              </section>
              <section className="detail-section">
                <h3>{t("contacts.notes")}</h3>
                {notes.data?.results.length ? (
                  <ul className="notes-list">
                    {notes.data.results.map((item) => (
                      <li key={item.id}>
                        <NotebookPen />
                        <div>
                          <p>{item.body}</p>
                          <small>
                            {item.author_name} ·{" "}
                            {formatDateTime(item.created_at, undefined)}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted-copy">{t("contacts.noNotes")}</p>
                )}
                {canEdit ? (
                  <div className="note-form">
                    <textarea
                      value={note}
                      onChange={(event) => setNote(event.target.value)}
                      placeholder={t("contacts.notePlaceholder")}
                      rows={3}
                    />
                    <button
                      className="button secondary"
                      onClick={addNote}
                      disabled={!note.trim()}
                    >
                      <NotebookPen />
                      {t("contacts.addNote")}
                    </button>
                  </div>
                ) : null}
              </section>
              <section className="detail-section">
                <h3>{t("contacts.tags")}</h3>
                {selected.tags.length ? (
                  <div className="tag-cloud">
                    {selected.tags.map((tag) => (
                      <span key={tag.id}>{tag.name}</span>
                    ))}
                  </div>
                ) : (
                  <p className="muted-copy">{t("contacts.noTags")}</p>
                )}
              </section>
            </>
          ) : (
            <EmptyState
              icon={<ContactRound />}
              title={t("contacts.selectTitle")}
              description={t("contacts.selectDescription")}
            />
          )}
        </aside>
      </div>
      {createOpen ? (
        <CrmDialog
          title={t("contacts.createTitle")}
          closeLabel={t("common.close")}
          onClose={() => setCreateOpen(false)}
        >
          <ContactForm
            t={t}
            name={name}
            company={company}
            setName={setName}
            setCompany={setCompany}
            identityType={identityType}
            setIdentityType={setIdentityType}
            identityValue={identityValue}
            setIdentityValue={setIdentityValue}
            onCancel={() => setCreateOpen(false)}
            onSubmit={() => create.mutate()}
            pending={create.isPending}
          />
        </CrmDialog>
      ) : null}
      {editOpen && selected ? (
        <CrmDialog
          title={t("contacts.editTitle")}
          closeLabel={t("common.close")}
          onClose={() => setEditOpen(false)}
        >
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate(() =>
                workspace.api.updateContact(selected.id, {
                  display_name: name,
                  company_name: company,
                }),
              );
              setEditOpen(false);
            }}
          >
            <label className="field">
              {t("contacts.displayName")}
              <input
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </label>
            <label className="field">
              {t("contacts.companyName")}
              <input
                value={company}
                onChange={(event) => setCompany(event.target.value)}
              />
            </label>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setEditOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button className="button primary">{t("common.save")}</button>
            </div>
          </form>
        </CrmDialog>
      ) : null}
      {mergeOpen && selected ? (
        <CrmDialog
          title={t("contacts.mergeTitle")}
          description={t("contacts.mergeDescription", {
            name: selected.display_name,
          })}
          closeLabel={t("common.close")}
          onClose={() => setMergeOpen(false)}
        >
          <div className="merge-options">
            {contacts.data?.results
              .filter((item) => item.id !== selected.id)
              .map((candidate) => (
                <button
                  key={candidate.id}
                  onClick={() => {
                    mutation.mutate(() =>
                      workspace.api.mergeContact(selected.id, candidate.id),
                    );
                    setSelectedId(candidate.id);
                    setMergeOpen(false);
                  }}
                >
                  <span className="conversation-avatar">
                    {candidate.display_name.slice(0, 1)}
                  </span>
                  <span>
                    <strong>{candidate.display_name}</strong>
                    <small>{t("contacts.survivorHint")}</small>
                  </span>
                </button>
              ))}
          </div>
        </CrmDialog>
      ) : null}
    </>
  );
}

function ContactForm({
  t,
  name,
  company,
  setName,
  setCompany,
  identityType,
  setIdentityType,
  identityValue,
  setIdentityValue,
  onCancel,
  onSubmit,
  pending,
}: {
  t: ReturnType<typeof useTranslations>;
  name: string;
  company: string;
  setName: (value: string) => void;
  setCompany: (value: string) => void;
  identityType: ContactIdentityType;
  setIdentityType: (value: ContactIdentityType) => void;
  identityValue: string;
  setIdentityValue: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  return (
    <form
      className="form-grid"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <label className="field">
        {t("contacts.displayName")}
        <input
          autoFocus
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={200}
        />
      </label>
      <label className="field">
        {t("contacts.companyName")}
        <input
          value={company}
          onChange={(event) => setCompany(event.target.value)}
          maxLength={200}
        />
      </label>
      <div className="form-grid two">
        <label className="field">
          {t("contacts.identityType")}
          <select
            value={identityType}
            onChange={(event) =>
              setIdentityType(event.target.value as ContactIdentityType)
            }
          >
            <option value="phone">{t("identity.phone")}</option>
            <option value="email">{t("identity.email")}</option>
            <option value="web_chat">{t("identity.web_chat")}</option>
          </select>
        </label>
        <label className="field">
          {t("contacts.identityValue")}
          <input
            value={identityValue}
            onChange={(event) => setIdentityValue(event.target.value)}
          />
        </label>
      </div>
      <div className="form-actions">
        <button type="button" className="button secondary" onClick={onCancel}>
          {t("common.cancel")}
        </button>
        <button className="button primary" disabled={pending}>
          <Plus />
          {t("contacts.create")}
        </button>
      </div>
    </form>
  );
}
