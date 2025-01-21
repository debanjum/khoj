import React, { useEffect, useState, useRef } from "react";
import mermaid from "mermaid";
import { Download, Info } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";

interface MermaidProps {
    chart: string;
}

const Mermaid: React.FC<MermaidProps> = ({ chart }) => {
    const [mermaidError, setMermaidError] = useState<string | null>(null);
    const [mermaidId] = useState(`mermaid-chart-${Math.random().toString(12).substring(7)}`);
    const elementRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        mermaid.initialize({
            startOnLoad: false,
        });

        mermaid.parseError = (error) => {
            console.error("Mermaid errors:", error);
            // Extract error message from error object
            // Parse error message safely
            let errorMessage;
            try {
                errorMessage = typeof error === "string" ? JSON.parse(error) : error;
            } catch (e) {
                errorMessage = error?.toString() || "Unknown error";
            }

            console.log("Mermaid error message:", errorMessage);

            if (errorMessage.str !== "element is null") {
                setMermaidError(
                    "Something went wrong while rendering the diagram. Please try again later or downvote the message if the issue persists.",
                );
            } else {
                setMermaidError(null);
            }
        };

        mermaid.contentLoaded();
    }, []);

    const handleExport = async () => {
        if (!elementRef.current) return;

        try {
            // Get SVG element
            const svgElement = elementRef.current.querySelector("svg");
            if (!svgElement) throw new Error("No SVG found");

            // Get SVG viewBox dimensions
            const viewBox = svgElement.getAttribute("viewBox")?.split(" ").map(Number) || [
                0, 0, 0, 0,
            ];
            const [, , viewBoxWidth, viewBoxHeight] = viewBox;

            // Create canvas with viewBox dimensions
            const canvas = document.createElement("canvas");
            const scale = 2; // For better resolution
            canvas.width = viewBoxWidth * scale;
            canvas.height = viewBoxHeight * scale;
            const ctx = canvas.getContext("2d");
            if (!ctx) throw new Error("Failed to get canvas context");

            // Convert SVG to data URL
            const svgData = new XMLSerializer().serializeToString(svgElement);
            const svgBlob = new Blob([svgData], { type: "image/svg+xml;charset=utf-8" });
            const svgUrl = URL.createObjectURL(svgBlob);

            // Create and load image
            const img = new Image();
            img.src = svgUrl;

            await new Promise((resolve, reject) => {
                img.onload = () => {
                    // Scale context for better resolution
                    ctx.scale(scale, scale);
                    ctx.drawImage(img, 0, 0, viewBoxWidth, viewBoxHeight);

                    canvas.toBlob((blob) => {
                        if (!blob) {
                            reject(new Error("Failed to create blob"));
                            return;
                        }

                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `mermaid-diagram-${Date.now()}.png`;
                        a.click();

                        // Cleanup
                        URL.revokeObjectURL(url);
                        URL.revokeObjectURL(svgUrl);
                        resolve(true);
                    }, "image/png");
                };

                img.onerror = () => reject(new Error("Failed to load SVG"));
            });
        } catch (error) {
            console.error("Error exporting diagram:", error);
            setMermaidError("Failed to export diagram");
        }
    };

    useEffect(() => {
        if (elementRef.current) {
            elementRef.current.removeAttribute("data-processed");

            mermaid
                .run({
                    nodes: [elementRef.current],
                })
                .then(() => {
                    setMermaidError(null);
                })
                .catch((error) => {
                    let errorMessage;
                    try {
                        errorMessage = typeof error === "string" ? JSON.parse(error) : error;
                    } catch (e) {
                        errorMessage = error?.toString() || "Unknown error";
                    }

                    console.log("Mermaid error message:", errorMessage);

                    if (errorMessage.str !== "element is null") {
                        setMermaidError(
                            "Something went wrong while rendering the diagram. Please try again later or downvote the message if the issue persists.",
                        );
                    } else {
                        setMermaidError(null);
                    }
                });
        }
    }, [chart]);

    return (
        <div>
            {mermaidError ? (
                <div className="flex items-center gap-2 bg-red-100 border border-red-500 rounded-md p-3 mt-3 text-red-900 text-sm">
                    <Info className="w-12 h-12" />
                    <span>Error rendering diagram: {mermaidError}</span>
                </div>
            ) : (
                <div
                    id={mermaidId}
                    ref={elementRef}
                    className="mermaid"
                    style={{
                        width: "auto",
                        height: "auto",
                        boxSizing: "border-box",
                        overflow: "auto",
                    }}
                >
                    {chart}
                </div>
            )}
            {!mermaidError && (
                <Button onClick={handleExport} variant={"secondary"} className="mt-3">
                    <Download className="w-5 h-5" />
                    Export as PNG
                </Button>
            )}
        </div>
    );
};

export default Mermaid;
