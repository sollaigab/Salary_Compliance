# Osservatorio retribuzioni negli annunci di lavoro

Dal 7 giugno 2026 il D.Lgs. 96/2026 obbliga chi pubblica un annuncio di lavoro a indicare la retribuzione iniziale o la fascia. Questo progetto raccoglie gli annunci delle principali aziende che assumono in Italia e misura quanti indicano davvero una cifra, quanti usano formule vaghe come "commisurata all'esperienza" o "secondo CCNL" e quali RAL offrono per settore, funzione, seniority e regione.

A ottobre 2026 le aziende analizzate sono 216; 56 hanno annunci scaricabili, per circa 2.400 annunci in Italia. I risultati sono pubblicati in una pagina web con soli dati aggregati per settore.

## Avvio rapido

Serve Python 3.11 o successivo.

```bash
pip install -r requirements.txt
python -m playwright install chromium    # serve solo alla scoperta degli ATS (pagine in JavaScript)
python -m pytest -q                      # controlla che tutto funzioni
```

Il database `data/jobs.db` non è nella repository. Per ricrearlo da zero:

```bash
python discover_ats.py          # quale sistema di selezione usa ogni azienda (circa 2 ore la prima volta)
python crawl_jobs.py            # scarica gli annunci (circa 1 ora)
python export.py public         # genera i dati della pagina web
python -m http.server -d webapp 8000    # apri http://localhost:8000
```

## Come si aggiorna

Il lavoro ricorrente è uno solo: riscaricare gli annunci. Lo fa un'attività pianificata di Windows, "Osservatorio RAL - crawl settimanale", ogni lunedì alle 7:00. Se a quell'ora il PC è spento, parte appena lo si riaccende. L'attività lancia `scripts/crawl_settimanale.ps1`, che scarica gli annunci senza cache, rigenera l'export privato e quello pubblico e scrive un log in `data/logs/`.

Comandi utili da PowerShell, nella cartella del progetto:

```powershell
# l'attività esiste e quando parte?
Get-ScheduledTask -TaskName "Osservatorio RAL - crawl settimanale" | Get-ScheduledTaskInfo

# prova senza fare richieste ai siti
powershell -ExecutionPolicy Bypass -File scripts\crawl_settimanale.ps1 -Prova

# lancia subito il crawl vero, senza aspettare lunedì
Start-ScheduledTask -TaskName "Osservatorio RAL - crawl settimanale"

# registra di nuovo l'attività (dopo averla cancellata o spostato la cartella)
powershell -ExecutionPolicy Bypass -File scripts\registra_crawl_settimanale.ps1

# rimuovi l'attività
Unregister-ScheduledTask -TaskName "Osservatorio RAL - crawl settimanale" -Confirm:$false
```

Dopo ogni crawl:

1. Leggi l'ultimo file in `data/logs/`. In fondo trovi il totale degli annunci e le board andate in errore.
2. Se una board continua a dare errore per più settimane, controlla l'azienda con `python discover_ats.py --only "Nome azienda" --force`.
3. Fai il commit dei dati pubblici aggiornati: `git add webapp/data` e poi `git commit -m "Aggiornamento dati"`.

Ogni mese circa conviene rilanciare `python discover_ats.py`, per trovare aziende che hanno cambiato sistema di selezione. Il comando salta da solo quelle controllate negli ultimi 7 giorni.

Quando si modificano le regole di estrazione (`lib/salary.py`, `lib/enrich.py`) non serve riscaricare nulla:

```bash
python crawl_jobs.py --reprocess    # ricalcola i campi sugli annunci già salvati
python export.py public
```

## Come funziona

Il lavoro è diviso in tre passi, ciascuno con il suo script.

1. `discover_ats.py` scopre quale sistema di selezione (ATS) usa ogni azienda della lista `data/companies_seed.csv`. Cerca le tracce dell'ATS nella pagina carriere, anche con un browser vero per le pagine costruite in JavaScript, e prova i nomi più probabili sulle API pubbliche. Il risultato va nella tabella `ats_registry` e in `data/ats_registry.csv`. Le correzioni scritte a mano in `data/manual_overrides.csv` hanno sempre la precedenza.
2. `crawl_jobs.py` scarica gli annunci delle aziende pronte, tiene quelli in Italia o da remoto e li arricchisce: sede normalizzata sull'elenco ISTAT dei comuni, funzione, seniority, tipo di contratto, modalità di lavoro e retribuzione. Tiene anche lo storico (`first_seen`, `last_seen`, `is_active`).
3. `export.py` produce le uscite: una copia completa per le analisi in DuckDB (privata) e i dati aggregati per la pagina web (pubblici). `search.py` cerca negli annunci salvati e tiene lo storico delle ricerche.

### Fonti

Si usano solo endpoint e pagine pubblici, senza login.

