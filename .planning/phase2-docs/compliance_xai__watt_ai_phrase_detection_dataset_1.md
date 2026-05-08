# Watt_AI_Phrase_Detection_Dataset (1)

Watt Utilities - AI Compliance Phrase Detection Dataset
Developer-ready phrase library for AI transcribe, alerting, and compliance review
Purpose: This document gives your developers a structured phrase and behaviour dataset for two workflows: lead generation calls and full verbal confirmation scripts. Each row can be used as a rule seed for exact-match checks, semantic matching, prompt evaluation, score weighting, and real-time alerts.
Important note: This dataset is based on Watt's Sales Partner Compliance Guide plus the recurring call issues you have raised in our conversations. It is designed as a production-ready starter dataset and should be tuned further against your own historical call recordings.
How to use it: Treat each trigger as either exact language, a semantic equivalent, or a behavioural pattern. Where the same risk appears more than once, developers should group them into a single detection family.
Severity guide: Critical = block / escalate, High = manual review, Medium = coaching.

## Recommended developer fields

rule_id
call_stage
category
severity
trigger_phrase_or_pattern
detection_type (exact phrase / semantic / behavioural)
why_flagged
approved_alternative
action

## Lead Generation - Identity and transparency (20 examples)


## Lead Generation - Qualification and authority (12 examples)


## Lead Generation - Pricing and savings claims (20 examples)


## Lead Generation - Market comparison and search scope (12 examples)


## Lead Generation - Pressure, objections and vulnerability (12 examples)


## Lead Generation - Supplier and industry claims (12 examples)


## Verbal Confirmation - Script framing and legal nature (5 examples)


## Verbal Confirmation - Commission disclosure (5 examples)


## Verbal Confirmation - Contract terms (8 examples)


## Verbal Confirmation - Understanding and consent (8 examples)


## Verbal Confirmation - Script delivery and call quality (6 examples)


## Implementation notes

Do not rely on exact wording only. Detect semantic equivalents such as 'best deal', 'lowest you'll get', and 'nobody can beat this' in the same risk family.
Where the issue is behavioural rather than phrase-based, use transcript timing, interruption count, customer objection markers, and pace analysis.
When a customer sounds confused, vulnerable, or unsure, increase the risk score even if the script wording appears technically correct.
For verbal confirmation, compare the spoken contract reading to the contract data source. Any mismatch in supplier, term, rate, or standing charge should trigger a separate discrepancy flag.
Build a whitelist of approved wording so the AI can coach agents, not only flag them.

## Source note

Primary source reference for the rules in this dataset: Watt Sales Partner Compliance Guide V1. This includes standards on early identification, avoiding misleading statements, handling customer circumstances, honesty about remuneration, confirming authority, explaining principal terms, ensuring customer understanding, and following approved verbal sales scripts.
Total examples included: 120


