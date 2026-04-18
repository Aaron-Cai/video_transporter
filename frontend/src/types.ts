export type ChannelStatus = "active" | "paused";
export type VideoStatus =
  | "pending"
  | "downloading"
  | "completed"
  | "failed"
  | "skipped"
  | "deferred";

export interface Video {
  id: number;
  youtube_video_id: string;
  title: string | null;
  webpage_url: string;
  status: VideoStatus;
  download_path: string | null;
  downloaded_at: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChannelListItem {
  id: number;
  name: string;
  url: string;
  description: string | null;
  poll_minutes: number;
  auto_download: boolean;
  download_concurrency: number;
  initial_download_limit: number;
  preferred_resolution: number;
  prefer_hdr: boolean;
  status: ChannelStatus;
  last_checked_at: string | null;
  last_sync_at: string | null;
  next_check_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  video_count: number;
  completed_video_count: number;
}

export interface Channel extends Omit<
  ChannelListItem,
  "video_count" | "completed_video_count"
> {
  videos: Video[];
}

export interface ChannelFormState {
  name: string;
  url: string;
  description: string;
  poll_minutes: number;
  auto_download: boolean;
  download_concurrency: number;
  initial_download_limit: number;
  preferred_resolution: number;
  prefer_hdr: boolean;
  status: ChannelStatus;
  trigger_initial_sync: boolean;
}
