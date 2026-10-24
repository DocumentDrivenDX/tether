"""Pure context foundations; Engine execution and compaction integration are separate."""

from .strategies import FullContext as FullContext
from .strategies import TailContext as TailContext
from .summarizing import SummarizingContext as SummarizingContext
