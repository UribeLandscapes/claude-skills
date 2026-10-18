Ideas adapted from pbakaus/impeccable, Apache-2.0

# Craft floor

Use after choosing a direction, when a render is available. The project's brief and
intentional choices take precedence. These checks assess execution; they do not select
a palette, typeface, or layout. Scanner findings are prompts for review, not proof of
poor design. Source patterns cannot establish contrast, overflow, touch targets, or
actual line length.

## Verify the result

- Check text contrast against every surface it crosses: aim for 4.5:1 for ordinary
  text and placeholders, 3:1 for large text. Test muted and disabled treatments too.
- Make depth legible. Offset and blur should explain elevation; inspect colored
  zero-offset halos before keeping them.
- Check spacing in computed styles. Related controls belong together; section gaps
  should establish rhythm. Give headings clear ownership of the content beneath them.
- Read real copy at narrow and wide widths. Start body measure around 65–75 characters,
  then adjust for the face and task. Keep tracking at or above -0.04em; check glyph
  collisions, wrapping, clipping, and readable small labels.
- If motion serves the brief, give one moment a specific purpose. Avoid repeating the
  same entrance everywhere. Content must remain available with reduced motion and
  before animation starts.
- Exercise hover, keyboard focus, disabled, loading, empty, and error states. Test real
  controls, long content, small screens, and adequate touch areas.
- Inspect browser surfaces: selection, caret, scrollbar, focus ring, and link underline.
  Integrate them with the design while preserving visibility and familiar behavior.
  Use tabular numerals where changing numbers should align.
- Read controls and errors aloud. Name the action, the problem, and the recovery using
  the product's own terms. Verify every promised capability is present and findable.

## Refuse an unexplained default

Ask what each treatment contributes before keeping it:

- Repeated icon cards, or cards nested inside cards, that obscure the content hierarchy.
- A giant hero metric without a sourced claim or a useful comparison.
- Tiny uppercase labels and decorative section numbers that repeat a heading instead
  of clarifying sequence.
- A modal for a task that could remain in context without interrupting the user.
- Stripes, grid overlays, blob clipping, neon halos, or bouncing status dots that have
  no relationship to the subject or interaction.
- Border plus oversized shadow, or hard block shadows, inherited without a deliberate
  depth system.
- Hype copy or invented statistics standing in for concrete product information.

A documented brand choice can earn any treatment. Keep it intentional; use
`unslop-ignore` for scanner matches chosen on purpose.
