---
created: 2026-05-14
tags: [operations, skills, claude-code, auto-trigger]
---

# Available Claude Code Skills — full roster + auto-trigger notes

> **What this is.** Every Skill currently registered in Claude Code for this
> workstation. Skills are discrete, named units of behaviour the assistant
> can invoke via the `Skill` tool. They are NOT generic prompts — each has
> its own metadata, working directory, and trigger conditions.
>
> **Why this lives in BRAIN.** When the user asks "what can you do?" or
> wonders why a particular Skill fired during a task, this is the index.
> Updated 2026-05-14.

---

## How auto-invocation works

1. **Slash commands** — when the user types `/<skill-name>` (e.g. `/gsd`,
   `/review`, `/security-review`), the matching skill is invoked directly.
2. **Pattern-based auto-trigger** — some skills declare TRIGGER conditions
   in their description (e.g. `claude-api` triggers when a file imports
   `anthropic` or `@anthropic-ai/sdk`). These fire WITHOUT the user typing
   the slash command, the moment the assistant detects the trigger.
3. **Tool-routing auto-trigger** — task-shape skills (`tdd-guide`,
   `code-reviewer`, `security-reviewer`, `e2e-runner`) are surfaced
   proactively by the assistant when the task shape matches.
4. **Manual invocation only** — the long tail of domain skills
   (bioinformatics, supplier-specific cloud SDKs, niche frameworks) only
   fire when the user explicitly names them.

The Skill tool refuses to invoke a name that isn't in the system-reminder
allowlist — meaning the assistant CAN'T guess a skill name from training
data. The roster below is the literal source of truth.

---

## Core Claude Code workflow (auto-trigger ON for many)

These are the day-to-day skills the assistant reaches for without being asked:

| Skill | When it fires |
|---|---|
| `gsd` | User types `/gsd …` (Get Shit Done — routes to a sub-workflow: plan-phase, execute-phase, verify-phase, ship, review, debug, …) |
| `plan` | User asks for an implementation strategy or "plan first" before code |
| `write-plan` | User explicitly asks for a written-out plan document |
| `executing-plans` / `execute-plan` | Plan handoff — runs the steps |
| `verify` / `verification-loop` / `verification-before-completion` | Pre-completion gate |
| `review` / `code-review` / `code-reviewer` | After non-trivial code changes |
| `comprehensive-review-full-review` | Full multi-perspective review pass |
| `commit` / `pr-writer` / `create-pr` / `release` | Git workflow |
| `address-github-comments` | Reviewer left comments on a PR |
| `tdd-guide` / `tdd` / `tdd-workflows-tdd-cycle` (red/green/refactor variants) | Bug fix or new feature path |
| `debugging-strategies` / `systematic-debugging` / `debug-buttercup` | Stuck on a bug |
| `find-bugs` / `bug-hunter` | Open-ended bug audit |
| `simplify` / `simplify-code` / `code-simplifier` | Refactor pass |
| `ask` / `ask-questions-if-underspecified` | Underspecified user request |
| `cancel` | User wants to abort an in-flight workflow |

## Subagent orchestration

These coordinate multiple parallel agents:

`dispatching-parallel-agents`, `parallel-agents`, `subagent-driven-development`,
`multi-agent-brainstorming`, `multi-agent-patterns`, `agent-orchestration-improve-agent`,
`agent-orchestration-multi-agent-optimize`, `agent-orchestrator`, `agent-manager-skill`,
`agent-designer`, `agent-workflow-designer`, `agent-framework-azure-ai-py`,
`agents-md`, `agent-memory-mcp`, `agent-memory-systems`, `hierarchical-agent-memory`,
`hosted-agents`, `hosted-agents-v2-py`.

## Skill authorship

`skill`, `skill-create`, `skill-creator`, `skill-creator-ms`, `skill-developer`,
`skill-writer`, `skill-improver`, `skill-tester`, `skill-installer`,
`skill-scanner`, `skill-sentinel`, `skill-seekers`, `skill-router`,
`skill-check`, `skill-rails-upgrade`, `skill-security-auditor`,
`project-skill-audit`, `audit-skills`, `tool-design`, `agent-tool-builder`,
`personal-tool-builder`, `mcp-builder`, `mcp-builder-ms`, `mcp-server-builder`,
`mcp-setup`, `claude-code-setup:claude-automation-recommender`,
`agent-sdk-dev:new-sdk-app`, `skill-creator:skill-creator`.

## Senior-reviewer skills (multi-perspective)

`senior-architect`, `senior-frontend`, `senior-fullstack`, `senior-backend`,
`senior-prompt-engineer`, `senior-data-engineer`, `senior-data-scientist`,
`senior-ml-engineer`, `senior-computer-vision`, `senior-devops`,
`senior-secops`, `senior-security`, `senior-qa`.

## Anthropic-specific (auto-trigger ON for many)

