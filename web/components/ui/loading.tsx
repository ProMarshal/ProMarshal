import Image from "next/image"

interface LoadingProps {
    size?: "sm" | "md" | "lg"
    message?: string
}

export function Loading({ size = "md", message }: LoadingProps) {
    const sizeMap = {
        sm: 24,
        md: 40,
        lg: 64
    }

    const dimension = sizeMap[size]

    return (
        <div className="flex flex-col items-center justify-center gap-3">
            <Image
                src="/logos/loading.gif"
                alt="Loading..."
                width={dimension}
                height={dimension}
                unoptimized
                priority
            />
            {message && (
                <p className="text-sm text-gray-600 animate-pulse">{message}</p>
            )}
        </div>
    )
}
