'use client'
import './globals.css';

import styles from './page.module.css';
import React, { Suspense, useEffect, useState, useMemo } from 'react';

import SuggestionCard from './components/suggestions/suggestionCard';
import SidePanel from './components/sidePanel/chatHistorySidePanel';
import NavMenu from './components/navMenu/navMenu';
import Loading from './components/loading/loading';
import useSWR from 'swr';
import Image from 'next/image';

import 'katex/dist/katex.min.css';

import { StreamMessage } from './components/chatMessage/chatMessage';
import ChatInputArea, { ChatOptions } from './components/chatInputArea/chatInputArea';
import { useAuthenticatedData } from './common/auth';

//samples for suggestion cards (should be moved to json later)
const suggestions: Suggestion[] = [["Automation", "blue", "Send me a summary of HackerNews every morning.", "/automations?subject=Summarizing%20Top%20Headlines%20from%20HackerNews&query=Summarize%20the%20top%20headlines%20on%20HackerNews&crontime=00%207%20*%20*%20*"], ["Automation", "blue", "Compose a bedtime story that a five-year-old might enjoy.", "/automations?subject=Daily%20Bedtime%20Story&query=Compose%20a%20bedtime%20story%20that%20a%20five-year-old%20might%20enjoy.%20It%20should%20not%20exceed%20five%20paragraphs.%20Appeal%20to%20the%20imagination%2C%20but%20weave%20in%20learnings.&crontime=0%2021%20*%20*%20*"], ["Paint", "green", "Paint a picture of a sunset but it's made of stained glass tiles", ""], ["Online Search", "yellow", "Search for the best attractions in Austria Hungary", ""]];

import {
    Lightbulb,
    Robot,
    Aperture,
    GraduationCap,
    Jeep,
    Island,
    MathOperations,
    Asclepius,
    Couch,
    Code,
    Atom,
    ClockCounterClockwise,
    PaperPlaneTilt,
    Info,
    UserCircle,
    Globe,
    Palette,
    LinkBreak,
} from "@phosphor-icons/react";
import Chat from './page';

interface IconMap {
    [key: string]: (color: string, width: string, height: string) => JSX.Element | null;
}

const iconMap: IconMap = {
    Lightbulb: (color: string, width: string, height: string) => <Lightbulb className={`${width} ${height} ${color} mr-2`} />,
    Robot: (color: string, width: string, height: string) => <Robot className={`${width} ${height} ${color} mr-2`} />,
    Aperture: (color: string, width: string, height: string) => <Aperture className={`${width} ${height} ${color} mr-2`} />,
    GraduationCap: (color: string, width: string, height: string) => <GraduationCap className={`${width} ${height} ${color} mr-2`} />,
    Jeep: (color: string, width: string, height: string) => <Jeep className={`${width} ${height} ${color} mr-2`} />,
    Island: (color: string, width: string, height: string) => <Island className={`${width} ${height} ${color} mr-2`} />,
    MathOperations: (color: string, width: string, height: string) => <MathOperations className={`${width} ${height} ${color} mr-2`} />,
    Asclepius: (color: string, width: string, height: string) => <Asclepius className={`${width} ${height} ${color} mr-2`} />,
    Couch: (color: string, width: string, height: string) => <Couch className={`${width} ${height} ${color} mr-2`} />,
    Code: (color: string, width: string, height: string) => <Code className={`${width} ${height} ${color} mr-2`} />,
    Atom: (color: string, width: string, height: string) => <Atom className={`${width} ${height} ${color} mr-2`} />,
    ClockCounterClockwise: (color: string, width: string, height: string) => <ClockCounterClockwise className={`${width} ${height} ${color} mr-2`} />,
    Globe: (color: string, width: string, height: string) => <Globe className={`${width} ${height} ${color} mr-2`} />,
    Palette: (color: string, width: string, height: string) => <Palette className={`${width} ${height} ${color} mr-2`} />,
};

