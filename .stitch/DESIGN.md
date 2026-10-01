---
name: PostSteward Publishing Workspace
colors:
  background: "#f4f4f0"
  surface: "#ffffff"
  surface-container: "#fafaf7"
  surface-active: "#ecece6"
  on-surface: "#1b1d1a"
  on-surface-variant: "#62675f"
  outline: "#bcc1b7"
  outline-variant: "#d9dcd4"
  primary: "#171815"
  on-primary: "#f6f6f0"
  brand-accent: "#ff6847"
  link: "#a93a23"
  success: "#1f7650"
  success-container: "#e7f5ed"
  warning: "#8b6400"
  warning-container: "#fff5d8"
  error: "#aa3d33"
  error-container: "#fcecea"
  focus: "#3f63f4"
---

# Design System: PostSteward Publishing Workspace

Status: implementation reference, 1 October 2026. Focused task views, the four publishing steps, grouped preparation fields, aligned consent rows, the draft preview and compact saved preparations are implemented in the workspace. Deployment is verified separately through the production workflow.

## Evidence and scope

The supplied 18-second recording of app.poststeward.com/app#publishing is the visual reference. It shows recovery, automation, agent access, billing, schedules, preparation and copy review in one continuous page. The active navigation remains Create & schedule. Long preparation forms stay in the right half of a panel after left-side content ends. Consent boxes are large and sit above their associated text. At the top, repeated headings and navigation consume almost the whole first screen.

Google Labs' DESIGN.md skill supplies the documentation method. Its companion source-extraction skill supplies the approach for reading frontend tokens without a Stitch connection. The inspected PostSteward source snapshot is commit 4cc6ac2 dated 20 September 2026; it grounds existing colours and font choices, not a claim about the exact CSS deployed in the recording. Layout dimensions below are proposed decisions for this product, not rules attributed to Google.

## 1. Visual Theme & Atmosphere

PostSteward should feel like a clear, dependable publishing workspace. Warm neutral surfaces, dark text and restrained coral accents give the existing identity a recognisable home. Each view should make its current task and next action immediately apparent. Typography and alignment do the organisational work; decorative treatments remain quiet.

The density is moderate. Related fields sit close enough to read as a group, while distinct tasks have their own views. Open space frames useful content; it does not reserve half a page for an exhausted column. Evidence remains readily inspectable alongside the action it explains. Material warnings, spending information and consent remain visible when they affect the user's decision.

## 2. Color Palette & Roles

### Primary Foundation

