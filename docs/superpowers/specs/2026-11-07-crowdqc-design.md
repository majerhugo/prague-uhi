# Kontrola kvality Netatmo dát (CrowdQC+) – dizajn

Dátum: 2026-11-07 · Vetva: `qc` · Stav: na schválenie

## 1. Cieľ a východiská

Mapa pražského mestského tepelného ostrova má za **uzavreté dni** zobrazovať iba stanice, ktoré prešli
kontrolou kvality CrowdQC+ (Fenner a kol., 2021; R balík `CrowdQCplus`, GPL-3, kroky m1–m5).
Dnešný (neuzavretý) deň sa zobrazuje surový a je označený „preliminary“.

Požiadavky od používateľa:

- použiť **všetky kroky m1–m5** s predvolenými parametrami balíka,
- použiť **originálny R balík**, nie vlastný port,
- archív zostáva **surový**; kontrola dáta len označuje, nič nemaže,
- označené stanice sú v mape predvolene skryté, dajú sa zapnúť ako šedé,
- **stanice s málo dátami sa zobrazujú** (kontrola ich len nevylúči),
- **izolované stanice** (menej než 5 susedov do 3 km) sa **nechávajú** (`keep_isolated = TRUE`),
- vývoj je lokálny na vetve `qc`, do `main` až po súhlase (cron beží z `main`).

Úspech: za uzavretý deň má každá stanica v archíve príznak (prešla / neprešla + dôvod); mapa podľa neho
stanice skrýva; hodinové behy pipeline sa nespomalia; výpadok kontroly nikdy nezablokuje publikovanie mapy.

## 2. Vstup

- Zdroj: `archive/` (jedna snímka na UTC hodinu, teplota ×10, ofset času, riadok registra).
- Identita stanice = **riadok registra** (MAC + poloha, presun > 15 m = nový riadok, ktorý sa hodnotí odznova).
- Stĺpce pre balík: `p_id` (riadok registra), `time` (UTC hodina snímky), `ta` (°C), `lon`, `lat`,
  `z` (výška z registra).
- Teploty sú okamžité hodnoty z asi :30 každej hodiny, nie hodinové priemery (balík čaká hodinové dáta).
  Považujeme za prijateľné a uvedieme v popise metódy.

## 3. Algoritmus

Spúšťa sa originálny balík (`CrowdQCplus` ≥ 1.1.2) bez úprav:

1. `cqcp_padding()` – zarovnanie na pravidelnú hodinovú mriežku.
2. `cqcp_check_input()` – kontrola vstupu.
3. `cqcp_qcCWS(data, complete = TRUE, m5_keep_isolated = TRUE, includeOptional = FALSE)` – m1–m5, voliteľné kroky o1–o4 vypnuté,
   ostatné parametre predvolené (m2: low 0,01 / high 0,95, výšková korekcia 0,0065 K/m; m3: cutOff 0,2;
   m4: cutOff 0,9; m5: 3 km, ≥ 5 susedov, alfa 0,1, max. výškový rozdiel 100 m).

Parametre sú konštanty na jednom mieste v `qc.R`, aby sa dali ladiť.

## 4. Okno a načasovanie

- **Okno = 30 dní** končiacich uzatváraným dňom (zatiaľ menej, kým archív nemá 30 dní).
  Autori odporúčajú 7 dní až 1 mesiac, pre takmer reálny čas ~14 dní; 30 dní zvolené kvôli stabilnej
  korelácii v m4, hodnota je konštanta.
- Deň sa uzatvára **po skončení UTC dňa**: pri prvom hodinovom behu po 00:00 UTC pre každý deň bez
  súboru kontroly sa vypočíta a zapíše natrvalo. Zapísaný deň sa už nemení.
- m1, m3, m4 rozhodujú o stanici za celé okno; m2 a m5 označujú jednotlivé hodiny.
  Z výstupu sa uloží len deň, ktorý sa uzatvára.
- Stanica s málo dátami (menej než 24 platných hodín v okne) sa **vylúči pred volaním R** a mapa ju
  **zobrazuje** (bez príznaku); do siete pre m2–m5 nevstupuje.
- Jednorazový príkaz `python qc.py --recompute` prepočíta celú históriu (po zmene pravidiel alebo balíka).

## 5. Uloženie

V archíve (vetva `data`), append-only, jeden súbor na deň: `qc/YYYY-MM-DD.json`:

