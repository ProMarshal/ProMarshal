export function createAuthFetch(token: string) {
  return (url: string, options: RequestInit = {}): Promise<Response> => {
    const baseHeaders: Record<string, string> = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    }
    return fetch(url, {
      ...options,
      headers: {
        ...baseHeaders,
        ...(options.headers || {}),
      },
    })
  }
}

