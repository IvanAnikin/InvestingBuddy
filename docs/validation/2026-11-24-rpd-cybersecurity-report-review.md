# Validace výstupu: discovery „US small-cap cybersecurity“ → report Rapid7 (RPD)

*Datum: 2026-11-24 · Prostředí: staging (API `bb8666de`) · Hodnotitel: Claude (nezávislá kontrola proti SEC XBRL)*

> Interní QA dokument. Není to investiční doporučení; hodnotí se kvalita výstupu aplikace, ne atraktivita akcie.

## 1. Co bylo hodnoceno

| Objekt | ID | Poznámka |
|---|---|---|
| Discovery běh | `036a518d` | Zadání: *„US small-cap cybersecurity software companies with growing recurring revenue, under-followed by analysts“* |
| Kandidáti | RPD (eligible, skóre 61.6), OSPN (eligible_unverified, 57.4) | CGNT vyřazen jako `size_unverified` |
| Auto-report (council) | `4519d02a` | 30 sekcí, 220 k znaků, gpt-4.1-mini + DeepSeek investigator, V3 research engine |
| Souběžný „live data“ draft | `65dd1e3b` | Starší Phase-9 pipeline, stejný den |

Ověření: všechna čísla z reportu jsem porovnal s `data.sec.gov/api/xbrl/companyfacts/CIK0001560327` (10-K FY2025 podaný 2026-02-19, 10-Q Q3 2026 podaný 2026-11-04).

## 2. Souhrnné hodnocení

| Dimenze | Známka (1–10) | Stručně |
|---|---|---|
| Číselná přesnost | **9** | Každé ověřené číslo sedí na SEC XBRL na dolar. |
| Citace a původ dat | **7** | Vše T1 (10-K, 10-Q, 8-K) s odkazem; ale jediný typ zdroje (issuer), žádné zprávy ani nezávislé zdroje. |
| Úplnost finanční analýzy | **4** | Chybí dluh (895 M USD konvertibilek), SBC, trend ARR, čísla guidance, počet zákazníků. |
| Kvalita interpretace | **4** | Správná fakta, ale obecné a místy chybné závěry (likvidita, původ růstu kapitálu, „margin compression“). |
| Shoda s tezí / segmentem | **3** | RPD nesplňuje dvě ze tří podmínek teze (rostoucí recurring revenue, málo sledovaná). |
| Kvalita discovery segmentu | **3** | Jen 8 leadů z DeepSeek recall, 2 kandidáti, webové hledání nedostupné. |
| Čitelnost a konzistence reportu | **3** | 220 k znaků JSON, boilerplate a vnitřní rozpory mezi sekcemi. |
| Bezpečnost a compliance | **9** | Žádné doporučení, cílová cena ani zakázané termíny; human review vynucen. |
| **Celkem** | **≈ 5 / 10** | **Spolehlivý sběr faktů, slabá analytická vrstva a výběr kandidátů.** |

## 3. Ověření čísel (report vs. SEC XBRL)

