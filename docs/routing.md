# Routing di TalkToMyExcel

Versione inglese: [routing.en.md](routing.en.md).

TalkToMyExcel risponde a domande su file tabellari usando più motori specializzati. Il routing serve a scegliere il percorso più adatto per ogni domanda, senza costringere l'utente a sapere se sotto verrà usato SQL, ricerca semantica, Python o una combinazione.

## Router e Query Engine

Nel codice ci sono due responsabilità diverse.

### Router

Il router decide quale strada tentare.

Input principali:

- domanda dell'utente
- metadati del workbook, come fogli, colonne e colonne semantiche
- euristiche veloci
- router LLM solo per casi ambigui

Output:

- `route`: percorso primario scelto
- `reason`: motivo della scelta
- `confidence`: confidenza, quando disponibile
- `candidates`: percorsi ordinati da usare come fallback o subroute
- `execution`: modalità di esecuzione, di solito `fallback`, oppure `multi`

Il router non esegue query, non legge i dati riga per riga e non produce la risposta finale. Il suo compito è costruire un `RoutePlan`.

### Query Engine

Il query engine esegue il piano prodotto dal router.

Responsabilità principali:

- eseguire la route primaria
- validare SQL generato prima dell'esecuzione
- interrogare DuckDB in sola lettura
- interrogare Chroma per la ricerca semantica
- lanciare Python nella sandbox quando serve analisi tabellare avanzata
- recuperare le righe sorgente da mostrare come fonti
- provare i fallback quando una route non produce risultati affidabili
- costruire la risposta finale con il contesto raccolto

In breve: il router decide dove andare; il query engine fa il lavoro e verifica se il risultato è utilizzabile.

## Route Disponibili

| Route | Quando si usa |
| --- | --- |
| `count` | Conteggi larghi e deterministici, per esempio totali o distribuzioni standard open/closed. |
| `status` | Lookup deterministico dello stato di una richiesta, matricola, seriale, asset o macchina. |
| `sql` | Filtri esatti, date, aggregazioni semplici, medie/somme, group-by espliciti e distribuzioni per colonna. |
| `semantic` | Ricerca fuzzy su colonne semantiche come descrizioni problema, note, verifiche e soluzioni. |
| `hybrid` | Prima filtro strutturato SQL, poi ranking semantico dentro il sottoinsieme filtrato. |
| `multi` | Domande con più intenzioni, per esempio conteggio più esempi o note rilevanti. |
| `python` | Analisi pandas in sandbox: confronti tra file, missing ID, ratio, calcoli custom, dump righe. |

## Strategia di Routing

Il routing segue questo ordine generale:

1. Euristiche deterministiche per casi ovvi.
2. Router LLM per casi ambigui.
3. Esecuzione del piano da parte del query engine.
4. Fallback se la route scelta non produce evidenza sufficiente.

Esempi di scelte deterministicamente riconosciute:

- richiesta esplicita di Python o CSV -> `python`
- stato di una richiesta, matricola o seriale -> `status`
- conteggio più note, esempi o casi simili -> `multi`
- filtro strutturato più testo fuzzy -> `hybrid`
- testo simile, note che citano qualcosa, sintomi simili -> `semantic`
- dettagli o elenco righe -> `python`
- confronti, differenze, mancanti, ratio, correlazioni -> `python`
- "per ciascun valore di X" o group-by esplicito -> `sql`
- conteggio semplice e largo -> `count`
- filtri esatti, date e aggregazioni semplici -> `sql`

## Count vs SQL

`count` e `sql` sembrano simili, ma hanno scopi diversi.

`count` è pensato per riepiloghi larghi e molto comuni, come:

- "Quante richieste abbiamo?"
- "Quante sono aperte e quante chiuse?"

`sql` è preferito quando la domanda richiede una colonna specifica, un filtro preciso o un raggruppamento esplicito:

- "Quante richieste hanno stato NEW?"
- "Quante richieste dal 2025-11-12 in poi?"
- "Quante richieste ci sono per ciascun valore di STATO?"
- "Raggruppa le richieste per linea prodotto."

