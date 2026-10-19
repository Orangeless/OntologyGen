import os
import time
import json
import shutil
from pathlib import Path
from typing import TypedDict, List, Dict, Optional, Annotated

# PDF
import fitz

# LangChain / LangGraph
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage, AIMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# Tools
from tools.file_management import *
from tools.syntax_checks import verify_rdf_syntax, verify_owl_consistency
from helper.tool_count import tool_call_count
from helper.domain_profiles import PROFILES
from helper.reference import Reference
from helper.snapshot import compact_snapshot
from helper import grounding
from helper.usage import UsageHandler

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import Literal
import argparse

# --------------------------
# Setup
# --------------------------
load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY_DIR = PROJECT_ROOT / "ontology"
ONTOLOGY_DIR.mkdir(exist_ok=True)

MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen2-72B-Instruct")
MAX_QA_CYCLES = int(os.getenv("MAX_QA_CYCLES", "20"))
MAX_SYNTAX_CHECKS = 20
MAX_TOOL_MESSAGES = int(os.getenv("MAX_TOOL_MESSAGES", "0"))        # 0 = unlimited (original); cap on a tool loop's message history
READ_CONTEXT_LINES = int(os.getenv("READ_CONTEXT_LINES", "200"))   # lines the QA-coder is told to read around an error
SNAPSHOT_MAX_LINES = int(os.getenv("ONTOLOGY_SNAPSHOT_MAX_LINES", "0"))  # 0 = paste the whole ontology (original behaviour)

# Domain wording and the optional reference ontology; both are set from the CLI in __main__.
# Defaults reproduce the original life-insurance behaviour exactly.
MAX_DEBUG_CYCLES = int(os.getenv("MAX_DEBUG_CYCLES", "0"))  # repair rounds allowed for the syntax / consistency gates (0 = unlimited, as published)
PIPELINE_MODE = os.getenv("PIPELINE_MODE", "paper")  # "paper" = the published LLM QA loop; "lite" = deterministic checks (helper/grounding.py)
PAGE_BASE = None        # lite mode: the ontology as it was before the current page
SEED_INDS = frozenset() # lite mode: individuals of the reference seed (must not be modified by the documents)
PROFILE = PROFILES["insurance"]
REF = None  # helper.reference.Reference when --reference is given


def _p(text: str) -> str:
    """Apply the active domain profile (currency)."""
    return text.replace("USD", PROFILE["currency"])


def _ref_block(role: str) -> str:
    """Reference-ontology guidance for an agent role ('' when no reference is used)."""
    return REF.prompt_block(role) if REF is not None else ""

# Shared LLM. Endpoint/key: LLM_BASE_URL + LLM_API_KEY, else OPENROUTER_API_KEY -> OpenRouter, else a local vLLM server.
_OR_KEY = os.getenv("OPENROUTER_API_KEY")
USAGE = UsageHandler(os.getenv("USAGE_LOG"))
llm = ChatOpenAI(
    model=MODEL_NAME,
    base_url=os.getenv("LLM_BASE_URL") or ("https://openrouter.ai/api/v1" if _OR_KEY else "http://localhost:8000/v1"),
    api_key=os.getenv("LLM_API_KEY") or _OR_KEY or "not-needed",
    temperature=0.0,
    max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),  # a dense page needs far more than 4096 tokens of Turtle in one tool call
    callbacks=[USAGE],
    timeout=float(os.getenv("LLM_TIMEOUT", "120")),  # a stalled request should fail fast, not wait minutes
    max_retries=1,
    # optional provider parameters as JSON, e.g. {"reasoning": {"effort": "low"}} for reasoning models on OpenRouter
    extra_body=json.loads(os.getenv("LLM_EXTRA_BODY") or "null"),
)
qa_llm = ChatOpenAI(
    model=MODEL_NAME,
    base_url=os.getenv("LLM_BASE_URL") or ("https://openrouter.ai/api/v1" if _OR_KEY else "http://localhost:8000/v1"),
    api_key=os.getenv("LLM_API_KEY") or _OR_KEY or "not-needed",
    temperature=0.0,
    max_tokens=int(os.getenv("QA_MAX_TOKENS") or os.getenv("LLM_MAX_TOKENS", "4096")),   # same cap as the coder unless QA_MAX_TOKENS is set
    callbacks=[USAGE],
    timeout=float(os.getenv("LLM_TIMEOUT", "120")),
    max_retries=1,
    extra_body=json.loads(os.getenv("LLM_EXTRA_BODY") or "null"),
)

# --------------------------
# Reducer for role-scoped memory
# --------------------------
CLEAR = "__CLEAR__"

def list_reducer(existing: list, new: list | str | None) -> list:
    if new == CLEAR:
        return []
    if new is None:
        return existing
    return existing + new

# --------------------------
# Pydantic models
# --------------------------
class QAReview(BaseModel):
    status: Literal["QA_PASSED", "CHANGES_REQUIRED"]
    feedback: List[str] = Field(default_factory=list)

# --------------------------
# State
# --------------------------
class OrchestratorState(TypedDict):
    # Inputs per page
    page_text: str
    cqs_for_page: List[Dict[str, str]]
    ontology_file_path: str
    page_number: int

    # Artifacts
    requirements_doc: str
    implementation_plan: str
    reflection: Optional[str]  # For coder to reflect on previous attempts

    # QA & checks
    qa_feedback: Optional[List[str]]
    syntax_error: Optional[str]
    consistency_error: Optional[str]
    review_type: int  # 0: nothing, 1: QA, 2: syntax, 3: semantics

    # Loop control
    qa_cycles: int
    debug_cycles: int

    # The only message buffer (consumed by ToolNode/tools_condition)
    messages: Annotated[List[BaseMessage], list_reducer]


# --------------------------
# Helpers
# --------------------------
def _retry_invoke(model, messages, tries=3):
    last = None
    for i in range(tries):
        try:
            return model.invoke(messages)
        except Exception as e:
            last = e
            print(f"[LLM retry {i+1}/{tries}] {type(e).__name__}: {e}")
            time.sleep(min(30, 3 * 2 ** i))  # back off: a network blip should not burn all retries in a second
    raise last or RuntimeError("LLM failed with no exception?")

def _read_ontology_snapshot(path: str) -> str:
    """
    Read ontology with line numbers. Works if the imported symbol is either:
    - a LangChain Tool (has .invoke) that expects {"file_path": "..."}
    - a plain function read_file_with_line_numbers(file_path=...)
    Returns a string (content).
    With ONTOLOGY_SNAPSHOT_MAX_LINES > 0 a large ontology is shown as a term index + its last lines (helper/snapshot.py).
    """
    if SNAPSHOT_MAX_LINES > 0:
        known = None
        if REF is not None:
            from helper.reference import local
            known = {local(c) for c in REF.classes} | {local(p) for p in REF.props} | {"sourceClause"}
        compact = compact_snapshot(path, SNAPSHOT_MAX_LINES, known=known)
        if compact is not None:
            return compact
    try:
        if hasattr(read_file_with_line_numbers, "invoke"):
            raw = read_file_with_line_numbers.invoke({"file_path": str(path)})
        else:
            raw = read_file_with_line_numbers(file_path=str(path))
    except TypeError:
        # Some tool signatures may accept a dict directly, try that
        raw = read_file_with_line_numbers({"file_path": str(path)})

    if isinstance(raw, dict) and "content" in raw:
        return str(raw["content"])
    return str(raw)

