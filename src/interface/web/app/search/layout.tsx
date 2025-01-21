import type { Metadata } from "next";

import "../globals.css";
import { ContentSecurityPolicy } from "../common/layoutHelper";
import { Toaster } from "@/components/ui/toaster";

export const metadata: Metadata = {
    title: "Khoj AI - Search",
    description:
        "Find anything in documents you've shared with Khoj using natural language queries.",
    icons: {
        icon: "/static/assets/icons/khoj_lantern.ico",
        apple: "/static/assets/icons/khoj_lantern_256x256.png",
    },
    openGraph: {
        siteName: "Khoj AI",
        title: "Khoj AI - Search",
        description: "Your Second Brain.",
        url: "https://app.khoj.dev/search",
        type: "website",
        images: [
            {
                url: "https://assets.khoj.dev/khoj_lantern_256x256.png",
                width: 256,
                height: 256,
            },
        ],
    },
};

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html>
            <ContentSecurityPolicy />
            <body>
                {children}
                <Toaster />
            </body>
        </html>
    );
}
