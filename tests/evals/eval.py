import argparse
import base64
import concurrent.futures
import json
import logging
import os
import re
import time
from datetime import datetime
from functools import partial
from io import StringIO
from textwrap import dedent
from threading import Lock
from typing import Any, Dict

import pandas as pd
import requests
import yaml
from datasets import Dataset, load_dataset
from tqdm import tqdm

from khoj.utils.helpers import (
    batcher,
    get_cost_of_chat_message,
    is_none_or_empty,
    timer,
)

# Configure root logger
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Configuration
KHOJ_URL = os.getenv("KHOJ_URL", "http://localhost:42110")
KHOJ_CHAT_API_URL = f"{KHOJ_URL}/api/chat"
KHOJ_API_KEY = os.getenv("KHOJ_API_KEY")
KHOJ_MODE = os.getenv("KHOJ_MODE", "default").lower()  # E.g research, general, notes etc.

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_EVAL_MODEL = os.getenv("GEMINI_EVAL_MODEL", "gemini-1.5-pro-002")

SAMPLE_SIZE = os.getenv("SAMPLE_SIZE")  # Number of examples to evaluate
RANDOMIZE = os.getenv("RANDOMIZE", "false").lower() == "true"  # Randomize examples
BATCH_SIZE = int(
    os.getenv("BATCH_SIZE", int(SAMPLE_SIZE) / 10 if SAMPLE_SIZE else 10)
)  # Examples to evaluate in each batch
SLEEP_SECONDS = 3 if KHOJ_MODE == "general" else 1  # Sleep between API calls to avoid rate limiting


class Counter:
    """Thread-safe counter for tracking metrics"""

    def __init__(self, value=0.0):
        self.value = value
        self.lock = Lock()

    def add(self, amount):
        with self.lock:
            self.value += amount

    def get(self):
        with self.lock:
            return self.value


# Track running metrics while evaluating
running_cost = Counter()
running_true_count = Counter(0)
running_total_count = Counter(0)


def get_article_filename(article: dict[str, str]) -> str:
    """Create a unique filename for a Wikipedia article"""
    # Construct filename from frames prompt ids associated with each article and url
    encoded_url = base64.urlsafe_b64encode(article["link"].encode()).decode()
    return "-".join(map(str, article["frames_prompt_id"])) + f"_{encoded_url}.txt"


def extract_prompt_ids_from_filename(filename: str) -> set[int]:
    """Extract frames prompt id from a indexed file name"""
    return set(map(int, filename.split("_", 1)[0].split("-")))


def extract_article_url_from_filename(filename: str) -> set[int]:
    """Decode URL from filename"""
    encoded_url = filename.split("_", 1)[1].rsplit(".", 1)[0]
    return base64.urlsafe_b64decode(encoded_url).decode()


def get_articles_by_prompt_id(prompt_id: int):
    """Get all Wikipedia articles relevant to a specific FRAMES prompt ID"""
    try:
        # Load dataset
        dataset = load_dataset("parasail-ai/frames-benchmark-wikipedia")

        # Filter function to check if prompt_id exists in sequence
        def has_prompt_id(example):
            return prompt_id in example["frames_prompt_id"]

        # Filter dataset and return matching rows
        filtered_dataset = dataset["train"].filter(has_prompt_id)
        return filtered_dataset

    except Exception as e:
        logger.error(f"Error filtering dataset for prompt {prompt_id}: {e}")
        return None


def load_frames_kb():
    """
    Load Wikipedia articles used as Knowledge Base by the FRAMES benchmark dataset from HuggingFace

    FRAMES is a benchmark dataset to evaluate retrieval and answering capabilities of agents.
    It contains ~800 requiring multi-hop retrieval and reasoning across various topics from Wikipedia.

    ### Data Fields
    - link: The link to the Wikipedia article
    - text: The text content of the Wikipedia article
    - frames_prompt_id: The list of FRAMES prompt ids for which this article is relevant
    """
    try:
        dataset_name = "parasail-ai/frames-benchmark-wikipedia"
        dataset = load_dataset(dataset_name)
        return dataset["train"]

    except Exception as e:
        logger.error(f"Error loading {dataset_name} dataset: {e}")
        return None


