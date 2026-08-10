import { brand } from "@workspace/brand";
import { BrandMark } from "@workspace/brand/mark";
import {
  Accordion,
  Container,
  Section,
  SectionHeading,
  buttonStyles,
  cn,
} from "@workspace/ui";
import {
  ArrowRight,
  BookOpen,
  Bot,
  BriefcaseBusiness,
  Building2,
  Car,
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
  UsersRound,
  Workflow,
} from "lucide-react";
import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { SiGmail, SiInstagram, SiTelegram, SiWhatsapp } from "react-icons/si";
import { CinematicJourney } from "./cinematic-journey";
import { EarlyAccessDialog } from "./early-access-dialog";
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
type StageMeta = { status: string; proof: string[] };
type SceneCopy = {
  identityKicker: string;
  identityProof: string[];
  identityStatus: string;
  identityStep: string;
  loading: string;
  orbitCue: string;
  orbitLabel: string;
  scrollCue: string;
  stageKicker: string;
  state: string;
  telemetry: string;
};
type ContextGroup = { title: string; items: string[] };
type ContextDecision = { label: string; status: string; detail: string };

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
const knowledgeIcons = [BookOpen, Languages, Workflow];

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
  const contextGroups = context.raw("groups") as ContextGroup[];
  const contextDecision = context.raw("decision") as ContextDecision;
  const outcomeItems = outcomes.raw("items") as CopyItem[];
  const industryItems = industries.raw("items") as string[];
  const securityItems = security.raw("items") as CopyItem[];
  const faqItems = faq.raw("items") as FaqItem[];

  return (
    <>
      <CinematicJourney
        channelEyebrow={channels("eyebrow")}
        channelNames={channelItems.map((item) => item.name)}
        hero={{
          badge: hero("badge"),
          description: hero("description"),
          hub: hero("hub"),
          identityTitle: hero("identityTitle"),
          messages: hero.raw("messages") as {
            label: string;
            text: string;
          }[],
          note: hero("note"),
          primary: hero("primary"),
          scene: hero.raw("scene") as SceneCopy,
          secondary: hero("secondary"),
          titleBefore: hero("titleBefore"),
          titleHighlight: hero("titleHighlight"),
          visualLabel: hero("visualLabel"),
        }}
        how={{
          description: how("description"),
          eyebrow: how("eyebrow"),
          stageMeta: how.raw("stageMeta") as StageMeta[],
          steps,
          title: how("title"),
        }}
      />

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
            <p className="signal-signature">{channels("signature")}</p>
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
                  <span>{context("workspaceCaption")}</span>
                </div>
                <span className="console-live">
                  {context("workspaceStatus")}
                </span>
              </div>
              <div className="knowledge-groups">
                {contextGroups.map((group, index) => {
                  const Icon = knowledgeIcons[index] ?? BookOpen;
                  return (
                    <article
                      className="knowledge-group motion-item"
                      key={group.title}
                    >
                      <div className="knowledge-group-title">
                        <span aria-hidden="true">
                          <Icon />
                        </span>
                        <h3>{group.title}</h3>
                      </div>
                      <div className="knowledge-chips">
                        {group.items.map((item) => (
                          <span key={item}>{item}</span>
                        ))}
                      </div>
                    </article>
                  );
                })}
                <div className="knowledge-decision motion-item">
                  <small>{contextDecision.label}</small>
                  <div>
                    <CheckCircle2 aria-hidden="true" />
                    <strong>{contextDecision.status}</strong>
                  </div>
                  <p>{contextDecision.detail}</p>
                </div>
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
