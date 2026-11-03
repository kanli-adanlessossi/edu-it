"""Rebuild ``evals/suites/golden.json`` and its Replay Fixtures from the Golden_Set.

Run after a new Prompt version or a Golden_Set change::

    python tools/build_eval_golden.py [--repo PATH]

The Golden_Set is the reviewed seed content of Phases 2 and 3 (``content/linux-fundamentals``).
The tool writes three kinds of case (ai-content Req 11.1):

* ``answer-key``: one per golden question and locale. The Fixture answers with the golden key.
* ``schema-conformance``: one per golden quiz. The Fixture is a ``QuizDraft`` assembled from the
  golden questions and options. The seed has no explanations, so each explanation names the
  correct option text; the Fixture shows that a conforming output passes, nothing more.
* ``glossary-terms``: one per golden concept, question and lab, listing the glossary pairs that
  appear in both locales of its text.

Output is canonical (sorted keys, LF, UTF-8), so rerunning on unchanged input changes nothing.
No model is called: Fixtures are derived from the Golden_Set itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

from edu_it_domain.ai.fixtures import FixtureAttempt
from edu_it_domain.ports.llm_provider import LLMInput, PromptRef
from edu_it_infrastructure.llm.fixture_store import FixtureStore
from edu_it_worker.ai.eval_runner import ANSWER_PROMPT_ID, EvalRunner
from edu_it_worker.ai.prompt_store import GLOSSARY_PROMPT_ID, PromptStore, glossary_pairs

TRACK: Final = "linux-fundamentals"
FIXTURE_MODEL: Final = "golden-seed"
_DIFFICULTY: Final = "beginner"  # the quizzes Stage's difficulty (units.QuizBuilder)


def _read(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _slug(stable_id: str) -> str:
    return stable_id.split(".", 1)[1]


def _terms(pairs: tuple[tuple[str, str], ...], en: str, fr: str) -> list[list[str]]:
    en_l, fr_l = en.casefold(), fr.casefold()
    return [[a, b] for a, b in pairs if a.casefold() in en_l and b.casefold() in fr_l]


def build(repo: Path) -> int:
    """Write the suite and Fixtures; return the number of cases."""
    content = repo / "content" / TRACK
    prompts = PromptStore()
    runner = EvalRunner(None, prompts)  # type: ignore[arg-type]  # only its prompt helpers are used
    store = FixtureStore(repo / "evals" / "fixtures")
    pairs = glossary_pairs(
        prompts.load(GLOSSARY_PROMPT_ID, runner.latest_version(GLOSSARY_PROMPT_ID))
    )
    glossary = runner.glossary_text()

    modules = {m["provenance"]["stable_id"]: m for m in _read(content / "modules.json")}
    lessons = {m["provenance"]["stable_id"]: m for m in _read(content / "lessons.json")}
    concepts = {m["provenance"]["stable_id"]: m for m in _read(content / "concepts.json")}
    options: dict[str, list[dict[str, Any]]] = {}
    for option in _read(content / "question_options.json"):
        options.setdefault(option["question_stable_id"], []).append(option)

    cases: list[dict[str, Any]] = []
    answer_version = runner.latest_version(ANSWER_PROMPT_ID)

    # answer-key: one per question and locale.
    questions = _read(content / "questions.json")
    for question in questions:
        qid = question["provenance"]["stable_id"]
        opts = options[qid]
        expected = [i for i, o in enumerate(opts) if o["is_correct"]]
        for locale in ("en", "fr"):
            texts = [o["text"][locale] for o in opts]
            prompt_text = question["prompt_text"][locale]
            cases.append(
                {
                    "id": f"answer-key.{qid}.{locale}",
                    "kind": "answer-key",
                    "source": qid,
                    "question": prompt_text,
                    "options": texts,
                    "locale": locale,
                    "expected": expected,
                }
            )
            store.record_attempt(
                PromptRef(ANSWER_PROMPT_ID, answer_version),
                LLMInput(
                    "verify_answer",
                    {
                        "question": prompt_text,
                        "options": json.dumps(texts, ensure_ascii=False),
                        "locale": locale,
                    },
                ),
                FixtureAttempt(json.dumps({"selected": expected}), FIXTURE_MODEL, 0, 0),
            )

    # schema-conformance: one per quiz.
    quiz_version = runner.latest_version("quiz")
    for quiz in _read(content / "quizzes.json"):
        quiz_id = quiz["provenance"]["stable_id"]
        lesson = lessons[quiz["lesson_stable_id"]]
        module = modules[lesson["module_stable_id"]]
        concept = concepts.get(f"concept.{_slug(lesson['provenance']['stable_id'])}")
        concept_title = (concept or lesson)["title"]
        draft_questions = []
        for question in (q for q in questions if q["quiz_stable_id"] == quiz_id):
            opts = options[question["provenance"]["stable_id"]]
            correct = [o["text"] for o in opts if o["is_correct"]]
            draft_questions.append(
                {
                    "prompt_text": question["prompt_text"],
                    "options": [{"text": o["text"], "is_correct": o["is_correct"]} for o in opts],
                    "explanation": {
                        loc: ("Correct: " if loc == "en" else "Correct : ")
                        + ", ".join(c[loc] for c in correct)
                        for loc in ("en", "fr")
                    },
                    "concept_title": concept_title,
                }
            )
        fields = {
            "track": TRACK,
            "module": module["title"]["en"],
            "concept": lesson["title"]["en"],
            "lesson": lesson["title"]["en"],
            "locale": "en",
            "difficulty": _DIFFICULTY,
        }
        cases.append(
            {
                "id": f"schema-conformance.{quiz_id}",
                "kind": "schema-conformance",
                "source": quiz_id,
                "prompt_id": "quiz",
                "schema": "QuizDraft",
                "fields": fields,
            }
        )
        store.record_attempt(
            PromptRef("quiz", quiz_version),
            LLMInput("quiz", {**fields, "glossary": glossary}),
            FixtureAttempt(
                json.dumps(
                    {"title": quiz["title"], "questions": draft_questions}, ensure_ascii=False
                ),
                FIXTURE_MODEL,
                0,
                0,
            ),
        )

    # glossary-terms: concepts, questions and labs.
    def add_terms(case_id: str, source: str, en: str, fr: str) -> None:
        terms = _terms(pairs, en, fr)
        if terms:
            cases.append(
                {
                    "id": f"glossary-terms.{case_id}",
                    "kind": "glossary-terms",
                    "source": source,
                    "texts": {"en": en, "fr": fr},
                    "terms": terms,
                }
            )

    for cid, concept in concepts.items():
        add_terms(cid, cid, concept["title"]["en"], concept["title"]["fr"])
    for question in questions:
        qid = question["provenance"]["stable_id"]
        en = " ".join([question["prompt_text"]["en"], *(o["text"]["en"] for o in options[qid])])
        fr = " ".join([question["prompt_text"]["fr"], *(o["text"]["fr"] for o in options[qid])])
        add_terms(qid, qid, en, fr)
    for lab in _read(content / "labs.json"):
        lid = lab["provenance"]["stable_id"]
        add_terms(
            lid,
            lid,
            f"{lab['title']['en']} {lab['instructions']['en']}",
            f"{lab['title']['fr']} {lab['instructions']['fr']}",
        )

    cases.sort(key=lambda c: c["id"])
    suite = {"suite": "golden", "track": TRACK, "cases": cases}
    target = repo / "evals" / "suites" / "golden.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(suite, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return len(cases)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    count = build(args.repo)
    print(f"wrote {count} case(s) to evals/suites/golden.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