def index_frames_kb():
    """Index Wikipedia articles from FRAMES dataset into Khoj"""
    try:
        # Load dataset
        dataset = load_frames_kb()
        dataset_files = set(map(get_article_filename, dataset))

        # Get indexed files from Khoj API
        headers = {"Authorization": f"Bearer {KHOJ_API_KEY}"} if KHOJ_API_KEY else {}
        try:
            response = requests.get(f"{KHOJ_URL}/api/content/computer", headers=headers)
            response.raise_for_status()
            indexed_files = set(response.json())
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get indexed files: {e}")
            return False

        # Find missing files to index
        missing_files = dataset_files - indexed_files
        filtered_dataset = [
            article
            for article in dataset
            if get_article_filename(article) in missing_files and not is_none_or_empty(article["text"])
        ]
        if not filtered_dataset:
            return True
        logger.info(f"Found {len(filtered_dataset)} files to index")

        # Process Wikipedia articles from FRAMES knowledge base in batches
        batch_size = 300
        total_batches = len(filtered_dataset) // batch_size + 1
        for batch in tqdm(batcher(filtered_dataset, batch_size), total=total_batches, desc="Indexing FRAMES KB"):
            # Create files batch to index
            files = []
            for article in batch:
                filename = get_article_filename(article)
                files.append(("files", (filename, article["text"], "text/plaintext")))
            # Send files batch to index
            try:
                response = requests.patch(f"{KHOJ_URL}/api/content?client=eval", headers=headers, files=files)
                response.raise_for_status()
                time.sleep(SLEEP_SECONDS)  # Rate limiting
            except Exception as e:
                logger.error(f"Failed to index batch: {e}")
                return False
        return True
    except Exception as e:
        logger.error(f"Failed to index KB: {e}")
        return False


def load_frames_dataset():
    """
    Load the Google FRAMES benchmark dataset from HuggingFace

    FRAMES is a benchmark dataset to evaluate retrieval and answering capabilities of agents.
    It contains ~800 requiring multi-hop retrieval and reasoning across various topics.

    ### Data Fields
    - Prompt: The question to be answered
    - Answer: The ground truth answer
    - reasoning_types: The type of reasoning required to answer the question
    """
    try:
        dataset = load_dataset("google/frames-benchmark")
        # Use test split for evaluation. Sample and shuffle dataset if configured
        dataset = dataset.shuffle() if RANDOMIZE else dataset
        return dataset["test"][: int(SAMPLE_SIZE)] if SAMPLE_SIZE else dataset["test"]

    except Exception as e:
        logger.error(f"Error loading dataset: {e}")
        return None


def load_simpleqa_dataset():
    """
    Load the OpenAI SimpleQA benchmark dataset from their public bucket.

    SimpleQA is a dataset of moderately difficult q&a for 2024 models to answer across various topics.
    It contains ~4000 human vetted questions and answers with additional metadata.
    Its usage can be seen in openai/simple-evals github repository as well.

    ### Data Fields
    - problem: The question to be answered
    - answer: The ground truth answer
    - metadata: Additional metadata including topic information
    """

    try:
        # Load SimpleQA benchmark from OpenAI public bucket
        raw_url = "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv"
        response = requests.get(raw_url)
        response.raise_for_status()

        # Parse benchmark from raw CSV response
        csv_data = pd.read_csv(StringIO(response.text))
        # Normalize it into FRAMES format
        formatted_data = [
            {
                "Prompt": d["problem"],
                "Answer": d["answer"],
                "reasoning_types": json.loads(csv_data.to_dict("records")[0]["metadata"].replace("'", '"'))["topic"],
            }
            for d in csv_data.to_dict("records")
        ]

        # Convert benchmark to HF Dataset
        dataset = Dataset.from_list(formatted_data)
        dataset = dataset.shuffle() if RANDOMIZE else dataset
        dataset = dataset.select(range(int(SAMPLE_SIZE))) if SAMPLE_SIZE else dataset

        return dataset
    except Exception as e:
        logger.error(f"Error loading simpleqa dataset: {e}")
        return None


