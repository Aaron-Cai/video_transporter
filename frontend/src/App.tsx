import { FormEvent, useEffect, useState } from "react";
import {
  createChannel,
  deleteChannel,
  downloadDeferredVideos,
  downloadPendingVideos,
  fetchChannel,
  fetchChannels,
  retryFailedDownloads,
  syncChannel,
  updateChannel,
} from "./api";
import type {
  Channel,
  ChannelFormState,
  ChannelListItem,
  ChannelStatus,
} from "./types";

const initialForm: ChannelFormState = {
  name: "",
  url: "",
  description: "",
  poll_minutes: 30,
  auto_download: true,
  download_concurrency: 1,
  initial_download_limit: 100,
  preferred_resolution: 1080,
  prefer_hdr: false,
  status: "active",
  trigger_initial_sync: true,
};

function parseUtcDate(value: string): Date {
  const normalized = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  return new Date(normalized);
}

function padDatePart(value: number): string {
  return value.toString().padStart(2, "0");
}

function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  const date = parseUtcDate(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  const offsetMinutes = -date.getTimezoneOffset();
  const offsetSign = offsetMinutes >= 0 ? "+" : "-";
  const absoluteOffset = Math.abs(offsetMinutes);
  const offsetHours = Math.floor(absoluteOffset / 60);
  const offsetRemainderMinutes = absoluteOffset % 60;

  return `${date.getFullYear()}-${padDatePart(date.getMonth() + 1)}-${padDatePart(
    date.getDate(),
  )} ${padDatePart(date.getHours())}:${padDatePart(
    date.getMinutes(),
  )}:${padDatePart(date.getSeconds())}${offsetSign}${padDatePart(
    offsetHours,
  )}${padDatePart(offsetRemainderMinutes)}`;
}

function countVideosByStatus(
  channel: Channel,
  statuses: Array<Channel["videos"][number]["status"]>,
): number {
  return channel.videos.filter((video) => statuses.includes(video.status))
    .length;
}

