"use client";

import { Input } from "@/components/ui/input";

import { useEffect, useRef, useState } from "react";
import styles from "./search.module.css";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
    Card,
    CardContent,
    CardDescription,
    CardFooter,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import {
    ArrowLeft,
    ArrowRight,
    FileDashed,
    GithubLogo,
    Lightbulb,
    LinkSimple,
    MagnifyingGlass,
    NoteBlank,
    NotionLogo,
    Trash,
    DotsThreeVertical,
    Waveform,
    Plus,
    Download,
    Brain,
    Check,
    BoxArrowDown,
    Funnel,
} from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import { formatDateTime, useIsMobileWidth } from "../common/utils";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "../components/appSidebar/appSidebar";
import { Separator } from "@/components/ui/separator";
import { KhojLogoType } from "../components/logo/khojLogo";
import { InlineLoading } from "../components/loading/loading";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
    Pagination,
    PaginationContent,
    PaginationEllipsis,
    PaginationItem,
    PaginationLink,
    PaginationNext,
    PaginationPrevious,
} from "@/components/ui/pagination";

import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command"
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover"

import { uploadDataForIndexing } from "../common/chatFunctions";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
interface AdditionalData {
    file: string;
    source: string;
    compiled: string;
    heading: string;
}

interface SearchResult {
    type: string;
    additional: AdditionalData;
    entry: string;
    score: number;
    "corpus-id": string;
}

interface FileObject {
    file_name: string;
    raw_text: string;
    updated_at: string;
}

function getNoteTypeIcon(source: string) {
    if (source === "notion") {
        return <NotionLogo className="text-muted-foreground" />;
    }
    if (source === "github") {
        return <GithubLogo className="text-muted-foreground" />;
    }
    return <NoteBlank className="text-muted-foreground" />;
}

interface FileCardProps {
    file: FileObject;
    index: number;
    setSelectedFile: (file: string) => void;
    setSelectedFileFullText: (file: string) => void;
    handleDownload: (fileName: string, content: string) => void;
    handleDelete: (fileName: string) => void;
    isDeleting: boolean;
    selectedFileFullText: string | null;
}

function FileCard({ file, setSelectedFile, setSelectedFileFullText, handleDownload, handleDelete, isDeleting, selectedFileFullText }: FileCardProps) {
    return (
        <Card
            className="animate-fade-in-up bg-secondary h-52"
        >
            <CardHeader className="p-2">
                <CardTitle
                    className="flex items-center gap-2 justify-between"
                    title={file.file_name}
                >
                    <Dialog>
                        <DialogTrigger asChild>
                            <div
                                className="text-sm font-medium truncate hover:text-clip hover:whitespace-normal cursor-pointer"
                                onClick={() => {
                                    setSelectedFileFullText(
                                        '',
                                    );
                                    setSelectedFile(
                                        file.file_name,
                                    );
                                }}
                            >
                                {file.file_name
                                    .split("/")
                                    .pop()}
                            </div>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>
                                    <div className="flex items-center gap-2">
                                        {file.file_name
                                            .split("/")
                                            .pop()}
                                        <Button
                                            variant={
                                                "ghost"
                                            }
                                            title="Download as plaintext"
                                            onClick={() =>
                                                handleDownload(
                                                    file.file_name,
                                                    file.raw_text,
                                                )
                                            }
                                        >
                                            <Download className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </DialogTitle>
                            </DialogHeader>
                            <ScrollArea className="h-[50vh]">
                                <p className="whitespace-pre-wrap break-words text-sm font-normal">
                                    {!selectedFileFullText && (
                                        <InlineLoading
                                            className="mt-4"
                                            message={
                                                "Loading"
                                            }
                                            iconClassName="h-5 w-5"
                                        />
                                    )}
                                    {
                                        selectedFileFullText
                                    }
                                </p>
                            </ScrollArea>
                        </DialogContent>
                    </Dialog>
                    <DropdownMenu>
                        <DropdownMenuTrigger>
                            <Button variant={"ghost"}>
                                <DotsThreeVertical className="h-4 w-4" />
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent className="flex flex-col gap-0 w-fit">
                            <DropdownMenuItem className="p-0">
                                <Button
                                    variant={"ghost"}
                                    className="flex items-center gap-2 p-1 text-sm"
                                    onClick={() => {
                                        handleDelete(
                                            file.file_name,
                                        );
                                    }}
                                >
                                    <Trash className="h-4 w-4" />
                                    <span className="text-xs">
                                        {isDeleting
                                            ? "Deleting..."
                                            : "Delete"}
                                    </span>
                                </Button>
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </CardTitle>
            </CardHeader>
            <CardContent className="p-2">
                <ScrollArea className="h-24 bg-background rounded-lg">
                    <p className="whitespace-pre-wrap break-words text-sm font-normal text-muted-foreground p-2 h-full">
                        {file.raw_text.slice(0, 100)}...
                    </p>
                </ScrollArea>
            </CardContent>
            <CardFooter className="flex justify-end gap-2 p-2">
                <div className="text-muted-foreground text-xs">
                    {formatDateTime(file.updated_at)}
                </div>
            </CardFooter>
        </Card>
    )
}

