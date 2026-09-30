"use client";

import { useEffect, useRef, useState } from "react";
import { discoveryRunPath } from "./runRoute";

type CopyState = "idle" | "copied" | "manual";

/**
 * Copy this run's address.
 *
 * The link is the ordinary, signed-in route — there is no share token and no
 * public view of a run — so the helper text says plainly that whoever opens it
 * has to sign in. When the clipboard is unavailable (an insecure context, or a
 * browser that refuses), the address is shown selected in a read-only field so
 * it can still be copied by hand.
 */
export default function CopyRunLink({ runId }: { runId: string }) {
  const [state, setState] = useState<CopyState>("idle");
  const [url, setUrl] = useState("");
  const fieldRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (state === "manual") fieldRef.current?.select();
  }, [state]);

  async function copy() {
    // Built at click time from the page's own origin, never during render, so
    // the server and the browser always render the same markup.
    const href = window.location.origin + discoveryRunPath(runId);
    setUrl(href);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
      await navigator.clipboard.writeText(href);
      setState("copied");
    } catch {
      setState("manual");
    }
  }

  return (
    <div className="min-w-0 max-w-full space-y-1.5" data-testid="copy-run-link-block">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="copy-run-link"
          onClick={() => void copy()}
          className="rounded-md border border-[color:var(--ib-line)] px-2.5 py-1 text-xs text-[color:var(--ib-ink-2)] transition-colors hover:border-[color:var(--ib-line-strong)]"
        >
          Copy link
        </button>
        <span
          aria-live="polite"
          data-testid="copy-run-link-status"
          className="text-xs text-[color:var(--ib-ink-3)]"
        >
          {state === "copied" ? "Copied" : ""}
        </span>
      </div>
      {state === "manual" && (
        <label className="block min-w-0 text-xs text-[color:var(--ib-ink-3)]">
          Copy this link:
          <input
            ref={fieldRef}
            readOnly
            data-testid="copy-run-link-field"
            value={url}
            onFocus={(e) => e.currentTarget.select()}
            className="mt-1 block w-full min-w-0 rounded-md border border-[color:var(--ib-line)] bg-[color:var(--ib-surface)] px-2 py-1 font-mono text-xs text-[color:var(--ib-ink-2)]"
          />
        </label>
      )}
      <p className="text-xs text-[color:var(--ib-ink-3)]">
        Opening this link requires signing in.
      </p>
    </div>
  );
}
