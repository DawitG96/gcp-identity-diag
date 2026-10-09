# gcp-identity-diag

GUI (tkinter, solo stdlib) per diagnosticare e gestire due identità GCP sullo
stesso PC — ADC (Application Default Credentials), gcloud CLI, kubectl/Lens — più
strumenti di rete e un client **FortiVPN** integrato.

Pensata per chi convive con due account Google (es. lavoro + personale) e deve
tenere allineati ADC, configurazioni gcloud e l'identità usata da kubectl/Lens.

## Funzionalità

- **Diagnostica identità**: stato delle configurazioni gcloud, identità reale di
  ogni file `adc-<label>.json` (via tokeninfo), sincronizzazione dell'ADC di
  default, cache del plugin GKE, launcher Lens.
- **Fix per account**: attiva la configuration giusta, ripulisce la cache
  `gke_gcloud_auth_plugin`, rigenera il launcher di Lens.
- **Login ADC / CLI**, rinnovo silenzioso dei token, sincronizzazione ADC.
- **Stato rete**: interfacce UP, default route, SSID WiFi, raggiungibilità di un
  eventuale proxy aziendale; wizard per aggiungere una host-route al proxy su un
  hotspot (via `nmcli`).
- **FortiVPN integrato**: connetti/disconnetti `openfortivpn` in un terminale
  dentro l'app (tab *FortiVPN*). Password sudo e OTP come campi; disconnessione
  pulita con un bottone (invia Ctrl-C al processo via pty).

## Requisiti

- Linux con `python3` + `python3-tk`
- `gcloud`, `kubectl`, `gke-gcloud-auth-plugin` (per le funzioni GCP)
- `openfortivpn` (per la VPN)

```bash
bash setup.sh      # installa python3-tk e verifica gli strumenti
python3 gui.py
```

Alla prima esecuzione l'app prova a rilevare automaticamente i due account dalle
configurazioni gcloud / dai file ADC esistenti; altrimenti apre il form di
configurazione.

## Risorse locali (non versionate)

Alcuni bottoni (Guida, Note, Setup proxy) compaiono solo se esistono i file
corrispondenti in `local/` (ignorata da git). È il posto dove tenere contenuti
specifici della propria azienda/ambiente senza pubblicarli:

- `local/guida.md` — testo mostrato dal bottone *Guida*
- `local/note.md` — testo mostrato dal bottone *Note*
- `local/setup_proxy.py` — script lanciato dal bottone *Setup proxy*
