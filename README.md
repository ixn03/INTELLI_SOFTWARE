# INTELLI_SOFTWARE
# This was created by Ian Gaic Nana Nzouakeu. We are currently 5/12/2026 and I finally decided to seriously start working on this.
# At my previous job at Georgia-Pacific and even now at Tesla, i have noticed often times how engineers struggle with wasted time on troubleshooting Controls systems
# documentations are not always accurate, sometimes they do not have good ways of capturing faults, or even parsing PLC or DCS programs to understand exactly what is happening
# Intelli will be a reasoning and intelligence engine
# This will include: Data ingestion layer , parsing layer, normalization layer, deterministic reasoning layer, LLM interpretation, knowledged storage  

# Copyright (c) 2026 INTELLI Software

# All rights reserved.

# This software and associated documentation are proprietary and confidential.
# Unauthorized copying, distribution, modification, or use is strictly prohibited.

## Internal Model Architecture

INTELLI treats vendor files as source inputs, not as the reasoning surface.
Connectors parse each vendor export into a deterministic intermediate model,
then the normalizer converts that model into vendor-neutral control objects,
relationships, and execution contexts. The LLM layer should only explain,
query, or summarize that normalized model; it should not reason directly over
raw controller exports.

The parsed intermediate model now has generic logic IR objects for common
controls concepts:

- `LogicObject`, `LogicBlock`, `LogicCondition`, `LogicRead`, `LogicWrite`,
  `LogicBranch`, and `LogicSequence`
- controller hierarchy objects: `Controller`, `Program`, `Routine`, and `Tag`
- Rockwell AOI staging objects exposed through generic names:
  `AOIDefinition` and `AOIInstance`
- ladder objects: `LadderRung` and `LadderBranch`
- FBD/SFC placeholders for future data-flow normalization:
  `FBDBlock`, `FBDPin`, `SFCStep`, and `SFCTransition`

Vendor-specific fields belong in `metadata` or `platform_specific`; concepts
that are true across PLC/DCS platforms should be promoted to the neutral model.
Source locations are structural paths, so reads, writes, and blocks can be
audited without copying source logic into documentation or fixtures.

## Control Dependency Graph

The normalized model now feeds a deterministic dependency graph generator.
For each normalized write, INTELLI links the written tag to the upstream tags
and structured conditions read by the same logic object. The graph records
tag provenance: who writes a tag, who reads it, where those accesses occur,
which conditions gate the write, and which evidence is still missing for a
high-confidence troubleshooting conclusion.

The dependency graph is intentionally vendor-neutral and LLM-free. Unknown
logic blocks can be preserved as direction-unknown references for review, but
they do not create inferred cause-and-effect edges until their parameter
directions are known.

### FBD Phase 1

Function Block Diagram support is structural. Rockwell FBD exports that expose
blocks, pins, and wires are normalized into `FBDBlock`, `FBDPin`, and
connection relationships. Explicit input pins can read tags, explicit output
pins can write tags, and output-to-input wires can participate in dependency
tracing. Unknown-direction pins remain `REFERENCES` with direction-unknown
metadata and do not create cause/effect edges.

Phase 1 does not infer vendor block behavior from block type names. If an FBD
routine is present but the export contains no block/pin/wire body, INTELLI
preserves the routine as unsupported structure rather than inventing a diagram.