def _normalize_pages(cqs_obj) -> Dict[str, List[Dict]]:
    """
    Normalize arbitrary CQ JSON into: { "0": [cq, ...], "1": [cq, ...], ... }
    Accepts a few shapes:
      1) {"SomeContractId": {"0": [...], "1": [...], ...}}
      2) {"0": [...], "1": [...], ...}
      3) [ {"page_number": 0, ...}, {"page_number": 1, ...}, ... ]
    """
    if isinstance(cqs_obj, dict) and len(cqs_obj) == 1 and isinstance(next(iter(cqs_obj.values())), dict):
        cqs_obj = next(iter(cqs_obj.values()))
    if isinstance(cqs_obj, dict) and all(isinstance(k, str) and k.isdigit() for k in cqs_obj.keys()):
        return cqs_obj
    if isinstance(cqs_obj, list):
        page_map: Dict[str, List[Dict]] = {}
        for item in cqs_obj:
            pn = int(item.get("page_number", 0))
            page_map.setdefault(str(pn), []).append(item)
        return page_map
    return {}

# --------------------------
# Agents / Nodes
# --------------------------
def domain_expert_node(state: OrchestratorState) -> Dict:
    print("--- 👨‍⚖️ DOMAIN EXPERT ---")

    # Domain-savvy, terse, schema-constrained prompt
    sys = (
        f"{PROFILE['expert_intro']} Extract a concise, precise "
        "Semantic Requirements Document (SRD) from the contract excerpt. Your SRD must be sufficient "
        f"for ontology engineering and SPARQL QA. {PROFILE['expert_knowledge']}"
        "but DO NOT invent facts beyond the text. Normalize:\n"
        "• Money: {amount: number, currency: 'USD'}\n"
        "• Durations: ISO-8601 (e.g., 'P31D', 'P5Y')\n"
        "• Dates: 'YYYY-MM-DD'\n"
        "• Events/States: nouns (Event/State) + clear triggers/guards\n"
        "Be concise. Return ONLY JSON matching the schema below—no prose."
    )

    # Keep it tight; cap list sizes so it stays actionable
    human = (
        "CONTRACT TEXT:\n"
        f"{state['page_text']}\n\n"
        "COMPETENCY QUESTIONS (CQs):\n"
        f"{json.dumps(state['cqs_for_page'], indent=2)}\n\n"
        "SRD JSON SCHEMA (keep entries tight; max: 12 concepts, 12 relationships, 10 rules):\n"
        "{\n"
        '  "key_concepts": [\n'
        '    {"term": str, "type": "Entity|Role|Event|State|Attribute|TimeInterval|MonetaryAmount", "definition": str}\n'
        "  ],\n"
        '  "relationships": [\n'
        '    {"subject": str, "predicate": str, "object": str, "qualifiers": {"when?": str|null, "unless?": str|null}}\n'
        "  ],\n"
        '  "business_rules": [\n'
        '    {"if": str, "then": str, "else?": str|null, "notes?": str|null}\n'
        "  ],\n"
        '  "temporal_constraints": [\n'
        '    {"name": str, "kind": "Duration|Date|Window", "value": "ISO-8601 duration or date", "applies_to": str}\n'
        "  ],\n"
        '  "monetary_values": [\n'
        '    {"name": str, "amount": number, "currency": "USD"}\n'
        "  ],\n"
        '  "state_machine": {\n'
        '    "states": [str],\n'
        '    "transitions": [\n'
        '      {"from": str, "to": str, "trigger": str, "guard?": str|null, "max_duration?": "ISO-8601 duration"|null}\n'
        "    ]\n"
        "  },\n"
        '  "cq_alignment": [\n'
        '    {"cq": str, "supported_by": ["concepts|relationships|rules|temporal_constraints|monetary_values|state_machine"], "notes?": str|null}\n'
        "  ],\n"
        '  "assumptions": [str]\n'
        "}\n\n"
        "INSTRUCTIONS:\n"
        f"• {PROFILE['canonical_terms']}\n"
        "• Use short, plain definitions (1 sentence). No citations are required.\n"
        "• Only include items present or reasonably implied by THIS text. If unknown, omit.\n"
        "• Ensure every CQ maps to cq_alignment or explain briefly why not answerable.\n"
        "• Output JSON only."
    )
    human += _ref_block("expert")

    resp = _retry_invoke(llm, [SystemMessage(content=_p(sys)), HumanMessage(content=_p(human))])

    #print(f"Domain Expert SRD:\n{resp.content.strip()}\n")
    
    return {"requirements_doc": resp.content.strip()}


