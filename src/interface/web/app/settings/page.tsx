"use client";

import styles from "./settings.module.css";
import "intl-tel-input/styles";

import { Suspense, useEffect, useState } from "react";
import { useToast } from "@/components/ui/use-toast";

import { useUserConfig, ModelOptions, UserConfig, SubscriptionStates } from "../common/auth";
import { toTitleCase, useIsMobileWidth } from "../common/utils";

import { isValidPhoneNumber } from "libphonenumber-js";

import { Button } from "@/components/ui/button";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuRadioGroup,
    DropdownMenuRadioItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

import {
    ArrowRight,
    ChatCircleText,
    Key,
    Palette,
    UserCircle,
    Trash,
    Copy,
    CreditCard,
    CheckCircle,
    NotionLogo,
    GithubLogo,
    Files,
    WhatsappLogo,
    ExclamationMark,
    Plugs,
    CloudSlash,
    Laptop,
    Plus,
    FloppyDisk,
    PlugsConnected,
    ArrowCircleUp,
    ArrowCircleDown,
    ArrowsClockwise,
    CaretDown,
    Waveform,
    MagnifyingGlass,
    Brain,
    EyeSlash,
    Eye,
} from "@phosphor-icons/react";

import Loading from "../components/loading/loading";

import IntlTelInput from "intl-tel-input/react";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "../components/appSidebar/appSidebar";
import { Separator } from "@/components/ui/separator";
import { KhojLogoType } from "../components/logo/khojLogo";

interface DropdownComponentProps {
    items: ModelOptions[];
    selected: number;
    callbackFunc: (value: string) => Promise<void>;
}

const DropdownComponent: React.FC<DropdownComponentProps> = ({ items, selected, callbackFunc }) => {
    const [position, setPosition] = useState(selected?.toString() ?? "0");

    return (
        !!selected && (
            <div className="overflow-hidden shadow-md rounded-lg">
                <DropdownMenu>
                    <DropdownMenuTrigger asChild className="w-full rounded-lg">
                        <Button variant="outline" className="justify-start py-6 rounded-lg">
                            {items.find((item) => item.id.toString() === position)?.name}{" "}
                            <CaretDown className="h-4 w-4 ml-auto text-muted-foreground" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                        style={{
                            maxHeight: "200px",
                            overflowY: "auto",
                            minWidth: "var(--radix-dropdown-menu-trigger-width)",
                        }}
                    >
                        <DropdownMenuRadioGroup
                            value={position}
                            onValueChange={async (value) => {
                                setPosition(value);
                                await callbackFunc(value);
                            }}
                        >
                            {items.map((item) => (
                                <DropdownMenuRadioItem
                                    key={item.id.toString()}
                                    value={item.id.toString()}
                                >
                                    {item.name}
                                </DropdownMenuRadioItem>
                            ))}
                        </DropdownMenuRadioGroup>
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>
        )
    );
};

interface TokenObject {
    token: string;
    name: string;
}

