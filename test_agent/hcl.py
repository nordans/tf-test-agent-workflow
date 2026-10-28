"""Bounded HCL parsing shared by source and test policies."""

import hcl2
from lark.exceptions import LarkError

from test_agent.context import AgentError

MAX_HCL_BYTES = 32_768
OPTIONS = hcl2.SerializationOptions(
    with_comments=False, explicit_blocks=False, strip_string_quotes=True
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AgentError(f"HCL policy rejected proposal: {code}.")


def parse_hcl(text: str, *, allowed_functions=frozenset({"length"})) -> dict:
    require(len(text.encode("utf-8")) <= MAX_HCL_BYTES, "FILE_TOO_LARGE")
    try:
        tree = hcl2.parses_to_tree(text)
        for node in tree.iter_subtrees():
            if "function" in str(node.data):
                require(str(node.data) == "function_call", "UNSUPPORTED_FUNCTION")
                name = str(node.children[0].children[0])
                require(name in allowed_functions, "UNSUPPORTED_FUNCTION")
        return hcl2.loads(text, serialization_options=OPTIONS)
    except (
        LarkError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        RecursionError,
    ):
        raise AgentError("HCL policy rejected proposal: INVALID_SYNTAX.") from None
