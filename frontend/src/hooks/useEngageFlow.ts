import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const useProfiles = () =>
  useQuery({
    queryKey: ["profiles"],
    queryFn: api.getProfiles,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });

export const useCommunities = () =>
  useQuery({
    queryKey: ["communities"],
    queryFn: api.getCommunities,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });

export const useLabels = () =>
  useQuery({
    queryKey: ["labels"],
    queryFn: api.getLabels,
  });

export const useKeywordRules = () =>
  useQuery({
    queryKey: ["keywordRules"],
    queryFn: api.getKeywordRules,
  });

export const useAutomationSettings = () =>
  useQuery({
    queryKey: ["automationSettings"],
    queryFn: api.getAutomationSettings,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });

export const useQueue = () =>
  useQuery({
    queryKey: ["queue"],
    queryFn: api.getQueue,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });

export const useLogs = () =>
  useQuery({
    queryKey: ["logs"],
    queryFn: api.getLogs,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });

export const useActivity = () =>
  useQuery({
    queryKey: ["activity"],
    queryFn: api.getActivity,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });

export const useAnalytics = () =>
  useQuery({
    queryKey: ["analytics"],
    queryFn: api.getAnalytics,
  });

export const useConversations = () =>
  useQuery({
    queryKey: ["conversations"],
    queryFn: api.getConversations,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: false,
  });
