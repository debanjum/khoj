"use client";

import styles from "./chatMessage.module.css";

import markdownIt from "markdown-it";
import mditHljs from "markdown-it-highlightjs";
import React, { useEffect, useRef, useState, forwardRef } from "react";
import { createRoot } from "react-dom/client";

import "katex/dist/katex.min.css";

import { TeaserReferencesSection, constructAllReferences } from "../referencePanel/referencePanel";
import { renderCodeGenImageInline } from "@/app/common/chatFunctions";

import {
    ThumbsUp,
    ThumbsDown,
    Copy,
    Brain,
    Cloud,
    Folder,
    Book,
    Aperture,
    SpeakerHigh,
    MagnifyingGlass,
    Pause,
    Palette,
    ClipboardText,
    Check,
    Code,
    Shapes,
    Trash,
    Toolbox,
} from "@phosphor-icons/react";

import DOMPurify from "dompurify";
import { InlineLoading } from "../loading/loading";
import { convertColorToTextClass } from "@/app/common/colorUtils";
import { AgentData } from "@/app/agents/page";

import renderMathInElement from "katex/contrib/auto-render";
import "katex/dist/katex.min.css";
import ExcalidrawComponent from "../excalidraw/excalidraw";
import { AttachedFileText } from "../chatInputArea/chatInputArea";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTrigger,
} from "@/components/ui/dialog";
import { DialogTitle } from "@radix-ui/react-dialog";
import { convertBytesToText } from "@/app/common/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getIconFromFilename } from "@/app/common/iconUtils";
import Mermaid from "../mermaid/mermaid";

const md = new markdownIt({
    html: true,
    linkify: true,
    typographer: true,
});

md.use(mditHljs, {
    inline: true,
    code: true,
});

export interface Context {
    compiled: string;
    file: string;
}

export interface OnlineContext {
    [key: string]: OnlineContextData;
}

export interface WebPage {
    link: string;
    query: string;
    snippet: string;
}

interface OrganicContext {
    snippet: string;
    title: string;
    link: string;
}

interface PeopleAlsoAsk {
    link: string;
    question: string;
    snippet: string;
    title: string;
}

export interface OnlineContextData {
    webpages: WebPage[];
    answerBox: {
        answer: string;
        source: string;
        title: string;
    };
    knowledgeGraph: {
        attributes: {
            [key: string]: string;
        };
        description: string;
        descriptionLink: string;
        descriptionSource: string;
        imageUrl: string;
        title: string;
        type: string;
    };
    organic: OrganicContext[];
    peopleAlsoAsk: PeopleAlsoAsk[];
}

export interface CodeContext {
    [key: string]: CodeContextData;
}

export interface CodeContextData {
    code: string;
    results: {
        success: boolean;
        output_files: CodeContextFile[];
        std_out: string;
        std_err: string;
        code_runtime: number;
    };
}

export interface CodeContextFile {
    filename: string;
    b64_data: string;
}

interface Intent {
    type: string;
    query: string;
    "memory-type": string;
    "inferred-queries": string[];
}

interface TrainOfThoughtObject {
    type: string;
    data: string;
}

export interface SingleChatMessage {
    automationId: string;
    by: string;
    message: string;
    created: string;
    context: Context[];
    onlineContext: OnlineContext;
    codeContext: CodeContext;
    trainOfThought?: TrainOfThoughtObject[];
    rawQuery?: string;
    intent?: Intent;
    agent?: AgentData;
    images?: string[];
    conversationId: string;
    turnId?: string;
    queryFiles?: AttachedFileText[];
    excalidrawDiagram?: string;
    mermaidjsDiagram?: string;
}

export interface StreamMessage {
    rawResponse: string;
    trainOfThought: string[];
    context: Context[];
    onlineContext: OnlineContext;
    codeContext: CodeContext;
    completed: boolean;
    rawQuery: string;
    timestamp: string;
    agent?: AgentData;
    images?: string[];
    intentType?: string;
    inferredQueries?: string[];
    turnId?: string;
    queryFiles?: AttachedFileText[];
    excalidrawDiagram?: string;
    mermaidjsDiagram?: string;
    generatedFiles?: AttachedFileText[];
    generatedImages?: string[];
    generatedExcalidrawDiagram?: string;
    generatedMermaidjsDiagram?: string;
}

