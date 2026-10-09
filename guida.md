══ PC NUOVO (primo avvio) ════════════════════════════════

1. Prerequisiti
   bash setup.sh
   (installa python3-tk e verifica gcloud/kubectl/gke-gcloud-auth-plugin)

2. Avvia app → configura account
   python3 gui.py
   Il form di configurazione si apre automaticamente.
   I due account sono identificati da una label breve a tua scelta
   (es. CompanyRossi, CompanyRossiInterna).

3. Login ADC per account NON-default (es. CompanyRossi)
   → Clicca "Login ADC" per quell'account
   → Completa autenticazione nel browser
   → ATTENDI chiusura finestra (bottoni disabilitati)
   → App esegue automaticamente: sync ADC → fix account → fix account default
   → Log deve mostrare adc-<label>.json creato ✓

4. Login ADC per account default ADC (es. CompanyRossiInterna)
   → Stessa procedura del punto 3

5. Verifica "Stato identità"
   Tutti i file ADC: ✓ email corretta
   DEFAULT_ADC == adc-<label>.json: ✓ sì

6. Configura rete aziendale (solo se lavori via hotspot/proxy aziendale)
   → Bottone "Configura rete aziendale"
   → Esegui i comandi mostrati nel preview (richiedono sudo)


══ PC ESISTENTE (token scaduto / ⚠ in stato identità) ═══

1. Clicca "Rinnova token ADC"
   → Se tutti ✓: fatto.

2. Se un account fallisce con "Refresh token scaduto":
   → Clicca "Login ADC" per l'account con ⚠
   → ATTENDI chiusura finestra (sync + fix automatici inclusi)

3. Se Fix rimane arancione dopo il login:
   → Clicca "Fix <label>" manualmente


══ FORTIVPN ══════════════════════════════════════════════

Tab "FortiVPN" (terminale integrato):
  - Config: lista dei file openfortivpn nella cartella salvata
    (seleziona/doppio-click per connetterti; "Cambia…" per la cartella, ↻ aggiorna)
  - OTP: codice one-time (se richiesto dal gateway)
  - Password sudo: serve per avviare openfortivpn
  - "Connetti a FortiVPN" avvia, "Disconnetti" (rosso) chiude pulito.


══ NOTE ══════════════════════════════════════════════════

ORDINE su PC nuovo:
  Prima Login ADC account NON-default, poi account default ADC.
  Invertire → adc-<label>.json del non-default non viene creato.

Login ADC ≠ Login CLI:
  Login ADC  → application_default_credentials.json (SDK / tool esterni)
  Login CLI  → credenziali gcloud CLI (kubectl / Lens)

Bottoni disabilitati durante Fix / Login ADC / Sync:
  Attendi "completato" nel log prima di cliccare altro.
  Il label "⏳ Operazione in corso" scompare quando è finita.

Auto-refresh ogni 5s: stato identità e rete aggiornati automaticamente.
