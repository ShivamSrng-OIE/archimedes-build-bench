"""The architecture delta graph that links the failure to repository hazards.

``model`` defines the node, edge and graph types; ``builder`` creates the
initial graph for a Case. The graph is diagnostic: its size is reported in the
trace and the repair episode.
"""

from .model import DeltaGraph

__all__ = ["DeltaGraph"]