### Table 1

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lead generation | Identity and transparency | Critical No mention of 'Watt Utilities' in the first 20 seconds | Customer may not know who is calling or who the business is | Open with: 'Hi, it's [name] calling from Watt Utilities, an energy consultancy regarding your business energy contract.' | Review / block if repeated |
| 2 | Lead generation | Identity and transparency | Critical 'I'm calling about your renewal' without naming Watt Utilities | Purpose given without business identity | State company and role before discussing renewal | Review |
| 3 | Lead generation | Identity and transparency | Critical 'I'm from your energy provider' | Risks supplier impersonation | 'I'm from Watt Utilities, an independent energy consultancy.' | Escalate |
| 4 | Lead generation | Identity and transparency | Critical 'I'm calling from E.ON' when the caller is a broker | Misrepresentation of who the customer is speaking to | Explain clearly that Watt Utilities is a broker/consultancy, not the supplier | Escalate |
| 5 | Lead generation | Identity and transparency | High 'We work with your supplier' used before saying who Watt is | Can create confusion by affiliation before identity | Name Watt first, then explain you can compare current supplier and other options | Coach |
| 6 | Lead generation | Identity and transparency | Medium Rushed intro with name but no company description | Customer may still be unclear on role | Add 'from Watt Utilities, an energy consultancy/broker' | Coach |
| 7 | Lead generation | Identity and transparency | High 'We said we'd call you back' when there is no logged prior contact | Implies an existing relationship that may not exist | Use neutral wording: 'I'm calling regarding your business energy renewal window.' | Review |
| 8 | Lead generation | Identity and transparency | High 'There is nothing to worry about' used as an opener | Can sound evasive or manipulative | Use factual, calm wording and explain the purpose of the call | Coach |
| 9 | Lead generation | Identity and transparency | High 'We're the team that handles your contract renewals' | Implies authority over the contract rather than advisory support | 'We help businesses review renewal options and compare rates.' | Review |
| 10 | Lead generation | Identity and transparency | High 'I'm ringing on behalf of your supplier' without proof or consent | May be inaccurate and misleading | If there is a genuine partner arrangement, explain it factually and still identify Watt first | Review |
| 11 | Lead generation | Identity and transparency | Medium 'We are one of the biggest in the market' without substantiation | Unverified company claim | Use factual company description only | Coach |
| 12 | Lead generation | Identity and transparency | High 'We control a large part of the energy market' | Overstates market position and risks misleading | Avoid size/market share claims unless evidenced and relevant | Review |
| 13 | Lead generation | Identity and transparency | Medium 'We work across Europe' where this is irrelevant to the call | Irrelevant positioning can inflate perceived scale | Keep intro relevant to the customer's contract review | Coach |
| 14 | Lead generation | Identity and transparency | High 'We're not trying to get you to change supplier' then moving into market comparison | Creates inconsistency and deviousness risk | Be open: 'We can review your current supplier offer and compare other options if appropriate.' | Review |
| 15 | Lead generation | Identity and transparency | Critical Caller states only first name and no business name at all | No transparent identification | Always identify self, business, and reason for call | Escalate |
| 16 | Lead generation | Identity and transparency | Medium Caller gives long intro about company history before purpose | May obscure the actual purpose and confuse customer | Keep intro short: identity, role, purpose | Coach |
| 17 | Lead generation | Identity and transparency | High 'We are your renewal department' | False authority / internal team implication | Say 'We are Watt Utilities and help businesses review renewal options.' | Review |
| 18 | Lead generation | Identity and transparency | High Caller says 'survey' or 'account check' but is really selling | False purpose of call | Describe the real purpose accurately | Escalate |
| 19 | Lead generation | Identity and transparency | Critical No purpose stated at all before asking questions about contract | Information gathering without clear purpose | State reason for call before qualification | Escalate |
| 20 | Lead generation | Identity and transparency | Medium Internal jargon like 'passover' used to customer | Unclear language can reduce transparency | Use customer-friendly wording about review and handover to pricing | Coach |


### Table 2

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lead generation | Qualification and authority | Critical No question confirming whether the customer is the decision maker | Risk that caller is not authorised to discuss or agree contracts | Ask: 'Are you the person who deals with the business energy contract?' | Review / block if progressed |
| 2 | Lead generation | Qualification and authority | High Agent continues after hearing 'my partner / director handles that' | Authority warning ignored | Offer to speak with the authorised person or arrange a callback | Review |
| 3 | Lead generation | Qualification and authority | High No question about whether the contract has already been renewed | Can waste time and create inaccurate positioning | Ask early whether the contract has already been renewed | Coach |
| 4 | Lead generation | Qualification and authority | High No question about contract end date / renewal window | Weak qualification and poor basis for urgency | Establish timing before discussing next steps | Coach |
| 5 | Lead generation | Qualification and authority | High No confirmation that the supply is business / non-domestic | Risk of engaging unsuitable or domestic customer | Confirm the call relates to a business energy supply | Review |
| 6 | Lead generation | Qualification and authority | Critical Customer indicates home use / domestic use and agent keeps selling | Potentially unsuitable sale | Stop and verify usage before proceeding | Escalate |
| 7 | Lead generation | Qualification and authority | Critical Customer mentions prepayment meter and agent continues as normal | Unsuitable meter type for supported process | Stop and explain that the case needs separate handling | Escalate |
| 8 | Lead generation | Qualification and authority | High Agent does not reconfirm site or supply address before pricing handover | Risk of wrong-meter / wrong-site discussion | Reconfirm premises before handover | Coach |
| 9 | Lead generation | Qualification and authority | Medium Agent asks too many qualification questions before identity | Can feel intrusive before transparent introduction | Identify first, then qualify | Coach |
| 10 | Lead generation | Qualification and authority | High Agent implies authority by default: 'I'll just get this renewed for you' | Assumes consent and authority that have not been proven | Use conditional wording until authority is confirmed | Review |
| 11 | Lead generation | Qualification and authority | High No consent obtained before passing to pricing team | Customer may not understand the next step | Ask: 'I can connect you to our pricing team to review your options - is that okay?' | Review |
| 12 | Lead generation | Qualification and authority | Medium Agent asks closed authority question in a leading way: 'You handle the bills, yes?' | May produce unreliable confirmation | Use neutral authority check | Coach |


