"use client";

import styles from "./chatHistory.module.css";
import { useRef, useEffect, useState } from "react";

import ChatMessage, {
    ChatHistoryData,
    StreamMessage,
    TrainOfThought,
} from "../chatMessage/chatMessage";

import { ScrollArea } from "@/components/ui/scroll-area";

import { InlineLoading } from "../loading/loading";

import { Lightbulb, ArrowDown, XCircle } from "@phosphor-icons/react";

import AgentProfileCard from "../profileCard/profileCard";
import { getIconFromIconName } from "@/app/common/iconUtils";
import { AgentData } from "@/app/agents/page";
import React from "react";
import { useIsMobileWidth } from "@/app/common/utils";
import { Button } from "@/components/ui/button";

interface ChatResponse {
    status: string;
    response: ChatHistoryData;
}

interface ChatHistory {
    [key: string]: string;
}

interface ChatHistoryProps {
    conversationId: string;
    setTitle: (title: string) => void;
    pendingMessage?: string;
    incomingMessages?: StreamMessage[];
    setIncomingMessages?: (incomingMessages: StreamMessage[]) => void;
    publicConversationSlug?: string;
    setAgent: (agent: AgentData) => void;
    customClassName?: string;
}

interface TrainOfThoughtComponentProps {
    trainOfThought: string[];
    lastMessage: boolean;
    agentColor: string;
    keyId: string;
    completed?: boolean;
}

function TrainOfThoughtComponent(props: TrainOfThoughtComponentProps) {
    const lastIndex = props.trainOfThought.length - 1;
    const [collapsed, setCollapsed] = useState(props.completed);

    useEffect(() => {
        if (props.completed) {
            setCollapsed(true);
        }
    }, [props.completed]);

    return (
        <div
            className={`${!collapsed ? styles.trainOfThought + " shadow-sm" : ""}`}
            key={props.keyId}
        >
            {!props.completed && <InlineLoading className="float-right" />}
            {props.completed &&
                (collapsed ? (
                    <Button
                        className="w-fit text-left justify-start content-start text-xs"
                        onClick={() => setCollapsed(false)}
                        variant="ghost"
                        size="sm"
                    >
                        What was my train of thought?
                    </Button>
                ) : (
                    <Button
                        className="w-fit text-left justify-start content-start text-xs p-0 h-fit"
                        onClick={() => setCollapsed(true)}
                        variant="ghost"
                        size="sm"
                    >
                        <XCircle size={16} className="mr-1" />
                        Close
                    </Button>
                ))}
            {!collapsed &&
                props.trainOfThought.map((train, index) => (
                    <TrainOfThought
                        key={`train-${index}`}
                        message={train}
                        primary={index === lastIndex && props.lastMessage && !props.completed}
                        agentColor={props.agentColor}
                    />
                ))}
        </div>
    );
}

