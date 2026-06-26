import { vi } from "vitest";

export const mockReplace = vi.fn();
export let mockSearchParams = new URLSearchParams();

export function resetNavigationMocks(search = "") {
  mockReplace.mockReset();
  mockSearchParams = new URLSearchParams(search);
}

export function useRouter() {
  return { replace: mockReplace };
}

export function useSearchParams() {
  return mockSearchParams;
}