def manager_node(state: OrchestratorState) -> Dict:
    print("--- 🧑‍💼 MANAGER ---")

    sys = (
        f"You are a Chief Ontology Architect with {PROFILE['manager_expertise']}. "
        "Your job is to translate a Semantic Requirements Document (SRD) into a practical, buildable "
        "Technical Implementation Plan (TIP) that a developer can execute with minimal ambiguity.\n\n"
        "Author a crisp, actionable plan in Markdown (not JSON). Be opinionated, avoid fluff, and prefer "
        "industry-standard Ontology Design Patterns (ODPs). The plan must be implementable in Turtle with "
        "minimal back-and-forth.\n"
        "STYLE GUIDANCE:\n"
        "- Be concise but complete. Short bullets > paragraphs. No generic lectures.\n"
        "- Clearly separate MUST/SHOULD/MAY. Provide concrete class/property names and IRIs.\n"
        "- Tie everything back to the SRD and the competency questions (CQs).\n"
        "- Assume OWL 2 DL profile, open world, monotonic semantics.\n"
        "- do not repeat the direction I gave you in the output. For example, do not say 'For each Competency Question (CQ), provide a concise analysis using the exact format below.' This is a direction for you to follow not something that should be in the output.\n"
        
        """  
Metapatterns:
 Explicit Typing
 Property Reification
 Stubs

Organization of Data:
 Aggregation, Bag, Collection
 Sequence, List
 Tree

Space, Time, and Movement:
 Spatiotemporal Extent
 Spatial Extent
 Temporal Extent
 Trajectory
 Event
 
Agents and Roles:
 AgentRole
 ParticipantRole
 Name Stub

Description and Details:
 Quantities and Units
 Partonymy/Meronymy
 Provenance
 Identifier

Metapatterns: This category contains patterns that can be considered to be ``patterns for patterns.'' In other literature,, they may be called {structural ontology design patterns}, as they are independent of any specific context, i.e. they are content-independent. This is particularly true for the metapattern for property reification, which, while a modelling strategy, is also a workaround for the lack of $n$-ary relationships in OWL. The other metapatterns address structural design choices frequently encountered when working with domain experts. They present a best practice to non-ontologists for addressing language specific limitations.

Organization of Data: This category contains patterns that pertain to how data might be organized. These patterns are necessarily highly abstract, as they are ontological reflections of common data structures in computer science. The pattern for aggregation, bag, or collection is a simple model for connecting many concepts to a single concept. Analogously, for the list and tree pattterns, which aim to capture ordinality and acyclicity, as well. More so than other patterns in this library, these patterns provide an axiomatization as a high-level framework that must be specialized (or modularized) to be truly useful.

Space, Time, and Movement: This category contains patterns that model the movement of a thing through a space or spaces and a general event pattern. The semantic trajectory pattern is a more general pattern for modelling the discrete movements along some dimensions. The spatiotemporal extent pattern is a trajectory along the familiar dimensions of time and space. Both patterns are included for convenience.

Agents and Roles: This category contains patterns that pertain to agents interacting with things. Here, we consider an agent to be anything that performs some action or role. This is important, as it decouples the role of an agent from the agent itself. For example, a Person may be Husband and Widower at some point, but should not be both simultaneously. These patterns enable the capture of this data. In fact, the agent role and participante role patterns are convenient specializations of property reification that have evolved into a modelling practice writ large. In this category, we also include the name stub, which is a convenient instantiation of the stub metapattern; it allows us to acknowledge that a name is a complicated thing, but sometimes we only really need the string representation.

Description and Details: This category contains patterns that model the description of things. These patterns are relatively straightforward, models for capturing ``how much?'' and ``what kind?'' for a particular thing; patterns that are derived from Winston's part-whole taxonomy; a pattern extracted from PROV-O, perhaps to be used to answer ``where did this data come from?''; and a pattern for associating an identifier with something.
"""

    )

    human = (
        "INPUTS\n"
        "------\n"
        "SRD:\n"
        f"{state['requirements_doc']}\n\n"
        "Contract page text (for grounding):\n"
        f"{state['page_text']}\n\n"
        "Competency Questions (CQs):\n"
        f"{json.dumps(state['cqs_for_page'], indent=2)}\n\n"

        "OUTPUT FORMAT (write EXACTLY these Markdown sections):\n"
        "------------------------------------------------\n"
        "1) Conceptual Model & Pattern Guidance\n"
        "   - For the 2-4 most central concepts on this page, provide a modeling guide.\n"
        "   - For each concept, use the following four-part structure. Be brief and clear.\n\n"
        "   **Concept:** `[Cl_ClassName]`\n"
        "   **Represents:** (A succinct, one-sentence definition of the concept).\n"
        "   **Modeling Approach (ODPs):** (List the ODPs to model this concept's facets, e.g., 'Use AgentRole for its function, Temporal Extent for its duration').\n"
        "   **Key Relationships:** (Describe its conceptual links to other key classes, e.g., 'Acts as a temporary state for Cl_Policy').\n\n"
        "2) Reuse & Extension Plan\n"
        "   - Based on the existing ontology content, specify what to reuse and what to create.\n"
        "   - **Extend/Reuse:** List existing classes (e.g., `Cl_Policy`) that should be extended or reused.\n"
        "   - **Create New:** List the essential new classes that must be declared.\n\n"
        "3) Competency Question Alignment\n"
        "   - For each Competency Question (CQ), provide a concise analysis using the exact format below.\n\n"
        "   **Question:** [Full text of the competency question]\n"
        "   **Answer:** [Full text of the expected answer]\n"
        "   **Classes:** [List of core classes needed to answer it]\n"
        "   **ODP:** [The primary ODP required to model the answer]\n\n"

        "AUTHORING RULES\n"
        "--------------\n"
        "- Be specific: propose concrete names and ranges.\n"
        "- Keep total length focused; avoid restating the whole SRD.\n"
        "- Do NOT output code; this is a plan the coder will implement with tools.\n"
        "- Do NOT restate the directions I gave you in the output. For example, do not say 'For each Competency Question (CQ), provide a concise analysis using the exact format below.' This is a direction for you to follow not something that should be in the output.\n"
        "Be PRECISE, CONCISE, and ACTIONABLE.\n"
        f"{_ref_block('manager')}\n"
        "Current ontology file content (line-numbered):\n"
        f"{_read_ontology_snapshot(state['ontology_file_path'])}\n"
    )

    resp = _retry_invoke(llm, [SystemMessage(content=_p(sys)), HumanMessage(content=_p(human))])

    # print the content of the response
    print(f"Implementation Plan:\n{resp.content.strip()}\n")
    
    return {"implementation_plan": resp.content.strip()}


# Tools for coder & qa-coder
CODER_TOOLS = [write_file_with_range, append_to_file]
QA_CODER_TOOLS = [write_file_with_range, read_lines_from_file, append_to_file, grep_file, insert_into_file, insert_at_top_of_file, delete_lines_from_file]