| Skill | Trigger |
|---|---|
| `claude-api` | File imports `anthropic` / `@anthropic-ai/sdk` · Anthropic SDK feature changes · prompt caching / thinking / batch / tool use / citations work |
| `claude-code-expert` / `claude-code-guide` / `claude-ally-health` | Claude Code questions |
| `prompt-caching` | Cacheable-system-prompt work in Claude API |
| `prompt-engineer` / `prompt-engineering` / `prompt-engineering-patterns` / `prompt-library` / `prompt-optimize` / `enhance-prompt` | Prompt design |
| `claude-monitor` / `claude-settings-audit` / `claude-speed-reader` / `claude-d3js-skill` / `claude-in-chrome-troubleshooting` | Misc Claude tooling |

## Cloud SDKs (manual-trigger)

Massive auto-generated set per cloud:

**Azure (~80 skills)** — `azure-ai-*`, `azure-appconfiguration-*`,
`azure-communication-*`, `azure-cosmos-*`, `azure-data-tables-*`,
`azure-eventgrid-*`, `azure-eventhub-*`, `azure-functions`,
`azure-identity-*`, `azure-keyvault-*`, `azure-maps-*`, `azure-messaging-*`,
`azure-mgmt-*`, `azure-monitor-*`, `azure-postgres-ts`, `azure-resource-manager-*`,
`azure-search-documents-*`, `azure-security-keyvault-*`, `azure-servicebus-*`,
`azure-speech-to-text-rest-py`, `azure-storage-*`, `azure-web-pubsub-ts`,
`azd-deployment`.

**AWS** — `aws-cost-cleanup`, `aws-cost-optimizer`, `aws-penetration-testing`,
`aws-serverless`, `aws-skills`, `aws-solution-architect`, `cdk-patterns`,
`cloudformation-best-practices`, `cloud-architect`, `cost-optimization`,
`hybrid-cloud-architect`, `hybrid-cloud-networking`, `multi-cloud-architecture`.

**GCP** — `gcp-cloud-run`.

**Cloudflare** — `cloudflare-workers-expert`.

**Vercel** — `vercel-ai-sdk-expert`, `vercel-automation`, `vercel-deployment`.

**Supabase** — `nextjs-supabase-auth`, `supabase-automation`.

**Neon / Postgres** — `neon-postgres`, `using-neon`, `postgresql`,
`postgres-best-practices`, `postgres-patterns`, `postgresql-optimization`,
`drizzle-orm-expert`, `prisma-expert`, `sql-pro`, `sql-optimization-patterns`,
`sql-injection-testing`, `claimable-postgres`.

**Kubernetes / DevOps** — `kubernetes-architect`, `kubernetes-deployment`,
`k8s-manifest-generator`, `k8s-security-policies`, `helm-chart-scaffolding`,
`istio-traffic-management`, `linkerd-patterns`, `service-mesh-expert`,
`service-mesh-observability`, `gitops-workflow`, `terraform-aws-modules`,
`terraform-infrastructure`, `terraform-module-library`, `terraform-skill`,
`terraform-specialist`, `cicd-automation-workflow-automate`,
`ci-cd-pipeline-builder`, `circleci-automation`, `gitlab-ci-patterns`,
`github-actions-templates`, `github-workflow-automation`, `gha-security-review`,
`mlops-engineer`, `ml-pipeline-workflow`, `machine-learning-ops-ml-pipeline`,
`dx-optimizer`, `dependency-management-deps-audit`, `dependency-upgrade`,
`dependency-auditor`, `release-manager`, `appdeploy`, `devops-deploy`,
`devops-troubleshooter`, `deployment-engineer`, `deployment-pipeline-design`,
`deployment-procedures`, `deployment-validation-config-validate`.

## Language patterns + reviewers

**Python** — `python-pro`, `python-patterns`, `python-packaging`,
`python-development-python-scaffold`, `python-fastapi-development`,
`python-performance-optimization`, `python-review`, `python-testing`,
`python-testing-patterns`, `async-python-patterns`, `fastapi-pro`,
`fastapi-router-py`, `fastapi-templates`, `pydantic-ai`, `pydantic-models-py`,
`django-access-review`, `django-patterns`, `django-perf-review`, `django-pro`,
`django-security`, `django-tdd`, `django-verification`, `uv-package-manager`,
`hypothesis-generation`.

**TypeScript / JS / Node** — `typescript-pro`, `typescript-expert`,
`typescript-advanced-types`, `javascript-mastery`, `javascript-pro`,
`javascript-testing-patterns`, `javascript-typescript-typescript-scaffold`,
`modern-javascript-patterns`, `nodejs-backend-patterns`, `nodejs-best-practices`,
`bun-development`, `pnpm`, `tsdown`, `vite`, `vitest`, `vitepress`,
`zod-validation-expert`, `esm`, `varlock`, `varlock-claude-skill`.

**React / Next / Vue / Svelte** — `react-best-practices`,
`react-component-performance`, `react-flow-architect`, `react-flow-node-ts`,
`react-modernization`, `react-native-architecture`, `react-nextjs-development`,
`react-patterns`, `react-state-management`, `react-ui-patterns`,
`nextjs-app-router-patterns`, `nextjs-best-practices`, `nextjs-supabase-auth`,
`vue`, `vue-best-practices`, `vue-router-best-practices`,
`vue-testing-best-practices`, `vueuse`, `vueuse-functions`, `pinia`,
`nuxt`, `nuxt-better-auth`, `nuxt-content`, `nuxt-modules`, `nuxt-seo`,
`nuxt-studio`, `nuxt-ui`, `nuxthub`, `sveltekit`, `astro`, `angular`,
`angular-best-practices`, `angular-migration`, `angular-state-management`,
`angular-ui-patterns`.