### Table 3

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lead generation | Pricing and savings claims | Critical 'We will get you cheaper rates' | Guarantees an outcome that may not be true | 'We can review available options and compare rates, but savings are not guaranteed.' | Escalate |
| 2 | Lead generation | Pricing and savings claims | Critical 'This will save you money' stated as fact | Absolute saving claim | Use qualified language with no guarantee | Escalate |
| 3 | Lead generation | Pricing and savings claims | Critical 'Best price in the market' | Whole-market superiority claim likely unsubstantiated | Only describe the scope actually checked | Escalate |
| 4 | Lead generation | Pricing and savings claims | High 'Cheapest rate we can find' without defining search scope | Can imply whole-market search | Say 'best rate we can obtain from the suppliers we approached' | Review |
| 5 | Lead generation | Pricing and savings claims | High 'Your renewal rates are always higher' | Absolute statement may not always be true | Use cautious wording: 'Renewal letters are often based on generic rates.' | Review |
| 6 | Lead generation | Pricing and savings claims | Critical 'Prices will definitely go up' | Unsubstantiated forward-looking claim | Use evidence-based, qualified commentary only | Escalate |
| 7 | Lead generation | Pricing and savings claims | Critical 'The only way is up with energy prices' | Potentially misleading market prediction | Avoid certainty; reference historical trends if relevant and evidenced | Escalate |
| 8 | Lead generation | Pricing and savings claims | High 'Take a 5 year deal to protect yourself because prices always rise' | Pressure plus unsupported prediction | Explain term options factually and without certainty claims | Review |
| 9 | Lead generation | Pricing and savings claims | Critical 'You'll make your money back in years 2, 3, 4 and 5' | Future financial outcome presented as certain | Do not forecast returns unless evidenced and carefully qualified | Escalate |
| 10 | Lead generation | Pricing and savings claims | High Specific future price number given without source | Unsupported forecast | Only reference public evidence and cite basis internally | Review |
| 11 | Lead generation | Pricing and savings claims | Medium Agent speculates about geopolitical events causing price rises with weak explanation | Can confuse or mislead | Keep market commentary short, factual, and evidenced | Coach |
| 12 | Lead generation | Pricing and savings claims | High 'Lock this in now before the market jumps tomorrow' without evidence | Manufactured urgency | Avoid deadline pressure unless there is a genuine expiring pricebook | Review |
| 13 | Lead generation | Pricing and savings claims | High 'Your current supplier will never beat this' | Absolute competitive claim without proof | Use comparative wording only where evidenced | Review |
| 14 | Lead generation | Pricing and savings claims | Medium 'This is a no-brainer' | Dismissive and high-pressure language | Keep recommendations professional and factual | Coach |
| 15 | Lead generation | Pricing and savings claims | High 'Everyone is seeing big rises now' | Generalised claim without evidence | Use measured, substantiated commentary | Review |
| 16 | Lead generation | Pricing and savings claims | Medium 'We should smash your current rates' | Informal savings promise | Use controlled and accurate language | Coach |
| 17 | Lead generation | Pricing and savings claims | Critical 'I can guarantee this is the cheapest you'll get' | Explicit guarantee | Remove guarantee language completely | Escalate |
| 18 | Lead generation | Pricing and savings claims | High 'You'd be mad not to take this now' | Pressure selling / emotional coercion | Present options neutrally | Review |
| 19 | Lead generation | Pricing and savings claims | High 'This is fixed so nothing else can change' | Over-simplifies contractual risk and pass-through charges | Describe fixed elements accurately and avoid blanket statements | Review |
| 20 | Lead generation | Pricing and savings claims | Medium 'You'll be laughing in a year' | Outcome promise framed as banter | Keep benefits factual and non-speculative | Coach |


