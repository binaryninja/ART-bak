// Build-time public mock switch; NEXT_PUBLIC_ variables are readable in the browser.
// Set NEXT_PUBLIC_MOCK=1 on Vercel for an entirely mocked site without a backend.
export const MOCK = process.env.NEXT_PUBLIC_MOCK === "1";
