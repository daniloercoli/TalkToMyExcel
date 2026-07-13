# TalkToMyExcel Routing

Italian version: [routing.md](routing.md).

TalkToMyExcel answers questions about tabular files by using multiple specialized engines. Routing chooses the best path for each question, without requiring the user to know whether the system will use SQL, semantic search, Python, or a combination of those tools.

## Router and Query Engine

The code separates two responsibilities.

### Router

The router decides which path to try.

Main inputs:

- the user's question
- recent conversation context when the question contains a follow-up reference
- workbook metadata, such as sheets, columns, and semantic columns
- fast heuristics
- the LLM router only for ambiguous cases

Output:

- `route`: the selected primary path
- `reason`: why the path was selected
- `confidence`: confidence score, when available
- `candidates`: ordered paths used as fallbacks or subroutes
- `execution`: execution mode, usually `fallback`, or `multi`

The router does not execute queries, does not read row-level data, and does not produce the final answer. Its job is to build a `RoutePlan`.

### Query Engine

The query engine executes the plan produced by the router.

Main responsibilities:

- execute the primary route
- validate generated SQL before execution
- query DuckDB in read-only mode
- query Chroma for semantic search
- run Python in the sandbox when advanced tabular analysis is needed
- fetch source rows to show as evidence
- try fallbacks when a route fails technically or is unsupported
- build the final answer from the gathered context

In short: the router decides where to go; the query engine does the work and checks whether the result is usable.

## Available Routes

| Route | When it is used |
| --- | --- |
| `count` | Broad deterministic counts, such as totals or standard open/closed distributions. |
| `status` | Deterministic status lookup for a request, matricola, serial, asset, or machine. |
| `sql` | Exact filters, dates, simple aggregates, averages/sums, explicit group-by questions, and column distributions. |
| `semantic` | Fuzzy search over semantic columns such as problem descriptions, notes, checks, and solutions. |
| `hybrid` | SQL structured filtering first, then semantic ranking inside the filtered subset. |
| `multi` | Multi-intent questions, such as a count plus examples or relevant notes. |
| `python` | Sandboxed pandas analysis: cross-file comparisons, missing IDs, ratios, correlations, and custom calculations. |

## Routing Strategy

Routing follows this general order:

1. Deterministic heuristics for obvious cases.
2. LLM router for ambiguous cases.
3. Query engine execution of the route plan.
4. Fallback when the selected route fails technically or is unsupported.

Examples of deterministic choices:

- explicit Python or CSV request -> `python`
- status of a request, matricola, or serial -> `status`
- count plus notes, examples, or similar cases -> `multi`
- structured filter plus fuzzy text -> `hybrid`
- similar text, notes mentioning something, similar symptoms -> `semantic`
- row details or row listing -> `sql`
- comparisons, differences, missing values, ratios, correlations -> `python`
- "for each value of X" or explicit group-by -> `sql`
- simple broad count -> `count`
- exact filters, dates, and simple aggregates -> `sql`

## Follow-up Questions and Context

Questions containing references such as "same thing", "those", or "but only" are contextualized with the last conversation exchange before routing. A request such as "same, but only open" can therefore retain the previous intent while adding a new constraint.

The session retains at most 20 messages per user. History can also contribute to final answer synthesis, while routing uses recent context only when it recognizes a follow-up question. Importing a new dataset or using the clear control in the UI resets the conversation.

## Count vs SQL

`count` and `sql` may look similar, but they serve different purposes.

`count` is meant for broad, common summaries, such as:

- "How many requests do we have?"
- "How many are open and how many are closed?"

`sql` is preferred when the question asks for a specific column, a precise filter, or an explicit grouping:

- "How many requests have status NEW?"
- "How many requests from 2025-11-12 onward?"
- "How many requests are there for each value of STATUS?"
- "Group requests by product line."

This distinction helps after-sales users: simple questions stay immediate, while precise conditions go through a more expressive engine.

## Fallbacks

Each route has an ordered candidate chain. The query engine tries the next candidate only after a technical failure or an unsupported route; zero rows is a valid terminal result.

| Primary route | Candidate chain |
| --- | --- |
| `count` | `count -> sql -> python -> semantic` |
| `sql` | `sql -> python -> semantic` |
| `status` | `status -> sql -> semantic -> python` |
| `semantic` | `semantic -> sql -> python` |
| `hybrid` | `hybrid -> sql -> python` |
| `multi` | `multi -> sql -> semantic -> python` |
| `python` | `python -> sql -> semantic` |

For almost every route, candidates are fallbacks. For `multi`, candidates are subroutes to combine: for example, `sql` for the count and `semantic` for finding notes or relevant cases.

## Hybrid Route

`hybrid` is used when the question combines structured data and free text.

Examples:

- "Find open cases similar to motor vibration."
- "Among requests with status NEW, which ones mention machine?"
- "In Robot WIP cases, which notes mention hydraulic leakage?"

Flow:

1. The system generates a DuckDB query that selects only `row_id` using structured filters.
2. SQL is validated as read-only.
3. DuckDB returns the candidate row subset.
4. Chroma runs semantic search only inside those `row_id` values.
5. The query engine fetches the original rows and builds the answer with sources.

This avoids semantic search over the whole file when the user already provided clear constraints such as status, product, priority, customer, or date.

## Multi Route

`multi` is used when one route is not enough.

Examples:

- "How many open requests are there, and which notes mention vibration?"
- "Count WIP cases and show me cases similar to pressure loss."

Flow:

1. The query engine executes the expected subroutes, usually `sql` and `semantic`.
2. It keeps only successful results.
3. It deduplicates row sources.
4. It asks the final model for one synthesis grounded in the collected results.

## SQL Safety

Generated SQL queries run against DuckDB in read-only mode and are validated before execution.

Main rules:

- queries must start with `SELECT` or `WITH`
- multiple statements are not allowed
- mutating or dangerous keywords are blocked, such as `INSERT`, `UPDATE`, `DELETE`, `DROP`, `COPY`, `PRAGMA`, `ATTACH`, `INSTALL`, and `LOAD`
- the number of rows read into answer context is limited
- debug metadata reports whether the result was truncated

## Semantic Index

Semantic columns selected during import are concatenated per row and stored in Chroma.

By default, TalkToMyExcel keeps one embedding document per spreadsheet row. This works well for short after-sales fields such as problem, checks, notes, and solution.

Optional chunking is controlled with environment variables:

- `SEMANTIC_CHUNK_SIZE=0`: disables chunking. This is the default.
- `SEMANTIC_CHUNK_OVERLAP=0`: overlap between adjacent chunks when chunking is enabled.

When a row is split into chunks, Chroma receives distinct chunk IDs, but each hit is mapped back to the original `row_id`. Answers and sources therefore remain at spreadsheet-row level.

Importing adds only the new dataset's documents to the index; removing a dataset deletes only its associated documents. A full rebuild remains available from the UI or through `POST /api/semantic-index/rebuild` for maintenance and realignment.

## Debug

The response may include debug metadata useful for understanding what happened:

- `debug.route_plan`: primary route, reason, confidence, source strategy, candidates, and execution mode
- `debug.route_attempts`: executed attempts, with status `ok` or `failed`
- route-specific details, such as generated SQL, returned rows, semantic hits, or Python sandbox status

These fields are meant for development, support, and golden set tuning. They are not required for the end user.