| ATS | Fonte |
|---|---|
| Greenhouse, Lever, Ashby, Workable, Recruitee | API pubbliche dei job board, documentate dal fornitore |
| Personio | feed XML pubblico delle offerte |
| Workday, Oracle Recruiting | endpoint JSON usato dalla pagina carriere |
| Teamtailor | feed RSS `/jobs.rss` del sito carriere |
| SuccessFactors | `sitemap.xml` del sito carriere e dati schema.org/JobPosting di ogni annuncio |

Alcuni ATS vengono riconosciuti ma non scaricati, e restano nel registro con `supported = 0` per misurare la copertura. SmartRecruiters è escluso perché il `robots.txt` della sua API vieta l'accesso automatico. Taleo, inRecruiting, Avature, Altamira, Phenom, Eightfold, iCIMS, Cornerstone e SuccessFactors senza sitemap non hanno un feed pubblico.

### Regole di raccolta

Le applica `lib/http.py` a ogni richiesta, comprese quelle alle API.

- Rispetta il `robots.txt` di ogni host.
- Rispetta la riserva sul text and data mining prevista dall'art. 70-quater della L. 633/1941: se un sito la dichiara con `/.well-known/tdmrep.json` o con l'header `tdm-reservation: 1`, il contenuto non viene usato.
- Aspetta tra una richiesta e l'altra: 1 secondo sui siti aziendali, circa 0,35 secondi sulle API, con un solo ritmo per tutti i server Workday.
- Se un servizio risponde 429 chiedendo una pausa lunga, non lo contatta più fino alla scadenza indicata. I blocchi sono salvati in `data/cache/blocked_hosts.json` e valgono anche per i run successivi.
- Toglie email e numeri di telefono dalle descrizioni prima di salvarle.

### Retribuzione

Se l'ATS ha un campo dedicato alla retribuzione, si usa quello. Altrimenti la cifra si cerca nel testo con le regole di `lib/salary.py`. Ogni annuncio finisce in una di tre classi (`salary_transparency`): cifra, solo formula vaga, nessuna indicazione.

Le cifre diventano RAL annua lorda in euro: le mensili si moltiplicano per 14, o per 12 negli stage. Paghe orarie o giornaliere, cifre nette e altre valute contano come "cifra" ma non vengono convertite. Un valore annuo fuori dall'intervallo 5.000-500.000 euro viene scartato, perché di solito è un altro importo (un'indennità, il fatturato dell'azienda). Le cifre vicine a parole come "ricavi", "ordini" o "mld" vengono ignorate.

### Seniority

Il livello si legge prima dal titolo ("Senior", "Junior", "Head of"). Se il titolo non lo indica, si deduce dagli anni di esperienza richiesti nel testo: meno di 2 anni junior, da 2 a 4 intermedio, da 5 in su senior. La colonna `seniority_source` dice da dove viene il livello (`titolo`, `esperienza`, `non_indicata`). Sigle come "CFO Services" o "CEO Office" sono nomi di practice e non contano come ruoli dirigenziali.

### Periodo rispetto alla legge

`posting_period` vale `post_legge` per gli annunci pubblicati dal 7 giugno 2026, `pre_legge` per quelli precedenti ma pubblicati entro 12 mesi dall'osservazione e `storico` per quelli online da più tempo, che di solito sono posizioni sempre aperte. Le statistiche principali usano solo `post_legge`.

## Pagina web e dati pubblici

La pagina in `webapp/` è un sito statico (HTML, CSS e JavaScript, senza build). Mostra solo dati aggregati: non contiene annunci singoli, nomi, titoli o link delle aziende.

`python export.py public` scrive in `webapp/data/`:

- `aggregati.json`, letto dalla pagina, e `aggregati.csv`, scaricabile. Ogni riga è una combinazione di periodo, macro-settore e una dimensione (regione, funzione, seniority, contratto, modalità o periodo di pubblicazione).
- `meta.json`, con le date della rilevazione e i conteggi citati nella metodologia.
- `labels.json`, con i nomi leggibili dei valori.

Le regole che proteggono l'identità delle aziende sono in `lib/disclosure.py`. Una combinazione viene pubblicata solo se riunisce almeno 5 annunci di almeno 3 aziende e se nessuna azienda supera il 70% dei suoi annunci. Le mediane RAL seguono le stesse soglie. Quando in un gruppo una sola combinazione è nascosta, si nasconde anche la più piccola delle altre, perché non la si possa ricavare per differenza dal totale. I settori sono riuniti in 6 macro-settori da almeno 3 aziende ciascuno (`MACRO_OF_SECTOR` in `lib/labels.py`).

I dati per azienda e per annuncio restano sul PC, in `data/jobs.db` e nell'export DuckDB, entrambi esclusi da git.

