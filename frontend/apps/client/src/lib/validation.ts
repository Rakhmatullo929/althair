import { z } from "zod";

type AuthValidationMessages = {
  emailInvalid: string;
  passwordRequired: string;
  passwordLength: string;
  nameRequired: string;
  companyRequired: string;
};

export function buildLoginSchema(messages: AuthValidationMessages) {
  return z.object({
    email: z.email(messages.emailInvalid),
    password: z.string().min(1, messages.passwordRequired),
  });
}

export function buildRegistrationSchema(messages: AuthValidationMessages) {
  return z.object({
    first_name: z.string().trim().min(2, messages.nameRequired),
    last_name: z.string().trim(),
    email: z.email(messages.emailInvalid),
    password: z.string().min(10, messages.passwordLength),
    organization_name: z.string().trim().min(2, messages.companyRequired),
    industry: z.string(),
  });
}