def coder_node(state: OrchestratorState) -> Dict:
    print("--- 💻 CODER ---")
    
    snapshot = _read_ontology_snapshot(state['ontology_file_path'])

    sys = """You are a professional Ontology Developer working with Turtle (.ttl) files. Use ONLY tool calls to append/edit. Do not print code directly. YOU MUST MAKE CHANGES TO THE ONTOLOGY FILE.
    - do not code large chunks of comments or explanations.
    - mainly focus on writing code to the ontology file and only do SMALL comments or explanations.
    
    ### CRITICAL TOOL CALL FORMATTING RULES ###
    1.  You MUST generate all required ontology content in a SINGLE `append_to_file` tool call. Do not perform multiple calls for one page of context.
    2.  The 'content' argument for the tool call MUST be a valid, single-line JSON string.
    3.  All newlines in the generated Turtle code MUST be escaped as `\n`.
    4.  All double quotes (") within the Turtle code (e.g., in comments or string literals) MUST be escaped as `\"`.
    
    Don't append or write everything all at once. Think about what you need to do step by step. Use `append_to_file` to add new content and `write_file_with_range` to fix mistakes in previous content.
    First, add the classes and properties for modeling the main concepts and relationships from the managers plan, the cqs, and the contract.
    Then, instantiate the facts from the contract page text.
    """

    human = f"""USE `append_to_file` or `write_file_with_range` to add or modify triples. Your goal is to make targeted edits so the ontology can answer all Competency Questions (CQs) and represent all details in the contract text, requirements, and existing TTL content.

## MODELING INSTRUCTIONS ##
Follow the managers plan but be faithful to the existing ontology structure for REUSEABILITY and LOGICAL CONSISTENCY. Be faithful to the contract.

here are some possible mistakes that you might make:
1- forgetting to add prefixes at the beginning of the codebase.
2- forgetting to write pivot classes at the beginning before starting to code.
5- you usually forget to write the name of the reification (pivot) that you want to create at the beginning of the output
6- In reification, the reification node (pivot class) is connected to all related classes by object properties, not by the subclassof. it can be a subclass of something, but for reification, it needs object properties.
common mistakes in extracting classes:
1- mistake: not extracting all classes and missing many of them. classes can be found in the story, or in the competency question number and restrictions.
2- Returning empty answer
3- Providing comments or explanations
4- Extra int classes like 'Date', and 'integer' are wrong since they are data properties.
5- not using RDF reification: not extracting pivot classes for modeling relation between classes (more than one class and one data property, or more than two classes)
6- extracting individuals in the text as a class
7- The pivot class is not a sublcass of its components.
common mistakes in the hierarchy extraction:
1- creating an ontology for non-existing classes: creating a new leaf and expanding it into the root
2- returning empty answer or very short
3- Providing comments or explanations
4- Extracting attributes such as date, time, and string names that are related to data properties
5- Forget to add "" around the strings in the tuples
Common mistakes in the object_properties:
1- returning new variables with anything except object_properties
2- returning empty answer or very short
3- providing comments or explanations
4- when the pivot class is created, all of the related classes should point to it (direction of relation is from the classes (domains) 'to'  pivot class (range))
Common mistakes in the data_properties:
1- returning new variables with anything except data_properties
2- returning empty answer or very short
3- providing comments or explanations
4- creating data properties that you will never use
Common mistakes in the workflow:
1- not using any of the tools to write code to the ontology file
2- only adding prefixes and not writing any classes or properties or triples.
Common mistakes in the ontology design:
1- not instantiating the classes and properties, just defining them.
2- not using the defined classes and properties in the ontology.
3- creating redundant classes that are already defined in the ontology.
4- creating triples with the incorrect domain and range.

Failure to follow these formatting rules will break the entire process. Adhere to them strictly.

Before creating new classes/properties, check the existing ontology for reusable or extendable elements via rdfs:subClassOf / rdfs:subPropertyOf. Only create new concepts if unavoidable, and make them abstract and reusable. Apply Ontology Design Patterns: Agent Role (entity vs. function), Participation & Event Reification (central events with participants), Quantity (value + unit), Time Interval (durations, points in time), Situation (changing states), Collection (lists). Ensure all additions integrate hierarchically and cohesively.

DO NOT MISS IMPORTANT FACTS AND DETAILS FROM THE PAGE TEXT. Make sure to populate the facts onto the ontology file with MANY RDF triples. The manager will provide the general guidelines that you MUST follow but the contract page has even more details that you must represent in the ontology file.

these are the prefixes:
@prefix : <http://www.example.org/test#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#>.
@prefix dcterms: <http://purl.org/dc/terms/> .

**Requirements for this page:**
THINK ABOUT WHETHER THE ONTOLOGY YOU ARE CREATING CAN BE USED TO FORM SPARQL QUERIES TO ANSWER THE COMPETENCY QUESTIONS.

The Managers Implementation Plan:
{state['implementation_plan']}

**Context from Current Page ({state["page_number"]}):**
---
{state['page_text']}
---

**PATH to File to APPEND / Edit:** `{state['ontology_file_path']}`
{_ref_block('coder')}
Current ontology file content (line-numbered):
{snapshot}

**Your Task:**
Use the tools to add to the ontology file as described by the requirements and contract and the mistakes to avoid.

INSTANTIATE the facts and don't just define the classes and properties.

When you are done editing the file and writing code, you will be done with your task.
Now 
Use ONLY tool calls to append/edit. Do not print code directly.
"""

    nudge_to_write = """
    Did you contribute new and substantial content to the ontology file? 
    Did you add the new classes from the manager? Did you reuse or extend existing classes and properties where possible?
    If not, please use the tools to append or edit the file.
    If you are done, finish gracefully, don't call any more tools, and do not repeatedly write to the file that you are done. 
    """
    
    if len(state["messages"]) > 0:
        state["messages"].append(HumanMessage(content=nudge_to_write))

    # Put the messages together
    if not state["messages"] or len(state["messages"]) == 0:
        state["messages"].append(SystemMessage(content=sys))
        state["messages"].append(HumanMessage(content=human))
        

    if MAX_TOOL_MESSAGES and len(state["messages"]) > MAX_TOOL_MESSAGES:
        return {"messages": [AIMessage(content="Stopping: tool-iteration cap reached.")]}

    llm_with_tools = llm.bind_tools(CODER_TOOLS)
    
    nudges = [
        "You did not call any tools. Call `append_to_file` or `write_file_with_range` to add content to the ontology file.",
        "Reminder: You need to produce a tool call to add content to the ontology file. Follow the managers plan and the contract text.",
        "Final attempt: respond only with a tool call (`append_to_file` or `write_file_with_range`)."
    ]
    
    max_retries = 3
    if len(state["messages"]) < 3:
        for attempt in range(max_retries):
            ai = _retry_invoke(llm_with_tools, state["messages"])
            if tool_call_count(ai) > 0:
                # success: model produced at least one tool call
                return {"messages": [ai]}
            # no tool calls → add a stronger nudge and retry
            nudge_text = nudges[min(attempt, len(nudges) - 1)]
            state["messages"].append(HumanMessage(content=nudge_text))
    else:
        ai = _retry_invoke(llm_with_tools, state["messages"])

    return {
        "messages": [ai]   
    }

def clear_coder_messages_node(state: OrchestratorState) -> Dict:
    print("--- 🧹 CLEAR CODER MESSAGES ---")
    return {"messages": CLEAR}

def lite_verify_node(state: OrchestratorState) -> Dict:
    """Simplified loop: deterministic checks (grounding, duplicates, source, CQ coverage) instead of an LLM QA review. Free and precise."""
    if state["qa_cycles"] >= MAX_QA_CYCLES:
        print("--- VERIFY SKIPPED (repair rounds used up; unsupported values are stripped afterwards) ---")
        return {"qa_feedback": [], "qa_cycles": state["qa_cycles"], "review_type": 0}
    print(f"--- ✅ VERIFY (deterministic checks, no LLM) --- round {state['qa_cycles']} / {MAX_QA_CYCLES}")
    try:
        import rdflib
        now = rdflib.Graph()
        now.parse(state["ontology_file_path"], format="turtle")
    except Exception:
        return {"qa_feedback": [], "review_type": 0}  # invalid Turtle: the syntax gate handles it next
    fb = grounding.build_feedback(PAGE_BASE, now, state["page_text"], state["cqs_for_page"], SEED_INDS)
    if not fb:
        print("--- all checks passed ---")
        return {"qa_feedback": [], "review_type": 0}
    print("--- VERIFY found:\n" + "\n".join("  - " + x[:200] for x in fb))
    return {"qa_feedback": fb, "qa_cycles": state["qa_cycles"] + 1, "review_type": 1, "messages": CLEAR, "reflection": None}


