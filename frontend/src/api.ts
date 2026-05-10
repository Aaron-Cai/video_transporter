import axios from "axios";
import type {
  Channel,
  ChannelFormState,
  ChannelListItem,
  ChannelScanType,
} from "./types";

const http = axios.create({
  baseURL: "/api",
});

export async function fetchChannels(): Promise<ChannelListItem[]> {
  const response = await http.get<ChannelListItem[]>("/channels");
  return response.data;
}

export async function fetchChannel(channelId: number): Promise<Channel> {
  const response = await http.get<Channel>(`/channels/${channelId}`);
  return response.data;
}

export async function fetchChannelMetadata(
  url: string,
  scanType: ChannelScanType,
): Promise<{ name: string; url: string; scan_type: ChannelScanType }> {
  const response = await http.get<{
    name: string;
    url: string;
    scan_type: ChannelScanType;
  }>("/channels/metadata", {
    params: { url, scan_type: scanType },
  });
  return response.data;
}

export async function createChannel(
  payload: ChannelFormState,
): Promise<Channel> {
  const response = await http.post<Channel>("/channels", payload);
  return response.data;
}

export async function updateChannel(
  channelId: number,
  payload: Partial<ChannelFormState>,
): Promise<Channel> {
  const response = await http.put<Channel>(`/channels/${channelId}`, payload);
  return response.data;
}

export async function deleteChannel(
  channelId: number,
  deleteDownloads: boolean,
): Promise<void> {
  await http.delete(`/channels/${channelId}`, {
    params: { delete_downloads: deleteDownloads },
  });
}

export async function syncChannel(channelId: number): Promise<void> {
  await http.post(`/channels/${channelId}/sync`);
}

export async function downloadPendingVideos(
  channelId: number,
  limit: number,
): Promise<{ detail: string; queued_count: number; limit: number }> {
  const response = await http.post<{
    detail: string;
    queued_count: number;
    limit: number;
  }>(`/channels/${channelId}/download-pending?limit=${limit}`);
  return response.data;
}

export async function downloadDeferredVideos(
  channelId: number,
  limit: number,
): Promise<{ detail: string; queued_count: number; limit: number }> {
  const response = await http.post<{
    detail: string;
    queued_count: number;
    limit: number;
  }>(`/channels/${channelId}/download-deferred?limit=${limit}`);
  return response.data;
}

export async function retryFailedDownloads(
  channelId: number,
  limit: number,
): Promise<{ detail: string; queued_count: number; limit: number }> {
  const response = await http.post<{
    detail: string;
    queued_count: number;
    limit: number;
  }>(`/channels/${channelId}/retry-failed?limit=${limit}`);
  return response.data;
}
