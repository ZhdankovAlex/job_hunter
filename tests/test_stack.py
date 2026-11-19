"""stack.language_mismatch_text: the main language of a vacancy — Python or another one — from its title and text."""
import pytest

from jobhunter.stack import language_mismatch_text


@pytest.mark.parametrize("title, expected", [
    ("Senior Go Engineer", "Go"),
    ("Golang Developer", "Go"),
    ("Java Backend Developer", "Java"),
    ("Backend Engineer (Python/Go)", ""),  # Python in the title — our stack
    ("JavaScript Developer", ""),           # JavaScript is not Java
])
def test_title_decides_first(title, expected):
    assert language_mismatch_text(title, "") == expected


def test_requirements_block_names_other_language():
    desc = ("You will code in C++ and Python on our trading engine.\n"
            "What We Look For\n- 5+ years of C++\n- Linux\n"
            "Nice to have\n- Python\n")
    assert language_mismatch_text("Software Engineer", desc) == "C++"


def test_requirements_with_python_are_not_mismatch():
    desc = "Requirements\n- Go or Python\n- Kubernetes\nBenefits\n- remote\n"
    assert language_mismatch_text("Backend Engineer", desc) == ""


def test_plus_line_inside_requirements_is_ignored():
    desc = "Требования\n- опыт backend от 5 лет\n- Rust будет плюсом\n"
    assert language_mismatch_text("Backend Engineer", desc) == ""


def test_requirements_without_end_marker_are_cut():
    desc = "Requirements:\n- Kotlin and Spring\n" + "x" * 3000 + "\nJava Java Java"
    assert language_mismatch_text("Backend Engineer", desc) == "Kotlin"


def test_two_languages_in_requirements_are_ambiguous():
    desc = "Requirements\n- Java\n- Kotlin\n"
    assert language_mismatch_text("Backend Engineer", desc) == ""


def test_text_without_python():
    assert language_mismatch_text("Backend Engineer", "We use Java. Java 17, Spring.") == "Java"
    assert language_mismatch_text("Backend Engineer", "Some Java here.") == ""  # one mention is not enough
    assert language_mismatch_text("Backend Engineer", "Plain text, no languages.") == ""


def test_python_listed_alongside_other_language():
    desc = "Experience with Python, Go, or Java.\nJava Java Java Java Java Java"
    assert language_mismatch_text("Backend Engineer", desc) == ""


def test_other_language_dominates():
    desc = "We write Java.\nJava services.\nJava 21.\nSome scripts in Python."
    assert language_mismatch_text("Backend Engineer", desc) == "Java"


def test_python_only_nice_to_have():
    desc = "We write Java services.\nJava 21 and Spring.\nNice to have: Python for scripts."
    assert language_mismatch_text("Backend Engineer", desc) == "Java"


def test_python_after_nice_to_have_heading():
    desc = "Our services use Java and Kafka.\nWe deploy Java to k8s.\nBonus points\n- Python\n"
    assert language_mismatch_text("Backend Engineer", desc) == "Java"


def test_required_section_after_nice_to_have_keeps_python_primary():
    desc = ("We run Java.\nJava legacy.\nNice to have: Kafka.\n"
            "You have: strong Python.\n")
    assert language_mismatch_text("Backend Engineer", desc) == ""


def test_python_core_requirement_is_not_mismatch():
    desc = "We write Java.\nOur core is Python.\nPython everywhere."
    assert language_mismatch_text("Backend Engineer", desc) == ""
    # a tie, and Python is not a nice-to-have — the stack is ours
    desc = "We write Java.\nJava 21.\nPython scripts.\nPython tools."
    assert language_mismatch_text("Backend Engineer", desc) == ""
