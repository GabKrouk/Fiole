from app.importers import parse_number, parse_text


def test_english_csv():
    t = parse_text("a,b\n1.5,2\n3,NA\n")
    assert t["detected"] == {"delimiter": ",", "decimal": ".", "header": True}
    assert t["rows"][1]["cells"] == {"c1": 3.0, "c2": None}


def test_french_semicolon_csv():
    t = parse_text("x;y\n1,5;2,25\n3;\n")
    assert t["detected"]["delimiter"] == ";" and t["detected"]["decimal"] == ","
    assert t["rows"][0]["cells"] == {"c1": 1.5, "c2": 2.25}
    assert t["rows"][1]["cells"]["c2"] is None


def test_missing_tokens_never_zero():
    t = parse_text("v\n1\n-\nn.d.\n#N/A\n\n0\n")
    vals = [r["cells"]["c1"] for r in t["rows"]]
    assert vals[:4] == [1.0, None, None, None]
    assert vals[-1] == 0.0


def test_no_header_detected():
    t = parse_text("1\t2\n3\t4\n")
    assert t["detected"]["header"] is False and len(t["rows"]) == 2


def test_mixed_column_warns():
    t = parse_text("a\n1\n2\nabc\n")
    assert t["columns"][0]["type"] == "text"
    assert t["warnings"][0]["code"] == "mixed_column"


def test_thousands_space():
    assert parse_number("1 234,5", ",") == 1234.5
    assert parse_number("1,5", ".") is None