function convertColorToTextClass(color: string) {
    if (color === 'red') return `text-red-500`;
    if (color === 'yellow') return `text-yellow-500`;
    if (color === 'green') return `text-green-500`;
    if (color === 'blue') return `text-blue-500`;
    if (color === 'orange') return `text-orange-500`;
    if (color === 'purple') return `text-purple-500`;
    if (color === 'pink') return `text-pink-500`;
    if (color === 'teal') return `text-teal-500`;
    if (color === 'cyan') return `text-cyan-500`;
    if (color === 'lime') return `text-lime-500`;
    if (color === 'indigo') return `text-indigo-500`;
    if (color === 'fuschia') return `text-fuschia-500`;
    if (color === 'rose') return `text-rose-500`;
    if (color === 'sky') return `text-sky-500`;
    if (color === 'amber') return `text-amber-500`;
    if (color === 'emerald') return `text-emerald-500`;
    return `text-gray-500`;
}

function convertSuggestionColorToTextClass(color: string) {
    console.log(color);
    const colors = ['blue', 'yellow', 'green', 'pink', 'purple'];
    if (colors.includes(color)) {
        return ""+`bg-gradient-to-b from-[hsl(var(--background))] to-${color}-100/${color=="green" ? "90" : "70"} dark:from-[hsl(var(--background))] dark:to-${color}-950/30 dark:border dark:border-neutral-700`;
    }
    return `bg-gradient-to-b from-white to-orange-50`;
}

function getIconFromIconName(iconName: string, color: string = 'gray', width: string = 'w-6', height: string = 'h-6') {
    const icon = iconMap[iconName];
    const colorName = color.toLowerCase();
    const colorClass = convertColorToTextClass(colorName);

    return icon ? icon(colorClass, width, height) : null;
}

export interface AgentData {
    slug: string;
    avatar: string;
    name: string;
    personality: string;
    color: string;
    icon: string;
}

interface ChatBodyDataProps {
    chatOptionsData: ChatOptions | null;
    setTitle: (title: string) => void;
    onConversationIdChange?: (conversationId: string) => void;
    setQueryToProcess: (query: string) => void;
    streamedMessages: StreamMessage[];
    setUploadedFiles: (files: string[]) => void;
    isMobileWidth?: boolean;
    isLoggedIn: boolean;
    conversationId: string | null; // Added this line
}
type Suggestion = [string, string, string, string];