La cartella `webapp/` si può pubblicare così com'è su GitHub Pages o Cloudflare Pages. Prima di farlo vanno completati la validazione della retribuzione, la revisione delle condizioni d'uso dei siti usati e una verifica legale.

## Validazione

L'estrazione della retribuzione va misurata a mano prima di pubblicare i risultati.

```bash
python validate_salary.py sample                       # campione di circa 175 annunci da etichettare
python validate_salary.py sample --exclude <csv già fatti>
python validate_salary.py refresh <csv>                # aggiorna le righe non ancora giudicate
python validate_salary.py score <csv>                  # precisione e recall
```

Nel CSV, la colonna `giudizio` accetta `ok`, `errato`, `parziale`, `mancata` e `nessuna`. I file stanno in `data/validation/` e restano privati, perché contengono frasi degli annunci.

## Riferimento comandi

```bash
# scoperta degli ATS
python discover_ats.py --only "companyc,companyb" --force   # alcune aziende, anche se controllate da poco
python discover_ats.py --retry-none                     # riprova le aziende senza ATS trovato
python discover_ats.py --workday-only                   # solo la ricerca dei tenant Workday
python discover_ats.py --no-browser                     # senza Playwright, più veloce
python discover_ats.py --report                         # report di copertura

# annunci
python crawl_jobs.py --only "companyb,examplecorp"
python crawl_jobs.py --reprocess

# ricerca ed export
python search.py "data analyst" --location milano --periodo post_legge
python export.py duckdb                                 # data/export/osservatorio.duckdb, privato
python export.py public                                 # webapp/data/, pubblico
python export.py bigquery --project mio-progetto        # facoltativo, ricarica completa
```

L'opzione `--cache` di `discover_ats.py` e `crawl_jobs.py` conserva le risposte per 7 giorni in `data/cache/`. Serve durante lo sviluppo: per i dati veri il crawl settimanale non la usa. BigQuery è facoltativo; nella sandbox gratuita le tabelle scadono dopo 60 giorni, quindi il dato di riferimento resta `data/jobs.db`.

## Struttura

```
discover_ats.py          scoperta degli ATS
crawl_jobs.py            download e normalizzazione degli annunci
search.py                ricerche e storico
export.py                export DuckDB, dati pubblici, BigQuery
validate_salary.py       validazione manuale della retribuzione
test_ats.py              prova veloce di un singolo ATS
lib/http.py              richieste HTTP con robots.txt, riserva TDM, pause, retry, cache
lib/browser.py           browser Playwright per le pagine in JavaScript
lib/ats_fetchers.py      download degli annunci per ogni ATS
lib/ats_fingerprints.py  tracce degli ATS nelle pagine
lib/slugs.py             nomi candidati per le board, verifica del nome azienda
lib/locations.py         sedi e comuni ISTAT
lib/enrich.py            funzione, seniority, contratto, modalità, lingua, retribuzione, privacy
lib/salary.py            estrazione della retribuzione dal testo
lib/labels.py            etichette e macro-settori
lib/disclosure.py        regole di protezione dei dati pubblici
lib/db.py                connessione al database e schema
sql/schema.sql           tabelle, con tipi compatibili BigQuery
sql/views.sql            viste
sql/aggregates.sql       statistiche per l'export DuckDB
scripts/                 crawl settimanale e registrazione dell'attività pianificata
webapp/                  pagina web e dati pubblici
data/                    lista aziende, correzioni, registro, dati ISTAT
tests/                   test
```

## Limiti

- L'osservazione è iniziata il 4 ottobre 2026 e vede solo gli annunci online da quel giorno. Gli annunci pre-legge ancora visibili non sono un campione del mercato prima della legge.
- La data di pubblicazione ha significati un po' diversi tra gli ATS: prima pubblicazione, ripubblicazione o, per alcune board Greenhouse, ultimo aggiornamento.
- Le aziende con annunci scaricabili sono 56 su 216. Chi usa un ATS senza fonte pubblica è escluso, quindi i risultati descrivono queste aziende e non l'intero mercato del lavoro.
- La retribuzione e la seniority si ricavano con regole automatiche. La loro precisione va misurata con `validate_salary.py`.

## Note sui dati

- `website_verified = 0` nella lista aziende segnala un sito da verificare.
- `company_type` e `source` (FTSE MIB, Mid Cap e altri) riflettono la composizione degli indici al momento della creazione della lista. Gli indici cambiano: controllali prima di usarli in un'analisi.
- `size_band` è una stima dei dipendenti nel mondo: S sotto 250, M 250-1.000, L 1.000-10.000, XL oltre 10.000.

## Fonti dei dati di riferimento

- ISTAT, Elenco dei comuni italiani, licenza CC BY 4.0: `data/reference/comuni_istat.csv`
- comuni-json di Matteo Contrini, licenza MIT, dati ISTAT, per la popolazione: `data/reference/comuni_popolazione.json`

Questo README non è un parere legale.
