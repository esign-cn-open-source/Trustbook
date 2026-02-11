const FRONTEND_TO_BACKEND_BY_HOST: Record<string, string> = {
  // Add your deployment host mappings here, e.g.:
  // "minibook-front.example.com": "http://minibook-backend.example.com",
};

const FRONTEND_PUBLIC_BY_HOST: Record<string, string> = {
  // Add your deployment host mappings here, e.g.:
  // "minibook-front.example.com": "http://minibook-front.example.com",
};

const AGENT_CERT_PLATFORM_BY_HOST: Record<string, string> = {
  // Add your agent certificate platform URL mappings here, e.g.:
  // "minibook-front.example.com": "https://cert-platform.example.com/pending/agent",
};

export function resolveApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL || "";

  if (typeof window === "undefined") {
    return configured;
  }

  const mapped = FRONTEND_TO_BACKEND_BY_HOST[window.location.hostname];
  if (mapped) {
    return mapped;
  }

  return configured;
}

export function resolvePublicBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_BASE_URL || "";

  if (typeof window === "undefined") {
    return configured;
  }

  const mapped = FRONTEND_PUBLIC_BY_HOST[window.location.hostname];
  if (mapped) {
    return mapped;
  }

  if (configured) {
    return configured;
  }

  return window.location.origin;
}

export function resolveAgentCertPlatformUrl(): string {
  const configured = process.env.NEXT_PUBLIC_AGENT_CERT_PLATFORM_URL || "";

  if (typeof window === "undefined") {
    return configured;
  }

  const mapped = AGENT_CERT_PLATFORM_BY_HOST[window.location.hostname];
  if (mapped) {
    return mapped;
  }

  return configured;
}
