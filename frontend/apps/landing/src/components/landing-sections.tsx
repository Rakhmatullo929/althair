import { brand } from "@workspace/brand";
import { BrandMark } from "@workspace/brand/mark";
import {
  Accordion,
  Badge,
  Card,
  Container,
  IconTile,
  Section,
  SectionHeading,
  buttonStyles,
  cn,
} from "@workspace/ui";
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Bot,
  Box,
  BriefcaseBusiness,
  Building2,
  CalendarCheck2,
  Car,
  Check,
  CheckCircle2,
  Clock3,
  ContactRound,
  GraduationCap,
  Handshake,
  Headphones,
  HeartPulse,
  Hotel,
  Inbox,
  Languages,
  LockKeyhole,
  MessageCircle,
  MessageSquareText,
  MessagesSquare,
  Phone,
  RefreshCcw,
  Scissors,
  ShieldCheck,
  ShoppingBag,
  SlidersHorizontal,
  Sparkles,
  Store,
  UserRound,
  UsersRound,
  Webhook,
  Workflow,
} from "lucide-react";
import { getTranslations } from "next-intl/server";
import type { ComponentType, ReactNode, SVGProps } from "react";
import { SiGmail, SiInstagram, SiTelegram, SiWhatsapp } from "react-icons/si";
import { EarlyAccessDialog } from "./early-access-dialog";
import { ScenarioDemo } from "./scenario-demo";

type CopyItem = { title: string; description: string };
type ChannelItem = {
  description: string;
  id: "instagram" | "telegram" | "whatsapp" | "phone" | "gmail" | "sms" | "web";
  name: string;
  status: CapabilityStatus;
};
type CapabilityStatus = "planned" | "beta" | "available";
type FaqItem = { id: string; question: string; answer: string };

const featureIcons = [Sparkles, MessagesSquare, ContactRound, Workflow];
const outcomeIcons = [Clock3, Inbox, RefreshCcw, Bot, CheckCircle2, Handshake];
const industryIcons = [
  Scissors,
  HeartPulse,
  ShoppingBag,
  GraduationCap,
  Car,
  Building2,
  Hotel,
  BriefcaseBusiness,
];
const securityIcons = [
  LockKeyhole,
  UsersRound,
  SlidersHorizontal,
  Headphones,
  ShieldCheck,
];

