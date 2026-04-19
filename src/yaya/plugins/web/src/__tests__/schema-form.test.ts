/**
 * Unit tests for the schema-form helpers.
 *
 * We exercise the exported `_test` surface (`isSecretField`,
 * `inferType`) rather than rendering — the Lit templates are exercised
 * by the BDD layer downstream.
 */

import { describe, expect, it } from "vitest";

import { _test } from "../schema-form.js";

describe("schema-form helpers", () => {
	it("flags fields ending in _key / _token / _secret / _password as secret", () => {
		expect(_test.isSecretField("api_key")).toBe(true);
		expect(_test.isSecretField("auth_token")).toBe(true);
		expect(_test.isSecretField("client_secret")).toBe(true);
		expect(_test.isSecretField("user_password")).toBe(true);
	});

	it("does not flag plain string fields as secret", () => {
		expect(_test.isSecretField("model")).toBe(false);
		expect(_test.isSecretField("base_url")).toBe(false);
	});

	it("infers types from concrete values", () => {
		expect(_test.inferType(true)).toBe("boolean");
		expect(_test.inferType(1)).toBe("integer");
		expect(_test.inferType(1.5)).toBe("number");
		expect(_test.inferType("x")).toBe("string");
		expect(_test.inferType([])).toBe("array");
		expect(_test.inferType({})).toBe("object");
	});
});