### Table 4

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lead generation | Market comparison | High 'I have checked the whole market' when only a panel is used | Misrepresents search scope | State the actual supplier panel or scope checked | Review |
| 2 | Lead generation | Market comparison | High 'I have finalised the search and this is the most competitive rate' without stating who was searched | Incomplete basis for claim | Clarify the group of suppliers approached | Review |
| 3 | Lead generation | Market comparison | High 'We work with all suppliers' when not true | False panel breadth claim | State the number or type of suppliers factually | Review |
| 4 | Lead generation | Market comparison | Medium 'We get preferential rates that other brokers don't get' without qualification | May overstate access advantage | If true, qualify carefully: 'from some suppliers' and only if factual | Coach |
| 5 | Lead generation | Market comparison | High 'I'll send a tender to the market' but only a few suppliers will be approached | Implies a full-market tender | Use specific wording about selected suppliers | Review |
| 6 | Lead generation | Market comparison | High 'Nobody can beat this' | Absolute comparative claim | Avoid unless fully evidenced, which is rarely practical | Review |
| 7 | Lead generation | Market comparison | Medium 'This is the best tariff out there' | Vague comparative claim | Describe what has actually been compared | Coach |
| 8 | Lead generation | Market comparison | High Agent says competitor broker prices are always worse | Unsupported competitor criticism | Avoid competitor claims unless public and evidenced | Review |
| 9 | Lead generation | Market comparison | Medium 'We've searched everywhere' | Overbroad and unverifiable wording | Use precise scope language | Coach |
| 10 | Lead generation | Market comparison | High 'I know the market inside out, trust me this is the best' | Relies on authority instead of substantiation | Explain evidence and scope, not personal certainty | Review |
| 11 | Lead generation | Market comparison | High Agent implies direct supplier pricing is worse in every case | Likely inaccurate and misleading | Do not make blanket direct-vs-broker claims | Review |
| 12 | Lead generation | Market comparison | Medium Use of 'most competitive' with no documented comparison set | Insufficient evidence trail | Require internal note of comparison basis if used | Coach |


### Table 5

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lead generation | Pressure and vulnerability | Critical Customer says 'not interested' and agent continues hard sell | Possible coercion / harassment | Respect objection or seek permission for one brief final question only | Escalate |
| 2 | Lead generation | Pressure and vulnerability | High Customer says 'busy' and agent refuses callback option | Ignores inconvenience | Offer suitable callback or end call politely | Review |
| 3 | Lead generation | Pressure and vulnerability | Critical Customer sounds confused and agent pushes for transfer anyway | Potential vulnerability risk | Slow down, clarify, or stop the call | Escalate |
| 4 | Lead generation | Pressure and vulnerability | High Customer says they do not understand and agent replies 'don't worry, just let me put you through' | Deflects confusion rather than resolving it | Answer the concern before any handover | Review |
| 5 | Lead generation | Pressure and vulnerability | Critical Agent talks over the customer repeatedly during objection handling | Reduces informed participation | Allow customer to finish and address points calmly | Escalate if persistent |
| 6 | Lead generation | Pressure and vulnerability | High 'This will only take a second' used repeatedly to keep customer on call | Can be manipulative | Use honest timing and permission-based language | Review |
| 7 | Lead generation | Pressure and vulnerability | Medium Agent becomes overly familiar or patronising | Professionalism concern | Keep tone courteous and professional | Coach |
| 8 | Lead generation | Pressure and vulnerability | High Use of guilt language: 'I'm only trying to help you save money' after refusal | Emotional pressure | Accept refusal respectfully | Review |
| 9 | Lead generation | Pressure and vulnerability | Critical Customer mentions illness, language barrier, or not being in a fit state, and agent continues | Vulnerability and validity risk | Pause and arrange alternative contact or support | Escalate |
| 10 | Lead generation | Pressure and vulnerability | Medium Agent ignores signs of misunderstanding such as repeated 'sorry?' or 'what do you mean?' | May lead to poor understanding | Simplify and confirm understanding | Coach |
| 11 | Lead generation | Pressure and vulnerability | High No suppression / opt-out acknowledgment after 'please remove me' | Data and contact preference risk | Acknowledge and trigger suppression workflow | Review |
| 12 | Lead generation | Pressure and vulnerability | Critical Agent uses threat-like wording about missing prices or higher future costs to force engagement | High-pressure tactic | Remove threat framing and stick to factual options | Escalate |


