"""Neo4j graph store for Agent Memory."""

from typing import Optional, Tuple, List, Dict, Any, TYPE_CHECKING
from neo4j import GraphDatabase, Driver
import logging

if TYPE_CHECKING:
    from agent_memory.models import Trajectory, Fragment, Methodology, ErrorPattern, Strategy, CanonicalRule

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

    def init_schema(self, vector_dimensions: int = 1536) -> None:
        """Initialize Neo4j schema with constraints, indexes, and vector indexes.

        Args:
            vector_dimensions: Dimensionality of embedding vectors (1536 for OpenAI, 256 for Mock).
        """
        schema_queries = [
            # Uniqueness constraints
            "CREATE CONSTRAINT trajectory_id IF NOT EXISTS FOR (t:Trajectory) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT fragment_id IF NOT EXISTS FOR (f:Fragment) REQUIRE f.id IS UNIQUE",
            # Note: State nodes are not persisted with id field, so no constraint needed
            "CREATE CONSTRAINT methodology_id IF NOT EXISTS FOR (m:Methodology) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT error_pattern_id IF NOT EXISTS FOR (e:ErrorPattern) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT community_id IF NOT EXISTS FOR (c:Community) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT strategy_id IF NOT EXISTS FOR (s:Strategy) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT playbook_entry_id IF NOT EXISTS FOR (p:PlaybookEntry) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT canonical_rule_id IF NOT EXISTS FOR (cr:CanonicalRule) REQUIRE cr.id IS UNIQUE",

            # Indexes for common lookups
            "CREATE INDEX trajectory_instance IF NOT EXISTS FOR (t:Trajectory) ON (t.instance_id)",
            "CREATE INDEX trajectory_repo IF NOT EXISTS FOR (t:Trajectory) ON (t.repo)",
            "CREATE INDEX trajectory_success IF NOT EXISTS FOR (t:Trajectory) ON (t.success)",
            "CREATE INDEX fragment_type IF NOT EXISTS FOR (f:Fragment) ON (f.fragment_type)",
            "CREATE INDEX state_phase IF NOT EXISTS FOR (s:State) ON (s.phase)",
            "CREATE INDEX error_pattern_type IF NOT EXISTS FOR (e:ErrorPattern) ON (e.error_type)",
            "CREATE INDEX community_community_id IF NOT EXISTS FOR (c:Community) ON (c.community_id)",
            "CREATE INDEX strategy_category IF NOT EXISTS FOR (s:Strategy) ON (s.category)",
            "CREATE INDEX playbook_prefix IF NOT EXISTS FOR (p:PlaybookEntry) ON (p.prefix)",
            "CREATE INDEX canonical_rule_category IF NOT EXISTS FOR (cr:CanonicalRule) ON (cr.category)",

            # Full-text search indexes (BM25) for keyword matching
            """
            CREATE FULLTEXT INDEX methodology_situation IF NOT EXISTS
            FOR (m:Methodology) ON EACH [m.situation, m.strategy]
            """,
            """
            CREATE FULLTEXT INDEX fragment_description IF NOT EXISTS
            FOR (f:Fragment) ON EACH [f.description]
            """,
            """
            CREATE FULLTEXT INDEX trajectory_summary IF NOT EXISTS
            FOR (t:Trajectory) ON EACH [t.summary]
            """,
            """
            CREATE FULLTEXT INDEX error_keywords_text IF NOT EXISTS
            FOR (e:ErrorPattern) ON EACH [e.error_keywords_text]
            """,
            """
            CREATE FULLTEXT INDEX strategy_text IF NOT EXISTS
            FOR (s:Strategy) ON EACH [s.rule_text]
            """,
            """
            CREATE FULLTEXT INDEX playbook_text IF NOT EXISTS
            FOR (p:PlaybookEntry) ON EACH [p.text]
            """,
            """
            CREATE FULLTEXT INDEX canonical_rule_text IF NOT EXISTS
            FOR (cr:CanonicalRule) ON EACH [cr.rule_text]
            """,
        ]

        # Vector indexes (Neo4j 5.11+)
        vector_queries = [
            f"""
            CREATE VECTOR INDEX fragment_embedding IF NOT EXISTS
            FOR (f:Fragment) ON (f.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {vector_dimensions},
                `vector.similarity_function`: 'cosine'
            }}}}
            """,
            f"""
            CREATE VECTOR INDEX trajectory_embedding IF NOT EXISTS
            FOR (t:Trajectory) ON (t.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {vector_dimensions},
                `vector.similarity_function`: 'cosine'
            }}}}
            """,
            f"""
            CREATE VECTOR INDEX community_embedding IF NOT EXISTS
            FOR (c:Community) ON (c.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {vector_dimensions},
                `vector.similarity_function`: 'cosine'
            }}}}
            """,
            f"""
            CREATE VECTOR INDEX strategy_embedding IF NOT EXISTS
            FOR (s:Strategy) ON (s.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {vector_dimensions},
                `vector.similarity_function`: 'cosine'
            }}}}
            """,
            f"""
            CREATE VECTOR INDEX playbook_embedding IF NOT EXISTS
            FOR (p:PlaybookEntry) ON (p.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {vector_dimensions},
                `vector.similarity_function`: 'cosine'
            }}}}
            """,
            f"""
            CREATE VECTOR INDEX canonical_rule_embedding IF NOT EXISTS
            FOR (cr:CanonicalRule) ON (cr.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {vector_dimensions},
                `vector.similarity_function`: 'cosine'
            }}}}
            """,
        ]

        for query in schema_queries:
            try:
                self.execute_write(query.strip())
                logger.debug(f"Executed schema query: {query[:50]}...")
            except Exception as e:
                # Some queries may fail if already exists, that's OK
                logger.debug(f"Schema query note: {e}")

        for query in vector_queries:
            try:
                self.execute_write(query.strip())
                logger.debug(f"Created vector index: {query[:60]}...")
            except Exception as e:
                # Vector indexes may not be supported on older Neo4j versions
                logger.debug(f"Vector index note (may require Neo4j 5.11+): {e}")

        logger.info("Neo4j schema initialized (vector_dimensions=%d)", vector_dimensions)

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
        """Create a Fragment node and link to Trajectory with temporal edge."""
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
        CREATE (t)-[:HAS_FRAGMENT {t_created: datetime(), t_valid: datetime()}]->(f)
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
        params = error_pattern.to_dict()
        # Add error_keywords_text for BM25 full-text indexing
        params["error_keywords_text"] = " ".join(error_pattern.error_keywords)
        query = """
        MERGE (e:ErrorPattern {error_type: $error_type})
        ON CREATE SET
            e.id = $id,
            e.error_keywords = $error_keywords,
            e.error_keywords_text = $error_keywords_text,
            e.context = $context,
            e.frequency = $frequency
        ON MATCH SET
            e.error_keywords = e.error_keywords + [kw IN $error_keywords WHERE NOT kw IN e.error_keywords],
            e.error_keywords_text = REDUCE(
                s = '',
                kw IN e.error_keywords + [kw IN $error_keywords WHERE NOT kw IN e.error_keywords] |
                CASE WHEN s = '' THEN kw ELSE s + ' ' + kw END
            ),
            e.frequency = e.frequency + $frequency
        """
        self.execute_write(query, params)

    def create_strategy(self, strategy: "Strategy") -> None:
        """Create or update a Strategy node in Neo4j (idempotent)."""
        query = """
        MERGE (s:Strategy {id: $id})
        SET s.rule_text = $rule_text,
            s.category = $category,
            s.source_trajectory_id = $source_trajectory_id,
            s.source_repo = $source_repo,
            s.confidence = $confidence,
            s.embedding = $embedding
        """
        self.execute_write(query, strategy.to_dict())

    def link_strategy_to_trajectory(self, strategy_id: str, trajectory_id: str) -> None:
        """Create DERIVED_FROM relation from Strategy to Trajectory."""
        query = """
        MATCH (s:Strategy {id: $strategy_id})
        MATCH (t:Trajectory {id: $trajectory_id})
        CREATE (s)-[:DERIVED_FROM]->(t)
        """
        self.execute_write(query, {
            "strategy_id": strategy_id,
            "trajectory_id": trajectory_id,
        })

    def create_playbook_entry(self, entry: "PlaybookEntry") -> None:
        """Create a PlaybookEntry node in Neo4j."""
        query = """
        MERGE (p:PlaybookEntry {id: $id})
        SET p.prefix = $prefix,
            p.section = $section,
            p.text = $text,
            p.embedding = $embedding
        """
        self.execute_write(query, entry.to_dict())

    def batch_create_playbook_entries(self, entries: list) -> int:
        """Batch create PlaybookEntry nodes. Returns count created."""
        query = """
        UNWIND $entries AS e
        MERGE (p:PlaybookEntry {id: e.id})
        SET p.prefix = e.prefix,
            p.section = e.section,
            p.text = e.text,
            p.embedding = e.embedding
        RETURN count(p) AS created
        """
        params = [entry.to_dict() for entry in entries]
        results = self.execute_query(query, {"entries": params})
        return results[0]["created"] if results else 0

    def link_fragment_to_error_pattern(self, fragment_id: str, error_type: str) -> None:
        """Create CAUSED_ERROR relation from Fragment to ErrorPattern with temporal properties."""
        query = """
        MATCH (f:Fragment {id: $fragment_id})
        MATCH (e:ErrorPattern {error_type: $error_type})
        MERGE (f)-[r:CAUSED_ERROR]->(e)
        ON CREATE SET r.t_created = datetime(), r.t_valid = datetime()
        """
        self.execute_write(query, {"fragment_id": fragment_id, "error_type": error_type})

    def create_canonical_rule(self, rule: "CanonicalRule") -> None:
        """Create or update a CanonicalRule node in Neo4j."""
        query = """
        MERGE (cr:CanonicalRule {id: $id})
        SET cr.rule_text = $rule_text,
            cr.category = $category,
            cr.prefix = $prefix,
            cr.section = $section,
            cr.member_count = $member_count,
            cr.avg_confidence = $avg_confidence,
            cr.source_repos = $source_repos,
            cr.error_types = $error_types,
            cr.embedding = $embedding
        """
        self.execute_write(query, rule.to_dict())

    def batch_create_canonical_rules(self, rules: list) -> int:
        """Batch create CanonicalRule nodes. Returns count created."""
        query = """
        UNWIND $rules AS r
        MERGE (cr:CanonicalRule {id: r.id})
        SET cr.rule_text = r.rule_text,
            cr.category = r.category,
            cr.prefix = r.prefix,
            cr.section = r.section,
            cr.member_count = r.member_count,
            cr.avg_confidence = r.avg_confidence,
            cr.source_repos = r.source_repos,
            cr.error_types = r.error_types,
            cr.embedding = r.embedding
        RETURN count(cr) AS created
        """
        params = [rule.to_dict() for rule in rules]
        results = self.execute_query(query, {"rules": params})
        return results[0]["created"] if results else 0

    def link_strategy_to_canonical_rule(
        self, strategy_id: str, rule_id: str
    ) -> None:
        """Create MERGED_INTO relation from Strategy to CanonicalRule."""
        query = """
        MATCH (s:Strategy {id: $strategy_id})
        MATCH (cr:CanonicalRule {id: $rule_id})
        MERGE (s)-[:MERGED_INTO]->(cr)
        """
        self.execute_write(query, {
            "strategy_id": strategy_id,
            "rule_id": rule_id,
        })

    def link_canonical_rule_to_error(
        self, rule_id: str, error_type: str
    ) -> None:
        """Create ADDRESSES_ERROR relation from CanonicalRule to ErrorPattern."""
        query = """
        MATCH (cr:CanonicalRule {id: $rule_id})
        MATCH (e:ErrorPattern {error_type: $error_type})
        MERGE (cr)-[:ADDRESSES_ERROR]->(e)
        """
        self.execute_write(query, {
            "rule_id": rule_id,
            "error_type": error_type,
        })

    def export_graph_for_ppr(self) -> Dict[str, Any]:
        """Export adjacency list and node metadata for PPR computation.

        Returns dict with:
            adjacency: dict[node_id] -> list[neighbor_id]
            node_labels: dict[node_id] -> label (e.g. 'CanonicalRule', 'ErrorPattern')
            node_degrees: dict[node_id] -> int
        """
        query = """
        MATCH (a)-[r]->(b)
        WHERE a:Trajectory OR a:Fragment OR a:ErrorPattern OR a:Strategy OR a:CanonicalRule
          AND (b:Trajectory OR b:Fragment OR b:ErrorPattern OR b:Strategy OR b:CanonicalRule)
        RETURN a.id AS src, b.id AS dst, labels(a)[0] AS src_label, labels(b)[0] AS dst_label
        """
        results = self.execute_query(query)

        adjacency: Dict[str, list] = {}
        node_labels: Dict[str, str] = {}
        node_degrees: Dict[str, int] = {}

        for r in results:
            src, dst = r["src"], r["dst"]
            if src is None or dst is None:
                continue

            # Build undirected adjacency
            adjacency.setdefault(src, []).append(dst)
            adjacency.setdefault(dst, []).append(src)

            # Track labels
            if src not in node_labels:
                node_labels[src] = r["src_label"]
            if dst not in node_labels:
                node_labels[dst] = r["dst_label"]

            # Count degrees
            node_degrees[src] = node_degrees.get(src, 0) + 1
            node_degrees[dst] = node_degrees.get(dst, 0) + 1

        return {
            "adjacency": adjacency,
            "node_labels": node_labels,
            "node_degrees": node_degrees,
        }

    def expire_contradictions(
        self, error_type: str, new_methodology_id: str
    ) -> int:
        """Invalidate old RESOLVED_BY edges when a new methodology supersedes.

        Sets t_invalid on old RESOLVED_BY edges for the same error_type,
        keeping only the newest methodology active.

        Returns the number of edges invalidated.
        """
        query = """
        MATCH (e:ErrorPattern {error_type: $error_type})-[r:RESOLVED_BY]->(m:Methodology)
        WHERE m.id <> $new_methodology_id AND r.t_invalid IS NULL
        SET r.t_invalid = datetime()
        RETURN count(r) AS expired
        """
        try:
            results = self.execute_query(query, {
                "error_type": error_type,
                "new_methodology_id": new_methodology_id,
            })
            return results[0]["expired"] if results else 0
        except Exception as e:
            logger.debug("expire_contradictions failed: %s", e)
            return 0
