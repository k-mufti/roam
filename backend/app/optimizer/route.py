"""Route ordering within a day: nearest-neighbour construction + 2-opt.

Exact TSP is unnecessary here and the spec says so. A day holds 3-8 stops, where
brute force would actually be tractable (7! = 5040), but the *scheduled* problem
is not symmetric TSP anyway — opening hours make a tour's feasibility depend on
its direction and on waiting time, so an optimal-distance tour can be an
infeasible schedule. Hence a heuristic whose objective is total travel time and
whose moves are validated by the scheduler.

`two_opt` is the classic move: reverse a contiguous segment, keep the reversal
if it shortens the tour, repeat until no improvement. It fixes exactly the
pathology nearest-neighbour creates — the long "return leg" after greedily
walking away from the start.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

Matrix = Sequence[Sequence[float]]


def tour_cost(order: Sequence[int], matrix: Matrix) -> float:
    """Total travel time along `order`. Open tour: no return to the start,
    because a day's itinerary ends at the last stop, not back at the first."""
    return sum(matrix[order[i]][order[i + 1]] for i in range(len(order) - 1))


def nearest_neighbour(matrix: Matrix, start: int = 0) -> list[int]:
    n = len(matrix)
    if n == 0:
        return []
    unvisited = set(range(n)) - {start}
    order = [start]
    current = start
    while unvisited:
        nxt = min(unvisited, key=lambda j: matrix[current][j])
        order.append(nxt)
        unvisited.remove(nxt)
        current = nxt
    return order


def two_opt(
    order: list[int],
    matrix: Matrix,
    *,
    max_passes: int = 50,
    is_feasible: Callable[[list[int]], bool] | None = None,
    cost_fn: Callable[[list[int]], float] | None = None,
) -> list[int]:
    """Improve `order` by segment reversal.

    `is_feasible` lets the caller veto a shorter tour that breaks a constraint
    (typically opening hours). Without that hook, 2-opt would happily produce a
    tour that is 4 minutes shorter and schedules a nightclub at 10am.

    `cost_fn` replaces the default travel-only objective. The scheduler supplies
    one that also charges for *waiting*, which is necessary rather than
    decorative: with a travel-only objective, 2-opt found an order that saved
    five minutes of walking at the cost of nearly three hours standing outside
    closed restaurants, and dutifully reported it as an improvement.
    """
    n = len(order)
    if n < 4:
        return list(order)

    objective = cost_fn or (lambda o: tour_cost(o, matrix))
    best = list(order)
    best_cost = objective(best)

    for _ in range(max_passes):
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                candidate = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                cost = objective(candidate)
                if cost + 1e-9 >= best_cost:
                    continue
                if is_feasible is not None and not is_feasible(candidate):
                    continue
                best, best_cost = candidate, cost
                improved = True
        if not improved:
            break
    return best


def optimize_order(
    matrix: Matrix,
    *,
    start: int = 0,
    is_feasible: Callable[[list[int]], bool] | None = None,
) -> list[int]:
    """Nearest-neighbour seed, then 2-opt. Tries every start and keeps the best.

    Trying all starts is affordable at this size (n<=8) and meaningfully better:
    nearest-neighbour is very sensitive to where it begins.
    """
    n = len(matrix)
    if n <= 1:
        return list(range(n))

    best: list[int] | None = None
    best_cost = float("inf")
    starts = range(n) if n <= 12 else [start]
    for seed in starts:
        order = two_opt(nearest_neighbour(matrix, seed), matrix, is_feasible=is_feasible)
        if is_feasible is not None and not is_feasible(order):
            continue
        cost = tour_cost(order, matrix)
        if cost < best_cost:
            best, best_cost = order, cost

    if best is None:
        # No fully feasible ordering; return the plain shortest tour and let the
        # scheduler drop what it cannot fit.
        return two_opt(nearest_neighbour(matrix, start), matrix)
    return best
