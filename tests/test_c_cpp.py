from pathlib import Path

from dryer.discover import language_of
from dryer.extract import entries_in_source
from dryer.scan import find_duplicates, scan_files
from dryer.shape import jaccard


def write_source(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def pairs(root: Path):
    files = sorted(path for path in root.rglob("*") if path.is_file())
    entries, warnings = scan_files(files, root, min_lines=1, min_nodes=1)
    assert warnings == []
    return find_duplicates(entries, threshold=0.99)


def entries(language: str, source: str):
    name = "sample.c" if language == "c" else "sample.cpp"
    found, warning = entries_in_source(language, source, name, name)
    assert warning is None
    return found


def score(language: str, left: str, right: str) -> float:
    [one] = entries(language, left)
    [two] = entries(language, right)
    return jaccard(one.fingerprints, two.fingerprints)


C_LEFT = """\
static int alpha(int *xs, int n) {
    int total = 0;
    for (int i = 0; i < n; i++) total += square(xs[i]);
    return total;
}
"""

C_RIGHT = """\
static int beta(int *items, int count) {
    int sum = 0;
    for (int k = 0; k < count; k++) sum += square(items[k]);
    return sum;
}
"""

CPP_LEFT = """\
int Board::alpha(std::vector<int> xs) const {
    auto ys = std::sort(xs.begin(), xs.end());
    return std::accumulate(ys.begin(), ys.end(), this->base);
}
"""

CPP_RIGHT = """\
int Panel::beta(std::vector<int> items) const {
    auto kept = std::sort(items.begin(), items.end());
    return std::accumulate(kept.begin(), kept.end(), this->origin);
}
"""


def test_extensions_name_c_and_cpp():
    assert {ext: language_of("x" + ext) for ext in (".c", ".h")} == {".c": "c", ".h": "c"}
    cpp = (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx")
    assert {language_of("x" + ext) for ext in cpp} == {"cpp"}


def test_c_and_cpp_match_renamed_locals(tmp_path):
    write_source(tmp_path, "left.c", C_LEFT)
    write_source(tmp_path, "right.c", C_RIGHT)
    write_source(tmp_path, "left.cpp", CPP_LEFT)
    write_source(tmp_path, "right.cpp", CPP_RIGHT)
    found = pairs(tmp_path)
    assert [(item.language, item.left.file, item.right.file, item.score) for item in found] == [
        ("c", "left.c", "right.c", 1.0),
        ("cpp", "left.cpp", "right.cpp", 1.0),
    ]


def test_changed_literals_still_match():
    left = 'int f(void) { log("start", 1, \'a\', 2.5); return 0; }\n'
    right = 'int g(void) { log("other", 42, \'z\', 0x10); return 7; }\n'
    assert score("c", left, right) == 1.0
    assert score("c", "int f(void) { return g(1); }\n", 'int f(void) { return g("x"); }\n') == 1.0
    left_cpp = 'int f() { log("a" "b", 10ms, nullptr); return 1; }\n'
    right_cpp = 'int g() { log("c" "d", 20ms, nullptr); return 2; }\n'
    assert score("cpp", left_cpp, right_cpp) == 1.0
    assert score("cpp", 'int f() { return g("a" "b"); }\n', 'int f() { return g("x"); }\n') == 1.0
    assert score("cpp", "int f() { return g(10ms); }\n", "int f() { return g(10); }\n") == 1.0


def test_a_different_control_structure_is_not_the_same_structure():
    body = "{ s += n; n--; }"
    loop = "int g(int n) { int s = 0; while (n > 0) " + body + " return s; }\n"
    for other in (
        "int f(int n) { int s = 0; for (; n > 0;) " + body + " return s; }\n",
        "int f(int n) { int s = 0; if (n > 0) " + body + " return s; }\n",
    ):
        assert score("c", other, loop) < 1.0


def test_qualified_callee_names_stay():
    left = "void f(std::vector<int> &xs) { std::sort(xs.begin(), xs.end()); }\n"
    right = "void g(std::vector<int> &xs) { std::reverse(xs.begin(), xs.end()); }\n"
    assert score("cpp", left, right) < 1.0
    left = "int f(Box b) { return b.get<int>(); }\n"
    right = "int g(Box b) { return b.put<int>(); }\n"
    assert score("cpp", left, right) < 1.0
    differing = [
        ("void f(V xs) { std::sort(xs); }", "void f(V xs) { boost::sort(xs); }"),
        ("int f(int a) { return std::max<int>(a); }", "int f(int a) { return std::min<int>(a); }"),
        ("int f(int n) { return Foo<int>::make(n); }", "int f(int n) { return Bar<int>::make(n); }"),
    ]
    for left, right in differing:
        assert score("cpp", left + "\n", right + "\n") < 1.0, (left, right)


def test_macro_calls_are_compared_as_written():
    left = "int f(int a, int b) { return MIN(a, b); }\n"
    right = "int g(int a, int b) { return MAX(a, b); }\n"
    assert score("c", left, right) < 1.0
    expanded = "int g(int a, int b) { return ((a) < (b) ? (a) : (b)); }\n"
    assert score("c", left, expanded) < 0.82


def test_conditional_code_inside_a_body_is_compared_as_written():
    left = """\
int f(int x) {
    int y = x * 2;
#ifdef TRACE
    trace(y);
#endif
    return y;
}
"""
    right = "int g(int x) {\n    int y = x * 2;\n    return y;\n}\n"
    assert score("c", left, right) < 1.0


def test_functions_in_preprocessor_branches_are_candidates_and_macros_are_not():
    source = """\
#define SQUARE(x) ((x) * (x))
#ifdef FAST
int fast(int x) { return SQUARE(x); }
#else
int slow(int x) { return x * x; }
#endif
#if 0
int dead(int x) { return x; }
#endif
"""
    assert [entry.start_line for entry in entries("c", source)] == [3, 5, 8]


def test_c_is_never_compared_with_cpp(tmp_path):
    source = "int alpha(int x) {\n    int y = twice(x);\n    return y + 1;\n}\n"
    write_source(tmp_path, "a.c", source)
    write_source(tmp_path, "b.cpp", source)
    [c_entry] = entries("c", source)
    [cpp_entry] = entries("cpp", source)
    assert c_entry.fingerprints == cpp_entry.fingerprints
    assert pairs(tmp_path) == []


def test_only_definitions_with_bodies_are_candidates():
    source = """\
int proto(int a);
struct Shape {
    Shape() = default;
    Shape(const Shape &) = delete;
    virtual int area() const = 0;
    int width() const;
    int height() const { return h; }
    int h;
};
int Shape::width() const {
    auto twice = [](int v) { return v * 2; };
    struct Local { int m() { return 1; } };
    return twice(h);
}
void guarded() try { work(); } catch (...) { recover(); }
template <typename T>
T identity(T value) { return value; }
auto callback = []() { struct In { int m() { return 2; } }; return 0; };
"""
    assert [(entry.start_line, entry.end_line) for entry in entries("cpp", source)] == [
        (7, 7),
        (10, 14),
        (15, 15),
        (17, 17),
    ]
