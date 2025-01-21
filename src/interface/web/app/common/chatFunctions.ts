import { AttachedFileText } from "../components/chatInputArea/chatInputArea";
import {
    CodeContext,
    Context,
    OnlineContext,
    StreamMessage,
} from "../components/chatMessage/chatMessage";

export interface RawReferenceData {
    context?: Context[];
    onlineContext?: OnlineContext;
    codeContext?: CodeContext;
}

export interface MessageMetadata {
    conversationId: string;
    turnId: string;
}

export interface GeneratedAssetsData {
    images: string[];
    mermaidjsDiagram: string;
    files: AttachedFileText[];
}

export interface ResponseWithIntent {
    intentType: string;
    response: string;
    inferredQueries?: string[];
}

interface MessageChunk {
    type: string;
    data: string | object;
}

export function convertMessageChunkToJson(chunk: string): MessageChunk {
    if (chunk.startsWith("{") && chunk.endsWith("}")) {
        try {
            const jsonChunk = JSON.parse(chunk);
            if (!jsonChunk.type) {
                return {
                    type: "message",
                    data: jsonChunk,
                };
            }
            return jsonChunk;
        } catch (error) {
            return {
                type: "message",
                data: chunk,
            };
        }
    } else if (chunk.length > 0) {
        return {
            type: "message",
            data: chunk,
        };
    } else {
        return {
            type: "message",
            data: "",
        };
    }
}

function handleJsonResponse(chunkData: any) {
    const jsonData = chunkData as any;
    if (jsonData.image || jsonData.detail) {
        let responseWithIntent = handleImageResponse(chunkData, true);
        return responseWithIntent;
    } else if (jsonData.response) {
        return {
            response: jsonData.response,
            intentType: "",
            inferredQueries: [],
        };
    } else {
        throw new Error("Invalid JSON response");
    }
}

export function processMessageChunk(
    rawChunk: string,
    currentMessage: StreamMessage,
    context: Context[] = [],
    onlineContext: OnlineContext = {},
    codeContext: CodeContext = {},
): { context: Context[]; onlineContext: OnlineContext; codeContext: CodeContext } {
    const chunk = convertMessageChunkToJson(rawChunk);

    if (!currentMessage || !chunk || !chunk.type) return { context, onlineContext, codeContext };

    console.log(`chunk type: ${chunk.type}`);

    if (chunk.type === "status") {
        console.log(`status: ${chunk.data}`);
        const statusMessage = chunk.data as string;
        currentMessage.trainOfThought.push(statusMessage);
    } else if (chunk.type === "references") {
        const references = chunk.data as RawReferenceData;

        if (references.context) context = references.context;
        if (references.onlineContext) onlineContext = references.onlineContext;
        if (references.codeContext) codeContext = references.codeContext;
        return { context, onlineContext, codeContext };
    } else if (chunk.type === "metadata") {
        const messageMetadata = chunk.data as MessageMetadata;
        currentMessage.turnId = messageMetadata.turnId;
    } else if (chunk.type === "generated_assets") {
        const generatedAssets = chunk.data as GeneratedAssetsData;

        if (generatedAssets.images) {
            currentMessage.generatedImages = generatedAssets.images;
        }

        if (generatedAssets.mermaidjsDiagram) {
            currentMessage.generatedMermaidjsDiagram = generatedAssets.mermaidjsDiagram;
        }

        if (generatedAssets.files) {
            currentMessage.generatedFiles = generatedAssets.files;
        }
    } else if (chunk.type === "message") {
        const chunkData = chunk.data;
        // Here, handle if the response is a JSON response with an image, but the intentType is excalidraw
        if (chunkData !== null && typeof chunkData === "object") {
            let responseWithIntent = handleJsonResponse(chunkData);

            if (responseWithIntent.intentType && responseWithIntent.intentType === "excalidraw") {
                currentMessage.rawResponse = responseWithIntent.response;
            } else {
                currentMessage.rawResponse += responseWithIntent.response;
            }

            currentMessage.intentType = responseWithIntent.intentType;
            currentMessage.inferredQueries = responseWithIntent.inferredQueries;
        } else if (
            typeof chunkData === "string" &&
            chunkData.trim()?.startsWith("{") &&
            chunkData.trim()?.endsWith("}")
        ) {
            try {
                const jsonData = JSON.parse(chunkData.trim());
                let responseWithIntent = handleJsonResponse(jsonData);
                currentMessage.rawResponse += responseWithIntent.response;
                currentMessage.intentType = responseWithIntent.intentType;
                currentMessage.inferredQueries = responseWithIntent.inferredQueries;
            } catch (e) {
                currentMessage.rawResponse += JSON.stringify(chunkData);
            }
        } else {
            currentMessage.rawResponse += chunkData;
        }
    } else if (chunk.type === "start_llm_response") {
        console.log(`Started streaming: ${new Date()}`);
    } else if (chunk.type === "end_llm_response") {
        console.log(`Completed streaming: ${new Date()}`);
    } else if (chunk.type === "end_response") {
        // Append any references after all the data has been streamed
        if (codeContext) currentMessage.codeContext = codeContext;
        if (onlineContext) currentMessage.onlineContext = onlineContext;
        if (context) currentMessage.context = context;

        // Mark current message streaming as completed
        currentMessage.completed = true;
    }
    return { context, onlineContext, codeContext };
}