**Go** — `go-build`, `go-playwright`, `go-review`, `go-rod-master`,
`go-test`, `golang-*` (30+ skills covering benchmark, cli, code-style,
concurrency, context, ci, data-structures, database, dependency-injection,
dependency-management, design-patterns, documentation, error-handling,
grpc, lint, modernize, naming, observability, patterns, performance,
popular-libraries, pro, project-layout, safety, samber-*, security,
stay-updated, stretchr-testify, structs-interfaces, testing,
troubleshooting), `grpc-golang`, `temporal-golang-pro`, `dbos-golang`,
`gradle-build` (Go variant exists).

**Rust** — `rust-async-patterns`, `rust-pro`, `arm-cortex-expert`,
`bevy-ecs-expert`, `cqrs-implementation`.

**Java / Kotlin / Scala** — `java-coding-standards`, `java-pro`,
`jpa-patterns`, `springboot-patterns`, `springboot-security`,
`springboot-tdd`, `springboot-verification`, `kotlin-build`,
`kotlin-coroutines-expert`, `kotlin-review`, `kotlin-test`, `scala-pro`.

**.NET / C# / F#** — `dotnet-architect`, `dotnet-backend`,
`dotnet-backend-patterns`, `csharp-pro`.

**Other** — `cpp-pro`, `c-pro`, `swift-concurrency-expert`,
`swiftui-expert-skill`, `swiftui-liquid-glass`, `swiftui-performance-audit`,
`swiftui-ui-patterns`, `swiftui-view-refactor`, `elixir-pro`, `haskell-pro`,
`julia-pro`, `php-pro`, `laravel-expert`, `laravel-security-audit`,
`ruby-pro`, `bash-defensive-patterns`, `bash-linux`, `bash-pro`,
`bash-scripting`, `bats-testing-patterns`, `posix-shell-pro`,
`linux-shell-scripting`, `powershell-windows`, `windows-shell-reliability`.

## Security & pentest

`security-audit`, `security-auditor`, `security-bluebook-builder`,
`security-compliance-compliance-check`, `security-requirement-extraction`,
`security-review`, `security-scan`, `security-scanning-*` (dependencies,
hardening, sast), `secrets-management`, `pci-compliance`, `gdpr-data-handling`,
`privacy-by-design`, `threat-modeling-expert`, `threat-mitigation-mapping`,
`attack-tree-construction`, `stride-analysis-patterns`,
`anti-reversing-techniques`, `active-directory-attacks`,
`aws-penetration-testing`, `cloud-penetration-testing`,
`api-security-best-practices`, `api-security-testing`, `api-fuzzing-bug-bounty`,
`burp-suite-testing`, `burpsuite-project-parser`, `web-security-testing`,
`top-web-vulnerabilities`, `xss-html-injection`, `html-injection-testing`,
`sql-injection-testing`, `sqlmap-database-pentesting`,
`ssh-penetration-testing`, `smtp-penetration-testing`,
`wordpress-penetration-testing`, `windows-privilege-escalation`,
`linux-privilege-escalation`, `privilege-escalation-methods`,
`broken-authentication`, `file-path-traversal`, `file-uploads`,
`memory-safety-patterns`, `memory-forensics`, `binary-analysis-patterns`,
`malware-analyst`, `dwarf-expert`, `metasploit-framework`,
`reverse-engineer`, `protocol-reverse-engineering`, `red-team-tactics`,
`red-team-tools`, `ethical-hacking-methodology`, `pentest-checklist`,
`pentest-commands`, `incident-responder`, `incident-response-*`,
`incident-commander`, `incident-runbook-templates`, `scanning-tools`,
`semgrep-rule-creator`, `semgrep-rule-variant-creator`,
`shodan-reconnaissance`, `solidity-security`, `vulnerability-scanner`,
`wireshark-analysis`, `ctf-*` (8 CTF specialties: ai-ml, crypto, forensics,
malware, misc, osint, pwn, reverse, web, writeup).

## Frontend / design

