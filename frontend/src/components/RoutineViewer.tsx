import { ControlRoutine } from "@/types/intelli";

interface Props {
  routine: ControlRoutine | null;
}

export default function RoutineViewer({
  routine,
}: Props) {
  if (!routine) {
    return (
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6">
        Select a routine
      </div>
    );
  }

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 h-full overflow-auto">

      <h2 className="text-2xl font-bold mb-2">
        {routine.name}
      </h2>

      <p className="text-zinc-400 mb-6">
        {routine.language}
      </p>

      <div className="mb-8">

        <h3 className="text-lg font-semibold mb-3">
          Raw Logic
        </h3>

        <div className="bg-black rounded-xl p-4 overflow-auto">
          <pre className="text-sm whitespace-pre-wrap">
            {routine.raw_logic}
          </pre>
        </div>
      </div>

      <div>

        <h3 className="text-lg font-semibold mb-3">
          Parsed Instructions
        </h3>

        <div className="flex flex-col gap-3">

          {routine.instructions.map((instruction) => (
            <div
              key={instruction.id}
              className="bg-zinc-800 rounded-xl p-4"
            >

              <div className="font-semibold text-blue-400 mb-2">
                {instruction.instruction_type}
              </div>

              <div className="text-sm text-zinc-300 mb-2">
                Operands:
              </div>

              <div className="flex flex-wrap gap-2 mb-3">

                {instruction.operands.map((operand) => (
                  <span
                    key={operand}
                    className="bg-zinc-700 px-2 py-1 rounded-md text-xs"
                  >
                    {operand}
                  </span>
                ))}
              </div>

              <div className="text-xs text-zinc-500">
                {instruction.raw_text}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
