"use client";

import { useState } from "react";
import { Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import { useAgentChat } from "@/lib/mutations/reviewer";
import type { ChatMessage } from "@/lib/queries/reviewer";

/**
 * AgentChatPanel — chat with the compliance agent about this call.
 *
 * Each assistant turn may carry citation chips (e.g. [T1] for transcript
 * line, [S5] for source quote). Hovering a chip opens a Popover with the
 * cited quote (UX-D16). Clicking a chip calls `onSeekTranscript` so the
 * left-rail TranscriptTimeline scrolls to the cited word range.
 */
export function AgentChatPanel({
  callId,
  onSeekTranscript,
}: {
  callId: string;
  /** Called with seconds when a chip is clicked. */
  onSeekTranscript?: (seconds: number) => void;
}) {
  const chat = useAgentChat();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");

  async function send() {
    const text = draft.trim();
    if (!text || chat.isPending) return;
    const next: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setDraft("");
    try {
      const res = await chat.mutateAsync({ call_id: callId, messages: next });
      if (res?.message) setMessages([...next, res.message]);
    } catch {
      /* toast handled in mutation */
    }
  }

  return (
    <div className="flex h-full flex-col" data-testid="agent-chat-panel">
      <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-[13px] text-[var(--text-muted)]">
            <div>Ask anything about this call.</div>
            <div className="text-[12px] text-[var(--text-dim)]">
              The agent has full transcript + checkpoint access.
            </div>
          </div>
        ) : (
          messages.map((m, i) => (
            <ChatBubble key={i} message={m} onCitationClick={onSeekTranscript} />
          ))
        )}
        {chat.isPending && (
          <div className="text-[12px] text-[var(--text-dim)]">Agent is thinking…</div>
        )}
      </div>

      <div className="flex gap-2 border-t border-[var(--border-subtle)] p-3">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about this call…"
          className="text-[13px]"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <Button onClick={send} disabled={chat.isPending || !draft.trim()}>
          <Send className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

function ChatBubble({
  message,
  onCitationClick,
}: {
  message: ChatMessage;
  onCitationClick?: (seconds: number) => void;
}) {
  const isUser = message.role === "user";
  const text = message.content ?? "";
  const citationsById = new Map<string, NonNullable<ChatMessage["citations"]>[number]>();
  for (const c of message.citations ?? []) citationsById.set(c.id, c);

  // Split on [T1] / [S5] / [T12] tokens; keep delimiters.
  const parts = text.split(/(\[[TS]\d+\])/g);

  return (
    <div className={`flex flex-col ${isUser ? "items-end" : "items-start"}`}>
      <div className="mb-1 text-[11px] text-[var(--text-dim)]">
        {isUser ? "You" : "Compliance Agent"}
      </div>
      <div
        className={`max-w-[85%] rounded-lg border px-3 py-2.5 text-[13px] leading-[1.55] ${
          isUser
            ? "border-[var(--border-subtle)] bg-[var(--bg-elev2)]"
            : "border-[var(--border-subtle)] bg-[var(--bg-elev1)]"
        }`}
      >
        {parts.map((p, i) => {
          const m = p.match(/^\[([TS])(\d+)\]$/);
          if (!m) return <span key={i}>{p}</span>;
          const id = `${m[1]}${m[2]}`;
          const cite = citationsById.get(id);
          return (
            <Popover key={i}>
              <PopoverTrigger
                data-testid="citation-chip"
                className="mx-0.5 inline-flex items-center rounded-[3px] border px-1.5 py-[1px] font-mono text-[11px]"
                style={{
                  background: "color-mix(in oklab, var(--blue-coaching) 10%, transparent)",
                  color: "var(--blue-coaching)",
                  borderColor: "color-mix(in oklab, var(--blue-coaching) 30%, transparent)",
                }}
                onClick={(e) => {
                  if (cite?.word_start != null && onCitationClick) {
                    // The chat citations carry word indices; the parent
                    // converts to seconds via the words array. For
                    // robustness, also pass the raw word_start as seconds
                    // when the backend stores it as a float.
                    e.preventDefault();
                    onCitationClick(cite.word_start);
                  }
                }}
              >
                {id}
              </PopoverTrigger>
              <PopoverContent
                side="left"
                align="start"
                className="w-[280px] border-[var(--border-strong)] bg-[var(--bg-elev2)] p-3 shadow-2xl"
              >
                <div className="mb-1.5 flex items-center gap-2 text-[11px] text-[var(--text-dim)]">
                  <span className="font-mono">{id}</span>
                  <span>{cite?.timestamp ?? (cite?.kind === "source" ? "Source" : "Transcript")}</span>
                </div>
                <div className="font-mono text-[12px] leading-[1.55] text-[var(--text-primary)]">
                  {cite?.quote ?? "(no preview available)"}
                </div>
                {cite?.word_start != null && onCitationClick ? (
                  <button
                    type="button"
                    onClick={() => onCitationClick(cite.word_start as number)}
                    className="mt-2 text-[11px] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                  >
                    Click to scroll transcript →
                  </button>
                ) : null}
              </PopoverContent>
            </Popover>
          );
        })}
      </div>
    </div>
  );
}
