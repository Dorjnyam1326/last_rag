import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rag.routing import (
    ENABLED_RESTRICTION_IDS,
    INTENT_LABELS,
    MAX_HISTORY_MESSAGES,
    RELATIONSHIP_LABELS,
    RESTRICTION_RULES,
    classify_intent,
    get_enabled_restriction,
)


class ClassifyIntentTests(unittest.TestCase):
    def test_uses_structured_model_and_preserves_original_question(self):
        question = "bi margaash emchid uzuuleh geed chuluu yaj awah ve"
        search_query = "Маргааш эмчид үзүүлэхийн тулд хэрхэн чөлөө авах вэ?"
        client = Mock()
        client.with_structured_output.return_value.invoke.return_value = SimpleNamespace(
            intent="workplace",
            search_query=search_query,
            relationship="new",
            resolved_question=question,
        )
        with (
            patch("rag.routing.load_dotenv") as load_dotenv,
            patch.dict(os.environ, {"OPENAI_MODEL": "test-model"}),
            patch("langchain_openai.ChatOpenAI", return_value=client) as chat_openai,
        ):
            result = classify_intent(question)

        self.assertEqual(
            result,
            {
                "intent": "workplace",
                "search_query": search_query,
                "relationship": "new",
                "resolved_question": question,
            },
        )
        chat_openai.assert_called_once_with(model="test-model", max_retries=0)
        client.with_structured_output.assert_called_once()
        self.assertEqual(
            client.with_structured_output.call_args.kwargs,
            {"method": "json_schema"},
        )
        schema = client.with_structured_output.call_args.args[0]
        self.assertEqual(
            schema.model_json_schema()["properties"]["intent"]["enum"],
            list(INTENT_LABELS),
        )
        self.assertEqual(
            schema.model_json_schema()["properties"]["relationship"]["enum"],
            list(RELATIONSHIP_LABELS),
        )
        messages = client.with_structured_output.return_value.invoke.call_args.args[0]
        self.assertIn("salary_amount", messages[0][1])
        self.assertIn("хуваарь", messages[0][1])
        self.assertEqual(messages[1], ("human", question))
        client.with_structured_output.return_value.invoke.assert_called_once()
        load_dotenv.assert_called_once_with(
            dotenv_path=Path(__file__).resolve().parents[1] / ".env"
        )

    def test_sends_only_bounded_recent_history_in_one_router_call(self):
        history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": str(index)}
            for index in range(MAX_HISTORY_MESSAGES + 3)
        ]
        client = Mock()
        client.with_structured_output.return_value.invoke.return_value = SimpleNamespace(
            intent="workplace",
            search_query="асуулт",
            relationship="follow_up",
            resolved_question="Өмнөх асуулттай холбоотой асуулт",
        )
        with (
            patch("rag.routing.load_dotenv"),
            patch("langchain_openai.ChatOpenAI", return_value=client),
        ):
            classify_intent("Тэгвэл яах вэ?", history)

        invoke = client.with_structured_output.return_value.invoke
        invoke.assert_called_once()
        messages = invoke.call_args.args[0]
        self.assertEqual(len(messages), MAX_HISTORY_MESSAGES + 2)
        self.assertEqual(messages[1:-1], [
            (item["role"], item["content"])
            for item in history[-MAX_HISTORY_MESSAGES:]
        ])
        self.assertEqual(messages[-1], ("human", "Тэгвэл яах вэ?"))

    def test_restriction_registry_has_stable_metadata_and_salary_exception(self):
        rule_ids = {rule["id"] for rule in RESTRICTION_RULES}

        self.assertLessEqual(ENABLED_RESTRICTION_IDS, rule_ids)
        for rule in RESTRICTION_RULES:
            with self.subTest(rule=rule["id"]):
                self.assertTrue(rule["description"])
                self.assertTrue(rule["matches_intents"])
                self.assertTrue(rule["response"])
                if rule["id"] in ENABLED_RESTRICTION_IDS:
                    self.assertEqual(
                        get_enabled_restriction(rule["matches_intents"][0]), rule
                    )

        self.assertIsNone(get_enabled_restriction("salary_procedure"))


if __name__ == "__main__":
    unittest.main()