def load_gpqa_dataset():
    """
    Load the Google GPQA benchmark dataset from HuggingFace

    GPQA is a benchmark dataset to evaluate retrieval and answering capabilities of agents.
    It contains ~800 requiring multi-hop retrieval and reasoning across various topics.

    ### Data Fields
    - Prompt: The question to be answered
    - Answer: The ground truth answer
    - reasoning_types: The type of reasoning required to answer the question
    """
    import random

    def format_multiple_choice_question(row: Dict) -> tuple[str, str]:
        """
        Create GPQA multi-choice prompt from shuffled answer choices and question.
        Refer: https://github.com/openai/simple-evals/blob/a8e85cc8a5dea497d915f870895250e07f9cc737/common.py#L12

        Returns formatted prompt and correct answer letter.
        """
        # Gather choices
        choices = [
            row["Incorrect Answer 1"],
            row["Incorrect Answer 2"],
            row["Incorrect Answer 3"],
            row["Correct Answer"],
        ]
        # Shuffle choices
        random.shuffle(choices)

        # Get correct answer letter
        correct_index = choices.index(row["Correct Answer"])
        correct_letter = "ABCD"[correct_index]

        prompt = f"""
Answer the following multiple choice question. Answer should be of the following format: 'Answer: $LETTER' (without quotes) where $LETTER is one of ABCD. Think step by step before answering.

{row["Question"]}

A) {choices[0]}
B) {choices[1]}
C) {choices[2]}
D) {choices[3]}
        """.strip()

        return prompt, correct_letter

    try:
        dataset = load_dataset("Idavidrein/gpqa", "gpqa_diamond", split="train")

        # Create multi-choice q&a prompt from choices and correct answer
        prompts_and_answers = [format_multiple_choice_question(row) for row in dataset]

        # Normalize dataset to FRAMES format
        dataset = dataset.rename_columns({"Subdomain": "reasoning_types"})
        dataset = dataset.add_column("Prompt", [p[0] for p in prompts_and_answers])
        dataset = dataset.add_column("Answer", [p[1] for p in prompts_and_answers])

        # Sample and shuffle dataset if configured
        dataset = dataset.shuffle() if RANDOMIZE else dataset
        dataset = dataset[: int(SAMPLE_SIZE)] if SAMPLE_SIZE else dataset

        return dataset
    except Exception as e:
        logger.error(f"Error loading dataset: {e}")
        return None


def load_math500_dataset():
    """
    Load and format the MATH500 dataset to match the evaluation script's structure.

    Args:
        sample_size (int, optional): Number of samples to include. Defaults to None (use full dataset).
        randomize (bool, optional): Whether to randomize the dataset. Defaults to False.

    Returns:
        Dataset: Formatted HuggingFace Dataset.
    """
    try:
        # Load the MATH500 dataset from HuggingFace
        dataset = load_dataset("HuggingFaceH4/MATH-500", split="test")
        dataset = dataset.rename_columns({"problem": "Prompt", "answer": "Answer", "subject": "reasoning_types"})
        dataset = dataset.shuffle() if RANDOMIZE else dataset
        dataset = dataset.select(range(int(SAMPLE_SIZE))) if SAMPLE_SIZE else dataset

        return dataset
    except Exception as e:
        print(f"Error loading and formatting MATH500 dataset: {e}")
        return None


def get_agent_response(prompt: str) -> Dict[str, Any]:
    """Get response from the Khoj API"""
    # Set headers
    headers = {"Content-Type": "application/json"}
    if not is_none_or_empty(KHOJ_API_KEY):
        headers["Authorization"] = f"Bearer {KHOJ_API_KEY}"

    try:
        response = requests.post(
            KHOJ_CHAT_API_URL,
            headers=headers,
            json={
                "q": prompt,
                "create_new": True,
            },
        )
        response.raise_for_status()
        response_json = response.json()
        return {
            "response": response_json.get("response", ""),
            "usage": response_json.get("usage", {}),
            "references": response_json.get("references", {}),
        }
    except Exception as e:
        logger.error(f"Error getting agent response: {e}")
        return {"response": "", "usage": {}, "references": {}}


def calculate_precision_recall(numerator: int, denominator: int) -> float:
    """Calculate precision and recall from numerator and denominator"""
    if numerator == 0 and denominator == 0:
        return 1.0
    elif numerator > 0 and denominator == 0:
        return 0.0
    else:
        return numerator / denominator


def calculate_fi(precision: float, recall: float) -> float:
    """Calculate F1 score from precision and recall"""
    return 2 * (precision * recall) / (precision + recall) if precision + recall > 0 else 0.0


