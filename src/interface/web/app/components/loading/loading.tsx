import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { CircleNotch } from "@phosphor-icons/react";
import { AppSidebar } from "../appSidebar/appSidebar";
import { Separator } from "@/components/ui/separator";
import { useIsMobileWidth } from "@/app/common/utils";
import { KhojLogoType } from "../logo/khojLogo";

interface LoadingProps {
    className?: string;
    iconClassName?: string;
    message?: string;
}

export default function Loading(props: LoadingProps) {
    const isMobileWidth = useIsMobileWidth();

    return (
        // NOTE: We can display usage tips here for casual learning moments.
        <SidebarProvider>
            <AppSidebar conversationId={""} />
            <SidebarInset>
                <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4">
                    <SidebarTrigger className="-ml-1" />
                    <Separator orientation="vertical" className="mr-2 h-4" />
                    {isMobileWidth ? (
                        <a className="p-0 no-underline" href="/">
                            <KhojLogoType className="h-auto w-16" />
                        </a>
                    ) : (
                        <h2 className="text-lg">Ask Anything</h2>
                    )}
                </header>
            </SidebarInset>
            <div
                className={
                    props.className ||
                    "bg-background opacity-50 flex items-center justify-center h-full w-full fixed top-0 left-0 z-50"
                }
            >
                <div>
                    {props.message || "Loading"}{" "}
                    <span>
                        <CircleNotch className="inline animate-spin h-5 w-5" />
                    </span>
                </div>
            </div>
        </SidebarProvider>
    );
}

export function InlineLoading(props: LoadingProps) {
    return (
        <button className={`${props.className}`}>
            <span>
                {props.message}{" "}
                <CircleNotch
                    className={`inline animate-spin ${props.iconClassName ? props.iconClassName : "h-5 w-5 mx-3"}`}
                />
            </span>
        </button>
    );
}
