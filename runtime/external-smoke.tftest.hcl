mock_provider "external" {
  override_during = plan
  mock_data "external" {
    defaults = {
      result = { revision = "mocked-revision" }
    }
  }
}

run "external_program_is_mocked" {
  command = plan
  assert {
    condition     = output.revision == "mocked-revision"
    error_message = "The external program must be replaced by mocked data."
  }
}
