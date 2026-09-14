---
name: Savvy Preview Library
description: A quiet extension of the existing charcoal and brass review desk.
---
# Design

## Overview
**Creative North Star: "Let the footage lead."**
The library inherits the existing Savvy Review ground, panels, warm accent and system typography. It gives real photos and videos the largest visual area. It does not introduce a replacement brand or decorative motion.

**Key Characteristics:**
- Real imagery ahead of interface decoration.
- Reversible choices beside native playback.
- Availability expressed in words as well as emphasis.

## Colors
Charcoal ground (#141419), raised panel (#1c1c22), warm text (#eeeae2), secondary text (#b0afb8), brass selection (#d1bb90). The light appearance uses an off-white ground (#f5f3ee), near-white panel (#fffefa), dark text (#25242b), muted text (#625d65), and dark brass (#705a31). Error and saved text have separate semantic tokens.

**The Selection Rule.** Brass identifies the selected view, selected item, selected choice and keyboard focus. It is not spread across decorative surfaces.

## Typography
One native system sans-serif family. Page heading (1.35rem), section heading (1.12rem), main controls (15px), secondary details (approximately 12–14px). Dense labels remain subordinate to previews and choice controls. User zoom is supported without disabling viewport scaling.

## Layout
Desktop: a 220px filter sidebar and flexible content region. The selected preview and 250px details pane share one row above the thumbnail shelf. At 1000px the details move below the preview. At 650px filters become a compact two-column section with full-width search, and the library becomes a two-column shelf. Grid rendering is capped at 36 items per page.

**The Whole Frame Rule.** Selected media uses contain sizing. Thumbnail crops are navigation aids, not the judgment view.

## Elevation & Depth
Single borders divide surfaces. No shadows, floating decoration or blur. Selection is a border/accent change; loading uses static neutral placeholders.

## Shapes
Controls use modest six-pixel corners; the review pane uses eight-pixel corners. Repeated image tiles keep one consistent shape.

## Components
- Native video controls with explicit playback speed and no autoplay.
- Three equal Keep/Maybe/Pass controls; undo and reset stay visible.
- Event, search, choice and ready/all controls use native fields and buttons.
- Source path is progressive detail, with text wrapping rather than clipping.
- Distinct loading, empty, request-error and failed-media states expose recovery.
- Dark, light and system appearance are available for the current visit.

## Do's and Don'ts
- Keep source filenames and paths literal and safely rendered as text.
- Keep every decision reversible and show save failures beside the selected item.
- Never infer permission, client identity or production quality from a folder label.
- Never autoplay sound or hide a download behind browsing.

## Review scope
Implementation records only. Lead owns real-data browser proof at desktop and 390px, plus the independent finish verdict. The one mechanical design pass ran with missing optional parsers and reported compact type hierarchy; it did not evaluate computed contrast and is not a clean audit. Compact type is deliberate Operate-mode density. No raster imagery was created by this frontend: media arrives from local verified preview endpoints, retaining source provenance in the library records.

Final real-data review: desktop dark and390px light passed the lead's visual check; no horizontal overflow at390px. Native video played, a portrait photo decoded, choices persisted and undid correctly, empty search recovered, and one new preview was prepared from the visible button. Independent review found a stale-choice refresh race; corrected and regression-checked. No outstanding material screen finding.
