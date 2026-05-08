"use client";

/**
 * /portal-batches — W4.5 (v3-watt-coverage). Admin-only page.
 *
 * Lists FIXED rejections grouped by supplier. The admin team checks the
 * rows they want to ship in this run, hits "Send batch to {supplier}
 * portal", and the server flips each one to SUBMITTED_TO_PORTAL + writes
 * an audit row. The portal call is stubbed today (logs PORTAL_BATCH_SUBMIT
 * server-side); the real adapter lands in a follow-up.
 *
 * Design rhythm matches the /rejections page:
 *   - Top bar with H1 + admin pill + supplier-filter chips
 *   - Body is a stack of supplier "cards" (one per supplier batch)
 *   - Each card lists its FIXED rejections with a row-level checkbox
 *   - Footer of each card has a primary "Send batch" button
 */
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Pill } from "@/components/design/Pill";
import {
  type PortalBatch,
  usePortalBatchesQuery,
} from "@/lib/queries/rejections";
import { useSubmitPortalBatch } from "@/lib/mutations/rejections";

function pluralise(n: number, w: string): string {
  return `${n} ${w}${n === 1 ? "" : "s"}`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}

// ── Per-supplier card ──────────────────────────────────────────────────

function SupplierBatchCard({ batch }: { batch: PortalBatch }) {
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(batch.rejections.map((r) => r.id)),
  );
  const submit = useSubmitPortalBatch();

  const ids = useMemo(() => Array.from(picked), [picked]);
  const allSelected = picked.size === batch.rejections.length;

  const toggleAll = () => {
    if (allSelected) setPicked(new Set());
    else setPicked(new Set(batch.rejections.map((r) => r.id)));
  };

  const toggleOne = (id: string) => {
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onSend = () => {
    if (ids.length === 0) return;
    submit.mutate(
      { supplier: batch.supplier, rejection_ids: ids },
      {
        onSuccess: () => {
          // Optimistic-feeling: clear the picks. Query invalidation in the
          // mutation hook re-fetches the batches list, so the rows shipping
          // out will disappear from the next render naturally.
          setPicked(new Set());
        },
      },
    );
  };

  return (
    <section
      data-slot="portal-batch-card"
      data-supplier={batch.supplier}
      style={{
        border: "1px solid var(--border-subtle)",
        borderRadius: 10,
        background: "var(--bg-elev1)",
        overflow: "hidden",
        marginBottom: 16,
      }}
    >
      {/* Card header */}
      <header
        style={{
          padding: "14px 18px",
          borderBottom: "1px solid var(--border-subtle)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div
          style={{
            fontSize: 15,
            fontWeight: 600,
            letterSpacing: "-0.012em",
            color: "var(--text-primary)",
          }}
        >
          {batch.supplier}
        </div>
        <Pill tone="emerald" dot>
          {pluralise(batch.count, "fixed")}
        </Pill>
        <div style={{ flex: 1 }} />
        <button
          type="button"
          onClick={toggleAll}
          style={{
            fontSize: 11.5,
            fontWeight: 500,
            color: "var(--text-muted)",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            padding: "4px 6px",
          }}
        >
          {allSelected ? "Deselect all" : "Select all"}
        </button>
      </header>

      {/* Row list */}
      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
        }}
      >
        {batch.rejections.map((rej) => {
          const checked = picked.has(rej.id);
          return (
            <li
              key={rej.id}
              data-slot="portal-batch-row"
              data-checked={checked ? "1" : "0"}
              style={{
                display: "grid",
                gridTemplateColumns: "26px 1.4fr 1fr 1fr 110px",
                alignItems: "center",
                gap: 12,
                padding: "10px 18px",
                borderBottom: "1px solid var(--border-subtle)",
                fontSize: 13,
              }}
            >
              <label
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleOne(rej.id)}
                  data-slot="portal-batch-checkbox"
                />
              </label>
              <div
                style={{
                  color: "var(--text-primary)",
                  fontWeight: 500,
                  letterSpacing: "-0.005em",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {rej.customer_name ?? rej.customer_slug ?? "—"}
                {rej.external_watt_site_id != null && (
                  <span
                    style={{
                      marginLeft: 6,
                      fontFamily: "var(--font-mono)",
                      fontSize: 10.5,
                      color: "var(--text-dim)",
                    }}
                  >
                    site_{rej.external_watt_site_id}
                  </span>
                )}
              </div>
              <div
                title={rej.rejection_reason}
                style={{
                  color: "var(--text-muted)",
                  fontSize: 12.5,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {rej.rejection_reason}
              </div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-dim)",
                }}
              >
                {rej.category}
              </div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-dim)",
                  textAlign: "right",
                }}
              >
                {fmtDate(rej.fixed_at)}
              </div>
            </li>
          );
        })}
      </ul>

      {/* Card footer */}
      <footer
        style={{
          padding: "12px 18px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: "var(--bg-elev2)",
        }}
      >
        <span
          style={{
            fontSize: 11.5,
            color: "var(--text-dim)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {ids.length} of {batch.rejections.length} selected
        </span>
        <div style={{ flex: 1 }} />
        <Button
          variant="default"
          size="sm"
          onClick={onSend}
          disabled={ids.length === 0 || submit.isPending}
        >
          {submit.isPending
            ? "Sending…"
            : `Send batch to ${batch.supplier} portal`}
        </Button>
      </footer>
    </section>
  );
}

