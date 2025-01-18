"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
    Carousel,
    CarouselContent,
    CarouselItem,
    CarouselNext,
    CarouselPrevious,
} from "@/components/ui/carousel";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Drawer, DrawerContent } from "@/components/ui/drawer";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import {
    ArrowLeft,
    ArrowsClockwise,
    LineVertical,
    PaperPlaneTilt,
    PencilSimple,
    Spinner,
} from "@phosphor-icons/react";
import Autoplay from "embla-carousel-autoplay";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { GoogleSignIn } from "./GoogleSignIn";
import styles from "./loginPrompt.module.css";

export interface LoginPromptProps {
    onOpenChange: (open: boolean) => void;
    isMobileWidth?: boolean;
}

const fetcher = (url: string) => fetch(url).then((res) => res.json());

const ALLOWED_OTP_ATTEMPTS = 5;

interface Provider {
    client_id: string;
    redirect_uri: string;
}

interface CredentialsData {
    [provider: string]: Provider;
}

export default function LoginPrompt(props: LoginPromptProps) {
    const { data, error, isLoading } = useSWR<CredentialsData>("/auth/oauth/metadata", fetcher);
    const [isBasicAuth, setIsBasicAuth] = useState(false);
    const [useEmailSignIn, setUseEmailSignIn] = useState(false);
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [loginError, setLoginError] = useState("");
    const [isLoggingIn, setIsLoggingIn] = useState(false);

    const [email, setEmail] = useState("");
    const [checkEmail, setCheckEmail] = useState(false);
    const [recheckEmail, setRecheckEmail] = useState(false);

    useEffect(() => {
        // We're in basic auth mode if:
        // 1. Data is empty object (no providers)
        // 2. Data has google: null (controlled access mode)
        if (!isLoading && (!data || Object.keys(data).length === 0 || data.google === null)) {
            setIsBasicAuth(true);
        }
    }, [data, isLoading]);

    const handleBasicLogin = async () => {
        if (isLoggingIn || !username || !password) return;
        setIsLoggingIn(true);
        setLoginError("");
        const formData = new FormData();
        formData.append("username", username);
        formData.append("password", password);

        try {
            const response = await fetch("/auth/login", {
                method: "POST",
                body: formData,
            });

            if (response.ok && response.redirected) {
                window.location.href = response.url;
            } else {
                setLoginError("Invalid username or password");
            }
        } catch (err) {
            setLoginError("Login failed. Please try again.");
        } finally {
            setIsLoggingIn(false);
        }
    };

    useEffect(() => {
        const google = (window as any).google;

        if (!google) return;

        // Initialize Google Sign In after script loads
        google.accounts.id.initialize({
            client_id: data?.google?.client_id,
            callback: handleGoogleSignIn,
            auto_select: false,
            login_uri: data?.google?.redirect_uri,
        });

        // Render the button
        google.accounts.id.renderButton(document.getElementById("g_id_signin")!, {
            theme: "outline",
            size: "large",
            width: "100%",
        });
    }, [data]);

    const handleGoogleSignIn = () => {
        if (!data?.google?.client_id || !data?.google?.redirect_uri) return;

        // Create full redirect URL using current origin
        const fullRedirectUri = `${window.location.origin}${data.google.redirect_uri}`;

        const params = new URLSearchParams({
            client_id: data.google.client_id,
            redirect_uri: fullRedirectUri,
            response_type: "code",
            scope: "email profile openid",
            state: window.location.pathname,
            access_type: "offline",
            prompt: "consent select_account",
            include_granted_scopes: "true",
        });

        window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
    };

    const handleGoogleScriptLoad = () => {
        const google = (window as any).google;

        if (!data?.google?.client_id || !data?.google?.redirect_uri) return;

        // Initialize Google Sign In after script loads
        google.accounts.id.initialize({
            client_id: data?.google?.client_id,
            callback: handleGoogleSignIn,
            auto_select: false,
            login_uri: data?.google?.redirect_uri,
        });

        // Render the button
        google.accounts.id.renderButton(document.getElementById("g_id_signin")!, {
            theme: "outline",
            size: "large",
            width: "100%",
        });
    };

    function handleMagicLinkSignIn() {
        fetch("/auth/magic", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ email: email }),
        })
            .then((res) => {
                if (res.ok) {
                    setCheckEmail(true);
                    if (checkEmail) {
                        setRecheckEmail(true);
                    }
                    return res.json();
                } else {
                    throw new Error("Failed to send magic link");
                }
            })
            .then((data) => {
                console.log(data);
            })
            .catch((err) => {
                console.error(err);
            });
    }

    // Basic auth form component
    const BasicAuthForm = () => (
        <div className="flex flex-col gap-4 p-4">
            <div>
                <div className="text-center font-bold text-xl">Sign in to Khoj</div>
            </div>
            <Input
                placeholder="Username"
                className="p-6 w-[300px] mx-auto rounded-lg"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === "Enter") {
                        handleBasicLogin();
                    }
                }}
            />
            <Input
                type="password"
                placeholder="Password"
                className="p-6 w-[300px] mx-auto rounded-lg"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === "Enter") {
                        handleBasicLogin();
                    }
                }}
            />
            {loginError && <div className="text-red-500 text-sm text-center">{loginError}</div>}
            <Button
                variant="default"
                className="p-6 w-[300px] mx-auto flex gap-2 items-center justify-center rounded-lg"
                onClick={handleBasicLogin}
                disabled={isLoggingIn}
            >
                {isLoggingIn ? (
                    <>
                        <Spinner className="h-5 w-5 animate-spin" />
                        Signing in...
                    </>
                ) : (
                    "Sign In"
                )}
            </Button>
        </div>
    );

    if (props.isMobileWidth) {
        return (
            <Drawer open={true} onOpenChange={props.onOpenChange}>
                <DrawerContent className={`flex flex-col gap-4 w-full mb-4`}>
                    <div>
                        {isBasicAuth ? (
                            <BasicAuthForm />
                        ) : useEmailSignIn ? (
                            <EmailSignInContext
                                email={email}
                                setEmail={setEmail}
                                checkEmail={checkEmail}
                                setCheckEmail={setCheckEmail}
                                setUseEmailSignIn={setUseEmailSignIn}
                                recheckEmail={recheckEmail}
                                setRecheckEmail={setRecheckEmail}
                                handleMagicLinkSignIn={handleMagicLinkSignIn}
                            />
                        ) : (
                            <MainSignInContext
                                handleGoogleScriptLoad={handleGoogleScriptLoad}
                                handleGoogleSignIn={handleGoogleSignIn}
                                isLoading={isLoading}
                                data={data}
                                setUseEmailSignIn={setUseEmailSignIn}
                                isMobileWidth={props.isMobileWidth}
                            />
                        )}
                    </div>
                </DrawerContent>
            </Drawer>
        );
    }

    return (
        <Dialog open={true} onOpenChange={props.onOpenChange}>
            <DialogContent
                className={`flex flex-col gap-4 ${!useEmailSignIn ? "p-0 pb-4 m-0 max-w-xl" : "w-fit"}`}
            >
                <div>
                    {isBasicAuth ? (
                        <div className="flex flex-col gap-4 p-4">
                            <div>
                                <div className="text-center font-bold text-xl">Sign in to Khoj</div>
                            </div>
                            <Input
                                placeholder="Username"
                                className="p-6 w-[300px] mx-auto rounded-lg"
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                        handleBasicLogin();
                                    }
                                }}
                            />
                            <Input
                                type="password"
                                placeholder="Password"
                                className="p-6 w-[300px] mx-auto rounded-lg"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                        handleBasicLogin();
                                    }
                                }}
                            />
                            {loginError && (
                                <div className="text-red-500 text-sm text-center">{loginError}</div>
                            )}
                            <Button
                                variant="default"
                                className="p-6 w-[300px] mx-auto flex gap-2 items-center justify-center rounded-lg"
                                onClick={handleBasicLogin}
                                disabled={isLoggingIn}
                            >
                                {isLoggingIn ? (
                                    <>
                                        <Spinner className="h-5 w-5 animate-spin" />
                                        Signing in...
                                    </>
                                ) : (
                                    "Sign In"
                                )}
                            </Button>
                        </div>
                    ) : useEmailSignIn ? (
                        <EmailSignInContext
                            email={email}
                            setEmail={setEmail}
                            checkEmail={checkEmail}
                            setCheckEmail={setCheckEmail}
                            setUseEmailSignIn={setUseEmailSignIn}
                            recheckEmail={recheckEmail}
                            setRecheckEmail={setRecheckEmail}
                            handleMagicLinkSignIn={handleMagicLinkSignIn}
                        />
                    ) : (
                        <MainSignInContext
                            handleGoogleScriptLoad={handleGoogleScriptLoad}
                            handleGoogleSignIn={handleGoogleSignIn}
                            isLoading={isLoading}
                            data={data}
                            setUseEmailSignIn={setUseEmailSignIn}
                            isMobileWidth={props.isMobileWidth ?? false}
                        />
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}

