import { expect, test } from "@playwright/test";

test("the exact Althair logo is a closed outward-facing solid", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/ru");

  const journey = page.locator("[data-cinematic-journey]");
  await expect(journey).toHaveAttribute("data-render-path", "live");
  await expect(journey).toHaveAttribute(
    "data-solid-geometry",
    "watertight-manifold",
    { timeout: 15_000 },
  );

  await expect(journey.locator("canvas")).toHaveCount(1);
  await expect(journey).toHaveAttribute("data-actor-meshes", "1");
  await expect(journey).toHaveAttribute(
    "data-model-type",
    "exact-svg-logo-webgl-3d",
  );
  await expect(journey).toHaveAttribute("data-solid-material-side", "front");
  await expect(journey).toHaveAttribute("data-solid-components", "6");
  await expect(journey).toHaveAttribute("data-solid-parts", "6");
  await expect(journey).toHaveAttribute("data-solid-holes", "3");

  for (const attribute of [
    "data-solid-boundary-edges",
    "data-solid-degenerate-triangles",
    "data-solid-inconsistent-edges",
    "data-solid-inward-cap-triangles",
    "data-solid-inward-components",
    "data-solid-misaligned-normal-vertices",
    "data-solid-non-manifold-edges",
  ]) {
    await expect(journey).toHaveAttribute(attribute, "0");
  }

  const metrics = await journey.evaluate((element) => {
    const read = (name: string) => Number(element.getAttribute(name));
    return {
      backCaps: read("data-solid-back-cap-triangles"),
      caps: read("data-solid-cap-triangles"),
      depthSpread: read("data-solid-depth-spread"),
      frontCaps: read("data-solid-front-cap-triangles"),
      maxDepth: read("data-solid-max-depth"),
      minDepth: read("data-solid-min-depth"),
      outwardCaps: read("data-solid-outward-cap-triangles"),
      sides: read("data-solid-side-triangles"),
      signedVolume: read("data-solid-signed-volume"),
    };
  });

  expect(metrics.frontCaps).toBeGreaterThan(0);
  expect(metrics.backCaps).toBe(metrics.frontCaps);
  expect(metrics.outwardCaps).toBe(metrics.caps);
  expect(metrics.sides).toBeGreaterThan(0);
  expect(metrics.signedVolume).toBeGreaterThan(0);
  expect(metrics.minDepth).toBeGreaterThan(0.55);
  expect(metrics.maxDepth).toBeLessThan(0.57);
  expect(metrics.depthSpread).toBeLessThanOrEqual(1e-6);
});
