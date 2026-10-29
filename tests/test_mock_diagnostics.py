"""Actionable structural errors preserve the computed-only mocking boundary."""

import pytest

from test_agent.context import AgentError
from test_agent.terraform_policy import computed_defaults

IDENTITY_SCHEMA = {
    "block_types": {
        "identity": {
            "block": {
                "attributes": {
                    "type": {"type": "string", "required": True},
                    "principal_id": {"type": "string", "computed": True},
                }
            }
        }
    }
}


def test_nested_computed_mock_accepts_a_single_object():
    computed_defaults(
        {"identity": {"principal_id": "00000000-0000-0000-0000-000000000001"}},
        IDENTITY_SCHEMA,
        "azurerm_example",
    )


@pytest.mark.parametrize("value", [[{"principal_id": "computed"}], [], "invalid"])
def test_nested_block_mock_rejects_nonobject_shape_with_a_field_path(value):
    with pytest.raises(
        AgentError,
        match=r"NESTED_MOCK_DEFAULTS_MUST_BE_OBJECT: azurerm_example.identity",
    ):
        computed_defaults({"identity": value}, IDENTITY_SCHEMA, "azurerm_example")


def test_configurable_nested_field_is_rejected_with_its_exact_path():
    with pytest.raises(
        AgentError,
        match=r"ONLY_COMPUTED_ATTRIBUTES_CAN_BE_MOCKED: azurerm_example.identity.type",
    ):
        computed_defaults(
            {"identity": {"type": "SystemAssigned"}},
            IDENTITY_SCHEMA,
            "azurerm_example",
        )


def test_unknown_nested_field_is_rejected_with_its_exact_path():
    with pytest.raises(
        AgentError, match=r"UNKNOWN_MOCK_ATTRIBUTE: azurerm_example.identity.unknown"
    ):
        computed_defaults(
            {"identity": {"unknown": "value"}}, IDENTITY_SCHEMA, "azurerm_example"
        )