| Tvrzení v reportu | Report | SEC XBRL | Stav |
|---|---|---|---|
| Tržby FY2025 | 859.79 M | 859 794 k | ✅ |
| Hrubý zisk FY2025 / marže | 604.75 M / 70.3 % | 604 754 k → 70.34 % | ✅ |
| Provozní zisk FY2025 / marže | 11.57 M / 1.4 % | 11 568 k → 1.35 % | ✅ |
| Čistý zisk FY2025 | 23.38 M | 23 381 k | ✅ |
| Provozní CF FY2025 | 153.83 M | 153 827 k | ✅ |
| Capex FY2025 | 7.6 M (0.9 % tržeb) | 7 599 k | ✅ |
| FCF FY2025 | 146.23 M | 153 827 − 7 599 = 146 228 k | ✅ |
| Pokles tržeb 9M 2026 | −9.4 M | 632 978 k vs 642 406 k → −9 428 k | ✅ |
| Tržby Q3 2026 vs Q3 2025 | 212.4 vs 217.96 M | 212 404 k vs 217 960 k | ✅ |
| Provozní CF 9M 2026 / čistý zisk 9M | 106.9 M / 10.7 M | 106 926 k / 10 714 k | ✅ |
| Čistý zisk Q3 2026 | 3.511 M | 3 511 k | ✅ |
| Hotovost FY2025 → Q3 2026 | 246.7 → 335.7 M | 246 664 k → 335 714 k | ✅ (numeric verifier to přesto označil jako „konflikt“ – falešný poplach) |
| Vlastní kapitál FY2024 → FY2025 | 17.7 → 154.7 M | 17 711 k → 154 730 k | ✅ |
| Tržní kapitalizace | 980 M USD (68.54 M akcií × 14.30) | cover 10-Q: 68 535 768 akcií | ✅ |
| ROE 15.1 % | z koncového kapitálu | 23.38 / 154.73 = 15.1 % | ⚠️ aritmeticky správně, ale zavádějící: z průměrného kapitálu je to 27 % |
| P/E ~40.3× (draft `65dd1e3b`) | 66 M akcií, mcap 942 M | starší počet akcií z 10-K | ⚠️ nekonzistentní s 980 M v council reportu |
| Momentum 6M +96.7 %, 3M +17.3 % | EODHD | **neověřeno** (Stooq vyžaduje JS ověření) | ❓ |

**Závěr:** v reportu jsem nenašel ani jedno vymyšlené nebo špatně přepsané číslo. Disciplína „každé číslo má zdroj“ funguje.

## 4. Co report minul (fakta jsou ve stejných podáních, které už platforma stáhla)

### 4.1 Dluh a likvidita — nejvážnější mezera
- Q3 2026: konvertibilní dluhopisy **296.4 M dlouhodobé + 598.8 M krátkodobé = 895.2 M USD** (`ConvertibleDebtNoncurrent`, `ConvertibleDebtCurrent`).
- Hotovost + krátkodobé investice: 335.7 + 395.9 = **731.6 M** → čistý dluh ≈ **164 M USD**.
- Current ratio kleslo z 1.28 (FY2025) na **0.83** (Q3 2026), protože ~599 M konvertibilek se splatností do 12 měsíců přešlo do krátkodobých závazků.
- Report místo toho píše „total debt not acquired“, „large equity base reduces financial risk“ a „substantial cash position supports liquidity“. Refinancování ~599 M během příštích 12 měsíců je pro tuto firmu klíčová otázka a report ji vůbec nezmiňuje.
- **Příčina:** `app/integrations/sec_total_debt.py` nezná koncepty `ConvertibleDebt*` / `ConvertibleNotesPayable*`. Proto chybí i net debt, EV, EV/EBIT a debt/equity.

### 4.2 Stock-based compensation
- SBC FY2025 = **104.3 M** (12 % tržeb). FCF po odečtení SBC ≈ **42 M** (4.9 % tržeb), ne 146 M.
- „Cash conversion 658 %“ označuje red team za možný „účetní efekt“, ale skutečné vysvětlení (nepeněžní SBC) nikdo neuvádí.

### 4.3 Původ růstu vlastního kapitálu
- Council spekuluje o „capital raises / dilution / accounting changes“.
- Ve skutečnosti čistý zisk 23.4 M + SBC 104.3 M = 127.7 M, což vysvětluje ~93 % nárůstu o 137 M. Nejde o emisi akcií.

### 4.4 Trend růstu (klíčové pro tezi)
| Období | Růst tržeb |
|---|---|
| FY2023 → FY2024 | +8.5 % |
| FY2024 → FY2025 | +1.9 % |
| 9M 2026 vs 9M 2025 | −1.5 % |
| Q3 2026 vs Q3 2025 | −2.5 % (product subscriptions −1.9 %) |

Report pokles zmiňuje, ale jen jako jednu větu. Křivku zpomalování ani ARR trend (820 M v Q3 2026) neukazuje.

