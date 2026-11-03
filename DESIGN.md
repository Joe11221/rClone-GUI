---
name: rClone GUI
description: A calm, dense control room for scheduled rclone backups.
colors:
  accent-dusk: "#7b97ff"
  accent-dusk-contrast: "#0b0f1a"
  bg-graphite: "#121316"
  surface-graphite: "#1b1d22"
  surface-raised: "#252830"
  border-graphite: "#343944"
  text-primary: "#eceef2"
  text-muted: "#a4abb8"
  accent-ink: "#3f55d9"
  bg-paper: "#f3f4f7"
  surface-paper: "#ffffff"
  surface-paper-raised: "#eef0f4"
  border-paper: "#d5d9e1"
  text-ink: "#1c2028"
  text-ink-muted: "#566071"
  status-ok: "#2fd08a"
  status-fail: "#f87171"
  status-warn: "#f5b400"
  status-info: "#22bedc"
typography:
  title:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    letterSpacing: "-0.01em"
  stat:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "1.35rem"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "0.72rem"
    fontWeight: 600
    letterSpacing: "0.05em"
  mono:
    fontFamily: "Cascadia Code, Consolas, Monaco, Courier New, monospace"
    fontSize: "0.85rem"
rounded:
  badge: "0.4rem"
  button: "0.5rem"
  card: "0.75rem"
  pill: "999px"
spacing:
  table-cell-y: "0.7rem"
  table-cell-x: "1rem"
  card-header: "0.8rem 1rem"
components:
  button-primary:
    backgroundColor: "{colors.accent-dusk}"
    textColor: "{colors.accent-dusk-contrast}"
    rounded: "{rounded.button}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.button}"
  card:
    backgroundColor: "{colors.surface-graphite}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.card}"
  input:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.button}"
  badge-success:
    textColor: "{colors.status-ok}"
    rounded: "{rounded.badge}"
---

# Design System: rClone GUI

## Overview

**Creative North Star: "The Quiet Control Room"**

This is the canonical **Graphite** theme, the default of six user-selectable themes (Liquid Glass, Keycaps, Terminal, Synthwave and Brutalist are palette and shape variants not covered here). The interface is a calm, dense, glanceable surface for someone who checks in briefly to learn whether last night's backups ran. Neutral cool-grey surfaces recede; a single periwinkle accent marks navigation and primary action, and status colour (green, red, amber, cyan) is the only thing allowed to be loud.

Density is moderate and tabular. Information is carried by text, tinted badges and progress bars, not ornament. Dark is the default mode, with a matching light mode ("paper") that keeps the same structure.

**Key Characteristics:**
- Flat tonal layering: surface vs raised surface with 1px borders
- One accent, used for navigation state, primary actions, icons and progress
- Soft status badges: tinted fill, coloured text
- Uppercase small labels for table headers and stat captions
- Built on vendored Bootstrap 5 with theme variables mapped over it

## Colors

A cool-grey graphite ramp with one periwinkle accent; status hues stay separate from the accent.

