import { DefaultSession } from "next-auth"

declare module "next-auth" {
  interface Session {
    user: {
      id: string
      userId: string
      backendToken: string
    } & DefaultSession["user"]
  }

  interface User {
    userId?: string
    backendToken?: string
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    dbUserId?: string
    userId?: string
    backendToken?: string
  }
}