def evaluate_response_for_ir(
    query: str, agent_response: str, ground_truth: int, agent_references: dict = {}
) -> tuple[bool | None, str, float]:
    """Evaluate Khoj response against benchmark ground truth using string matching"""
    try:
        # Extract answer from agent response
        referenced_files: list[dict[str, str]] = agent_references.get("context", [])
        count_of_correct_articles_used_by_agent: int = 0
        # Count how many of the expected articles the agent actually retrieved from the KB
        unique_file_refs = {file["file"] for file in referenced_files}
        referenced_articles = list(map(extract_article_url_from_filename, unique_file_refs))
        for file in unique_file_refs:
            frames_ids_for_articles_used_by_agent = extract_prompt_ids_from_filename(file)
            count_of_correct_articles_used_by_agent += int(ground_truth in frames_ids_for_articles_used_by_agent)

        articles = get_articles_by_prompt_id(ground_truth)
        precision = calculate_precision_recall(count_of_correct_articles_used_by_agent, len(unique_file_refs))
        recall = calculate_precision_recall(count_of_correct_articles_used_by_agent, len(articles))
        f1 = calculate_fi(precision, recall)

        explanation = (
            f"Information Retrieval F1 Score: {f1:.2%} Recall: {recall:.2%}, Precision: {precision:.2%}.\n"
            f"{count_of_correct_articles_used_by_agent} of {len(articles)} correct from {len(unique_file_refs)} total retrievals for {ground_truth}.\n"
            f"Queries:\n{yaml.dump(sorted([r['query'] for r in referenced_files]))}\n"
            f"Expected Articles for {ground_truth}:\n{yaml.dump(sorted([a['link'] for a in articles]))}\n"
            f"Retrieved Articles for {ground_truth}:\n{yaml.dump(referenced_articles)}\n"
        )

        # Truncate referenced files for logging
        truncated_refs = [
            {k: v[:200] + "..." if len(v) > 200 else v for k, v in ref.items()} for ref in referenced_files
        ]
        logger.info(f"Retrieved Article Details:\n{yaml.dump(truncated_refs, sort_keys=False)}\n")

        # Return decision, explanation and cost in structured form
        return recall, explanation, 0.0
    except Exception as e:
        logger.error(f"Error in IR evaluation: {e}")
        return None, f"Evaluation failed: {str(e)}", 0.0


def evaluate_response_with_mcq_match(
    query: str, agent_response: str, ground_truth: str, agent_references: dict = {}
) -> tuple[bool | None, str, float]:
    """Evaluate Khoj response against benchmark ground truth using string matching"""
    try:
        # Extract answer from agent response
        answer_pattern_multichoice = r"(?i)Answer\s*:\s*([A-D])"
        match = re.search(answer_pattern_multichoice, agent_response)
        extracted_answer = match.group(1) if match else None

        # Check if extracted answer matches ground truth
        decision = extracted_answer == ground_truth
        explanation = f"Agent response {'matches' if decision else 'does not match'} ground truth {ground_truth}"

        # Return decision, explanation and cost in structured form
        return float(decision), explanation, 0.0
    except Exception as e:
        logger.error(f"Error in evaluation: {e}")
        return None, f"Evaluation failed: {str(e)}", 0.0


def evaluate_response_with_gemini(
    query: str, agent_response: str, ground_truth: str, agent_references: dict = {}, eval_model=GEMINI_EVAL_MODEL
) -> tuple[bool | None, str, float]:
    """Evaluate Khoj response against benchmark ground truth using Gemini"""
    evaluation_prompt = f"""
    Compare the following agent response with the ground truth answer.
    Determine if the agent response contains the key information from the ground truth.
    Focus on factual correctness rather than exact wording.

    Query: {query}
    Agent Response: {agent_response}
    Ground Truth: {ground_truth}

    Provide your evaluation in the following json format:
    {"explanation:" "[How you made the decision?)", "decision:" "(TRUE if response contains key information, FALSE otherwise)"}
    """
    gemini_api_url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{eval_model}:generateContent?key={GEMINI_API_KEY}"
    )

    try:
        response = requests.post(
            gemini_api_url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": evaluation_prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"},
            },
        )
        response.raise_for_status()
        response_json = response.json()

        # Update cost of evaluation
        input_tokens = response_json["usageMetadata"]["promptTokenCount"]
        ouput_tokens = response_json["usageMetadata"]["candidatesTokenCount"]
        cost = get_cost_of_chat_message(eval_model, input_tokens, ouput_tokens)

        # Parse evaluation response
        eval_response: dict[str, str] = json.loads(
            clean_json(response_json["candidates"][0]["content"]["parts"][0]["text"])
        )
        decision = float(str(eval_response.get("decision", "")).upper() == "TRUE")
        explanation = eval_response.get("explanation", "")
        # Handle evaluation service errors
        if "503 Service Error" in explanation:
            decision = None
        # Extract decision and explanation from structured response
        return decision, explanation, cost
    except Exception as e:
        logger.error(f"Error in evaluation: {e}")
        return None, f"Evaluation failed: {str(e)}", 0.0


