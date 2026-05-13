"""Parser and connector smoke tests against L5X fixtures."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.connectors.rockwell_l5x import RockwellL5XConnector
from app.parsers.structured_text_blocks import (
    STComplexBlock,
    STIfBlock,
    parse_structured_text_blocks,
)
from app.services.normalization_service import (
    _detect_rung_branches,
    normalize_l5x_project,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "l5x"

# Optional full user exports (copy into ``fixtures/l5x`` to exercise).
FULL_FIXTURE_NAMES = (
    "Conveyance LD.l5x",
    "Conveyance ST.l5x",
    "LiOH DIW TankSystem.l5x",
    "LiOH_DIW_TankSystem_Ladder.L5X",
    "INTELLI_Ladder_Test.L5X",
    "INTELLI_StructuredText_Test.L5X",
)


def _full_fixtures_present() -> bool:
    return all((FIXTURE_DIR / n).is_file() for n in FULL_FIXTURE_NAMES)


class TestL5xMiniFixture(unittest.TestCase):
    """Always-on checks using committed ``mini_routine_mix.L5X``."""

    def test_mini_parses(self) -> None:
        path = FIXTURE_DIR / "mini_routine_mix.L5X"
        self.assertTrue(path.is_file(), "mini fixture must exist")
        conn = RockwellL5XConnector()
        project = conn.parse(path.name, path.read_bytes())
        self.assertEqual(project.project_name, "Mini_Ctrl")
        self.assertEqual(len(project.controllers), 1)
        ctrl = project.controllers[0]
        self.assertEqual(len(ctrl.programs), 1)
        prog = ctrl.programs[0]
        self.assertEqual(prog.name, "PRG_One")
        self.assertEqual(len(prog.routines), 2)

        ladder = next(r for r in prog.routines if r.language == "ladder")
        self.assertEqual(ladder.name, "R_Ladder")
        self.assertIsNotNone(ladder.raw_logic)
        self.assertEqual(len(ladder.raw_logic.splitlines()), 5)
        types = {i.instruction_type for i in ladder.instructions}
        self.assertIn("BST", types)
        self.assertIn("NXB", types)
        self.assertIn("BND", types)
        self.assertIn("PARALLEL_BRANCH", types)
        self.assertIn("ONS", types)
        self.assertGreaterEqual(len(ladder.instructions), 10)

        st = next(r for r in prog.routines if r.language == "structured_text")
        self.assertIn("(* block *)", st.raw_logic or "")
        blocks = parse_structured_text_blocks(st.raw_logic)
        self.assertGreaterEqual(len(blocks), 2)
        self.assertTrue(any(isinstance(b, STIfBlock) for b in blocks))
        self.assertTrue(any(isinstance(b, STComplexBlock) for b in blocks))

    def test_mini_normalizes(self) -> None:
        path = FIXTURE_DIR / "mini_routine_mix.L5X"
        conn = RockwellL5XConnector()
        project = conn.parse(path.name, path.read_bytes())
        out = normalize_l5x_project(project)
        self.assertIn("control_objects", out)
        self.assertIn("relationships", out)


class TestL5xFullFixturesOptional(unittest.TestCase):
    """Heavy fixtures: skipped unless all six reference files are present."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _full_fixtures_present():
            raise unittest.SkipTest(
                "Copy the six reference L5X exports into tests/fixtures/l5x/"
            )

    def test_each_file_parses(self) -> None:
        conn = RockwellL5XConnector()
        for name in FULL_FIXTURE_NAMES:
            path = FIXTURE_DIR / name
            project = conn.parse(name, path.read_bytes())
            self.assertTrue(project.project_name)
            self.assertGreater(len(project.controllers), 0)

    def test_conveyance_ld_ladder(self) -> None:
        conn = RockwellL5XConnector()
        path = FIXTURE_DIR / "Conveyance LD.l5x"
        p = conn.parse(path.name, path.read_bytes())
        r = p.controllers[0].programs[0].routines[0]
        self.assertEqual(r.language, "ladder")
        self.assertEqual(len((r.raw_logic or "").splitlines()), 22)
        self.assertGreater(len(r.instructions), 80)
        br = sum(
            1
            for line in (r.raw_logic or "").splitlines()
            if _detect_rung_branches(line)[0]
        )
        self.assertGreaterEqual(br, 1)

    def test_intelli_ladder_branches(self) -> None:
        conn = RockwellL5XConnector()
        path = FIXTURE_DIR / "INTELLI_Ladder_Test.L5X"
        p = conn.parse(path.name, path.read_bytes())
        r = p.controllers[0].programs[0].routines[0]
        self.assertEqual(r.language, "ladder")
        self.assertEqual(len((r.raw_logic or "").splitlines()), 11)
        self.assertGreaterEqual(len(r.instructions), 40)
        raw = r.raw_logic or ""
        self.assertIn("BST", raw)
        self.assertIn("ONS(", raw)

    def test_intelli_st_blocks(self) -> None:
        conn = RockwellL5XConnector()
        path = FIXTURE_DIR / "INTELLI_StructuredText_Test.L5X"
        p = conn.parse(path.name, path.read_bytes())
        r = p.controllers[0].programs[0].routines[0]
        self.assertEqual(r.language, "structured_text")
        self.assertIsNotNone(r.raw_logic)
        blocks = parse_structured_text_blocks(r.raw_logic)
        self.assertGreaterEqual(len(blocks), 4)
        cx = [b for b in blocks if isinstance(b, STComplexBlock)]
        self.assertEqual(len(cx), 0)

    def test_lioh_ladder_routines(self) -> None:
        conn = RockwellL5XConnector()
        path = FIXTURE_DIR / "LiOH_DIW_TankSystem_Ladder.L5X"
        p = conn.parse(path.name, path.read_bytes())
        prog = p.controllers[0].programs[0]
        self.assertEqual(len(prog.routines), 4)
        for rt in prog.routines:
            self.assertEqual(rt.language, "ladder")
            self.assertIsNotNone(rt.raw_logic)
            self.assertGreater(len(rt.instructions), 0)

    def test_lioh_controller_st_routines(self) -> None:
        conn = RockwellL5XConnector()
        path = FIXTURE_DIR / "LiOH DIW TankSystem.l5x"
        p = conn.parse(path.name, path.read_bytes())
        prog = p.controllers[0].programs[0]
        self.assertEqual(len(prog.routines), 6)
        for rt in prog.routines:
            self.assertEqual(rt.language, "structured_text")
            self.assertIsNotNone(rt.raw_logic)

    def test_conveyance_st_parses_blocks(self) -> None:
        conn = RockwellL5XConnector()
        path = FIXTURE_DIR / "Conveyance ST.l5x"
        p = conn.parse(path.name, path.read_bytes())
        r = p.controllers[0].programs[0].routines[0]
        self.assertEqual(r.language, "structured_text")
        blocks = parse_structured_text_blocks(r.raw_logic or "")
        self.assertGreater(len(blocks), 10)
        cx = [b for b in blocks if isinstance(b, STComplexBlock)]
        self.assertGreater(len(cx), 0)
