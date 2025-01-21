function copyParentText(event, message=null) { //same
    const button = event.currentTarget;
    const textContent = message ?? button.parentNode.textContent.trim();
    navigator.clipboard.writeText(textContent).then(() => {
        button.firstChild.src = "./assets/icons/copy-button-success.svg";
        setTimeout(() => {
            button.firstChild.src = "./assets/icons/copy-button.svg";
        }, 1000);
    }).catch((error) => {
        console.error("Error copying text to clipboard:", error);
        const originalButtonText = button.innerHTML;
        button.innerHTML = "⛔️";
        setTimeout(() => {
            button.innerHTML = originalButtonText;
            button.firstChild.src = "./assets/icons/copy-button.svg";
        }, 2000);
    });
}

function createCopyParentText(message) { //same
    return function(event) {
        copyParentText(event, message);
    }
}
function formatDate(date) { //same
    // Format date in HH:MM, DD MMM YYYY format
    let time_string = date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
    let date_string = date.toLocaleString('en-IN', { year: 'numeric', month: 'short', day: '2-digit'}).replaceAll('-', ' ');
    return `${time_string}, ${date_string}`;
}

function generateReference(referenceJson, index) { //same
    let reference = referenceJson.hasOwnProperty("compiled") ? referenceJson.compiled : referenceJson;
    let referenceFile = referenceJson.hasOwnProperty("file") ? referenceJson.file : null;

    // Escape reference for HTML rendering
    let escaped_ref = reference.replaceAll('"', '&quot;');

    // Generate HTML for Chat Reference
    let short_ref = escaped_ref.slice(0, 100);
    short_ref = short_ref.length < escaped_ref.length ? short_ref + "..." : short_ref;
    let referenceButton = document.createElement('button');
    referenceButton.textContent = short_ref;
    referenceButton.id = `ref-${index}`;
    referenceButton.classList.add("reference-button");
    referenceButton.classList.add("collapsed");
    referenceButton.tabIndex = 0;

    // Add event listener to toggle full reference on click
    referenceButton.addEventListener('click', function() {
        if (this.classList.contains("collapsed")) {
            this.classList.remove("collapsed");
            this.classList.add("expanded");
            this.textContent = escaped_ref;
        } else {
            this.classList.add("collapsed");
            this.classList.remove("expanded");
            this.textContent = short_ref;
        }
    });

    return referenceButton;
}