function EmailSignInContext({
    email,
    setEmail,
    checkEmail,
    setCheckEmail,
    setUseEmailSignIn,
    recheckEmail,
    handleMagicLinkSignIn,
}: {
    email: string;
    setEmail: (email: string) => void;
    checkEmail: boolean;
    setCheckEmail: (checkEmail: boolean) => void;
    setUseEmailSignIn: (useEmailSignIn: boolean) => void;
    recheckEmail: boolean;
    setRecheckEmail: (recheckEmail: boolean) => void;
    handleMagicLinkSignIn: () => void;
}) {
    const [otp, setOTP] = useState("");
    const [otpError, setOTPError] = useState("");
    const [numFailures, setNumFailures] = useState(0);

    function checkOTPAndRedirect() {
        const verifyUrl = `/auth/magic?code=${otp}&email=${email}`;

        if (numFailures >= ALLOWED_OTP_ATTEMPTS) {
            setOTPError("Too many failed attempts. Please try again tomorrow.");
            return;
        }

        fetch(verifyUrl, {
            method: "GET",
            headers: {
                "Content-Type": "application/json",
            },
        })
            .then((res) => {
                if (res.ok) {
                    // Check if the response is a redirect
                    if (res.redirected) {
                        window.location.href = res.url;
                    }
                } else if (res.status === 401) {
                    setOTPError("Invalid OTP.");
                    setNumFailures(numFailures + 1);
                    if (numFailures + 1 >= ALLOWED_OTP_ATTEMPTS) {
                        setOTPError("Too many failed attempts. Please try again tomorrow.");
                    }
                } else if (res.status === 429) {
                    setOTPError("Too many failed attempts. Please try again tomorrow.");
                    setNumFailures(ALLOWED_OTP_ATTEMPTS);
                } else if (res.status === 403) {
                    setOTPError("OTP expired. Please request a new one.");
                } else {
                    throw new Error("Failed to verify OTP");
                }
            })
            .catch((err) => {
                console.error(err);
            });
    }

    return (
        <div className="flex flex-col gap-4 p-4">
            <Button
                variant="ghost"
                className="w-fit p-1 m-0 flex gap-2 items-center justify-center text-sm absolute top-5 left-5 h-fit rounded-full border border-gray-200"
                onClick={() => {
                    setUseEmailSignIn(false);
                }}
            >
                <ArrowLeft className="h-6 w-6" />
            </Button>
            <div>
                <div className="text-center font-bold text-xl">Get started with Khoj</div>
            </div>
            <div className="text-center text-sm text-muted-foreground">
                {checkEmail
                    ? recheckEmail
                        ? `A new link has been sent to ${email}.`
                        : `A one time sign in code has been sent to ${email}.`
                    : "You will receive a sign-in code on the email address you provide below"}
            </div>
            {!checkEmail && (
                <>
                    <Input
                        placeholder="Email"
                        className="p-6 w-[300px] mx-auto rounded-lg"
                        disabled={checkEmail}
                        value={email}
                        autoFocus={true}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") {
                                handleMagicLinkSignIn();
                            }
                        }}
                        onChange={(e) => setEmail(e.target.value)}
                    />
                    <Button
                        variant="default"
                        className="p-6 w-[300px] mx-auto flex gap-2 items-center justify-center rounded-lg"
                        onClick={handleMagicLinkSignIn}
                        disabled={checkEmail}
                    >
                        <PaperPlaneTilt className="h-6 w-6 mr-2 font-bold" />
                        {checkEmail ? "Check your email" : "Send sign in code"}
                    </Button>
                </>
            )}
            {checkEmail && (
                <div className="flex items-center justify-center gap-4 text-muted-foreground flex-col">
                    <InputOTP
                        autoFocus={true}
                        maxLength={6}
                        value={otp || ""}
                        onChange={setOTP}
                        disabled={numFailures >= ALLOWED_OTP_ATTEMPTS}
                        onComplete={() =>
                            setTimeout(() => {
                                checkOTPAndRedirect();
                            }, 1000)
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
                    {otpError && (
                        <div className="text-red-500 text-sm">
                            {otpError} {ALLOWED_OTP_ATTEMPTS - numFailures} remaining attempts.
                        </div>
                    )}
                </div>
            )}

            {checkEmail && (
                <div className="flex items-center justify-center gap-4 text-muted-foreground flex-col md:flex-row">
                    <Button
                        variant="ghost"
                        className="p-0 text-orange-500"
                        disabled={recheckEmail}
                        onClick={() => {
                            handleMagicLinkSignIn();
                        }}
                    >
                        <ArrowsClockwise className="h-6 w-6 mr-2 text-gray-500" />
                        Resend email
                    </Button>
                    <LineVertical className="h-6 w-6 hidden md:block opacity-50" />
                    <Button
                        variant="ghost"
                        className="p-0 text-orange-500"
                        disabled={recheckEmail}
                        onClick={() => {
                            setEmail("");
                            setCheckEmail(false);
                        }}
                    >
                        <PencilSimple className="h-6 w-6 mr-2 text-gray-500" />
                        Use a different email
                    </Button>
                </div>
            )}
        </div>
    );
}