`frontend-design`, `frontend-developer`, `frontend-patterns`,
`frontend-security-coder`, `frontend-slides`, `frontend-ui-dark-ts`,
`frontend-mobile-development-component-scaffold`, `frontend-mobile-security-xss-scan`,
`tailwind-design-system`, `tailwind-patterns`, `radix-ui-design-system`,
`baseline-ui`, `building-native-ui`, `core-components`, `ui-skills`,
`ui-styling`, `ui-ux-designer`, `ui-ux-pro-max`, `ui-design-system`,
`ui-visual-validator`, `uxui-principles`, `theme-factory`, `iconsax-library`,
`reka-ui`, `unocss`, `magic-ui-generator`, `mobile-design`, `mobile-developer`,
`mobile-security-coder`, `pdf`, `pdf-official`, `pptx`, `pptx-official`,
`pptx-posters`, `slides`, `slidev`, `docx`, `docx-official`,
`web-design-guidelines`, `web-asset-generator`, `web-artifacts-builder`,
`design-system`, `design-orchestration`, `design-spells`, `design-md`,
`hig-foundations`, `hig-inputs`, `hig-patterns`, `hig-platforms`,
`hig-project-context`, `hig-technologies`, `hig-components-*`,
`fixing-accessibility`, `fixing-metadata`, `fixing-motion-performance`,
`accessibility-compliance-accessibility-audit`, `screen-reader-testing`,
`wcag-audit-patterns`, `motion`, `scroll-experience`, `animejs-animation`,
`magic-animator`, `interactive-portfolio`, `landing-page-generator`,
`shadcn`, `remotion`, `remotion-best-practices`, `electron-development`,
`chrome-extension-developer`, `progressive-web-app`,
`browser-extension-builder`, `browser-automation`, `app-builder`,
`stitch-loop`, `stitch-ui-design`.

## AI / ML / LLM apps

`ai-agent-development`, `ai-agents-architect`, `ai-analyzer`,
`ai-engineer`, `ai-engineering-toolkit`, `ai-md`, `ai-ml`, `ai-native-cli`,
`ai-product`, `ai-seo`, `ai-slop-cleaner`, `ai-studio-image`,
`ai-wrapper-product`, `agentflow`, `agentfolio`, `agentic-actions-auditor`,
`autonomous-agent-patterns`, `autonomous-agents`, `autopilot`,
`agents-v2-py`, `bdistill-behavioral-xray`, `bdistill-knowledge-extraction`,
`langchain-architecture`, `langfuse`, `langgraph`, `crewai`, `convex`,
`copilot-sdk`, `gemini-api-dev`, `gemini-api-integration`, `inngest`,
`llm-app-patterns`, `llm-application-dev-*`, `llm-evaluation`,
`llm-ops`, `llm-prompt-optimizer`, `llm-structured-output`,
`local-llm-expert`, `hud`, `eval`, `eval-harness`, `evaluation`,
`advanced-evaluation`, `agent-evaluation`, `learn`, `learn-eval`,
`learner`, `self-improving-agent`, `voice-agents`, `voice-ai-development`,
`voice-ai-engine-development`, `computer-use-agents`,
`hugging-face-*` (15 skills: cli, community-evals, dataset-viewer, datasets,
evaluation, gradio, jobs, model-trainer, paper-publisher, papers,
tool-builder, trackio, vision-trainer), `transformers`, `transformers-js`,
`fal-*` (6 image/video skills), `imagen`, `image-studio`,
`generate-image`, `stability-ai`, `higgsfield-*` (6 skills: generate,
marketplace-cards, product-photoshoot, soul-id, ai-studio-image), `unsplash-integration`,
`videodb`, `videodb-skills`, `seek-and-analyze-video`, `video-editing`,
`audio-transcriber`, `embedding-strategies`, `rag-engineer`,
`rag-implementation`, `rag-architect`, `vector-database-engineer`,
`vector-index-tuning`, `similarity-search-patterns`, `hybrid-search-implementation`,
`iterative-retrieval`, `mcp-builder` / `mcp-builder-ms` / `mcp-server-builder`,
`mcp-setup`.

## Integrations / SaaS

Slack (`slack-automation`, `slack-bot-builder`, `slack-gif-creator`),
Discord (`discord-automation`, `discord-bot-architect`),
Telegram (`telegram`, `telegram-automation`, `telegram-bot-builder`,
`telegram-mini-app`), Twitter/X (`x-api`, `x-article-publisher-skill`,
`x-twitter-scraper`, `twitter-automation`),
LinkedIn (`linkedin-automation`, `linkedin-cli`),
WhatsApp (`whatsapp-automation`, `whatsapp-cloud-api`),
Instagram (`instagram`, `instagram-automation`),
TikTok (`tiktok-automation`), YouTube (`youtube-automation`,
`youtube-summarizer`), Reddit (`reddit-automation`).

CRM / GTM — `hubspot-automation`, `hubspot-integration`,
`salesforce-automation`, `salesforce-development`, `pipedrive-automation`,
`zoho-crm-automation`, `monday-automation`, `monday-com-automation` (via `monday`),
`close-automation`, `freshdesk-automation`, `freshservice-automation`,
`intercom-automation`, `zendesk-automation`, `helpdesk-automation`,
`activecampaign-automation`, `brevo-automation`, `convertkit-automation`,
`klaviyo-automation`, `mailchimp-automation`, `sendgrid-automation`,
`postmark-automation`, `sales-automator`, `sales-enablement`.

