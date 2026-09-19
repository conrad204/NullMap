import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { CheckCircle, FileText, UploadSimple, X } from "@phosphor-icons/react";
import type { ContributionReceipt, Verdict } from "../types";
import { submitContribution, usingMockApi } from "../api/client";
import { VERDICT_META } from "../lib/verdicts";
import { cx, formatBytes } from "../lib/format";

const ACCEPT = ".csv,.tsv,.xlsx,.json,.ipynb,.pdf,.zip,.txt,.md";
const OUTCOMES: Verdict[] = ["credible_null", "inconclusive", "failed", "unreported", "effect"];

type Status =
  | { kind: "editing" }
  | { kind: "submitting" }
  | { kind: "done"; receipt: ContributionReceipt }
  | { kind: "error"; message: string };

export default function ContributePanel() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [outcome, setOutcome] = useState<Verdict>("credible_null");
  const [files, setFiles] = useState<File[]>([]);
  const [ownership, setOwnership] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [touched, setTouched] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "editing" });
  const inputRef = useRef<HTMLInputElement>(null);

  const errors = {
    title: title.trim().length < 4 ? "Give the experiment a short title." : null,
    description:
      description.trim().length < 40
        ? "Describe what you tested and what happened, at least a couple of sentences."
        : null,
    files: files.length === 0 ? "Add at least one file: data, analysis code, or a draft."
      : files.length > 3 ? "Choose up to 3 files for this contribution."
      : files.reduce((bytes, file) => bytes + file.size, 0) > 5 * 1024 * 1024 ? "Keep the combined upload at 5 MB or smaller."
      : null,
    ownership: ownership ? null : "Confirm the ownership terms to continue.",
  };
  const hasErrors = Object.values(errors).some(Boolean);
  const busy = status.kind === "submitting";

  function addFiles(list: FileList | null) {
    if (!list || busy) return;
    const incoming = Array.from(list);
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...incoming.filter((file) => {
        const key = `${file.name}:${file.size}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })];
    });
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (hasErrors || busy) return;
    setStatus({ kind: "submitting" });
    try {
      const receipt = await submitContribution({
        title: title.trim(),
        description: description.trim(),
        outcome,
        files,
        ownershipAcknowledged: ownership,
      });
      setStatus({ kind: "done", receipt });
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : "Upload failed",
      });
    }
  }

  function reset() {
    setTitle("");
    setDescription("");
    setOutcome("credible_null");
    setFiles([]);
    setOwnership(false);
    setTouched(false);
    setStatus({ kind: "editing" });
  }

  return (
    <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(320px,380px)_1fr] lg:gap-14">
      <div className="lg:sticky lg:top-24">
        <h1 className="text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">
          Shelved an experiment? Drop the data.
        </h1>
        <p className="mt-3 max-w-[44ch] leading-relaxed text-ink-2">
          Save your files and research notes as a contribution draft. You keep ownership
          of the data, and submissions are held for review before entering the evidence index.
        </p>
        <dl className="mt-8 flex flex-col gap-5 text-sm">
          <div>
            <dt className="font-medium text-ink">What to include</dt>
            <dd className="mt-1 leading-relaxed text-ink-2">
              Raw or cleaned data, analysis scripts or notebooks, a preregistration if there was
              one, and any half-written draft.
            </dd>
          </div>
          <div>
            <dt className="font-medium text-ink">What happens next</dt>
            <dd className="mt-1 leading-relaxed text-ink-2">
              Your notes and files are stored together with a receipt. A submitted outcome is
              self-reported and needs review before it can support search findings.
            </dd>
          </div>
        </dl>
      </div>

      {status.kind === "done" ? (
        <div className="fade-up rounded-panel border border-line bg-surface p-6 shadow-panel">
          <div className="flex items-start gap-3">
            <CheckCircle size={22} weight="fill" className="mt-0.5 shrink-0 text-v-effect" aria-hidden />
            <div>
              <h2 className="text-lg font-medium text-ink">{usingMockApi ? "Demo upload complete." : status.receipt.status === "ready" ? "Contribution draft saved." : "Contribution draft received."}</h2>
              <p className="mt-1 leading-relaxed text-ink-2">
                {usingMockApi ? "This illustrative upload was not saved." : <>{files.length} {files.length === 1 ? "file" : "files"} received under <span className="text-ink">{title}</span>. {status.receipt.status === "ready" ? "Your files and notes are stored as a draft for review." : "The contribution is held as a draft for review."}</>}
              </p>
              <p className="mt-3 font-mono text-xs text-ink-3">
                contribution {status.receipt.contributionId}
              </p>
            </div>
          </div>
          <button type="button" onClick={reset} className="btn btn-secondary mt-6">
            Contribute another
          </button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-7">
          <Field
            id="c-title"
            label="Title"
            error={touched ? errors.title : null}
            help="A working title is fine."
          >
            <input
              id="c-title"
              maxLength={300}
              type="text"
              value={title}
              disabled={busy}
              onChange={(e) => setTitle(e.target.value)}
              className="field disabled:opacity-60"
              aria-invalid={touched && errors.title ? "true" : undefined}
            />
          </Field>

          <Field
            id="c-desc"
            label="What you tested and what happened"
            error={touched ? errors.description : null}
            help="Hypothesis, design, sample size, and why it stopped or stayed unpublished."
          >
            <textarea
              id="c-desc"
              maxLength={20000}
              rows={5}
              value={description}
              disabled={busy}
              onChange={(e) => setDescription(e.target.value)}
              className="field resize-y disabled:opacity-60"
              aria-invalid={touched && errors.description ? "true" : undefined}
            />
          </Field>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium text-ink">Outcome</legend>
            <div className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {OUTCOMES.map((v) => {
                const meta = VERDICT_META[v];
                const checked = outcome === v;
                return (
                  <label
                    key={v}
                    className={cx(
                      "flex cursor-pointer items-start gap-3 rounded-control border p-3 transition-colors",
                      checked ? "border-accent bg-accent-soft/60" : "border-line hover:bg-surface-2",
                      busy && "pointer-events-none opacity-60",
                    )}
                  >
                    <input
                      disabled={busy}
                      type="radio"
                      name="outcome"
                      value={v}
                      checked={checked}
                      onChange={() => setOutcome(v)}
                      className="mt-1 accent-[var(--accent)]"
                    />
                    <span>
                      <span className="flex items-center gap-2 text-sm font-medium text-ink">
                        <span aria-hidden className={cx("h-2 w-2 rounded-mark", meta.bg)} />
                        {meta.label}
                      </span>
                      <span className="mt-0.5 block text-xs leading-relaxed text-ink-2">
                        {meta.description}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <div className="flex flex-col gap-2">
            <span id="c-files-label" className="text-sm font-medium text-ink">
              Files
            </span>
            <div
              role="button"
              tabIndex={0}
              aria-labelledby="c-files-label"
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  inputRef.current?.click();
                }
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              className={cx(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-control border border-dashed px-4 py-8 text-center transition-colors",
                dragging ? "border-accent bg-accent-soft/60" : "border-line-strong hover:bg-surface-2",
                busy && "pointer-events-none opacity-60",
              )}
            >
              <UploadSimple size={22} className="text-ink-2" aria-hidden />
              <p className="text-sm text-ink">
                Drop files here or <span className="text-accent">browse</span>
              </p>
              <p className="text-xs text-ink-3">CSV, XLSX, JSON, notebooks, PDF, ZIP · 3 files, 5 MB total</p>
              <input
                ref={inputRef}
                disabled={busy}
                type="file"
                multiple
                accept={ACCEPT}
                className="sr-only"
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            {touched && errors.files && (
              <p role="alert" className="text-sm text-v-failed">
                {errors.files}
              </p>
            )}
            {files.length > 0 && (
              <ul className="mt-1 flex flex-col">
                {files.map((f) => (
                  <li
                    key={`${f.name}:${f.size}`}
                    className="flex items-center gap-3 border-t border-line py-2.5 text-sm"
                  >
                    <FileText size={16} className="shrink-0 text-ink-3" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-ink">{f.name}</span>
                    <span className="font-mono text-xs tabular-nums text-ink-3">
                      {formatBytes(f.size)}
                    </span>
                    <button
                      type="button"
                      aria-label={`Remove ${f.name}`}
                      disabled={busy}
                      onClick={() => setFiles((prev) => prev.filter((x) => x !== f))}
                      className="rounded-mark p-1 text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink"
                    >
                      <X size={14} aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <label className="flex items-start gap-3 text-sm leading-relaxed text-ink-2">
              <input
                type="checkbox"
                checked={ownership}
                disabled={busy}
                onChange={(e) => setOwnership(e.target.checked)}
                className="mt-1 accent-[var(--accent)]"
              />
              <span>
                I own this data or have the right to share it. I keep ownership and grant nullMap
                a non-exclusive license to store it and index reviewed findings.
              </span>
            </label>
            {touched && errors.ownership && (
              <p role="alert" className="text-sm text-v-failed">
                {errors.ownership}
              </p>
            )}
          </div>

          {status.kind === "error" && (
            <p role="alert" className="text-sm text-v-failed">
              {status.message}
            </p>
          )}

          <div>
            <button type="submit" disabled={busy} className="btn btn-primary">
              {busy ? "Uploading" : "Save contribution draft"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function Field({
  id,
  label,
  help,
  error,
  children,
}: {
  id: string;
  label: string;
  help?: string;
  error: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium text-ink">
        {label}
      </label>
      {children}
      {help && <p className="text-sm text-ink-3">{help}</p>}
      {error && (
        <p role="alert" className="text-sm text-v-failed">
          {error}
        </p>
      )}
    </div>
  );
}
