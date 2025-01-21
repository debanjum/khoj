"use client";

import styles from "./chat.module.css";
import React, { Suspense, useEffect, useRef, useState } from "react";

import ChatHistory from "../components/chatHistory/chatHistory";
import { useSearchParams } from "next/navigation";
import Loading from "../components/loading/loading";

import { generateNewTitle, processMessageChunk } from "../common/chatFunctions";

import "katex/dist/katex.min.css";

import {
    CodeContext,
    Context,
    OnlineContext,
    StreamMessage,
} from "../components/chatMessage/chatMessage";
import { useIPLocationData, useIsMobileWidth, welcomeConsole } from "../common/utils";
import {
    AttachedFileText,
    ChatInputArea,
    ChatOptions,
} from "../components/chatInputArea/chatInputArea";
import { useAuthenticatedData } from "../common/auth";
import { AgentData } from "../agents/page";
import { ChatSessionActionMenu } from "../components/allConversations/allConversations";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "../components/appSidebar/appSidebar";
import { Separator } from "@/components/ui/separator";
import { KhojLogoType } from "../components/logo/khojLogo";

interface ChatBodyDataProps {
    chatOptionsData: ChatOptions | null;
    setTitle: (title: string) => void;
    onConversationIdChange?: (conversationId: string) => void;
    setQueryToProcess: (query: string) => void;
    streamedMessages: StreamMessage[];
    setStreamedMessages: (messages: StreamMessage[]) => void;
    setUploadedFiles: (files: AttachedFileText[] | undefined) => void;
    isMobileWidth?: boolean;
    isLoggedIn: boolean;
    setImages: (images: string[]) => void;
    setTriggeredAbort: (triggeredAbort: boolean) => void;
}

function ChatBodyData(props: ChatBodyDataProps) {
    const searchParams = useSearchParams();
    const conversationId = searchParams.get("conversationId");
    const [message, setMessage] = useState("");
    const [images, setImages] = useState<string[]>([]);
    const [processingMessage, setProcessingMessage] = useState(false);
    const [agentMetadata, setAgentMetadata] = useState<AgentData | null>(null);
    const [isInResearchMode, setIsInResearchMode] = useState(false);
    const chatInputRef = useRef<HTMLTextAreaElement>(null);

    const setQueryToProcess = props.setQueryToProcess;
    const onConversationIdChange = props.onConversationIdChange;

    const chatHistoryCustomClassName = props.isMobileWidth ? "w-full" : "w-4/6";

    useEffect(() => {
        if (images.length > 0) {
            const encodedImages = images.map((image) => encodeURIComponent(image));
            props.setImages(encodedImages);
        }
    }, [images, props.setImages]);

    useEffect(() => {
        const storedImages = localStorage.getItem("images");
        if (storedImages) {
            const parsedImages: string[] = JSON.parse(storedImages);
            setImages(parsedImages);
            const encodedImages = parsedImages.map((img: string) => encodeURIComponent(img));
            props.setImages(encodedImages);
            localStorage.removeItem("images");
        }

        const storedMessage = localStorage.getItem("message");
        if (storedMessage) {
            setProcessingMessage(true);
            setQueryToProcess(storedMessage);

            if (storedMessage.trim().startsWith("/research")) {
                setIsInResearchMode(true);
            }
        }

        const storedUploadedFiles = localStorage.getItem("uploadedFiles");

        if (storedUploadedFiles) {
            const parsedFiles = storedUploadedFiles ? JSON.parse(storedUploadedFiles) : [];
            const uploadedFiles: AttachedFileText[] = [];
            for (const file of parsedFiles) {
                uploadedFiles.push({
                    name: file.name,
                    file_type: file.file_type,
                    content: file.content,
                    size: file.size,
                });
            }
            localStorage.removeItem("uploadedFiles");
            props.setUploadedFiles(uploadedFiles);
        }
    }, [setQueryToProcess, props.setImages, conversationId]);

    useEffect(() => {
        if (message) {
            setProcessingMessage(true);
            setQueryToProcess(message);
        }
    }, [message, setQueryToProcess]);

    useEffect(() => {
        if (conversationId) {
            onConversationIdChange?.(conversationId);
        }
    }, [conversationId, onConversationIdChange]);

    useEffect(() => {
        if (
            props.streamedMessages &&
            props.streamedMessages.length > 0 &&
            props.streamedMessages[props.streamedMessages.length - 1].completed
        ) {
            setProcessingMessage(false);
            setImages([]); // Reset images after processing
            props.setUploadedFiles(undefined); // Reset uploaded files after processing
        } else {
            setMessage("");
        }
    }, [props.streamedMessages]);

    if (!conversationId) {
        window.location.href = "/";
        return;
    }

    return (
        <>
            <div className={false ? styles.chatBody : styles.chatBodyFull}>
                <ChatHistory
                    conversationId={conversationId}
                    setTitle={props.setTitle}
                    setAgent={setAgentMetadata}
                    pendingMessage={processingMessage ? message : ""}
                    incomingMessages={props.streamedMessages}
                    setIncomingMessages={props.setStreamedMessages}
                    customClassName={chatHistoryCustomClassName}
                />
            </div>
            <div
                className={`${styles.inputBox} p-1 md:px-2 shadow-md bg-background align-middle items-center justify-center dark:bg-neutral-700 dark:border-0 dark:shadow-sm rounded-2xl md:rounded-xl h-fit ${chatHistoryCustomClassName} mr-auto ml-auto`}
            >
                <ChatInputArea
                    agentColor={agentMetadata?.color}
                    isLoggedIn={props.isLoggedIn}
                    sendMessage={(message) => setMessage(message)}
                    sendImage={(image) => setImages((prevImages) => [...prevImages, image])}
                    sendDisabled={processingMessage}
                    chatOptionsData={props.chatOptionsData}
                    conversationId={conversationId}
                    isMobileWidth={props.isMobileWidth}
                    setUploadedFiles={props.setUploadedFiles}
                    ref={chatInputRef}
                    isResearchModeEnabled={isInResearchMode}
                    setTriggeredAbort={props.setTriggeredAbort}
                />
            </div>
        </>
    );
}

