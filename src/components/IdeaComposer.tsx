import { useState, type FormEvent } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import type { SearchRequest } from "../types";

interface Props {
  busy: boolean;
  onSubmit: (req: SearchRequest) => void;
  onCancel: () => void;
}

const EXAMPLES = [
  "Does intermittent fasting improve working memory in healthy adults?",
  "Do code review checklists reduce defect rates in open-source projects?",
  "Does background music at 60 bpm improve reading comprehension in adolescents?",
];

const FIELDS = [
  "Any field",
  "Medicine and health",
  "Psychology",
  "Neuroscience",
  "Computer science",
  "Economics",
  "Education",
  "Biology",
];

const MIN_LENGTH = 20;

export default function IdeaComposer({ busy, onSubmit, onCancel }: Props) {
  const [idea, setIdea] = useState("");
  const [field, setField] = useState(FIELDS[0]);
  const [touched, setTouched] = useState(false);

  const tooShort = idea.trim().length < MIN_LENGTH;
  const error =
    touched && tooShort
      ? "Describe the idea in at least a sentence so the search has something to work with."
      : null;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (tooShort || busy) return;
    onSubmit({ idea: idea.trim(), field: field === FIELDS[0] ? undefined : field });
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-7">
      <div>
        <h1 className="text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">
          Check the file drawer before you run the study.
        </h1>
        <p className="mt-3 max-w-[44ch] leading-relaxed text-ink-2">
          Describe an idea. nullMap searches the literature for prior attempts, sorts them by what
          they found, and estimates whether pursuit is worth it.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor="idea" className="text-sm font-medium text-ink">
          Your idea
        </label>
        <textarea
          id="idea"
          name="idea"
          rows={5}
          value={idea}
          disabled={busy}
          onChange={(e) => setIdea(e.target.value)}
          onBlur={() => setTouched(true)}
          placeholder="Does X change Y in population Z?"
          aria-invalid={error ? "true" : undefined}
          aria-describedby={error ? "idea-help idea-error" : "idea-help"}
          className="field resize-y disabled:opacity-60"
        />
        <p id="idea-help" className="text-sm text-ink-3">
          A hypothesis, a question, or a rough paragraph. Plain language is fine.
        </p>
        {error && (
          <p id="idea-error" role="alert" className="text-sm text-v-failed">
            {error}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor="field" className="text-sm font-medium text-ink">
          Field
        </label>
        <select
          id="field"
          name="field"
          value={field}
          disabled={busy}
          onChange={(e) => setField(e.target.value)}
          className="field appearance-none disabled:opacity-60"
        >
          {FIELDS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <p className="text-sm text-ink-3">Narrows which indexes and journals are weighted.</p>
      </div>

      <div className="flex items-center gap-3">
        {busy ? (
          <button type="button" onClick={onCancel} className="btn btn-secondary">
            Cancel search
          </button>
        ) : (
          <button type="submit" className="btn btn-primary">
            Map the literature
            <ArrowRight size={16} weight="bold" aria-hidden />
          </button>
        )}
      </div>

      <div>
        <p className="mb-2 text-sm text-ink-3">Or start from an example</p>
        <ul className="flex flex-col gap-1.5">
          {EXAMPLES.map((ex) => (
            <li key={ex}>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setIdea(ex);
                  setTouched(false);
                }}
                className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline disabled:opacity-60"
              >
                {ex}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </form>
  );
}
