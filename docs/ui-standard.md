# UI standard

The standard every screen meets **where the requirements and the wireframes do not say**. The
requirements and the wireframes win every conflict; this fills their silence. It decides *how* a
screen works, never *what* the product does: a field, a rule or a screen the requirements do not
ask for is not added because this document mentions it — that is a question for
[`open-questions.md`](open-questions.md).

Each rule is meant to be checked, not admired. The [checklist](#checklist) at the end is the same
rules in the form a reviewer ticks.

## How the factory uses this

1. **Design.** A run that adds or changes a screen with no wireframe writes a short *screen
   description* in its Design section — purpose, what is on it, its states, its one main action,
   its labels in plain words — following this standard. A person approves it at the design gate
   (or, below the High tier, at the merge gate with the rest of the change).
2. **Build.** The build follows this standard and the starter's design system (`src/ui/`): its
   tokens and components first, a new one only when none fits.
3. **Tests.** A screen's tests find what they check by role and accessible name, the way a person
   with a screen reader would. A screen that is not accessible fails its own tests.
4. **Review.** The reviewer checks every UI change against the [checklist](#checklist). A missed
   accessibility rule or a missing state is a finding, not a matter of taste.

## 1. Accessible — WCAG 2.2 AA

- **Contrast.** Text at least **4.5:1** against its background; large text (24px, or 18.66px bold)
  and the parts of a control you need to see to use it (input borders, focus rings, icons that
  carry meaning) at least **3:1**. The design tokens are chosen to meet this in both themes; a
  colour outside the tokens needs its ratio checked.
- **Keyboard.** Everything a mouse can do, the keyboard can: Tab reaches every control in reading
  order, Enter and Space activate, Escape closes a dialog or menu. Nothing traps focus except an
  open dialog, which returns focus to what opened it when it closes.
- **Visible focus.** Every focusable element shows a clear focus ring (`:focus-visible`), never
  removed with `outline: none` and nothing in its place. A focused element is never hidden behind a
  sticky header.
- **Labels.** Every field has a visible `<label>` tied to it. A placeholder is not a label. An
  icon-only button has an accessible name (`aria-label`) that says what it does.
- **Real elements.** An action is a `<button>`; going somewhere is a link `<a href>`. Never a
  clickable `<div>` or `<span>`. Headings are real headings, in order, one `<h1>` per screen.
  Landmarks: `header`, `nav`, `main`; a skip link to the main content is the first thing on the page.
- **Images.** A meaningful image has `alt` text saying what it shows; a decorative one has `alt=""`.
- **Not colour alone.** Status, errors and required fields are said in words or an icon with text,
  not only by colour.
- **Targets.** Controls are at least **24×24px** (the tokens give 40px); small links in running
  text are exempt.
- **Announcements.** A change the person did not cause by moving focus — a saved message, a list
  that finished loading, an error — is announced (`role="status"` for news, `role="alert"` for
  errors).
- **Motion.** Respect `prefers-reduced-motion`; no flashing; nothing moves for more than five
  seconds without a way to stop it.
- **Zoom.** The page works at 200% zoom and with larger browser text: sizes in `rem`, no fixed
  heights on text.

## 2. Easy for people who are not technical

- **One main action per screen**, styled as the primary button. Everything else is secondary.
- **Plain words.** The user's words, not the system's: "Save changes", not "Submit"; "Customer",
  not "Entity". Sentence case. Short sentences.
- **No codes, ids or internal names on screen** — no database ids, enum values (`PENDING_REVIEW`),
  error codes, stack traces or field names (`createdAt`). Show the human label; an id appears only
  where the requirements name it as something a user reads (an order number).
- **Say what will happen.** A button names its result ("Delete 3 records", not "OK"). A
  confirmation says what changes and whether it can be undone.
- **Destructive actions** — delete, remove, cancel something sent — either ask first (a dialog that
  names the thing and the consequence, with the safe choice as the default focus) or let the
  person undo it for a few seconds. Never both missing.
- **Helpful empty states.** An empty list says why it is empty and what to do next ("No invoices
  yet. Create the first one."), with the action when the person may take it.
- **Errors say how to recover**, in plain words: what went wrong, whether anything was saved, and
  what to do ("We could not save your changes. Check your connection and try again.").
- **Recognition over recall.** Show choices rather than asking people to type codes; remember the
  last filter or sort on a list where the requirements allow.

## 3. Every screen's states

Every screen and every part of a screen that loads data has all of these, designed, not left to
chance:

| State | What the person sees |
|---|---|
| **Loading** | Nothing for the first 300ms; then a skeleton of the content's shape (lists, cards) or a spinner with a label (a single value). Never a blank page. |
| **Empty** | The empty state above: why, and the next step. |
| **Error** | What went wrong, in plain words, and a way out — *Try again*, or a link back. The rest of the screen keeps working where it can. |
| **Success** | The content. After a change, a short confirmation (a toast or an inline message) saying what was done. |
| **Partial / no permission** | What the person may not do is hidden or disabled with a reason, never an action that fails after they try. |

A screen's description, at the design step, lists its states; its tests cover the ones the
criteria name.

## 4. Forms

- **Required fields are marked in words** ("(required)") — or, when most fields are required,
  the optional ones are marked "(optional)". One convention per project.
- **Validate inline**, after the person leaves a field or submits — not on every keystroke. The
  message sits under the field, is tied to it (`aria-describedby`, `aria-invalid`), and says **how
  to fix it** ("Enter a date after 1 January 2024", not "Invalid date").
- **On submit with errors**, move focus to the first field in error (or to an error summary
  listing them as links). The person never hunts for what failed.
- **Never lose what was typed.** A failed save keeps every value; leaving a form with unsaved
  changes asks first.
- **Sensible defaults** — today's date, the person's own team, the most common choice —
  where the requirements allow. The right input type (`email`, `tel`, `number`, `date`), so the
  phone shows the right keyboard, and `autocomplete` where it applies.
- **One column**, labels above fields, the primary button at the end. The button stays enabled and
  says why when the form is not ready; while saving it shows progress and ignores a second click.
- **Limits are said up front** — a hint under the field ("Up to 200 characters", "PDF or JPG,
  10 MB at most"), not only in the error.

## 5. Consistent — one small design system

- **Tokens, not values.** Colour, type size, spacing and radius come from the tokens
  (`src/ui/tokens.css` in the React starter). A raw colour, pixel size or font in a component is a
  finding.
  - Colour: background, surface, text, muted text, border, primary, danger, success, warning, each
    with its *on* colour for text placed on it.
  - Type scale: five sizes (small, base, large, extra large, page title), one font stack.
  - Spacing: a 4px-based scale; radius: small and medium.
- **The same pattern every time:**
  - **Layout** — the app shell (below); one `<h1>` page title, the main action at its right.
  - **Lists** — a table on wide screens; the same columns order, the same row actions in the same
    place; cards or stacked rows on narrow screens.
  - **Forms** — the field component, with its label, hint and error in the same places.
  - **Dialogs** — a native `<dialog>` (or one component), title, body, actions with the primary
    last; Escape and a visible *Cancel* close it.
  - **Toasts** — short news after an action, announced, gone after about five seconds, never the
    only place an error is shown.
- **Light and dark themes.** Both from the same tokens; the operating system's choice by default,
  with an override (`data-theme="light|dark"` on `<html>`). Both meet the contrast rules.

## 6. Responsive

- **Works from 360px wide** up to large screens, with **no sideways scrolling of the page**. A
  wide table scrolls inside its own container or becomes stacked rows.
- Mobile first: one column on narrow screens; the side navigation collapses behind a *Menu* button
  (with `aria-expanded`).
- Touch and pointer both work; nothing depends on hover alone.

## 7. Today's conventions

- **App shell:** a top bar (product name, the person's account), a side navigation listing the
  main areas with the current one marked (`aria-current="page"`), and the main region.
- **Search on long lists** (more than about 20 rows), at the top of the list, with the result
  count. Filters show what is applied and clear in one action.
- **Tables page and sort.** A sortable column header is a button that says its sort
  (`aria-sort`); paging says where you are ("21–40 of 312"). Server-side when the data is large.
- **Dates, times and numbers in the user's locale** — `Intl.DateTimeFormat` and
  `Intl.NumberFormat`, never a hand-built format string. Times in the user's time zone unless the
  requirements say otherwise. Money with its currency.
- **Waits over 300ms** show a skeleton or a labelled spinner; waits over a few seconds say what is
  happening.
- **The browser's back button and links work**: each screen has its own address; opening a link
  in a new tab works.

## The screen description

Written in the run's Design section when a screen has no wireframe — short, for a person to
approve:

```
### Screen — <name>
- **Purpose:** what the person comes here to do, in one sentence.
- **Main action:** the one primary button, and its label.
- **On it:** the page title; each field, column or section, by the label the person reads.
- **States:** loading, empty (its message and next step), error, success (its confirmation),
  and what someone without permission sees.
- **Words:** the labels and messages, in plain words.
```

## Checklist

The reviewer ticks these for every change that adds or changes a screen. An unticked box is a
finding; *a11y* and *states* items are at least major.

- [ ] *a11y* — Every field has a visible label tied to it; icon-only buttons have a name.
- [ ] *a11y* — Actions are `<button>`, navigation is `<a href>`; no clickable `div`.
- [ ] *a11y* — One `<h1>`; headings in order; `header`, `nav`, `main` landmarks; skip link.
- [ ] *a11y* — Everything works by keyboard; focus is visible; dialogs trap and return focus.
- [ ] *a11y* — Text 4.5:1, controls and large text 3:1, in light and dark; only token colours.
- [ ] *a11y* — No meaning by colour alone; images have the right `alt`.
- [ ] *a11y* — Errors and news are announced (`role="alert"` / `role="status"`).
- [ ] *states* — Loading (after 300ms), empty, error with a way out, and success are all there.
- [ ] *states* — What a person without permission sees is designed.
- [ ] *forms* — Required marked in words; inline messages say how to fix; focus goes to the first
      error; nothing typed is lost on a failed save.
- [ ] *words* — Plain words; no ids, codes, enum values or internal names on screen.
- [ ] *words* — Buttons name their result; destructive actions confirm or undo.
- [ ] *consistency* — Tokens and the shared components; the project's list, form, dialog and toast
      patterns.
- [ ] *responsive* — Works at 360px with no sideways page scroll; the menu collapses.
- [ ] *conventions* — Dates and numbers through `Intl`; long lists search, tables page and sort.
- [ ] *tests* — Tests find elements by role and label, not by test id, class or structure.
