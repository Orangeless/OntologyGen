"""Domain wording for the multi-agent prompts.

The original pipeline was written for life-insurance contracts. A profile swaps the domain-specific
sentences (persona, canonical terms, currency) without touching the pipeline logic.
"insurance" reproduces the original prompts exactly.
"""

PROFILES = {
    "insurance": {
        "currency": "USD",
        "expert_intro": "You are a senior life-insurance legal analyst (US practice).",
        "expert_knowledge": (
            "Use industry knowledge (e.g., grace period, lapse, reinstatement, contestability, "
            "suicide clause, misrepresentation) to identify implicit structure, "
        ),
        "canonical_terms": (
            "Prefer canonical insurance terms (Policy, Policy Owner, Beneficiary, Premium, Grace Period, "
            "Lapse, Reinstatement, Contestability Period)."
        ),
        "manager_expertise": "deep expertise in life-insurance law and OWL 2 DL",
    },
    "wba": {
        "currency": "AUD",
        "expert_intro": (
            "You are a senior analyst of Australian wholesale telecommunications agreements (the NBN Co "
            "Wholesale Broadband Agreement: Head Terms, Dictionary, Product/Service Terms, Product and Service "
            "Descriptions, Technical Specifications, Service Levels Schedules, Price Lists, Discounts Credits and "
            "Rebates annexures, Operations Manuals, Fair Use and Credit policies)."
        ),
        "expert_knowledge": (
            "Use industry knowledge (e.g., NNI Group, NNI Bearer, CVC, AVC, CSA, POI, traffic classes TC-1/TC-2/TC-4, "
            "Bearer Limit, Product Component, service levels, rebates, fair use) to identify implicit structure, "
        ),
        "canonical_terms": (
            "Prefer the defined terms exactly as capitalised in the text (e.g., NNI Group, NNI Bearer, CVC, AVC, CSA, POI, "
            "Product Component, Bearer Limit, Service Level, Network Availability). Capture every numeric limit, rate "
            "(Gbps/Mbps), percentage, charge, duration and threshold together with the conditions under which it applies "
            "(rate, redundancy mode, product, region)."
        ),
        "manager_expertise": "deep expertise in telecommunications wholesale agreements and OWL 2 DL",
    },
}
