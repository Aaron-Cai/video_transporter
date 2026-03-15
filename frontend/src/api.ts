import axios from "axios";
import type { Channel, ChannelFormState, ChannelListItem } from "./types";

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

export async function deleteChannel(channelId: number): Promise<void> {
  await http.delete(`/channels/${channelId}`);
}

export async function syncChannel(channelId: number): Promise<void> {
  await http.post(`/channels/${channelId}/sync`);
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
