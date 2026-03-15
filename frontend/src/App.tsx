import { FormEvent, useEffect, useState } from "react";
import {
  createChannel,
  deleteChannel,
  fetchChannel,
  fetchChannels,
  retryFailedDownloads,
  syncChannel,
  updateChannel,
} from "./api";
import type { Channel, ChannelFormState, ChannelListItem } from "./types";

const initialForm: ChannelFormState = {
  name: "",
  url: "",
  description: "",
  poll_minutes: 30,
  auto_download: true,
  status: "active",
  trigger_initial_sync: true,
};

const currentTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

function parseUtcDate(value: string): Date {
  const normalized = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  return new Date(normalized);
}

function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  const date = parseUtcDate(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: currentTimeZone,
  }).format(date);
}

export function App() {
  const [channels, setChannels] = useState<ChannelListItem[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [form, setForm] = useState<ChannelFormState>(initialForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [retryLimit, setRetryLimit] = useState(20);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshSeconds, setRefreshSeconds] = useState(10);
  const [statusFilter, setStatusFilter] = useState<
    "all" | "pending" | "downloading" | "completed" | "failed" | "skipped"
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
    if (!autoRefresh || !selectedChannel) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadChannels(selectedChannel.id);
    }, refreshSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refreshSeconds, selectedChannel]);

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
      status: channel.status,
      trigger_initial_sync: false,
    });
  }

  function resetForm() {
    setEditingId(null);
    setForm(initialForm);
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

  const filteredVideos = !selectedChannel
    ? []
    : statusFilter === "all"
      ? selectedChannel.videos
      : selectedChannel.videos.filter((video) => video.status === statusFilter);

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
        <div className="hero-meta">
          <span>{channels.length} 个频道</span>
          <span>
            {channels.reduce(
              (sum, item) => sum + item.completed_video_count,
              0,
            )}{" "}
            个已下载视频
          </span>
          <span>显示时区 {currentTimeZone}</span>
        </div>
      </section>

      <main className="layout">
        <section className="panel">
          <div className="panel-header">
            <h2>{editingId ? "编辑频道" : "新增频道"}</h2>
            {editingId ? <button onClick={resetForm}>取消编辑</button> : null}
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
                <span>创建后立即下载频道全部存量视频</span>
              </label>
            ) : null}
            <button className="primary" type="submit" disabled={submitting}>
              {submitting ? "提交中..." : editingId ? "保存修改" : "创建频道"}
            </button>
          </form>
          {message ? <p className="message">{message}</p> : null}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>频道列表</h2>
            <button onClick={() => void loadChannels(selectedChannel?.id)}>
              刷新
            </button>
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
                    {channel.completed_video_count}/{channel.video_count} 已下载
                  </span>
                </div>
              </article>
            ))}
            {!channels.length ? (
              <p className="empty">还没有频道，先在左侧添加一个。</p>
            ) : null}
          </div>
        </section>

        <section className="panel detail-panel">
          <div className="panel-header">
            <h2>频道详情</h2>
            {selectedChannel ? (
              <div className="actions">
                <button onClick={() => handleEdit(selectedChannel)}>
                  编辑
                </button>
                <button onClick={() => void handleSync(selectedChannel.id)}>
                  立即同步
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
                  style={{ width: "96px" }}
                />
                <button
                  onClick={() => void handleRetryFailed(selectedChannel.id)}
                >
                  重试失败下载
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
              <div className="toolbar">
                <label className="checkbox compact">
                  <input
                    type="checkbox"
                    checked={autoRefresh}
                    onChange={(event) => setAutoRefresh(event.target.checked)}
                  />
                  <span>自动刷新</span>
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
                          | "skipped",
                      )
                    }
                  >
                    <option value="all">全部</option>
                    <option value="pending">待处理</option>
                    <option value="downloading">下载中</option>
                    <option value="completed">已完成</option>
                    <option value="failed">失败</option>
                    <option value="skipped">跳过</option>
                  </select>
                </label>
              </div>
              <div className="detail-grid">
                <div>
                  <span>频道</span>
                  <strong>{selectedChannel.name}</strong>
                </div>
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
                  <span>失败数量</span>
                  <strong>
                    {
                      selectedChannel.videos.filter(
                        (video) => video.status === "failed",
                      ).length
                    }
                  </strong>
                </div>
                <div>
                  <span>待处理数量</span>
                  <strong>
                    {
                      selectedChannel.videos.filter(
                        (video) =>
                          video.status === "pending" ||
                          video.status === "downloading",
                      ).length
                    }
                  </strong>
                </div>
              </div>
              {selectedChannel.last_error ? (
                <div className="error-box">{selectedChannel.last_error}</div>
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
                    <span>{video.status}</span>
                    <span>{video.download_path ?? "-"}</span>
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
    </div>
  );
}
