"use server"

import { signIn } from "@/lib/auth"

export async function handleGoogleSignIn() {
  await signIn("google", { redirectTo: "/projects" })
}