// ── Page ───────────────────────────────────────────────────────────────

export default function PortalBatchesPage() {
  const [supplierFilter, setSupplierFilter] = useState<string | null>(null);
  // Always fetch the full list — supplier filter chips operate client-side
  // so toggling between suppliers doesn't trigger a refetch.
  const q = usePortalBatchesQuery();

  const allSuppliers = useMemo(
    () => (q.data?.batches ?? []).map((b) => b.supplier),
    [q.data],
  );

  const visible = useMemo(() => {
    const all = q.data?.batches ?? [];
    if (!supplierFilter) return all;
    return all.filter((b) => b.supplier === supplierFilter);
  }, [q.data, supplierFilter]);

  const totalRows = useMemo(
    () => (q.data?.batches ?? []).reduce((acc, b) => acc + b.count, 0),
    [q.data],
  );

  return (
    <div
      data-slot="portal-batches-page"
      style={{
        flex: 1,
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
        background: "var(--bg-elev1)",
      }}
    >
      {/* Top bar */}
      <div
        style={{
          borderBottom: "1px solid var(--border-subtle)",
          background: "var(--bg-elev1)",
          flexShrink: 0,
          padding: "16px 24px 12px",
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <h1
          style={{
            fontSize: 19,
            fontWeight: 600,
            letterSpacing: "-0.018em",
            margin: 0,
            color: "var(--text-primary)",
          }}
        >
          Portal batches
        </h1>
        <Pill tone="emerald" dot>
          {pluralise(totalRows, "ready")}
        </Pill>
        <Pill tone="violet" mono>
          admin
        </Pill>

        <div style={{ flex: 1 }} />

        <span
          style={{
            fontSize: 11.5,
            color: "var(--text-dim)",
            fontFamily: "var(--font-mono)",
          }}
        >
          stage 5 — submit fixes to supplier portals
        </span>
      </div>

      {/* Supplier filter chips */}
      {allSuppliers.length > 0 && (
        <div
          style={{
            padding: "12px 24px",
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            borderBottom: "1px solid var(--border-subtle)",
          }}
        >
          <span
            style={{
              fontSize: 11,
              color: "var(--text-dim)",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              fontWeight: 500,
              marginRight: 4,
            }}
          >
            Supplier
          </span>
          {allSuppliers.map((sup) => {
            const active = supplierFilter === sup;
            return (
              <button
                key={sup}
                type="button"
                onClick={() => setSupplierFilter((cur) => (cur === sup ? null : sup))}
                data-slot="portal-batch-supplier-chip"
                data-active={active ? "1" : "0"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  height: 26,
                  padding: "0 10px",
                  fontSize: 11.5,
                  fontWeight: 500,
                  borderRadius: 6,
                  background: active
                    ? "var(--emerald-bg-strong, rgba(16,185,129,0.18))"
                    : "var(--bg-elev2)",
                  color: active ? "var(--emerald-400)" : "var(--text-muted)",
                  border: `1px solid ${active ? "var(--emerald-border, rgba(16,185,129,0.55))" : "var(--border-subtle)"}`,
                  cursor: "pointer",
                  letterSpacing: "-0.003em",
                  whiteSpace: "nowrap",
                }}
              >
                {sup}
              </button>
            );
          })}
          {supplierFilter && (
            <button
              type="button"
              onClick={() => setSupplierFilter(null)}
              style={{
                fontSize: 11,
                color: "var(--text-dim)",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                marginLeft: 4,
              }}
            >
              clear
            </button>
          )}
        </div>
      )}

      {/* Body — stack of supplier batch cards */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: 24,
        }}
      >
        {q.isLoading ? (
          <div
            style={{
              padding: 60,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
            }}
          >
            Loading batches…
          </div>
        ) : visible.length === 0 ? (
          <div
            style={{
              padding: 60,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
              border: "1px dashed var(--border-subtle)",
              borderRadius: 10,
              background: "var(--bg-elev1)",
            }}
          >
            <div
              style={{
                fontSize: 14,
                color: "var(--text-muted)",
                marginBottom: 6,
              }}
            >
              No FIXED rejections waiting for the portal.
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              Fix a rejection on /rejections to see it here.
            </div>
          </div>
        ) : (
          visible.map((batch) => (
            <SupplierBatchCard key={batch.supplier} batch={batch} />
          ))
        )}
      </div>
    </div>
  );
}
