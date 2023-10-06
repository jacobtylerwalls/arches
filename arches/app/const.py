from enum import Enum, unique


IntegrityCheckDescriptions = {
    1005: "Nodes with ontologies found in graphs without ontologies",
    1012: "Node Groups without matching nodes",
    2000: "Tiles storing nonexistent concept values",
    2001: "Tiles storing invalid concept values",
}

@unique
class IntegrityCheck(Enum):
    # Nodes
    NODE_HAS_ONTOLOGY_GRAPH_DOES_NOT = 1005
    NODELESS_NODE_GROUP = 1012

    # Tiles
    TILE_STORING_NONEXISTENT_CONCEPT = 2000
    TILE_STORING_INVALID_CONCEPT = 2001

    def __str__(self):
        return IntegrityCheckDescriptions[self.value]