export default function ChatHistory(props: ChatHistoryProps) {
    const [data, setData] = useState<ChatHistoryData | null>(null);
    const [currentPage, setCurrentPage] = useState(0);
    const [hasMoreMessages, setHasMoreMessages] = useState(true);
    const [currentTurnId, setCurrentTurnId] = useState<string | null>(null);
    const sentinelRef = useRef<HTMLDivElement | null>(null);
    const scrollAreaRef = useRef<HTMLDivElement | null>(null);
    const latestUserMessageRef = useRef<HTMLDivElement | null>(null);
    const latestFetchedMessageRef = useRef<HTMLDivElement | null>(null);

    const [incompleteIncomingMessageIndex, setIncompleteIncomingMessageIndex] = useState<
        number | null
    >(null);
    const [fetchingData, setFetchingData] = useState(false);
    const [isNearBottom, setIsNearBottom] = useState(true);
    const isMobileWidth = useIsMobileWidth();
    const scrollAreaSelector = "[data-radix-scroll-area-viewport]";
    const fetchMessageCount = 10;

    useEffect(() => {
        const scrollAreaEl = scrollAreaRef.current?.querySelector<HTMLElement>(scrollAreaSelector);
        if (!scrollAreaEl) return;

        const detectIsNearBottom = () => {
            const { scrollTop, scrollHeight, clientHeight } = scrollAreaEl;
            const bottomThreshold = 50; // pixels from bottom
            const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
            const isNearBottom = distanceFromBottom <= bottomThreshold;
            setIsNearBottom(isNearBottom);
        };

        scrollAreaEl.addEventListener("scroll", detectIsNearBottom);
        return () => scrollAreaEl.removeEventListener("scroll", detectIsNearBottom);
    }, []);

    // Auto scroll while incoming message is streamed
    useEffect(() => {
        if (props.incomingMessages && props.incomingMessages.length > 0 && isNearBottom) {
            scrollToBottom();
        }
    }, [props.incomingMessages, isNearBottom]);

    // Scroll to most recent user message after the first page of chat messages is loaded.
    useEffect(() => {
        if (data && data.chat && data.chat.length > 0 && currentPage < 2) {
            requestAnimationFrame(() => {
                latestUserMessageRef.current?.scrollIntoView({ behavior: "auto", block: "start" });
            });
        }
    }, [data, currentPage]);

    useEffect(() => {
        if (!hasMoreMessages || fetchingData) return;

        // TODO: A future optimization would be to add a time to delay to re-enabling the intersection observer.
        const observer = new IntersectionObserver(
            (entries) => {
                if (entries[0].isIntersecting && hasMoreMessages) {
                    setFetchingData(true);
                    fetchMoreMessages(currentPage);
                }
            },
            { threshold: 1.0 },
        );

        if (sentinelRef.current) {
            observer.observe(sentinelRef.current);
        }

        return () => observer.disconnect();
    }, [hasMoreMessages, currentPage, fetchingData]);

    useEffect(() => {
        setHasMoreMessages(true);
        setFetchingData(false);
        setCurrentPage(0);
        setData(null);
    }, [props.conversationId]);

    useEffect(() => {
        if (props.incomingMessages) {
            const lastMessage = props.incomingMessages[props.incomingMessages.length - 1];
            if (lastMessage && !lastMessage.completed) {
                setIncompleteIncomingMessageIndex(props.incomingMessages.length - 1);
                props.setTitle(lastMessage.rawQuery);
                // Store the turnId when we get it
                if (lastMessage.turnId) {
                    setCurrentTurnId(lastMessage.turnId);
                }
            }
        }
    }, [props.incomingMessages]);

    const adjustScrollPosition = () => {
        const scrollAreaEl = scrollAreaRef.current?.querySelector<HTMLElement>(scrollAreaSelector);
        requestAnimationFrame(() => {
            // Snap scroll position to the latest fetched message ref
            latestFetchedMessageRef.current?.scrollIntoView({ behavior: "auto", block: "start" });
            // Now scroll up smoothly to render user scroll action
            scrollAreaEl?.scrollBy({ behavior: "smooth", top: -150 });
        });
    };

    function fetchMoreMessages(currentPage: number) {
        if (!hasMoreMessages || fetchingData) return;
        const nextPage = currentPage + 1;
        const maxMessagesToFetch = nextPage * fetchMessageCount;
        let conversationFetchURL = "";

        if (props.conversationId) {
            conversationFetchURL = `/api/chat/history?client=web&conversation_id=${encodeURIComponent(props.conversationId)}&n=${maxMessagesToFetch}`;
        } else if (props.publicConversationSlug) {
            conversationFetchURL = `/api/chat/share/history?client=web&public_conversation_slug=${props.publicConversationSlug}&n=${maxMessagesToFetch}`;
        } else {
            return;
        }

        fetch(conversationFetchURL)
            .then((response) => response.json())
            .then((chatData: ChatResponse) => {
                props.setTitle(chatData.response.slug);
                if (
                    chatData &&
                    chatData.response &&
                    chatData.response.chat &&
                    chatData.response.chat.length > 0
                ) {
                    setCurrentPage(Math.ceil(chatData.response.chat.length / fetchMessageCount));
                    if (chatData.response.chat.length === data?.chat.length) {
                        setHasMoreMessages(false);
                        setFetchingData(false);
                        return;
                    }
                    props.setAgent(chatData.response.agent);
                    setData(chatData.response);
                    setFetchingData(false);
                    if (currentPage === 0) {
                        scrollToBottom(true);
                    } else {
                        adjustScrollPosition();
                    }
                } else {
                    if (chatData.response.agent && chatData.response.conversation_id) {
                        const chatMetadata = {
                            chat: [],
                            agent: chatData.response.agent,
                            conversation_id: chatData.response.conversation_id,
                            slug: chatData.response.slug,
                        };
                        props.setAgent(chatData.response.agent);
                        setData(chatMetadata);
                    }

                    setHasMoreMessages(false);
                    setFetchingData(false);
                }
            })
            .catch((err) => {
                console.error(err);
                window.location.href = "/";
            });
    }

    const scrollToBottom = (instant: boolean = false) => {
        const scrollAreaEl = scrollAreaRef.current?.querySelector<HTMLElement>(scrollAreaSelector);
        requestAnimationFrame(() => {
            scrollAreaEl?.scrollTo({
                top: scrollAreaEl.scrollHeight,
                behavior: instant ? "auto" : "smooth",
            });
        });
        setIsNearBottom(true);
    };

    function constructAgentLink() {
        if (!data || !data.agent || !data.agent?.slug) return `/agents`;
        return `/agents?agent=${data.agent?.slug}`;
    }

    function constructAgentName() {
        if (!data || !data.agent || !data.agent?.name) return `Agent`;
        return data.agent?.name;
    }

    function constructAgentPersona() {
        if (!data || !data.agent || !data.agent?.persona)
            return `Your agent is no longer available. You will be reset to the default agent.`;
        return data.agent?.persona;
    }

    const handleDeleteMessage = (turnId?: string) => {
        if (!turnId) return;

        setData((prevData) => {
            if (!prevData || !turnId) return prevData;
            return {
                ...prevData,
                chat: prevData.chat.filter((msg) => msg.turnId !== turnId),
            };
        });

        // Update incoming messages if they exist
        if (props.incomingMessages && props.setIncomingMessages) {
            props.setIncomingMessages(
                props.incomingMessages.filter((msg) => msg.turnId !== turnId),
            );
        }
    };

    if (!props.conversationId && !props.publicConversationSlug) {
        return null;
    }

    return (
        <ScrollArea className={`h-[73vh] relative`} ref={scrollAreaRef}>
            <div>
                <div className={`${styles.chatHistory} ${props.customClassName}`}>
                    <div ref={sentinelRef} style={{ height: "1px" }}>
                        {fetchingData && <InlineLoading className="opacity-50" />}
                    </div>
                    {data &&
                        data.chat &&
                        data.chat.map((chatMessage, index) => (
                            <>
                                {chatMessage.trainOfThought && chatMessage.by === "khoj" && (
                                    <TrainOfThoughtComponent
                                        trainOfThought={chatMessage.trainOfThought?.map(
                                            (train) => train.data,
                                        )}
                                        lastMessage={false}
                                        agentColor={data?.agent?.color || "orange"}
                                        key={`${index}trainOfThought`}
                                        keyId={`${index}trainOfThought`}
                                        completed={true}
                                    />
                                )}
                                <ChatMessage
                                    key={`${index}fullHistory`}
                                    ref={
                                        // attach ref to the second last message to handle scroll on page load
                                        index === data.chat.length - 2
                                            ? latestUserMessageRef
                                            : // attach ref to the newest fetched message to handle scroll on fetch
                                              // note: stabilize index selection against last page having less messages than fetchMessageCount
                                              index ===
                                                data.chat.length -
                                                    (currentPage - 1) * fetchMessageCount
                                              ? latestFetchedMessageRef
                                              : null
                                    }
                                    isMobileWidth={isMobileWidth}
                                    chatMessage={chatMessage}
                                    customClassName="fullHistory"
                                    borderLeftColor={`${data?.agent?.color}-500`}
                                    isLastMessage={index === data.chat.length - 1}
                                    onDeleteMessage={handleDeleteMessage}
                                    conversationId={props.conversationId}
                                />
                            </>
                        ))}
                    {props.incomingMessages &&
                        props.incomingMessages.map((message, index) => {
                            const messageTurnId = message.turnId ?? currentTurnId ?? undefined;
                            return (
                                <React.Fragment key={`incomingMessage${index}`}>
                                    <ChatMessage
                                        key={`${index}outgoing`}
                                        isMobileWidth={isMobileWidth}
                                        chatMessage={{
                                            message: message.rawQuery,
                                            context: [],
                                            onlineContext: {},
                                            codeContext: {},
                                            created: message.timestamp,
                                            by: "you",
                                            automationId: "",
                                            images: message.images,
                                            conversationId: props.conversationId,
                                            turnId: messageTurnId,
                                            queryFiles: message.queryFiles,
                                        }}
                                        customClassName="fullHistory"
                                        borderLeftColor={`${data?.agent?.color}-500`}
                                        onDeleteMessage={handleDeleteMessage}
                                        conversationId={props.conversationId}
                                        turnId={messageTurnId}
                                    />
                                    {message.trainOfThought && (
                                        <TrainOfThoughtComponent
                                            trainOfThought={message.trainOfThought}
                                            lastMessage={index === incompleteIncomingMessageIndex}
                                            agentColor={data?.agent?.color || "orange"}
                                            key={`${index}trainOfThought`}
                                            keyId={`${index}trainOfThought`}
                                            completed={message.completed}
                                        />
                                    )}
                                    <ChatMessage
                                        key={`${index}incoming`}
                                        isMobileWidth={isMobileWidth}
                                        chatMessage={{
                                            message: message.rawResponse,
                                            context: message.context,
                                            onlineContext: message.onlineContext,
                                            codeContext: message.codeContext,
                                            created: message.timestamp,
                                            by: "khoj",
                                            automationId: "",
                                            rawQuery: message.rawQuery,
                                            intent: {
                                                type: message.intentType || "",
                                                query: message.rawQuery,
                                                "memory-type": "",
                                                "inferred-queries": message.inferredQueries || [],
                                            },
                                            conversationId: props.conversationId,
                                            images: message.generatedImages,
                                            queryFiles: message.generatedFiles,
                                            mermaidjsDiagram: message.generatedMermaidjsDiagram,
                                            turnId: messageTurnId,
                                        }}
                                        conversationId={props.conversationId}
                                        turnId={messageTurnId}
                                        onDeleteMessage={handleDeleteMessage}
                                        customClassName="fullHistory"
                                        borderLeftColor={`${data?.agent?.color}-500`}
                                        isLastMessage={true}
                                    />
                                </React.Fragment>
                            );
                        })}
                    {props.pendingMessage && (
                        <ChatMessage
                            key={`pendingMessage-${props.pendingMessage.length}`}
                            isMobileWidth={isMobileWidth}
                            chatMessage={{
                                message: props.pendingMessage,
                                context: [],
                                onlineContext: {},
                                codeContext: {},
                                created: new Date().getTime().toString(),
                                by: "you",
                                automationId: "",
                                conversationId: props.conversationId,
                                turnId: undefined,
                            }}
                            conversationId={props.conversationId}
                            onDeleteMessage={handleDeleteMessage}
                            customClassName="fullHistory"
                            borderLeftColor={`${data?.agent?.color}-500`}
                            isLastMessage={true}
                        />
                    )}
                    {data && (
                        <div className={`${styles.agentIndicator} pb-4`}>
                            <div className="relative group mx-2 cursor-pointer">
                                <AgentProfileCard
                                    name={constructAgentName()}
                                    link={constructAgentLink()}
                                    avatar={
                                        getIconFromIconName(
                                            data.agent?.icon,
                                            data.agent?.color,
                                        ) || <Lightbulb />
                                    }
                                    description={constructAgentPersona()}
                                />
                            </div>
                        </div>
                    )}
                </div>
                <div className={`${props.customClassName} fixed bottom-[20%] z-10`}>
                    {!isNearBottom && (
                        <button
                            title="Scroll to bottom"
                            className="absolute bottom-0 right-0 bg-white dark:bg-[hsl(var(--background))] text-neutral-500 dark:text-white p-2 rounded-full shadow-xl"
                            onClick={() => {
                                scrollToBottom();
                                setIsNearBottom(true);
                            }}
                        >
                            <ArrowDown size={24} />
                        </button>
                    )}
                </div>
            </div>
        </ScrollArea>
    );
}
