// Registers the jest-dom matchers (toBeInTheDocument, toHaveTextContent, …)
// and their Vitest type augmentation.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
});

// jsdom implements neither of these, and both are reached by components
// under test: ToastProvider mints ids with crypto.randomUUID, and the
// reactflow/recharts trees observe their container size.
if (!globalThis.crypto?.randomUUID) {
  Object.defineProperty(globalThis, "crypto", {
    value: { ...globalThis.crypto, randomUUID: () => Math.random().toString(36).slice(2) },
    configurable: true,
  });
}

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// next/navigation is a server-aware module; route components that call
// useRouter/useParams need a stub in a bare jsdom environment.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));