### 4.5 Marže
Council opakovaně píše „margin compression“. Q3 provozní marže ale meziročně **vzrostla** z 2.7 % na 4.2 % (8.95 M vs 5.90 M) díky škrtům v S&M (−19.7 M za 9M). Závěr je tedy opačný, než jaký naznačují data.

### 4.6 Další drobnější mezery
- Guidance (Q4/FY2026: ARR, tržby, FCF) je zmíněna jako událost, ale bez čísel. Gap tvrdí „no FY2026 guidance in evidence“.
- Počet zákazníků: v gapu „no customer-count metric“, přestože definice zákazníka je ve findings (F-customers) a číslo je v 10-K.
- Výměna CEO (Wael Mohamed od 1. 6. 2026), odchod 4 ředitelů a restrukturalizace jsou zachyceny správně (dobře).
- Peers: platforma nemá Tenable, Qualys, SentinelOne ani Varonis. PRGS, TLS a ASUR správně vyřadila jako nesrovnatelné, výsledkem je ale nulová peer tabulka.

## 5. Shoda se zadáním segmentu

| Podmínka teze | Výsledek pro RPD | Hodnocení |
|---|---|---|
| US, small-cap < 2 mld USD | 980 M USD, Nasdaq | ✅ ověřeno správně (shares × price) |
| Cybersecurity software | ano, 97 % recurring | ✅ |
| *Growing* recurring revenue | product subscriptions Q3 −1.9 % YoY, tržby 9M −1.5 % | ❌ discovery prošla na ročním FY2024→25 (+1.9 %) a ignorovala novější kvartály |
| *Under-followed by analysts* | parser podmínku nezachytil (`keywords: []`, confidence 0.25); obscurity skóre `null` (chybí Alpha Vantage, N-PORT, GDELT) | ❌ neověřeno; Rapid7 je navíc obecně široce pokrytý titul |

**Kvalita výběru:**
- 8 leadů pouze z DeepSeek recall, protože web byl nedostupný (`daily_used 300/300` = interní denní limit, ne jen kvóta Tavily).
- 4 leady vyřazeny kvůli velikosti (např. Zscaler), CGNT kvůli neověřitelné velikosti (20-F filer).
- Výsledek: 2 kandidáti, oba se zpomalujícím nebo klesajícím růstem.
- Obecně segment US small-cap cybersecurity obsahuje víc menších jmen, z nichž některé platforma zná z dřívějších běhů (authID, Rekor, Telos, Intrusion). V tomto běhu se ale vůbec neobjevily.
- Momentum 90 a „positive_momentum_candidate“ výrazně zvedly skóre (61.6), přestože fundamentální růst je záporný.

**Závěr:** Discovery našla správnou *velikostní* a *oborovou* kategorii, ale *investiční* kritéria teze (růst, nízké pokrytí) neuplatnila. RPD je spíš „turnaround / restrukturalizace s refinancováním“ než „rostoucí, přehlížená small-cap“.

## 6. Nalezené chyby v aplikaci (podle dopadu)