def qa_review_node(state: OrchestratorState) -> Dict:
    if PIPELINE_MODE == "lite":
        return lite_verify_node(state)
    # Disable QA review after max cycles
    if state["qa_cycles"] >= MAX_QA_CYCLES:
        print("--- ❌ QA REVIEW SKIPPED (max cycles reached) ---")
        return {"qa_feedback": [], "qa_cycles": state["qa_cycles"]}
    
    print("--- 🔎 QA REVIEW (no tools) ---")
    print(f"--- QA CYCLES: {state['qa_cycles']} / {MAX_QA_CYCLES} ---")

    # Snapshot ontology (line-numbered) without using ToolNode
    snapshot = _read_ontology_snapshot(state["ontology_file_path"])

    # Prior feedback context (optional)
    prior_feedback = state.get("qa_feedback") or []
    prior_feedback_text = "\n".join(prior_feedback) if prior_feedback else "None"

    # Force structured output
    structured_llm = qa_llm.with_structured_output(QAReview)

    # --- Prompt templates (no ChatPromptTemplate) ---
    system_prompt = (
        "You are a senior ontology QA engineer. Evaluate whether the provided Turtle (TTL) file "
        "faithfully implements BOTH the Technical Implementation Plan (TIP) and the Contract text.\n\n"
        "Decision policy:\n"
        "• APPROVE (QA_PASSED) when the ontology is materially correct and answers the intent of TIP + contract.\n"
        "  Minor naming/style inconsistencies, docstring nitpicks, or optional pattern choices MUST NOT block approval.\n"
        "• REQUEST CHANGES (CHANGES_REQUIRED) only for issues that affect correctness, coverage, consistency, or reasoning,\n"
        "  e.g., wrong domains/ranges, missing key classes/properties, broken prefixes, invalid TTL, misuse of OWL patterns,\n"
        "  contradictions with the contract, or missing pieces needed to answer the competency questions.\n\n"
        "Feedback policy (when CHANGES_REQUIRED):\n"
        "• Be concise and actionable (max 10 items). No generic suggestions.\n"
        "• Reference line numbers when possible (e.g., 'L123-L140').\n"
        "• Focus on: mapping TIP→TTL, contract coverage, domains/ranges, cardinalities, naming collisions, disjointness,\n"
        "  OWL-Time usage for durations, Role-Objectification and Situation patterns where relevant, and consistency with prefixes.\n"
        "• If suggesting a pattern (Role-Objectification, Situation, OWL-Time), explain why it's needed for this contract/TIP.\n"
        "• Do NOT require external data. Do NOT block on purely stylistic concerns.\n\n"
        "Output: Return ONLY a JSON object matching this schema:\n"
        f"{json.dumps(QAReview.model_json_schema(), indent=2)}\n"
        + _ref_block("qa")
    )

    human_prompt = (
        "--- Contract Text ---\n"
        f"{state['page_text']}\n\n"
        "--- Technical Implementation Plan (TIP) ---\n"
        f"{state['implementation_plan']}\n\n"
        "--- Ontology (line-numbered) ---\n"
        f"{snapshot}\n\n"
        "--- Prior QA feedback (if any) ---\n"
        f"{prior_feedback_text}\n\n"
        "Now judge the TTL against the plan and contract using the policy above. "
        "If everything important is correct and sufficient, return QA_PASSED. "
        "Otherwise, return CHANGES_REQUIRED with up to 3 precise, line-anchored fixes.\n"
        "Check that your previous feedback was addressed and that the ontology is logically consistent.\n If your previous feedback was not addressed, give better advise on exactly what the issue is AND how to solve it.\n"
        
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]
    
    # Retry a few times in case the model gets cheeky
    for attempt in range(5):
        try:
            result: QAReview = structured_llm.invoke(messages)
            print(f"[QA attempt {attempt+1}] ✅ Structured result")
            if result.status == "QA_PASSED":
                return {"qa_feedback": [], "review_type":0}
            else:
                reflection = None
                if state.get("messages") and len(state["messages"]) > 2 and state["review_type"] == 1:
                    # Add a nudge to the coder to reflect on previous attempts
                    # We summarize the feedback, our attempts to fix it, and what went wrong in our last attempt
                    previous_attempts = state["messages"][2:]
                    
                    system_prompt = """
                    You are a professional Ontology Developer working with Turtle (.ttl) files. You have been asked to reflect on your previous attempts to fix the ontology file. You have tried to fix the error but it is still not fixed. You need to create a summary of the error, your attempts to fix it, and what went wrong in your last attempt. You will then use this summary to fix the error.
                    """
                    human_prompt = f"""
                    Your previous attempts to fix the error:
                    {previous_attempts}
                    
                    The current feedback is:
                    {prior_feedback_text}
                    
                    Return an output in this exact format:
                    **EXACT ERROR MESSAGE:** 
                    - What was the previous error message you got from the syntax checker?
                    
                    **ATTEMPTS TO FIX:**
                        - Summarize your attempts to fix the error. What did you try? What did you change? What tools did you use? What did you think the error was? What have you already tried?

                    **Reflection:**
                        - Did your previous attempts fix the error work based on what the current error and previous error messages are or is the error message around the same and your attempts failed?
                        - Did you make correct use of the tools? Were you reading too much that it bloated your context size? Were you reading too little that you missed important context? Did you write enough code to fix the error? Did you write too much code that it was not needed? When you were writing, did you target the correct parts of the ontology file? Did you target the correct classes and properties? Did you target the correct lines in the ontology file?
                        - What are the next steps to fix the error? How will you read differently? How will you write differently? Will you need to read more around the error? How much more or less will you read? Will you need to write more or less code? How will you target the correct parts of the ontology file? How will you target the correct classes and properties? How will you target the correct lines in the ontology file? What should you do differently next time?
                        The programmer has access to a variety of tools. The programmer can `append_to_file`, `write_file_with_range`, `read_lines_from_file`, `grep_file`, `insert_into_file`, `insert_at_top_of_file`, and `delete_lines_from_file` so think about how the tools can be used to fix the error.
                    """
                    
                    reflection = _retry_invoke(llm, [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]).content.strip()
                
                # Ensure it's a plain list[str]
                print(f"""
                {"="*50}
                
                QA requested changes:
                {list(result.feedback)}
                
                {"="*50}
                """)
                return {"qa_feedback": list(result.feedback), "qa_cycles": state["qa_cycles"] + 1, "review_type": 1, "messages": CLEAR, "reflection": reflection}
        except Exception as e:
            print(f"[QA attempt {attempt+1}] ❌ Structured parse failed: {e}")

    print("❌ QA failed to produce structured JSON after 5 attempts.")
    if state.get("review_type") == 1:
        return {"qa_feedback": [], "qa_cycles": state["qa_cycles"] + 1, "review_type": 0, "messages": CLEAR, "reflection": None}
    else:
        return {"qa_feedback": [], "qa_cycles": state["qa_cycles"] + 1, "messages": CLEAR, "reflection": None}


def _parse_tool_status(result: str):
    # Handle JSON or plain strings
    try:
        obj = json.loads(result)
        status = obj.get("status", "").lower()
        return status or "error", obj
    except Exception:
        # Fallback for old tools that returned plain strings
        text = result.lower()
        if "success" in text or "✅" in text:
            return "success", {"message": result}
        return "error", {"message": result}

def syntax_check_node(state: OrchestratorState) -> dict:
    print("\n" + "="*50 + "\nSTEP 1: Checking RDF Syntax\n" + "="*50)
    file_path = state['ontology_file_path']
    raw = verify_rdf_syntax.invoke({"file_path": str(file_path)})
    status, payload = _parse_tool_status(raw)
    if status == "success":
        print("✅ RDF Syntax is valid.")
        return {"syntax_error": None, "messages": CLEAR, "reflection": None, "review_type": 0}  # review_type 0 means no review needed, no errors
    else:
        print(f"❌ RDF Syntax ERROR: {payload.get('message', raw)}")
        
        reflection = None
        if state["messages"] and len(state["messages"]) > 2:
            # Add a nudge to the coder to fix the syntax error
            # We summarize the error, our attempts to fix it, and what went wrong in our last attempt
            previous_attempts = state["messages"][2:]
            
            system_prompt = """
            You are a professional Ontology Developer working with Turtle (.ttl) files. You have been asked to fix a syntax error in the ontology file. You have tried to fix the error but it is still not fixed. You need to create a summary of the error, your attempts to fix it, and what went wrong in your last attempt. You will then use this summary to fix the error.
            """
            human_prompt = f"""
            Your previous attempts to fix the error:
            {previous_attempts}
            
            The current error message is:
            {raw}
            
            Return an output in this exact format:
            **EXACT ERROR MESSAGE:** 
               - What was the previous error message you got from the syntax checker?
               
            **ATTEMPTS TO FIX:**
                - Summarize your attempts to fix the error. What did you try? What did you change? What tools did you use? What did you think the error was? What have you already tried?
                
            **Reflection:**
                - Did your previous attempts fix the error work based on what the current error and previous error messages are?
                - Did you make correct use of the tools? Were you reading too much that it bloated your context size? Were you reading too little that you missed important context? Did you write enough code to fix the error? Did you write too much code that it was not needed?
                - What do you think the next steps are to fix the error? What do you need to do next? How will you read differently? How will you write differently? What will you do differently?
            """
            
            reflection = _retry_invoke(llm, [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]).content.strip()
            
        return {"syntax_error": raw, "debug_cycles": state.get('debug_cycles', 0) + (1 if MAX_DEBUG_CYCLES else 0.01), "reflection": reflection, "messages": CLEAR, "review_type":1}  # keep raw so the bug fixer sees the JSON

