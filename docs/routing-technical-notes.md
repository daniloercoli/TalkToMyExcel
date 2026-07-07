# Note Tecniche Sul Routing

Questo documento tiene separati i riferimenti tecnici e di ricerca dalla guida principale sul routing.

TalkToMyExcel non dipende da LlamaIndex. Il progetto prende ispirazione da alcuni pattern architetturali e li implementa direttamente con il routing locale, DuckDB, Chroma e la sandbox Python.

## Pattern Esterni

- LlamaIndex `RouterQueryEngine`: routing tra query engine usando metadati descrittivi dei tool, inclusi selector e multi-selector per domande ambigue.
- LlamaIndex `SQLAutoVectorQueryEngine`: combinazione di SQL strutturato e retrieval vettoriale invece di trattarli come alternative mutuamente esclusive.
- LlamaIndex `RouterRetriever`: scelta tra retriever descritti come tool e supporto a piani di retrieval compositi.
- Pattern retrieve-then-verify di RealRoute: non fidarsi solo della predizione del router, ma validare evidenza e risultati prima della sintesi finale.
- Routing neuro-simbolico adattivo, come in SymRAG: usare route simboliche economiche per domande semplici e route neurali/codice più costose solo quando servono.

## Mappatura Nel Progetto

- I metadati delle route sono definiti in `app.routing.ROUTE_TOOLS`.
- Il router restituisce un `RoutePlan`; il query engine si occupa di validazione ed esecuzione.
- `hybrid` implementa prima filtro strutturato, poi retrieval semantico sul set filtrato di `row_id`.
- `multi` esegue più subroute e sintetizza i risultati riusciti.
- I fallback sono guidati dall'evidenza: tentativi `no_results` o `failed` passano al candidato successivo.

## Riferimenti

- https://developers.llamaindex.ai/python/examples/query_engine/routerqueryengine/
- https://developers.llamaindex.ai/python/examples/query_engine/sqlautovectorqueryengine/
- https://developers.llamaindex.ai/python/framework/integrations/retrievers/router_retriever/
- https://arxiv.org/abs/2604.20860
- https://arxiv.org/abs/2506.12981
