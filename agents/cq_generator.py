import os
import json
import argparse
import fitz  # PyMuPDF
from typing import List
from pathlib import Path

# LangChain and Pydantic imports
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ---- 1. Define Pydantic schema for structured output ----
# (models/ is reconstructed: the original repository imports it but does not ship it)
from models.competency_questions import CompetencyQuestion, CQList

from helper.connections import get_vllm_llm


# ---- 3. Prompt Template ----
# The prompt is the paper's: an ODP-driven guide with four patterns (event reification & participation, quantities,
# time instants/intervals, situations & states). `domain="insurance"` is the original wording, unchanged.
# `domain="wba"` keeps the structure and swaps the domain sentence and the worked examples (illustrative only).
_INTRO = {
    "insurance": "You are an expert Ontology Engineer specializing in legal and insurance domains. ",
    "wba": ("You are an expert Ontology Engineer specializing in legal and telecommunications wholesale-agreement domains "
            "(the NBN Co Wholesale Broadband Agreement: terms, product descriptions, technical specifications, service levels, price lists). "),
}

_EXAMPLES = {
    "insurance": (
        "### 1. Event Reification & Participation (Core Pattern)\n"
        "Model actions and events as central classes. Instead of connecting an agent directly to an object, "
        "connect them both to an 'Event' class that captures the context (time, location, manner).\n\n"
        "• **INSTEAD OF THIS (flat model):**\n"
        "  - CQ: “Who can cancel the policy?”\n"
        "• **DO THIS (reified model):** Break the event down into its components:\n"
        "  - CQ 1: “What is the name of the event when a policy's coverage is terminated at the owner's request?” -> odp_hint: 'Event Reification', expected_answer: 'Cancellation'\n"
        "  - CQ 2: “Who is the agent participant in a 'Cancellation' event?” -> odp_hint: 'Participation', expected_answer: 'the Policy Owner'\n"
        "  - CQ 3: “What is the object participant in a 'Cancellation' event?” -> odp_hint: 'Participation', expected_answer: 'the Policy'\n"
        "  - CQ 4: “What is the required manner for initiating a 'Cancellation' event?” -> odp_hint: 'Participation', expected_answer: 'written notice'\n\n"
        "### 2. Quantities and Qualities\n"
        "Treat values like money, time durations, and percentages as classes with their own properties (value and unit).\n\n"
        "• **INSTEAD OF THIS (simple value):**\n"
        "  - CQ: “What is the death benefit?”\n"
        "• **DO THIS (quantity model):** Separate the value from its unit.\n"
        "  - CQ 1: “What is the numeric value of the death benefit?” -> odp_hint: 'Quantity', expected_answer: '500000'\n"
        "  - CQ 2: “What is the currency unit of the death benefit?” -> odp_hint: 'Quantity', expected_answer: 'USD'\n\n"
        "### 3. Time Instants and Intervals\n"
        "Distinguish between points in time (instants) and durations (intervals).\n\n"
        "• **INSTEAD OF THIS (ambiguous time):**\n"
        "  - CQ: “When is the reinstatement period?”\n"
        "• **DO THIS (precise time model):**\n"
        "  - CQ 1: “What is the duration of the reinstatement period?” -> odp_hint: 'Time Interval', expected_answer: '5 years'\n"
        "  - CQ 2: “What event marks the beginning of the reinstatement period interval?” -> odp_hint: 'Time Interval', expected_answer: 'the date of lapse'\n\n"
        "### 4. Situations & States\n"
        "Model the different states a policy can be in (e.g., Active, Lapsed, InGracePeriod) as distinct classes. "
        "Generate CQs about the states themselves and the events that cause transitions between them.\n\n"
        "• **DO THIS (situation model):**\n"
        "  - CQ 1: “What event causes a transition from the 'Active' state to the 'In Grace Period' state?” -> odp_hint: 'Situation', expected_answer: 'a missed premium payment'\n"
        "  - CQ 2: “What is the maximum duration a policy can be in the 'In Grace Period' state?” -> odp_hint: 'Situation', expected_answer: '31 days'\n"
        "  - CQ 3: “What state does the policy enter if a premium is not paid by the end of the Grace Period?” -> odp_hint: 'Situation', expected_answer: 'Lapsed'\n\n"
    ),
    "wba": (
        "### 1. Event Reification & Participation (Core Pattern)\n"
        "Model actions and events as central classes. Instead of connecting an agent directly to an object, "
        "connect them both to an 'Event' class that captures the context (time, location, manner).\n\n"
        "• **INSTEAD OF THIS (flat model):**\n"
        "  - CQ: “Who can give a Default Notice?”\n"
        "• **DO THIS (reified model):** Break the event down into its components:\n"
        "  - CQ 1: “What is the name of the notice given when a party must be told that it has failed to comply with the Agreement?” -> odp_hint: 'Event Reification', expected_answer: 'Default Notice'\n"
        "  - CQ 2: “Who is the agent participant in a 'Default Notice' event?” -> odp_hint: 'Participation', expected_answer: 'the notifying party'\n"
        "  - CQ 3: “What is the object participant in a 'Default Notice' event?” -> odp_hint: 'Participation', expected_answer: 'the Default'\n\n"
        "### 2. Quantities and Qualities\n"
        "Treat values like money, time durations, rates and percentages as classes with their own properties (value and unit).\n\n"
        "• **INSTEAD OF THIS (simple value):**\n"
        "  - CQ: “What is the deadline for a Default Notice?”\n"
        "• **DO THIS (quantity model):** Separate the value from its unit.\n"
        "  - CQ 1: “What is the numeric value of the period within which a Default Notice must be issued?” -> odp_hint: 'Quantity', expected_answer: '6'\n"
        "  - CQ 2: “What is the unit of that period?” -> odp_hint: 'Quantity', expected_answer: 'months'\n\n"
        "### 3. Time Instants and Intervals\n"
        "Distinguish between points in time (instants) and durations (intervals).\n\n"
        "• **INSTEAD OF THIS (ambiguous time):**\n"
        "  - CQ: “When must nbn act after the Agreement ends?”\n"
        "• **DO THIS (precise time model):**\n"
        "  - CQ 1: “What is the duration within which nbn must act after the effective date of expiry or termination?” -> odp_hint: 'Time Interval', expected_answer: '20 Business Days'\n"
        "  - CQ 2: “What event marks the beginning of that interval?” -> odp_hint: 'Time Interval', expected_answer: 'the effective date of expiry or termination of the Agreement'\n\n"
        "### 4. Situations & States\n"
        "Model the different states something can be in (e.g., a product component that is Ordered, Active or Disconnected) as distinct classes. "
        "Generate CQs about the states themselves and the events that cause transitions between them.\n\n"
        "• **DO THIS (situation model):**\n"
        "  - CQ 1: “What event causes a transition from the '<state A>' state to the '<state B>' state?” -> odp_hint: 'Situation', expected_answer: '<the triggering event stated in the text>'\n"
        "  - CQ 2: “What is the maximum duration something can remain in the '<state B>' state?” -> odp_hint: 'Situation', expected_answer: '<the duration stated in the text>'\n\n"
        "(The examples above only illustrate the question patterns; the questions and answers you write must come from the document text below.)\n\n"
    ),
}