def semantics_check_node(state: OrchestratorState) -> dict:
    print("\n" + "="*50 + "\nSTEP 2: Checking OWL Consistency\n" + "="*50)
    file_path = state['ontology_file_path']
    # The reasoner (HermiT via Owlready2) needs Java; without it every check would "fail" and trap the agents in a fix loop.
    if shutil.which("java") is None:
        print("⚠️  Java not found - OWL reasoner check skipped (RDF syntax is still checked).")
        return {"consistency_error": None, "messages": CLEAR, "reflection": None, "review_type": 0}
    raw = verify_owl_consistency.invoke({"file_path": str(file_path)})
    status, payload = _parse_tool_status(raw)
    if status == "success":
        print("✅ OWL Logic is consistent.")
        return {"consistency_error": None, "messages": CLEAR, "reflection": None, "review_type": 0} 
    else:
        print(f"❌ OWL Consistency ERROR: {payload.get('message', raw)}")
        return {"consistency_error": raw, "debug_cycles": state.get('debug_cycles', 0) + 1, "messages": CLEAR, "reflection": None, "review_type": 3}


def qa_coder_node(state: OrchestratorState) -> Dict:
    print("--- 🧯 QA-CODER (bug fixer) ---")

    # Decide mode based on available feedback
    if state.get("qa_feedback") and len(state["qa_feedback"]) > 0:
        mode = "quality assurance"
        feedback = "\n".join(state["qa_feedback"])
    elif state.get("syntax_error") and state["syntax_error"]:
        mode = "SYNTAX"
        feedback = state["syntax_error"]
    elif state.get("consistency_error") and state["consistency_error"]:
        mode = "SEMANTICS"
        feedback = state["consistency_error"]
        
    system_prompt = ""
    
    # print system mode
    print(f"--- QA-CODER MODE: {mode} ---")
    
    if mode == "SYNTAX" or mode == "SEMANTICS":
        system_prompt = """
        You are an expert programmer responsible for fixing errors in an ontology file.
        Your process is:
        1. THINK step-by-step about how to fix the error and why the error is occurring.
        2.  Call `read_lines_from_file` to inspect the code and read around the reported lines to get context. Read 200 lines before and after the error. For example, if the error is on line 250, read from line 50 to line 450. You can also use `grep_file` to match specific patterns. Do not try to read the entire file.
        3. Formulate how you would fix the error based on the feedback and the context you read. Use the tools to make changes to the file. You can use `write_file_with_range` to replace lines in the file, `append_to_file` to add new content, and `insert_into_file` to insert new content at a specific line and push the current line and lines after down. You can also use `delete_lines_from_file` to delete lines from the file, and `insert_at_top_of_file` to insert new content at the top of the file.
        4. For some errors you will need to make LARGER or SUBSTANTIAL changes and in other errors you will only need to change a few lines.
        5. Dont be afraid to make multiple changes in one go if you think they are all needed to fix the error. You can even edit or read many lines before and after the error if you think it will help.
        6. DO NOT REPEAT THE SAME FIXES AS BEFORE if you have already tried them and they did not work. Use the reflection to guide your fixes and avoid making the same mistakes again.
        
        Some of the errors may require deeper structural changes to the ontology, such as renaming classes, adjusting properties, or even rethinking the ontology design. Be faithful to the contract text and follow the QA review feedback closely. Change what they asked you to change. Follow the instructions from the QA. Do not hesitate to make the required changes to the ontology file to fix the error.

        The checklist below contains common mistakes to avoid when writing Turtle syntax. You can use this checklist to guide your fixes.
        **Common mistakes to avoid:**
        - forgetting to add prefixes at the beginning of the codebase.
        - not reading enough lines around the error to get context.
        - reading small segments of lines rather than 200 lines before and after the error.
        - not using the `read_lines_from_file` tool to read the file.
        - not using the `grep_file` tool to match specific patterns.
        - not using the `write_file_with_range` tool to replace lines in the file.
        - not using the `append_to_file` tool to add new content to the file.
        - continously reading and reading without making any changes to the file to fix the error.
        - hesitating to make changes to the file because you are not sure if they will work.
        - not making enough changes to the file to fix the error.
        - repeating the same mistakes as before.
        - not following the reflection steps to guide your fixes and making the same mistakes over and over again.
        """
    else:
        system_prompt = """You are a QUALITY ASSURANCE specialist responsible for fixing issues in an ontology file based on QA feedback. 
        Your process is:
        1. THINK step-by-step about how to fix the error and why the error is occurring.
        2.  Call `read_lines_from_file` to inspect the code and read around the reported lines to get context. Read 200 lines before and after the error. For example, if the error is on line 250, read from line 50 to line 450. You can also use `grep_file` to match specific patterns. This is particularly useful if the feedback references specific classes or properties. Do not try to read the entire file.
        3. Formulate how you would fix the error based on the feedback and the context you read. Use the tools to make changes to the file. You can use `write_file_with_range` to replace lines in the file, `append_to_file` to add new content, and `insert_into_file` to insert new content at a specific line and push the current line and lines after down. You can also use `delete_lines_from_file` to delete lines from the file, and `insert_at_top_of_file` to insert new content at the top of the file.
        
        The QA review feedback will contain specific issues that need to be fixed. This may require class renaming, property adjustments, or even structural changes to the ontology. Be faithful to the contract text and follow the QA review feedback closely. Change what they asked you to change. Follow the instructions from the QA.
        
        Common mistakes to avoid:
        - forgetting to add prefixes at the beginning of the codebase.
        - not reading enough lines around the detailed feedback to get context.
        - reading small segments of lines rather than 200 lines before and after the error.
        - not using the `read_lines_from_file` tool to read the file.
        - not using the `grep_file` tool to match specific patterns.
        - not using the `write_file_with_range` tool to replace lines in the file.
        - not using the `append_to_file` tool to add new content to the file.
        - continously reading and reading without making any changes to the file to fix the error.
        - hesitating to make changes to the file because you are not sure if they will work
        - not making enough changes to the file to fix the error.
    """
    system_prompt += """
    You will also sometimes receive a reflection on previous attempts to fix the error. This will help you understand what went wrong in previous attempts and how to fix the error this time.
    DO NOT REPEAT THE SAME MISTAKES AS BEFORE. Use the reflection to guide your fixes and avoid making the same mistakes again.
    """
    system_prompt += _ref_block("qa_coder")



    heading = ""
    if mode == "SYNTAX":
        heading = "Syntax Error Fix"
    elif mode == "SEMANTICS":
        heading = "OWL Consistency Fix"
    else:
        heading = "Quality Assurance Bug Fix"
        
    

    prompt = f"""The ontology file at `{state['ontology_file_path']}` has a {mode} error.
    Feedback: 
    \n{feedback}\n
    
    Address the issue in the ontology file step by step, using the tools provided.
    """
    
    if mode == "quality assurance":
        prompt += f"""
        The Managers Implementation Plan:
        \n{state['implementation_plan']}\n
        
        The contract text:
        \n{state['page_text']}\n
        """

    if not state["messages"] or len(state["messages"]) == 0:
        print("No previous messages found, creating new ones.")
        state["messages"] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=prompt)
        ]
        if state.get("reflection"):
            prompt += f"\n\n--- REFLECTION ON PREVIOUS ATTEMPTS ---\n{state['reflection']}\n"
            print(f"--- REFLECTION ON PREVIOUS ATTEMPTS ---\n\n{state['reflection']}\n\n")
        
    else:
        state["messages"].append(HumanMessage(content="Address all the issues in the feedback step by step, using the tools provided. Only stop if you have addressed all the issues in the feedback."))
    

    if READ_CONTEXT_LINES != 200:
        system_prompt_msg = state["messages"][0]
        if hasattr(system_prompt_msg, "content"):
            system_prompt_msg.content = system_prompt_msg.content.replace("200 lines", f"{READ_CONTEXT_LINES} lines")
    if MAX_TOOL_MESSAGES and len(state["messages"]) > MAX_TOOL_MESSAGES:
        # every tool result stays in the history and is re-sent on each call: stop runaway read/edit loops
        print(f"--- QA-CODER: tool-loop cap reached ({len(state['messages'])} messages) - handing back to QA ---")
        return {"messages": [AIMessage(content="Stopping: tool-iteration cap reached.")]}

    llm_with_tools = llm.bind_tools(QA_CODER_TOOLS)
    ai = _retry_invoke(llm_with_tools, state["messages"])

    return {"messages": [ai]}



