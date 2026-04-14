## ADDED Requirements

### Requirement: ChromaDB vector search for semantic queries
The system SHALL use ChromaDB for vector-based semantic search, returning top-k=10 results. Embeddings SHALL be generated for all course documents and stored in ChromaDB with metadata (course_id, semester, doc_type).

#### Scenario: Semantic query returns relevant documents
- **WHEN** a user asks "What are the prerequisites for machine learning?"
- **THEN** ChromaDB returns the top-10 semantically similar documents

#### Scenario: Metadata filtering narrows search space
- **WHEN** a query includes context for a specific course_id and semester
- **THEN** ChromaDB filters by those metadata fields before computing similarity

### Requirement: PostgreSQL keyword filtering for precise lookups
The system SHALL use PostgreSQL for keyword-based structured filtering on fields including course_id, semester, and doc_type.

#### Scenario: Precise lookup by course and semester
- **WHEN** a user asks "CS101 Fall 2025 syllabus"
- **THEN** PostgreSQL returns documents matching course_id=CS101, semester=Fall2025, doc_type=syllabus

### Requirement: Reciprocal Rank Fusion merges results
The system SHALL merge ChromaDB vector results and PostgreSQL keyword results using Reciprocal Rank Fusion (RRF). Each result receives a score of `1 / (k + rank)` from each source, and scores are summed. The top-5 documents after fusion SHALL be passed to the LLM context window.

#### Scenario: RRF merges results from both sources
- **WHEN** ChromaDB returns 10 results and PostgreSQL returns results for a query
- **THEN** RRF computes fused scores and returns the top-5 documents ranked by combined score

#### Scenario: Document appears in only one source
- **WHEN** a document appears in ChromaDB results but not PostgreSQL results
- **THEN** the document's RRF score is computed from its ChromaDB rank only

### Requirement: Hybrid retrieval achieves measurable precision improvement
The hybrid retrieval pipeline SHALL achieve approximately 75% context precision measured against 100 human-annotated query-document pairs, compared to a 50% baseline with vector-only search.

#### Scenario: Precision evaluation against ground truth
- **WHEN** the evaluation harness runs against the 100 annotated pairs
- **THEN** hybrid retrieval context precision is at least 70% (target 75%)
