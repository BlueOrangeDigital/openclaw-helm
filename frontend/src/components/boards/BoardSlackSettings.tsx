"use client";

import { useCallback, useEffect, useState } from "react";

import { customFetch } from "@/api/mutator";
import { Button } from "@/components/ui/button";

interface SlackConnectionStatus {
  connected: boolean;
  slack_team_id: string | null;
  slack_team_name: string | null;
  slack_channel_id: string | null;
  slack_channel_name: string | null;
  bot_user_id: string | null;
  is_active: boolean;
  created_at: string | null;
}

interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

interface BoardSlackSettingsProps {
  boardId: string;
  isAdmin: boolean;
}

export function BoardSlackSettings({
  boardId,
  isAdmin,
}: BoardSlackSettingsProps) {
  const [status, setStatus] = useState<SlackConnectionStatus | null>(null);
  const [channels, setChannels] = useState<SlackChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);
  const [showChannelPicker, setShowChannelPicker] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await customFetch<{
        data: SlackConnectionStatus;
        status: number;
      }>(`/api/v1/slack/boards/${boardId}/status`, { method: "GET" });
      if (response.status === 200) {
        setStatus(response.data);
      }
    } catch (err) {
      // Slack not configured — that's fine, just hide the section
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [boardId]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleConnect = () => {
    const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
    window.location.href = `${apiUrl}/api/v1/slack/oauth/authorize?board_id=${boardId}`;
  };

  const handleDisconnect = async () => {
    setDisconnecting(true);
    setError(null);
    try {
      await customFetch(`/api/v1/slack/boards/${boardId}/disconnect`, {
        method: "DELETE",
      });
      setStatus(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect");
    } finally {
      setDisconnecting(false);
    }
  };

  const handleChangeChannel = async () => {
    setError(null);
    try {
      const response = await customFetch<{
        data: { channels: SlackChannel[] };
        status: number;
      }>(`/api/v1/slack/boards/${boardId}/channels`, { method: "GET" });
      if (response.status === 200) {
        setChannels(response.data.channels);
        setShowChannelPicker(true);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load channels",
      );
    }
  };

  const handleSelectChannel = async (channel: SlackChannel) => {
    setError(null);
    try {
      await customFetch(`/api/v1/slack/boards/${boardId}/channel`, {
        method: "POST",
        body: JSON.stringify({
          channel_id: channel.id,
          channel_name: channel.name,
        }),
      });
      setShowChannelPicker(false);
      await fetchStatus();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to set channel",
      );
    }
  };

  if (loading) {
    return null;
  }

  if (!isAdmin) {
    return null;
  }

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="space-y-1">
        <h2 className="text-base font-semibold text-slate-900">
          Slack Integration
        </h2>
        <p className="text-sm text-slate-600">
          Connect a Slack channel to sync messages bidirectionally with this
          board.
        </p>
      </div>

      {error ? (
        <p className="text-sm text-red-500">{error}</p>
      ) : null}

      {status?.connected ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                status.is_active ? "bg-green-500" : "bg-yellow-500"
              }`}
            />
            <span className="text-sm font-medium text-slate-700">
              {status.is_active ? "Connected" : "Inactive"}
            </span>
          </div>
          <div className="rounded-md bg-slate-50 px-4 py-3">
            <div className="grid gap-2 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-500">Workspace</span>
                <span className="font-medium text-slate-900">
                  {status.slack_team_name ?? status.slack_team_id}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Channel</span>
                <span className="font-medium text-slate-900">
                  {status.slack_channel_name
                    ? `#${status.slack_channel_name}`
                    : status.slack_channel_id ?? "Not set"}
                </span>
              </div>
              {status.created_at ? (
                <div className="flex justify-between">
                  <span className="text-slate-500">Connected</span>
                  <span className="text-slate-700">
                    {new Date(status.created_at).toLocaleDateString()}
                  </span>
                </div>
              ) : null}
            </div>
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={handleChangeChannel}
            >
              Change channel
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={handleDisconnect}
              disabled={disconnecting}
              className="text-red-600 hover:text-red-700"
            >
              {disconnecting ? "Disconnecting..." : "Disconnect"}
            </Button>
          </div>
        </div>
      ) : (
        <Button type="button" onClick={handleConnect}>
          Connect Slack
        </Button>
      )}

      {showChannelPicker ? (
        <div className="space-y-2 rounded-lg border border-slate-200 p-4">
          <h3 className="text-sm font-semibold text-slate-900">
            Select a channel
          </h3>
          {channels.length === 0 ? (
            <p className="text-sm text-slate-500">
              No channels found. The bot may need to be invited to a channel
              first.
            </p>
          ) : null}
          <div className="max-h-64 space-y-1 overflow-y-auto">
            {channels.map((channel) => (
              <button
                key={channel.id}
                type="button"
                onClick={() => handleSelectChannel(channel)}
                className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm hover:bg-slate-100"
              >
                <span className="text-slate-900">
                  {channel.is_private ? "🔒 " : "#"}
                  {channel.name}
                </span>
              </button>
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            onClick={() => setShowChannelPicker(false)}
          >
            Cancel
          </Button>
        </div>
      ) : null}
    </div>
  );
}
