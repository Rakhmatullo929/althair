"use client";

import {
  ApiError,
  createApiClient,
  type CurrentUser,
  type MembershipSummary,
} from "@workspace/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLocale } from "next-intl";
import { useRouter } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { validateOrganizationSelection } from "@/lib/organization-selection";

const selectedOrganizationKey = "aifo:selected-organization";

type WorkspaceValue = {
  api: ReturnType<typeof createApiClient>;
  user: CurrentUser | null;
  membership: MembershipSummary | null;
  selectedOrganizationId: string | null;
  loading: boolean;
  switching: boolean;
  error: ApiError | null;
  selectOrganization: (id: string) => void;
  refreshUser: () => Promise<void>;
  logout: () => Promise<void>;
};

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false },
          mutations: { retry: 0 },
        },
      }),
  );
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState<
    string | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const router = useRouter();
  const locale = useLocale();
  const [api] = useState(() => createApiClient());

  async function refreshUser() {
    try {
      const nextUser = await api.me();
      const activeMemberships = nextUser.memberships.filter(
        (item) => item.status === "active",
      );
      const stored = window.localStorage.getItem(selectedOrganizationKey);
      const validated = validateOrganizationSelection(
        activeMemberships,
        stored,
      );
      api.setOrganizationId(validated);
      setSelectedOrganizationId(validated);
      setUser(nextUser);
      setError(null);
      if (!validated && !window.location.pathname.includes("/app/onboarding")) {
        router.replace(`/${locale}/app/onboarding`);
      }
    } catch (caught) {
      const nextError =
        caught instanceof ApiError
          ? caught
          : new ApiError("Unable to load workspace.", 500, "workspace_failed");
      setError(nextError);
      setUser(null);
      if (nextError.status === 401) {
        router.replace(
          `/${locale}/login?next=${encodeURIComponent(window.location.pathname)}`,
        );
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      void api
        .me()
        .then((nextUser) => {
          if (!active) return;
          const validated = validateOrganizationSelection(
            nextUser.memberships,
            window.localStorage.getItem(selectedOrganizationKey),
          );
          api.setOrganizationId(validated);
          setSelectedOrganizationId(validated);
          setUser(nextUser);
          setError(null);
          if (
            !validated &&
            !window.location.pathname.includes("/app/onboarding")
          ) {
            router.replace(`/${locale}/app/onboarding`);
          }
        })
        .catch((caught: unknown) => {
          if (!active) return;
          const nextError =
            caught instanceof ApiError
              ? caught
              : new ApiError(
                  "Unable to load workspace.",
                  500,
                  "workspace_failed",
                );
          setError(nextError);
          setUser(null);
          if (nextError.status === 401) {
            router.replace(
              `/${locale}/login?next=${encodeURIComponent(window.location.pathname)}`,
            );
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [api, locale, router]);

  function selectOrganization(id: string) {
    if (!user?.memberships.some((membership) => membership.organization === id))
      return;
    setSwitching(true);
    api.setOrganizationId(null);
    setSelectedOrganizationId(null);
    queryClient.clear();
    window.localStorage.setItem(selectedOrganizationKey, id);
    window.requestAnimationFrame(() => {
      api.setOrganizationId(id);
      setSelectedOrganizationId(id);
      setSwitching(false);
      router.refresh();
    });
  }

  const membership =
    user?.memberships.find(
      (item) => item.organization === selectedOrganizationId,
    ) ?? null;

  async function logout() {
    await api.logout();
    api.setOrganizationId(null);
    setUser(null);
    setSelectedOrganizationId(null);
    queryClient.clear();
    window.localStorage.removeItem(selectedOrganizationKey);
    router.replace(`/${locale}/login`);
  }

  const value: WorkspaceValue = {
    api,
    user,
    membership,
    selectedOrganizationId,
    loading,
    switching,
    error,
    selectOrganization,
    refreshUser,
    logout,
  };

  return (
    <QueryClientProvider client={queryClient}>
      <WorkspaceContext.Provider value={value}>
        {children}
      </WorkspaceContext.Provider>
    </QueryClientProvider>
  );
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value)
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}