### Primary
- **Dusk Periwinkle** (#7b97ff): The dark-mode accent. Active nav link (at 14% tint), primary buttons, links, focus ring (20% glow), progress bars, card-header icons, `code`. In light mode it becomes **Ink Indigo** (#3f55d9) for contrast.

### Neutral
- **Graphite Floor** (#121316): Page background in dark mode.
- **Graphite Surface** (#1b1d22): Navbar, cards, modals.
- **Raised Graphite** (#252830): Form fields, progress track, hover fills, secondary badges.
- **Graphite Rule** (#343944): All borders and dividers.
- **Mist Text** (#eceef2) / **Slate Muted** (#a4abb8): Primary and secondary text.
- Light mode: **Paper** (#f3f4f7) page, **White** (#ffffff) surface, **Paper Raised** (#eef0f4), **Paper Rule** (#d5d9e1), **Ink** (#1c2028) text, **Ink Muted** (#566071).

### Status
- **Run Green** (#2fd08a) completed, **Fault Red** (#f87171) failed, **Late Amber** (#f5b400) warning, **Signal Cyan** (#22bedc) info. Light mode darkens the text colours (#0a6640, #b42323, #7a5400, #08627a; all six themes define these as `--app-ok`, `--app-fail`, `--app-warn`, `--app-info`).

### Named Rules
**The One Accent Rule.** Periwinkle is the only non-status hue. Never use it to signal success or failure.

**The Status Is Loud Rule.** Status colours appear as tinted badges and progress, never as large fills or decorative colour.

## Typography

**Font:** the system UI stack (Bootstrap default: system-ui, Segoe UI, Roboto) for everything; **Mono:** Cascadia Code / Consolas for paths, flags, command fragments and file lists.

**Character:** Plain and native; no webfonts. Hierarchy comes from weight, size and case rather than typeface changes.

### Hierarchy
- **Title** (600, ~1.5rem, -0.01em): Page headings (h4-level).
- **Stat** (600, 1.35rem, 1.2): Dashboard figures.
- **Body** (400, 1rem): Forms, table cells.
- **Label** (600-700, 0.72-0.78rem, +0.05em, uppercase): Table headers and stat captions.
- **Mono** (0.85rem): File lists, paths, `code`.

### Named Rules
**The Paths Are Mono Rule.** Anything the user might copy into rclone (paths, flags, remote names) is set in monospace.

## Layout

Full-width fluid container (`container-fluid`) under a sticky top navbar that collapses on small screens. Content is stacked Bootstrap cards, tables and a stat-card row on the dashboard. Table cells use 0.7rem 1rem padding; card headers 0.8rem 1rem. Large lists (files, failures) are built to stay readable at thousands of rows.

## Elevation & Depth

Flat by default. Depth comes from tonal steps (floor, surface, raised) and 1px borders. The only shadow is a faint card hairline (`box-shadow: 0 1px 2px rgba(0,0,0,0.12)`). Focus is shown by an accent border plus a 0.2rem 20% accent glow.

### Named Rules
**The Tonal Depth Rule.** Express hierarchy with surface steps and borders, not shadows. Heavy shadows, glass blur and glow belong to other themes.

## Shapes

Soft but not bubbly: badges 0.4rem, buttons and inputs 0.5rem, stat icon tiles 0.7rem, cards and modals 0.75rem, dropdowns 0.75rem, progress bars fully round (999px). Borders are 1px in the rule colour.

## Components

### Buttons
- **Shape:** 0.5rem radius.
- **Primary:** accent fill, dark contrast text; hover brightens (`filter: brightness(1.1)`).
- **Outline primary:** accent border and text, fills with accent on hover.
- **Outline secondary:** muted text and rule border; hover shifts to raised fill and full text colour.

### Badges
- Tinted 16% background with coloured text, weight 600, 0.4rem radius; used for run and job status. Secondary badge uses raised surface and muted text.

### Cards / Containers
- 0.75rem radius, surface background, 1px rule border, hairline shadow; headers weight 600 with an accent icon.

### Inputs / Fields
- Raised-surface fill, rule border, 0.5rem radius. Focus: accent border plus soft accent glow. Placeholder muted at 70%.

### Navigation
- Sticky navbar on surface with bottom border. Links are muted with 0.5rem radius; hover gains a raised fill; active link takes the accent colour on a 14% accent tint. A small running-count badge sits beside Running. Theme picker and connection status live at the right.

### Stat Card
- Icon tile (2.6rem, 0.7rem radius, 14% accent tint) beside an uppercase caption and a stat value.

## Do's and Don'ts

### Do:
- **Do** drive every colour from the `--app-*` theme variables so all themes and both modes keep working.
- **Do** keep status in tinted badges and progress bars.
- **Do** set paths, flags and remote names in monospace.
- **Do** keep light mode in step with dark; both are first-class.

### Don't:
- **Don't** use the accent colour to mean success or failure.
- **Don't** add shadows, blur or glow to Graphite; those are other themes' signatures.
- **Don't** hardcode hex values in components; add a theme variable.
- **Don't** introduce webfonts or a CDN dependency; assets are vendored.
