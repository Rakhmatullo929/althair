import { describe, expect, it } from "vitest";
import { buildRegistrationSchema } from "@/lib/validation";

const schema = buildRegistrationSchema({
  emailInvalid: "email",
  passwordRequired: "password",
  passwordLength: "length",
  nameRequired: "name",
  companyRequired: "company",
});

describe("registration validation", () => {
  it("rejects invalid identity and company values before an API call", () => {
    expect(
      schema.safeParse({
        first_name: "A",
        last_name: "",
        email: "not-an-email",
        password: "short",
        organization_name: "",
        industry: "generic",
      }).success,
    ).toBe(false);
  });

  it("accepts a complete registration payload", () => {
    expect(
      schema.safeParse({
        first_name: "Aziza",
        last_name: "Karimova",
        email: "aziza@example.test",
        password: "long-password",
        organization_name: "Mehr Clinic",
        industry: "healthcare",
      }).success,
    ).toBe(true);
  });
});