function formatCountdown(
  value: string | null,
  status: ChannelStatus,
  now: Date,
): string {
  if (status === "paused") {
    return "已暂停";
  }
  if (!value) {
    return "-";
  }
  const date = parseUtcDate(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const totalSeconds = Math.max(
    0,
    Math.floor((date.getTime() - now.getTime()) / 1000),
  );
  if (totalSeconds === 0) {
    return "即将检查";
  }

  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (days > 0) {
    return `${days} 天 ${hours} 小时`;
  }
  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分`;
  }
  if (minutes > 0) {
    return `${minutes} 分 ${seconds} 秒`;
  }
  return `${seconds} 秒`;
}

function toDownloadHref(videoId: number): string {
  return `/api/channels/videos/${videoId}/play`;
}

function formatVideoStatus(status: Channel["videos"][number]["status"]): string {
  const labels: Record<Channel["videos"][number]["status"], string> = {
    pending: "待处理",
    downloading: "下载中",
    completed: "已完成",
    failed: "失败",
    skipped: "跳过",
    deferred: "暂不下载",
  };
  return labels[status];
}

export function App() {
  const [channels, setChannels] = useState<ChannelListItem[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [form, setForm] = useState<ChannelFormState>(initialForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [deferredLimit, setDeferredLimit] = useState(20);
  const [pendingLimit, setPendingLimit] = useState(20);
  const [retryLimit, setRetryLimit] = useState(20);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshSeconds, setRefreshSeconds] = useState(10);
  const [now, setNow] = useState(() => new Date());
  const [statusFilter, setStatusFilter] = useState<
    | "all"
    | "pending"
    | "downloading"
    | "completed"
    | "failed"
    | "skipped"
    | "deferred"
  >("all");

  async function loadChannels(selectedId?: number) {
    const items = await fetchChannels();
    setChannels(items);
    const nextId = selectedId ?? items[0]?.id;
    if (nextId) {
      const detail = await fetchChannel(nextId);
      setSelectedChannel(detail);
    } else {
      setSelectedChannel(null);
    }
  }

  useEffect(() => {
    void loadChannels();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(new Date());
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!autoRefresh || !selectedChannel) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadChannels(selectedChannel.id);
    }, refreshSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refreshSeconds, selectedChannel]);

  useEffect(() => {
    if (!isEditorOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !submitting) {
        resetForm();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isEditorOpen, submitting]);

  async function handleSelect(channelId: number) {
    const detail = await fetchChannel(channelId);
    setSelectedChannel(detail);
  }

  function handleEdit(channel: Channel) {
    setEditingId(channel.id);
    setForm({
      name: channel.name,
      url: channel.url,
      description: channel.description ?? "",
      poll_minutes: channel.poll_minutes,
      auto_download: channel.auto_download,
      download_concurrency: channel.download_concurrency,
      initial_download_limit: channel.initial_download_limit,
      preferred_resolution: channel.preferred_resolution,
      prefer_hdr: channel.prefer_hdr,
      status: channel.status,
      trigger_initial_sync: false,
    });
    setIsEditorOpen(true);
  }

  function handleCreate() {
    setEditingId(null);
    setForm(initialForm);
    setIsEditorOpen(true);
  }

  function resetForm() {
    setEditingId(null);
    setForm(initialForm);
    setIsEditorOpen(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    try {
      if (editingId) {
        await updateChannel(editingId, form);
        setMessage("频道已更新");
        await loadChannels(editingId);
      } else {
        const created = await createChannel(form);
        setMessage("频道已创建，后台已开始同步");
        await loadChannels(created.id);
      }
      resetForm();
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(channelId: number) {
    await deleteChannel(channelId);
    setMessage("频道已删除");
    resetForm();
    await loadChannels();
  }

  async function handleSync(channelId: number) {
    await syncChannel(channelId);
    setMessage("已触发手动同步，后台会检查并下载新视频");
    await loadChannels(channelId);
  }

  async function handleRetryFailed(channelId: number) {
    const result = await retryFailedDownloads(channelId, retryLimit);
    setMessage(`已重新加入最近失败的 ${result.queued_count} 个下载任务`);
    await loadChannels(channelId);
  }

  async function handleDownloadPending(channelId: number) {
    const result = await downloadPendingVideos(channelId, pendingLimit);
    setMessage(`已加入最新待处理的 ${result.queued_count} 个下载任务`);
    await loadChannels(channelId);
  }

  async function handleDownloadDeferred(channelId: number) {
    const result = await downloadDeferredVideos(channelId, deferredLimit);
    setMessage(`已加入最新暂不下载的 ${result.queued_count} 个下载任务`);
    await loadChannels(channelId);
  }

  const filteredVideos = !selectedChannel
    ? []
    : statusFilter === "all"
      ? selectedChannel.videos
      : selectedChannel.videos.filter((video) => video.status === statusFilter);

  const pendingVideoCount = selectedChannel
    ? countVideosByStatus(selectedChannel, ["pending"])
    : 0;
  const activeDownloadCount = selectedChannel
    ? countVideosByStatus(selectedChannel, ["downloading"])
    : 0;
  const deferredVideoCount = selectedChannel
    ? countVideosByStatus(selectedChannel, ["deferred"])
    : 0;
  const failedVideoCount = selectedChannel
    ? countVideosByStatus(selectedChannel, ["failed"])
    : 0;
  const completedVideoCount = selectedChannel
    ? countVideosByStatus(selectedChannel, ["completed"])
    : 0;

  return (
    <div className="app-shell">
      <section className="hero">
        <div>
          <p className="eyebrow">Video Transporter</p>
          <h1>YouTube 频道自动下载控制台</h1>
          <p className="hero-copy">
            管理频道、补齐历史视频，并按周期自动巡检新内容。
          </p>
        </div>
        <div className="hero-side">
          <div className="hero-actions">
            <button className="primary" onClick={handleCreate}>
              新增频道
            </button>
            {selectedChannel ? (
              <button onClick={() => handleEdit(selectedChannel)}>
                编辑当前频道
              </button>
            ) : null}
          </div>
          <div className="hero-meta">
            <span>{channels.length} 个频道</span>
            <span>
              {channels.reduce(
                (sum, item) => sum + item.completed_video_count,
                0,
              )}{" "}
              个已下载视频
            </span>
          </div>
        </div>
      </section>

      <main className="layout">
        <section className="panel">
          <div className="panel-header">
            <h2>频道列表</h2>
            <div className="actions">
              <button className="primary" onClick={handleCreate}>
                新增频道
              </button>
              <button onClick={() => void loadChannels(selectedChannel?.id)}>
                刷新
              </button>
            </div>
          </div>
          <div className="channel-list">
            {channels.map((channel) => (
              <article
                className={`channel-card ${selectedChannel?.id === channel.id ? "selected" : ""}`}
                key={channel.id}
                onClick={() => void handleSelect(channel.id)}
              >
                <div>
                  <h3>{channel.name}</h3>
                  <p>{channel.description || channel.url}</p>
                </div>
                <div className="channel-card-meta">
                  <span>{channel.status === "active" ? "启用" : "暂停"}</span>
                  <span>
                    下次{" "}
                    {formatCountdown(
                      channel.next_check_at,
                      channel.status,
                      now,
                    )}
                  </span>
                  <span>
                    {channel.completed_video_count}/{channel.video_count} 已下载
                  </span>
                </div>
              </article>
            ))}
            {!channels.length ? (
              <p className="empty">还没有频道，点击上方“新增频道”开始配置。</p>
            ) : null}
          </div>
          {message ? <p className="message">{message}</p> : null}
        </section>

        <section className="panel detail-panel">
          <div className="panel-header">
            <h2>
              频道详情
              {selectedChannel ? (
                <span className="detail-title-name">{selectedChannel.name}</span>
              ) : null}
            </h2>
            {selectedChannel ? (
              <div className="actions">
                <button onClick={() => handleEdit(selectedChannel)}>
                  编辑
                </button>
                <button
                  className="danger"
                  onClick={() => void handleDelete(selectedChannel.id)}
                >
                  删除
                </button>
              </div>
            ) : null}
          </div>
          {selectedChannel ? (
            <>
              <div className="detail-sections">
                <section className="detail-section">
                  <h3>页面控制</h3>
                  <div className="toolbar">
                    <label className="checkbox compact">
                      <input
                        type="checkbox"
                        checked={autoRefresh}
                        onChange={(event) =>
                          setAutoRefresh(event.target.checked)
                        }
                      />
                      <span>页面自动刷新</span>
                    </label>
                    <label className="inline-field">
                      <span>间隔</span>
                      <select
                        value={refreshSeconds}
                        onChange={(event) =>
                          setRefreshSeconds(Number(event.target.value))
                        }
                      >
                        <option value={5}>5 秒</option>
                        <option value={10}>10 秒</option>
                        <option value={15}>15 秒</option>
                        <option value={30}>30 秒</option>
                      </select>
                    </label>
                    <label className="inline-field">
                      <span>状态筛选</span>
                      <select
                        value={statusFilter}
                        onChange={(event) =>
                          setStatusFilter(
                            event.target.value as
                              | "all"
                              | "pending"
                              | "downloading"
                              | "completed"
                              | "failed"
                              | "skipped"
                              | "deferred",
                          )
                        }
                      >
                        <option value="all">全部</option>
                        <option value="pending">待处理</option>
                        <option value="downloading">下载中</option>
                        <option value="completed">已完成</option>
                        <option value="failed">失败</option>
                        <option value="skipped">跳过</option>
                        <option value="deferred">暂不下载</option>
                      </select>
                    </label>
                  </div>
                </section>

                <section className="detail-section">
                  <h3>下载偏好</h3>
                  <div className="detail-grid">
                    <div>
                      <span>目标分辨率</span>
                      <strong>{selectedChannel.preferred_resolution}p</strong>
                    </div>
                    <div>
                      <span>HDR 偏好</span>
                      <strong>
                        {selectedChannel.prefer_hdr ? "优先 HDR" : "仅 SDR"}
                      </strong>
                    </div>
                    <div>
                      <span>初次下载最近</span>
                      <strong>{selectedChannel.initial_download_limit} 个</strong>
                    </div>
                  </div>
                </section>

                <section className="detail-section">
                  <div className="detail-section-header">
                    <h3>频道状态</h3>
                    <button onClick={() => void handleSync(selectedChannel.id)}>
                      立即同步
                    </button>
                  </div>
                  <div className="detail-grid">
                    <div>
                      <span>状态</span>
                      <strong>
                        {selectedChannel.status === "active" ? "启用" : "暂停"}
                      </strong>
                    </div>
                    <div>
                      <span>最近检查</span>
                      <strong>{formatDate(selectedChannel.last_checked_at)}</strong>
                    </div>
                    <div>
                      <span>最近同步</span>
                      <strong>{formatDate(selectedChannel.last_sync_at)}</strong>
                    </div>
                    <div>
                      <span>下次检查</span>
                      <strong>{formatDate(selectedChannel.next_check_at)}</strong>
                    </div>
                  </div>
                </section>

                <section className="detail-section">
                  <div className="detail-section-header">
                    <h3>下载情况</h3>
                    <div className="actions">
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={pendingLimit}
                        onChange={(event) =>
                          setPendingLimit(Number(event.target.value))
                        }
                        title="下载最新待处理任务数"
                        aria-label="下载最新待处理任务数"
                      />
                      <button
                        onClick={() =>
                          void handleDownloadPending(selectedChannel.id)
                        }
                      >
                        下载待处理
                      </button>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={deferredLimit}
                        onChange={(event) =>
                          setDeferredLimit(Number(event.target.value))
                        }
                        title="下载最新暂不下载的视频数"
                        aria-label="下载最新暂不下载的视频数"
                      />
                      <button
                        onClick={() =>
                          void handleDownloadDeferred(selectedChannel.id)
                        }
                      >
                        下载历史视频
                      </button>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={retryLimit}
                        onChange={(event) =>
                          setRetryLimit(Number(event.target.value))
                        }
                        title="重试最近失败任务数"
                        aria-label="重试最近失败任务数"
                      />
                      <button
                        onClick={() => void handleRetryFailed(selectedChannel.id)}
                      >
                        重试失败下载
                      </button>
                    </div>
                  </div>
                  <div className="detail-grid">
                    <div>
                      <span>下载并发</span>
                      <strong>{selectedChannel.download_concurrency}</strong>
                    </div>
                    <div>
                      <span>待处理数量</span>
                      <strong>{pendingVideoCount}</strong>
                    </div>
                    <div>
                      <span>下载中数量</span>
                      <strong>{activeDownloadCount}</strong>
                    </div>
                    <div>
                      <span>暂不下载数量</span>
                      <strong>{deferredVideoCount}</strong>
                    </div>
                    <div>
                      <span>失败数量</span>
                      <strong>{failedVideoCount}</strong>
                    </div>
                    <div>
                      <span>已下载数量</span>
                      <strong>{completedVideoCount}</strong>
                    </div>
                  </div>
                </section>
              </div>
              {selectedChannel.last_error ? (
                <div className="error-box">
                  <span>最近检查失败原因</span>
                  <strong>{selectedChannel.last_error}</strong>
                </div>
              ) : null}
              <div className="video-table">
                <div className="video-row header">
                  <span>标题</span>
                  <span>状态</span>
                  <span>下载路径</span>
                </div>
                {filteredVideos.map((video) => (
                  <div className="video-row" key={video.id}>
                    <span>{video.title ?? video.youtube_video_id}</span>
                    <span>{formatVideoStatus(video.status)}</span>
                    <span>
                      {video.download_path ? (
                        <a
                          className="download-link"
                          href={toDownloadHref(video.id)}
                          target="_blank"
                          rel="noreferrer"
                          title={video.download_path}
                        >
                          {video.download_path}
                        </a>
                      ) : (
                        "-"
                      )}
                    </span>
                  </div>
                ))}
                {!selectedChannel.videos.length ? (
                  <p className="empty">该频道还没有同步到视频记录。</p>
                ) : !filteredVideos.length ? (
                  <p className="empty">当前筛选条件下没有匹配的视频。</p>
                ) : null}
              </div>
            </>
          ) : (
            <p className="empty">选择一个频道查看详情和下载历史。</p>
          )}
        </section>
      </main>

      {isEditorOpen ? (
        <div
          className="modal-overlay"
          onClick={() => {
            if (!submitting) {
              resetForm();
            }
          }}
        >
          <section
            className="modal-panel"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="panel-header">
              <div>
                <p className="manager-label">
                  {editingId ? "编辑频道" : "新增频道"}
                </p>
                <h2>{editingId ? "更新频道配置" : "创建新的频道任务"}</h2>
              </div>
              <button onClick={resetForm} disabled={submitting}>
                关闭
              </button>
            </div>
            <form className="channel-form" onSubmit={handleSubmit}>
              <label>
                <span>频道名称</span>
                <input
                  value={form.name}
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                  required
                />
              </label>
              <label>
                <span>频道 URL</span>
                <input
                  value={form.url}
                  onChange={(event) =>
                    setForm({ ...form, url: event.target.value })
                  }
                  placeholder="https://www.youtube.com/@channel"
                  required
                />
              </label>
              <label>
                <span>描述</span>
                <textarea
                  rows={3}
                  value={form.description}
                  onChange={(event) =>
                    setForm({ ...form, description: event.target.value })
                  }
                />
              </label>
              <div className="grid-two">
                <label>
                  <span>巡检间隔(分钟)</span>
                  <input
                    type="number"
                    min={5}
                    max={1440}
                    value={form.poll_minutes}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        poll_minutes: Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  <span>下载并发数</span>
                  <select
                    value={form.download_concurrency}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        download_concurrency: Number(event.target.value),
                      })
                    }
                  >
                    <option value={1}>1</option>
                    <option value={2}>2</option>
                    <option value={3}>3</option>
                    <option value={4}>4</option>
                    <option value={5}>5</option>
                  </select>
                </label>
              </div>
              <label>
                <span>初次下载最近视频数</span>
                <input
                  type="number"
                  min={1}
                  max={5000}
                  value={form.initial_download_limit}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      initial_download_limit: Number(event.target.value),
                    })
                  }
                />
              </label>
              <div className="grid-two">
                <label>
                  <span>状态</span>
                  <select
                    value={form.status}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        status: event.target.value as ChannelFormState["status"],
                      })
                    }
                  >
                    <option value="active">启用</option>
                    <option value="paused">暂停</option>
                  </select>
                </label>
                <label>
                  <span>目标分辨率</span>
                  <select
                    value={form.preferred_resolution}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        preferred_resolution: Number(event.target.value),
                      })
                    }
                  >
                    <option value={2160}>2160p</option>
                    <option value={1440}>1440p</option>
                    <option value={1080}>1080p</option>
                    <option value={720}>720p</option>
                    <option value={480}>480p</option>
                    <option value={360}>360p</option>
                  </select>
                </label>
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.auto_download}
                  onChange={(event) =>
                    setForm({ ...form, auto_download: event.target.checked })
                  }
                />
                <span>发现新视频后自动下载</span>
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.prefer_hdr}
                  onChange={(event) =>
                    setForm({ ...form, prefer_hdr: event.target.checked })
                  }
                />
                <span>优先下载 HDR，若无 HDR 则回退到 SDR</span>
              </label>
              {!editingId ? (
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.trigger_initial_sync}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        trigger_initial_sync: event.target.checked,
                      })
                    }
                  />
                  <span>创建后立即同步频道视频</span>
                </label>
              ) : null}
              <div className="modal-actions">
                <button type="button" onClick={resetForm} disabled={submitting}>
                  取消
                </button>
                <button className="primary" type="submit" disabled={submitting}>
                  {submitting ? "提交中..." : editingId ? "保存修改" : "创建频道"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}
