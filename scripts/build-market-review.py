#!/usr/bin/env python3
"""Build the source-backed, standalone Italian competitor review (stdlib only).

Run from any directory. Input files stay unchanged. Release status is explicit:
use --release-status verified only after the application changes pass checks.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BASE_INPUT_NAMES = (
    "italian-competitors.json",
    "suite-competitors.json",
    "productivity-competitors.json",
    "market-context.json",
)
BENCHMARK_INPUT_NAMES = (
    "feature-benchmarks-commercial.json",
    "feature-benchmarks-specialists.json",
    "feature-benchmarks-core.json",
)
INPUT_NAMES = BASE_INPUT_NAMES + BENCHMARK_INPUT_NAMES
DATE = "2026-10-05"


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def money(value: int | float, currency: str = "EUR") -> str:
    amount = f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{amount} {'€' if currency == 'EUR' else 'USD'}"


def validate_url(url: str) -> str:
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
        raise ValueError(f"Invalid source URL: {url!r}")
    return url


def load_inputs(directory: Path) -> tuple[dict, dict]:
    data, hashes = {}, {}
    for name in INPUT_NAMES:
        raw = (directory / name).read_bytes()
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get("research_date") != DATE:
            raise ValueError(f"Invalid snapshot/date: {name}")
        if not isinstance(doc.get("sources"), list) or not doc["sources"]:
            raise ValueError(f"No source list in {name}")
        if name in BASE_INPUT_NAMES and name != "market-context.json":
            if not isinstance(doc.get("competitors"), list) or not doc["competitors"]:
                raise ValueError(f"No competitor list in {name}")
        elif name in BENCHMARK_INPUT_NAMES:
            entries = doc.get("benchmarks", doc.get("features"))
            if not isinstance(entries, list) or not entries:
                raise ValueError(f"No feature benchmarks in {name}")
        data[name] = doc
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return data, hashes


def collect_sources(data: dict) -> tuple[list[dict], dict]:
    by_url, by_id = {}, {}
    for name, doc in data.items():
        for source in doc["sources"]:
            url = validate_url(source["url"])
            sid = source["id"]
            if not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9-]+", sid):
                raise ValueError(f"Invalid source ID in {name}")
            if sid in by_id and by_id[sid]["url"] != url:
                raise ValueError(f"Conflicting source ID {sid}")
            if url not in by_url:
                by_url[url] = {**source, "snapshot_names": [name], "aliases": [sid]}
            else:
                by_url[url]["snapshot_names"].append(name)
                by_url[url]["aliases"].append(sid)
            by_id[sid] = by_url[url]
    for doc in data.values():
        def check(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"source_ids", "evidence_source_ids"}:
                        for sid in child:
                            if sid not in by_id:
                                raise ValueError(f"Unresolved source reference {sid}")
                    else:
                        check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(doc)
    sources = list(by_url.values())
    for index, source in enumerate(sources, 1):
        source["number"] = index
    return sources, by_id


def refs(ids: list[str], by_id: dict) -> str:
    ordered = list(dict.fromkeys(by_id[sid]["number"] for sid in ids))
    return " ".join(f'<a class="ref" href="#source-{number}" aria-label="Fonte {number}">[{number}]</a>' for number in ordered)


def paragraphs(values: list[str]) -> str:
    return "".join(f"<p>{e(value)}</p>" for value in values if value)


def ul(values: list[str], css: str = "") -> str:
    return f'<ul class="{e(css)}">' + "".join(f"<li>{e(value)}</li>" for value in values) + "</ul>"


def pricing_lines(product: dict, stream: str) -> tuple[tuple[list[str], list[str], list[str]], list[str]]:
    """Separate observed prices, purchasing conditions and unresolved facts."""
    price = product.get("pricing", product.get("price", {}))
    name = product.get("name", product.get("product"))
    lines, conditions, gaps = [], [], []
    ids = list(price.get("source_ids", []))
    if stream == "italian":
        for offer in price.get("verified_offers", []):
            ids += offer.get("source_ids", [])
            if offer.get("annual_amount") is not None:
                line = f"{offer['plan']}: {money(offer['annual_amount'])}/anno"
                if offer.get("monthly_equivalent") is not None:
                    line += f" ({money(offer['monthly_equivalent'])}/mese equivalente)"
                lines.append(line + ".")
            elif offer.get("monthly_advertised_amount") is not None:
                lines.append(f"{offer['plan']}: {money(offer['monthly_advertised_amount'])}/mese pubblicizzato; totale annuo non verificato.")
            if offer.get("eligibility"):
                conditions.append(offer["eligibility"] + ".")
        conditions.extend([price.get("basis", ""), price.get("vat", ""), price.get("renewal", "")])
        fees = price.get("payment_fees")
        if fees:
            conditions.append(f"Commissioni del pagamento della licenza annuale: carta/PayPal {money(fees['card_or_paypal'])}; bonifico {money(fees['bank_transfer'])}, IVA esclusa.")
        if name == "Danea Easyfatt":
            lines.append("Licenza principale: importo non verificato.")
            for offer in price.get("verified_optional_offers", []):
                ids += offer.get("source_ids", [])
                amount = offer.get("annual_amount")
                text = (f"{money(amount)}/anno" if amount is not None else f"{money(offer['monthly_advertised_amount'])}/mese pubblicizzato")
                conditions.append(f"Optional {offer['product']}: {text}. {offer['basis']}.")
        if name == "Fattura24":
            limits = price["professional_limits"]
            conditions.append(f"Professional: {limits['documents_per_class_last_12_months']} documenti per classe negli ultimi 12 mesi, fino a {limits['users_max']} utenti e {limits['custom_models']} modelli personalizzati; connettore AI {limits['ai_connector']}.")
        if not lines:
            lines.append("Preventivo personalizzato; nessuna cifra pubblica verificata." if name == "TeamSystem Enterprise" else "Prezzo numerico non verificato nelle fonti ufficiali lette.")
        gaps.append(price.get("notes", ""))
    elif stream == "suite":
        conditions.extend([price.get("unit", ""), price.get("vat", "") or "Trattamento IVA non verificato."])
        if name == "Pipedrive":
            for plan in price["plans"]:
                lines.append(f"{plan['name']}: {money(plan['monthly_equivalent'], 'USD')}/posto/mese equivalente; pagamento annuo {money(plan['annual_payment_per_seat'], 'USD')}/posto.")
            gaps.append("Prezzo mensile senza impegno annuale e listino italiano EUR non verificati.")
        elif name == "Bitrix24":
            for plan in price["plans"]:
                lines.append(f"{plan['name']} ({plan['included_users']} utenti): {money(plan['monthly_billing'])}/mese oppure {money(plan['annual_monthly_equivalent'])}/mese equivalente, {money(plan['annual_payment'])} anticipati/anno.")
        elif name == "HubSpot Smart CRM + Sales Hub":
            lines += ["Starter: $20/posto/mese ordinario mostrato.", "Offerta nuovi clienti: $7/posto/mese equivalente con pagamento anticipato e impegno annuale, oppure $10/posto/mese."]
            conditions.append(price["annual_terms"])
            conditions.append(price["promotion"])
            gaps.append("Simbolo $ della pagina globale: checkout italiano EUR e fine dell'offerta non verificati.")
        elif name == "Zoho Books":
            lines.append(f"Standard USA: {money(price['observed_standard_amount'], 'USD')}/organizzazione/mese; {price['included_users']} utenti inclusi.")
            gaps.append("Selettore annuale/mensile non risolto; non è un preventivo italiano.")
        elif name == "Zoho One (CRM + Flow + Books)":
            lines.append("Importo non verificato.")
            conditions.extend(price["licensing_models"])
        else:
            lines.append("Listino EUR Italia non verificato; pagina italiana restituisce US$.")
            conditions.append(price.get("promotion", ""))
        extras = price.get("extra_costs", price.get("additional_costs", []))
        if extras:
            conditions.append("Costi aggiuntivi possibili: " + "; ".join(extras) + ".")
        gaps.extend(price.get("limitations", []))
    else:
        currency = price.get("currency", "USD")
        for plan in price.get("plans", []):
            amount = plan.get("amount")
            line = f"{plan['name']}: {money(amount, currency)}/utente/mese" if amount is not None else f"{plan['name']}: importo non verificato"
            if plan.get("condition"):
                line += f"; {plan['condition']}"
            lines.append(line + ".")
        conditions += [price.get("commitment", ""), price.get("tax_status", ""), price.get("conditions", "")]
        if name == "Superhuman Mail":
            conditions.append("Importi con fatturazione annuale: Starter 300 USD/utente/anno, Business 396 USD/utente/anno.")
        if name == "Missive":
            conditions.append("Importi con fatturazione annuale: Starter 168, Productive 288, Business 432 USD/utente/anno.")
        if name.startswith("Microsoft"):
            conditions.append("Copilot Business è un componente aggiuntivo: la licenza Microsoft 365 idonea si paga separatamente. Il totale della suite non è 18,20€.")
            gaps.append("Esclusa l'offerta 15,60€: validità 1 luglio–30 settembre 2026, scaduta prima della ricerca. Bundle 20,36€/27,73€ non trattati come listino ordinario per condizioni non chiare.")
        if name.startswith("Google"):
            gaps.append("Pagina in italiano, prezzi in USD: nessuna conversione valutaria e nessun preventivo EUR Italia. Offerta 50% esclusa per durata incoerente fra testo e nota.")
        if name == "ClickUp":
            conditions.append("Annuale anticipato: Unlimited 84 USD/utente/anno; Business 144 USD/utente/anno. I 10/19 USD indicati sono i corrispondenti piani con fatturazione mensile.")
        if name in {"Front", "Asana"}:
            gaps.append("Il prezzo base non appare nel contenuto estratto; non sostituito da listini storici, recensioni o prezzo degli add-on.")
    return ([line for line in lines if line], [line for line in conditions if line], [line for line in gaps if line]), list(dict.fromkeys(ids))


def competitor_rows(data: dict, by_id: dict) -> str:
    rows = []
    streams = (
        ("italian-competitors.json", "italian", "Gestionali italiani"),
        ("suite-competitors.json", "suite", "Suite e CRM"),
        ("productivity-competitors.json", "productivity", "Email e lavoro"),
    )
    for filename, stream, group in streams:
        for product in data[filename]["competitors"]:
            name = product.get("name", product.get("product"))
            features = product.get("strongest_features", product.get("features", product.get("verified_features")))
            (prices, conditions, gaps), price_ids = pricing_lines(product, stream)
            feature_html = "".join(f"<li>{e(feature['feature'])} {refs(feature['source_ids'], by_id)}" + (f"<small>{e(feature['limits'])}</small>" if feature.get("limits") else "") + "</li>" for feature in features)
            subgroup = product.get("category", product.get("segment", ""))
            corporate = f"Gruppo {product['group']}" if product.get("group") else ""
            fit = product.get("fit_for_filo", "")
            if stream == "italian":
                fit = "; ".join(item["feature"] for item in product.get("filo_improvements", [])[:2])
            search = " ".join([name, group, subgroup, fit] + [f["feature"] for f in features])
            rows.append(f'''<tr data-segment="{e(group)}" data-search="{e(search.casefold())}">
              <td data-label="Prodotto"><strong>{e(name)}</strong><span class="segment">{e(group)}</span><small>{e(corporate)}</small><p>{e(subgroup)}</p></td>
              <td data-label="Funzioni verificate"><ul class="compact">{feature_html}</ul></td>
              <td data-label="Prezzo e condizioni">{ul(prices, 'price-lines')}<div class="price-refs">{refs(price_ids, by_id)}</div>
                <details><summary>Condizioni e limiti del confronto</summary>{paragraphs(conditions)}<div class="caveat">{paragraphs(gaps)}</div></details></td>
              <td data-label="Cosa adattare in Filo"><p>{e(fit)}</p></td>
            </tr>''')
    return "\n".join(rows)


def source_appendix(sources: list[dict]) -> str:
    result = []
    labels = {"official_product": "Produttore · prodotto", "official_pricing": "Produttore · prezzi", "official_help": "Produttore · guida", "firsthand_practitioner_with_disclosed_referral": "Utilizzatore · referral dichiarato", "firsthand_practitioner": "Utilizzatore"}
    for source in sources:
        number = source["number"]
        kind = source.get("type", source.get("quality", ""))
        title = source.get("title", source["id"].replace("-", " ").capitalize())
        excerpts = source.get("excerpts", [source.get("excerpt", "")])
        short_quotes = [(quote[:257].rsplit(" ", 1)[0] + "…" if len(quote) > 260 else quote) for quote in excerpts[:2] if quote]
        quotes = "".join(f"<blockquote>{e(quote)}</blockquote>" for quote in short_quotes)
        published = source.get("publication_date", source.get("source_date", source.get("source_last_updated")))
        date = f"Data fonte/aggiornamento: {published}." if published else "Data di aggiornamento non indicata nello snapshot."
        result.append(f'''<details class="source" id="source-{number}"><summary><span class="source-no">{number:02}</span><span>{e(title)}<small>{e(labels.get(kind, kind))}</small></span></summary>
          <div class="source-body"><a href="{e(source['url'])}" target="_blank" rel="noopener noreferrer">{e(source['url'])}</a>{quotes}<p class="muted">{e(date)} Lettura: 5 ottobre 2026.</p></div></details>''')
    return "\n".join(result)


BUSINESS_IDS = {
    "clients", "quotes", "invoices", "receivables", "expenses", "suppliers",
    "purchases", "projects", "deadlines", "documents", "contracts", "inventory",
    "support", "content", "newsletter", "procedures", "meetings", "leave",
    "training", "expense-claims", "shipments", "quality", "time-measurement",
}
INTEGRATION_IDS = {
    "sync-calendar", "sdi", "bank", "pec-send", "erp-sync", "social-publish",
    "phone", "whatsapp",
}
EXISTING_IDS = {"priority-email", "agenda"}
DATE_LABELS = {
    "quotes": "validità del preventivo", "receivables": "data attesa d'incasso",
    "expenses": "data prevista di pagamento", "purchases": "data di consegna",
    "contracts": "data di rinnovo", "content": "data di pubblicazione",
    "meetings": "data della riunione", "leave": "inizio dell'assenza",
    "training": "data della sessione", "shipments": "data di consegna",
}
SERVICE_NAMES = {
    "priority-email": "Segreteria email", "agenda": "Agenda e ordine del giorno",
    "clients": "Clienti e opportunità", "quotes": "Preventivi", "invoices": "Bozze fattura",
    "receivables": "Incassi e solleciti", "expenses": "Spese e pagamenti",
    "suppliers": "Fornitori", "purchases": "Acquisti e ordini", "projects": "Commesse e lavori",
    "deadlines": "Scadenze amministrative", "documents": "Documenti e checklist",
    "contracts": "Contratti e rinnovi", "inventory": "Magazzino e riordini",
    "support": "Assistenza clienti", "content": "Contenuti e social",
    "newsletter": "Newsletter e comunicazioni", "procedures": "Procedure e attività ricorrenti",
    "meetings": "Verbali e azioni", "leave": "Ferie e copertura operativa",
    "training": "Formazione e onboarding", "expense-claims": "Note spese",
    "shipments": "Spedizioni e consegne", "quality": "Controlli e manutenzioni",
    "time-measurement": "Tempo operativo misurato", "sync-calendar": "Calendari Google e Microsoft",
    "sdi": "Fatturazione elettronica e SDI", "bank": "Banche e riconciliazione",
    "pec-send": "PEC e ricevute", "erp-sync": "Gestionali e contabilità",
    "social-publish": "Pubblicazione sui social", "phone": "Telefonia e segreteria vocale",
    "whatsapp": "WhatsApp Business",
}
RESEARCH_STATUS_LABELS = {
    "proposed-local": "proposta locale prima dell'aggiornamento",
    "local_available_with_proposed_optimization": "funzione locale con miglioramento proposto",
    "requires_provider": "richiede un provider",
    "already_matching": "pattern già presente",
    "already_matching_pending_configuration": "percorso predisposto, configurazione richiesta",
    "already_matching_with_scope_gap": "pattern presente con copertura limitata",
    "already_matching_with_limits": "pattern presente con limiti",
    "requires_configuration_and_implementation": "configurazione e implementazione da aggiungere",
    "material_opportunity_not_implemented": "opportunità futura non implementata",
    "material_opportunity_for_current_turn": "miglioramento proposto per questa revisione",
    "improvements_under_validation_current_turn": "miglioramenti in verifica nello snapshot",
}
READINESS_LABELS = {
    "available_local": "disponibile in locale",
    "available_local_with_commercial_gap": "accesso locale disponibile, recupero password e verifica email da aggiungere",
    "available_local_single_account_workspace": "spazio privato per singolo account",
    "implementation_present_provider_unconfigured": "percorso implementato, provider da configurare",
    "available_local_business_records": "ricerca sulle schede aziendali locali",
    "available_local_deterministic_proposals": "proposte locali basate su regole",
    "available_local_with_current_turn_improvements": "panoramica disponibile, aggiornamenti descritti nello snapshot",
    "available_local_execution_history": "storico delle esecuzioni disponibile",
    "available_local_text_exports": "esportazioni di testo disponibili",
    "available_owner_only_report": "report riservato al gestore",
}


def feature_entries(data: dict) -> list[dict]:
    """Normalize independently researched schemas without modifying snapshots."""
    entries = []
    for filename in BENCHMARK_INPUT_NAMES:
        doc = data[filename]
        for raw in doc.get("benchmarks", doc.get("features", [])):
            core = filename.endswith("-core.json")
            sid = raw.get("service_id", raw.get("feature_id", raw.get("id")))
            name = raw.get("service_name", raw.get("name", raw.get("feature_name", SERVICE_NAMES.get(sid, sid))))
            if not sid or not name:
                raise ValueError(f"Missing feature identity in {filename}")
            references = raw.get("references")
            if not references:
                references = [{
                    "product": raw.get("reference_product", raw.get("best_reference", "")),
                    "pattern": raw.get("reference_pattern", raw.get("pattern", "")),
                    "source_ids": raw.get("source_ids", raw.get("evidence_source_ids", [])),
                }]
            known = raw.get("filo_verified", raw.get("filo_existing", raw.get("current_filo", "")))
            if isinstance(known, list):
                known = " ".join(item.get("behavior", "") if isinstance(item, dict) else str(item) for item in known)
            elif isinstance(known, dict):
                known = known.get("behavior", known.get("description", ""))
            improvement = raw.get("improvement", raw.get("optimization", raw.get("optimized_local_recipe", "")))
            if isinstance(improvement, dict):
                proposed = improvement.get("description", "")
                proposed_status = improvement.get("status", "")
            else:
                proposed = " ".join(str(item) for item in improvement) if isinstance(improvement, list) else improvement
                proposed_status = raw.get("status", "")
            limits = raw.get("constraints", raw.get("limits", []))
            if isinstance(limits, str):
                limits = [limits]
            else:
                limits = list(limits)
            limits += raw.get("provider_dependencies", [])
            criteria = raw.get("selection_criteria", {})
            if criteria.get("manual_verifiability"):
                limits.append("Verificabilità manuale: " + criteria["manual_verifiability"])
            checks = raw.get("acceptance_checks", raw.get("verification_checks", []))
            if isinstance(checks, str):
                checks = [checks]
            entries.append({
                "id": sid, "name": name, "core": core,
                "group": "Funzioni trasversali" if core else (
                    "Collegamenti esterni" if sid in INTEGRATION_IDS else
                    "Email e agenda" if sid in EXISTING_IDS else "Moduli operativi"
                ),
                "references": references,
                "fit": raw.get("fit_reason", raw.get("why_fit", raw.get("rationale", " ".join([criteria.get("task_fit", ""), criteria.get("simplicity", "")]).strip()))),
                "existing": known,
                "proposed": proposed,
                "proposed_status": proposed_status,
                "limits": limits,
                "checks": checks,
                "readiness": raw.get("readiness", criteria.get("readiness", "")),
                "snapshot": filename,
            })
    catalog = [entry["id"] for entry in entries if not entry["core"]]
    expected = BUSINESS_IDS | INTEGRATION_IDS | EXISTING_IDS
    if len(catalog) != 33 or set(catalog) != expected:
        raise ValueError(f"Feature benchmark catalog incomplete/duplicated: {set(catalog) ^ expected}")
    core_ids = [entry["id"] for entry in entries if entry["core"]]
    if len(core_ids) != 11 or len(set(core_ids)) != 11:
        raise ValueError("Expected eleven distinct cross-cutting features")
    return entries


def delivered_behavior(entry: dict, verified: bool) -> tuple[str, str, str]:
    """Application facts remain distinct from research proposals and integrations."""
    sid = entry["id"]
    if not entry["core"] and sid in INTEGRATION_IDS:
        return "Da integrare", "external", "Il catalogo indica la dipendenza. Collegamento e azioni esterne non sono implementati da una scheda o da una checklist locale."
    if not entry["core"] and sid in BUSINESS_IDS:
        description = "Checklist specifica persistente, con il primo passaggio non spuntato visibile come prossimo passo. La persona registra i controlli fatti; una spunta non dimostra esecuzione esterna o approvazione."
        if sid in DATE_LABELS:
            description += f" La {DATE_LABELS[sid]} inserita può alimentare la scadenza operativa, con origine visibile e precedenza della data esplicita scelta dall'utente."
        if sid in {"receivables", "expenses"}:
            description += " La panoramica distingue importi aperti e scaduti dai record inseriti; il totale esclude preventivi/bozze e non è un saldo bancario o un pagamento verificato."
        if sid == "inventory":
            description += " Sotto soglia mostra la quantità suggerita per tornare al minimo; nessun ordine o movimento di magazzino viene creato."
        if sid in {"quotes", "invoices", "receivables"}:
            description += " Restano disponibili i passaggi guidati fra preventivo, bozza interna e incasso, con origine e prevenzione dei duplicati."
        description += " Ripetizione futura preparata da comando esplicito, da rivedere, senza pianificatore automatico."
        return ("Implementato e verificato" if verified else "Aggiornamento in verifica"), ("delivered" if verified else "review"), description
    if not entry["core"] and sid == "priority-email":
        return "Disponibile con casella configurata", "existing", "Regole locali sui messaggi acquisiti, motivazione delle priorità, avvisi da istruzioni e bozze da rivedere. Gmail/Outlook richiedono configurazione OAuth; IMAP richiede credenziali autorizzate. Copertura limitata ai messaggi sincronizzati, nessun modello AI esterno dichiarato attivo."
    if not entry["core"] and sid == "agenda":
        return "Disponibile in locale", "existing", "Appuntamenti e ordine del giorno dai dati inseriti, con preparazione del riepilogo. La sincronizzazione di calendari esterni resta un'integrazione distinta."
    if entry["core"] and sid == "morning-overview":
        return ("Aggiornamento verificato" if verified else "Aggiornamento in verifica"), ("delivered" if verified else "review"), str(entry["existing"]) + " Nuovo aggiornamento: prossimo controllo incompleto per servizio, avanzamento manuale, date di servizio con fonte e importi aperti/scaduti di incassi e pagamenti. Importi da dati inseriti, senza doppio conteggio delle bozze o riconciliazione bancaria."
    if entry["core"] and sid == "local-document-exports":
        return ("Copia e download verificati" if verified else "Comando copia in verifica"), ("delivered" if verified else "review"), str(entry["existing"]) + " Nuovo comando Copia il documento per il testo delle schede dei 23 servizi; il download rimane TXT. PDF e CSV di schede selezionate sono estensioni future."
    if entry["core"] and sid == "protected-market-report":
        return ("Matrice verificata nel report" if verified else "Matrice in verifica"), ("delivered" if verified else "review"), str(entry["existing"]) + " Il report è ora esteso con 44 benchmark filtrabili, stato effettivo e fonti deduplicate per URL."
    if entry["core"] and sid == "subscription-checkout":
        return "Percorso predisposto, Stripe da configurare", "existing", str(entry["existing"])
    if entry["core"] and sid == "login-register":
        return "Accesso disponibile; recupero da aggiungere", "existing", str(entry["existing"])
    return "Comportamento e gap documentati", "existing", str(entry["existing"])


def feature_rows(entries: list[dict], by_id: dict, verified: bool) -> str:
    rows = []
    for entry in entries:
        label, state, actual = delivered_behavior(entry, verified)
        reference_html = ""
        products = []
        for reference in entry["references"]:
            product = reference.get("product", "")
            if isinstance(product, dict):
                product = product.get("name", "")
            products.append(str(product))
            reference_html += f"<p><strong>{e(product)}</strong> {refs(reference.get('source_ids', []), by_id)}<br>{e(reference.get('pattern', ''))}</p>"
        constraints = entry["limits"] + entry["checks"]
        if entry["readiness"]:
            readiness = json.dumps(entry["readiness"], ensure_ascii=False) if not isinstance(entry["readiness"], str) else READINESS_LABELS.get(entry["readiness"], entry["readiness"])
            constraints.append("Disponibilità/dependenze nello snapshot: " + readiness)
        status = RESEARCH_STATUS_LABELS.get(entry["proposed_status"], entry["proposed_status"])
        details = f"<details><summary>Proposta originale, limiti e controlli</summary><p><strong>Proposta di ricerca.</strong> {e(entry['proposed'])}</p><p class=\"muted\">Lo stato dello snapshot è {e(status) or 'non indicato'}; il comportamento applicativo sopra è valutato separatamente.</p>{ul([str(value) for value in constraints], 'compact')}</details>"
        search = " ".join([str(entry[key]) for key in ("name", "fit", "existing", "proposed")] + products)
        rows.append(f'''<tr data-feature-group="{e(entry['group'])}" data-feature-state="{e(state)}" data-feature-search="{e(search.casefold())}">
          <td data-label="Funzione"><strong>{e(entry['name'])}</strong><span class="segment">{e(entry['group'])}</span><small>{'Capacità trasversale' if entry['core'] else 'Servizio del catalogo'}</small></td>
          <td data-label="Riferimento e scelta">{reference_html}<p class="muted"><strong>Perché è pertinente.</strong> {e(entry['fit'])}</p></td>
          <td data-label="Applicazione in Filo"><span class="status {state}">{e(label)}</span><p>{e(actual)}</p>{details}</td>
        </tr>''')
    return "\n".join(rows)


def benchmark_method_rows(data: dict) -> str:
    labels = {BENCHMARK_INPUT_NAMES[0]: "Feature commerciali", BENCHMARK_INPUT_NAMES[1]: "Feature specialistiche", BENCHMARK_INPUT_NAMES[2]: "Funzioni trasversali"}
    rows = []
    for filename in BENCHMARK_INPUT_NAMES:
        doc = data[filename]
        method = doc.get("methodology", doc.get("method", {}))
        requested = method.get("fetch_urls_requested", method.get("url_fetch_count", method.get("fetch_url_requests", method.get("fetched_urls_requested", "Non dichiarati"))))
        successful = method.get("fetch_urls_succeeded", method.get("successful_unique_fetched_pages", method.get("fetch_urls_successful_unique", method.get("fetched_urls_succeeded", "Non dichiarati"))))
        rows.append(f"<tr><td>{e(labels[filename])}</td><td>{e(method.get('search_calls', 0))}</td><td>{e(method.get('sources_reviewed', 0))}</td><td>{e(requested)}</td><td>{e(successful)}</td><td>{len(doc['sources'])}, inclusi riusi</td></tr>")
    return "".join(rows)


CSS = r"""
:root{--ink:#222222;--muted:#656565;--paper:#fafafa;--line:#e2e2e2;--orange:#d66229;--soft:#fff1e8;--radius:20px}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:#95431c;text-underline-offset:3px;overflow-wrap:anywhere}button,input,select{font:inherit}button,select,input{min-height:48px}button,select,input,summary,a:focus{outline-offset:4px}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #965a35}a.ref{white-space:nowrap;text-decoration:none;font-size:12px;font-weight:700}p{margin:.7em 0 1em}h1,h2,h3{font-weight:650;line-height:1.14;letter-spacing:-.04em}h1{font-size:clamp(38px,5.2vw,74px);max-width:980px;margin:32px 0 24px}h2{font-size:clamp(28px,3vw,42px);margin:0 0 24px;max-width:850px}h3{font-size:24px;margin:0 0 14px}small{display:block;font-size:13px;line-height:1.5;color:var(--muted);margin-top:6px}ul{padding-left:22px}li{margin:.55em 0}header{max-width:1200px;margin:auto;padding:24px 32px 0}.brand{display:flex;justify-content:space-between;gap:16px;align-items:center;font-size:14px;color:var(--muted)}.wordmark{font-size:26px;font-weight:750;letter-spacing:-.05em;color:var(--ink)}.brand a{color:var(--ink)}main{max-width:1200px;margin:auto;padding:0 32px 72px}.hero{padding:48px 0 52px}.eyebrow{font-size:12px;letter-spacing:.1em;font-weight:700;text-transform:uppercase;color:var(--orange)}.lede{font-size:22px;line-height:1.5;max-width:830px}.summary{border-left:3px solid var(--orange);padding:4px 0 4px 24px;margin-top:36px;max-width:890px}.summary p{font-size:17px}.summary strong{font-weight:650}.nav{display:flex;flex-wrap:wrap;gap:10px 24px;margin-top:32px}.nav a{font-size:14px;min-height:44px;display:flex;align-items:center}.section{border-top:1px solid var(--line);padding:48px 0}.section-intro{max-width:790px;font-size:18px}.map{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:32px}.map article{padding:25px;border:1px solid var(--line);border-radius:var(--radius);background:white}.map h3{font-size:20px}.map .tag{font-size:12px;display:inline-block;padding:4px 9px;background:#f3f3f5;border-radius:20px;margin-bottom:16px}.toolbar{display:flex;gap:16px;margin:26px 0 12px;align-items:end}.toolbar label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600}.toolbar label:first-child{flex:1}.toolbar input,.toolbar select{border:1px solid #d1d1d1;border-radius:12px;padding:10px 14px;background:white;max-width:100%;min-width:0}.toolbar select{width:220px}.table-note{font-size:13px;color:var(--muted);max-width:870px}.table-container{margin-top:26px}table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:14px}th{text-align:left;font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);padding:16px 14px;background:#f3f3f5}td{vertical-align:top;border-bottom:1px solid var(--line);padding:24px 14px;overflow-wrap:anywhere}th:first-child{width:18%}th:nth-child(2){width:30%}th:nth-child(3){width:32%}th:last-child{width:20%}td strong{font-size:16px;line-height:1.35;display:block}td p{line-height:1.5}tr[hidden]{display:none}.segment{font-size:11px;display:inline-block;border-radius:20px;background:#f3f3f5;padding:4px 8px;margin-top:10px}.compact,.price-lines{padding-left:17px;margin-top:0}.compact li,.price-lines li{margin:0 0 14px}.price-lines{font-weight:550}.price-refs{margin-bottom:12px}details{border-top:1px solid var(--line)}summary{cursor:pointer;min-height:48px;padding:13px 0;font-size:13px;font-weight:550}details p{font-size:13px}.caveat{border-left:2px solid #d1d1d1;padding-left:12px;color:#656565}.empty{padding:30px;border:1px solid var(--line);border-radius:14px;color:var(--muted)}.patterns{display:grid;grid-template-columns:repeat(2,1fr);gap:40px 32px;margin:34px 0}.pattern{padding-top:22px;border-top:2px solid var(--ink)}.pattern .num{display:block;font-size:13px;color:var(--orange);font-weight:700;margin-bottom:16px}.pattern h3{font-size:25px}.pattern p{font-size:15px}.pattern .from{font-size:13px;color:var(--muted)}.status{display:inline-block;padding:5px 10px;font-size:12px;font-weight:650;background:#f3f3f5;border-radius:7px;margin-bottom:12px}.status.release{background:var(--soft);color:#8f411d}.flow{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:28px 0}.flow div{border:1px solid var(--line);border-radius:14px;padding:20px;background:white}.flow strong{display:block;font-size:18px}.flow span{font-size:14px;color:var(--muted)}.columns{display:grid;grid-template-columns:repeat(2,1fr);gap:40px;margin-top:28px}.columns>div{min-width:0}.roadmap{margin:32px 0;display:grid;gap:18px}.roadmap article{display:grid;grid-template-columns:180px 1fr;gap:30px;padding:22px 0;border-top:1px solid var(--line)}.roadmap h3{font-size:20px;margin-top:10px}.roadmap ul{margin-top:0}.price-decision{background:#f3f3f5;border-radius:var(--radius);padding:32px;margin:28px 0;display:grid;grid-template-columns:270px 1fr;gap:30px}.price-range{font-size:44px;letter-spacing:-.05em;font-weight:700;line-height:1.1}.price-decision p{margin-top:0}.method-table{table-layout:auto;font-size:14px;margin:24px 0}.method-table th{width:auto!important;text-transform:none;letter-spacing:0;font-size:13px}.method-table td{padding:15px 12px}.sources{display:grid;grid-template-columns:repeat(2,1fr);column-gap:28px;margin-top:28px}.source{scroll-margin-top:16px}.source summary{display:flex;gap:14px;line-height:1.4;padding:18px 0;min-height:64px;font-size:14px}.source-no{font:12px/1.5 ui-monospace,monospace;flex:none;color:var(--orange);padding-top:2px}.source summary small{font-weight:400}.source-body{padding:0 0 18px 30px;font-size:13px}.source-body a{display:block;margin-bottom:18px}.source-body blockquote{padding-left:14px;border-left:2px solid var(--line);margin:12px 0;color:var(--muted)}.muted{color:var(--muted)}.metadata{font-size:12px;margin-top:30px}.metadata pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f3f5;padding:18px;border-radius:12px;font-size:11px}footer{border-top:1px solid var(--line);padding:28px 0 0;color:var(--muted);font-size:13px}#filter-count{font-size:13px;color:var(--muted)}.skip{position:absolute;left:20px;top:-60px;background:white;padding:12px}.skip:focus{top:10px}section:target h2{color:#95431c}
@media(max-width:1000px){main,header{padding-left:24px;padding-right:24px}th:first-child{width:19%}th:nth-child(2){width:32%}th:nth-child(3){width:31%}th:last-child{width:18%}td{padding:22px 10px}.map{gap:12px}.map article{padding:20px}}
@media(max-width:760px){main,header{padding-left:20px;padding-right:20px}.hero{padding:30px 0 36px}h1{margin-top:22px}.lede{font-size:19px}.brand{font-size:12px}.brand a{max-width:135px;text-align:right}.section{padding:36px 0}.map,.patterns,.columns,.sources,.flow{grid-template-columns:1fr}.patterns{gap:24px}.summary{padding-left:17px}.toolbar{flex-direction:column;align-items:stretch;gap:12px}.toolbar select{width:100%}.table-container{margin-top:20px}#competitor-table,#competitor-table tbody,#competitor-table tr,#competitor-table td{display:block;width:100%}#competitor-table thead{display:none}#competitor-table tr{border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:18px;background:white}#competitor-table tr[hidden]{display:none}#competitor-table td{border:0;padding:0;margin-bottom:22px}#competitor-table td:last-child{margin-bottom:0}#competitor-table td::before{content:attr(data-label);display:block;font-size:11px;letter-spacing:.07em;text-transform:uppercase;font-weight:600;color:var(--muted);margin-bottom:10px}#competitor-table td:first-child strong{font-size:21px}.roadmap article{grid-template-columns:1fr;gap:0}.price-decision{grid-template-columns:1fr;padding:24px;gap:20px}.price-range{font-size:40px}.method-wrap{overflow-x:auto}.method-table{min-width:540px}.sources{gap:0}.nav{gap:0 22px}.pattern h3{font-size:23px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{.toolbar,.nav,.skip{display:none}body{background:white;font-size:11pt}main,header{max-width:none;padding:0}.hero{padding:20px 0}.section{padding:25px 0}.map,.patterns,.columns{break-inside:avoid}.table-container{overflow:visible}td{font-size:9pt}.source{break-inside:avoid}a{color:inherit}details{display:block}h1{font-size:36pt}.sources{grid-template-columns:1fr}}
.feature-table th:first-child{width:20%}.feature-table th:nth-child(2){width:35%}.feature-table th:last-child{width:45%}.feature-table td p{font-size:14px}.feature-table td strong{display:inline;font-size:14px}.feature-table td:first-child strong{display:block;font-size:16px}.feature-table .status{margin:2px 0 12px;font-size:11px}.feature-table .delivered{border:1px solid #d66229;background:white;color:#95431c}.feature-table .review{border:1px solid #d1d1d1;background:white}.feature-toolbar{flex-wrap:wrap}.feature-toolbar label:nth-child(2){flex:none}.feature-toolbar label:nth-child(3){flex:none}.feature-toolbar label:first-child{min-width:240px}.feature-toolbar select{width:195px}#feature-count{color:var(--muted);font-size:13px}.feature-outcomes{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}.feature-outcomes p{margin:0;font-size:14px;padding:18px;border:1px solid var(--line);border-radius:14px}.feature-outcomes strong{display:block;font-size:24px;font-weight:650;letter-spacing:-.03em}.feature-outcomes small{font-size:12px}.feature-table details p{font-size:13px}
@media(max-width:760px){.feature-outcomes{grid-template-columns:repeat(2,1fr)}.feature-toolbar label:first-child{min-width:0}.feature-toolbar select{width:100%}#feature-table,#feature-table tbody,#feature-table tr,#feature-table td{display:block;width:100%}#feature-table thead{display:none}#feature-table tr{border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:18px;background:white}#feature-table tr[hidden]{display:none}#feature-table td{border:0;padding:0;margin-bottom:22px}#feature-table td:last-child{margin-bottom:0}#feature-table td::before{content:attr(data-label);display:block;font-size:11px;letter-spacing:.07em;text-transform:uppercase;font-weight:600;color:var(--muted);margin-bottom:10px}#feature-table td:first-child strong{font-size:21px}.feature-outcomes p{padding:14px}}
"""

JS = r"""
(() => {
  const search = document.getElementById('competitor-search');
  const segment = document.getElementById('competitor-segment');
  const rows = [...document.querySelectorAll('#competitor-table tbody tr')];
  const fold = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('it');
  const apply = () => {
    const terms = fold(search.value.trim()).split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const row of rows) {
      const keep = (!segment.value || row.dataset.segment === segment.value) && terms.every(term => fold(row.dataset.search).includes(term));
      row.hidden = !keep;
      visible += Number(keep);
    }
    document.getElementById('filter-count').textContent = `${visible} di ${rows.length} prodotti`;
    document.getElementById('filter-empty').hidden = visible !== 0;
  };
  search.addEventListener('input', apply);
  segment.addEventListener('change', apply);
  document.getElementById('filter-reset').addEventListener('click', () => { search.value = ''; segment.value = ''; apply(); search.focus(); });
  document.addEventListener('click', event => {
    const link = event.target.closest('a.ref');
    if (!link) return;
    const source = document.querySelector(link.getAttribute('href'));
    if (source?.tagName === 'DETAILS') source.open = true;
  });
  apply();
  const featureSearch = document.getElementById('feature-search');
  const featureGroup = document.getElementById('feature-group');
  const featureState = document.getElementById('feature-state');
  const featureRows = [...document.querySelectorAll('#feature-table tbody tr')];
  const applyFeatures = () => {
    const terms = fold(featureSearch.value.trim()).split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const row of featureRows) {
      const keep = (!featureGroup.value || row.dataset.featureGroup === featureGroup.value) && (!featureState.value || row.dataset.featureState === featureState.value) && terms.every(term => fold(row.dataset.featureSearch).includes(term));
      row.hidden = !keep;
      visible += Number(keep);
    }
    document.getElementById('feature-count').textContent = `${visible} di ${featureRows.length} funzioni`;
    document.getElementById('feature-empty').hidden = visible !== 0;
  };
  featureSearch.addEventListener('input', applyFeatures);
  featureGroup.addEventListener('change', applyFeatures);
  featureState.addEventListener('change', applyFeatures);
  document.getElementById('feature-reset').addEventListener('click', () => {featureSearch.value = '';featureGroup.value = '';featureState.value = '';applyFeatures();featureSearch.focus();});
  applyFeatures();
})();
"""


def build(data: dict, hashes: dict, release_status: str) -> tuple[str, dict]:
    sources, by_id = collect_sources(data)
    entries = feature_entries(data)
    verified = release_status == "verified"
    product_count = sum(len(doc.get("competitors", [])) for doc in data.values())
    methods = {name: doc.get("methodology", doc.get("method")) for name, doc in data.items()}
    requested_count = sum(method["sources_reviewed"] for method in methods.values())
    calls = sum(method["search_calls"] for method in methods.values())
    previous_requested = sum(methods[name]["sources_reviewed"] for name in BASE_INPUT_NAMES)
    previous_calls = sum(methods[name]["search_calls"] for name in BASE_INPUT_NAMES)
    new_requested = requested_count - previous_requested
    new_calls = calls - previous_calls
    if product_count != 19 or previous_requested != 139:
        raise ValueError("Research scope changed: review report prose and methodology before rebuilding")
    release = "Aggiornamento operativo implementato e verificato" if verified else "Flussi presenti; aggiornamento operativo in verifica"
    metadata = {
        "title": "Filo per studi e imprese di servizi: una giornata operativa, con meno reinserimenti",
        "as_of": DATE,
        "target": "Studi e imprese di servizi in Italia",
        "product_count": product_count,
        "unique_cited_urls": len(sources),
        "sources_reviewed": requested_count,
        "sources_reviewed_definition": "Somma dei numResults richiesti: non pagine uniche lette o fonti indipendenti.",
        "search_calls": calls,
        "previous_search_calls": previous_calls,
        "previous_results_requested": previous_requested,
        "new_search_calls": new_calls,
        "new_results_requested": new_requested,
        "feature_benchmarks": len(entries),
        "catalog_features": 33,
        "cross_cutting_features": 11,
        "release_status": release_status,
        "input_sha256": hashes,
        "methods_by_snapshot": methods,
        "source_urls": [source["url"] for source in sources],
    }
    pattern_data = [
        ("01", "Dati aziendali inseriti una volta", "Odoo, Fatture in Cloud, Fattura24 e Tieni il Conto PRO", ["odoo-contacts", "odoo-quotes", "fic-features", "f24-quote-help", "tic-pro-help"],
         "Il pattern verificato è l'anagrafica che alimenta i documenti, anziché ricopiare ragione sociale e contatti a ogni passaggio.",
         "Filo riusa il profilo dello studio nei documenti locali pertinenti. Campi opzionali all'inizio, controllo dei dati mancanti nella preparazione e provenienza leggibile. Una vera anagrafica clienti comune fra moduli resta un'evoluzione distinta.",
         "Misurare quanti campi l'utente deve reinserire per preparare il secondo documento reale; non chiamare validazione fiscale il semplice salvataggio di partita IVA."),
        ("02", "Preventivo, bozza e incasso collegati", "Odoo, Zoho, Bitrix24 e Fatture in Cloud", ["odoo-quotes", "zoho-sales-to-invoice", "bitrix-invoicing", "fic-features"],
         "I gestionali trasformano il documento precedente mantenendo cliente e importi. Nei servizi questo percorso è più pertinente della gestione di un magazzino complesso.",
         "Un comando esplicito prepara la bozza successiva da rivedere: conserva origine, importi e IVA e impedisce doppioni se viene ripetuto. Il passaggio a incasso crea una previsione da seguire, non un pagamento ricevuto.",
         "Misurare tempo, campi riscritti e conversioni duplicate. Il percorso locale non emette fatture, non trasmette XML/SDI e non incassa denaro."),
        ("03", "Prossimo passo e azione nella panoramica", "Pipedrive, HubSpot, Outlook Copilot e Tieni il Conto", ["pipedrive-activities", "hubspot-sales", "microsoft-priority", "tic"],
         "Attività collegate, code giornaliere e spiegazioni delle priorità riducono i passaggi per capire cosa controllare. Le prestazioni restano dichiarazioni dei produttori, non risultati misurati per Filo.",
         "Tenere prima scadute e attività di oggi, mostrare perché sono in evidenza e offrire completamento diretto con annullamento. Il report resta un riepilogo laterale: la persona vede subito il lavoro.",
         "Verificare se una persona identifica il compito corretto senza aprire altre schermate; contare tempo e clic. Separare compiti eseguibili da quelli in attesa di un cliente."),
        ("04", "Una ricerca per tutte le schede", "HubSpot e Tieni il Conto PRO", ["hubspot-search", "tic-pro-help"],
         "Ricerca per testo/proprietà e accesso diretto alle funzioni evitano di ricordare dove si trovano i dati. È un pattern utile in un catalogo Filo con 23 moduli.",
         "Ricerca sulle attività del proprio spazio con nome, contatto, tipo, stato e apertura del risultato. Il termine digitato conduce alla scheda, senza scegliere prima il servizio.",
         "Testare ricerche di preventivi, clienti e scadenze con termini parziali. La ricerca copre schede locali; posta non sincronizzata, allegati non importati e file esterni non diventano ricercabili per descrizione."),
    ]
    patterns = "".join(f'''<article class="pattern"><span class="num">{number}</span><h3>{e(title)}</h3><p class="from">Pattern da {e(vendors)} {refs(ids, by_id)}</p><p>{e(fact)}</p><p><strong>Come adattarlo.</strong> {e(optimization)}</p><p class="muted"><strong>Cosa verificare.</strong> {e(check)}</p></article>''' for number, title, vendors, ids, fact, optimization, check in pattern_data)
    html_doc = f'''<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Ricerca competitiva Filo per studi e imprese di servizi: 19 prodotti, prezzi con condizioni, funzionalità da adattare e roadmap. Fonti consultate il 5 ottobre 2026."><title>Filo — Ricerca di mercato per studi e servizi</title><style>{CSS}</style></head>
<body><a class="skip" href="#main">Vai al contenuto</a><header><div class="brand"><span class="wordmark">filo<span style="color:var(--orange)">.</span></span><span>Ricerca di mercato · 5 ottobre 2026</span><a href="https://filo-production-65a1.up.railway.app/app#services">Apri Filo</a></div></header>
<main id="main"><section class="hero"><div class="eyebrow">Studi e imprese di servizi · Italia</div><h1>Una giornata operativa, con meno reinserimenti.</h1><p class="lede">Per Filo la direzione più utile è collegare email, clienti, documenti e scadenze in un prossimo passo chiaro. I competitor documentano flussi utili da cui partire; la loro ottimizzazione va verificata sulle attività reali degli studi.</p>
<div class="summary"><p><strong>Partire dai servizi.</strong> Consulenti, agenzie e piccoli studi hanno un percorso comune: richiesta → preventivo → lavoro → documento → incasso. È il percorso da rendere immediato, prima di ampliare logistica o contabilità.</p><p><strong>Ogni funzione ha il suo riferimento.</strong> La matrice confronta 33 funzioni di catalogo e 11 capacità trasversali: pattern scelto, adattamento in Filo e dipendenze ancora necessarie. I 23 moduli locali diventano percorsi guidati con 94 controlli manuali persistenti; le integrazioni esterne restano esplicite.</p><p><strong>Il prezzo richiede una prova di valore.</strong> Fattura24 Professional rinnova a 120€ + IVA/anno, pari a 10€/mese equivalente. Filo deve mostrare risparmio operativo ulteriore per giustificare l'ipotesi 19–29€ + IVA/mese, ancora da validare.</p></div>
<nav class="nav" aria-label="Indice del report"><a href="#market">Mercato e segmenti</a><a href="#comparison">19 prodotti a confronto</a><a href="#feature-benchmarks">Ogni funzione, il suo riferimento</a><a href="#patterns">Funzioni da adattare</a><a href="#filo">Filo oggi e roadmap</a><a href="#pricing">Prezzo da testare</a><a href="#method">Metodo e fonti</a></nav></section>

<section class="section" id="market"><div class="eyebrow">Posizionamento</div><h2>Tre categorie competono per pezzi della stessa giornata.</h2><p class="section-intro">I gestionali italiani presidiano documenti e adempimenti; le suite collegano clienti e processi; gli strumenti di produttività organizzano email e attività. Filo può diventare il punto di lavoro quotidiano di uno studio, mantenendo espliciti i servizi esterni necessari.</p>
<div class="map"><article><span class="tag">Gestionali italiani</span><h3>Documenti, incassi, fiscalità</h3><p>Fatture in Cloud, Fattura24, Danea Easyfatt, TeamSystem Enterprise, Tieni il Conto e PRO.</p><p><strong>Da adattare:</strong> anagrafiche comuni, documenti collegati, scadenziario e avvisi.</p><small>I sei prodotti italiani appartengono a tre gruppi: TeamSystem, Zucchetti e Fattura24. Non sono sei fornitori indipendenti.</small></article><article><span class="tag">Suite e CRM</span><h3>Cliente e prossimo passo</h3><p>Odoo, Zoho One, Zoho Books, HubSpot, Pipedrive e Bitrix24.</p><p><strong>Da adattare:</strong> contesto del cliente, conversione dei documenti, attività collegate e ricerca.</p><small>La complessità di un ERP o di molte app/seat è un costo di configurazione da valutare, non una prova che il prodotto sia inadatto.</small></article><article><span class="tag">Email e lavoro</span><h3>Priorità, collaborazione, ricorrenze</h3><p>Superhuman, Outlook Copilot, Gemini in Gmail, Front, Missive, ClickUp e Asana.</p><p><strong>Da adattare:</strong> motivazione della priorità, attese esplicite, ricorrenze e azioni annullabili.</p><small>Inbox condivise e responsabilità di squadra richiedono organizzazioni e permessi reali: Filo ha oggi spazi privati separati.</small></article></div>
<div class="columns"><div><h3>Il primo cliente da servire</h3><p>Studio, consulente o impresa di servizi che segue richieste, appuntamenti, proposte e pagamenti. La selezione è quella indicata dall'utente; non deriva da una stima della dimensione di mercato.</p><p>Per questo segmento vanno prima riuso dei dati, documenti, rinnovi e incassi. Commercio/artigianato richiedono poi movimenti di magazzino, ordini e rapporti d'intervento; produzione e PMI strutturate richiedono costi di commessa, ERP e maggiore integrazione.</p></div><div><h3>La semplicità deve riguardare il lavoro</h3><p>ISTAT rileva software gestionali nel 56,0% delle imprese con almeno 10 addetti nel 2025. Quasi il 60% delle aziende che hanno valutato ma non realizzato investimenti in IA cita mancanza di competenze adeguate. {refs(['istat-ict-2025'],by_id)}</p><p class="muted">Sono popolazioni e denominatori diversi. L'indagine non rappresenta gli studi con meno di 10 addetti e non misura domanda, disponibilità a pagare o benefici di Filo. La ricerca AssoSoftware su 520 PMI è cross-settoriale e coinvolge un'associazione di fornitori. {refs(['assosoftware-pmi-2023'],by_id)}</p></div></div></section>

<section class="section" id="comparison"><div class="eyebrow">Evidenza competitiva</div><h2>Funzioni e prezzi, con le condizioni che cambiano il confronto.</h2><p class="section-intro">Le funzioni sotto sono descritte nelle pagine ufficiali consultate. Un piano di ingresso può non includerle tutte. Prezzi annuali equivalenti, addebiti mensili, licenze per utente e licenze per azienda restano distinti.</p>
<div class="toolbar"><label for="competitor-search">Cerca prodotto o funzione<input id="competitor-search" type="search" placeholder="Es. preventivi, ricorrenze, priorità" autocomplete="off"></label><label for="competitor-segment">Categoria<select id="competitor-segment"><option value="">Tutte le categorie</option><option>Gestionali italiani</option><option>Suite e CRM</option><option>Email e lavoro</option></select></label><button id="filter-reset" type="button" style="border:1px solid #d1d1d1;border-radius:12px;background:white;padding:10px 16px">Azzera filtri</button></div>
<div id="filter-count" role="status" aria-live="polite">19 di 19 prodotti</div><p class="table-note">EUR e USD sono riportati come nelle fonti, senza conversioni. Apri “Condizioni e limiti” per IVA, promozioni, vincoli e componenti aggiuntive. “Non verificato” indica un dato mancante, non un prezzo nullo.</p>
<div class="table-container"><table id="competitor-table"><caption class="skip" style="position:static;display:none">Confronto di 19 prodotti, funzioni ufficiali e prezzi osservati</caption><thead><tr><th scope="col">Prodotto</th><th scope="col">Funzioni verificate</th><th scope="col">Prezzo e condizioni</th><th scope="col">Cosa adattare in Filo</th></tr></thead><tbody>{competitor_rows(data,by_id)}</tbody></table><p id="filter-empty" class="empty" hidden>Nessun prodotto corrisponde. Prova un termine più breve o azzera i filtri.</p></div></section>

<section class="section" id="feature-benchmarks"><div class="eyebrow">Benchmark per ogni funzione</div><h2>Per ogni compito, un riferimento e un adattamento concreto.</h2><p class="section-intro">Il confronto generale è stato approfondito sulle 33 funzioni del catalogo e su 11 capacità trasversali. “Migliore riferimento” significa aderenza al compito, semplicità per uno studio, disponibilità tecnica e possibilità di controllare i dati. Non indica una leadership di mercato o una superiorità collaudata.</p>
<div class="feature-outcomes"><p><strong>33</strong>funzioni di catalogo<small>23 moduli, email, agenda e 8 collegamenti</small></p><p><strong>11</strong>capacità trasversali<small>Profilo, ricerca, accesso e altri flussi comuni</small></p><p><strong>94</strong>passaggi manuali<small>{'Checklist specifiche verificate sui 23 moduli' if verified else 'Nuove checklist in verifica sui 23 moduli'}</small></p><p><strong>10</strong>date di servizio<small>Fonte visibile e precedenza della scadenza esplicita</small></p></div>
<p><span class="status {'delivered' if verified else 'review'}">{'Aggiornamento operativo implementato e verificato' if verified else 'Aggiornamento operativo in verifica'}</span> Le nuove checklist conservano i passaggi spuntati e propongono il primo controllo incompleto. Date, importi, consensi e approvazioni annotati restano dichiarazioni della persona. Non rappresentano verifiche di un provider o azioni di altri utenti.</p>
<div class="toolbar feature-toolbar"><label for="feature-search">Cerca funzione, strumento o flusso<input id="feature-search" type="search" autocomplete="off" placeholder="Es. note spese, Stripe, checklist"></label><label for="feature-group">Tipo<select id="feature-group"><option value="">Tutte le funzioni</option><option>Moduli operativi</option><option>Email e agenda</option><option>Collegamenti esterni</option><option>Funzioni trasversali</option></select></label><label for="feature-state">Stato in Filo<select id="feature-state"><option value="">Tutti gli stati</option><option value="delivered">Implementato e verificato</option><option value="review">In verifica</option><option value="existing">Comportamento disponibile</option><option value="external">Da integrare</option></select></label><button id="feature-reset" type="button" style="border:1px solid #d1d1d1;border-radius:12px;background:white;padding:10px 16px">Azzera filtri</button></div><div id="feature-count" role="status" aria-live="polite">44 di 44 funzioni</div><p class="table-note">Il riferimento spiega il pattern selezionato. L'applicazione in Filo distingue funzioni verificate e dipendenze esterne; la proposta originale della ricerca rimane consultabile nei dettagli. I benchmark specialistici aggiungono strumenti pertinenti al confronto generale dei 19 prodotti.</p>
<div class="table-container"><table id="feature-table" class="feature-table"><thead><tr><th scope="col">Funzione</th><th scope="col">Riferimento e scelta</th><th scope="col">Applicazione in Filo</th></tr></thead><tbody>{feature_rows(entries,by_id,verified)}</tbody></table><p id="feature-empty" class="empty" hidden>Nessuna funzione corrisponde. Cambia il termine o azzera i filtri.</p></div></section>

<section class="section" id="patterns"><div class="eyebrow">Scelte di prodotto</div><h2>Quattro pattern da rendere più immediati per uno studio.</h2><p class="section-intro">L'ispirazione riguarda flussi funzionali pubblici. Filo li reimplementa con interfaccia e codice propri. “Ottimizzare” è qui una scelta di progettazione da verificare, non una superiorità già dimostrata.</p><div class="patterns">{patterns}</div>
<p class="muted">Un piccolo team racconta di aver lasciato Front per Missive per costi e funzioni non usate. È una sola testimonianza con referral dichiarato, utile a formulare un'ipotesi su piani leggibili e cataloghi essenziali; non prova frequenza del problema, prezzi attuali o ritorno economico. {refs(['helium-practitioner'],by_id)}</p></section>

<section class="section" id="filo"><div class="eyebrow">Applicazione e limiti</div><h2>Collegare ciò che Filo gestisce già, poi integrare ciò che manca.</h2><span class="status release">{e(release)}</span><p class="section-intro">I 23 moduli locali ora hanno checklist persistenti e ripetizione futura da rivedere; 10 date di servizio alimentano le priorità, la panoramica distingue incassi e pagamenti aperti/scaduti e i documenti si possono copiare. Restano i flussi già presenti: profilo riutilizzato, preventivo → bozza fattura → incasso, ricerca trasversale e completamento con annullamento. Le integrazioni esterne e la collaborazione di squadra richiedono implementazioni dedicate.</p>
<div class="flow"><div><strong>1. Preventivo</strong><span>Cliente, attività e importi inseriti dalla persona. Il documento si rivede prima del passo successivo.</span></div><div><strong>2. Bozza fattura</strong><span>Riuso dei dati, origine visibile e conversione senza duplicati. Documento interno da verificare.</span></div><div><strong>3. Incasso da seguire</strong><span>Scadenza e prossimo passo. Importo previsto distinto da pagamento realmente registrato.</span></div></div>
<div class="columns"><div><span class="status">Disponibile nel prodotto</span><h3>Schede e priorità nel proprio spazio</h3>{ul(['23 moduli locali per clienti, preventivi, bozze fattura, incassi, spese, fornitori, acquisti, progetti, scadenze, documenti e organizzazione.', 'Documenti testuali preparati dai dati inseriti, importi/IVA calcolati e download; nessuna azienda o fattura fittizia necessaria.', 'Panoramica ordinata per scadute, oggi, attività da controllare e prossime date; alert di scorte da quantità e soglie dichiarate.', 'Email e agenda: collegamenti Gmail/Outlook/IMAP predisposti, con configurazione e autorizzazione reali richieste; agenda locale disponibile.', 'Le priorità delle email reali usano regole locali sui messaggi acquisiti; non va dichiarato un modello AI esterno già attivo né copertura di posta non sincronizzata.'])}</div><div><span class="status">Richiede un lavoro dedicato</span><h3>Servizi esterni e condivisione</h3>{ul(['SDI, ricevute e conservazione: provider, credenziali, XML valido e gestione degli esiti. La bozza locale non è una fattura emessa.', 'Banche, pagamenti e riconciliazione: collegamento autorizzato, consensi e dati acquisiti; nessun saldo bancario inferito.', 'Calendari esterni, PEC, ERP, social, telefonia e WhatsApp: integrazioni reali; il catalogo non le attiva da solo.', 'OCR e file: caricamento protetto ed estrazione da verificare; una scheda documento non importa automaticamente il file.', 'Team e commercialista: organizzazioni, inviti, ruoli e audit. Gli account attuali sono privati e isolati.'])}</div></div>
<div class="roadmap"><article><div><span class="status">P1 · Flussi locali</span><h3>Fare meno inserimenti ripetuti</h3></div><div>{ul(['Canoni e rinnovi pianificati: la ripetizione manuale futura è ora preparata da comando esplicito; un pianificatore con periodicità, skip e protezione da duplicati richiede un’evoluzione dedicata. Pattern ClickUp, Asana e Danea.', 'Import CSV di clienti e fornitori: anteprima, mappatura colonne, duplicati visibili e annullamento della sola importazione.', 'Incassi e pagamenti: gli importi aperti e scaduti dai record inseriti sono ora separati nella panoramica, senza sommare preventivi e bozze. Restano da aggiungere vista per periodo, copertura dei dati e distinzione dei pagamenti verificati tramite banca.', 'Pacchetto per il commercialista: selezione esplicita del periodo, documenti mancanti segnalati e download; condivisione solo dopo permessi veri.'])}<p>{refs(['clickup-recurrence','asana-pricing','easyfatt-features','f24-pricing','tic-pro-help'],by_id)}</p></div></article><article><div><span class="status">P2 · Integrazioni</span><h3>Acquisire dati e canali reali</h3></div><div>{ul(['Scegliere provider SDI/conservazione, banche e calendari in base a disponibilità tecnica, contratti e costo totale.', 'OCR con anteprima e conferma; non creare registrazioni fiscali sulla sola estrazione automatica.', 'Workspace condiviso con membri/ruoli prima di inbox di team, assegnazioni, commenti e controlli sulle doppie risposte.', 'AI sui numeri soltanto con dati affidabili, fonti cliccabili, periodo e autorizzazioni; distinguere calcolo deterministico e commento del modello.'])}<p>{refs(['front-rules','missive-rules','fic-features','ts-enterprise','f24-home'],by_id)}</p></div></article></div></section>

<section class="section" id="pricing"><div class="eyebrow">Raccomandazione commerciale</div><h2>Testare il prezzo sulla riduzione del lavoro ripetitivo.</h2><p class="section-intro">I listini descrivono alternative, ma non dicono quanto un cliente italiano pagherà per Filo. Il confronto evidenzia una pressione sui prezzi amministrativi e una possibilità di valore nei flussi collegati; nessuna delle due misura già la disponibilità a pagare.</p>
<div class="price-decision"><div><div class="price-range">19–29€</div><p>+ IVA/mese<br><strong>Ipotesi da validare</strong><br>Per spazio privato individuale</p></div><div><p>Una fascia da provare con studi e imprese di servizi che gestiscono richieste e documenti ricorrenti. Deve dichiarare cosa include, i limiti, il supporto e i costi di eventuali provider. Non va venduta come prezzo per team finché non esiste un workspace condiviso.</p><p><strong>Stripe resta senza prezzo approvato o attivato.</strong> La ricerca non cambia tariffe né avvia addebiti. Una scelta fra 19 e 29€ richiede evidenza da utenti reali e costi di servizio.</p></div></div>
<div class="columns"><div><h3>Il confronto che conta</h3><p>Fattura24 Professional rinnova a 120€ + IVA/anno, pari a 10€/mese equivalente, con limiti di piano; la promo del primo anno è 48€ + IVA. Fatture in Cloud Standard Forfettari rinnova a 96€ + IVA/anno, ma l'offerta è riservata al regime forfettario. {refs(['f24-pricing','f24-2026-notice','fic-pricing'],by_id)}</p><p>Missive Starter e Pipedrive Lite sono mostrati a 14 USD/utente/mese equivalente annuale. Bitrix24 Basic costa 49€/organizzazione/mese equivalente con anticipo annuale per 5 utenti. Queste basi diverse non vanno ordinate come un unico listino “dal più economico”. {refs(['missive-pricing','pipedrive-pricing','bitrix-pricing-eu'],by_id)}</p></div><div><h3>La prova prima di scegliere</h3>{ul(['Osservare utenti del segmento mentre svolgono attività reali: secondo documento per lo stesso cliente, scadenza da recuperare, incasso da seguire.', 'Rilevare tempo e clic prima/dopo, campi ripetuti e correzioni; usare lo stesso compito per un confronto leggibile.', 'Presentare separatamente le proposte 19€ e 29€ con contenuti identici, raccogliendo accettazione, obiezioni e conversione effettiva.', 'Stimare costi di email, AI, pagamenti, provider esterni, supporto e onboarding prima di confermare un margine. I dati attuali non permettono quel conto.'])}</div></div>
<p class="muted">Nessuna promessa di sostituzione di personale o di risparmio economico è ricavabile da questi listini. Il confronto volontario dei tempi in Filo registra dati forniti dall'utente; non dimostra automaticamente produttività, ritorno economico o causalità.</p></section>

<section class="section" id="method"><div class="eyebrow">Metodo e provenienza</div><h2>44 funzioni, {len(sources)} URL citati e confini espliciti.</h2><p>Ricerca di pagine pubbliche al 5 ottobre 2026, con Exa: confronto generale di 19 prodotti e approfondimento per 33 funzioni di catalogo e 11 capacità trasversali. Documentazione, listini e pagine ufficiali sostengono i fatti sui competitor; due fonti di contesto italiano e una testimonianza con referral dichiarato completano il quadro.</p><p><strong>Ricerca precedente: {previous_calls} ricerche e {previous_requested} risultati richiesti. Approfondimento per funzione: {new_calls} nuove ricerche e {new_requested} risultati richiesti.</strong> Totale: {calls} ricerche, {requested_count} risultati richiesti. Il campo <code>sources_reviewed</code> è la somma dei <code>numResults</code>, non pagine uniche lette o fonti validate. I {len(sources)} URL citati sono deduplicati globalmente nei sette snapshot; le fonti riusate non sono contate come nuove fonti.</p>
<div class="method-wrap"><table class="method-table"><thead><tr><th scope="col">Filone</th><th scope="col">Ricerche</th><th scope="col">Risultati richiesti</th><th scope="col">Fetch richiesti</th><th scope="col">Successi registrati</th><th scope="col">Fonti citate</th></tr></thead><tbody><tr><td>Gestionali italiani</td><td>5</td><td>50</td><td>18 URL</td><td>Non conteggiati nello snapshot</td><td>16</td></tr><tr><td>Suite e CRM</td><td>5</td><td>35</td><td>19 URL</td><td>15 pagine uniche</td><td>15</td></tr><tr><td>Email e lavoro</td><td>5</td><td>40</td><td>19 tentativi / 18 URL unici</td><td>17 pagine uniche</td><td>17</td></tr><tr><td>Contesto italiano</td><td>2</td><td>14</td><td>2 URL</td><td>2 pagine</td><td>2</td></tr>{benchmark_method_rows(data)}</tbody></table></div>
<p class="muted">I conteggi delle fonti citate per filone includono riusi e non si sommano come URL unici: il totale deduplicato è riportato sopra. I fetch riusciti restano distinti dalle citazioni; il filone italiano non conserva il conteggio dei successi, quindi non viene dichiarato un totale globale omogeneo.</p><div class="columns"><div><h3>Cosa sostiene la ricerca</h3>{ul(['Esistenza e funzionamento descritto delle feature; importi soltanto dove leggibili e collegati a un piano e alle sue condizioni.', 'Scelta di priorità per il target indicato dall’utente: documenti, incassi, scadenze e prossimo passo.', 'Ipotesi di ottimizzazione fondate sui pattern osservati; da validare con test e uso reale.'])}</div><div><h3>Cosa resta da verificare</h3>{ul(['Nessuna prova comparativa negli account dei prodotti, intervista a clienti Filo o misura di disponibilità a pagare.', 'Nessun TAM, quota di mercato, stima di dipendenti sostituibili o beneficio causale.', 'Listini core dinamici mancanti e checkout italiani non confermati dove indicato. L’assenza di prezzo estratto non implica assenza del servizio.', 'Recensioni di concorrenti, listini storici e claim di ROI promozionali esclusi come prova numerica.'])}</div></div>
<h3 style="margin-top:32px">Fonti citate</h3><p class="muted">Ogni riferimento apre la fonte pertinente. Gli estratti brevi documentano cosa è stato letto; le condizioni complete restano nella pagina originale e negli snapshot JSON.</p><div class="sources">{source_appendix(sources)}</div>
<details class="metadata"><summary>Dati di provenienza e riproducibilità</summary><p>File HTML autonomo, senza risorse o chiamate esterne. Builder: <code>scripts/build-market-review.py</code>. Input JSON locali nella stessa cartella del report. Nessuna ricerca web viene rieseguita dal builder.</p><pre>{e(json.dumps(metadata,ensure_ascii=False,indent=2))}</pre></details></section>
<footer>Filo · Ricerca competitiva per studi e imprese di servizi · Lettura delle fonti al 5 ottobre 2026. Le indicazioni di prodotto sono raccomandazioni, le condizioni commerciali sono fotografie delle fonti consultate.</footer></main><script>{JS}</script></body></html>'''
    return html_doc, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "docs/research")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/research/market-review-2026-10-05.html")
    parser.add_argument("--release-status", choices=("in-progress", "verified"), default="in-progress")
    args = parser.parse_args()
    data, hashes = load_inputs(args.input_dir)
    report, metadata = build(data, hashes, args.release_status)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    text = html.unescape(re.sub(r"<[^>]+>", " ", report.split("<style>", 1)[0] + report.split("</style>", 1)[1].split("<script>", 1)[0]))
    print(json.dumps({"output": str(args.output), "products": metadata["product_count"], "feature_benchmarks": metadata["feature_benchmarks"], "catalog_features": metadata["catalog_features"], "cross_cutting_features": metadata["cross_cutting_features"], "unique_cited_urls": metadata["unique_cited_urls"], "sources_reviewed": metadata["sources_reviewed"], "search_calls": metadata["search_calls"], "words_including_metadata": len(text.split()), "bytes": len(report.encode()), "release_status": args.release_status}, ensure_ascii=False))


if __name__ == "__main__":
    main()