def clear_qa_coder_messages_node(state: OrchestratorState) -> Dict:
    print("--- 🧹 CLEAR QA-CODER MESSAGES ---")
    return {}

# --------------------------
# Routers
# --------------------------
def _gave_up(state) -> bool:
    return bool(MAX_DEBUG_CYCLES) and state.get("debug_cycles", 0) >= MAX_DEBUG_CYCLES

def route_after_syntax(state: OrchestratorState) -> str:
    if state.get("syntax_error") and _gave_up(state):
        print(f"--- repair rounds used up ({MAX_DEBUG_CYCLES}): giving up on this page's syntax error ---")
        return "to_end"
    return "to_qa_coder" if state.get("syntax_error") else "to_semantics"

def route_after_semantics(state: OrchestratorState) -> str:
    if state.get("consistency_error") and _gave_up(state):
        print(f"--- repair rounds used up ({MAX_DEBUG_CYCLES}): giving up on this page's consistency error ---")
        return "to_end"
    return "to_qa_coder" if state.get("consistency_error") else "to_end"
    
def route_after_qa_review(state: OrchestratorState) -> str:
    """Decide where to go after QA review."""
    
    # If QA found issues, go to QA coder
    if state.get("qa_feedback") and len(state["qa_feedback"]) > 0:
        return "to_qa_coder"
    # No QA feedback → continue to syntax check
    return "to_syntax_check"
    

# --------------------------
# Graph
# --------------------------
def build_graph():
    builder = StateGraph(OrchestratorState)

    # Core nodes
    builder.add_node("domain_expert", domain_expert_node)
    builder.add_node("manager", manager_node)
    builder.add_node("coder", coder_node)
    builder.add_node("clear_coder_msgs", clear_coder_messages_node)
    builder.add_node("qa_review", qa_review_node)
    builder.add_node("syntax_check", syntax_check_node)
    builder.add_node("semantics_check", semantics_check_node)
    builder.add_node("qa_coder", qa_coder_node)
    builder.add_node("clear_qa_coder_msgs", clear_qa_coder_messages_node)

    # Tool nodes (only for coder & qa_coder)
    builder.add_node("coder_tools", ToolNode(CODER_TOOLS))
    builder.add_node("qa_coder_tools", ToolNode(QA_CODER_TOOLS))

    # Entry
    builder.set_entry_point("domain_expert")
    builder.add_edge("domain_expert", "manager")
    builder.add_edge("manager", "coder")

    # coder → tools or continue
    builder.add_conditional_edges(
        "coder",
        tools_condition,
        {"tools": "coder_tools", "__end__": "clear_coder_msgs"}
    )
    builder.add_edge("coder_tools", "coder")  # loop tools
    
    # bug review sequence (circular)
    builder.add_edge("clear_coder_msgs", "qa_review")

    # QA sequence (linear)
    builder.add_conditional_edges(
        "qa_review",
        route_after_qa_review,
        {"to_qa_coder": "qa_coder", "to_syntax_check": "syntax_check"}
    )

    builder.add_conditional_edges(
        "syntax_check",
        route_after_syntax,
        {"to_qa_coder": "qa_coder", "to_semantics": "semantics_check", "to_end": END}
    )

    builder.add_conditional_edges(
        "semantics_check",
        route_after_semantics,
        {"to_qa_coder": "qa_coder", "to_end": END}
    )

    # QA-coder → tools or clear → back to qa_review
    builder.add_conditional_edges(
        "qa_coder",
        tools_condition,
        {"tools": "qa_coder_tools", "__end__": "qa_review"}
    )
    builder.add_edge("qa_coder_tools", "qa_coder")
    builder.add_edge("clear_qa_coder_msgs", "qa_review")

    # Compile with MemorySaver (requires configurable IDs at runtime)
    return builder.compile(checkpointer=MemorySaver())

