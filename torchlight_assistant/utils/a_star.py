"""
A-Star (A*) pathfinding algorithm implementation.
"""

import heapq
import math
from typing import List, Optional, Sequence, Tuple

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
    maze: Sequence[Sequence[int]],
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
    try:
        rows = len(maze)
        cols = len(maze[0]) if rows else 0
    except (TypeError, IndexError):
        return None
    if rows == 0 or cols == 0:
        return None
    if not (
        0 <= start[0] < rows
        and 0 <= start[1] < cols
        and 0 <= end[0] < rows
        and 0 <= end[1] < cols
    ):
        return None
    if maze[start[0]][start[1]] == 0 or maze[end[0]][end[1]] == 0:
        return None

    start_node = Node(None, start)
    end_node = Node(None, end)

    open_list = []
    closed_set = set()
    best_g = {start: 0.0}

    heapq.heappush(open_list, start_node)

    while open_list:
        current_node = heapq.heappop(open_list)
        if current_node.g != best_g.get(current_node.position):
            continue
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

            # 斜向移动不能穿过两个正交障碍形成的墙角。否则规划结果在栅格上
            # 看似连通，游戏角色实际会撞在拐角处。
            if new_position[0] != 0 and new_position[1] != 0:
                side_a = (current_node.position[0] + new_position[0], current_node.position[1])
                side_b = (current_node.position[0], current_node.position[1] + new_position[1])
                if maze[side_a[0]][side_a[1]] == 0 or maze[side_b[0]][side_b[1]] == 0:
                    continue

            if node_position in closed_set:
                continue

            step_cost = math.sqrt(2.0) if new_position[0] and new_position[1] else 1.0
            tentative_g = current_node.g + step_cost
            if tentative_g >= best_g.get(node_position, math.inf):
                continue

            new_node = Node(current_node, node_position)
            new_node.g = tentative_g
            delta_row = abs(new_node.position[0] - end_node.position[0])
            delta_col = abs(new_node.position[1] - end_node.position[1])
            # Octile distance is admissible/consistent for 8 directions with
            # cardinal cost 1 and diagonal cost sqrt(2).
            diagonal = min(delta_row, delta_col)
            straight = max(delta_row, delta_col) - diagonal
            new_node.h = straight + diagonal * math.sqrt(2.0)
            new_node.f = new_node.g + new_node.h
            best_g[node_position] = tentative_g
            heapq.heappush(open_list, new_node)

    return None # Path not found
