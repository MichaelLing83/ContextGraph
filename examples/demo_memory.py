"""Demo script showing AgentMemory usage."""

from agent_memory import (
    AgentMemory,
    State,
    RawTrajectory,
)


def main():
    """Demonstrate AgentMemory usage."""

    # Initialize memory (mock mode without Neo4j)
    print("Initializing AgentMemory...")
    memory = AgentMemory(
        neo4j_uri=None,  # Use None for mock mode
        embedding_api_key=None,  # Use mock embedder
    )

    # Simulate a trajectory
    print("\n1. Learning from a trajectory...")
    trajectory = RawTrajectory(
        instance_id="django__django-12345",
        repo="django/django",
        success=True,
        problem_statement="Fix ImportError in models.py",
        steps=[
            {"action": "search", "observation": "Found models.py"},
            {"action": "open", "observation": "Opened models.py"},
            {"action": "edit", "observation": "ImportError: cannot import 'Model'"},
            {"action": "search", "observation": "Found correct import path"},
            {"action": "edit", "observation": "File saved successfully"},
            {"action": "test", "observation": "All 5 tests passed"},
        ],
    )

    traj_id = memory.learn(trajectory)
    print(f"   Trajectory learned: {traj_id}")

    # Query memory during agent run
    print("\n2. Querying memory for current state...")
    current_state = State(
        tools=["bash", "search", "edit", "view"],
        repo_summary="Django web framework",
        task_description="Fix import bug in views.py",
        current_error="ImportError: cannot import name 'View'",
        phase="fixing",
    )

    context = memory.query(current_state)
    print(f"   Methodologies found: {len(context.methodologies)}")
    print(f"   Similar fragments: {len(context.similar_fragments)}")
    print(f"   Warnings: {context.warnings}")

    # Check for loops
    print("\n3. Checking for loops...")
    state_history = []
    for i in range(5):
        state = State(
            tools=["bash"],
            repo_summary="Django",
            task_description="Fix bug",
            current_error="ImportError: cannot import 'Foo'",
            phase="fixing",
        )
        state.last_action_type = "edit"
        state_history.append(state)

    loop_info = memory.check_loop(state_history)
    if loop_info and loop_info.is_stuck:
        print(f"   LOOP DETECTED: {loop_info.description}")
    else:
        print("   No loop detected")

    # Get stats
    print("\n4. Memory statistics...")
    stats = memory.get_stats()
    print(f"   Total trajectories: {stats.total_trajectories}")
    print(f"   Total methodologies: {stats.total_methodologies}")

    print("\nDemo complete!")
    memory.close()


if __name__ == "__main__":
    main()
