import { brand } from "@workspace/brand";
import { BrandMark } from "@workspace/brand/mark";
import {
  Accordion,
  Badge,
  Container,
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
import { RobotHeadModel } from "./robot-head-model";
import { ScenarioDemo } from "./scenario-demo";

type CopyItem = { title: string; description: string };
type CapabilityStatus = "planned" | "beta" | "available";
type ChannelItem = {
  description: string;
  id: "instagram" | "telegram" | "whatsapp" | "phone" | "gmail" | "sms" | "web";
  name: string;
  status: CapabilityStatus;
};
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
    scenario,
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
    getTranslations("scenario"),
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
      <section id="top" className="althair-hero">
        <div className="hero-noise" aria-hidden="true" />
        <Container className="hero-layout">
          <div className="hero-copy">
            <p className="brand-kicker">{brand.name} / AI front office</p>
            <Badge className="hero-badge">
              <span className="status-beacon" />
              {hero("badge")}
            </Badge>
            <h1 className="hero-title">
              <span className="hero-title-line">
                <span className="hero-title-ink">{hero("titleBefore")}</span>
              </span>
              <span className="hero-title-line">
                <span className="hero-title-highlight">
                  {hero("titleHighlight")}
                </span>
              </span>
            </h1>
            <p className="hero-description">{hero("description")}</p>
            <div className="hero-actions">
              <EarlyAccessDialog label={hero("primary")} />
              <a
                href="#how"
                className={buttonStyles({
                  variant: "secondary",
                  className: "hero-secondary-action",
                })}
              >
                {hero("secondary")}
                <ArrowRight className="size-4" />
              </a>
            </div>
            <p className="hero-note">
              <Check className="size-4" />
              {hero("note")}
            </p>
          </div>
          <RobotHeadModel
            label={hero("visualLabel")}
            hub={hero("hub")}
            messages={hero.raw("messages") as { label: string; text: string }[]}
          />
        </Container>
        <div className="signal-rail" aria-label={channels("eyebrow")}>
          <Container className="signal-rail-inner">
            <span className="signal-rail-label">{channels("eyebrow")}</span>
            {channelItems.slice(0, 5).map((item) => (
              <span className="signal-rail-item" key={item.id}>
                <span />
                {item.name}
              </span>
            ))}
            <span className="signal-rail-item">
              <span />
              CRM
            </span>
          </Container>
        </div>
      </section>

      <Section id="product" className="product-section">
        <Container>
          <div className="section-heading-grid">
            <SectionHeading
              eyebrow={product("eyebrow")}
              title={product("title")}
            />
            <p className="section-lead">
              {product("description", { brand: brand.name })}
            </p>
          </div>
          <div className="capability-list">
            {features.map((feature, index) => {
              const Icon = featureIcons[index] ?? Sparkles;
              return (
                <article
                  className="capability-row motion-item"
                  key={feature.title}
                >
                  <span className="capability-icon" aria-hidden="true">
                    <Icon />
                  </span>
                  <h3>{feature.title}</h3>
                  <p>{feature.description}</p>
                  <ArrowRight aria-hidden="true" />
                </article>
              );
            })}
          </div>
        </Container>
      </Section>

      <section id="channels" className="channel-section section-space">
        <Container className="channel-layout">
          <div className="channel-intro">
            <SectionHeading
              eyebrow={channels("eyebrow")}
              title={channels("title")}
              description={channels("description")}
              className="section-heading-on-dark"
            />
            <p className="signal-signature">{brand.shortName} / signal map</p>
          </div>
          <div className="channel-ledger">
            {channelItems.map((item) => (
              <article className="channel-row motion-item" key={item.id}>
                <ChannelTile id={item.id} />
                <div>
                  <h3>{item.name}</h3>
                  <p>{item.description}</p>
                </div>
                <span className="channel-status">
                  {channels(`status.${item.status}`)}
                </span>
              </article>
            ))}
          </div>
        </Container>
      </section>

      <Section id="how" className="process-section">
        <Container className="process-layout">
          <div className="process-intro">
            <SectionHeading
              eyebrow={how("eyebrow")}
              title={how("title")}
              description={how("description")}
            />
          </div>
          <div>
            <ol className="process-list">
              {steps.map((step, index) => (
                <li className="motion-item" key={step.title}>
                  <span className="process-index">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div>
                    <h3>{step.title}</h3>
                    <p>{step.description}</p>
                  </div>
                </li>
              ))}
            </ol>
            <CrmMockup copy={how.raw("mockup") as MockupCopy} />
          </div>
        </Container>
      </Section>

      <Section className="scenario-section">
        <Container>
          <div className="section-heading-grid">
            <SectionHeading
              eyebrow={scenario("eyebrow")}
              title={scenario("title")}
            />
            <p className="section-lead">{scenario("description")}</p>
          </div>
          <div className="scenario-shell">
            <ScenarioDemo />
          </div>
        </Container>
      </Section>

      <Section className="knowledge-section">
        <Container>
          <div className="knowledge-layout">
            <SectionHeading
              eyebrow={context("eyebrow")}
              title={context("title")}
              description={context("description")}
            />
            <div className="knowledge-console">
              <div className="knowledge-console-head">
                <span className="knowledge-lock">
                  <LockKeyhole />
                </span>
                <div>
                  <p>{context("workspace")}</p>
                  <span>{brand.name} / private context</span>
                </div>
                <span className="console-live">live</span>
              </div>
              <div className="knowledge-cloud">
                {contextItems.map((item, index) => (
                  <span className="motion-item" key={item}>
                    {index === contextItems.length - 1 ? (
                      <Webhook />
                    ) : index % 3 === 0 ? (
                      <BookOpen />
                    ) : index % 3 === 1 ? (
                      <Box />
                    ) : (
                      <Languages />
                    )}
                    {item}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <div className="outcome-heading">
            <p className="eyebrow">{outcomes("eyebrow")}</p>
            <h2>{outcomes("title")}</h2>
          </div>
          <div className="outcome-grid">
            {outcomeItems.map((item, index) => {
              const Icon = outcomeIcons[index] ?? CheckCircle2;
              return (
                <article className="motion-item" key={item.title}>
                  <Icon aria-hidden="true" />
                  <div>
                    <h3>{item.title}</h3>
                    <p>{item.description}</p>
                  </div>
                </article>
              );
            })}
          </div>
        </Container>
      </Section>

      <Section id="industries" className="industry-section">
        <Container>
          <div className="section-heading-grid">
            <SectionHeading
              eyebrow={industries("eyebrow")}
              title={industries("title")}
            />
            <p className="section-lead">{industries("description")}</p>
          </div>
          <div className="industry-ledger">
            {industryItems.map((item, index) => {
              const Icon = industryIcons[index] ?? Store;
              return (
                <article className="motion-item" key={item}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <Icon aria-hidden="true" />
                  <h3>{item}</h3>
                </article>
              );
            })}
          </div>
        </Container>
      </Section>

      <section className="trust-section section-space">
        <Container className="trust-layout">
          <SectionHeading
            eyebrow={security("eyebrow")}
            title={security("title")}
            description={security("description")}
          />
          <div className="trust-list">
            {securityItems.map((item, index) => {
              const Icon = securityIcons[index] ?? ShieldCheck;
              return (
                <article className="motion-item" key={item.title}>
                  <span>
                    <Icon aria-hidden="true" />
                  </span>
                  <div>
                    <h3>{item.title}</h3>
                    <p>{item.description}</p>
                  </div>
                </article>
              );
            })}
          </div>
        </Container>
      </section>

      <Section id="faq" className="faq-section">
        <Container className="faq-layout">
          <div>
            <SectionHeading eyebrow={faq("eyebrow")} title={faq("title")} />
            <p className="faq-signature">{brand.name} / FAQ</p>
          </div>
          <Accordion items={faqItems} />
        </Container>
      </Section>

      <Section id="early-access" className="final-section">
        <Container>
          <div className="final-cta">
            <BrandMark className="final-mark" aria-hidden="true" />
            <div className="final-cta-content">
              <p className="eyebrow">{cta("eyebrow")}</p>
              <h2>{cta("title", { brand: brand.name })}</h2>
              <p>{cta("description")}</p>
              <div className="final-actions">
                <EarlyAccessDialog label={cta("primary")} />
                <a
                  href="#how"
                  className={buttonStyles({
                    variant: "secondary",
                    className: "final-secondary-action",
                  })}
                >
                  {cta("secondary")}
                  <ArrowRight className="size-4" />
                </a>
              </div>
              <small>{cta("note")}</small>
            </div>
          </div>
        </Container>
      </Section>
    </>
  );
}

function ChannelTile({ id }: { id: ChannelItem["id"] }) {
  const content: Record<
    ChannelItem["id"],
    { icon: ReactNode; className: string }
  > = {
    instagram: { icon: <SiInstagram />, className: "instagram" },
    telegram: { icon: <SiTelegram />, className: "telegram" },
    whatsapp: { icon: <SiWhatsapp />, className: "whatsapp" },
    gmail: { icon: <SiGmail />, className: "gmail" },
    phone: { icon: <Phone />, className: "phone" },
    sms: { icon: <MessageCircle />, className: "sms" },
    web: { icon: <MessageSquareText />, className: "web" },
  };
  return (
    <span
      className={cn("channel-icon", content[id].className)}
      aria-hidden="true"
    >
      {content[id].icon}
    </span>
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
    <div className="crm-window">
      <div className="crm-window-bar">
        <div>
          <span />
          <span />
          <span />
        </div>
        <p>{brand.name} / workspace</p>
      </div>
      <div className="crm-layout">
        <aside className="crm-sidebar">
          <BrandMark className="crm-brand-mark" />
          <nav aria-label={copy.inbox}>
            {[
              [Inbox, copy.inbox, true],
              [ContactRound, copy.customers],
              [CalendarCheck2, copy.appointments],
              [BarChart3, copy.analytics],
            ].map(([Icon, label, active]) => {
              const NavIcon = Icon as ComponentType<SVGProps<SVGSVGElement>>;
              return (
                <div className={cn(active && "active")} key={String(label)}>
                  <NavIcon />
                  <span>{String(label)}</span>
                </div>
              );
            })}
          </nav>
        </aside>
        <div className="crm-conversation">
          <header>
            <div>
              <p>{copy.conversation}</p>
              <span>
                <i />
                {copy.aiMode}
              </span>
            </div>
            <Bot />
          </header>
          <div className="crm-messages">
            {copy.messages.map((message, index) => (
              <p className={index === 1 ? "ai" : "client"} key={message}>
                {message}
              </p>
            ))}
            <div className="crm-tags">
              <span>{copy.newLead}</span>
              <span>{copy.booking}</span>
            </div>
          </div>
          <footer>
            <span />
            <button aria-label={copy.handoff}>
              <ArrowRight />
            </button>
          </footer>
        </div>
        <aside className="crm-customer">
          <span className="crm-avatar">
            <UserRound />
          </span>
          <p>{copy.customer}</p>
          <small>{copy.history}</small>
          <div>
            <span>{copy.customerInfo}</span>
            <p>{copy.booking}</p>
          </div>
          <button>{copy.handoff}</button>
        </aside>
      </div>
    </div>
  );
}
