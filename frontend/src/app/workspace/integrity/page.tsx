import { Suspense } from "react";

import ControlDocumentIntegrityWorkspace from "@/components/intelli/ControlDocumentIntegrityWorkspace";

export default function IntegrityWorkspacePage() {
  return (
    <Suspense fallback={null}>
      <ControlDocumentIntegrityWorkspace />
    </Suspense>
  );
}
