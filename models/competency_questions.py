"""Schema of the competency questions (CQs) produced by agents/cq_generator.py.

The original repository imports this module but does not ship it; it is reconstructed from the generator prompt
("Fill in all fields: competency_question, key_entities, odp_hint, expected_answer") and from the published CQ files
in cqs/generated/, which store exactly `competency_question` and `expected_answer` per question.
"""
from typing import List

from pydantic import BaseModel, Field


class CompetencyQuestion(BaseModel):
    competency_question: str = Field(description="One atomic question the ontology must be able to answer.")
    key_entities: List[str] = Field(default_factory=list, description="Main concepts the question is about.")
    odp_hint: str = Field(default="", description="Ontology Design Pattern the question is meant to drive "
                                                  "(Event Reification, Participation, Quantity, Time Interval, Situation, ...).")
    expected_answer: str = Field(description="The precise value or phrase taken from the document text.")


class CQList(BaseModel):
    questions: List[CompetencyQuestion] = Field(default_factory=list, description="All competency questions for the page.")