### Table 6

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lead generation | Supplier and industry claims | High 'British Gas are about to put their prices up' with no evidence | Unsupported supplier-specific claim | Only use public, evidenced information | Review |
| 2 | Lead generation | Supplier and industry claims | High 'Your supplier is terrible' or derogatory retailer comments | Unprofessional and potentially misleading | Keep competitor/supplier commentary factual and neutral | Review |
| 3 | Lead generation | Supplier and industry claims | High Use of fines or industry news as a scare tactic | May mislead without proper context | Discuss public information carefully and only when relevant | Review |
| 4 | Lead generation | Supplier and industry claims | Medium 'I've heard E.ON are doing X' based on hearsay | No reliable source | Do not rely on rumours | Coach |
| 5 | Lead generation | Supplier and industry claims | High 'Supplier direct teams cannot match us' | Blanket claim with no evidence | Avoid direct comparison claims unless evidenced | Review |
| 6 | Lead generation | Supplier and industry claims | High 'That supplier always adds hidden charges' | Absolute negative supplier statement | Discuss contract terms factually rather than attacking supplier | Review |
| 7 | Lead generation | Supplier and industry claims | Medium Over-detailed industry explanation given inaccurately | Confusion / misinformation risk | Keep industry context accurate, simple, and relevant | Coach |
| 8 | Lead generation | Supplier and industry claims | High 'The regulator is forcing everyone to move now' | False regulatory claim | Never invent regulatory urgency | Escalate |
| 9 | Lead generation | Supplier and industry claims | Medium Agent references internal supplier strategy not in public domain | Competition / confidentiality concern | Use only public information | Coach |
| 10 | Lead generation | Supplier and industry claims | High 'This contract is green / fully renewable' without substantiation | Environmental claim risk | Only make sustainability claims when evidenced | Review |
| 11 | Lead generation | Supplier and industry claims | Medium 'This supplier has the best service levels' with no basis | Unsupported quality claim | Qualify with evidence or avoid | Coach |
| 12 | Lead generation | Supplier and industry claims | High 'The ombudsman is flooded with complaints against them' without source | Fear-based and unsupported | Do not use complaint narratives to pressure sale | Review |


### Table 7

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Verbal confirmation | Script framing | Critical Agent describes the script as 'just a formality' | Downplays legal effect of verbal agreement | Explain that it is a legally binding verbal contract / confirmation process | Escalate |
| 2 | Verbal confirmation | Script framing | High Agent does not explain before the script that the customer is entering a contract | Customer may not appreciate significance | Set expectation before starting the script | Review |
| 3 | Verbal confirmation | Script framing | High Agent says 'we're just locking prices in' without referring to contract | Can understate the legal commitment | State that the script confirms a binding contract for supply terms | Review |
| 4 | Verbal confirmation | Script framing | Critical Wrong script used for the supplier / situation | Script mismatch can invalidate compliance protection | Use only the approved script for the correct supplier and scenario | Escalate |
| 5 | Verbal confirmation | Script framing | High Agent paraphrases large sections of the approved script | May omit key protections | Follow script wording closely | Review |


### Table 8

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Verbal confirmation | Commission disclosure | Critical No mention that commission is included in the rates | Missing required transparency on remuneration | Explain that Watt receives commission from the supplier and it is included in the rates quoted | Escalate |
| 2 | Verbal confirmation | Commission disclosure | High 'You don't pay anything for our service' | Misses the fact the cost is embedded in the rates | Use transparent commission wording | Review |
| 3 | Verbal confirmation | Commission disclosure | High 'We are paid by the supplier' with no mention of bill/rate impact | Incomplete remuneration explanation | Clarify that the commission is reflected in the unit rate / proposal | Review |
| 4 | Verbal confirmation | Commission disclosure | Medium Agent rushes commission wording so quickly it is unintelligible | Poor clarity even if words are present | Deliver clearly and allow customer time to absorb | Coach |
| 5 | Verbal confirmation | Commission disclosure | High Customer asks about commission and agent gives evasive answer | Weak transparency under direct challenge | Answer directly and factually | Review |