export interface ChatHistoryData {
    chat: SingleChatMessage[];
    agent: AgentData;
    conversation_id: string;
    slug: string;
}

function sendFeedback(uquery: string, kquery: string, sentiment: string) {
    fetch("/api/chat/feedback", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ uquery: uquery, kquery: kquery, sentiment: sentiment }),
    });
}

function FeedbackButtons({ uquery, kquery }: { uquery: string; kquery: string }) {
    // Tri-state feedback state.
    // Null = no feedback, true = positive feedback, false = negative feedback.
    const [feedbackState, setFeedbackState] = useState<boolean | null>(null);

    useEffect(() => {
        if (feedbackState !== null) {
            setTimeout(() => {
                setFeedbackState(null);
            }, 2000);
        }
    }, [feedbackState]);

    return (
        <div className={`${styles.feedbackButtons} flex align-middle justify-center items-center`}>
            <button
                title="Like"
                className={styles.thumbsUpButton}
                disabled={feedbackState !== null}
                onClick={() => {
                    sendFeedback(uquery, kquery, "positive");
                    setFeedbackState(true);
                }}
            >
                {feedbackState === true ? (
                    <ThumbsUp alt="Liked Message" className="text-green-500" weight="fill" />
                ) : (
                    <ThumbsUp
                        alt="Like Message"
                        className="hsl(var(--muted-foreground)) hover:text-green-500"
                    />
                )}
            </button>
            <button
                title="Dislike"
                className={styles.thumbsDownButton}
                disabled={feedbackState !== null}
                onClick={() => {
                    sendFeedback(uquery, kquery, "negative");
                    setFeedbackState(false);
                }}
            >
                {feedbackState === false ? (
                    <ThumbsDown alt="Disliked Message" className="text-red-500" weight="fill" />
                ) : (
                    <ThumbsDown
                        alt="Dislike Message"
                        className="hsl(var(--muted-foreground)) hover:text-red-500"
                    />
                )}
            </button>
        </div>
    );
}

interface ChatMessageProps {
    chatMessage: SingleChatMessage;
    isMobileWidth: boolean;
    customClassName?: string;
    borderLeftColor?: string;
    isLastMessage?: boolean;
    agent?: AgentData;
    onDeleteMessage: (turnId?: string) => void;
    conversationId: string;
    turnId?: string;
    generatedImage?: string;
    excalidrawDiagram?: string;
    mermaidjsDiagram?: string;
    generatedFiles?: AttachedFileText[];
}

interface TrainOfThoughtProps {
    message: string;
    primary: boolean;
    agentColor: string;
}

function chooseIconFromHeader(header: string, iconColor: string) {
    const compareHeader = header.toLowerCase();
    const classNames = `inline mt-1 mr-2 ${iconColor} h-4 w-4`;
    if (compareHeader.includes("understanding")) {
        return <Brain className={`${classNames}`} />;
    }

    if (compareHeader.includes("generating")) {
        return <Cloud className={`${classNames}`} />;
    }

    if (compareHeader.includes("tools")) {
        return <Toolbox className={`${classNames}`} />;
    }

    if (compareHeader.includes("notes")) {
        return <Folder className={`${classNames}`} />;
    }

    if (compareHeader.includes("read")) {
        return <Book className={`${classNames}`} />;
    }

    if (compareHeader.includes("search")) {
        return <MagnifyingGlass className={`${classNames}`} />;
    }

    if (
        compareHeader.includes("summary") ||
        compareHeader.includes("summarize") ||
        compareHeader.includes("enhanc")
    ) {
        return <Aperture className={`${classNames}`} />;
    }

    if (compareHeader.includes("diagram")) {
        return <Shapes className={`${classNames}`} />;
    }

    if (compareHeader.includes("paint")) {
        return <Palette className={`${classNames}`} />;
    }

    if (compareHeader.includes("code")) {
        return <Code className={`${classNames}`} />;
    }

    return <Brain className={`${classNames}`} />;
}

