# okgv, organizing knowledge: graphs and vectors

A system that allows to build a self-organized synthetic knowledge base to train LLMs.

Why?
Coding agents can be used to generate instances that can be used to train/fine-tune a LLM. However, as the number of entries grows it is difficult to keep trace of the already covered topics/categories to
avoid redundancies and ensure coverage of all the examples of interest.

How?
Every entry is grouped into a topic, the agent can be prompted to expand underrepresented topics, create new topics or group entries in a topic in further sub-topics. This graph structure can be naturally
handled by a NoSQL database. To avoid redundancies in the generated instances within a certain topic (or sub-topic) the agent receives feedback on generated instances that are too similar to existing ones.
This similarity computation is performed leveraging a vector database.

## General Structure
The knowledge graph has topics and entries. A topic can have as children both topics and entries. A topic that is child of another topic is called a sub-topic. 

Entries are organized into sub-topics when they exceed a certain number, defined as a custom threshold value (or follow other rules, maybe agent should guide). Organization of an entry into a sub-topic is handled via clustering.
Root topics are defined a-priori, meaning that the generation phase of entries that belong to a topic that is not in the knowledge base must be intentional. This implies that entries without
a parent topic cannot exist in the knowledge base.

The knowledge base makes use of two components:
- Neo4j: used to handle relationships between topics, sub-topics and entries. 
- Weaviate: stores entries content and vector representation.

### Neo4j
Every node has a set of metadata that helps to identify it.

Entry nodes:
- a unique id, obtained via uuid5 hashing of the content of the node.
- Custom metadata, defined by user.
- Optionally, for visual inspection, the actual content of the dataset entry. 

Topic/Sub-topic nodes:
- number of children (only entry nodes are counted, meaning that if a topic node has a child sub-topic which has 10 entry nodes the topic node has 10 children too)

### Weaviate
Used as vector database, it contains the vector representation of every topic and its raw content, identified with the same id used in the graph.

## The generation process
1. User prompts the agent to generate entries in a specific topic  
Compare with the k most similar examples within the reference topic/sub-topic node and check if not redundant

```mermaid
flowchart LR
    A([Start]) --> B[Agent]
 
    G[(Graph Database)] -->|Get number of entries per topic| B
 
    B --> C[Generate entries for underrepresented topic]
 
    C --> D[For each instance]
 
    V[(Vector DB)] -->|Retrieve 5 most similar images| D
 
    D --> E[Agent evaluates if too similar, if yes re-generates]
```

Every session of the process adds a set of nodes, which are identified by their ids. To allow undoing insert operations a log.json file is kept with timestamp as key and list of inserted node ids as values.

## Setup

### Neo4j

Neo4j Desktop is recommended — provides visual graph exploration alongside the database.

1. Download and install [Neo4j Desktop](https://neo4j.com/download/)
2. Create a new Project, then add a local DBMS (any recent version, 5.x recommended)
3. Set a password and start the DBMS
4. Note connection details (default: `bolt://localhost:7687`, user `neo4j`)
5. Optionally install **APOC** plugin via the DBMS plugins panel — useful for bulk operations

Configure connection in your environment:
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

`NEO4J_DATABASE` matches the database name shown in Neo4j Desktop (default database is `neo4j`). To use a different database, create it via Neo4j Desktop → DBMS → Manage → Databases, then set its name here.

### Weaviate

Follow the [official Weaviate installation guide](https://weaviate.io/developers/weaviate/installation) to run a local instance. Docker is the recommended approach for local development.

Configure connection in your environment:
```
WEAVIATE_HOST=localhost
WEAVIATE_PORT=8080
WEAVIATE_COLLECTION=your_collection_name
```

`WEAVIATE_COLLECTION` is the name of the Weaviate collection that stores entry vectors. Collections can be inspected via the Weaviate console at `http://localhost:8080/v1/schema`.

### Entry Schema

okgv does not assume a fixed entry structure. You define your own by writing two classes:

1. **Entry class** — owns field extraction from raw JSON and computed properties.
2. **Schema class** — owns DB mapping (what goes in graph vs vector DB, what text to embed).

Each entry is identified by a deterministic UUID5 computed from its canonical JSON serialization. This is handled automatically.

Create a file (e.g. `schema.py`) in your project:

```python
from okgv.protocols import PropertyDefinition


# Entry class: extracts fields from raw JSON, defines computed properties.
# __init__ receives the raw dict — any missing key raises KeyError,
# which okgv catches and reports as a validation error.
class MyEntry:
    def __init__(self, raw: dict):
        self.text = raw["text"]
        self.label = raw["label"]

    def num_vowels(self) -> int:
        return sum(1 for c in self.text if c in "aeiouAEIOU")

    def text_length(self) -> int:
        return len(self.text)


# Schema class: maps entry to DB properties.
class MySchema:
    entry_class = MyEntry

    @staticmethod
    def metadata(entry: MyEntry) -> dict:
        """Computed metadata — stored in both DBs."""
        return {
            "num_vowels": entry.num_vowels(),
            "text_length": entry.text_length(),
        }

    @staticmethod
    def graph_properties(entry: MyEntry) -> dict:
        """Additional properties for graph DB only."""
        return {"label": entry.label}

    @staticmethod
    def vector_properties(entry: MyEntry) -> dict:
        """Additional properties for vector DB only."""
        return {"text": entry.text}

    @staticmethod
    def embedding_text(entry: MyEntry) -> str:
        """Text used for vector embedding."""
        return entry.text

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        """Collection schema for the vector DB.
        Must cover properties from both metadata() and vector_properties().
        """
        return [
            PropertyDefinition(name="num_vowels", data_type="int"),
            PropertyDefinition(name="text_length", data_type="int"),
            PropertyDefinition(name="text", data_type="text"),
        ]
```

Then set the environment variable:
```
OKGV_SCHEMA=schema:MySchema
```

Format is `module:ClassName` — `module` is the Python module name (resolved relative to the current working directory), `ClassName` is the class inside it.

If `OKGV_SCHEMA` is not set, okgv falls back to a built-in QA schema (see `okgv/schemas/qa.py`).

### Example `.env`

Copy and fill in values:

```env
# Schema
OKGV_SCHEMA=schema:MySchema

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j

# Weaviate
WEAVIATE_HOST=localhost
WEAVIATE_PORT=8080
WEAVIATE_COLLECTION=your_collection_name
```