export function handleImageResponse(imageJson: any, liveStream: boolean): ResponseWithIntent {
    let rawResponse = "";

    if (imageJson.image) {
        // If response has image field, response may be a generated image
        rawResponse = imageJson.image;
    }

    let responseWithIntent: ResponseWithIntent = {
        intentType: imageJson.intentType,
        response: rawResponse,
        inferredQueries: imageJson.inferredQueries,
    };

    if (imageJson.detail) {
        // The detail field contains the improved image prompt
        rawResponse += imageJson.detail;
    }

    return responseWithIntent;
}

export function renderCodeGenImageInline(message: string, codeContext: CodeContext) {
    if (!codeContext) return message;

    Object.values(codeContext).forEach((contextData) => {
        contextData.results.output_files?.forEach((file) => {
            const regex = new RegExp(`!?\\[.*?\\]\\(.*${file.filename}\\)`, "g");
            if (file.filename.match(/\.(png|jpg|jpeg)$/i)) {
                const replacement = `![${file.filename}](data:image/${file.filename.split(".").pop()};base64,${file.b64_data})`;
                message = message.replace(regex, replacement);
            } else if (file.filename.match(/\.(txt|org|md|csv|json)$/i)) {
                // render output files generated by codegen as downloadable links
                const replacement = `![${file.filename}](data:text/plain;base64,${file.b64_data})`;
                message = message.replace(regex, replacement);
            }
        });
    });

    return message;
}

export function modifyFileFilterForConversation(
    conversationId: string | null,
    filenames: string[],
    setAddedFiles: (files: string[]) => void,
    mode: "add" | "remove",
) {
    if (!conversationId) {
        console.error("No conversation ID provided");
        return;
    }

    const method = mode === "add" ? "POST" : "DELETE";

    const body = {
        conversation_id: conversationId,
        filenames: filenames,
    };
    const addUrl = `/api/chat/conversation/file-filters/bulk`;

    fetch(addUrl, {
        method: method,
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
    })
        .then((res) => {
            if (!res.ok)
                throw new Error(`Failed to call API at ${addUrl} with error ${res.statusText}`);
            return res.json();
        })
        .then((data) => {
            setAddedFiles(data);
        })
        .catch((err) => {
            console.error(err);
            return;
        });
}

export async function createNewConversation(slug: string) {
    try {
        const response = await fetch(`/api/chat/sessions?client=web&agent_slug=${slug}`, {
            method: "POST",
        });
        if (!response.ok)
            throw new Error(`Failed to fetch chat sessions with status: ${response.status}`);
        const data = await response.json();
        const conversationID = data.conversation_id;
        if (!conversationID) throw new Error("Conversation ID not found in response");
        return conversationID;
    } catch (error) {
        console.error("Error creating new conversation:", error);
        throw error;
    }
}

