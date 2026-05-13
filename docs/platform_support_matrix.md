# INTELLI Platform Support Matrix

This matrix describes the current offline import/parsing scope. It does not imply live controller connectivity.

## Rockwell Studio 5000 L5X

- Supported file types: `.l5x`
- Parsed objects: controllers, controller/program tags, programs, ladder routines, structured text routines, common ladder/ST instructions.
- Normalized relationships: `CONTAINS`, `READS`, `WRITES`, `CALLS`, latch/unlatch/reset/write variants where statically obvious.
- Unsupported areas: full branch-aware ladder execution semantics, complete instruction catalog, deep AOI semantics, vendor FBD/SFC diagram parsing.
- Confidence level: useful parser for declared L5X ladder/ST scope.
- Next improvements: expand instruction coverage, structured text grammar, branch metadata, AOI handling, and Rockwell FBD/SFC extraction.

## Siemens TIA XML

- Supported file types: `.xml` TIA Portal Openness-style exports, with `.scl` reserved for later text handling.
- Parsed objects: project/controller names when present, OB/FB/FC/DB block shells, block language metadata, safe interface/member declarations, raw XML per block.
- Normalized relationships: `CONTAINS` for controller, program, tags, and block routines; explicit FBD placeholders only when safely identifiable.
- Unsupported areas: deep LAD/FBD network parsing, SCL statement parsing, STL semantics, compile/runtime semantics, archives beyond conservative detection.
- Confidence level: foundation support.
- Next improvements: add representative fixtures, improve block/interface extraction, then parse explicit network nodes and connections without guessing scan behavior.

## Emerson DeltaV FHX

- Supported file types: `.fhx`
- Parsed objects: system/project name when present, area grouping, control/equipment/module sections, function block inventory, parameters, links/references when syntactically visible, raw module text.
- Normalized relationships: `CONTAINS`; function block objects from explicit block instances; references/connectivity remain metadata unless direction is obvious.
- Unsupported areas: full FHX grammar, DeltaV execution semantics, module class inheritance, parameter direction inference beyond clear markers, live DeltaV connections.
- Confidence level: foundation support.
- Next improvements: build fixture-backed module grammar, improve block/link extraction, and promote only explicit read/write directions into graph relationships.

## Honeywell Experion

- Supported file types: conservative detection for `.xml`, `.txt`, `.csv`, `.cl`, and future `.zip` preservation.
- Parsed objects: project name when present, obvious controller/control module/strategy names, preserved raw source.
- Normalized relationships: `CONTAINS` for preserved project/program/routine objects.
- Unsupported areas: proprietary Experion/Control Builder semantics, C300/CEE execution model, strategy internals, live connections, LLM interpretation.
- Confidence level: preservation foundation.
- Next improvements: collect representative export fixtures, identify stable public export shapes, and add fixture-specific parsers without inventing semantics.
