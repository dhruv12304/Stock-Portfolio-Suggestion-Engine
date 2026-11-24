// TanStack Query hooks for the REST surface.
//
// Each hook returns the typed payload plus mutation helpers where useful.
// Callers never need to touch the api module directly.

import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { api } from '../api';
import type {
  BuildRequest,
  Period,
  PortfolioList,
  StrategyDescriptor,
} from '../types';

export function useStrategies() {
  return useQuery<StrategyDescriptor[]>({
    queryKey: ['strategies'],
    queryFn: () => api.listStrategies(),
    staleTime: 5 * 60 * 1000,
  });
}

export function usePortfolioList() {
  return useQuery({
    queryKey: ['portfolios'],
    queryFn: () => api.listPortfolios(),
    staleTime: 10_000,
  });
}

export function usePortfolio(portfolioId: string | null) {
  return useQuery({
    queryKey: ['portfolio', portfolioId],
    queryFn: () => api.getPortfolio(portfolioId!),
    enabled: !!portfolioId,
    staleTime: 30_000,
  });
}

export function useHistory(portfolioId: string | null, period: Period) {
  return useQuery({
    queryKey: ['history', portfolioId, period],
    queryFn: () => api.getHistory(portfolioId!, period),
    enabled: !!portfolioId,
    staleTime: 60_000,
  });
}

export function useRisk(portfolioId: string | null, period: Period) {
  return useQuery({
    queryKey: ['risk', portfolioId, period],
    queryFn: () => api.getRisk(portfolioId!, period),
    enabled: !!portfolioId,
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------
export function useBuildPortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: BuildRequest) => api.buildPortfolio(req),
    onSuccess: (data) => {
      qc.setQueryData(['portfolio', data.portfolio_id], data);
      qc.setQueryData<PortfolioList>(['portfolios'], (old) =>
        old ? { ...old, active_id: data.portfolio_id } : old,
      );
      qc.invalidateQueries({ queryKey: ['portfolios'] });
    },
  });
}

export function useRenamePortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      api.renamePortfolio(id, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portfolios'] });
    },
  });
}

export function useDuplicatePortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name?: string }) =>
      api.duplicatePortfolio(id, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portfolios'] });
    },
  });
}

export function useActivatePortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.activatePortfolio(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portfolios'] });
    },
  });
}

export function useDeletePortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deletePortfolio(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portfolios'] });
    },
  });
}