interface NoteResultProps {
    note: SearchResult;
    setFocusSearchResult: (note: SearchResult) => void;
}

function Note(props: NoteResultProps) {
    const note = props.note;
    const isFileNameURL = (note.additional.file || "").startsWith("http");
    const fileName = isFileNameURL
        ? note.additional.heading
        : note.additional.file.split("/").pop();

    return (
        <Card className="bg-secondary h-full shadow-sm rounded-lg border border-muted mb-4 animate-fade-in-up">
            <CardHeader>
                <CardTitle className="inline-flex gap-2">
                    {getNoteTypeIcon(note.additional.source)}
                    {fileName}
                </CardTitle>
            </CardHeader>
            <CardContent>
                <div className="line-clamp-4 text-muted-foreground">{note.entry}</div>
                <Button
                    onClick={() => props.setFocusSearchResult(note)}
                    variant={"ghost"}
                    className="p-0 mt-2 text-orange-400 hover:bg-inherit"
                >
                    See content
                    <ArrowRight className="inline ml-2" />
                </Button>
            </CardContent>
            {isFileNameURL && (
                <CardFooter>
                    <a
                        href={note.additional.file}
                        target="_blank"
                        className="underline text-sm bg-muted p-1 rounded-lg text-muted-foreground"
                    >
                        <LinkSimple className="inline m-2" />
                        {note.additional.file}
                    </a>
                </CardFooter>
            )}
        </Card>
    );
}

function focusNote(note: SearchResult) {
    const isFileNameURL = (note.additional.file || "").startsWith("http");
    const fileName = isFileNameURL
        ? note.additional.heading
        : note.additional.file.split("/").pop();

    return (
        <Card className="bg-secondary h-full shadow-sm rounded-lg border border-muted mb-4 animate-fade-in-up">
            <CardHeader>
                <CardTitle>{fileName}</CardTitle>
            </CardHeader>
            {isFileNameURL && (
                <CardFooter>
                    <a
                        href={note.additional.file}
                        target="_blank"
                        className="underline text-sm bg-muted p-3 rounded-lg text-muted-foreground flex items-center gap-2"
                    >
                        <LinkSimple className="inline" />
                        {note.additional.file}
                    </a>
                </CardFooter>
            )}
            <CardContent>
                <div className="text-m">{note.entry}</div>
            </CardContent>
        </Card>
    );
}

