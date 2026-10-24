"""Tether invariant facade; Pydantic AI Agent owns the execution loop."""

from .agent import Engine as Engine
from .recovery import plan_resume as plan_resume
