const INTERNAL_SERVICE_SECRET = process.env.INTERNAL_SERVICE_SECRET || ""

function buildInternalHeaders(extraHeaders?: HeadersInit): HeadersInit {
  const base: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Request-Timestamp": Math.floor(Date.now() / 1000).toString(),
  }
  if (INTERNAL_SERVICE_SECRET) {
    base["X-Internal-Secret"] = INTERNAL_SERVICE_SECRET
  }
  return {
    ...base,
    ...(extraHeaders || {}),
  }
}

export async function callInternalApi(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  return fetch(url, {
    ...options,
    headers: buildInternalHeaders(options.headers),
  })
}