Questa distinzione aiuta il post-vendita: le domande semplici restano immediate, mentre quelle con condizioni precise passano a un motore più espressivo.

## Fallback

Ogni route ha una catena ordinata di candidati. Se la prima route fallisce, non produce righe o non genera una risposta utilizzabile, il query engine prova la successiva.

| Route primaria | Catena candidati |
| --- | --- |
| `count` | `count -> sql -> python -> semantic` |
| `sql` | `sql -> python -> semantic` |
| `status` | `status -> sql -> semantic -> python` |
| `semantic` | `semantic -> sql -> python` |
| `hybrid` | `hybrid -> semantic -> sql -> python` |
| `multi` | `multi -> sql -> semantic -> python` |
| `python` | `python -> sql -> semantic` |

Per quasi tutte le route i candidati sono fallback. Per `multi`, invece, i candidati sono subroute da combinare: per esempio `sql` per il conteggio e `semantic` per trovare note o casi rilevanti.

## Route Ibrida

`hybrid` serve quando la domanda combina dati strutturati e testo libero.

Esempi:

- "Trova casi aperti simili a vibrazione motore."
- "Tra le richieste con stato NEW, quali citano macchina?"
- "Nei WIP Robot, quali note parlano di perdita idraulica?"

Flusso:

1. Il sistema genera una query DuckDB che seleziona solo `row_id` usando filtri strutturati.
2. La SQL viene validata come read-only.
3. DuckDB restituisce il sottoinsieme di righe candidate.
4. Chroma esegue la ricerca semantica solo dentro quei `row_id`.
5. Il query engine recupera le righe originali e costruisce la risposta con fonti.

Questo evita di cercare semanticamente su tutto il file quando l'utente ha già dato vincoli chiari come stato, prodotto, priorità, cliente o data.

## Route Multi

`multi` serve quando una sola route non basta.

Esempi:

- "Quante richieste aperte ci sono e quali note citano vibrazione?"
- "Conta i WIP e mostrami casi simili a perdita pressione."

Flusso:

1. Il query engine esegue le subroute previste, di solito `sql` e `semantic`.
2. Tiene solo i risultati riusciti.
3. Deduplica le fonti riga.
4. Chiede al modello finale una sintesi unica basata sui risultati raccolti.

## Sicurezza SQL

Le query SQL generate sono eseguite su DuckDB in sola lettura e vengono validate prima dell'esecuzione.

Regole principali:

- devono iniziare con `SELECT` o `WITH`
- non possono contenere più statement
- sono bloccate parole chiave mutanti o pericolose come `INSERT`, `UPDATE`, `DELETE`, `DROP`, `COPY`, `PRAGMA`, `ATTACH`, `INSTALL` e `LOAD`
- il numero di righe lette per il contesto è limitato
- il debug indica se il risultato è stato troncato

## Indice Semantico

Le colonne semantiche selezionate in import vengono concatenate per riga e salvate in Chroma.

Di default TalkToMyExcel mantiene un documento embedding per ogni riga. Questo funziona bene per campi post-vendita brevi, come problema, verifiche, note e soluzione.

Il chunking opzionale si abilita con variabili d'ambiente:

- `SEMANTIC_CHUNK_SIZE=0`: disabilita il chunking. È il default.
- `SEMANTIC_CHUNK_OVERLAP=0`: overlap tra chunk adiacenti quando il chunking è attivo.

Quando una riga viene divisa in chunk, Chroma riceve ID chunk distinti, ma ogni hit viene ricondotto al `row_id` originale. In questo modo le risposte e le fonti restano a livello di riga Excel.

## Debug

La risposta può includere metadati di debug utili per capire cosa è successo:

- `debug.route_plan`: route primaria, motivo, confidenza, strategia sorgente, candidati e modalità di esecuzione
- `debug.route_attempts`: lista dei tentativi eseguiti, con stato `ok`, `no_results` o `failed`
- dati specifici della route, come SQL generata, righe restituite, hit semantici o stato della sandbox Python

Questi campi sono pensati per sviluppo, assistenza e tuning del golden set. Non sono necessari per l'utente finale.