export function TrainOfThought(props: TrainOfThoughtProps) {
    // The train of thought comes in as a markdown-formatted string. It starts with a heading delimited by two asterisks at the start and end and a colon, followed by the message. Example: **header**: status. This function will parse the message and render it as a div.
    let extractedHeader = props.message.match(/\*\*(.*)\*\*/);
    let header = extractedHeader ? extractedHeader[1] : "";
    const iconColor = props.primary ? convertColorToTextClass(props.agentColor) : "text-gray-500";
    const icon = chooseIconFromHeader(header, iconColor);
    let markdownRendered = DOMPurify.sanitize(md.render(props.message));

    // Remove any header tags from markdownRendered
    markdownRendered = markdownRendered.replace(/<h[1-6].*?<\/h[1-6]>/g, "");
    return (
        <div
            className={`${styles.trainOfThoughtElement} break-words items-center ${props.primary ? "text-gray-400" : "text-gray-300"} ${styles.trainOfThought} ${props.primary ? styles.primary : ""}`}
        >
            {icon}
            <div dangerouslySetInnerHTML={{ __html: markdownRendered }} className="break-words" />
        </div>
    );
}

const ChatMessage = forwardRef<HTMLDivElement, ChatMessageProps>((props, ref) => {
    const [copySuccess, setCopySuccess] = useState<boolean>(false);
    const [isHovering, setIsHovering] = useState<boolean>(false);
    const [textRendered, setTextRendered] = useState<string>("");
    const [markdownRendered, setMarkdownRendered] = useState<string>("");
    const [isPlaying, setIsPlaying] = useState<boolean>(false);
    const [interrupted, setInterrupted] = useState<boolean>(false);
    const [excalidrawData, setExcalidrawData] = useState<string>("");
    const [mermaidjsData, setMermaidjsData] = useState<string>("");

    const interruptedRef = useRef<boolean>(false);
    const messageRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        interruptedRef.current = interrupted;
    }, [interrupted]);

    useEffect(() => {
        const observer = new MutationObserver((mutationsList, observer) => {
            // If the addedNodes property has one or more nodes
            if (messageRef.current) {
                for (let mutation of mutationsList) {
                    if (mutation.type === "childList" && mutation.addedNodes.length > 0) {
                        // Call your function here
                        renderMathInElement(messageRef.current, {
                            delimiters: [
                                { left: "$$", right: "$$", display: true },
                                { left: "\\[", right: "\\]", display: true },
                                { left: "\\(", right: "\\)", display: false },
                            ],
                        });
                    }
                }
            }
        });

        if (messageRef.current) {
            observer.observe(messageRef.current, { childList: true });
        }

        // Clean up the observer on component unmount
        return () => observer.disconnect();
    }, [messageRef.current]);

    useEffect(() => {
        // Prepare initial message for rendering
        let message = props.chatMessage.message;

        if (props.chatMessage.excalidrawDiagram) {
            setExcalidrawData(props.chatMessage.excalidrawDiagram);
        }

        if (props.chatMessage.mermaidjsDiagram) {
            setMermaidjsData(props.chatMessage.mermaidjsDiagram);
        }

        // Replace LaTeX delimiters with placeholders
        message = message
            .replace(/\\\(/g, "LEFTPAREN")
            .replace(/\\\)/g, "RIGHTPAREN")
            .replace(/\\\[/g, "LEFTBRACKET")
            .replace(/\\\]/g, "RIGHTBRACKET");

        // Replace file links with base64 data
        message = renderCodeGenImageInline(message, props.chatMessage.codeContext);

        // Add code context files to the message
        if (props.chatMessage.codeContext) {
            Object.entries(props.chatMessage.codeContext).forEach(([key, value]) => {
                value.results.output_files?.forEach((file) => {
                    if (file.filename.endsWith(".png") || file.filename.endsWith(".jpg")) {
                        // Don't add the image again if it's already in the message!
                        if (!message.includes(`![${file.filename}](`)) {
                            message += `\n\n![${file.filename}](data:image/png;base64,${file.b64_data})`;
                        }
                    }
                });
            });
        }

        // Handle user attached images rendering
        let messageForClipboard = message;
        let messageToRender = message;
        if (props.chatMessage.images && props.chatMessage.images.length > 0) {
            const sanitizedImages = props.chatMessage.images.map((image) => {
                const decodedImage = image.startsWith("data%3Aimage")
                    ? decodeURIComponent(image)
                    : image;
                return DOMPurify.sanitize(decodedImage);
            });
            const imagesInMd = sanitizedImages
                .map((sanitizedImage, index) => {
                    return `![uploaded image ${index + 1}](${sanitizedImage})`;
                })
                .join("\n");
            const imagesInHtml = sanitizedImages
                .map((sanitizedImage, index) => {
                    return `<div class="${styles.imageWrapper}"><img src="${sanitizedImage}" alt="uploaded image ${index + 1}" /></div>`;
                })
                .join("");
            const userImagesInHtml = `<div class="${styles.imagesContainer}">${imagesInHtml}</div>`;
            messageForClipboard = `${imagesInMd}\n\n${messageForClipboard}`;
            messageToRender = `${userImagesInHtml}${messageToRender}`;
        }

        // Set the message text
        setTextRendered(messageForClipboard);

        // Render the markdown
        let markdownRendered = md.render(messageToRender);

        // Replace placeholders with LaTeX delimiters
        markdownRendered = markdownRendered
            .replace(/LEFTPAREN/g, "\\(")
            .replace(/RIGHTPAREN/g, "\\)")
            .replace(/LEFTBRACKET/g, "\\[")
            .replace(/RIGHTBRACKET/g, "\\]");

        // Sanitize and set the rendered markdown
        setMarkdownRendered(DOMPurify.sanitize(markdownRendered));
    }, [props.chatMessage.message, props.chatMessage.images, props.chatMessage.intent]);

    useEffect(() => {
        if (copySuccess) {
            setTimeout(() => {
                setCopySuccess(false);
            }, 2000);
        }
    }, [copySuccess]);

    useEffect(() => {
        if (messageRef.current) {
            const preElements = messageRef.current.querySelectorAll("pre > .hljs");
            preElements.forEach((preElement) => {
                if (!preElement.querySelector(`${styles.codeCopyButton}`)) {
                    const copyButton = document.createElement("button");
                    const copyIcon = <ClipboardText size={24} />;
                    createRoot(copyButton).render(copyIcon);

                    copyButton.className = `hljs ${styles.codeCopyButton}`;
                    copyButton.addEventListener("click", () => {
                        let textContent = preElement.textContent || "";
                        // Strip any leading $ characters
                        textContent = textContent.replace(/^\$+/, "");
                        // Remove 'Copy' if it's at the start of the string
                        textContent = textContent.replace(/^Copy/, "");
                        textContent = textContent.trim();
                        navigator.clipboard.writeText(textContent);

                        // Replace the copy icon with a checkmark
                        const copiedIcon = <Check size={24} />;
                        createRoot(copyButton).render(copiedIcon);
                    });
                    preElement.prepend(copyButton);
                }
            });

            renderMathInElement(messageRef.current, {
                delimiters: [
                    { left: "$$", right: "$$", display: true },
                    { left: "\\[", right: "\\]", display: true },
                    { left: "\\(", right: "\\)", display: false },
                ],
            });
        }
    }, [markdownRendered, isHovering, messageRef]);

    function formatDate(timestamp: string) {
        // Format date in HH:MM, DD MMM YYYY format
        let date = new Date(timestamp + "Z");
        let time_string = date
            .toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: true })
            .toUpperCase();
        let date_string = date
            .toLocaleString("en-US", { year: "numeric", month: "short", day: "2-digit" })
            .replaceAll("-", " ");
        return `${time_string} on ${date_string}`;
    }

    function renderTimeStamp(timestamp: string) {
        if (!timestamp.endsWith("Z")) {
            timestamp = timestamp + "Z";
        }
        const messageDateTime = new Date(timestamp);
        const currentDateTime = new Date();
        const timeDiff = currentDateTime.getTime() - messageDateTime.getTime();

        if (timeDiff < 60e3) {
            return "Just now";
        }

        if (timeDiff < 3600e3) {
            // Using Math.round for closer to actual time representation
            return `${Math.round(timeDiff / 60e3)}m ago`;
        }

        if (timeDiff < 86400e3) {
            return `${Math.round(timeDiff / 3600e3)}h ago`;
        }

        return `${Math.round(timeDiff / 86400e3)}d ago`;
    }

    function constructClasses(chatMessage: SingleChatMessage) {
        let classes = [styles.chatMessageContainer, "shadow-md"];
        classes.push(styles[chatMessage.by]);
        if (!chatMessage.message) {
            classes.push(styles.emptyChatMessage);
        }

        if (props.customClassName) {
            classes.push(styles[`${chatMessage.by}${props.customClassName}`]);
        }

        return classes.join(" ");
    }

    function chatMessageWrapperClasses(chatMessage: SingleChatMessage) {
        let classes = [styles.chatMessageWrapper];
        classes.push(styles[chatMessage.by]);
        if (chatMessage.by === "khoj") {
            classes.push(
                `border-l-4 border-opacity-50 ${"border-l-" + props.borderLeftColor || "border-l-orange-400"}`,
            );
        }
        return classes.join(" ");
    }

    async function playTextToSpeech() {
        // Browser native speech API
        // const utterance = new SpeechSynthesisUtterance(props.chatMessage.message);
        // speechSynthesis.speak(utterance);

        // Using the Khoj speech API
        // Break the message up into chunks of sentences
        const sentenceRegex = /[^.!?]+[.!?]*/g;
        const chunks = props.chatMessage.message.match(sentenceRegex) || [];

        if (!chunks || chunks.length === 0 || !chunks[0]) return;

        setIsPlaying(true);

        let nextBlobPromise = fetchBlob(chunks[0]);

        for (let i = 0; i < chunks.length; i++) {
            if (interruptedRef.current) {
                break; // Exit the loop if interrupted
            }

            const currentBlobPromise = nextBlobPromise;
            if (i < chunks.length - 1) {
                nextBlobPromise = fetchBlob(chunks[i + 1]);
            }

            try {
                const blob = await currentBlobPromise;
                const url = URL.createObjectURL(blob);
                await playAudio(url);
            } catch (error) {
                console.error("Error:", error);
                break; // Exit the loop on error
            }
        }

        setIsPlaying(false);
        setInterrupted(false); // Reset interrupted state after playback
    }

    async function fetchBlob(text: string) {
        const response = await fetch(`/api/chat/speech?text=${encodeURIComponent(text)}`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
        });

        if (!response.ok) {
            throw new Error("Network response was not ok");
        }

        return await response.blob();
    }

    function playAudio(url: string) {
        return new Promise((resolve, reject) => {
            const audio = new Audio(url);
            audio.onended = resolve;
            audio.onerror = reject;
            audio.play();
        });
    }

    const deleteMessage = async (message: SingleChatMessage) => {
        const turnId = message.turnId || props.turnId;
        const response = await fetch("/api/chat/conversation/message", {
            method: "DELETE",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                conversation_id: props.conversationId,
                turn_id: turnId,
            }),
        });

        if (response.ok) {
            // Update the UI after successful deletion
            props.onDeleteMessage(turnId);
        } else {
            console.error("Failed to delete message");
        }
    };

    const allReferences = constructAllReferences(
        props.chatMessage.context,
        props.chatMessage.onlineContext,
        props.chatMessage.codeContext,
    );

    return (
        <div
            ref={ref}
            className={constructClasses(props.chatMessage)}
            onMouseLeave={(event) => setIsHovering(false)}
            onMouseEnter={(event) => setIsHovering(true)}
        >
            <div className={chatMessageWrapperClasses(props.chatMessage)}>
                {props.chatMessage.queryFiles && props.chatMessage.queryFiles.length > 0 && (
                    <div className="flex flex-wrap flex-col mb-2 max-w-full">
                        {props.chatMessage.queryFiles.map((file, index) => (
                            <Dialog key={index}>
                                <DialogTrigger asChild>
                                    <div
                                        className="flex items-center space-x-2 cursor-pointer bg-gray-500 bg-opacity-25 rounded-lg p-2 w-full
                                "
                                    >
                                        <div className="flex-shrink-0">
                                            {getIconFromFilename(file.file_type)}
                                        </div>
                                        <span className="truncate flex-1 min-w-0 max-w-[200px]">
                                            {file.name}
                                        </span>
                                        {file.size && (
                                            <span className="text-gray-400 flex-shrink-0">
                                                ({convertBytesToText(file.size)})
                                            </span>
                                        )}
                                    </div>
                                </DialogTrigger>
                                <DialogContent>
                                    <DialogHeader>
                                        <DialogTitle>
                                            <div className="truncate min-w-0 break-words break-all text-wrap max-w-full whitespace-normal">
                                                {file.name}
                                            </div>
                                        </DialogTitle>
                                    </DialogHeader>
                                    <DialogDescription>
                                        <ScrollArea className="h-72 w-full rounded-md break-words break-all text-wrap">
                                            {file.content}
                                        </ScrollArea>
                                    </DialogDescription>
                                </DialogContent>
                            </Dialog>
                        ))}
                    </div>
                )}
                <div
                    ref={messageRef}
                    className={styles.chatMessage}
                    dangerouslySetInnerHTML={{ __html: markdownRendered }}
                />
                {excalidrawData && <ExcalidrawComponent data={excalidrawData} />}
                {mermaidjsData && <Mermaid chart={mermaidjsData} />}
            </div>
            <div className={styles.teaserReferencesContainer}>
                <TeaserReferencesSection
                    isMobileWidth={props.isMobileWidth}
                    notesReferenceCardData={allReferences.notesReferenceCardData}
                    onlineReferenceCardData={allReferences.onlineReferenceCardData}
                    codeReferenceCardData={allReferences.codeReferenceCardData}
                />
            </div>
            <div className={styles.chatFooter}>
                {(isHovering || props.isMobileWidth || props.isLastMessage || isPlaying) && (
                    <>
                        <div
                            title={formatDate(props.chatMessage.created)}
                            className={`text-gray-400 relative top-0 left-4`}
                        >
                            {renderTimeStamp(props.chatMessage.created)}
                        </div>
                        <div className={`${styles.chatButtons} shadow-sm`}>
                            {props.chatMessage.by === "khoj" &&
                                (isPlaying ? (
                                    interrupted ? (
                                        <InlineLoading iconClassName="p-0" className="m-0" />
                                    ) : (
                                        <button
                                            title="Pause Speech"
                                            onClick={(event) => setInterrupted(true)}
                                        >
                                            <Pause
                                                alt="Pause Message"
                                                className="hsl(var(--muted-foreground))"
                                            />
                                        </button>
                                    )
                                ) : (
                                    <button title="Speak" onClick={(event) => playTextToSpeech()}>
                                        <SpeakerHigh
                                            alt="Speak Message"
                                            className="hsl(var(--muted-foreground)) hover:text-green-500"
                                        />
                                    </button>
                                ))}
                            {props.chatMessage.turnId && (
                                <button
                                    title="Delete"
                                    className={`${styles.deleteButton}`}
                                    onClick={() => deleteMessage(props.chatMessage)}
                                >
                                    <Trash
                                        alt="Delete Message"
                                        className="hsl(var(--muted-foreground)) hover:text-red-500"
                                    />
                                </button>
                            )}
                            <button
                                title="Copy"
                                className={`${styles.copyButton}`}
                                onClick={() => {
                                    navigator.clipboard.writeText(textRendered);
                                    setCopySuccess(true);
                                }}
                            >
                                {copySuccess ? (
                                    <Copy
                                        alt="Copied Message"
                                        weight="fill"
                                        className="text-green-500"
                                    />
                                ) : (
                                    <Copy
                                        alt="Copy Message"
                                        className="hsl(var(--muted-foreground)) hover:text-green-500"
                                    />
                                )}
                            </button>
                            {props.chatMessage.by === "khoj" &&
                                (props.chatMessage.intent ? (
                                    <FeedbackButtons
                                        uquery={props.chatMessage.intent.query}
                                        kquery={props.chatMessage.message}
                                    />
                                ) : (
                                    <FeedbackButtons
                                        uquery={
                                            props.chatMessage.rawQuery || props.chatMessage.message
                                        }
                                        kquery={props.chatMessage.message}
                                    />
                                ))}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
});

ChatMessage.displayName = "ChatMessage";

export default ChatMessage;