const useApiKeys = () => {
    const [apiKeys, setApiKeys] = useState<TokenObject[]>([]);
    const { toast } = useToast();

    const generateAPIKey = async () => {
        try {
            const response = await fetch(`/auth/token`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            const tokenObj = await response.json();
            setApiKeys((prevKeys) => [...prevKeys, tokenObj]);
        } catch (error) {
            console.error("Error generating API key:", error);
        }
    };

    const copyAPIKey = async (token: string) => {
        try {
            await navigator.clipboard.writeText(token);
            toast({
                title: "🔑 API Key",
                description: "Copied to clipboard",
            });
        } catch (error) {
            console.error("Error copying API key:", error);
        }
    };

    const deleteAPIKey = async (token: string) => {
        try {
            const response = await fetch(`/auth/token?token=${token}`, { method: "DELETE" });
            if (response.ok) {
                setApiKeys((prevKeys) => prevKeys.filter((key) => key.token !== token));
            }
        } catch (error) {
            console.error("Error deleting API key:", error);
        }
    };

    const listApiKeys = async () => {
        try {
            const response = await fetch(`/auth/token`);
            const tokens = await response.json();
            if (tokens?.length > 0) {
                setApiKeys(tokens);
            }
        } catch (error) {
            console.error("Error listing API keys:", error);
        }
    };

    useEffect(() => {
        listApiKeys();
    }, []);

    return {
        apiKeys,
        generateAPIKey,
        copyAPIKey,
        deleteAPIKey,
    };
};

function ApiKeyCard() {
    const { apiKeys, generateAPIKey, copyAPIKey, deleteAPIKey } = useApiKeys();
    const [visibleApiKeys, setVisibleApiKeys] = useState<Set<string>>(new Set());
    const { toast } = useToast();

    return (
        <Card className="grid grid-flow-column border border-gray-300 shadow-md rounded-lg dark:bg-muted dark:border-none border-opacity-50 lg:w-2/3">
            <CardHeader className="text-xl grid grid-flow-col grid-cols-[1fr_auto] pb-0">
                <span className="flex flex-wrap">
                    <Key className="h-7 w-7 mr-2" />
                    API Keys
                </span>
                <Button variant="secondary" className="!mt-0" onClick={generateAPIKey}>
                    <Plus weight="bold" className="h-5 w-5 mr-2" />
                    Generate Key
                </Button>
            </CardHeader>
            <CardContent className="overflow-hidden grid gap-6">
                <p className="text-md text-gray-400">
                    Access Khoj from the{" "}
                    <a href="https://docs.khoj.dev/clients/desktop" target="_blank">
                        Desktop
                    </a>
                    , <a href="https://docs.khoj.dev/clients/obsidian">Obsidian</a>,{" "}
                    <a href="https://docs.khoj.dev/clients/emacs">Emacs</a> apps and more.
                </p>
                <Table>
                    <TableBody>
                        {apiKeys.map((key) => (
                            <TableRow key={key.token}>
                                <TableCell className="pl-0 py-3">{key.name}</TableCell>
                                <TableCell className="grid grid-flow-col grid-cols-[1fr_auto] bg-secondary dark:bg-background rounded-xl p-3 m-1">
                                    <span className="font-mono text-left w-[50px] md:w-[400px]">
                                        {visibleApiKeys.has(key.token)
                                            ? key.token
                                            : `${key.token.slice(0, 6)}...${key.token.slice(-4)}`}
                                    </span>
                                    <div className="grid grid-flow-col">
                                        {visibleApiKeys.has(key.token) ? (
                                            <EyeSlash
                                                weight="bold"
                                                className="h-4 w-4 mr-2 hover:bg-primary/40"
                                                onClick={() =>
                                                    setVisibleApiKeys((prev) => {
                                                        const next = new Set(prev);
                                                        next.delete(key.token);
                                                        return next;
                                                    })
                                                }
                                            />
                                        ) : (
                                            <Eye
                                                weight="bold"
                                                className="h-4 w-4 mr-2 hover:bg-primary/40"
                                                onClick={() =>
                                                    setVisibleApiKeys(
                                                        new Set([...visibleApiKeys, key.token]),
                                                    )
                                                }
                                            />
                                        )}
                                        <Copy
                                            weight="bold"
                                            className="h-4 w-4 mr-2 hover:bg-primary/40"
                                            onClick={() => {
                                                toast({
                                                    title: `🔑 Copied API Key: ${key.name}`,
                                                    description: `Set this API key in the Khoj apps you want to connect to this Khoj account`,
                                                });
                                                copyAPIKey(key.token);
                                            }}
                                        />
                                        <Trash
                                            weight="bold"
                                            className="h-4 w-4 mr-2 md:ml-4 text-red-400 hover:bg-primary/40"
                                            onClick={() => {
                                                toast({
                                                    title: `🔑 Deleted API Key: ${key.name}`,
                                                    description: `Apps using this API key will no longer connect to this Khoj account`,
                                                });
                                                deleteAPIKey(key.token);
                                            }}
                                        />
                                    </div>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </CardContent>
            <CardFooter className="flex flex-wrap gap-4" />
        </Card>
    );
}

enum PhoneNumberValidationState {
    Setup = "setup",
    SendOTP = "otp",
    VerifyOTP = "verify",
    Verified = "verified",
}

export default function SettingsView() {
    const { userConfig: initialUserConfig } = useUserConfig(true);
    const [userConfig, setUserConfig] = useState<UserConfig | null>(null);
    const [name, setName] = useState<string | undefined>(undefined);
    const [notionToken, setNotionToken] = useState<string | null>(null);
    const [phoneNumber, setPhoneNumber] = useState<string | undefined>(undefined);
    const [otp, setOTP] = useState("");
    const [numberValidationState, setNumberValidationState] = useState<PhoneNumberValidationState>(
        PhoneNumberValidationState.Verified,
    );
    const { toast } = useToast();
    const isMobileWidth = useIsMobileWidth();

    const title = "Settings";

    const cardClassName =
        "w-full lg:w-5/12 grid grid-flow-column border border-gray-300 shadow-md rounded-lg border dark:border-none border-opacity-50 dark:bg-muted";

    useEffect(() => {
        setUserConfig(initialUserConfig);
        setPhoneNumber(initialUserConfig?.phone_number);
        setNumberValidationState(
            initialUserConfig?.is_phone_number_verified
                ? PhoneNumberValidationState.Verified
                : initialUserConfig?.phone_number
                  ? PhoneNumberValidationState.SendOTP
                  : PhoneNumberValidationState.Setup,
        );
        setName(initialUserConfig?.given_name);
        setNotionToken(initialUserConfig?.notion_token ?? null);
    }, [initialUserConfig]);

    const sendOTP = async () => {
        try {
            const response = await fetch(`/api/phone?phone_number=${phoneNumber}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!response.ok) throw new Error("Failed to send OTP");

            setNumberValidationState(PhoneNumberValidationState.VerifyOTP);
        } catch (error) {
            console.error("Error sending OTP:", error);
            toast({
                title: "📱 Phone",
                description: "Failed to send OTP. Try again or contact us at team@khoj.dev",
            });
        }
    };

    const verifyOTP = async () => {
        try {
            const response = await fetch(`/api/phone/verify?code=${otp}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!response.ok) throw new Error("Failed to verify OTP");

            setNumberValidationState(PhoneNumberValidationState.Verified);
            toast({
                title: "📱 Phone",
                description: "Phone number verified",
            });
        } catch (error) {
            console.error("Error verifying OTP:", error);
            toast({
                title: "📱 Phone",
                description: "Failed to verify OTP. Try again or contact us at team@khoj.dev",
            });
        }
    };

    const disconnectNumber = async () => {
        try {
            const response = await fetch(`/api/phone`, {
                method: "DELETE",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!response.ok) throw new Error("Failed to disconnect phone number");

            setPhoneNumber(undefined);
            setNumberValidationState(PhoneNumberValidationState.Setup);
            toast({
                title: "📱 Phone",
                description: "Phone number disconnected",
            });
        } catch (error) {
            console.error("Error disconnecting phone number:", error);
            toast({
                title: "📱 Phone",
                description:
                    "Failed to disconnect phone number. Try again or contact us at team@khoj.dev",
            });
        }
    };

    const setSubscription = async (state: string) => {
        try {
            const url = `/api/subscription?operation=${state}`;
            const response = await fetch(url, {
                method: "PATCH",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!response.ok) throw new Error("Failed to change subscription");

            // Set updated user settings
            if (userConfig) {
                let newUserConfig = userConfig;
                newUserConfig.subscription_state =
                    state === "cancel"
                        ? SubscriptionStates.UNSUBSCRIBED
                        : SubscriptionStates.SUBSCRIBED;
                setUserConfig(newUserConfig);
            }

            // Notify user of subscription change
            toast({
                title: "💳 Subscription",
                description:
                    userConfig?.subscription_state === "unsubscribed"
                        ? "Your subscription was cancelled"
                        : "Your Futurist subscription has been renewed",
            });
        } catch (error) {
            console.error("Error changing subscription:", error);
            toast({
                title: "💳 Subscription",
                description:
                    state === "cancel"
                        ? "Failed to cancel subscription. Try again or contact us at team@khoj.dev"
                        : "Failed to renew subscription. Try again or contact us at team@khoj.dev",
            });
        }
    };

    const enableFreeTrial = async () => {
        const formatDate = (dateString: Date) => {
            const date = new Date(dateString);
            return new Intl.DateTimeFormat("en-US", {
                day: "2-digit",
                month: "short",
                year: "numeric",
            }).format(date);
        };

        try {
            const response = await fetch(`/api/subscription/trial`, {
                method: "POST",
            });
            if (!response.ok) throw new Error("Failed to enable free trial");

            const responseBody = await response.json();

            // Set updated user settings
            if (responseBody.trial_enabled && userConfig) {
                let newUserConfig = userConfig;
                newUserConfig.subscription_state = SubscriptionStates.TRIAL;
                const renewalDate = new Date(
                    Date.now() + userConfig.length_of_free_trial * 24 * 60 * 60 * 1000,
                );
                newUserConfig.subscription_renewal_date = formatDate(renewalDate);
                newUserConfig.subscription_enabled_trial_at = new Date().toISOString();
                setUserConfig(newUserConfig);

                // Notify user of free trial
                toast({
                    title: "🎉 Trial Enabled",
                    description: `Your free trial will end on ${newUserConfig.subscription_renewal_date}`,
                });
            }
        } catch (error) {
            console.error("Error enabling free trial:", error);
            toast({
                title: "⚠️ Failed to Enable Free Trial",
                description:
                    "Failed to enable free trial. Try again or contact us at team@khoj.dev",
            });
        }
    };

    const saveName = async () => {
        if (!name) return;
        try {
            const response = await fetch(`/api/user/name?name=${name}`, {
                method: "PATCH",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!response.ok) throw new Error("Failed to update name");

            // Set updated user settings
            if (userConfig) {
                let newUserConfig = userConfig;
                newUserConfig.given_name = name;
                setUserConfig(newUserConfig);
            }

            // Notify user of name change
            toast({
                title: `✅ Updated Profile`,
                description: `You name has been updated to ${name}`,
            });
        } catch (error) {
            console.error("Error updating name:", error);
            toast({
                title: "⚠️ Failed to Update Profile",
                description: "Failed to update name. Try again or contact team@khoj.dev",
            });
        }
    };

    const updateModel = (name: string) => async (id: string) => {
        if (!userConfig?.is_active) {
            toast({
                title: `Model Update`,
                description: `You need to be subscribed to update ${name} models`,
                variant: "destructive",
            });
            return;
        }

        try {
            const response = await fetch(`/api/model/${name}?id=` + id, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
            });

            if (!response.ok) throw new Error("Failed to update model");

            toast({
                title: `✅ Updated ${toTitleCase(name)} Model`,
            });
        } catch (error) {
            console.error(`Failed to update ${name} model:`, error);
            toast({
                description: `❌ Failed to update ${toTitleCase(name)} model. Try again.`,
                variant: "destructive",
            });
        }
    };

    const saveNotionToken = async () => {
        if (!notionToken) return;
        // Save Notion API key to server
        try {
            const response = await fetch(`/api/content/notion`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ token: notionToken }),
            });
            if (!response.ok) throw new Error("Failed to save Notion API key");

            // Set updated user settings
            if (userConfig) {
                let newUserConfig = userConfig;
                newUserConfig.notion_token = notionToken;
                setUserConfig(newUserConfig);
            }

            // Notify user of Notion API key save
            toast({
                title: `✅ Saved Notion Settings`,
                description: `You Notion API key has been saved.`,
            });
        } catch (error) {
            console.error("Error updating name:", error);
            toast({
                title: "⚠️ Failed to Save Notion Settings",
                description: "Failed to save Notion API key. Try again or contact team@khoj.dev",
            });
        }
    };

    const syncContent = async (type: string) => {
        try {
            const response = await fetch(`/api/content?t=${type}`, {
                method: "PATCH",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!response.ok) throw new Error(`Failed to sync content from ${type}`);

            toast({
                title: `🔄 Syncing ${type}`,
                description: `Your ${type} content is being synced.`,
            });
        } catch (error) {
            console.error("Error syncing content:", error);
            toast({
                title: `⚠️ Failed to Sync ${type}`,
                description: `Failed to sync ${type} content. Try again or contact team@khoj.dev`,
            });
        }
    };

    const disconnectContent = async (type: string) => {
        try {
            const response = await fetch(`/api/content/${type}`, {
                method: "DELETE",
                headers: {
                    "Content-Type": "application/json",
                },
            });
            if (!response.ok) throw new Error(`Failed to disconnect ${type}`);

            // Set updated user settings
            if (userConfig) {
                let newUserConfig = userConfig;
                if (type === "computer") {
                    newUserConfig.enabled_content_source.computer = false;
                } else if (type === "notion") {
                    newUserConfig.enabled_content_source.notion = false;
                    newUserConfig.notion_token = null;
                    setNotionToken(newUserConfig.notion_token);
                } else if (type === "github") {
                    newUserConfig.enabled_content_source.github = false;
                }
                setUserConfig(newUserConfig);
            }

            // Notify user about disconnecting content source
            if (type === "computer") {
                toast({
                    title: `✅ Deleted Synced Files`,
                    description: "Your synced documents have been deleted.",
                });
            } else {
                toast({
                    title: `✅ Disconnected ${type}`,
                    description: `Your ${type} integration to Khoj has been disconnected.`,
                });
            }
        } catch (error) {
            console.error(`Error disconnecting ${type}:`, error);
            toast({
                title: `⚠️ Failed to Disconnect ${type}`,
                description: `Failed to disconnect from ${type}. Try again or contact team@khoj.dev`,
            });
        }
    };

    if (!userConfig) return <Loading />;

    return (
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
                        <h2 className="text-lg">Settings</h2>
                    )}
                </header>
                <div className={styles.page}>
                    <title>{title}</title>
                    <div className={styles.content}>
                        <div className={`${styles.contentBody} mx-10 my-2`}>
                            <Suspense fallback={<Loading />}>
                                <div
                                    id="content"
                                    className="grid grid-flow-column sm:grid-flow-row gap-16 m-8"
                                >
                                    <div className="section grid gap-8">
                                        <div className="text-2xl">Profile</div>
                                        <div className="cards flex flex-wrap gap-16">
                                            <Card className={cardClassName}>
                                                <CardHeader className="text-xl flex flex-row">
                                                    <UserCircle className="h-7 w-7 mr-2" />
                                                    Name
                                                </CardHeader>
                                                <CardContent className="overflow-hidden">
                                                    <p className="pb-4 text-gray-400">
                                                        What should Khoj refer to you as?
                                                    </p>
                                                    <Input
                                                        type="text"
                                                        onChange={(e) => setName(e.target.value)}
                                                        value={name}
                                                        className="w-full border border-gray-300 rounded-lg p-4 py-6"
                                                    />
                                                </CardContent>
                                                <CardFooter className="flex flex-wrap gap-4">
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={saveName}
                                                        disabled={name === userConfig.given_name}
                                                    >
                                                        <FloppyDisk className="h-5 w-5 inline mr-2" />
                                                        Save
                                                    </Button>
                                                </CardFooter>
                                            </Card>
                                            <Card id="subscription" className={cardClassName}>
                                                <CardHeader className="text-xl flex flex-row">
                                                    <CreditCard className="h-7 w-7 mr-2" />
                                                    Subscription
                                                </CardHeader>
                                                <CardContent className="grid gap-2 overflow-hidden">
                                                    <p className="text-gray-400">Current Plan</p>
                                                    {(userConfig.subscription_state === "trial" && (
                                                        <>
                                                            <p className="text-xl text-primary/80">
                                                                Futurist (Trial)
                                                            </p>
                                                            <p className="text-gray-400">
                                                                You are on a{" "}
                                                                {userConfig.length_of_free_trial}{" "}
                                                                day trial of the Khoj Futurist plan.
                                                                Your trial ends on{" "}
                                                                {
                                                                    userConfig.subscription_renewal_date
                                                                }
                                                                . Check{" "}
                                                                <a
                                                                    href="https://khoj.dev/#pricing"
                                                                    target="_blank"
                                                                >
                                                                    pricing page
                                                                </a>{" "}
                                                                to compare plans.
                                                            </p>
                                                        </>
                                                    )) ||
                                                        (userConfig.subscription_state ===
                                                            "subscribed" && (
                                                            <>
                                                                <p className="text-xl text-primary/80">
                                                                    Futurist
                                                                </p>
                                                                <p className="text-gray-400">
                                                                    Subscription <b>renews</b> on{" "}
                                                                    <b>
                                                                        {
                                                                            userConfig.subscription_renewal_date
                                                                        }
                                                                    </b>
                                                                </p>
                                                            </>
                                                        )) ||
                                                        (userConfig.subscription_state ===
                                                            "unsubscribed" && (
                                                            <>
                                                                <p className="text-xl">Futurist</p>
                                                                <p className="text-gray-400">
                                                                    Subscription <b>ends</b> on{" "}
                                                                    <b>
                                                                        {
                                                                            userConfig.subscription_renewal_date
                                                                        }
                                                                    </b>
                                                                </p>
                                                            </>
                                                        )) ||
                                                        (userConfig.subscription_state ===
                                                            "expired" && (
                                                            <>
                                                                <p className="text-xl">Humanist</p>
                                                                {(userConfig.subscription_renewal_date && (
                                                                    <p className="text-gray-400">
                                                                        Subscription <b>expired</b>{" "}
                                                                        on{" "}
                                                                        <b>
                                                                            {
                                                                                userConfig.subscription_renewal_date
                                                                            }
                                                                        </b>
                                                                    </p>
                                                                )) || (
                                                                    <p className="text-gray-400">
                                                                        Check{" "}
                                                                        <a
                                                                            href="https://khoj.dev/#pricing"
                                                                            target="_blank"
                                                                        >
                                                                            pricing page
                                                                        </a>{" "}
                                                                        to compare plans.
                                                                    </p>
                                                                )}
                                                            </>
                                                        ))}
                                                </CardContent>
                                                <CardFooter className="flex flex-wrap gap-4">
                                                    {(userConfig.subscription_state ==
                                                        "subscribed" && (
                                                        <Button
                                                            variant="outline"
                                                            className="hover:text-red-400"
                                                            onClick={() =>
                                                                setSubscription("cancel")
                                                            }
                                                        >
                                                            <ArrowCircleDown className="h-5 w-5 mr-2" />
                                                            Unsubscribe
                                                        </Button>
                                                    )) ||
                                                        (userConfig.subscription_state ==
                                                            "unsubscribed" && (
                                                            <Button
                                                                variant="outline"
                                                                className="text-primary/80 hover:text-primary"
                                                                onClick={() =>
                                                                    setSubscription("resubscribe")
                                                                }
                                                            >
                                                                <ArrowCircleUp
                                                                    weight="bold"
                                                                    className="h-5 w-5 mr-2"
                                                                />
                                                                Resubscribe
                                                            </Button>
                                                        )) ||
                                                        (userConfig.subscription_enabled_trial_at && (
                                                            <Button
                                                                variant="outline"
                                                                className="text-primary/80 hover:text-primary"
                                                                onClick={() =>
                                                                    window.open(
                                                                        `${userConfig.khoj_cloud_subscription_url}?prefilled_email=${userConfig.username}`,
                                                                        "_blank",
                                                                        "noopener,noreferrer",
                                                                    )
                                                                }
                                                            >
                                                                <ArrowCircleUp
                                                                    weight="bold"
                                                                    className="h-5 w-5 mr-2"
                                                                />
                                                                Subscribe
                                                            </Button>
                                                        )) || (
                                                            <Button
                                                                variant="outline"
                                                                className="text-primary/80 hover:text-primary"
                                                                onClick={enableFreeTrial}
                                                            >
                                                                <ArrowCircleUp
                                                                    weight="bold"
                                                                    className="h-5 w-5 mr-2"
                                                                />
                                                                Enable Trial
                                                            </Button>
                                                        )}
                                                </CardFooter>
                                            </Card>
                                        </div>
                                    </div>
                                    <div className="section grid gap-8">
                                        <div className="text-2xl">Content</div>
                                        <div className="cards flex flex-wrap gap-16">
                                            <Card id="computer" className={cardClassName}>
                                                <CardHeader className="flex flex-row text-xl">
                                                    <Brain className="h-8 w-8 mr-2" />
                                                    Knowledge Base
                                                    {userConfig.enabled_content_source.computer && (
                                                        <CheckCircle
                                                            className="h-6 w-6 ml-auto text-green-500"
                                                            weight="fill"
                                                        />
                                                    )}
                                                </CardHeader>
                                                <CardContent className="overflow-hidden pb-12 text-gray-400">
                                                    Manage and search through your digital brain.
                                                </CardContent>
                                                <CardFooter className="flex flex-wrap gap-4">
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        title="Search thorugh files"
                                                        onClick={() =>
                                                            (window.location.href = "/search")
                                                        }
                                                    >
                                                        <MagnifyingGlass className="h-5 w-5 inline mr-1" />
                                                        Search
                                                    </Button>
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        className={`${userConfig.enabled_content_source.computer || "hidden"}`}
                                                        onClick={() =>
                                                            disconnectContent("computer")
                                                        }
                                                    >
                                                        <CloudSlash className="h-5 w-5 inline mr-1" />
                                                        Clear All
                                                    </Button>
                                                </CardFooter>
                                            </Card>
                                            <Card id="github" className={`${cardClassName} hidden`}>
                                                <CardHeader className="flex flex-row text-2xl">
                                                    <GithubLogo className="h-8 w-8 mr-2" />
                                                    Github
                                                </CardHeader>
                                                <CardContent className="overflow-hidden pb-12 text-gray-400">
                                                    Set Github repositories to index
                                                </CardContent>
                                                <CardFooter className="flex flex-wrap gap-4">
                                                    <Button variant="outline" size="sm">
                                                        {(userConfig.enabled_content_source
                                                            .github && (
                                                            <>
                                                                <Files className="h-5 w-5 inline mr-1" />
                                                                Manage
                                                            </>
                                                        )) || (
                                                            <>
                                                                <Plugs className="h-5 w-5 inline mr-1" />
                                                                Connect
                                                            </>
                                                        )}
                                                    </Button>
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        className={`${userConfig.enabled_content_source.github || "hidden"}`}
                                                    >
                                                        <CloudSlash className="h-5 w-5 inline mr-1" />
                                                        Disable
                                                    </Button>
                                                </CardFooter>
                                            </Card>
                                            <Card id="notion" className={cardClassName}>
                                                <CardHeader className="text-xl flex flex-row">
                                                    <NotionLogo className="h-7 w-7 mr-2" />
                                                    Notion
                                                    {userConfig.enabled_content_source.notion && (
                                                        <CheckCircle
                                                            className="h-6 w-6 ml-auto text-green-500"
                                                            weight="fill"
                                                        />
                                                    )}
                                                </CardHeader>
                                                <CardContent className="grid gap-4">
                                                    <p className="text-gray-400">
                                                        Sync your Notion workspace.
                                                    </p>
                                                    {!userConfig.notion_oauth_url && (
                                                        <Input
                                                            onChange={(e) =>
                                                                setNotionToken(e.target.value)
                                                            }
                                                            value={notionToken || ""}
                                                            placeholder="Enter API Key of your Khoj integration on Notion"
                                                            className="w-full border border-gray-300 rounded-lg px-4 py-6"
                                                        />
                                                    )}
                                                </CardContent>
                                                <CardFooter className="flex flex-wrap gap-4">
                                                    {
                                                        /* Show connect to notion button if notion oauth url setup and user disconnected*/
                                                        userConfig.notion_oauth_url &&
                                                        !userConfig.enabled_content_source
                                                            .notion ? (
                                                            <Button
                                                                variant="outline"
                                                                size="sm"
                                                                onClick={() => {
                                                                    window.open(
                                                                        userConfig.notion_oauth_url,
                                                                    );
                                                                }}
                                                            >
                                                                <Plugs className="h-5 w-5 inline mr-1" />
                                                                Connect
                                                            </Button>
                                                        ) : /* Show sync button if user connected to notion and API key unchanged */
                                                        userConfig.enabled_content_source.notion &&
                                                          notionToken ===
                                                              userConfig.notion_token ? (
                                                            <Button
                                                                variant="outline"
                                                                size="sm"
                                                                onClick={() =>
                                                                    syncContent("notion")
                                                                }
                                                            >
                                                                <ArrowsClockwise className="h-5 w-5 inline mr-1" />
                                                                Sync
                                                            </Button>
                                                        ) : /* Show set API key button notion oauth url not set setup */
                                                        !userConfig.notion_oauth_url ? (
                                                            <Button
                                                                variant="outline"
                                                                size="sm"
                                                                onClick={saveNotionToken}
                                                                disabled={
                                                                    notionToken ===
                                                                    userConfig.notion_token
                                                                }
                                                            >
                                                                <FloppyDisk className="h-5 w-5 inline mr-1" />
                                                                {(userConfig.enabled_content_source
                                                                    .notion &&
                                                                    "Update API Key") ||
                                                                    "Set API Key"}
                                                            </Button>
                                                        ) : (
                                                            <></>
                                                        )
                                                    }
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        className={`${userConfig.notion_token || "hidden"}`}
                                                        onClick={() => disconnectContent("notion")}
                                                    >
                                                        <CloudSlash className="h-5 w-5 inline mr-1" />
                                                        Disconnect
                                                    </Button>
                                                </CardFooter>
                                            </Card>
                                        </div>
                                    </div>
                                    <div className="section grid gap-8">
                                        <div className="text-2xl">Models</div>
                                        <div className="cards flex flex-wrap gap-16">
                                            {userConfig.chat_model_options.length > 0 && (
                                                <Card className={cardClassName}>
                                                    <CardHeader className="text-xl flex flex-row">
                                                        <ChatCircleText className="h-7 w-7 mr-2" />
                                                        Chat
                                                    </CardHeader>
                                                    <CardContent className="overflow-hidden pb-12 grid gap-8 h-fit">
                                                        <p className="text-gray-400">
                                                            Pick the chat model to generate text
                                                            responses
                                                        </p>
                                                        <DropdownComponent
                                                            items={userConfig.chat_model_options}
                                                            selected={
                                                                userConfig.selected_chat_model_config
                                                            }
                                                            callbackFunc={updateModel("chat")}
                                                        />
                                                    </CardContent>
                                                    <CardFooter className="flex flex-wrap gap-4">
                                                        {!userConfig.is_active && (
                                                            <p className="text-gray-400">
                                                                Subscribe to switch model
                                                            </p>
                                                        )}
                                                    </CardFooter>
                                                </Card>
                                            )}
                                            {userConfig.paint_model_options.length > 0 && (
                                                <Card className={cardClassName}>
                                                    <CardHeader className="text-xl flex flex-row">
                                                        <Palette className="h-7 w-7 mr-2" />
                                                        Paint
                                                    </CardHeader>
                                                    <CardContent className="overflow-hidden pb-12 grid gap-8 h-fit">
                                                        <p className="text-gray-400">
                                                            Pick the paint model to generate image
                                                            responses
                                                        </p>
                                                        <DropdownComponent
                                                            items={userConfig.paint_model_options}
                                                            selected={
                                                                userConfig.selected_paint_model_config
                                                            }
                                                            callbackFunc={updateModel("paint")}
                                                        />
                                                    </CardContent>
                                                    <CardFooter className="flex flex-wrap gap-4">
                                                        {!userConfig.is_active && (
                                                            <p className="text-gray-400">
                                                                Subscribe to switch model
                                                            </p>
                                                        )}
                                                    </CardFooter>
                                                </Card>
                                            )}
                                            {userConfig.voice_model_options.length > 0 && (
                                                <Card className={cardClassName}>
                                                    <CardHeader className="text-xl flex flex-row">
                                                        <Waveform className="h-7 w-7 mr-2" />
                                                        Voice
                                                    </CardHeader>
                                                    <CardContent className="overflow-hidden pb-12 grid gap-8 h-fit">
                                                        <p className="text-gray-400">
                                                            Pick the voice model to generate speech
                                                            responses
                                                        </p>
                                                        <DropdownComponent
                                                            items={userConfig.voice_model_options}
                                                            selected={
                                                                userConfig.selected_voice_model_config
                                                            }
                                                            callbackFunc={updateModel("voice")}
                                                        />
                                                    </CardContent>
                                                    <CardFooter className="flex flex-wrap gap-4">
                                                        {!userConfig.is_active && (
                                                            <p className="text-gray-400">
                                                                Subscribe to switch model
                                                            </p>
                                                        )}
                                                    </CardFooter>
                                                </Card>
                                            )}
                                        </div>
                                    </div>
                                    <div className="section grid gap-8">
                                        <div id="clients" className="text-2xl">
                                            Clients
                                        </div>
                                        <div className="cards flex flex-col flex-wrap gap-8">
                                            {!userConfig.anonymous_mode && <ApiKeyCard />}
                                            <Card className={`${cardClassName} lg:w-2/3`}>
                                                <CardHeader className="text-xl flex flex-row">
                                                    <WhatsappLogo className="h-7 w-7 mr-2" />
                                                    Chat on Whatsapp
                                                    {(numberValidationState ===
                                                        PhoneNumberValidationState.Verified && (
                                                        <CheckCircle
                                                            weight="bold"
                                                            className="h-4 w-4 ml-1 text-green-400"
                                                        />
                                                    )) ||
                                                        (numberValidationState !==
                                                            PhoneNumberValidationState.Setup && (
                                                            <ExclamationMark
                                                                weight="bold"
                                                                className="h-4 w-4 ml-1 text-yellow-400"
                                                            />
                                                        ))}
                                                </CardHeader>
                                                <CardContent className="grid gap-4">
                                                    <p className="text-gray-400">
                                                        Connect your number to chat with Khoj on
                                                        WhatsApp. Learn more about the integration{" "}
                                                        <a href="https://docs.khoj.dev/clients/whatsapp">
                                                            here
                                                        </a>
                                                        .
                                                    </p>
                                                    <div>
                                                        <IntlTelInput
                                                            initialValue={phoneNumber || ""}
                                                            onChangeNumber={setPhoneNumber}
                                                            disabled={
                                                                numberValidationState ===
                                                                PhoneNumberValidationState.VerifyOTP
                                                            }
                                                            initOptions={{
                                                                separateDialCode: true,
                                                                initialCountry: "af",
                                                                utilsScript:
                                                                    "https://assets.khoj.dev/intl-tel-input%4023.8.0_build_js_utils.js",
                                                                containerClass: `${styles.phoneInput}`,
                                                            }}
                                                        />
                                                        {numberValidationState ===
                                                            PhoneNumberValidationState.VerifyOTP && (
                                                            <>
                                                                <p>{`Enter the OTP sent to your number: ${phoneNumber}`}</p>
                                                                <InputOTP
                                                                    autoFocus={true}
                                                                    maxLength={6}
                                                                    value={otp || ""}
                                                                    onChange={setOTP}
                                                                    onComplete={() =>
                                                                        setNumberValidationState(
                                                                            PhoneNumberValidationState.VerifyOTP,
                                                                        )
                                                                    }
                                                                >
                                                                    <InputOTPGroup>
                                                                        <InputOTPSlot index={0} />
                                                                        <InputOTPSlot index={1} />
                                                                        <InputOTPSlot index={2} />
                                                                        <InputOTPSlot index={3} />
                                                                        <InputOTPSlot index={4} />
                                                                        <InputOTPSlot index={5} />
                                                                    </InputOTPGroup>
                                                                </InputOTP>
                                                            </>
                                                        )}
                                                    </div>
                                                </CardContent>
                                                <CardFooter className="flex flex-wrap gap-4">
                                                    {(numberValidationState ===
                                                        PhoneNumberValidationState.VerifyOTP && (
                                                        <Button
                                                            variant="outline"
                                                            onClick={verifyOTP}
                                                        >
                                                            Verify
                                                        </Button>
                                                    )) || (
                                                        <Button
                                                            variant="outline"
                                                            disabled={
                                                                !phoneNumber ||
                                                                (phoneNumber ===
                                                                    userConfig.phone_number &&
                                                                    numberValidationState ===
                                                                        PhoneNumberValidationState.Verified) ||
                                                                !isValidPhoneNumber(phoneNumber)
                                                            }
                                                            onClick={sendOTP}
                                                        >
                                                            {!userConfig.phone_number ? (
                                                                <>
                                                                    <Plugs className="inline mr-2" />
                                                                    Setup Whatsapp
                                                                </>
                                                            ) : !phoneNumber ||
                                                              (phoneNumber ===
                                                                  userConfig.phone_number &&
                                                                  numberValidationState ===
                                                                      PhoneNumberValidationState.Verified) ||
                                                              !isValidPhoneNumber(phoneNumber) ? (
                                                                <>
                                                                    <PlugsConnected className="inline mr-2 text-green-400" />
                                                                    Switch Number
                                                                </>
                                                            ) : (
                                                                <>
                                                                    Send OTP{" "}
                                                                    <ArrowRight
                                                                        className="inline ml-2"
                                                                        weight="bold"
                                                                    />
                                                                </>
                                                            )}
                                                        </Button>
                                                    )}
                                                    {numberValidationState ===
                                                        PhoneNumberValidationState.Verified && (
                                                        <Button
                                                            variant="outline"
                                                            onClick={() => disconnectNumber()}
                                                        >
                                                            <CloudSlash className="h-5 w-5 mr-2" />
                                                            Disconnect
                                                        </Button>
                                                    )}
                                                </CardFooter>
                                            </Card>
                                        </div>
                                    </div>
                                </div>
                            </Suspense>
                        </div>
                    </div>
                </div>
            </SidebarInset>
        </SidebarProvider>
    );
}