async function createNewConvo(slug: string) {
    try {
      const response = await fetch(`/api/chat/sessions?client=web&agent_slug=${slug}`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      const conversationID = data.conversation_id;
      if (!conversationID) {
        throw new Error("Conversation ID not found in response");
      }
      return conversationID;
    } catch (error) {
      console.error("Error creating new conversation:", error);
      throw error;
    }
  }

function ChatBodyData(props: ChatBodyDataProps) {
    const [message, setMessage] = useState('');
    const [processingMessage, setProcessingMessage] = useState(false);
    const [shuffledOptions, setShuffledOptions] = useState<Suggestion[]>([]);
    const [shuffledColors, setShuffledColors] = useState<string[]>([]);
    const [selectedAgent, setSelectedAgent] = useState<string | null>("khoj");

    const agentsFetcher = () => window.fetch('/api/agents').then(res => res.json()).catch(err => console.log(err));
    const { data, error } = useSWR<AgentData[]>('agents', agentsFetcher, { revalidateOnFocus: false });

    function shuffleAndSetOptions() {
        const shuffled = [...suggestions].sort(() => 0.5 - Math.random());
        setShuffledOptions(shuffled.slice(0, 3));
        //use the text to color function above convertSuggestionColorToTextClass
        const colors = shuffled.map(option => convertSuggestionColorToTextClass(option[1]));
        setShuffledColors(colors);
    }

    useEffect(() => {
        if (props.chatOptionsData) {
            shuffleAndSetOptions();
        }
    }, [props.chatOptionsData]);

    function onButtonClick() {
        shuffleAndSetOptions();
    }

    useEffect(() => { const processMessage = async () => {
        if (message && !processingMessage) {
            setProcessingMessage(true);
            try {
                const newConversationId = await createNewConvo(selectedAgent || "khoj");
                props.onConversationIdChange?.(newConversationId);
                window.location.href = `/chat?conversationId=${newConversationId}`;
                localStorage.setItem('message', message);
            }
            catch (error) {
                console.error("Error creating new conversation:", error);
                setProcessingMessage(false);
            }
            setMessage('');
        }
        };
        processMessage();
        if(message){
            setProcessingMessage(true);
            props.setQueryToProcess(message);
        };
    }, [selectedAgent, message]);

    useEffect(() => {
        if (props.streamedMessages &&
            props.streamedMessages.length > 0 &&
            props.streamedMessages[props.streamedMessages.length - 1].completed) {
            setProcessingMessage(false);
        } else {
            setMessage('');
        }
    }, [props.streamedMessages]);

    const agents = data ? data.slice(0, 4) : []; //select first 4 agents to show as options
    //generate colored icons for the selected agents
    const icons = agents.map(agent => getIconFromIconName(agent.icon, agent.color) || <Image src={agent.avatar} alt={agent.name} width={50} height={50} />);
    function fillArea(link: string, type: string, prompt: string) {
        if (!link) {
            let message_str = "";
            prompt = prompt.charAt(0).toLowerCase() + prompt.slice(1);

            if (type === "Online Search") {
                message_str = "/online " + prompt;
            } else if (type === "Paint") {
                message_str = "/paint " + prompt;
            } else {
                message_str = prompt;
            }

            // Get the textarea element
            const message_area = document.getElementById("message") as HTMLTextAreaElement;

            if (message_area) {
                // Update the value directly
                message_area.value = message_str;
                setMessage(message_str);
            }
        }
    }
    function handleAgentsClick(slug: string) {
        return async () => {
            setSelectedAgent(slug);
            try {
                const unauthenticatedRedirectUrl = `/login?next=/agents?agent=${slug}`;
                const response = await fetch(`/api/chat/sessions?agent_slug=${slug}`, { method: "POST" });
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                if (response.status == 200) {;
                    highlightHandler(slug);
                } else if (response.status == 403 || response.status == 401) {
                    window.location.href = unauthenticatedRedirectUrl;
                } else {
                    alert("Failed to start chat session");
                }
            } catch (error) {
                console.error("Error starting a conversation with the agent:", error);
            }
        };
    }

    const colorMap: Record<string, string> = {
        'red': 'border-red-500',
        'blue': 'border-blue-500',
        'green': 'border-green-500',
        'yellow': 'border-yellow-500',
        'purple': 'border-purple-500',
        'pink': 'border-pink-500',
        'indigo': 'border-indigo-500',
        'gray': 'border-gray-500',
        'orange': 'border-orange-500',
    };

    function getTailwindBorderClass(color: string): string {
        return colorMap[color] || 'border-black'; // Default to black if color not found
    }

    function highlightHandler(slug: string): void {
        const buttons = document.getElementsByClassName("agent");
        const agent = agents.find(agent => agent.slug === slug);
        const borderColorClass = getTailwindBorderClass(agent?.color || 'gray');

        Array.from(buttons).forEach((button: Element) => {
          const buttonElement = button as HTMLElement;
          if (buttonElement.classList.contains(slug)) {
            buttonElement.classList.add(borderColorClass, 'border');
            buttonElement.classList.remove('border-stone-100', 'dark:border-neutral-700');
          }
          else {
            Object.values(colorMap).forEach(colorClass => {
                buttonElement.classList.remove(colorClass, 'border');
            });
            buttonElement.classList.add('border', 'border-stone-100', 'dark:border-neutral-700');
          }
        });
    }

    return (
        <div>
        <div className="w-full text-center">
        <div className="items-center">
            <h1 className="text-center pb-6">What would you like to do?</h1>
        </div>
        <div className="flex pb-6 ms-10 gap-2">
            {icons.map((icon, index) => (
                <a className={`agent ${agents[index].slug} no-underline w-200 flex pl-4 pt-3 pb-3 border rounded-xl ${agents[index].slug === "khoj" ? "border-orange-500" : "border-stone-100 dark:border-neutral-700"} rounded-md shadow-sm`} onClick={handleAgentsClick(agents[index].slug)}>
                {icon}
                <p className="pr-4">{agents[index].name}</p>
                </a>
            ))}
            <a className="no-underline w-200 flex pl-3 pt-1 pb-1 ps-4" href="/agents">
                <p className="relative top-1 hover:underline">See More →</p>
            </a>
        </div>
        </div>
        <div className="w-fit">
            <div className={`${styles.inputBox} bg-background align-middle items-center justify-center px-3 dark:bg-neutral-700 dark:border-0 dark:shadow-none`}>
                <ChatInputArea
                    isLoggedIn={props.isLoggedIn}
                    sendMessage={(message) => setMessage(message)}
                    sendDisabled={processingMessage}
                    chatOptionsData={props.chatOptionsData}
                    conversationId={null}
                    isMobileWidth={props.isMobileWidth}
                    setUploadedFiles={props.setUploadedFiles} />
            </div>
            <div className={`suggestions ${styles.suggestions} w-full flex`}>
                {shuffledOptions.map(([key, styleClass, value, link], index) => (
                    <div onClick={() => fillArea(link, key, value)}>
                        <SuggestionCard
                        key={key + Math.random()}
                        title={key}
                        body={value.length > 65 ? value.substring(0, 65) + '...' : value}
                        link={link}
                        color={shuffledColors[index]}
                        image={shuffledColors[index]}
                        />
                    </div>
                ))}
            </div>
            <div className="flex items-center justify-center">
                <button onClick={onButtonClick} className="m-2 p-1 rounded-lg dark:hover:bg-[var(--background-color)] hover:bg-stone-100 border border-stone-100 text-sm text-stone-500 dark:text-stone-300 dark:border-neutral-700">More Examples ⟳</button>
            </div>
        </div>
        </div>
    );
}

export default function Home(){
    const [chatOptionsData, setChatOptionsData] = useState<ChatOptions | null>(null);
    const [isLoading, setLoading] = useState(true);
    const [title, setTitle] = useState('');
    const [conversationId, setConversationID] = useState<string | null>(null);
    const [messages, setMessages] = useState<StreamMessage[]>([]);
    const [queryToProcess, setQueryToProcess] = useState<string>('');
    const [uploadedFiles, setUploadedFiles] = useState<string[]>([]);
    const [isMobileWidth, setIsMobileWidth] = useState(false);

    const authenticatedData = useAuthenticatedData();

    const handleConversationIdChange = (newConversationId: string) => {
        setConversationID(newConversationId);
    };

    useEffect(() => {
        fetch('/api/chat/options')
            .then(response => response.json())
            .then((data: ChatOptions) => {
                setLoading(false);
                if (data) {
                    setChatOptionsData(data);
                }
            })
            .catch(err => {
                console.error(err);
                return;
            });

        setIsMobileWidth(window.innerWidth < 786);

        window.addEventListener('resize', () => {
            setIsMobileWidth(window.innerWidth < 786);
        });

    }, []);

    if (isLoading) {
        return <Loading />;
    }

    return (
        <div className={`${styles.main} ${styles.chatLayout}`}>
            <title>
                {title}
            </title>
            <div className={`${styles.sidePanel}`}>
                <SidePanel
                    webSocketConnected={true}
                    conversationId={conversationId}
                    uploadedFiles={uploadedFiles}
                    isMobileWidth={isMobileWidth}
                />
            </div>
            <div className={`${styles.chatBox}`}>
                <NavMenu selected="Chat" title={title}></NavMenu>
                <div className={`${styles.chatBoxBody} flex flex-col justify-center fixed top-3/4 left-1/2 transform -translate-x-1/2 -translate-y-1/2`}>
                    <ChatBodyData
                        isLoggedIn={authenticatedData !== null}
                        streamedMessages={messages}
                        chatOptionsData={chatOptionsData}
                        setTitle={setTitle}
                        setQueryToProcess={setQueryToProcess}
                        setUploadedFiles={setUploadedFiles}
                        isMobileWidth={isMobileWidth}
                        onConversationIdChange={handleConversationIdChange}
                        conversationId={conversationId}
                    />
                </div>
            </div>
        </div>
    );
}
