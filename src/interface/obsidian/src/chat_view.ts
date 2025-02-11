import { ItemView, MarkdownRenderer, Scope, WorkspaceLeaf, request, requestUrl, setIcon, Platform, TFile } from 'obsidian';
import * as DOMPurify from 'dompurify';
import { KhojSetting } from 'src/settings';
import { KhojPaneView } from 'src/pane_view';
import { KhojView, createCopyParentText, getLinkToEntry, pasteTextAtCursor } from 'src/utils';
import { KhojSearchModal } from './search_modal';

export interface ChatJsonResult {
    image?: string;
    detail?: string;
    intentType?: string;
    inferredQueries?: string[];
}

interface ChunkResult {
    objects: string[];
    remainder: string;
}

interface MessageChunk {
    type: string;
    data: any;
}

interface ChatMessageState {
    newResponseTextEl: HTMLElement | null;
    newResponseEl: HTMLElement | null;
    loadingEllipsis: HTMLElement | null;
    references: any;
    generatedAssets: string;
    rawResponse: string;
    rawQuery: string;
    isVoice: boolean;
    turnId: string;
    editBlocks?: EditBlock[];  // Add this line
}

interface EditBlock {
    noteTitle?: string;  // Make noteTitle optional
    before: string;
    after: string;
    replacement: string;
}

interface Location {
    region?: string;
    city?: string;
    countryName?: string;
    countryCode?: string;
    timezone: string;
}

interface RenderMessageOptions {
    chatBodyEl: Element;
    message: string;
    sender: string;
    turnId?: string;
    dt?: Date;
    raw?: boolean;
    willReplace?: boolean;
    isSystemMessage?: boolean;
}

interface ChatMode {
    value: string;
    label: string;
    emoji: string;
    command: string;
}

interface Agent {
    name: string;
    slug: string;
    description: string;
}

export class KhojChatView extends KhojPaneView {
    result: string;
    setting: KhojSetting;
    waitingForLocation: boolean;
    location: Location = { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone };
    keyPressTimeout: NodeJS.Timeout | null = null;
    userMessages: string[] = [];  // Store user sent messages for input history cycling
    currentMessageIndex: number = -1;  // Track current message index in userMessages array
    private currentUserInput: string = ""; // Stores the current user input that is being typed in chat
    private startingMessage: string = "Message";
    chatMessageState: ChatMessageState;
    private agents: Agent[] = [];
    private currentAgent: string | null = null;
    private fileAccessMode: 'none' | 'read' | 'write' = 'none'; // Track the current file access mode
    private chatModes: ChatMode[] = [
        { value: "default", label: "Default", emoji: "🎯", command: "/default" },
        { value: "general", label: "General", emoji: "💭", command: "/general" },
        { value: "notes", label: "Notes", emoji: "📝", command: "/notes" },
        { value: "online", label: "Online", emoji: "🌐", command: "/online" },
        { value: "image", label: "Image", emoji: "🖼️", command: "/image" },
        { value: "research", label: "Research", emoji: "🔬", command: "/research" }
    ];

    constructor(leaf: WorkspaceLeaf, setting: KhojSetting) {
        super(leaf, setting);

        // Register chat view keybindings
        this.scope = new Scope(this.app.scope);
        this.scope.register(["Ctrl"], 'n', (_) => this.createNewConversation());
        this.scope.register(["Ctrl"], 'o', async (_) => await this.toggleChatSessions());
        this.scope.register(["Ctrl"], 'f', (_) => new KhojSearchModal(this.app, this.setting).open());
        this.scope.register(["Ctrl"], 'r', (_) => new KhojSearchModal(this.app, this.setting, true).open());

        this.waitingForLocation = true;

        fetch("https://ipapi.co/json")
            .then(response => response.json())
            .then(data => {
                this.location = {
                    region: data.region,
                    city: data.city,
                    countryName: data.country_name,
                    countryCode: data.country_code,
                    timezone: data.timezone,
                };
            })
            .catch(err => {
                console.log(err);
            })
            .finally(() => {
                this.waitingForLocation = false;
            });

    }

    getViewType(): string {
        return KhojView.CHAT;
    }

    getDisplayText(): string {
        return "Khoj Chat";
    }

    getIcon(): string {
        return "message-circle";
    }

    async chat(isVoice: boolean = false) {
        // Cancel any pending edits before sending a new message
        await this.cancelPendingEdits();

        // Get text in chat input element
        let input_el = <HTMLTextAreaElement>this.contentEl.getElementsByClassName("khoj-chat-input")[0];

        // Clear text after extracting message to send
        let user_message = input_el.value.trim();

        // Store the message in the array if it's not empty
        if (user_message) {
            // Get the selected mode
            const selectedMode = this.chatModes.find(mode =>
                this.contentEl.querySelector(`#khoj-mode-${mode.value}:checked`)
            );

            // Check if message starts with a mode command
            const modeMatch = this.chatModes.find(mode => user_message.startsWith(mode.command));

            let displayMessage = user_message;
            let apiMessage = user_message;

            if (modeMatch) {
                // If message starts with a mode command, replace it with the emoji in display
                displayMessage = user_message.replace(modeMatch.command, modeMatch.emoji);
            } else if (selectedMode) {
                // If no mode in message but mode selected, add the mode command for API
                displayMessage = `${selectedMode.emoji} ${user_message}`;
                apiMessage = `${selectedMode.command} ${user_message}`;
            }

            this.userMessages.push(user_message);
            // Update starting message after sending a new message
            const modifierKey = Platform.isMacOS ? '⌘' : '^';
            this.startingMessage = `(${modifierKey}+↑/↓) for prev messages`;
            input_el.placeholder = this.startingMessage;

            // Clear input and resize
            input_el.value = "";
            this.autoResize();

            // Get and render chat response to user message
            await this.getChatResponse(apiMessage, displayMessage, isVoice);
        }
    }

    async onOpen() {
        let { contentEl } = this;
        contentEl.addClass("khoj-chat");

        super.onOpen();

        // Fetch available agents
        await this.fetchAgents();

        // Construct Content Security Policy
        let defaultDomains = `'self' ${this.setting.khojUrl} https://*.obsidian.md https://app.khoj.dev https://assets.khoj.dev`;
        const defaultSrc = `default-src ${defaultDomains};`;
        const scriptSrc = `script-src ${defaultDomains} 'unsafe-inline';`;
        const connectSrc = `connect-src ${this.setting.khojUrl} wss://*.obsidian.md/ https://ipapi.co/json;`;
        const styleSrc = `style-src ${defaultDomains} 'unsafe-inline';`;
        const imgSrc = `img-src * app: data:;`;
        const childSrc = `child-src 'none';`;
        const objectSrc = `object-src 'none';`;
        const csp = `${defaultSrc} ${scriptSrc} ${connectSrc} ${styleSrc} ${imgSrc} ${childSrc} ${objectSrc}`;

        // WARNING: CSP DISABLED for now as it breaks other Obsidian plugins. Enable when can scope CSP to only Khoj plugin.
        // CSP meta tag for the Khoj Chat modal
        // document.head.createEl("meta", { attr: { "http-equiv": "Content-Security-Policy", "content": `${csp}` } });

        // Create area for chat logs
        let chatBodyEl = contentEl.createDiv({ attr: { id: "khoj-chat-body", class: "khoj-chat-body" } });

        // Add top control bar
        let topControlRow = contentEl.createDiv("khoj-top-control-row");

        // Create agent container
        let agentContainer = topControlRow.createDiv("khoj-agent-container");

        // Add agent selector
        let agentSelect = agentContainer.createEl("select", {
            attr: {
                class: "khoj-agent-select",
                title: "Select Agent"
            }
        });

        // Add default option
        let defaultOption = agentSelect.createEl("option", {
            text: "Default Agent",
            value: ""
        });

        // Add options for each agent
        this.agents.forEach(agent => {
            let option = agentSelect.createEl("option", {
                text: agent.name,
                value: agent.slug
            });
            if (agent.description) {
                option.title = agent.description;
            }
        });

        // Add New Chat button
        let newChatButton = topControlRow.createEl("button", {
            text: "New Chat",
            attr: {
                class: "khoj-new-chat-button",
                title: "Start New Chat with Selected Agent"
            }
        });
        setIcon(newChatButton, "plus-circle");
        newChatButton.addEventListener('click', async () => {
            const selectedAgent = (agentSelect as HTMLSelectElement).value;
            await this.createNewConversation(selectedAgent);
        });

        // Add hint message for agent change
        let agentHint = topControlRow.createEl("div", {
            attr: {
                class: "khoj-agent-hint",
            },
            text: "👈 Click this button to use the selected agent in a new chat !"
        });

        // Add event listener for agent selection change
        agentSelect.addEventListener('change', (event) => {
            const select = event.target as HTMLSelectElement;
            const selectedAgent = select.value;
            // Show hint if selected agent is different from current agent
            const shouldShowHint = Boolean(selectedAgent !== this.currentAgent && chatBodyEl.dataset.conversationId);
            agentHint.classList.toggle('visible', shouldShowHint);
        });

        // Add chat input field
        let inputRow = contentEl.createDiv("khoj-input-row");

        let chatSessions = inputRow.createEl("button", {
            text: "Chat Sessions",
            attr: {
                class: "khoj-input-row-button clickable-icon",
                title: "Show Conversations (^O)",
            },
        })
        chatSessions.addEventListener('click', async (_) => { await this.toggleChatSessions() });
        setIcon(chatSessions, "history");

        // Add file access mode button
        let fileAccessButton = inputRow.createEl("button", {
            text: "File Access",
            attr: {
                class: "khoj-input-row-button clickable-icon",
                title: "Toggle file access mode (No Access)",
            },
        });
        setIcon(fileAccessButton, "file-x");
        fileAccessButton.addEventListener('click', () => {
            // Cycle through modes: none -> read -> write -> none
            switch (this.fileAccessMode) {
                case 'none':
                    this.fileAccessMode = 'read';
                    setIcon(fileAccessButton, "file-search");
                    fileAccessButton.title = "Toggle file access mode (Read Only)";
                    break;
                case 'read':
                    this.fileAccessMode = 'write';
                    setIcon(fileAccessButton, "file-edit");
                    fileAccessButton.title = "Toggle file access mode (Read & Write)";
                    break;
                case 'write':
                    this.fileAccessMode = 'none';
                    setIcon(fileAccessButton, "file-x");
                    fileAccessButton.title = "Toggle file access mode (No Access)";
                    break;
            }
        });

        let chatInput = inputRow.createEl("textarea", {
            attr: {
                id: "khoj-chat-input",
                autofocus: "autofocus",
                class: "khoj-chat-input option",
            },
        })
        chatInput.addEventListener('input', (_) => { this.onChatInput() });
        chatInput.addEventListener('keydown', (event) => {
            this.incrementalChat(event);
            this.handleArrowKeys(event);
        });

        // Add event listeners for long press keybinding
        this.contentEl.addEventListener('keydown', this.handleKeyDown.bind(this));
        this.contentEl.addEventListener('keyup', this.handleKeyUp.bind(this));

        let transcribe = inputRow.createEl("button", {
            text: "Transcribe",
            attr: {
                id: "khoj-transcribe",
                class: "khoj-transcribe khoj-input-row-button clickable-icon ",
                title: "Start Voice Chat (^S)",
            },
        })
        transcribe.addEventListener('mousedown', (event) => { this.startSpeechToText(event) });
        transcribe.addEventListener('mouseup', async (event) => { await this.stopSpeechToText(event) });
        transcribe.addEventListener('touchstart', async (event) => { await this.speechToText(event) });
        transcribe.addEventListener('touchend', async (event) => { await this.speechToText(event) });
        transcribe.addEventListener('touchcancel', async (event) => { await this.speechToText(event) });
        setIcon(transcribe, "mic");

        let send = inputRow.createEl("button", {
            text: "Send",
            attr: {
                id: "khoj-chat-send",
                class: "khoj-chat-send khoj-input-row-button clickable-icon",
            },
        })
        setIcon(send, "arrow-up-circle");
        let sendImg = <SVGElement>send.getElementsByClassName("lucide-arrow-up-circle")[0]
        sendImg.addEventListener('click', async (_) => { await this.chat() });

        // After all the input row elements, add the mode selector
        let chatModeRow = contentEl.createDiv("khoj-mode-row");

        // Create radio buttons for each mode
        this.chatModes.forEach((mode) => {
            let modeContainer = chatModeRow.createDiv({ attr: { class: "khoj-mode-container" } });
            let modeInput = modeContainer.createEl("input", {
                attr: {
                    type: "radio",
                    id: `khoj-mode-${mode.value}`,
                    name: "khoj-mode",
                    value: mode.value,
                    class: "khoj-mode-input",
                    ...(mode.value === "default" && { checked: "checked" })
                }
            });
            let modeLabel = modeContainer.createEl("label", {
                text: `${mode.emoji} ${mode.label}`,
                attr: {
                    for: `khoj-mode-${mode.value}`,
                    class: "khoj-mode-label"
                }
            });
        });

        // Get chat history from Khoj backend and set chat input state
        let getChatHistorySucessfully = await this.getChatHistory(chatBodyEl);

        let placeholderText: string = getChatHistorySucessfully ? this.startingMessage : "Configure Khoj to enable chat";
        chatInput.placeholder = placeholderText;
        chatInput.disabled = !getChatHistorySucessfully;

        // Scroll to bottom of chat messages and focus on chat input field, once messages rendered
        requestAnimationFrame(() => {
            // Ensure layout and paint have occurred
            requestAnimationFrame(() => {
                this.scrollChatToBottom();
                const chatInput = <HTMLTextAreaElement>this.contentEl.getElementsByClassName("khoj-chat-input")[0];
                chatInput?.focus();
            });
        });
    }

