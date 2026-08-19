import NextAuth from "next-auth"
import Google from "next-auth/providers/google"
import Credentials from "next-auth/providers/credentials"
import { callInternalApi } from "@/lib/internal-api"

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000"

async function createOrGetUser(email: string, name: string) {
  try {
    // Use full email as user_id for uniqueness
    const userId = email

    // Check if user exists by email
    const checkResponse = await callInternalApi(
      `${PYTHON_API_URL}/api/users/email/${encodeURIComponent(email)}`,
      { method: "GET" },
    )

    if (checkResponse.ok) {
      // User exists, return it
      const user = await checkResponse.json()
      return user
    }

    // User doesn't exist, create new user
    const createResponse = await callInternalApi(`${PYTHON_API_URL}/api/users/`, {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        name: name,
        email: email,
      }),
    })

    if (!createResponse.ok) {
      console.error("Failed to create user:", await createResponse.text())
      throw new Error("Failed to create user")
    }

    const newUser = await createResponse.json()
    return newUser
  } catch (error) {
    console.error("Error creating/getting user:", error)
    throw error
  }
}

async function exchangeBackendToken(email: string, name: string): Promise<string> {
  const response = await callInternalApi(`${PYTHON_API_URL}/api/auth/internal/token`, {
    method: "POST",
    body: JSON.stringify({
      email,
      name,
    }),
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`Failed to exchange backend token: ${response.status} ${body}`)
  }
  const payload = await response.json()
  return String(payload.access_token || "").trim()
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
    Credentials({
      id: "email-otp",
      name: "Email OTP",
      credentials: {
        email: { label: "Email", type: "email" },
        otp: { label: "OTP", type: "text" }
      },
      async authorize(credentials) {
        try {
          if (!credentials?.email || !credentials?.otp) {
            return null
          }

          // Verify OTP with Python backend
          const response = await fetch(`${PYTHON_API_URL}/api/auth/verify-otp`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              email: credentials.email,
              otp: credentials.otp
            })
          })

          if (!response.ok) {
            console.error("OTP verification failed:", await response.text())
            return null
          }

          const user = await response.json()
          
          // Return user object for NextAuth session
          return {
            id: user.id,
            email: user.email,
            name: user.name,
            userId: user.userId,
            backendToken: user.access_token,
          }
        } catch (error) {
          console.error("Error verifying OTP:", error)
          return null
        }
      }
    }),
  ],
  pages: {
    signIn: "/login",
  },
  callbacks: {
    async signIn({ user, account, profile }) {
      try {
        // For Google OAuth, create/get user from database
        if (account?.provider === "google" && user.email && user.name) {
          await createOrGetUser(user.email, user.name)
          return true
        }
        
        // For email OTP, user is already created in verify-otp endpoint
        if (account?.provider === "email-otp" && user.email) {
          return true
        }
        
        return false
      } catch (error) {
        console.error("Sign in error:", error)
        return false
      }
    },
    async jwt({ token, user, account }) {
      // Store user info in token
      if (user && user.email) {
        if (account?.provider === "google") {
          // For Google OAuth, fetch from database
          const dbUser = await createOrGetUser(user.email, user.name || "")
          token.dbUserId = dbUser._id
          token.userId = dbUser.user_id
          token.backendToken = await exchangeBackendToken(
            user.email,
            user.name || dbUser.name || "",
          )
        } else if (account?.provider === "email-otp") {
          // For email OTP, user data already comes from verify endpoint
          token.dbUserId = (user as any).id
          token.userId = (user as any).userId
          token.backendToken = (user as any).backendToken
        }
      }
      return token
    },
    async session({ session, token }) {
      // Add custom fields to session
      if (session.user) {
        session.user.id = token.dbUserId as string
        session.user.userId = token.userId as string
        session.user.backendToken = token.backendToken as string
      }
      return session
    },
    async redirect({ url, baseUrl }) {
      // Always strip nav/tab params on login redirect so users land on PM Board
      try {
        const target = url.startsWith("/") ? `${baseUrl}${url}` : url
        const parsed = new URL(target)
        parsed.searchParams.delete("nav")
        parsed.searchParams.delete("tab")
        return parsed.toString()
      } catch {
        return baseUrl
      }
    },
  },
})
