import pytest
import yaml

from ltlf.convert import convert, main


def write(d, name, text):
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


def test_safety_is_copied_as_written_and_logged(tmp_path):
    spec = convert(write(tmp_path, "no-pay_safety.ltlf", "G ( !paid )\n"))
    assert spec == {
        "name": "no-pay",
        "class": "safety",
        "enforcement": "log",
        "formula": "G ( !paid )",
        "message": "Converted from no-pay_safety.ltlf (safety): G ( !paid )",
    }


@pytest.mark.parametrize("kind", ["goal", "final"])
def test_goal_and_final_keep_their_class(tmp_path, kind):
    spec = convert(write(tmp_path, f"done_{kind}.ltlf", "F(done)"))
    assert spec["name"] == f"done-{kind}"  # done_goal and done_final must not collide
    assert spec["class"] == kind
    assert spec["formula"] == "F(done)"
    assert "enforcement" not in spec


@pytest.mark.parametrize("name", ["nounderscore.ltlf", "a_b_safety.ltlf", "x_other.ltlf"])
def test_bad_filename_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        convert(write(tmp_path, name, "F(x)"))


def test_empty_formula_rejected(tmp_path):
    with pytest.raises(ValueError):
        convert(write(tmp_path, "e_safety.ltlf", "  \n"))


def test_main_writes_yaml_and_never_overwrites(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    write(src, "a_safety.ltlf", "G(!x)")
    write(src, "b_goal", "F(y)")  # no extension: skipped, like the original loader

    assert main(src, out) == 0
    assert [p.name for p in out.iterdir()] == ["a.yaml"]
    assert yaml.safe_load((out / "a.yaml").read_text())["formula"] == "G(!x)"

    (out / "a.yaml").write_text("keep", encoding="utf-8")
    assert main(src, out) == 1
    assert (out / "a.yaml").read_text() == "keep"