| # | Chyba | Dopad | Místo |
|---|---|---|---|
| B1 | Konvertibilní dluh se nemapuje na `total_debt` | Chybí net debt, EV, multiples a current-ratio kontext; chybné závěry o likviditě | `app/integrations/sec_total_debt.py` |
| B2 | Firma z discovery není navázaná na `legal_entity` | Earnings actuals „No SEC CIK“ (CIK přitom známe), relationships `entity_unresolved` | `app/services/pipeline/earnings_step.py:33` + vytvoření firmy v discovery |
| B3 | Auto-report nemá discovery lineage | Sekce `discovery_rationale` a `discovery_lineage` jsou prázdné, přestože `v3_research.thesis` zná `discovery_candidate_id` | generátor reportu (auto-report cesta) |
| B4 | Industry context ukazuje spotřebu elektřiny v USA u cybersecurity firmy | Irelevantní data v reportu | `app/services/industry_data/context.py:154` — `not_electricity` padá na `FOCUS_DEMAND` místo vynechání bloku |
| B5 | Kontrola růstu bere jen poslední roční pár | Klesající firma projde podmínkou „growing“ | `discovery/pipeline.py` `_sec_growth` / `directories.sec_revenue_growth` |
| B6 | Podmínka „under-followed“ se neparsuje ani nevynucuje | Teze se splní jen napůl | `market_thesis_parser.py`, obscurity |
| B7 | Screening odmítá všechna tvrzení z `sec.gov` jako `url_unreachable` | Discovery claims bez ověření (pravděpodobně chybí SEC User-Agent nebo jde o `cgi-bin` URL) | screening fetcher |
| B8 | Numeric verifier hlásí správná čísla o hotovosti jako konflikt; 91 ze 108 tvrzení neověřeno | Šum v review, falešná nedůvěra | numeric_verification |
| B9 | Deterministické Phase-9 sekce jsou boilerplate s rozpory | Např. „Missing fields (e.g. financials, ISIN, LEI)“ při LEI k dispozici; „market cap sourced“ vs. council „no market cap“; „AI adoption tailwind“ pro celý Technology sektor | bull/bear/risk agenti Phase 9 |
| B10 | Dva reporty téže firmy ve stejný den s jiným počtem akcií a P/E | Nekonzistence pro reviewera | Phase-9 live draft vs. V3 |
| B11 | Investigator: 17 z 19 odpovědí bylo useknuto délkou a znovu posláno | Náklady a latence | V3 loop (max_tokens) |

## 7. Doporučené priority oprav

1. **B1 + B2** (dluh a legal entity): největší dopad na správnost finanční analýzy. Malé, dobře testovatelné změny (doplnit koncepty, navázat entitu podle CIK při vytvoření firmy).
2. **B5 + B6** (růst z posledních kvartálů a vynucení „under-followed“): bez toho discovery nesplní hlavní zadání „firmy s potenciálem růstu“.
3. **B3 + B4** (lineage a irelevantní industry context): rychlé opravy, viditelné v každém auto-reportu.
4. **Analytická vrstva:** do council promptu přidat povinné kontroly pro SaaS: SBC-adjusted FCF, net debt a splatnosti, ARR trend, kvartální trajektorie a vysvětlení změny kapitálu.
5. **Čitelnost:** jedna stručná titulní stránka (teze → fit → 5 klíčových čísel → 3 rizika → otevřené otázky) nad 220 k znaků JSON.
6. **Provoz:** dobít Tavily nebo zvednout interní denní web cap, nastavit `ALPHA_VANTAGE_API_KEY` (pokrytí analytiky, konsenzus).

### Stav oprav

- **B1** (konvertibilní dluh a SEC net debt pro EV), **B2** (navázání zkoumané firmy na legal entity) a **B5** (růst z nejnovějšího 10-Q) jsou opravené ve V3.21.9 (ADR-118).
- Dopad na reálných datech Rapid7:
  - celkový dluh FY2025 je 892,28 M;
  - čistý dluh je 645,6 M a EV zhruba 1,63 mld.;
  - růst je −1,47 % (9M 2026 proti 9M 2025).
- Ostatní body zůstávají otevřené.

## 8. Co funguje dobře

- Přesnost čísel a citace na konkrétní XBRL fakta a stránky podání.
- Oddělení ročních a kvartálních čísel; nic se neanualizuje.
- Ověření velikosti (shares × price), správné vyřazení velkých firem.
- V3 research engine (findings F1–F31) je výrazně kvalitnější než Phase-9 sekce: 97 % recurring revenue, ARR 820 M, výměna CEO, restrukturalizace, PSU navázané na MDR/SIEM ARR.
- Bezpečnostní vrstva: žádné doporučení ani cílová cena, gapy jsou explicitně přiznané a nic se nedoplňuje vymyšlenou hodnotou.
