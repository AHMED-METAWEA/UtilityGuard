# Design system

Calm by default, loud only when needed. The map and the data are the loud things; everything else is quiet.

- **Type:** IBM Plex Sans (and IBM Plex Sans Arabic for RTL), bundled with the app so it works offline. Big figures use proportional numerals; tables, axes and tooltips use tabular numerals. Sentence case everywhere, no all-caps labels.
- **Themes:** light and dark are two selected token sets in `src/design/theme.css`, switched with `data-theme` (system, light, dark; the choice is remembered). No colour is hard-coded in components.
- **Network colours** come from the validated categorical palette (blue, yellow, aqua) and pass the colour-blind and contrast checks in both themes. Each network also has its own icon (drop, bolt, snowflake), so colour is never the only signal.
- **Status** is always an icon plus a label plus a colour: normal, warning, fault, offline.
- **Charts** follow the dataviz rules: 2 px lines, about 10% area wash, columns at most 24 px thick with a 4 px rounded end, hairline grid, a legend whenever there are two or more series, text in ink colours (never the series colour), crosshair and tooltip on hover, uncertainty shown as a band or a P10 to P90 bar.
- **Layout:** one page per question: Live twin (what is happening), Incidents (what to decide), Savings (where to spend less), Forecast and models (what to expect, how good the models are), Executive (how we are doing against targets).
- **Motion:** short and purposeful. The map flies to an incident, agent steps appear one by one, flow moves along healthy lines, new data updates quietly. `prefers-reduced-motion` turns it all off.
- **Accessibility:** keyboard shortcuts (Ctrl+K palette, S scenario lab, C chat, T theme), visible focus, labelled controls, tooltips with units, skeletons for loading, designed empty and error states.