def get_prompt_template(domain: str = "insurance"):
    """Creates a prompt template to generate CQs that drive Ontology Design Patterns."""
    final = (
        "================================================\n"
        "## Final Instructions ##\n"
        "================================================\n"
        "1.  **Deconstruct:** For every important clause, deconstruct it into fine-grained CQs following the ODP guides above.\n"
        "2.  **Be Specific:** Your `expected_answer` must be the precise value from the text.\n"
        "3.  **Complete the Schema:** Fill in all fields for every question: `competency_question`, `key_entities`, `odp_hint`, and `expected_answer`.\n"
    )
    if domain != "insurance":
        final += ("4.  **Never invent:** Every `expected_answer` must be copied from the document text. "
                  "If the page has no answerable facts (cover page, contents, blank), return an empty `questions` list.\n")
    return PromptTemplate(
        template=(
            _INTRO[domain] +
            "Your task is to analyze the document text and generate a list of atomic Competency Questions (CQs) "
            "that will drive the construction of a well-designed ontology using established Ontology Design Patterns (ODPs). "
            "You must return ONLY a JSON object that strictly follows the provided schema.\n\n"
            "**JSON Schema (use EXACT keys and structure):**\n{schema}\n\n"
            "================================================\n"
            "## Guide to Generating ODP-Driven CQs ##\n"
            "================================================\n"
            "Your primary goal is to create CQs that force a rich, reified model structure, not just a flat list of facts. "
            "For each significant piece of information, formulate a set of CQs that break it down according to the patterns below.\n\n"
            + _EXAMPLES[domain] + final +
            "\n**Document Text:**\n---\n{page_text}\n---\n"
        ),
        input_variables=["page_text"],
        # We pass the new, more detailed schema to the prompt
        partial_variables={"schema": CQList.model_json_schema()}
    )


# ---- 4. PDF Extraction ----
def extract_pages_from_pdf(pdf_path: str):
    """Extracts text from each page of a PDF file."""
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=0):  # Start pages from 0 for consistency
            text = page.get_text("text")
            if text.strip():
                yield i, text


