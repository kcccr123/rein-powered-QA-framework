"""Receive environment output and evaluate QA requirements without controlling gameplay."""
from qa_framework.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    EvaluationReport,
    UnitReport,
    evaluate_unit,
)
from qa_framework.receiver import QAReportReceiver

__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "EvaluationReport",
    "UnitReport",
    "QAReportReceiver",
    "evaluate_unit",
]
