# PostSteward visual system

PostSteward is social publishing infrastructure for AI agents. The interface is designed for two audiences at once: agents execute a bounded operation contract, while owners and operators establish authority, inspect consequences and decide what may continue autonomously.

The visual system therefore optimizes for **machine legibility, human auditability and calm operational density**. It must not look like email, a generic social scheduler, a cyber-security dashboard or a decorative "AI" product.

## Product grammar

The recurring product story is:

`intent -> route -> external effect -> evidence`

UI should repeatedly expose those concepts through destinations, schedules, authority scopes, receipt states and readback evidence. Provider logos are destinations, not the PostSteward identity.

## Principles

1. **Semantics before decoration.** Success, warning, danger, uncertainty and brand accent are different roles. Never use the brand colour to imply success.
2. **Neutral-first surfaces.** Most of the interface uses neutral canvas, surface, text and border tokens. Saturated colour is reserved for actions, states and small identity moments.
3. **Dense but calm.** Technical users need substantial information without an admin-form wall. Use grouping, spacing, typographic hierarchy and progressive disclosure before adding visual chrome.
4. **Human-readable, machine-shaped.** Product copy uses a high-legibility sans stack. Operation names, identifiers, hashes, timestamps and evidence use a mono stack.
5. **Consequences stay visible.** Destructive, financial, authority-changing and external-effect actions must remain visually distinct and cannot be hidden behind decorative treatments.
6. **Light and dark are modes of one system.** Components use semantic tokens so hierarchy and meaning survive both colour modes.
7. **No AI clichés.** Avoid robot heads, glowing cubes, neural-network wallpaper, glassmorphism and arbitrary neon gradients. Agent-native means exposing the operation model clearly.
8. **One dominant story per viewport.** Do not solve complexity by placing every capability in an equal-weight card. A page should tell the user what to understand next.
9. **Product evidence sits beside the claim it proves.** Use real operation, route, receipt, authority and provider-state representations instead of decorative graphics.
10. **Everything aligns deliberately.** Text, controls and product visuals align to a shared grid, baseline or edge. Accidental offsets and floating blocks are design defects.

## Composition system

The public website follows one ordered narrative:

1. **What PostSteward is** — one concise hero and one control-plane visual.
2. **The operating contract** — exact intent, bounded authority and inspectable evidence.
3. **How work moves** — capture intent, route the external effect, inspect evidence.
4. **The agent surface** — machine interfaces and operation catalogue.
5. **Pricing** — after the product model is understood, not before it.

The public page uses a consistent maximum content width and large section rhythm. Major feature sections are split compositions rather than a field of small cards. Product visuals use the lowest amount of chrome required to read as product evidence.

The owner workspace follows an operator sequence:

`destinations -> publishing -> evidence -> agent access -> sources -> Advanced -> recovery`

On large screens the workspace exposes a compact section index so long operational pages do not become an undifferentiated vertical form wall. On smaller screens that index becomes a horizontal jump rail.

### Graphics rule

Graphics are allowed only when they reduce explanation cost. Preferred graphics are:

- execution/control-plane diagrams built from real product concepts;
- exact-intent review frames;
- route timelines;
- receipt-state ledgers;
- destination identity marks;
- product screenshots when the live interface is mature enough to communicate the same idea more clearly.

Avoid generic illustrations, 3D AI objects, abstract network wallpaper, stock imagery and graphics that cannot be mapped back to a real PostSteward operation or state.

## Colour roles

The implementation uses CSS custom properties by meaning rather than raw hue names.

- `--bg-*`: canvas and surface hierarchy.
- `--text-*`: primary, secondary and tertiary information hierarchy.
- `--border-*`: default and strong separation.
- `--brand-*`: PostSteward signal accent. It is deliberately non-semantic.
- `--success-*`: verified/readback-complete states only.
- `--warning-*`: bounded uncertainty or attention.
- `--danger-*`: destructive, failed or irreversible states.
- `--focus`: keyboard focus indication independent from brand.

The brand signal is a restrained warm red-orange used for route markers, the favicon verification terminal and selective emphasis. Primary actions remain high-contrast neutral so brand colour never becomes a false status signal.

## Typography

No third-party font request is required for the product shell. The stack intentionally prefers modern platform sans fonts and system monospace fonts so the interface remains fast, private and CSP-friendly.

- Display: `ui-sans-serif`, platform system stack; tight tracking, restrained weight.
- Product UI: same sans stack at normal tracking and 14-16px body sizes.
- Machine data: `ui-monospace`, `SFMono-Regular`, `Consolas`, `Liberation Mono` fallbacks.
- Long explanatory copy stays at 16px or above.

Mono is semantic, not decorative. It is for operations, evidence, IDs, timestamps and machine-facing labels.

## Core components

### Masthead

A compact PostSteward mark plus wordmark, quiet text navigation and one clear workspace action. The mark is a routed `P` with a verified terminal: publish path plus evidence, not mail.

### Control map

The public hero uses a bounded product diagram rather than a generic architecture illustration. It shows an agent intent entering PostSteward, passing identity/scope/duplicate fences, reaching a named social destination and ending in an evidence state.

### Operational panel

A neutral bordered surface with a concise title, optional machine label, explanatory copy and controls. Panels group one authority or task domain rather than simply decorating forms.

### Record

Accounts, projects, grants, profiles and receipts use compact record cards with strong primary text and quieter evidence below. Actions sit after evidence rather than competing with it.

### Receipt/evidence

Receipt copy must preserve the distinction between reservation, verified publication, unverified publication, ambiguous external effect, drift block, failure and cancellation. Colour may reinforce these states but text remains authoritative.

### Danger zone

Deletion, recovery and irreversible authority transitions use danger tokens, explicit labels and typed confirmations. Styling must make seriousness clear without creating alarm fatigue elsewhere.

## Interaction

- 40-44px minimum primary control height.
- Visible keyboard focus via the dedicated focus token.
- Hover/pressed states change surface or border, not layout.
- Motion is optional and should explain state progression; respect `prefers-reduced-motion`.
- Disabled states remain legible and do not masquerade as available operations.
- Mobile navigation must collapse deliberately rather than wrapping arbitrary desktop links into multiple rows.
- Empty, sparse, dense, loading, error and ambiguous states all need designed treatments.

## Repository boundary

Visual work must preserve existing form IDs, names, operation bindings, confirmation requirements and JavaScript contracts unless a behaviour change is separately reviewed. The design layer cannot broaden authority, infer success or alter external-effect handling.
