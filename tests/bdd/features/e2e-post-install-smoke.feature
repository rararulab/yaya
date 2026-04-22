Feature: Post-install smoke tests

  Mirrors specs/e2e-post-install-smoke.spec. These scenarios cover the
  gap between "unit + integration tests pass in the dev env" and "the
  built wheel / sdist / binary actually works after install".

  Scenario: AC-01 bundled plugins list post-install
    Given the wheel has been installed into a fresh venv
    When the test runs yaya --json plugin list
    Then every bundled plugin name appears with status loaded and its declared category

  Scenario: AC-02 binary smoke honours YAYA_BIN when set
    Given YAYA_BIN points at a working yaya executable
    When the test runs version doctor and plugin list through that binary
    Then all four invocations exit zero with the expected JSON shapes

  Scenario: AC-03 broken binary fails the gate
    Given a stand-in executable that always exits one
    When the smoke helpers drive it through version and plugin list
    Then the helpers raise AssertionError proving the gate blocks the merge
