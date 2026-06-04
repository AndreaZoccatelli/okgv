# Schema Setup Guide

You are helping the user define an entry schema for their synthetic dataset. The schema determines the structure of every entry in the knowledge base.

## What you need to find out

Ask the user these questions to build the schema. Do not assume answers.

### 1. Dataset purpose
- What is this dataset for? (e.g. fine-tuning a classifier, building a Q&A set, generating training pairs)
- What downstream task will consume the data?

### 2. Entry fields
- What fields should each entry have? (e.g. text, question, answer, label, code)
- Which fields are the main content vs metadata?
- Which field(s) should be used for similarity search / deduplication?

### 3. Constraints and valid values
- Are there fields with a fixed set of valid values? (e.g. difficulty: easy/medium/hard)
- Are there numeric fields with valid ranges? (e.g. score: 0-100)
- Are there fields that must be non-empty?
- For each constrained field: what does each valid value mean? (this helps agents generate correct entries)

### 4. Balancing
- Which fields should the dataset be balanced across? (e.g. difficulty, category)
- Not all fields need balancing. Computed metadata like text length or fields derived from topic structure typically don't.

### 5. Storage layout
- Which fields should be searchable in the graph DB? (typically: labels, categories, metadata)
- Which fields should be stored in the vector DB? (typically: text content for retrieval)
- Which fields should be in both? (typically: shared metadata like counts or categories)

## What you produce

After gathering answers, create a `candidate_schema.py` with:

1. **Validators** at module level using `okgv.validators` (`OneOf`, `InRange`, `NotEmpty`, `Matches`)
2. **Entry class** that validates fields in `__init__` using the validators
3. **Schema class** with:
   - `entry_class` pointing to the Entry class
   - `validators` list for prompt generation
   - `field_descriptions` dict for agent instructions (use tuples for fields that need per-option explanations)
   - `balance_fields` list of field names the dataset should be balanced across
   - `metadata()` returning fields stored in both DBs
   - `graph_properties()` returning graph-only fields
   - `vector_properties()` returning vector-only fields
   - `embedding_text()` returning the text to embed for similarity
   - `vector_property_definitions()` listing all vector DB fields with types

Refer to `config/schema.py` for the template structure.

## Validation

After creating the schema, verify it works:
```bash
okgv entry-prompt        # check field descriptions render correctly
```

## Interact with user

If everything works correctly, tell user to review it and remind that schema path should be specified in .env file. 
