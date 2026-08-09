import { brand } from "@workspace/brand";
import { ImageResponse } from "next/og";

export const alt = `${brand.name} — AI assistant for business`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    <div
      style={{
        alignItems: "center",
        background: "#f4fbf7",
        display: "flex",
        height: "100%",
        justifyContent: "center",
        padding: 70,
        width: "100%",
      }}
    >
      <div
        style={{
          background: "white",
          border: "1px solid #dcebe3",
          borderRadius: 40,
          boxShadow: "0 30px 90px rgba(8, 80, 55, .10)",
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
            display: "flex",
            fontSize: 28,
            fontWeight: 700,
            gap: 18,
          }}
        >
          <div
            style={{
              alignItems: "center",
              background: "#08a66a",
              borderRadius: 15,
              color: "white",
              display: "flex",
              fontSize: 32,
              height: 58,
              justifyContent: "center",
              width: 58,
            }}
          >
            <div
              style={{
                background: "white",
                borderRadius: 999,
                display: "flex",
                height: 18,
                width: 18,
              }}
            />
          </div>
          {brand.name}
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div
            style={{
              color: "#08a66a",
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
              color: "#111827",
              fontSize: 68,
              fontWeight: 800,
              letterSpacing: -3,
              lineHeight: 1.05,
              marginTop: 18,
              maxWidth: 930,
            }}
          >
            AI assistant for business growth
          </div>
        </div>
      </div>
    </div>,
    size,
  );
}
