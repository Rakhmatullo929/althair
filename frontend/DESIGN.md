# Althair AI — Design Tokens

> Operational intelligence under studio glass — warm paper UI orbiting a deep-green AI core.

**Theme:** light product interface with a cinematic green studio stage

## Tokens — Colors

| Name             |     Value | Token            | Role                                            |
| ---------------- | --------: | ---------------- | ----------------------------------------------- |
| Althair Paper    | `#fbfcf8` | `--paper`        | Page and readable copy surfaces                 |
| Intelligence Ink | `#14231c` | `--ink`          | Primary type and controls                       |
| Deep Signal      | `#071a13` | `--deep`         | Cinematic depth, dark sections, shadow color    |
| Althair Green    | `#087b50` | `--primary`      | Single primary accent and robot shell           |
| Circuit Mint     | `#dff2e7` | `--primary-soft` | Context fields, quiet illumination              |
| Photon Lime      | `#dff79e` | `--lime`         | Rare emissive energy: eyes and live signal only |
| Operational Grey | `#5f6d65` | `--muted`        | Secondary copy, utility labels                  |

Rules: Althair Green is the only interactive accent. Photon Lime is reserved for emitted light and must never become a general UI color.

## Tokens — Typography

- **Display:** Geologica, variable weight `520–620`, slightly compressed tracking.
- **Body:** Manrope, weight `400–700`, readable measures up to `62ch`.
- **Telemetry:** system monospace, weight `700`, uppercase with `0.12–0.16em` tracking.
- **Scale:** `11 · 12 · 14 · 16 · 18 · 24 · 38 · 56 · 76 · 98px`.
- **Line height:** `0.92–1.0` display · `1.6–1.75` body.

## Layout

- Desktop scene: copy owns the left `40%`; the robot focal point sits at screen anchor `0.68, 0.50`.
- Mobile scene: model owns the top `43svh`; copy becomes a grounded card below it.
- Real DOM chapters drive the WebGL state. The canvas never contains essential body copy.

## Signature

One extruded Althair robot head persists across the product story. It does not spin idly: every pose proves a specific stage — receive, understand, act, remember.
