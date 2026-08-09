import { brand } from "@workspace/brand";
import { ImageResponse } from "next/og";

export const alt = `${brand.name} — digital front office for business`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    <div
      style={{
        alignItems: "center",
        background: "#eef3ed",
        display: "flex",
        height: "100%",
        justifyContent: "center",
        padding: 70,
        width: "100%",
      }}
    >
      <div
        style={{
          background: "#071a13",
          border: "1px solid #17382a",
          borderRadius: 34,
          boxShadow: "0 30px 90px rgba(8, 36, 23, .16)",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          justifyContent: "space-between",
          padding: 58,
          width: "100%",
        }}
      >
        <div
          style={{
            alignItems: "center",
            color: "#ffffff",
            display: "flex",
            fontSize: 28,
            fontWeight: 700,
            gap: 18,
          }}
        >
          <div
            style={{
              alignItems: "center",
              border: "1px solid #27543f",
              borderRadius: 15,
              color: "#dff79e",
              display: "flex",
              fontSize: 18,
              height: 58,
              justifyContent: "center",
              width: 58,
            }}
          >
            AI
          </div>
          {brand.name}
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div
            style={{
              color: "#9ee1bd",
              fontSize: 22,
              fontWeight: 700,
              letterSpacing: 2,
              textTransform: "uppercase",
            }}
          >
            Pre-launch
          </div>
          <div
            style={{
              color: "#ffffff",
              fontSize: 68,
              fontWeight: 800,
              letterSpacing: -3,
              lineHeight: 1.05,
              marginTop: 18,
              maxWidth: 930,
            }}
          >
            Every conversation, already in motion
          </div>
        </div>
      </div>
    </div>,
    size,
  );
}