function MainSignInContext({
    handleGoogleScriptLoad,
    handleGoogleSignIn,
    isLoading,
    data,
    setUseEmailSignIn,
    isMobileWidth,
}: {
    handleGoogleScriptLoad: () => void;
    handleGoogleSignIn: () => void;
    isLoading: boolean;
    data: CredentialsData | undefined;
    setUseEmailSignIn: (useEmailSignIn: boolean) => void;
    isMobileWidth: boolean;
}) {
    const plugin = useRef(Autoplay({ delay: 4000, stopOnInteraction: true }));
    const [showArrows, setShowArrows] = useState(false);

    const tips = [
        {
            src: "https://assets.khoj.dev/sign_in_demos/research_mode.gif",
            alt: "Research tip",
            description: "Simplify Deep Work",
        },
        {
            src: "https://assets.khoj.dev/sign_in_demos/custom_agents.gif",
            alt: "Personalize tip",
            description: "Personalize your AI",
        },
        {
            src: "https://assets.khoj.dev/sign_in_demos/docment_questions.gif",
            alt: "Document tip",
            description: "Ask Anything",
        },
    ];

    return (
        <div className="flex flex-col gap-4 p-4 md:p-0">
            {!isMobileWidth && (
                <Carousel
                    plugins={[plugin.current]}
                    className="w-full"
                    onMouseEnter={() => {
                        plugin.current.stop();
                        setShowArrows(true);
                    }}
                    onMouseLeave={() => {
                        plugin.current.play();
                        setShowArrows(false);
                    }}
                >
                    <CarouselContent>
                        {tips.map((tip, index) => (
                            <CarouselItem key={index}>
                                <div className="relative p-0">
                                    <Card>
                                        <CardContent className="flex flex-col items-center justify-center rounded-b-none rounded-t-lg p-0">
                                            <img
                                                src={tip.src}
                                                alt={tip.alt}
                                                className="w-full h-auto rounded-b-none rounded-t-lg"
                                            />
                                            <div className="absolute bottom-0 flex items-center justify-center text-white bg-gradient-to-t from-black to-transparent text-center w-full p-4 py-6">
                                                {tip.description}
                                            </div>
                                        </CardContent>
                                    </Card>
                                </div>
                            </CarouselItem>
                        ))}
                    </CarouselContent>
                    {showArrows && (
                        <>
                            <CarouselPrevious className="absolute left-0" />
                            <CarouselNext className="absolute right-0" />
                        </>
                    )}
                </Carousel>
            )}
            <div className="flex flex-col gap-4 text-center p-2">
                <div className="text-center font-bold text-xl">
                    Sign in to unlock your second brain
                </div>
            </div>
            <div className="flex flex-col gap-8 pb-4 text-center align-middle items-center">
                <GoogleSignIn onLoad={handleGoogleScriptLoad} />
                {/* <div id="g_id_signin" /> */}
                <Button
                    variant="outline"
                    className="w-[300px] p-8 flex gap-2 items-center justify-center rounded-lg font-bold"
                    onClick={handleGoogleSignIn}
                    disabled={
                        isLoading ||
                        !data?.google ||
                        !data?.google.client_id ||
                        !data?.google.redirect_uri
                    }
                >
                    {isLoading ? (
                        <Spinner className="h-6 w-6" />
                    ) : (
                        <button className={`${styles.gsiMaterialButton}`}>
                            <div className={styles.gsiMaterialButtonState}></div>
                            <div className={styles.gsiMaterialButtonContentWrapper}>
                                <div
                                    className={`${styles.gsiMaterialButtonIcon} flex items-center justify-center`}
                                >
                                    <svg
                                        version="1.1"
                                        xmlns="http://www.w3.org/2000/svg"
                                        viewBox="0 0 48 48"
                                        xmlnsXlink="http://www.w3.org/1999/xlink"
                                    >
                                        <path
                                            fill="#EA4335"
                                            d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
                                        ></path>
                                        <path
                                            fill="#4285F4"
                                            d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
                                        ></path>
                                        <path
                                            fill="#FBBC05"
                                            d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
                                        ></path>
                                        <path
                                            fill="#34A853"
                                            d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
                                        ></path>
                                        <path fill="none" d="M0 0h48v48H0z"></path>
                                    </svg>
                                </div>
                            </div>
                        </button>
                    )}
                    Continue with Google
                </Button>

                <Button
                    variant="outline"
                    className="w-[300px] p-8 flex gap-2 items-center justify-center rounded-lg font-bold"
                    onClick={() => {
                        setUseEmailSignIn(true);
                    }}
                >
                    <PaperPlaneTilt className="h-6 w-6" />
                    Continue with Email
                </Button>
            </div>
            <div className="text-center text-muted-foreground text-sm mb-[20px]">
                By logging in, you agree to our{" "}
                <Link href="https://khoj.dev/terms-of-service">Terms of Service</Link>. See{" "}
                <Link href="https://khoj.dev/privacy-policy">Privacy Policy</Link>.
            </div>
        </div>
    );
}
