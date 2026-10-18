"""Flows: one Python file per step order. Select one with `pipeline.flow` in factory.yaml."""

from __future__ import annotations

from . import sdlc

FLOWS = {"sdlc": sdlc.run}