export async function packageFilesForUpload(files: FileList): Promise<FormData> {
    const formData = new FormData();

    const fileReadPromises = Array.from(files).map((file) => {
        return new Promise<void>((resolve, reject) => {
            let reader = new FileReader();
            reader.onload = function (event) {
                if (event.target === null) {
                    reject();
                    return;
                }

                let fileContents = event.target.result;
                let fileType = file.type;
                let fileName = file.name;
                if (fileType === "") {
                    let fileExtension = fileName.split(".").pop();
                    if (fileExtension === "org") {
                        fileType = "text/org";
                    } else if (fileExtension === "md") {
                        fileType = "text/markdown";
                    } else if (fileExtension === "txt") {
                        fileType = "text/plain";
                    } else if (fileExtension === "html") {
                        fileType = "text/html";
                    } else if (fileExtension === "pdf") {
                        fileType = "application/pdf";
                    } else if (fileExtension === "docx") {
                        fileType =
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
                    } else {
                        // Skip this file if its type is not supported
                        resolve();
                        return;
                    }
                }

                if (fileContents === null) {
                    reject();
                    return;
                }

                let fileObj = new Blob([fileContents], { type: fileType });
                formData.append("files", fileObj, file.name);
                resolve();
            };
            reader.onerror = reject;
            reader.readAsArrayBuffer(file);
        });
    });

    await Promise.all(fileReadPromises);
    return formData;
}

export function generateNewTitle(conversationId: string, setTitle: (title: string) => void) {
    fetch(`/api/chat/title?conversation_id=${conversationId}`, {
        method: "POST",
    })
        .then((res) => {
            if (!res.ok) throw new Error(`Failed to call API with error ${res.statusText}`);
            return res.json();
        })
        .then((data) => {
            setTitle(data.title);
        })
        .catch((err) => {
            console.error(err);
            return;
        });
}

export function uploadDataForIndexing(
    files: FileList,
    setWarning: (warning: string) => void,
    setUploading: (uploading: boolean) => void,
    setError: (error: string) => void,
    setUploadedFiles?: (files: string[]) => void,
    conversationId?: string | null,
) {
    const allowedExtensions = [
        "text/org",
        "text/markdown",
        "text/plain",
        "text/html",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ];
    const allowedFileEndings = ["org", "md", "txt", "html", "pdf", "docx"];
    const badFiles: string[] = [];
    const goodFiles: File[] = [];

    const uploadedFiles: string[] = [];

    for (let file of files) {
        const fileEnding = file.name.split(".").pop();
        if (!file || !file.name || !fileEnding) {
            if (file) {
                badFiles.push(file.name);
            }
        } else if (
            !allowedExtensions.includes(file.type) &&
            !allowedFileEndings.includes(fileEnding.toLowerCase())
        ) {
            badFiles.push(file.name);
        } else {
            goodFiles.push(file);
        }
    }

    if (goodFiles.length === 0) {
        setWarning("No supported files found");
        return;
    }

    if (badFiles.length > 0) {
        setWarning("The following files are not supported yet:\n" + badFiles.join("\n"));
    }

    const formData = new FormData();

    // Create an array of Promises for file reading
    const fileReadPromises = Array.from(goodFiles).map((file) => {
        return new Promise<void>((resolve, reject) => {
            let reader = new FileReader();
            reader.onload = function (event) {
                if (event.target === null) {
                    reject();
                    return;
                }

                let fileContents = event.target.result;
                let fileType = file.type;
                let fileName = file.name;
                if (fileType === "") {
                    let fileExtension = fileName.split(".").pop();
                    if (fileExtension === "org") {
                        fileType = "text/org";
                    } else if (fileExtension === "md") {
                        fileType = "text/markdown";
                    } else if (fileExtension === "txt") {
                        fileType = "text/plain";
                    } else if (fileExtension === "html") {
                        fileType = "text/html";
                    } else if (fileExtension === "pdf") {
                        fileType = "application/pdf";
                    } else {
                        // Skip this file if its type is not supported
                        resolve();
                        return;
                    }
                }

                if (fileContents === null) {
                    reject();
                    return;
                }

                let fileObj = new Blob([fileContents], { type: fileType });
                formData.append("files", fileObj, file.name);
                resolve();
            };
            reader.onerror = reject;
            reader.readAsArrayBuffer(file);
        });
    });

    setUploading(true);

    // Wait for all files to be read before making the fetch request
    Promise.all(fileReadPromises)
        .then(() => {
            return fetch("/api/content?client=web", {
                method: "PATCH",
                body: formData,
            });
        })
        .then((data) => {
            for (let file of goodFiles) {
                uploadedFiles.push(file.name);
                if (conversationId && setUploadedFiles) {
                    modifyFileFilterForConversation(
                        conversationId,
                        [file.name],
                        setUploadedFiles,
                        "add",
                    );
                }
            }
            if (setUploadedFiles) setUploadedFiles(uploadedFiles);
        })
        .catch((error) => {
            console.log(error);
            setError(`Error uploading file: ${error}`);
        })
        .finally(() => {
            setUploading(false);
        });
}
