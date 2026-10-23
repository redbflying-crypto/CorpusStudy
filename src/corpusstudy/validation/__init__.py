"""Phase 6: citation validator; every reference, quotation and knowledge citation in an
answer is checked against the corpus."""

from corpusstudy.validation.citations import Problem, Report, markers_in, validate

__all__ = ["Problem", "Report", "markers_in", "validate"]
