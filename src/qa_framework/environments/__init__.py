"""Environment contract and engine-free development implementation."""

from qa_framework.environments.base import QAEnvironment
from qa_framework.environments.fake import FakeQAEnvironment

__all__ = ["QAEnvironment", "FakeQAEnvironment"]
