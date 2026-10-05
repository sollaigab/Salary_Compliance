# Contesto

Sto costruendo un osservatorio sulle retribuzioni negli annunci di lavoro in Italia. Dal 7 giugno 2026 (D.Lgs. 96/2026) gli annunci devono indicare la retribuzione iniziale o la fascia retributiva. Voglio misurare quanti annunci la indicano davvero, quanti usano formule vaghe ("commisurata all'esperienza", "secondo CCNL") e quali sono le RAL offerte per ruolo e città.

Il mio profilo: sono forte in SQL e BigQuery, meno in Python. Scrivi codice leggibile, commentato in italiano, senza astrazioni inutili.

## Cosa esiste già: `test_ats.py` che stai guardando ora, crea una nuova repo per il progetto

Script Python (solo `requests`) che interroga le API pubbliche dei job board degli ATS e normalizza gli annunci in un unico formato (`ats, company, title, location, url, description, salary_structured, salary_in_text, salary_vague`):

- **Greenhouse**: `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`, con fallback sull'istanza EU (`boards-api.eu.greenhouse.io`). Non espone lo stipendio strutturato.
- **Lever**: `api.lever.co/v0/postings/{slug}?mode=json`, con fallback EU (`api.eu.lever.co`). A volte ha `salaryRange`.
- **Ashby**: `api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`. Ha la retribuzione strutturata.
- **Workday**: endpoint interno del sito carriere. `POST https://{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (paginato, 20 per volta), poi `GET .../wday/cxs/{tenant}/{site}{externalPath}` per la descrizione completa. Se manca il nome del sito, lo ricava dal redirect della home del dominio.
- Estrazione RAL con regex (`SALARY_RE`) e rilevamento delle formule vaghe (`VAGUE_SALARY_RE`).

Stato: **testato e funzionante**, anche su tenant (Workday). Slug già verificati: Company A → Lever EU `prima`; examplepay → Greenhouse EU `examplepay`; tenant → Workday `https://tenant.wd3.myworkdayjobs.com/en-US/CompanyCareers`.

Riusa queste funzioni di fetch invece di riscriverle: spostale in un modulo e importale.

# Obiettivo

Raccogliere tutte le offerte delle principali aziende che operano in Italia, sia tech sia non tech (banche, assicurazioni, consulenza e Big4, agenzie marketing e media, energia, telco, moda e lusso, GDO, industria, fintech e startup), e costruire un database interrogabile.

Il problema da risolvere per primo è che oggi gli slug li trovo a mano. **La priorità assoluta è automatizzare la scoperta di quale ATS usa ogni azienda e del relativo slug o URL.** I dati che arrivano dagli ATS sono già buoni.

# Fase 1 (la priorità): scoperta automatica di ATS e slug

## 1a. Lista seed delle aziende

Crea `data/companies_seed.csv` con le colonne `name, website, sector, is_tech, source`. Popolala con un primo elenco ragionato di circa 150 aziende. Includi grandi aziende quotate (FTSE MIB e Mid Cap), le maggiori banche e assicurazioni, Big4 e società di consulenza, network pubblicitari e agenzie, le multinazionali con sedi importanti in Italia e le scaleup e startup italiane più note. Il file deve essere facile da estendere a mano. Marca come "da verificare" i siti web di cui non sei sicuro.

## 1b. Rilevamento dell'ATS (`discover_ats.py`)

Per ogni azienda prova questi metodi nell'ordine e fermati al primo risultato valido:

1. **Fingerprint sulla pagina carriere.** Scarica il sito, cerca la pagina carriere (link con testo o percorso tipo `careers`, `carriere`, `lavora-con-noi`, `jobs`, `work-with-us`) e analizza HTML, iframe, script e link alla ricerca delle impronte degli ATS:
   - `boards.greenhouse.io/{slug}`, `job-boards.greenhouse.io/{slug}`, `job-boards.eu.greenhouse.io/{slug}`, `greenhouse.io/embed/job_board?for={slug}`
   - `jobs.lever.co/{slug}`, `jobs.eu.lever.co/{slug}`
   - `jobs.ashbyhq.com/{slug}`
   - `{tenant}.wd{N}.myworkdayjobs.com/{locale?}/{site}`
   - Riconosci anche gli ATS **non ancora supportati**, per misurare la copertura: SmartRecruiters, Workable, Recruitee, Teamtailor, Personio, SAP SuccessFactors, Oracle/Taleo, iCIMS, Avature, inRecruiting/Intervieweb (molto diffuso in Italia). Salvali con `supported = false`.