Productivity — `notion-automation`, `notion-template-business`,
`obsidian-bases`, `obsidian-cli`, `obsidian-clipper-template-creator`,
`obsidian-markdown`, `airtable-automation`, `coda-automation`,
`asana-automation`, `basecamp-automation`, `clickup-automation`,
`trello-automation`, `wrike-automation`, `todoist-automation`,
`linear-automation`, `linear-claude-skill`, `jira-automation`,
`bitbucket-automation`, `github-automation`, `gitlab-automation`,
`confluence-automation`, `bamboohr-automation`, `cal-com-automation`,
`calendly-automation`, `docusign-automation`, `dropbox-automation`,
`box-automation`, `gmail-automation`, `google-calendar-automation`,
`google-docs-automation`, `google-drive-automation`, `google-sheets-automation`,
`googlesheets-automation`, `google-slides-automation`,
`google-analytics-automation`, `outlook-automation`,
`outlook-calendar-automation`, `microsoft-teams-automation`,
`one-drive-automation`, `m365-agents-dotnet`, `m365-agents-py`,
`m365-agents-ts`, `office-productivity`, `google-workspace-cli`,
`zoom-automation`, `miro-automation`, `figma-automation`,
`canva-automation`, `webflow-automation`, `make-automation`,
`zapier-make-patterns`, `n8n-*` (7 skills), `apify-*` (14 skills).

Payments / Fintech — `stripe-automation`, `stripe-integration`,
`stripe-integration-expert`, `square-automation`, `paypal-integration`,
`plaid-fintech`, `pakistan-payments-stack`.

Analytics — `mixpanel-automation`, `amplitude-automation`,
`posthog-automation`, `segment-automation`, `segment-cdp`,
`datadog-automation`, `sentry-automation`, `pagerduty-automation`,
`render-automation`, `grafana-dashboards`, `prometheus-configuration`,
`distributed-tracing`, `observability-engineer`,
`observability-monitoring-monitor-setup`, `observability-monitoring-slo-implement`,
`slo-implementation`.

Other — `algolia-search`, `firecrawl-scraper`, `firebase`, `clerk-auth`,
`auth-implementation-patterns`, `twilio-communications`,
`shopify-development`, `shopify-apps`, `shopify-automation`,
`webflow-automation`, `wordpress-development` (via `wordpress`,
`wordpress-plugin-development`, `wordpress-theme-development`,
`wordpress-woocommerce-development`), `odoo-*` (25 skills),
`labarchive-integration`, `latchbio-integration`, `apify-*` (14 skills),
`exa-search`, `tavily-web`, `perplexity-search`, `parallel-web`,
`web-scraper`, `pubmed-database`, `uniprot-database`, `alpha-vantage`,
`datamol`, `food-database-query`.

## Bioinformatics / scientific (manual-trigger)

`anndata`, `arboreto`, `astropy`, `benchling-integration`,
`biopython`, `bioservices`, `cellxgene-census`, `cirq`, `cobrapy`,
`deepchem`, `deeptools`, `depmap`, `diffdock`, `dnanexus-integration`,
`etetoolkit`, `flowio`, `fluidsim`, `geniml`, `geomaster`, `geopandas`,
`gget`, `ginkgo-cloud-lab`, `glycoengineering`, `gtars`, `histolab`,
`hypogenic`, `imaging-data-commons`, `lamindb`, `latchbio-integration`,
`matchms`, `matplotlib`, `medchem`, `molecular-dynamics`, `molfeat`,
`molykit`, `networkx`, `neurokit2`, `neuropixels-analysis`,
`omero-integration`, `open-notebook`, `opentrons-integration`,
`pandas`, `pathml`, `pennylane`, `phylogenetics`, `plotly`, `polars`,
`polars-bio`, `primekg`, `protocolsio-integration`, `pufferlib`,
`pydeseq2`, `pydicom`, `pyhealth`, `pylabrobot`, `pymatgen`, `pymc`,
`pymoo`, `pyopenms`, `pysam`, `pytdc`, `pytorch-lightning`, `pyzotero`,
`qiskit`, `qutip`, `rdkit`, `rowan`, `scanpy`, `scarcity-urgency-psychologist`,
`scikit-bio`, `scikit-learn`, `scikit-survival`, `scvelo`, `scvi-tools`,
`seaborn`, `shap`, `simpy`, `stable-baselines3`, `statistical-analysis`,
`statsmodels`, `sympy`, `tiledbvcf`, `timesfm-forecasting`,
`torch-geometric`, `torchdrug`, `umap-learn`, `usfiscaldata`, `vaex`,
`zarr-python`.

## Business / marketing / writing (auto-trigger by content)

