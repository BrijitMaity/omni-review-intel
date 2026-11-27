# Design System: Omni Review Intel

## 1. Visual Theme & Atmosphere
Omni Review Intel blends an airy, luminous analytics workspace in Light Mode with an obsidian, high-contrast command surface in Dark Mode. The default Light Mode feels crisp, open, and readable like a modern editorial intelligence hub, while Dark Mode delivers sleek elevated depth with crisp hairline borders and high-clarity data series. Interactive Plotly visualizations use saturated, accessible emerald, electric blue, violet, amber, and coral tones so trends stand out immediately in both themes.

## 2. Color Palette & Roles

### Light Mode (Default)
* **Cool Alabaster Canvas (`#F8FAFC`)**: Primary application background establishing airy visual breathing room.
* **Slate Mist Surface (`#F1F5F9`)**: Sidebar and secondary neutral surface separating navigation and settings from content.
* **Pure Crisp Card (`#FFFFFF`)**: Elevated container surface for KPI tiles, source badges, summary cards, and inputs.
* **Deep Slate Ink (`#0F172A`)**: Primary headings, metrics, and review body text ensuring high 4.5:1+ contrast.
* **Steel Slate Muted (`#475569`)**: Secondary captions, field labels, and supporting metadata.
* **Whisper Gray Hairline (`#E2E8F0`)**: Subtle 1px structural borders framing cards and dividers.
* **Electric Cobalt (`#2563EB`) & Vivid Azure (`#0EA5E9`)**: Hero header gradient and accent links.
* **Vibrant Tangerine CTA (`#F97316`)**: Primary analyze trigger, active highlights, and download actions.

### Dark Mode
* **Obsidian Void (`#0F1117`)**: Base application background preventing eye glare.
* **Graphite Panel (`#161922`)**: Sidebar and secondary framing surface.
* **Elevated Charcoal (`#1E222D`)**: Elevated card containers, inputs, and expandable sections.
* **Luminous Snow (`#F8FAFC`)**: Primary headings and high-emphasis data readouts.
* **Cool Silver (`#94A3B8`)**: Secondary metadata and input placeholders.
* **Translucent Mist Border (`rgba(255, 255, 255, 0.12)`)**: Crisp dark-mode card edges without harsh neon outlines.
* **Electric Violet Accent (`#8B5CF6`)**: Focus accents and multi-series chart contrast.

### Shared Vibrant DataViz & Sentiment Palette
* **Emerald Positive (`#10B981`)**: Positive sentiment bars, slices, and affirmative pills.
* **Neutral Slate (`#64748B`)**: Neutral comment share and balanced baselines.
* **Vivid Coral-Red (`#EF4444`)**: Negative sentiment segments and critical issue highlights.
* **Electric Blue (`#3B82F6`) / Amber Gold (`#F59E0B`) / Cyan Glow (`#06B6D4`)**: Platform and source volume charts.

## 3. Typography Rules
* **Font Stack**: Clean modern sans-serif (`"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`) paired with monospace log viewports.
* **Hierarchy**: Bold geometric display headings (`700` weight) for topic banners, uppercase tracked labels (`0.75rem`, `600` weight, `0.08em` tracking) on KPI cards, and comfortable `1.55` line height on review summaries.

## 4. Component Stylings
* **Buttons:** Gently rounded pill-friendly rectangles (`8px` radius) with smooth `150ms` color/shadow hover transitions and clear pointer cursors. Primary action buttons use Saturated Tangerine gradients; non-action icon buttons (help tooltips and password reveals) remain borderless and transparent.
* **Cards/Containers:** Softly rounded corners (`12px` radius), `1px` crisp perimeter stroke, and whisper-soft diffused drop shadows (`0 4px 14px rgba(15, 23, 42, 0.05)` in Light Mode).
* **Inputs/Forms:** Full-width single-bordered containers with visible orange (`#F97316`) focus rings and inline transparent visibility toggles.

## 5. Layout Principles
* **Wide Workspace Grid:** Uses responsive multi-column splits (5 KPI cards across top, 1:2 breakdown vs. AI insight card, side-by-side Pros/Cons, and 2×2 interactive Plotly analytics grid).
* **Whitespace & Scannability:** Consistent `16px–20px` vertical rhythm, collapsible secondary configuration inside the sidebar, and clear separation between targeted URL analysis and cross-web discovery.