# --------------------------
# Main
# --------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Agent Ontology Generator (single-contract mode)")
    parser.add_argument("--contract", required=True, type=str, help="Path to the contract PDF to process")
    parser.add_argument("--cqs", required=True, type=str, help="Path to the CQs JSON file for that contract")
    parser.add_argument("--ontology-out", required=True, type=str, help="Output TTL path (will be created if missing)")
    parser.add_argument("--recursion-limit", type=int, default=10000, help="LangGraph recursion limit")
    parser.add_argument("--domain", choices=sorted(PROFILES), default="insurance",
                        help="Prompt wording profile (helper/domain_profiles.py). Default reproduces the original insurance prompts.")
    parser.add_argument("--reference", type=str, default=None,
                        help="Reference ontology .ttl (e.g. reference/dart_core.ttl). Seeds the output file and guides every agent; "
                             "new content found in the documents is added as tagged extensions.")
    parser.add_argument("--reference-scope", choices=["core", "wba", "all"], default="core",
                        help="Which part of the reference to use: core (WBA-named entities + what they link to), wba (whole wholesale layer), all")
    parser.add_argument("--start-page", type=int, default=0, help="First page index (0-based) to process")
    parser.add_argument("--max-pages", type=int, default=None, help="Process at most this many pages (cheap pilot runs)")
    parser.add_argument("--page-list", type=str, default=None, help='1-based pages to process, e.g. "5,6,24-33" (all others are skipped)')
    parser.add_argument("--mode", choices=["paper", "lite"], default=None,
                        help="paper = published LLM QA loop (default); lite = deterministic checks instead of the LLM QA review")
    parser.add_argument("--min-words", type=int, default=0, help="Skip pages with fewer words (blank / separator pages)")
    parser.add_argument("--snapshot-max-lines", type=int, default=None,
                        help="Show agents a term index + last lines once the ontology exceeds this many lines (0 = always the full file)")
    args = parser.parse_args()
    if args.snapshot_max_lines is not None:
        SNAPSHOT_MAX_LINES = args.snapshot_max_lines
    if args.mode:
        PIPELINE_MODE = args.mode
    wanted = None
    if args.page_list:
        wanted = set()
        for part in args.page_list.split(","):
            a, _, b = part.strip().partition("-")
            wanted |= set(range(int(a) - 1, int(b or a)))  # 1-based inclusive -> 0-based

    PROFILE = PROFILES[args.domain]
    if args.reference:
        REF = Reference(args.reference, args.reference_scope)
        print(f"📚 Reference ontology: {args.reference} (scope={args.reference_scope}, "
              f"{len(REF.classes)} classes, {len(REF.props)} properties)")

    print("🤖 Initializing Multi-Agent Ontology Generation System...")

    app = build_graph()

    # Resolve paths
    contract_pdf_path = Path(args.contract).expanduser().resolve()
    cqs_path = Path(args.cqs).expanduser().resolve()
    ontology_file_path = Path(args.ontology_out).expanduser().resolve()

    if not contract_pdf_path.exists():
        raise FileNotFoundError(f"Contract PDF not found: {contract_pdf_path}")
    if not cqs_path.exists():
        raise FileNotFoundError(f"CQs file not found: {cqs_path}")

    # Ensure ontology directory exists
    ontology_file_path.parent.mkdir(parents=True, exist_ok=True)

    # Create TTL if missing (with valid Turtle prefixes)
    if not ontology_file_path.exists():
        initial_content = (
            "@prefix : <http://www.example.com/insurance#> .\n"
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
            "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n"
        )
        ontology_file_path.write_text(initial_content, encoding="utf-8")

    # Seed the reference schema into a fresh (prefix-only) file; never overwrite existing work
    if REF is not None:
        from helper.reference import SEED_MARKER
        current = ontology_file_path.read_text(encoding="utf-8")
        has_content = any(l.strip() and not l.lstrip().startswith(("@prefix", "#")) for l in current.splitlines())
        if SEED_MARKER in current:
            print("📚 Reference seed already present - continuing from the existing ontology file.")
        elif has_content:
            print("⚠️  Ontology file already has content but no reference seed; leaving it untouched "
                  "(agents still receive the reference guidance in their prompts).")
        else:
            ontology_file_path.write_text(REF.seed_turtle(), encoding="utf-8")
            print(f"📚 Seeded {ontology_file_path.name} with the reference schema.")

    if REF is not None and PIPELINE_MODE == "lite":  # individuals of the reference seed must not be modified by the documents
        from rdflib import RDF, OWL, URIRef
        seed_g = REF.seed_graph()
        seed_cls = set(seed_g.subjects(RDF.type, OWL.Class))
        SEED_INDS = frozenset(x for x in set(seed_g.subjects(RDF.type, None)) if isinstance(x, URIRef) and x not in seed_cls
                              and not any((x, RDF.type, t) in seed_g for t in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty)))

    progress_path = ontology_file_path.with_suffix(".progress.json")  # next page per document, for resuming

    # Load CQs & normalize to { "0": [...], "1": [...], ... }
    with open(cqs_path, "r", encoding="utf-8") as f:
        cqs_obj = json.load(f)
    pages = _normalize_pages(cqs_obj)

    # Process entire PDF
    pdf_doc = fitz.open(contract_pdf_path)
    contract_id = contract_pdf_path.stem
    try:
        total_pages = len(pdf_doc)
        print(f"\n{'='*60}\n  Processing Contract: {contract_id}\n{'='*60}")

        last_page = total_pages if args.max_pages is None else min(total_pages, args.start_page + args.max_pages)
        for page_number in range(args.start_page, last_page):
            if wanted is not None and page_number not in wanted:
                continue
            cqs_on_page = pages.get(str(page_number), [])
            print(f"\n--- Starting Page {page_number + 1} of {total_pages} ---")

            page_text = pdf_doc[page_number].get_text("text")
            if len(page_text.split()) < args.min_words:
                print(f"--- ⏭️  Page {page_number + 1} skipped ({len(page_text.split())} words) ---")
                continue

            initial_page_state: OrchestratorState = {
                "page_text": page_text,
                "cqs_for_page": [{"page_number": page_number, **cq} for cq in cqs_on_page],
                "ontology_file_path": str(ontology_file_path),
                "page_number": page_number,

                "requirements_doc": "",
                "implementation_plan": "",
    
                "qa_feedback": [],
                "syntax_error": None,
                "consistency_error": None,

                "qa_cycles": 0,
                "debug_cycles": 0,
                "review_type": 0,
                "coder_messages": [],
                "qa_coder_messages": [],
                "messages": [],
            }

            # ✅ Provide checkpointer IDs so MemorySaver works
            config = {
                "recursion_limit": args.recursion_limit,
                "configurable": {
                    "thread_id": f"{contract_id}::page-{page_number}",
                    "checkpoint_ns": "multi_agent_generation",
                },
            }
            # Keep the last good ontology so a page that fails cannot leave invalid Turtle behind for every later page.
            backup = ontology_file_path.read_text(encoding="utf-8")
            if PIPELINE_MODE == "lite":
                import rdflib
                PAGE_BASE = rdflib.Graph()
                PAGE_BASE.parse(data=backup, format="turtle")
            final_state, error = {}, None
            try:
                final_state = app.invoke(initial_page_state, config=config)
            except Exception as e:  # recursion limit, API failure, ... : log it and carry on with the next page
                error = f"{type(e).__name__}: {str(e)[:300]}"

            if error or final_state.get("syntax_error") or final_state.get("consistency_error"):
                print(f"--- ❌ Page {page_number + 1} FAILED after retries. {error or ''}---")
                try:
                    import rdflib
                    rdflib.Graph().parse(str(ontology_file_path), format="turtle")
                    if final_state.get("consistency_error") or final_state.get("syntax_error"):
                        raise ValueError("unresolved error: do not keep this page")
                except Exception:
                    ontology_file_path.write_text(backup, encoding="utf-8")
                    print(f"--- ↩️  Restored the ontology from before page {page_number + 1} (the failed page left an invalid or inconsistent ontology). ---")
            else:
                print(f"--- ✅ Page {page_number + 1} processed successfully. ---")
            if PIPELINE_MODE == "lite" and PAGE_BASE is not None:  # accuracy first: drop numeric values the page does not support
                try:
                    removed = grounding.strip_ungrounded(ontology_file_path, PAGE_BASE, page_text, SEED_INDS)
                    if removed:
                        print(f"[stripped] page={page_number + 1} unsupported_values_removed={removed}", flush=True)
                except Exception as e:
                    print(f"(strip skipped: {type(e).__name__}: {str(e)[:120]})")
            # "Success" only means the checks passed - count what the page really added (a model that never calls the file tools adds nothing).
            try:
                import rdflib
                before, after = rdflib.Graph(), rdflib.Graph()
                before.parse(data=backup, format="turtle")
                after.parse(str(ontology_file_path), format="turtle")
                new_triples = len(after) - len(before)
                print(f"[added] page={page_number + 1} triples={new_triples}", flush=True)
                if new_triples <= 0:
                    print(f"--- ⚠️  Page {page_number + 1} added NO new triples to the ontology. ---", flush=True)
            except Exception:
                pass
            print(f"[usage] calls={USAGE.calls} in={USAGE.tokens_in} out={USAGE.tokens_out}", flush=True)
            progress_path.write_text(json.dumps({**(json.loads(progress_path.read_text()) if progress_path.exists() else {}),
                                                 contract_id: page_number + 1}), encoding="utf-8")

    finally:
        pdf_doc.close()

    print("\n\n✅ Ontology generation complete.")