const UploadFiles: React.FC<{
    setUploadedFiles: (files: string[]) => void;
}> = ({ setUploadedFiles }) => {
    const [isDragAndDropping, setIsDragAndDropping] = useState(false);

    const [warning, setWarning] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const [progressValue, setProgressValue] = useState(0);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!uploading) {
            setProgressValue(0);
            if (!warning && !error) {
                // Force close the dialog by simulating a click on the escape key
                const event = new KeyboardEvent("keydown", { key: "Escape" });
                document.dispatchEvent(event);
            }
        }

        if (uploading) {
            const interval = setInterval(() => {
                setProgressValue((prev) => {
                    const increment = Math.floor(Math.random() * 5) + 1; // Generates a random number between 1 and 5
                    const nextValue = prev + increment;
                    return nextValue < 100 ? nextValue : 100; // Ensures progress does not exceed 100
                });
            }, 800);
            return () => clearInterval(interval);
        }
    }, [uploading]);

    function handleDragOver(event: React.DragEvent<HTMLDivElement>) {
        event.preventDefault();
        setIsDragAndDropping(true);
    }

    function handleDragLeave(event: React.DragEvent<HTMLDivElement>) {
        event.preventDefault();
        setIsDragAndDropping(false);
    }

    function handleDragAndDropFiles(event: React.DragEvent<HTMLDivElement>) {
        event.preventDefault();
        setIsDragAndDropping(false);

        if (!event.dataTransfer.files) return;

        uploadFiles(event.dataTransfer.files);
    }

    function openFileInput() {
        if (fileInputRef && fileInputRef.current) {
            fileInputRef.current.click();
        }
    }

    function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
        if (!event.target.files) return;

        uploadFiles(event.target.files);
    }

    function uploadFiles(files: FileList) {
        uploadDataForIndexing(files, setWarning, setUploading, setError, setUploadedFiles);
    }

    return (
        <Dialog>
            <DialogTrigger asChild>
                <Button variant={"secondary"} className="mt-4">
                    <Brain className="h-4 w-4 mr-2" />
                    Add Documents
                </Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Build Your Knowledge Base</DialogTitle>
                    <DialogDescription>
                        Add context for your Khoj knowledge base.
                        Quickly search and get personalized answers from your documents.
                    </DialogDescription>
                </DialogHeader>
                <div
                    className={`flex flex-col h-full`}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDragAndDropFiles}
                    onClick={openFileInput}
                >
                    <input
                        type="file"
                        multiple
                        ref={fileInputRef}
                        style={{ display: "none" }}
                        onChange={handleFileChange}
                    />
                    <div className="flex-none p-4">
                        {uploading && (
                            <Progress
                                indicatorColor="bg-slate-500"
                                className="w-full h-2 rounded-full"
                                value={progressValue}
                            />
                        )}
                    </div>
                    {warning && (
                        <div className="flex-none p-4 border-b rounded-lg">
                            <div className="flex items-center gap-2">
                                <Lightbulb className="h-6 w-6" />
                                <span>{warning}</span>
                            </div>
                            <Button
                                onClick={() => setWarning(null)}
                                className="mt-2"
                                variant={"ghost"}
                            >
                                Dismiss
                            </Button>
                        </div>
                    )}
                    {error && (
                        <div className="flex-none p-4 border-b rounded-lg">
                            <div className="flex items-center gap-2">
                                <Lightbulb className="h-6 w-6" />
                                <span>{error}</span>
                            </div>
                            <Button
                                onClick={() => setError(null)}
                                className="mt-2"
                                variant={"ghost"}
                            >
                                Dismiss
                            </Button>
                        </div>
                    )}
                    <div
                        className={`flex-none p-4 border-b ${isDragAndDropping ? "animate-pulse border-blue-500 bg-blue-500 bg-opacity-25" : "bg-secondary"} rounded-lg`}
                    >
                        <div className="flex items-center justify-center w-full h-32 border-2 border-dashed border-gray-300 rounded-lg">
                            {isDragAndDropping ? (
                                <div className="flex items-center justify-center w-full h-full">
                                    <Waveform className="h-6 w-6 mr-2" />
                                    <span>Drop files to upload</span>
                                </div>
                            ) : (
                                <div className="flex items-center justify-center w-full h-full">
                                    <Plus className="h-6 w-6 mr-2" />
                                    <span>Drag and drop files here</span>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
};

interface FileFilterComboBoxProps {
    allFiles: string[];
    onChooseFile: (file: string) => void;
    isMobileWidth: boolean;
    explicitFile?: string;
}

function FileFilterComboBox(props: FileFilterComboBoxProps) {
    const [open, setOpen] = useState(false)
    const [value, setValue] = useState(props.explicitFile || "")

    useEffect(() => {
        if (props.explicitFile) {
            setValue(props.explicitFile);
        }
    }, [props.explicitFile]);

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    role="combobox"
                    aria-expanded={open}
                    className={`justify-between" ${props.isMobileWidth ? "w-full" : "w-[200px]"}`}
                >
                    {value
                        ? (
                            props.isMobileWidth ?
                                "✔️"
                                : "Selected"
                        )
                        : (
                            props.isMobileWidth ?
                                " "
                                : "Select file"
                        )
                    }
                    <Funnel className="opacity-50" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[200px] p-0">
                <Command>
                    <CommandInput placeholder="Search framework..." />
                    <CommandList>
                        <CommandEmpty>No framework found.</CommandEmpty>
                        <CommandGroup>
                            {props.allFiles.map((file) => (
                                <CommandItem
                                    key={file}
                                    value={file}
                                    onSelect={(currentValue) => {
                                        setValue(currentValue === value ? "" : currentValue)
                                        setOpen(false)
                                        props.onChooseFile(currentValue)
                                    }}
                                >
                                    {file}
                                    <Check
                                        className={cn(
                                            "ml-auto",
                                            value === file ? "opacity-100" : "opacity-0"
                                        )}
                                    />
                                </CommandItem>
                            ))}
                        </CommandGroup>
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>
    )
}

