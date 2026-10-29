mock_provider "azurerm" {
  override_during = plan
}

variables {
  name                  = "foundry-tags"
  resource_group_name   = "rg-test"
  location              = "westeurope"
  custom_subdomain_name = "foundry-tags"
}

run "default_tags_contain_the_module_owner" {
  command = plan

  assert {
    condition     = azurerm_cognitive_account.this.tags == tomap({ managed_by = "terraform" })
    error_message = "With no caller tags, only the module-owned tag should be set."
  }
}

run "module_owned_tag_takes_precedence" {
  command = plan

  variables {
    tags = {
      managed_by = "caller-value"
      team       = "platform"
    }
  }

  assert {
    condition     = azurerm_cognitive_account.this.tags == tomap({ managed_by = "terraform", team = "platform" })
    error_message = "Caller tags must be preserved while managed_by remains module-owned."
  }
}
