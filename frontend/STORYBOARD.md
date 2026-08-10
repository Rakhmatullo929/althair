# Althair AI — Cinematic Product Journey

**Register:** Data / Product-premium with an Immersive-3D experience layer.

**Hero moment:** the exact supplied Althair `robot-head.svg` is extruded into one persistent PBR WebGL model. The logo itself performs the entire front-office workflow; it is never replaced by a procedural robot, raster view, or second hero object.

**Motion contract:** one primary movement per shot. `0.00–0.20` enter, `0.20–0.68` readable hold, `0.68–1.00` exit into the next pose. Real `[data-shot]` geometry supplies each local `0..1` range. A nested user-orbit sits inside the authored story pose: pointer/touch drag gives unrestricted 360° yaw, keyboard gives precise inspection, release carries physical inertia, and a chapter change returns the inner orbit to the story pose without snapping.

| ID / anchor                      | Narrative job                                        | Actor pose + screen anchor                                                                  | Copy zone                                  | Primary motion | Hold proof                                   | Mobile recompose                           | Reduced motion           |
| -------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------ | -------------- | -------------------------------------------- | ------------------------------------------ | ------------------------ |
| `identity` / `#top`              | Establish the exact Althair mark before product copy | Exact extruded SVG, centered and frontal; outline wordfield crosses depth                   | Caption at lower-left; scroll cue centered | Frame/type     | Exact silhouette, bevel and enamel finish    | Mark centered above localized caption      | Layered exact SVG poster |
| `ready` / `#top`                 | Establish Althair as the operating core              | Same logo moves right and settles nearly frontal at `0.68,0.50`                             | Left, max 5 headline lines                 | Actor          | Product thesis and CTA                       | Logo centered in top stage; headline below | Exact SVG poster         |
| `receive` / `#how-receive`       | A client reaches the company from any channel        | Same logo turns 32°; side extrusion catches the moving key light                            | Left-lower safe card                       | Actor          | Signal status and channel labels hold        | Shorter 24° turn                           | Exact SVG poster         |
| `understand` / `#how-understand` | AI combines message, history, and company rules      | Same logo pitches into a controlled top scan; emerald emission rises                        | Left, 3 proof chips                        | Actor/light    | History, rules and prices lock into context  | Shallower pitch and centered crop          | Exact SVG poster         |
| `act` / `#how-act`               | AI answers or performs a permitted action            | Same logo turns 36° in the opposite direction; bevel and physical depth become the evidence | Left                                       | Actor          | Output resolves into answer, lead or booking | Shorter 29° turn                           | Exact SVG poster         |
| `remember` / `#how-remember`     | Result persists in CRM and is ready for the team     | Same logo returns to a resolved frontal pose; rear rim light creates closure                | Left; CRM proof rail below                 | Actor/light    | CRM, dialogue and owner show saved state     | Smaller frontal resolve                    | Exact SVG poster         |

## Continuity

- The live Talento reference was used only for interaction grammar: a composed identity hold opens into the product story. Althair keeps its own typography, emerald palette, exact mark, copy hierarchy, motion timings, and operational narrative.
- The same exact SVG-derived mesh, key-light direction, lacquered green PBR face, and dark metallic extrusion persist across every shot.
- The opening identity beat briefly reveals the metallic profile before settling frontally; afterward the object never spins gratuitously. Free 360° inspection is user-driven and pauses the ambient parallax.
- There are no baked-view handoffs or object swaps in the live path: only the logo pose, camera distance, and motivated light change.
- Actor perspective change completes before copy reaches its full-emphasis hold.
- The final frontal return gives spatial closure; the static fallback preserves the same exact identity.
- Reverse scroll and rapid scrub are pure `progress → pose`; no one-way callbacks own visual state.

## Render contract

The experience root exposes `data-render-path`, `data-scene-ready`, and `data-active-shot`. QA waits for a ready frame, then captures every hold and five points around each boundary using actual DOM offsets.