def process_batch(batch, batch_start, results, dataset_length, response_evaluator):
    global running_cost
    for idx, (prompt, answer, reasoning_type) in enumerate(batch):
        current_index = batch_start + idx
        logger.info(f"Processing example: {current_index}/{dataset_length}")

        # Trigger research mode if enabled
        prompt = f"/{KHOJ_MODE} {prompt}" if KHOJ_MODE and not prompt.startswith(f"/{KHOJ_MODE}") else prompt

        # Get agent response
        response = get_agent_response(prompt)
        agent_response = response["response"]
        agent_usage = response["usage"]
        agent_references = response["references"]

        # Evaluate response
        if is_none_or_empty(agent_response):
            decision = None
            explanation = "Agent response is empty. This maybe due to a service error."
        else:
            decision, explanation, eval_cost = response_evaluator(prompt, agent_response, answer, agent_references)

        # Store results
        results.append(
            {
                "index": current_index,
                "prompt": prompt,
                "ground_truth": answer,
                "agent_response": agent_response,
                "evaluation_decision": decision,
                "evaluation_explanation": explanation,
                "reasoning_type": reasoning_type,
                "usage": agent_usage,
                "references": agent_references,
            }
        )

        # Update running cost
        query_cost = float(agent_usage.get("cost", 0.0))
        running_cost.add(query_cost + eval_cost)

        # Update running accuracy
        running_accuracy = 0.0
        if decision is not None:
            running_true_count.add(decision)
            running_total_count.add(1)
            running_accuracy = running_true_count.get() / running_total_count.get()

        ## Log results
        decision_color = {True: "green", None: "blue", False: "red"}[decision > 0.5]
        colored_decision = color_text(str(decision), decision_color)
        result_to_print = f"""
---------
Decision: {colored_decision}
Accuracy: {running_accuracy:.2%}
Progress: {running_total_count.get()/dataset_length:.2%}
Question: {prompt}
Expected Answer: {answer}
Agent Answer: {agent_response}
Explanation: {explanation}
Cost: ${running_cost.get():.5f} (Query: ${query_cost:.5f}, Eval: ${eval_cost:.5f})
---------
        """
        logger.info(dedent(result_to_print).lstrip())

        # Sleep between API calls to avoid rate limiting
        time.sleep(SLEEP_SECONDS)


def color_text(text, color):
    colors = {
        "red": "\033[91m",  # Bright red
        "green": "\033[32m",  # Standard green
        "blue": "\033[34m",  # Bright blue
        "reset": "\033[0m",
    }
    return f"{colors[color]}{text}{colors['reset']}"


def clean_json(response: str):
    """Remove any markdown json codeblock and newline formatting if present. Useful for non schema enforceable models"""
    return response.strip().replace("\n", "").removeprefix("```json").removesuffix("```")


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate Khoj on a supported benchmark.")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Path to store evaluation results CSV (default: [benchmark]_evaluation_results_[datetime].csv)",
    )
    parser.add_argument(
        "--dataset",
        "-d",
        default="frames",
        choices=["frames", "frames_ir", "simpleqa", "gpqa", "math500"],
        help="Dataset to use for evaluation (default: frames)",
    )
    return parser.parse_args()