2. **Probing degli slug.** Genera slug candidati dal nome (minuscolo, senza spazi, con trattini, con o senza `spa`, `srl`, `italia`, `group`). Prova gli endpoint pubblici di Greenhouse, Lever e Ashby, istanze USA ed EU. Per Workday prova `{tenant}.wd{1,3,5,103}.myworkdayjobs.com` e scopri il sito via redirect.
3. **Validazione.** Un candidato è valido solo se l'endpoint risponde con almeno un annuncio **e** il nome dell'azienda corrisponde. Controlla il nome nelle descrizioni o negli URL, perché uno slug generico può appartenere a un'azienda omonima.

Output in `ats_registry` con le colonne: `company, sector, is_tech, ats, slug_or_url, instance (us/eu), supported, detection_method (fingerprint/probe/manual), confidence (high/medium/low), n_jobs_total, n_jobs_italy, last_checked, notes`.

Regole:
- Le righe a confidenza bassa vanno in revisione manuale, non nel crawler.
- Lo script deve essere **idempotente**: si può rilanciare e aggiorna solo ciò che serve. Deve anche accettare una correzione manuale (`manual_overrides.csv`) che ha sempre la precedenza.
- Alla fine stampa un report di copertura: quante aziende hanno un ATS rilevato, quanti supportati e non supportati, quante senza nessun ATS trovato. Il tutto anche diviso tra tech e non tech.

# Fase 2: crawler degli annunci

`crawl_jobs.py` legge `ats_registry` (solo le righe `supported = true` con confidenza alta o media), scarica gli annunci e li salva normalizzati. Requisiti:
- Tieni solo gli annunci in Italia, oppure remoti con possibilità di lavorare dall'Italia. Salva il campo `location` grezzo più `city` e `country` normalizzati.
- Deduplica per `(ats, slug, job_id)` e tieni `first_seen` e `last_seen`, così nel tempo si vede quando un annuncio sparisce.
- Salva la descrizione completa e i campi stipendio: strutturato, cifre estratte dal testo (min, max, valuta, periodo, lordo o netto se deducibile) e il flag `salary_vague`.

# Fase 3: database e ricerche

Usa **SQLite** in locale (`data/jobs.db`), con uno schema compatibile con BigQuery e uno script `export_bigquery.py` per caricarlo dopo.

Tabelle: `companies`, `ats_registry`, `jobs`, `searches`.

Le ricerche sono il cuore dell'uso che ne farò. Crea un comando `search.py "data analyst" --location milano` che:
- cerca il termine in titolo e descrizione degli annunci nel DB;
- salva in `searches` i campi `search_term, location, run_at, n_jobs, n_companies, n_jobs_with_salary, n_jobs_vague, ral_min_median, ral_max_median`;
- stampa le aziende che hanno annunci per quel termine e luogo, con il conteggio per azienda e quante indicano la retribuzione.

Aggiungi una vista SQL `v_search_summary` per vedere lo storico delle ricerche.

# Vincoli

- Usa solo endpoint pubblici e pagine pubbliche: niente login, niente LinkedIn o Indeed, e rispetta `robots.txt` per le pagine dei siti aziendali.
- Applica un rate limiting gentile (pause tra le richieste, max qualche richiesta al secondo per dominio), con retry e backoff sugli errori.
- Metti in cache le risposte HTTP in sviluppo, per non ripetere centinaia di chiamate a ogni prova.
- Tutto da riga di comando, con un `README.md` che spiega in poche righe come lanciare ogni fase.
- Aggiungi test minimi sulle funzioni di parsing (URL Workday, generazione slug, regex RAL).

# Come procedere

1. Leggi `test_ats.py` e proponimi la struttura delle cartelle e lo schema delle tabelle **prima** di scrivere codice.
2. Implementa la **Fase 1** e provala su 20 aziende miste (10 tech e 10 non tech). Mostrami `ats_registry` e il report di copertura, poi **fermati** perché io possa rivedere i risultati.
3. Solo dopo il mio ok passa alle fasi 2 e 3.
