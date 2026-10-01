import { Notice } from "../components/ui";

/** Pages that later phases build. They never show sample numbers in the meantime. */
export function Placeholder({ title, phase }: { title: string; phase: number }) {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <h1 className="text-lg font-semibold">{title}</h1>
      <Notice>
        Not built yet. This page arrives in Phase {phase} of the roadmap. Until then it shows nothing rather than
        sample data.
      </Notice>
    </div>
  );
}
