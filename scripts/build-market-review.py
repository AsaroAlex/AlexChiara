#!/usr/bin/env python3
"""Build the source-backed, standalone Italian competitor review (stdlib only).

Run from any directory. Input files stay unchanged. Release status is explicit:
use --release-status verified only after the four application changes pass checks.
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
INPUT_NAMES = (
    "italian-competitors.json",
    "suite-competitors.json",
    "productivity-competitors.json",
    "market-context.json",
)
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
        if name != "market-context.json":
            if not isinstance(doc.get("competitors"), list) or not doc["competitors"]:
                raise ValueError(f"No competitor list in {name}")
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


CSS = r"""
:root{--ink:#222222;--muted:#656565;--paper:#fafafa;--line:#e2e2e2;--orange:#d66229;--soft:#fff1e8;--radius:20px}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:#95431c;text-underline-offset:3px;overflow-wrap:anywhere}button,input,select{font:inherit}button,select,input{min-height:48px}button,select,input,summary,a:focus{outline-offset:4px}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #965a35}a.ref{white-space:nowrap;text-decoration:none;font-size:12px;font-weight:700}p{margin:.7em 0 1em}h1,h2,h3{font-weight:650;line-height:1.14;letter-spacing:-.04em}h1{font-size:clamp(38px,5.2vw,74px);max-width:980px;margin:32px 0 24px}h2{font-size:clamp(28px,3vw,42px);margin:0 0 24px;max-width:850px}h3{font-size:24px;margin:0 0 14px}small{display:block;font-size:13px;line-height:1.5;color:var(--muted);margin-top:6px}ul{padding-left:22px}li{margin:.55em 0}header{max-width:1200px;margin:auto;padding:24px 32px 0}.brand{display:flex;justify-content:space-between;gap:16px;align-items:center;font-size:14px;color:var(--muted)}.wordmark{font-size:26px;font-weight:750;letter-spacing:-.05em;color:var(--ink)}.brand a{color:var(--ink)}main{max-width:1200px;margin:auto;padding:0 32px 72px}.hero{padding:48px 0 52px}.eyebrow{font-size:12px;letter-spacing:.1em;font-weight:700;text-transform:uppercase;color:var(--orange)}.lede{font-size:22px;line-height:1.5;max-width:830px}.summary{border-left:3px solid var(--orange);padding:4px 0 4px 24px;margin-top:36px;max-width:890px}.summary p{font-size:17px}.summary strong{font-weight:650}.nav{display:flex;flex-wrap:wrap;gap:10px 24px;margin-top:32px}.nav a{font-size:14px;min-height:44px;display:flex;align-items:center}.section{border-top:1px solid var(--line);padding:48px 0}.section-intro{max-width:790px;font-size:18px}.map{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:32px}.map article{padding:25px;border:1px solid var(--line);border-radius:var(--radius);background:white}.map h3{font-size:20px}.map .tag{font-size:12px;display:inline-block;padding:4px 9px;background:#f3f3f5;border-radius:20px;margin-bottom:16px}.toolbar{display:flex;gap:16px;margin:26px 0 12px;align-items:end}.toolbar label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600}.toolbar label:first-child{flex:1}.toolbar input,.toolbar select{border:1px solid #d1d1d1;border-radius:12px;padding:10px 14px;background:white;max-width:100%;min-width:0}.toolbar select{width:220px}.table-note{font-size:13px;color:var(--muted);max-width:870px}.table-container{margin-top:26px}table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:14px}th{text-align:left;font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);padding:16px 14px;background:#f3f3f5}td{vertical-align:top;border-bottom:1px solid var(--line);padding:24px 14px;overflow-wrap:anywhere}th:first-child{width:18%}th:nth-child(2){width:30%}th:nth-child(3){width:32%}th:last-child{width:20%}td strong{font-size:16px;line-height:1.35;display:block}td p{line-height:1.5}tr[hidden]{display:none}.segment{font-size:11px;display:inline-block;border-radius:20px;background:#eeeae4;padding:4px 8px;margin-top:10px}.compact,.price-lines{padding-left:17px;margin-top:0}.compact li,.price-lines li{margin:0 0 14px}.price-lines{font-weight:550}.price-refs{margin-bottom:12px}details{border-top:1px solid var(--line)}summary{cursor:pointer;min-height:48px;padding:13px 0;font-size:13px;font-weight:550}details p{font-size:13px}.caveat{border-left:2px solid #dcc4b3;padding-left:12px;color:#67604f}.empty{padding:30px;border:1px solid var(--line);border-radius:14px;color:var(--muted)}.patterns{display:grid;grid-template-columns:repeat(2,1fr);gap:40px 32px;margin:34px 0}.pattern{padding-top:22px;border-top:2px solid var(--ink)}.pattern .num{display:block;font-size:13px;color:var(--orange);font-weight:700;margin-bottom:16px}.pattern h3{font-size:25px}.pattern p{font-size:15px}.pattern .from{font-size:13px;color:var(--muted)}.status{display:inline-block;padding:5px 10px;font-size:12px;font-weight:650;background:#eeece6;border-radius:7px;margin-bottom:12px}.status.release{background:var(--soft);color:#8f411d}.flow{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:28px 0}.flow div{border:1px solid var(--line);border-radius:14px;padding:20px;background:white}.flow strong{display:block;font-size:18px}.flow span{font-size:14px;color:var(--muted)}.columns{display:grid;grid-template-columns:repeat(2,1fr);gap:40px;margin-top:28px}.columns>div{min-width:0}.roadmap{margin:32px 0;display:grid;gap:18px}.roadmap article{display:grid;grid-template-columns:180px 1fr;gap:30px;padding:22px 0;border-top:1px solid var(--line)}.roadmap h3{font-size:20px;margin-top:10px}.roadmap ul{margin-top:0}.price-decision{background:#f3f3f5;border-radius:var(--radius);padding:32px;margin:28px 0;display:grid;grid-template-columns:270px 1fr;gap:30px}.price-range{font-size:44px;letter-spacing:-.05em;font-weight:700;line-height:1.1}.price-decision p{margin-top:0}.method-table{table-layout:auto;font-size:14px;margin:24px 0}.method-table th{width:auto!important;text-transform:none;letter-spacing:0;font-size:13px}.method-table td{padding:15px 12px}.sources{display:grid;grid-template-columns:repeat(2,1fr);column-gap:28px;margin-top:28px}.source{scroll-margin-top:16px}.source summary{display:flex;gap:14px;line-height:1.4;padding:18px 0;min-height:64px;font-size:14px}.source-no{font:12px/1.5 ui-monospace,monospace;flex:none;color:var(--orange);padding-top:2px}.source summary small{font-weight:400}.source-body{padding:0 0 18px 30px;font-size:13px}.source-body a{display:block;margin-bottom:18px}.source-body blockquote{padding-left:14px;border-left:2px solid var(--line);margin:12px 0;color:var(--muted)}.muted{color:var(--muted)}.metadata{font-size:12px;margin-top:30px}.metadata pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f3f5;padding:18px;border-radius:12px;font-size:11px}footer{border-top:1px solid var(--line);padding:28px 0 0;color:var(--muted);font-size:13px}#filter-count{font-size:13px;color:var(--muted)}.skip{position:absolute;left:20px;top:-60px;background:white;padding:12px}.skip:focus{top:10px}section:target h2{color:#95431c}
@media(max-width:1000px){main,header{padding-left:24px;padding-right:24px}th:first-child{width:19%}th:nth-child(2){width:32%}th:nth-child(3){width:31%}th:last-child{width:18%}td{padding:22px 10px}.map{gap:12px}.map article{padding:20px}}
@media(max-width:760px){main,header{padding-left:20px;padding-right:20px}.hero{padding:30px 0 36px}h1{margin-top:22px}.lede{font-size:19px}.brand{font-size:12px}.brand a{max-width:135px;text-align:right}.section{padding:36px 0}.map,.patterns,.columns,.sources,.flow{grid-template-columns:1fr}.patterns{gap:24px}.summary{padding-left:17px}.toolbar{flex-direction:column;align-items:stretch;gap:12px}.toolbar select{width:100%}.table-container{margin-top:20px}#competitor-table,#competitor-table tbody,#competitor-table tr,#competitor-table td{display:block;width:100%}#competitor-table thead{display:none}#competitor-table tr{border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:18px;background:white}#competitor-table tr[hidden]{display:none}#competitor-table td{border:0;padding:0;margin-bottom:22px}#competitor-table td:last-child{margin-bottom:0}#competitor-table td::before{content:attr(data-label);display:block;font-size:11px;letter-spacing:.07em;text-transform:uppercase;font-weight:600;color:var(--muted);margin-bottom:10px}#competitor-table td:first-child strong{font-size:21px}.roadmap article{grid-template-columns:1fr;gap:0}.price-decision{grid-template-columns:1fr;padding:24px;gap:20px}.price-range{font-size:40px}.method-wrap{overflow-x:auto}.method-table{min-width:540px}.sources{gap:0}.nav{gap:0 22px}.pattern h3{font-size:23px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{.toolbar,.nav,.skip{display:none}body{background:white;font-size:11pt}main,header{max-width:none;padding:0}.hero{padding:20px 0}.section{padding:25px 0}.map,.patterns,.columns{break-inside:avoid}.table-container{overflow:visible}td{font-size:9pt}.source{break-inside:avoid}a{color:inherit}details{display:block}h1{font-size:36pt}.sources{grid-template-columns:1fr}}
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
})();
"""


def build(data: dict, hashes: dict, release_status: str) -> tuple[str, dict]:
    sources, by_id = collect_sources(data)
    product_count = sum(len(doc.get("competitors", [])) for doc in data.values())
    methods = {name: doc.get("methodology", doc.get("method")) for name, doc in data.items()}
    requested_count = sum(method["sources_reviewed"] for method in methods.values())
    calls = sum(method["search_calls"] for method in methods.values())
    if product_count != 19 or requested_count != 139:
        raise ValueError("Research scope changed: review report prose and methodology before rebuilding")
    release = "Implementate e verificate nella release" if release_status == "verified" else "Implementazione in corso nella release"
    metadata = {
        "title": "Filo per studi e imprese di servizi: una giornata operativa, con meno reinserimenti",
        "as_of": DATE,
        "target": "Studi e imprese di servizi in Italia",
        "product_count": product_count,
        "unique_cited_urls": len(sources),
        "sources_reviewed": requested_count,
        "sources_reviewed_definition": "Somma dei numResults richiesti: non pagine uniche lette o fonti indipendenti.",
        "search_calls": calls,
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
<div class="summary"><p><strong>Partire dai servizi.</strong> Consulenti, agenzie e piccoli studi hanno un percorso comune: richiesta → preventivo → lavoro → documento → incasso. È il percorso da rendere immediato, prima di ampliare logistica o contabilità.</p><p><strong>Adattare quattro pattern concreti.</strong> Profilo aziendale riutilizzato, documenti collegati, azioni dirette nella panoramica e ricerca trasversale. Le funzionalità pubbliche di Odoo, Pipedrive, HubSpot e dei gestionali italiani ne documentano la pertinenza.</p><p><strong>Il prezzo richiede una prova di valore.</strong> Fattura24 Professional rinnova a 120€ + IVA/anno, pari a 10€/mese equivalente. Filo deve mostrare risparmio operativo ulteriore per giustificare l'ipotesi 19–29€ + IVA/mese, ancora da validare.</p></div>
<nav class="nav" aria-label="Indice del report"><a href="#market">Mercato e segmenti</a><a href="#comparison">19 prodotti a confronto</a><a href="#patterns">Funzioni da adattare</a><a href="#filo">Filo oggi e roadmap</a><a href="#pricing">Prezzo da testare</a><a href="#method">Metodo e fonti</a></nav></section>

<section class="section" id="market"><div class="eyebrow">Posizionamento</div><h2>Tre categorie competono per pezzi della stessa giornata.</h2><p class="section-intro">I gestionali italiani presidiano documenti e adempimenti; le suite collegano clienti e processi; gli strumenti di produttività organizzano email e attività. Filo può diventare il punto di lavoro quotidiano di uno studio, mantenendo espliciti i servizi esterni necessari.</p>
<div class="map"><article><span class="tag">Gestionali italiani</span><h3>Documenti, incassi, fiscalità</h3><p>Fatture in Cloud, Fattura24, Danea Easyfatt, TeamSystem Enterprise, Tieni il Conto e PRO.</p><p><strong>Da adattare:</strong> anagrafiche comuni, documenti collegati, scadenziario e avvisi.</p><small>I sei prodotti italiani appartengono a tre gruppi: TeamSystem, Zucchetti e Fattura24. Non sono sei fornitori indipendenti.</small></article><article><span class="tag">Suite e CRM</span><h3>Cliente e prossimo passo</h3><p>Odoo, Zoho One, Zoho Books, HubSpot, Pipedrive e Bitrix24.</p><p><strong>Da adattare:</strong> contesto del cliente, conversione dei documenti, attività collegate e ricerca.</p><small>La complessità di un ERP o di molte app/seat è un costo di configurazione da valutare, non una prova che il prodotto sia inadatto.</small></article><article><span class="tag">Email e lavoro</span><h3>Priorità, collaborazione, ricorrenze</h3><p>Superhuman, Outlook Copilot, Gemini in Gmail, Front, Missive, ClickUp e Asana.</p><p><strong>Da adattare:</strong> motivazione della priorità, attese esplicite, ricorrenze e azioni annullabili.</p><small>Inbox condivise e responsabilità di squadra richiedono organizzazioni e permessi reali: Filo ha oggi spazi privati separati.</small></article></div>
<div class="columns"><div><h3>Il primo cliente da servire</h3><p>Studio, consulente o impresa di servizi che segue richieste, appuntamenti, proposte e pagamenti. La selezione è quella indicata dall'utente; non deriva da una stima della dimensione di mercato.</p><p>Per questo segmento vanno prima riuso dei dati, documenti, rinnovi e incassi. Commercio/artigianato richiedono poi movimenti di magazzino, ordini e rapporti d'intervento; produzione e PMI strutturate richiedono costi di commessa, ERP e maggiore integrazione.</p></div><div><h3>La semplicità deve riguardare il lavoro</h3><p>ISTAT rileva software gestionali nel 56,0% delle imprese con almeno 10 addetti nel 2025. Quasi il 60% delle aziende che hanno valutato ma non realizzato investimenti in IA cita mancanza di competenze adeguate. {refs(['istat-ict-2025'],by_id)}</p><p class="muted">Sono popolazioni e denominatori diversi. L'indagine non rappresenta gli studi con meno di 10 addetti e non misura domanda, disponibilità a pagare o benefici di Filo. La ricerca AssoSoftware su 520 PMI è cross-settoriale e coinvolge un'associazione di fornitori. {refs(['assosoftware-pmi-2023'],by_id)}</p></div></div></section>

<section class="section" id="comparison"><div class="eyebrow">Evidenza competitiva</div><h2>Funzioni e prezzi, con le condizioni che cambiano il confronto.</h2><p class="section-intro">Le funzioni sotto sono descritte nelle pagine ufficiali consultate. Un piano di ingresso può non includerle tutte. Prezzi annuali equivalenti, addebiti mensili, licenze per utente e licenze per azienda restano distinti.</p>
<div class="toolbar"><label for="competitor-search">Cerca prodotto o funzione<input id="competitor-search" type="search" placeholder="Es. preventivi, ricorrenze, priorità" autocomplete="off"></label><label for="competitor-segment">Categoria<select id="competitor-segment"><option value="">Tutte le categorie</option><option>Gestionali italiani</option><option>Suite e CRM</option><option>Email e lavoro</option></select></label><button id="filter-reset" type="button" style="border:1px solid #d1d1d1;border-radius:12px;background:white;padding:10px 16px">Azzera filtri</button></div>
<div id="filter-count" role="status" aria-live="polite">19 di 19 prodotti</div><p class="table-note">EUR e USD sono riportati come nelle fonti, senza conversioni. Apri “Condizioni e limiti” per IVA, promozioni, vincoli e componenti aggiuntive. “Non verificato” indica un dato mancante, non un prezzo nullo.</p>
<div class="table-container"><table id="competitor-table"><caption class="skip" style="position:static;display:none">Confronto di 19 prodotti, funzioni ufficiali e prezzi osservati</caption><thead><tr><th scope="col">Prodotto</th><th scope="col">Funzioni verificate</th><th scope="col">Prezzo e condizioni</th><th scope="col">Cosa adattare in Filo</th></tr></thead><tbody>{competitor_rows(data,by_id)}</tbody></table><p id="filter-empty" class="empty" hidden>Nessun prodotto corrisponde. Prova un termine più breve o azzera i filtri.</p></div></section>

<section class="section" id="patterns"><div class="eyebrow">Scelte di prodotto</div><h2>Quattro pattern da rendere più immediati per uno studio.</h2><p class="section-intro">L'ispirazione riguarda flussi funzionali pubblici. Filo li reimplementa con interfaccia e codice propri. “Ottimizzare” è qui una scelta di progettazione da verificare, non una superiorità già dimostrata.</p><div class="patterns">{patterns}</div>
<p class="muted">Un piccolo team racconta di aver lasciato Front per Missive per costi e funzioni non usate. È una sola testimonianza con referral dichiarato, utile a formulare un'ipotesi su piani leggibili e cataloghi essenziali; non prova frequenza del problema, prezzi attuali o ritorno economico. {refs(['helium-practitioner'],by_id)}</p></section>

<section class="section" id="filo"><div class="eyebrow">Applicazione e limiti</div><h2>Collegare ciò che Filo gestisce già, poi integrare ciò che manca.</h2><span class="status release">{e(release)}</span><p class="section-intro">L'aggiornamento applicativo riguarda quattro miglioramenti locali: profilo aziendale riutilizzabile; passaggi guidati preventivo → bozza fattura → incasso; ricerca delle attività fra moduli; completamento dalla panoramica con annullamento. Il loro completamento non equivale a realizzare un ERP o una suite collaborativa.</p>
<div class="flow"><div><strong>1. Preventivo</strong><span>Cliente, attività e importi inseriti dalla persona. Il documento si rivede prima del passo successivo.</span></div><div><strong>2. Bozza fattura</strong><span>Riuso dei dati, origine visibile e conversione senza duplicati. Documento interno da verificare.</span></div><div><strong>3. Incasso da seguire</strong><span>Scadenza e prossimo passo. Importo previsto distinto da pagamento realmente registrato.</span></div></div>
<div class="columns"><div><span class="status">Disponibile nel prodotto</span><h3>Schede e priorità nel proprio spazio</h3>{ul(['23 moduli locali per clienti, preventivi, bozze fattura, incassi, spese, fornitori, acquisti, progetti, scadenze, documenti e organizzazione.', 'Documenti testuali preparati dai dati inseriti, importi/IVA calcolati e download; nessuna azienda o fattura fittizia necessaria.', 'Panoramica ordinata per scadute, oggi, attività da controllare e prossime date; alert di scorte da quantità e soglie dichiarate.', 'Email e agenda: collegamenti Gmail/Outlook/IMAP predisposti, con configurazione e autorizzazione reali richieste; agenda locale disponibile.', 'Le priorità delle email reali usano regole locali sui messaggi acquisiti; non va dichiarato un modello AI esterno già attivo né copertura di posta non sincronizzata.'])}</div><div><span class="status">Richiede un lavoro dedicato</span><h3>Servizi esterni e condivisione</h3>{ul(['SDI, ricevute e conservazione: provider, credenziali, XML valido e gestione degli esiti. La bozza locale non è una fattura emessa.', 'Banche, pagamenti e riconciliazione: collegamento autorizzato, consensi e dati acquisiti; nessun saldo bancario inferito.', 'Calendari esterni, PEC, ERP, social, telefonia e WhatsApp: integrazioni reali; il catalogo non le attiva da solo.', 'OCR e file: caricamento protetto ed estrazione da verificare; una scheda documento non importa automaticamente il file.', 'Team e commercialista: organizzazioni, inviti, ruoli e audit. Gli account attuali sono privati e isolati.'])}</div></div>
<div class="roadmap"><article><div><span class="status">P1 · Flussi locali</span><h3>Fare meno inserimenti ripetuti</h3></div><div>{ul(['Canoni e rinnovi ricorrenti: ricreare un promemoria/bozza alla data concordata, con prossima occorrenza, skip e protezione da duplicati. Pattern ClickUp, Asana e Danea.', 'Import CSV di clienti e fornitori: anteprima, mappatura colonne, duplicati visibili e annullamento della sola importazione.', 'Vista di incassi e impegni: separare preventivi, bozze, entrate previste e pagamenti registrati, con periodo e copertura dei dati.', 'Pacchetto per il commercialista: selezione esplicita del periodo, documenti mancanti segnalati e download; condivisione solo dopo permessi veri.'])}<p>{refs(['clickup-recurrence','asana-pricing','easyfatt-features','f24-pricing','tic-pro-help'],by_id)}</p></div></article><article><div><span class="status">P2 · Integrazioni</span><h3>Acquisire dati e canali reali</h3></div><div>{ul(['Scegliere provider SDI/conservazione, banche e calendari in base a disponibilità tecnica, contratti e costo totale.', 'OCR con anteprima e conferma; non creare registrazioni fiscali sulla sola estrazione automatica.', 'Workspace condiviso con membri/ruoli prima di inbox di team, assegnazioni, commenti e controlli sulle doppie risposte.', 'AI sui numeri soltanto con dati affidabili, fonti cliccabili, periodo e autorizzazioni; distinguere calcolo deterministico e commento del modello.'])}<p>{refs(['front-rules','missive-rules','fic-features','ts-enterprise','f24-home'],by_id)}</p></div></article></div></section>

<section class="section" id="pricing"><div class="eyebrow">Raccomandazione commerciale</div><h2>Testare il prezzo sulla riduzione del lavoro ripetitivo.</h2><p class="section-intro">I listini descrivono alternative, ma non dicono quanto un cliente italiano pagherà per Filo. Il confronto evidenzia una pressione sui prezzi amministrativi e una possibilità di valore nei flussi collegati; nessuna delle due misura già la disponibilità a pagare.</p>
<div class="price-decision"><div><div class="price-range">19–29€</div><p>+ IVA/mese<br><strong>Ipotesi da validare</strong><br>Per spazio privato individuale</p></div><div><p>Una fascia da provare con studi e imprese di servizi che gestiscono richieste e documenti ricorrenti. Deve dichiarare cosa include, i limiti, il supporto e i costi di eventuali provider. Non va venduta come prezzo per team finché non esiste un workspace condiviso.</p><p><strong>Stripe resta senza prezzo approvato o attivato.</strong> La ricerca non cambia tariffe né avvia addebiti. Una scelta fra 19 e 29€ richiede evidenza da utenti reali e costi di servizio.</p></div></div>
<div class="columns"><div><h3>Il confronto che conta</h3><p>Fattura24 Professional rinnova a 120€ + IVA/anno, pari a 10€/mese equivalente, con limiti di piano; la promo del primo anno è 48€ + IVA. Fatture in Cloud Standard Forfettari rinnova a 96€ + IVA/anno, ma l'offerta è riservata al regime forfettario. {refs(['f24-pricing','f24-2026-notice','fic-pricing'],by_id)}</p><p>Missive Starter e Pipedrive Lite sono mostrati a 14 USD/utente/mese equivalente annuale. Bitrix24 Basic costa 49€/organizzazione/mese equivalente con anticipo annuale per 5 utenti. Queste basi diverse non vanno ordinate come un unico listino “dal più economico”. {refs(['missive-pricing','pipedrive-pricing','bitrix-pricing-eu'],by_id)}</p></div><div><h3>La prova prima di scegliere</h3>{ul(['Osservare utenti del segmento mentre svolgono attività reali: secondo documento per lo stesso cliente, scadenza da recuperare, incasso da seguire.', 'Rilevare tempo e clic prima/dopo, campi ripetuti e correzioni; usare lo stesso compito per un confronto leggibile.', 'Presentare separatamente le proposte 19€ e 29€ con contenuti identici, raccogliendo accettazione, obiezioni e conversione effettiva.', 'Stimare costi di email, AI, pagamenti, provider esterni, supporto e onboarding prima di confermare un margine. I dati attuali non permettono quel conto.'])}</div></div>
<p class="muted">Nessuna promessa di sostituzione di personale o di risparmio economico è ricavabile da questi listini. Il confronto volontario dei tempi in Filo registra dati forniti dall'utente; non dimostra automaticamente produttività, ritorno economico o causalità.</p></section>

<section class="section" id="method"><div class="eyebrow">Metodo e provenienza</div><h2>19 prodotti, 50 URL citati e confini espliciti.</h2><p>Ricerca di pagine pubbliche al 5 ottobre 2026, con Exa: documentazione, listini e pagine prodotto ufficiali per i fatti sui competitor; due fonti di contesto italiano e una testimonianza con referral dichiarato. I documenti statistici hanno date proprie, riportate nelle fonti.</p><p><strong>{calls} ricerche, {requested_count} risultati richiesti.</strong> Il campo <code>sources_reviewed</code> è la somma dei <code>numResults</code> richiesti: non indica {requested_count} pagine uniche lette, né {requested_count} fonti validate. I 50 URL citati sono deduplicati globalmente dai quattro snapshot.</p>
<div class="method-wrap"><table class="method-table"><thead><tr><th scope="col">Filone</th><th scope="col">Ricerche</th><th scope="col">Risultati richiesti</th><th scope="col">Fetch richiesti</th><th scope="col">Successi registrati</th><th scope="col">Fonti citate</th></tr></thead><tbody><tr><td>Gestionali italiani</td><td>5</td><td>50</td><td>18 URL</td><td>Non conteggiati nello snapshot</td><td>16</td></tr><tr><td>Suite e CRM</td><td>5</td><td>35</td><td>19 URL</td><td>15 pagine uniche</td><td>15</td></tr><tr><td>Email e lavoro</td><td>5</td><td>40</td><td>19 tentativi / 18 URL unici</td><td>17 pagine uniche</td><td>17</td></tr><tr><td>Contesto italiano</td><td>2</td><td>14</td><td>2 URL</td><td>2 pagine</td><td>2</td></tr></tbody></table></div>
<p class="muted">Il totale dei fetch riusciti non è ricostruibile con un denominatore omogeneo: alcuni snapshot contano successi, altri fonti selezionate dopo la lettura. Non viene sostituito con un numero inventato.</p><div class="columns"><div><h3>Cosa sostiene la ricerca</h3>{ul(['Esistenza e funzionamento descritto delle feature; importi soltanto dove leggibili e collegati a un piano e alle sue condizioni.', 'Scelta di priorità per il target indicato dall’utente: documenti, incassi, scadenze e prossimo passo.', 'Ipotesi di ottimizzazione fondate sui pattern osservati; da validare con test e uso reale.'])}</div><div><h3>Cosa resta da verificare</h3>{ul(['Nessuna prova comparativa negli account dei prodotti, intervista a clienti Filo o misura di disponibilità a pagare.', 'Nessun TAM, quota di mercato, stima di dipendenti sostituibili o beneficio causale.', 'Listini core dinamici mancanti e checkout italiani non confermati dove indicato. L’assenza di prezzo estratto non implica assenza del servizio.', 'Recensioni di concorrenti, listini storici e claim di ROI promozionali esclusi come prova numerica.'])}</div></div>
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
    print(json.dumps({"output": str(args.output), "products": metadata["product_count"], "unique_cited_urls": metadata["unique_cited_urls"], "sources_reviewed": metadata["sources_reviewed"], "search_calls": metadata["search_calls"], "words_including_metadata": len(text.split()), "bytes": len(report.encode()), "release_status": args.release_status}, ensure_ascii=False))


if __name__ == "__main__":
    main()