# ---- 5. Main CQ Generation Logic ----
def generate_cqs_for_contract(contract_path: str, output_json: str, structured_llm, domain: str = "insurance",
                              start_page: int = 0, max_pages: int = None, min_words: int = 0, page_list: str = None):
    """
    Processes a single PDF contract, generates CQs for each page,
    and saves the result to a JSON file.
    Resumable: pages already present in `output_json` are skipped. Only `competency_question` and `expected_answer`
    are stored, which is the format of the published CQ files and what the ontology agents and eval.py consume.
    """
    contract_id = Path(contract_path).stem
    all_results = {contract_id: {}}
    if os.path.exists(output_json):
        try:
            all_results = json.load(open(output_json, encoding="utf-8"))
            all_results.setdefault(contract_id, {})
        except Exception:
            pass
    prompt = get_prompt_template(domain)
    wanted = None
    if page_list:  # 1-based pages, e.g. "5,6,24-33"
        wanted = set()
        for part in page_list.split(","):
            a, _, b = part.strip().partition("-")
            wanted |= set(range(int(a) - 1, int(b or a)))

    print(f"\n📄 Processing contract: {contract_id}")

    for page_number, page_text in extract_pages_from_pdf(contract_path):
        if page_number < start_page or (max_pages is not None and page_number >= start_page + max_pages):
            continue
        if wanted is not None and page_number not in wanted:
            continue
        if str(page_number) in all_results[contract_id]:
            continue
        if len(page_text.split()) < min_words:
            print(f"  - Skipping page {page_number} ({len(page_text.split())} words)")
            continue
        print(f"  - Analyzing page {page_number}...")
        message = HumanMessage(
            content=prompt.format(
                page_text=page_text[:8000]
            )
        )
        for attempt in (1, 2, 3):  # JSON mode occasionally returns malformed output: try again
            try:
                cq_list_object = structured_llm.invoke([message])
                result_dict = cq_list_object.model_dump()
                all_results[contract_id][str(page_number)] = [
                    {"competency_question": q["competency_question"], "expected_answer": q["expected_answer"]}
                    for q in result_dict['questions']]
                print(f"  ✔ Successfully parsed page {page_number} with {len(result_dict.get('questions', []))} CQs.")
                break
            except ValidationError as ve:
                print(f"  ❌ [ERROR] Validation failed for page {page_number} (attempt {attempt}): {ve}")
            except Exception as e:
                print(f"  ❌ [ERROR] An unexpected error occurred on page {page_number} (attempt {attempt}): {e}")
        os.makedirs(os.path.dirname(os.path.abspath(output_json)), exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:  # save after every page so a stopped run can resume
            json.dump(all_results, f, indent=4)

    os.makedirs(os.path.dirname(os.path.abspath(output_json)), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=4)
    print(f"\n💾 Saved all CQs for {contract_id} to {output_json}")


def build_structured_llm():
    llm = get_vllm_llm()
    usage_log = os.getenv("USAGE_LOG")
    if usage_log:  # optional token accounting (helper/usage.py)
        from helper.usage import UsageHandler
        llm.callbacks = [UsageHandler(usage_log)]
    return llm.with_structured_output(schema=CQList, method="json_mode")


# ---- 6. Main Execution Block ----
if __name__ == "__main__":
    # --- Dynamic Path Configuration ---
    # Assumes the script is run from the project root 'llm-ontology-generation'
    PROJECT_ROOT = Path(__file__).parent.parent
    parser = argparse.ArgumentParser(description="Generate ODP-driven competency questions per page of each contract PDF.")
    parser.add_argument("--contracts-dir", default=str(PROJECT_ROOT / "contracts"), help="Process every PDF in this folder (default: contracts/)")
    parser.add_argument("--contract", default=None, help="Process just this PDF")
    parser.add_argument("--out", default=None, help="Output JSON for --contract (default: cqs/generated/<name>.json)")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "cqs" / "generated"))
    parser.add_argument("--domain", choices=sorted(_INTRO), default="insurance", help="Prompt wording (insurance = the original prompt)")
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--min-words", type=int, default=0, help="Skip pages with fewer words")
    parser.add_argument("--page-list", default=None, help='1-based pages to process, e.g. "5,6,24-33"')
    args = parser.parse_args()

    OUTPUT_DIR = Path(args.output_dir)

    # Create the output directory if it doesn't exist
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pdf_files = [Path(args.contract)] if args.contract else list(Path(args.contracts_dir).glob("*.pdf"))
    if not args.contract and not Path(args.contracts_dir).exists():
        print(f"🚨 [FATAL ERROR] The contracts directory was not found at: {args.contracts_dir}")
        print("Please ensure the 'contracts' folder exists in your project root.")
    else:
        # --- Initialization ---
        structured_chat_llm = build_structured_llm()

        # --- Run the Generation Process for all PDFs in the directory ---
        if not pdf_files:
            print(f"🟡 [WARNING] No PDF files found in {args.contracts_dir}.")

        for contract_file in pdf_files:
            # Define a unique output file for each contract
            output_filename = contract_file.with_suffix(".json").name
            output_path = Path(args.out) if (args.contract and args.out) else OUTPUT_DIR / output_filename

            generate_cqs_for_contract(
                contract_path=str(contract_file),
                output_json=str(output_path),
                structured_llm=structured_chat_llm,
                domain=args.domain, start_page=args.start_page, max_pages=args.max_pages, min_words=args.min_words, page_list=args.page_list,
            )

        print("\n🎉 All contracts processed.")