`brand`, `brand-guidelines`, `brand-guidelines-anthropic`,
`brand-guidelines-community`, `brand-perception-psychologist`,
`copywriting`, `copywriting-psychologist`, `cold-email`, `email-sequence`,
`email-systems`, `email-template-builder`, `subject-line-psychologist`,
`headline-psychologist`, `pitch-psychologist`, `objection-preemptor`,
`social-proof-architect`, `loss-aversion-designer`,
`scarcity-urgency-psychologist`, `ux-persuasion-engineer`,
`visual-emotion-engineer`, `customer-psychographic-profiler`,
`awareness-stage-mapper`, `jobs-to-be-done-analyst`, `internal-comms`,
`internal-comms-anthropic`, `internal-comms-community`, `peer-review`,
`pr-writer`, `pricing-strategy`, `price-psychology-strategist`,
`product-marketing-context`, `product-strategist`, `product-team`,
`product-manager`, `product-manager-toolkit`, `product-design`,
`product-discovery`, `product-inventor`, `product-analytics`,
`market-research`, `market-research-reports`, `market-sizing-analysis`,
`marketing-ideas`, `marketing-psychology`, `landing-page-generator`,
`launch-strategy`, `lead-magnets`, `free-tool-strategy`,
`growth-engine`, `app-store-changelog`, `app-store-optimization`,
`form-cro`, `onboarding-cro`, `page-cro`, `paywall-upgrade-cro`,
`popup-cro`, `signup-flow-cro`, `paid-ads`, `referral-program`,
`saas-multi-tenant`, `saas-mvp-launcher`, `saas-scaffolder`,
`micro-saas-launcher`, `monetization`, `revops`, `kaizen`,
`content-creator`, `content-engine`, `content-marketer`,
`content-strategy`, `social-content`, `social-orchestrator`,
`article-writing`, `blog-writing-guide`, `beautiful-prose`,
`copy-editing`, `professional-proofreader`, `humanize-chinese`,
`avoid-ai-writing`, `scientific-writing`, `scientific-brainstorming`,
`scientific-critical-thinking`, `scientific-schematics`, `scientific-slides`,
`scientific-visualization`, `latex-paper-conversion`, `latex-posters`,
`literature-review`, `research-grants`, `paper-lookup`,
`research-lookup`, `citation-management`, `scholar-evaluation`,
`viral-generator-builder`, `interview-coach`, `crosspost`.

## Strategy / planning / brainstorming

`brainstorm`, `brainstorming`, `concise-planning`, `progressive-estimation`,
`planning-with-files`, `clarity-gate`, `closed-loop-delivery`,
`competitive-landscape`, `competitor-alternatives`, `competitive-teardown`,
`startup-analyst`, `startup-business-analyst-business-case`,
`startup-business-analyst-financial-projections`,
`startup-business-analyst-market-opportunity`, `startup-financial-modeling`,
`startup-metrics-framework`, `kpi-dashboard-design`, `data-storytelling`,
`exploratory-data-analysis`, `business-analyst`, `business-growth`,
`investor-materials`, `investor-outreach`, `c-level-advisor`,
`andrej-karpathy`, `bill-gates`, `elon-musk`, `geoffrey-hinton`,
`ilya-sutskever`, `sam-altman`, `steve-jobs`, `warren-buffett`,
`yann-lecun`, `yann-lecun-debate`, `yann-lecun-filosofia`,
`yann-lecun-tecnico`, `uncle-bob-craft`, `matematico-tao`,
`consciousness-council`, `goal-analyzer`, `instinct-export`,
`instinct-import`, `instinct-status`, `roadmap-communicator`,
`runbook-generator`, `task-intelligence`, `team-collaboration-issue`,
`team-collaboration-standup-notes`, `team-composition-analysis`,
`engineering`, `engineering-team`, `agile-product-owner`,
`competitive-landscape`, `experiment-designer`, `interview-system-designer`,
`migration-architect`, `observability-designer`, `runbook-generator`,
`tech-debt-tracker`, `tech-stack-evaluator`, `tdd-orchestrator`,
`acceptance-orchestrator`, `multi-advisor`, `andruia-*` (4 niche skills).

## Data / database

`data-engineer`, `data-engineering-data-driven-feature`,
`data-engineering-data-pipeline`, `data-quality-frameworks`,
`data-scientist`, `data-structure-protocol`, `database`, `database-admin`,
`database-architect`, `database-cloud-optimization-cost-optimize`,
`database-design`, `database-designer`, `database-lookup`,
`database-migration`, `database-migrations-migration-observability`,
`database-migrations-sql-migrations`, `database-optimizer`,
`database-schema-designer`, `dbt-transformation-patterns`, `airflow-dag-patterns`,
`spark-optimization`, `dask`, `polars`, `nosql-expert`,
`event-sourcing-architect`, `event-store-design`, `projection-patterns`,
`saga-orchestration`, `ddd-context-mapping`, `ddd-strategic-design`,
`ddd-tactical-patterns`, `domain-driven-design`, `microservices-patterns`,
`monorepo-architect`, `monorepo-management`, `monorepo-navigator`,
`nx-workspace-patterns`, `bazel-build-optimization`, `turborepo`,
`turborepo-caching`, `gitlab-ci-patterns`.

## Game dev / graphics

`bevy-ecs-expert`, `game-development`, `godot-4-migration`,
`godot-gdscript-patterns`, `minecraft-bukkit-pro`,
`unity-developer`, `unity-ecs-patterns`, `unreal-engine-cpp-pro`,
`shader-programming-glsl`, `3d-web-experience`, `threejs-*` (12 skills:
animation, fundamentals, geometry, interaction, lighting, loaders,
materials, postprocessing, shaders, skills, textures), `tresjs`,
`spline-3d-integration`, `phaser-best-practices`, `algorithmic-art`,
`makepad-*` (15 skills), `robius-*` (5 skills), `avalonia-*` (3 skills),
`vizcom`.