- `f`: `{"<UTC hodina snímky, epoch s>": [[riadok registra, "m1".."m5"], ...]}` – stanice, ktoré neprešli;
  dôvod je prvá úroveň, ktorá je FALSE (príznaky v balíku sú kumulatívne); stanica vyradená v m1/m3/m4
  sa uvádza v každej svojej hodine,
- `ver`, `day`, `window_days`, `min_hours`, `package`, `checked`, `unchecked` (počet staníc).

`scripts/push_data.sh` už ukladá celý `archive/`, takže netreba meniť.

## 6. Pipeline

- Nový krok v workflowe `update-map.yml` (po „Restore archive“, pred „Save archive“):
  nastaví R (`r-lib/actions/setup-r`, závislosti kešované) **iba keď je čo počítať**
  (existuje uzavretý deň bez súboru kontroly), spustí `python qc.py`.
- Závislosti balíka: `data.table`, `robustbase`, `lubridate`, `terra`, `geodata`
  (posledné dva len kvôli importu; výšku dodávame sami).
- Krok je **neblokujúci**: pri zlyhaní `::warning::` a pokračuje sa, mapa sa publikuje bez kontroly
  pre dotknutý deň. Chýbajúci súbor sa doplní pri ďalšom behu.
- Hodinové behy nemajú R nainštalované, kým nie je čo počítať (cca 1 beh z 24).
- Limit `timeout-minutes: 30` ostáva; m5 pri 30 dňoch a stovkách staníc má bežať v minútach.

## 7. Web

- `archive.build_web` pridá do `days/YYYY-MM-DD.json` pre každú snímku príznaky
  (zoznam indexov staníc, ktoré neprešli, s dôvodom); pre dni bez súboru kontroly príznaky nie sú.
- `index.html`: označené stanice predvolene **skryté**; prepínač vrstvy „Stations that failed QC“
  ich ukáže šedo; popup uvedie dôvod. Označené stanice sa nezapočítavajú do štatistík panelu.
- Pri neuzavretom dni (bez súboru kontroly) panel ukáže „preliminary, not yet quality-checked“.
- Text metódy v paneli (anglicky): kontrola CrowdQC+, hodinové okamžité hodnoty, 30-dňové okno.

## 8. Testy a overenie

- Lokálne R v conda prostredí `netatmo-uhi` (`r-base`, `CrowdQCplus` z GitHubu).
- `test_archive.py`: testy exportu/importu a vloženia príznakov do webu (bez R).
- `test_qc.py` (vyžaduje R): syntetická sieť so staniciami – interiérová (stála teplota), s radiačnou chybou,
  dve stanice s rovnakými súradnicami, **horúci zhluk v centre, ktorý musí prejsť**, nová stanica s málo dátami.
- Overenie na skutočných dátach: koľko staníc vyradí ktorý krok a podiel označených v centre vs. na okraji.
  Ak m2 vyradzuje centrum hromadne, parametre sa zmiernia **až po konzultácii s používateľom**.
- `scripts/test_pipeline.sh` sa rozšíri o kontrolu, že krok kvality nezablokuje publikovanie.

## 9. Postup nasadenia

1. Lokálny skript a správa nad archívom (bez zásahu do pipeline) – kontrola výsledkov s používateľom.
2. Napojenie na pipeline a web; test v cloude na testovacích vetvách (`data_branch`/`pages_branch`).
3. Po súhlase používateľa merge do `main`, spustenie `--recompute` na históriu.

Pri každom pushi, merge a mazaní vetvy sa pred krokom pýtame používateľa (`fetch.py` sa lokálne nespúšťa bez `--no-fetch`).

## 10. Riziká a otvorené body

- Hodinový okamžitý vzorek namiesto priemeru môže zhoršiť m4 oproti odporúčaniu autorov; sledovať.
- Inštalácia `terra`/`geodata` v CI môže byť pomalá; riešenie: binárne balíky + kešovanie.
- Presné výstupné stĺpce balíka (príznaky m1–m5) treba pri implementácii overiť na skutočnom behu.
- Zatiaľ máme len 9 dní histórie; výsledky m3/m4 sú spoľahlivé až pri dlhšom okne.
- Licencia GPL-3 sa týka balíka; používame ho ako samostatný nástroj, náš kód ho nekopíruje.