function generateOnlineReference(reference, index) { //same

    // Generate HTML for Chat Reference
    let title = reference.title || reference.link;
    let link = reference.link;
    let snippet = reference.snippet;
    let question = reference.question;
    if (question) {
        question = `<b>Question:</b> ${question}<br><br>`;
    } else {
        question = "";
    }

    let linkElement = document.createElement('a');
    linkElement.setAttribute('href', link);
    linkElement.setAttribute('target', '_blank');
    linkElement.setAttribute('rel', 'noopener noreferrer');
    linkElement.classList.add("inline-chat-link");
    linkElement.classList.add("reference-link");
    linkElement.setAttribute('title', title);
    linkElement.textContent = title;

    let referenceButton = document.createElement('button');
    referenceButton.innerHTML = linkElement.outerHTML;
    referenceButton.id = `ref-${index}`;
    referenceButton.classList.add("reference-button");
    referenceButton.classList.add("collapsed");
    referenceButton.tabIndex = 0;

    // Add event listener to toggle full reference on click
    referenceButton.addEventListener('click', function() {
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

function renderMessage(message, by, dt=null, annotations=null, raw=false, renderType="append") { //same
    let message_time = formatDate(dt ?? new Date());
    let by_name =  by == "khoj" ? "🏮 Khoj" : "🤔 You";
    let formattedMessage = formatHTMLMessage(message, raw);

    // Create a new div for the chat message
    let chatMessage = document.createElement('div');
    chatMessage.className = `chat-message ${by}`;
    chatMessage.dataset.meta = `${by_name} at ${message_time}`;

    // Create a new div for the chat message text and append it to the chat message
    let chatMessageText = document.createElement('div');
    chatMessageText.className = `chat-message-text ${by}`;
    chatMessageText.appendChild(formattedMessage);
    chatMessage.appendChild(chatMessageText);

    // Append annotations div to the chat message
    if (annotations) {
        chatMessageText.appendChild(annotations);
    }

    // Append chat message div to chat body
    let chatBody = document.getElementById("chat-body");
    let body = document.body;
    if (renderType === "append") {
        chatBody.appendChild(chatMessage);
        // Scroll to bottom of chat-body element
        body.scrollTop = chatBody.scrollHeight;
    } else if (renderType === "prepend") {
        chatBody.insertBefore(chatMessage, chatBody.firstChild);
    } else if (renderType === "return") {
        return chatMessage;
    }

    let chatBodyWrapper = document.getElementById("chat-body");
    chatBodyWrapperHeight = chatBodyWrapper.clientHeight;
}

function processOnlineReferences(referenceSection, onlineContext) { //same
    let numOnlineReferences = 0;
    for (let subquery in onlineContext) {
        let onlineReference = onlineContext[subquery];
        if (onlineReference.organic && onlineReference.organic.length > 0) {
            numOnlineReferences += onlineReference.organic.length;
            for (let index in onlineReference.organic) {
                let reference = onlineReference.organic[index];
                let polishedReference = generateOnlineReference(reference, index);
                referenceSection.appendChild(polishedReference);
            }
        }

        if (onlineReference.knowledgeGraph && onlineReference.knowledgeGraph.length > 0) {
            numOnlineReferences += onlineReference.knowledgeGraph.length;
            for (let index in onlineReference.knowledgeGraph) {
                let reference = onlineReference.knowledgeGraph[index];
                let polishedReference = generateOnlineReference(reference, index);
                referenceSection.appendChild(polishedReference);
            }
        }

        if (onlineReference.peopleAlsoAsk && onlineReference.peopleAlsoAsk.length > 0) {
            numOnlineReferences += onlineReference.peopleAlsoAsk.length;
            for (let index in onlineReference.peopleAlsoAsk) {
                let reference = onlineReference.peopleAlsoAsk[index];
                let polishedReference = generateOnlineReference(reference, index);
                referenceSection.appendChild(polishedReference);
            }
        }

        if (onlineReference.webpages && onlineReference.webpages.length > 0) {
            numOnlineReferences += onlineReference.webpages.length;
            for (let index in onlineReference.webpages) {
                let reference = onlineReference.webpages[index];
                let polishedReference = generateOnlineReference(reference, index);
                referenceSection.appendChild(polishedReference);
            }
        }
    }

    return numOnlineReferences;
}

function renderMessageWithReference(message, by, context=null, dt=null, onlineContext=null, intentType=null, inferredQueries=null, conversationId=null, hostURL=null) {
    let chatEl;
    if (intentType?.includes("text-to-image")) {
        let imageMarkdown = generateImageMarkdown(message, intentType, inferredQueries);
        chatEl = renderMessage(imageMarkdown, by, dt, null, false, "return");
    } else if (intentType === "excalidraw") {
        let domain = hostURL ?? "https://app.khoj.dev/";

        if (!domain.endsWith("/")) domain += "/";

        let excalidrawMessage = `Hey, I'm not ready to show you diagrams yet here. But you can view it in the web app at ${domain}chat?conversationId=${conversationId}`;

        chatEl = renderMessage(excalidrawMessage, by, dt, null, false, "return");
    } else {
        chatEl = renderMessage(message, by, dt, null, false, "return");
    }

    // If no document or online context is provided, render the message as is
    if ((context == null || context?.length == 0)
        && (onlineContext == null || (onlineContext && Object.keys(onlineContext).length == 0))) {
        return chatEl;
    }

    // If document or online context is provided, render the message with its references
    let references = {};
    if (!!context) references["notes"] = context;
    if (!!onlineContext) references["online"] = onlineContext;
    let chatMessageEl = chatEl.getElementsByClassName("chat-message-text")[0];
    chatMessageEl.appendChild(createReferenceSection(references));

    return chatEl;
}

function generateImageMarkdown(message, intentType, inferredQueries=null) { //same
    let imageMarkdown;
    if (intentType === "text-to-image") {
        imageMarkdown = `![](data:image/png;base64,${message})`;
    } else if (intentType === "text-to-image2") {
        imageMarkdown = `![](${message})`;
    } else if (intentType === "text-to-image-v3") {
        imageMarkdown = `![](${message})`;
    }
    const inferredQuery = inferredQueries?.[0];
    if (inferredQuery) {
        imageMarkdown += `\n\n**Inferred Query**:\n\n${inferredQuery}`;
    }
    return imageMarkdown;
}

function formatHTMLMessage(message, raw=false, willReplace=true) { //same
    var md = window.markdownit();
    let newHTML = message;

    // Remove any text between <s>[INST] and </s> tags. These are spurious instructions for the AI chat model.
    newHTML = newHTML.replace(/<s>\[INST\].+(<\/s>)?/g, '');

    // Customize the rendering of images
    md.renderer.rules.image = function(tokens, idx, options, env, self) {
        let token = tokens[idx];

        // Add class="text-to-image" to images
        token.attrPush(['class', 'text-to-image']);

        // Use the default renderer to render image markdown format
        return self.renderToken(tokens, idx, options);
    };

    // Render markdown
    newHTML = raw ? newHTML : md.render(newHTML);
    // Sanitize the rendered markdown
    newHTML = DOMPurify.sanitize(newHTML);
    // Set rendered markdown to HTML DOM element
    let element = document.createElement('div');
    element.innerHTML = newHTML;
    element.className = "chat-message-text-response";

    // Add a copy button to each chat message
    if (willReplace === true) {
        let copyButton = document.createElement('button');
        copyButton.classList.add("copy-button");
        copyButton.title = "Copy Message";
        let copyIcon = document.createElement("img");
        copyIcon.id = "copy-icon";
        copyIcon.src = "./assets/icons/copy-button.svg";
        copyIcon.classList.add("copy-icon");
        copyButton.appendChild(copyIcon);
        copyButton.addEventListener('click', createCopyParentText(message));
        element.append(copyButton);
    }

    // Get any elements with a class that starts with "language"
    let codeBlockElements = element.querySelectorAll('[class^="language-"]');
    // For each element, add a parent div with the class "programmatic-output"
    codeBlockElements.forEach((codeElement, key) => {
        // Create the parent div
        let parentDiv = document.createElement('div');
        parentDiv.classList.add("programmatic-output");
        // Add the parent div before the code element
        codeElement.parentNode.insertBefore(parentDiv, codeElement);
        // Move the code element into the parent div
        parentDiv.appendChild(codeElement);
        // Add a copy button to each element
    });

    // Get all code elements that have no class.
    let codeElements = element.querySelectorAll('code:not([class])');
    codeElements.forEach((codeElement) => {
        // Add the class "chat-response" to each element
        codeElement.classList.add("chat-response");
    });

    let anchorElements = element.querySelectorAll('a');
    anchorElements.forEach((anchorElement) => {
        // Tag external links to open in separate window
        if (
            !anchorElement.href.startsWith("./") &&
            !anchorElement.href.startsWith("#") &&
            !anchorElement.href.startsWith("/")
        ) {
            anchorElement.setAttribute('target', '_blank');
            anchorElement.setAttribute('rel', 'noopener noreferrer');
        }

        // Add the class "inline-chat-link" to each element
        anchorElement.classList.add("inline-chat-link");
    });

    return element
}

function createReferenceSection(references, createLinkerSection=false) {
    let referenceSection = document.createElement('div');
    referenceSection.classList.add("reference-section");
    referenceSection.classList.add("collapsed");

    let numReferences = 0;

    if (references.hasOwnProperty("notes")) {
        numReferences += references["notes"].length;

        references["notes"].forEach((reference, index) => {
            let polishedReference = generateReference(reference, index);
            referenceSection.appendChild(polishedReference);
        });
    }
    if (references.hasOwnProperty("online")){
        numReferences += processOnlineReferences(referenceSection, references["online"]);
    }

    let referenceExpandButton = document.createElement('button');
    referenceExpandButton.id = "reference-expand-button";
    referenceExpandButton.classList.add("reference-expand-button");
    referenceExpandButton.innerHTML = numReferences == 1 ? "1 reference" : `${numReferences} references`;

    referenceExpandButton.addEventListener('click', function() {
        if (referenceSection.classList.contains("collapsed")) {
            referenceSection.classList.remove("collapsed");
            referenceSection.classList.add("expanded");
        } else {
            referenceSection.classList.add("collapsed");
            referenceSection.classList.remove("expanded");
        }
    });

    let referencesDiv = document.createElement('div');
    referencesDiv.classList.add("references");
    referencesDiv.appendChild(referenceExpandButton);
    if (createLinkerSection) {
        //add a linker button back to the desktop application
        let linkerButton = document.createElement('button');
        linkerButton.innerHTML = "Continue Conversation";
        linkerButton.id = "linker-button";
        linkerButton.addEventListener('click', function() {
            window.routeBackToMainWindowAPI.sendSignal();
        });
        referencesDiv.appendChild(linkerButton);
        console.log("shortcut window");
    }
    referencesDiv.appendChild(referenceSection);

    return referencesDiv;
}

function createLoadingEllipsis() {
    let loadingEllipsis = document.createElement("div");
    loadingEllipsis.classList.add("lds-ellipsis");

    let firstEllipsis = document.createElement("div");
    firstEllipsis.classList.add("lds-ellipsis-item");

    let secondEllipsis = document.createElement("div");
    secondEllipsis.classList.add("lds-ellipsis-item");

    let thirdEllipsis = document.createElement("div");
    thirdEllipsis.classList.add("lds-ellipsis-item");

    let fourthEllipsis = document.createElement("div");
    fourthEllipsis.classList.add("lds-ellipsis-item");

    loadingEllipsis.appendChild(firstEllipsis);
    loadingEllipsis.appendChild(secondEllipsis);
    loadingEllipsis.appendChild(thirdEllipsis);
    loadingEllipsis.appendChild(fourthEllipsis);

    return loadingEllipsis;
}

function handleStreamResponse(newResponseElement, rawResponse, rawQuery, loadingEllipsis, replace=true) {
    if (!newResponseElement) return;
    // Remove loading ellipsis if it exists
    if (newResponseElement.getElementsByClassName("lds-ellipsis").length > 0 && loadingEllipsis)
        newResponseElement.removeChild(loadingEllipsis);
    // Clear the response element if replace is true
    if (replace) newResponseElement.innerHTML = "";

    // Append response to the response element
    newResponseElement.appendChild(formatHTMLMessage(rawResponse, false, replace, rawQuery));

    // Append loading ellipsis if it exists
    if (!replace && loadingEllipsis) newResponseElement.appendChild(loadingEllipsis);
    // Scroll to bottom of chat view
    document.getElementById("chat-body").scrollTop = document.getElementById("chat-body").scrollHeight;
}

function handleImageResponse(imageJson, rawResponse) {
    if (imageJson.image) {
        const inferredQuery = imageJson.inferredQueries?.[0] ?? "generated image";

        // If response has image field, response is a generated image.
        if (imageJson.intentType === "text-to-image") {
            rawResponse += `![generated_image](data:image/png;base64,${imageJson.image})`;
        } else if (imageJson.intentType === "text-to-image2") {
            rawResponse += `![generated_image](${imageJson.image})`;
        } else if (imageJson.intentType === "text-to-image-v3") {
            rawResponse = `![](${imageJson.image})`;
        } else if (imageJson.intentType === "excalidraw") {
            const redirectMessage = `Hey, I'm not ready to show you diagrams yet here. But you can view it in the web app`;
            rawResponse += redirectMessage;
        }

        if (inferredQuery) {
            rawResponse += `\n\n**Inferred Query**:\n\n${inferredQuery}`;
        }
    }

    // If response has detail field, response is an error message.
    if (imageJson.detail) rawResponse += imageJson.detail;

    return rawResponse;
}

function finalizeChatBodyResponse(references, newResponseElement) {
    if (!!newResponseElement && references != null && Object.keys(references).length > 0) {
        newResponseElement.appendChild(createReferenceSection(references));
    }
    document.getElementById("chat-body").scrollTop = document.getElementById("chat-body").scrollHeight;
    document.getElementById("chat-input")?.removeAttribute("disabled");
}

function convertMessageChunkToJson(rawChunk) {
    // Split the chunk into lines
    if (rawChunk?.startsWith("{") && rawChunk?.endsWith("}")) {
        try {
            let jsonChunk = JSON.parse(rawChunk);
            if (!jsonChunk.type)
                jsonChunk = {type: 'message', data: jsonChunk};
            return jsonChunk;
        } catch (e) {
            return {type: 'message', data: rawChunk};
        }
    } else if (rawChunk.length > 0) {
        return {type: 'message', data: rawChunk};
    }
}

function processMessageChunk(rawChunk) {
    const chunk = convertMessageChunkToJson(rawChunk);
    console.debug("Chunk:", chunk);
    if (!chunk || !chunk.type) return;
    if (chunk.type ==='status') {
        console.log(`status: ${chunk.data}`);
        const statusMessage = chunk.data;
        handleStreamResponse(chatMessageState.newResponseTextEl, statusMessage, chatMessageState.rawQuery, chatMessageState.loadingEllipsis, false);
    } else if (chunk.type === 'start_llm_response') {
        console.log("Started streaming", new Date());
    } else if (chunk.type === 'end_llm_response') {
        console.log("Stopped streaming", new Date());

        // Automatically respond with voice if the subscribed user has sent voice message
        if (chatMessageState.isVoice && "{{ is_active }}" == "True")
            textToSpeech(chatMessageState.rawResponse);

        // Append any references after all the data has been streamed
        finalizeChatBodyResponse(chatMessageState.references, chatMessageState.newResponseTextEl);

        const liveQuery = chatMessageState.rawQuery;
        // Reset variables
        chatMessageState = {
            newResponseTextEl: null,
            newResponseEl: null,
            loadingEllipsis: null,
            references: {},
            rawResponse: "",
            rawQuery: liveQuery,
            isVoice: false,
        }
    } else if (chunk.type === "references") {
        chatMessageState.references = {"notes": chunk.data.context, "online": chunk.data.onlineContext};
    } else if (chunk.type === 'message') {
        const chunkData = chunk.data;
        if (typeof chunkData === 'object' && chunkData !== null) {
            // If chunkData is already a JSON object
            handleJsonResponse(chunkData);
        } else if (typeof chunkData  === 'string' && chunkData.trim()?.startsWith("{") && chunkData.trim()?.endsWith("}")) {
            // Try process chunk data as if it is a JSON object
            try {
                const jsonData = JSON.parse(chunkData.trim());
                handleJsonResponse(jsonData);
            } catch (e) {
                chatMessageState.rawResponse += chunkData;
                handleStreamResponse(chatMessageState.newResponseTextEl, chatMessageState.rawResponse, chatMessageState.rawQuery, chatMessageState.loadingEllipsis);
            }
        } else {
            chatMessageState.rawResponse += chunkData;
            handleStreamResponse(chatMessageState.newResponseTextEl, chatMessageState.rawResponse, chatMessageState.rawQuery, chatMessageState.loadingEllipsis);
        }
    }
}

function handleJsonResponse(jsonData) {
    if (jsonData.image || jsonData.detail) {
        chatMessageState.rawResponse = handleImageResponse(jsonData, chatMessageState.rawResponse);
    } else if (jsonData.response) {
        chatMessageState.rawResponse = jsonData.response;
    }

    if (chatMessageState.newResponseTextEl) {
        chatMessageState.newResponseTextEl.innerHTML = "";
        chatMessageState.newResponseTextEl.appendChild(formatHTMLMessage(chatMessageState.rawResponse));
    }
}

async function readChatStream(response) {
    if (!response.body) return;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const eventDelimiter = '␃🔚␗';
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        // If the stream is done
        if (done) {
            // Process the last chunk
            processMessageChunk(buffer);
            buffer = '';
            break;
        }

        // Read chunk from stream and append it to the buffer
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
            if (event) processMessageChunk(event);
        }
    }
}