- Warm chalk (#f4f4f0): workspace canvas.
- Clean white (#ffffff): working surfaces and form panels.
- Pale stone (#fafaf7): supporting summaries and input surfaces.
- Soft stone (#ecece6): active navigation background.
- Quiet grey (#d9dcd4): panel dividers; stronger grey (#bcc1b7): input boundaries.

### Accent & Interactive

- Near-black (#171815) with ivory text (#f6f6f0): primary action.
- Coral (#ff6847): brand accent, used sparingly rather than as a generic error signal.
- Burnt coral (#a93a23): links where contrast is appropriate.
- Clear blue (#3f63f4): visible keyboard focus.

### Typography & Text Hierarchy

- Charcoal (#1b1d1a): headings, labels and important values.
- Muted olive-grey (#62675f): supporting text; verify contrast against each actual surface.
- Lighter tertiary text from the old source is not suitable for essential instructions without contrast measurement.

### Functional States

Forest green (#1f7650), amber (#8b6400) and brick red (#aa3d33) represent success, caution and failure. Every state includes an explicit text label. Distinguish Connected, Scheduled, Published, Verified, Unverified, Ambiguous, Blocked and Failed using the actual backend state. A green connection indicator must not imply that a post was verified.

## 3. Typography Rules

### Hierarchy & Weights

Retain the existing system sans-serif stack: platform UI font, Segoe UI on Windows and the system equivalent on macOS. Use the existing monospace stack only for short identifiers and technical details.

| Role | Proposed size / line height | Weight |
|---|---|---|
| Page title | 28 / 36 px | 700 |
| Section heading | 20 / 28 px | 650 |
| Subheading | 16 / 24 px | 600 |
| Body and form input | 16 / 24 px | 400–450 |
| Field label | 14 / 20 px | 600 |
| Supporting metadata | 13 / 20 px | 400 |

Use one page title per view and a short description beneath it. Avoid oversized marketing-style headings inside the working application. Repository names wrap; long identifiers have a copy action and an expandable detail area.

### Spacing Principles

Use a 4 px base scale with 8, 12, 16, 24 and 32 px steps. Leave 8 px between a label and its input, 16–24 px between field groups, and 24–32 px between sections. These gaps express relationships consistently.

## 4. Component Stylings

### Buttons

Use subtly rounded corners (8 px) and a minimum 44 px interactive height. Put one visually dominant action at the end of the current task. Secondary actions use an outline or quiet text treatment. Action labels describe the consequence: Prepare drafts, Save draft, Review schedule or Approve and schedule. Do not change when provider effects occur merely to simplify the interface.

Disabled actions include a nearby reason and a useful next step. Destructive actions occupy a separate danger area and retain the applicable confirmation requirements. Mobile actions may span the available width.

### Cards & Containers

White surfaces have gently rounded corners (12 px), a fine neutral border and 24 px internal padding on desktop, reducing to 16 px on narrow screens. Use a panel for a meaningful group, rather than wrapping every field in another card. Default surfaces are flat; reserve elevation for menus and dialogs.

### Navigation

Keep one primary navigation system. On desktop, use the existing approximately 232 px sidebar. Remove the duplicate row of six navigation buttons from the publishing page. Group regular work above workspace settings:

| Group | View | Contents |
|---|---|---|
| Publishing | Overview | Compact readiness summary, upcoming deliveries and recent outcomes |
| Publishing | Social accounts | Connections, destinations and account-specific capabilities |
| Publishing | Create & schedule | Destination, preparation, content review and scheduling |
| Publishing | Schedules & results | Filters, deliveries, receipts and provider links |
| Publishing | Automation inventory | Monitors, templates, replenishment and automation status |
| Workspace | Agent permissions | Scoped grants, expiry and revoke controls |
| Workspace | Sources & models | GitHub sources, model connection, funding route and budgets |
| Workspace | Advanced & billing | Subscription status, quote and billing actions |
| Workspace | Local runtime | Runtime configuration and host status |
| Workspace | Data & recovery | Separate subsections for data, deletion and recovery |
| Help | Help & support | Guides and support destinations |

These are focused views and may reuse existing routes or accessible panels. Only the selected view's content appears in its task area. Its navigation state, title and URL agree. Retain current deep links by mapping them to the relevant view. Browser Back restores the previous view. Unsaved text survives ordinary internal navigation or prompts before a destructive transition.

### Inputs & Forms

Use a readable form column around 680 px wide. Inputs have a minimum 44 px height, 12 px horizontal padding, 8 px corners and persistent labels. Text areas start at a useful height and allow vertical resizing. Place related short fields, such as date and time, on one row when space permits; long text fields span the form width.

Checkboxes and radios have separate sizing from text inputs. A checkbox is approximately 18 px square, beside its label, aligned with the first text line. The complete labelled row offers a minimum 44 px interaction area and supports keyboard activation. Multi-line labels wrap beside the control. Never place a large isolated box above a paragraph.

Put explanatory text directly beneath the relevant field. Show validation at the field and provide a summary when submission fails. Essential consent, destination identity, model charges and spending limits remain visible at the point of authorisation.

### Publishing Components

The editor and live preview are complementary columns. The preview shows the selected provider and account, exact text, thread parts where applicable, and meaningful character limits. It must use the product's existing preparation and splitting behaviour; visual preview is not provider readback evidence.

Saved preparations use a compact list with name, state, last activity and an Open action. Selecting a preparation opens its content and evidence. Avoid printing every preparation's complete metadata and diagnostics above the new-preparation form.

Delivery records show destination, status, scheduled time and the relevant next action first. Receipt identifiers and detailed evidence remain expandable. Each displayed time includes an intelligible timezone; mixed date formats must not require the user to infer how times relate.

## 5. Layout Principles

### Grid & Structure

The top bar is approximately 60 px high and contains the brand and account controls. Main content starts about 32 px from the sidebar, with a maximum useful width around 1200 px. Do not centre a narrow task inside a large padded wrapper that creates excessive left and right gutters.

Use three deliberate content arrangements:

| Task | Arrangement |
|---|---|
| Configuration and preparation | One form column, approximately 680 px, with optional short contextual help |
| Content editing and review | Approximately 680 px editor + 320 px preview, separated by 24 px |
| Accounts, preparations, schedules | A useful-width list with an optional selected-item detail view |

Two columns are justified only when both support the same task. If one side is empty, remove it and let the current step own the layout. Never balance an account-management column against all generation, saved preparation and review content in one continuous panel.

### Publishing Sequence

1. Destination: select the project and connected account. Put account connection behind a contextual link if it is needed. Project creation is a focused subtask rather than a permanently visible neighbouring form.
2. Prepare: choose manual copy or source-assisted preparation. Show source, audience, objective and context groups. Model selection, funding and mandatory consent remain associated with generation. Optional advanced inputs can expand.
3. Review: edit exact copy and inspect the preview. Show blockers and necessary source evidence. Preserve the exact-copy approval boundary and provider identity checks.
4. Schedule: choose date, time and timezone. Show an explicit summary of destination, approved text, time and any consequential charge before final authorisation.

Step navigation must reflect actual state. Persist work when moving between steps. Do not silently regenerate, alter approved copy, execute a provider action or incur charges during navigation.

### Whitespace Strategy

Use 24 px panel padding and 24 px gaps between panels. Overview contains a concise summary rather than the complete working forms. The first desktop screen should show the current title, short purpose and first useful fields or action. Remove repeated introductory headings, duplicate action cards and generic process explanations that push the task below the fold.

### Alignment & Visual Balance

Align page title, form panel and action row to the same left edge. Align labels and input edges within each group. Keep the preview adjacent to the editor on wide screens; use a sticky preview only if it fits beneath the top bar without hiding content. Technical details belong to their associated item rather than as unrelated workspace-wide banners.

### Responsive Behavior & Touch

Below roughly 900 px, replace the sidebar with a labelled menu and stack editor and preview. The content order remains destination, content, review and schedule. Use 16–20 px mobile gutters. Related field pairs collapse when their labels or values cannot fit comfortably. Preserve 16 px form text and visible focus. Avoid horizontal page overflow, fixed-height form clipping and action bars covering fields.

## 6. Design System Notes for Stitch Generation

### Language to Use

Describe a calm publishing application with warm neutral surfaces, a compact sidebar, consistent field groups and a visible preview. Use concrete task names, realistic text lengths and honest status examples. Preserve the PostSteward identity and product behaviour.

### Component Prompts

- Create the PostSteward Prepare step with a readable main form, clearly grouped source and audience inputs, a compact model-and-budget summary and inline consent rows. The next action is Prepare drafts.
- Create the Review step with the editor on the left and the chosen account's post preview on the right. Display an explicit review state and a clear route to scheduling. Put optional evidence in labelled expandable sections.
- Create Schedules & results with concise filters and delivery rows that prioritise provider, destination, time and evidence state. Put technical receipt details inside each row's detail view.

### Incremental Iteration and Acceptance

Implement focused navigation and the broken checkbox treatment first. Then split the publishing sequence, consolidate saved preparations and align the form/preview layout. Apply the shared tokens to remaining settings views.

Visually inspect populated, empty, validation, loading, disabled and error states at 390, 768, 1280 and 1920 px widths, plus 200% zoom. Check keyboard navigation, visible focus, label activation and screen-reader names. Confirm no inactive task remains exposed in the selected view, no blank half-page column survives, all required consent remains legible, and navigation preserves drafts. Exercise the existing owner-to-provider workflow to verify that layout changes preserve scope, budget, review, approval and publishing safeguards. Screenshots and successful builds alone do not prove those operational boundaries.

## References

- https://github.com/google-labs-code/stitch-skills/blob/main/plugins/stitch-utilities/skills/design-md/SKILL.md
- https://github.com/google-labs-code/stitch-skills/blob/main/plugins/stitch-design/skills/extract-design-md/SKILL.md
- https://github.com/google-labs-code/stitch-skills/blob/main/plugins/stitch-design/skills/extract-design-md/references/plain-css.md

The Google documents informed the specification method. PostSteward's proposed navigation, task sequence, dimensions and acceptance requirements are product-specific recommendations grounded in the supplied recording.