## Health / medical / domain-specific

`tcm-constitution-analyzer`, `clinical-decision-support`,
`clinical-reports`, `family-health-analyzer`, `fitness-analyzer`,
`food-database-query`, `health-trend-analyzer`,
`mental-health-analyzer`, `nutrition-analyzer`, `occupational-health-analyzer`,
`oral-health-analyzer`, `rehabilitation-analyzer`, `sexual-health-analyzer`,
`skin-health-analyzer`, `sleep-analyzer`, `travel-health-analyzer`,
`treatment-plans`, `weightloss-analyzer`, `iso-13485-certification`,
`fda-food-safety-auditor`, `fda-medtech-compliance-auditor`,
`ra-qm-team`, `quality-nonconformance`, `supply-chain-risk-auditor`,
`carrier-relationship-management`, `customs-trade-compliance`,
`returns-reverse-logistics`, `inventory-demand-planning`,
`production-scheduling`, `logistics-exception-management`,
`energy-procurement`, `employment-contract-templates`,
`advogado-criminal`, `advogado-especialista`, `legal-advisor`,
`junta-leiloeiros`, `leiloeiro-*` (6 specialist skills: avaliacao, edital,
ia, juridico, mercado, risco), `local-legal-seo-audit`.

## SEO / search / web

`seo-*` (~40 skills covering aeo, audits, authority-builder, cannibalization,
competitor-pages, content, content-cluster, content-planner, content-quality,
content-refresher, dataforseo, fundamentals, forensic-incident-response,
geo, hreflang, image-gen, images, internal-linking, keyword-research,
keyword-strategist, landing-page, meta, page, plan, programmatic,
schema, schema-generator, sitemap, snippet-hunter, structure-architect,
technical, content-auditor, content-writer, content-strategist),
`schema-markup`, `programmatic-seo`, `site-architecture`,
`local-legal-seo-audit`.

## Visual / artifacts / output

`banner-design`, `canvas-design`, `favicon`, `infographics`,
`json-canvas`, `mermaid-expert`, `markdown-mermaid-writing`,
`screenshots`, `visual-verdict`, `web-asset-generator`,
`web-artifacts-builder`, `webapp-testing`, `playwright-skill`,
`playwright-java`, `playwright-pro`, `azure-microsoft-playwright-testing-ts`,
`azure-resource-manager-playwright-dotnet`, `puppeteer` (none registered —
playwright covers headless browser tasks).

## Personal / misc

