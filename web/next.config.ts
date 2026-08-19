import type { NextConfig } from "next"
import { withSentryConfig } from "@sentry/nextjs"

const nextConfig: NextConfig = {
  /* config options here */
}

export default withSentryConfig(nextConfig, {
  // Find these in Sentry → Settings → General → Organization Slug / Project Slug
  org: "promarshal",           // update with your exact Sentry org slug
  project: "promarshal-web",   // update with your exact Sentry project slug

  // Only print logs for uploading source maps in CI
  silent: !process.env.CI,

  // Upload source maps so stack traces are readable in Sentry
  widenClientFileUpload: true,

  // Hides source maps from generated client bundles
  sourcemaps: {
    disable: false,
    deleteSourcemapsAfterUpload: true,
  },

  // Automatically tree-shake Sentry logger statements
  disableLogger: true,
})
