"""Tests for the SQL-facing catalog shape and discovery metadata."""

from __future__ import annotations

import json

import pytest

from vgi_tennis.examples import COMPOSED_EXAMPLES
from vgi_tennis.functions import FUNCTIONS, CurrentMatchesFunction, CurrentRankingsFunction
from vgi_tennis.meta import comment_of, result_columns_schema, sql_type
from vgi_tennis.schemas import MATCH_SCHEMA, RANKING_SCHEMA
from vgi_tennis.worker import TENNIS_CATALOG

DUCKDB_KEYWORDS = {"date", "end", "from", "interval", "type", "order", "group", "select", "to", "year"}


def test_catalog_surface() -> None:
    schema = TENNIS_CATALOG.schemas[0]
    assert TENNIS_CATALOG.name == "tennis"
    assert schema.path == ["main"]
    assert {function.Meta.name for function in schema.functions} == {
        "all_current_rankings",
        "all_current_matches",
        "rankings",
        "matches_on",
        "tournaments",
        "tournament_matches",
        "grand_slam_results",
        "player",
        "player_matches",
    }
    assert {table.name: table.function for table in schema.tables} == {
        "current_rankings": CurrentRankingsFunction,
        "current_matches": CurrentMatchesFunction,
    }


def _key_of(table, kind: str) -> list[tuple[str, ...]]:
    return [
        tuple(name for path in record.definition.column_paths for name in path)
        for record in table.constraints
        if record.definition.kind == kind
    ]


def test_table_keys() -> None:
    rankings, matches = TENNIS_CATALOG.schemas[0].tables
    assert _key_of(rankings, "PRIMARY_KEY") == [("tour", "player_id")]
    assert _key_of(matches, "PRIMARY_KEY") == [("match_id",)]
    assert _key_of(matches, "NOT_NULL") == [("match_id",)]


@pytest.mark.parametrize("function", FUNCTIONS, ids=lambda function: function.Meta.name)
def test_function_metadata_is_complete(function) -> None:
    tags = function.Meta.tags
    for key in ("vgi.title", "vgi.doc_llm", "vgi.doc_md", "vgi.example_queries", "vgi.result_columns_schema"):
        assert tags[key], key
    assert len(tags["vgi.result_columns_schema"]) < 4000
    columns = json.loads(tags["vgi.result_columns_schema"])
    assert [column["name"] for column in columns] == function.FIXED_SCHEMA.names
    assert all(column["description"] for column in columns)
    examples = json.loads(tags["vgi.example_queries"])
    assert examples and all(function.Meta.name in example["sql"] for example in examples)
    parameters = function.get_metadata().parameters
    assert not {parameter.name for parameter in parameters} & DUCKDB_KEYWORDS


def test_every_column_has_a_comment() -> None:
    for function in FUNCTIONS:
        assert all(comment_of(value) for value in function.FIXED_SCHEMA)


def test_sql_type_rendering() -> None:
    assert sql_type(MATCH_SCHEMA.field("sets").type) == (
        "STRUCT(set_number INTEGER, side1_games INTEGER, side2_games INTEGER, "
        "tiebreak_loser_points INTEGER)[]"
    )
    assert json.loads(result_columns_schema(RANKING_SCHEMA))[3]["type"] == "DATE"
    import pyarrow as pa

    assert sql_type(pa.timestamp("us")) == "TIMESTAMP"
    with pytest.raises(ValueError):
        sql_type(pa.float64())


def test_catalog_discovery_contract_is_public_and_machine_readable() -> None:
    examples = json.loads(TENNIS_CATALOG.tags["vgi.executable_examples"])
    tasks = json.loads(TENNIS_CATALOG.tags["vgi.agent_test_tasks"])
    assert examples
    assert all({"name", "description", "sql", "expected_result"} <= example.keys() for example in examples)
    assert 6 <= len(tasks) <= 12
    assert all(set(task) == {"name", "prompt"} for task in tasks)
    assert len({task["name"] for task in tasks}) == len(tasks)
    assert "not affiliated" in TENNIS_CATALOG.tags["vgi.copyright"]


def test_agent_task_file_matches_catalog_tasks() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "vgi-agent-tests.yaml").read_text()
    names = [line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("  - name:")]
    catalog = [task["name"] for task in json.loads(TENNIS_CATALOG.tags["vgi.agent_test_tasks"])]
    assert names == catalog
    assert "\n    reference_sql:" not in text
    # Every task has a judge rubric; tasks whose answer cannot be asserted from the data alone
    # (a check_sql would pass whatever the analyst answered) are graded by that rubric only.
    assert text.count("success_criteria:") == len(names)


def test_composed_examples_are_listed_at_schema_level_and_on_their_driver() -> None:
    schema = TENNIS_CATALOG.schemas[0]
    schema_sql = [example["sql"] for example in json.loads(schema.tags["vgi.example_queries"])]
    assert len(schema.tags["vgi.example_queries"]) <= 8000
    object_sql = [
        [example["sql"] for example in json.loads(tags["vgi.example_queries"])[:5]]
        for tags in [*(table.tags for table in schema.tables), *(f.Meta.tags for f in FUNCTIONS)]
    ]
    for description, sql in COMPOSED_EXAMPLES:
        assert description and sql in schema_sql
        assert any(sql in examples for examples in object_sql), description
