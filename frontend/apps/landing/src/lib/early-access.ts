import { z } from "zod";

const contactPattern = /^(?:[^\s@]+@[^\s@]+\.[^\s@]+|[+]?\d[\d\s().-]{6,20})$/;

export const earlyAccessSchema = z.object({
  fullName: z.string().trim().min(2).max(100),
  companyName: z.string().trim().min(2).max(120),
  contact: z.string().trim().min(5).max(160).regex(contactPattern),
  industry: z.string().trim().min(1).max(80),
  preferredChannel: z.string().trim().min(1).max(40),
  note: z.string().trim().max(1000).optional().default(""),
  consent: z.literal(true),
  website: z.string().max(0).optional().default(""),
  startedAt: z.number().int().positive(),
  locale: z.enum(["ru", "uz", "en"]),
});

export type EarlyAccessInput = z.infer<typeof earlyAccessSchema>;

export type EarlyAccessResponse =
  | { ok: true; code: "DELIVERED" }
  | {
      ok: false;
      code: "INVALID" | "RATE_LIMITED" | "DEMO_MODE" | "DELIVERY_FAILED";
      fieldErrors?: Record<string, string[]>;
    };
