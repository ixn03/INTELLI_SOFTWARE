import { ControlProject, ControlRoutine } from "@/types/intelli";

interface Props {
  project: ControlProject;
  selectedRoutine: ControlRoutine | null;
  onSelectRoutine: (routine: ControlRoutine) => void;
}

export default function ProjectExplorer({
  project,
  selectedRoutine,
  onSelectRoutine,
}: Props) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4 h-full overflow-auto">
      <h2 className="text-xl font-bold mb-4">
        Project Explorer
      </h2>

      {project.controllers.map((controller) => (
        <div key={controller.name} className="mb-6">

          <div className="font-semibold text-blue-400 mb-2">
            {controller.name}
          </div>

          {controller.programs.map((program) => (
            <div key={program.name} className="ml-4 mb-4">

              <div className="text-zinc-300 mb-2">
                {program.name}
              </div>

              <div className="ml-4 flex flex-col gap-2">

                {program.routines.map((routine) => {

                  const active =
                    selectedRoutine?.name === routine.name;

                  return (
                    <button
                      key={routine.name}
                      onClick={() =>
                        onSelectRoutine(routine)
                      }
                      className={`text-left px-3 py-2 rounded-lg border transition ${
                        active
                          ? "bg-blue-600 border-blue-500"
                          : "bg-zinc-800 border-zinc-700 hover:bg-zinc-700"
                      }`}
                    >
                      <div className="font-medium">
                        {routine.name}
                      </div>

                      <div className="text-xs text-zinc-400">
                        {routine.language}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
