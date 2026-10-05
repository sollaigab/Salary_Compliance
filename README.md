# Osservatorio retribuzioni negli annunci di lavoro (Italia)

Dal 7 giugno 2026 (D.Lgs. 96/2026) gli annunci devono indicare la retribuzione iniziale o la fascia.
Questo progetto misura quanti annunci la indicano davvero, quanti usano formule vaghe
("commisurata all'esperienza", "secondo CCNL") e quali RAL vengono offerte per ruolo e città.

## Fonti

Solo endpoint e pagine **pubblici**, senza login:

| ATS | Fonte |
|---|---|
| Greenhouse, Lever, Ashby, Workable, Recruitee | API pubbliche dei job board, documentate dal fornitore |
| Personio | feed XML pubblico delle offerte |
| Workday, Oracle Recruiting | endpoint JSON pubblico usato dalla pagina carriere |
| Teamtailor | feed RSS pubblico `/jobs.rss` del sito carriere |
| SuccessFactors | `sitemap.xml` del sito carriere + dati schema.org/JobPosting di ogni annuncio |

ATS rilevati ma **volutamente non scaricati**: SmartRecruiters (il `robots.txt` dell'API vieta
l'accesso automatico: rispettiamo la volontà del fornitore anche se le pagine sono pubbliche),
SuccessFactors "classico" senza sitemap, Taleo, inRecruiting, Avature, Altamira, Phenom, Eightfold,
iCIMS, Cornerstone (nessun feed pubblico). Restano nel registro con `supported = 0` per misurare
la copertura.

## Regole di conformità (applicate dal codice a tutte le fonti)

- **robots.txt** rispettato per ogni host, comprese le API (`lib/http.py`, `check_robots=True`).
- **Riserva sul text and data mining** (art. 70-quater L. 633/1941, direttiva UE 2019/790):
  se il sito la dichiara via `/.well-known/tdmrep.json` o header `tdm-reservation: 1`,
  il contenuto non viene usato.
- **Pause tra le richieste** (1 s per i siti aziendali, ~0,35 s per le API, rate limit unico per
  tutta la piattaforma Workday) e **stop su richiesta**: se un servizio risponde 429 con un
  `Retry-After` lungo, non viene più contattato fino alla scadenza (`data/cache/blocked_hosts.json`).
- **Niente dati personali**: nomi ed email dei recruiter non si salvano.
- Le **condizioni d'uso** scritte dei singoli siti non sono verificabili in automatico:
  vanno riviste a mano prima di pubblicare (elenco host in `data/ats_registry.csv`).

## Pubblicazione dei risultati

- **Sì**: statistiche aggregate (% annunci con RAL, % formule vaghe, RAL mediane per ruolo,
  città, settore), anche per azienda, con metodologia, data di rilevazione e limiti.
- **No**: ripubblicare il testo integrale degli annunci o l'intera banca dati di un'azienda
  (diritto d'autore e diritto sui generis sulle banche dati, art. 102-bis L. 633/1941).
  I dati grezzi restano in `data/jobs.db`, privato ed escluso da git.
- Prima di pubblicare: verifica legale consigliata (questo README non è un parere legale).

## Installazione

```bash
pip install -r requirements.txt
python -m playwright install chromium     # browser headless per le pagine carriere in JavaScript
```

## Fase 1: scoperta degli ATS

Risultato attuale: 216 aziende, 59 pronte per il crawler (vedi `python discover_ats.py --report`).

```bash
python discover_ats.py --sample 20 --cache        # prova su 20 aziende (10 tech, 10 non tech)
python discover_ats.py --cache                    # tutte le aziende di data/companies_seed.csv
python discover_ats.py --only "companyc,companyb" --force
python discover_ats.py --retry-none --cache       # riprova le aziende senza ATS trovato
python discover_ats.py --workday-only --cache     # solo il probe Workday ampio sulle aziende senza ATS
python discover_ats.py --report                   # solo il report di copertura
```

- Input: `data/companies_seed.csv` (da estendere a mano) e `data/manual_overrides.csv`
  (le correzioni manuali vincono sempre).
- Output: tabella `ats_registry` in `data/jobs.db` e copia in `data/ats_registry.csv`.
- Lo script è rilanciabile: salta le aziende controllate negli ultimi 7 giorni (`--max-age-days`),
  a meno di `--force` o di un override nuovo.
- `confidence = low` vuol dire revisione manuale: queste righe non entrano nel crawler.
- `--cache` salva le risposte HTTP in `data/cache/` per 7 giorni (sviluppo).
- `--no-browser` salta Playwright: più veloce, ma non vede le pagine costruite in JavaScript.

## Fase 2: crawler degli annunci

```bash
python crawl_jobs.py --cache                     # tutte le board pronte (supported=1, confidenza high/medium)
python crawl_jobs.py --only "companyb,examplecorp"  # solo alcune aziende
```

- Tiene gli annunci in Italia o da remoto (Italia/Europa). Per Workday, Oracle e SuccessFactors
  il filtro Italia si applica **prima** di scaricare i dettagli (EY: 75 pagine invece di 8.000).
- Sede normalizzata sull'elenco ISTAT dei comuni (tabella `locations`: comune, provincia, regione,
  macro-area, classe di popolazione). Annunci con più sedi: una riga per sede in `job_locations`.
- Campi per i filtri: `seniority`, `job_function`, `contract_type`, `work_schedule`,
  `workplace_type`, `description_lang` (regole in `lib/enrich.py`, modificabili a mano).
- Retribuzione: prima quella strutturata dell'ATS, poi la cifra nel testo; `salary_transparency`
  = cifra / vaga / assente; `ral_min_annual`/`ral_max_annual` = RAL annua lorda in euro (mensile x 14,
  stage x 12). Valori annui fuori da 5.000-500.000 € non vengono trasformati in RAL (sono altri importi).
- **Periodo rispetto alla legge** (`posting_period`): `post_legge` = pubblicato dal 7/6/2026 (D.Lgs. 96/2026),
  `pre_legge` = prima ma entro 12 mesi, `storico` = online da oltre 12 mesi (posizioni "sempre aperte").
  Le statistiche pubbliche (`sql/aggregates.sql`, `v_transparency_company`) usano solo `post_legge`.
- `python validate_salary.py sample` / `score <csv>`: campione da etichettare a mano e calcolo di
  precisione e recall dell'estrazione della retribuzione. **Da fare prima di pubblicare.**
- Storico: `first_seen`, `last_seen`, `is_active` (0 se l'annuncio sparisce dalla board); ogni passaggio
  in `crawl_runs`.
- Email e telefoni vengono tolti dalle descrizioni prima del salvataggio.

## Fase 3: ricerche ed export

```bash
python search.py "data analyst" --location milano   # città, sigla provincia, regione, macro-area o "remoto"
python export.py duckdb                             # data/export/osservatorio.duckdb (privato, per analisi SQL)
python export.py public                             # webapp/data/*.parquet (pubblicabile, letto dalla web app)
python export.py bigquery --project mio-progetto    # facoltativo: ricarica completa (WRITE_TRUNCATE)
```

- Ogni ricerca salva una riga in `searches`; storico con `SELECT * FROM v_search_summary`.
- Statistiche aggregate (mediane RAL per settore, funzione, seniority, regione) in `sql/aggregates.sql`.
- BigQuery sandbox: le tabelle scadono dopo 60 giorni e non c'è DML; la fonte di verità resta `data/jobs.db`.

## Web app

Sito statico in `webapp/` (HTML, CSS, JavaScript, nessun build). Pubblica **solo dati aggregati**:
nessun annuncio singolo, nessun nome, titolo o link di azienda.

- Dati: `webapp/data/aggregati.json` (per l'app) e `aggregati.csv` (scaricabile), generati da
  `python export.py public`. Ogni riga = periodo x macro-settore x una dimensione
  (regione, funzione, seniority, contratto, modalità, periodo di pubblicazione).
- **Protezione delle aziende** (`lib/disclosure.py`): una cella si pubblica solo con almeno 5 annunci
  di almeno 3 aziende, senza un'azienda oltre il 70% degli annunci; le mediane RAL seguono le stesse
  soglie; se in un gruppo una sola cella è nascosta, si nasconde anche la più piccola delle altre
  (altrimenti si ricaverebbe per differenza). I settori sono raggruppati in 6 macro-settori da almeno
  3 aziende ciascuno (`lib/labels.py`, `MACRO_OF_SECTOR`).
- Filtri: periodo (dal 7/6/2026 o tutti) e macro-settore; finiscono nell'URL per condividere la vista.
- I dati per azienda e per annuncio restano solo in locale (`data/jobs.db`, `python export.py duckdb`).

```bash
python export.py public                          # aggiorna webapp/data/
python -m http.server -d webapp 8000             # poi apri http://localhost:8000
```

Pubblicazione (GitHub Pages / Cloudflare Pages, cartella `webapp/` così com'è) **solo dopo**:
validazione completata, revisione delle condizioni d'uso e verifica legale.

## Crawl settimanale

Attività pianificata di Windows "Osservatorio RAL - crawl settimanale", ogni lunedì alle 7:00
(se il PC è spento, parte appena possibile): scarica gli annunci senza cache e rigenera gli export.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\crawl_settimanale.ps1 -Prova   # verifica senza richieste
powershell -ExecutionPolicy Bypass -File scripts\registra_crawl_settimanale.ps1 # registra o aggiorna
Unregister-ScheduledTask -TaskName "Osservatorio RAL - crawl settimanale" -Confirm:$false   # rimuovi
```

Log in `data/logs/`. La scoperta degli ATS (`discover_ats.py`) non è settimanale: va rilanciata
a mano ogni tanto, per esempio una volta al mese.

## Viste utili

| Vista | Contenuto |
|---|---|
| `v_coverage` | copertura della scoperta ATS (tech / non tech) |
| `v_jobs_enriched` | annunci + azienda + luogo (privata: include tutto) |
| `v_jobs_public` | annunci senza testo integrale (privata: base degli aggregati pubblici) |
| `v_transparency_company` | % annunci con cifra e RAL medie per azienda |
| `v_search_summary` | storico delle ricerche |

## Fonti dei dati di riferimento

- ISTAT, Elenco dei comuni italiani (CC BY 4.0) → `data/reference/comuni_istat.csv`
- comuni-json di Matteo Contrini (MIT, dati ISTAT) per la popolazione → `data/reference/comuni_popolazione.json`

## Test

```bash
python -m pytest -q
```

## Struttura

```
discover_ats.py          Fase 1: scoperta ATS e slug
crawl_jobs.py            Fase 2: download e normalizzazione degli annunci
search.py                Fase 3: ricerche e storico
export.py                Fase 3: export DuckDB / Parquet pubblico / BigQuery
test_ats.py              prova veloce delle API ATS su singoli slug
lib/http.py              sessione HTTP: robots.txt, riserva TDM, rate limit, retry, stop su richiesta, cache
lib/browser.py           Playwright per le pagine carriere in JavaScript
lib/ats_fetchers.py      download annunci (Greenhouse, Lever, Ashby, Workday, Workable, Personio,
                         Recruitee, Oracle, Teamtailor, SuccessFactors)
lib/ats_fingerprints.py  impronte degli ATS (supportati e non)
lib/slugs.py             slug e tenant candidati, verifica del nome
lib/locations.py         riconoscimento sedi e dimensione geografica ISTAT
lib/enrich.py            seniority, funzione, contratto, modalità, lingua, retribuzione, privacy
lib/salary.py            regex RAL, formule vaghe, normalizzazione (mensile x 14)
sql/schema.sql           tabelle (tipi compatibili BigQuery)
sql/views.sql            viste
sql/aggregates.sql       statistiche pubblicabili (DuckDB)
data/                    seed, override, registro, dati di riferimento (jobs.db, cache ed export privati esclusi da git)
```

## Limiti da dichiarare in pubblicazione

- L'osservazione parte dal **4 ottobre 2026**: vediamo solo gli annunci online in quel momento.
  Gli annunci pre-legge ancora online non rappresentano il mercato prima della legge (bias di sopravvivenza).
- La data di pubblicazione ha significati leggermente diversi tra ATS (prima pubblicazione, ripubblicazione,
  per Greenhouse a volte ultimo aggiornamento).
- Copertura: 58 aziende su 216 della lista (gli ATS senza fonte pubblica sono esclusi), quindi i risultati
  descrivono queste aziende, non tutto il mercato del lavoro italiano.
- L'estrazione della retribuzione dal testo è basata su regole: la precisione va misurata con `validate_salary.py`.

## Note sui dati

- `website_verified = 0`: sito da verificare.
- `company_type` e `source` (FTSE MIB, Mid Cap...) riflettono la composizione degli indici nota
  alla creazione del file: gli indici cambiano, controllali prima di usarli in un'analisi.
- `size_band` = dipendenti nel mondo (S <250, M 250-1k, L 1k-10k, XL >10k), stima indicativa.
- RAL annua: le cifre mensili sono moltiplicate per 14; orarie, nette o in altra valuta non vengono convertite.
