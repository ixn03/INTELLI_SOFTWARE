from app.parsers.st_if_elsif_split import parse_outer_if_elsif_else


def test_parse_if_elsif_else_three_branches() -> None:
    src = """
IF A THEN
    X := TRUE;
ELSIF B THEN
    Y := TRUE;
ELSE
    Z := TRUE;
END_IF
""".strip()
    segs = parse_outer_if_elsif_else(src)
    assert segs is not None
    assert len(segs) == 3
    assert segs[0][0] == "A"
    assert segs[1][0] == "B"
    assert segs[2][0] is None
