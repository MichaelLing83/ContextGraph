"""Neo4j graph store for Agent Memory."""

from typing import Optional, Tuple, List, Dict, Any, TYPE_CHECKING
from neo4j import GraphDatabase, Driver
import logging

if TYPE_CHECKING:
    from agent_memory.models import Trajectory, Fragment, Methodology, ErrorPattern

logger = logging.getLogger(__name__)


class Neo4jStore:
    """Neo4j connection manager and query executor."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        auth: Tuple[str, str] = ("neo4j", "password"),
        database: str = "neo4j",
    ):
        self.uri = uri
        self.auth = auth
        self.database = database
        self._driver: Optional[Driver] = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(self.uri, auth=self.auth)
        return self._driver

    def close(self) -> None:
        """Close the driver connection."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> "Neo4jStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def verify_connectivity(self) -> bool:
        """Test connection to Neo4j."""
        try:
            self.driver.verify_connectivity()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            return False

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return results."""
        with self.driver.session(database=self.database) as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def execute_write(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Execute a write query."""
        with self.driver.session(database=self.database) as session:
            session.execute_write(lambda tx: tx.run(query, parameters or {}))

    def init_schema(self) -> None:
        """Initialize Neo4j schema with constraints and indexes."""
        schema_queries = [
            # Uniqueness constraints
            "CREATE CONSTRAINT trajectory_id IF NOT EXISTS FOR (t:Trajectory) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT fragment_id IF NOT EXISTS FOR (f:Fragment) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT state_id IF NOT EXISTS FOR (s:State) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT methodology_id IF NOT EXISTS FOR (m:Methodology) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT error_pattern_id IF NOT EXISTS FOR (e:ErrorPattern) REQUIRE e.id IS UNIQUE",

            # Indexes for common lookups
            "CREATE INDEX trajectory_instance IF NOT EXISTS FOR (t:Trajectory) ON (t.instance_id)",
            "CREATE INDEX trajectory_repo IF NOT EXISTS FOR (t:Trajectory) ON (t.repo)",
            "CREATE INDEX trajectory_success IF NOT EXISTS FOR (t:Trajectory) ON (t.success)",
            "CREATE INDEX fragment_type IF NOT EXISTS FOR (f:Fragment) ON (f.fragment_type)",
            "CREATE INDEX state_phase IF NOT EXISTS FOR (s:State) ON (s.phase)",
            "CREATE INDEX error_pattern_type IF NOT EXISTS FOR (e:ErrorPattern) ON (e.error_type)",

            # Full-text search indexes (for keyword matching)
            """
            CREATE FULLTEXT INDEX methodology_situation IF NOT EXISTS
            FOR (m:Methodology) ON EACH [m.situation, m.strategy]
            """,
        ]

        for query in schema_queries:
            try:
                self.execute_write(query.strip())
                logger.debug(f"Executed schema query: {query[:50]}...")
            except Exception as e:
                # Some queries may fail if already exists, that's OK
                logger.debug(f"Schema query note: {e}")

        logger.info("Neo4j schema initialized")

    def create_trajectory(self, trajectory: "Trajectory") -> None:
        """Create a Trajectory node in Neo4j."""
        query = """
        CREATE (t:Trajectory {
            id: $id,
            instance_id: $instance_id,
            repo: $repo,
            task_type: $task_type,
            success: $success,
            total_steps: $total_steps,
            summary: $summary,
            embedding: $embedding,
            created_at: $created_at
        })
        """
        self.execute_write(query, trajectory.to_dict())

    def get_trajectory(self, trajectory_id: str) -> Optional["Trajectory"]:
        """Get a Trajectory by ID."""
        from agent_memory.models import Trajectory

        query = "MATCH (t:Trajectory {id: $id}) RETURN t"
        results = self.execute_query(query, {"id": trajectory_id})

        if not results:
            return None

        node_data = results[0]["t"]
        return Trajectory.from_dict(node_data)

    def create_fragment(self, fragment: "Fragment", trajectory_id: str) -> None:
        """Create a Fragment node and link to Trajectory."""
        query = """
        MATCH (t:Trajectory {id: $trajectory_id})
        CREATE (f:Fragment {
            id: $id,
            step_range: $step_range,
            fragment_type: $fragment_type,
            description: $description,
            action_sequence: $action_sequence,
            outcome: $outcome,
            embedding: $embedding
        })
        CREATE (t)-[:HAS_FRAGMENT]->(f)
        """
        params = fragment.to_dict()
        params["trajectory_id"] = trajectory_id
        self.execute_write(query, params)

    def create_methodology(self, methodology: "Methodology") -> None:
        """Create a Methodology node."""
        query = """
        CREATE (m:Methodology {
            id: $id,
            situation: $situation,
            strategy: $strategy,
            confidence: $confidence,
            success_count: $success_count,
            failure_count: $failure_count,
            embedding: $embedding,
            source_fragment_ids: $source_fragment_ids
        })
        """
        self.execute_write(query, methodology.to_dict())

    def create_error_pattern(self, error_pattern: "ErrorPattern") -> None:
        """Create an ErrorPattern node."""
        query = """
        MERGE (e:ErrorPattern {error_type: $error_type})
        ON CREATE SET
            e.id = $id,
            e.error_keywords = $error_keywords,
            e.context = $context,
            e.frequency = $frequency
        ON MATCH SET
            e.error_keywords = e.error_keywords + [kw IN $error_keywords WHERE NOT kw IN e.error_keywords],
            e.frequency = e.frequency + 1
        """
        self.execute_write(query, error_pattern.to_dict())
