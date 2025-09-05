"""
A-Star (A*) pathfinding algorithm implementation.
"""

import heapq
from typing import List, Tuple, Optional

class Node:
    """A node in the search grid."""
    def __init__(self, parent=None, position=None):
        self.parent = parent
        self.position = position
        self.g = 0  # Cost from start to current node
        self.h = 0  # Heuristic cost from current node to end
        self.f = 0  # Total cost (g + h)

    def __eq__(self, other):
        return self.position == other.position

    def __lt__(self, other):
        return self.f < other.f

    def __hash__(self):
        return hash(self.position)

def astar(
    maze: List[List[int]], 
    start: Tuple[int, int], 
    end: Tuple[int, int]
) -> Optional[List[Tuple[int, int]]]:
    """
    Finds a path from start to end using the A* algorithm.

    Args:
        maze: A 2D list representing the map, where 0 is an obstacle and 1 is walkable.
        start: A tuple (row, col) for the start position.
        end: A tuple (row, col) for the end position.

    Returns:
        A list of tuples as a path from the given start to the given end, or None if no path exists.
    """
    start_node = Node(None, start)
    end_node = Node(None, end)

    open_list = []
    closed_set = set()

    heapq.heappush(open_list, start_node)

    while open_list:
        current_node = heapq.heappop(open_list)
        closed_set.add(current_node.position)

        if current_node == end_node:
            path = []
            current = current_node
            while current is not None:
                path.append(current.position)
                current = current.parent
            return path[::-1]  # Return reversed path

        # Generate children
        for new_position in [(0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            node_position = (current_node.position[0] + new_position[0], current_node.position[1] + new_position[1])

            # Check bounds
            if not (0 <= node_position[0] < len(maze) and 0 <= node_position[1] < len(maze[0])):
                continue

            # Check if walkable (0 is obstacle, any non-zero is walkable)
            if maze[node_position[0]][node_position[1]] == 0:
                continue

            if node_position in closed_set:
                continue

            new_node = Node(current_node, node_position)

            new_node.g = current_node.g + 1
            if new_node.position and end_node.position:
                new_node.h = ((new_node.position[0] - end_node.position[0]) ** 2) + \
                             ((new_node.position[1] - end_node.position[1]) ** 2)
            else:
                new_node.h = 0
            new_node.f = new_node.g + new_node.h

            # Check if neighbor is in open list and if it has a lower f value
            skip_node = False
            for i, open_node in enumerate(open_list):
                if new_node == open_node:
                    if new_node.g > open_node.g:
                        skip_node = True
                    else:
                        # Remove the old node with higher g value
                        open_list[i] = open_list[-1]
                        open_list.pop()
                        heapq.heapify(open_list)
                    break

            if not skip_node:
                heapq.heappush(open_list, new_node)

    return None # Path not found
