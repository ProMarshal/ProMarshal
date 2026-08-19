import { auth } from "@/lib/auth"
import { redirect } from "next/navigation"

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const session = await auth()

  if (!session?.user) {
    redirect("/login")
  }

  return (
    <div className="h-screen overflow-hidden bg-[#f5f6f8]">
      <main className="h-full">
        {children}
      </main>
    </div>
  )
}