    startSpeechToText(event: KeyboardEvent | MouseEvent | TouchEvent, timeout = 200) {
        if (!this.keyPressTimeout) {
            this.keyPressTimeout = setTimeout(async () => {
                // Reset auto send voice message timer, UI if running
                if (this.sendMessageTimeout) {
                    // Stop the auto send voice message countdown timer UI
                    clearTimeout(this.sendMessageTimeout);
                    const sendButton = <HTMLButtonElement>this.contentEl.getElementsByClassName("khoj-chat-send")[0]
                    setIcon(sendButton, "arrow-up-circle")
                    let sendImg = <SVGElement>sendButton.getElementsByClassName("lucide-arrow-up-circle")[0]
                    sendImg.addEventListener('click', async (_) => { await this.chat() });
                    // Reset chat input value
                    const chatInput = <HTMLTextAreaElement>this.contentEl.getElementsByClassName("khoj-chat-input")[0];
                    chatInput.value = "";
                }
                // Start new voice message
                await this.speechToText(event);
            }, timeout);
        }
    }
    async stopSpeechToText(event: KeyboardEvent | MouseEvent | TouchEvent) {
        if (this.mediaRecorder) {
            await this.speechToText(event);
        }
        if (this.keyPressTimeout) {
            clearTimeout(this.keyPressTimeout);
            this.keyPressTimeout = null;
        }
    }

    handleKeyDown(event: KeyboardEvent) {
        // Start speech to text if keyboard shortcut is pressed
        if (event.key === 's' && event.getModifierState('Control')) this.startSpeechToText(event);
    }

    async handleKeyUp(event: KeyboardEvent) {
        // Stop speech to text if keyboard shortcut is released
        if (event.key === 's' && event.getModifierState('Control')) await this.stopSpeechToText(event);
    }

    processOnlineReferences(referenceSection: HTMLElement, onlineContext: any) {
        let numOnlineReferences = 0;
        for (let subquery in onlineContext) {
            let onlineReference = onlineContext[subquery];
            if (onlineReference.organic && onlineReference.organic.length > 0) {
                numOnlineReferences += onlineReference.organic.length;
                for (let key in onlineReference.organic) {
                    let reference = onlineReference.organic[key];
                    let polishedReference = this.generateOnlineReference(referenceSection, reference, key);
                    referenceSection.appendChild(polishedReference);
                }
            }

            if (onlineReference.knowledgeGraph && onlineReference.knowledgeGraph.length > 0) {
                numOnlineReferences += onlineReference.knowledgeGraph.length;
                for (let key in onlineReference.knowledgeGraph) {
                    let reference = onlineReference.knowledgeGraph[key];
                    let polishedReference = this.generateOnlineReference(referenceSection, reference, key);
                    referenceSection.appendChild(polishedReference);
                }
            }

            if (onlineReference.peopleAlsoAsk && onlineReference.peopleAlsoAsk.length > 0) {
                numOnlineReferences += onlineReference.peopleAlsoAsk.length;
                for (let key in onlineReference.peopleAlsoAsk) {
                    let reference = onlineReference.peopleAlsoAsk[key];
                    let polishedReference = this.generateOnlineReference(referenceSection, reference, key);
                    referenceSection.appendChild(polishedReference);
                }
            }

            if (onlineReference.webpages && onlineReference.webpages.length > 0) {
                numOnlineReferences += onlineReference.webpages.length;
                for (let key in onlineReference.webpages) {
                    let reference = onlineReference.webpages[key];
                    let polishedReference = this.generateOnlineReference(referenceSection, reference, key);
                    referenceSection.appendChild(polishedReference);
                }
            }
        }

        return numOnlineReferences;
    }

    generateOnlineReference(messageEl: Element, reference: any, index: string) {
        // Generate HTML for Chat Reference
        let title = reference.title || reference.link;
        let link = reference.link;
        let snippet = reference.snippet;
        let question = reference.question ? `<b>Question:</b> ${reference.question}<br><br>` : "";

        let referenceButton = messageEl.createEl('button');
        let linkElement = referenceButton.createEl('a');
        linkElement.setAttribute('href', link);
        linkElement.setAttribute('target', '_blank');
        linkElement.setAttribute('rel', 'noopener noreferrer');
        linkElement.classList.add("reference-link");
        linkElement.setAttribute('title', title);
        linkElement.textContent = title;

        referenceButton.id = `ref-${index}`;
        referenceButton.classList.add("reference-button");
        referenceButton.classList.add("collapsed");
        referenceButton.tabIndex = 0;

        // Add event listener to toggle full reference on click
        referenceButton.addEventListener('click', function () {
            if (this.classList.contains("collapsed")) {
                this.classList.remove("collapsed");
                this.classList.add("expanded");
                this.innerHTML = linkElement.outerHTML + `<br><br>${question + snippet}`;
            } else {
                this.classList.add("collapsed");
                this.classList.remove("expanded");
                this.innerHTML = linkElement.outerHTML;
            }
        });

        return referenceButton;
    }

