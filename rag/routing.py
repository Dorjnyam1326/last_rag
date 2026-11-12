import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field


INTENT_LABELS = (
    "workplace",
    "salary_procedure",
    "salary_amount",
    "programming",
    "math",
    "general",
    "greeting",
)
RELATIONSHIP_LABELS = ("new", "follow_up", "clarification")
MAX_HISTORY_MESSAGES = 6

_WORKPLACE_SCOPE_REFUSAL = (
    "Энэ туслах ажлын байрны бодлого, HR болон үйл ажиллагааны асуултад зориулагдсан."
)

RESTRICTION_RULES = (
    {
        "id": "salary_amount",
        "description": (
            "Хүний цалин, bonus эсвэл нөхөн олговрын тодорхой дүн асуух. "
            "Цалин олгох хуваарь болон цалин хянах/нэмүүлэх журам үүнд орохгүй."
        ),
        "matches_intents": ("salary_amount",),
        "response": (
            "Уучлаарай, хувь хүний цалин, урамшуулал болон нөхөн олговрын дүнгээр "
            "туслах боломжгүй."
        ),
    },
    {
        "id": "programming",
        "description": (
            "Ажлын байрны сэдэвтэй холбоогүй дан programming эсвэл код бичих хүсэлт."
        ),
        "matches_intents": ("programming",),
        "response": _WORKPLACE_SCOPE_REFUSAL,
    },
    {
        "id": "math",
        "description": "Ажлын байрны сэдэвтэй холбоогүй дан математикийн бодлого.",
        "matches_intents": ("math",),
        "response": _WORKPLACE_SCOPE_REFUSAL,
    },
)

ENABLED_RESTRICTION_IDS = {"salary_amount", "programming", "math"}


class _RoutedRequest(BaseModel):
    intent: Literal[*INTENT_LABELS] = Field(
        description="Хүсэлтийн утгад тохирох ганц intent ангилал"
    )
    search_query: str = Field(
        description="Ажлын сэдвээр хайхад зориулсан утгыг хадгалсан Монгол кирилл query"
    )
    relationship: Literal[*RELATIONSHIP_LABELS] = Field(
        description="Асуулт шинэ сэдэв, үргэлжлэл, эсвэл тодруулга шаардсан эсэх"
    )
    resolved_question: str = Field(
        description="Follow-up бол context-тэй бүрэн асуулт; шинэ асуулт бол эх асуулт"
    )


def get_enabled_restriction(intent: str) -> dict | None:
    """Return the first enabled restriction matching a classified intent."""
    return next(
        (
            rule
            for rule in RESTRICTION_RULES
            if rule["id"] in ENABLED_RESTRICTION_IDS
            and intent in rule["matches_intents"]
        ),
        None,
    )


def _recent_history(history: list[dict] | None) -> list[tuple[str, str]]:
    if not isinstance(history, list):
        return []
    return [
        (message["role"], message["content"])
        for message in history[-MAX_HISTORY_MESSAGES:]
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
        and message["content"].strip()
    ]


def classify_intent(question: str, history: list[dict] | None = None) -> dict:
    """Classify, resolve relevant follow-up context, and normalize one request."""
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    from langchain_openai import ChatOpenAI

    restriction_descriptions = "; ".join(
        f"{rule['id']}: {rule['description']}" for rule in RESTRICTION_RULES
    )
    system_prompt = (
        "Хамгийн сүүлийн human message бол одоогийн асуулт; түүнээс өмнөх user/assistant "
        "messages нь хамгийн ихдээ хэдхэн recent conversation history юм. Асуултыг "
        "утгаар нь ганц ангилалд оруул. Кирилл Монгол, латин галигтай "
        "Монгол болон English асуултыг тань. workplace нь ажлын байрны policy, "
        "HR, чөлөө, үйл ажиллагааны асуулт. salary_procedure нь цалин олгох "
        "хуваарь эсвэл цалин хянах/нэмүүлэх журам; энэ нь цалингийн дүнгээс "
        "ялгаатай бөгөөд зөвшөөрөгдсөн ажлын сэдэв. general нь эдгээрээс гадуурх "
        "ерөнхий сэдэв, greeting нь мэндчилгээ. Хязгаарлалтын ангиллууд: "
        f"{restriction_descriptions}. Холимог хүсэлтэд эдгээр ангиллын аль нэг "
        "мөн хамаарвал түүнийг сонго. Workplace болон salary_procedure-ийг "
        "general гэж ангилж болохгүй. search_query-г зөвхөн document search-д "
        "зориулж байгалийн Монгол кириллээр бич; эх асуулт аль хэдийн кирилл бол "
        "утгыг өөрчлөхгүй хэвээр үлдээж болно. Latin Mongolian, English, холимог "
        "эсвэл алдаатай бичвэрийг утгыг нь хадгалан хэвийн кирилл Монгол хэлээр "
        "буулга. Нэр, technical identifier, acronym, үүрэг/оролцогч, огноо, дүн, "
        "үгүйсгэл болон нөхцөлийг хадгал. Асуулт, history-д байхгүй role/actor, "
        "огноо, хугацаа, зөвшөөрлийн зам, хэлтэс эсвэл policy нөхцөл бүү нэм. "
        "'neg chuluu awah' гэх тодорхойгүй "
        "хэллэгийг хангалттай context байхгүй бол 'нэг өдрийн чөлөө' гэж бүү "
        "тодруул. Одоогийн асуулт дангаараа ойлгомжтой шинэ сэдэв бол history-г "
        "үл тоож relationship=new, resolved_question-д эх асуултыг хэвээр өг. "
        "Одоогийн асуулт өмнөх ярианд зайлшгүй тулгуурлавал зөвхөн холбогдох recent "
        "контекстийг ашиглан relationship=follow_up, resolved_question-д бүрэн "
        "утгатай асуулт өг; current follow-up-ийн үг хэллэгийг аль болох хэвээр "
        "хадгалж, зөвхөн ойлгомжтой болгоход шаардлагатай context нэмж болно. "
        "Сэдэв эсвэл заасан зүйл тодорхойгүй бол relationship="
        "clarification, resolved_question болон search_query-г хоосон үлдээ. "
        "Өмнөх assistant message нь зөвхөн ярианы context болохоос policy evidence "
        "биш; түүнээс дүрэм, огноо, хугацаа, үүрэг, зөвшөөрлийн зам бүү ав. "
        "Intent болон search_query-г шинэ үед одоогийн асуултаас, follow-up үед "
        "resolved_question-оос гарга. Хариулт бүү үүсгэ; зөвхөн schema-г буцаа."
    )

    messages = [("system", system_prompt), *_recent_history(history), ("human", question)]
    response = (
        ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-6-luna"), max_retries=0)
        .with_structured_output(_RoutedRequest, method="json_schema")
        .invoke(messages)
    )
    return {
        "intent": response.intent,
        "search_query": response.search_query,
        "relationship": response.relationship,
        "resolved_question": response.resolved_question,
    }
