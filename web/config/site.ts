export const siteConfig = {
  name: "ProMarshal",
  description: "AI-powered project management assistant that automates coordination work",
  url: process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000",
  ogImage: "/og-image.png",
  links: {
    twitter: "https://twitter.com/promarshal",
    github: "https://github.com/promarshal/promarshal",
  },
  features: [
    {
      title: "Monitor",
      description: "Monitor conversations, transcribe meetings and extract tasks & action items",
      icon: "eye",
    },
    {
      title: "Manage",
      description: "Create, update tasks & action items from conversations and meetings",
      icon: "check-square",
    },
    {
      title: "Follow-Up",
      description: "Follow-up individually and nudge you on your tasks & action items",
      icon: "bell",
    },
    {
      title: "Learn",
      description: "Learn & build knowledge base for your project",
      icon: "brain",
    },
  ],
}

export type SiteConfig = typeof siteConfig