export default function Search() {
    const [searchQuery, setSearchQuery] = useState("");
    const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
    const [searchResultsLoading, setSearchResultsLoading] = useState(false);
    const searchInputRef = useRef<HTMLInputElement>(null);
    const [focusSearchResult, setFocusSearchResult] = useState<SearchResult | null>(null);
    const [files, setFiles] = useState<FileObject[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [fileObjectsLoading, setFileObjectsLoading] = useState(true);
    const [allFiles, setAllFiles] = useState<string[]>([]);
    const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [selectedFileFilter, setSelectedFileFilter] = useState<string | undefined>(undefined);
    const [selectedFileFullText, setSelectedFileFullText] = useState<string | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);
    const [uploadedFiles, setUploadedFiles] = useState<string[]>([]);
    const [pageNumber, setPageNumber] = useState(0);
    const [numPages, setNumPages] = useState(1);

    const { toast } = useToast();

    const isMobileWidth = useIsMobileWidth();

    useEffect(() => {
        // Load all files once on page load
        fetch('/api/content/computer', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            },
        })
            .then(response => response.json())
            .then(data => {
                setAllFiles(data);
            })
            .catch(error => {
                console.error('Error loading files:', error);
            });
    }, []);

    useEffect(() => {
        // Replace the file: filter with the selected suggestion
        const fileFilterMatch = searchQuery.match(/file:([^"\s]*|"[^"]*")?/);

        if (fileFilterMatch) {
            const extractedFileFilter = fileFilterMatch[1];
            // Strip the double quotes
            const extractedFileFilterValue = extractedFileFilter?.replace(/"/g, "");

            if (extractedFileFilterValue) {
                setSelectedFileFilter(extractedFileFilterValue);
            }
        }
    }, [searchQuery]);


    function handleSearchInputChange(value: string) {
        setSearchQuery(value);

        if (!value.trim()) {
            setSearchResults(null);
            return;
        }

        // Clear previous search timeout
        if (searchTimeoutRef.current) {
            clearTimeout(searchTimeoutRef.current);
        }

        // Debounce search
        if (value.trim()) {
            searchTimeoutRef.current = setTimeout(() => {
                search();
            }, 750);
        }
    }

    function applySuggestion(suggestion: string) {
        // Append the file: filter with the selected suggestion
        const newQuery = `file:"${suggestion}" ${searchQuery}`;
        setSearchQuery(newQuery);
        searchInputRef.current?.focus();
        search();
    }

    function search() {
        if (searchResultsLoading || !searchQuery.trim()) return;

        setSearchResultsLoading(true);

        const apiUrl = `/api/search?q=${encodeURIComponent(searchQuery)}&client=web`;
        fetch(apiUrl, {
            method: "GET",
            headers: {
                "Content-Type": "application/json",
            },
        })
            .then((response) => response.json())
            .then((data) => {
                setSearchResults(data);
                setSearchResultsLoading(false);
            })
            .catch((error) => {
                console.error("Error:", error);
            });
    }

    const fetchFiles = async (currentPageNumber: number) => {
        try {
            const url = `api/content/files?page=${currentPageNumber}`;
            const response = await fetch(url);
            if (!response.ok) throw new Error("Failed to fetch files");

            const filesData = await response.json();
            const filesList = filesData.files;
            const totalPages = filesData.num_pages;

            setNumPages(totalPages);

            if (Array.isArray(filesList)) {
                setFiles(filesList.toSorted());
            }

        } catch (error) {
            setError("Failed to load files");
            console.error("Error fetching files:", error);
        } finally {
            setFileObjectsLoading(false);
        }
    };

    useEffect(() => {
        fetchFiles(pageNumber);
    }, [pageNumber]);

    const fetchSpecificFile = async (fileName: string) => {
        try {
            const response = await fetch(`/api/content/file?file_name=${fileName}`);
            if (!response.ok) throw new Error("Failed to fetch file");

            const file = await response.json();
            setSelectedFileFullText(file.raw_text);
        } catch (error) {
            setError("Failed to load file");
            console.error("Error fetching file:", error);
        }
    };

    const handleDownload = (fileName: string, content: string) => {
        const blob = new Blob([content], { type: "text/plain" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${fileName.split("/").pop()}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    };

    useEffect(() => {
        if (selectedFile) {
            fetchSpecificFile(selectedFile);
        }
    }, [selectedFile]);

    useEffect(() => {
        fetchFiles(pageNumber);
    }, []);

    useEffect(() => {
        if (uploadedFiles.length > 0) {
            setPageNumber(0);
            fetchFiles(0);
        }
    }, [uploadedFiles]);

    const handleDelete = async (fileName: string) => {
        setIsDeleting(true);
        try {
            const response = await fetch(`/api/content/file?filename=${fileName}`, {
                method: "DELETE",
            });
            if (!response.ok) throw new Error("Failed to delete file");
            toast({
                title: "File deleted",
                description: `File ${fileName} has been deleted`,
                variant: "default",
            });

            // Refresh files list
            fetchFiles(pageNumber);
        } catch (error) {
            toast({
                title: "Error deleting file",
                description: `Failed to delete file ${fileName}`,
                variant: "destructive",
            });
        } finally {
            setIsDeleting(false);
        }
    };

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
                        <h2 className="text-lg">Search Your Knowledge Base</h2>
                    )}
                </header>
                <div>
                    <div className={`${styles.searchLayout}`}>
                        <div className="w-full md:w-5/6 mx-auto pt-6 md:pt-8">
                            <div className="p-4 w-full mx-auto">
                                <div className="flex justify-between items-center border-2 border-muted p-1 gap-1 rounded-lg flex-col md:flex-row">
                                    <div className="relative flex-1 w-full">
                                        <Input
                                            autoFocus={true}
                                            className="border-none pl-4 focus-visible:ring-transparent focus-visible:ring-offset-transparent"
                                            onChange={(e) => handleSearchInputChange(e.currentTarget.value)}
                                            onKeyDown={(e) => {
                                                if (e.key === "Enter") {
                                                    search();
                                                }
                                            }}
                                            ref={searchInputRef}
                                            type="search"
                                            placeholder="Search Documents"
                                            value={searchQuery}
                                        />
                                    </div>
                                    <div id="actions" className="flex gap-2">
                                        <FileFilterComboBox allFiles={allFiles} onChooseFile={(file) => applySuggestion(file)} isMobileWidth={isMobileWidth} explicitFile={selectedFileFilter} />
                                        <Button
                                            className="px-2 gap-2 inline-flex rounded-none items-center border-l border-gray-300 hover:text-gray-500"
                                            variant={"ghost"}
                                            onClick={() => search()}
                                        >
                                            <MagnifyingGlass className="h-4 w-4" />
                                            <span>Find</span>
                                        </Button>
                                    </div>
                                </div>
                                {searchResults === null && (
                                    <UploadFiles setUploadedFiles={setUploadedFiles} />
                                )}
                                {searchResultsLoading && (
                                    <div className="mt-4 flex items-center justify-center">
                                        <InlineLoading
                                            className="mt-4"
                                            message={"Searching"}
                                            iconClassName="h-5 w-5"
                                        />
                                    </div>
                                )}
                                {focusSearchResult && (
                                    <div className="mt-4">
                                        <Button
                                            onClick={() => setFocusSearchResult(null)}
                                            className="mb-4"
                                            variant={"outline"}
                                        >
                                            <ArrowLeft className="inline mr-2" />
                                            Back
                                        </Button>
                                        {focusNote(focusSearchResult)}
                                    </div>
                                )}
                                {!focusSearchResult &&
                                    !searchResultsLoading &&
                                    searchResults &&
                                    searchResults.length > 0 && (
                                        <div className="mt-4 max-w-[92vw] break-all">
                                            <Button
                                                onClick={() => handleSearchInputChange("")}
                                                className="mb-4"
                                                variant={"outline"}
                                            >
                                                <ArrowLeft className="inline mr-2" />
                                                See All
                                            </Button>
                                            <ScrollArea className="h-[80vh]">
                                                {searchResults.map((result, index) => {
                                                    return (
                                                        <Note
                                                            key={result["corpus-id"]}
                                                            note={result}
                                                            setFocusSearchResult={
                                                                setFocusSearchResult
                                                            }
                                                        />
                                                    );
                                                })}
                                            </ScrollArea>
                                        </div>
                                    )}
                                {!searchResultsLoading &&
                                    searchResults === null &&
                                    !searchQuery.trim() && (
                                        <div className="w-full mt-4">
                                            {fileObjectsLoading && (
                                                <div className="mt-4 flex items-center justify-center">
                                                    <InlineLoading
                                                        className="mt-4"
                                                        message={"Loading"}
                                                        iconClassName="h-5 w-5"
                                                    />
                                                </div>
                                            )}
                                            {error && <div className="text-red-500">{error}</div>}

                                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                                {files.map((file, index) => (
                                                    <FileCard
                                                        key={index}
                                                        file={file}
                                                        index={index}
                                                        setSelectedFile={setSelectedFile}
                                                        setSelectedFileFullText={setSelectedFileFullText}
                                                        handleDownload={handleDownload}
                                                        handleDelete={handleDelete}
                                                        isDeleting={isDeleting}
                                                        selectedFileFullText={selectedFileFullText}
                                                    />
                                                ))}
                                            </div>

                                            <Pagination>
                                                <PaginationContent>
                                                    {/* Show prev button if not on first page */}
                                                    {pageNumber > 0 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationPrevious onClick={() => setPageNumber(pageNumber - 1)} />
                                                        </PaginationItem>
                                                    )}

                                                    {/* Show first page if not on first two pages */}
                                                    {pageNumber > 1 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationLink onClick={() => setPageNumber(0)}>1</PaginationLink>
                                                        </PaginationItem>
                                                    )}

                                                    {/* Show ellipsis if there's a gap */}
                                                    {pageNumber > 2 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationEllipsis />
                                                        </PaginationItem>
                                                    )}

                                                    {/* Show previous page if not on first page */}
                                                    {pageNumber > 0 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationLink onClick={() => setPageNumber(pageNumber - 1)}>{pageNumber}</PaginationLink>
                                                        </PaginationItem>
                                                    )}

                                                    {/* Current page */}
                                                    <PaginationItem className="list-none">
                                                        <PaginationLink isActive>{pageNumber + 1}</PaginationLink>
                                                    </PaginationItem>

                                                    {/* Show next page if not on last page */}
                                                    {pageNumber < numPages - 1 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationLink onClick={() => setPageNumber(pageNumber + 1)}>{pageNumber + 2}</PaginationLink>
                                                        </PaginationItem>
                                                    )}

                                                    {/* Show ellipsis if there's a gap before last page */}
                                                    {pageNumber < numPages - 3 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationEllipsis />
                                                        </PaginationItem>
                                                    )}

                                                    {/* Show last page if not on last two pages */}
                                                    {pageNumber < numPages - 2 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationLink onClick={() => setPageNumber(numPages - 1)}>{numPages}</PaginationLink>
                                                        </PaginationItem>
                                                    )}

                                                    {/* Show next button if not on last page */}
                                                    {pageNumber < numPages - 1 && (
                                                        <PaginationItem className="list-none">
                                                            <PaginationNext onClick={() => setPageNumber(pageNumber + 1)} />
                                                        </PaginationItem>
                                                    )}
                                                </PaginationContent>
                                            </Pagination>

                                        </div>
                                    )}
                                {searchResults && searchResults.length === 0 && (
                                    <Card className="flex flex-col items-center justify-center border-none shadow-none">
                                        <CardHeader className="flex flex-col items-center justify-center">
                                            <CardDescription className="border-muted-foreground border w-fit rounded-lg mb-2 text-center text-lg p-4">
                                                <FileDashed
                                                    weight="fill"
                                                    className="text-muted-foreground h-10 w-10"
                                                />
                                            </CardDescription>
                                            <CardTitle className="text-center">
                                                No documents found
                                            </CardTitle>
                                        </CardHeader>
                                        <CardContent>
                                            <div className="text-muted-foreground items-center justify-center text-center flex">
                                                To use search, upload your docs to your account.
                                            </div>
                                            <Link
                                                href="https://docs.khoj.dev/data-sources/share_your_data"
                                                className="no-underline"
                                            >
                                                <div className="mt-4 text-center text-secondary-foreground bg-secondary w-fit m-auto p-2 rounded-lg">
                                                    Learn More
                                                </div>
                                            </Link>
                                        </CardContent>
                                    </Card>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </SidebarInset>
        </SidebarProvider>
    );
}
