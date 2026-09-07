"use client";

/**
 * FollowButton — per-user follow toggle for a view.
 * Optimistic update: flips state immediately, silently reverts on API error.
 *
 * DESIGN LAW: a BARE lucide Heart + count. Color-only state, square hit area.
 * NO pill radius, NO border, NO background fill, NO box padding. The heart is
 * filled + var(--color-loss) when following, else var(--text-tertiary).
 */

import * as React from "react";
import { Heart } from "lucide-react";
import { followView, unfollowView } from "@/lib/api";
import { Num } from "@/components/views/Stat";
import { cn } from "@/lib/utils";

interface FollowButtonProps {
  viewId: string;
  isFollowing: boolean;
  followerCount: number;
  onChange?: (next: { is_following: boolean; follower_count: number }) => void;
  size?: "sm" | "md";
}

export function FollowButton({
  viewId,
  isFollowing,
  followerCount,
  onChange,
  size = "md",
}: FollowButtonProps) {
  const [localFollowing, setLocalFollowing] = React.useState(isFollowing);
  const [localCount, setLocalCount] = React.useState(followerCount);
  const [busy, setBusy] = React.useState(false);

  // Sync if props change from outside (grid re-render)
  React.useEffect(() => {
    setLocalFollowing(isFollowing);
    setLocalCount(followerCount);
  }, [isFollowing, followerCount]);

  async function handleToggle(e: React.MouseEvent | React.KeyboardEvent) {
    e.stopPropagation();
    if (busy) return;

    // Optimistic
    const wasFollowing = localFollowing;
    const wasCount = localCount;
    const nextFollowing = !wasFollowing;
    const nextCount = wasFollowing ? wasCount - 1 : wasCount + 1;

    setLocalFollowing(nextFollowing);
    setLocalCount(nextCount);
    setBusy(true);

    try {
      const res = wasFollowing
        ? await unfollowView(viewId)
        : await followView(viewId);

      if ("error" in res) {
        // Silently revert
        setLocalFollowing(wasFollowing);
        setLocalCount(wasCount);
      } else {
        // Confirm server values
        setLocalFollowing(res.data.is_following);
        setLocalCount(res.data.follower_count);
        onChange?.(res.data);
      }
    } catch {
      // Network failure — silent revert
      setLocalFollowing(wasFollowing);
      setLocalCount(wasCount);
    } finally {
      setBusy(false);
    }
  }

  const iconSize = size === "sm" ? 15 : 17;
  const numSize = size === "sm" ? "label" : "md"; // 13px / 15px — both >= floor
  const activeColor = "var(--color-loss)";
  const idleColor = "var(--text-tertiary)";

  return (
    <button
      type="button"
      aria-label={localFollowing ? "Unfollow view" : "Follow view"}
      aria-pressed={localFollowing}
      disabled={busy}
      onClick={handleToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleToggle(e);
        }
      }}
      className={cn(
        "inline-flex select-none items-center gap-1.5 rounded-sm",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:pointer-events-none disabled:opacity-50",
      )}
      style={{
        // Bare: no border, no fill, no pill, no box padding.
        border: "none",
        background: "transparent",
        borderRadius: "var(--radius-xs)",
        padding: 0,
        lineHeight: 1,
        cursor: "pointer",
        color: localFollowing ? activeColor : idleColor,
        transition: "color 180ms var(--ease-quartr)",
      }}
    >
      <Heart
        size={iconSize}
        aria-hidden
        style={{
          fill: localFollowing ? "currentColor" : "none",
          strokeWidth: 1.8,
          flexShrink: 0,
        }}
      />
      <Num size={numSize} weight={600} color="currentColor">
        {localCount}
      </Num>
    </button>
  );
}