def main():
    # Initialize variables
    args = parse_args()
    dataset = None

    # Load dataset
    with timer(f"Loaded {args.dataset} dataset in", logger, log_level=logging.INFO):
        if args.dataset == "frames":
            dataset = load_frames_dataset()
        elif args.dataset == "simpleqa":
            dataset = load_simpleqa_dataset()
        elif args.dataset == "gpqa":
            dataset = load_gpqa_dataset()
        elif args.dataset == "math500":
            dataset = load_math500_dataset()
        elif args.dataset == "frames_ir":
            indexed = index_frames_kb()
            if indexed:
                dataset = load_frames_dataset()
                # Rename the index field, 'Unnamed: 0' to 'Answer' for IR evaluation
                dataset["Answer"] = dataset["Unnamed: 0"]
    if dataset is None:
        return

    # Initialize variables
    results = []
    dataset_length = len(dataset["Prompt"])
    if args.dataset == "gpqa":
        response_evaluator = evaluate_response_with_mcq_match
    elif args.dataset == "math500":
        response_evaluator = partial(
            evaluate_response_with_gemini, eval_model=os.getenv("GEMINI_EVAL_MODEL", "gemini-2.0-flash-001")
        )
    elif args.dataset == "frames_ir":
        response_evaluator = evaluate_response_for_ir
    else:
        response_evaluator = evaluate_response_with_gemini

    # Process examples in batches
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for i in range(0, dataset_length, BATCH_SIZE):
            batch_start = i
            batch = zip(
                dataset["Prompt"][i : i + BATCH_SIZE],
                dataset["Answer"][i : i + BATCH_SIZE],
                dataset["reasoning_types"][i : i + BATCH_SIZE],
            )
            futures.append(
                executor.submit(process_batch, batch, batch_start, results, dataset_length, response_evaluator)
            )

        # Wait for all futures to complete
        concurrent.futures.wait(futures)

    # Calculate metrics
    df = pd.DataFrame(results)
    eval_df = df.dropna(subset=["evaluation_decision"])  # Exclude rows with missing evaluation decision
    accuracy = (eval_df["evaluation_decision"]).mean()

    # Calculate accuracy by reasoning type
    reasoning_type_accuracy = (eval_df.groupby("reasoning_type")["evaluation_decision"]).apply(lambda x: x.mean())

    # Collect summary
    colored_accuracy = color_text(f"{accuracy:.2%}", "blue")
    colored_accuracy_str = f"Overall Accuracy: {colored_accuracy} on {args.dataset.title()} dataset."
    accuracy_str = f"Overall Accuracy: {accuracy:.2%} on {args.dataset}."
    accuracy_by_reasoning = f"Accuracy by Reasoning Type:\n{reasoning_type_accuracy}"
    cost = f"Total Cost: ${running_cost.get():.5f}."
    sample_type = f"Sampling Type: {SAMPLE_SIZE} samples." if SAMPLE_SIZE else "Whole dataset."
    sample_type += " Randomized." if RANDOMIZE else ""
    logger.info(f"\n{colored_accuracy_str}\n\n{accuracy_by_reasoning}\n\n{cost}\n\n{sample_type}\n")

    # Save summary to file
    summary = f"{accuracy_str}\n\n{accuracy_by_reasoning}\n\n{cost}\n\n{sample_type}\n"
    summary_file = args.output.replace(".csv", ".txt") if args.output else None
    summary_file = (
        summary_file or f"{args.dataset}_evaluation_summary_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    )
    with open(summary_file, "w") as f:
        f.write(summary)

    # Save raw results to file
    output_file = args.output or f"{args.dataset}_evaluation_results_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    df.to_csv(output_file, index=False)
    logger.info(f"Results saved to {summary_file}, {output_file}")


if __name__ == "__main__":
    """
    Evaluate Khoj on supported benchmarks.
    Response are evaluated by GEMINI_EVAL_MODEL (default: gemini-pro-1.5-002).

    Khoj should be running at KHOJ_URL (default: http://localhost:42110).
    The Gemini judge model is accessed via the Gemini API with your GEMINI_API_KEY.
    To evaluate Khoj in research mode, set the KHOJ_MODE environment variable to "research".

    Run the script using the following command:
    KHOJ_MODE="research" GEMINI_API_KEY="<your_gemini_api_key>" python eval_frames.py
    """
    logger.info(f"{datetime.now()} - Begin Quizzing Khoj.")
    with timer("Ran eval script in", logger, log_level=logging.INFO):
        main()
    logger.info(f"{datetime.now()} - End Quizzing Khoj.")