export default function Chat() {
    const defaultTitle = "Khoj AI - Chat";
    const [chatOptionsData, setChatOptionsData] = useState<ChatOptions | null>(null);
    const [isLoading, setLoading] = useState(true);
    const [title, setTitle] = useState(defaultTitle);
    const [conversationId, setConversationID] = useState<string | null>(null);
    const [messages, setMessages] = useState<StreamMessage[]>([]);
    const [queryToProcess, setQueryToProcess] = useState<string>("");
    const [processQuerySignal, setProcessQuerySignal] = useState(false);
    const [uploadedFiles, setUploadedFiles] = useState<AttachedFileText[] | undefined>(undefined);
    const [images, setImages] = useState<string[]>([]);

    const [abortMessageStreamController, setAbortMessageStreamController] =
        useState<AbortController | null>(null);
    const [triggeredAbort, setTriggeredAbort] = useState(false);

    const { locationData, locationDataError, locationDataLoading } = useIPLocationData() || {
        locationData: {
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        },
    };
    const {
        data: authenticatedData,
        error: authenticationError,
        isLoading: authenticationLoading,
    } = useAuthenticatedData();
    const isMobileWidth = useIsMobileWidth();

    useEffect(() => {
        fetch("/api/chat/options")
            .then((response) => response.json())
            .then((data: ChatOptions) => {
                setLoading(false);
                // Render chat options, if any
                if (data) {
                    setChatOptionsData(data);
                }
            })
            .catch((err) => {
                console.error(err);
                return;
            });

        welcomeConsole();
    }, []);

    useEffect(() => {
        if (triggeredAbort) {
            abortMessageStreamController?.abort();
            handleAbortedMessage();
            setTriggeredAbort(false);
        }
    }),
        [triggeredAbort];

    useEffect(() => {
        if (queryToProcess) {
            const newStreamMessage: StreamMessage = {
                rawResponse: "",
                trainOfThought: [],
                context: [],
                onlineContext: {},
                codeContext: {},
                completed: false,
                timestamp: new Date().toISOString(),
                rawQuery: queryToProcess || "",
                images: images,
                queryFiles: uploadedFiles,
            };
            setMessages((prevMessages) => [...prevMessages, newStreamMessage]);
            setProcessQuerySignal(true);
            setAbortMessageStreamController(new AbortController());
        }
    }, [queryToProcess]);

    useEffect(() => {
        if (processQuerySignal) {
            if (locationDataLoading) {
                return;
            }

            chat();
        }
    }, [processQuerySignal, locationDataLoading]);

    async function readChatStream(response: Response) {
        if (!response.ok) throw new Error(response.statusText);
        if (!response.body) throw new Error("Response body is null");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const eventDelimiter = "␃🔚␗";
        let buffer = "";

        // Track context used for chat response
        let context: Context[] = [];
        let onlineContext: OnlineContext = {};
        let codeContext: CodeContext = {};

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                setQueryToProcess("");
                setProcessQuerySignal(false);
                setImages([]);

                if (conversationId) generateNewTitle(conversationId, setTitle);

                break;
            }

            const chunk = decoder.decode(value, { stream: true });
            buffer += chunk;

            let newEventIndex;
            while ((newEventIndex = buffer.indexOf(eventDelimiter)) !== -1) {
                const event = buffer.slice(0, newEventIndex);
                buffer = buffer.slice(newEventIndex + eventDelimiter.length);
                if (event) {
                    const currentMessage = messages.find((message) => !message.completed);

                    if (!currentMessage) {
                        console.error("No current message found");
                        return;
                    }

                    // Track context used for chat response. References are rendered at the end of the chat
                    ({ context, onlineContext, codeContext } = processMessageChunk(
                        event,
                        currentMessage,
                        context,
                        onlineContext,
                        codeContext,
                    ));

                    setMessages([...messages]);
                }
            }
        }
    }

    function handleAbortedMessage() {
        const currentMessage = messages.find((message) => !message.completed);
        if (!currentMessage) return;

        currentMessage.completed = true;
        setMessages([...messages]);
        setQueryToProcess("");
        setProcessQuerySignal(false);
    }

    async function chat() {
        localStorage.removeItem("message");
        if (!queryToProcess || !conversationId) return;
        const chatAPI = "/api/chat?client=web";
        const chatAPIBody = {
            q: queryToProcess,
            conversation_id: conversationId,
            stream: true,
            ...(locationData && {
                city: locationData.city,
                region: locationData.region,
                country: locationData.country,
                country_code: locationData.countryCode,
                timezone: locationData.timezone,
            }),
            ...(images.length > 0 && { images: images }),
            ...(uploadedFiles && { files: uploadedFiles }),
        };

        const response = await fetch(chatAPI, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(chatAPIBody),
            signal: abortMessageStreamController?.signal,
        });

        try {
            await readChatStream(response);
        } catch (err) {
            let apiError;
            try {
                apiError = await response.json();
            } catch (err) {
                // Error reading API error response
                apiError = {
                    streamError: "Error reading API error response stream. Expected JSON response.",
                };
            }
            console.error(apiError);
            // Retrieve latest message being processed
            const currentMessage = messages.find((message) => !message.completed);
            if (!currentMessage) return;

            // Render error message as current message
            const errorMessage = (err as Error).message;
            const errorName = (err as Error).name;
            if (errorMessage.includes("Error in input stream"))
                currentMessage.rawResponse = `Woops! The connection broke while I was writing my thoughts down. Maybe try again in a bit or dislike this message if the issue persists?`;
            else if (apiError.streamError) {
                currentMessage.rawResponse = `Umm, not sure what just happened but I lost my train of thought. Could you try again or ask my developers to look into this if the issue persists? They can be contacted at the Khoj Github, Discord or team@khoj.dev.`;
            } else if (response.status === 429) {
                "detail" in apiError
                    ? (currentMessage.rawResponse = `${apiError.detail}`)
                    : (currentMessage.rawResponse = `I'm a bit overwhelmed at the moment. Could you try again in a bit or dislike this message if the issue persists?`);
            } else if (errorName === "AbortError") {
                currentMessage.rawResponse = `I've stopped processing this message. If you'd like to continue, please send the message again.`;
            } else {
                currentMessage.rawResponse = `Umm, not sure what just happened. I see this error message: ${errorMessage}. Could you try again or dislike this message if the issue persists?`;
            }

            // Complete message streaming teardown properly
            currentMessage.completed = true;
            setMessages([...messages]);
            setQueryToProcess("");
            setProcessQuerySignal(false);
        }
    }

    const handleConversationIdChange = (newConversationId: string) => {
        setConversationID(newConversationId);
    };

    if (isLoading) return <Loading />;

    return (
        <SidebarProvider>
            <AppSidebar conversationId={conversationId || ""} />
            <SidebarInset>
                <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4">
                    <SidebarTrigger className="-ml-1" />
                    <Separator orientation="vertical" className="mr-2 h-4" />
                    {conversationId && (
                        <div
                            className={`${styles.chatTitleWrapper} text-nowrap text-ellipsis overflow-hidden max-w-screen-md grid items-top font-bold mx-2 md:mr-8 col-auto h-fit`}
                        >
                            {isMobileWidth ? (
                                <a className="p-0 no-underline" href="/">
                                    <KhojLogoType className="h-auto w-16" />
                                </a>
                            ) : (
                                title && (
                                    <>
                                        <h2
                                            className={`text-lg text-ellipsis whitespace-nowrap overflow-x-hidden mr-4`}
                                        >
                                            {title}
                                        </h2>
                                        <ChatSessionActionMenu
                                            conversationId={conversationId}
                                            setTitle={setTitle}
                                            sizing={"md"}
                                        />
                                    </>
                                )
                            )}
                        </div>
                    )}
                </header>
                <div className={`${styles.main} ${styles.chatLayout}`}>
                    <title>
                        {`${defaultTitle}${!!title && title !== defaultTitle ? `: ${title}` : ""}`}
                    </title>
                    <div className={styles.chatBox}>
                        <div className={styles.chatBoxBody}>
                            <Suspense fallback={<Loading />}>
                                <ChatBodyData
                                    isLoggedIn={authenticatedData ? true : false}
                                    streamedMessages={messages}
                                    setStreamedMessages={setMessages}
                                    chatOptionsData={chatOptionsData}
                                    setTitle={setTitle}
                                    setQueryToProcess={setQueryToProcess}
                                    setUploadedFiles={setUploadedFiles}
                                    isMobileWidth={isMobileWidth}
                                    onConversationIdChange={handleConversationIdChange}
                                    setImages={setImages}
                                    setTriggeredAbort={setTriggeredAbort}
                                />
                            </Suspense>
                        </div>
                    </div>
                </div>
            </SidebarInset>
        </SidebarProvider>
    );
}