### Table 9

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Verbal confirmation | Contract terms | Critical Supplier name not clearly stated | Core principle term missing | State supplier name clearly | Escalate |
| 2 | Verbal confirmation | Contract terms | Critical Contract term / duration omitted | Customer cannot assess commitment | State term in months/years clearly | Escalate |
| 3 | Verbal confirmation | Contract terms | Critical Unit rate not stated or unclear | Key price term missing | Read unit rate clearly and accurately | Escalate |
| 4 | Verbal confirmation | Contract terms | Critical Standing charge omitted where applicable | Incomplete tariff disclosure | Include standing charge where relevant | Escalate |
| 5 | Verbal confirmation | Contract terms | High Agent reads rates too quickly or mumbles numbers | Even correct terms may not be understood | Slow down and repeat if needed | Review |
| 6 | Verbal confirmation | Contract terms | High Agent states term/rates that do not match contract reading | Mismatch between pre-sales discussion and script | Flag for immediate review | Escalate |
| 7 | Verbal confirmation | Contract terms | High 'Fixed' described as meaning absolutely nothing can change | Potentially inaccurate depending on pass-through structure | Describe fixed elements accurately and avoid blanket guarantees | Review |
| 8 | Verbal confirmation | Contract terms | High Charges / service levels / applicable processes not covered | Customer may not understand how supply transfer works | Include process, charges, tariffs, and service level points required by script | Review |


### Table 10

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Verbal confirmation | Understanding and consent | Critical No clear 'yes' or equivalent customer agreement at the end | No reliable evidence of consent | Obtain a clear, audible affirmative response | Escalate |
| 2 | Verbal confirmation | Understanding and consent | Critical Customer sounds uncertain ('I think so', 'probably') and agent treats it as full consent | Ambiguous consent | Clarify until explicit agreement or stop | Escalate |
| 3 | Verbal confirmation | Understanding and consent | High Agent does not ask if the customer understands the terms | Understanding not tested | Ask clear understanding questions | Review |
| 4 | Verbal confirmation | Understanding and consent | Critical Customer asks 'what does that mean?' and agent continues without explanation | Known confusion ignored | Pause and explain before continuing | Escalate |
| 5 | Verbal confirmation | Understanding and consent | Critical Agent answers the script questions for the customer | Destroys evidence that the customer understood and agreed | Customer must answer for themselves | Escalate |
| 6 | Verbal confirmation | Understanding and consent | High Agent repeatedly prompts the same answer in a leading way | Consent may be coached rather than genuine | Use neutral questioning | Review |
| 7 | Verbal confirmation | Understanding and consent | High No confirmation that the customer has authority to bind the business | Contract authority not proven | Confirm authority within or before the script | Review |
| 8 | Verbal confirmation | Understanding and consent | Critical Customer indicates someone else signs contracts and agent proceeds anyway | Direct authority red flag | Stop and speak with authorised person | Escalate |


### Table 11

| ID | Stage | Category | Severity and Trigger | Why flagged | Approved wording / safer alternative | Action |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Verbal confirmation | Script delivery | High Script read at excessive speed with minimal pauses | Rushing can undermine understanding and enforceability | Require pace thresholds and pause points | Review |
| 2 | Verbal confirmation | Script delivery | High Agent skips sections to 'save time' | Approved script not adhered to | Read the full approved script | Escalate |
| 3 | Verbal confirmation | Script delivery | Medium Background noise makes key answers hard to hear | Evidence quality risk | Flag for review / re-call if material sections unclear | Coach / review |
| 4 | Verbal confirmation | Script delivery | High Agent interrupts customer answers during the script | Reduces clarity of the evidence | Allow full customer responses | Review |
| 5 | Verbal confirmation | Script delivery | High No closing recap of next steps / confirmation email / what happens next | Weak customer understanding after agreement | Provide a short end-of-call recap | Review |
| 6 | Verbal confirmation | Script delivery | Medium Unprofessional tone or visible frustration during script | Professionalism concern in a high-risk stage | Keep tone calm, clear, and methodical | Coach |