    generateReference(messageEl: Element, referenceJson: any, index: number | string) {
        let reference: string = referenceJson.hasOwnProperty("compiled") ? referenceJson.compiled : referenceJson;
        let referenceFile = referenceJson.hasOwnProperty("file") ? referenceJson.file : null;

        // Get all markdown and PDF files in vault
        const mdFiles = this.app.vault.getMarkdownFiles();
        const pdfFiles = this.app.vault.getFiles().filter(file => file.extension === 'pdf');

        // Escape reference for HTML rendering
        reference = reference.split('\n').slice(1).join('\n');
        let escaped_ref = reference.replace(/"/g, "&quot;")

        // Generate HTML for Chat Reference
        let referenceButton = messageEl.createEl('button');

        if (referenceFile) {
            // Find vault file associated with current reference
            const linkToEntry = getLinkToEntry(mdFiles.concat(pdfFiles), referenceFile, reference);

            const linkElement: Element = referenceButton.createEl('span');
            linkElement.setAttribute('title', escaped_ref);
            linkElement.textContent = referenceFile;
            if (linkElement && linkToEntry) {
                linkElement.classList.add("reference-link");
                linkElement.addEventListener('click', (event) => {
                    event.stopPropagation();
                    this.app.workspace.openLinkText(linkToEntry, '');
                });
            }
        }

        let referenceText = referenceButton.createDiv();
        referenceText.textContent = escaped_ref;

        referenceButton.id = `ref-${index}`;
        referenceButton.classList.add("reference-button");
        referenceButton.classList.add("collapsed");
        referenceButton.tabIndex = 0;

        // Add event listener to toggle full reference on click
        referenceButton.addEventListener('click', function () {
            if (this.classList.contains("collapsed")) {
                this.classList.remove("collapsed");
                this.classList.add("expanded");
            } else {
                this.classList.add("collapsed");
                this.classList.remove("expanded");
            }
        });

        return referenceButton;
    }

    textToSpeech(message: string, event: MouseEvent | null = null): void {
        // Replace the speaker with a loading icon.
        let loader = document.createElement("span");
        loader.classList.add("loader");

        let speechButton: HTMLButtonElement;
        let speechIcon: Element;

        if (event === null) {
            // Pick the last speech button if none is provided
            let speechButtons = document.getElementsByClassName("speech-button");
            speechButton = speechButtons[speechButtons.length - 1] as HTMLButtonElement;

            let speechIcons = document.getElementsByClassName("speech-icon");
            speechIcon = speechIcons[speechIcons.length - 1];
        } else {
            speechButton = event.currentTarget as HTMLButtonElement;
            speechIcon = event.target as Element;
        }

        speechButton.appendChild(loader);
        speechButton.disabled = true;

        const context = new AudioContext();
        let textToSpeechApi = `${this.setting.khojUrl}/api/chat/speech?text=${encodeURIComponent(message)}`;
        fetch(textToSpeechApi, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                "Authorization": `Bearer ${this.setting.khojApiKey}`,
            },
        })
            .then(response => response.arrayBuffer())
            .then(arrayBuffer => context.decodeAudioData(arrayBuffer))
            .then(audioBuffer => {
                const source = context.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(context.destination);
                source.start(0);
                source.onended = function () {
                    speechButton.removeChild(loader);
                    speechButton.disabled = false;
                };
            })
            .catch(err => {
                console.error("Error playing speech:", err);
                speechButton.removeChild(loader);
                speechButton.disabled = false; // Consider enabling the button again to allow retrying
            });
    }

    formatHTMLMessage(message: string, raw = false, willReplace = true) {
        // Remove any text between <s>[INST] and </s> tags. These are spurious instructions for some AI chat model.
        message = message.replace(/<s>\[INST\].+(<\/s>)?/g, '');

        // Transform khoj-edit blocks into accordions if not raw
        if (!raw) {
            message = this.transformKhojEditBlocks(message);
        }

        // Sanitize the markdown message
        message = DOMPurify.sanitize(message);

        // Convert the message to html, sanitize the message html and render it to the real DOM
        let chatMessageBodyTextEl = this.contentEl.createDiv();
        chatMessageBodyTextEl.innerHTML = this.markdownTextToSanitizedHtml(message, this);

        // Add a copy button to each chat message, if it doesn't already exist
        if (willReplace === true) {
            this.renderActionButtons(message, chatMessageBodyTextEl);
        }

        return chatMessageBodyTextEl;
    }

    markdownTextToSanitizedHtml(markdownText: string, component: ItemView): string {
        // Render markdown to an unlinked DOM element
        let virtualChatMessageBodyTextEl = document.createElement("div");

        // Convert the message to html
        MarkdownRenderer.render(this.app, markdownText, virtualChatMessageBodyTextEl, '', component);

        // Remove image HTML elements with any non whitelisted src prefix
        virtualChatMessageBodyTextEl.innerHTML = virtualChatMessageBodyTextEl.innerHTML.replace(
            /<img(?:(?!src=["'](app:|data:|https:\/\/generated\.khoj\.dev)).)*?>/gis,
            ''
        );

        // Sanitize the markdown text rendered as HTML
        return DOMPurify.sanitize(virtualChatMessageBodyTextEl.innerHTML);
    }

    renderMessageWithReferences(
        chatEl: Element,
        message: string,
        sender: string,
        turnId: string,
        context?: string[],
        onlineContext?: object,
        dt?: Date,
        intentType?: string,
        inferredQueries?: string[],
        conversationId?: string,
        images?: string[],
        excalidrawDiagram?: string,
        mermaidjsDiagram?: string
    ) {
        if (!message) return;

        let chatMessageEl;
        if (
            intentType?.includes("text-to-image") ||
            intentType === "excalidraw" ||
            (images && images.length > 0) ||
            mermaidjsDiagram ||
            excalidrawDiagram) {
            let imageMarkdown = this.generateImageMarkdown(message, intentType ?? "", inferredQueries, conversationId, images, excalidrawDiagram, mermaidjsDiagram);
            chatMessageEl = this.renderMessage({
                chatBodyEl: chatEl,
                message: imageMarkdown,
                sender,
                dt,
                turnId
            });
        } else {
            chatMessageEl = this.renderMessage({
                chatBodyEl: chatEl,
                message,
                sender,
                dt,
                turnId
            });
        }

        // If no document or online context is provided, skip rendering the reference section
        if ((context == null || context.length == 0)
            && (onlineContext == null || (onlineContext && Object.keys(onlineContext).length == 0))) {
            return;
        }

        // If document or online context is provided, render the message with its references
        let references: any = {};
        if (!!context) references["notes"] = context;
        if (!!onlineContext) references["online"] = onlineContext;
        let chatMessageBodyEl = chatMessageEl.getElementsByClassName("khoj-chat-message-text")[0];
        chatMessageBodyEl.appendChild(this.createReferenceSection(references));
    }

    generateImageMarkdown(message: string, intentType: string, inferredQueries?: string[], conversationId?: string, images?: string[], excalidrawDiagram?: string, mermaidjsDiagram?: string): string {
        let imageMarkdown = "";
        if (intentType === "text-to-image") {
            imageMarkdown = `![](data:image/png;base64,${message})`;
        } else if (intentType === "text-to-image2") {
            imageMarkdown = `![](${message})`;
        } else if (intentType === "text-to-image-v3") {
            imageMarkdown = `![](${message})`;
        } else if (intentType === "excalidraw" || excalidrawDiagram) {
            const domain = this.setting.khojUrl.endsWith("/") ? this.setting.khojUrl : `${this.setting.khojUrl}/`;
            const redirectMessage = `Hey, I'm not ready to show you diagrams yet here. But you can view it in ${domain}chat?conversationId=${conversationId}`;
            imageMarkdown = redirectMessage;
        } else if (mermaidjsDiagram) {
            imageMarkdown = "```mermaid\n" + mermaidjsDiagram + "\n```";
        } else if (images && images.length > 0) {
            imageMarkdown += images.map(image => `![](${image})`).join('\n\n');
            imageMarkdown += message;
        }

        if (images?.length === 0 && inferredQueries) {
            imageMarkdown += "\n\n**Inferred Query**:";
            for (let inferredQuery of inferredQueries) {
                imageMarkdown += `\n\n${inferredQuery}`;
            }
        }
        return imageMarkdown;
    }

    renderMessage({ chatBodyEl, message, sender, dt, turnId, raw = false, willReplace = true, isSystemMessage = false }: RenderMessageOptions): Element {
        let message_time = this.formatDate(dt ?? new Date());

        // Append message to conversation history HTML element.
        // The chat logs should display above the message input box to follow standard UI semantics
        let chatMessageEl = chatBodyEl.createDiv({
            attr: {
                "data-meta": message_time,
                class: `khoj-chat-message ${sender}`,
                ...(turnId && { "data-turnId": turnId })
            },
        })
        let chatMessageBodyEl = chatMessageEl.createDiv();
        chatMessageBodyEl.addClasses(["khoj-chat-message-text", sender]);
        let chatMessageBodyTextEl = chatMessageBodyEl.createDiv();

        // Sanitize the markdown to render
        message = DOMPurify.sanitize(message);

        if (raw) {
            chatMessageBodyTextEl.innerHTML = message;
        } else {
            // @ts-ignore
            chatMessageBodyTextEl.innerHTML = this.markdownTextToSanitizedHtml(message, this);
        }

        // Add action buttons to each chat message element
        if (willReplace === true) {
            this.renderActionButtons(message, chatMessageBodyTextEl, isSystemMessage);
        }

        // Remove user-select: none property to make text selectable
        chatMessageEl.style.userSelect = "text";

        // Scroll to bottom after inserting chat messages
        this.scrollChatToBottom();

        return chatMessageEl;
    }

    createKhojResponseDiv(dt?: Date): HTMLDivElement {
        let messageTime = this.formatDate(dt ?? new Date());

        // Append message to conversation history HTML element.
        // The chat logs should display above the message input box to follow standard UI semantics
        let chatBodyEl = this.contentEl.getElementsByClassName("khoj-chat-body")[0];
        let chatMessageEl = chatBodyEl.createDiv({
            attr: {
                "data-meta": messageTime,
                class: `khoj-chat-message khoj`
            },
        })

        // Scroll to bottom after inserting chat messages
        this.scrollChatToBottom();

        return chatMessageEl;
    }

    async renderIncrementalMessage(htmlElement: HTMLDivElement, additionalMessage: string) {
        this.chatMessageState.rawResponse += additionalMessage;
        htmlElement.innerHTML = "";
        // Sanitize the markdown to render
        this.chatMessageState.rawResponse = DOMPurify.sanitize(this.chatMessageState.rawResponse);
        // @ts-ignore
        htmlElement.innerHTML = this.markdownTextToSanitizedHtml(this.chatMessageState.rawResponse, this);
        // Render action buttons for the message
        this.renderActionButtons(this.chatMessageState.rawResponse, htmlElement);
        // Scroll to bottom of modal, till the send message input box
        this.scrollChatToBottom();
    }

    renderActionButtons(message: string, chatMessageBodyTextEl: HTMLElement, isSystemMessage: boolean = false) {
        let copyButton = this.contentEl.createEl('button');
        copyButton.classList.add("chat-action-button");
        copyButton.title = "Copy Message to Clipboard";
        setIcon(copyButton, "copy-plus");
        copyButton.addEventListener('click', createCopyParentText(message));

        // Add button to paste into current buffer
        let pasteToFile = this.contentEl.createEl('button');
        pasteToFile.classList.add("chat-action-button");
        pasteToFile.title = "Paste Message to File";
        setIcon(pasteToFile, "clipboard-paste");
        pasteToFile.addEventListener('click', (event) => { pasteTextAtCursor(createCopyParentText(message, 'clipboard-paste')(event)); });

        // Add edit button only for user messages
        let editButton = null;
        if (!isSystemMessage && chatMessageBodyTextEl.closest('.khoj-chat-message.you')) {
            editButton = this.contentEl.createEl('button');
            editButton.classList.add("chat-action-button");
            editButton.title = "Edit Message";
            setIcon(editButton, "edit-3");
            editButton.addEventListener('click', () => {
                const messageEl = chatMessageBodyTextEl.closest('.khoj-chat-message');
                if (messageEl) {
                    // Get all messages up to this one
                    const allMessages = Array.from(this.contentEl.getElementsByClassName('khoj-chat-message'));
                    const currentIndex = allMessages.indexOf(messageEl as HTMLElement);

                    // Remove all messages after and including this one
                    for (let i = allMessages.length - 1; i >= currentIndex; i--) {
                        allMessages[i].remove();
                    }

                    // Get the message content without the emoji if it exists
                    let messageContent = message;
                    const emojiRegex = /^[^\p{L}\p{N}]+\s*/u;
                    messageContent = messageContent.replace(emojiRegex, '');

                    // Set the message in the input field
                    const chatInput = this.contentEl.querySelector('.khoj-chat-input') as HTMLTextAreaElement;
                    if (chatInput) {
                        chatInput.value = messageContent;
                        chatInput.focus();
                    }
                }
            });
        }

        // Add delete button
        let deleteButton = null;
        if (!isSystemMessage) {
            deleteButton = this.contentEl.createEl('button');
            deleteButton.classList.add("chat-action-button");
            deleteButton.title = "Delete Message";
            setIcon(deleteButton, "trash-2");
            deleteButton.addEventListener('click', () => {
                const messageEl = chatMessageBodyTextEl.closest('.khoj-chat-message');
                if (messageEl) {
                    // Ask for confirmation before deleting
                    if (confirm('Are you sure you want to delete this message?')) {
                        this.deleteMessage(messageEl as HTMLElement);
                    }
                }
            });
        }

        // Append buttons to parent element
        chatMessageBodyTextEl.append(copyButton, pasteToFile);
        if (editButton) {
            chatMessageBodyTextEl.append(editButton);
        }
        if (deleteButton) {
            chatMessageBodyTextEl.append(deleteButton);
        }
        if (this.setting.userInfo?.is_active) {
            // Create a speech button icon to play the message out loud
            let speechButton = this.contentEl.createEl('button');
            speechButton.classList.add("chat-action-button", "speech-button");
            speechButton.title = "Listen to Message";
            setIcon(speechButton, "speech")
            speechButton.addEventListener('click', (event) => this.textToSpeech(message, event));
            chatMessageBodyTextEl.append(speechButton);
        }
    }

    formatDate(date: Date): string {
        // Format date in HH:MM, DD MMM YYYY format
        let time_string = date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
        let date_string = date.toLocaleString('en-IN', { year: 'numeric', month: 'short', day: '2-digit' }).replace(/-/g, ' ');
        return `${time_string}, ${date_string}`;
    }

    async createNewConversation(agentSlug?: string) {
        let chatBodyEl = this.contentEl.getElementsByClassName("khoj-chat-body")[0] as HTMLElement;
        chatBodyEl.innerHTML = "";
        chatBodyEl.dataset.conversationId = "";
        chatBodyEl.dataset.conversationTitle = "";
        this.userMessages = [];
        this.startingMessage = "Message";

        // Update the placeholder of the chat input
        const chatInput = this.contentEl.querySelector('.khoj-chat-input') as HTMLTextAreaElement;
        if (chatInput) {
            chatInput.placeholder = this.startingMessage;
        }

        try {
            // Create a new conversation with or without an agent
            let endpoint = `${this.setting.khojUrl}/api/chat/sessions`;
            if (agentSlug) {
                endpoint += `?agent_slug=${encodeURIComponent(agentSlug)}`;
            }

            const response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "Authorization": `Bearer ${this.setting.khojApiKey}`,
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({}) // Empty body as agent_slug is in the URL
            });

            if (response.ok) {
                const sessionInfo = await response.json();
                chatBodyEl.dataset.conversationId = sessionInfo.conversation_id;
                this.currentAgent = agentSlug || null;

                // Update agent selector to reflect current agent
                const agentSelect = this.contentEl.querySelector('.khoj-agent-select') as HTMLSelectElement;
                if (agentSelect) {
                    agentSelect.value = this.currentAgent || '';
                }
            } else {
                console.error("Failed to create session:", response.statusText);
            }
        } catch (error) {
            console.error("Error creating session:", error);
        }

        this.renderMessage({ chatBodyEl, message: "Hey 👋🏾, what's up?", sender: "khoj", isSystemMessage: true });
    }

    async toggleChatSessions(forceShow: boolean = false): Promise<boolean> {
        this.userMessages = [];  // clear user previous message history
        let chatBodyEl = this.contentEl.getElementsByClassName("khoj-chat-body")[0] as HTMLElement;
        if (!forceShow && this.contentEl.getElementsByClassName("side-panel")?.length > 0) {
            chatBodyEl.innerHTML = "";
            return this.getChatHistory(chatBodyEl);
        }
        chatBodyEl.innerHTML = "";
        const sidePanelEl = chatBodyEl.createDiv("side-panel");
        const newConversationEl = sidePanelEl.createDiv("new-conversation");
        const conversationHeaderTitleEl = newConversationEl.createDiv("conversation-header-title");
        conversationHeaderTitleEl.textContent = "Conversations";

        const newConversationButtonEl = newConversationEl.createEl("button");
        newConversationButtonEl.classList.add("new-conversation-button");
        newConversationButtonEl.classList.add("side-panel-button");
        newConversationButtonEl.addEventListener('click', (_) => this.createNewConversation());
        setIcon(newConversationButtonEl, "plus");
        newConversationButtonEl.innerHTML += "New";
        newConversationButtonEl.title = "New Conversation (^N)";

        const existingConversationsEl = sidePanelEl.createDiv("existing-conversations");
        const conversationListEl = existingConversationsEl.createDiv("conversation-list");
        const conversationListBodyHeaderEl = conversationListEl.createDiv("conversation-list-header");
        const conversationListBodyEl = conversationListEl.createDiv("conversation-list-body");

        const chatSessionsUrl = `${this.setting.khojUrl}/api/chat/sessions?client=obsidian`;
        const headers = { 'Authorization': `Bearer ${this.setting.khojApiKey}` };
        try {
            let response = await fetch(chatSessionsUrl, { method: "GET", headers: headers });
            let responseJson: any = await response.json();
            let conversationId = chatBodyEl.dataset.conversationId;

            if (responseJson.length > 0) {
                conversationListBodyHeaderEl.style.display = "block";
                for (let key in responseJson) {
                    let conversation = responseJson[key];
                    let conversationSessionEl = this.contentEl.createEl('div');
                    let incomingConversationId = conversation["conversation_id"];
                    conversationSessionEl.classList.add("conversation-session");
                    if (incomingConversationId == conversationId) {
                        conversationSessionEl.classList.add("selected-conversation");
                    }
                    const conversationTitle = conversation["slug"] || `New conversation 🌱`;
                    const conversationSessionTitleEl = conversationSessionEl.createDiv("conversation-session-title");
                    conversationSessionTitleEl.textContent = conversationTitle;
                    conversationSessionTitleEl.addEventListener('click', () => {
                        chatBodyEl.innerHTML = "";
                        chatBodyEl.dataset.conversationId = incomingConversationId;
                        chatBodyEl.dataset.conversationTitle = conversationTitle;
                        this.getChatHistory(chatBodyEl);
                    });

                    let conversationMenuEl = this.contentEl.createEl('div');
                    conversationMenuEl = this.addConversationMenu(
                        conversationMenuEl,
                        conversationSessionEl,
                        conversationTitle,
                        conversationSessionTitleEl,
                        chatBodyEl,
                        incomingConversationId,
                        incomingConversationId == conversationId,
                    );

                    conversationSessionEl.appendChild(conversationMenuEl);
                    conversationListBodyEl.appendChild(conversationSessionEl);
                }
            }
        } catch (err) {
            return false;
        }
        return true;
    }

    addConversationMenu(
        conversationMenuEl: HTMLDivElement,
        conversationSessionEl: HTMLElement,
        conversationTitle: string,
        conversationSessionTitleEl: HTMLElement,
        chatBodyEl: HTMLElement,
        incomingConversationId: string,
        selectedConversation: boolean,
    ) {
        conversationMenuEl.classList.add("conversation-menu");

        const headers = { 'Authorization': `Bearer ${this.setting.khojApiKey}` };

        let editConversationTitleButtonEl = this.contentEl.createEl('button');
        setIcon(editConversationTitleButtonEl, "edit");
        editConversationTitleButtonEl.title = "Rename";
        editConversationTitleButtonEl.classList.add("edit-title-button", "three-dot-menu-button-item", "clickable-icon");
        if (selectedConversation) editConversationTitleButtonEl.classList.add("selected-conversation");
        editConversationTitleButtonEl.addEventListener('click', (event) => {
            event.stopPropagation();

            let conversationMenuChildren = conversationMenuEl.children;
            let totalItems = conversationMenuChildren.length;

            for (let i = totalItems - 1; i >= 0; i--) {
                conversationMenuChildren[i].remove();
            }

            // Create a dialog box to get new title for conversation
            let editConversationTitleInputEl = this.contentEl.createEl('input');
            editConversationTitleInputEl.classList.add("conversation-title-input");
            editConversationTitleInputEl.value = conversationTitle;
            editConversationTitleInputEl.addEventListener('click', function (event) {
                event.stopPropagation();
            });
            editConversationTitleInputEl.addEventListener('keydown', function (event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                    editConversationTitleSaveButtonEl.click();
                }
            });
            let editConversationTitleSaveButtonEl = this.contentEl.createEl('button');
            conversationSessionTitleEl.replaceWith(editConversationTitleInputEl);
            editConversationTitleSaveButtonEl.innerHTML = "Save";
            editConversationTitleSaveButtonEl.classList.add("three-dot-menu-button-item", "clickable-icon");
            if (selectedConversation) editConversationTitleSaveButtonEl.classList.add("selected-conversation");
            editConversationTitleSaveButtonEl.addEventListener('click', (event) => {
                event.stopPropagation();
                let newTitle = editConversationTitleInputEl.value;
                if (newTitle != null) {
                    let editURL = `/api/chat/title?client=web&conversation_id=${incomingConversationId}&title=${newTitle}`;
                    fetch(`${this.setting.khojUrl}${editURL}`, { method: "PATCH", headers })
                        .then(response => response.ok ? response.json() : Promise.reject(response))
                        .then(data => {
                            conversationSessionTitleEl.textContent = newTitle;
                        })
                        .catch(err => {
                            return;
                        });
                    const conversationSessionTitleEl = conversationSessionEl.createDiv("conversation-session-title");
                    conversationSessionTitleEl.textContent = newTitle;
                    conversationSessionTitleEl.addEventListener('click', () => {
                        chatBodyEl.innerHTML = "";
                        chatBodyEl.dataset.conversationId = incomingConversationId;
                        chatBodyEl.dataset.conversationTitle = conversationTitle;
                        this.getChatHistory(chatBodyEl);
                    });

                    let newConversationMenuEl = this.contentEl.createEl('div');
                    newConversationMenuEl = this.addConversationMenu(
                        newConversationMenuEl,
                        conversationSessionEl,
                        newTitle,
                        conversationSessionTitleEl,
                        chatBodyEl,
                        incomingConversationId,
                        selectedConversation,
                    );

                    conversationMenuEl.replaceWith(newConversationMenuEl);
                    editConversationTitleInputEl.replaceWith(conversationSessionTitleEl);
                }
            });
            conversationMenuEl.appendChild(editConversationTitleSaveButtonEl);
        });

        conversationMenuEl.appendChild(editConversationTitleButtonEl);

        let deleteConversationButtonEl = this.contentEl.createEl('button');
        setIcon(deleteConversationButtonEl, "trash");
        deleteConversationButtonEl.title = "Delete";
        deleteConversationButtonEl.classList.add("delete-conversation-button", "three-dot-menu-button-item", "clickable-icon");
        if (selectedConversation) deleteConversationButtonEl.classList.add("selected-conversation");
        deleteConversationButtonEl.addEventListener('click', () => {
            // Ask for confirmation before deleting chat session
            let confirmation = confirm('Are you sure you want to delete this chat session?');
            if (!confirmation) return;
            let deleteURL = `/api/chat/history?client=obsidian&conversation_id=${incomingConversationId}`;
            fetch(`${this.setting.khojUrl}${deleteURL}`, { method: "DELETE", headers })
                .then(response => response.ok ? response.json() : Promise.reject(response))
                .then(data => {
                    chatBodyEl.innerHTML = "";
                    chatBodyEl.dataset.conversationId = "";
                    chatBodyEl.dataset.conversationTitle = "";
                    this.toggleChatSessions(true);
                })
                .catch(err => {
                    return;
                });
        });

        conversationMenuEl.appendChild(deleteConversationButtonEl);
        return conversationMenuEl;
    }

    async getChatHistory(chatBodyEl: HTMLElement): Promise<boolean> {
        // Get chat history from Khoj backend
        let chatUrl = `${this.setting.khojUrl}/api/chat/history?client=obsidian`;
        if (chatBodyEl.dataset.conversationId) {
            chatUrl += `&conversation_id=${chatBodyEl.dataset.conversationId}`;
        }

        console.log("Fetching chat history from:", chatUrl);

        try {
            let response = await fetch(chatUrl, {
                method: "GET",
                headers: { "Authorization": `Bearer ${this.setting.khojApiKey}` },
            });

            let responseJson: any = await response.json();
            console.log("Chat history response:", responseJson);

            chatBodyEl.dataset.conversationId = responseJson.conversation_id;

            if (responseJson.detail) {
                // If the server returns error details in response, render a setup hint.
                let setupMsg = "Hi 👋🏾, to start chatting add available chat models options via [the Django Admin panel](/server/admin) on the Server";
                this.renderMessage({
                    chatBodyEl,
                    message: setupMsg,
                    sender: "khoj",
                    isSystemMessage: true
                });

                return false;
            } else if (responseJson.response) {
                // Render conversation history, if any
                chatBodyEl.dataset.conversationId = responseJson.response.conversation_id;
                chatBodyEl.dataset.conversationTitle = responseJson.response.slug || `New conversation 🌱`;

                // Update current agent from conversation history
                if (responseJson.response.agent?.slug) {
                    console.log("Found agent in conversation history:", responseJson.response.agent);
                    this.currentAgent = responseJson.response.agent.slug;
                    // Update the agent selector if it exists
                    const agentSelect = this.contentEl.querySelector('.khoj-agent-select') as HTMLSelectElement;
                    if (agentSelect && this.currentAgent) {
                        agentSelect.value = this.currentAgent;
                        console.log("Updated agent selector to:", this.currentAgent);
                    }
                }

                let chatLogs = responseJson.response?.conversation_id ? responseJson.response.chat ?? [] : responseJson.response;
                chatLogs.forEach((chatLog: any) => {
                    // Convert commands to emojis for user messages
                    if (chatLog.by === "you") {
                        chatLog.message = this.convertCommandsToEmojis(chatLog.message);
                    }

                    // Transform khoj-edit blocks into accordions
                    chatLog.message = this.transformKhojEditBlocks(chatLog.message);

                    this.renderMessageWithReferences(
                        chatBodyEl,
                        chatLog.message,
                        chatLog.by,
                        chatLog.turnId,
                        chatLog.context,
                        chatLog.onlineContext,
                        new Date(chatLog.created),
                        chatLog.intent?.type,
                        chatLog.intent?.["inferred-queries"],
                        chatBodyEl.dataset.conversationId ?? "",
                        chatLog.images,
                        chatLog.excalidrawDiagram,
                        chatLog.mermaidjsDiagram,
                    );
                    // push the user messages to the chat history
                    if (chatLog.by === "you") {
                        this.userMessages.push(chatLog.message);
                    }
                });

                // Update starting message after loading history
                const modifierKey: string = Platform.isMacOS ? '⌘' : '^';
                this.startingMessage = this.userMessages.length > 0
                    ? `(${modifierKey}+↑/↓) for prev messages`
                    : "Message";

                // Update the placeholder of the chat input
                const chatInput = this.contentEl.querySelector('.khoj-chat-input') as HTMLTextAreaElement;
                if (chatInput) {
                    chatInput.placeholder = this.startingMessage;
                }
            }
        } catch (err) {
            let errorMsg = "Unable to get response from Khoj server ❤️‍🩹. Ensure server is running or contact developers for help at [team@khoj.dev](mailto:team@khoj.dev) or in [Discord](https://discord.gg/BDgyabRM6e)";
            this.renderMessage({
                chatBodyEl,
                message: errorMsg,
                sender: "khoj",
                isSystemMessage: true
            });
            return false;
        }
        return true;
    }

    convertMessageChunkToJson(rawChunk: string): MessageChunk {
        if (rawChunk?.startsWith("{") && rawChunk?.endsWith("}")) {
            try {
                let jsonChunk = JSON.parse(rawChunk);
                if (!jsonChunk.type)
                    jsonChunk = { type: 'message', data: jsonChunk };
                return jsonChunk;
            } catch (e) {
                return { type: 'message', data: rawChunk };
            }
        } else if (rawChunk.length > 0) {
            return { type: 'message', data: rawChunk };
        }
        return { type: '', data: '' };
    }

    processMessageChunk(rawChunk: string): void {
        const chunk = this.convertMessageChunkToJson(rawChunk);
        if (!chunk || !chunk.type) return;
        if (chunk.type === 'status') {
            const statusMessage = chunk.data;
            this.handleStreamResponse(this.chatMessageState.newResponseTextEl, statusMessage, this.chatMessageState.loadingEllipsis, false);
        } else if (chunk.type === 'generated_assets') {
            const generatedAssets = chunk.data;
            const imageData = this.handleImageResponse(generatedAssets, this.chatMessageState.rawResponse);
            this.chatMessageState.generatedAssets = imageData;
            this.handleStreamResponse(this.chatMessageState.newResponseTextEl, imageData, this.chatMessageState.loadingEllipsis, false);
        } else if (chunk.type === 'start_llm_response') {
            // Start of streaming
        } else if (chunk.type === 'end_llm_response') {
            // End of streaming
        } else if (chunk.type === 'end_response') {
            // Check for edit blocks if in write mode
            if (this.fileAccessMode === 'write') {
                const editBlocks = this.parseEditBlocks(this.chatMessageState.rawResponse);
                if (editBlocks.length > 0) {
                    this.applyEditBlocks(editBlocks);
                }
            }

            // Automatically respond with voice if the subscribed user has sent voice message
            if (this.chatMessageState.isVoice && this.setting.userInfo?.is_active)
                this.textToSpeech(this.chatMessageState.rawResponse);

            // Append any references after all the data has been streamed
            this.finalizeChatBodyResponse(this.chatMessageState.references, this.chatMessageState.newResponseTextEl, this.chatMessageState.turnId);

            const liveQuery = this.chatMessageState.rawQuery;
            // Reset variables
            this.chatMessageState = {
                newResponseTextEl: null,
                newResponseEl: null,
                loadingEllipsis: null,
                references: {},
                rawResponse: "",
                rawQuery: liveQuery,
                isVoice: false,
                generatedAssets: "",
                turnId: "",
                editBlocks: []
            };
        } else if (chunk.type === "references") {
            this.chatMessageState.references = { "notes": chunk.data.context, "online": chunk.data.onlineContext };
        } else if (chunk.type === 'message') {
            const chunkData = chunk.data;
            if (typeof chunkData === 'object' && chunkData !== null) {
                this.handleJsonResponse(chunkData);
            } else if (typeof chunkData === 'string' && chunkData.trim()?.startsWith("{") && chunkData.trim()?.endsWith("}")) {
                try {
                    const jsonData = JSON.parse(chunkData.trim());
                    this.handleJsonResponse(jsonData);
                } catch (e) {
                    this.chatMessageState.rawResponse += chunkData;
                    this.handleStreamResponse(this.chatMessageState.newResponseTextEl, this.chatMessageState.rawResponse + this.chatMessageState.generatedAssets, this.chatMessageState.loadingEllipsis);
                }
            } else {
                this.chatMessageState.rawResponse += chunkData;
                this.handleStreamResponse(this.chatMessageState.newResponseTextEl, this.chatMessageState.rawResponse + this.chatMessageState.generatedAssets, this.chatMessageState.loadingEllipsis);
            }
        } else if (chunk.type === "metadata") {
            const { turnId } = chunk.data;
            if (turnId) {
                this.chatMessageState.turnId = turnId;
            }
        }
    }

    handleJsonResponse(jsonData: any): void {
        if (jsonData.image || jsonData.detail || jsonData.images || jsonData.mermaidjsDiagram) {
            this.chatMessageState.rawResponse = this.handleImageResponse(jsonData, this.chatMessageState.rawResponse);
        } else if (jsonData.response) {
            this.chatMessageState.rawResponse = jsonData.response;
        }

        if (this.chatMessageState.newResponseTextEl) {
            this.chatMessageState.newResponseTextEl.innerHTML = "";
            this.chatMessageState.newResponseTextEl.appendChild(this.formatHTMLMessage(this.chatMessageState.rawResponse));
        }
    }

    async readChatStream(response: Response): Promise<void> {
        // Exit if response body is empty
        if (response.body == null) return;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const eventDelimiter = '␃🔚␗';
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();

            if (done) {
                this.processMessageChunk(buffer);
                buffer = '';
                // Break if the stream is done
                break;
            }

            const chunk = decoder.decode(value, { stream: true });
            console.debug("Raw Chunk:", chunk)
            // Start buffering chunks until complete event is received
            buffer += chunk;

            // Once the buffer contains a complete event
            let newEventIndex;
            while ((newEventIndex = buffer.indexOf(eventDelimiter)) !== -1) {
                // Extract the event from the buffer
                const event = buffer.slice(0, newEventIndex);
                buffer = buffer.slice(newEventIndex + eventDelimiter.length);

                // Process the event
                if (event) this.processMessageChunk(event);
            }
        }
    }

    async getChatResponse(query: string | undefined | null, displayQuery: string | undefined | null, isVoice: boolean = false): Promise<void> {
        // Exit if query is empty
        if (!query || query === "") return;

        // Render user query as chat message with display version
        let chatBodyEl = this.contentEl.getElementsByClassName("khoj-chat-body")[0] as HTMLElement;
        this.renderMessage({ chatBodyEl, message: displayQuery || query, sender: "you" });

        let conversationId = chatBodyEl.dataset.conversationId;

        if (!conversationId) {
            try {
                const requestBody = {
                    ...(this.currentAgent && { agent_slug: this.currentAgent })
                };

                const response = await fetch(`${this.setting.khojUrl}/api/chat/sessions`, {
                    method: "POST",
                    headers: {
                        "Authorization": `Bearer ${this.setting.khojApiKey}`,
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify(requestBody)
                });
                if (response.ok) {
                    const data = await response.json();
                    conversationId = data.conversation_id;
                    chatBodyEl.dataset.conversationId = conversationId;
                } else {
                    console.error("Failed to create session:", response.statusText);
                    return;
                }
            } catch (error) {
                console.error("Error creating session:", error);
                return;
            }
        }

        // Get open files content if we have access
        const openFilesContent = await this.getOpenFilesContent();

        // Extract mode command if present
        const modeMatch = this.chatModes.find(mode => query.startsWith(mode.command));
        const modeCommand = modeMatch ? query.substring(0, modeMatch.command.length) : '';
        const queryWithoutMode = modeMatch ? query.substring(modeMatch.command.length).trim() : query;

        // Combine mode, query and files content
        const finalQuery = modeCommand + (modeCommand ? ' ' : '') + queryWithoutMode + openFilesContent;

        // Get chat response from Khoj backend
        const chatUrl = `${this.setting.khojUrl}/api/chat?client=obsidian`;
        const body = {
            q: finalQuery,
            n: this.setting.resultsCount,
            stream: true,
            conversation_id: conversationId,
            ...(this.currentAgent && { agent_slug: this.currentAgent }),
            ...(!!this.location && this.location.city && { city: this.location.city }),
            ...(!!this.location && this.location.region && { region: this.location.region }),
            ...(!!this.location && this.location.countryName && { country: this.location.countryName }),
            ...(!!this.location && this.location.countryCode && { country_code: this.location.countryCode }),
            ...(!!this.location && this.location.timezone && { timezone: this.location.timezone }),
        };

        let newResponseEl = this.createKhojResponseDiv();
        let newResponseTextEl = newResponseEl.createDiv();
        newResponseTextEl.classList.add("khoj-chat-message-text", "khoj");

        // Temporary status message to indicate that Khoj is thinking
        let loadingEllipsis = this.createLoadingEllipse();
        newResponseTextEl.appendChild(loadingEllipsis);

        // Set chat message state
        this.chatMessageState = {
            newResponseEl: newResponseEl,
            newResponseTextEl: newResponseTextEl,
            loadingEllipsis: loadingEllipsis,
            references: {},
            rawQuery: query,
            rawResponse: "",
            isVoice: isVoice,
            generatedAssets: "",
            turnId: "",
            editBlocks: []
        };

        let response = await fetch(chatUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${this.setting.khojApiKey}`,
            },
            body: JSON.stringify(body),
        })

        try {
            if (response.body === null) throw new Error("Response body is null");

            // Stream and render chat response
            await this.readChatStream(response);
        } catch (err) {
            console.error(`Khoj chat response failed with\n${err}`);
            let errorMsg = "Sorry, unable to get response from Khoj backend ❤️‍🩹. Retry or contact developers for help at <a href=mailto:'team@khoj.dev'>team@khoj.dev</a> or <a href='https://discord.gg/BDgyabRM6e'>on Discord</a>";
            newResponseTextEl.textContent = errorMsg;
        }
    }

    flashStatusInChatInput(message: string) {
        // Get chat input element and original placeholder
        let chatInput = <HTMLTextAreaElement>this.contentEl.getElementsByClassName("khoj-chat-input")[0];
        let originalPlaceholder = chatInput.placeholder;
        // Set placeholder to message
        chatInput.placeholder = message;
        // Reset placeholder after 2 seconds
        setTimeout(() => {
            chatInput.placeholder = originalPlaceholder;
        }, 2000);
    }

    async clearConversationHistory() {
        let chatBody = this.contentEl.getElementsByClassName("khoj-chat-body")[0] as HTMLElement;

        let response = await request({
            url: `${this.setting.khojUrl}/api/chat/history?client=obsidian`,
            method: "DELETE",
            headers: { "Authorization": `Bearer ${this.setting.khojApiKey}` },
        })
        try {
            let result = JSON.parse(response);
            if (result.status !== "ok") {
                // Throw error if conversation history isn't cleared
                throw new Error("Failed to clear conversation history");
            } else {
                let getChatHistoryStatus = await this.getChatHistory(chatBody);
                // If conversation history is cleared successfully, clear chat logs from modal
                if (getChatHistoryStatus) chatBody.innerHTML = "";
                let statusMsg = getChatHistoryStatus ? result.message : "Failed to clear conversation history";
                this.flashStatusInChatInput(statusMsg);
            }
        } catch (err) {
            this.flashStatusInChatInput("Failed to clear conversation history");
        }
    }

    sendMessageTimeout: NodeJS.Timeout | undefined;
    mediaRecorder: MediaRecorder | undefined;
    async speechToText(event: MouseEvent | TouchEvent | KeyboardEvent) {
        event.preventDefault();
        const transcribeButton = <HTMLButtonElement>this.contentEl.getElementsByClassName("khoj-transcribe")[0];
        const chatInput = <HTMLTextAreaElement>this.contentEl.getElementsByClassName("khoj-chat-input")[0];
        const sendButton = <HTMLButtonElement>this.contentEl.getElementsByClassName("khoj-chat-send")[0]

        const generateRequestBody = async (audioBlob: Blob, boundary_string: string) => {
            const boundary = `------${boundary_string}`;
            const chunks: ArrayBuffer[] = [];

            chunks.push(new TextEncoder().encode(`${boundary}\r\n`));
            chunks.push(new TextEncoder().encode(`Content-Disposition: form-data; name="file"; filename="blob"\r\nContent-Type: "application/octet-stream"\r\n\r\n`));
            chunks.push(await audioBlob.arrayBuffer());
            chunks.push(new TextEncoder().encode('\r\n'));

            await Promise.all(chunks);
            chunks.push(new TextEncoder().encode(`${boundary}--\r\n`));
            return await new Blob(chunks).arrayBuffer();
        };

        const sendToServer = async (audioBlob: Blob) => {
            const boundary_string = `Boundary${Math.random().toString(36).slice(2)}`;
            const requestBody = await generateRequestBody(audioBlob, boundary_string);

            const response = await requestUrl({
                url: `${this.setting.khojUrl}/api/transcribe?client=obsidian`,
                method: 'POST',
                headers: { "Authorization": `Bearer ${this.setting.khojApiKey}` },
                contentType: `multipart/form-data; boundary=----${boundary_string}`,
                body: requestBody,
            });

            // Parse response from Khoj backend
            let noSpeechText: string[] = [
                "Thanks for watching!",
                "Thanks for watching.",
                "Thank you for watching!",
                "Thank you for watching.",
                "You",
                "Bye."
            ];
            let noSpeech: boolean = false;
            if (response.status === 200) {
                console.log(response);
                noSpeech = noSpeechText.includes(response.json.text.trimStart());
                if (!noSpeech) chatInput.value += response.json.text.trimStart();
                this.autoResize();
            } else if (response.status === 501) {
                throw new Error("⛔️ Configure speech-to-text model on server.");
            } else if (response.status === 422) {
                throw new Error("⛔️ Audio file to large to process.");
            } else {
                throw new Error("⛔️ Failed to transcribe audio.");
            }

            // Don't auto-send empty messages or when no speech is detected
            if (chatInput.value.length === 0 || noSpeech) return;

            // Show stop auto-send button. It stops auto-send when clicked
            setIcon(sendButton, "stop-circle");
            let stopSendButtonImg = <SVGElement>sendButton.getElementsByClassName("lucide-stop-circle")[0]
            stopSendButtonImg.addEventListener('click', (_) => { this.cancelSendMessage() });

            // Start the countdown timer UI
            stopSendButtonImg.getElementsByTagName("circle")[0].style.animation = "countdown 3s linear 1 forwards";
            stopSendButtonImg.getElementsByTagName("circle")[0].style.color = "var(--icon-color-active)";

            // Auto send message after 3 seconds
            this.sendMessageTimeout = setTimeout(() => {
                // Stop the countdown timer UI
                setIcon(sendButton, "arrow-up-circle")
                let sendImg = <SVGElement>sendButton.getElementsByClassName("lucide-arrow-up-circle")[0]
                sendImg.addEventListener('click', async (_) => { await this.chat() });

                // Send message
                this.chat(true);
            }, 3000);
        };

        const handleRecording = (stream: MediaStream) => {
            const audioChunks: Blob[] = [];
            const recordingConfig = { mimeType: 'audio/webm' };
            this.mediaRecorder = new MediaRecorder(stream, recordingConfig);

            this.mediaRecorder.addEventListener("dataavailable", function (event) {
                if (event.data.size > 0) audioChunks.push(event.data);
            });

            this.mediaRecorder.addEventListener("stop", async function () {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                await sendToServer(audioBlob);
            });

            this.mediaRecorder.start();
            // setIcon(transcribeButton, "mic-off");
            transcribeButton.classList.add("loading-encircle")
        };

        // Toggle recording
        if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive' || event.type === 'touchstart' || event.type === 'mousedown' || event.type === 'keydown') {
            navigator.mediaDevices
                .getUserMedia({ audio: true })
                ?.then(handleRecording)
                .catch((e) => {
                    this.flashStatusInChatInput("⛔️ Failed to access microphone");
                });
        } else if (this.mediaRecorder?.state === 'recording' || event.type === 'touchend' || event.type === 'touchcancel' || event.type === 'mouseup' || event.type === 'keyup') {
            this.mediaRecorder.stop();
            this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
            this.mediaRecorder = undefined;
            transcribeButton.classList.remove("loading-encircle");
            setIcon(transcribeButton, "mic");
        }
    }

    cancelSendMessage() {
        // Cancel the auto-send chat message timer if the stop-send-button is clicked
        clearTimeout(this.sendMessageTimeout);

        // Revert to showing send-button and hide the stop-send-button
        let sendButton = <HTMLButtonElement>this.contentEl.getElementsByClassName("khoj-chat-send")[0];
        setIcon(sendButton, "arrow-up-circle");
        let sendImg = <SVGElement>sendButton.getElementsByClassName("lucide-arrow-up-circle")[0]
        sendImg.addEventListener('click', async (_) => { await this.chat() });
    };

    incrementalChat(event: KeyboardEvent) {
        if (!event.shiftKey && event.key === 'Enter') {
            event.preventDefault();
            this.chat();
        }
    }

    onChatInput() {
        const chatInput = <HTMLTextAreaElement>this.contentEl.getElementsByClassName("khoj-chat-input")[0];
        chatInput.value = chatInput.value.trimStart();
        this.currentMessageIndex = -1;
        // store the current input
        this.currentUserInput = chatInput.value;
        this.autoResize();
    }

    autoResize() {
        const chatInput = <HTMLTextAreaElement>this.contentEl.getElementsByClassName("khoj-chat-input")[0];
        const scrollTop = chatInput.scrollTop;
        chatInput.style.height = '0';
        const scrollHeight = chatInput.scrollHeight + 8;  // +8 accounts for padding
        chatInput.style.height = Math.min(scrollHeight, 200) + 'px';
        chatInput.scrollTop = scrollTop;
        this.scrollChatToBottom();
    }

    scrollChatToBottom() {
        const chat_body_el = this.contentEl.getElementsByClassName("khoj-chat-body")[0];
        if (!!chat_body_el) chat_body_el.scrollTop = chat_body_el.scrollHeight;
    }

    createLoadingEllipse() {
        // Temporary status message to indicate that Khoj is thinking
        let loadingEllipsis = this.contentEl.createEl("div");
        loadingEllipsis.classList.add("lds-ellipsis");

        let firstEllipsis = this.contentEl.createEl("div");
        firstEllipsis.classList.add("lds-ellipsis-item");

        let secondEllipsis = this.contentEl.createEl("div");
        secondEllipsis.classList.add("lds-ellipsis-item");

        let thirdEllipsis = this.contentEl.createEl("div");
        thirdEllipsis.classList.add("lds-ellipsis-item");

        let fourthEllipsis = this.contentEl.createEl("div");
        fourthEllipsis.classList.add("lds-ellipsis-item");

        loadingEllipsis.appendChild(firstEllipsis);
        loadingEllipsis.appendChild(secondEllipsis);
        loadingEllipsis.appendChild(thirdEllipsis);
        loadingEllipsis.appendChild(fourthEllipsis);

        return loadingEllipsis;
    }

    handleStreamResponse(newResponseElement: HTMLElement | null, rawResponse: string, loadingEllipsis: HTMLElement | null, replace = true) {
        if (!newResponseElement) return;
        // Remove loading ellipsis if it exists
        if (newResponseElement.getElementsByClassName("lds-ellipsis").length > 0 && loadingEllipsis)
            newResponseElement.removeChild(loadingEllipsis);
        // Clear the response element if replace is true
        if (replace) newResponseElement.innerHTML = "";

        // Append response to the response element
        newResponseElement.appendChild(this.formatHTMLMessage(rawResponse, false, replace));

        // Append loading ellipsis if it exists
        if (!replace && loadingEllipsis) newResponseElement.appendChild(loadingEllipsis);
        // Scroll to bottom of chat view
        this.scrollChatToBottom();
    }

    handleImageResponse(imageJson: any, rawResponse: string) {
        if (imageJson.image) {
            const inferredQuery = imageJson.inferredQueries?.[0] ?? "generated image";

            // If response has image field, response is a generated image.
            if (imageJson.intentType === "text-to-image") {
                rawResponse += `![generated_image](data:image/png;base64,${imageJson.image})`;
            } else if (imageJson.intentType === "text-to-image2") {
                rawResponse += `![generated_image](${imageJson.image})`;
            } else if (imageJson.intentType === "text-to-image-v3") {
                rawResponse = `![generated_image](${imageJson.image})`;
            } else if (imageJson.intentType === "excalidraw") {
                const domain = this.setting.khojUrl.endsWith("/") ? this.setting.khojUrl : `${this.setting.khojUrl}/`;
                const redirectMessage = `Hey, I'm not ready to show you diagrams yet here. But you can view it in ${domain}`;
                rawResponse += redirectMessage;
            }
            if (inferredQuery) {
                rawResponse += `\n\n**Inferred Query**:\n\n${inferredQuery}`;
            }
        } else if (imageJson.images) {
            // If response has images field, response is a list of generated images.
            imageJson.images.forEach((image: any) => {
                rawResponse += `![generated_image](${image})\n\n`;
            });
        } else if (imageJson.excalidrawDiagram) {
            const domain = this.setting.khojUrl.endsWith("/") ? this.setting.khojUrl : `${this.setting.khojUrl}/`;
            const redirectMessage = `Hey, I'm not ready to show you diagrams yet here. But you can view it in ${domain}`;
            rawResponse += redirectMessage;
        } else if (imageJson.mermaidjsDiagram) {
            rawResponse += imageJson.mermaidjsDiagram;
        }

        // If response has detail field, response is an error message.
        if (imageJson.detail) rawResponse += imageJson.detail;

        return rawResponse;
    }

    finalizeChatBodyResponse(references: object, newResponseElement: HTMLElement | null, turnId: string) {
        if (!!newResponseElement && references != null && Object.keys(references).length > 0) {
            newResponseElement.appendChild(this.createReferenceSection(references));
        }
        if (!!newResponseElement && turnId) {
            // Set the turnId for the new response and the previous user message
            newResponseElement.parentElement?.setAttribute("data-turnId", turnId);
            newResponseElement.parentElement?.previousElementSibling?.setAttribute("data-turnId", turnId);
        }
        this.scrollChatToBottom();
        let chatInput = this.contentEl.getElementsByClassName("khoj-chat-input")[0];
        if (chatInput) chatInput.removeAttribute("disabled");
    }

    createReferenceSection(references: any) {
        let referenceSection = this.contentEl.createEl('div');
        referenceSection.classList.add("reference-section");
        referenceSection.classList.add("collapsed");

        let numReferences = 0;

        if (references.hasOwnProperty("notes")) {
            numReferences += references["notes"].length;

            references["notes"].forEach((reference: any, index: number) => {
                let polishedReference = this.generateReference(referenceSection, reference, index.toString());
                referenceSection.appendChild(polishedReference);
            });
        }
        if (references.hasOwnProperty("online")) {
            numReferences += this.processOnlineReferences(referenceSection, references["online"]);
        }

        let referenceExpandButton = this.contentEl.createEl('button');
        referenceExpandButton.classList.add("reference-expand-button");
        referenceExpandButton.innerHTML = numReferences == 1 ? "1 reference" : `${numReferences} references`;

        referenceExpandButton.addEventListener('click', function () {
            if (referenceSection.classList.contains("collapsed")) {
                referenceSection.classList.remove("collapsed");
                referenceSection.classList.add("expanded");
            } else {
                referenceSection.classList.add("collapsed");
                referenceSection.classList.remove("expanded");
            }
        });

        let referencesDiv = this.contentEl.createEl('div');
        referencesDiv.classList.add("references");
        referencesDiv.appendChild(referenceExpandButton);
        referencesDiv.appendChild(referenceSection);

        return referencesDiv;
    }

    // function to loop through the user's past messages
    handleArrowKeys(event: KeyboardEvent) {
        const chatInput = event.target as HTMLTextAreaElement;
        const isModKey = Platform.isMacOS ? event.metaKey : event.ctrlKey;

        if (isModKey && event.key === 'ArrowUp') {
            event.preventDefault();
            if (this.currentMessageIndex < this.userMessages.length - 1) {
                this.currentMessageIndex++;
                chatInput.value = this.userMessages[this.userMessages.length - 1 - this.currentMessageIndex];
            }
        } else if (isModKey && event.key === 'ArrowDown') {
            event.preventDefault();
            if (this.currentMessageIndex > 0) {
                this.currentMessageIndex--;
                chatInput.value = this.userMessages[this.userMessages.length - 1 - this.currentMessageIndex];
            } else if (this.currentMessageIndex === 0) {
                this.currentMessageIndex = -1;
                chatInput.value = this.currentUserInput;
            }
        }
    }

    // Add this new method to handle message deletion
    async deleteMessage(messageEl: HTMLElement) {
        const chatBodyEl = this.contentEl.getElementsByClassName("khoj-chat-body")[0] as HTMLElement;
        const conversationId = chatBodyEl.dataset.conversationId;

        // Get the turnId from the message's data-turn attribute
        const turnId = messageEl.getAttribute("data-turnId");
        if (!turnId || !conversationId) return;

        try {
            const response = await fetch(`${this.setting.khojUrl}/api/chat/conversation/message`, {
                method: "DELETE",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${this.setting.khojApiKey}`
                },
                body: JSON.stringify({
                    conversation_id: conversationId,
                    turn_id: turnId
                })
            });

            if (response.ok) {
                // Remove both the user message and Khoj response (the conversation turn)
                const isKhojMessage = messageEl.classList.contains("khoj");
                const messages = Array.from(chatBodyEl.getElementsByClassName("khoj-chat-message"));
                const messageIndex = messages.indexOf(messageEl);

                if (isKhojMessage && messageIndex > 0) {
                    // If it is a Khoj message, remove the previous user message too
                    messages[messageIndex - 1].remove();
                } else if (!isKhojMessage && messageIndex < messages.length - 1) {
                    // If it is a user message, remove the next Khoj message too
                    messages[messageIndex + 1].remove();
                }
                messageEl.remove();
            } else {
                this.flashStatusInChatInput("Failed to delete message");
            }
        } catch (error) {
            console.error("Error deleting message:", error);
            this.flashStatusInChatInput("Error deleting message");
        }
    }

    async fetchAgents() {
        try {
            const response = await fetch(`${this.setting.khojUrl}/api/agents`, {
                headers: {
                    "Authorization": `Bearer ${this.setting.khojApiKey}`
                }
            });

            if (response.ok) {
                this.agents = await response.json();
            } else {
                console.error("Failed to fetch agents:", response.statusText);
            }
        } catch (error) {
            console.error("Error fetching agents:", error);
        }
    }

    // Add this new method after the class declaration
    private async getOpenFilesContent(): Promise<string> {
        // Only proceed if we have read or write access
        if (this.fileAccessMode === 'none') return '';

        // Get all open markdown leaves
        const leaves = this.app.workspace.getLeavesOfType('markdown');
        if (leaves.length === 0) return '';

        let openFilesContent = "\n\n[SYSTEM]The user is currently working on the following files (content provided for context):\n\n";

        // Simplification des instructions d'édition
        if (this.fileAccessMode === 'write') {
            openFilesContent += `[EDIT INSTRUCTIONS] I can help you edit your notes using targeted modifications. I'll use multiple edit blocks to make precise changes rather than rewriting entire sections.

\`\`\`khoj-edit
"<start words>", "<end words>", "<complete new content>"
\`\`\`

📝 Example note:

\`\`\`
# Meeting Notes
Date: January 20, 2024

Action items from today:
- Review Q4 metrics
- Schedule follow-up with marketing team about new campaign launch
- Update project timeline and milestones for Q1 2024

Next steps:
- Send summary to team
- Book conference room for next week
\`\`\`

Examples of targeted edits:

1. Using just a few words to identify long text:
// Updating the marketing item (only need start/end words to identify it)
\`\`\`khoj-edit
"- Schedule follow-up", "campaign launch", "- Schedule follow-up with marketing team by Wednesday to discuss Q1 campaign launch"
\`\`\`

2. Multiple targeted changes:
// Adding priority and updating timeline item
\`\`\`khoj-edit
"- Review Q4", "metrics", "- [HIGH] Review Q4 metrics"
\`\`\`
\`\`\`khoj-edit
"- Update project", "Q1 2024", "- Update project timeline and add resource allocation for Q1 2024"
\`\`\`

3. Adding new content between sections:
// Just need the section headers to locate the insertion point
\`\`\`khoj-edit
"Action items from today:", "Next steps:", "Action items from today:\\n- Review Q4 metrics\\n- Schedule follow-up\\n- Update timeline\\n\\nDiscussion Points:\\n- Budget review\\n- Team feedback\\n\\nNext steps:"
\`\`\`

💡 Key points:
- Use minimal but unique words to identify text locations
- First argument: few words from start of target text
- Second argument: few words from end of target text
- Third argument: complete new content
- Words must uniquely identify the location (no ambiguity)
- Use \\n for line breaks
- Changes apply to first matching location

[END OF EDIT INSTRUCTIONS]\n\n`;
        }

        openFilesContent += `[OPEN FILES CONTEXT]\n\n`;

        for (const leaf of leaves) {
            const view = leaf.view as any;
            const file = view?.file;
            if (!file || file.extension !== 'md') continue;

            // Add file title without brackets
            openFilesContent += `# ${file.basename}\n\`\`\`markdown\n`;

            // Read file content
            try {
                const content = await this.app.vault.read(file);
                openFilesContent += content;
            } catch (error) {
                console.error(`Error reading file ${file.path}:`, error);
            }

            openFilesContent += "\n```\n\n";
        }

        openFilesContent += "[END OF CURRENT FILES CONTEXT]\n\n";
        openFilesContent += "[END OF SYSTEM INSTRUCTIONS]\n\n";

        return openFilesContent;
    }

    // Add these new methods
    private parseEditBlocks(message: string): EditBlock[] {
        const editBlocks: EditBlock[] = [];
        // Nouveau regex qui capture mieux les blocs d'édition
        const regex = /```khoj-edit(?::[^\n]+)?\n"((?:[^"]|\\"|\\n)*)", *"((?:[^"]|\\"|\\n)*)", *"((?:[^"]|\\"|\\n)*)"\n```/g;
        let match;

        while ((match = regex.exec(message)) !== null) {
            editBlocks.push({
                before: match[1].replace(/\\n/g, '\n').replace(/\\"/g, '"'),
                after: match[2].replace(/\\n/g, '\n').replace(/\\"/g, '"'),
                replacement: match[3].replace(/\\n/g, '\n').replace(/\\"/g, '"')
            });
        }

        return editBlocks;
    }

    private async applyEditBlocks(editBlocks: EditBlock[]) {
        // Store original content for each file in case we need to cancel
        const fileBackups = new Map<string, string>();

        // Get all open markdown files
        const files = this.app.workspace.getLeavesOfType('markdown')
            .map(leaf => (leaf.view as any)?.file)
            .filter(file => file && file.extension === 'md');

        for (const file of files) {
            try {
                // Read the file content
                const content = await this.app.vault.read(file);
                let newContent = content;
                let hasChanges = false;

                // Collect all edits for this file first
                interface PlannedEdit {
                    startIndex: number;
                    endIndex: number;
                    preview: string;
                }
                const plannedEdits: PlannedEdit[] = [];

                // First pass: collect all edits
                for (const block of editBlocks) {
                    // Handle special markers for file start and end
                    const before = block.before.replace('<file-start>', '');
                    const after = block.after.replace('<file-end>', '');

                    // Find the text to replace in original content
                    let startIndex = -1;
                    let endIndex = -1;

                    if (block.before.includes('<file-start>')) {
                        startIndex = 0;
                    } else {
                        startIndex = content.indexOf(before);
                    }

                    if (block.after.includes('<file-end>')) {
                        endIndex = content.length;
                    } else {
                        if (startIndex !== -1) {
                            endIndex = content.indexOf(after, startIndex);
                            if (endIndex !== -1) {
                                endIndex = endIndex + after.length; // Include the 'after' text in the replacement
                            }
                        }
                    }

                    if (startIndex !== -1 && endIndex !== -1) {
                        // Get the text to replace from original content
                        const textToReplace = content.substring(startIndex, endIndex);
                        const originalText = textToReplace;
                        const newText = block.replacement;

                        // Find common prefix and suffix between original and new text
                        let prefixLength = 0;
                        const minLength = Math.min(originalText.length, newText.length);
                        while (prefixLength < minLength && originalText[prefixLength] === newText[prefixLength]) {
                            prefixLength++;
                        }

                        let suffixLength = 0;
                        while (
                            suffixLength < minLength - prefixLength &&
                            originalText[originalText.length - 1 - suffixLength] === newText[newText.length - 1 - suffixLength]
                        ) {
                            suffixLength++;
                        }

                        // Extract the common and different parts
                        const commonPrefix = originalText.slice(0, prefixLength);
                        const commonSuffix = originalText.slice(originalText.length - suffixLength);
                        const originalDiff = originalText.slice(prefixLength, originalText.length - suffixLength);
                        const newDiff = newText.slice(prefixLength, newText.length - suffixLength);

                        // Format each line of the differences
                        const formatLines = (text: string, marker: string): string => {
                            if (!text) return '';
                            return text.split('\n')
                                .map(line => {
                                    line = line.trim();
                                    if (!line) return '';
                                    // Si la ligne commence par un tiret, on met le marqueur après le tiret
                                    if (line.startsWith('- ')) {
                                        return `- ${marker}${line.substring(2)}${marker}`;
                                    }
                                    return `${marker}${line}${marker}`;
                                })
                                .filter(line => line.length > 0)  // Supprimer les lignes vides
                                .join('\n');
                        };

                        // Create the preview with only the differences marked, line by line
                        const formattedPreview =
                            commonPrefix +
                            (originalDiff ? formatLines(originalDiff, '~~') : '') +
                            (newDiff ? formatLines(newDiff, '==') : '') +
                            commonSuffix;

                        plannedEdits.push({
                            startIndex,
                            endIndex,
                            preview: formattedPreview
                        });
                        hasChanges = true;
                    }
                }

                // Sort edits by start index in reverse order (to apply from end to start)
                plannedEdits.sort((a, b) => b.startIndex - a.startIndex);

                // Second pass: apply all edits
                for (const edit of plannedEdits) {
                    newContent =
                        newContent.substring(0, edit.startIndex) +
                        edit.preview +
                        newContent.substring(edit.endIndex);
                }

                // If any changes were made, backup the original content and save the changes
                if (hasChanges) {
                    fileBackups.set(file.path, content);
                    await this.app.vault.modify(file, newContent);
                }
            } catch (error) {
                console.error(`Error applying edits to ${file.path}:`, error);
            }
        }

        // Add confirmation buttons to the last message
        const chatBodyEl = this.contentEl.getElementsByClassName("khoj-chat-body")[0];
        const lastMessage = chatBodyEl.lastElementChild;

        if (lastMessage) {
            const buttonsContainer = lastMessage.createDiv({ cls: "edit-confirmation-buttons" });

            // Create Apply button
            const applyButton = buttonsContainer.createEl("button", {
                text: "✅ Apply",
                cls: ["edit-confirm-button", "edit-apply-button"],
            });

            // Create Cancel button
            const cancelButton = buttonsContainer.createEl("button", {
                text: "❌ Cancel",
                cls: ["edit-confirm-button", "edit-cancel-button"],
            });

            // Scroll to the buttons
            buttonsContainer.scrollIntoView({ behavior: "smooth", block: "center" });

            // Handle Apply button click
            applyButton.addEventListener("click", async () => {
                try {
                    for (const [filePath, originalContent] of fileBackups) {
                        const file = this.app.vault.getAbstractFileByPath(filePath);
                        if (file && file instanceof TFile) {
                            const currentContent = await this.app.vault.read(file);
                            let finalContent = currentContent;

                            // 1. Supprimer le texte entre ~~ (et non pas les lignes entières)
                            finalContent = finalContent.split('\n')
                                .map(line => {
                                    // Supprimer tous les textes entre ~~ dans la ligne
                                    return line.replace(/~~[^~]*~~/g, '').trim();
                                })
                                .filter(line => line.length > 0)  // Supprimer les lignes vides
                                .join('\n');

                            // 2. Enlever les == des lignes restantes
                            finalContent = finalContent.split('\n')
                                .map(line => line.replace(/==/g, ''))
                                .join('\n')
                                .replace(/\n{3,}/g, '\n\n'); // Remplacer les triples sauts de ligne ou plus par des doubles

                            await this.app.vault.modify(file, finalContent);
                        }
                    }

                    // Show success message
                    const successMessage = lastMessage.createDiv({ cls: "edit-status-message success" });
                    successMessage.textContent = "✅ Changes applied successfully";
                    setTimeout(() => successMessage.remove(), 3000);
                } catch (error) {
                    console.error("Error applying changes:", error);
                    // Show error message
                    const errorMessage = lastMessage.createDiv({ cls: "edit-status-message error" });
                    errorMessage.textContent = "❌ Error applying changes";
                    setTimeout(() => errorMessage.remove(), 3000);
                } finally {
                    // Remove the buttons after applying
                    buttonsContainer.remove();
                }
            });

            // Handle Cancel button click
            cancelButton.addEventListener("click", async () => {
                try {
                    // Restore original content for all modified files
                    for (const [filePath, originalContent] of fileBackups) {
                        const file = this.app.vault.getAbstractFileByPath(filePath);
                        if (file && file instanceof TFile) {
                            await this.app.vault.modify(file, originalContent);
                        }
                    }
                    // Show success message
                    const successMessage = lastMessage.createDiv({ cls: "edit-status-message success" });
                    successMessage.textContent = "✅ Changes cancelled successfully";
                    setTimeout(() => successMessage.remove(), 3000);
                } catch (error) {
                    console.error("Error cancelling changes:", error);
                    // Show error message
                    const errorMessage = lastMessage.createDiv({ cls: "edit-status-message error" });
                    errorMessage.textContent = "❌ Error cancelling changes";
                    setTimeout(() => errorMessage.remove(), 3000);
                } finally {
                    // Remove the buttons after canceling
                    buttonsContainer.remove();
                }
            });
        }
    }

    private convertCommandsToEmojis(message: string): string {
        const modeMatch = this.chatModes.find(mode => message.startsWith(mode.command));
        if (modeMatch) {
            return message.replace(modeMatch.command, modeMatch.emoji);
        }
        return message;
    }

    private async cancelPendingEdits() {
        const chatBodyEl = this.contentEl.getElementsByClassName("khoj-chat-body")[0];
        const lastMessage = chatBodyEl.lastElementChild;
        if (!lastMessage) return;

        // Check for edit confirmation buttons
        const buttonsContainer = lastMessage.querySelector(".edit-confirmation-buttons");
        if (!buttonsContainer) return;

        // Find and click the cancel button if it exists
        const cancelButton = buttonsContainer.querySelector(".edit-cancel-button");
        if (cancelButton instanceof HTMLElement) {
            cancelButton.click();
        }
    }

    // Add this new method to handle khoj-edit block transformation
    private transformKhojEditBlocks(message: string): string {
        return message.replace(/```khoj-edit(?:\n|\r\n?)([\s\S]*?)```/g, (match, content) => {
            return `<details class="khoj-edit-accordion">
                <summary>Khoj edited file</summary>
                <div class="khoj-edit-content">
                    <pre><code class="language-khoj-edit">${content}</code></pre>
                </div>
            </details>`;
        });
    }
}