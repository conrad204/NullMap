import { WarningCircle } from "@phosphor-icons/react";

interface Props {
  message: string;
  onRetry?: () => void;
}

export default function ErrorNotice({ message, onRetry }: Props) {
  return (
    <div
      role="alert"
      className="fade-up flex flex-col gap-4 rounded-panel border border-line bg-surface p-5 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex gap-3">
        <WarningCircle size={20} className="mt-0.5 shrink-0 text-v-failed" aria-hidden />
        <div>
          <p className="font-medium text-ink">The search did not finish</p>
          <p className="mt-1 text-sm text-ink-2">{message}</p>
        </div>
      </div>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn btn-secondary self-start">
          Try again
        </button>
      )}
    </div>
  );
}
