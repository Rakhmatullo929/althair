import type { SVGProps } from "react";

/** Replace this component to swap the temporary symbol everywhere. */
export function BrandMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 40 40"
      fill="none"
      {...props}
      aria-hidden="true"
      focusable="false"
    >
      <rect width="40" height="40" rx="12" fill="#08A66A" />
      <path
        d="M11 21.8c4.2 0 6.8-2.4 8.1-7.3.3-1.2 2-1.2 2.3 0 1.3 4.9 3.9 7.3 8.1 7.3-4.2 0-6.8 2.4-8.1 7.3-.3 1.2-2 1.2-2.3 0-1.3-4.9-3.9-7.3-8.1-7.3Z"
        fill="white"
      />
      <circle cx="29" cy="11" r="2.2" fill="#BDF4D9" />
    </svg>
  );
}
