import { InviteAcceptClient } from "./client"

interface Props {
  params: Promise<{ token: string }>
}

export default async function InviteAcceptPage({ params }: Props) {
  const { token } = await params
  return <InviteAcceptClient token={token} />
}