export async function LandingSections() {
  const [
    hero,
    product,
    channels,
    how,
    context,
    outcomes,
    industries,
    security,
    faq,
    cta,
  ] = await Promise.all([
    getTranslations("hero"),
    getTranslations("product"),
    getTranslations("channels"),
    getTranslations("how"),
    getTranslations("context"),
    getTranslations("outcomes"),
    getTranslations("industries"),
    getTranslations("security"),
    getTranslations("faq"),
    getTranslations("cta"),
  ]);

  const features = product.raw("features") as CopyItem[];
  const channelItems = channels.raw("items") as ChannelItem[];
  const steps = how.raw("steps") as CopyItem[];
  const contextItems = context.raw("items") as string[];
  const outcomeItems = outcomes.raw("items") as CopyItem[];
  const industryItems = industries.raw("items") as string[];
  const securityItems = security.raw("items") as CopyItem[];
  const faqItems = faq.raw("items") as FaqItem[];

  return (
    <>
      <section
        id="top"
        className="hero-atmosphere relative overflow-hidden pt-28 pb-20 sm:pt-36 sm:pb-28 lg:min-h-[820px] lg:pt-44"
      >
        <div
          className="dot-grid pointer-events-none absolute inset-0 opacity-55"
          aria-hidden="true"
        />
        <Container className="relative grid items-center gap-14 lg:grid-cols-[.9fr_1.1fr] lg:gap-10">
          <div className="max-w-2xl">
            <Badge>
              <Sparkles className="size-3.5" />
              {hero("badge")}
            </Badge>
            <h1 className="text-ink mt-6 text-4xl leading-[1.06] font-extrabold tracking-[-0.05em] text-balance sm:text-6xl lg:text-[4.6rem]">
              {hero("titleBefore")}{" "}
              <span className="text-primary">{hero("titleHighlight")}</span>
            </h1>
            <p className="text-secondary mt-6 max-w-xl text-lg leading-8 text-pretty sm:text-xl">
              {hero("description")}
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <EarlyAccessDialog
                label={hero("primary")}
                className="w-full sm:w-auto"
              />
              <a
                href="#how"
                className={buttonStyles({
                  variant: "secondary",
                  className: "w-full sm:w-auto",
                })}
              >
                {hero("secondary")}
                <ArrowRight className="size-4" />
              </a>
            </div>
            <p className="text-muted mt-4 flex items-center gap-2 text-sm">
              <Check className="text-primary size-4" />
              {hero("note")}
            </p>
          </div>
          <HeroVisual
            label={hero("visualLabel")}
            hub={hero("hub")}
            messages={hero.raw("messages") as { label: string; text: string }[]}
          />
        </Container>
      </section>

      <Section id="product">
        <Container>
          <SectionHeading
            eyebrow={product("eyebrow")}
            title={product("title")}
            description={product("description", { brand: brand.name })}
            align="center"
          />
          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {features.map((feature, index) => {
              const Icon = featureIcons[index] ?? Sparkles;
              return (
                <Card
                  className="group p-6 transition hover:-translate-y-1 hover:shadow-[0_16px_40px_rgba(16,24,40,.08)]"
                  key={feature.title}
                >
                  <IconTile className="group-hover:border-emerald-200 group-hover:bg-emerald-50">
                    <Icon className="text-primary size-5" />
                  </IconTile>
                  <h3 className="text-ink mt-5 text-lg font-bold">
                    {feature.title}
                  </h3>
                  <p className="text-secondary mt-2 text-sm leading-6">
                    {feature.description}
                  </p>
                </Card>
              );
            })}
          </div>
        </Container>
      </Section>

      <Section id="channels" className="bg-section border-y border-slate-100">
        <Container>
          <SectionHeading
            eyebrow={channels("eyebrow")}
            title={channels("title")}
            description={channels("description")}
          />
          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {channelItems.map((item) => (
              <Card
                key={item.id}
                className="p-5 transition hover:-translate-y-1 hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-3">
                  <ChannelTile id={item.id} />
                  <span className="border-border text-muted rounded-full border bg-slate-50 px-2.5 py-1 text-[10px] font-bold tracking-wide uppercase">
                    {channels(`status.${item.status}`)}
                  </span>
                </div>
                <h3 className="text-ink mt-5 font-bold">{item.name}</h3>
                <p className="text-secondary mt-2 text-sm leading-6">
                  {item.description}
                </p>
              </Card>
            ))}
          </div>
        </Container>
      </Section>

      <Section id="how">
        <Container>
          <div className="grid items-start gap-12 lg:grid-cols-[.72fr_1.28fr] lg:gap-16">
            <div>
              <SectionHeading
                eyebrow={how("eyebrow")}
                title={how("title")}
                description={how("description")}
              />
              <ol className="mt-9 space-y-6">
                {steps.map((step, index) => (
                  <li key={step.title} className="flex gap-4">
                    <span className="bg-primary grid size-8 shrink-0 place-items-center rounded-full text-sm font-bold text-white">
                      {index + 1}
                    </span>
                    <div>
                      <h3 className="text-ink font-bold">{step.title}</h3>
                      <p className="text-secondary mt-1 text-sm leading-6">
                        {step.description}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
            <CrmMockup copy={how.raw("mockup") as MockupCopy} />
          </div>
        </Container>
      </Section>

      <Section className="bg-section border-y border-slate-100">
        <Container>
          <SectionHeading
            eyebrow={(await getTranslations("scenario"))("eyebrow")}
            title={(await getTranslations("scenario"))("title")}
            description={(await getTranslations("scenario"))("description")}
            align="center"
          />
          <div className="mx-auto mt-10 max-w-4xl">
            <ScenarioDemo />
          </div>
        </Container>
      </Section>

      <Section>
        <Container className="grid items-center gap-12 lg:grid-cols-[.85fr_1.15fr] lg:gap-16">
          <SectionHeading
            eyebrow={context("eyebrow")}
            title={context("title")}
            description={context("description")}
          />
          <Card className="relative overflow-hidden p-6 sm:p-8">
            <div
              className="bg-primary-softer absolute inset-0"
              aria-hidden="true"
            />
            <div className="relative">
              <div className="flex items-center gap-3 border-b border-emerald-100 pb-5">
                <span className="bg-primary grid size-10 place-items-center rounded-xl text-white">
                  <LockKeyhole className="size-5" />
                </span>
                <p className="text-ink font-bold">{context("workspace")}</p>
              </div>
              <div className="mt-5 grid gap-2 sm:grid-cols-2">
                {contextItems.map((item, index) => (
                  <div
                    className="flex items-center gap-3 rounded-xl border border-emerald-100 bg-white/90 px-3.5 py-3 text-sm font-medium text-slate-700"
                    key={item}
                  >
                    {index === contextItems.length - 1 ? (
                      <Webhook className="text-primary size-4 shrink-0" />
                    ) : index % 3 === 0 ? (
                      <BookOpen className="text-primary size-4 shrink-0" />
                    ) : index % 3 === 1 ? (
                      <Box className="text-primary size-4 shrink-0" />
                    ) : (
                      <Languages className="text-primary size-4 shrink-0" />
                    )}
                    {item}
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </Container>
      </Section>

      <Section className="bg-section border-y border-slate-100">
        <Container>
          <SectionHeading
            eyebrow={outcomes("eyebrow")}
            title={outcomes("title")}
            align="center"
          />
          <div className="mt-12 grid gap-x-8 gap-y-9 sm:grid-cols-2 lg:grid-cols-3">
            {outcomeItems.map((item, index) => {
              const Icon = outcomeIcons[index] ?? CheckCircle2;
              return (
                <div className="flex gap-4" key={item.title}>
                  <IconTile>
                    <Icon className="text-primary size-5" />
                  </IconTile>
                  <div>
                    <h3 className="text-ink font-bold">{item.title}</h3>
                    <p className="text-secondary mt-1.5 text-sm leading-6">
                      {item.description}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </Container>
      </Section>

      <Section id="industries">
        <Container>
          <SectionHeading
            eyebrow={industries("eyebrow")}
            title={industries("title")}
            description={industries("description")}
            align="center"
          />
          <div className="mt-12 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {industryItems.map((item, index) => {
              const Icon = industryIcons[index] ?? Store;
              return (
                <Card
                  className="group min-h-40 p-5 transition hover:-translate-y-1 hover:border-emerald-200"
                  key={item}
                >
                  <span className="bg-primary-soft grid size-10 place-items-center rounded-xl">
                    <Icon className="text-primary size-5" />
                  </span>
                  <h3 className="text-ink mt-7 text-sm leading-5 font-bold sm:text-base">
                    {item}
                  </h3>
                </Card>
              );
            })}
          </div>
        </Container>
      </Section>

      <Section className="bg-slate-950 text-white">
        <Container className="grid gap-12 lg:grid-cols-[.8fr_1.2fr] lg:gap-20">
          <SectionHeading
            eyebrow={security("eyebrow")}
            title={security("title")}
            description={security("description")}
            className="[&_h2]:text-white [&_p:last-child]:text-slate-300"
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {securityItems.map((item, index) => {
              const Icon = securityIcons[index] ?? ShieldCheck;
              return (
                <div
                  key={item.title}
                  className={cn(
                    "rounded-2xl border border-white/10 bg-white/[.045] p-5",
                    index === securityItems.length - 1 && "sm:col-span-2",
                  )}
                >
                  <Icon className="size-5 text-emerald-400" />
                  <h3 className="mt-4 font-bold">{item.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-400">
                    {item.description}
                  </p>
                </div>
              );
            })}
          </div>
        </Container>
      </Section>

      <Section id="faq">
        <Container className="grid gap-10 lg:grid-cols-[.55fr_1fr] lg:gap-20">
          <SectionHeading eyebrow={faq("eyebrow")} title={faq("title")} />
          <Accordion items={faqItems} />
        </Container>
      </Section>

      <Section id="early-access" className="pt-0">
        <Container>
          <div className="relative overflow-hidden rounded-[28px] border border-emerald-100 bg-[#eaf8f1] px-5 py-14 text-center sm:px-12 sm:py-20">
            <div
              className="pointer-events-none absolute top-0 left-1/2 size-[420px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/60 blur-3xl"
              aria-hidden="true"
            />
            <div className="relative mx-auto max-w-3xl">
              <p className="eyebrow">{cta("eyebrow")}</p>
              <h2 className="text-ink mt-4 text-3xl font-extrabold tracking-[-0.04em] text-balance sm:text-5xl">
                {cta("title", { brand: brand.name })}
              </h2>
              <p className="text-secondary mx-auto mt-5 max-w-2xl leading-7">
                {cta("description")}
              </p>
              <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
                <EarlyAccessDialog label={cta("primary")} />
                <a
                  href="#how"
                  className={buttonStyles({ variant: "secondary" })}
                >
                  {cta("secondary")}
                  <ArrowRight className="size-4" />
                </a>
              </div>
              <p className="text-muted mx-auto mt-5 max-w-xl text-xs leading-5">
                {cta("note")}
              </p>
            </div>
          </div>
        </Container>
      </Section>
    </>
  );
}

function HeroVisual({
  label,
  hub,
  messages,
}: {
  label: string;
  hub: string;
  messages: { label: string; text: string }[];
}) {
  const positions = [
    "top-[8%] left-[1%]",
    "top-[4%] right-[1%]",
    "bottom-[7%] left-[2%]",
    "right-[1%] bottom-[3%]",
  ];
  return (
    <div
      className="relative mx-auto h-[470px] w-full max-w-[610px] sm:h-[560px]"
      role="img"
      aria-label={label}
    >
      <div className="absolute inset-[13%] rounded-full border border-emerald-100 bg-white/70 shadow-[0_30px_80px_rgba(8,166,106,.10)]" />
      <svg
        viewBox="0 0 600 560"
        className="absolute inset-0 size-full"
        aria-hidden="true"
      >
        <path
          className="connector"
          d="M300 280 105 92M300 280 500 90M300 280 78 428M300 280 520 440M300 280 300 34M300 280 300 530"
          stroke="#08A66A"
          strokeOpacity=".35"
          strokeWidth="2"
          fill="none"
        />
      </svg>
      <div className="hub-pulse absolute top-1/2 left-1/2 z-10 -translate-x-1/2 -translate-y-1/2">
        <div className="grid size-24 place-items-center rounded-[28px] border-8 border-white bg-emerald-50 shadow-[0_20px_60px_rgba(8,166,106,.24)] sm:size-28">
          <BrandMark className="size-16 sm:size-20" />
          <span className="sr-only">{hub}</span>
        </div>
      </div>
      <HeroChannel className="top-[5%] left-[45%]" color="text-[#E94235]">
        <SiGmail />
      </HeroChannel>
      <HeroChannel className="top-[29%] right-[8%]" color="text-[#25D366]">
        <SiWhatsapp />
      </HeroChannel>
      <HeroChannel className="right-[17%] bottom-[13%]" color="text-[#229ED9]">
        <SiTelegram />
      </HeroChannel>
      <HeroChannel className="bottom-[4%] left-[44%]" color="text-primary">
        <MessageSquareText />
      </HeroChannel>
      <HeroChannel className="bottom-[16%] left-[12%]" color="text-primary">
        <Phone />
      </HeroChannel>
      <HeroChannel className="top-[27%] left-[8%]" color="text-[#E4405F]">
        <SiInstagram />
      </HeroChannel>
      {messages.map((message, index) => (
        <div
          className={cn(
            "float-card absolute z-20 max-w-[185px] rounded-2xl border border-slate-200/80 bg-white p-3 shadow-[0_16px_45px_rgba(16,24,40,.10)] sm:max-w-[215px] sm:p-4",
            positions[index],
            index > 1 && "hidden sm:block",
          )}
          key={message.label}
        >
          <p className="text-primary text-[10px] font-bold tracking-wide uppercase">
            {message.label}
          </p>
          <p className="text-ink mt-1 text-xs leading-5 font-medium sm:text-sm">
            {message.text}
          </p>
        </div>
      ))}
    </div>
  );
}

function HeroChannel({
  children,
  className,
  color,
}: {
  children: ReactNode;
  className: string;
  color: string;
}) {
  return (
    <span
      className={cn(
        "absolute z-10 grid size-12 place-items-center rounded-2xl border border-slate-200 bg-white text-xl shadow-lg sm:size-14 sm:text-2xl",
        className,
        color,
      )}
      aria-hidden="true"
    >
      {children}
    </span>
  );
}

function ChannelTile({ id }: { id: ChannelItem["id"] }) {
  const content: Record<
    ChannelItem["id"],
    { icon: ReactNode; className: string }
  > = {
    instagram: { icon: <SiInstagram />, className: "text-[#E4405F]" },
    telegram: { icon: <SiTelegram />, className: "text-[#229ED9]" },
    whatsapp: { icon: <SiWhatsapp />, className: "text-[#25D366]" },
    gmail: { icon: <SiGmail />, className: "text-[#E94235]" },
    phone: { icon: <Phone />, className: "text-primary" },
    sms: { icon: <MessageCircle />, className: "text-primary" },
    web: { icon: <MessageSquareText />, className: "text-primary" },
  };
  return (
    <IconTile className={cn("text-xl", content[id].className)}>
      {content[id].icon}
    </IconTile>
  );
}

type MockupCopy = {
  inbox: string;
  customers: string;
  appointments: string;
  analytics: string;
  conversation: string;
  aiMode: string;
  customer: string;
  customerInfo: string;
  newLead: string;
  booking: string;
  handoff: string;
  history: string;
  messages: string[];
};

function CrmMockup({ copy }: { copy: MockupCopy }) {
  return (
    <div className="border-border overflow-hidden rounded-[22px] border bg-white shadow-[0_28px_80px_rgba(16,24,40,.10)]">
      <div className="flex h-10 items-center gap-1.5 border-b border-slate-100 bg-slate-50 px-4">
        <span className="size-2.5 rounded-full bg-red-300" />
        <span className="size-2.5 rounded-full bg-amber-300" />
        <span className="size-2.5 rounded-full bg-emerald-300" />
      </div>
      <div className="grid min-h-[480px] grid-cols-[64px_1fr] sm:grid-cols-[150px_1fr] xl:grid-cols-[140px_1fr_170px]">
        <aside className="border-r border-slate-100 bg-slate-50/70 p-3">
          <BrandMark className="size-8" />
          <nav className="mt-6 space-y-1" aria-label={copy.inbox}>
            {[
              [Inbox, copy.inbox, true],
              [ContactRound, copy.customers],
              [CalendarCheck2, copy.appointments],
              [BarChart3, copy.analytics],
            ].map(([Icon, label, active]) => {
              const NavIcon = Icon as ComponentType<SVGProps<SVGSVGElement>>;
              return (
                <div
                  className={cn(
                    "flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-medium",
                    active
                      ? "bg-emerald-50 text-emerald-700"
                      : "text-slate-500",
                  )}
                  key={String(label)}
                >
                  <NavIcon className="size-4 shrink-0" />
                  <span className="hidden sm:block">{String(label)}</span>
                </div>
              );
            })}
          </nav>
        </aside>
        <div className="min-w-0">
          <div className="flex min-h-16 items-center justify-between gap-2 border-b border-slate-100 px-4">
            <div>
              <p className="text-ink text-sm font-bold">{copy.conversation}</p>
              <p className="text-primary mt-0.5 flex items-center gap-1 text-[10px] font-semibold">
                <span className="size-1.5 rounded-full bg-emerald-500" />
                {copy.aiMode}
              </p>
            </div>
            <span className="border-border rounded-lg border p-2">
              <Bot className="text-primary size-4" />
            </span>
          </div>
          <div className="bg-section flex min-h-[340px] flex-col justify-end gap-3 p-4">
            {copy.messages.map((message, index) => (
              <div
                className={cn(
                  "max-w-[85%] rounded-2xl px-3 py-2.5 text-xs leading-5 shadow-sm",
                  index === 1
                    ? "bg-primary ml-auto rounded-br-sm text-white"
                    : "rounded-bl-sm bg-white text-slate-700",
                )}
                key={message}
              >
                {message}
              </div>
            ))}
            <div className="mt-2 flex flex-wrap gap-2">
              <span className="rounded-full bg-blue-50 px-2 py-1 text-[10px] font-bold text-blue-700">
                {copy.newLead}
              </span>
              <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-bold text-emerald-700">
                {copy.booking}
              </span>
            </div>
          </div>
          <div className="flex min-h-60 items-center gap-2 border-t border-slate-100 px-3">
            <div className="h-9 flex-1 rounded-lg border border-slate-200 bg-slate-50" />
            <button
              className="bg-primary grid size-9 place-items-center rounded-lg text-white"
              aria-label={copy.handoff}
            >
              <ArrowRight className="size-4" />
            </button>
          </div>
        </div>
        <aside className="hidden border-l border-slate-100 p-4 xl:block">
          <div className="grid size-10 place-items-center rounded-full bg-emerald-50">
            <UserRound className="text-primary size-5" />
          </div>
          <p className="text-ink mt-3 text-sm font-bold">{copy.customer}</p>
          <p className="text-muted text-[10px]">{copy.history}</p>
          <div className="mt-6 border-t border-slate-100 pt-4">
            <p className="text-muted text-[10px] font-bold tracking-wide uppercase">
              {copy.customerInfo}
            </p>
            <p className="text-secondary mt-3 text-xs">{copy.booking}</p>
          </div>
          <button className="mt-6 w-full rounded-lg border border-slate-200 px-2 py-2 text-[10px] font-bold text-slate-600">
            {copy.handoff}
          </button>
        </aside>
      </div>
    </div>
  );
}
