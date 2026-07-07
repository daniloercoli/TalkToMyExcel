# Mini Guida Per Sviluppatori: Come TalkToMyExcel Risponde Alle Domande

## Idea Generale

TalkToMyExcel non usa un unico metodo per rispondere a tutto. Quando arriva una domanda, il sistema sceglie il motore più adatto tra più possibilità:

- conteggi deterministici
- SQL su DuckDB
- ricerca semantica su Chroma
- combinazione SQL + ricerca semantica
- esecuzione Python in sandbox
- sintesi finale con LLM

Il punto chiave è separare due responsabilità:

- **router**: decide quale strada tentare
- **query engine**: esegue quella strada, valida i risultati e produce la risposta

## Router vs Query Engine

### Router

Il router guarda la domanda e i metadati del workbook: fogli, colonne, colonne semantiche, ecc.

Non legge davvero le righe, non interroga DuckDB, non chiama Chroma e non produce la risposta finale.

Il suo output è un `RoutePlan`, cioè un piano di routing:

```json
{
  "route": "hybrid",
  "reason": "structured_semantic_search",
  "confidence": 1.0,
  "candidates": ["hybrid", "semantic", "sql", "python"],
  "execution": "fallback"
}
```

Il router quindi risponde alla domanda:
**"Qual è il modo migliore per provare a rispondere?"**

### Query Engine

Il query engine prende il `RoutePlan` e lo esegue.

Fa il lavoro operativo:

- esegue DuckDB in sola lettura
- valida SQL generata
- interroga Chroma
- recupera le righe sorgente
- lancia Python in sandbox quando serve
- gestisce fallback
- costruisce il contesto per l'LLM finale
- restituisce risposta, fonti e debug

Il query engine risponde alla domanda:
**"Con questo piano, riesco a produrre una risposta affidabile?"**

## Le Route

| Route | Uso principale |
| --- | --- |
| `count` | Conteggi semplici e larghi, tipo "quante richieste abbiamo?" |
| `status` | Stato di una richiesta, matricola, seriale o macchina specifica |
| `sql` | Filtri esatti, date, aggregazioni, group-by espliciti |
| `semantic` | Ricerca fuzzy su note, descrizioni problema, verifiche, soluzioni |
| `hybrid` | Prima SQL filtra righe, poi Chroma cerca semanticamente dentro quel sottoinsieme |
| `multi` | Domande con più intenzioni, per esempio conteggio + esempi |
| `python` | Analisi pandas: confronti tra file, missing ID, ratio, calcoli custom |

## Esempi Di Routing

```text
"Quante richieste abbiamo?"
-> count
```

```text
"Quante richieste hanno stato NEW?"
-> sql
```

```text
"Qual è lo stato della richiesta UT#001644?"
-> status
```

```text
"Trova casi simili a vibrazione motore"
-> semantic
```

```text
"Tra le richieste aperte, quali citano vibrazione?"
-> hybrid
```

```text
"Quante richieste aperte ci sono e quali note citano vibrazione?"
-> multi
```

```text
"Confronta i due file e dimmi quali matricole mancano"
-> python
```

## Perché Non Usare Sempre LLM

L'LLM non è il database.

Il sistema cerca di usare strumenti deterministici quando possibile:

- DuckDB per dati strutturati
- Chroma per similarità semantica
- Python sandbox per analisi complesse
- LLM solo per routing ambiguo, generazione controllata o sintesi finale

Questo rende le risposte più verificabili e riduce il rischio di allucinazioni.

## Count vs SQL

`count` è per riepiloghi molto comuni e generici.

Esempio:

```text
"Quante richieste ci sono?"
```

`sql` è per domande con condizioni, colonne o raggruppamenti precisi.

Esempio:

```text
"Quante richieste ci sono per ciascun valore di STATO?"
```

Anche se entrambe "contano", la seconda richiede una query più espressiva, quindi va su `sql`.

## Hybrid: SQL + RAG

`hybrid` serve quando la domanda contiene sia vincoli strutturati sia testo libero.

Esempio:

```text
"Tra le richieste con stato NEW, quali citano macchina?"
```

Flusso:

1. SQL seleziona i `row_id` con `STATO = NEW`.
2. Chroma cerca semanticamente solo dentro quei `row_id`.
3. DuckDB recupera le righe originali.
4. L'LLM sintetizza la risposta usando quelle righe.

Questo evita una ricerca semantica su tutto il dataset quando l'utente ha già dato un filtro chiaro.

## Multi: Più Route Insieme

`multi` serve quando la domanda richiede più tipi di evidenza.

Esempio:

```text
"Quante richieste aperte ci sono e quali note citano vibrazione?"
```

Il sistema può fare:

- `sql` per il conteggio
- `semantic` per trovare le note rilevanti
- sintesi finale unica

Qui i `candidates` non sono solo fallback: sono subroute da combinare.

## Fallback

Ogni route ha una catena di fallback.

Esempio per `hybrid`:

```text
hybrid -> semantic -> sql -> python
```

Se `hybrid` non trova righe o fallisce, il query engine prova la route successiva.

Ogni tentativo produce uno stato:

- `ok`
- `no_results`
- `failed`

Questi stati finiscono nel debug.

## Sicurezza

La parte SQL è protetta:

- solo `SELECT` o `WITH`
- niente statement multipli
- blocco di keyword mutanti come `INSERT`, `UPDATE`, `DELETE`, `DROP`
- connessione DuckDB read-only
- limite sulle righe usate come contesto

La parte Python gira in sandbox:

- input montato read-only
- output controllato
- niente rete
- esecuzione isolata

## RAG E Colonne Semantiche

Durante l'import, alcune colonne vengono marcate come semantiche: descrizione problema, note intervento, verifiche, soluzione, ecc.

Per ogni riga, il sistema concatena quelle colonne e crea documenti embedding in Chroma.

Default:

```env
SEMANTIC_CHUNK_SIZE=0
SEMANTIC_CHUNK_OVERLAP=0
```

Con `chunk_size=0`, il comportamento è:

```text
1 riga Excel = 1 documento semantico
```

Se in futuro una riga contiene testo lungo, si può attivare chunking. Anche in quel caso ogni chunk rimane collegato al `row_id` originale.

## Debug Utile Per Sviluppatori

Le risposte possono includere:

```json
{
  "debug": {
    "route_plan": {
      "primary": "hybrid",
      "reason": "structured_semantic_search",
      "candidates": ["hybrid", "semantic", "sql", "python"],
      "execution": "fallback"
    },
    "route_attempts": [
      {"route": "hybrid", "status": "ok", "detail": ""}
    ]
  }
}
```

Questo permette di capire:

- perché è stata scelta una route
- quali fallback sono stati provati
- dove una route ha fallito
- quale SQL è stata generata
- quanti hit semantici sono stati trovati

## Golden Set

Per migliorare il routing usiamo un golden set privato.

Contiene:

- domanda
- route attesa
- risposta attesa
- colonne coinvolte
- eventuali righe sorgente attese
- criterio di verifica

Il file resta in `private/` e non viene committato.

Esempio comando:

```bash
.venv/bin/python scripts/evaluate_golden_qa.py private/golden_qa.json --output private/golden_qa_eval.json
```

Serve per capire se le euristiche coprono bene i casi reali del post-vendita.

## Spiegazione In Una Frase

TalkToMyExcel non "chiede tutto all'LLM": interpreta la domanda, sceglie il motore più affidabile, recupera evidenza dai dati reali e solo alla fine usa il modello per spiegare il risultato in modo leggibile.
