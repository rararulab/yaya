Feature: Web adapter Settings UI for LLM provider instances

  The Settings → LLM Providers tab consumes the D4c instance-shaped
  CRUD surface. This feature mirrors
  specs/plugin-web-instance-ui.spec Completion Criteria and is kept
  in sync by scripts/check_feature_sync.py.

  Scenario: LLM Providers tab renders one row per instance with active radio set
    Given the provider list is seeded with three instances and one active
    When the settings view mounts
    Then one row per instance is rendered and the active radio matches the seeded id

  Scenario: Clicking a non-active radio PATCHes active with the instance id
    Given the provider list has an inactive row for llm-openai-2
    When the operator clicks the inactive radio
    Then a PATCH to api llm providers active fires with name equal to llm-openai-2

  Scenario: Expanding a row renders the schema-driven form with action buttons
    Given a row whose backing plugin exposes a config schema
    When the operator expands the row
    Then the schema fields render inside the row body with Save Reset and Delete actions

  Scenario: Save sends a PATCH carrying only changed fields
    Given the operator edits only the row label
    When the operator clicks Save
    Then the PATCH body contains only the label field

  Scenario: Delete rejected with 409 surfaces inline row error
    Given the active instance cannot be deleted per the D4c safety 409
    When the operator confirms Delete
    Then the row renders the server detail inline instead of a toast

  Scenario: Test connection records Connected status on the row
    Given the operator clicks Test connection on a row
    When the server returns ok true with a latency
    Then the row status dot switches to the connected variant

  Scenario: Add instance happy path POSTs and re-fetches the list
    Given the add-instance modal is open with a fresh id
    When the operator submits
    Then a POST to api llm providers fires with the id and the list reloads with the new row

  Scenario: Add instance duplicate id surfaces 409 inline
    Given the server returns 409 on the create call
    When the operator submits the add-instance form
    Then the modal shows the server detail inline

  Scenario: Add instance unknown plugin surfaces 400 inline
    Given the server returns 400 on the create call
    When the operator submits the add-instance form
    Then the modal shows the server detail inline

  Scenario: Client-side instance id validator rejects dots and invalid characters
    Given the isValidInstanceId helper from the api module
    When the caller passes ids containing a dot or starting with a dash or uppercase letters
    Then the helper returns false for all invalid forms
