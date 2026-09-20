import { useLayoutEffect, useRef } from "react";
import { PencilSimple } from "@phosphor-icons/react";

/** Viewport position and size of the question text in the composer at the moment it was submitted. */
export interface GlideOrigin {
  left: number;
  top: number;
  fontSize: number;
}

export const GLIDE_MS = 800;

interface Props {
  question: string;
  busy: boolean;
  origin: GlideOrigin | null;
  onEdit: () => void;
  onCancel: () => void;
}

export default function QuestionHeader({ question, busy, origin, onEdit, onCancel }: Props) {
  const textRef = useRef<HTMLHeadingElement>(null);

  // FLIP: the heading is already laid out at the top; play it back from where the text was typed.
  useLayoutEffect(() => {
    const text = textRef.current;
    if (!text) return;
    text.focus({ preventScroll: true });
    if (!origin || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const rect = text.getBoundingClientRect();
    const scale = origin.fontSize / parseFloat(getComputedStyle(text).fontSize);
    const animation = text.animate(
      [
        { transform: `translate(${origin.left - rect.left}px, ${origin.top - rect.top}px) scale(${scale})` },
        { transform: "none" },
      ],
      // Ease-in-out: the text is already on screen and travels a long way, so it should glide, not snap.
      { duration: GLIDE_MS, easing: "cubic-bezier(0.6, 0, 0.2, 1)" },
    );
    return () => animation.cancel();
  }, [origin]);

  return (
    <header className="flex flex-col gap-3 border-b border-line pb-6 sm:flex-row sm:items-start sm:justify-between sm:gap-8">
      <div className="min-w-0">
        <p className="fade-up text-sm text-ink-3">Research question</p>
        <h1 ref={textRef} tabIndex={-1} className="mt-1 max-w-[60ch] origin-top-left text-xl leading-snug tracking-tight text-ink focus:outline-none sm:text-2xl">{question}</h1>
      </div>
      <div className="fade-up shrink-0">
        {busy ? <button type="button" onClick={onCancel} className="btn btn-secondary">Cancel search</button>
          : <button type="button" onClick={onEdit} className="btn btn-secondary"><PencilSimple size={16} aria-hidden /> Edit question</button>}
      </div>
    </header>
  );
}
