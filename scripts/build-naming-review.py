#!/usr/bin/env python3
"""Build a standalone naming/branding proposal from reviewed local evidence.

The builder uses only Python's standard library. It does not query registries,
purchase a domain, reserve a username or modify the application brand.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-10-05"
INPUT_PATHS = {
    "market": "docs/research/naming-market-2026-10-05.json",
    "collisions": "docs/research/naming-collisions-2026-10-05.json",
    "domains": "docs/research/naming-domains-2026-10-05.json",
    "domain_methods": "docs/research/naming-domain-methods-2026-10-05.json",
    "concepts": "docs/branding/naming-concepts-2026-10-05.json",
}
FINALISTS = ("Giorvia", "Spazelia", "Prontela")


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def load_inputs() -> tuple[dict, dict]:
    data, hashes = {}, {}
    for key, relative in INPUT_PATHS.items():
        raw = (ROOT / relative).read_bytes()
        doc = json.loads(raw)
        date = doc.get("research_date", doc.get("researchDate", doc.get("observed_date", doc.get("date"))))
        if date != DATE:
            raise ValueError(f"Snapshot date mismatch: {relative}")
        if key not in {"concepts", "domains"} and not isinstance(doc.get("sources"), list):
            raise ValueError(f"Missing source list: {relative}")
        data[key] = doc
        hashes[relative] = hashlib.sha256(raw).hexdigest()
    if {item["name"] for item in data["concepts"]["concepts"]} != set(FINALISTS):
        raise ValueError("Expected three reviewed brand concepts")
    domains = data["domains"]
    if len(domains["checks"]) != domains["summary"]["domainsChecked"]:
        raise ValueError("Domain count does not match observed checks")
    domain_sources = []
    for check in domains["checks"]:
        if check["status"] not in {"registered", "unverified"}:
            raise ValueError("Unexpected confirmed domain status: review evidence first")
        domain_sources.append({"id": domain_source_id(check["domain"]), "url": check["source"]["url"], "title": f"{check['domain']} — {check['source']['provider']}", "excerpt": check.get("evidenceQuote", check["observation"]), "checked_at": check["checkedAt"]})
        for index, source in enumerate(check.get("additionalSources", [])):
            domain_sources.append({"id": domain_source_id(check["domain"]) + f"-extra-{index}", "url": source["url"], "title": f"{check['domain']} — {source['provider']} RDAP", "excerpt": source["observation"]})
    for index, source in enumerate(domains["controls"]):
        domain_sources.append({"id": f"domain-control-{index}", "url": source["url"], "title": f"Controllo: {source['domain']}", "excerpt": source["observed"]})
    domains["sources"] = domain_sources
    return data, hashes


def sources_for(data: dict) -> tuple[list[dict], dict]:
    by_url, by_id = {}, {}
    for key in ("market", "collisions", "domains", "domain_methods"):
        for raw in data[key]["sources"]:
            sid, url = raw["id"], raw["url"]
            if urlparse(url).scheme not in {"http", "https"}:
                raise ValueError(f"Unexpected source URL: {sid}")
            if sid in by_id and by_id[sid]["url"] != url:
                raise ValueError(f"Source ID collision: {sid}")
            source = by_url.setdefault(url, {**raw, "number": len(by_url) + 1})
            by_id[sid] = source
    return list(by_url.values()), by_id


def refs(ids: list[str], by_id: dict) -> str:
    numbers = []
    for sid in ids:
        if sid not in by_id:
            raise ValueError(f"Unresolved source ID: {sid}")
        if by_id[sid]["number"] not in numbers:
            numbers.append(by_id[sid]["number"])
    return " ".join(f'<a class="ref" href="#source-{number}" aria-label="Fonte {number}">[{number}]</a>' for number in numbers)


def ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{e(item)}</li>" for item in items) + "</ul>"


def inline_svg(relative: str, prefix: str, hashes: dict) -> str:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT / "docs/branding/concepts"):
        raise ValueError("SVG outside approved concept assets")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("SVG document declarations are not allowed")
    root = ET.fromstring(raw)
    allowed = {"svg", "title", "desc", "g", "path", "circle", "rect", "text", "ellipse", "line", "polyline", "polygon"}
    ids = {node.attrib["id"] for node in root.iter() if "id" in node.attrib}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in allowed:
            raise ValueError(f"Unexpected SVG element in {relative}")
        for key, value in list(node.attrib.items()):
            local = key.rsplit("}", 1)[-1]
            if local.startswith("on") or local in {"href", "src"}:
                raise ValueError("Interactive/external SVG resources are not allowed")
            if local == "id":
                node.set(key, prefix + value)
            elif local == "aria-labelledby":
                node.set(key, " ".join(prefix + part if part in ids else part for part in value.split()))
            elif "url(" in value:
                raise ValueError("SVG URL references are not allowed")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    hashes[relative] = hashlib.sha256(raw).hexdigest()
    return ET.tostring(root, encoding="unicode")


def indexed(items: list[dict]) -> dict:
    return {item["name"].casefold(): item for item in items}


def concept_cards(data: dict, by_id: dict, hashes: dict) -> str:
    market = indexed(data["market"]["candidate_names"])
    collisions = indexed(data["collisions"]["candidates"])
    cards = []
    labels = {"Spazelia": "Direzione consigliata", "Giorvia": "Alternativa da confrontare", "Prontela": "Concept esplorativo · .com registrato"}
    concepts = indexed(data["concepts"]["concepts"])
    for name in data["concepts"]["creative_ranking"]:
        concept = concepts[name.casefold()]
        name, slug = concept["name"], concept["slug"]
        candidate, collision = market[name.casefold()], collisions[name.casefold()]
        wordmark = inline_svg(concept["assets"]["wordmark"], slug + "-card-", hashes)
        icon = inline_svg(concept["assets"]["icon"], slug + "-icon-", hashes)
        palette = concept["palette"]
        swatches = "".join(f'<span class="swatch"><i style="background:{e(palette[key])}"></i><small>{e(label)}<br>{e(palette[key])}</small></span>' for key, label in (("canvas", "Bianco"), ("ink", "Carbone"), ("surface", "Grigio"), ("brand_orange", "Accento")))
        source_ids = [sid for row in collision.get("similar_names", []) for sid in row.get("source_ids", [])]
        source_ids += [sid for row in collision.get("other_public_usage", []) if row.get("kind") == "archived_social_username" for sid in row.get("source_ids", [])]
        caution = {
            "Giorvia": "Da controllare Girovia, nome vicino usato nell'e-commerce. Un vecchio archivio riporta @Giorvia su X: disponibilità attuale non verificata.",
            "Spazelia": "Da provare la dettatura: la z e la vicinanza a “spaziale” possono produrre varianti. Marchi e handle restano da verificare.",
            "Prontela": "Prontela.com risulta registrato. Nomi vicini Prontelas e Prontotela: richiedono verifica. Non è la prima scelta per un dominio internazionale coerente.",
        }[name]
        cards.append(f'''<article class="concept" id="{e(slug)}"><span class="tag">{e(labels[name])}</span><div class="wordmark">{wordmark}</div><p class="pronunciation">{e(candidate['pronunciation_it'])} · {candidate['letters']} lettere</p><h3>{e(concept['personality'])}</h3><p>{e(candidate['rationale'])}</p><div class="logo-detail"><span>{icon}</span><p>{e(concept['icon_concept'])}</p></div><div class="swatches">{swatches}</div><p class="payoff">{e(data['concepts']['main_payoff'])}</p><p class="caution">{e(caution)} {refs(source_ids,by_id)}</p></article>''')
    return "\n".join(cards)


def domain_source_id(domain: str) -> str:
    if not re.fullmatch(r"[a-z]+\.(it|com|eu)", domain):
        raise ValueError(f"Unexpected candidate domain {domain}")
    return "domain-" + domain.replace(".", "-")


def domain_rows(checks: list[dict], by_id: dict) -> str:
    rows = []
    for check in checks:
        available_indication = "AVAILABLE" in (check.get("evidenceQuote", "") + " " + check["observation"])
        registered = check["status"] == "registered"
        if registered:
            status, css, observation = "Registrato nella fonte", "registered", "Record di registrazione con date presente."
        elif available_indication:
            status, css, observation = "Indicazione terza: AVAILABLE", "indicated", "Who.is riporta AVAILABLE. Freschezza del registro non verificata."
        else:
            status, css, observation = "Disponibilità non confermata", "unverified", "Nessun record mostrato dalla fonte. Non conferma che sia acquistabile."
        source = refs([domain_source_id(check["domain"])], by_id)
        snapshot = f"Snapshot dichiarato: {check['sourceSnapshotAt']}." if check.get("sourceSnapshotAt") else "Data del registro non mostrata."
        rows.append(f'''<tr><td data-label="Dominio"><span class="domain">{e(check['domain'])}</span></td><td data-label="Esito"><span class="domain-status {css}">{e(status)}</span></td><td data-label="Lettura"><span>{e(check['checkedAt'])}</span><small>{e(snapshot)}</small></td><td data-label="Evidenza e limite">{e(observation)} {source}<small>Fonte: {e(check['source']['provider'])}, non registro autorevole. Checkout specifico non confermato.</small></td></tr>''')
    return "".join(rows)


def domain_matrix(domains: dict, by_id: dict) -> str:
    finalists = {name.casefold() for name in FINALISTS}
    principal = sorted([check for check in domains["checks"] if check["candidate"] in finalists and check["tld"] in {"it", "com"}], key=lambda check: (["spazelia", "giorvia", "prontela"].index(check["candidate"]), check["tld"] == "com"))
    others = [check for check in domains["checks"] if check not in principal]
    header = '<thead><tr><th scope="col">Dominio</th><th scope="col">Esito</th><th scope="col">Lettura UTC</th><th scope="col">Evidenza e limite</th></tr></thead>'
    return f'''<p><strong>19 domini controllati: 3 registrati nella fonte, 16 non verificati, 0 disponibilità confermate.</strong> Nessun dominio acquistato o prenotato. Le indicazioni AVAILABLE sono terze e contengono anche testo generico contraddittorio; serve un riscontro aggiornato nel registrar.</p><div class="domain-wrap"><table>{header}<tbody>{domain_rows(principal,by_id)}</tbody></table><details class="metadata"><summary>Altri 13 domini e riserve esaminati</summary><table>{header}<tbody>{domain_rows(others,by_id)}</tbody></table></details></div><p class="quiet">Cattura del rapporto: {e(domains['recordedAt'])}. La lettura è in UTC, non è la data garantita della risposta del registro.</p>'''


def method_sentence(data: dict) -> str:
    market = data["market"]["methodology"]
    collisions = data["collisions"]["methodology"]
    domains = data["domains"]["researchAccounting"]
    methods = data["domain_methods"]["methodology"]
    requested = market["sources_reviewed"] + collisions["sources_reviewed"] + domains["sourcesReviewed"] + methods["search_results_requested"]
    queries = market["search_calls"] + collisions["search_queries"] + domains["searchCalls"] + methods["search_calls"]
    return f"{queries} ricerche, {requested} risultati richiesti nei quattro filoni. Mercato 18/3, collisioni 48/8, domini 18/3 e metodo domini 12/2 (risultati/ricerche). Sono incluse fonti ufficiali riusate dal confronto precedente."


def source_appendix(sources: list[dict]) -> str:
    output = []
    for source in sources:
        number = source["number"]
        title = source.get("title", source.get("publisher", source["id"].replace("-", " ")))
        quote = source.get("excerpt", source.get("evidence", source.get("observation", "")))
        if isinstance(quote, list):
            quote = " ".join(str(part) for part in quote)
        words = str(quote).split()
        excerpt = " ".join(words[:20]) + ("…" if len(words) > 20 else "")
        output.append(f'''<details class="source" id="source-{number}"><summary><span>{number:02}</span>{e(title)}</summary><div><a href="{e(source['url'])}" target="_blank" rel="noopener noreferrer">{e(source['url'])}</a><blockquote>{e(excerpt)}</blockquote></div></details>''')
    return "\n".join(output)


CSS = r"""
:root{--ink:#222;--muted:#656565;--line:#e3e3e5;--orange:#ab3f18;--accent:#e96034;--surface:#f3f3f5}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#fff;color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}a{color:var(--orange);text-underline-offset:3px;overflow-wrap:anywhere}a:focus-visible,summary:focus-visible,button:focus-visible{outline:3px solid var(--orange);outline-offset:4px}header,main{max-width:1180px;margin:auto;padding:0 32px}header{padding-top:24px;display:flex;justify-content:space-between;gap:20px;align-items:center;font-size:13px;color:var(--muted)}header strong{font-size:20px;color:var(--ink);letter-spacing:-.04em}h1,h2,h3{letter-spacing:-.04em;line-height:1.12;font-weight:650}h1{font-size:clamp(38px,5.4vw,68px);max-width:880px;margin:26px 0}h2{font-size:clamp(27px,3.5vw,40px);max-width:800px;margin:0 0 20px}h3{font-size:22px;margin:20px 0 12px}p{margin:.6em 0 1em}.hero{padding:58px 0 48px}.eyebrow{font-size:12px;font-weight:650;letter-spacing:.09em;text-transform:uppercase;color:var(--orange)}.lede{font-size:21px;line-height:1.5;max-width:850px}.recommendation{border-left:3px solid var(--accent);padding:8px 0 8px 22px;margin:28px 0;max-width:850px}.recommendation p{margin:0}.nav{display:flex;gap:22px;flex-wrap:wrap;margin-top:24px}.nav a{display:flex;align-items:center;min-height:44px;font-size:14px}.section{border-top:1px solid var(--line);padding:44px 0}.section-intro{font-size:18px;max-width:820px}.columns{display:grid;grid-template-columns:1fr 1fr;gap:38px}.columns>div{min-width:0}.quiet{color:var(--muted);font-size:14px}.concepts{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:28px}.concept{border:1px solid var(--line);border-radius:20px;padding:22px;background:white;min-width:0}.tag{display:inline-block;font-size:11px;font-weight:650;background:var(--surface);padding:6px 9px;border-radius:8px;margin-bottom:16px}.wordmark{height:98px;display:flex;align-items:center;max-width:100%;margin:0 -4px}.wordmark svg{width:100%;height:auto;max-height:98px}.pronunciation{font-size:13px;color:var(--muted);margin-top:0}.concept h3{font-size:20px}.concept>p{font-size:14px}.logo-detail{display:flex;align-items:center;gap:14px;margin:24px 0}.logo-detail span{flex:none;width:52px;height:52px;background:var(--surface);border-radius:12px;padding:7px}.logo-detail svg{width:100%;height:100%}.logo-detail p{font-size:12px;line-height:1.5;color:var(--muted);margin:0}.swatches{display:flex;gap:10px;margin:20px 0}.swatch{display:flex;flex:1;flex-direction:column;gap:6px;min-width:0}.swatch i{height:25px;border:1px solid #e3e3e5;border-radius:6px}.swatch small{font-size:10px;line-height:1.5;color:var(--muted)}.payoff{font-weight:650;color:var(--ink);border-top:1px solid var(--line);padding-top:18px}.caution{font-size:12px!important;color:var(--muted)}.ref{font-size:11px;font-weight:700;text-decoration:none;white-space:nowrap}.domain-wrap{margin-top:26px}table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:14px}th{text-align:left;padding:14px 12px;background:var(--surface);font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}td{vertical-align:top;padding:20px 12px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}th:first-child{width:18%}th:nth-child(2){width:22%}th:nth-child(3){width:24%}th:last-child{width:36%}.domain{font-weight:650;display:block}.domain-status{display:inline-block;font-size:12px;font-weight:600;border:1px solid #d1d1d1;border-radius:7px;padding:5px 8px}.domain-status.registered{color:#333;background:var(--surface)}.domain-status.indicated{border-color:var(--accent);color:var(--orange)}td small{display:block;font-size:12px;line-height:1.5;color:var(--muted);margin-top:10px}.example{border:1px solid var(--line);border-radius:22px;background:#fff;padding:38px;margin:28px 0}.example .example-brand{width:240px;max-width:100%;margin-bottom:22px}.example-brand svg{width:100%;height:auto}.example h3{font-size:clamp(30px,4vw,48px);max-width:700px;margin:22px 0}.example .copy{max-width:680px;font-size:18px}.cta{display:inline-flex;align-items:center;justify-content:center;min-height:48px;border-radius:12px;font-family:inherit;font-size:14px;font-weight:600;line-height:1.3;padding:12px 18px;background:#b44922;color:white;border:1px solid #b44922;gap:12px}.cta.secondary{background:white;color:var(--ink);border-color:#d1d1d1;margin-left:10px}.sample-actions{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}.sample-actions .cta.secondary{margin-left:0}.tone-examples{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:26px}.tone-examples p{padding:20px;border:1px solid var(--line);border-radius:14px;font-size:14px;margin:0}.tone-examples strong{display:block;font-size:12px;color:var(--muted);margin-bottom:8px}.shortlist{display:grid;grid-template-columns:repeat(2,1fr);gap:12px 28px;margin:24px 0}.shortlist p{border-top:1px solid var(--line);padding-top:14px;font-size:14px;margin:0}.shortlist strong{display:block;margin-bottom:4px}ul,ol{padding-left:22px}li{margin:.6em 0}.next{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin-top:24px}.next h3{font-size:20px}.next>div{border-top:2px solid var(--ink);padding-top:16px}.next p{font-size:14px}.sources{display:grid;grid-template-columns:repeat(2,1fr);gap:0 28px;margin-top:26px}.source{border-top:1px solid var(--line);scroll-margin-top:16px}summary{cursor:pointer;min-height:48px;padding:14px 0;font-size:13px;line-height:1.4}.source summary{display:flex;gap:12px}.source summary span{color:var(--orange);font-size:11px;flex:none}.source>div{font-size:12px;padding:0 0 18px 28px}.source blockquote{border-left:2px solid var(--line);padding-left:12px;margin:14px 0;color:var(--muted)}.metadata{border-top:1px solid var(--line);margin-top:24px}.metadata pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.6 ui-monospace,monospace;background:var(--surface);padding:18px;border-radius:12px}.muted{color:var(--muted)}footer{border-top:1px solid var(--line);padding:24px 0 44px;font-size:13px;color:var(--muted)}.skip{position:absolute;top:-80px;left:20px;background:white;padding:12px}.skip:focus{top:12px}
@media(max-width:900px){.concepts{grid-template-columns:1fr}.concept{display:grid;grid-template-columns:1fr 1fr;gap:0 28px}.concept .tag,.concept .wordmark,.concept .pronunciation{grid-column:1/-1}.concept .wordmark{max-width:360px}.concept .caution{grid-column:1/-1}.concept .logo-detail{grid-column:2;grid-row:4/6}.concept .swatches{grid-column:1/-1}.tone-examples{grid-template-columns:1fr}.next{gap:16px}}
@media(max-width:640px){main,header{padding-left:20px;padding-right:20px}header{font-size:12px}.hero{padding:36px 0}.lede{font-size:19px}.section{padding:34px 0}.columns,.shortlist,.sources,.next{grid-template-columns:1fr}.concept{display:block;padding:22px}.concept .wordmark{height:92px}.concepts{gap:16px}.concept .logo-detail{margin:22px 0}.example{padding:24px}.example .copy{font-size:16px}.domain-wrap table,.domain-wrap tbody,.domain-wrap tr,.domain-wrap td{display:block;width:100%}.domain-wrap thead{display:none}.domain-wrap tr{border:1px solid var(--line);border-radius:14px;margin-bottom:16px;padding:18px}.domain-wrap td{border:0;padding:0;margin-bottom:18px}.domain-wrap td:last-child{margin-bottom:0}.domain-wrap td::before{content:attr(data-label);display:block;text-transform:uppercase;font-size:10px;letter-spacing:.06em;color:var(--muted);margin-bottom:8px}.example .sample-actions{flex-direction:column;align-items:stretch}.example .cta{width:100%;text-align:center}.nav{gap:0 20px}.next{gap:20px}.cta.secondary{margin-left:0}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{.nav,.skip{display:none}body{font-size:11pt}.concepts{grid-template-columns:1fr}.concept{break-inside:avoid}.section{padding:20px 0}.source{break-inside:avoid}.sources{grid-template-columns:1fr}}
"""


JS = r"""
document.addEventListener('click', event => {
  const link = event.target.closest('a.ref');
  if (!link) return;
  const source = document.querySelector(link.getAttribute('href'));
  if (source?.tagName === 'DETAILS') source.open = true;
});
"""


def build(data: dict, hashes: dict) -> tuple[str, dict]:
    sources, by_id = sources_for(data)
    concept = next(item for item in data["concepts"]["concepts"] if item["name"] == "Spazelia")
    mock_wordmark = inline_svg(concept["assets"]["wordmark"], "example-", hashes)
    cards = concept_cards(data, by_id, hashes)
    collision = indexed(data["collisions"]["candidates"])
    fico = data["market"]["current_name_assessment"]["collision_source_ids"]
    longlist = [
        ("Ordivela", "Bel legame fra ordine e direzione, ma .com registrato e società omonima in ambito odontoiatrico. Fuori dalla rosa pratica."),
        ("Tenvia", "Piattaforma software B2B già esistente: troppo vicina alla categoria per costruire una marca distinguibile."),
        ("Largiva", "Nome usato per un prodotto sanitario; associazione poco coerente con il brief e con il rifiuto di un tono ospedaliero."),
        ("Svolia", "Rischia di richiamare “svogliata”. La promessa deve dare iniziativa e fiducia; non è una preferenza verificata con clienti."),
        ("Svoltiva", "Riserva da approfondire: suggerisce un cambiamento positivo, ma non ha un concept prioritario e richiede gli stessi controlli."),
        ("Altri nomi", "Ordavia, Temelia, Operiva, Semplora, Avviora, Tempiva e Spaziva restano proposte secondarie, con disponibilità non accertata."),
    ]
    rejection_html = "".join(f"<p><strong>{e(name)}</strong>{e(reason)} {refs([sid for row in collision.get(name.casefold(),{}).get('commercial_collisions',[]) for sid in row.get('source_ids',[])],by_id)}</p>" for name, reason in longlist)
    contrast_checks = [check for item in data["concepts"]["concepts"] for check in item["contrast_checks"]]
    if any(not check["pass"] or check["ratio"] < check["minimum"] for check in contrast_checks):
        raise ValueError("Brand palette contrast checks have not passed")
    metadata = {"date": DATE, "decision": "Spazelia recommended, Giorvia alternative; proposal awaiting live checks", "unique_cited_urls": len(sources), "creative_candidates": len(data['market']['candidate_names']), "concepts": data['concepts']['creative_ranking'], "palette_contrast_checks_passed": len(contrast_checks), "domains_checked": data['domains']['summary']['domainsChecked'], "confirmed_domain_availability": data['domains']['summary']['confirmedAvailable'], "input_sha256": hashes, "domain_note": "Observed third-party indications are separate from live registrar verification", "application_rebranded": False, "domains_purchased": False, "trademark_clearance": False, "handles_reserved": False}
    document = f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Spazelia — Nome e identità proposti</title><meta name="description" content="Proposta di naming e branding per Filo: Giorvia, Spazelia e Prontela, concept originali, domini osservati, collisioni e verifiche da completare."><style>{CSS}</style></head><body>
<a class="skip" href="#main">Vai al contenuto</a><header><strong>Filo · studio di marca</strong><span>Proposta · 5 ottobre 2026</span></header><main id="main">
<section class="hero"><div class="eyebrow">Nome, identità, promessa</div><h1>Spazelia: più spazio nella tua giornata.</h1><p class="lede">La prima proposta per rinominare Filo parte da <strong>spazio</strong>: tempo, attenzione e margine per il lavoro che conta. Un nome ampio per email, clienti e amministrazione, con un tono vivo e professionale.</p><div class="recommendation"><p><strong>Consiglio Spazelia, con Giorvia come alternativa.</strong> È una scelta creativa sostenuta dal posizionamento e dallo screening preliminare. Il nome definitivo richiede ancora verifica di dominio, marchi e username. Prontela resta un concept utile, con .com già registrato.</p></div><nav class="nav" aria-label="Indice"><a href="#change">Perché cambiare</a><a href="#concepts">Tre identità</a><a href="#domains">Domini e verifiche</a><a href="#voice">Homepage e tono</a><a href="#next">Scelta finale</a><a href="#sources">Fonti</a></nav></section>

<section class="section" id="change"><div class="eyebrow">Territorio di marca</div><h2>Filo è un buon significato, ma un nome già vicino ad altri software.</h2><div class="columns"><div><p>Il filo tiene insieme le cose e suggerisce continuità. Il problema è la riconoscibilità: <strong>FiloMail</strong> opera su email e attività, <strong>Filo</strong> su riunioni e follow-up, e un altro <strong>Filo</strong> sulla conoscenza aziendale. Sono usi ufficiali in ambiti molto vicini. {refs(fico,by_id)}</p><p>Cambiare ora permette di investire in un'identità più distinguibile prima del lancio. Questi usi commerciali sono un fatto osservato; non sono una conclusione sui diritti di marchio.</p></div><div><p>I nomi del settore si dividono fra compiti espliciti, come Fatture in Cloud, nomi di suite, come Odoo o Zoho, e benefici/metafore, come Pipedrive e Tieni il Conto. Un nome legato a “mail” o “fattura” restringerebbe il prodotto a un solo servizio. {refs(["reused-fic-features","reused-odoo-pricing","reused-tic"],by_id)}</p><p><strong>Il territorio scelto è la giornata operativa.</strong> Titolari, professionisti e segreterie aprono l'app per vedere cosa viene prima. Il nome deve sostenere tutti i moduli e lasciare al descrittore la spiegazione del prodotto.</p></div></div><p><strong>Fra gli assistenti rivolti alle imprese italiane,</strong> Ditta presenta una piattaforma AI per tutta l’azienda e Arya si propone come ambiente operativo dell’impresa. {refs(["ditta","arya"],by_id)} MAITIME lega il nome a tempo e AI per le piccole imprese; Hooney usa un tono amichevole e il beneficio della semplicità. {refs(["maitime","hooney"],by_id)} Per Spazelia la distinzione da costruire è più concreta: spazio nella giornata, priorità già visibili e prossimo passo chiaro, senza imitare una promessa generica di assistente universale.</p><p class="quiet">Descrittore proposto: <strong>«L'assistente operativo per studi e piccole imprese».</strong> AI può descrivere una funzione realmente attiva; non serve nel nome principale. La promessa di più tempo deve restare concreta, senza ore garantite o risultati inventati.</p></section>

<section class="section" id="concepts"><div class="eyebrow">Tre direzioni creative</div><h2>Vive nel tono. Ordinate nell'interfaccia.</h2><p class="section-intro">Bianco, carbone e grigi neutri; un accento arancio, usato dove aiuta a orientarsi. Niente verde o viola, grandi pannelli arancioni o simboli sanitari. I segni qui sotto sono proposte originali, non loghi già registrati.</p><div class="concepts">{cards}</div><p class="quiet">I wordmark usano testo SVG e font di sistema: la forma precisa varia fra computer. Per la versione finale occorre scegliere il carattere e convertirlo in tracciati con i diritti necessari. I simboli sono leggibili da 32 px; i pulsanti usano arancio scuro con testo bianco, l'arancio vivo resta un accento.</p></section>

<section class="section" id="domains"><div class="eyebrow">Disponibilità osservata</div><h2>Un'indicazione sul dominio è un controllo diverso dal nome.</h2><p class="section-intro">Le pagine consultate danno segnali utili, ma non sostituiscono una verifica aggiornata nel flusso del registrar. “AVAILABLE” su un servizio terzo è riportato come indicazione; un RDAP assente o un errore non conferma che un dominio sia acquistabile.</p>{domain_matrix(data['domains'],by_id)}<p><strong>Sistema da verificare:</strong> <code>spazelia.it</code> per l'Italia e <code>spazelia.com</code> se acquistabile alle condizioni confermate. <code>app.spazelia.it</code> sarebbe un sottodominio del dominio principale, non una seconda registrazione. Email come <code>ciao@spazelia.it</code> sono esempi futuri, non caselle attivate. {refs(['naming-nic-registration'],by_id)}</p><p class="quiet"><strong>Costo indicativo:</strong> Aruba pubblicizza domini selezionati da 0,99€ + IVA il primo anno e rinnovi da 11,99€ + IVA. Sono minimi generici, non preventivi per questi nomi: estensione, pacchetto, promozione e rinnovo vanno confermati nel checkout. Hosting, posta e marchio sono costi distinti. {refs(["naming-aruba-domain-pricing"],by_id)}</p><div class="columns"><div><h3>Marchi: verifica live da completare</h3><p>TMview restituiva manutenzione, eSearch una schermata senza risultati e WIPO una verifica umana. Non è stata completata una ricerca di anteriorità per nome. Per il SaaS è centrale la classe 42; la 9 riguarda eventuale software/app scaricabile, e la 35 va valutata solo in base ai servizi effettivamente offerti. {refs(data['collisions']['trademark_checks']['source_ids'],by_id)}</p></div><div><h3>Social: nessun handle confermato</h3><p>L'assenza di una pagina non prova che un username sia assegnabile. Per Giorvia è emerso un vecchio uso di @Giorvia su X, non verificato dal vivo. Prima di scegliere, controllare nome e varianti coerenti su Instagram, LinkedIn e X. <strong>Nessun account è stato riservato.</strong></p></div></div></section>

<section class="section" id="voice"><div class="eyebrow">Sistema di comunicazione</div><h2>La promessa resta: «La tua giornata, con più spazio».</h2><p class="section-intro">Il nome cambia; il beneficio già apprezzato resta. Il tono è chiaro, incoraggiante e operativo: un prossimo passo leggibile, parole quotidiane e controllo alla persona.</p><div class="example"><div class="example-brand">{mock_wordmark}</div><span class="eyebrow">L'assistente operativo per studi e piccole imprese</span><h3>La tua giornata,<br>con più spazio.</h3><p class="copy">Le priorità davanti a te. Appuntamenti, attività e documenti già in ordine, per capire da dove iniziare e portare avanti il lavoro con pochi passaggi.</p><div class="sample-actions"><span class="cta">Crea il tuo spazio</span><span class="cta secondary">Guarda come funziona</span></div><p class="quiet">Esempio di copy per una homepage futura. Non è la homepage pubblicata e i pulsanti sono esempi visivi.</p></div><div class="tone-examples"><p><strong>Priorità</strong>Questo incasso va controllato oggi. Apri i dettagli e segna il prossimo passo.</p><p><strong>Documento</strong>La bozza è pronta da rivedere. Controlla i dati prima di usarla.</p><p><strong>Connessione</strong>Collega la tua casella per controllare le email. I messaggi restano nel tuo spazio.</p></div><p>La comunicazione deve distinguere controlli manuali, documenti preparati e collegamenti davvero attivi. Un referente annotato non è un collega invitato; una bozza non è un invio. Meglio «Il prossimo passo, già pronto» di una promessa universale di automazione.</p></section>

<section class="section"><div class="eyebrow">Rosa e scarti</div><h2>Ridurre la scelta per rendere il test utile.</h2><p>La ricerca ha generato 13 proposte creative e approfondito un gruppo più ristretto con nomi di controllo e riserve. La priorità non è collezionare nomi: è confrontare due direzioni che si possano pronunciare, ricordare e usare senza ambiguità.</p><div class="shortlist">{rejection_html}</div></section>

<section class="section" id="next"><div class="eyebrow">Decisione e prossimi passi</div><h2>Portare Spazelia e Giorvia alla verifica finale.</h2><p class="section-intro">Spazelia sostiene più direttamente il beneficio di spazio e copre anche lavori mensili e scadenze. Giorvia è più breve e lega giorno e azione, ma ha il vicino Girovia e un precedente username da controllare. La scelta resta reversibile finché non si completano i controlli; oggi il prodotto e il dominio Railway mantengono Filo.</p><div class="next"><div><h3>1. Dominio e nomi simili</h3><p>Controllare .it e .com nello stesso registrar, salvando esito e ora. Per Giorvia includere Girovia nella ricerca di somiglianza; per Spazelia provare le varianti di dettatura. Nessun acquisto durante questa proposta.</p></div><div><h3>2. Marchio e username</h3><p>Ricercare esatto e simili in UIBM/TMview, EUIPO e WIPO per Italia/UE e servizi pertinenti. Esaminare stato ed elenco prodotti, oltre alla classe. Poi verificare una coppia di handle uniforme nei canali scelti.</p></div><div><h3>3. Test di comprensione</h3><p>Con cinque persone del segmento: mostrare ogni nome per cinque secondi, chiedere che prodotto immaginano, poi dettarlo senza mostrarlo. Annotare grafia, ricordo e associazioni. È un test qualitativo per trovare problemi, non una misura delle preferenze del mercato.</p></div></div></section>

<section class="section" id="sources"><div class="eyebrow">Metodo e fonti</div><h2>Ricerca pubblica e proposta creativa, con confini leggibili.</h2><p>Posizionamento da pagine ufficiali e ricerca competitiva precedente; screening di usi commerciali, nomi simili, strumenti marchi e indicazioni sui domini. Le testimonianze indicizzate o archiviate sono distinte dai riscontri diretti. Non sono state provate preferenze o notorietà dei nomi con clienti.</p><p class="quiet">{e(method_sentence(data))} Le citazioni sono deduplicate per URL. I risultati richiesti non sono pagine uniche lette; i dati sui domini sono fotografie delle risposte osservate, con tempi e limiti nella matrice.</p><div class="sources">{source_appendix(sources)}</div><details class="metadata"><summary>Provenienza e ricostruzione</summary><p>HTML autonomo con SVG incorporati, senza font o risorse esterne. Builder: <code>scripts/build-naming-review.py</code>. Le impronte identificano gli input; il builder non contatta registri o provider.</p><pre>{e(json.dumps(metadata,ensure_ascii=False,indent=2))}</pre></details></section><footer>Proposta di nome e identità · 5 ottobre 2026. Nessun dominio acquistato, marchio depositato, username riservato o cambiamento dell'applicazione in produzione.</footer></main><script>{JS}</script></body></html>'''
    return document, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/research/naming-branding-2026-10-05.html")
    args = parser.parse_args()
    data, hashes = load_inputs()
    output, metadata = build(data, hashes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "unique_cited_urls": metadata["unique_cited_urls"], "concepts": metadata["concepts"], "bytes": len(output.encode())},ensure_ascii=False))


if __name__ == "__main__":
    main()
