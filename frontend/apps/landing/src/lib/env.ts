import { z } from "zod";

const optionalValue = <T extends z.ZodType>(schema: T) =>
  z.preprocess(
    (value) =>
      typeof value === "string" && value.trim() === "" ? undefined : value,
    schema.optional(),
  );

const serverEnvSchema = z.object({
  LEAD_WEBHOOK_URL: optionalValue(z.url()),
  LEAD_WEBHOOK_SECRET: optionalValue(z.string().min(8).max(512)),
});

/** Server-only environment validation. Import this module only from Route Handlers. */
export const serverEnv = serverEnvSchema.parse({
  LEAD_WEBHOOK_URL: process.env.LEAD_WEBHOOK_URL,
  LEAD_WEBHOOK_SECRET: process.env.LEAD_WEBHOOK_SECRET,
});