`adhx`, `aegisops-ai`, `aeon`, `agentmail`, `agentphone`, `aside`,
`auri-core`, `auto`, `awareness-stage-mapper`, `awt-e2e-testing`,
`banner-design`, `blockchain-developer`, `blockrun`, `blueprint`,
`build`, `build-fix`, `bullmq-specialist`, `busybox-on-windows`,
`ccg`, `cell` (`cellxgene-census` only — no generic `cell`),
`chat-widget`, `checkpoint`, `claw`, `clarvia-aeo-check`,
`competitive-teardown`, `comfyui-gateway`, `context-*` (12 context-mgmt
skills: agent, compression, degradation, driven-development, fundamentals,
guardian, optimization, restore, save, window-management, audit-context-building,
external-context), `convex`, `cred-omega`, `crypto-bd-agent`,
`defi-protocol-templates`, `defuddle`, `devcontainer-setup`,
`diary`, `differential-review`, `dispatching-parallel-agents`,
`dmux-workflows`, `docs-architect`, `dubai`, `earllm-build`,
`emblemai-crypto-wallet`, `engineering`, `evolution`, `evolve`,
`emergency-card`, `emotional-arc-designer`, `external-context`,
`feature-coverage` (none — covered by test-coverage), `feed-data`,
`finance`, `finishing-a-development-branch`, `find-bugs`,
`fishing-accessibility` (typo — actual is `fixing-accessibility`),
`food-database-query`, `git-worktree-manager`, `using-git-worktrees`,
`go-build`, `harness-audit`, `hudgen` (via `hud`), `hugging-face-*`,
`hypogenic`, `i18n-localization`, `identity-mirror`, `imaging-data-commons`,
`infinite-gratitude`, `infographics`, `inngest`, `internal-comms`,
`investor-outreach`, `ios-debugger-agent`, `ios-developer`,
`jobgpt`, `keyword-extractor`, `lex`, `lightning-architecture-review`,
`lightning-channel-factories`, `lightning-factory-explainer`,
`linkedin-cli`, `loki-mode`, `loop-start`, `loop-status`, `make-automation`,
`manifest`, `matlab`, `maxia`, `mcp-builder`, `model-route`,
`moodle-external-api-development`, `monorepo-navigator`,
`ms365-tenant-manager`, `multi-backend`, `multi-execute`,
`multi-frontend`, `multi-plan`, `multi-workflow`, `nerdzao-elite`,
`nerdzao-elite-gemini-high`, `nestjs-expert`, `network-101`,
`network-engineer`, `networkx`, `nft-standards`, `notebooklm`,
`omc-doctor`, `omc-reference`, `omc-setup`, `omc-teams`,
`onboarding-cro`, `onboarding-psychologist`, `oss-hunter`,
`overhead-cost-cleanup` (covered by aws-cost-cleanup), `pakistan-payments-stack`,
`parallel-web`, `peer-review`, `pentest-checklist`, `performance-profiler`,
`performance-engineer`, `performance-optimizer`, `performance-profiling`,
`performance-testing-review-*`, `pipecat-friday-agent`, `pmc` (not registered),
`pm2`, `podcast-generation`, `popup-cro`, `pricing-strategy`,
`product-development` (covered by product-* family), `production-code-audit`,
`progressive-pwa` (typo — covered by progressive-web-app),
`projects`, `promote`, `pyhealth`, `pyzotero`, `qrcode` (not registered),
`quality-gate`, `quant-analyst`, `ralph`, `ralplan`, `rdkit`,
`recallmax`, `reka-ui`, `release`, `release-manager`,
`requesting-code-review`, `resume-session`, `risk-manager`,
`risk-metrics-calculation`, `runbook-generator`, `save-session`,
`sankhya-dashboard-html-jsp-custom-best-pratices`, `scrub-and-search`,
`security-bluebook-builder`, `senior-architect`, `setup`,
`shopify-development`, `simplify-code`, `slidev`, `solve-challenge`,
`speckit-updater`, `speed`, `spec-to-code-compliance`,
`sred-project-organizer`, `sred-work-summary`, `state-machines`,
`stitch-loop`, `stitch-ui-design`, `superpowers-lab`, `using-superpowers`,
`tcm-constitution-analyzer`, `team`, `temporal-python-pro`,
`temporal-python-testing`, `temporal-golang-pro`, `terraform-skill`,
`test-automator`, `test-coverage`, `test-driven-development`,
`test-fixing`, `testing-patterns`, `testing-qa`, `tmux`, `tool-design`,
`tool-use-guardian`, `topic-coverage` (not registered — covered by `test-coverage`),
`trace`, `track-management`, `trust-calibrator`, `ts-library`,
`tutorial-engineer`, `twilio-communications`, `ultraqa`, `ultrawork`,
`update-codemaps`, `update-docs`, `upstash-qstash`, `upgrading-expo`,
`uv-package-manager`, `ux-researcher-designer`, `vault-secret-management`
(not registered — covered by `secrets-management`), `vercel-ai-sdk-expert`,
`vexor`, `vexor-cli`, `vibe-code-auditor`, `vibers-code-review`,
`viboscope`, `visual-emotion-engineer`, `vue`, `vueuse`, `vueuse-functions`,
`warren-buffett`, `wellally-tech`, `what-if-oracle`, `wiki-*` (8 skills:
architect, changelog, onboarding, page-writer, qa, researcher, vitepress),
`writing-plans`, `writing-skills`, `writing-web-documentation`,
`xvary-stock-research`, `yes-md`, `zeroize-audit`, `zod-validation-expert`,
`zustand-store-ts`, `007`, `dubai`.

## OAuth-only (claude.ai integrations)

These require user-authorised OAuth tokens; first call returns an
authentication URL. They are NOT auto-triggered.

`claude_ai_Asana`, `claude_ai_Atlassian`, `claude_ai_Box`,
`claude_ai_Canva`, `claude_ai_Gmail`, `claude_ai_Google_Drive`,
`claude_ai_HubSpot`, `claude_ai_Intercom`, `claude_ai_Linear`,
`claude_ai_Microsoft_365`, `claude_ai_Notion`, `claude_ai_Windsor_ai`,
`claude_ai_higgsfield`, `claude_ai_monday_com`, `claude_ai_WordPress_com`.

## Anthropic SDK app-builder (auto-trigger on SDK code)

`agent-sdk-dev:new-sdk-app`, `agent-sdk-dev:agent-sdk-verifier-py`,
`agent-sdk-dev:agent-sdk-verifier-ts`, `adspirer-ads-agent:keyword-research`,
`adspirer-ads-agent:ad-campaign-best-practices`,
`claude-code-setup:claude-automation-recommender`,
`skill-creator:skill-creator`.

---

## How to use this index

- **User-facing**: type `/<skill-name>` to fire any single skill.
- **Implicit**: the assistant will reach for the matching skill when a
  task's shape matches its description. The full Skill descriptions are
  in `~/.claude/skills/<skill-name>/SKILL.md` (or equivalent).
- **Discovery**: when in doubt about which skill applies, ask the
  assistant — it has the live registry from the system reminder.
- **Adding new**: write a new SKILL.md, restart Claude Code, and the
  Skill tool registers it on next session. See [[Skill_Authorship]]
  (TBD) for the spec.

---

## Maintenance

Roster snapshot taken 2026-05-14 (~1500+ skill names registered).
Rosters change between Claude Code sessions as MCP servers add/remove
their own skill bundles. Re-snapshot via the `<system-reminder>` block
at session start and diff against this file when significant.
